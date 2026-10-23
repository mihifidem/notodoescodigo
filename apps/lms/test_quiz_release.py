from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .campus import requirement_status
from .models import AuditEvent, CourseMembership, GradeEntry, Notification, Quiz, QuizAnswer, QuizAttempt, Section
from .quiz_results import quiz_result_groups
from .quiz_status import quiz_solutions_visible
from .test_quiz_attempts import StudentQuizTests


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class QuizResultReleaseTests(TestCase):
    setUp = StudentQuizTests.setUp
    url = StudentQuizTests.url
    create_quiz = StudentQuizTests.create_quiz
    published_quiz = StudentQuizTests.published_quiz

    def graded_attempt(self, quiz, user=None):
        attempt = QuizAttempt.objects.create(
            quiz=quiz, user=user or self.student, status='submitted',
            started_at=timezone.now(), submitted_at=timezone.now(),
        )
        item = quiz.questions.get()
        QuizAnswer.objects.create(
            attempt=attempt, question=item, question_version=item.question_version,
            response=self.question_data['option_a'],
        )
        quiz.evaluate_attempt(attempt)
        attempt.refresh_from_db()
        return attempt

    def release_url(self, quiz, attempt):
        return reverse('lms:quiz_release_results', kwargs={
            'course_id': quiz.course_id, 'quiz_id': quiz.pk, 'attempt_id': attempt.pk,
        })

    def test_teacher_releases_one_students_grade_solution_and_notification_once(self):
        quiz = self.published_quiz()
        attempt = self.graded_attempt(quiz)
        CourseMembership.objects.create(course=self.course, user=self.outsider, role='student')
        other = self.graded_attempt(quiz, self.outsider)
        detail = self.url('quiz_detail', quiz)
        self.assertNotContains(self.client.get(detail), 'SOLUCION SECRETA')
        self.assertIsNone(quiz_result_groups(self.course, self.student, teacher_view=False)[0]['rows'][0]['results'][0]['grade'])
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(detail), self.release_url(quiz, attempt))
        for _ in range(2):
            self.assertRedirects(
                self.client.post(self.release_url(quiz, attempt)),
                detail + '#quiz-attempt-history', fetch_redirect_response=False,
            )
        attempt.refresh_from_db()
        attempt.grade_entry.refresh_from_db()
        self.assertIsNotNone(attempt.results_released_at)
        self.assertEqual(attempt.grade_entry.status, GradeEntry.Status.PUBLISHED)
        self.assertTrue(attempt.grade_entry.is_published)
        self.assertEqual(Notification.objects.filter(
            recipient=self.student, event__dedupe_key=f'grade:{attempt.grade_entry.pk}:published',
        ).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(
            metadata__quiz_action='results_released', metadata__attempt_id=attempt.pk,
        ).count(), 1)
        quiz.refresh_from_db()
        self.assertIsNone(quiz.publish_grade_at)
        self.assertIsNone(quiz.publish_answers_at)
        self.assertIsNone(quiz.answers_released_at)
        self.client.force_login(self.student)
        response = self.client.get(detail)
        self.assertContains(response, 'SOLUCION SECRETA')
        self.assertContains(response, 'Tu nota')
        self.assertContains(response, 'Notas publicadas')
        self.assertNotContains(response, self.release_url(quiz, attempt))
        groups = quiz_result_groups(self.course, self.student, teacher_view=False)
        self.assertEqual(groups[0]['rows'][0]['results'][0]['grade'].pk, attempt.grade_entry.pk)
        self.assertTrue(requirement_status({
            'criterion': 'passed', 'activity_id': None, 'quiz_id': quiz.pk,
        }, self.student))
        self.client.force_login(self.outsider)
        response = self.client.get(detail)
        self.assertNotContains(response, 'SOLUCION SECRETA')
        self.assertNotContains(response, 'Tu nota')
        other.refresh_from_db()
        self.assertIsNone(other.results_released_at)
        self.assertEqual(other.grade_entry.status, GradeEntry.Status.DRAFT)
        self.assertFalse(Notification.objects.filter(recipient=self.outsider, event__event_type='grade').exists())
        quiz.evaluate_attempt(attempt)
        attempt.grade_entry.refresh_from_db()
        self.assertEqual(attempt.grade_entry.status, GradeEntry.Status.PUBLISHED)

    def test_release_requires_teacher_post_csrf_and_matching_quiz(self):
        quiz = self.published_quiz()
        attempt = self.graded_attempt(quiz)
        url = self.release_url(quiz, attempt)
        for user in (self.student, self.outsider):
            self.client.force_login(user)
            self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(url).status_code, 405)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.teacher)
        self.assertEqual(csrf.post(url).status_code, 403)
        self.client.force_login(self.teacher)
        other_quiz = Quiz.objects.create(
            course=self.course, title='Otra prueba', reference_code='TEST-OTHER',
        )
        self.assertEqual(self.client.post(self.release_url(other_quiz, attempt)).status_code, 404)
        attempt.refresh_from_db()
        self.assertIsNone(attempt.results_released_at)

    def test_scheduled_ungraded_hidden_or_manual_review_results_cannot_be_released(self):
        quiz = self.published_quiz()
        attempt = self.graded_attempt(quiz)
        self.client.force_login(self.teacher)
        for reason in ('scheduled', 'ungraded', 'hidden', 'manual_review', 'missing_grade'):
            with self.subTest(reason=reason):
                quiz.publish_grade_at = timezone.now() + timedelta(days=1) if reason == 'scheduled' else None
                quiz.save(update_fields=['publish_grade_at'])
                attempt.status = 'submitted' if reason == 'ungraded' else 'graded'
                attempt.save(update_fields=['status'])
                GradeEntry.objects.filter(attempt=attempt).update(is_hidden=reason == 'hidden')
                attempt.answers.update(manual_review=reason == 'manual_review')
                if reason == 'missing_grade':
                    GradeEntry.objects.filter(attempt=attempt).delete()
                response = self.client.post(self.release_url(quiz, attempt))
                self.assertEqual(response.status_code, 302)
                attempt.refresh_from_db()
                self.assertIsNone(attempt.results_released_at)
        self.assertFalse(Notification.objects.filter(event__event_type='grade').exists())

    def test_teacher_cannot_release_another_section_students_result(self):
        quiz = self.published_quiz()
        attempt = self.graded_attempt(quiz)
        section = Section.objects.create(course=self.course, title='Otra clase')
        quiz.section = section
        quiz.save(update_fields=['section'])
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.post(self.release_url(quiz, attempt)).status_code, 403)
        self.assertFalse(quiz_solutions_visible(quiz, student=self.student))
