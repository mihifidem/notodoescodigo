from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from apps.courses.models import Curso, InscripcionCurso, Progreso
from apps.gamification.models import Insignia
from apps.users.models import Aula


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class InsigniasViewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.aula = Aula.objects.create(
            nombre='Aula Gamificacion',
            direccion='Calle Badge 1',
            horario='Manana',
        )
        self.user = user_model.objects.create_user(
            username='usuario_insignias',
            password='test1234',
            aula=self.aula,
        )
        self.curso = Curso.objects.create(
            nombre='Curso con insignia',
            descripcion='Descripcion',
            fecha_inicio=date.today(),
            horas_duracion=2,
            activo=True,
        )
        self.tema = self.curso.temas.create(
            titulo='Tema con insignia',
            contenido='Contenido',
            orden=1,
            puntos_otorgados=10,
        )
        self.insignia = Insignia.objects.create(
            nombre='Insignia del tema',
            descripcion='Se activa al completar el tema',
            tipo='tema',
            tema_objetivo=self.tema,
            visible=True,
        )
        self.aula.cursos.add(self.curso)
        InscripcionCurso.objects.create(usuario=self.user, curso=self.curso)
        self.client.force_login(self.user)

    def test_insignia_de_tema_completado_aparece_activa_en_color(self):
        Progreso.objects.create(usuario=self.user, tema=self.tema, completado=True)

        response = self.client.get(reverse('gamification:insignias'))

        self.assertEqual(response.status_code, 200)
        insignias_con_estado = dict(response.context['insignias_con_estado'])
        self.assertTrue(insignias_con_estado[self.insignia])
        self.assertContains(response, 'Desbloqueada')


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class ComoGanarPuntosViewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.aula = Aula.objects.create(
            nombre='Aula Puntos',
            direccion='Calle Puntos 1',
            horario='Tarde',
        )
        self.user = user_model.objects.create_user(
            username='usuario_puntos',
            password='test1234',
            role='alumno',
            aula=self.aula,
        )
        self.client.force_login(self.user)

    def test_pagina_como_ganar_puntos_renderiza_listado(self):
        response = self.client.get(reverse('gamification:como_ganar_puntos'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Listado de formas de conseguir puntos')
        self.assertContains(response, 'Completar temas del curso')
        self.assertContains(response, 'Bug validado por admin')