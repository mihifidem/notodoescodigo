from datetime import timedelta
import csv
from io import StringIO
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .knowledge import parse_question_csv, save_bank_question
from .lifecycle import process_quiz
from .models import AuditEvent, CourseMembership, GradeEntry, Notification, NotificationEvent, Quiz, QuizAnswer, QuizAttempt, Section
from .quiz_status import teacher_quiz_status
from . import test_quiz_management


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class StudentQuizTests(TestCase):
    setUp = test_quiz_management.QuizManagementTests.setUp
    url = test_quiz_management.QuizManagementTests.url
    create_quiz = test_quiz_management.QuizManagementTests.create_quiz

    def published_quiz(self, **changes):
        quiz = self.create_quiz(**changes)
        self.course.status = 'published'
        self.course.activo = True
        self.course.save(update_fields=['status', 'activo'])
        quiz.status = 'published'
        quiz.save(update_fields=['status'])
        self.client.force_login(self.student)
        return quiz

    def start(self, quiz):
        response = self.client.post(self.url('quiz_start', quiz))
        attempt = quiz.attempts.get(user=self.student)
        self.assertRedirects(response, self.attempt_url(quiz, attempt))
        return attempt

    def attempt_url(self, quiz, attempt):
        return reverse('lms:quiz_attempt', kwargs={
            'course_id': self.course.pk, 'quiz_id': quiz.pk, 'attempt_id': attempt.pk,
        })

    def test_submit_records_dates_counts_and_score_and_blocks_rewrite(self):
        questions = [self.question]
        for i in range(3):
            questions.append(save_bank_question(bank=self.bank, actor=self.teacher,
                                                data={**self.question_data, 'prompt': f'Pregunta {i}'}))
        now = timezone.now()
        quiz = self.published_quiz(selected_questions=[q.pk for q in questions], passing_score='5',
                                   penalize_errors='on', error_deduction='0.25')
        self.assertContains(self.client.get(self.url('quiz_detail', quiz)), 'Comenzar prueba')
        with patch('apps.lms.quiz_attempts.timezone.now', return_value=now):
            attempt = self.start(quiz)
        self.assertEqual(attempt.started_at, now)
        items = list(quiz.questions.all())
        response = self.client.get(self.attempt_url(quiz, attempt))
        self.assertContains(response, 'OPCION SECRETA A')
        self.assertNotContains(response, 'SOLUCION SECRETA')
        submitted = now + timedelta(minutes=3)
        with patch('apps.lms.quiz_attempts.timezone.now', return_value=submitted):
            response = self.client.post(self.attempt_url(quiz, attempt), {
                'action': 'submit', f'question_{items[0].pk}': '0', f'question_{items[1].pk}': '0',
                f'question_{items[2].pk}': '1', f'question_{items[3].pk}': '',
            })
        self.assertRedirects(response, self.url('quiz_detail', quiz))
        attempt.refresh_from_db()
        self.assertEqual(attempt.submitted_at, submitted)
        self.assertEqual((attempt.correct_count, attempt.wrong_count, attempt.blank_count), (2, 1, 1))
        self.assertEqual(attempt.score, Decimal('4.75'))
        self.assertEqual(attempt.grade_entry.max_grade, Decimal('10'))
        self.assertFalse(attempt.is_passed)
        self.client.post(self.attempt_url(quiz, attempt), {'action': 'submit', f'question_{items[2].pk}': '0'})
        attempt.refresh_from_db()
        self.assertEqual(attempt.score, Decimal('4.75'))
        self.assertEqual(attempt.submitted_at, submitted)
        self.assertEqual(GradeEntry.objects.filter(attempt=attempt).count(), 1)
        self.client.force_login(self.teacher)
        response = self.client.get(self.url('quiz_detail', quiz))
        self.assertContains(response, 'Registro de intentos')
        self.assertContains(response, 'Acertadas')
        self.assertContains(response, 'Falladas')
        self.assertContains(response, 'quiz_student')

    def test_autosave_resume_preserves_order_and_blank_answers(self):
        quiz = self.published_quiz(shuffle_questions='on')
        attempt = self.start(quiz)
        item = quiz.questions.get()
        target = self.attempt_url(quiz, attempt)
        result = self.client.post(target, {'action': 'save', f'question_{item.pk}': '0'},
                                  HTTP_ACCEPT='application/json').json()
        self.assertTrue(result['saved'])
        self.assertFalse(result['finished'])
        self.assertIsNotNone(result['saved_at'])
        self.client.post(self.url('quiz_start', quiz))
        self.assertEqual(quiz.attempts.count(), 1)
        self.assertContains(self.client.get(target), 'value="0" checked')
        self.client.post(target, {'action': 'save', f'question_{item.pk}': ''})
        self.client.post(target, {'action': 'submit'})
        attempt.refresh_from_db()
        self.assertEqual((attempt.correct_count, attempt.wrong_count, attempt.blank_count), (0, 0, 1))
        self.assertEqual(attempt.score, Decimal('0'))

    def test_late_delivery_ignores_new_answers_and_grades_saved_work(self):
        quiz = self.published_quiz()
        attempt = self.start(quiz)
        item = quiz.questions.get()
        self.client.post(self.attempt_url(quiz, attempt), {'action': 'save', f'question_{item.pk}': '1'})
        with patch('apps.lms.quiz_attempts.timezone.now', return_value=attempt.deadline_at):
            self.client.post(self.attempt_url(quiz, attempt), {'action': 'submit', f'question_{item.pk}': '0'})
        attempt.refresh_from_db()
        self.assertEqual(attempt.wrong_count, 1)
        self.assertEqual(attempt.score, 0)
        self.assertEqual(attempt.submitted_at, attempt.deadline_at)
        self.assertEqual(attempt.answers.get().response, 'B')

    def test_get_after_deadline_and_scheduler_record_delivery_and_blank_count(self):
        quiz = self.published_quiz()
        attempt = self.start(quiz)
        with patch('apps.lms.quiz_views.timezone.now', return_value=attempt.deadline_at):
            response = self.client.get(self.attempt_url(quiz, attempt))
        self.assertRedirects(response, self.url('quiz_detail', quiz))
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'graded')
        self.assertEqual(attempt.blank_count, 1)
        self.assertEqual(attempt.submitted_at, attempt.deadline_at)
        second = QuizAttempt.objects.create(quiz=quiz, user=self.student, status='in_progress',
                                           started_at=timezone.now(), deadline_at=timezone.now() - timedelta(seconds=1))
        now = timezone.now()
        process_quiz(quiz.pk, now=now)
        second.refresh_from_db()
        self.assertEqual(second.blank_count, 1)
        self.assertEqual(second.submitted_at, now)

    def test_question_selection_tampering_is_rejected_without_partial_save(self):
        quiz = self.published_quiz()
        attempt = self.start(quiz)
        item = quiz.questions.get()
        for values in (
            {f'question_{item.pk}': '4'}, {f'question_{item.pk}': '-1'},
            {f'question_{item.pk}': ['0', '1']}, {'question_99999': '0'}, {'question_bad': '0'},
        ):
            response = self.client.post(self.attempt_url(quiz, attempt),
                                        {'action': 'submit', **values}, HTTP_ACCEPT='application/json')
            self.assertEqual(response.status_code, 400)
            self.assertIn('error', response.json())
            self.assertFalse(QuizAnswer.objects.filter(attempt=attempt).exists())
            attempt.refresh_from_db()
            self.assertEqual(attempt.status, 'in_progress')

    def test_permissions_and_csrf_protect_start_and_attempt(self):
        quiz = self.published_quiz()
        attempt = self.start(quiz)
        target = self.attempt_url(quiz, attempt)
        for user in (self.teacher, self.outsider):
            self.client.force_login(user)
            self.assertEqual(self.client.post(self.url('quiz_start', quiz)).status_code, 403)
            self.assertEqual(self.client.get(target).status_code, 403)
            self.assertEqual(self.client.post(target, {'action': 'submit'}).status_code, 403)
        CourseMembership.objects.create(course=self.course, user=self.outsider, role='student')
        self.assertEqual(self.client.get(target).status_code, 404)
        self.client.force_login(self.student)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.student)
        self.assertEqual(csrf.post(self.url('quiz_start', quiz)).status_code, 403)
        self.assertEqual(csrf.post(target, {'action': 'submit'}).status_code, 403)
        self.membership.status = 'suspended'
        self.membership.save()
        self.assertEqual(self.client.post(target, {'action': 'submit'}).status_code, 403)

    def test_opening_and_group_and_maximum_attempts(self):
        now = timezone.now()
        quiz = self.published_quiz(available_from=(now + timedelta(hours=1)).isoformat(), max_attempts=1)
        self.client.post(self.url('quiz_start', quiz))
        self.assertFalse(quiz.attempts.exists())
        quiz.available_from = now - timedelta(minutes=1)
        section = Section.objects.create(course=self.course, title='Clase privada')
        quiz.section = section
        quiz.save(update_fields=['available_from', 'section'])
        self.assertEqual(self.client.post(self.url('quiz_start', quiz)).status_code, 403)
        self.membership.section = section
        self.membership.save()
        attempt = self.start(quiz)
        self.client.post(self.attempt_url(quiz, attempt), {'action': 'submit'})
        self.client.post(self.url('quiz_start', quiz))
        self.assertEqual(quiz.attempts.count(), 1)

    def test_results_hidden_until_release_and_teacher_sees_phases(self):
        quiz = self.published_quiz(publish_grade_at=(timezone.now() + timedelta(days=1)).isoformat())
        attempt = self.start(quiz)
        self.client.post(self.attempt_url(quiz, attempt), {'action': 'submit'})
        self.assertContains(self.client.get(self.url('quiz_detail', quiz)), 'Resultados pendientes de publicación')
        self.assertNotContains(self.client.get(self.url('quiz_detail', quiz)), '(No aprobado)')
        self.client.force_login(self.teacher)
        area = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'quizzes'})
        self.assertContains(self.client.get(area), 'Realizándose (abierta)')
        quiz.status = 'closed'
        quiz.publish_grade_at = quiz.publish_answers_at = timezone.now() - timedelta(seconds=1)
        quiz.save(update_fields=['status', 'publish_grade_at', 'publish_answers_at'])
        process_quiz(quiz.pk, now=timezone.now())
        response = self.client.get(area)
        for phase in ('Cerrada', 'Solución publicada', 'Notas publicadas'):
            self.assertContains(response, phase)
        self.client.force_login(self.student)
        self.assertContains(self.client.get(self.url('quiz_detail', quiz)), '(No aprobado)')

    def test_phase_boundaries_and_release_dates_do_not_claim_unpublished_results(self):
        quiz = self.published_quiz()
        now = timezone.now()
        quiz.available_from = now + timedelta(minutes=1)
        self.assertEqual(teacher_quiz_status(quiz, now=now), ['Pendiente de apertura'])
        self.assertEqual(teacher_quiz_status(quiz, now=quiz.available_from), ['Realizándose (abierta)'])
        quiz.due_at = now + timedelta(minutes=2)
        self.assertEqual(teacher_quiz_status(quiz, now=quiz.due_at), ['Cerrada'])
        quiz.publish_grade_at = quiz.publish_answers_at = now
        self.assertNotIn('Solución publicada', teacher_quiz_status(quiz, now=now))
        self.assertNotIn('Notas publicadas', teacher_quiz_status(quiz, now=now))

    def test_student_phase_labels_on_listing_and_detail_are_private(self):
        quiz = self.published_quiz()
        area = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'quizzes'})
        for target in (area, self.url('quiz_detail', quiz)):
            self.assertContains(self.client.get(target), 'Realizándose (abierta)')
        quiz.available_from = timezone.now() + timedelta(minutes=5)
        quiz.save(update_fields=['available_from'])
        self.assertContains(self.client.get(area), 'Pendiente de apertura')
        quiz.available_from = timezone.now() - timedelta(minutes=1)
        quiz.save(update_fields=['available_from'])
        attempt = self.start(quiz)
        self.client.post(self.attempt_url(quiz, attempt), {'action': 'submit'})
        quiz.status = 'closed'
        quiz.publish_answers_at = quiz.publish_grade_at = timezone.now() - timedelta(seconds=1)
        quiz.save(update_fields=['status', 'publish_answers_at', 'publish_grade_at'])
        process_quiz(quiz.pk, now=timezone.now())
        for target in (area, self.url('quiz_detail', quiz)):
            response = self.client.get(target)
            for label in ('Cerrada', 'Solución publicada', 'Notas publicadas'):
                self.assertContains(response, label)
        CourseMembership.objects.create(course=self.course, user=self.outsider, role='student')
        self.client.force_login(self.outsider)
        for target in (area, self.url('quiz_detail', quiz)):
            response = self.client.get(target)
            self.assertContains(response, 'Cerrada')
            self.assertNotContains(response, 'Notas publicadas')
        attempt.grade_entry.is_hidden = True
        attempt.grade_entry.save(update_fields=['is_hidden'])
        self.client.force_login(self.student)
        self.assertNotContains(self.client.get(area), 'Notas publicadas')

    def test_all_five_dates_visible_to_teacher_and_student(self):
        now = timezone.now().replace(microsecond=0)
        dates = {
            'available_from': now - timedelta(hours=1),
            'available_until': now + timedelta(hours=1),
            'due_at': now + timedelta(minutes=30),
            'publish_answers_at': now + timedelta(hours=2),
            'publish_grade_at': now + timedelta(hours=3),
        }
        quiz = self.published_quiz(**{key: value.isoformat() for key, value in dates.items()})
        area = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'quizzes'})
        for user in (self.teacher, self.student):
            self.client.force_login(user)
            for target in (area, self.url('quiz_detail', quiz)):
                response = self.client.get(target)
                for label in ('Inicio / apertura', 'Cierre', 'Entrega', 'Publicación de soluciones', 'Publicación de notas'):
                    self.assertContains(response, label)
                for date in dates.values():
                    self.assertContains(response, timezone.localtime(date).strftime('%d/%m/%Y %H:%M:%S'))
                self.assertContains(response, 'data-quiz-live-region')
                self.assertContains(response, 'lms/quiz-live.js')

    def test_grades_group_students_by_quiz_and_keep_results_private(self):
        from bs4 import BeautifulSoup
        quiz = self.published_quiz()
        attempt = self.start(quiz)
        self.client.post(self.attempt_url(quiz, attempt), {'action': 'submit'})
        CourseMembership.objects.create(course=self.course, user=self.outsider, role='student')
        quiz.status = Quiz.Status.CLOSED
        quiz.save(update_fields=['status'])
        Quiz.objects.create(course=self.course, title='Segunda prueba', reference_code='TEST-SECOND', status=Quiz.Status.CLOSED)
        url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'grades'})
        self.client.force_login(self.teacher)
        response = self.client.get(url)
        self.assertNotContains(response, 'Ver páginas')
        self.assertContains(response, 'quiz_student')
        self.assertContains(response, 'quiz_outsider')
        self.assertContains(response, 'No realizada')
        self.assertEqual(len(BeautifulSoup(response.content, 'html.parser').select('[data-quiz-info^="grades-"]')), 2)
        self.client.force_login(self.student)
        response = self.client.get(url)
        self.assertNotContains(response, 'quiz_outsider')
        self.assertNotContains(response, 'Acertadas')
        self.assertContains(response, 'Resultados pendientes de publicación')
        quiz.publish_grade_at = timezone.now() - timedelta(seconds=1)
        quiz.save(update_fields=['publish_grade_at'])
        process_quiz(quiz.pk, now=timezone.now())
        self.assertContains(self.client.get(url), 'Acertadas')
        attempt.grade_entry.is_hidden = True
        attempt.grade_entry.save(update_fields=['is_hidden'])
        self.assertNotContains(self.client.get(url), 'Acertadas')
        self.client.force_login(self.outsider)
        response = self.client.get(url)
        self.assertContains(response, 'No realizada')
        self.assertNotContains(response, 'Acertadas')

    def test_gradebook_matrix_filters_exports_and_keeps_student_results_private(self):
        from bs4 import BeautifulSoup
        from .models import GradeItem

        quiz = self.published_quiz()
        attempt = self.start(quiz)
        self.client.post(self.attempt_url(quiz, attempt), {'action': 'submit'})
        CourseMembership.objects.create(course=self.course, user=self.outsider, role='student')
        manual = GradeItem.objects.create(course=self.course, title='Proyecto final', max_grade=10)
        GradeEntry.objects.create(
            course=self.course, item=manual, user=self.student, grade=8, max_grade=10,
        )
        url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'grades'})
        self.client.force_login(self.teacher)
        response = self.client.get(url)
        self.assertContains(response, 'gradebook-table')
        self.assertContains(response, 'Proyecto final')
        self.assertContains(response, 'Exportar CSV')
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertIsNotNone(soup.select_one('[data-quiz-live-region] .gradebook-table'))
        self.assertIsNone(soup.select_one('[data-quiz-live-region] .gradebook-filters'))
        book = response.context['gradebook']
        self.assertEqual(len(book['columns']), 2)
        self.assertEqual(len(book['rows']), 2)
        filtered = self.client.get(url, {'student': self.student.username, 'activity': 'Proyecto'})
        self.assertEqual(len(filtered.context['gradebook']['rows']), 1)
        self.assertEqual(filtered.context['gradebook']['columns'][0]['title'], 'Proyecto final')
        export = self.client.get(url, {
            'export': 'csv', 'student': self.student.username, 'activity': 'Proyecto',
        })
        self.assertEqual(export.status_code, 200)
        records = list(csv.reader(StringIO(export.content.decode('utf-8-sig'))))
        self.assertEqual(records[0][1], 'Proyecto final')
        self.assertEqual(records[1][1], '8.00')
        self.assertEqual(len(records), 2)
        self.client.force_login(self.student)
        response = self.client.get(url)
        self.assertNotContains(response, 'Proyecto final')
        self.assertNotContains(response, self.outsider.username)
        self.assertIsNone(response.context['gradebook']['rows'][0]['cells'][0]['grade'])
        export = self.client.get(url, {'export': 'csv'})
        self.assertNotIn(self.outsider.username, export.content.decode('utf-8-sig'))
        self.assertNotIn('Proyecto final', export.content.decode('utf-8-sig'))
        quiz.publish_grade_at = timezone.now() - timedelta(seconds=1)
        quiz.save(update_fields=['publish_grade_at'])
        process_quiz(quiz.pk, now=timezone.now())
        self.assertEqual(self.client.get(url).context['gradebook']['rows'][0]['cells'][0]['grade'], 0)
        self.assertEqual(list(csv.reader(StringIO(
            self.client.get(url, {'export': 'csv'}).content.decode('utf-8-sig'),
        )))[1][1], '0.00')
        grade = attempt.grade_entry
        grade.is_hidden = True
        grade.save(update_fields=['is_hidden'])
        self.assertIsNone(self.client.get(url).context['gradebook']['rows'][0]['cells'][0]['grade'])
        self.assertNotIn('0.00', self.client.get(url, {'export': 'csv'}).content.decode('utf-8-sig'))
        self.client.force_login(self.outsider)
        CourseMembership.objects.filter(course=self.course, user=self.outsider).delete()
        self.assertEqual(self.client.get(url, {'export': 'csv'}).status_code, 403)

    def test_gradebook_export_escapes_spreadsheet_formulas(self):
        quiz = self.published_quiz(title='=HYPERLINK("example")')
        self.client.force_login(self.teacher)
        url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'grades'})
        response = self.client.get(url, {'export': 'csv'})
        records = list(csv.reader(StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(records[0][1], "'" + quiz.title)

    def test_no_show_notifications_wait_for_closure_and_grade_date_and_deduplicate(self):
        quiz = self.published_quiz()
        CourseMembership.objects.create(course=self.course, user=self.outsider, role='student')
        QuizAttempt.objects.create(quiz=quiz, user=self.outsider, status=QuizAttempt.Status.DRAFT)
        attempt = self.start(quiz)
        self.client.post(self.attempt_url(quiz, attempt), {'action': 'submit'})
        now = timezone.now()
        quiz.publish_grade_at = now
        quiz.due_at = now + timedelta(minutes=2)
        quiz.save(update_fields=['publish_grade_at', 'due_at'])
        process_quiz(quiz.pk, now=now)
        self.assertFalse(NotificationEvent.objects.filter(dedupe_key__contains='no-show').exists())
        self.assertEqual(Notification.objects.filter(event__dedupe_key=f'grade:{attempt.grade_entry.pk}:published', recipient=self.student).count(), 1)
        process_quiz(quiz.pk, now=now + timedelta(minutes=2))
        process_quiz(quiz.pk, now=now + timedelta(minutes=3))
        notification = Notification.objects.get(event__dedupe_key=f'quiz:{quiz.pk}:no-show:{self.outsider.pk}')
        self.assertEqual(notification.recipient, self.outsider)
        self.assertIn('no hay nota registrada', notification.message)
        self.assertFalse(NotificationEvent.objects.filter(dedupe_key=f'quiz:{quiz.pk}:no-show:{self.student.pk}').exists())
        self.assertFalse(GradeEntry.objects.filter(user=self.outsider).exists())

    def test_no_show_excludes_wrong_sections_inactive_students_and_waits_for_grade_date(self):
        quiz = self.published_quiz()
        section = Section.objects.create(course=self.course, title='Grupo A')
        quiz.section = section
        now = timezone.now()
        quiz.status = Quiz.Status.CLOSED
        quiz.publish_grade_at = now + timedelta(minutes=1)
        quiz.save(update_fields=['section', 'status', 'publish_grade_at'])
        CourseMembership.objects.create(course=self.course, user=self.outsider, role='student', section=section)
        inactive = get_user_model().objects.create_user('inactive_quiz_student', is_active=False)
        CourseMembership.objects.create(course=self.course, user=inactive, role='student', section=section)
        process_quiz(quiz.pk, now=now)
        self.assertFalse(NotificationEvent.objects.filter(dedupe_key__contains='no-show').exists())
        process_quiz(quiz.pk, now=quiz.publish_grade_at)
        self.assertEqual(Notification.objects.filter(event__dedupe_key__contains='no-show').count(), 1)
        self.assertTrue(Notification.objects.filter(recipient=self.outsider, event__dedupe_key__contains='no-show').exists())
        self.assertFalse(Notification.objects.filter(recipient=self.student, event__dedupe_key__contains='no-show').exists())

    def test_feedback_saved_notified_and_visible_only_to_recipient_and_teacher(self):
        quiz = self.published_quiz()
        CourseMembership.objects.create(course=self.course, user=self.outsider, role='student')
        url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'grades'})
        self.client.force_login(self.teacher)
        data = {'quiz_id': quiz.pk, 'student_id': self.student.pk, 'message': 'FEEDBACK PRIVADO <script>alert(1)</script>'}
        self.assertRedirects(self.client.post(url, data), url)
        event = NotificationEvent.objects.get(metadata__kind='quiz_feedback')
        self.assertEqual(event.actor, self.teacher)
        self.assertEqual(event.metadata['quiz_id'], quiz.pk)
        self.assertEqual(list(event.notifications.values_list('recipient_id', flat=True)), [self.student.pk])
        self.assertContains(self.client.get(url), 'FEEDBACK PRIVADO')
        self.client.force_login(self.student)
        response = self.client.get(url)
        self.assertContains(response, 'FEEDBACK PRIVADO')
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertNotContains(response, 'Guardar y enviar feedback')
        self.client.force_login(self.outsider)
        self.assertNotContains(self.client.get(url), 'FEEDBACK PRIVADO')
        feed = reverse('lms:lifecycle_notifications')
        self.assertFalse(self.client.get(feed, {'after': 0}).json()['notifications'])
        self.client.force_login(self.student)
        self.assertTrue(any('FEEDBACK PRIVADO' in row['message'] for row in self.client.get(feed, {'after': 0}).json()['notifications']))

    def test_feedback_validates_permissions_scope_csrf_and_nonempty_message(self):
        quiz = self.published_quiz()
        url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'grades'})
        data = {'quiz_id': quiz.pk, 'student_id': self.student.pk, 'message': 'Comentario'}
        self.assertEqual(self.client.post(url, data).status_code, 403)
        self.client.force_login(self.teacher)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.teacher)
        self.assertEqual(csrf.post(url, data).status_code, 403)
        foreign = Quiz.objects.create(course=self.other, title='Ajena')
        for changes in ({'message': '  '}, {'message': 'x' * 5001}, {'student_id': self.outsider.pk}, {'quiz_id': foreign.pk}):
            response = self.client.post(url, {**data, **changes})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['quiz_feedback_form'].errors)
        section = Section.objects.create(course=self.course, title='Grupo B')
        quiz.section = section
        quiz.save(update_fields=['section'])
        response = self.client.post(url, data)
        self.assertTrue(response.context['quiz_feedback_form'].errors)
        self.assertFalse(NotificationEvent.objects.filter(metadata__kind='quiz_feedback').exists())

    def test_grades_hide_other_sections_and_keep_multiple_attempts(self):
        quiz = self.published_quiz()
        first = self.start(quiz)
        self.client.post(self.attempt_url(quiz, first), {'action': 'submit'})
        self.client.post(self.url('quiz_start', quiz))
        second = quiz.attempts.latest('pk')
        self.client.post(self.attempt_url(quiz, second), {'action': 'submit'})
        url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'grades'})
        groups = self.client.get(url).context['course_quiz_result_groups']
        self.assertEqual(len(groups[0]['rows'][0]['results']), 2)
        first.grade_entry.grade = 7
        first.grade_entry.save(update_fields=['grade'])
        second.grade_entry.grade = 3
        second.grade_entry.save(update_fields=['grade'])
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(url).context['gradebook']['rows'][0]['cells'][0]['grade'], 3)
        self.assertEqual(len(self.client.get(url, {'student': 'no-coincide'}).context['gradebook']['rows']), 0)
        self.assertEqual(len(self.client.get(url, {'activity': 'no-coincide'}).context['gradebook']['columns']), 0)
        self.client.force_login(self.student)
        quiz.section = Section.objects.create(course=self.course, title='Otro grupo')
        quiz.save(update_fields=['section'])
        self.assertNotContains(self.client.get(url), quiz.title)

    def test_compact_summary_and_collapsed_information_for_both_roles(self):
        from bs4 import BeautifulSoup
        now = timezone.now().replace(microsecond=0)
        quiz = self.published_quiz(
            reference_code='REFERENCE-2026',
            available_from=(now - timedelta(minutes=1)).isoformat(),
            available_until=(now + timedelta(hours=1)).isoformat(),
            due_at=(now + timedelta(minutes=30)).isoformat(),
        )
        area = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'quizzes'})
        for user in (self.teacher, self.student):
            self.client.force_login(user)
            for target in (area, self.url('quiz_detail', quiz)):
                response = self.client.get(target)
                soup = BeautifulSoup(response.content, 'html.parser')
                region = soup.select_one('[data-quiz-live-region]')
                info = region.find('summary', string='Información de la prueba').parent
                self.assertFalse(info.has_attr('open'))
                self.assertIsNotNone(info.select_one('.quiz-dates'))
                summary = region.find('strong', string='REFERENCE-2026')
                self.assertIsNotNone(summary)
                self.assertIsNone(summary.find_parent('details'))
                self.assertContains(response, timezone.localtime(quiz.due_at).strftime('%d/%m/%Y %H:%M:%S'))

    def test_solutions_icon_requires_release_and_student_attempt(self):
        quiz = self.published_quiz()
        area = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'quizzes'})
        icon = 'aria-label="Ver preguntas y soluciones"'
        for user in (self.teacher, self.student):
            self.client.force_login(user)
            for target in (area, self.url('quiz_detail', quiz)):
                self.assertNotContains(self.client.get(target), icon)
        self.client.force_login(self.student)
        attempt = self.start(quiz)
        self.client.post(self.attempt_url(quiz, attempt), {'action': 'submit'})
        quiz.publish_answers_at = timezone.now() - timedelta(seconds=1)
        quiz.save(update_fields=['publish_answers_at'])
        process_quiz(quiz.pk, now=timezone.now())
        for user in (self.teacher, self.student):
            self.client.force_login(user)
            for target in (area, self.url('quiz_detail', quiz)):
                self.assertContains(self.client.get(target), icon)
            response = self.client.get(self.url('quiz_detail', quiz))
            self.assertContains(response, 'Respuesta correcta')
            self.assertContains(response, 'OPCION SECRETA A')
            self.assertContains(response, 'SOLUCION SECRETA')
            self.assertContains(response, 'id="quiz-solutions"')
        CourseMembership.objects.create(course=self.course, user=self.outsider, role='student')
        self.client.force_login(self.outsider)
        for target in (area, self.url('quiz_detail', quiz)):
            response = self.client.get(target)
            self.assertNotContains(response, icon)
            self.assertNotContains(response, 'SOLUCION SECRETA')

    def test_statistics_icon_opens_private_existing_history(self):
        quiz = self.published_quiz()
        area = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'quizzes'})
        icon = 'aria-label="Estadísticas de la prueba"'
        self.assertNotContains(self.client.get(area), icon)
        self.start(quiz)
        for user in (self.teacher, self.student):
            self.client.force_login(user)
            self.assertContains(self.client.get(area), icon)
            response = self.client.get(self.url('quiz_detail', quiz))
            self.assertContains(response, icon)
            self.assertContains(response, 'id="quiz-attempt-history"')
            self.assertContains(response, 'Resultados pendientes de publicación')
        CourseMembership.objects.create(course=self.course, user=self.outsider, role='student')
        self.client.force_login(self.outsider)
        for target in (area, self.url('quiz_detail', quiz)):
            self.assertNotContains(self.client.get(target), icon)

    def test_web_lifecycle_opens_closes_and_releases_results(self):
        now = timezone.now()
        quiz = self.published_quiz()
        quiz.status = 'scheduled'
        quiz.available_from = now
        quiz.available_until = quiz.due_at = now + timedelta(minutes=10)
        quiz.publish_answers_at = quiz.publish_grade_at = now + timedelta(minutes=11)
        quiz.save()
        url = reverse('lms:course_lifecycle_update', kwargs={'course_id': self.course.pk})
        self.assertEqual(self.client.get(url).status_code, 405)
        with patch('apps.lms.lifecycle_views.timezone.now', return_value=now):
            response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {'updated_at'})
        quiz.refresh_from_db()
        self.assertEqual(quiz.status, 'published')
        attempt = self.start(quiz)
        with patch('apps.lms.lifecycle_views.timezone.now', return_value=quiz.publish_grade_at):
            self.client.post(url)
            self.client.post(url)
        quiz.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(quiz.status, 'closed')
        self.assertIsNotNone(quiz.answers_released_at)
        self.assertEqual(attempt.status, 'graded')
        self.assertEqual(attempt.grade_entry.status, 'published')
        self.assertEqual(NotificationEvent.objects.filter(dedupe_key=f'quiz:{quiz.pk}:closed').count(), 1)

    def test_web_lifecycle_requires_membership_and_csrf(self):
        quiz = self.published_quiz()
        url = reverse('lms:course_lifecycle_update', kwargs={'course_id': self.course.pk})
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.student)
        self.assertEqual(csrf.post(url).status_code, 403)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 302)

    def test_grades_use_full_width_without_upcoming_sidebar_for_both_roles(self):
        self.published_quiz()
        for user in (self.teacher, self.student):
            self.client.force_login(user)
            grades = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'grades'})
            response = self.client.get(grades)
            self.assertContains(response, 'course-context-grid-full')
            self.assertContains(response, 'Libro de notas')
            self.assertNotContains(response, 'class="upcoming-panel"')
            self.assertNotContains(response, 'Próximas entregas')
            quizzes = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'quizzes'})
            response = self.client.get(quizzes)
            self.assertContains(response, 'class="upcoming-panel"')
            self.assertNotContains(response, 'course-context-grid-full')

    def test_expired_start_finalizes_existing_attempt_before_creating_another(self):
        quiz = self.published_quiz()
        attempt = self.start(quiz)
        now = attempt.deadline_at
        with patch('apps.lms.quiz_attempts.timezone.now', return_value=now):
            self.client.post(self.url('quiz_start', quiz))
            attempt.refresh_from_db()
            self.assertEqual(attempt.status, 'graded')
            self.assertEqual(attempt.blank_count, 1)
            self.assertEqual(quiz.attempts.count(), 1)
            self.client.post(self.url('quiz_start', quiz))
        self.assertEqual(quiz.attempts.count(), 2)

    def test_snapshot_preserves_penalty_and_passing_threshold(self):
        second = save_bank_question(bank=self.bank, actor=self.teacher, data=self.question_data)
        quiz = self.published_quiz(selected_questions=[self.question.pk, second.pk],
                                   penalize_errors='on', error_deduction='0.25', passing_score='4')
        attempt = self.start(quiz)
        items = list(quiz.questions.all())
        items[1].metadata['error_deduction'] = '4'
        items[1].save(update_fields=['metadata'])
        quiz.passing_score = 9
        quiz.save(update_fields=['passing_score'])
        self.client.post(self.attempt_url(quiz, attempt), {
            'action': 'submit', f'question_{items[0].pk}': '0', f'question_{items[1].pk}': '1',
        })
        attempt.refresh_from_db()
        self.assertEqual(attempt.score, Decimal('4.75'))
        self.assertTrue(attempt.is_passed)

    def test_duration_is_limited_by_global_and_course_deadlines(self):
        now = timezone.now()
        quiz = self.published_quiz()
        quiz.due_at = now + timedelta(minutes=5)
        quiz.available_until = now + timedelta(minutes=10)
        quiz.save(update_fields=['due_at', 'available_until'])
        with patch('apps.lms.quiz_attempts.timezone.now', return_value=now):
            attempt = self.start(quiz)
        self.assertEqual(attempt.deadline_at, quiz.due_at)
        quiz.attempts.all().delete()
        earlier = now + timedelta(minutes=2)
        with patch('apps.lms.quiz_attempts.timezone.now', return_value=now), \
                patch('apps.lms.quiz_attempts.course_end', return_value=earlier):
            attempt = self.start(quiz)
        self.assertEqual(attempt.deadline_at, earlier)

    def test_legacy_enrollment_and_completed_membership_can_take_quiz(self):
        from apps.courses.models import InscripcionCurso
        quiz = self.published_quiz()
        self.membership.delete()
        InscripcionCurso.objects.create(curso=self.course, usuario=self.student)
        attempt = self.start(quiz)
        self.client.post(self.attempt_url(quiz, attempt), {'action': 'submit'})
        CourseMembership.objects.create(course=self.course, user=self.student, role='student', status='completed')
        self.client.post(self.url('quiz_start', quiz))
        self.assertEqual(quiz.attempts.count(), 2)

    def test_second_submission_and_late_autosave_cannot_modify_grade(self):
        quiz = self.published_quiz()
        attempt = self.start(quiz)
        item = quiz.questions.get()
        target = self.attempt_url(quiz, attempt)
        self.client.post(target, {'action': 'submit', f'question_{item.pk}': '0'})
        result = self.client.post(target, {'action': 'save', f'question_{item.pk}': '1'},
                                  HTTP_ACCEPT='application/json')
        self.assertEqual(result.status_code, 400)
        attempt.refresh_from_db()
        self.assertEqual(attempt.correct_count, 1)
        self.assertEqual(attempt.score, 10)

    def test_full_94_question_csv_can_be_taken_and_graded(self):
        upload = SimpleUploadedFile('0201QZ.csv', (Path(settings.BASE_DIR) / 'static' / 'quiz' / '0201QZ.csv').read_bytes())
        rows = parse_question_csv(upload)
        questions = [save_bank_question(bank=self.bank, actor=self.teacher,
                                       data={**row, 'difficulty': 1, 'points': 1, 'status': 'published'})
                     for row in rows]
        quiz = self.published_quiz(selected_questions=[q.pk for q in questions], passing_score=7)
        attempt = self.start(quiz)
        response = self.client.get(self.attempt_url(quiz, attempt))
        self.assertContains(response, '&lt;!DOCTYPE html&gt;')
        self.assertContains(response, '<fieldset', count=94)
        data = {'action': 'submit'}
        for item in quiz.questions.select_related('question_version')[:70]:
            version = item.question_version
            data[f'question_{item.pk}'] = str(version.options.index(version.correct_answers[0]))
        self.client.post(self.attempt_url(quiz, attempt), data)
        attempt.refresh_from_db()
        self.assertEqual((attempt.correct_count, attempt.wrong_count, attempt.blank_count), (70, 0, 24))
        self.assertEqual(attempt.score, Decimal('7.45'))
        self.assertTrue(attempt.is_passed)

    def test_teacher_reopens_with_grace_and_preserves_grades_and_attempt_limit(self):
        quiz = self.published_quiz(max_attempts=2)
        attempt = self.start(quiz)
        self.client.post(self.attempt_url(quiz, attempt), {
            'action': 'submit', f'question_{quiz.questions.get().pk}': '0',
        })
        attempt.refresh_from_db()
        previous = (attempt.score, attempt.submitted_at, attempt.correct_count, attempt.answers.get().pk)
        quiz.status = 'closed'
        quiz.save(update_fields=['status'])
        self.client.force_login(self.teacher)
        area = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'quizzes'})
        url = self.url('quiz_reopen', quiz)
        for target in (area, self.url('quiz_detail', quiz)):
            self.assertContains(self.client.get(target), url)
        end = timezone.now() + timedelta(minutes=10)
        self.assertEqual(self.client.get(url).status_code, 200)
        quiz.refresh_from_db()
        self.assertEqual(quiz.status, 'closed')
        self.assertRedirects(self.client.post(url, {'grace_until': end.isoformat()}),
                             self.url('quiz_detail', quiz))
        quiz.refresh_from_db()
        self.assertEqual(quiz.status, 'published')
        self.assertEqual(quiz.due_at, end)
        self.assertEqual(quiz.available_until, end)
        self.assertEqual(quiz.max_attempts, 2)
        attempt.refresh_from_db()
        self.assertEqual((attempt.score, attempt.submitted_at, attempt.correct_count, attempt.answers.get().pk), previous)
        self.assertTrue(AuditEvent.objects.filter(metadata__quiz_action='reopened', metadata__quiz_id=quiz.pk).exists())
        self.assertEqual(NotificationEvent.objects.filter(title='Prueba reabierta').count(), 1)
        self.client.force_login(self.student)
        self.client.post(self.url('quiz_start', quiz))
        second = quiz.attempts.exclude(pk=attempt.pk).get()
        self.assertEqual(second.deadline_at, end)
        self.client.post(self.attempt_url(quiz, second), {'action': 'submit'})
        self.client.post(self.url('quiz_start', quiz))
        self.assertEqual(quiz.attempts.count(), 2)
        process_quiz(quiz.pk, now=end)
        process_quiz(quiz.pk, now=end)
        quiz.refresh_from_db()
        self.assertEqual(quiz.status, 'closed')
        self.assertEqual(NotificationEvent.objects.filter(title='Prueba finalizada').count(), 1)
        self.assertContains(self.client.get(area), 'Cerrada')

    def test_reopening_finalizes_old_attempt_before_deadline_extension(self):
        quiz = self.published_quiz()
        attempt = self.start(quiz)
        quiz.due_at = attempt.deadline_at
        quiz.save(update_fields=['due_at'])
        item = quiz.questions.get()
        self.client.post(self.attempt_url(quiz, attempt), {'action': 'save', f'question_{item.pk}': '0'})
        now = attempt.deadline_at + timedelta(minutes=1)
        self.client.force_login(self.teacher)
        end = now + timedelta(minutes=20)
        with patch('apps.lms.quiz_management.timezone.now', return_value=now):
            self.client.post(self.url('quiz_reopen', quiz), {'grace_until': end.isoformat()})
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'graded')
        self.assertEqual(attempt.correct_count, 1)
        self.assertEqual(attempt.deadline_at + timedelta(minutes=1), now)
        quiz.refresh_from_db()
        self.assertEqual(quiz.status, 'published')
        self.assertEqual(quiz.due_at, end)

    def test_reopen_rejects_invalid_dates_permissions_and_course_end(self):
        quiz = self.published_quiz()
        quiz.status = 'closed'
        quiz.save(update_fields=['status'])
        url = self.url('quiz_reopen', quiz)
        end = timezone.now() + timedelta(minutes=30)
        for user in (self.student, self.outsider):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, {'grace_until': end.isoformat()}).status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.teacher)
        self.assertEqual(csrf.post(url, {'grace_until': end.isoformat()}).status_code, 403)
        self.client.force_login(self.teacher)
        for value in ('', 'invalid', (timezone.now() - timedelta(seconds=1)).isoformat()):
            response = self.client.post(url, {'grace_until': value})
            self.assertTrue(response.context['form'].errors)
        with patch('apps.lms.lifecycle.course_end', return_value=timezone.now() + timedelta(minutes=10)):
            response = self.client.post(url, {'grace_until': end.isoformat()})
        self.assertTrue(response.context['form'].errors)
        quiz.refresh_from_db()
        self.assertEqual(quiz.status, 'closed')
        self.assertFalse(AuditEvent.objects.filter(metadata__quiz_action='reopened').exists())
        self.course.status = 'archived'
        self.course.save(update_fields=['status'])
        self.assertRedirects(self.client.post(url, {'grace_until': end.isoformat()}),
                             self.url('quiz_detail', quiz))
        self.assertNotContains(self.client.get(self.url('quiz_detail', quiz)), url)
