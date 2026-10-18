from datetime import date
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import RestrictedError
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from . import test_module_progression as progression_tests
from .forms import FormativeUnitForm, ModuleContentForm
from .models import AuditEvent, FileAsset, FormativeUnit, LearningActivity, ModuleContent, Page, Quiz
from .module_progression import module_rows


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class FormativeUnitTests(TestCase):
    setUp = progression_tests.ModuleProgressionTests.setUp
    complete = progression_tests.ModuleProgressionTests.complete

    def url(self, route, unit=None, module=None):
        kwargs = {'course_id': self.course.pk, 'module_id': (module or self.first).pk}
        if unit:
            kwargs['unit_id'] = unit.pk
        return reverse(f'lms:{route}', kwargs=kwargs)

    def test_default_unit_and_model_integrity(self):
        self.assertEqual(self.item.unit.title, 'General')
        foreign = FormativeUnit.objects.create(module=self.second, title='Fuera')
        self.item.unit = foreign
        with self.assertRaises(ValidationError):
            self.item.full_clean()
        with self.assertRaises(ValidationError):
            self.item.save()
        self.assertFalse(FormativeUnitForm({'title': 'General', 'position': 0}, module=self.first).is_valid())

    def test_teacher_crud_audit_permissions_and_csrf(self):
        create = self.url('formative_unit_create')
        self.assertEqual(self.client.get(create).status_code, 403)
        self.assertEqual(self.client.post(create, {'title': 'No permitido', 'position': 1}).status_code, 403)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(create).status_code, 403)
        self.client.force_login(self.teacher)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.teacher)
        self.assertEqual(strict.post(create, {'title': 'Unidad', 'position': 1}).status_code, 403)
        self.assertEqual(self.client.post(create, {
            'title': 'Fundamentos', 'description': 'Primera unidad', 'position': 2,
        }).status_code, 302)
        unit = FormativeUnit.objects.get(module=self.first, title='Fundamentos')
        self.assertTrue(AuditEvent.objects.filter(course=self.course, actor=self.teacher).exists())
        edit = self.url('formative_unit_edit', unit)
        self.assertEqual(self.client.post(edit, {'title': 'Conceptos', 'position': 1}).status_code, 302)
        unit.refresh_from_db()
        self.assertEqual(unit.title, 'Conceptos')
        self.assertEqual(self.client.get(self.url('formative_unit_edit', unit, self.second)).status_code, 404)
        self.assertEqual(self.client.post(edit, {'action': 'delete'}).status_code, 302)
        self.assertFalse(FormativeUnit.objects.filter(pk=unit.pk).exists())

    def test_resource_batch_all_four_types_and_unit_prefill(self):
        self.client.force_login(self.teacher)
        unit = FormativeUnit.objects.create(module=self.first, title='Mixta', position=1)
        page = Page.objects.create(course=self.course, title='Lectura', slug='lectura', content='Texto')
        asset = FileAsset.objects.create(course=self.course, title='Archivo', file='units/file.txt', size=1)
        quiz = Quiz.objects.create(course=self.course, title='Test')
        activity = LearningActivity.objects.create(course=self.course, title='Trabajo')
        url = self.url('formative_unit_contents', unit)
        response = self.client.get(url + '?panel=content', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.context['content_form'].initial['unit'], unit.pk)
        response = self.client.post(url, {
            'action': 'content', 'page': [page.pk], 'file': [asset.pk], 'quiz': [quiz.pk],
            'activity': [activity.pk], 'criterion': 'manual', 'position': 1, 'required': 'on',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(unit.contents.count(), 4)
        self.assertEqual(list(unit.contents.values_list('position', flat=True)), [1, 2, 3, 4])
        self.assertEqual({content.kind for content in unit.contents.all()}, {'page', 'file', 'quiz', 'activity'})
        foreign = FormativeUnit.objects.create(module=self.second, title='Otro módulo')
        form = ModuleContentForm({
            'unit': foreign.pk, 'page': self.page.pk, 'criterion': 'manual',
        }, module=self.first, instance=self.item)
        self.assertFalse(form.is_valid())
        self.assertIn('unit', form.errors)

    def test_move_preserves_progress_prerequisites_and_protected_deletion(self):
        self.complete(self.item)
        unit = FormativeUnit.objects.create(module=self.first, title='Destino')
        self.client.force_login(self.teacher)
        response = self.client.post(self.url('module_contents') + f'?edit={self.item.pk}', {
            'action': 'content', 'unit': unit.pk, 'page': self.page.pk, 'position': 4,
            'criterion': 'manual', 'required': 'on',
        })
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.unit_id, unit.pk)
        self.assertTrue(self.item.progress.filter(user=self.student, completed_at__isnull=False).exists())
        with self.assertRaises(RestrictedError):
            unit.delete()
        self.assertEqual(self.client.post(self.url('formative_unit_edit', unit), {'action': 'delete'}).status_code, 302)
        self.assertTrue(FormativeUnit.objects.filter(pk=unit.pk).exists())
        unit.module = self.second
        with self.assertRaises(ValidationError):
            unit.full_clean()

    def test_module_deletion_keeps_original_page_and_unit_rejects_cross_module_removal(self):
        self.client.force_login(self.teacher)
        other_unit = FormativeUnit.objects.create(module=self.first, title='Otra')
        url = self.url('formative_unit_contents', other_unit)
        response = self.client.post(url, {'action': 'remove', 'content_id': self.item.pk})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(ModuleContent.objects.filter(pk=self.item.pk).exists())
        self.page.module = self.second
        with self.assertRaises(ValidationError):
            self.page.full_clean()
        self.first.delete()
        self.page.refresh_from_db()
        self.assertIsNone(self.page.module_id)
        self.assertFalse(ModuleContent.objects.filter(pk=self.item.pk).exists())
        self.assertFalse(FormativeUnit.objects.filter(pk=other_unit.pk).exists())

    def test_grouping_order_hidden_resources_and_ajax_form_errors(self):
        unit = FormativeUnit.objects.create(module=self.first, title='Vacía', position=2)
        draft = Page.objects.create(course=self.course, module=self.first, title='Secreta', slug='secreta')
        ModuleContent.objects.create(module=self.first, unit=unit, page=draft)
        teacher_rows = module_rows(self.course, self.teacher)
        student_rows = module_rows(self.course, self.student)
        self.assertEqual([row['unit'].title for row in teacher_rows[0]['units']], ['General', 'Vacía'])
        self.assertEqual([row['unit'].title for row in student_rows[0]['units']], ['General'])
        content = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'content'})
        self.assertContains(self.client.get(content), 'General')
        self.assertNotContains(self.client.get(content), 'Secreta')
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(content), 'Añadir unidad formativa')
        response = self.client.post(self.url('formative_unit_create'), {'title': 'General', 'position': 0},
                                    HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'errorlist', status_code=400)
        response = self.client.post(self.url('formative_unit_create'), {'title': 'Ajax', 'position': 5},
                                    HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.json(), {'ok': True})


class FormativeUnitMigrationTests(TransactionTestCase):
    def test_existing_content_and_progress_preserved_through_migration_and_reverse(self):
        before = [('lms', '0034_badge_image_badge_module')]
        after = [('lms', '0035_formative_units')]
        executor = MigrationExecutor(connection)
        try:
            executor.migrate(before)
            old = executor.loader.project_state(before).apps
            course = old.get_model('courses', 'Curso').objects.create(
                nombre='Migración unidades', descripcion='Curso', fecha_inicio=date(2026, 1, 1),
            )
            module = old.get_model('courses', 'Tema').objects.create(curso_id=course.pk, titulo='Módulo', contenido='')
            page = old.get_model('lms', 'Page').objects.create(course_id=course.pk, title='Original', slug='original')
            content = old.get_model('lms', 'ModuleContent').objects.create(module_id=module.pk, page_id=page.pk)
            user = get_user_model().objects.create_user(username='unit_migration')
            progress = old.get_model('lms', 'ModuleContentProgress').objects.create(content_id=content.pk, user_id=user.pk)
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            new = executor.loader.project_state(after).apps
            updated = new.get_model('lms', 'ModuleContent').objects.get(pk=content.pk)
            self.assertEqual(updated.unit.title, 'General')
            self.assertEqual(updated.page_id, page.pk)
            self.assertEqual(new.get_model('lms', 'ModuleContentProgress').objects.get(pk=progress.pk).content_id, content.pk)
            executor = MigrationExecutor(connection)
            executor.migrate(before)
            flat = executor.loader.project_state(before).apps
            self.assertTrue(flat.get_model('lms', 'ModuleContent').objects.filter(pk=content.pk).exists())
            self.assertTrue(flat.get_model('lms', 'ModuleContentProgress').objects.filter(pk=progress.pk).exists())
        finally:
            MigrationExecutor(connection).migrate(after)
