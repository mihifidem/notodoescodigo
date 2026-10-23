from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from .campus import learning_status
from .knowledge import save_bank_question
from .lifecycle import process_quiz
from .models import ModuleContent, QuestionBank, Quiz, QuizQuestion
from .quiz_attempts import begin_attempt
from .test_campus_access import CampusFixture


class CampusQuizTests(CampusFixture):
    def setUp(self):
        super().setUp()
        bank = QuestionBank.objects.create(course=self.course, owner=self.teacher, title='Banco fixture', status='published')
        question = save_bank_question(bank=bank, actor=self.teacher, data={
            'prompt': 'Pregunta fixture', 'option_a': 'A', 'option_b': 'B', 'option_c': 'C',
            'option_d': 'D', 'correct': 'A', 'explanation': 'PRIVATE_ANSWER_EXPLANATION',
            'difficulty': 1, 'points': 1, 'status': 'published',
        })
        self.quiz = Quiz.objects.create(
            course=self.course, title='Examen fixture', status='published',
            duration_minutes=10, max_attempts=2, passing_score=6, allow_resume=True,
            publish_grade_at=timezone.now() + timedelta(days=1),
            publish_answers_at=timezone.now() + timedelta(days=1),
        )
        self.question = QuizQuestion.objects.create(
            quiz=self.quiz, question_version=question.current_version, points=1,
            metadata={'grading_scale': 10},
        )
        ModuleContent.objects.create(module=self.module, quiz=self.quiz, criterion='passed')
        self.membership = self.enroll()

    def test_timed_attempt_resume_hidden_answers_grade_release_and_completion(self):
        self.complete_lesson()
        self.assertFalse(learning_status(self.membership)['passed'])
        attempt = begin_attempt(user=self.student, course_id=self.course.pk, quiz_id=self.quiz.pk)
        deadline = attempt.deadline_at
        self.assertEqual(begin_attempt(
            user=self.student, course_id=self.course.pk, quiz_id=self.quiz.pk,
        ).deadline_at, deadline)
        url = reverse('lms:quiz_attempt', args=[self.course.pk, self.quiz.pk, attempt.pk])
        self.assertNotContains(self.client.get(url), 'PRIVATE_ANSWER_EXPLANATION')
        self.assertEqual(self.client.post(url, {
            'action': 'submit', f'question_{self.question.pk}': '0',
        }).status_code, 302)
        attempt.refresh_from_db()
        self.assertEqual(attempt.score, 10)
        self.assertTrue(attempt.is_passed)
        self.assertFalse(learning_status(self.membership)['passed'])
        self.quiz.publish_grade_at = timezone.now() - timedelta(seconds=1)
        self.quiz.save()
        process_quiz(self.quiz.pk, now=timezone.now())
        self.membership.refresh_from_db()
        self.assertIsNotNone(self.membership.passed_at)
        self.assertTrue(learning_status(self.membership)['passed'])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 403)
