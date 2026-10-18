import csv
import io
import logging
import re
import threading
import time
from abc import ABC, abstractmethod
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, transaction
from django.urls import reverse
from django.utils.crypto import get_random_string
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.courses.models import InscripcionCurso, Tema

from .models import (
    AchievementRule,
    AuditEvent,
    Badge,
    CalendarEvent,
    CertificateVerification,
    CodeExercise,
    CodeExecutionResult,
    CodeSubmission,
    Cohort,
    CourseCertificate,
    CourseInvitation,
    CourseMembership,
    CourseModule,
    CourseSection,
    CourseSettingsLMS,
    DeliveryLog,
    LearningActivity,
    ModuleItem,
    ModuleProgress,
    Notification,
    NotificationEvent,
    NotificationOutbox,
    NotificationPreference,
    Page,
    QuizAttempt,
    Section,
    Submission,
    UserBadge,
    UserProfileLMS,
)
from .policies import can_edit_course, can_enroll, can_publish_course

logger = logging.getLogger(__name__)


def _notification_channel_allowed(preference, channel):
    map_by_channel = {
        'in_app': getattr(preference, 'in_app_enabled', True),
        'email': getattr(preference, 'email_enabled', True),
        'push': getattr(preference, 'push_enabled', False),
        'webhook': getattr(preference, 'webhook_enabled', False),
    }
    return bool(map_by_channel.get(channel, False))


@transaction.atomic
def dispatch_notification_outbox(*, outbox_id=None, channel=None, recipient=None):
    if outbox_id is not None:
        outbox = NotificationOutbox.objects.select_related('event', 'recipient').get(pk=outbox_id)
    else:
        qs = NotificationOutbox.objects.select_related('event', 'recipient').filter(status=NotificationOutbox.Status.PENDING)
        if channel is not None:
            qs = qs.filter(channel=channel)
        if recipient is not None:
            qs = qs.filter(recipient=recipient)
        outbox = qs.order_by('scheduled_for').first()
    if outbox is None:
        return None

    if outbox.attempt_count >= 3:
        outbox.status = NotificationOutbox.Status.FAILED
        outbox.save(update_fields=['status', 'updated_at'])
        return outbox

    outbox.attempt_count += 1
    outbox.save(update_fields=['attempt_count', 'updated_at'])

    sending = {
        'channel': outbox.channel,
        'recipient_id': outbox.recipient_id,
        'payload': {'title': outbox.event.title, 'message': outbox.event.message, 'metadata': outbox.event.metadata},
    }
    log_key = f'{outbox.event_id}:{outbox.recipient_id}:{outbox.channel}:{outbox.attempt_count}'
    if DeliveryLog.objects.filter(dedupe_key=log_key).exists():
        outbox.status = NotificationOutbox.Status.SENT
        outbox.sent_at = timezone.now()
        outbox.save(update_fields=['status', 'sent_at', 'updated_at'])
        return outbox

    provider = 'in_process'
    if outbox.channel == NotificationOutbox.Channel.EMAIL:
        provider = 'django_email'
    elif outbox.channel == NotificationOutbox.Channel.PUSH:
        provider = 'push_service'
    elif outbox.channel == NotificationOutbox.Channel.WEBHOOK:
        provider = 'webhook_dispatcher'

    DeliveryLog.objects.get_or_create(
        outbox=outbox,
        channel=outbox.channel,
        dedupe_key=log_key,
        defaults={
            'status': NotificationOutbox.Status.SENT,
            'provider': provider,
            'payload': sending,
            'sent_at': timezone.now(),
        },
    )
    outbox.status = NotificationOutbox.Status.SENT
    outbox.sent_at = timezone.now()
    outbox.save(update_fields=['status', 'sent_at', 'updated_at'])
    return outbox


def _notification_preference_for(user, course=None):
    if course is not None:
        preference = NotificationPreference.objects.filter(user=user, course=course).first()
        if preference is not None:
            return preference
    return NotificationPreference.objects.filter(user=user, course__isnull=True).first() or NotificationPreference(
        user=user,
        course=course,
        in_app_enabled=True,
        email_enabled=True,
        push_enabled=False,
        webhook_enabled=False,
    )


@transaction.atomic
def process_course_announcements(course, *, now=None):
    from django.db.models import Q

    from .models import Announcement

    now = now or timezone.now()
    Announcement.objects.filter(
        course=course,
        status__in=(Announcement.Status.PUBLISHED, Announcement.Status.SCHEDULED),
        expire_at__lte=now,
    ).update(status=Announcement.Status.EXPIRED, updated_at=now)
    due = Announcement.objects.select_for_update().filter(
        course=course,
        status=Announcement.Status.SCHEDULED,
        publish_at__lte=now,
    ).filter(Q(expire_at__isnull=True) | Q(expire_at__gt=now)).select_related('author')
    for announcement in due:
        announcement.status = Announcement.Status.PUBLISHED
        announcement.save(update_fields=['status', 'updated_at'])
        notify_announcement_published(announcement, announcement.author)


def notify_announcement_published(announcement, actor):
    from django.db.models import Q

    course = announcement.course
    memberships = CourseMembership.objects.filter(course=course)
    active_user_ids = memberships.filter(
        status__in=(CourseMembership.Status.ACTIVE, CourseMembership.Status.COMPLETED),
    ).values('user_id')
    legacy_user_ids = InscripcionCurso.objects.filter(curso=course).exclude(
        usuario_id__in=memberships.values('user_id'),
    ).values('usuario_id')
    recipients = get_user_model().objects.filter(
        Q(pk__in=active_user_ids) | Q(pk__in=legacy_user_ids),
    )
    return queue_notification_event(
        event_type=NotificationEvent.Type.ANNOUNCEMENT,
        actor=actor,
        course=course,
        recipients=recipients,
        title=_('Nuevo anuncio: %(title)s') % {'title': announcement.title},
        message=_('%(teacher)s ha publicado un nuevo anuncio en %(course)s.') % {
            'teacher': (actor.get_full_name() or actor.username) if actor else _('El profesor'),
            'course': course.nombre,
        },
        metadata={
            'announcement_id': announcement.pk,
            'announcement_title': announcement.title,
            'course_id': course.pk,
            'url': reverse('lms:course_area', kwargs={'course_id': course.pk, 'area': 'announcements'}),
        },
        dedupe_key=f'announcement:{course.pk}:{announcement.pk}',
    )


def notify_forum_activity(post, actor, *, action='reply'):
    from django.db.models import Q
    from django.utils.html import strip_tags

    forum = post.topic
    root = post
    while root.parent_id:
        root = root.parent
    memberships = CourseMembership.objects.filter(course=forum.course)
    active_ids = memberships.filter(
        status__in=(CourseMembership.Status.ACTIVE, CourseMembership.Status.COMPLETED),
    ).values('user_id')
    legacy_ids = InscripcionCurso.objects.filter(curso=forum.course).exclude(
        usuario_id__in=memberships.values('user_id'),
    ).values('usuario_id')
    recipients = [
        user for user in get_user_model().objects.filter(Q(pk__in=active_ids) | Q(pk__in=legacy_ids)).exclude(pk=actor.pk)
        if forum.is_visible_to_user(user)
    ]
    if not recipients:
        return None
    labels = {
        'query': _('Nueva consulta'),
        'reply': _('Nueva respuesta'),
        'solution': _('Consulta resuelta'),
    }
    return queue_notification_event(
        event_type=NotificationEvent.Type.NOTE,
        actor=actor, course=forum.course, recipients=recipients,
        title=f'{labels[action]}: {root.title or forum.title}'[:200],
        message=f'{actor.get_full_name() or actor.username}: ' + (
            str(_('Hay una nueva respuesta en esta consulta.'))
            if forum.require_reply_before_view and action != 'query'
            else strip_tags(post.content)[:180]
        ),
        metadata={
            'forum_id': forum.pk, 'root_id': root.pk, 'post_id': post.pk,
            'forum_action': action,
            'url': reverse('lms:course_forum_query', kwargs={
                'course_id': forum.course_id, 'forum_id': forum.pk, 'post_id': root.pk,
            }) + f'#forum-post-{post.pk}',
        },
        dedupe_key=f'forum:{action}:{root.pk}:{post.pk}',
    )


@transaction.atomic
def queue_notification_event(*, event_type, actor=None, course=None, title, message, target_user=None, recipients=None, metadata=None, dedupe_key=None):
    metadata = metadata or {}
    course_id = course.pk if course else 'global'
    target_user_id = target_user.pk if target_user else 'all'
    if not dedupe_key:
        dedupe_key = f'{event_type}:{course_id}:{target_user_id}:{title}:{message}'
    event, created = NotificationEvent.objects.get_or_create(
        dedupe_key=dedupe_key,
        defaults={
            'course': course,
            'actor': actor,
            'target_user': target_user,
            'event_type': event_type,
            'title': title,
            'message': message,
            'metadata': metadata,
        },
    )
    if not created:
        event.title = title
        event.message = message
        event.metadata = metadata
        event.actor = actor
        event.course = course
        event.target_user = target_user
        event.save(update_fields=['title', 'message', 'metadata', 'actor', 'course', 'target_user', 'processed_at'])

    target_recipients = list(recipients or [])
    if target_user is not None:
        target_recipients.append(target_user)
    if not target_recipients and course is not None:
        target_recipients = list(
            get_user_model().objects.filter(
                course_memberships__course=course,
                course_memberships__status__in=(CourseMembership.Status.ACTIVE, CourseMembership.Status.COMPLETED),
            ).distinct()
        )

    unique_recipients = []
    seen = set()
    for recipient in target_recipients:
        if recipient.pk in seen:
            continue
        seen.add(recipient.pk)
        unique_recipients.append(recipient)

    for recipient in unique_recipients:
        preference = _notification_preference_for(recipient, course)
        notification_defaults = {'course': course, 'title': title, 'message': message, 'payload': metadata}
        notification, _ = Notification.objects.get_or_create(
            event=event,
            recipient=recipient,
            defaults=notification_defaults,
        )
        from apps.users.models import Notificacion
        platform, _ = Notificacion.objects.get_or_create(
            lms_notification=notification,
            defaults={
                'usuario': recipient, 'tipo': 'sistema',
                'titulo': title, 'mensaje': message,
                'leida': notification.status == Notification.Status.READ,
            },
        )
        if platform.titulo != title or platform.mensaje != message:
            platform.titulo = title
            platform.mensaje = message
            platform.save(update_fields=['titulo', 'mensaje'])
        if notification.title != title or notification.message != message:
            notification.title = title
            notification.message = message
            notification.payload = metadata
            notification.course = course
            notification.save(update_fields=['title', 'message', 'payload', 'course', 'updated_at'])

        for channel in ('in_app', 'email', 'push', 'webhook'):
            if channel == 'in_app' and not preference.in_app_enabled:
                continue
            if channel == 'email' and not preference.email_enabled:
                continue
            if channel == 'push' and not preference.push_enabled:
                continue
            if channel == 'webhook' and not preference.webhook_enabled:
                continue
            outbox_key = f'{event.pk}:{recipient.pk}:{channel}'
            NotificationOutbox.objects.get_or_create(
                event=event,
                recipient=recipient,
                channel=channel,
                defaults={'dedupe_key': outbox_key, 'scheduled_for': timezone.now()},
            )

    event.processed_at = timezone.now()
    event.save(update_fields=['processed_at'])
    if course is not None:
        from .consumers import broadcast_course_update
        transaction.on_commit(lambda: broadcast_course_update(course, 'updated', item_type='notifications'))
    return event


@transaction.atomic
def send_people_notification(*, course, actor, recipient_ids, title, message):
    from .selectors import course_participants, course_teachers

    if not course_teachers(course).filter(pk=actor.pk).exists():
        raise PermissionDenied
    students = {
        person['user'].pk: person['user'] for person in course_participants(course)
        if person['role_key'] == CourseMembership.Role.STUDENT
    }
    selected = set(recipient_ids)
    if not selected or not selected.issubset(students):
        raise ValidationError(_('Los destinatarios deben ser alumnos inscritos en este curso.'))
    recipients = [students[pk] for pk in sorted(selected)]
    event = queue_notification_event(
        event_type=NotificationEvent.Type.NOTE, actor=actor, course=course,
        title=title, message=message, recipients=recipients,
        metadata={'source': 'people', 'url': reverse('lms:course_area', kwargs={'course_id': course.pk, 'area': 'people'})},
        dedupe_key=f'people:{course.pk}:{get_random_string(32)}',
    )
    return event


@transaction.atomic
def delete_course(*, course_id, actor):
    from apps.courses.models import Curso
    from .models import QuestionAttempt, QuizQuestion
    from .policies import can_delete_course

    if not can_delete_course(actor):
        raise PermissionDenied(_('Solo el personal administrador puede eliminar cursos.'))
    course = Curso.objects.select_for_update().get(pk=course_id)
    metadata = {'course_action': 'deleted', 'course_id': course.pk, 'course_name': course.nombre}
    QuizQuestion.objects.filter(quiz__course=course).delete()
    QuestionAttempt.objects.filter(question__bank__course=course).delete()
    course.delete()
    AuditEvent.objects.create(actor=actor, action=AuditEvent.Action.COURSE_UPDATED, metadata=metadata)


class SandboxUnavailableError(RuntimeError):
    """Raised when a secure sandbox execution backend is not configured."""


class EnrollmentUnavailable(Exception):
    pass


class AIProviderConfigurationError(RuntimeError):
    """Raised when an AI provider is enabled but the required environment is missing."""


class AIRequestRejected(PermissionDenied):
    """Raised when the AI request is blocked for policy or budget reasons."""


class AIProvider(ABC):
    """Abstract base contract for optional LMS integrations with AI providers."""

    provider_name = 'abstract'

    def __init__(self, *, api_key='', model='', max_tokens=2000, max_monthly_cost=0.0):
        self.api_key = api_key
        self.model = model or getattr(settings, 'LMS_AI_MODEL', 'gpt-4o-mini')
        self.max_tokens = max_tokens or getattr(settings, 'LMS_AI_MAX_TOKENS', 2000)
        self.max_monthly_cost = float(max_monthly_cost or getattr(settings, 'LMS_AI_MAX_MONTHLY_COST', 0.0))

    @staticmethod
    def sanitize_text(value):
        if value is None:
            return ''
        text = str(value)
        patterns = [
            (
                r'(?i)((?:api[_ -]?key|secret|token|password|passphrase|client_secret|access_token))\s*[:=]\s*["\']?([A-Za-z0-9_\-\.]{3,})',
                r'\1=[REDACTED]',
            ),
            (
                r'(?i)\b(?:sk-[A-Za-z0-9_-]{6,}|ghp_[A-Za-z0-9_\-]{10,}|AIza[0-9A-Za-z\-_]{10,}|eyJ[A-Za-z0-9_\-\.]+\.[A-Za-z0-9_\-\.]+\.[A-Za-z0-9_\-\.]+)\b',
                '[REDACTED]',
            ),
        ]
        redacted = text
        for pattern, replacement in patterns:
            redacted = re.sub(pattern, replacement, redacted)
        return redacted

    def _assert_user_has_course_access(self, user, course):
        if user is None or not getattr(user, 'is_authenticated', False):
            raise PermissionDenied(_('Debes iniciar sesión para usar la IA del LMS.'))
        if course is None:
            raise ValueError(_('Se requiere un curso válido para usar la IA del LMS.'))

        membership = CourseMembership.objects.filter(user=user, course=course).first()
        if membership is None or membership.status != CourseMembership.Status.ACTIVE:
            raise PermissionDenied(_('No tienes acceso activo a este curso para usar la IA.'))
        return membership

    def _assert_allowed_course_scope(self, *, course, source_course_ids=None):
        if source_course_ids:
            invalid_course_ids = [course_id for course_id in source_course_ids if int(course_id) != int(course.pk)]
            if invalid_course_ids:
                raise PermissionDenied(_('La IA del curso solo puede consultar contenido del mismo curso.'))

    def _assert_approval(self, *, require_human_approval, approved_by):
        if require_human_approval and not approved_by:
            raise PermissionDenied(_('La publicación con IA requiere aprobación humana del profesor.'))

    def _assert_teacher_permissions(self, *, user, action, course):
        membership = self._assert_user_has_course_access(user, course)
        if action in {'draft_program', 'module_outline', 'summarize_page', 'exercise_suggestion', 'question_bank'}:
            if membership.role not in {
                CourseMembership.Role.OWNER,
                CourseMembership.Role.TEACHER,
                CourseMembership.Role.ASSISTANT,
            }:
                raise PermissionDenied(_('Solo el profesorado autorizado puede generar contenido docente con IA.'))
        return membership

    @abstractmethod
    def generate(self, *, user, course, action, prompt, source_course_ids=None, approved_by=None, require_human_approval=False, context=None):
        raise NotImplementedError


class DisabledAIProvider(AIProvider):
    provider_name = 'disabled'

    def generate(self, *, user, course, action, prompt, source_course_ids=None, approved_by=None, require_human_approval=False, context=None):
        raise AIRequestRejected(_('La IA del LMS está deshabilitada. Activa la feature flag y un proveedor real para usarla.'))


class OpenAICompatibleAIProvider(AIProvider):
    provider_name = 'openai'

    def __init__(self, *, api_key='', model='', max_tokens=2000, max_monthly_cost=0.0):
        super().__init__(api_key=api_key, model=model, max_tokens=max_tokens, max_monthly_cost=max_monthly_cost)

    def _estimate_cost(self, prompt):
        if not prompt:
            return 0.0
        tokens = max(1, len(str(prompt).split()))
        return round((tokens / 1000) * 0.01, 6)

    def generate(self, *, user, course, action, prompt, source_course_ids=None, approved_by=None, require_human_approval=False, context=None):
        if action in {'draft_program', 'module_outline', 'summarize_page', 'exercise_suggestion', 'question_bank'}:
            self._assert_teacher_permissions(user=user, action=action, course=course)
        else:
            self._assert_user_has_course_access(user, course)

        self._assert_allowed_course_scope(course=course, source_course_ids=source_course_ids)
        self._assert_approval(require_human_approval=require_human_approval, approved_by=approved_by)

        sanitized_prompt = self.sanitize_text(prompt)
        if self.max_tokens and len(str(sanitized_prompt).split()) > self.max_tokens:
            raise AIRequestRejected(_('La petición supera el máximo de tokens configurado para la IA.'))

        estimated_cost = self._estimate_cost(sanitized_prompt)
        if self.max_monthly_cost and estimated_cost > float(self.max_monthly_cost):
            raise AIRequestRejected(_('Se supera el límite de gasto mensual configurado para la IA.'))

        logger.info(
            'lms_ai_request user=%s course=%s action=%s provider=%s cost=%.6f',
            getattr(user, 'pk', None),
            getattr(course, 'pk', None),
            action,
            self.provider_name,
            estimated_cost,
        )

        response = {
            'provider': self.provider_name,
            'model': self.model,
            'course_id': course.pk if course else None,
            'action': action,
            'result': f'Respuesta preparada por {self.provider_name} para {action}.',
            'cost_estimate': estimated_cost,
            'review_required': bool(require_human_approval),
            'redacted': True,
            'content': sanitized_prompt,
        }
        if context:
            response['context'] = {key: self.sanitize_text(value) for key, value in context.items()}
        return response


def get_active_ai_provider():
    if not getattr(settings, 'LMS_ENABLE_AI', False):
        return DisabledAIProvider()

    provider = str(getattr(settings, 'LMS_AI_PROVIDER', 'disabled') or 'disabled').strip().lower()
    if provider in {'', 'disabled', 'none'}:
        return DisabledAIProvider()

    api_key = getattr(settings, 'LMS_AI_API_KEY', '') or ''
    if not api_key:
        raise AIProviderConfigurationError(_('El proveedor de IA está activado pero falta la clave API.'))

    if provider == 'openai':
        return OpenAICompatibleAIProvider(
            api_key=api_key,
            model=getattr(settings, 'LMS_AI_MODEL', 'gpt-4o-mini'),
            max_tokens=getattr(settings, 'LMS_AI_MAX_TOKENS', 2000),
            max_monthly_cost=getattr(settings, 'LMS_AI_MAX_MONTHLY_COST', 0.0),
        )

    raise AIProviderConfigurationError(_('Proveedor de IA no soportado: %(provider)s.') % {'provider': provider})


class CourseCompletionService:
    """Evaluate course completion rules using evidence-only metrics and issue verifiable certificates."""

    @staticmethod
    def evaluate_course_completion(user, course):
        if not user or not course:
            raise ValueError(_('Se requiere usuario y curso válidos.'))

        total_modules = CourseModule.objects.filter(course=course).count()
        completed_modules = ModuleProgress.objects.filter(
            user=user,
            course=course,
            status=ModuleProgress.Status.COMPLETED,
        ).count()
        module_completion = total_modules == 0 or completed_modules >= total_modules

        final_quiz_passed = QuizAttempt.objects.filter(
            quiz__course=course,
            user=user,
            is_passed=True,
            status__in=[QuizAttempt.Status.GRADED, QuizAttempt.Status.SUBMITTED],
        ).exists()

        assignment_submitted = Submission.objects.filter(
            assignment__course=course,
            user=user,
            status__in=[
                Submission.Status.SUBMITTED,
                Submission.Status.LATE,
                Submission.Status.GRADED,
                Submission.Status.RETURNED,
            ],
        ).exists()

        attendance_recorded = True
        try:
            from apps.attendance.models import Attendance
            attendance_recorded = Attendance.objects.filter(user=user, course=course).exists()
        except Exception:
            attendance_recorded = True

        metrics = {
            'module_completion': module_completion,
            'final_quiz_passed': final_quiz_passed,
            'assignment_submitted': assignment_submitted,
            'competency_mastery': True,
            'attendance_recorded': attendance_recorded,
        }

        required_competencies = []
        try:
            from .models import CourseCompetency, CompetencyAchievement
            required_competencies = list(
                CourseCompetency.objects.filter(course=course, is_required=True).values_list('competency_id', flat=True)
            )
            if required_competencies:
                metrics['competency_mastery'] = (
                    CompetencyAchievement.objects.filter(
                        course=course,
                        user=user,
                        competency_id__in=required_competencies,
                        is_achieved=True,
                    ).count() == len(required_competencies)
                )
        except Exception:
            metrics['competency_mastery'] = True

        is_complete = all([
            metrics['module_completion'],
            metrics['final_quiz_passed'],
            metrics['assignment_submitted'],
            metrics['competency_mastery'],
            metrics['attendance_recorded'],
        ])

        awarded_badges = []
        for badge in Badge.objects.filter(course=course, is_active=True, module__isnull=True).prefetch_related('rules').order_by('name'):
            rules = badge.rules.filter(is_active=True)
            if not rules.exists():
                continue
            if all(rule.matches(user, course, metrics=metrics) for rule in rules):
                user_badge, _created = UserBadge.objects.get_or_create(
                    user=user,
                    badge=badge,
                    course=course,
                    defaults={'reason': _('Curso completado con evidencias suficientes.')},
                )
                if user_badge.status != UserBadge.Status.AWARDED:
                    user_badge.status = UserBadge.Status.AWARDED
                    user_badge.reason = _('Curso completado con evidencias suficientes.')
                    user_badge.save(update_fields=['status', 'reason', 'updated_at'])
                awarded_badges.append(badge)

        return {
            'user_id': user.pk,
            'course_id': course.pk,
            'total_modules': total_modules,
            'completed_modules': completed_modules,
            'module_completion': metrics['module_completion'],
            'final_quiz_passed': metrics['final_quiz_passed'],
            'assignment_submitted': metrics['assignment_submitted'],
            'competency_mastery': metrics['competency_mastery'],
            'attendance_recorded': metrics['attendance_recorded'],
            'is_complete': is_complete,
            'awarded_badges': [badge.name for badge in awarded_badges],
        }

    @staticmethod
    def issue_certificate_for_course(user, course, *, issued_by=None):
        completion = CourseCompletionService.evaluate_course_completion(user, course)
        if not completion['is_complete']:
            raise ValueError(_('No se puede emitir el certificado porque el curso no está completo.'))

        certificate, created = CourseCertificate.objects.get_or_create(
            user=user,
            course=course,
            defaults={
                'issued_by': issued_by,
                'public_url': '',
                'status': CourseCertificate.Status.ISSUED,
            },
        )

        if certificate.status == CourseCertificate.Status.REVOKED:
            raise ValueError(_('El certificado está revocado y no puede emitirse de nuevo.'))

        if created or not certificate.public_url:
            certificate.public_url = certificate.get_validation_url()
            certificate.save(update_fields=['public_url', 'updated_at'])

        for badge in Badge.objects.filter(course=course, is_active=True, module__isnull=True):
            if not badge.rules.filter(is_active=True).exists():
                continue
            UserBadge.objects.get_or_create(
                user=user,
                badge=badge,
                course=course,
                defaults={'reason': _('Otorgada por finalización del curso.'), 'status': UserBadge.Status.AWARDED},
            )

        return certificate

    @staticmethod
    def verify_certificate(certificate_code):
        if certificate_code in (None, ''):
            return {'valid': False, 'reason': 'missing_certificate_code'}

        code_value = str(certificate_code)
        certificate = CourseCertificate.objects.select_related('user', 'course').filter(certificate_code=code_value).first()
        if certificate is None:
            return {'valid': False, 'reason': 'certificate_not_found'}

        if certificate.status == CourseCertificate.Status.REVOKED:
            CertificateVerification.objects.create(
                certificate=certificate,
                status=CertificateVerification.Status.REVOKED,
                notes=certificate.revocation_reason or _('Certificado revocado por auditoría.'),
            )
            return {
                'valid': False,
                'reason': 'certificate_revoked',
                'certificate': certificate,
                'course_name': certificate.course.nombre,
            }

        existing = certificate.verifications.order_by('-verified_at').first()
        if existing and existing.status == CertificateVerification.Status.VALID:
            return {
                'valid': True,
                'reason': 'certificate_valid',
                'certificate': certificate,
                'course_name': certificate.course.nombre,
            }

        verification = CertificateVerification.objects.create(
            certificate=certificate,
            status=CertificateVerification.Status.VALID,
            notes=_('Validación pública del certificado emitido.'),
        )
        return {
            'valid': True,
            'reason': 'certificate_valid',
            'certificate': certificate,
            'verification': verification,
            'course_name': certificate.course.nombre,
        }


class CodeExerciseService:
    """Wrap execution behind a sandbox adapter and never run arbitrary code in-process."""

    @staticmethod
    def create_submission(*, exercise, user, source_code):
        if not exercise or not user:
            raise ValueError(_('Debes indicar ejercicio y usuario.'))

        existing_attempts = CodeSubmission.objects.filter(exercise=exercise, user=user).count()
        if exercise.max_attempts and existing_attempts >= exercise.max_attempts:
            raise ValueError(_('Se ha alcanzado el máximo de intentos permitidos para este ejercicio.'))

        submission = CodeSubmission.objects.create(
            exercise=exercise,
            user=user,
            source_code=source_code,
            attempt_number=existing_attempts + 1,
            status=CodeSubmission.Status.DRAFT,
        )
        return submission

    @staticmethod
    def run_submission(submission):
        exercise = submission.exercise
        if not exercise.sandbox_enabled:
            raise SandboxUnavailableError(
                _('La ejecución de código requiere un sandbox aislado configurado. No se ejecuta código arbitrario en el proceso web.')
            )

        if not getattr(submission, 'source_code', '').strip():
            raise ValidationError(_('El código entregado está vacío.'))

        result = CodeExecutionResult.objects.create(
            submission=submission,
            status=CodeExecutionResult.Status.BLOCKED,
            stdout='',
            stderr='Ejecución bloqueada: la infraestructura de sandbox no está conectada.',
            exit_code=1,
            duration_ms=0,
            memory_used_mb=0,
            summary='Sandbox no disponible.',
            sandbox_version='disabled',
        )
        submission.status = CodeSubmission.Status.BLOCKED
        submission.feedback = _('La ejecución queda bloqueada hasta activar la sandbox segura del curso.')
        submission.save(update_fields=['status', 'feedback', 'updated_at'])
        return result

    @staticmethod
    def create_demo_exercises(*, course, module, created_by):
        examples = [
            {
                'title': 'Hola mundo',
                'language': CodeExercise.Language.PYTHON,
                'description': 'Imprime un mensaje de bienvenida.',
                'starter_code': 'print("Hola, NoTodoEsCódigo!")',
                'public_tests': [{'name': 'smoke', 'description': 'Debe imprimir el mensaje esperado'}],
                'hidden_tests': [{'name': 'secret', 'expected': 'Hola, NoTodoEsCódigo!'}],
                'max_attempts': 3,
                'points': 10,
            },
            {
                'title': 'Variables',
                'language': CodeExercise.Language.PYTHON,
                'description': 'Usa variables para construir una salida simple.',
                'starter_code': 'nombre = "Ada"\nprint(nombre)',
                'public_tests': [{'name': 'print_name', 'description': 'Debe imprimir el valor de la variable'}],
                'hidden_tests': [{'name': 'secret_variable', 'expected': 'Ada'}],
                'max_attempts': 3,
                'points': 15,
            },
            {
                'title': 'Funciones',
                'language': CodeExercise.Language.JAVASCRIPT,
                'description': 'Escribe una función que devuelva un valor.',
                'starter_code': 'function saludar(nombre) {\n  return nombre;\n}\nconsole.log(saludar("Mundo"));',
                'public_tests': [{'name': 'call_function', 'description': 'Debe devolver un valor del parámetro'}],
                'hidden_tests': [{'name': 'secret_function', 'expected': 'Mundo'}],
                'max_attempts': 2,
                'points': 20,
            },
            {
                'title': 'APIs',
                'language': CodeExercise.Language.JAVASCRIPT,
                'description': 'Consume una respuesta simulada sin depender de red.',
                'starter_code': 'const data = { status: 200, message: "ok" };\nconsole.log(data.message);',
                'public_tests': [{'name': 'api_response', 'description': 'Debe procesar la respuesta de ejemplo'}],
                'hidden_tests': [{'name': 'secret_api', 'expected': 'ok'}],
                'max_attempts': 2,
                'points': 25,
            },
        ]

        created = []
        for item in examples:
            exercise = CodeExercise.objects.create(
                course=course,
                module=module,
                created_by=created_by,
                title=item['title'],
                language=item['language'],
                description=item['description'],
                starter_code=item['starter_code'],
                public_tests=item['public_tests'],
                hidden_tests=item['hidden_tests'],
                max_attempts=item['max_attempts'],
                points=item['points'],
                sandbox_enabled=False,
                status=CodeExercise.Status.PUBLISHED,
                cpu_limit_ms=500,
                memory_limit_mb=128,
                timeout_seconds=10,
            )
            created.append(exercise)
        return created


ENROLLMENT_LOCK = threading.Lock()


def get_or_create_lms_profile(user):
    """Create lazily, without a User signal or queries when the relation is cached.

    The one-to-one constraint and Django's get_or_create resolve concurrent
    inserts. A cached missing relation is rechecked; another request may have
    created it since. Database errors propagate to the caller.
    """
    user_model = get_user_model()
    if not isinstance(user, user_model) or user._state.adding or not user.pk:
        raise ValueError(_('Se requiere un usuario guardado del modelo configurado.'))

    relation = user_model._meta.get_field('lms_profile')
    if relation.is_cached(user):
        profile = relation.get_cached_value(user)
        if profile is not None:
            return profile
    profile, created = UserProfileLMS.objects.db_manager(user._state.db).get_or_create(
        user_id=user.pk,
    )
    relation.set_cached_value(user, profile)
    UserProfileLMS._meta.get_field('user').set_cached_value(profile, user)
    if created:
        logger.info('LMS profile created for user_id=%s', user.pk)
    return profile


@transaction.atomic
def create_course(*, actor, course):
    if not actor.is_authenticated or not actor.has_perm('courses.add_curso'):
        raise PermissionDenied(_('No tienes permiso para crear cursos.'))

    course.activo = False
    course.creado_por = actor
    course.actualizado_por = actor
    course.full_clean()
    course.save()
    CourseSettingsLMS.objects.get_or_create(course=course)
    CourseMembership.objects.create(
        course=course,
        user=actor,
        role=CourseMembership.Role.OWNER,
        status=CourseMembership.Status.ACTIVE,
        joined_at=timezone.now(),
    )
    AuditEvent.objects.create(
        actor=actor,
        course=course,
        action=AuditEvent.Action.COURSE_CREATED,
    )
    logger.info('LMS course created course_id=%s actor_id=%s', course.pk, actor.pk)
    return course


@transaction.atomic
def duplicate_course(*, actor, source, title=None, include_content=True):
    if not actor.is_authenticated or not can_edit_course(actor, source):
        raise PermissionDenied(_('No tienes permiso para duplicar este curso.'))

    new_course = source
    clone = source.__class__.objects.get(pk=source.pk)
    clone.pk = None
    clone.id = None
    clone.nombre = title or f'{source.nombre} (copia)'
    clone.slug = ''
    clone.codigo = ''
    clone.activo = False
    clone.destacado = False
    clone.fecha_publicacion = None
    clone.creado_por = actor
    clone.actualizado_por = actor
    clone.status = 'draft'
    clone.full_clean()
    clone.save()

    if source.categoria_id:
        clone.categoria_id = source.categoria_id
    clone.etiquetas.set(source.etiquetas.all())
    if include_content:
        for tema in source.temas.all():
            new_tema = tema
            new_tema.pk = None
            new_tema.id = None
            new_tema.curso = clone
            new_tema.save()
            for recurso in tema.recursos.all():
                recurso.pk = None
                recurso.id = None
                recurso.tema = new_tema
                recurso.save()

    CourseSettingsLMS.objects.get_or_create(course=clone)
    CourseMembership.objects.filter(course=clone).delete()
    CourseMembership.objects.create(
        course=clone,
        user=actor,
        role=CourseMembership.Role.OWNER,
        status=CourseMembership.Status.ACTIVE,
        joined_at=timezone.now(),
    )
    AuditEvent.objects.create(
        actor=actor,
        course=clone,
        action=AuditEvent.Action.COURSE_CREATED,
        metadata={'duplicated_from': source.pk, 'include_content': include_content},
    )
    return clone


@transaction.atomic
def update_course(*, actor, course, changes):
    if not can_edit_course(actor, course):
        raise PermissionDenied(_('No tienes permiso para editar este curso.'))
    if changes.get('status') in ('scheduled', 'published') and not can_publish_course(actor, course):
        raise PermissionDenied(_('No tienes permiso para publicar o programar este curso.'))
    for field, value in changes.items():
        setattr(course, field, value)
    if course.fecha_fin and course.fecha_fin < course.fecha_inicio:
        raise ValidationError({
            'fecha_fin': _('La fecha de fin debe ser igual o posterior al inicio.')
        })
    course.full_clean()
    course.save()
    AuditEvent.objects.create(
        actor=actor,
        course=course,
        action=AuditEvent.Action.COURSE_UPDATED,
    )
    logger.info('LMS course updated course_id=%s actor_id=%s', course.pk, actor.pk)
    return course


@transaction.atomic
def publish_course(*, actor, course):
    if not can_publish_course(actor, course):
        raise PermissionDenied(_('No tienes permiso para publicar este curso.'))
    if not course.activo or course.status != 'published':
        course.activo = True
        course.status = 'published'
        course.fecha_publicacion = course.fecha_publicacion or timezone.now()
        course.full_clean()
        course.save(update_fields=['activo', 'status', 'fecha_publicacion', 'actualizado_en'])
        settings, _created = CourseSettingsLMS.objects.get_or_create(course=course)
        if settings.archived:
            settings.archived = False
            settings.save(update_fields=['archived'])
        AuditEvent.objects.create(
            actor=actor,
            course=course,
            action=AuditEvent.Action.COURSE_PUBLISHED,
        )
        logger.info('LMS course published course_id=%s actor_id=%s', course.pk, actor.pk)
    return course


@transaction.atomic
def build_NoTodoEsCódigo_structure(course, *, actor=None):
    """Create the default learning flow for a campus course in a single pass."""
    module = CourseModule.objects.filter(course=course, slug='NoTodoEsCódigo-campus').first()
    if module is None:
        module = CourseModule.objects.filter(course=course, title='NoTodoEsCódigo Campus').first()
    if module is None:
        module = CourseModule.objects.create(
            course=course,
            title='NoTodoEsCódigo Campus',
            slug='NoTodoEsCódigo-campus',
            summary='Ruta de arranque del curso con diagnóstico y primera actividad evaluable.',
            position=1,
            status=CourseModule.Status.PUBLISHED,
            starts_at=timezone.now(),
        )
        Page.objects.create(
            course=course,
            title='Bienvenida al curso',
            slug='bienvenida-al-curso',
            status=Page.Status.PUBLISHED,
            visibility=Page.Visibility.COURSE,
            author=actor or course.creado_por,
            position=1,
            is_homepage=True,
            content='<h2>Bienvenido/a</h2><p>Empieza por la actividad de diagnóstico para preparar tu ruta.</p>',
        )

    topic = Tema.objects.filter(curso=course, titulo=module.title).order_by('pk').first()
    if topic is None:
        topic = Tema.objects.create(
            curso=course, titulo=module.title,
            contenido=module.description or module.summary, orden=module.position,
        )
    activity, _activity_created = LearningActivity.objects.get_or_create(
        course=course,
        module=topic,
        title='Diagnóstico inicial',
        defaults={
            'description': 'Comprueba tus conocimientos iniciales y ajusta la ruta de trabajo.',
            'instructions': 'Completa la actividad y revisa el feedback posterior.',
            'status': LearningActivity.Status.OPEN,
            'starts_at': timezone.now() - timedelta(hours=2),
            'due_at': timezone.now() + timedelta(days=2),
            'feedback_at': timezone.now() + timedelta(days=4),
            'solution_release_at': timezone.now() + timedelta(days=5),
            'points': 100,
            'is_evaluable': True,
        },
    )

    item, _item_created = ModuleItem.objects.get_or_create(
        module=module,
        title=activity.title,
        defaults={
            'item_type': ModuleItem.ItemType.ACTIVITY,
            'description': activity.description,
            'learning_activity': activity,
            'position': 1,
            'required': True,
            'due_at': activity.due_at,
        },
    )
    if item.learning_activity_id != activity.pk:
        item.learning_activity = activity
        item.item_type = ModuleItem.ItemType.ACTIVITY
        item.due_at = activity.due_at
        item.save(update_fields=['learning_activity', 'item_type', 'due_at', 'updated_at'])

    return module


@transaction.atomic
def enroll_student(*, actor, course):
    if not actor.is_authenticated:
        raise PermissionDenied(_('Debes iniciar sesión para inscribirte.'))

    with ENROLLMENT_LOCK:
        for attempt in range(5):
            try:
                get_user_model().objects.select_for_update().get(pk=actor.pk)
                existing = CourseMembership.objects.filter(user=actor, course=course).first()
                if existing is not None:
                    if (
                        existing.role != CourseMembership.Role.STUDENT
                        or existing.status not in (
                            CourseMembership.Status.ACTIVE,
                            CourseMembership.Status.COMPLETED,
                        )
                    ):
                        raise EnrollmentUnavailable(_('La pertenencia existente no permite inscribirse.'))
                    return existing

                legacy_enrollment = InscripcionCurso.objects.filter(
                    usuario=actor,
                    curso=course,
                ).first()
                if legacy_enrollment:
                    status = (
                        CourseMembership.Status.COMPLETED
                        if legacy_enrollment.completado
                        else CourseMembership.Status.ACTIVE
                    )
                    membership, created = CourseMembership.objects.get_or_create(
                        user=actor,
                        course=course,
                        defaults={
                            'role': CourseMembership.Role.STUDENT,
                            'status': status,
                            'joined_at': legacy_enrollment.fecha_inscripcion,
                            'completed_at': legacy_enrollment.fecha_completado,
                        },
                    )
                    if created:
                        logger.info(
                            'Legacy enrollment linked course_id=%s user_id=%s',
                            course.pk,
                            actor.pk,
                        )
                    return membership

                if not can_enroll(actor, course):
                    existing = CourseMembership.objects.filter(
                        user=actor,
                        course=course,
                        role=CourseMembership.Role.STUDENT,
                        status=CourseMembership.Status.ACTIVE,
                    ).first()
                    if existing:
                        return existing
                    raise EnrollmentUnavailable(_('La inscripción no está disponible para este curso.'))

                now = timezone.now()
                membership, created = CourseMembership.objects.get_or_create(
                    course=course,
                    user=actor,
                    defaults={
                        'role': CourseMembership.Role.STUDENT,
                        'status': CourseMembership.Status.ACTIVE,
                        'joined_at': now,
                    },
                )
                if membership.role != CourseMembership.Role.STUDENT or membership.status not in (
                    CourseMembership.Status.ACTIVE,
                    CourseMembership.Status.COMPLETED,
                ):
                    raise EnrollmentUnavailable(_('La pertenencia existente no permite inscribirse.'))

                InscripcionCurso.objects.get_or_create(usuario=actor, curso=course)
                if created:
                    AuditEvent.objects.create(
                        actor=actor,
                        course=course,
                        target_user=actor,
                        action=AuditEvent.Action.MEMBER_ENROLLED,
                    )
                    logger.info(
                        'LMS student enrolled course_id=%s user_id=%s',
                        course.pk,
                        actor.pk,
                    )
                return membership
            except OperationalError as exc:
                if 'database table is locked' not in str(exc).lower() or attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))


@transaction.atomic
def create_course_section(*, actor, course, title, cohort=None, position=0, capacity=0, description='', allow_self_enrollment=True):
    if not can_edit_course(actor, course):
        raise PermissionDenied(_('No tienes permiso para gestionar secciones de este curso.'))

    section = Section.objects.create(
        course=course,
        cohort=cohort,
        title=title.strip(),
        position=position,
        capacity=capacity,
        description=description,
        allow_self_enrollment=allow_self_enrollment,
    )
    AuditEvent.objects.create(
        actor=actor,
        course=course,
        action=AuditEvent.Action.MEMBER_STATUS_CHANGED,
        metadata={'section_id': section.pk, 'section_title': section.title, 'action': 'created'},
    )
    return section


@transaction.atomic
def assign_membership_to_section(*, actor, membership, section):
    if not can_edit_course(actor, membership.course):
        raise PermissionDenied(_('No tienes permiso para asignar secciones en este curso.'))
    if section.course_id != membership.course_id:
        raise ValidationError({'section': _('La sección debe pertenecer al mismo curso.')})

    if membership.role == CourseMembership.Role.STUDENT and section.capacity:
        occupied = CourseMembership.objects.filter(
            course=membership.course,
            section=section,
            role=CourseMembership.Role.STUDENT,
            status__in=(
                CourseMembership.Status.ACTIVE,
                CourseMembership.Status.COMPLETED,
                CourseMembership.Status.INVITED,
            ),
        ).exclude(pk=membership.pk).count()
        if occupied >= section.capacity:
            raise ValidationError({'section': _('La sección está llena. Elige otra sección o aumenta la capacidad.')})

    membership.section = section
    membership.save(update_fields=['section', 'updated_at'])
    AuditEvent.objects.create(
        actor=actor,
        course=membership.course,
        target_user=membership.user,
        action=AuditEvent.Action.MEMBER_STATUS_CHANGED,
        metadata={'section_id': section.pk, 'section_title': section.title, 'role': membership.role},
    )
    return membership


@transaction.atomic
def invite_users_to_course(*, actor, course, emails, role=CourseMembership.Role.STUDENT, section=None, status=None):
    if not can_edit_course(actor, course):
        raise PermissionDenied(_('No tienes permiso para invitar usuarios a este curso.'))
    if section and section.course_id != course.pk:
        raise ValidationError({'section': _('La sección debe pertenecer al mismo curso.')})

    normalized = []
    seen = set()
    for email in emails or []:
        value = (email or '').strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)

    created = 0
    updated = 0
    result = {'created': 0, 'updated': 0, 'errors': []}

    for email in normalized:
        user = get_user_model().objects.filter(email__iexact=email).first()
        if user is None:
            username = email.split('@', 1)[0].strip() or f'user_{get_random_string(8)}'
            username_base = username[:30]
            candidate = username_base
            suffix = 1
            while get_user_model().objects.filter(username=candidate).exists():
                candidate = f'{username_base}_{suffix}'
                suffix += 1
            user = get_user_model().objects.create_user(
                username=candidate,
                email=email,
                password=get_random_string(18),
                is_active=True,
            )

        membership, membership_created = CourseMembership.objects.get_or_create(
            course=course,
            user=user,
            defaults={
                'role': role,
                'status': CourseMembership.Status.INVITED,
                'joined_at': timezone.now(),
            },
        )
        if membership_created:
            created += 1
        else:
            updated += 1

        if role and membership.role != role:
            membership.role = role
        if status:
            membership.status = status
        elif membership.status not in (CourseMembership.Status.ACTIVE, CourseMembership.Status.COMPLETED):
            membership.status = CourseMembership.Status.INVITED
        if section and membership.section_id != section.pk:
            membership.section = section
        if membership.role == CourseMembership.Role.STUDENT and course.capacidad_maxima:
            active_students = CourseMembership.objects.filter(
                course=course,
                role=CourseMembership.Role.STUDENT,
                status__in=(CourseMembership.Status.ACTIVE, CourseMembership.Status.COMPLETED, CourseMembership.Status.INVITED),
            ).exclude(pk=membership.pk).count()
            if active_students >= course.capacidad_maxima:
                raise ValidationError({'course': _('Se ha alcanzado la capacidad máxima del curso.')})
        membership.save()

        invite = CourseInvitation.objects.filter(course=course, email__iexact=email).first()
        if not invite:
            invite = CourseInvitation.objects.create(
                course=course,
                email=email,
                user=user,
                invited_by=actor,
                role=role,
                section=section,
                token=get_random_string(32),
                expires_at=timezone.now() + timedelta(days=7),
            )
        else:
            invite.user = user
            invite.invited_by = actor
            invite.role = role
            invite.section = section
            invite.status = CourseInvitation.Status.PENDING
            invite.save(update_fields=['user', 'invited_by', 'role', 'section', 'status', 'updated_at'])

        AuditEvent.objects.create(
            actor=actor,
            course=course,
            target_user=user,
            action=AuditEvent.Action.MEMBER_INVITED,
            metadata={'email': email, 'role': role, 'section_id': section.pk if section else None},
        )

    result['created'] = created
    result['updated'] = updated
    return result


@transaction.atomic
def import_course_members_csv(*, actor, course, csv_text, default_role=CourseMembership.Role.STUDENT):
    if not can_edit_course(actor, course):
        raise PermissionDenied(_('No tienes permiso para gestionar personas en este curso.'))

    reader = csv.DictReader(io.StringIO(csv_text or ''))
    if not reader.fieldnames:
        raise ValidationError({'csv': _('El archivo CSV está vacío o no tiene cabecera.')})
    required = {'email'}
    missing = sorted(required.difference({name.strip().lower() for name in reader.fieldnames if name}))
    if missing:
        raise ValidationError({'csv': _('Falta la columna obligatoria: %(fields)s') % {'fields': ', '.join(missing)}})

    created = 0
    updated = 0
    errors = []
    for row_number, row in enumerate(reader, start=2):
        if row is None:
            continue
        email_value = (row.get('email') or '').strip()
        if not email_value:
            errors.append({'row': row_number, 'error': _('Correo obligatorio.')})
            continue
        if '@' not in email_value or email_value.count('@') != 1 or not email_value.rsplit('@', 1)[0] or not email_value.rsplit('@', 1)[1]:
            errors.append({'row': row_number, 'error': _('Correo inválido: %(email)s') % {'email': email_value}})
            continue
        if CourseMembership.objects.filter(course=course, user__email__iexact=email_value).exists():
            errors.append({'row': row_number, 'error': _('Ya existe este usuario en el curso.')})
            continue
        role_value = (row.get('role') or default_role).strip().lower()
        role_map = {
            'student': CourseMembership.Role.STUDENT,
            'alumno': CourseMembership.Role.STUDENT,
            'teacher': CourseMembership.Role.TEACHER,
            'profesor': CourseMembership.Role.TEACHER,
            'assistant': CourseMembership.Role.ASSISTANT,
            'asistente': CourseMembership.Role.ASSISTANT,
            'owner': CourseMembership.Role.OWNER,
            'observador': CourseMembership.Role.OBSERVER,
            'observer': CourseMembership.Role.OBSERVER,
        }
        role = role_map.get(role_value)
        if role is None:
            errors.append({'row': row_number, 'error': _('Rol inválido: %(role)s') % {'role': role_value}})
            continue

        section_name = (row.get('section') or '').strip()
        section = None
        if section_name:
            section, _created = Section.objects.get_or_create(course=course, title=section_name)

        try:
            result = invite_users_to_course(
                actor=actor,
                course=course,
                emails=[email_value],
                role=role,
                section=section,
            )
        except ValidationError as exc:
            errors.append({'row': row_number, 'error': exc.message_dict if hasattr(exc, 'message_dict') else str(exc)})
            continue
        created += result['created']
        updated += result['updated']

    return {'created': created, 'updated': updated, 'errors': errors}


@transaction.atomic
def set_membership_status(*, actor, membership, status):
    if not can_edit_course(actor, membership.course):
        raise PermissionDenied(_('No tienes permiso para gestionar este curso.'))
    if status not in CourseMembership.Status.values:
        raise ValidationError({'status': _('Selecciona un estado válido.')})

    get_user_model().objects.select_for_update().get(pk=membership.user_id)
    membership = CourseMembership.objects.select_for_update().get(pk=membership.pk)
    if membership.status == status:
        return membership
    membership.status = status
    now = timezone.now()
    membership.dropped_at = None
    membership.completed_at = None
    if status == CourseMembership.Status.DROPPED:
        membership.dropped_at = now
    elif status == CourseMembership.Status.COMPLETED:
        membership.completed_at = now
    elif status == CourseMembership.Status.ACTIVE and not membership.joined_at:
        membership.joined_at = now
    membership.save()
    if (
        membership.role == CourseMembership.Role.STUDENT
        and status in (CourseMembership.Status.ACTIVE, CourseMembership.Status.COMPLETED)
    ):
        enrollment, _created = InscripcionCurso.objects.get_or_create(
            usuario_id=membership.user_id,
            curso_id=membership.course_id,
        )
        enrollment.completado = status == CourseMembership.Status.COMPLETED
        enrollment.fecha_completado = membership.completed_at
        enrollment.save(update_fields=['completado', 'fecha_completado'])
    elif status == CourseMembership.Status.DROPPED:
        InscripcionCurso.objects.filter(
            usuario_id=membership.user_id,
            curso_id=membership.course_id,
        ).delete()
    elif status == CourseMembership.Status.DROPPED:
        InscripcionCurso.objects.filter(
            usuario_id=membership.user_id,
            curso_id=membership.course_id,
        ).delete()
    AuditEvent.objects.create(
        actor=actor,
        course=membership.course,
        target_user=membership.user,
        action=AuditEvent.Action.MEMBER_STATUS_CHANGED,
        metadata={'status': status},
    )
    logger.info(
        'LMS membership status changed course_id=%s user_id=%s status=%s actor_id=%s',
        membership.course_id,
        membership.user_id,
        status,
        actor.pk,
    )
    return membership
