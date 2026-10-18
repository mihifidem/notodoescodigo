import html
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.courses.models import InscripcionCurso
from .lifecycle import course_end, process_quiz
from .models import CourseMembership, Question, Quiz, QuizAnswer, QuizAttempt


def require_student(user, quiz):
    if not user.is_authenticated or not user.is_active:
        raise PermissionDenied
    membership = CourseMembership.objects.filter(course=quiz.course, user=user).first()
    if membership:
        if membership.role != CourseMembership.Role.STUDENT or membership.status not in (
            CourseMembership.Status.ACTIVE, CourseMembership.Status.COMPLETED,
        ):
            raise PermissionDenied
        if quiz.section_id and quiz.section_id != membership.section_id:
            raise PermissionDenied
    elif quiz.section_id or not InscripcionCurso.objects.filter(curso=quiz.course, usuario=user).exists():
        raise PermissionDenied
    from .module_progression import require_target_access
    require_target_access(quiz, user)


def attempt_deadline(quiz, attempt=None):
    dates = [date for date in (quiz.due_at, quiz.available_until, course_end(quiz.course)) if date]
    from .module_progression import sequential_enabled, target_content
    content = target_content(quiz)
    if content and sequential_enabled(quiz.course):
        schedule = getattr(content.module, 'learning_schedule', None)
        if schedule and schedule.ends_at:
            dates.append(schedule.ends_at)
    if attempt and attempt.deadline_at:
        dates.append(attempt.deadline_at)
    return min(dates) if dates else None


def validate_quiz_items(quiz):
    items = list(quiz.questions.select_related('question_version__question').order_by('order', 'pk'))
    if not items or any(
        item.metadata.get('grading_scale') != 10 or
        item.question_version.question.question_type != Question.Type.SINGLE_CHOICE
        for item in items
    ):
        raise ValidationError(_('Esta prueba necesita una configuración de opción única sobre 10 antes de realizarse.'))
    return items


@transaction.atomic
def begin_attempt(*, user, quiz_id, course_id, now=None):
    now = now or timezone.now()
    quiz = Quiz.objects.select_for_update().select_related('course').get(pk=quiz_id, course_id=course_id)
    require_student(user, quiz)
    get_user_model().objects.select_for_update().get(pk=user.pk)
    active = quiz.attempts.select_for_update().filter(user=user, status=QuizAttempt.Status.IN_PROGRESS).first()
    if active:
        active_deadline = attempt_deadline(quiz, active)
        if (quiz.status == Quiz.Status.CLOSED or quiz.course.status == 'archived'
                or (active_deadline and now >= active_deadline)):
            return finish_attempt(quiz, active, now=now)
    deadline = attempt_deadline(quiz)
    if (quiz.status != Quiz.Status.PUBLISHED or quiz.course.status != 'published' or
            (quiz.available_from and now < quiz.available_from) or (deadline and now >= deadline)):
        raise ValidationError(_('La prueba no está disponible en este momento.'))
    items = validate_quiz_items(quiz)
    if active:
        if not quiz.allow_resume:
            raise ValidationError(_('Esta prueba no permite reanudar un intento.'))
        return active
    if quiz.attempts.filter(user=user).count() >= quiz.max_attempts:
        raise ValidationError(_('Se ha alcanzado el límite de intentos.'))
    if quiz.shuffle_questions:
        items = quiz.question_order(user=user)
    if quiz.duration_minutes:
        duration_end = now + timedelta(minutes=quiz.duration_minutes)
        deadline = min(deadline, duration_end) if deadline else duration_end
    return QuizAttempt.objects.create(
        quiz=quiz, user=user, status=QuizAttempt.Status.IN_PROGRESS, started_at=now, deadline_at=deadline,
        configuration_snapshot={
            'passing_score': str(quiz.passing_score), 'duration_minutes': quiz.duration_minutes,
            'question_order': [item.pk for item in items],
            'question_versions': {str(item.pk): item.question_version_id for item in items},
            'question_grading': {str(item.pk): item.metadata for item in items},
        },
    )


def attempt_questions(attempt):
    items = validate_quiz_items(attempt.quiz)
    by_id = {item.pk: item for item in items}
    order = attempt.configuration_snapshot.get('question_order', list(by_id))
    if set(order) != set(by_id) or len(order) != len(by_id):
        raise ValidationError(_('La configuración de preguntas ha cambiado desde el inicio del intento.'))
    versions = attempt.configuration_snapshot.get('question_versions', {})
    answers = {answer.question_id: answer for answer in attempt.answers.all()}
    result = []
    for pk in order:
        item = by_id[pk]
        if versions and versions.get(str(pk)) != item.question_version_id:
            raise ValidationError(_('La versión de una pregunta ha cambiado desde el inicio del intento.'))
        prompt = item.question_version.prompt
        answer = answers.get(pk)
        response = item.question_version._extract_answer_value(answer.response) if answer else ''
        result.append({
            'id': pk, 'prompt': html.unescape(prompt) if item.question_version.metadata.get('plain_text') else prompt,
            'options': [{'value': str(index), 'text': text, 'selected': response == text}
                        for index, text in enumerate(item.question_version.options)],
            'blank': not answer or not response,
        })
    return result


@transaction.atomic
def finish_attempt(quiz, attempt, *, now=None):
    now = now or timezone.now()
    if attempt.status != QuizAttempt.Status.IN_PROGRESS:
        return attempt
    attempt.status = QuizAttempt.Status.SUBMITTED
    attempt.submitted_at = now
    attempt.save(update_fields=['status', 'submitted_at', 'updated_at'])
    quiz.evaluate_attempt(attempt, now=now)
    process_quiz(quiz.pk, now=now)
    return attempt


@transaction.atomic
def update_attempt(*, user, quiz_id, course_id, attempt_id, data, submit=False, now=None):
    now = now or timezone.now()
    quiz = Quiz.objects.select_for_update().select_related('course').get(pk=quiz_id, course_id=course_id)
    require_student(user, quiz)
    attempt = QuizAttempt.objects.select_for_update().get(pk=attempt_id, quiz=quiz, user=user)
    if attempt.status != QuizAttempt.Status.IN_PROGRESS:
        if submit:
            return attempt, True
        raise ValidationError(_('No se puede modificar una prueba ya entregada.'))
    deadline = attempt_deadline(quiz, attempt)
    if quiz.status == Quiz.Status.CLOSED or quiz.course.status == 'archived' or (deadline and now >= deadline):
        finish_attempt(quiz, attempt, now=now)
        return attempt, True
    if (quiz.status != Quiz.Status.PUBLISHED or quiz.course.status != 'published'
            or (quiz.available_from and now < quiz.available_from)):
        raise ValidationError(_('La prueba no está disponible.'))
    attempt_questions(attempt)
    items = {item.pk: item for item in quiz.questions.select_related('question_version')}
    submitted = {}
    for name in data:
        if not name.startswith('question_'):
            continue
        try:
            pk = int(name.removeprefix('question_'))
        except ValueError as error:
            raise ValidationError(_('Identificador de pregunta inválido.')) from error
        if pk not in items or pk in submitted or len(data.getlist(name)) != 1:
            raise ValidationError(_('La respuesta contiene una pregunta ajena o varias opciones.'))
        choice = data[name]
        if choice == '':
            submitted[pk] = ''
        else:
            try:
                index = int(choice)
            except ValueError as error:
                raise ValidationError(_('Selecciona una opción válida.')) from error
            options = items[pk].question_version.options
            if index < 0 or index >= len(options):
                raise ValidationError(_('Selecciona una opción válida.'))
            submitted[pk] = options[index]
    for pk, response in submitted.items():
        QuizAnswer.objects.update_or_create(
            attempt=attempt, question=items[pk],
            defaults={'question_version': items[pk].question_version, 'response': response, 'autosave': not submit},
        )
    attempt.last_autosave_at = now
    attempt.save(update_fields=['last_autosave_at', 'updated_at'])
    if submit:
        finish_attempt(quiz, attempt, now=now)
    return attempt, submit
