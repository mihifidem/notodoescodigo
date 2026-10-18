from io import BytesIO
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from reportlab.pdfgen import canvas

from apps.curriculum.forms import CvPdfUploadForm
from apps.curriculum.models import (
    CurriculumVitae,
    Education,
    PersonalInfo,
    Skill,
    WorkExperience,
)
from apps.curriculum.services.cv_importer import (
    PdfImportError,
    apply_cv_fields,
    extract_cv_fields,
    parse_cv_text,
)
from apps.users.models import User


class CvPdfUploadFormTests(SimpleTestCase):
    def test_rejects_non_pdf_upload(self):
        upload = SimpleUploadedFile('resume.txt', b'not a pdf', content_type='text/plain')

        form = CvPdfUploadForm(files={'pdf': upload})

        self.assertFalse(form.is_valid())
        self.assertIn('pdf', form.errors)

    def test_rejects_pdf_larger_than_limit(self):
        upload = SimpleUploadedFile('resume.pdf', b'%PDF-' + b'x' * (5 * 1024 * 1024))

        form = CvPdfUploadForm(files={'pdf': upload})

        self.assertFalse(form.is_valid())
        self.assertIn('5 MB', str(form.errors['pdf']))


class CvTextExtractionTests(SimpleTestCase):
    def test_extracts_contact_links_summary_and_skills(self):
        fields = parse_cv_text(
            'María García López\n'
            'maria@example.com\n'
            '+34 612 345 678\n'
            'Ubicación: Madrid, España\n'
            'LinkedIn: https://linkedin.com/in/mariagarcia\n'
            'Perfil profesional\n'
            'Desarrolladora con experiencia en aplicaciones web.\n'
            'Habilidades: Python · Django · SQL\n'
        )

        self.assertEqual(fields['personal']['nombre'], 'María')
        self.assertEqual(fields['personal']['apellidos'], 'García López')
        self.assertEqual(fields['personal']['email_profesional1'], 'maria@example.com')
        self.assertEqual(fields['personal']['ciudad'], 'Madrid')
        self.assertEqual(fields['personal']['pais'], 'España')
        self.assertIn('linkedin', fields['social'])
        self.assertEqual(fields['professional']['resumen'], 'Desarrolladora con experiencia en aplicaciones web.')
        self.assertEqual(fields['skills'], ['Python', 'Django', 'SQL'])

    def test_extracts_dated_experience_and_education_entries(self):
        fields = parse_cv_text(
            'Experiencia profesional\n'
            'Desarrolladora | Acme | Madrid | 01/2022 - Actualidad\n'
            'Desarrollo de aplicaciones web.\n'
            'Educación\n'
            'Ingeniería informática | Universidad Central | 2018 - 2022\n'
        )

        self.assertEqual(fields['experience'], [{
            'puesto': 'Desarrolladora',
            'empresa': 'Acme',
            'ubicacion': 'Madrid',
            'fecha_inicio': '2022-01-01',
            'fecha_fin': '',
            'trabajo_actual': True,
            'descripcion': 'Desarrollo de aplicaciones web.',
        }])
        self.assertEqual(fields['education'], [{
            'titulo': 'Ingeniería informática',
            'centro': 'Universidad Central',
            'ubicacion': '',
            'fecha_inicio': '2018-01-01',
            'fecha_fin': '2022-01-01',
            'en_curso': False,
        }])

    def test_extracts_text_from_a_real_pdf(self):
        buffer = BytesIO()
        document = canvas.Canvas(buffer)
        document.drawString(40, 760, 'María García López')
        document.drawString(40, 740, 'maria@example.com')
        document.save()

        fields = extract_cv_fields(SimpleUploadedFile('cv.pdf', buffer.getvalue()))

        self.assertEqual(fields['personal']['email_profesional1'], 'maria@example.com')

    @patch('apps.curriculum.services.cv_importer.PdfReader')
    def test_rejects_pdf_without_selectable_text(self, pdf_reader):
        pdf_reader.return_value.is_encrypted = False
        pdf_reader.return_value.pages = [type('Page', (), {'extract_text': lambda self: ''})()]

        with self.assertRaises(PdfImportError):
            extract_cv_fields(SimpleUploadedFile('resume.pdf', b'%PDF-1.4'))


class CvImportPersistenceTests(TestCase):
    def test_import_fills_only_empty_fields_and_skips_duplicate_skills(self):
        user = User.objects.create_user(username='cv-import-user', password='test-password')
        cv = CurriculumVitae.objects.create(user=user)
        PersonalInfo.objects.create(
            cv=cv,
            nombre='Nombre existente',
            apellidos='Apellidos existentes',
            telefono1='',
            telefono2='',
            email_profesional1='existing@example.com',
            email_profesional2='',
            ciudad='',
            pais='',
        )
        Skill.objects.create(cv=cv, nombre='Python')

        changed = apply_cv_fields(cv, {
            'personal': {
                'nombre': 'Nombre del PDF',
                'email_profesional1': 'pdf@example.com',
                'telefono1': '+34 600 111 222',
            },
            'skills': ['python', 'Django'],
        })

        self.assertEqual(changed['personal'], 1)
        personal = PersonalInfo.objects.get(cv=cv)
        self.assertEqual(personal.nombre, 'Nombre existente')
        self.assertEqual(personal.email_profesional1, 'existing@example.com')
        self.assertEqual(personal.telefono1, '+34 600 111 222')
        self.assertEqual(Skill.objects.filter(cv=cv).count(), 2)
        self.assertEqual(changed['skills'], 1)

    def test_import_saves_and_deduplicates_experience_and_education(self):
        user = User.objects.create_user(username='cv-entry-import-user', password='test-password')
        cv = CurriculumVitae.objects.create(user=user)
        fields = {
            'experience': [{
                'puesto': 'Desarrolladora',
                'empresa': 'Acme',
                'ubicacion': 'Madrid',
                'fecha_inicio': '2022-01-01',
                'fecha_fin': '',
                'trabajo_actual': True,
                'descripcion': 'Desarrollo de aplicaciones web.',
            }],
            'education': [{
                'titulo': 'Ingeniería informática',
                'centro': 'Universidad Central',
                'ubicacion': '',
                'fecha_inicio': '2018-01-01',
                'fecha_fin': '2022-01-01',
                'en_curso': False,
            }],
        }

        self.assertEqual(apply_cv_fields(cv, fields)['experience'], 1)
        self.assertEqual(apply_cv_fields(cv, fields)['education'], 0)

        experience = WorkExperience.objects.get(cv=cv)
        education = Education.objects.get(cv=cv)
        self.assertEqual(experience.puesto, 'Desarrolladora')
        self.assertEqual(experience.descripcion, 'Desarrollo de aplicaciones web.')
        self.assertTrue(experience.trabajo_actual)
        self.assertEqual(education.titulo, 'Ingeniería informática')
        self.assertEqual(WorkExperience.objects.filter(cv=cv).count(), 1)
        self.assertEqual(Education.objects.filter(cv=cv).count(), 1)

    @patch('apps.curriculum.views.extract_cv_fields')
    def test_upload_shows_preview_and_saves_only_after_confirmation(self, extract_fields):
        user = User.objects.create_user(username='cv-upload-user', password='test-password')
        self.client.force_login(user)
        extract_fields.return_value = {
            'personal': {
                'nombre': 'Lucía',
                'apellidos': 'Pérez',
                'email_profesional1': 'lucia@example.com',
                'telefono1': '+34 600 111 333',
            },
        }
        upload = SimpleUploadedFile('cv.pdf', b'%PDF-1.4 test')

        response = self.client.post(reverse('curriculum:import_cv_pdf'), {'pdf': upload})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Revisa los datos detectados')
        self.assertFalse(CurriculumVitae.objects.filter(user=user).exists())

        response = self.client.post(
            reverse('curriculum:import_cv_pdf'),
            {'action': 'confirm'},
        )

        self.assertRedirects(response, reverse('curriculum:dashboard'))
        personal = PersonalInfo.objects.get(cv__user=user)
        self.assertEqual(personal.nombre, 'Lucía')
        self.assertEqual(personal.email_profesional1, 'lucia@example.com')
