from django.utils import timezone
from django.utils.translation import gettext as _

from .lifecycle import course_end
from .models import GradeEntry, Quiz, QuizAttempt


def quiz_solutions_visible(quiz, *, student=None, now=None):
    now = now or timezone.now()
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
    if quiz.answers_released_at and quiz.publish_answers_at and quiz.publish_answers_at <= now:
        labels.append(_('Solución publicada'))
    if quiz.publish_grade_at and quiz.publish_grade_at <= now:
        grades = GradeEntry.objects.filter(
            attempt__quiz=quiz, status=GradeEntry.Status.PUBLISHED, is_hidden=False,
        )
        if student is not None:
            grades = grades.filter(user=student)
        if grades.exists():
            labels.append(_('Notas publicadas'))
    return labels
