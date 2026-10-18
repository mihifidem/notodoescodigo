from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from apps.courses.models import (
    Curso,
    InscripcionCurso,
    Pregunta,
    Progreso,
    Quiz,
    Respuesta,
    ResultadoQuiz,
    TipoRecursoTema,
    TemaRecurso,
    TemaRecursoVisualizacion,
)
from apps.users.models import Aula


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class QuizFlowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.aula = Aula.objects.create(
            nombre='Aula Norte',
            direccion='Calle Falsa 123',
            horario='Manana',
        )
        self.user = user_model.objects.create_user(
            username='alumno',
            password='test1234',
            aula=self.aula,
        )

        self.curso = Curso.objects.create(
            nombre='Curso Test',
            descripcion='Descripcion',
            fecha_inicio=date.today(),
            horas_duracion=1,
            activo=True,
        )
        self.tema = self.curso.temas.create(
            titulo='Tema 1',
            contenido='Contenido',
            orden=1,
            puntos_otorgados=10,
        )
        self.quiz = Quiz.objects.create(
            tema=self.tema,
            titulo='Quiz Tema 1',
            porcentaje_aprobacion=70,
        )
        self.aula.cursos.add(self.curso)

        self.pregunta = Pregunta.objects.create(quiz=self.quiz, texto='2 + 2 = ?', orden=1)
        Respuesta.objects.create(pregunta=self.pregunta, texto='4', es_correcta=True)
        Respuesta.objects.create(pregunta=self.pregunta, texto='3', es_correcta=False)
        Respuesta.objects.create(pregunta=self.pregunta, texto='2', es_correcta=False)
        Respuesta.objects.create(pregunta=self.pregunta, texto='5', es_correcta=False)

        InscripcionCurso.objects.create(usuario=self.user, curso=self.curso)
        self.client.force_login(self.user)

    def test_no_puede_completar_tema_si_quiz_no_aprobado(self):
        response = self.client.post(reverse('courses:completar_tema', kwargs={'pk': self.tema.pk}))

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload['ok'])
        self.assertIn('Debes aprobar el quiz', payload['error'])
        self.assertFalse(
            Progreso.objects.filter(usuario=self.user, tema=self.tema, completado=True).exists()
        )

    def test_puede_completar_tema_si_quiz_ya_aprobado(self):
        ResultadoQuiz.objects.create(
            usuario=self.user,
            quiz=self.quiz,
            puntuacion=1,
            total_preguntas=1,
            aprobado=True,
        )

        response = self.client.post(reverse('courses:completar_tema', kwargs={'pk': self.tema.pk}))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertTrue(
            Progreso.objects.filter(usuario=self.user, tema=self.tema, completado=True).exists()
        )

    def test_enviar_quiz_crea_resultado_aprobado(self):
        with patch(
            'apps.courses.views.QuizView._require_csv_questions',
            return_value=(
                [{
                    'id': 'csv_1',
                    'texto': '2 + 2 = ?',
                    'opciones': [
                        {'key': 'A', 'texto': '4'},
                        {'key': 'B', 'texto': '3'},
                    ],
                    'correcta': 'A',
                }],
                None,
            ),
        ):
            response = self.client.post(
                reverse('courses:quiz', kwargs={'pk': self.quiz.pk}),
                data={'pregunta_csv_1': 'A'},
            )

        self.assertEqual(response.status_code, 200)
        resultado = ResultadoQuiz.objects.filter(usuario=self.user, quiz=self.quiz).latest('fecha')
        self.assertTrue(resultado.aprobado)
        self.assertEqual(resultado.puntuacion, 1)
        self.assertEqual(resultado.total_preguntas, 1)

    def test_enviar_quiz_guarda_tiempo_empleado(self):
        with patch(
            'apps.courses.views.QuizView._require_csv_questions',
            return_value=(
                [{
                    'id': 'csv_1',
                    'texto': '2 + 2 = ?',
                    'opciones': [
                        {'key': 'A', 'texto': '4'},
                        {'key': 'B', 'texto': '3'},
                    ],
                    'correcta': 'A',
                }],
                None,
            ),
        ):
            response = self.client.post(
                reverse('courses:quiz', kwargs={'pk': self.quiz.pk}),
                data={'pregunta_csv_1': 'A', 'tiempo_empleado_segundos': '42'},
            )

        self.assertEqual(response.status_code, 200)
        resultado = ResultadoQuiz.objects.filter(usuario=self.user, quiz=self.quiz).latest('fecha')
        self.assertEqual(resultado.tiempo_empleado, 42)


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class RestriccionCursosPorAulaTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.aula = Aula.objects.create(
            nombre='Aula Centro',
            direccion='Av. Principal 10',
            horario='Tarde',
        )
        self.user = user_model.objects.create_user(
            username='alumno_aula',
            password='test1234',
            aula=self.aula,
        )
        self.curso_permitido = Curso.objects.create(
            nombre='Curso Permitido',
            descripcion='Disponible para el aula',
            fecha_inicio=date.today(),
            horas_duracion=2,
            activo=True,
        )
        self.curso_bloqueado = Curso.objects.create(
            nombre='Curso Bloqueado',
            descripcion='No disponible para el aula',
            fecha_inicio=date.today(),
            horas_duracion=3,
            activo=True,
        )
        self.aula.cursos.add(self.curso_permitido)
        self.client.force_login(self.user)

    def test_listado_solo_muestra_cursos_del_aula(self):
        response = self.client.get(reverse('courses:list'))

        self.assertEqual(response.status_code, 200)
        cursos = list(response.context['cursos'])
        self.assertEqual(cursos, [self.curso_permitido])

    def test_listado_muestra_cursos_activospor_defecto_sin_aula(self):
        self.user.aula = None
        self.user.save(update_fields=['aula'])

        response = self.client.get(reverse('courses:list'))

        self.assertEqual(response.status_code, 200)
        cursos = list(response.context['cursos'])
        self.assertIn(self.curso_permitido, cursos)
        self.assertIn(self.curso_bloqueado, cursos)

    def test_no_puede_inscribirse_en_curso_fuera_de_su_aula(self):
        response = self.client.post(
            reverse('courses:inscribirse', kwargs={'pk': self.curso_bloqueado.pk}),
            follow=True,
        )

        self.assertRedirects(response, reverse('courses:list'))
        self.assertFalse(
            InscripcionCurso.objects.filter(usuario=self.user, curso=self.curso_bloqueado).exists()
        )

    def test_detalle_de_curso_no_asignado_devuelve_404(self):
        response = self.client.get(reverse('courses:detail', kwargs={'pk': self.curso_bloqueado.pk}))

        self.assertEqual(response.status_code, 404)


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class RecursosTemaTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.aula = Aula.objects.create(
            nombre='Aula Recursos',
            direccion='Calle Recursos 5',
            horario='Manana',
        )
        self.user = user_model.objects.create_user(
            username='usuario_recursos',
            password='test1234',
            aula=self.aula,
        )
        self.curso = Curso.objects.create(
            nombre='Curso Recursos',
            descripcion='Descripcion',
            fecha_inicio=date.today(),
            horas_duracion=2,
            activo=True,
        )
        self.tema = self.curso.temas.create(
            titulo='Tema Recursos',
            contenido='Contenido',
            orden=1,
            puntos_otorgados=10,
        )
        self.tipo_pdf, _ = TipoRecursoTema.objects.get_or_create(codigo='pdf', defaults={'nombre': 'PDF'})
        self.recurso = TemaRecurso.objects.create(
            tema=self.tema,
            titulo='Guia PDF',
            tipo_recurso=self.tipo_pdf,
            archivo='recursos_tema/guia.pdf',
            activo=True,
        )
        self.aula.cursos.add(self.curso)
        InscripcionCurso.objects.create(usuario=self.user, curso=self.curso)
        self.client.force_login(self.user)

    def test_recurso_guarda_numero_hojas_y_tiempo_lectura(self):
        recurso = TemaRecurso.objects.create(
            tema=self.tema,
            titulo='Guia PDF con metadata',
            tipo_recurso=self.tipo_pdf,
            archivo='recursos_tema/guia2.pdf',
            activo=True,
            numero_hojas=12,
            tiempo_lectura='8 min',
        )

        self.assertEqual(recurso.numero_hojas, 12)
        self.assertEqual(recurso.tiempo_lectura, '8 min')

    def test_ver_recurso_online_otorga_tres_puntos_solo_una_vez(self):
        puntos_iniciales = self.user.puntos

        first_response = self.client.get(reverse('courses:recurso_online', kwargs={'pk': self.recurso.pk}))
        self.user.refresh_from_db()
        second_response = self.client.get(reverse('courses:recurso_online', kwargs={'pk': self.recurso.pk}))
        self.user.refresh_from_db()

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(self.user.puntos, puntos_iniciales + 3)
        self.assertEqual(
            TemaRecursoVisualizacion.objects.filter(usuario=self.user, recurso=self.recurso).count(),
            1,
        )

    def test_detalle_tema_muestra_recursos_y_estado_de_recompensa(self):
        response = self.client.get(reverse('courses:tema_detail', kwargs={'pk': self.tema.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Recursos del tema')
        self.assertContains(response, 'Guia PDF')
        self.assertContains(response, 'Pendiente de recompensa')

    def test_recurso_pdf_usa_visualizador_compatible(self):
        response = self.client.get(reverse('courses:recurso_online', kwargs={'pk': self.recurso.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'type="application/pdf"')
        self.assertContains(response, 'Abrir PDF en otra pestaña')

    def test_abrir_o_descargar_recurso_otorga_puntos(self):
        puntos_iniciales = self.user.puntos

        response_open = self.client.get(reverse('courses:recurso_abrir', kwargs={'pk': self.recurso.pk}))
        self.user.refresh_from_db()

        self.assertEqual(response_open.status_code, 302)
        self.assertEqual(self.user.puntos, puntos_iniciales + 3)
        self.assertEqual(
            TemaRecursoVisualizacion.objects.filter(usuario=self.user, recurso=self.recurso).count(),
            1,
        )

        puntos_tras_abrir = self.user.puntos
        response_download = self.client.get(reverse('courses:recurso_descargar', kwargs={'pk': self.recurso.pk}))
        self.user.refresh_from_db()

        self.assertEqual(response_download.status_code, 302)
        self.assertEqual(self.user.puntos, puntos_tras_abrir)
        self.assertEqual(
            TemaRecursoVisualizacion.objects.filter(usuario=self.user, recurso=self.recurso).count(),
            1,
        )
