from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from apps.analytics.models import RegistroActividad
from apps.users.models import Aula, Notificacion

from .models import BugReport


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class BugReportsPermissionsAndRewardsTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.aula = Aula.objects.create(
            nombre='Aula Bugs',
            direccion='Calle Bug 1',
            horario='Manana',
        )
        self.alumno = user_model.objects.create_user(
            username='alumno_bug',
            password='test1234',
            role='alumno',
            aula=self.aula,
        )
        self.basic_user = user_model.objects.create_user(
            username='basic_bug',
            password='test1234',
            role='basic',
        )
        self.admin = user_model.objects.create_user(
            username='admin_bug',
            password='test1234',
            is_staff=True,
        )

    def test_alumno_puede_ver_y_crear_bug(self):
        self.client.force_login(self.alumno)

        list_response = self.client.get(reverse('bug_reports:list'))
        create_response = self.client.post(
            reverse('bug_reports:create'),
            data={
                'titulo': 'Error de prueba',
                'descripcion': 'Se rompe al cargar.',
                'pasos_reproduccion': '1. Entrar\n2. Fallo',
                'url_afectada': '/evaluaciones/',
            },
            follow=True,
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(create_response.status_code, 200)
        self.assertTrue(BugReport.objects.filter(alumno=self.alumno, titulo='Error de prueba').exists())

    def test_usuario_no_alumno_no_puede_acceder_area_alumnos(self):
        self.client.force_login(self.basic_user)

        response = self.client.get(reverse('bug_reports:list'))

        self.assertEqual(response.status_code, 403)

    def test_solo_admin_puede_acceder_panel_admin_bugs(self):
        self.client.force_login(self.alumno)
        alumno_response = self.client.get(reverse('bug_reports:admin_list'))

        self.client.force_login(self.admin)
        admin_response = self.client.get(reverse('bug_reports:admin_list'))

        self.assertEqual(alumno_response.status_code, 403)
        self.assertEqual(admin_response.status_code, 200)

    def test_validar_bug_asigna_puntos_una_sola_vez(self):
        bug = BugReport.objects.create(
            alumno=self.alumno,
            titulo='Bug duplicado',
            descripcion='Descripcion',
            puntos_premio=7,
        )
        self.client.force_login(self.admin)

        first_review = self.client.post(
            reverse('bug_reports:admin_review', kwargs={'pk': bug.pk}),
            data={
                'estado': BugReport.ESTADO_VALIDADO,
                'puntos_premio': 7,
                'comentarios_admin': 'Buen hallazgo.',
            },
            follow=True,
        )
        self.alumno.refresh_from_db()
        bug.refresh_from_db()

        second_review = self.client.post(
            reverse('bug_reports:admin_review', kwargs={'pk': bug.pk}),
            data={
                'estado': BugReport.ESTADO_VALIDADO,
                'puntos_premio': 20,
                'comentarios_admin': 'Revalidacion.',
            },
            follow=True,
        )
        self.alumno.refresh_from_db()
        bug.refresh_from_db()

        self.assertEqual(first_review.status_code, 200)
        self.assertEqual(second_review.status_code, 200)
        self.assertTrue(bug.puntos_asignados)
        self.assertEqual(self.alumno.puntos, 7)
        self.assertEqual(self.alumno.puntos_totales, 7)
        self.assertEqual(Notificacion.objects.filter(usuario=self.alumno, titulo='Bug validado').count(), 1)
        actividades = RegistroActividad.objects.filter(usuario=self.alumno, tipo='bug_validado')
        self.assertEqual(actividades.count(), 1)
        self.assertEqual(actividades.first().puntos_ganados, 7)
