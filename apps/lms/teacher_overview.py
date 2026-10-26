"""Vista global del espacio docente: datos de todas las aulas que imparte el usuario."""
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Announcement, CourseMembership, LearningActivity, Notification, Quiz

STUDENT_ORDERING = {
    'name': ('user__first_name', 'user__last_name', 'user__username'),
    '-name': ('-user__first_name', '-user__last_name', '-user__username'),
    'course': ('course__nombre', 'user__username'),
    '-course': ('-course__nombre', 'user__username'),
    'joined': ('joined_at', 'created_at'),
    '-joined': ('-joined_at', '-created_at'),
    'status': ('status', 'user__username'),
}
STUDENT_ORDER_LABELS = [
    ('name', _('Nombre (A-Z)')),
    ('-name', _('Nombre (Z-A)')),
    ('course', _('Curso (A-Z)')),
    ('-course', _('Curso (Z-A)')),
    ('-joined', _('Inscripción más reciente')),
    ('joined', _('Inscripción más antigua')),
    ('status', _('Estado')),
]
STUDENT_STATUSES = [
    CourseMembership.Status.ACTIVE,
    CourseMembership.Status.INVITED,
    CourseMembership.Status.SUSPENDED,
    CourseMembership.Status.COMPLETED,
    CourseMembership.Status.DROPPED,
]
STUDENTS_PER_PAGE = 20


def _closed_q(now):
    return Q(course__lms_settings__archived=True) | Q(course__lms_settings__ends_at__lte=now)


def course_lists(memberships, now):
    student_counts = dict(
        CourseMembership.objects.filter(
            course_id__in=memberships.values('course_id'),
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        ).values('course_id').annotate(total=Count('pk')).values_list('course_id', 'total')
    )

    def rows(queryset):
        result = []
        for membership in queryset.select_related('course', 'course__lms_settings'):
            course = membership.course
            settings = getattr(course, 'lms_settings', None)
            if settings and settings.archived:
                state, label = 'archived', _('Archivado')
            elif settings and settings.ends_at and settings.ends_at <= now:
                state, label = 'ended', _('Finalizado')
            elif course.activo:
                state, label = 'published', _('Publicado')
            else:
                state, label = 'draft', _('Borrador')
            result.append({
                'course': course,
                'role': membership.get_role_display(),
                'state': state,
                'state_label': label,
                'starts_at': settings.starts_at if settings else None,
                'ends_at': settings.ends_at if settings else None,
                'students': student_counts.get(course.pk, 0),
                'url': reverse('lms:course_detail', kwargs={'course_id': course.pk}),
                'dashboard_url': f"{reverse('lms:teacher_dashboard')}?course={course.pk}",
            })
        return result

    closed = _closed_q(now)
    return rows(memberships.exclude(closed)), rows(memberships.filter(closed))


def open_evaluations(course_ids, now):
    quizzes = Quiz.objects.filter(
        course_id__in=course_ids, status=Quiz.Status.PUBLISHED,
    ).filter(
        Q(available_from__isnull=True) | Q(available_from__lte=now),
    ).filter(
        Q(available_until__isnull=True) | Q(available_until__gt=now),
    ).select_related('course')
    activities = LearningActivity.objects.filter(
        course_id__in=course_ids, status=LearningActivity.Status.OPEN,
    ).filter(
        Q(starts_at__isnull=True) | Q(starts_at__lte=now),
    ).select_related('course', 'module')
    items = [
        {
            'kind': 'quiz',
            'kind_label': _('Prueba'),
            'title': quiz.title,
            'course': quiz.course,
            'closes_at': quiz.available_until or quiz.due_at,
            'url': reverse('lms:quiz_detail', kwargs={'course_id': quiz.course_id, 'quiz_id': quiz.pk}),
        }
        for quiz in quizzes
    ] + [
        {
            'kind': 'activity',
            'kind_label': _('Actividad'),
            'title': activity.title,
            'course': activity.course,
            'closes_at': activity.due_at,
            'url': reverse(
                'lms:course_activity_detail',
                kwargs={'course_id': activity.course_id, 'activity_id': activity.pk},
            ),
        }
        for activity in activities
    ]
    far_future = now.replace(year=now.year + 100)
    items.sort(key=lambda item: (item['closes_at'] or far_future, str(item['title']).lower()))
    return items


def student_directory(course_ids, params):
    """Alumnos de todas las aulas del docente con filtro, búsqueda, orden y paginación."""
    query = (params.get('q') or '').strip()
    course_filter = params.get('s_course') or ''
    status_filter = params.get('s_status') or ''
    order = params.get('order') or 'name'
    if order not in STUDENT_ORDERING:
        order = 'name'

    students = CourseMembership.objects.filter(
        course_id__in=course_ids, role=CourseMembership.Role.STUDENT,
    ).select_related('user', 'course', 'section')
    total = students.count()
    if course_filter.isdigit() and int(course_filter) in course_ids:
        students = students.filter(course_id=int(course_filter))
    else:
        course_filter = ''
    if status_filter in STUDENT_STATUSES:
        students = students.filter(status=status_filter)
    else:
        status_filter = ''
    if query:
        students = students.filter(
            Q(user__username__icontains=query)
            | Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
            | Q(user__email__icontains=query)
        )
    students = students.order_by(*STUDENT_ORDERING[order], 'pk')
    page = Paginator(students, STUDENTS_PER_PAGE).get_page(params.get('page'))

    keep = params.copy()
    keep.pop('page', None)
    return {
        'page': page,
        'total': total,
        'filtered': page.paginator.count,
        'unique_students': CourseMembership.objects.filter(
            course_id__in=course_ids, role=CourseMembership.Role.STUDENT,
        ).values('user_id').distinct().count(),
        'q': query,
        'course': course_filter,
        'status': status_filter,
        'order': order,
        'order_choices': STUDENT_ORDER_LABELS,
        'status_choices': [(value, CourseMembership.Status(value).label) for value in STUDENT_STATUSES],
        'is_filtered': bool(query or course_filter or status_filter),
        'querystring': keep.urlencode(),
    }


def pending_notices(user, course_ids, now):
    announcements = Announcement.objects.filter(course_id__in=course_ids).filter(
        Q(status=Announcement.Status.DRAFT)
        | Q(status=Announcement.Status.SCHEDULED)
        | Q(status=Announcement.Status.PUBLISHED, publish_at__gt=now)
    ).select_related('course').order_by('publish_at', '-updated_at')
    unread = Notification.objects.filter(
        recipient=user, status=Notification.Status.UNREAD,
    ).select_related('course').order_by('-created_at')
    return {
        'announcements': [
            {
                'title': announcement.title,
                'course': announcement.course,
                'state': 'draft' if announcement.status == Announcement.Status.DRAFT else 'scheduled',
                'state_label': (
                    _('Borrador') if announcement.status == Announcement.Status.DRAFT else _('Programado')
                ),
                'publish_at': announcement.publish_at,
                'url': reverse(
                    'lms:course_announcement_edit',
                    kwargs={'course_id': announcement.course_id, 'announcement_id': announcement.pk},
                ),
            }
            for announcement in announcements[:10]
        ],
        'announcements_total': announcements.count(),
        'notifications': list(unread[:10]),
        'notifications_total': unread.count(),
    }


def teacher_overview(user, memberships, params, now=None):
    now = now or timezone.now()
    course_ids = list(memberships.values_list('course_id', flat=True))
    open_courses, closed_courses = course_lists(memberships, now)
    notices = pending_notices(user, course_ids, now)
    return {
        'open_courses': open_courses,
        'closed_courses': closed_courses,
        'open_evaluations': open_evaluations(course_ids, now),
        'students': student_directory(course_ids, params),
        'notices': notices,
        'notices_total': notices['announcements_total'] + notices['notifications_total'],
    }
