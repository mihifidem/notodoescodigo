from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Notificacion


def broadcast_reading_changes(notices):
    from apps.courses.models import Curso
    from apps.lms.consumers import broadcast_course_update

    course_ids = list(notices.exclude(course__isnull=True).values_list('course_id', flat=True).distinct())
    for course in Curso.objects.filter(pk__in=course_ids):
        transaction.on_commit(lambda course=course: broadcast_course_update(
            course, 'updated', item_type='notification_read',
        ))


def platform_notifications(user):
    from apps.courses.models import Curso
    from apps.lms.notification_state import visible_course_notifications
    from apps.lms.models import Notification

    course_ids = Notification.objects.filter(recipient=user).values_list('course_id', flat=True).distinct()
    visible_ids = list(Notification.objects.filter(
        recipient=user, course__isnull=True,
    ).values_list('pk', flat=True))
    for course in Curso.objects.filter(pk__in=course_ids):
        visible_ids.extend(notice.pk for notice in visible_course_notifications(course, user))
    return Notificacion.objects.filter(usuario=user).filter(
        Q(lms_notification__isnull=True) | Q(lms_notification_id__in=visible_ids),
    ).select_related('lms_notification__event', 'lms_notification__course')


@transaction.atomic
def mark_lms_notifications_read(notices):
    from apps.lms.models import Notification

    ids = list(notices.values_list('pk', flat=True))
    changed = Notification.objects.filter(pk__in=ids).update(
        status=Notification.Status.READ, read_at=timezone.now(),
    )
    Notificacion.objects.filter(lms_notification_id__in=ids).update(leida=True)
    broadcast_reading_changes(Notification.objects.filter(pk__in=ids))
    return changed


@transaction.atomic
def set_platform_read(notices, *, read):
    from apps.lms.models import Notification

    ids = list(notices.values_list('pk', flat=True))
    lms_ids = list(Notificacion.objects.filter(pk__in=ids).exclude(
        lms_notification__isnull=True,
    ).values_list('lms_notification_id', flat=True))
    Notificacion.objects.filter(pk__in=ids).update(leida=read)
    Notification.objects.filter(pk__in=lms_ids).update(
        status=Notification.Status.READ if read else Notification.Status.UNREAD,
        read_at=timezone.now() if read else None,
    )
    broadcast_reading_changes(Notification.objects.filter(pk__in=lms_ids))
