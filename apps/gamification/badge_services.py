import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.users.access import has_access_role, is_platform_admin
from apps.users.models import AccessRoleAssignment, OrganizationMembership

from .models import Insignia, InsigniaAuditEvent, InsigniaUsuario, InsigniaVersion

logger = logging.getLogger(__name__)

PROGRESS_PENDING = 'pending'
PROGRESS_IN_PROGRESS = 'in_progress'
PROGRESS_EARNED = 'earned'
PROGRESS_ELIGIBLE = 'eligible'
PROGRESS_NOT_AVAILABLE = 'not_available'
PROGRESS_NOT_CALCULABLE = 'not_calculable'


def current_badge_version(badge, *, at=None):
    at = at or timezone.now()
    return badge.versions.filter(effective_at__lte=at).order_by('-version').first()


def is_badge_manager(actor, badge=None, organization=None):
    if not getattr(actor, 'is_authenticated', False) or not actor.is_active:
        return False
    if is_platform_admin(actor):
        return True
    organization = organization or getattr(badge, 'organization', None)
    if organization is None:
        return False
    return has_access_role(actor, AccessRoleAssignment.Role.ACADEMIA, organization=organization)


def _rule_progress(user, version):
    from apps.courses.models import InscripcionCurso, Progreso, ResultadoQuiz

    rule = version.RuleType
    if version.rule_type == rule.COURSE_COMPLETED:
        enrollment = InscripcionCurso.objects.filter(
            usuario=user,
            curso_id=version.course_id,
            completado=True,
        ).order_by('-fecha_completado').first()
        return {
            'current': 1 if enrollment else 0,
            'target': 1,
            'earned_at': enrollment.fecha_completado if enrollment else None,
            'action_url': reverse('courses:detail', args=[version.course_id]),
        }
    if version.rule_type == rule.TOPIC_COMPLETED:
        progress = Progreso.objects.filter(
            usuario=user,
            tema_id=version.topic_id,
            completado=True,
        ).order_by('-fecha_completado').first()
        return {
            'current': 1 if progress else 0,
            'target': 1,
            'earned_at': progress.fecha_completado if progress else None,
            'action_url': reverse('courses:detail', args=[version.topic.curso_id]),
        }
    if version.rule_type == rule.QUIZ_PASSED:
        results = ResultadoQuiz.objects.filter(
            usuario=user,
            quiz_id=version.quiz_id,
        ).order_by('-fecha')
        passing_threshold = max(version.threshold, version.quiz.porcentaje_aprobacion)
        result = next((item for item in results if item.aprobado and item.porcentaje >= passing_threshold), None)
        return {
            'current': 1 if result else 0,
            'target': 1,
            'earned_at': result.fecha if result else None,
            'action_url': reverse('courses:detail', args=[version.quiz.tema.curso_id]),
        }
    if version.rule_type == rule.TOPICS_COUNT:
        current = Progreso.objects.filter(usuario=user, completado=True).count()
        return {
            'current': current,
            'target': version.threshold,
            'earned_at': None,
            'action_url': reverse('courses:list'),
        }
    if version.rule_type == rule.STREAK_DAYS:
        return {
            'current': user.streak,
            'target': version.threshold,
            'earned_at': None,
            'action_url': reverse('users:dashboard'),
        }
    if version.rule_type == rule.TOTAL_POINTS:
        return {
            'current': user.puntos_totales,
            'target': version.threshold,
            'earned_at': None,
            'action_url': reverse('gamification:como_ganar_puntos'),
        }
    return None


def badge_progress(user, badge):
    award = InsigniaUsuario.objects.filter(
        usuario=user,
        insignia=badge,
        status=InsigniaUsuario.Status.VALID,
    ).select_related('version').first()
    if award:
        return {
            'status': PROGRESS_EARNED,
            'current': None,
            'target': None,
            'percent': None,
            'version': award.version,
            'award': award,
            'action_url': None,
        }

    version = current_badge_version(badge)
    if version is None:
        return {
            'status': PROGRESS_NOT_CALCULABLE,
            'current': None,
            'target': None,
            'percent': None,
            'version': None,
            'award': None,
            'action_url': None,
        }
    try:
        progress = _rule_progress(user, version)
    except (AttributeError, ValueError):
        logger.exception('Badge %s progress could not be calculated', badge.pk)
        progress = None
    if progress is None:
        return {
            'status': PROGRESS_NOT_AVAILABLE,
            'current': None,
            'target': None,
            'percent': None,
            'version': version,
            'award': None,
            'action_url': None,
        }

    binary = version.rule_type in {
        InsigniaVersion.RuleType.COURSE_COMPLETED,
        InsigniaVersion.RuleType.TOPIC_COMPLETED,
        InsigniaVersion.RuleType.QUIZ_PASSED,
    }
    earned = progress['current'] >= progress['target']
    return {
        'status': (
            PROGRESS_ELIGIBLE if earned
            else PROGRESS_IN_PROGRESS if progress['current']
            else PROGRESS_PENDING
        ),
        'current': None if binary else progress['current'],
        'target': None if binary else progress['target'],
        'percent': None if binary or not progress['target'] else min(
            100,
            int(progress['current'] / progress['target'] * 100),
        ),
        'version': version,
        'award': None,
        'action_url': progress['action_url'],
        'eligible': earned,
        'earned_at': progress['earned_at'],
    }


@transaction.atomic
def grant_if_eligible(user, badge, *, evidence_type='', evidence_id='', achieved_at=None):
    badge = Insignia.objects.select_for_update().get(pk=badge.pk)
    if badge.status != Insignia.Status.PUBLISHED or badge.issue_method != Insignia.IssueMethod.AUTOMATIC:
        return None
    version = current_badge_version(badge)
    if version is None:
        return None
    progress = _rule_progress(user, version)
    if progress['current'] < progress['target']:
        return None
    award, created = InsigniaUsuario.objects.get_or_create(
        usuario=user,
        insignia=badge,
        defaults={
            'version': version,
            'achieved_at': achieved_at or progress['earned_at'],
            'origin': InsigniaUsuario.Origin.AUTOMATIC,
            'evidence_type': evidence_type,
            'evidence_id': str(evidence_id)[:80],
            'issuer_name': badge.issuer_name,
            'reason': version.public_requirements,
            'status': InsigniaUsuario.Status.VALID,
            'is_public': False,
        },
    )
    if not created:
        return award
    InsigniaAuditEvent.objects.create(
        actor=None,
        badge=badge,
        award=award,
        action=InsigniaAuditEvent.Action.GRANTED,
        reason='Concesión automática tras verificar la fuente del servidor.',
        metadata={
            'origin': award.origin,
            'evidence_type': award.evidence_type,
            'evidence_id': award.evidence_id,
            'version': version.version,
        },
    )
    transaction.on_commit(lambda: _notify_award(user.pk, badge.pk))
    return award


def _notify_award(user_id, badge_id):
    from django.contrib.auth import get_user_model
    from apps.analytics.models import RegistroActividad
    from apps.users.models import Notificacion

    user = get_user_model().objects.filter(pk=user_id).first()
    badge = Insignia.objects.filter(pk=badge_id).first()
    if not user or not badge:
        return
    RegistroActividad.objects.get_or_create(
        usuario=user,
        tipo='insignia_obtenida',
        descripcion=f'Obtuvo la insignia: {badge.nombre}',
    )
    Notificacion.objects.get_or_create(
        usuario=user,
        tipo='insignia',
        titulo='¡Insignia desbloqueada!',
        mensaje=f'Has obtenido la insignia «{badge.nombre}».',
    )


def award_verified_topic(user, topic):
    from apps.courses.models import Progreso

    progress = Progreso.objects.filter(
        usuario=user,
        tema=topic,
        completado=True,
    ).order_by('-fecha_completado').first()
    if progress is None:
        return []
    badges = Insignia.objects.filter(
        status=Insignia.Status.PUBLISHED,
        issue_method=Insignia.IssueMethod.AUTOMATIC,
        versions__rule_type=InsigniaVersion.RuleType.TOPIC_COMPLETED,
        versions__topic=topic,
        versions__effective_at__lte=timezone.now(),
    ).distinct()
    return [
        award
        for badge in badges
        if (award := grant_if_eligible(
            user,
            badge,
            evidence_type='courses.Progreso',
            evidence_id=progress.pk,
            achieved_at=progress.fecha_completado,
        ))
    ]


def award_verified_course(user, course):
    from apps.courses.models import InscripcionCurso

    enrollment = InscripcionCurso.objects.filter(
        usuario=user,
        curso=course,
        completado=True,
    ).order_by('-fecha_completado').first()
    if enrollment is None:
        return []
    badges = Insignia.objects.filter(
        status=Insignia.Status.PUBLISHED,
        issue_method=Insignia.IssueMethod.AUTOMATIC,
        versions__rule_type=InsigniaVersion.RuleType.COURSE_COMPLETED,
        versions__course=course,
        versions__effective_at__lte=timezone.now(),
    ).distinct()
    return [
        award
        for badge in badges
        if (award := grant_if_eligible(
            user,
            badge,
            evidence_type='courses.InscripcionCurso',
            evidence_id=enrollment.pk,
            achieved_at=enrollment.fecha_completado,
        ))
    ]


def award_verified_quiz(user, result):
    from apps.courses.models import ResultadoQuiz

    verified = ResultadoQuiz.objects.filter(
        pk=result.pk,
        usuario=user,
        aprobado=True,
    ).first()
    if verified is None:
        return []
    badges = Insignia.objects.filter(
        status=Insignia.Status.PUBLISHED,
        issue_method=Insignia.IssueMethod.AUTOMATIC,
        versions__rule_type=InsigniaVersion.RuleType.QUIZ_PASSED,
        versions__quiz=verified.quiz,
        versions__effective_at__lte=timezone.now(),
    ).distinct()
    return [
        award
        for badge in badges
        if (award := grant_if_eligible(
            user,
            badge,
            evidence_type='courses.ResultadoQuiz',
            evidence_id=verified.pk,
            achieved_at=verified.fecha,
        ))
    ]


def _organization_membership(user, organization):
    return OrganizationMembership.objects.filter(
        user=user,
        organization=organization,
        status=OrganizationMembership.Status.ACTIVE,
        organization__is_active=True,
    ).exists()


def can_grant_badge(actor, badge, recipient):
    if not actor.is_authenticated or not actor.is_active or actor.pk == recipient.pk:
        return False
    if badge.issue_method != Insignia.IssueMethod.MANUAL or badge.status != Insignia.Status.PUBLISHED:
        return False
    if is_platform_admin(actor):
        return True
    if badge.organization_id and is_badge_manager(actor, badge):
        return _organization_membership(recipient, badge.organization)

    version = current_badge_version(badge)
    if version is None:
        return False
    course = version.course
    if version.rule_type == InsigniaVersion.RuleType.TOPIC_COMPLETED and version.topic_id:
        course = version.topic.curso
    if course is None:
        return False
    from apps.lms.models import CourseMembership
    from apps.courses.models import InscripcionCurso

    teacher_membership = CourseMembership.objects.filter(
        user=actor,
        course=course,
        role__in=[
            CourseMembership.Role.OWNER,
            CourseMembership.Role.TEACHER,
            CourseMembership.Role.ASSISTANT,
        ],
        status=CourseMembership.Status.ACTIVE,
    ).exists()
    student_membership = CourseMembership.objects.filter(
        user=recipient,
        course=course,
        role=CourseMembership.Role.STUDENT,
        status__in=[CourseMembership.Status.ACTIVE, CourseMembership.Status.COMPLETED],
    ).exists() or InscripcionCurso.objects.filter(usuario=recipient, curso=course).exists()
    return teacher_membership and student_membership


@transaction.atomic
def grant_manual_badge(actor, recipient, badge, *, reason, evidence):
    if not reason.strip() or not evidence.strip():
        raise ValidationError('El motivo y la evidencia son obligatorios.')
    if not can_grant_badge(actor, badge, recipient):
        raise PermissionDenied('No tienes autorización para conceder esta insignia en este ámbito.')
    version = current_badge_version(badge)
    award, created = InsigniaUsuario.objects.get_or_create(
        usuario=recipient,
        insignia=badge,
        defaults={
            'version': version,
            'achieved_at': None,
            'origin': InsigniaUsuario.Origin.MANUAL,
            'evidence_type': 'manual',
            'evidence_id': '',
            'issuer_name': badge.issuer_name,
            'granted_by': actor,
            'reason': reason,
            'status': InsigniaUsuario.Status.VALID,
            'is_public': False,
        },
    )
    if not created:
        raise ValidationError('Ya existe una concesión para este usuario e insignia.')
    InsigniaAuditEvent.objects.create(
        actor=actor,
        badge=badge,
        award=award,
        action=InsigniaAuditEvent.Action.GRANTED,
        reason=reason,
        metadata={'origin': award.origin, 'evidence': evidence[:1000]},
    )
    transaction.on_commit(lambda: _notify_award(recipient.pk, badge.pk))
    return award


@transaction.atomic
def revoke_badge(actor, award, *, reason):
    if not reason.strip() or not is_badge_manager(actor, award.insignia):
        raise PermissionDenied('La revocación requiere un administrador autorizado y un motivo.')
    award = InsigniaUsuario.objects.select_for_update().get(pk=award.pk)
    if award.status != InsigniaUsuario.Status.VALID:
        raise ValidationError('Solo se puede revocar una concesión vigente.')
    award.status = InsigniaUsuario.Status.REVOKED
    award.revoked_at = timezone.now()
    award.revoked_by = actor
    award.revocation_reason = reason
    award.is_public = False
    award.save(update_fields=[
        'status', 'revoked_at', 'revoked_by', 'revocation_reason', 'is_public',
    ])
    InsigniaAuditEvent.objects.create(
        actor=actor,
        badge=award.insignia,
        award=award,
        action=InsigniaAuditEvent.Action.REVOKED,
        reason=reason,
    )
    return award


@transaction.atomic
def restore_badge(actor, award, *, reason):
    if not reason.strip() or not is_badge_manager(actor, award.insignia):
        raise PermissionDenied('La restauración requiere un responsable autorizado y un motivo.')
    award = InsigniaUsuario.objects.select_for_update().get(pk=award.pk)
    if award.status != InsigniaUsuario.Status.REVOKED:
        raise ValidationError('Solo se puede restaurar una concesión revocada.')
    award.status = InsigniaUsuario.Status.VALID
    award.revoked_at = None
    award.revoked_by = None
    award.revocation_reason = ''
    award.is_public = False
    award.save(update_fields=[
        'status', 'revoked_at', 'revoked_by', 'revocation_reason', 'is_public',
    ])
    InsigniaAuditEvent.objects.create(
        actor=actor,
        badge=award.insignia,
        award=award,
        action=InsigniaAuditEvent.Action.RESTORED,
        reason=reason,
    )
    return award


@transaction.atomic
def set_award_visibility(actor, award, *, is_public):
    if not actor.is_authenticated or award.usuario_id != actor.pk:
        raise PermissionDenied('Solo la persona propietaria puede cambiar la visibilidad.')
    if award.status != InsigniaUsuario.Status.VALID:
        raise ValidationError('Solo se puede publicar una insignia vigente.')
    if is_public and (
        award.insignia.status != Insignia.Status.PUBLISHED
        or not award.insignia.visible
    ):
        raise ValidationError('Esta insignia no está publicada para compartir.')
    if award.is_public == is_public:
        return award
    award.is_public = is_public
    award.save(update_fields=['is_public'])
    InsigniaAuditEvent.objects.create(
        actor=actor,
        badge=award.insignia,
        award=award,
        action=InsigniaAuditEvent.Action.VISIBILITY_CHANGED,
        reason='La persona propietaria cambió la visibilidad.',
        metadata={'is_public': is_public},
    )
    return award
