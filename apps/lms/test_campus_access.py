from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.courses.models import Curso, InscripcionCurso, Tema
from apps.users.models import AccessMembership, PurchasedEntitlement
from .campus import course_right, freeze_contract, material_access
from .models import CourseMembership, CourseSettingsLMS, ModuleContent, Page
from .services import EnrollmentUnavailable, enroll_student


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class CampusFixture(TestCase):
    def setUp(self):
        User = get_user_model()
        self.student = User.objects.create_user('campus_student')
        self.other = User.objects.create_user('campus_other')
        self.teacher = User.objects.create_user('campus_teacher')
        self.course = Curso.objects.create(
            nombre='Campus fixture', descripcion='Fixture aislada', resumen='Presentación pública',
            fecha_inicio=date(2025, 1, 1), status='published', activo=True,
        )
        self.module = Tema.objects.create(curso=self.course, titulo='Unidad fixture', contenido='LEGACY_SECRET')
        self.page = Page.objects.create(
            course=self.course, module=self.module, title='Lección fixture', slug='leccion-fixture',
            content='PRIVATE_LESSON_BODY', status='published', author=self.teacher,
        )
        self.item = ModuleContent.objects.create(module=self.module, page=self.page, criterion='manual')
        self.settings = CourseSettingsLMS.objects.create(
            course=self.course, campus_enabled=True, campus_version='fixture-v1', campus_free=True,
        )
        CourseMembership.objects.create(course=self.course, user=self.teacher, role='teacher')
        self.url = reverse('lms:campus_course', args=[self.course.pk])
        self.page_url = reverse('lms:course_page_detail', args=[self.course.pk, self.page.slug])

    def enroll(self):
        return enroll_student(actor=self.student, course=self.course)

    def complete_lesson(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(self.page_url).status_code, 200)
        response = self.client.post(reverse(
            'lms:module_content_complete', args=[self.course.pk, self.item.pk],
        ))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.url)

    def entitlement(self):
        return PurchasedEntitlement.objects.create(
            user=self.student, code='campus-fixture-course', source='isolated-test',
            source_reference='campus-fixture', idempotency_key='campus-fixture',
        )


class ClassroomsDesignTests(CampusFixture):
    def test_login_required(self):
        self.assertEqual(self.client.get(reverse('lms:dashboard')).status_code, 302)

    def test_design_empty_state_and_sidebar(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse('lms:dashboard'))
        for text in (
            'campus-tech.css', 'billing-hero', 'classrooms-tech',
            'id="classrooms-heading"', 'href="#my-courses"',
            'href="#discover-courses"', 'Todavía no tienes aulas',
            'Tu siguiente aprendizaje empieza aquí',
        ):
            self.assertContains(response, text)
        self.assertNotContains(response, reverse('lms:course_create'))
        self.assertNotContains(response, 'Mi Perfil')
        self.assertNotContains(response, 'Cursos NTEC')
        self.assertContains(response, 'Mi cuenta')
        self.assertContains(response, 'Formación')
        self.assertEqual(response.context['course_count'], 0)

    def test_memberships_legacy_courses_and_private_data(self):
        self.enroll()
        legacy = Curso.objects.create(
            nombre='Aula anterior propia', descripcion='<p>Contenido anterior</p>',
            fecha_inicio=date(2025, 1, 1), activo=True,
        )
        InscripcionCurso.objects.create(usuario=self.student, curso=legacy)
        private_course = Curso.objects.create(
            nombre='Aula privada ajena', descripcion='No mostrar',
            fecha_inicio=date(2025, 1, 1), activo=False,
        )
        CourseMembership.objects.create(course=private_course, user=self.other, role='student')
        self.client.force_login(self.student)
        response = self.client.get(reverse('lms:dashboard'))
        self.assertContains(response, self.course.nombre)
        self.assertContains(response, legacy.nombre)
        self.assertContains(response, 'Contenido anterior')
        self.assertNotContains(response, '&lt;p&gt;Contenido anterior')
        self.assertNotContains(response, private_course.nombre)
        self.assertNotContains(response, 'PRIVATE_LESSON_BODY')
        self.assertNotContains(response, 'LEGACY_SECRET')
        self.assertEqual(response.context['course_count'], 2)
        self.assertContains(response, reverse('lms:course_detail', args=[self.course.pk]))

    def test_create_link_retains_permission_gate(self):
        from django.contrib.auth.models import Permission

        permission = Permission.objects.get(content_type__app_label='courses', codename='add_curso')
        self.student.user_permissions.add(permission)
        self.client.force_login(self.student)
        response = self.client.get(reverse('lms:dashboard'))
        self.assertContains(response, reverse('lms:course_create'))
        self.assertContains(response, 'Crear aula')


class CampusAccessTests(CampusFixture):
    def test_campus_tech_design_preserves_private_course_and_progress(self):
        url = reverse('lms:campus')
        response = self.client.get(url)
        self.assertContains(response, 'campus-tech.css')
        self.assertContains(response, 'billing-hero')
        self.assertContains(response, 'id="campus-heading"')
        self.assertNotContains(response, self.course.nombre)
        self.enroll()
        self.client.force_login(self.student)
        response = self.client.get(url)
        self.assertContains(response, 'campus-course')
        self.assertContains(response, self.course.nombre)
        self.assertContains(response, 'value="0" max="100"')
        self.assertContains(response, self.url)
        self.assertNotContains(response, reverse('users:goal_create'))
        self.client.force_login(self.other)
        response = self.client.get(url)
        self.assertContains(response, 'Todavía no tienes cursos')
        self.assertNotContains(response, self.course.nombre)

    def test_visitor_presentation_and_no_personal_history(self):
        response = self.client.get(reverse('lms:campus'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.student.username)
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(CourseMembership.objects.filter(user=self.student).count(), 0)

    def test_enroll_open_resume_and_idempotent_declared_completion(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(self.url, {'action': 'enroll'}).status_code, 302)
        membership = CourseMembership.objects.get(course=self.course, user=self.student)
        self.assertEqual(membership.learning_contract['version'], 'fixture-v1')
        self.assertEqual(self.client.get(self.page_url).status_code, 200)
        membership.refresh_from_db()
        self.assertEqual(membership.last_content_id, self.item.pk)
        self.assertContains(self.client.get(self.url), 'Continuar')
        self.complete_lesson()
        membership.refresh_from_db()
        self.assertIsNotNone(membership.passed_at)
        passed_at = membership.passed_at
        self.complete_lesson()
        membership.refresh_from_db()
        self.assertEqual(membership.passed_at, passed_at)
        self.assertEqual(self.item.progress.filter(user=self.student).count(), 1)
        self.assertTrue(InscripcionCurso.objects.get(usuario=self.student, curso=self.course).completado)

    def test_membership_loss_and_independent_purchase_preserve_history(self):
        self.settings.campus_free = False
        self.settings.campus_included_plan = 'plus'
        self.settings.campus_entitlement_code = 'campus-fixture-course'
        self.settings.save()
        plan, _created = AccessMembership.objects.update_or_create(
            user=self.student, defaults={'plan': 'plus', 'billing_period': 'monthly'},
        )
        membership = self.enroll()
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(self.page_url).status_code, 200)
        plan.status = 'expired'
        plan.save()
        self.assertFalse(material_access(self.student, self.course).allowed)
        self.assertEqual(self.client.get(self.page_url).status_code, 403)
        self.assertNotContains(self.client.get(self.url), 'PRIVATE_LESSON_BODY')
        right = self.entitlement()
        self.assertEqual(course_right(self.student, self.course).reason, 'individual_purchase')
        self.assertEqual(self.client.get(self.page_url).status_code, 200)
        right.revoked_at = timezone.now()
        right.save()
        self.assertEqual(self.client.get(self.page_url).status_code, 403)
        membership.refresh_from_db()
        self.assertTrue(membership.learning_contract)
        self.assertTrue(self.item.progress.filter(user=self.student).exists())

    def test_pro_does_not_include_unlisted_courses_or_teaching(self):
        self.settings.campus_free = False
        self.settings.save()
        AccessMembership.objects.update_or_create(
            user=self.student, defaults={'plan': 'pro', 'billing_period': 'monthly'},
        )
        with self.assertRaises(EnrollmentUnavailable):
            self.enroll()
        self.client.force_login(self.student)
        self.assertNotContains(self.client.get(reverse('lms:campus_teaching')), self.teacher.username)

    def test_expired_purchase_future_right_and_multiple_rights(self):
        self.settings.campus_free = False
        self.settings.campus_entitlement_code = 'campus-fixture-course'
        self.settings.save()
        right = self.entitlement()
        right.starts_at = timezone.now() + timedelta(days=1)
        right.save()
        self.assertFalse(course_right(self.student, self.course).allowed)
        right.starts_at = timezone.now() - timedelta(days=2)
        right.expires_at = timezone.now() - timedelta(days=1)
        right.save()
        self.assertFalse(course_right(self.student, self.course).allowed)
        self.settings.campus_free = True
        self.settings.save()
        self.assertTrue(course_right(self.student, self.course).allowed)

    def test_prior_membership_needs_review_and_does_not_get_fictitious_purchase(self):
        membership = CourseMembership.objects.create(course=self.course, user=self.student)
        with self.assertRaises(EnrollmentUnavailable):
            self.enroll()
        self.assertFalse(material_access(self.student, self.course).allowed)
        freeze_contract(membership, actor=self.teacher)
        self.assertTrue(material_access(self.student, self.course).allowed)
        self.assertFalse(PurchasedEntitlement.objects.filter(user=self.student).exists())

    def test_frozen_requirements_and_changed_lesson_are_not_silently_migrated(self):
        membership = self.enroll()
        self.settings.campus_version = 'v2'
        with self.assertRaises(ValidationError):
            self.settings.save()
        with self.assertRaises(PermissionDenied):
            with transaction.atomic():
                self.settings.delete()
        membership.learning_contract['version'] = 'tampered'
        with self.assertRaises(ValidationError):
            membership.save()
        self.page.content = 'MODIFIED_BODY'
        self.page.save()
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(self.page_url).status_code, 403)

    def test_new_requirements_do_not_rewrite_prior_enrollments_or_delete_history(self):
        membership = self.enroll()
        page = Page.objects.create(
            course=self.course, module=self.module, title='Nuevo requisito', slug='nuevo',
            content='NEW_PRIVATE_BODY', status='published', author=self.teacher,
        )
        ModuleContent.objects.create(module=self.module, page=page)
        self.complete_lesson()
        membership.refresh_from_db()
        self.assertIsNotNone(membership.passed_at)
        self.assertNotContains(self.client.get(self.url), 'Nuevo requisito')
        self.assertEqual(self.client.get(reverse(
            'lms:course_page_detail', args=[self.course.pk, page.slug],
        )).status_code, 403)
        with self.assertRaises(PermissionDenied):
            with transaction.atomic():
                self.item.delete()
        self.assertTrue(self.item.progress.filter(user=self.student).exists())

    def test_empty_course_cannot_create_a_successful_enrollment(self):
        self.item.delete()
        with self.assertRaises(ValidationError):
            self.enroll()
        self.assertFalse(CourseMembership.objects.filter(user=self.student, course=self.course).exists())
        self.assertFalse(InscripcionCurso.objects.filter(usuario=self.student, curso=self.course).exists())

    def test_frozen_free_access_is_not_retroactively_removed_by_catalog_changes(self):
        membership = self.enroll()
        self.settings.campus_free = False
        self.settings.save()
        self.assertTrue(material_access(self.student, self.course).allowed)
        self.assertFalse(course_right(self.other, self.course).allowed)
        membership.status = 'suspended'
        membership.save()
        self.assertFalse(material_access(self.student, self.course).allowed)

    def test_no_legacy_api_or_html_bypass(self):
        self.enroll()
        self.client.force_login(self.student)
        response = self.client.get(reverse('curso-detail', args=[self.course.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['temas'], [])
        self.assertEqual(self.client.get(reverse('tema-detail', args=[self.module.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('courses:tema_detail', args=[self.module.pk])).status_code, 403)
        for area in ('content', 'activities', 'quizzes', 'files', 'syllabus', 'pages'):
            with self.subTest(area=area):
                self.assertRedirects(self.client.get(reverse(
                    'lms:course_area', args=[self.course.pk, area],
                )), self.url)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.page_url).status_code, 403)
        self.assertFalse(self.item.progress.filter(user=self.other).exists())
