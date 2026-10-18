from datetime import date
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.storage import FileSystemStorage
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.courses.models import Curso, Progreso, Tema
from .badge_awards import award_module_badges
from .models import (
    Assignment, Badge, CourseMembership, LearningActivity, ModuleContent,
    ModuleContentProgress, Page, Quiz, QuizAttempt, Submission, UserBadge,
)


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class ModuleBadgeTests(TestCase):
    def setUp(self):
        user = get_user_model()
        self.teacher = user.objects.create_user('badge_teacher')
        self.student = user.objects.create_user('badge_student')
        self.other = user.objects.create_user('badge_other')
        self.course = Curso.objects.create(
            nombre='Insignias', descripcion='Curso', fecha_inicio=date(2026, 10, 1),
            fecha_fin=date(2026, 12, 1), status='published', activo=True,
        )
        for person, role in [(self.teacher, CourseMembership.Role.OWNER), (self.student, CourseMembership.Role.STUDENT)]:
            CourseMembership.objects.create(course=self.course, user=person, role=role, status='active')
        self.module = Tema.objects.create(curso=self.course, titulo='Tema uno')
        self.url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'competencies'})
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.storage_patch = patch.object(Badge._meta.get_field('image'), 'storage', FileSystemStorage(location=self.folder.name))
        self.storage_patch.start()
        self.addCleanup(self.storage_patch.stop)

    def image(self):
        buffer = BytesIO()
        Image.new('RGB', (16, 16), 'green').save(buffer, format='PNG')
        return SimpleUploadedFile('insignia.png', buffer.getvalue(), content_type='image/png')

    def create_badge(self, **kwargs):
        return Badge.objects.create(course=self.course, module=self.module, name='Primer tema', image=self.image(), **kwargs)

    def test_teacher_creates_edits_and_deactivates_badge_students_read_only(self):
        self.client.force_login(self.teacher)
        response = self.client.post(self.url, {
            'action': 'badge', 'name': 'Primer tema', 'module': self.module.pk,
            'image': self.image(), 'is_active': 'on',
        })
        self.assertEqual(response.status_code, 302)
        badge = Badge.objects.get(course=self.course)
        self.assertEqual(badge.module, self.module)
        self.assertTrue(badge.image.storage.exists(badge.image.name))
        self.assertContains(self.client.get(self.url), 'Editar insignia')
        self.assertEqual(self.client.post(self.url + f'?edit={badge.pk}', {
            'action': 'badge', 'name': 'Tema actualizado', 'module': self.module.pk, 'is_active': 'on',
        }).status_code, 302)
        badge.refresh_from_db()
        self.assertEqual(badge.name, 'Tema actualizado')
        self.client.force_login(self.student)
        self.assertContains(self.client.get(self.url), 'Pendiente de completar el tema')
        self.assertNotContains(self.client.get(self.url), 'Guardar insignia')
        self.assertEqual(self.client.post(self.url, {'action': 'deactivate', 'badge_id': badge.pk}).status_code, 403)
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.post(self.url, {'action': 'deactivate', 'badge_id': badge.pk}).status_code, 302)
        badge.refresh_from_db()
        self.assertFalse(badge.is_active)

    def test_automatic_award_requires_all_required_content_once_and_preserves_revocation(self):
        badge = self.create_badge()
        page = Page.objects.create(course=self.course, module=self.module, title='Lectura', slug='lectura')
        content = ModuleContent.objects.create(module=self.module, page=page)
        ModuleContentProgress.objects.create(content=content, user=self.student, opened_at=timezone.now())
        self.assertFalse(UserBadge.objects.filter(badge=badge).exists())
        progress = ModuleContentProgress.objects.get(content=content, user=self.student)
        progress.completed_at = timezone.now()
        progress.save()
        self.assertEqual(UserBadge.objects.filter(badge=badge, user=self.student).count(), 1)
        progress.save()
        self.assertEqual(UserBadge.objects.filter(badge=badge).count(), 1)
        self.client.force_login(self.student)
        self.assertContains(self.client.get(self.url), 'Insignia obtenida')
        award_module_badges(self.course, self.teacher)
        award_module_badges(self.course, self.other)
        self.assertEqual(UserBadge.objects.filter(badge=badge).count(), 1)
        award = UserBadge.objects.get(badge=badge)
        award.status = 'revoked'
        award.save()
        award_module_badges(self.course, self.student)
        award.refresh_from_db()
        self.assertEqual(award.status, 'revoked')

    def test_legacy_topic_completion_and_retroactive_award(self):
        Progreso.objects.create(usuario=self.student, tema=self.module, completado=True)
        self.client.force_login(self.teacher)
        self.client.post(self.url, {
            'action': 'badge', 'name': 'Retroactiva', 'module': self.module.pk,
            'image': self.image(), 'is_active': 'on',
        })
        badge = Badge.objects.get(name='Retroactiva')
        self.assertTrue(UserBadge.objects.filter(badge=badge, user=self.student).exists())
        second = Tema.objects.create(curso=self.course, titulo='Tema dos')
        other = Badge.objects.create(course=self.course, module=second, name='Segundo', image=self.image())
        Progreso.objects.create(usuario=self.student, tema=second, completado=True)
        self.assertTrue(UserBadge.objects.filter(badge=other, user=self.student).exists())

    def test_rejects_invalid_image_foreign_topic_and_editing_foreign_badge(self):
        self.client.force_login(self.teacher)
        data = {'action': 'badge', 'name': 'Inválida', 'module': self.module.pk,
                'image': SimpleUploadedFile('fake.png', b'no image', content_type='image/png')}
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['badge_form'].errors['image'])
        self.assertFalse(Badge.objects.exists())
        foreign = Curso.objects.create(nombre='Otra aula', fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 12, 1))
        topic = Tema.objects.create(curso=foreign, titulo='Ajeno')
        data.update(module=topic.pk, image=self.image())
        response = self.client.post(self.url, data)
        self.assertTrue(response.context['badge_form'].errors['module'])
        badge = Badge.objects.create(course=foreign, module=topic, name='Ajena')
        self.assertEqual(self.client.get(self.url + f'?edit={badge.pk}').status_code, 404)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_awarded_badge_cannot_move_and_duplicate_names_have_unique_slugs(self):
        badge = self.create_badge()
        duplicate = self.create_badge()
        self.assertNotEqual(badge.slug, duplicate.slug)
        UserBadge.objects.create(badge=badge, user=self.student, course=self.course)
        second = Tema.objects.create(curso=self.course, titulo='Tema dos')
        self.client.force_login(self.teacher)
        response = self.client.post(self.url + f'?edit={badge.pk}', {
            'action': 'badge', 'name': badge.name, 'module': second.pk, 'is_active': 'on',
        })
        self.assertTrue(response.context['badge_form'].errors['module'])
        badge.refresh_from_db()
        self.assertEqual(badge.module, self.module)

    def test_quiz_and_activity_signals_award_only_after_all_requirements(self):
        badge = self.create_badge()
        quiz = Quiz.objects.create(course=self.course, title='Prueba', status='published')
        activity = LearningActivity.objects.create(course=self.course, module=self.module, title='Entrega', status='open')
        ModuleContent.objects.create(module=self.module, quiz=quiz, criterion='finished')
        ModuleContent.objects.create(module=self.module, activity=activity, criterion='submitted')
        QuizAttempt.objects.create(quiz=quiz, user=self.student, status='graded', is_passed=False)
        self.assertFalse(UserBadge.objects.filter(badge=badge).exists())
        assignment = Assignment.objects.create(course=self.course, title='Práctica', learning_activity=activity)
        Submission.objects.create(
            assignment=assignment, user=self.student, submitted_at=timezone.now(),
            status='submitted', receipt_identifier='badge-receipt',
        )
        self.assertTrue(UserBadge.objects.filter(badge=badge, user=self.student).exists())

    def test_inactive_badge_does_not_award_and_awards_survive_deactivation(self):
        badge = self.create_badge(is_active=False)
        Progreso.objects.create(usuario=self.student, tema=self.module, completado=True)
        self.assertFalse(UserBadge.objects.filter(badge=badge).exists())
        badge.is_active = True
        badge.save()
        award_module_badges(self.course, self.student)
        self.assertTrue(UserBadge.objects.filter(badge=badge).exists())
        badge.is_active = False
        badge.save()
        self.client.force_login(self.student)
        self.assertContains(self.client.get(self.url), 'Insignia obtenida')
