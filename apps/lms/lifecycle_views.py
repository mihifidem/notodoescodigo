from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views import View
from django.utils import timezone
from django.db import transaction
from django.core.exceptions import PermissionDenied, ValidationError

from apps.users.notifications import platform_notifications
from .notification_state import mark_item_notifications, notification_counts, notification_feed
from .models import LearningActivity, Quiz
from .views import CourseContextMixin
from .lifecycle import process_activity, process_course, process_quiz
from .services import process_course_announcements
from .policies import can_edit_course, is_course_member


class CourseLifecycleUpdateView(CourseContextMixin, View):
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        course = self.get_course()
        if not can_edit_course(request.user, course) and not is_course_member(request.user, course):
            raise PermissionDenied
        now = timezone.now()
        try:
            with transaction.atomic():
                process_course(course.pk, now=now)
                process_course_announcements(course, now=now)
                quiz_ids = Quiz.objects.filter(
                    course=course, status__in=(Quiz.Status.SCHEDULED, Quiz.Status.PUBLISHED, Quiz.Status.CLOSED),
                ).values_list('pk', flat=True)
                for quiz_id in quiz_ids:
                    process_quiz(quiz_id, now=now)
                for activity_id in LearningActivity.objects.filter(course=course).values_list('pk', flat=True):
                    process_activity(activity_id, now=now)
        except ValidationError as error:
            return JsonResponse({'error': ' '.join(error.messages)}, status=400)
        return JsonResponse({'updated_at': now.isoformat()})


class LifecycleNotificationFeedView(LoginRequiredMixin, View):
    http_method_names = ['get']

    def get(self, request):
        course = None
        course_id = request.GET.get('course_id')
        if course_id:
            from apps.courses.models import Curso
            from django.shortcuts import get_object_or_404
            if not course_id.isdecimal():
                return JsonResponse({'error': 'Invalid course.'}, status=400)
            course = get_object_or_404(Curso, pk=int(course_id))
            if not can_edit_course(request.user, course) and not is_course_member(request.user, course):
                raise PermissionDenied
        after = request.GET.get('after')
        cursor = None
        if after is not None:
            try:
                cursor = int(after)
            except ValueError:
                return JsonResponse({'error': 'Invalid notification cursor.'}, status=400)
            if cursor < 0:
                return JsonResponse({'error': 'Invalid notification cursor.'}, status=400)
        return JsonResponse(notification_feed(
            request.user, course=course, after=cursor,
            teacher_view=False if request.GET.get('preview') == '1' else None,
        ))


class CourseNotificationStateView(CourseContextMixin, View):
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        course = self.get_course()
        if not can_edit_course(request.user, course) and not is_course_member(request.user, course):
            raise PermissionDenied
        event_id = request.POST.get('event_id')
        target = request.POST.get('target', '')
        if (not event_id and not target) or (event_id and not event_id.isdecimal()):
            return JsonResponse({'error': 'Select a notification item.'}, status=400)
        from urllib.parse import urlsplit
        from django.urls import Resolver404, resolve
        from django.shortcuts import get_object_or_404
        from .notification_state import visible_course_notifications
        if event_id:
            get_object_or_404(
                platform_notifications(request.user),
                lms_notification__course=course,
                lms_notification__event_id=int(event_id),
                lms_notification__event__metadata__source='people',
            )
        else:
            parsed = urlsplit(target)
            try:
                match = resolve(parsed.path)
            except Resolver404:
                return JsonResponse({'error': 'Invalid notification item.'}, status=400)
            if (
                parsed.netloc or parsed.scheme or match.namespace != 'lms'
                or match.kwargs.get('course_id') != course.pk
                or match.url_name not in ('quiz_detail', 'course_activity_detail', 'course_page_detail')
            ):
                return JsonResponse({'error': 'Invalid notification item.'}, status=400)
            if not any(
                notice.event.metadata.get('url', '').split('#')[0] == parsed.path
                or (match.url_name == 'quiz_detail' and notice.event.metadata.get('quiz_id') == match.kwargs['quiz_id'])
                for notice in visible_course_notifications(course, request.user)
            ):
                return JsonResponse({
                    'read': 0, 'course_counts': notification_counts(course, request.user),
                    'count': platform_notifications(request.user).filter(leida=False).count(),
                })
        changed = mark_item_notifications(
            course, request.user, target=target,
            event_id=int(event_id) if event_id else None, area=request.POST.get('area', ''),
        )
        return JsonResponse({
            'read': changed, 'course_counts': notification_counts(course, request.user),
            'count': platform_notifications(request.user).filter(leida=False).count(),
        })
