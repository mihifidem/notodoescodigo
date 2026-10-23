from decimal import Decimal

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import AuditEvent, GradeEntry, Quiz, QuizAnswer, QuizAttempt
from .quiz_management import check_editable, update_quiz_passing_score
from .test_quiz_attempts import StudentQuizTests
from django.core.exceptions import PermissionDenied, ValidationError


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class QuizPassingScoreTests(TestCase):
    setUp = StudentQuizTests.setUp
    url = StudentQuizTests.url
    create_quiz = StudentQuizTests.create_quiz
    published_quiz = StudentQuizTests.published_quiz

    def threshold_url(self, quiz):
        return reverse('lms:quiz_passing_score', kwargs={
            'course_id': quiz.course_id, 'quiz_id': quiz.pk,
        })

    def test_threshold_updates_closed_quiz_results_without_changing_grades_or_publication(self):
        quiz = self.published_quiz(passing_score='7')
        quiz.status = Quiz.Status.CLOSED
        quiz.save(update_fields=['status'])
        attempt = QuizAttempt.objects.create(
            quiz=quiz, user=self.student, status='graded', score=6, is_passed=False,
            configuration_snapshot={'passing_score': '7', 'question_versions': {'1': 2}},
        )
        entry = GradeEntry.objects.create(
            attempt=attempt, course=self.course, user=self.student,
            grade=6, max_grade=10, status='draft',
        )
        pending = QuizAttempt.objects.create(
            quiz=quiz, user=self.student, status='in_progress',
            configuration_snapshot={'passing_score': '7'},
        )
        self.client.force_login(self.teacher)
        url = self.threshold_url(quiz)
        self.assertContains(self.client.get(self.url('quiz_detail', quiz)), url)
        self.assertContains(self.client.get(url), 'name="passing_score"')
        self.assertRedirects(self.client.post(url, {'passing_score': '5'}), self.url('quiz_detail', quiz))
        quiz.refresh_from_db()
        attempt.refresh_from_db()
        pending.refresh_from_db()
        entry.refresh_from_db()
        self.assertEqual(quiz.passing_score, Decimal('5'))
        self.assertTrue(attempt.is_passed)
        self.assertEqual(attempt.score, Decimal('6'))
        self.assertEqual(attempt.configuration_snapshot['question_versions'], {'1': 2})
        self.assertEqual(pending.configuration_snapshot['passing_score'], '5')
        self.assertEqual(pending.status, 'in_progress')
        self.assertEqual(entry.grade, Decimal('6'))
        self.assertEqual(entry.status, 'draft')
        self.assertIsNone(entry.published_at)
        self.assertIsNone(quiz.publish_grade_at)
        self.assertEqual(AuditEvent.objects.filter(metadata__quiz_action='passing_score_updated').count(), 1)
        self.assertRedirects(self.client.post(url, {'passing_score': '8'}), self.url('quiz_detail', quiz))
        attempt.refresh_from_db()
        self.assertFalse(attempt.is_passed)
        with self.assertRaises(ValidationError):
            check_editable(quiz, allow_published=True)

    def test_pending_attempt_uses_updated_threshold_when_graded(self):
        quiz = self.published_quiz(passing_score='7')
        item = quiz.questions.get()
        attempt = QuizAttempt.objects.create(
            quiz=quiz, user=self.student, status='submitted',
            configuration_snapshot={'passing_score': '7'},
        )
        QuizAnswer.objects.create(
            attempt=attempt, question=item, question_version=item.question_version, response='B',
        )
        update_quiz_passing_score(course=self.course, actor=self.teacher, quiz_id=quiz.pk, passing_score=0)
        quiz.refresh_from_db()
        attempt.refresh_from_db()
        quiz.evaluate_attempt(attempt)
        attempt.refresh_from_db()
        self.assertEqual(attempt.score, Decimal('0'))
        self.assertTrue(attempt.is_passed)

    def test_validation_permissions_csrf_and_unchanged_threshold(self):
        quiz = self.published_quiz()
        url = self.threshold_url(quiz)
        original = quiz.passing_score
        for user in (self.student, self.outsider):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, {'passing_score': 0}).status_code, 403)
        with self.assertRaises(PermissionDenied):
            update_quiz_passing_score(course=self.course, actor=self.student, quiz_id=quiz.pk, passing_score=0)
        self.client.force_login(self.teacher)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.teacher)
        self.assertEqual(csrf.post(url, {'passing_score': 0}).status_code, 403)
        for value in ('-1', '10.01', 'NaN', '', 'incorrecto'):
            with self.subTest(value=value):
                response = self.client.post(url, {'passing_score': value})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form'].errors)
                quiz.refresh_from_db()
                self.assertEqual(quiz.passing_score, original)
        with self.assertRaises(ValidationError):
            update_quiz_passing_score(course=self.course, actor=self.teacher, quiz_id=quiz.pk, passing_score=11)
        update_quiz_passing_score(course=self.course, actor=self.teacher, quiz_id=quiz.pk, passing_score=original)
        self.assertFalse(AuditEvent.objects.filter(metadata__quiz_action='passing_score_updated').exists())
