from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from .models import GradeEntry, QuizAttempt


@transaction.atomic
def grade_scaled_attempt(quiz, attempt, *, timestamp):
    items = list(quiz.questions.select_related('question_version'))
    versions = attempt.configuration_snapshot.get('question_versions', {})
    if versions and (
        set(versions) != {str(item.pk) for item in items}
        or any(versions[str(item.pk)] != item.question_version_id for item in items)
    ):
        raise ValidationError(_('La configuración de preguntas ha cambiado desde el inicio del intento.'))
    if not items or any(item.metadata.get('grading_scale') != 10 for item in items):
        raise ValidationError(_('La prueba no tiene una configuración homogénea sobre 10.'))
    answers = {answer.question_id: answer for answer in attempt.answers.all()}
    if set(answers) - {item.pk for item in items}:
        raise ValidationError(_('El intento contiene respuestas ajenas a la prueba.'))
    value = Decimal(10) / len(items)
    total = Decimal(0)
    correct_count = wrong_count = 0
    blank_count = len(items) - len(answers)
    for item in items:
        answer = answers.get(item.pk)
        metadata = attempt.configuration_snapshot.get('question_grading', {}).get(str(item.pk), item.metadata)
        blank_deduction = Decimal(str(metadata.get('blank_deduction', '0')))
        if answer is None:
            total -= blank_deduction
            continue
        version = item.question_version
        if answer.question_version_id != version.pk:
            raise ValidationError(_('La respuesta no corresponde a la versión fijada en la prueba.'))
        submitted = version._extract_answer_value(answer.response)
        blank = submitted is None or (isinstance(submitted, str) and not submitted.strip()) or submitted == [] or submitted == {}
        if not blank and not isinstance(submitted, str):
            raise ValidationError(_('Una pregunta de opción única necesita una sola respuesta de texto.'))
        correct = not blank and version._normalise_text(submitted) in version._normalise_text(version.correct_answers)
        if blank:
            blank_count += 1
        elif correct:
            correct_count += 1
        else:
            wrong_count += 1
        deduction = Decimal(str(metadata['error_deduction'])) if 'error_deduction' in metadata else value / 3
        score = -blank_deduction if blank else value if correct else -deduction if metadata.get('penalize_errors') else Decimal(0)
        answer.score = score.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        answer.is_correct = correct
        answer.manual_review = False
        answer.submitted_at = answer.submitted_at or timestamp
        answer.save(update_fields=['score', 'is_correct', 'manual_review', 'submitted_at', 'updated_at'])
        total += score
    total = max(Decimal(0), min(Decimal(10), total)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    threshold = Decimal(str(attempt.configuration_snapshot.get('passing_score', quiz.passing_score)))
    attempt.score = total
    attempt.is_passed = total >= threshold
    attempt.status = QuizAttempt.Status.GRADED
    attempt.graded_at = timestamp
    attempt.correct_count = correct_count
    attempt.wrong_count = wrong_count
    attempt.blank_count = blank_count
    attempt.save(update_fields=[
        'score', 'is_passed', 'status', 'graded_at', 'correct_count', 'wrong_count', 'blank_count', 'updated_at',
    ])
    published = bool(
        attempt.results_released_at or (quiz.publish_grade_at and timestamp >= quiz.publish_grade_at)
    )
    entry, _created = GradeEntry.objects.update_or_create(attempt=attempt, defaults={
        'user': attempt.user, 'course': quiz.course, 'grade': total, 'max_grade': Decimal(10),
        'status': GradeEntry.Status.PUBLISHED if published else GradeEntry.Status.DRAFT,
        'is_published': published,
        'published_at': attempt.results_released_at or (timestamp if published else None),
    })
    return entry
