import base64
import importlib
import json
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.certifications.utils import _build_pdf_buffer
from apps.courses.models import Curso, InscripcionCurso, Progreso, Quiz, ResultadoQuiz, Tema
from apps.gamification.models import Insignia, InsigniaUsuario, InsigniaVersion
from apps.lms.models import CourseMembership
from apps.users.dashboard_models import PersonalGoal
from apps.users.forms import PerfilForm, RegistroForm
from apps.users.models import (
    AccessMembership,
    AccessRoleAssignment,
    Aula,
    Organization,
    OrganizationMembership,
    RamaProfesional,
)
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

    def test_registro_crea_usuario_free_sin_conceder_roles_profesionales(self):
        form = RegistroForm(data={
            'username': 'nuevo_usuario',
            'first_name': 'Nuevo',
            'last_name': 'Usuario',
            'email': 'nuevo@example.com',
            'codigo_acceso': '010819',
            'aula': self.aula.pk,
            'password1': 'ClaveSegura123',
            'password2': 'ClaveSegura123',
        })

        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()

        self.assertEqual(user.role, 'usuario')
        self.assertIsNone(user.aula)
        self.assertEqual(user.access_membership.plan, AccessMembership.Plan.FREE)
        self.assertTrue(user.access_role_assignments.filter(
            role=AccessRoleAssignment.Role.USUARIO, is_active=True,
        ).exists())
        self.assertFalse(user.access_role_assignments.filter(
            role=AccessRoleAssignment.Role.ALUMNO,
        ).exists())

    def test_registro_allows_selecting_only_an_active_branch_without_granting_a_role(self):
        branch = RamaProfesional.objects.get(code='marketing')
        form = RegistroForm(data={
            'username': 'rama_registro',
            'first_name': 'Rama',
            'last_name': 'Registro',
            'email': 'rama@example.com',
            'rama_principal': str(branch.pk),
            'password1': 'ClaveSegura123',
            'password2': 'ClaveSegura123',
        })

        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()

        self.assertEqual(user.rama_principal, branch)
        self.assertEqual(user.role, 'usuario')
        self.assertEqual(user.access_membership.plan, AccessMembership.Plan.FREE)
        self.assertFalse(user.access_role_assignments.filter(
            role__in=[
                AccessRoleAssignment.Role.ADMIN,
                AccessRoleAssignment.Role.ALUMNO,
                AccessRoleAssignment.Role.PROFESOR,
                AccessRoleAssignment.Role.HEADHUNTER,
                AccessRoleAssignment.Role.ACADEMIA,
            ],
        ).exists())

    def test_registration_cannot_select_a_deactivated_branch(self):
        branch = RamaProfesional.objects.get(code='ingles')
        branch.is_active = False
        branch.save(update_fields=['is_active'])
        form = RegistroForm(data={
            'username': 'rama_inactiva',
            'first_name': 'Rama',
            'last_name': 'Inactiva',
            'email': 'inactiva@example.com',
            'rama_principal': str(branch.pk),
            'password1': 'ClaveSegura123',
            'password2': 'ClaveSegura123',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('rama_principal', form.errors)

    def test_codigo_de_acceso_no_asigna_roles_ni_aula_en_registro_publico(self):
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

        self.assertEqual(user.role, 'usuario')
        self.assertIsNone(user.aula_id)


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class LoginActivationTests(TestCase):
    def test_logout_blocks_routes_until_reactivation(self):
        user = get_user_model().objects.create_user(username='logout_activation')
        for authenticated in (False, True):
            with self.subTest(authenticated=authenticated):
                if authenticated:
                    self.client.force_login(user)
                self.client.post(reverse('users:activate_auth_navigation'))
                self.assertTrue(self.client.session['auth_navigation_active'])
                self.assertContains(self.client.get(reverse('landing')), 'data-active="true"')

                response = self.client.post(reverse('account_logout'))
                self.assertEqual(response.status_code, 302)
                self.assertNotIn('auth_navigation_active', self.client.session)
                self.assertNotIn('_auth_user_id', self.client.session)
                self.assertContains(self.client.get(reverse('landing')), 'data-active="false"')
                for route in ('account_login', 'users:register', 'users:registro'):
                    for method in ('get', 'post'):
                        response = getattr(self.client, method)(reverse(route))
                        self.assertRedirects(
                            response, reverse('landing'), fetch_redirect_response=False,
                        )

                self.client.post(reverse('users:activate_auth_navigation'))
                self.assertEqual(self.client.get(reverse('account_login')).status_code, 200)
                self.assertEqual(self.client.get(reverse('users:register')).status_code, 200)

    def test_login_get_and_post_require_activation(self):
        for method in ('get', 'post'):
            with self.subTest(method=method):
                response = getattr(self.client, method)(reverse('account_login'))
                self.assertRedirects(response, reverse('landing'), fetch_redirect_response=False)

    def test_activation_enables_login_for_same_session_only(self):
        response = self.client.post(reverse('users:activate_auth_navigation'))
        self.assertJSONEqual(response.content, {'ok': True})
        self.assertTrue(self.client.session['auth_navigation_active'])
        self.assertEqual(self.client.get(reverse('account_login')).status_code, 200)
        self.assertEqual(self.client.post(reverse('account_login')).status_code, 200)
        self.assertContains(self.client.get(reverse('landing')), 'data-active="true"')
        self.assertRedirects(
            Client().get(reverse('account_login')),
            reverse('landing'),
            fetch_redirect_response=False,
        )

    def test_activation_rejects_get_and_requires_csrf(self):
        endpoint = reverse('users:activate_auth_navigation')
        self.assertEqual(self.client.get(endpoint).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(endpoint).status_code, 403)
        client.get(reverse('landing'))
        token = client.cookies['csrftoken'].value
        self.assertEqual(client.post(endpoint, HTTP_X_CSRFTOKEN=token).status_code, 200)
        self.assertEqual(client.get(reverse('account_login')).status_code, 200)


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class RegistrationAccessTests(TestCase):
    def test_registration_requires_activation_for_both_routes_and_methods(self):
        for route in ('users:registro', 'users:register'):
            for method in ('get', 'post'):
                with self.subTest(route=route, method=method):
                    response = getattr(self.client, method)(reverse(route))
                    self.assertRedirects(response, reverse('landing'), fetch_redirect_response=False)

    def test_activation_enables_registration_in_same_session_only(self):
        self.client.post(reverse('users:activate_auth_navigation'))
        for route in ('users:registro', 'users:register'):
            with self.subTest(route=route):
                self.assertContains(self.client.get(reverse(route)), 'Crear cuenta')
                self.assertContains(self.client.post(reverse(route), {}), 'Crear cuenta')
                self.assertRedirects(
                    Client().get(reverse(route)),
                    reverse('landing'),
                    fetch_redirect_response=False,
                )

    def test_activated_visitor_can_register_without_professional_roles(self):
        self.client.post(reverse('users:activate_auth_navigation'))
        response = self.client.post(reverse('users:register'), {
            'username': 'activated_visitor',
            'first_name': 'Nuevo',
            'last_name': 'Usuario',
            'email': 'activated@example.com',
            'password1': 'ClaveSegura123',
            'password2': 'ClaveSegura123',
        })
        self.assertRedirects(response, reverse('users:dashboard'), fetch_redirect_response=False)
        user = get_user_model().objects.get(username='activated_visitor')
        self.assertEqual(user.role, 'usuario')
        self.assertEqual(user.access_membership.plan, AccessMembership.Plan.FREE)

    def test_authenticated_users_are_redirected_after_activation(self):
        user = get_user_model().objects.create_user(username='already_registered')
        self.client.force_login(user)
        self.client.post(reverse('users:activate_auth_navigation'))
        self.assertRedirects(
            self.client.get(reverse('users:register')),
            reverse('users:dashboard'),
            fetch_redirect_response=False,
        )


class ProfessionalBranchTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='rama_usuario',
            password='test-password',
            role='usuario',
        )
        self.other = get_user_model().objects.create_user(
            username='rama_otro',
            password='test-password',
        )
        self.branch = RamaProfesional.objects.get(code='informatica')

    def test_initial_catalog_seed_is_idempotent(self):
        migration = importlib.import_module(
            'apps.users.migrations.0016_ramaprofesional_user_rama_principal'
        )
        migration.seed_initial_branches(apps, None)
        migration.seed_initial_branches(apps, None)

        expected = {
            'informatica': 'Informática',
            'marketing': 'Marketing',
            'gestion-administrativa': 'Gestión administrativa',
            'ingles': 'Inglés',
        }
        self.assertEqual(RamaProfesional.objects.count(), 4)
        for code, name in expected.items():
            self.assertEqual(
                RamaProfesional.objects.filter(code=code, name=name).count(),
                1,
            )

    def test_existing_branch_code_is_immutable(self):
        self.branch.code = 'informatica-actualizada'
        with self.assertRaisesMessage(ValidationError, 'no se puede cambiar'):
            self.branch.save()

    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_registration_and_my_account_render_accessible_branch_selectors(self):
        self.client.post(reverse('users:activate_auth_navigation'))
        registration = self.client.get(reverse('users:registro'))
        self.assertEqual(registration.status_code, 200)
        self.assertContains(
            registration,
            'for="id_rama_principal"',
        )
        self.assertContains(registration, 'Sin seleccionar')
        self.assertContains(registration, 'Inglés no acredita')

        self.client.force_login(self.user)
        account = self.client.get(reverse('users:editar_perfil'))
        self.assertEqual(account.status_code, 200)
        self.assertContains(account, 'for="id_rama_principal"')
        self.assertContains(account, 'La rama solo personaliza recomendaciones')

    def test_profile_form_can_change_only_own_branch_without_changing_roles_or_plan(self):
        AccessMembership.objects.update_or_create(
            user=self.user,
            defaults={'plan': AccessMembership.Plan.PRO, 'status': AccessMembership.Status.ACTIVE},
        )
        before_roles = list(
            self.user.access_role_assignments.order_by('pk').values_list(
                'role', 'organization_id', 'is_active',
            )
        )
        self.client.force_login(self.user)
        response = self.client.post(reverse('users:editar_perfil'), {
            'first_name': self.user.first_name,
            'last_name': self.user.last_name,
            'email': self.user.email,
            'bio': '',
            'rama_principal': self.branch.pk,
        })

        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.rama_principal, self.branch)
        self.assertEqual(self.user.role, 'usuario')
        self.assertEqual(self.user.access_membership.plan, AccessMembership.Plan.PRO)
        self.assertEqual(
            list(self.user.access_role_assignments.order_by('pk').values_list(
                'role', 'organization_id', 'is_active',
            )),
            before_roles,
        )

    def test_api_changes_only_authenticated_users_branch_and_rejects_inactive_branch(self):
        active_branch = RamaProfesional.objects.get(code='marketing')
        inactive_branch = RamaProfesional.objects.get(code='ingles')
        inactive_branch.is_active = False
        inactive_branch.save(update_fields=['is_active'])
        endpoint = '/api/v1/users/professional-branch/'

        self.client.force_login(self.user)
        response = self.client.patch(
            endpoint,
            data=json.dumps({'rama_principal': active_branch.pk, 'user': self.other.pk}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.user.rama_principal, active_branch)
        self.assertIsNone(self.other.rama_principal_id)

        response = self.client.patch(
            endpoint,
            data=json.dumps({'rama_principal': inactive_branch.pk}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.rama_principal, active_branch)

    def test_public_catalog_api_returns_only_active_public_fields(self):
        inactive_branch = RamaProfesional.objects.get(code='ingles')
        inactive_branch.is_active = False
        inactive_branch.save(update_fields=['is_active'])

        response = self.client.get('/api/v1/professional-branches/')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 3)
        self.assertEqual(set(payload[0]), {'id', 'code', 'name', 'description'})
        self.assertNotIn('ingles', [row['code'] for row in payload])

    def test_inactive_branch_preserves_existing_user_reference_and_cannot_be_reassigned(self):
        self.user.rama_principal = self.branch
        self.user.save(update_fields=['rama_principal'])
        self.branch.is_active = False
        self.branch.save(update_fields=['is_active'])

        self.user.refresh_from_db()
        self.assertEqual(self.user.rama_principal, self.branch)

        self.other.rama_principal = self.branch
        with self.assertRaisesMessage(ValidationError, 'no admite nuevas asignaciones'):
            self.other.full_clean()

    def test_cv_profile_keeps_its_branch_when_user_branch_changes(self):
        from apps.curriculum.forms import CVProfileForm
        from apps.curriculum.models import CVProfile, CurriculumVitae

        other_branch = RamaProfesional.objects.get(code='marketing')
        self.user.rama_principal = self.branch
        self.user.save(update_fields=['rama_principal'])
        initial_form = CVProfileForm(user=self.user)
        self.assertEqual(
            initial_form.initial['rama_principal'],
            self.branch.pk,
        )
        create_page = self.client.get(reverse('curriculum:profile_create'))
        self.assertEqual(create_page.status_code, 302)

        self.client.force_login(self.user)
        create_page = self.client.get(reverse('curriculum:profile_create'))
        self.assertEqual(create_page.status_code, 200)
        self.assertContains(create_page, 'for="id_rama_principal"')
        self.assertContains(create_page, 'no modifica perfiles ya creados')

        form = CVProfileForm(
            data={
                'nombre': 'Perfil de informática',
                'slug': 'perfil-informatica',
                'rama_principal': str(self.branch.pk),
                'templatecv': '',
                'is_public': 'on',
            },
            user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        profile = form.save(commit=False)
        profile.user = self.user
        profile.cv = self.user.curriculum
        profile.save()

        self.user.rama_principal = other_branch
        self.user.save(update_fields=['rama_principal'])

        profile.refresh_from_db()
        self.assertEqual(profile.rama_principal, self.branch)
        self.assertEqual(CVProfile.objects.filter(user=self.user).count(), 1)


class UserSerializerTests(TestCase):
    def test_serializer_expone_role(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username='premium_user',
            password='test1234',
            role='profesor',
        )

        serializer = UserSerializer(user)

        self.assertEqual(serializer.data['role'], 'profesor')


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

    def test_editar_perfil_no_permite_cambiar_rol_ni_aula(self):
        form = PerfilForm(
            data={
                'first_name': 'Perfil',
                'last_name': 'Actualizado',
                'email': 'perfil@example.com',
                'bio': 'Bio actualizada',
                'fecha_nacimiento': '',
                'aula': self.aula.pk,
                'role': 'premium',
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

    def test_ficha_usuario_muestra_plan_en_lugar_de_role(self):
        response = self.client.get(reverse('users:perfil', kwargs={'username': self.user.username}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-label="Plan del perfil FREE"')
        self.assertNotContains(response, 'Rol: Alumno')

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
            status=Insignia.Status.PUBLISHED,
        )
        InsigniaVersion.objects.create(
            badge=insignia,
            version=1,
            rule_type=InsigniaVersion.RuleType.TOPIC_COMPLETED,
            topic=tema,
            threshold=1,
            public_requirements='Completar el tema.',
            source_description='Progreso del curso',
            verification_description='Progreso guardado por servidor',
        )
        self.aula.cursos.add(curso)
        InscripcionCurso.objects.create(usuario=self.user, curso=curso)
        Progreso.objects.create(usuario=self.user, tema=tema, completado=True)

        response = self.client.get(reverse('users:perfil', kwargs={'username': self.user.username}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Insignias (1 / 1)')
        self.assertTrue(InsigniaUsuario.objects.filter(
            usuario=self.user,
            insignia=insignia,
            status=InsigniaUsuario.Status.VALID,
        ).exists())
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

    def test_visitor_sees_only_labeled_demo_without_private_account_data(self):
        Curso.objects.create(
            nombre='Curso privado no debe salir',
            descripcion='Privado',
            fecha_inicio=date.today(),
        )
        self.user.actividades.create(tipo='acceso', descripcion='Actividad privada')
        self.client.logout()

        response = self.client.get(reverse('users:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Vista de demostración')
        self.assertContains(response, 'Dato ficticio de demostración')
        self.assertNotContains(response, 'Curso privado no debe salir')
        self.assertNotContains(response, 'Actividad privada')
        self.assertTrue(response.context['is_demo'])

    def test_free_dashboard_does_not_expose_professional_scope_or_fake_balances(self):
        response = self.client.get(reverse('users:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['source_statuses']['teacher_courses'], 'not_authorized')
        self.assertEqual(response.context['source_statuses']['ducky_bank'], 'not_implemented')
        self.assertNotContains(response, 'Ducky Coins')
        self.assertFalse(any(card['title'] == 'Ranking' for card in response.context['ducky_summary_cards']))

    def test_pro_without_professional_role_does_not_get_teacher_panel(self):
        self.user.access_membership.plan = AccessMembership.Plan.PRO
        self.user.access_membership.billing_period = AccessMembership.BillingPeriod.MONTHLY
        self.user.access_membership.save(update_fields=['plan', 'billing_period', 'updated_at'])

        response = self.client.get(reverse('users:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_pro'])
        self.assertFalse(response.context['is_teacher'])
        self.assertNotContains(response, 'Cursos con asignación docente')

    def test_scoped_teacher_role_shows_only_own_active_course_memberships(self):
        organization = Organization.objects.create(
            name='Academia docente dashboard',
            kind=Organization.Kind.ACADEMY,
        )
        OrganizationMembership.objects.create(
            organization=organization,
            user=self.user,
            status=OrganizationMembership.Status.ACTIVE,
        )
        AccessRoleAssignment.objects.create(
            user=self.user,
            role=AccessRoleAssignment.Role.PROFESOR,
            organization=organization,
        )
        course = Curso.objects.create(
            nombre='Curso asignado al profesor',
            descripcion='Curso de prueba',
            fecha_inicio=date.today(),
            activo=True,
        )
        CourseMembership.objects.create(
            course=course,
            user=self.user,
            role=CourseMembership.Role.TEACHER,
            status=CourseMembership.Status.ACTIVE,
        )

        response = self.client.get(reverse('users:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_teacher'])
        self.assertEqual(
            [membership.course_id for membership in response.context['teacher_courses']],
            [course.pk],
        )


class PersonalGoalDashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='goal_owner',
            password='test1234',
        )
        self.user.access_membership.plan = AccessMembership.Plan.PRO
        self.user.access_membership.billing_period = AccessMembership.BillingPeriod.MONTHLY
        self.user.access_membership.save(update_fields=['plan', 'billing_period', 'updated_at'])
        self.client.force_login(self.user)

    def test_pro_can_create_edit_complete_and_export_owned_goal(self):
        response = self.client.post(reverse('users:goal_create'), {
            'title': 'Practicar Django',
            'description': 'Meta personal',
            'tracking_type': PersonalGoal.TrackingType.MANUAL,
            'current_value': '1',
            'target_value': '4',
            'target_label': 'sesiones',
        })
        self.assertEqual(response.status_code, 302)
        goal = PersonalGoal.objects.get(user=self.user, title='Practicar Django')

        response = self.client.post(reverse('users:goal_update', args=[goal.pk]), {
            'title': 'Practicar Django cada semana',
            'description': 'Meta personal actualizada',
            'tracking_type': PersonalGoal.TrackingType.MANUAL,
            'current_value': '2',
            'target_value': '4',
            'target_label': 'sesiones',
        })
        self.assertEqual(response.status_code, 302)
        goal.refresh_from_db()
        self.assertEqual(goal.title, 'Practicar Django cada semana')

        response = self.client.get(reverse('users:goal_export'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('Practicar Django cada semana', response.content.decode('utf-8-sig'))

        response = self.client.post(reverse('users:goal_state', args=[goal.pk, 'complete']))
        self.assertEqual(response.status_code, 302)
        goal.refresh_from_db()
        self.assertEqual(goal.status, PersonalGoal.Status.COMPLETED)
        self.assertIsNotNone(goal.completed_at)

    def test_automatic_goal_uses_owner_activity_records(self):
        self.user.actividades.create(tipo='acceso', descripcion='Actividad 1')
        self.user.actividades.create(tipo='acceso', descripcion='Actividad 2')
        goal = PersonalGoal.objects.create(
            user=self.user,
            title='Registrar cuatro actividades',
            tracking_type=PersonalGoal.TrackingType.AUTOMATIC,
            source_metric=PersonalGoal.SourceMetric.ACTIVITY_ENTRIES,
            target_value=4,
        )

        response = self.client.get(reverse('users:dashboard'))

        self.assertEqual(response.status_code, 200)
        matching = next(item for item in response.context['goals'] if item['goal'].pk == goal.pk)
        self.assertEqual(matching['progress']['current'], 2)
        self.assertEqual(matching['progress']['percent'], 50)

    def test_downgrade_preserves_goal_and_removes_management_and_export(self):
        goal = PersonalGoal.objects.create(
            user=self.user,
            title='Objetivo conservado',
            tracking_type=PersonalGoal.TrackingType.MANUAL,
            current_value=1,
            target_value=3,
        )
        self.user.access_membership.plan = AccessMembership.Plan.FREE
        self.user.access_membership.billing_period = ''
        self.user.access_membership.save(update_fields=['plan', 'billing_period', 'updated_at'])

        response = self.client.get(reverse('users:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Objetivo conservado')
        self.assertFalse(response.context['can_manage_goals'])
        self.assertEqual(
            self.client.get(reverse('users:goal_export')).status_code,
            403,
        )
        self.assertTrue(PersonalGoal.objects.filter(pk=goal.pk, user=self.user).exists())

    def test_free_cannot_create_or_access_another_users_goal(self):
        other_user = get_user_model().objects.create_user(
            username='other_goal_owner',
            password='test1234',
        )
        other_user.access_membership.plan = AccessMembership.Plan.PRO
        other_user.access_membership.billing_period = AccessMembership.BillingPeriod.MONTHLY
        other_user.access_membership.save(update_fields=['plan', 'billing_period', 'updated_at'])
        other_goal = PersonalGoal.objects.create(
            user=other_user,
            title='Privado de otra persona',
            tracking_type=PersonalGoal.TrackingType.MANUAL,
            current_value=0,
            target_value=2,
        )

        self.user.access_membership.plan = AccessMembership.Plan.FREE
        self.user.access_membership.billing_period = ''
        self.user.access_membership.save(update_fields=['plan', 'billing_period', 'updated_at'])

        self.assertEqual(self.client.get(reverse('users:goal_update', args=[other_goal.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('users:goal_create'), {'title': 'No'}).status_code, 403)


class PersonalProgressViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='progress_owner',
            password='test1234',
        )
        self.client.force_login(self.user)

    def _course_with_topic(self, course_name, topic_name):
        course = Curso.objects.create(
            nombre=course_name,
            descripcion='Curso para probar progreso',
            fecha_inicio=date.today(),
            activo=True,
        )
        topic = Tema.objects.create(
            curso=course,
            titulo=topic_name,
            contenido='Contenido',
        )
        return course, topic

    def test_progress_is_personal_and_reports_available_activity(self):
        course, topic = self._course_with_topic('Curso propio', 'Tema propio')
        Progreso.objects.create(
            usuario=self.user,
            tema=topic,
            completado=True,
            fecha_completado=timezone.now(),
        )
        InscripcionCurso.objects.create(
            usuario=self.user,
            curso=course,
            completado=True,
            fecha_completado=timezone.now(),
        )

        other = get_user_model().objects.create_user(username='progress_other')
        private_course, private_topic = self._course_with_topic('Curso privado ajeno', 'Tema privado')
        Progreso.objects.create(
            usuario=other,
            tema=private_topic,
            completado=True,
            fecha_completado=timezone.now(),
        )
        InscripcionCurso.objects.create(
            usuario=other,
            curso=private_course,
            completado=True,
            fecha_completado=timezone.now(),
        )

        response = self.client.get(reverse('users:progress'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['summary']['completed_lessons'], 1)
        self.assertEqual(response.context['summary']['completed_courses'], 1)
        self.assertContains(response, 'Tema propio')
        self.assertNotContains(response, 'Curso privado ajeno')
        self.assertNotContains(response, 'Tema privado')

    def test_progress_date_filter_and_pro_only_export_and_comparison(self):
        course, topic = self._course_with_topic('Curso filtrado', 'Tema filtrado')
        Progreso.objects.create(
            usuario=self.user,
            tema=topic,
            completado=True,
            fecha_completado=timezone.now() - timedelta(days=10),
        )
        today = timezone.localdate()

        response = self.client.get(reverse('users:progress'), {
            'from': today.isoformat(),
            'to': today.isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['summary']['completed_lessons'], 0)
        self.assertEqual(self.client.get(reverse('users:progress_export')).status_code, 403)
        self.assertEqual(self.client.get(reverse('users:progress'), {
            'from': today.isoformat(),
            'to': today.isoformat(),
            'compare': '1',
        }).status_code, 403)

        self.user.access_membership.plan = AccessMembership.Plan.PRO
        self.user.access_membership.billing_period = AccessMembership.BillingPeriod.MONTHLY
        self.user.access_membership.save(update_fields=['plan', 'billing_period', 'updated_at'])

        export = self.client.get(reverse('users:progress_export'))
        self.assertEqual(export.status_code, 200)
        self.assertIn('Tema filtrado', export.content.decode('utf-8-sig'))

        comparison = self.client.get(reverse('users:progress'), {
            'from': today.isoformat(),
            'to': today.isoformat(),
            'compare': '1',
        })
        self.assertEqual(comparison.status_code, 200)
        self.assertIsNotNone(comparison.context['comparison'])

    def test_visitor_gets_demo_without_persisted_personal_progress(self):
        self.client.logout()

        response = self.client.get(reverse('users:progress'))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_demo'])
        self.assertEqual(response.context['rows'], [])
        self.assertIsNone(response.context['summary'])