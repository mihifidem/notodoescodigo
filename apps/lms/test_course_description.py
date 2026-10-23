from datetime import date

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django_prose_editor.widgets import ProseEditorWidget

from apps.courses.models import Curso
from .forms import CourseForm, CourseSettingsForm


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class CourseDescriptionTests(TestCase):
    def test_creation_tech_design_keeps_fields_category_tools_and_validation(self):
        user = get_user_model().objects.create_superuser('course-tech-editor')
        self.client.force_login(user)
        url = reverse('lms:course_create')
        response = self.client.get(url)
        for content in (
            'lms/course-form-tech.css', 'css/billing.css', 'billing-hero',
            'id="course-form-heading"', 'name="csrfmiddlewaretoken"',
            'data-auto-slug', 'data-rich-editor-shell', 'data-category-dialog',
            'data-category-open', 'Crear borrador',
        ):
            self.assertContains(response, content)
        for field in CourseForm().fields:
            self.assertContains(response, f'name="{field}"')
        response = self.client.post(url, {'nombre': '', 'descripcion': '', 'fecha_inicio': ''})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, 'course-form-tech')
        self.assertFalse(Curso.objects.exists())

    def form(self, description):
        return CourseForm(data={
            'nombre': 'Curso con formato', 'descripcion': description, 'fecha_inicio': '2026-10-22',
        })

    def test_editor_is_available_for_creation_settings_and_admin(self):
        for form_class in (CourseForm, CourseSettingsForm):
            form = form_class()
            self.assertIsInstance(form.fields['descripcion'].widget, ProseEditorWidget)
            self.assertIn('Bold', str(form['descripcion']))
            self.assertIn('django_prose_editor', str(form.media))
        request = RequestFactory().get('/')
        request.user = get_user_model().objects.create_superuser('description-admin')
        admin_form = admin.site._registry[Curso].get_form(request)()
        self.assertIsInstance(admin_form.fields['descripcion'].widget, ProseEditorWidget)

    def test_form_persists_format_and_removes_active_content(self):
        form = self.form(
            '<h2>Objetivos</h2><p><strong>Aprende</strong> <em>Python</em>.</p>'
            '<ul><li>Practicar</li></ul><script>alert(1)</script>'
            '<a href="javascript:alert(1)" onclick="alert(1)">Enlace</a>'
        )
        self.assertTrue(form.is_valid(), form.errors)
        course = form.save()
        course.refresh_from_db()
        self.assertIn('<strong>Aprende</strong>', course.descripcion)
        self.assertIn('<li>Practicar</li>', course.descripcion)
        self.assertNotIn('script', course.descripcion)
        self.assertNotIn('onclick', course.descripcion)
        self.assertNotIn('javascript:', course.descripcion)
        self.assertNotIn('<', course.resumen)
        edited = CourseForm(instance=course)
        self.assertEqual(edited.initial['descripcion'], course.descripcion)

    def test_visually_empty_description_is_rejected(self):
        for description in ('<p></p>', '<p>&nbsp;</p>', '<script>alert(1)</script>'):
            form = self.form(description)
            self.assertFalse(form.is_valid())
            self.assertIn('descripcion', form.errors)

    def test_rendering_sanitizes_legacy_or_external_html(self):
        course = Curso(
            nombre='Descripción', fecha_inicio=date(2026, 10, 22),
            descripcion='<p><strong>Texto</strong></p><img src="x" onerror="alert(1)">',
        )
        self.assertIn('<strong>Texto</strong>', course.descripcion_html)
        self.assertNotIn('onerror', course.descripcion_html)
        self.assertEqual(course.descripcion_texto, 'Texto')
        course.descripcion = 'Texto existente sin formato'
        self.assertEqual(course.descripcion_html, course.descripcion)

    def test_course_form_loads_editor_assets_and_published_detail_renders_format(self):
        user = get_user_model().objects.create_superuser('description-editor')
        self.client.force_login(user)
        course = self.form('<p><strong>Descripción visible</strong></p>').save()
        request = RequestFactory().get('/')
        request.user = user
        html = render_to_string('lms/course_form.html', {
            'form': CourseForm(instance=course), 'form_title': 'Editar curso',
        }, request=request)
        self.assertIn('data-rich-editor-shell', html)
        self.assertIn('lms/prose-editor.js', html)
        self.assertIn('django_prose_editor', html)
        response = self.client.get(reverse('lms:course_area', kwargs={
            'course_id': course.pk, 'area': 'settings',
        }))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'lms/prose-editor.js')
        self.assertContains(response, 'django_prose_editor')
        self.assertContains(response, 'data-rich-editor-shell')
        response = self.client.get(reverse('lms:course_detail', kwargs={'course_id': course.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<strong>Descripción visible</strong>', html=True)
