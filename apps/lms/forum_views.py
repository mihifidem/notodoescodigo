from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View

from .consumers import broadcast_course_update
from .forms import DiscussionPostForm, DiscussionTopicForm
from .models import DiscussionPost, DiscussionTopic, Notification
from .policies import can_edit_course
from .services import notify_forum_activity
from .views import CourseContextMixin, CoursePageMixin


def root_post(post):
    while post.parent_id:
        post = post.parent
    return post


def visible_forum_notifications(course, user):
    notices = Notification.objects.filter(
        course=course, recipient=user, status=Notification.Status.UNREAD,
        payload__forum_id__isnull=False,
    ).select_related('event')
    forum_ids = {
        forum.pk for forum in DiscussionTopic.objects.filter(course=course, is_deleted=False)
        if forum.is_visible_to_user(user)
        and (can_edit_course(user, course) or forum.status not in (forum.Status.DRAFT, forum.Status.ARCHIVED))
    }
    root_ids = set(DiscussionPost.objects.filter(
        topic_id__in=forum_ids, parent__isnull=True, is_deleted=False, is_hidden=False,
    ).values_list('pk', flat=True))
    return [
        notice for notice in notices
        if notice.payload.get('forum_id') in forum_ids and notice.payload.get('root_id') in root_ids
    ]


class ForumMixin(CourseContextMixin, CoursePageMixin):
    def get_forum(self):
        self.course = self.get_course()
        forum = get_object_or_404(
            DiscussionTopic.objects.select_related('course', 'author', 'section'),
            course=self.course, pk=self.kwargs['forum_id'], is_deleted=False,
        )
        if not forum.is_visible_to_user(self.request.user):
            raise PermissionDenied
        if not can_edit_course(self.request.user, self.course) and forum.status in (forum.Status.DRAFT, forum.Status.ARCHIVED):
            raise PermissionDenied
        return forum

    def context(self, forum, **extra):
        return {
            **self.get_course_context(self.course),
            'area_key': 'discussion', 'forum': forum, **extra,
        }

    def require_writable(self, forum):
        if not self.can_write(forum):
            raise PermissionDenied

    def can_write(self, forum):
        return (
            forum.can_participate(self.request.user)
            and forum.status == forum.Status.OPEN and not forum.is_locked and forum.allow_replies
        )

    def query_url(self, forum, post):
        return reverse('lms:course_forum_query', kwargs={
            'course_id': forum.course_id, 'forum_id': forum.pk, 'post_id': root_post(post).pk,
        })

    def revision(self, forum):
        latest = forum.posts.aggregate(latest=Max('updated_at'))['latest']
        return f'{forum.updated_at.isoformat()}:{latest.isoformat() if latest else ""}:{forum.posts.count()}'

    def live_response(self, template, context, forum):
        return JsonResponse({
            'html': render_to_string(template, context, request=self.request),
            'revision': self.revision(forum),
        })


class ForumDetailView(ForumMixin, View):
    def get_queries(self, forum):
        queries = list(forum.posts.filter(
            parent__isnull=True, is_deleted=False, is_hidden=False,
        ).select_related('author').order_by('-created_at'))
        children = {}
        for post in forum.posts.filter(is_hidden=False).values('pk', 'parent_id', 'is_deleted'):
            children.setdefault(post['parent_id'], []).append(post)
        for query in queries:
            query.reply_count = 0
            pending = list(children.get(query.pk, []))
            while pending:
                reply = pending.pop()
                if not reply['is_deleted']:
                    query.reply_count += 1
                pending.extend(children.get(reply['pk'], []))
        return queries

    def get(self, request, *args, **kwargs):
        forum = self.get_forum()
        queries = self.get_queries(forum)
        context = self.context(
            forum, queries=queries, form=DiscussionPostForm(),
            writable=self.can_write(forum),
        )
        if request.GET.get('fragment') == '1':
            return self.live_response('lms/forum_queries.html', context, forum)
        return render(request, 'lms/forum_detail.html', context)

    def post(self, request, *args, **kwargs):
        forum = self.get_forum()
        self.require_writable(forum)
        post = DiscussionPost(topic=forum, author=request.user)
        form = DiscussionPostForm(request.POST, instance=post)
        if form.is_valid():
            with transaction.atomic():
                post = form.save()
                forum.last_activity_at = timezone.now()
                forum.save(update_fields=['last_activity_at', 'updated_at'])
                notify_forum_activity(post, request.user, action='query')
                transaction.on_commit(lambda: broadcast_course_update(self.course, 'created', item_type='forum'))
            return redirect(self.query_url(forum, post))
        context = self.context(
            forum, form=form, writable=True,
            queries=self.get_queries(forum),
        )
        return render(request, 'lms/forum_detail.html', context)


class ForumEditView(ForumMixin, View):
    def editable_forum(self):
        forum = self.get_forum()
        if not can_edit_course(self.request.user, self.course):
            raise PermissionDenied
        return forum

    def get(self, request, *args, **kwargs):
        forum = self.editable_forum()
        return render(request, 'lms/forum_form.html', self.context(
            forum, form=DiscussionTopicForm(instance=forum),
        ))

    def post(self, request, *args, **kwargs):
        forum = self.editable_forum()
        form = DiscussionTopicForm(request.POST, instance=forum)
        if form.is_valid():
            with transaction.atomic():
                form.save()
                transaction.on_commit(lambda: broadcast_course_update(self.course, 'updated', item_type='forum'))
            messages.success(request, _('El foro se ha actualizado.'))
            return redirect('lms:course_forum_detail', course_id=self.course.pk, forum_id=forum.pk)
        return render(request, 'lms/forum_form.html', self.context(forum, form=form))


class ForumQueryView(ForumMixin, View):
    def get_root(self, forum):
        return get_object_or_404(
            forum.posts.select_related('author', 'accepted_solution'),
            pk=self.kwargs['post_id'], parent__isnull=True, is_deleted=False, is_hidden=False,
        )

    def query_context(self, forum, root, form=None, *, mark_read=False):
        all_posts = list(forum.posts.filter(is_hidden=False).select_related('author', 'parent').order_by('created_at'))
        children = {}
        for post in all_posts:
            children.setdefault(post.parent_id, []).append(post)

        def replies(parent, level=0):
            for post in children.get(parent.pk, []):
                post.thread_depth = level
                post.can_manage = post.user_can_edit(self.request.user)
                post.can_reply = post.depth < DiscussionPost.MAX_DEPTH
                yield post
                yield from replies(post, level + 1)

        response_posts = list(replies(root))
        can_view_replies = (
            not forum.require_reply_before_view or can_edit_course(self.request.user, self.course)
            or root.author_id == self.request.user.pk
            or any(post.author_id == self.request.user.pk and not post.is_deleted for post in response_posts)
        )
        if not can_view_replies:
            response_posts = []
        elif mark_read:
            ids = [notice.pk for notice in visible_forum_notifications(self.course, self.request.user)
                   if notice.payload.get('root_id') == root.pk]
            from apps.users.notifications import mark_lms_notifications_read
            mark_lms_notifications_read(Notification.objects.filter(pk__in=ids))
        return self.context(
            forum, query=root, replies=response_posts, can_view_replies=can_view_replies,
            reply_count=sum(not post.is_deleted for post in response_posts),
            can_resolve=root.author_id == self.request.user.pk or can_edit_course(self.request.user, self.course),
            form=form or DiscussionPostForm(is_reply=True),
            can_manage=root.user_can_edit(self.request.user),
            forum_writable=self.can_write(forum),
            writable=self.can_write(forum) and not root.accepted_solution_id,
        )

    def get(self, request, *args, **kwargs):
        forum = self.get_forum()
        root = self.get_root(forum)
        context = self.query_context(forum, root, mark_read=request.GET.get('fragment') != '1')
        if request.GET.get('fragment') == '1':
            return self.live_response('lms/forum_conversation.html', context, forum)
        return render(request, 'lms/forum_query.html', context)

    def post(self, request, *args, **kwargs):
        forum = self.get_forum()
        root = self.get_root(forum)
        if request.POST.get('_action') == 'mark_read':
            context = self.query_context(forum, root, mark_read=True)
            if not context['can_view_replies']:
                raise PermissionDenied
            return redirect(self.query_url(forum, root))
        self.require_writable(forum)
        if root.accepted_solution_id:
            raise PermissionDenied(_('El hilo está cerrado porque ya tiene una solución aceptada.'))
        parent_id = request.POST.get('parent_id')
        if parent_id and not parent_id.isdecimal():
            return JsonResponse({'error': _('La respuesta indicada no es válida.')}, status=400)
        parent = get_object_or_404(forum.posts, pk=parent_id or root.pk, is_deleted=False, is_hidden=False)
        if root_post(parent).pk != root.pk:
            raise PermissionDenied
        if parent.depth >= DiscussionPost.MAX_DEPTH:
            return JsonResponse({'error': _('La profundidad máxima permitida es de 5 niveles.')}, status=400)
        if parent.pk != root.pk and not self.query_context(forum, root)['can_view_replies']:
            raise PermissionDenied
        post = DiscussionPost(topic=forum, author=request.user, parent=parent)
        form = DiscussionPostForm(request.POST, instance=post, is_reply=True)
        if form.is_valid():
            with transaction.atomic():
                locked_root = DiscussionPost.objects.select_for_update().get(pk=root.pk)
                if locked_root.accepted_solution_id:
                    raise PermissionDenied(_('El hilo está cerrado porque ya tiene una solución aceptada.'))
                post = form.save()
                forum.last_activity_at = timezone.now()
                forum.save(update_fields=['last_activity_at', 'updated_at'])
                notify_forum_activity(post, request.user)
                transaction.on_commit(lambda: broadcast_course_update(self.course, 'created', item_type='forum'))
            return redirect(self.query_url(forum, root) + f'#forum-post-{post.pk}')
        context = self.query_context(forum, root, form)
        context['reply_parent_id'] = parent.pk
        return render(request, 'lms/forum_query.html', context)


class ForumPostEditView(ForumMixin, View):
    def get_post(self, forum):
        post = get_object_or_404(forum.posts.select_related('parent'), pk=self.kwargs['post_id'], is_deleted=False, is_hidden=False)
        root = root_post(post)
        if root.is_deleted or root.is_hidden or not post.user_can_edit(self.request.user):
            raise PermissionDenied
        self.require_writable(forum)
        return post

    def get(self, request, *args, **kwargs):
        forum = self.get_forum()
        post = self.get_post(forum)
        return render(request, 'lms/forum_post_form.html', self.context(
            forum, post=post, form=DiscussionPostForm(instance=post, is_reply=bool(post.parent_id)),
            cancel_url=self.query_url(forum, post),
        ))

    def post(self, request, *args, **kwargs):
        forum = self.get_forum()
        post = self.get_post(forum)
        form = DiscussionPostForm(request.POST, instance=post, is_reply=bool(post.parent_id))
        if form.is_valid():
            with transaction.atomic():
                post = form.save()
                DiscussionPost.objects.filter(accepted_solution=post).update(accepted_solution=None, updated_at=timezone.now())
                transaction.on_commit(lambda: broadcast_course_update(self.course, 'updated', item_type='forum'))
            return redirect(self.query_url(forum, post))
        return render(request, 'lms/forum_post_form.html', self.context(
            forum, post=post, form=form, cancel_url=self.query_url(forum, post),
        ))


class ForumPostDeleteView(ForumPostEditView):
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        forum = self.get_forum()
        post = self.get_post(forum)
        with transaction.atomic():
            post.is_deleted = True
            post.save()
            DiscussionPost.objects.filter(accepted_solution=post).update(accepted_solution=None, updated_at=timezone.now())
            transaction.on_commit(lambda: broadcast_course_update(self.course, 'deleted', item_type='forum'))
        messages.success(request, _('La publicación se ha eliminado.'))
        if not post.parent_id:
            return redirect('lms:course_forum_detail', course_id=self.course.pk, forum_id=forum.pk)
        return redirect(self.query_url(forum, post))


class ForumSolutionView(ForumQueryView):
    http_method_names = ['post']

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        forum = self.get_forum()
        root = self.get_root(forum)
        if root.author_id != request.user.pk and not can_edit_course(request.user, self.course):
            raise PermissionDenied
        root = DiscussionPost.objects.select_for_update().get(pk=root.pk)
        solution_id = request.POST.get('solution_id', '')
        if solution_id and not solution_id.isdecimal():
            return JsonResponse({'error': _('La solución indicada no es válida.')}, status=400)
        root.accepted_solution = get_object_or_404(
            forum.posts, pk=solution_id, is_deleted=False, is_hidden=False,
        ) if solution_id else None
        try:
            root.full_clean()
        except ValidationError as error:
            return JsonResponse({'error': ' '.join(error.messages)}, status=400)
        root.save()
        if root.accepted_solution:
            notify_forum_activity(root.accepted_solution, request.user, action='solution')
        transaction.on_commit(lambda: broadcast_course_update(self.course, 'updated', item_type='forum'))
        return redirect(self.query_url(forum, root))


class ForumNotificationView(CourseContextMixin, CoursePageMixin, View):
    http_method_names = ['get']

    def get(self, request, *args, **kwargs):
        course = self.get_course()
        notices = visible_forum_notifications(course, request.user)
        return JsonResponse({
            'count': len(notices),
            'items': [{'title': notice.title, 'message': notice.message, 'url': notice.payload['url']}
                      for notice in notices[:10]],
        })
