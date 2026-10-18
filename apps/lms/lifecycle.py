from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.courses.models import Curso, InscripcionCurso, Progreso
from .models import (
    AuditEvent, CourseMembership, CourseModule, CourseSettingsLMS, GradeEntry,
    LearningActivity, ModuleProgress, NotificationEvent, Quiz, QuizAttempt, Submission,
)
from .services import queue_notification_event


def teacher_recipients(course):
    return list(get_user_model().objects.filter(
        course_memberships__course=course,
        course_memberships__role__in=(
            CourseMembership.Role.OWNER, CourseMembership.Role.TEACHER, CourseMembership.Role.ASSISTANT,
        ),
        course_memberships__status=CourseMembership.Status.ACTIVE, is_active=True,
    ).distinct())


def student_recipients(course, *, section_id=None):
    memberships = CourseMembership.objects.filter(
        course=course, role=CourseMembership.Role.STUDENT,
        status__in=(CourseMembership.Status.ACTIVE, CourseMembership.Status.COMPLETED),
    )
    if section_id:
        memberships = memberships.filter(section_id=section_id)
    ids = set(memberships.values_list('user_id', flat=True))
    if not section_id:
        ids.update(InscripcionCurso.objects.filter(curso=course).exclude(
            usuario_id__in=CourseMembership.objects.filter(course=course).values('user_id'),
        ).values_list('usuario_id', flat=True))
    return list(get_user_model().objects.filter(pk__in=ids, is_active=True))


@transaction.atomic
def notify_once(*, course, key, title, message, recipients, url, event_type=NotificationEvent.Type.NOTE):
    event, created = NotificationEvent.objects.get_or_create(
        dedupe_key=key, defaults={
            'course': course, 'event_type': event_type, 'title': title,
            'message': message, 'metadata': {'source': 'lifecycle', 'url': url},
        },
    )
    if not created or not recipients:
        return event
    queue_notification_event(
        course=course, event_type=event_type, title=title, message=message,
        recipients=recipients, dedupe_key=key, metadata=event.metadata,
    )
    from .consumers import broadcast_course_update
    transaction.on_commit(lambda: broadcast_course_update(course, 'updated', item_type='lifecycle'))
    return event


def course_end(course):
    course_settings = getattr(course, 'lms_settings', None)
    if course_settings and course_settings.ends_at:
        return course_settings.ends_at
    if not course.fecha_fin:
        return None
    try:
        zone = ZoneInfo(course.zona_horaria)
    except ZoneInfoNotFoundError as error:
        raise ValidationError(_('La zona horaria del curso no es válida.')) from error
    return datetime.combine(course.fecha_fin + timedelta(days=1), time.min, tzinfo=zone)


@transaction.atomic
def process_course(course_id, *, now):
    course = Curso.objects.select_for_update().get(pk=course_id)
    url = reverse('lms:course_detail', kwargs={'course_id': course.pk})
    recipients = student_recipients(course)
    end = course_end(course)
    if course.status == 'scheduled':
        if not course.fecha_publicacion:
            raise ValidationError(_('Un curso programado necesita fecha de publicación.'))
        if course.fecha_publicacion <= now:
            course.status = 'published'
            course.activo = True
            course.save(update_fields=['status', 'activo', 'actualizado_en'])
            CourseSettingsLMS.objects.update_or_create(
                course=course, defaults={'archived': False},
            )
            AuditEvent.objects.create(course=course, action=AuditEvent.Action.COURSE_PUBLISHED,
                                      metadata={'automatic': True})
            if not end or end > now:
                notify_once(course=course, key=f'course:{course.pk}:published:{course.fecha_publicacion.isoformat()}',
                            title=_('Curso disponible'), message=_('Ya puedes acceder a %(course)s.') % {'course': course.nombre},
                            recipients=recipients, url=url)
    if course.status == 'published' and end and end <= now:
        course.status = 'archived'
        course.activo = False
        course.matriculacion = 'closed'
        course.save(update_fields=['status', 'activo', 'matriculacion', 'actualizado_en'])
        CourseSettingsLMS.objects.update_or_create(
            course=course, defaults={'archived': True, 'enrollment_open': False},
        )
        AuditEvent.objects.create(course=course, action=AuditEvent.Action.COURSE_UPDATED,
                                  metadata={'automatic': True, 'course_action': 'closed'})
        notify_once(course=course, key=f'course:{course.pk}:closed:{end.isoformat()}',
                    title=_('Curso finalizado'), message=_('Ha terminado el plazo de %(course)s. Tu historial se conserva.') % {'course': course.nombre},
                    recipients=recipients, url=url, event_type=NotificationEvent.Type.DEADLINE)


@transaction.atomic
def process_quiz(quiz_id, *, now):
    quiz = Quiz.objects.select_for_update().select_related('course').get(pk=quiz_id)
    if quiz.course.status not in ('published', 'archived'):
        return
    url = reverse('lms:quiz_detail', kwargs={'course_id': quiz.course_id, 'quiz_id': quiz.pk})
    recipients = student_recipients(quiz.course, section_id=quiz.section_id)
    if quiz.status == Quiz.Status.SCHEDULED and quiz.available_from and quiz.available_from <= now:
        quiz.status = Quiz.Status.PUBLISHED
        quiz.save(update_fields=['status', 'updated_at'])
        AuditEvent.objects.create(course=quiz.course, action=AuditEvent.Action.COURSE_UPDATED,
                                  metadata={'automatic': True, 'quiz_id': quiz.pk, 'quiz_action': 'publish'})
    ends = [moment for moment in (quiz.available_until, quiz.due_at, course_end(quiz.course)) if moment]
    end = min(ends) if ends else None
    if quiz.status == Quiz.Status.PUBLISHED:
        if (end and end <= now) or quiz.course.status == 'archived':
            quiz.status = Quiz.Status.CLOSED
            quiz.save(update_fields=['status', 'updated_at'])
            AuditEvent.objects.create(course=quiz.course, action=AuditEvent.Action.COURSE_UPDATED,
                                      metadata={'automatic': True, 'quiz_id': quiz.pk, 'quiz_action': 'close'})
            grace = AuditEvent.objects.filter(
                course=quiz.course, metadata__quiz_id=quiz.pk, metadata__quiz_action='reopened',
            ).order_by('-pk').values_list('pk', flat=True).first()
            closure_key = f'quiz:{quiz.pk}:closed'
            if grace:
                closure_key += f':grace:{grace}'
            notify_once(course=quiz.course, key=closure_key, title=_('Prueba finalizada'),
                        message=_('Ha finalizado %(quiz)s.') % {'quiz': quiz.title},
                        recipients=recipients, url=url, event_type=NotificationEvent.Type.DEADLINE)
        elif not quiz.available_from or quiz.available_from <= now:
            notify_once(course=quiz.course, key=f'quiz:{quiz.pk}:opened', title=_('Prueba disponible'),
                        message=_('Ya está disponible %(quiz)s.') % {'quiz': quiz.title},
                        recipients=recipients, url=url)
    if quiz.status not in (Quiz.Status.PUBLISHED, Quiz.Status.CLOSED):
        return
    if quiz.status == Quiz.Status.CLOSED:
        grace = AuditEvent.objects.filter(
            course=quiz.course, metadata__quiz_id=quiz.pk, metadata__quiz_action='reopened',
        ).order_by('-pk').values_list('pk', flat=True).first()
        key = f'quiz:{quiz.pk}:closed' + (f':grace:{grace}' if grace else '')
        notify_once(
            course=quiz.course, key=key, title=_('Prueba finalizada'),
            message=_('Ha finalizado %(quiz)s.') % {'quiz': quiz.title},
            recipients=recipients, url=url, event_type=NotificationEvent.Type.DEADLINE,
        )
    attempts = quiz.attempts.select_for_update().filter(
        Q(status=QuizAttempt.Status.SUBMITTED) |
        Q(status=QuizAttempt.Status.IN_PROGRESS, deadline_at__lte=now) |
        Q(status=QuizAttempt.Status.IN_PROGRESS, quiz__status=Quiz.Status.CLOSED),
    ).exclude(answers__manual_review=True).distinct()
    for attempt in attempts:
        if not attempt.submitted_at:
            attempt.submitted_at = now
            attempt.save(update_fields=['submitted_at', 'updated_at'])
        quiz.evaluate_attempt(attempt, now=now)
    if quiz.publish_answers_at and quiz.publish_answers_at <= now and not quiz.answers_released_at:
        quiz.answers_released_at = now
        quiz.save(update_fields=['answers_released_at', 'updated_at'])
        notify_once(course=quiz.course, key=f'quiz:{quiz.pk}:answers', title=_('Respuestas publicadas'),
                    message=_('Puedes consultar las respuestas de %(quiz)s.') % {'quiz': quiz.title},
                    recipients=recipients, url=url, event_type=NotificationEvent.Type.FEEDBACK)
    if quiz.publish_grade_at and quiz.publish_grade_at <= now:
        for entry in GradeEntry.objects.select_for_update().filter(
            attempt__quiz=quiz, attempt__status=QuizAttempt.Status.GRADED, is_hidden=False,
            status__in=(GradeEntry.Status.DRAFT, GradeEntry.Status.PUBLISHED),
        ).select_related('user'):
            if entry.status != GradeEntry.Status.PUBLISHED or not entry.is_published:
                entry.status = GradeEntry.Status.PUBLISHED
                entry.is_published = True
                entry.published_at = now
                entry.save(update_fields=['status', 'is_published', 'published_at', 'updated_at'])
            notify_once(course=quiz.course, key=f'grade:{entry.pk}:published', title=_('Nota publicada'),
                        message=_('Tu nota de %(quiz)s es %(grade)s / %(maximum)s.') % {
                            'quiz': quiz.title, 'grade': entry.grade, 'maximum': entry.max_grade,
                        }, recipients=[entry.user], url=url, event_type=NotificationEvent.Type.GRADE)
        if quiz.status == Quiz.Status.CLOSED:
            performed_ids = set(quiz.attempts.exclude(
                status=QuizAttempt.Status.DRAFT, started_at__isnull=True, submitted_at__isnull=True,
            ).values_list('user_id', flat=True))
            for student in recipients:
                if student.pk not in performed_ids:
                    notify_once(
                        course=quiz.course, key=f'quiz:{quiz.pk}:no-show:{student.pk}',
                        title=_('Prueba no realizada'),
                        message=_('No has realizado %(quiz)s (%(reference)s). El plazo ha finalizado; no hay nota registrada.') % {
                            'quiz': quiz.title, 'reference': quiz.reference_code,
                        },
                        recipients=[student], url=url, event_type=NotificationEvent.Type.GRADE,
                    )


@transaction.atomic
def process_activity(activity_id, *, now):
    from .activity_workflow import activity_state, feedback_visible

    activity = LearningActivity.objects.select_for_update().select_related('course').get(pk=activity_id)
    url = reverse('lms:course_activity_detail', kwargs={
        'course_id': activity.course_id, 'activity_id': activity.pk,
    })
    notify_once(
        course=activity.course, key=f'activity:{activity.pk}:created',
        title=_('Actividad creada'), message=activity.title,
        recipients=teacher_recipients(activity.course), url=url,
    )
    if activity.course.status not in ('published', 'archived') or activity.status == LearningActivity.Status.DRAFT:
        return
    recipients = student_recipients(activity.course)
    state = activity_state(activity, now=now)
    if state != activity.lifecycle_state:
        labels = {'created': _('Actividad creada'), 'open': _('Actividad abierta'), 'closed': _('Actividad cerrada')}
        notify_once(
            course=activity.course, key=f'activity:{activity.pk}:{state}:{now.isoformat()}',
            title=labels[state], message=activity.title, recipients=recipients, url=url,
            event_type=NotificationEvent.Type.DEADLINE if state == 'closed' else NotificationEvent.Type.NOTE,
        )
        LearningActivity.objects.filter(pk=activity.pk).update(lifecycle_state=state)
    if activity.solution and activity.can_show_solution(now=now):
        notify_once(
            course=activity.course, key=f'activity:{activity.pk}:solution:{activity.solution_release_at.isoformat()}',
            title=_('Solución de actividad disponible'), message=activity.title,
            recipients=recipients, url=url, event_type=NotificationEvent.Type.FEEDBACK,
        )
    for submission in Submission.objects.filter(
        assignment__learning_activity=activity, feedback_published=True,
    ).select_related('user').order_by('user_id', '-version'):
        if feedback_visible(activity, submission, now=now) and submission.user in recipients:
            # A replaced delivery must not publish the grade of an older version.
            if submission.assignment.submissions.filter(user_id=submission.user_id, version__gt=submission.version).exists():
                continue
            notify_once(
                course=activity.course,
                key=f'activity:{activity.pk}:evaluated:{submission.pk}:{submission.graded_at or submission.updated_at}',
                title=_('Actividad evaluada'),
                message=_('Tu evaluación de %(activity)s ya está disponible.') % {'activity': activity.title},
                recipients=[submission.user], url=url, event_type=NotificationEvent.Type.GRADE,
            )


@transaction.atomic
def complete_student(course_id, user_id, *, now):
    course = Curso.objects.select_for_update().get(pk=course_id)
    membership = CourseMembership.objects.select_for_update().filter(course=course, user_id=user_id).first()
    if membership and (membership.role != CourseMembership.Role.STUDENT or membership.status != CourseMembership.Status.ACTIVE):
        return False
    enrollment = InscripcionCurso.objects.select_for_update().filter(curso=course, usuario_id=user_id).first()
    if not membership and (not enrollment or enrollment.completado):
        return False
    modules = course.lms_modules.filter(status=CourseModule.Status.PUBLISHED)
    module_ids = set(modules.values_list('pk', flat=True))
    completed = set(ModuleProgress.objects.filter(
        course=course, user_id=user_id, status=ModuleProgress.Status.COMPLETED,
        module_id__in=module_ids,
    ).values_list('module_id', flat=True))
    topics = set(course.temas.values_list('pk', flat=True))
    completed_topics = set(Progreso.objects.filter(
        usuario_id=user_id, tema_id__in=topics, completado=True,
    ).values_list('tema_id', flat=True))
    quizzes = course.lms_quizzes.filter(status__in=(Quiz.Status.PUBLISHED, Quiz.Status.CLOSED)).filter(
        Q(section__isnull=True) | Q(section_id=membership.section_id if membership else None),
    )
    quiz_ids = set(quizzes.values_list('pk', flat=True))
    passed = set(QuizAttempt.objects.filter(
        quiz_id__in=quiz_ids, user_id=user_id, status=QuizAttempt.Status.GRADED, is_passed=True,
    ).values_list('quiz_id', flat=True))
    if not (module_ids or topics or quiz_ids) or module_ids != completed or topics != completed_topics or quiz_ids != passed:
        return False
    if membership:
        membership.status = CourseMembership.Status.COMPLETED
        membership.completed_at = now
        membership.save(update_fields=['status', 'completed_at', 'updated_at'])
    InscripcionCurso.objects.update_or_create(
        curso=course, usuario_id=user_id, defaults={'completado': True, 'fecha_completado': now},
    )
    user = get_user_model().objects.get(pk=user_id)
    notify_once(course=course, key=f'course:{course.pk}:student:{user_id}:completed',
                title=_('¡Curso completado!'), message=_('Has completado los módulos y aprobado las pruebas de %(course)s.') % {'course': course.nombre},
                recipients=[user], url=reverse('lms:course_detail', kwargs={'course_id': course.pk}))
    AuditEvent.objects.create(course=course, target_user=user, action=AuditEvent.Action.COURSE_UPDATED,
                              metadata={'automatic': True, 'course_action': 'student_completed'})
    return True


def process_lms_lifecycle(*, now=None):
    timestamp = now or timezone.now()
    course_ids = list(Curso.objects.filter(status__in=('scheduled', 'published', 'archived')).values_list('pk', flat=True))
    for course_id in course_ids:
        process_course(course_id, now=timestamp)
    for quiz_id in Quiz.objects.filter(status__in=(Quiz.Status.SCHEDULED, Quiz.Status.PUBLISHED, Quiz.Status.CLOSED)).values_list('pk', flat=True):
        process_quiz(quiz_id, now=timestamp)
    for activity_id in LearningActivity.objects.values_list('pk', flat=True):
        process_activity(activity_id, now=timestamp)
    completed = 0
    for course in Curso.objects.filter(pk__in=course_ids, status__in=('published', 'archived')):
        for user in student_recipients(course):
            completed += int(complete_student(course.pk, user.pk, now=timestamp))
    return {'courses_checked': len(course_ids), 'students_completed': completed}
