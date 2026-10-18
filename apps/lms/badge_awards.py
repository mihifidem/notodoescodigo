from django.utils.translation import gettext as _

from apps.courses.models import InscripcionCurso
from .models import Badge, CourseMembership, UserBadge
from .policies import can_edit_course, is_course_member


def award_module_badges(course, user):
    from .module_progression import module_complete
    if can_edit_course(user, course) or not is_course_member(user, course):
        return
    completed = {}
    for badge in Badge.objects.filter(course=course, module__isnull=False, is_active=True).select_related('module'):
        if badge.module_id not in completed:
            completed[badge.module_id] = module_complete(badge.module, user)
        if completed[badge.module_id]:
            UserBadge.objects.get_or_create(
                user=user, badge=badge, course=course,
                defaults={'reason': _('Tema completado: %(title)s') % {'title': badge.module.titulo}},
            )


def award_module_badges_for_students(course):
    for membership in CourseMembership.objects.filter(
        course=course, role=CourseMembership.Role.STUDENT,
        status__in=[CourseMembership.Status.ACTIVE, CourseMembership.Status.COMPLETED],
    ).select_related('user'):
        award_module_badges(course, membership.user)
    for enrollment in InscripcionCurso.objects.filter(curso=course).exclude(
        usuario_id__in=CourseMembership.objects.filter(course=course).values('user_id'),
    ).select_related('usuario'):
        award_module_badges(course, enrollment.usuario)
