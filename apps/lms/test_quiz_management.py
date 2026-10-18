from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.courses.models import Curso
from .knowledge import save_bank_question
from .models import AuditEvent, CourseMembership, QuestionBank, Quiz, QuizAnswer, QuizAttempt, QuizQuestion, Section


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class QuizManagementTests(TestCase):
    def test_delete_confirmation_preserves_bank_questions(self):
        quiz = self.create_quiz()
        area = reverse(
            'lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'quizzes'},
        )
        response = self.client.get(area)
        self.assertContains(response, self.url('quiz_edit', quiz))
        self.assertContains(response, self.url('quiz_delete', quiz))
        self.assertContains(self.client.get(self.url('quiz_detail', quiz)), 'Eliminar prueba')
        self.assertContains(self.client.get(self.url('quiz_delete', quiz)), 'Confirmar eliminación')
        self.assertTrue(Quiz.objects.filter(pk=quiz.pk).exists())
        self.client.post(self.url('quiz_delete', quiz), {})
        self.assertTrue(Quiz.objects.filter(pk=quiz.pk).exists())
        version_id = self.question.current_version_id
        self.assertRedirects(self.client.post(self.url('quiz_delete', quiz), {'confirm': 'delete'}), area)
        self.assertFalse(Quiz.objects.filter(pk=quiz.pk).exists())
        self.assertFalse(QuizQuestion.objects.filter(quiz_id=quiz.pk).exists())
        self.question.refresh_from_db()
        self.assertEqual(self.question.current_version_id, version_id)
        self.assertTrue(QuestionBank.objects.filter(pk=self.bank.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(metadata__quiz_action='deleted', metadata__quiz_id=quiz.pk).exists())

    def test_delete_blocks_attempts_and_enforces_permissions_and_csrf(self):
        quiz = self.create_quiz()
        url = self.url('quiz_delete', quiz)
        for user in (self.student, self.outsider):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, {'confirm': 'delete'}).status_code, 403)
        self.client.force_login(self.teacher)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.teacher)
        self.assertEqual(csrf.post(url, {'confirm': 'delete'}).status_code, 403)
        foreign = Quiz.objects.create(course=self.other, title='Prueba ajena')
        self.assertEqual(self.client.get(self.url('quiz_delete', foreign)).status_code, 404)
        self.assertEqual(self.client.post(self.url('quiz_delete', foreign), {'confirm': 'delete'}).status_code, 404)
        for status in QuizAttempt.Status.values:
            with self.subTest(status=status):
                attempt = QuizAttempt.objects.create(quiz=quiz, user=self.student, status=status)
                response = self.client.post(url, {'confirm': 'delete'}, follow=True)
                self.assertContains(response, 'No se puede eliminar una prueba con intentos')
                self.assertTrue(Quiz.objects.filter(pk=quiz.pk).exists())
                self.assertTrue(QuizAttempt.objects.filter(pk=attempt.pk).exists())
                self.assertTrue(quiz.questions.exists())
                attempt.delete()
        self.assertFalse(AuditEvent.objects.filter(metadata__quiz_action='deleted').exists())

    def test_published_quiz_without_attempts_can_be_deleted(self):
        quiz = self.create_quiz()
        self.client.post(self.url('quiz_status', quiz), {'action': 'publish'})
        self.client.force_login(self.student)
        self.assertNotContains(self.client.get(self.url('quiz_detail', quiz)), 'Eliminar prueba')
        self.client.force_login(self.teacher)
        response = self.client.get(self.url('quiz_detail', quiz))
        self.assertContains(response, 'Editar prueba')
        self.client.post(self.url('quiz_delete', quiz), {'confirm': 'delete'})
        self.assertFalse(Quiz.objects.filter(pk=quiz.pk).exists())

    def test_deduction_required_only_when_enabled(self):
        for deduction in ('', '0', '-0.5', '11'):
            response = self.client.post(self.url('quiz_create'), {
                **self.data, 'penalize_errors': 'on', 'error_deduction': deduction,
            })
            self.assertTrue(response.context['form'].errors)
            self.assertFalse(Quiz.objects.exists())
        quiz = self.create_quiz(penalize_errors='on', error_deduction='0.25')
        self.assertEqual(quiz.questions.get().metadata['error_deduction'], '0.25')
        attempt = QuizAttempt.objects.create(quiz=quiz, user=self.student)
        item = quiz.questions.get()
        answer = QuizAnswer.objects.create(attempt=attempt, question=item, question_version=item.question_version, response='B')
        quiz.evaluate_attempt(attempt)
        answer.refresh_from_db()
        self.assertEqual(answer.score, Decimal('-0.25'))
        self.assertEqual(attempt.score, Decimal('0'))
        answer.response = ''
        answer.save()
        quiz.evaluate_attempt(attempt)
        answer.refresh_from_db()
        self.assertEqual(answer.score, Decimal('0'))

    def test_published_edit_buttons_and_first_attempt_lock(self):
        quiz = self.create_quiz()
        self.client.post(self.url('quiz_status', quiz), {'action': 'publish'})
        area = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'quizzes'})
        edit = self.url('quiz_edit', quiz)
        for target in (area, self.url('quiz_detail', quiz)):
            self.assertContains(self.client.get(target), edit)
        self.assertContains(self.client.get(edit), 'Guardar cambios')
        self.client.post(edit, {**self.data, 'title': 'Actualización publicada', 'passing_score': '7'})
        quiz.refresh_from_db()
        self.assertEqual(quiz.status, 'published')
        self.assertEqual(quiz.passing_score, Decimal('7'))
        self.assertEqual(quiz.title, 'Actualización publicada')
        self.assertNotContains(self.client.get(self.url('quiz_detail', quiz)), 'name="action" value="publish"')
        item_id = quiz.questions.get().pk
        QuizAttempt.objects.create(quiz=quiz, user=self.student, status='in_progress')
        for target in (area, self.url('quiz_detail', quiz)):
            self.assertNotContains(self.client.get(target), edit)
        self.assertRedirects(self.client.get(edit), self.url('quiz_detail', quiz))
        self.client.post(edit, {**self.data, 'title': 'Cambio bloqueado'})
        quiz.refresh_from_db()
        self.assertEqual(quiz.title, 'Actualización publicada')
        self.assertEqual(quiz.questions.get().pk, item_id)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(edit).status_code, 403)
        self.assertEqual(self.client.post(edit, self.data).status_code, 403)

    def test_attempt_created_while_editing_blocks_save(self):
        from .quiz_management import QuizConfigurationForm, save_quiz_configuration
        from django.core.exceptions import ValidationError
        quiz = self.create_quiz()
        self.client.post(self.url('quiz_status', quiz), {'action': 'publish'})
        form = QuizConfigurationForm({**self.data, 'title': 'Edición tardía'}, course=self.course, instance=quiz)
        self.assertTrue(form.is_valid())
        QuizAttempt.objects.create(quiz=quiz, user=self.student, status='in_progress')
        with self.assertRaises(ValidationError):
            save_quiz_configuration(course=self.course, actor=self.teacher, form=form, quiz=quiz)
        quiz.refresh_from_db()
        self.assertEqual(quiz.title, self.data['title'])
        self.assertEqual(quiz.status, 'published')

    def test_twenty_questions_scale_penalty_and_blank(self):
        questions = [self.question]
        for number in range(19):
            questions.append(save_bank_question(
                bank=self.bank, actor=self.teacher, data={**self.question_data, 'prompt': f'Pregunta {number}'},
            ))
        quiz = self.create_quiz(selected_questions=[q.pk for q in questions], passing_score=7, penalize_errors='on', error_deduction='0.1667')
        response = self.client.get(self.url('quiz_detail', quiz))
        self.assertEqual(response.context['question_value'], Decimal('0.5'))
        self.assertContains(response, 'Información de la prueba')
        self.assertContains(response, '<details class="content-accordion-item"', count=22)
        attempt = QuizAttempt.objects.create(quiz=quiz, user=self.student)
        items = list(quiz.questions.all())
        for item in items[:14]:
            QuizAnswer.objects.create(attempt=attempt, question=item, question_version=item.question_version,
                                      response=self.question_data['option_a'])
        entry = quiz.evaluate_attempt(attempt)
        self.assertEqual(entry.grade, Decimal('7.00'))
        self.assertEqual(entry.max_grade, Decimal('10'))
        self.assertTrue(attempt.is_passed)
        wrong = QuizAnswer.objects.create(attempt=attempt, question=items[14], question_version=items[14].question_version, response='B')
        QuizAnswer.objects.create(attempt=attempt, question=items[15], question_version=items[15].question_version, response='')
        entry = quiz.evaluate_attempt(attempt)
        self.assertEqual(entry.grade, Decimal('6.83'))
        wrong.refresh_from_db()
        self.assertEqual(wrong.score, Decimal('-0.17'))
        self.assertFalse(attempt.is_passed)

    def test_blank_deduction_counts_missing_and_explicit_blank_answers_and_is_frozen(self):
        questions = [self.question]
        for number in range(3):
            questions.append(save_bank_question(
                bank=self.bank, actor=self.teacher,
                data={**self.question_data, 'prompt': f'Pregunta adicional {number}'},
            ))
        quiz = self.create_quiz(
            selected_questions=[q.pk for q in questions], question_count=4,
            penalize_errors='on', error_deduction='0.5', blank_deduction='0.25',
        )
        items = list(quiz.questions.all())
        attempt = QuizAttempt.objects.create(
            quiz=quiz, user=self.student,
            configuration_snapshot={'question_grading': {str(item.pk): dict(item.metadata) for item in items}},
        )
        for item, answer in zip(items, [self.question_data['option_a'], 'incorrecta', '']):
            QuizAnswer.objects.create(
                attempt=attempt, question=item, question_version=item.question_version, response=answer,
            )
        items[-1].metadata['blank_deduction'] = '5'
        items[-1].save(update_fields=['metadata'])
        self.assertEqual(quiz.evaluate_attempt(attempt).grade, Decimal('1.50'))
        self.assertEqual(attempt.blank_count, 2)
        self.assertEqual(attempt.wrong_count, 1)
        self.assertEqual(attempt.answers.get(question=items[2]).score, Decimal('-0.25'))
        response = self.client.get(self.url('quiz_detail', quiz))
        self.assertEqual(response.context['blank_penalty'], Decimal('0.25'))

    def test_blank_deduction_rejects_negative_and_above_scale(self):
        from .quiz_management import QuizConfigurationForm
        for value in ('-0.1', '10.1', 'no-numero'):
            form = QuizConfigurationForm({**self.data, 'blank_deduction': value}, course=self.course)
            self.assertFalse(form.is_valid())
            self.assertIn('blank_deduction', form.errors)

    def test_scale_without_penalty_and_immutable_question_points(self):
        quiz = self.create_quiz(passing_score=7)
        attempt = QuizAttempt.objects.create(quiz=quiz, user=self.student)
        item = quiz.questions.get()
        answer = QuizAnswer.objects.create(attempt=attempt, question=item, question_version=item.question_version, response='B')
        self.assertEqual(quiz.evaluate_attempt(attempt).grade, Decimal('0'))
        answer.response = self.question_data['option_a']
        answer.save()
        save_bank_question(bank=self.bank, actor=self.teacher, question=self.question,
                           data={**self.question_data, 'points': 999, 'correct': 'B'})
        self.assertEqual(quiz.evaluate_attempt(attempt).grade, Decimal('10'))

    def test_open_quiz_appears_on_student_dashboard_at_opening(self):
        start = timezone.now().replace(microsecond=0)
        quiz = self.create_quiz(
            available_from=start.isoformat(), due_at=(start + timedelta(hours=1)).isoformat(),
        )
        self.client.post(self.url('quiz_status', quiz), {'action': 'publish'})
        self.client.force_login(self.student)
        dashboard = reverse('lms:student_dashboard')
        with patch('apps.lms.ui.timezone.now', return_value=start - timedelta(seconds=1)):
            self.assertNotContains(self.client.get(dashboard), quiz.title)
        with patch('apps.lms.ui.timezone.now', return_value=start):
            response = self.client.get(dashboard)
            self.assertContains(response, quiz.title)
            self.assertContains(response, self.url('quiz_detail', quiz))
            self.assertContains(self.client.get(reverse('lms:global_area', kwargs={'area': 'calendar'})), quiz.title)
        with patch('apps.lms.ui.timezone.now', return_value=start + timedelta(hours=1, seconds=1)):
            self.assertNotContains(self.client.get(dashboard), quiz.title)

    def test_student_dashboard_quizzes_respect_group_and_membership(self):
        quiz = self.create_quiz()
        self.client.post(self.url('quiz_status', quiz), {'action': 'publish'})
        section = Section.objects.create(course=self.course, title='Grupo privado')
        quiz.section = section
        quiz.save(update_fields=['section'])
        self.client.force_login(self.student)
        dashboard = reverse('lms:student_dashboard')
        self.assertNotContains(self.client.get(dashboard), quiz.title)
        self.membership.section = section
        self.membership.save()
        self.assertContains(self.client.get(dashboard), quiz.title)
        self.membership.status = 'suspended'
        self.membership.save()
        self.assertNotContains(self.client.get(dashboard), quiz.title)

    def setUp(self):
        User = get_user_model()
        self.teacher = User.objects.create_user('quiz_teacher')
        self.student = User.objects.create_user('quiz_student')
        self.outsider = User.objects.create_user('quiz_outsider')
        self.course = Curso.objects.create(nombre='Pruebas HTML', fecha_inicio=date(2026, 1, 1))
        self.other = Curso.objects.create(nombre='Otro curso', fecha_inicio=date(2026, 1, 1))
        CourseMembership.objects.create(course=self.course, user=self.teacher, role='teacher', status='active')
        self.membership = CourseMembership.objects.create(course=self.course, user=self.student, role='student', status='active')
        self.bank = QuestionBank.objects.create(course=self.course, owner=self.teacher, title='Banco publicado', status='published')
        self.question_data = {
            'prompt': 'ENUNCIADO PRIVADO <html>', 'option_a': 'OPCION SECRETA A',
            'option_b': 'B', 'option_c': 'C', 'option_d': 'D', 'correct': 'A',
            'explanation': 'SOLUCION SECRETA', 'difficulty': 1, 'points': 2, 'status': 'published',
        }
        self.question = save_bank_question(bank=self.bank, actor=self.teacher, data=self.question_data)
        self.client.force_login(self.teacher)
        self.data = {
            'title': 'Test de verificación', 'description': 'Instrucciones para alumnos',
            'reference_code': 'TEST-001',
            'selection': 'manual', 'selected_questions': [self.question.pk],
            'bank': self.bank.pk, 'question_count': 1,
            'duration_minutes': 30, 'max_attempts': 2, 'passing_score': '1', 'allow_resume': 'on',
        }

    def url(self, name, quiz=None, course=None):
        kwargs = {'course_id': (course or self.course).pk}
        if quiz:
            kwargs['quiz_id'] = quiz.pk
        return reverse('lms:' + name, kwargs=kwargs)

    def create_quiz(self, **changes):
        if 'selected_questions' in changes and 'question_count' not in changes:
            changes['question_count'] = len(changes['selected_questions'])
        response = self.client.post(self.url('quiz_create'), {**self.data, **changes})
        quiz = Quiz.objects.get(course=self.course)
        self.assertRedirects(response, self.url('quiz_detail', quiz))
        return quiz

    def test_create_edit_publish_and_close(self):
        self.assertContains(self.client.get(self.url('quiz_create')), 'Guardar borrador')
        quiz = self.create_quiz()
        item = quiz.questions.get()
        self.assertEqual(item.question_version_id, self.question.current_version_id)
        self.assertEqual(item.points, 2)
        self.assertContains(self.client.get(self.url('quiz_detail', quiz)), 'ENUNCIADO PRIVADO &lt;html&gt;')
        self.client.post(self.url('quiz_edit', quiz), {**self.data, 'title': 'Título editado'})
        quiz.refresh_from_db()
        self.assertEqual(quiz.title, 'Título editado')
        self.client.post(self.url('quiz_status', quiz), {'action': 'publish'})
        quiz.refresh_from_db()
        self.assertEqual(quiz.status, 'published')
        self.client.post(self.url('quiz_edit', quiz), {**self.data, 'title': 'Publicada editada'})
        quiz.refresh_from_db()
        self.assertEqual(quiz.title, 'Publicada editada')
        self.assertEqual(quiz.status, 'published')
        self.client.post(self.url('quiz_status', quiz), {'action': 'close'})
        quiz.refresh_from_db()
        self.assertEqual(quiz.status, 'closed')
        self.client.post(self.url('quiz_status', quiz), {'action': 'publish'})
        quiz.refresh_from_db()
        self.assertEqual(quiz.status, 'closed')
        self.assertTrue(AuditEvent.objects.filter(course=self.course, metadata__quiz_action='publish').exists())

    def test_invalid_configuration_never_creates_partial_quiz(self):
        start = timezone.now()
        for change in (
            {'max_attempts': 0}, {'passing_score': -1}, {'passing_score': 'invalid'},
            {'passing_score': 11}, {'selected_questions': []},
            {'available_from': start.isoformat(), 'available_until': start.isoformat()},
            {'available_from': start.isoformat(), 'due_at': (start - timedelta(hours=1)).isoformat()},
            {'available_until': start.isoformat(), 'publish_answers_at': (start - timedelta(hours=1)).isoformat()},
        ):
            with self.subTest(change=change):
                response = self.client.post(self.url('quiz_create'), {**self.data, **change})
                self.assertEqual(response.status_code, 200)
                self.assertFalse(Quiz.objects.filter(course=self.course).exists())

    def test_random_selection_filters_and_insufficient_pool(self):
        second = save_bank_question(bank=self.bank, actor=self.teacher, data={**self.question_data, 'prompt': 'Otra básica'})
        save_bank_question(bank=self.bank, actor=self.teacher, data={**self.question_data, 'difficulty': 3})
        data = {**self.data, 'selection': 'random', 'bank': self.bank.pk, 'difficulty': 1, 'question_count': 3}
        response = self.client.post(self.url('quiz_create'), data)
        self.assertContains(response, 'No hay suficientes preguntas')
        self.assertFalse(Quiz.objects.exists())
        quiz = self.create_quiz(selection='random', bank=self.bank.pk, difficulty=1, question_count=2)
        self.assertEqual(set(quiz.questions.values_list('question_version__question_id', flat=True)), {self.question.pk, second.pk})
        self.assertEqual(quiz.questions.count(), 2)

    def test_permissions_scope_and_csrf(self):
        quiz = self.create_quiz()
        for user in (self.student, self.outsider):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url('quiz_create')).status_code, 403)
            self.assertEqual(self.client.post(self.url('quiz_status', quiz), {'action': 'publish'}).status_code, 403)
        self.client.force_login(self.teacher)
        foreign = Quiz.objects.create(course=self.other, title='Fuera de ámbito')
        self.assertEqual(self.client.get(self.url('quiz_detail', foreign)).status_code, 404)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.teacher)
        self.assertEqual(csrf.post(self.url('quiz_status', quiz), {'action': 'publish'}).status_code, 403)
        self.assertEqual(self.client.get(self.url('quiz_status', quiz)).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.get(self.url('quiz_create')).status_code, 302)

    def test_students_see_only_published_configuration_without_answers(self):
        quiz = self.create_quiz()
        self.client.force_login(self.student)
        area = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'quizzes'})
        self.assertNotContains(self.client.get(area), quiz.title)
        self.assertEqual(self.client.get(self.url('quiz_detail', quiz)).status_code, 403)
        self.client.force_login(self.teacher)
        self.client.post(self.url('quiz_status', quiz), {'action': 'publish'})
        self.client.force_login(self.student)
        self.assertContains(self.client.get(area), quiz.title)
        response = self.client.get(self.url('quiz_detail', quiz))
        self.assertContains(response, 'Instrucciones para alumnos')
        for secret in ('ENUNCIADO PRIVADO', 'OPCION SECRETA', 'SOLUCION SECRETA', 'Publicar prueba', 'Editar prueba'):
            self.assertNotContains(response, secret)
        self.assertIsNone(response.context['quiz_questions'])

    def test_section_restrictions_and_foreign_questions(self):
        section = Section.objects.create(course=self.course, title='Grupo A')
        foreign_section = Section.objects.create(course=self.other, title='Grupo B')
        response = self.client.post(self.url('quiz_create'), {**self.data, 'section': foreign_section.pk})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Quiz.objects.exists())
        quiz = self.create_quiz(section=section.pk)
        self.client.post(self.url('quiz_status', quiz), {'action': 'publish'})
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(self.url('quiz_detail', quiz)).status_code, 403)
        self.membership.section = section
        self.membership.save()
        self.assertEqual(self.client.get(self.url('quiz_detail', quiz)).status_code, 200)

    def test_versions_and_attempts_cannot_be_overwritten(self):
        quiz = self.create_quiz()
        original = quiz.questions.get().question_version_id
        save_bank_question(bank=self.bank, actor=self.teacher, data={**self.question_data, 'points': 9}, question=self.question)
        self.assertEqual(quiz.questions.get().question_version_id, original)
        self.assertEqual(quiz.questions.get().points, 2)
        QuizAttempt.objects.create(quiz=quiz, user=self.student)
        self.client.post(self.url('quiz_edit', quiz), {**self.data, 'title': 'No permitido'})
        self.client.post(self.url('quiz_status', quiz), {'action': 'publish'})
        quiz.refresh_from_db()
        self.assertEqual(quiz.title, self.data['title'])
        self.assertEqual(quiz.status, 'draft')
        self.assertEqual(quiz.questions.get().question_version_id, original)

    def test_transaction_rolls_back_edit_and_publish_requires_questions(self):
        quiz = self.create_quiz()
        original = quiz.questions.get().pk
        with patch('apps.lms.quiz_management.QuizQuestion.objects.create', side_effect=RuntimeError('fallo de escritura')):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url('quiz_edit', quiz), {**self.data, 'title': 'Cambio fallido'})
        quiz.refresh_from_db()
        self.assertEqual(quiz.title, self.data['title'])
        self.assertEqual(quiz.questions.get().pk, original)
        quiz.questions.all().delete()
        self.client.post(self.url('quiz_status', quiz), {'action': 'publish'})
        quiz.refresh_from_db()
        self.assertEqual(quiz.status, 'draft')

    def test_foreign_or_unpublished_questions_are_rejected(self):
        foreign_bank = QuestionBank.objects.create(course=self.other, title='Banco ajeno', status='published')
        # The fixture deliberately bypasses the teacher service to exercise forged POST ids.
        foreign = self.question.__class__.objects.create(
            bank=foreign_bank, title='Pregunta ajena', status='published',
        )
        for question_id in (foreign.pk, 999999):
            response = self.client.post(self.url('quiz_create'), {**self.data, 'selected_questions': [question_id]})
            self.assertTrue(response.context['form'].errors)
            self.assertFalse(Quiz.objects.exists())
        quiz = self.create_quiz()
        self.bank.status = 'archived'
        self.bank.save()
        self.client.post(self.url('quiz_status', quiz), {'action': 'publish'})
        quiz.refresh_from_db()
        self.assertEqual(quiz.status, 'draft')

    def test_publication_validates_pinned_version_not_new_current_version(self):
        quiz = self.create_quiz()
        saved = quiz.questions.get().question_version
        save_bank_question(bank=self.bank, actor=self.teacher, data={**self.question_data, 'points': 9}, question=self.question)
        self.client.post(self.url('quiz_status', quiz), {'action': 'publish'})
        quiz.refresh_from_db()
        self.assertEqual(quiz.status, 'published')
        self.assertEqual(quiz.questions.get().question_version_id, saved.pk)
        self.assertEqual(quiz.questions.get().points, 2)

    def test_create_section_from_quiz_form(self):
        response = self.client.get(self.url('quiz_create'))
        self.assertContains(response, 'Añadir aula/clase')
        self.assertEqual(response.context['form'].fields['section'].label, 'Aula/clase')
        self.assertFalse(response.context['form'].fields['section'].required)
        quiz = self.create_quiz(new_section_title='Nuevo grupo')
        section = Section.objects.get(course=self.course, title='Nuevo grupo')
        self.assertEqual(quiz.section_id, section.pk)
        self.assertContains(self.client.get(self.url('quiz_edit', quiz)), 'Nuevo grupo')

    def test_invalid_quiz_does_not_create_section_and_preserves_name(self):
        response = self.client.post(self.url('quiz_create'), {
            **self.data, 'new_section_title': 'Grupo sin guardar', 'passing_score': 999,
        })
        self.assertContains(response, 'Grupo sin guardar')
        self.assertFalse(Section.objects.filter(course=self.course).exists())
        self.assertFalse(Quiz.objects.exists())

    def test_new_section_conflicts_and_course_scope(self):
        existing = Section.objects.create(course=self.course, title='Grupo existente')
        Section.objects.create(course=self.other, title='Grupo externo')
        for change in (
            {'new_section_title': 'Grupo existente'},
            {'new_section_title': 'Nuevo grupo', 'section': existing.pk},
        ):
            response = self.client.post(self.url('quiz_create'), {**self.data, **change})
            self.assertTrue(response.context['form'].errors)
            self.assertFalse(Quiz.objects.exists())
        quiz = self.create_quiz(new_section_title='Grupo externo')
        self.assertEqual(quiz.section.course_id, self.course.pk)

    def test_section_creation_rolls_back_with_quiz_failure(self):
        with patch('apps.lms.quiz_management.QuizQuestion.objects.create', side_effect=RuntimeError('fallo de escritura')):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url('quiz_create'), {**self.data, 'new_section_title': 'Grupo fallido'})
        self.assertFalse(Section.objects.filter(course=self.course, title='Grupo fallido').exists())
        self.assertFalse(Quiz.objects.exists())

    def test_duration_calculated_from_opening_and_delivery(self):
        start = timezone.now().replace(second=0, microsecond=0)
        quiz = self.create_quiz(
            available_from=start.isoformat(), due_at=(start + timedelta(minutes=90)).isoformat(),
            duration_minutes='',
        )
        self.assertEqual(quiz.duration_minutes, 90)
        self.client.post(self.url('quiz_edit', quiz), {
            **self.data, 'available_from': start.isoformat(),
            'due_at': (start + timedelta(minutes=125)).isoformat(), 'duration_minutes': 1,
        })
        quiz.refresh_from_db()
        self.assertEqual(quiz.duration_minutes, 125)
        self.assertContains(self.client.get(self.url('quiz_edit', quiz)), 'lms/quiz-form.js')

    def test_duration_manual_fallback_and_invalid_interval(self):
        quiz = self.create_quiz(duration_minutes=45)
        self.assertEqual(quiz.duration_minutes, 45)
        start = timezone.now()
        for due in (start, start - timedelta(minutes=1)):
            response = self.client.post(self.url('quiz_edit', quiz), {
                **self.data, 'available_from': start.isoformat(), 'due_at': due.isoformat(),
            })
            self.assertTrue(response.context['form'].errors)
            quiz.refresh_from_db()
            self.assertEqual(quiz.duration_minutes, 45)

    def test_failed_save_shows_error_summary_and_preserves_data(self):
        response = self.client.post(self.url('quiz_create'), {
            **self.data, 'selected_questions': [], 'title': 'Mi prueba sin preguntas',
        })
        self.assertContains(response, 'La prueba no se ha guardado')
        self.assertContains(response, 'Selecciona al menos una pregunta.')
        self.assertContains(response, 'Mi prueba sin preguntas')
        self.assertContains(response, 'id="quiz-form-errors"')
        self.assertFalse(Quiz.objects.exists())

    def test_empty_published_pool_explains_csv_drafts(self):
        self.bank.status = 'draft'
        self.bank.save()
        response = self.client.get(self.url('quiz_create'))
        self.assertContains(response, 'No hay preguntas publicadas disponibles')
        self.assertContains(response, 'Abrir Base del conocimiento')

    def test_quiz_form_selects_one_published_bank_without_bank_edit_buttons(self):
        from bs4 import BeautifulSoup
        self.bank.status = QuestionBank.Status.DRAFT
        self.bank.save(update_fields=['status'])
        archived = QuestionBank.objects.create(course=self.course, title='Archivado', status='archived')
        foreign = QuestionBank.objects.create(course=self.other, title='Ajeno', status='published')
        response = self.client.get(self.url('quiz_create'))
        selector = BeautifulSoup(response.content, 'html.parser').select_one('#id_bank')
        self.assertFalse(selector.has_attr('multiple'))
        options = selector.select('option')
        values = {option['value']: option.get_text() for option in options}
        self.assertNotIn(str(self.bank.pk), values)
        self.assertNotIn(str(archived.pk), values)
        self.assertNotIn(str(foreign.pk), values)
        edit = reverse('lms:knowledge_edit', kwargs={'course_id': self.course.pk, 'bank_id': self.bank.pk})
        self.assertNotContains(response, edit)
        self.assertNotContains(response, 'Editar banco')
        self.assertContains(response, 'Solo puedes elegir un banco publicado por prueba.')
        self.assertNotContains(response, foreign.title)
        self.assertEqual(self.client.get(edit).status_code, 200)
        response = self.client.post(self.url('quiz_create'), {**self.data, 'title': ''})
        self.assertNotContains(response, edit)
        self.assertNotContains(response, 'Editar banco')
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.status, QuestionBank.Status.DRAFT)
        self.assertFalse(Quiz.objects.exists())
        for user in (self.student, self.outsider):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url('quiz_create')).status_code, 403)
            self.assertEqual(self.client.get(edit).status_code, 403)

    def test_selection_blocks_only_display_for_selected_mode(self):
        from bs4 import BeautifulSoup
        response = self.client.get(self.url('quiz_create'))
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertTrue(soup.select_one('[data-manual-questions]').has_attr('hidden'))
        self.assertFalse(soup.select_one('[data-manual-questions]').has_attr('open'))
        self.assertTrue(all(block.has_attr('hidden') for block in soup.select('[data-random-questions]')))
        response = self.client.post(self.url('quiz_create'), {**self.data, 'title': ''})
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertFalse(soup.select_one('[data-manual-questions]').has_attr('hidden'))
        self.assertTrue(soup.select_one('[data-manual-questions]').has_attr('open'))
        response = self.client.post(self.url('quiz_create'), {
            **self.data, 'title': '', 'selection': 'random', 'selected_questions': [],
            'bank': self.bank.pk, 'question_count': 1,
        })
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertTrue(soup.select_one('[data-manual-questions]').has_attr('hidden'))
        self.assertTrue(all(not block.has_attr('hidden') for block in soup.select('[data-random-questions]')))
        self.assertContains(response, 'lms/quiz-form.js')

    def test_bank_and_total_required_for_both_selection_modes(self):
        for mode in ('manual', 'random'):
            for missing in ('bank', 'question_count'):
                response = self.client.post(self.url('quiz_create'), {
                    **self.data, 'selection': mode, missing: '',
                })
                self.assertIn(missing, response.context['form'].errors)
                self.assertFalse(Quiz.objects.exists())

    def test_reference_required_unique_and_preserved_on_edit(self):
        response = self.client.get(self.url('quiz_create'))
        self.assertEqual(response.context['form']['reference_code'].value(), '')
        for value in ('', '   '):
            response = self.client.post(self.url('quiz_create'), {**self.data, 'reference_code': value})
            self.assertIn('reference_code', response.context['form'].errors)
            self.assertFalse(Quiz.objects.exists())
        Quiz.objects.create(course=self.other, title='Referencia ajena', reference_code='EXISTING-REF')
        response = self.client.post(self.url('quiz_create'), {**self.data, 'reference_code': 'EXISTING-REF'})
        self.assertIn('reference_code', response.context['form'].errors)
        quiz = self.create_quiz(reference_code='MY-REF-001')
        self.assertEqual(quiz.reference_code, 'MY-REF-001')
        self.assertEqual(self.client.get(self.url('quiz_edit', quiz)).context['form']['reference_code'].value(), 'MY-REF-001')
        self.client.post(self.url('quiz_edit', quiz), {**self.data, 'reference_code': 'MY-REF-002'})
        quiz.refresh_from_db()
        self.assertEqual(quiz.reference_code, 'MY-REF-002')

    def test_quizzes_heading_does_not_offer_course_edit(self):
        quiz = self.create_quiz()
        quizzes = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'quizzes'})
        response = self.client.get(quizzes)
        self.assertNotContains(response, 'Editar aula')
        self.assertContains(response, self.url('quiz_edit', quiz))
        announcements = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'announcements'})
        self.assertContains(self.client.get(announcements), 'Editar aula')

    def test_manual_selection_must_match_bank_and_exact_total(self):
        second_bank = QuestionBank.objects.create(course=self.course, owner=self.teacher, title='Segundo', status='published')
        foreign = save_bank_question(bank=second_bank, actor=self.teacher, data=self.question_data)
        for changes in (
            {'selected_questions': [foreign.pk]},
            {'question_count': 2},
            {'selected_questions': [self.question.pk, foreign.pk], 'question_count': 2},
        ):
            response = self.client.post(self.url('quiz_create'), {**self.data, **changes})
            self.assertIn('selected_questions', response.context['form'].errors)
            self.assertFalse(Quiz.objects.exists())
        quiz = self.create_quiz()
        response = self.client.get(self.url('quiz_edit', quiz))
        self.assertEqual(response.context['form']['bank'].value(), self.bank.pk)
        self.assertEqual(response.context['form']['question_count'].value(), 1)
        self.assertContains(response, f'data-bank-id="{self.bank.pk}"')
        self.assertContains(response, f'data-bank-id="{second_bank.pk}"')
