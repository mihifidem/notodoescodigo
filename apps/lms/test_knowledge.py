import csv
import io
from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.courses.models import Curso
from .knowledge import CSV_COLUMNS, BankQuestionForm, parse_question_csv, save_bank_question
from .models import AuditEvent, CourseMembership, Question, QuestionBank, QuestionVersion, Quiz, QuizQuestion


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class KnowledgeTests(TestCase):
    def test_bulk_publication_selects_only_drafts_and_preserves_versions(self):
        draft = save_bank_question(bank=self.bank, actor=self.teacher, data={**self.data, 'status': 'draft'})
        second = save_bank_question(bank=self.bank, actor=self.teacher, data={**self.data, 'status': 'draft'})
        published = save_bank_question(bank=self.bank, actor=self.teacher, data=self.data)
        original = draft.current_version
        response = self.client.get(self.url())
        self.assertContains(response, 'Publicar seleccionadas')
        self.assertContains(response, f'value="{draft.pk}" form="publish-questions-form"')
        self.assertNotContains(response, f'value="{published.pk}" form="publish-questions-form"')
        self.client.post(self.url('knowledge_publish_questions'), {'questions': [draft.pk]})
        draft.refresh_from_db()
        second.refresh_from_db()
        original.refresh_from_db()
        self.assertEqual(draft.status, 'published')
        self.assertEqual(second.status, 'draft')
        self.assertTrue(draft.current_version.is_published)
        self.assertIsNotNone(draft.current_version.published_at)
        self.assertFalse(original.is_published)
        self.assertEqual(draft.versions.count(), 2)

    def test_bulk_publication_rejects_invalid_selection_and_rolls_back(self):
        draft = save_bank_question(bank=self.bank, actor=self.teacher, data={**self.data, 'status': 'draft'})
        published = save_bank_question(bank=self.bank, actor=self.teacher, data=self.data)
        for ids in ([], ['invalid'], [999999], [draft.pk, published.pk]):
            response = self.client.post(self.url('knowledge_publish_questions'), {'questions': ids}, follow=True)
            self.assertContains(response, 'Ninguna pregunta se ha publicado')
            draft.refresh_from_db()
            self.assertEqual(draft.status, 'draft')
        second = save_bank_question(bank=self.bank, actor=self.teacher, data={**self.data, 'status': 'draft'})
        real_save = save_bank_question
        def fail_second(**kwargs):
            if kwargs['question'].pk == second.pk:
                raise ValidationError('Publicación fallida')
            return real_save(**kwargs)
        with patch('apps.lms.knowledge.save_bank_question', side_effect=fail_second):
            self.client.post(self.url('knowledge_publish_questions'), {'questions': [draft.pk, second.pk]})
        draft.refresh_from_db()
        self.assertEqual(draft.status, 'draft')
        self.assertEqual(draft.versions.count(), 1)

    def test_bulk_publication_permissions_csrf_and_archived_bank(self):
        draft = save_bank_question(bank=self.bank, actor=self.teacher, data={**self.data, 'status': 'draft'})
        target = self.url('knowledge_publish_questions')
        for user in (self.student, self.outsider):
            self.client.force_login(user)
            self.assertEqual(self.client.post(target, {'questions': [draft.pk]}).status_code, 403)
        self.client.force_login(self.teacher)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.teacher)
        self.assertEqual(csrf.post(target, {'questions': [draft.pk]}).status_code, 403)
        self.assertEqual(self.client.get(target).status_code, 405)
        self.bank.status = 'archived'
        self.bank.save()
        self.assertNotContains(self.client.get(self.url()), 'Publicar seleccionadas')
        self.client.post(target, {'questions': [draft.pk]})
        draft.refresh_from_db()
        self.assertEqual(draft.status, 'draft')

    def setUp(self):
        User = get_user_model()
        self.teacher = User.objects.create_user('knowledge_teacher')
        self.student = User.objects.create_user('knowledge_student')
        self.outsider = User.objects.create_user('knowledge_outsider')
        self.course = Curso.objects.create(nombre='Banco HTML', fecha_inicio=date(2025, 1, 1))
        self.other = Curso.objects.create(nombre='Otro banco', fecha_inicio=date(2025, 1, 1))
        CourseMembership.objects.create(course=self.course, user=self.teacher, role='teacher', status='active')
        CourseMembership.objects.create(course=self.course, user=self.student, role='student', status='active')
        self.bank = QuestionBank.objects.create(course=self.course, title='HTML', owner=self.teacher)
        self.client.force_login(self.teacher)
        self.data = {
            'prompt': '¿Qué etiqueta engloba TODO? <html>', 'option_a': '<body>',
            'option_b': '<head>', 'option_c': '<html>', 'option_d': '<main>',
            'correct': 'C', 'explanation': 'Es la raíz', 'difficulty': 1, 'points': 2, 'status': 'published',
        }

    def url(self, name='knowledge_bank', **kwargs):
        return reverse('lms:' + name, kwargs={'course_id': self.course.pk, 'bank_id': self.bank.pk, **kwargs})

    def upload(self, text):
        return SimpleUploadedFile('preguntas.csv', text.encode('utf-8'), content_type='text/csv')

    def csv_text(self):
        output = io.StringIO()
        writer = csv.writer(output, delimiter=';')
        writer.writerow(CSV_COLUMNS)
        writer.writerow([self.data['prompt'], *(self.data['option_' + letter.lower()] for letter in 'ABCD'), 'C'])
        return output.getvalue()

    def test_manual_question_versions_preserve_historical_quiz_and_html(self):
        self.assertRedirects(self.client.post(self.url('knowledge_question_create'), self.data), self.url())
        question = self.bank.questions.get()
        original = question.current_version
        self.assertEqual(original.options, ['<body>', '<head>', '<html>', '<main>'])
        self.assertEqual(original.correct_answers, ['<html>'])
        self.assertContains(self.client.get(self.url()), '&lt;html&gt;')
        quiz = Quiz.objects.create(course=self.course, title='Histórico')
        quiz_question = QuizQuestion.objects.create(quiz=quiz, question_version=original, points=2)
        data = {**self.data, 'correct': 'A', 'prompt': 'Nuevo enunciado'}
        self.client.post(self.url('knowledge_question_edit', question_id=question.pk), data)
        question.refresh_from_db()
        original.refresh_from_db()
        quiz_question.refresh_from_db()
        self.assertEqual(question.versions.count(), 2)
        self.assertEqual(question.current_version.version_number, 2)
        self.assertEqual(original.correct_answers, ['<html>'])
        self.assertEqual(quiz_question.question_version_id, original.pk)

    def test_form_rejects_missing_duplicate_and_invalid_answer(self):
        for change in (
            {'option_d': ''}, {'option_d': '<body>'}, {'option_d': '<BODY>'},
            {'correct': 'E'}, {'points': 0},
        ):
            form = BankQuestionForm({**self.data, **change})
            self.assertFalse(form.is_valid())
            self.assertEqual(self.client.post(self.url('knowledge_question_create'), {**self.data, **change}).status_code, 200)
        self.assertFalse(self.bank.questions.exists())

    def test_csv_preview_confirmation_replay_export(self):
        response = self.client.post(self.url('knowledge_import'), {'file': self.upload(self.csv_text())})
        self.assertContains(response, 'Confirmar importación')
        self.assertContains(response, '&lt;body&gt;')
        self.assertFalse(self.bank.questions.exists())
        self.assertRedirects(self.client.post(self.url('knowledge_import'), {'action': 'confirm'}), self.url())
        question = self.bank.questions.get()
        self.assertEqual(question.status, 'draft')
        self.assertEqual(question.current_version.created_by, self.teacher)
        self.client.post(self.url('knowledge_import'), {'action': 'confirm'})
        self.assertEqual(self.bank.questions.count(), 1)
        export = self.client.get(self.url('knowledge_export'))
        rows = list(csv.DictReader(io.StringIO(export.content.decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(rows[0]['OpcionA'], '<body>')
        self.assertEqual(rows[0]['RespuestaCorrecta'], 'C')

    def test_supplied_csv_has_94_valid_questions(self):
        path = Path(settings.BASE_DIR) / 'static' / 'quiz' / '0201QZ.csv'
        rows = parse_question_csv(SimpleUploadedFile('0201QZ.csv', path.read_bytes()))
        self.assertEqual(len(rows), 94)
        self.assertTrue(any('<html>' in row['option_c'] for row in rows))
        self.client.post(self.url('knowledge_import'), {
            'file': SimpleUploadedFile('0201QZ.csv', path.read_bytes()),
        })
        self.assertRedirects(self.client.post(self.url('knowledge_import'), {'action': 'confirm'}), self.url())
        self.assertEqual(self.bank.questions.count(), 94)
        self.assertEqual(self.bank.questions.filter(status='draft').count(), 94)

    def test_csv_invalid_rows_are_saved_for_review_and_upload_errors_are_explicit(self):
        invalid = self.csv_text() + 'Pregunta;A;B;C;D;E\n'
        response = self.client.post(self.url('knowledge_import'), {'file': self.upload(invalid)})
        self.assertContains(response, 'Fila 3')
        self.assertContains(response, 'Revisar')
        self.assertEqual(len(self.client.session['knowledge_preview']['rows']), 2)
        self.assertFalse(self.bank.questions.exists())
        response = self.client.post(self.url('knowledge_import'), {'action': 'confirm'}, follow=True)
        self.assertContains(response, 'se han marcado Revisar')
        self.assertEqual(self.bank.questions.count(), 2)
        flagged = self.bank.questions.get(title='Pregunta')
        self.assertEqual(flagged.status, 'draft')
        self.assertFalse(flagged.current_version.is_published)
        self.assertEqual(flagged.current_version.correct_answers, [])
        self.assertEqual(flagged.current_version.metadata['csv_review']['row'], 3)
        self.assertEqual(flagged.current_version.metadata['csv_review']['data']['correct'], 'E')
        before = self.bank.questions.count()
        for upload in (
            self.upload('Sin;cabecera\n'),
            SimpleUploadedFile('file.csv', b'\xff'),
            SimpleUploadedFile('file.txt', b'not csv'),
            SimpleUploadedFile('large.csv', b'x' * (1024 * 1024 + 1)),
        ):
            response = self.client.post(self.url('knowledge_import'), {'file': upload})
            self.assertTrue(response.context['form'].errors)
        self.client.post(self.url('knowledge_import'), {'file': self.upload(self.csv_text())})
        with patch('apps.lms.knowledge_views.save_bank_question', side_effect=ValidationError('Error de importación')):
            response = self.client.post(self.url('knowledge_import'), {'action': 'confirm'})
        self.assertContains(response, 'Error de importación')
        self.assertEqual(self.bank.questions.count(), before)

    def test_permissions_scope_csrf_and_bank_course_owner_assignment(self):
        question = save_bank_question(bank=self.bank, actor=self.teacher, data=self.data)
        for user in (self.student, self.outsider):
            self.client.force_login(user)
            for name in ('knowledge_bank', 'knowledge_import', 'knowledge_export', 'knowledge_edit'):
                self.assertEqual(self.client.get(self.url(name)).status_code, 403)
            self.assertEqual(self.client.post(self.url('knowledge_question_create'), self.data).status_code, 403)
        self.client.force_login(self.teacher)
        foreign = QuestionBank.objects.create(course=self.other, title='Ajeno')
        self.assertEqual(self.client.get(self.url(bank_id=foreign.pk)).status_code, 404)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.teacher)
        self.assertEqual(csrf.post(self.url('knowledge_question_action', question_id=question.pk), {'action': 'archive'}).status_code, 403)
        create = reverse('lms:knowledge_create', kwargs={'course_id': self.course.pk})
        self.client.post(create, {'title': 'Nuevo', 'status': 'draft', 'course': self.other.pk, 'owner': self.student.pk})
        bank = QuestionBank.objects.get(title='Nuevo')
        self.assertEqual(bank.course, self.course)
        self.assertEqual(bank.owner, self.teacher)

    def test_duplicate_archive_filters_and_archived_bank(self):
        question = save_bank_question(bank=self.bank, actor=self.teacher, data=self.data)
        action_url = self.url('knowledge_question_action', question_id=question.pk)
        self.client.post(action_url, {'action': 'duplicate'})
        self.assertEqual(self.bank.questions.count(), 2)
        self.assertEqual(self.bank.questions.exclude(pk=question.pk).get().status, 'draft')
        self.client.post(action_url, {'action': 'archive'})
        question.refresh_from_db()
        self.assertEqual(question.status, 'archived')
        self.assertEqual(self.client.get(self.url(), {'status': 'archived'}).context['questions'].count(), 1)
        self.assertEqual(self.client.get(self.url(), {'q': 'inexistente'}).context['questions'].count(), 0)
        self.bank.status = 'archived'
        self.bank.save()
        response = self.client.post(self.url('knowledge_question_create'), self.data)
        self.assertContains(response, 'banco archivado')
        self.assertEqual(self.bank.questions.count(), 2)

    def test_create_bank_with_csv_preview_confirm_and_cancel(self):
        create = reverse('lms:knowledge_create', kwargs={'course_id': self.course.pk})
        self.assertContains(self.client.get(create), 'Preguntas y respuestas (CSV)')
        self.assertContains(self.client.get(create), 'enctype="multipart/form-data"')
        path = Path(settings.BASE_DIR) / 'static' / 'quiz' / '0201QZ.csv'
        response = self.client.post(create, {
            'title': 'Banco con CSV', 'status': 'draft',
            'file': SimpleUploadedFile('0201QZ.csv', path.read_bytes()),
            'course': self.other.pk, 'owner': self.student.pk,
        })
        bank = QuestionBank.objects.get(title='Banco con CSV')
        target = reverse('lms:knowledge_import', kwargs={'course_id': self.course.pk, 'bank_id': bank.pk})
        self.assertRedirects(response, target)
        preview = self.client.get(target)
        self.assertContains(preview, '94 preguntas revisadas')
        self.assertContains(preview, 'Confirmar importación')
        self.assertFalse(bank.questions.exists())
        self.assertEqual(bank.owner, self.teacher)
        self.assertEqual(bank.course, self.course)
        self.client.post(target, {'action': 'confirm'})
        self.assertEqual(bank.questions.count(), 94)
        self.assertEqual(bank.questions.filter(status='draft').count(), 94)
        self.client.post(create, {'title': 'Banco cancelado', 'status': 'draft', 'file': self.upload(self.csv_text())})
        cancelled = QuestionBank.objects.get(title='Banco cancelado')
        target = reverse('lms:knowledge_import', kwargs={'course_id': self.course.pk, 'bank_id': cancelled.pk})
        self.client.post(target, {'action': 'cancel'})
        self.assertNotIn('knowledge_preview', self.client.session)
        self.client.post(target, {'action': 'confirm'})
        self.assertFalse(cancelled.questions.exists())

    def test_unreadable_csv_creates_bank_with_persistent_errors_and_no_stale_preview(self):
        create = reverse('lms:knowledge_create', kwargs={'course_id': self.course.pk})
        for number, file in enumerate((
            self.upload('Sin;cabecera\n'),
            SimpleUploadedFile('bad.csv', b'\xff'),
            self.upload('Pregunta;OpcionA;OpcionB;OpcionC;OpcionD;RespuestaCorrecta\n"Sin cerrar'),
            SimpleUploadedFile('not-csv.txt', b'text'),
            SimpleUploadedFile('large.csv', b'x' * (1024 * 1024 + 1)),
        )):
            response = self.client.post(create, {'title': f'Con errores {number}', 'status': 'draft', 'file': file}, follow=True)
            bank = QuestionBank.objects.get(title=f'Con errores {number}')
            self.assertContains(response, 'El banco se ha guardado')
            self.assertContains(response, 'Revisar CSV')
            self.assertTrue(bank.import_errors)
            self.assertFalse(bank.questions.exists())
            self.assertNotIn('knowledge_preview', self.client.session)
            self.assertContains(self.client.get(self.url(bank_id=bank.pk)), bank.import_errors[0])
        response = self.client.post(create, {'title': 'No crear', 'status': 'archived', 'file': self.upload(self.csv_text())})
        self.assertTrue(response.context['form'].errors)
        self.assertFalse(QuestionBank.objects.filter(title='No crear').exists())
        self.client.post(self.url('knowledge_import'), {'file': self.upload(self.csv_text())})
        self.client.post(self.url('knowledge_import'), {'file': SimpleUploadedFile('bad.txt', b'bad')})
        self.assertNotIn('knowledge_preview', self.client.session)
        self.client.post(self.url('knowledge_import'), {'action': 'confirm'})
        self.assertFalse(self.bank.questions.exists())

    def test_create_bank_with_invalid_rows_then_correct_and_publish(self):
        create = reverse('lms:knowledge_create', kwargs={'course_id': self.course.pk})
        response = self.client.post(create, {
            'title': 'Revisar respuestas', 'status': 'published',
            'file': self.upload(self.csv_text() + 'Capitalización;True;true;TRUE;False;A\n'),
        })
        bank = QuestionBank.objects.get(title='Revisar respuestas')
        target = self.url('knowledge_import', bank_id=bank.pk)
        self.assertRedirects(response, target)
        self.assertContains(self.client.get(target), 'Fila 3')
        self.client.post(target, {'action': 'confirm'})
        flagged = bank.questions.get(title='Capitalización')
        valid = bank.questions.exclude(pk=flagged.pk).get()
        original = flagged.current_version
        detail = self.client.get(self.url(bank_id=bank.pk))
        self.assertContains(detail, 'Revisar CSV')
        self.assertNotContains(detail, f'value="{flagged.pk}" form="publish-questions-form"')
        self.assertContains(self.client.get(self.url('knowledge_question_edit', bank_id=bank.pk, question_id=flagged.pk)), 'True')
        publish = self.url('knowledge_publish_questions', bank_id=bank.pk)
        response = self.client.post(publish, {'questions': [valid.pk, flagged.pk]}, follow=True)
        self.assertContains(response, 'Ninguna pregunta se ha publicado')
        valid.refresh_from_db()
        self.assertEqual(valid.status, 'draft')
        with self.assertRaises(ValidationError):
            save_bank_question(bank=bank, actor=self.teacher, question=flagged, data={
                **original.metadata['csv_review']['data'],
                'csv_review': original.metadata['csv_review'], 'status': 'published',
            })
        edit = self.url('knowledge_question_edit', bank_id=bank.pk, question_id=flagged.pk)
        self.client.post(edit, {**self.data, 'option_d': '<BODY>'})
        flagged.refresh_from_db()
        self.assertEqual(flagged.current_version_id, original.pk)
        self.client.post(edit, {**self.data, 'status': 'draft'})
        flagged.refresh_from_db()
        self.assertNotIn('csv_review', flagged.current_version.metadata)
        self.assertIn('csv_review', original.metadata)
        self.assertEqual(flagged.versions.count(), 2)
        self.assertNotContains(self.client.get(self.url(bank_id=bank.pk)), 'Revisar CSV')
        self.client.post(publish, {'questions': [valid.pk, flagged.pk]})
        self.assertEqual(bank.questions.filter(status='published').count(), 2)

    def test_missing_extra_and_empty_fields_are_preserved_for_review(self):
        text = (
            ';A;B;C;D;A\n'
            'Incompleta;A;B\n'
            'Extra;A;B;C;D;A;dato adicional\n'
            'Vacía;;B;C;D;B\n'
        )
        self.client.post(self.url('knowledge_import'), {'file': self.upload(';'.join(CSV_COLUMNS) + '\n' + text)})
        self.client.post(self.url('knowledge_import'), {'action': 'confirm'})
        self.assertEqual(self.bank.questions.count(), 4)
        for question in self.bank.questions.select_related('current_version'):
            self.assertEqual(question.status, 'draft')
            self.assertTrue(question.current_version.metadata['csv_review']['errors'])
            self.assertEqual(self.client.get(self.url('knowledge_question_edit', question_id=question.pk)).status_code, 200)
        extra = self.bank.questions.get(title='Extra').current_version.metadata['csv_review']
        self.assertEqual(extra['extra_columns'], ['dato adicional'])
        self.assertEqual(extra['raw_row']['Pregunta'], 'Extra')
        self.assertContains(self.client.get(self.url()), 'dato adicional')

    def test_successful_import_clears_file_errors_but_cancel_does_not(self):
        self.client.post(self.url('knowledge_import'), {'file': self.upload('Sin;cabecera\n')})
        self.bank.refresh_from_db()
        self.assertTrue(self.bank.import_errors)
        self.client.post(self.url('knowledge_import'), {'file': self.upload(self.csv_text())})
        self.client.post(self.url('knowledge_import'), {'action': 'cancel'})
        self.bank.refresh_from_db()
        self.assertTrue(self.bank.import_errors)
        self.client.post(self.url('knowledge_import'), {'file': self.upload(self.csv_text())})
        self.client.post(self.url('knowledge_import'), {'action': 'confirm'})
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.import_errors, [])

    def test_delete_bank_confirmation_cascade_audit_and_preview_cleanup(self):
        question = save_bank_question(bank=self.bank, actor=self.teacher, data=self.data)
        version_id = question.current_version_id
        self.client.post(self.url('knowledge_import'), {'file': self.upload(self.csv_text())})
        target = self.url('knowledge_delete')
        self.assertContains(self.client.get(self.url()), 'Eliminar banco')
        response = self.client.get(target)
        self.assertContains(response, 'Confirmar eliminación')
        self.assertContains(response, 'Cancelar')
        self.assertTrue(QuestionBank.objects.filter(pk=self.bank.pk).exists())
        self.client.post(target, {})
        self.assertTrue(QuestionBank.objects.filter(pk=self.bank.pk).exists())
        area = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'item-bank'})
        self.assertContains(self.client.get(area), target)
        self.assertRedirects(self.client.post(target, {'confirm': 'delete'}), area)
        self.assertFalse(QuestionBank.objects.filter(pk=self.bank.pk).exists())
        self.assertFalse(Question.objects.filter(pk=question.pk).exists())
        self.assertFalse(QuestionVersion.objects.filter(pk=version_id).exists())
        self.assertNotIn('knowledge_preview', self.client.session)
        event = AuditEvent.objects.get(metadata__knowledge_action='bank_deleted')
        self.assertEqual(event.actor, self.teacher)
        self.assertEqual(event.metadata['bank_id'], self.bank.pk)
        self.assertEqual(event.metadata['question_count'], 1)

    def test_used_bank_cannot_be_deleted_even_when_only_old_version_is_used(self):
        question = save_bank_question(bank=self.bank, actor=self.teacher, data=self.data)
        original = question.current_version
        quiz = Quiz.objects.create(course=self.course, title='Prueba con historial')
        item = QuizQuestion.objects.create(quiz=quiz, question_version=original, points=2)
        save_bank_question(bank=self.bank, actor=self.teacher, data=self.data, question=question)
        unused = save_bank_question(bank=self.bank, actor=self.teacher, data=self.data)
        response = self.client.post(self.url('knowledge_delete'), {'confirm': 'delete'}, follow=True)
        self.assertContains(response, 'No se puede eliminar este banco')
        self.assertTrue(QuestionBank.objects.filter(pk=self.bank.pk).exists())
        self.assertEqual(self.bank.questions.count(), 2)
        self.assertTrue(Question.objects.filter(pk=unused.pk).exists())
        self.assertEqual(question.versions.count(), 2)
        item.refresh_from_db()
        self.assertEqual(item.question_version_id, original.pk)
        self.assertFalse(AuditEvent.objects.filter(metadata__knowledge_action='bank_deleted').exists())

    def test_bank_delete_permissions_scope_csrf_and_empty_bank(self):
        target = self.url('knowledge_delete')
        for user in (self.student, self.outsider):
            self.client.force_login(user)
            self.assertEqual(self.client.get(target).status_code, 403)
            self.assertEqual(self.client.post(target, {'confirm': 'delete'}).status_code, 403)
        self.client.force_login(self.teacher)
        foreign = QuestionBank.objects.create(course=self.other, title='No eliminar')
        self.assertEqual(self.client.post(self.url('knowledge_delete', bank_id=foreign.pk), {'confirm': 'delete'}).status_code, 404)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.teacher)
        self.assertEqual(csrf.post(target, {'confirm': 'delete'}).status_code, 403)
        self.assertTrue(QuestionBank.objects.filter(pk=self.bank.pk).exists())
        self.client.logout()
        self.assertEqual(self.client.post(target, {'confirm': 'delete'}).status_code, 302)
        self.client.force_login(self.teacher)
        self.client.post(target, {'confirm': 'delete'})
        self.assertFalse(QuestionBank.objects.filter(pk=self.bank.pk).exists())
