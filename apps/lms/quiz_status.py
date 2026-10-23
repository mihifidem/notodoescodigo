from django.utils import timezone
from django.db.models import Q
from django.utils.translation import gettext as _

from .lifecycle import course_end
from .models import GradeEntry, Quiz, QuizAttempt


def quiz_grade_release_filter(*, attempt_path='attempt__', now=None):
    now = now or timezone.now()
    return (
        Q(**{f'{attempt_path}quiz__publish_grade_at__lte': now})
        | Q(**{f'{attempt_path}results_released_at__lte': now})
    )


def quiz_grade_visible(entry, *, now=None):
    now = now or timezone.now()
    return bool(
        entry and entry.status == GradeEntry.Status.PUBLISHED and not entry.is_hidden
        and (
            (entry.attempt.quiz.publish_grade_at and entry.attempt.quiz.publish_grade_at <= now)
            or (entry.attempt.results_released_at and entry.attempt.results_released_at <= now)
        )
    )


def quiz_solutions_visible(quiz, *, student=None, now=None):
    now = now or timezone.now()
    if student is not None and quiz.attempts.filter(
        user=student, status=QuizAttempt.Status.GRADED,
        results_released_at__lte=now, grade_entry__status=GradeEntry.Status.PUBLISHED,
        grade_entry__is_hidden=False,
    ).exists():
        return True
    released = bool(quiz.answers_released_at and quiz.publish_answers_at and quiz.publish_answers_at <= now)
    return released and (
        student is None or quiz.attempts.filter(user=student, status=QuizAttempt.Status.GRADED).exists()
    )


def quiz_is_closed(quiz, *, now=None):
    now = now or timezone.now()
    dates = [date for date in (quiz.available_until, quiz.due_at, course_end(quiz.course)) if date]
    return quiz.status == Quiz.Status.CLOSED or (
        quiz.status == Quiz.Status.PUBLISHED
        and (quiz.course.status == 'archived' or bool(dates and min(dates) <= now))
    )


def teacher_quiz_status(quiz, *, now=None, student=None):
    now = now or timezone.now()
    labels = []
    if quiz.status == Quiz.Status.DRAFT:
        labels.append(_('Borrador'))
    elif quiz.status == Quiz.Status.SCHEDULED:
        labels.append(_('Programada'))
    elif quiz_is_closed(quiz, now=now):
        labels.append(_('Cerrada'))
    elif quiz.status == Quiz.Status.PUBLISHED:
        if quiz.course.status != 'published':
            labels.append(_('Pendiente de publicación del curso'))
        elif quiz.available_from and quiz.available_from > now:
            labels.append(_('Pendiente de apertura'))
        else:
            labels.append(_('Realizándose (abierta)'))
    if quiz_solutions_visible(quiz, student=student, now=now):
        labels.append(_('Solución publicada'))
    grades = GradeEntry.objects.filter(
        quiz_grade_release_filter(now=now),
        attempt__quiz=quiz, status=GradeEntry.Status.PUBLISHED, is_hidden=False,
    )
    if student is not None:
        grades = grades.filter(user=student)
    if grades.exists():
        labels.append(_('Notas publicadas'))
    return labels
