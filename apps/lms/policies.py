from django.db.models import Q

from apps.courses.models import CourseSettings, InscripcionCurso

from .models import CourseMembership, CourseSettingsLMS


TEACHING_ROLES = (
    CourseMembership.Role.OWNER,
    CourseMembership.Role.TEACHER,
    CourseMembership.Role.ASSISTANT,
)
ACTIVE_READ_STATUSES = (
    CourseMembership.Status.ACTIVE,
    CourseMembership.Status.SUSPENDED,
    CourseMembership.Status.COMPLETED,
)


def is_platform_admin(user):
    if not user.is_authenticated:
        return False
    return user.is_superuser or user.has_perm('lms.manage_platform')


def is_course_teacher(user, course):
    if not user.is_authenticated:
        return False
    return CourseMembership.objects.filter(
        user=user,
        course=course,
        role__in=TEACHING_ROLES,
        status=CourseMembership.Status.ACTIVE,
    ).exists()


def is_course_owner(user, course):
    if not user.is_authenticated:
        return False
    return CourseMembership.objects.filter(
        user=user,
        course=course,
        role=CourseMembership.Role.OWNER,
        status=CourseMembership.Status.ACTIVE,
    ).exists()


def can_edit_course(user, course):
    if not user.is_authenticated:
        return False
    return (
        is_platform_admin(user)
        or user.has_perm('courses.change_curso')
        or is_course_teacher(user, course)
    )


def can_delete_course(user):
    return user.is_authenticated and user.is_active and user.is_staff


def can_view_course(user, course):
    if not user.is_authenticated:
        return False
    if is_platform_admin(user) or course.activo:
        return True
    return is_course_member(user, course) or can_edit_course(user, course)


def can_enroll(user, course):
    if not user.is_authenticated or not course.activo:
        return False
    settings = getattr(course, 'lms_settings', None)
    publication_status = getattr(course, 'status', '') or ''
    if (
        publication_status != 'published'
        or not course.activo
        or course.matriculacion != 'open'
        or (settings is not None and (not settings.enrollment_open or settings.archived))
    ):
        return False
    return not CourseMembership.objects.filter(
        user=user,
        course=course,
    ).exists() and not InscripcionCurso.objects.filter(
        usuario=user,
        curso=course,
    ).exists()


def can_publish_course(user, course):
    if not user.is_authenticated:
        return False
    return (
        is_platform_admin(user)
        or user.has_perm('courses.change_curso')
        or is_course_owner(user, course)
        or (
            is_course_teacher(user, course)
            and user.has_perm('lms.publish_course')
        )
    )


def is_course_member(user, course):
    if not user.is_authenticated:
        return False
    status = CourseMembership.objects.filter(
        user=user,
        course=course,
    ).values_list('status', flat=True).first()
    if status is not None:
        return status in ACTIVE_READ_STATUSES
    return InscripcionCurso.objects.filter(
        usuario=user,
        curso=course,
    ).exists()


def legacy_student_enrollments(user):
    """Legacy rows are fallback only when no LMS membership exists."""
    return InscripcionCurso.objects.filter(usuario=user).exclude(
        curso_id__in=CourseMembership.objects.filter(user=user).values('course_id'),
    )


def course_evaluations(course, *, include_unpublished=False):
    from apps.evaluations.models import Evaluacion

    queryset = Evaluacion.objects.filter(
        Q(tema__curso=course)
        | Q(curso=course)
        | Q(cursos=course)
    ).distinct()
    if not include_unpublished:
        queryset = queryset.filter(estado=Evaluacion.ESTADO_PUBLICADA)
    return queryset
