"""Campus contracts and learning operations over the existing LMS entities."""
import hashlib
import json
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.courses.models import InscripcionCurso
from apps.users.access import AccessDecision, can, effective_plan
from .models import (
    AuditEvent, CourseCertificate, CourseMembership, ModuleContent, QuizAttempt, Submission,
)
from .policies import can_edit_course


def campus_settings(course):
    settings = getattr(course, 'lms_settings', None)
    return settings if settings and settings.campus_enabled else None


def course_right(user, course, *, contract=None):
    settings = campus_settings(course)
    if not user.is_authenticated or not user.is_active:
        return AccessDecision(False, 'authentication_required')
    if not settings:
        return AccessDecision(False, 'legacy_course')
    terms = contract.get('access', {}) if contract else {}
    free = terms.get('free', settings.campus_free)
    code = terms.get('entitlement_code', settings.campus_entitlement_code)
    included_plan = terms.get('included_plan', settings.campus_included_plan)
    if free:
        return AccessDecision(True, 'free_course')
    if code and can(
        user, 'access_entitlement', entitlement=code,
    ).allowed:
        return AccessDecision(True, 'individual_purchase')
    plan = effective_plan(user)
    if included_plan and (
        plan == included_plan or plan == 'pro' and included_plan == 'plus'
    ):
        return AccessDecision(True, 'included_membership')
    return AccessDecision(False, 'course_right_required')


def material_access(user, course):
    if not campus_settings(course):
        return AccessDecision(True, 'legacy_course')
    if not user.is_authenticated or not user.is_active:
        return AccessDecision(False, 'authentication_required')
    if can_edit_course(user, course):
        return AccessDecision(True, 'assigned_teacher')
    membership = CourseMembership.objects.filter(course=course, user=user).first()
    if not membership or membership.role not in (
        CourseMembership.Role.STUDENT, CourseMembership.Role.OBSERVER,
    ) or membership.status not in (
        CourseMembership.Status.ACTIVE, CourseMembership.Status.COMPLETED,
    ):
        return AccessDecision(False, 'active_enrollment_required')
    if not membership.learning_contract:
        return AccessDecision(False, 'learning_contract_required')
    settings = campus_settings(course)
    now = timezone.now()
    if not course.activo or settings.archived or course.status != 'published':
        return AccessDecision(False, 'course_closed')
    if settings.starts_at and now < settings.starts_at:
        return AccessDecision(False, 'course_not_started')
    if settings.ends_at and now >= settings.ends_at:
        return AccessDecision(False, 'course_closed')
    return course_right(user, course, contract=membership.learning_contract)


def require_material_access(user, course):
    decision = material_access(user, course)
    if not decision.allowed:
        raise PermissionDenied(access_message(decision.reason))
    return decision


def access_message(reason):
    return {
        'course_right_required': _('Sin derecho vigente: revisa tu compra o la inclusión del curso en tu membresía.'),
        'active_enrollment_required': _('La matrícula no está activa. El historial se conserva.'),
        'learning_contract_required': _('Un administrador debe revisar y vincular los requisitos de tu matrícula anterior.'),
        'course_closed': _('El curso está cerrado. Tu historial y certificados se conservan.'),
        'course_not_started': _('El curso todavía no ha comenzado.'),
        'authentication_required': _('Debes iniciar sesión con una cuenta activa.'),
    }.get(reason, reason)


def content_definition(content):
    target = content.target
    definition = {
        'id': content.pk, 'module_id': content.module_id, 'title': content.title,
        'required': content.required, 'criterion': content.criterion,
        'passing_percentage': str(content.passing_percentage),
        'page_id': content.page_id, 'file_id': content.file_id,
        'quiz_id': content.quiz_id, 'activity_id': content.activity_id,
        'revision_id': getattr(target, 'current_revision_id', None),
        'checksum': getattr(target, 'checksum', ''),
        'points': str(getattr(target, 'points', 0)),
        'prerequisite_id': content.prerequisite_id,
        'visibility': getattr(target, 'visibility', ''),
        'access_policy': getattr(target, 'access_policy', ''),
    }
    if content.page_id:
        definition['body_sha256'] = hashlib.sha256(target.content.encode()).hexdigest()
    if content.activity_id:
        definition['body_sha256'] = hashlib.sha256(
            (target.description + '\n' + target.instructions).encode(),
        ).hexdigest()
    if content.quiz_id:
        definition['quiz_configuration'] = {
            'passing_score': str(target.passing_score), 'duration_minutes': target.duration_minutes,
            'questions': list(target.questions.order_by('order', 'pk').values(
                'id', 'question_version_id', 'points', 'metadata',
            )),
        }
        definition['quiz_configuration']['questions'] = json.loads(
            json.dumps(definition['quiz_configuration']['questions'], default=str),
        )
    return definition


def content_available(content, membership):
    target = content.target
    if content.page_id:
        return target.is_currently_published() and target.visibility != target.Visibility.TEACHERS
    if content.file_id:
        return (
            target.is_active and (target.allow_preview or target.allow_download)
            and target.visibility != target.Visibility.TEACHERS and target.access_policy != target.AccessPolicy.TEACHERS
        )
    if content.quiz_id:
        return target.status == 'published' and (
            not target.section_id or target.section_id == membership.section_id
        )
    return target.status != 'draft'


def freeze_contract(membership, *, actor=None):
    settings = campus_settings(membership.course)
    if not settings or membership.learning_contract:
        return
    contents = ModuleContent.objects.filter(module__curso=membership.course).select_related(
        'module', 'page', 'file', 'quiz', 'activity',
    ).order_by('module__orden', 'position', 'pk')
    items = []
    for content in contents:
        if content.quiz_id and content.quiz.section_id and content.quiz.section_id != membership.section_id:
            continue
        if not content_available(content, membership):
            if content.required:
                raise ValidationError(_('Un contenido obligatorio no está publicado para este grupo. Revisa el curso.'))
            continue
        items.append(content_definition(content))
    if not any(item['required'] for item in items):
        raise ValidationError(_('Configura contenidos obligatorios antes de abrir matrículas Campus.'))
    membership.learning_contract = {
        'schema': 1, 'version': settings.campus_version,
        'course_title': membership.course.nombre,
        'frozen_at': timezone.now().isoformat(), 'items': items,
        'certificate_included': settings.campus_certificate_included,
        'issuer_id': settings.campus_issuer_id,
        'issuer_name': settings.campus_issuer.name if settings.campus_issuer_id else '',
        'section_id': membership.section_id,
        'access': {
            'free': settings.campus_free, 'included_plan': settings.campus_included_plan,
            'entitlement_code': settings.campus_entitlement_code,
        },
    }
    membership.access_origin = course_right(membership.user, membership.course).reason
    membership.save(update_fields=['learning_contract', 'access_origin', 'updated_at'])
    AuditEvent.objects.create(
        actor=actor or membership.user, target_user=membership.user, course=membership.course,
        action=AuditEvent.Action.COURSE_UPDATED,
        metadata={'campus_action': 'contract_frozen', 'version': settings.campus_version},
    )


def require_frozen_content(content, user):
    course = content.module.curso
    if not campus_settings(course) or can_edit_course(user, course):
        return
    membership = CourseMembership.objects.get(course=course, user=user)
    frozen = next((
        item for item in membership.learning_contract.get('items', []) if item['id'] == content.pk
    ), None)
    if frozen is None or frozen != content_definition(content):
        raise PermissionDenied(_('Este contenido cambió o no está incluido en tu versión. Solicita revisión docente.'))


def requirement_status(item, user):
    criterion = item['criterion']
    if criterion in ('manual', 'viewed'):
        from .models import ModuleContentProgress
        field = 'completed_at' if criterion == 'manual' else 'opened_at'
        return ModuleContentProgress.objects.filter(
            content_id=item['id'], user=user, **{f'{field}__isnull': False},
        ).exists()
    if item['activity_id']:
        submission = Submission.objects.filter(
            assignment__learning_activity_id=item['activity_id'], user=user,
            submitted_at__isnull=False,
        ).order_by('-version', '-pk').first()
        if not submission:
            return False
        if criterion == 'submitted':
            return True
        from .activity_workflow import feedback_visible
        return bool(
            submission.graded_at and feedback_visible(submission.assignment.learning_activity, submission)
            and Decimal(item['points']) > 0
            and submission.grade * 100 >= Decimal(item['points']) * Decimal(item['passing_percentage'])
        )
    attempts = QuizAttempt.objects.filter(
        quiz_id=item['quiz_id'], user=user, status=QuizAttempt.Status.GRADED,
    )
    if criterion == 'passed':
        from .quiz_status import quiz_grade_release_filter
        attempts = attempts.filter(
            quiz_grade_release_filter(attempt_path=''),
            is_passed=True, grade_entry__status='published', grade_entry__is_hidden=False,
        )
    return attempts.exists()


def learning_status(membership):
    items = membership.learning_contract.get('items', [])
    requirements = []
    for item in items:
        if item['required']:
            requirements.append({
                **item, 'complete': requirement_status(item, membership.user),
                'source': 'declared' if item['criterion'] == 'manual' else 'verified',
            })
    total = len(requirements)
    completed = sum(item['complete'] for item in requirements)
    content_requirements = [item for item in requirements if item['criterion'] in ('manual', 'viewed', 'submitted')]
    content_finished = bool(content_requirements) and all(item['complete'] for item in content_requirements)
    if requirements and not content_requirements:
        content_finished = completed == total
    return {
        'requirements': requirements, 'total': total, 'completed': completed,
        'percent': int(completed * 100 / total) if total else 0,
        'content_finished': content_finished,
        'passed': bool(total) and completed == total,
        'missing': [item for item in requirements if not item['complete']],
    }


@transaction.atomic
def refresh_completion(membership_id):
    membership = CourseMembership.objects.select_for_update().select_related('course', 'user').get(pk=membership_id)
    status = learning_status(membership)
    if membership.role != CourseMembership.Role.STUDENT or membership.status not in (
        CourseMembership.Status.ACTIVE, CourseMembership.Status.COMPLETED,
    ):
        return status
    now = timezone.now()
    if status['content_finished'] and not membership.content_completed_at:
        membership.content_completed_at = now
        membership.save(update_fields=['content_completed_at', 'updated_at'])
    if status['passed'] and not membership.passed_at:
        membership.passed_at = now
        membership.completed_at = membership.completed_at or now
        membership.status = CourseMembership.Status.COMPLETED
        membership.save(update_fields=['passed_at', 'completed_at', 'status', 'updated_at'])
        InscripcionCurso.objects.update_or_create(
            usuario=membership.user, curso=membership.course,
            defaults={'completado': True, 'fecha_completado': now},
        )
        AuditEvent.objects.create(
            target_user=membership.user, course=membership.course,
            action=AuditEvent.Action.COURSE_UPDATED,
            metadata={'campus_action': 'passed', 'version': membership.learning_contract['version']},
        )
        from .lifecycle import notify_once
        notify_once(
            course=membership.course, key=f'campus:{membership.pk}:passed',
            title=_('Curso superado'), message=membership.course.nombre,
            recipients=[membership.user],
            url=reverse('lms:campus_course', args=[membership.course_id]),
        )
    return status


@transaction.atomic
def issue_certificate(user, course, *, actor=None):
    membership = CourseMembership.objects.select_for_update().select_related('user', 'course').filter(
        user=user, course=course, role=CourseMembership.Role.STUDENT,
    ).first()
    if not membership:
        raise ValidationError(_('No existe una matrícula de alumno para emitir el certificado.'))
    if actor and actor != user and not scoped_students(actor, course).filter(user=user).exists():
        raise PermissionDenied
    existing = CourseCertificate.objects.filter(user=user, course=course).first()
    if existing:
        if not existing.is_valid:
            raise ValidationError(_('El certificado está revocado; requiere revisión administrativa.'))
        return existing
    contract = membership.learning_contract
    if not contract.get('certificate_included') or not contract.get('issuer_id'):
        raise ValidationError(_('El contrato no incluye un certificado con emisor identificado.'))
    status = refresh_completion(membership.pk)
    membership.refresh_from_db()
    if not membership.passed_at or not status['passed']:
        raise ValidationError(_('Faltan requisitos de superación.'))
    from apps.certifications.models import CredentialIssuer
    if not CredentialIssuer.objects.filter(pk=contract['issuer_id'], is_active=True).exists():
        raise ValidationError(_('El emisor no está activo; solicita revisión administrativa.'))
    certificate = CourseCertificate.objects.create(
        user=user, course=course, issued_by=actor,
        metadata={'campus': True, 'course_version': contract['version'],
                  'course_title': contract['course_title'],
                  'issuer_id': contract['issuer_id'], 'issuer_name': contract['issuer_name'],
                  'contract_sha256': hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()},
    )
    AuditEvent.objects.create(
        actor=actor or user, target_user=user, course=course, action=AuditEvent.Action.COURSE_UPDATED,
        metadata={'campus_action': 'certificate_issued', 'certificate_id': certificate.pk},
    )
    return certificate


def scoped_students(actor, course):
    queryset = CourseMembership.objects.filter(course=course, role=CourseMembership.Role.STUDENT)
    if not can_edit_course(actor, course):
        return queryset.none()
    teaching = CourseMembership.objects.filter(
        course=course, user=actor, status=CourseMembership.Status.ACTIVE,
        role__in=(CourseMembership.Role.OWNER, CourseMembership.Role.TEACHER, CourseMembership.Role.ASSISTANT),
    ).first()
    if teaching and teaching.section_id:
        queryset = queryset.filter(section_id=teaching.section_id)
    return queryset.exclude(user=actor)


def can_review_submission(actor, submission):
    return actor.pk != submission.user_id and scoped_students(
        actor, submission.assignment.course,
    ).filter(user_id=submission.user_id).exists()


def certificate_pdf(certificate):
    from io import BytesIO
    from xml.sax.saxutils import escape
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    stream = BytesIO()
    styles = getSampleStyleSheet()
    lines = [
        ('Certificado propio de superación', 'Title'),
        (certificate.metadata['issuer_name'], 'Heading2'),
        (certificate.user.get_full_name() or certificate.user.username, 'Heading1'),
        (certificate.metadata['course_title'], 'Heading2'),
        (f"Versión: {certificate.metadata['course_version']}", 'Normal'),
        (f"Código: {certificate.certificate_code}", 'Normal'),
        (f"Fecha de emisión: {certificate.issued_at:%Y-%m-%d}", 'Normal'),
        ('Acreditación propia del emisor. No implica homologación oficial.', 'Normal'),
        (certificate.get_validation_url(), 'Normal'),
    ]
    story = []
    for text, style in lines:
        story.extend([Paragraph(escape(text), styles[style]), Spacer(1, 14)])
    SimpleDocTemplate(stream, pagesize=landscape(A4)).build(story)
    stream.seek(0)
    return stream
