from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import connection
from django.db.models.query import QuerySet
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.courses.models import Curso, InscripcionCurso, Progreso, Tema
from apps.users.models import Notificacion
from .lifecycle import complete_student, course_end, process_course, process_lms_lifecycle, process_quiz
from .models import CourseMembership, CourseModule, GradeEntry, ModuleProgress, Notification, Quiz, QuizAnswer, QuizAttempt, Section
from .quiz_management import change_quiz_status
from .services import update_course
from .test_quiz_management import QuizManagementTests


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class LifecycleTests(TestCase):
    setUp = QuizManagementTests.setUp
    url = QuizManagementTests.url
    create_quiz = QuizManagementTests.create_quiz

    def scheduled_quiz(self):
        self.start = timezone.now() + timedelta(hours=1)
        self.end = self.start + timedelta(hours=1)
        self.release = self.end + timedelta(hours=1)
        quiz = self.create_quiz(
            available_from=self.start.isoformat(), due_at=self.end.isoformat(),
            publish_grade_at=self.release.isoformat(), publish_answers_at=self.release.isoformat(),
        )
        self.course.status = 'published'
        self.course.save(update_fields=['status'])
        change_quiz_status(course=self.course, actor=self.teacher, quiz_id=quiz.pk, action='schedule')
        quiz.refresh_from_db()
        return quiz

    def test_status_post_locks_attempts_without_distinct(self):
        quiz = self.create_quiz()
        self.course.status = 'published'
        self.course.save(update_fields=['status'])
        select_for_update = QuerySet.select_for_update
        with patch(
            'django.db.models.query.QuerySet.select_for_update',
            autospec=True,
            side_effect=select_for_update,
        ) as lock, CaptureQueriesContext(connection) as queries:
            response = self.client.post(self.url('quiz_status', quiz), {'action': 'publish'})
        self.assertRedirects(response, self.url('quiz_detail', quiz), fetch_redirect_response=False)
        attempt_locks = [
            call.args[0] for call in lock.call_args_list
            if call.args[0].model == QuizAttempt
        ]
        self.assertTrue(attempt_locks)
        selects = [
            query['sql'] for query in queries.captured_queries
            if query['sql'].lstrip().upper().startswith('SELECT')
            and 'FROM "lms_quizattempt"' in query['sql']
            and 'manual_review' in query['sql']
        ]
        self.assertEqual(len(selects), 1)
        self.assertNotIn('SELECT DISTINCT', selects[0].upper())
        self.assertIn('EXISTS', selects[0].upper())
        quiz.refresh_from_db()
        self.assertEqual(quiz.status, Quiz.Status.PUBLISHED)

    def test_schedule_opens_and_closes_at_exact_dates_without_duplicates(self):
        quiz = self.scheduled_quiz()
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(self.url('quiz_detail', quiz)).status_code, 403)
        process_quiz(quiz.pk, now=self.start - timedelta(seconds=1))
        quiz.refresh_from_db()
        self.assertEqual(quiz.status, Quiz.Status.SCHEDULED)
        self.assertEqual(Notificacion.objects.filter(usuario=self.student).count(), 0)
        process_quiz(quiz.pk, now=self.start)
        quiz.refresh_from_db()
        self.assertEqual(quiz.status, Quiz.Status.PUBLISHED)
        self.assertContains(self.client.get(self.url('quiz_detail', quiz)), quiz.title)
        process_quiz(quiz.pk, now=self.start)
        self.assertEqual(Notificacion.objects.filter(usuario=self.student).count(), 1)
        process_quiz(quiz.pk, now=self.end)
        process_quiz(quiz.pk, now=self.end)
        quiz.refresh_from_db()
        self.assertEqual(quiz.status, Quiz.Status.CLOSED)
        self.assertEqual(Notificacion.objects.filter(usuario=self.student).count(), 2)
        self.assertEqual(Notificacion.objects.filter(usuario=self.teacher).count(), 1)

    def test_grade_and_answers_released_at_dates_only_to_correct_student(self):
        quiz = self.scheduled_quiz()
        item = quiz.questions.get()
        attempt = QuizAttempt.objects.create(quiz=quiz, user=self.student, status='in_progress')
        QuizAnswer.objects.create(attempt=attempt, question=item, question_version=item.question_version,
                                  response=self.question_data['option_a'])
        process_quiz(quiz.pk, now=self.end)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, QuizAttempt.Status.GRADED)
        self.assertEqual(attempt.score, Decimal('10'))
        self.assertEqual(attempt.grade_entry.status, GradeEntry.Status.DRAFT)
        self.client.force_login(self.student)
        self.assertNotContains(self.client.get(self.url('quiz_detail', quiz)), 'OPCION SECRETA')
        grades_url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'grades'})
        self.assertNotContains(self.client.get(grades_url), '10,00')
        # Use a past release date for HTTP views, whose clock is the real current time.
        quiz.publish_grade_at = quiz.publish_answers_at = timezone.now() - timedelta(seconds=1)
        quiz.save(update_fields=['publish_grade_at', 'publish_answers_at'])
        process_quiz(quiz.pk, now=timezone.now())
        process_quiz(quiz.pk, now=timezone.now())
        attempt.grade_entry.refresh_from_db()
        self.assertEqual(attempt.grade_entry.status, GradeEntry.Status.PUBLISHED)
        self.assertTrue(attempt.grade_entry.is_published)
        self.assertContains(self.client.get(self.url('quiz_detail', quiz)), 'OPCION SECRETA')
        self.assertContains(self.client.get(self.url('quiz_detail', quiz)), 'Tu nota')
        self.assertContains(self.client.get(grades_url), quiz.title)
        self.assertEqual(Notification.objects.filter(recipient=self.student, event__event_type='grade').count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.outsider).count(), 0)
        CourseMembership.objects.create(course=self.course, user=self.outsider, role='student')
        self.client.force_login(self.outsider)
        self.assertNotContains(self.client.get(self.url('quiz_detail', quiz)), 'OPCION SECRETA')
        self.assertNotContains(self.client.get(self.url('quiz_detail', quiz)), 'Tu nota')
        response = self.client.get(grades_url)
        self.assertContains(response, quiz.title)
        self.assertContains(response, 'No realizada')
        self.assertNotContains(response, '10,00')
        self.assertNotContains(response, 'Acertadas')

    def test_group_and_suspended_students_do_not_receive_open_notifications(self):
        quiz = self.scheduled_quiz()
        section = Section.objects.create(course=self.course, title='Grupo')
        quiz.section = section
        quiz.save(update_fields=['section'])
        process_quiz(quiz.pk, now=self.start)
        self.assertFalse(Notificacion.objects.filter(usuario=self.student).exists())
        self.membership.section = section
        self.membership.status = 'suspended'
        self.membership.save()
        process_quiz(quiz.pk, now=self.end)
        self.assertFalse(Notificacion.objects.filter(usuario=self.student).exists())

    def test_course_scheduled_publication_and_closing_preserve_enrollment(self):
        start = timezone.now() + timedelta(days=1)
        self.course.status = 'scheduled'
        self.course.activo = False
        self.course.fecha_publicacion = start
        self.course.fecha_fin = timezone.localtime(start).date()
        self.course.save()
        enrollment = InscripcionCurso.objects.create(curso=self.course, usuario=self.student)
        process_course(self.course.pk, now=start - timedelta(seconds=1))
        self.course.refresh_from_db()
        self.assertFalse(self.course.activo)
        process_course(self.course.pk, now=start)
        self.course.refresh_from_db()
        self.assertTrue(self.course.activo)
        self.assertEqual(self.course.status, 'published')
        end = course_end(self.course)
        process_course(self.course.pk, now=end - timedelta(seconds=1))
        self.course.refresh_from_db()
        self.assertEqual(self.course.status, 'published')
        process_course(self.course.pk, now=end)
        process_course(self.course.pk, now=end)
        self.course.refresh_from_db()
        enrollment.refresh_from_db()
        self.assertEqual(self.course.status, 'archived')
        self.assertFalse(self.course.activo)
        self.assertFalse(enrollment.completado)
        self.assertTrue(self.course.lms_settings.archived)
        self.assertFalse(self.course.lms_settings.enrollment_open)
        self.assertEqual(Notificacion.objects.filter(usuario=self.student).count(), 2)

    def test_completion_requires_all_published_modules_and_applicable_quizzes(self):
        quiz = self.create_quiz()
        quiz.status = 'published'
        quiz.save(update_fields=['status'])
        module = CourseModule.objects.create(course=self.course, title='Publicado', status='published')
        CourseModule.objects.create(course=self.course, title='Borrador', status='draft')
        topic = Tema.objects.create(curso=self.course, titulo='Tema anterior', contenido='Texto')
        now = timezone.now()
        self.assertFalse(complete_student(self.course.pk, self.student.pk, now=now))
        ModuleProgress.objects.create(course=self.course, user=self.student, module=module,
                                      completed_items=1, total_items=1)
        Progreso.objects.create(usuario=self.student, tema=topic, completado=True)
        self.assertFalse(complete_student(self.course.pk, self.student.pk, now=now))
        other_quiz = Quiz.objects.create(course=self.course, title='Otra prueba', status='published')
        self.assertFalse(complete_student(self.course.pk, self.student.pk, now=now))
        QuizAttempt.objects.create(quiz=quiz, user=self.student, status='graded', is_passed=True)
        self.assertFalse(complete_student(self.course.pk, self.student.pk, now=now))
        QuizAttempt.objects.create(quiz=other_quiz, user=self.student, status='graded', is_passed=True)
        self.assertTrue(complete_student(self.course.pk, self.student.pk, now=now))
        self.assertFalse(complete_student(self.course.pk, self.student.pk, now=now))
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, 'completed')
        self.assertTrue(InscripcionCurso.objects.get(curso=self.course, usuario=self.student).completado)
        self.assertEqual(Notificacion.objects.filter(usuario=self.student).count(), 1)

    def test_empty_course_and_suspended_memberships_never_auto_complete(self):
        self.assertFalse(complete_student(self.course.pk, self.student.pk, now=timezone.now()))
        module = CourseModule.objects.create(course=self.course, title='Módulo', status='published')
        ModuleProgress.objects.create(course=self.course, user=self.student, module=module, completed_items=1, total_items=1)
        self.membership.status = 'suspended'
        self.membership.save()
        self.assertFalse(complete_student(self.course.pk, self.student.pk, now=timezone.now()))

    def test_completion_signals_wait_for_quiz_and_preserve_group_history_access(self):
        self.course.status = 'published'
        self.course.save(update_fields=['status'])
        quiz = self.create_quiz()
        section = Section.objects.create(course=self.course, title='Clase del alumno')
        self.membership.section = section
        self.membership.save()
        quiz.status = 'published'
        quiz.section = section
        quiz.save(update_fields=['status', 'section'])
        topic = Tema.objects.create(curso=self.course, titulo='Tema', contenido='Contenido')
        Progreso.objects.create(usuario=self.student, tema=topic, completado=True)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, 'active')
        self.assertFalse(Notificacion.objects.filter(usuario=self.student, titulo='¡Curso completado!').exists())
        QuizAttempt.objects.create(quiz=quiz, user=self.student, status='graded', is_passed=True)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, 'completed')
        self.assertEqual(Notificacion.objects.filter(usuario=self.student, titulo='¡Curso completado!').count(), 1)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(self.url('quiz_detail', quiz)).status_code, 200)

    def test_quiz_waits_for_parent_course_and_overdue_events_never_claim_open(self):
        quiz = self.scheduled_quiz()
        self.course.status = 'scheduled'
        self.course.fecha_publicacion = self.start
        self.course.save()
        process_quiz(quiz.pk, now=self.start)
        quiz.refresh_from_db()
        self.assertEqual(quiz.status, 'scheduled')
        process_course(self.course.pk, now=self.start)
        process_quiz(quiz.pk, now=self.end)
        self.assertFalse(Notification.objects.filter(event__dedupe_key=f'quiz:{quiz.pk}:opened').exists())
        self.assertTrue(Notification.objects.filter(event__dedupe_key=f'quiz:{quiz.pk}:closed').exists())

    def test_grade_created_after_release_is_notified_and_manual_review_is_not_approved(self):
        quiz = self.scheduled_quiz()
        quiz.publish_grade_at = self.release
        attempt = QuizAttempt.objects.create(quiz=quiz, user=self.student, status='submitted')
        item = quiz.questions.get()
        answer = QuizAnswer.objects.create(attempt=attempt, question=item, question_version=item.question_version,
                                          response='B', manual_review=True)
        process_quiz(quiz.pk, now=self.release)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'submitted')
        self.assertFalse(GradeEntry.objects.filter(attempt=attempt).exists())
        answer.manual_review = False
        answer.save(update_fields=['manual_review'])
        process_quiz(quiz.pk, now=self.release + timedelta(minutes=1))
        process_quiz(quiz.pk, now=self.release + timedelta(minutes=2))
        self.assertEqual(GradeEntry.objects.get(attempt=attempt).status, 'published')
        self.assertEqual(Notification.objects.filter(event__event_type='grade', recipient=self.student).count(), 1)

    def test_unscheduled_drafts_remain_private_and_schedule_validation(self):
        quiz = self.create_quiz()
        with self.assertRaises(ValidationError):
            change_quiz_status(course=self.course, actor=self.teacher, quiz_id=quiz.pk, action='schedule')
        process_quiz(quiz.pk, now=timezone.now() + timedelta(days=10))
        quiz.refresh_from_db()
        self.assertEqual(quiz.status, 'draft')
        with self.assertRaises(PermissionDenied):
            update_course(actor=self.teacher, course=self.course, changes={'status': 'scheduled'})
        self.course.status = 'published'
        self.course.save(update_fields=['status'])
        result = process_lms_lifecycle()
        self.assertEqual(result['students_completed'], 0)

    def test_notification_feed_authenticated_private_and_non_mutating(self):
        quiz = self.scheduled_quiz()
        feed = reverse('lms:lifecycle_notifications')
        self.client.logout()
        self.assertEqual(self.client.get(feed).status_code, 302)
        self.client.force_login(self.student)
        initial = self.client.get(feed).json()
        process_quiz(quiz.pk, now=self.start)
        data = self.client.get(feed, {'after': initial['cursor']}).json()
        self.assertEqual(len(data['notifications']), 1)
        self.assertEqual(data['count'], 1)
        self.assertEqual(self.client.get(feed, {'after': data['cursor']}).json()['notifications'], [])
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(feed, {'after': 0}).json()['notifications'], [])
        self.assertEqual(self.client.get(feed, {'after': 'invalid'}).status_code, 400)
        self.assertEqual(self.client.post(feed).status_code, 405)

    def test_command_runs_without_broker_and_rejects_invalid_interval(self):
        call_command('process_lms_lifecycle', stdout=StringIO())
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command('process_lms_lifecycle', interval=0, stdout=StringIO())
