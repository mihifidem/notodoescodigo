from collections import defaultdict

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.translation import gettext as _

from .forms import QuizFeedbackForm
from .lifecycle import student_recipients
from .models import AuditEvent, GradeEntry, GradeItem, LearningActivity, Notification, NotificationEvent, Quiz, QuizAttempt, Submission
from .policies import can_edit_course
from .quiz_status import quiz_grade_release_filter, quiz_is_closed
from .services import queue_notification_event


@transaction.atomic
def release_attempt_results(*, course, actor, quiz_id, attempt_id):
    if not can_edit_course(actor, course):
        raise PermissionDenied
    quiz = Quiz.objects.select_for_update().get(pk=quiz_id, course=course)
    attempt = QuizAttempt.objects.select_for_update().get(pk=attempt_id, quiz=quiz)
    from .campus import campus_settings, scoped_students
    allowed_ids = {student.pk for student in student_recipients(course, section_id=quiz.section_id)}
    if (
        attempt.user_id == actor.pk or attempt.user_id not in allowed_ids
        or (campus_settings(course) and not scoped_students(actor, course).filter(user_id=attempt.user_id).exists())
    ):
        raise PermissionDenied
    if quiz.publish_grade_at:
        raise ValidationError(_('Esta prueba tiene una fecha de publicación de notas programada.'))
    if attempt.status != QuizAttempt.Status.GRADED or attempt.answers.filter(manual_review=True).exists():
        raise ValidationError(_('Solo se pueden enviar resultados de intentos calificados sin revisión pendiente.'))
    entry = GradeEntry.objects.select_for_update().filter(attempt=attempt, user_id=attempt.user_id, course=course).first()
    if entry is None or entry.is_hidden or entry.status not in (GradeEntry.Status.DRAFT, GradeEntry.Status.PUBLISHED):
        raise ValidationError(_('El intento no tiene una nota disponible para publicar.'))
    if attempt.results_released_at:
        return
    now = timezone.now()
    entry.status = GradeEntry.Status.PUBLISHED
    entry.is_published = True
    entry.published_at = now
    entry.save(update_fields=['status', 'is_published', 'published_at', 'updated_at'])
    attempt.results_released_at = now
    attempt.save(update_fields=['results_released_at', 'updated_at'])
    AuditEvent.objects.create(
        course=course, actor=actor, target_user=attempt.user,
        action=AuditEvent.Action.COURSE_UPDATED,
        metadata={'quiz_action': 'results_released', 'quiz_id': quiz.pk, 'attempt_id': attempt.pk},
    )
    queue_notification_event(
        course=course, actor=actor, target_user=attempt.user,
        event_type=NotificationEvent.Type.GRADE,
        title=_('Resultado y solución disponibles'),
        message=_('Tu nota de %(quiz)s es %(grade)s / %(maximum)s. Ya puedes consultar el resultado y la solución.') % {
            'quiz': quiz.title, 'grade': entry.grade, 'maximum': entry.max_grade,
        },
        dedupe_key=f'grade:{entry.pk}:published',
        metadata={
            'quiz_id': quiz.pk, 'attempt_id': attempt.pk,
            'url': reverse('lms:quiz_detail', kwargs={'course_id': course.pk, 'quiz_id': quiz.pk}),
        },
    )
    from .consumers import broadcast_course_update
    transaction.on_commit(lambda: broadcast_course_update(course, 'updated', item_type='lifecycle'))


def quiz_result_groups(course, user, *, teacher_view, feedback_form=None):
    now = timezone.now()
    quizzes = list(Quiz.objects.filter(course=course).select_related('course__lms_settings').order_by('title', 'pk'))
    students_by_section = {
        section_id: student_recipients(course, section_id=section_id)
        for section_id in {quiz.section_id for quiz in quizzes}
    }
    if not teacher_view:
        quizzes = [
            quiz for quiz in quizzes
            if quiz.status in (Quiz.Status.PUBLISHED, Quiz.Status.CLOSED)
            and any(student.pk == user.pk for student in students_by_section[quiz.section_id])
        ]
    quiz_ids = [quiz.pk for quiz in quizzes]
    attempts = QuizAttempt.objects.filter(quiz_id__in=quiz_ids).exclude(
        status=QuizAttempt.Status.DRAFT, started_at__isnull=True, submitted_at__isnull=True,
    ).select_related('user').order_by('created_at', 'pk')
    grades = GradeEntry.objects.filter(course=course, attempt__quiz_id__in=quiz_ids).select_related('attempt')
    feedback = Notification.objects.filter(
        course=course, event__event_type=NotificationEvent.Type.FEEDBACK,
        event__metadata__kind='quiz_feedback',
    ).select_related('event__actor').order_by('created_at', 'pk')
    if not teacher_view:
        attempts = attempts.filter(user=user)
        grades = grades.filter(
            quiz_grade_release_filter(now=now),
            user=user, status=GradeEntry.Status.PUBLISHED, is_hidden=False,
        )
        feedback = feedback.filter(recipient=user)
    grades_by_attempt = {grade.attempt_id: grade for grade in grades}
    attempts_by_student = defaultdict(list)
    users_by_quiz = defaultdict(dict)
    for attempt in attempts:
        attempts_by_student[(attempt.quiz_id, attempt.user_id)].append(attempt)
        users_by_quiz[attempt.quiz_id][attempt.user_id] = attempt.user
    feedback_by_student = defaultdict(list)
    for notification in feedback:
        feedback_by_student[(notification.event.metadata['quiz_id'], notification.recipient_id)].append(notification)
    groups = []
    for quiz in quizzes:
        students = users_by_quiz[quiz.pk]
        students.update({
            student.pk: student for student in students_by_section[quiz.section_id]
            if teacher_view or student.pk == user.pk
        })
        rows = []
        for student in sorted(students.values(), key=lambda person: (person.get_full_name() or person.username, person.pk)):
            student_attempts = attempts_by_student[(quiz.pk, student.pk)]
            form = None
            if (
                teacher_view and quiz.status in (Quiz.Status.PUBLISHED, Quiz.Status.CLOSED)
                and student in students_by_section[quiz.section_id]
            ):
                form = QuizFeedbackForm(
                    initial={'quiz_id': quiz.pk, 'student_id': student.pk},
                    auto_id=f'feedback_{quiz.pk}_{student.pk}_%s',
                )
                if feedback_form and (
                    feedback_form.data.get('quiz_id') == str(quiz.pk)
                    and feedback_form.data.get('student_id') == str(student.pk)
                ):
                    form = feedback_form
            rows.append({
                'student': student,
                'results': [{'attempt': attempt, 'grade': grades_by_attempt.get(attempt.pk)} for attempt in student_attempts],
                'not_performed': not student_attempts and quiz_is_closed(quiz, now=now),
                'feedback': feedback_by_student[(quiz.pk, student.pk)],
                'feedback_form': form,
            })
        groups.append({
            'quiz': quiz, 'rows': rows,
            'feedback_error': bool(feedback_form and feedback_form.data.get('quiz_id') == str(quiz.pk)),
        })
    return groups


def course_gradebook(course, groups, *, teacher_view, user=None, student_query='', activity_query=''):
    """Build the matrix from authorized result groups, retaining the latest attempt."""
    columns = []
    students = {}
    cells = {}
    for group in groups:
        quiz = group['quiz']
        key = f'quiz-{quiz.pk}'
        columns.append({
            'key': key, 'title': quiz.title, 'max_grade': None,
            'url': reverse('lms:quiz_detail', kwargs={'course_id': course.pk, 'quiz_id': quiz.pk}),
        })
        for row in group['rows']:
            student = row['student']
            students[student.pk] = student
            result = row['results'][-1] if row['results'] else None
            grade = result['grade'] if result else None
            if grade:
                columns[-1]['max_grade'] = grade.max_grade
            cells[(student.pk, key)] = {
                'grade': grade.grade if grade else None,
                'status': grade.get_status_display() if grade else (
                    _('Resultados pendientes de publicación') if result else (
                        _('No realizada') if row['not_performed'] else _('Pendiente de realización')
                    )
                ),
            }

    if teacher_view:
        students.update({student.pk: student for student in student_recipients(course)})
    elif user is not None:
        students[user.pk] = user
    from .activity_workflow import feedback_visible
    activities = LearningActivity.objects.filter(course=course, is_evaluable=True).order_by('pk')
    if not teacher_view:
        activities = activities.exclude(status=LearningActivity.Status.DRAFT)
    for activity in activities:
        key = f'activity-{activity.pk}'
        columns.append({
            'key': key, 'title': activity.title, 'max_grade': activity.points,
            'url': reverse('lms:course_activity_detail', kwargs={'course_id': course.pk, 'activity_id': activity.pk}),
        })
        submissions = Submission.objects.filter(
            assignment__learning_activity=activity, user_id__in=students,
        ).order_by('user_id', 'version', 'pk')
        for submission in submissions:
            visible = teacher_view or feedback_visible(activity, submission)
            cells[(submission.user_id, key)] = {
                'grade': submission.grade if visible else None,
                'status': _('Evaluada') if visible and submission.grade is not None else _('Entregada, pendiente de evaluación'),
            }

    # Other non-quiz entries are currently available only in the teacher's book.
    # Student release/section policies remain in the existing result groups.
    if teacher_view:
        items = list(GradeItem.objects.filter(
            course=course, is_active=True, quiz__isnull=True,
        ).exclude(source_type=GradeItem.SourceType.QUIZ))
        entries = GradeEntry.objects.filter(
            course=course, item__in=items, user_id__in=students,
        ).order_by('updated_at', 'pk')
        for item in items:
            columns.append({
                'key': f'item-{item.pk}', 'title': item.title,
                'max_grade': item.max_grade, 'url': '',
            })
        for entry in entries:
            cells[(entry.user_id, f'item-{entry.item_id}')] = {
                'grade': None if entry.is_exempt else entry.grade,
                'status': entry.get_status_display(),
            }

    columns = [
        column for column in columns
        if activity_query.casefold() in column['title'].casefold()
    ]
    rows = []
    for student in sorted(students.values(), key=lambda person: (
        (person.get_full_name() or person.username).casefold(), person.pk,
    )):
        name = student.get_full_name() or student.username
        if student_query.casefold() not in f'{name} {student.username}'.casefold():
            continue
        rows.append({
            'student': student,
            'name': name,
            'cells': [
                cells.get((student.pk, column['key']), {
                    'grade': None, 'status': _('Sin calificación'),
                })
                for column in columns
            ],
        })
    return {'columns': columns, 'rows': rows}


@transaction.atomic
def send_quiz_feedback(*, course, actor, quiz_id, student_id, message):
    if not can_edit_course(actor, course):
        raise PermissionDenied
    quiz = Quiz.objects.select_for_update().filter(course=course, pk=quiz_id).first()
    if not quiz or quiz.status not in (Quiz.Status.PUBLISHED, Quiz.Status.CLOSED):
        raise ValidationError(_('Selecciona una prueba publicada de este curso.'))
    student = next(
        (student for student in student_recipients(course, section_id=quiz.section_id) if student.pk == student_id),
        None,
    )
    if student is None:
        raise ValidationError(_('El destinatario debe ser un alumno de esta prueba.'))
    title = (_('Feedback de %(quiz)s') % {'quiz': quiz.title})[:200]
    event = queue_notification_event(
        course=course, actor=actor, target_user=student, recipients=[student],
        event_type=NotificationEvent.Type.FEEDBACK, title=title, message=message,
        metadata={
            'source': 'lifecycle', 'kind': 'quiz_feedback', 'quiz_id': quiz.pk,
            'url': reverse('lms:course_area', kwargs={'course_id': course.pk, 'area': 'grades'}),
        },
        dedupe_key=f'quiz-feedback:{quiz.pk}:{student.pk}:{get_random_string(32)}',
    )
    return event
