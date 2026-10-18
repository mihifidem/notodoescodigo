from urllib.parse import urlsplit

from django.urls import Resolver404, resolve, reverse
from django.utils import timezone

from .models import Announcement, DiscussionPost, DiscussionTopic, LearningActivity, Notification, NotificationEvent, Quiz
from .policies import can_edit_course, is_course_member
from .ui import AREA_KEYS


def notification_target(notice):
    metadata = notice.event.metadata
    url = metadata.get('url')
    if url:
        if metadata.get('kind') == 'quiz_feedback' and metadata.get('quiz_id'):
            return f"{url.split('#')[0]}#grades-{metadata['quiz_id']}"
        return url
    areas = {
        NotificationEvent.Type.ANNOUNCEMENT: 'announcements',
        NotificationEvent.Type.MODULE: 'content',
        NotificationEvent.Type.GRADE: 'grades',
        NotificationEvent.Type.NOTE_PUBLISHED: 'grades',
        NotificationEvent.Type.ENROLLMENT: 'people',
        NotificationEvent.Type.MENTION: 'discussion',
    }
    return reverse('lms:course_area', kwargs={
        'course_id': notice.course_id,
        'area': areas.get(notice.event.event_type, 'home'),
    }) if notice.course_id else reverse('lms:dashboard')


def notification_area(notice):
    metadata = notice.event.metadata
    if metadata.get('forum_id'):
        return 'discussion'
    if metadata.get('source') == 'people':
        return 'people'
    try:
        match = resolve(urlsplit(notification_target(notice)).path)
    except Resolver404:
        return 'home'
    if 'activity_id' in match.kwargs:
        return 'activities'
    if 'quiz_id' in match.kwargs:
        return 'grades' if notice.event.event_type == NotificationEvent.Type.GRADE else 'quizzes'
    if 'page_id' in match.kwargs or 'page_slug' in match.kwargs:
        return 'pages'
    if 'file_id' in match.kwargs:
        return 'files'
    if 'topic_id' in match.kwargs or 'module_id' in match.kwargs:
        return 'content'
    return match.kwargs.get('area', 'home')


def visible_course_notifications(course, user):
    teacher = can_edit_course(user, course)
    if not teacher and not is_course_member(user, course):
        return []
    notices = Notification.objects.filter(
        course=course, recipient=user,
    ).select_related('event')
    announcements = {str(item.pk) for item in Announcement.visible_for_course(course, user)}
    from .activity_workflow import can_read_activity
    from .lifecycle import student_recipients

    forums = {
        forum.pk for forum in DiscussionTopic.objects.filter(course=course, is_deleted=False)
        if forum.is_visible_to_user(user)
        and (teacher or forum.status not in (forum.Status.DRAFT, forum.Status.ARCHIVED))
    }
    roots = set(DiscussionPost.objects.filter(
        topic_id__in=forums, parent__isnull=True, is_deleted=False, is_hidden=False,
    ).values_list('pk', flat=True))
    activities = {
        item.pk for item in LearningActivity.objects.filter(course=course).select_related('course')
        if can_read_activity(user, item)
    }
    quizzes = {
        quiz.pk for quiz in Quiz.objects.filter(course=course)
        if teacher or (
            quiz.status in (Quiz.Status.PUBLISHED, Quiz.Status.CLOSED)
            and any(student.pk == user.pk for student in student_recipients(course, section_id=quiz.section_id))
        )
    }
    visible = []
    for notice in notices:
        metadata = notice.event.metadata
        if notice.event.event_type == NotificationEvent.Type.ANNOUNCEMENT:
            if str(metadata.get('announcement_id')) not in announcements:
                continue
        if metadata.get('forum_id') and (
            metadata['forum_id'] not in forums or metadata.get('root_id') not in roots
        ):
            continue
        try:
            match = resolve(urlsplit(notification_target(notice)).path)
        except Resolver404:
            continue
        if match.namespace != 'lms' or match.kwargs.get('course_id') != course.pk:
            continue
        if match.kwargs.get('activity_id') and match.kwargs['activity_id'] not in activities:
            continue
        quiz_id = match.kwargs.get('quiz_id') or metadata.get('quiz_id')
        if quiz_id and quiz_id not in quizzes:
            continue
        visible.append(notice)
    return visible


def notification_counts(course, user, *, teacher_view=None):
    counts = dict.fromkeys(AREA_KEYS, 0)
    announcements = set()
    for notice in visible_course_notifications(course, user):
        area = notification_area(notice)
        if notice.status == Notification.Status.UNREAD and area in counts:
            if area == 'announcements':
                announcement_id = str(notice.event.metadata.get('announcement_id'))
                if announcement_id in announcements:
                    continue
                announcements.add(announcement_id)
            counts[area] += 1
    if teacher_view is not False and can_edit_course(user, course):
        counts['announcements'] = announcement_reading_state(course, user)['pending_students']
    return counts


def announcement_reading_state(course, user):
    if not can_edit_course(user, course):
        return {'pending_students': 0, 'announcements': {}}
    from .lifecycle import student_recipients

    students = student_recipients(course)
    announcements = {
        item.pk: item for item in Announcement.visible_for_course(course, user)
        if item.is_currently_published()
    }
    recipients = {pk: set() for pk in announcements}
    pending = {pk: set() for pk in announcements}
    notices = Notification.objects.filter(
        course=course, recipient__in=students,
        event__event_type=NotificationEvent.Type.ANNOUNCEMENT,
    ).select_related('event', 'recipient')
    for notice in notices:
        identifier = notice.event.metadata.get('announcement_id')
        if not str(identifier).isdecimal():
            continue
        identifier = int(identifier)
        announcement = announcements.get(identifier)
        if not announcement or not announcement.is_visible_to_user(notice.recipient):
            continue
        recipients[identifier].add(notice.recipient_id)
        if notice.status == Notification.Status.UNREAD:
            pending[identifier].add(notice.recipient_id)
    return {
        'pending_students': len(set().union(*pending.values())),
        'announcements': {
            str(pk): {
                'pending': len(pending[pk]), 'read': len(recipients[pk] - pending[pk]),
                'total': len(recipients[pk]),
            }
            for pk in announcements
        },
    }


def notification_feed(user, *, course=None, after=None, teacher_view=None):
    from apps.users.notifications import platform_notifications

    events = platform_notifications(user)
    if after is None:
        rows = []
        cursor = events.order_by('-pk').values_list('pk', flat=True).first() or 0
    else:
        cursor = after
        rows = list(events.filter(pk__gt=cursor).order_by('pk')[:20])
        if rows:
            cursor = rows[-1].pk
    return {
        'cursor': cursor,
        'count': events.filter(leida=False).count(),
        'total_count': events.count(),
        'has_more': len(rows) == 20,
        'unread_ids': list(events.filter(leida=False).values_list('pk', flat=True)),
        'course_counts': notification_counts(course, user, teacher_view=teacher_view) if course else {},
        'announcement_reading': announcement_reading_state(course, user) if course and teacher_view is not False else None,
        'notifications': [
            {
                'id': row.pk, 'title': row.titulo, 'message': row.mensaje,
                'url': notification_target(row.lms_notification) if row.lms_notification_id else reverse('users:notificaciones'),
                'read_url': reverse('users:marcar_leida', kwargs={'pk': row.pk}),
                'course_id': row.lms_notification.course_id if row.lms_notification_id else None,
            }
            for row in rows if not row.leida
        ],
    }


def mark_item_notifications(course, user, *, target='', event_id=None, area=''):
    notices = visible_course_notifications(course, user)
    ids = []
    for notice in notices:
        if notice.status != Notification.Status.UNREAD:
            continue
        metadata = notice.event.metadata
        if event_id is not None:
            matches = notice.event_id == event_id and metadata.get('source') == 'people'
        else:
            try:
                match = resolve(urlsplit(target).path)
            except Resolver404:
                return 0
            matches = (
                match.namespace == 'lms' and match.kwargs.get('course_id') == course.pk
                and match.url_name in ('quiz_detail', 'course_activity_detail', 'course_page_detail')
                and (
                    urlsplit(notification_target(notice)).path == urlsplit(target).path
                    or (
                        match.url_name == 'quiz_detail'
                        and metadata.get('kind') == 'quiz_feedback'
                        and metadata.get('quiz_id') == match.kwargs['quiz_id']
                    )
                )
                and (not area or notification_area(notice) == area)
            )
        if matches:
            ids.append(notice.pk)
    from apps.users.notifications import mark_lms_notifications_read
    return mark_lms_notifications_read(Notification.objects.filter(pk__in=ids, recipient=user, course=course))
