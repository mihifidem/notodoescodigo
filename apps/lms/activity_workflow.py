from pathlib import Path
import hashlib
from decimal import Decimal
from uuid import uuid4

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.courses.models import InscripcionCurso
from .models import Assignment, AuditEvent, CourseMembership, FileAsset, LearningActivity, Submission
from .policies import can_edit_course, is_course_member


class ActivitySubmissionForm(forms.Form):
    idempotency_key = forms.UUIDField(required=False, initial=uuid4, widget=forms.HiddenInput)
    text_answer = forms.CharField(
        label=_('Tu respuesta'), required=False,
        widget=forms.Textarea(attrs={'rows': 8, 'class': 'lms-input'}),
    )
    file = forms.FileField(label=_('Archivo adjunto'), required=False)

    def clean_file(self):
        file = self.cleaned_data.get('file')
        if file and Path(file.name).suffix.lower() not in FileAsset.ALLOWED_EXTENSIONS:
            raise forms.ValidationError(_('Extensión de archivo no permitida.'))
        return file

    def clean(self):
        data = super().clean()
        if not data.get('text_answer') and not data.get('file'):
            raise forms.ValidationError(_('Escribe una respuesta o adjunta un archivo.'))
        return data


class ActivityGradeForm(forms.Form):
    grade = forms.DecimalField(label=_('Nota'), min_value=0, max_digits=8, decimal_places=2)
    feedback = forms.CharField(
        label=_('Feedback'), required=False,
        widget=forms.Textarea(attrs={'rows': 5, 'class': 'lms-input'}),
    )

    def __init__(self, *args, activity, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['grade'].max_value = activity.points
        from django.core.validators import MaxValueValidator
        self.fields['grade'].validators.append(MaxValueValidator(activity.points))


def activity_state(activity, *, now=None):
    now = now or timezone.now()
    if activity.status == LearningActivity.Status.DRAFT:
        return 'created'
    if activity.status == LearningActivity.Status.CLOSED or activity.course.status == 'archived':
        return 'closed'
    if activity.due_at and now >= activity.due_at:
        return 'closed'
    if activity.starts_at and now < activity.starts_at:
        return 'created'
    return 'open'


def can_read_activity(user, activity):
    from .module_progression import target_lock_reason
    if target_lock_reason(activity, user):
        return False
    return can_edit_course(user, activity.course) or (
        is_course_member(user, activity.course) and activity.status != LearningActivity.Status.DRAFT
    )


def active_student(user, course):
    membership = CourseMembership.objects.filter(course=course, user=user).first()
    if membership:
        return membership.role == CourseMembership.Role.STUDENT and membership.status == CourseMembership.Status.ACTIVE
    return InscripcionCurso.objects.filter(curso=course, usuario=user, completado=False).exists()


def can_submit_activity(user, activity, *, now=None):
    from .module_progression import target_lock_reason
    return (
        not target_lock_reason(activity, user, now=now)
        and active_student(user, activity.course) and activity_state(activity, now=now) == 'open'
        and not Submission.objects.filter(
            assignment__learning_activity=activity, user=user,
        ).exists()
    )


def feedback_visible(activity, submission, *, now=None):
    now = now or timezone.now()
    return bool(
        submission and submission.feedback_published
        and (not activity.feedback_at or activity.feedback_at <= now)
    )


@transaction.atomic
def submit_activity(*, activity_id, course, user, data):
    activity = LearningActivity.objects.select_for_update().get(pk=activity_id, course=course)
    from .campus import campus_settings
    settings = campus_settings(course)
    if not can_read_activity(user, activity):
        raise PermissionDenied
    key = data.get('idempotency_key')
    if settings and not key:
        raise ValidationError(_('Falta el identificador de entrega. Recarga el formulario.'))
    receipt = f'campus-{activity.pk}-{user.pk}-{key}' if key else uuid4().hex
    digest = hashlib.sha256((data.get('text_answer') or '').encode())
    upload = data.get('file')
    if upload:
        if Path(upload.name).suffix.lower() not in FileAsset.ALLOWED_EXTENSIONS:
            raise ValidationError(_('Extensión de archivo no permitida.'))
        if settings and (
            not settings.campus_max_submission_bytes or upload.size > settings.campus_max_submission_bytes
        ):
            raise ValidationError(_('Archivo demasiado grande o límite de subida pendiente de configuración.'))
        digest.update(upload.name.encode())
        for chunk in upload.chunks():
            digest.update(chunk)
        upload.seek(0)
    fingerprint = digest.hexdigest()
    replay = Submission.objects.filter(
        receipt_identifier=receipt, user=user, assignment__learning_activity=activity,
    ).first()
    if replay:
        if replay.metadata.get('payload_sha256') != fingerprint:
            raise ValidationError(_('El identificador ya se usó con otro contenido. Abre un nuevo formulario.'))
        return replay
    now = timezone.now()
    if Submission.objects.filter(assignment__learning_activity=activity, user=user).exists():
        raise ValidationError(_('Ya has enviado esta actividad. No se permiten nuevas entregas.'))
    if not can_submit_activity(user, activity, now=now):
        raise ValidationError(_('No se puede entregar esta actividad fuera del plazo o sin matrícula activa.'))
    assignment, _created = Assignment.objects.get_or_create(
        learning_activity=activity,
        defaults={
            'course': course, 'title': activity.title, 'points': activity.points,
            'status': Assignment.Status.PUBLISHED,
            'submission_types': [Assignment.SubmissionType.TEXT, Assignment.SubmissionType.FILE],
            'max_attempts': 1, 'allow_resubmission': False,
        },
    )
    latest = assignment.submissions.filter(user=user).order_by('-version').first()
    if latest and not assignment.allow_resubmission:
        raise ValidationError(_('Esta actividad no permite reentregas.'))
    if assignment.max_attempts and assignment.submissions.filter(user=user).count() >= assignment.max_attempts:
        raise ValidationError(_('Has agotado las entregas permitidas.'))
    if assignment.available_at and now < assignment.available_at or assignment.lock_at and now >= assignment.lock_at:
        raise ValidationError(_('La tarea no admite entregas en este momento.'))
    if data.get('text_answer') and Assignment.SubmissionType.TEXT not in assignment.submission_types:
        raise ValidationError(_('Esta tarea no admite respuestas de texto.'))
    if upload and Assignment.SubmissionType.FILE not in assignment.submission_types:
        raise ValidationError(_('Esta tarea no admite archivos.'))
    if not data.get('text_answer') and not upload:
        raise ValidationError(_('Escribe una respuesta o adjunta un archivo.'))
    submission = Submission.objects.create(
        assignment=assignment, user=user, version=latest.version + 1 if latest else 1,
        receipt_identifier=receipt, text_answer=data.get('text_answer') or '',
        metadata={'payload_sha256': fingerprint},
    )
    if data.get('file'):
        submission.add_attachment(file=data['file'])
    submission.submit(now=now)
    return submission


@transaction.atomic
def grade_activity(*, activity_id, course, actor, submission_id, data):
    if not can_edit_course(actor, course):
        raise PermissionDenied
    activity = LearningActivity.objects.select_for_update().get(pk=activity_id, course=course)
    if not activity.is_evaluable:
        raise ValidationError(_('Esta actividad no es evaluable.'))
    submission = Submission.objects.select_for_update().get(
        pk=submission_id, assignment__learning_activity=activity,
    )
    from .campus import can_review_submission
    if not can_review_submission(actor, submission):
        raise PermissionDenied(_('No puedes evaluar tu propia entrega ni alumnos de otros grupos.'))
    if not submission.submitted_at:
        raise ValidationError(_('La entrega todavía es un borrador.'))
    grade = Decimal(data['grade'])
    if not grade.is_finite() or grade < 0 or grade > activity.points:
        raise ValidationError(_('La nota debe estar entre cero y los puntos de la actividad.'))
    latest = submission.assignment.submissions.filter(user=submission.user).order_by('-version').first()
    if latest.pk != submission.pk:
        raise ValidationError(_('Hay una entrega más reciente. Evalúa la última versión.'))
    previous = {'grade': str(submission.grade), 'feedback': submission.feedback}
    if submission.graded_at and submission.grade == grade and submission.feedback == data['feedback']:
        return submission
    submission.grade = grade
    submission.feedback = data['feedback']
    submission.graded_at = timezone.now()
    submission.status = Submission.Status.GRADED
    submission.save(update_fields=['grade', 'feedback', 'graded_at', 'status', 'updated_at'])
    submission.publish_feedback()
    AuditEvent.objects.create(
        actor=actor, target_user=submission.user, course=course,
        action=AuditEvent.Action.COURSE_UPDATED,
        metadata={'campus_action': 'activity_graded', 'submission_id': submission.pk,
                  'previous': previous, 'current': {'grade': str(grade), 'feedback': submission.feedback}},
    )
    from .lifecycle import process_activity
    process_activity(activity.pk, now=timezone.now())
    return submission
