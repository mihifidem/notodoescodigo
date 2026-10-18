import base64
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from apps.certifications.utils import _build_pdf_buffer
from apps.courses.models import Curso, InscripcionCurso, Progreso, Quiz, ResultadoQuiz, Tema
from apps.gamification.models import Insignia
from apps.users.forms import PerfilForm, RegistroForm
from apps.users.models import Aula
from apps.users.serializers import UserSerializer
from apps.users.views import generar_avatar_ducky_con_ia


class CertificatePdfGenerationTests(TestCase):
    def test_build_pdf_buffer_accepts_uuid_code(self):
        user = get_user_model().objects.create_user(
            username='cert_user',
            password='test1234',
            first_name='Cert',
            last_name='User',
        )
        curso = Curso.objects.create(
            nombre='Curso QR',
            descripcion='Curso para validar QR',
            fecha_inicio=date.today(),
        )

        from apps.certifications.models import Certificado

        cert = Certificado.objects.create(usuario=user, curso=curso)

        buffer = _build_pdf_buffer(cert)

        self.assertTrue(buffer.getvalue())

    def test_build_pdf_buffer_includes_topic_quiz_summary(self):
        user = get_user_model().objects.create_user(
            username='quiz_cert_user',
            password='test1234',
            first_name='Quiz',
            last_name='User',
        )
        curso = Curso.objects.create(
            nombre='Curso con temas',
            descripcion='Curso con evaluación por temas',
            fecha_inicio=date.today(),
        )
        tema = Tema.objects.create(
            curso=curso,
            titulo='Tema 1',
            contenido='Contenido',
        )
        quiz = Quiz.objects.create(
            tema=tema,
            titulo='Quiz Tema 1',
            porcentaje_aprobacion=70,
        )
        ResultadoQuiz.objects.create(
            usuario=user,
            quiz=quiz,
            puntuacion=8,
            total_preguntas=10,
            aprobado=True,
        )

        from apps.certifications.models import Certificado

        cert = Certificado.objects.create(usuario=user, curso=curso)
        buffer = _build_pdf_buffer(cert)

        self.assertTrue(buffer.getvalue())

    def test_build_pdf_buffer_renders_topics_on_second_page(self):
        user = get_user_model().objects.create_user(
            username='multi_page_cert_user',
            password='test1234',
            first_name='Multi',
            last_name='Page',
        )
        curso = Curso.objects.create(
            nombre='Curso multipágina',
            descripcion='Curso con resultados por tema',
            fecha_inicio=date.today(),
        )
        tema = Tema.objects.create(
            curso=curso,
            titulo='Tema 1',
            contenido='Contenido',
        )
        quiz = Quiz.objects.create(
            tema=tema,
            titulo='Quiz Tema 1',
            porcentaje_aprobacion=70,
        )
        ResultadoQuiz.objects.create(
            usuario=user,
            quiz=quiz,
            puntuacion=9,
            total_preguntas=10,
            aprobado=True,
        )

        from apps.certifications.models import Certificado

        cert = Certificado.objects.create(usuario=user, curso=curso)
        buffer = _build_pdf_buffer(cert)
        pdf_bytes = buffer.getvalue()

        self.assertGreater(pdf_bytes.count(b'/Type /Page'), 1)


class RegistroFormTests(TestCase):
    def setUp(self):
        self.aula = Aula.objects.create(
            nombre='Aula Registro',
            direccion='Calle Registro 10',
            horario='Manana',
        )

    def test_registro_asigna_role_basic_por_defecto(self):
        form = RegistroForm(data={
            'username': 'nuevo_usuario',
            'first_name': 'Nuevo',
            'last_name': 'Usuario',
            'email': 'nuevo@example.com',
            'codigo_acceso': 'xxxxxx',
            'password1': 'ClaveSegura123',
            'password2': 'ClaveSegura123',
        })

        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()

        self.assertEqual(user.role, 'basic')
        self.assertIsNone(user.aula)

    def test_registro_con_codigo_alumno_asigna_role_alumno_y_aula(self):
        form = RegistroForm(data={
            'username': 'alumno_nuevo',
            'first_name': 'Alumno',
            'last_name': 'Nuevo',
            'email': 'alumno@example.com',
            'codigo_acceso': '010819',
            'aula': self.aula.pk,
            'password1': 'ClaveSegura123',
            'password2': 'ClaveSegura123',
        })

        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()

        self.assertEqual(user.role, 'alumno')
        self.assertEqual(user.aula_id, self.aula.id)


class UserSerializerTests(TestCase):
    def test_serializer_expone_role(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username='premium_user',
            password='test1234',
            role='premium',
        )

        serializer = UserSerializer(user)

        self.assertEqual(serializer.data['role'], 'premium')


class AdminCsvManagerTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.admin_user = self.user_model.objects.create_user(
            username='admin_csv',
            password='test1234',
            is_staff=True,
            is_superuser=True,
        )
        self.normal_user = self.user_model.objects.create_user(
            username='student_csv',
            password='test1234',
            role='alumno',
        )

    def test_admin_csv_manager_requires_staff(self):
        self.client.force_login(self.normal_user)

        response = self.client.get(reverse('users:admin_csv_manager'))

        self.assertEqual(response.status_code, 403)

    def test_admin_csv_manager_can_export_model_csv(self):
        self.client.force_login(self.admin_user)
        Aula.objects.create(nombre='Aula Export', direccion='Calle Export 1', horario='Mañana')

        response = self.client.get(reverse('users:admin_csv_manager') + '?model=users.aula&action=export')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn('nombre,direccion,horario', response.content.decode('utf-8'))
        self.assertIn('Aula Export', response.content.decode('utf-8'))

    def test_admin_csv_manager_can_replace_model_rows_from_csv(self):
        self.client.force_login(self.admin_user)
        Aula.objects.create(nombre='Aula Vieja', direccion='Old Street', horario='Tarde')

        csv_content = 'nombre,direccion,horario\nAula Nueva,Calle Nueva,Mana\n'
        file_upload = SimpleUploadedFile('aulas.csv', csv_content.encode('utf-8'), content_type='text/csv')

        response = self.client.post(
            reverse('users:admin_csv_manager'),
            {
                'model': 'users.aula',
                'action': 'import',
                'csv_file': file_upload,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Aula.objects.count(), 1)
        self.assertTrue(Aula.objects.filter(nombre='Aula Nueva', direccion='Calle Nueva', horario='Mañana').exists())


class PerfilFormTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.aula = Aula.objects.create(
            nombre='Aula Form',
            direccion='Calle Form 1',
            horario='Manana',
        )
        self.user = user_model.objects.create_user(
            username='perfil_form_user',
            password='test1234',
            first_name='Perfil',
            last_name='Usuario',
            email='perfil@example.com',
            role='alumno',
            aula=self.aula,
            bio='Bio original',
        )

    def test_editar_perfil_sin_codigo_conserva_aula_y_rol(self):
        form = PerfilForm(
            data={
                'first_name': 'Perfil',
                'last_name': 'Actualizado',
                'email': 'perfil@example.com',
                'bio': 'Bio actualizada',
                'fecha_nacimiento': '',
                'aula': self.aula.pk,
                'codigo_acceso_alumno': '',
            },
            instance=self.user,
        )

        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.user.refresh_from_db()

        self.assertEqual(self.user.role, 'alumno')
        self.assertEqual(self.user.aula_id, self.aula.id)
        self.assertEqual(self.user.bio, 'Bio actualizada')

    def test_usuario_sin_aula_sigue_viendo_curso_inscrito(self):
        curso = Curso.objects.create(
            nombre='Curso Persistente',
            descripcion='Descripcion',
            fecha_inicio=date.today(),
            horas_duracion=2,
            activo=True,
        )
        InscripcionCurso.objects.create(usuario=self.user, curso=curso)
        self.user.aula = None
        self.user.save(update_fields=['aula'])

        cursos = self.user.get_cursos_disponibles()

        self.assertIn(curso, cursos)


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class PerfilViewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.aula = Aula.objects.create(
            nombre='Aula Perfil',
            direccion='Calle Perfil 1',
            horario='Tarde',
        )
        self.user = user_model.objects.create_user(
            username='alumno_demo',
            password='test1234',
            role='alumno',
            aula=self.aula,
        )
        self.client.force_login(self.user)

    def test_ficha_usuario_muestra_role(self):
        response = self.client.get(reverse('users:perfil', kwargs={'username': self.user.username}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Rol: Alumno')

    def test_ficha_usuario_muestra_insignia_de_tema_activa(self):
        curso = Curso.objects.create(
            nombre='Curso Perfil',
            descripcion='Descripcion',
            fecha_inicio=date.today(),
            horas_duracion=2,
            activo=True,
        )
        tema = curso.temas.create(
            titulo='Tema Perfil',
            contenido='Contenido',
            orden=1,
            puntos_otorgados=10,
        )
        insignia = Insignia.objects.create(
            nombre='Insignia Perfil',
            descripcion='Se activa al completar el tema',
            tipo='tema',
            tema_objetivo=tema,
            visible=True,
        )
        self.aula.cursos.add(curso)
        InscripcionCurso.objects.create(usuario=self.user, curso=curso)
        Progreso.objects.create(usuario=self.user, tema=tema, completado=True)

        response = self.client.get(reverse('users:perfil', kwargs={'username': self.user.username}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Insignias (1 / 1)')
        insignias_con_estado = dict(response.context['insignias_con_estado'])
        self.assertTrue(insignias_con_estado[insignia])


class DuckyAvatarGenerationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='ducky_avatar_user',
            password='test1234',
            first_name='Avatar',
            last_name='User',
        )
        self.client.force_login(self.user)

    def test_generar_avatar_ducky_requiere_foto(self):
        response = self.client.post(
            reverse('users:generar_avatar_ducky'),
            {},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('foto', response.json()['error'])

    @override_settings(OPENAI_API_KEY='sk-test')
    @patch('openai.OpenAI')
    def test_generar_avatar_ducky_con_ia_no_envia_response_format_unsupported(self, mock_openai):
        image_bytes = b'fake-image-bytes'
        mock_openai.return_value.images.generate.return_value.data = [
            SimpleNamespace(b64_json=base64.b64encode(image_bytes).decode('utf-8'))
        ]

        result = generar_avatar_ducky_con_ia(self.user)

        self.assertEqual(result, image_bytes)
        _, kwargs = mock_openai.return_value.images.generate.call_args
        self.assertNotIn('response_format', kwargs)

    @override_settings(OPENAI_API_KEY='sk-test')
    @patch('openai.OpenAI')
    def test_generar_avatar_ducky_usa_la_foto_nueva_en_las_ia(self, mock_openai):
        image_bytes = b'fake-image-bytes'
        uploaded_file = SimpleUploadedFile(
            'selfie_nueva.jpg',
            b'foto-nueva',
            content_type='image/jpeg',
        )
        mock_openai.return_value.images.edit.return_value.data = [
            SimpleNamespace(b64_json=base64.b64encode(image_bytes).decode('utf-8'))
        ]

        result = generar_avatar_ducky_con_ia(self.user, uploaded_file)

        self.assertEqual(result, image_bytes)
        mock_openai.return_value.images.edit.assert_called_once()
        _, kwargs = mock_openai.return_value.images.edit.call_args
        self.assertEqual(kwargs['model'], 'gpt-image-1')
        self.assertIsInstance(kwargs['image'], tuple)
        self.assertEqual(kwargs['image'][0], 'selfie_nueva.jpg')
        self.assertEqual(kwargs['image'][1], b'foto-nueva')
        self.assertEqual(kwargs['image'][2], 'image/jpeg')
        self.assertIn('Ducky', kwargs['prompt'])

    @patch('apps.users.views.generar_avatar_ducky_con_ia')
    def test_confirmar_avatar_ducky_guarda_imagen_generada(self, mock_generar):
        mock_generar.return_value = b'fake-image-bytes'
        image_file = SimpleUploadedFile(
            'selfie.jpg',
            b'fake-image-bytes',
            content_type='image/jpeg',
        )

        response = self.client.post(
            reverse('users:generar_avatar_ducky'),
            {'avatar': image_file},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['ok'])

        confirm_response = self.client.post(
            reverse('users:confirmar_avatar_ducky'),
            {},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(confirm_response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.avatar.name)


class DashboardDuckyMeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='ducky_user',
            password='test1234',
            first_name='Ducky',
            last_name='User',
            role='alumno',
            puntos=120,
            puntos_totales=350,
            streak=5,
        )
        self.client.force_login(self.user)

    def test_dashboard_ducky_me_muestra_resumen_y_enlaces_reales(self):
        curso = Curso.objects.create(
            nombre='Curso Ducky',
            descripcion='Curso para dashboard',
            fecha_inicio=date.today(),
            horas_duracion=10,
            activo=True,
        )
        tema = curso.temas.create(
            titulo='Tema Ducky',
            contenido='Contenido',
            orden=1,
            puntos_otorgados=20,
        )
        InscripcionCurso.objects.create(usuario=self.user, curso=curso)
        Progreso.objects.create(usuario=self.user, tema=tema, completado=True)

        self.user.actividades.create(
            tipo='tema_completado',
            descripcion='Completaste el tema Ducky',
            puntos_ganados=20,
        )

        response = self.client.get(reverse('users:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ducky Me')
        self.assertContains(response, 'Mi progreso')
        self.assertContains(response, 'Próxima acción')
        self.assertContains(response, 'Explorar mis resultados')
        self.assertContains(response, 'Cursos en marcha')
        self.assertContains(response, 'Última actividad')
        self.assertIn('ducky_summary_cards', response.context)