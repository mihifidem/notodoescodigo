from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.courses.models import Curso
from .models import AuditEvent, CourseMembership, Quiz, QuizAttempt, QuizQuestion
from .services import delete_course
from .test_quiz_management import QuizManagementTests


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class StaffCourseDeletionTests(TestCase):
    setUp = QuizManagementTests.setUp
    url = QuizManagementTests.url
    create_quiz = QuizManagementTests.create_quiz

    def delete_url(self):
        return reverse('lms:course_delete', kwargs={'course_id': self.course.pk})

    def confirmation(self):
        return {'confirm': 'delete', 'course_name': self.course.nombre}

    def test_teacher_owner_and_student_cannot_delete_even_with_course_permissions(self):
        from django.contrib.auth.models import Permission
        for role in ('owner', 'teacher', 'assistant'):
            CourseMembership.objects.filter(course=self.course, user=self.teacher).update(role=role)
            self.client.force_login(self.teacher)
            self.teacher.user_permissions.add(Permission.objects.get(codename='delete_curso'))
            self.assertEqual(self.client.get(self.delete_url()).status_code, 403)
            self.assertEqual(self.client.post(self.delete_url(), self.confirmation()).status_code, 403)
            with self.assertRaises(PermissionDenied):
                delete_course(course_id=self.course.pk, actor=self.teacher)
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(self.delete_url(), self.confirmation()).status_code, 403)
        self.assertTrue(Curso.objects.filter(pk=self.course.pk).exists())

    def test_staff_requires_confirmation_and_csrf_and_get_does_not_delete(self):
        self.teacher.is_staff = True
        self.teacher.save(update_fields=['is_staff'])
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(self.delete_url()), 'Esta acción es irreversible')
        self.assertTrue(Curso.objects.filter(pk=self.course.pk).exists())
        for data in ({}, {'confirm': 'delete'}, {'confirm': 'delete', 'course_name': 'Otro nombre'}):
            self.assertEqual(self.client.post(self.delete_url(), data).status_code, 400)
            self.assertTrue(Curso.objects.filter(pk=self.course.pk).exists())
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.teacher)
        self.assertEqual(csrf.post(self.delete_url(), self.confirmation()).status_code, 403)

    def test_staff_deletes_course_with_quizzes_and_keeps_users_and_other_courses(self):
        quiz = self.create_quiz()
        QuizAttempt.objects.create(quiz=quiz, user=self.student)
        self.teacher.is_staff = True
        self.teacher.save(update_fields=['is_staff'])
        self.client.force_login(self.teacher)
        self.assertRedirects(self.client.post(self.delete_url(), self.confirmation()), reverse('lms:dashboard'))
        self.assertFalse(Curso.objects.filter(pk=self.course.pk).exists())
        self.assertFalse(Quiz.objects.filter(pk=quiz.pk).exists())
        self.assertTrue(Curso.objects.filter(pk=self.other.pk).exists())
        self.assertTrue(get_user_model().objects.filter(pk=self.student.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(
            actor=self.teacher, metadata__course_action='deleted', metadata__course_id=self.course.pk,
        ).exists())

    def test_shared_protected_questions_block_deletion_without_partial_changes(self):
        quiz = self.create_quiz()
        shared = Quiz.objects.create(course=self.other, title='Compartida')
        QuizQuestion.objects.create(quiz=shared, question_version=self.question.current_version)
        self.teacher.is_staff = True
        self.teacher.save(update_fields=['is_staff'])
        self.client.force_login(self.teacher)
        response = self.client.post(self.delete_url(), self.confirmation())
        self.assertContains(response, 'No se ha borrado ningún dato', status_code=400)
        self.assertTrue(Curso.objects.filter(pk=self.course.pk).exists())
        self.assertTrue(quiz.questions.exists())
        self.assertTrue(shared.questions.exists())

    def test_delete_link_is_only_visible_to_staff(self):
        detail = reverse('lms:course_detail', kwargs={'course_id': self.course.pk})
        self.assertNotContains(self.client.get(detail), self.delete_url())
        self.teacher.is_staff = True
        self.teacher.save(update_fields=['is_staff'])
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(detail), self.delete_url())
