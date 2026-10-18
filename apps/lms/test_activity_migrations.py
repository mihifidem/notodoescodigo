from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from apps.courses.models import Tema
from .tests import make_course


class ActivityModuleMigrationTests(TransactionTestCase):
    def test_existing_activities_keep_their_course_module_and_can_be_reversed(self):
        old_target = [('lms', '0029_quiz_reference_code')]
        new_target = [('lms', '0030_learningactivity_course_modules')]
        executor = MigrationExecutor(connection)
        executor.migrate(old_target)
        try:
            old_apps = executor.loader.project_state(old_target).apps
            CourseModule = old_apps.get_model('lms', 'CourseModule')
            Activity = old_apps.get_model('lms', 'LearningActivity')
            course = make_course('Migración de actividades')
            shared_topic = Tema.objects.create(curso=course, titulo='Compartido', contenido='Existente')
            shared_module = CourseModule.objects.create(
                course_id=course.pk, title='Compartido', slug='compartido',
            )
            legacy_module = CourseModule.objects.create(
                course_id=course.pk, title='Solo actividades', slug='solo-actividades',
                summary='Resumen anterior', position=4,
            )
            shared = Activity.objects.create(course_id=course.pk, module=shared_module, title='Compartida')
            legacy = Activity.objects.create(course_id=course.pk, module=legacy_module, title='Anterior')
            no_module = Activity.objects.create(course_id=course.pk, title='Sin módulo')
            executor = MigrationExecutor(connection)
            executor.migrate(new_target)
            new_apps = executor.loader.project_state(new_target).apps
            Activity = new_apps.get_model('lms', 'LearningActivity')
            self.assertEqual(Activity.objects.get(pk=shared.pk).module_id, shared_topic.pk)
            new_topic = Activity.objects.get(pk=legacy.pk).module
            self.assertEqual(new_topic.curso_id, course.pk)
            self.assertEqual(new_topic.titulo, legacy_module.title)
            self.assertEqual(new_topic.contenido, legacy_module.summary)
            self.assertEqual(new_topic.orden, 4)
            self.assertIsNone(Activity.objects.get(pk=no_module.pk).module_id)
            self.assertEqual(Activity.objects.count(), 3)
            executor = MigrationExecutor(connection)
            executor.migrate(old_target)
            old_apps = executor.loader.project_state(old_target).apps
            Activity = old_apps.get_model('lms', 'LearningActivity')
            self.assertEqual(Activity.objects.get(pk=shared.pk).module_id, shared_module.pk)
            self.assertEqual(Activity.objects.get(pk=legacy.pk).module_id, legacy_module.pk)
            self.assertIsNone(Activity.objects.get(pk=no_module.pk).module_id)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
