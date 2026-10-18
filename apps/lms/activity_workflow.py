from pathlib import Path
from uuid import uuid4

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.courses.models import InscripcionCurso
from .models import Assignment, CourseMembership, FileAsset, LearningActivity, Submission
from .policies import can_edit_course, is_course_member


class ActivitySubmissionForm(forms.Form):
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
    now = timezone.now()
    if not can_submit_activity(user, activity, now=now):
        raise ValidationError(_('No se puede entregar esta actividad fuera del plazo o sin matrícula activa.'))
    assignment, _created = Assignment.objects.get_or_create(
        learning_activity=activity,
        defaults={
            'course': course, 'title': activity.title, 'points': activity.points,
            'status': Assignment.Status.PUBLISHED,
            'submission_types': [Assignment.SubmissionType.TEXT, Assignment.SubmissionType.FILE],
            'max_attempts': 0, 'allow_resubmission': True,
        },
    )
    latest = assignment.submissions.filter(user=user).order_by('-version').first()
    submission = Submission.objects.create(
        assignment=assignment, user=user, version=latest.version + 1 if latest else 1,
        receipt_identifier=uuid4().hex, text_answer=data.get('text_answer') or '',
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
    latest = submission.assignment.submissions.filter(user=submission.user).order_by('-version').first()
    if latest.pk != submission.pk:
        raise ValidationError(_('Hay una entrega más reciente. Evalúa la última versión.'))
    submission.grade = data['grade']
    submission.feedback = data['feedback']
    submission.graded_at = timezone.now()
    submission.status = Submission.Status.GRADED
    submission.save(update_fields=['grade', 'feedback', 'graded_at', 'status', 'updated_at'])
    submission.publish_feedback()
    from .lifecycle import process_activity
    process_activity(activity.pk, now=timezone.now())
    return submission
