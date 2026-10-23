from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from threading import Barrier

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone, translation
from django.template import Context, Engine
from django.templatetags.static import static
from django.test import SimpleTestCase

from apps.courses.models import CourseCategory, Curso, InscripcionCurso, Progreso, Tema
from apps.evaluations.models import Evaluacion
from apps.users.models import Aula


class CourseSlugFormTests(TestCase):
    def make_form(self, **kwargs):
        from .forms import CourseForm

        data = {
            'nombre': 'Introducción   a Python',
            'descripcion': 'Contenido del curso.',
            'fecha_inicio': '2026-10-13',
            **kwargs.pop('data', {}),
        }
        return CourseForm(data=data, **kwargs)

    def test_generates_slug_when_empty_without_javascript(self):
        form = self.make_form()
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).slug, 'introduccion-a-python')

    def test_preserves_manual_slug(self):
        form = self.make_form(data={'slug': 'mi-aula-personalizada'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).slug, 'mi-aula-personalizada')

    def test_editing_name_preserves_existing_slug(self):
        course = Curso.objects.create(
            nombre='Curso original',
            slug='curso-original',
            descripcion='Contenido original.',
            fecha_inicio=date(2026, 10, 13),
        )
        form = self.make_form(instance=course)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).slug, 'curso-original')

    def test_generated_slug_is_checked_for_duplicates(self):
        Curso.objects.create(
            nombre='Introducción a Python',
            descripcion='Contenido original.',
            fecha_inicio=date(2026, 10, 13),
        )
        form = self.make_form()
        self.assertFalse(form.is_valid())
        self.assertIn('slug', form.errors)


class CourseDetailTemplateTests(SimpleTestCase):
    def test_homepage_is_visible_by_default_for_both_roles(self):
        from bs4 import BeautifulSoup

        for can_edit in (False, True):
            for home_page in (None, {'title': 'Portada', 'slug': 'portada', 'content': 'Contenido de portada'}):
                with self.subTest(can_edit=can_edit, custom_page=bool(home_page)):
                    html = self.template.render(Context({
                        'course': self.course, 'can_edit': can_edit, 'home_page': home_page,
                    }))
                    panel = BeautifulSoup(html, 'html.parser').select_one('section.home-preview-panel')
                    self.assertIsNotNone(panel)
                    self.assertIsNotNone(panel.select_one('.page-body'))
                    self.assertIsNotNone(panel.select_one('.action-row a'))
                    self.assertIsNotNone(BeautifulSoup(html, 'html.parser').select_one('.course-home-sidebar'))

    def setUp(self):
        engine = Engine(
            dirs=[settings.BASE_DIR / 'apps' / 'lms' / 'templates'],
            loaders=[
                ('django.template.loaders.locmem.Loader', {
                    'lms/base.html': '{% block content %}{% endblock %}',
                }),
                'django.template.loaders.filesystem.Loader',
            ],
            libraries={
                'i18n': 'django.templatetags.i18n',
                'static': 'django.templatetags.static',
            },
        )
        self.template = engine.get_template('lms/course_detail.html')
        self.area_template = engine.get_template('lms/course_area.html')
        self.navbar_template = engine.get_template('lms/navbar.html')
        self.course = {'pk': 7, 'nombre': 'Course', 'descripcion': '', 'resumen': ''}

    def test_course_status_labels(self):
        for archived, active, label, css_class in [
            (True, True, 'Archivado', 'status-draft'),
            (True, False, 'Archivado', 'status-draft'),
            (False, True, 'Publicado', 'status-published'),
            (False, False, 'Borrador', 'status-draft'),
        ]:
            with self.subTest(archived=archived, active=active):
                with translation.override('es'):
                    html = self.template.render(Context({
                        'course': {**self.course, 'activo': active},
                        'course_archived': archived,
                    }))
                self.assertIn(f'class="status {css_class}">{label}</span>', html)
                self.assertNotIn('{%', html)
                self.assertNotIn('{{', html)

    def test_shared_navbar_on_home_and_course_areas(self):
                areas = [
                    ('home', 'Inicio', 'bi-house'),
                    ('announcements', 'Anuncios', 'bi-megaphone'),
                    ('settings', 'Configuraciones', 'bi-gear'),
                ]
                for template, active_key in [
                    (self.template, 'home'),
                    (self.area_template, 'announcements'),
                    (self.area_template, 'settings'),
                ]:
                    with self.subTest(active_key=active_key):
                        html = template.render(Context({
                            'course': self.course,
                            'course_areas': areas,
                            'teacher_view': True,
                            'area_key': active_key,
                        }))
                        self.assertEqual(html.count('class="course-nav-panel"'), 1)
                        self.assertEqual(html.count('class="course-nav-link is-active"'), 1)
                        self.assertEqual(html.count('aria-current="page"'), 2)
                        self.assertIn('aria-label="Configuraciones"', html)
                        self.assertIn('class="bi bi-gear"', html)
                        for key, _label, _icon in areas:
                            url = reverse(
                                'lms:course_detail' if key == 'home' else 'lms:course_area',
                                kwargs={'course_id': 7} if key == 'home' else {
                                    'course_id': 7, 'area': key,
                                },
                            )
                            self.assertIn(f'href="{url}"', html)

    def test_shared_navbar_student_permissions_and_pending_announcements(self):
                with translation.override('es'):
                    html = self.navbar_template.render(Context({
                        'course': self.course,
                        'course_areas': [
                            ('home', 'Inicio', 'bi-house'),
                            ('announcements', 'Anuncios', 'bi-megaphone'),
                            ('settings', 'Configuraciones', 'bi-gear'),
                        ],
                        'student_area_keys': ['home', 'announcements'],
                        'announcements_pending_count': 2,
                        'request': {'resolver_match': {'kwargs': {'area': 'announcements'}}},
                    }))
                self.assertNotIn('Configuraciones', html)
                self.assertIn('aria-label="2 anuncios pendientes">2</span>', html)
                self.assertIn('class="course-nav-link is-active"', html)
                self.assertEqual(html.count('aria-current="page"'), 1)
                self.assertIn('<small class="nav-label" aria-hidden="true">Anuncios</small>', html)

    def test_conditional_content_renders_without_literal_template_tags(self):
        with translation.override('es'):
            html = self.template.render(Context({
                'course': self.course,
                'membership': {
                    'status': 'active', 'get_role_display': 'Alumno',
                    'section': {'title': 'Grupo A'},
                },
                'can_edit': True,
                'home_page': {'title': 'Portada', 'slug': 'portada', 'content': 'Hola'},
                'course_areas': [('home', 'Inicio', 'bi-house')],
                'student_area_keys': ['home'],
                'course_steps': [{'state': 'done', 'label': 'Paso terminado'}],
                'course_timeline': [
                    {'is_complete': True, 'topic': {'pk': 1, 'titulo': 'Tema uno'}},
                    {'index': 2, 'topic': {'pk': 2, 'titulo': 'Tema dos'}},
                ],
                'upcoming_evaluations': [
                    {'title': 'Entrega pendiente', 'state': 'draft', 'published': False},
                ],
            }))
        for text in ['Grupo A', 'Editar aula', 'Ver página', 'Continuar aprendiendo',
                     'COMPLETADO', 'SIGUIENTE 2', 'Entrega pendiente', 'Borrador',
                     'Consultar el programa', 'aria-current="page"']:
            self.assertIn(text, html)
        self.assertNotIn('{%', html)
        self.assertNotIn('{{', html)

    def test_navbar_shows_exact_student_menu(self):
        from .ui import COURSE_AREAS, STUDENT_AREA_KEYS

        student_keys = [
            'home', 'announcements', 'content', 'discussion', 'grades',
            'people', 'pages', 'syllabus', 'quizzes', 'activities', 'files', 'competencies',
        ]
        self.assertEqual(STUDENT_AREA_KEYS, frozenset(student_keys))
        for teacher_view in [False, True]:
            with self.subTest(teacher_view=teacher_view):
                with translation.override('es'):
                    html = self.navbar_template.render(Context({
                        'course': self.course,
                        'course_areas': COURSE_AREAS,
                        'student_area_keys': STUDENT_AREA_KEYS,
                        'teacher_view': teacher_view,
                        'announcements_pending_count': 7,
                        'course_notification_counts': {} if teacher_view else {'announcements': 7},
                    }))
                expected_keys = (
                    [key for key, _label, _icon in COURSE_AREAS]
                    if teacher_view else student_keys
                )
                self.assertEqual(html.count('class="course-nav-link '), len(expected_keys))
                for key, label, _icon in COURSE_AREAS:
                    route = (
                        'lms:course_detail' if key == 'home' else
                        'lms:course_pages' if key == 'pages' else 'lms:course_area'
                    )
                    url = reverse(
                        route,
                        kwargs={'course_id': 7} if key in ('home', 'pages') else {
                            'course_id': 7, 'area': key,
                        },
                    )
                    if key in expected_keys:
                        self.assertIn(f'href="{url}"', html)
                        self.assertIn(f'aria-label="{label}"', html)
                    else:
                        self.assertNotIn(f'href="{url}"', html)
                if teacher_view:
                    self.assertNotIn('class="nav-count"', html)
                else:
                    self.assertIn('aria-label="Avisos pendientes: 7">7</span>', html)

    def test_topic_delete_button_is_only_visible_to_teachers(self):
        for teacher_view in [False, True]:
            with self.subTest(teacher_view=teacher_view):
                html = self.area_template.render(Context({
                    'course': self.course,
                    'area_key': 'content',
                    'teacher_view': teacher_view,
                    'topics': [{'pk': 1, 'titulo': 'Tema', 'contenido': 'Texto'}],
                }))
                url = reverse('lms:course_topic_delete', kwargs={
                    'course_id': 7, 'topic_id': 1,
                })
                if teacher_view:
                    self.assertIn(f'action="{url}"', html)
                    self.assertIn('onsubmit="return confirm(', html)
                else:
                    self.assertNotIn(f'action="{url}"', html)

from .models import (
    AchievementRule,
    Announcement,
    AnnouncementAcknowledgement,
    Assignment,
    AuditEvent,
    Badge,
    CertificateVerification,
    Competency,
    CompetencyAchievement,
    CompetencyFramework,
    CourseCertificate,
    CourseCompetency,
    CourseGroup,
    CourseMembership,
    CourseModule,
    CourseSettingsLMS,
    DiscussionPost,
    Evidence,
    GroupMembership,
    GroupProject,
    GroupSubmission,
    DiscussionTag,
    DiscussionTopic,
    FileAsset,
    FileAssetLink,
    LearningActivity,
    LearningEvent,
    LearningProgress,
    ModuleItem,
    ModuleProgress,
    Notification,
    NotificationEvent,
    NotificationOutbox,
    NotificationPreference,
    Page,
    PageRevision,
    GradeEntry,
    CalendarEvent,
    CodeExercise,
    CodeExecutionResult,
    CodeSubmission,
    Question,
    QuestionAttempt,
    QuestionBank,
    QuestionTag,
    QuestionVersion,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizQuestion,
    Rubric,
    RubricAssessment,
    RubricCriterion,
    RubricLevel,
    Section,
    Submission,
    SubmissionAttachment,
    UserBadge,
    UserProfileLMS,
)
from .policies import can_edit_course, is_course_member, is_platform_admin
from .services import (
    AIProvider,
    AIProviderConfigurationError,
    AIRequestRejected,
    CodeExerciseService,
    CourseCompletionService,
    DisabledAIProvider,
    EnrollmentUnavailable,
    SandboxUnavailableError,
    assign_membership_to_section,
    build_NoTodoEsCódigo_structure,
    create_course,
    enroll_student,
    get_active_ai_provider,
    get_or_create_lms_profile,
    import_course_members_csv,
    invite_users_to_course,
    publish_course,
    queue_notification_event,
    set_membership_status,
    update_course,
)
from .ui import COURSE_AREAS, course_nav, due_state


User = get_user_model()


def make_course(name='Curso'):
    return Curso.objects.create(
        nombre=name,
        descripcion=f'Descripción de {name}',
        fecha_inicio=date(2025, 1, 1),
    )


class LmsModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('learner', password='password')
        self.other_user = User.objects.create_user('another', password='password')
        self.course = make_course('Fundamentos')
        self.section = Section.objects.create(course=self.course, title='Grupo A')

    def test_lms_profile_is_one_to_one_and_idempotent(self):
        first = get_or_create_lms_profile(self.user)
        second = get_or_create_lms_profile(self.user)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(self.user.lms_profile, first)
        self.assertEqual(first.preferred_language, 'es')
        self.assertEqual(first.time_zone, '')
        self.assertEqual(UserProfileLMS.objects.filter(user=self.user).count(), 1)

    def test_profile_validates_timezone_and_accessibility_data(self):
        profile = get_or_create_lms_profile(self.user)
        profile.time_zone = 'Not/A_Timezone'
        with self.assertRaises(ValidationError):
            profile.full_clean()
        profile.time_zone = 'Europe/Madrid'
        profile.accessibility_preferences = []
        with self.assertRaises(ValidationError):
            profile.full_clean()

    def test_membership_rejects_a_section_from_another_course(self):
        other_course = make_course('Otro')
        membership = CourseMembership(
            course=other_course,
            user=self.user,
            section=self.section,
        )
        with self.assertRaises(ValidationError):
            membership.full_clean()

    def test_membership_is_unique_per_user_and_course(self):
        CourseMembership.objects.create(
            course=self.course,
            user=self.user,
            role=CourseMembership.Role.TEACHER,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CourseMembership.objects.create(course=self.course, user=self.user)

    def test_course_settings_preserve_enrollment_and_legacy_url_metadata(self):
        settings = CourseSettingsLMS.objects.create(
            course=self.course,
            summary='Resumen heredado',
            legacy_slug='fundamentos',
            enrollment_open=False,
            archived=True,
        )
        self.assertEqual(self.course.lms_settings, settings)


class LmsPrivacyAnalyticsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('analytics_student', password='password')
        self.course = make_course('Analytics course')
        self.module = CourseModule.objects.create(
            course=self.course,
            title='Módulo de prueba',
            slug='modulo-prueba',
            position=1,
            status=CourseModule.Status.PUBLISHED,
        )
        CourseMembership.objects.create(
            course=self.course,
            user=self.user,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )

    def test_learning_event_keeps_context_minimal_and_rejects_sensitive_input(self):
        event = LearningEvent(
            user=self.user,
            course=self.course,
            object_type='module',
            object_id=self.module.pk,
            event_type=LearningEvent.EventType.VIEW,
            context={'module_id': self.module.pk, 'status': 'started', 'duration_seconds': 12},
        )
        event.full_clean()
        event.save()

        with self.assertRaises(ValidationError):
            invalid = LearningEvent(
                user=self.user,
                course=self.course,
                object_type='module',
                object_id=self.module.pk,
                event_type=LearningEvent.EventType.VIEW,
                context={'answer_text': 'Esto no debe registrarse'},
            )
            invalid.full_clean()

    def test_module_and_course_progress_aggregate_without_personal_content(self):
        ModuleProgress.objects.create(
            user=self.user,
            course=self.course,
            module=self.module,
            completed_items=3,
            total_items=4,
            percentage=75,
            status=ModuleProgress.Status.IN_PROGRESS,
        )
        progress = LearningProgress.objects.create(
            user=self.user,
            course=self.course,
            modules_completed=1,
            total_modules=2,
            completion_percentage=75,
        )

        self.assertEqual(
            ModuleProgress.objects.filter(user=self.user, course=self.course).get().percentage,
            75,
        )
        self.assertEqual(progress.completion_percentage, 50)
        self.assertIsNotNone(progress.computed_at)


class LmsCodeExerciseTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('teacher_code', password='password')
        self.student = User.objects.create_user('student_code', password='password')
        self.course = make_course('Laboratorio de código')
        self.module = CourseModule.objects.create(
            course=self.course,
            title='Módulo de programación',
            slug='modulo-programacion',
            position=1,
            status=CourseModule.Status.PUBLISHED,
        )
        CourseMembership.objects.create(
            course=self.course,
            user=self.teacher,
            role=CourseMembership.Role.TEACHER,
            status=CourseMembership.Status.ACTIVE,
        )
        CourseMembership.objects.create(
            course=self.course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )

    def test_code_exercise_requires_sandbox_before_run(self):
        exercise = CodeExercise.objects.create(
            course=self.course,
            module=self.module,
            title='Hola mundo',
            language=CodeExercise.Language.PYTHON,
            starter_code='print("hola")',
            public_tests=[{'name': 'smoke', 'description': 'imprime valor esperado'}],
            hidden_tests=[{'name': 'secret_hidden', 'expected': 'hola'}],
            max_attempts=2,
            status=CodeExercise.Status.PUBLISHED,
            cpu_limit_ms=500,
            memory_limit_mb=128,
            timeout_seconds=10,
        )
        submission = CodeSubmission.objects.create(
            exercise=exercise,
            user=self.student,
            source_code='print("hola")',
            attempt_number=1,
        )

        with self.assertRaises(SandboxUnavailableError):
            CodeExerciseService.run_submission(submission)

    def test_course_exercise_examples_do_not_expose_hidden_tests(self):
        items = CodeExerciseService.create_demo_exercises(
            course=self.course,
            module=self.module,
            created_by=self.teacher,
        )
        self.assertTrue(items)
        public_payload = items[0].to_public_summary()
        self.assertIn('public_tests', public_payload)
        self.assertNotIn('hidden_tests', public_payload)
        self.assertTrue(all('expected' not in test for test in public_payload['public_tests']))

    def test_submission_rejects_excess_attempts(self):
        exercise = CodeExercise.objects.create(
            course=self.course,
            module=self.module,
            title='Variables',
            language=CodeExercise.Language.JAVASCRIPT,
            starter_code='const x = 1;\nconsole.log(x)',
            public_tests=[{'name': 'smoke'}],
            hidden_tests=[{'name': 'secret'}],
            max_attempts=1,
            status=CodeExercise.Status.PUBLISHED,
            cpu_limit_ms=500,
            memory_limit_mb=128,
            timeout_seconds=10,
        )
        CodeSubmission.objects.create(
            exercise=exercise,
            user=self.student,
            source_code='const x = 1; console.log(x)',
            attempt_number=1,
            status=CodeSubmission.Status.SUBMITTED,
        )
        with self.assertRaises(ValueError):
            CodeExerciseService.create_submission(
                exercise=exercise,
                user=self.student,
                source_code='const x = 2; console.log(x)',
            )


class LmsCompletionAndCertificateTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user('completion_student', password='password')
        self.teacher = User.objects.create_user('completion_teacher', password='password')
        self.course = make_course('Curso final')
        self.module = CourseModule.objects.create(
            course=self.course,
            title='Módulo final',
            slug='modulo-final',
            position=1,
            status=CourseModule.Status.PUBLISHED,
        )
        CourseMembership.objects.create(
            course=self.course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )
        self.quiz = Quiz.objects.create(
            course=self.course,
            title='Quiz final',
            status=Quiz.Status.PUBLISHED,
            passing_score=Decimal('70'),
            max_attempts=1,
        )
        self.quiz_attempt = QuizAttempt.objects.create(
            quiz=self.quiz,
            user=self.student,
            status=QuizAttempt.Status.GRADED,
            score=Decimal('90'),
            is_passed=True,
        )
        self.assignment = Assignment.objects.create(
            course=self.course,
            created_by=self.teacher,
            title='Práctica final',
            slug='practica-final',
            instructions='Entregar la práctica antes del cierre.',
            points=20,
            status=Assignment.Status.PUBLISHED,
            submission_types=['text'],
            available_at=timezone.now(),
            due_at=timezone.now() + timedelta(days=5),
        )
        self.submission = Submission.objects.create(
            assignment=self.assignment,
            user=self.student,
            version=1,
            status=Submission.Status.GRADED,
            grade=Decimal('9.0'),
            receipt_identifier='receipt-1',
        )
        ModuleProgress.objects.create(
            user=self.student,
            course=self.course,
            module=self.module,
            completed_items=5,
            total_items=5,
            percentage=100,
            status=ModuleProgress.Status.COMPLETED,
        )
        self.badge = Badge.objects.create(
            course=self.course,
            slug='curso-completado',
            name='Curso completado',
            description='Finaliza el curso y demuestra dominio.',
            icon='medal',
            color='#1f6feb',
        )
        AchievementRule.objects.create(
            badge=self.badge,
            course=self.course,
            rule_type=AchievementRule.RuleType.MODULE_COMPLETION,
            threshold=1,
            is_active=True,
        )
        AchievementRule.objects.create(
            badge=self.badge,
            course=self.course,
            rule_type=AchievementRule.RuleType.FINAL_QUIZ_PASS,
            threshold=1,
            is_active=True,
        )
        AchievementRule.objects.create(
            badge=self.badge,
            course=self.course,
            rule_type=AchievementRule.RuleType.ASSIGNMENT_SUBMITTED,
            threshold=1,
            is_active=True,
        )

    def test_completion_service_marks_course_complete_and_issues_certificate(self):
        result = CourseCompletionService.evaluate_course_completion(self.student, self.course)

        self.assertTrue(result['is_complete'])
        self.assertTrue(result['module_completion'])
        self.assertTrue(result['final_quiz_passed'])
        self.assertTrue(result['assignment_submitted'])

        certificate = CourseCompletionService.issue_certificate_for_course(self.student, self.course, issued_by=self.teacher)
        self.assertIsNotNone(certificate)
        self.assertTrue(CourseCertificate.objects.filter(user=self.student, course=self.course, status=CourseCertificate.Status.ISSUED).exists())
        self.assertTrue(UserBadge.objects.filter(user=self.student, badge=self.badge).exists())

    def test_verification_is_idempotent_and_revocation_marks_certificate_invalid(self):
        certificate = CourseCompletionService.issue_certificate_for_course(self.student, self.course, issued_by=self.teacher)

        first_verification = CourseCompletionService.verify_certificate(certificate.certificate_code)
        self.assertTrue(first_verification['valid'])
        self.assertEqual(CertificateVerification.objects.filter(certificate=certificate).count(), 1)

        certificate.revoke(reason='Prueba de revocación de certificado')
        second_verification = CourseCompletionService.verify_certificate(certificate.certificate_code)
        self.assertFalse(second_verification['valid'])
        self.assertIn('revoked', second_verification['reason'].lower())


class LmsCourseEditorModeTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('teacher_editor', password='password')
        self.student = User.objects.create_user('student_editor', password='password')
        self.course = make_course('Curso editor')
        CourseMembership.objects.create(
            course=self.course,
            user=self.teacher,
            role=CourseMembership.Role.TEACHER,
            status=CourseMembership.Status.ACTIVE,
        )
        CourseModule.objects.create(course=self.course, title='Módulo 1', slug='modulo-1', position=1)
        CourseModule.objects.create(course=self.course, title='Módulo 2', slug='modulo-2', position=2)
        CourseModule.objects.create(course=self.course, title='Módulo 3', slug='modulo-3', position=3)

    def test_teacher_course_editor_and_preview_are_available_and_restricted(self):
        client = Client()
        client.force_login(self.teacher)

        response = client.get(reverse('lms:course_edit', kwargs={'course_id': self.course.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Guardar borrador')
        self.assertContains(response, 'Vista previa')
        self.assertContains(response, 'Reordenar actividades')

        preview = client.get(reverse('lms:course_preview', kwargs={'course_id': self.course.pk}))
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, 'Vista previa del alumno')

        client.force_login(self.student)
        blocked = client.get(reverse('lms:course_edit', kwargs={'course_id': self.course.pk}))
        self.assertEqual(blocked.status_code, 403)

    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_settings_details_save_and_audit(self):
        self.client.force_login(self.teacher)
        url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'settings'})
        response = self.client.get(url)
        self.assertContains(response, 'Detalles de la asignatura')
        self.assertContains(response, 'Actualizar detalles de la asignatura')
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, f'value="{self.course.fecha_inicio.isoformat()}"')
        self.assertNotContains(response, 'Esta sección aún no está habilitada')
        self.assertEqual(response.context['settings_teacher_count'], 1)
        data = {
            name: value if value is not None else ''
            for name, value in response.context['settings_form'].initial.items()
            if name != 'imagen'
        }
        data['nombre'] = 'Curso actualizado desde configuraciones'
        self.assertRedirects(self.client.post(url, data), url)
        self.course.refresh_from_db()
        self.assertEqual(self.course.nombre, data['nombre'])
        self.assertTrue(AuditEvent.objects.filter(
            course=self.course, actor=self.teacher, action=AuditEvent.Action.COURSE_UPDATED,
        ).exists())

    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_settings_invalid_dates_preserve_course(self):
        self.client.force_login(self.teacher)
        url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'settings'})
        response = self.client.post(url, {
            'nombre': 'No guardar', 'descripcion': 'Descripción',
            'fecha_inicio': '2026-10-13', 'fecha_fin': '2026-10-12',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('fecha_fin', response.context['settings_form'].errors)
        self.assertContains(response, 'No guardar')
        self.course.refresh_from_db()
        self.assertEqual(self.course.nombre, 'Curso editor')

    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_settings_reject_invalid_image(self):
        self.client.force_login(self.teacher)
        url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'settings'})
        response = self.client.post(url, {
            'nombre': 'No guardar imagen', 'descripcion': 'Descripción',
            'fecha_inicio': '2026-10-13',
            'imagen': SimpleUploadedFile('imagen.png', b'not an image', content_type='image/png'),
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('imagen', response.context['settings_form'].errors)
        self.course.refresh_from_db()
        self.assertFalse(self.course.imagen)
        self.assertEqual(self.course.nombre, 'Curso editor')

    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_settings_sections_and_permissions(self):
        self.client.force_login(self.teacher)
        url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'settings'})
        Section.objects.create(course=self.course, title='Grupo configuraciones')
        response = self.client.get(url, {'tab': 'sections'})
        self.assertContains(response, 'Grupo configuraciones')
        self.assertNotContains(response, 'Actualizar detalles de la asignatura')
        CourseMembership.objects.create(
            course=self.course, user=self.student, role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {'nombre': 'Cambio sin permiso'}).status_code, 403)


class LmsDiscussionForumTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('teacher_forum', password='password')
        self.student = User.objects.create_user('student_forum', password='password')
        self.course = make_course('Foro curso')
        self.section = Section.objects.create(course=self.course, title='Grupo A')
        CourseMembership.objects.create(
            course=self.course,
            user=self.teacher,
            role=CourseMembership.Role.TEACHER,
            status=CourseMembership.Role.TEACHER and CourseMembership.Status.ACTIVE,
        )
        CourseMembership.objects.create(
            course=self.course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
            section=self.section,
        )

    def test_topic_restricts_private_visibility_and_section_scope(self):
        topic = DiscussionTopic.objects.create(
            course=self.course,
            author=self.teacher,
            title='Tema de prueba',
            content='Contenido del tema',
            status=DiscussionTopic.Status.OPEN,
            section=self.section,
        )
        self.assertTrue(topic.is_visible_to_user(self.student))
        self.assertFalse(topic.is_visible_to_user(User.objects.create_user('anon', password='password')))

    def test_reply_depth_is_controlled_and_parent_must_match_topic(self):
        topic = DiscussionTopic.objects.create(
            course=self.course,
            author=self.teacher,
            title='Tema anidado',
            content='Contenido del tema',
            status=DiscussionTopic.Status.OPEN,
        )
        root = DiscussionPost.objects.create(topic=topic, author=self.student, content='Respuesta raíz')
        level_two = DiscussionPost.objects.create(topic=topic, author=self.student, parent=root, content='Nivel 2')
        level_three = DiscussionPost.objects.create(topic=topic, author=self.student, parent=level_two, content='Nivel 3')
        self.assertEqual(level_three.depth, 2)

        other_topic = DiscussionTopic.objects.create(
            course=make_course('Otro curso'),
            author=self.teacher,
            title='Otro tema',
            content='Contenido',
            status=DiscussionTopic.Status.OPEN,
        )
        invalid = DiscussionPost(topic=other_topic, author=self.student, parent=root, content='Mismatched')
        with self.assertRaises(ValidationError):
            invalid.full_clean()

    def test_tag_globally_unique_per_course_and_post_uses_is_edited_when_updated(self):
        tag = DiscussionTag.objects.create(course=self.course, name='django')
        self.assertEqual(str(tag), 'django')
        topic = DiscussionTopic.objects.create(
            course=self.course,
            author=self.teacher,
            title='Tema',
            content='x',
            status=DiscussionTopic.Status.OPEN,
        )
        post = DiscussionPost.objects.create(topic=topic, author=self.student, content='comentario')
        self.assertFalse(post.history)

        post.content = 'comentario editado'
        post.save(update_fields=['content', 'updated_at'])

        self.assertTrue(post.is_edited)
        self.assertTrue(post.history)


class LmsCompetencyTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('competency_teacher', password='password')
        self.student = User.objects.create_user('competency_student', password='password')
        self.course = make_course('Competencias')
        CourseMembership.objects.create(
            course=self.course,
            user=self.teacher,
            role=CourseMembership.Role.TEACHER,
            status=CourseMembership.Status.ACTIVE,
        )
        CourseMembership.objects.create(
            course=self.course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )
        self.framework = CompetencyFramework.objects.create(
            course=self.course,
            name='Marco digital',
            code='DIGITAL-01',
            version='1.0',
            created_by=self.teacher,
        )
        self.root = Competency.objects.create(
            framework=self.framework,
            code='DIG-1',
            title='Comunicación digital',
            description='Capacidad de comunicar con claridad en entornos digitales.',
            criterion='Debe demostrar una entrega con criterios claros y uso correcto de lenguaje técnico.',
            mastery_levels=[
                {'label': 'Básico', 'min_score': 0.0},
                {'label': 'Desarrollando', 'min_score': 0.50},
                {'label': 'Competente', 'min_score': 0.75},
                {'label': 'Dominado', 'min_score': 0.90},
            ],
            mastery_threshold=0.75,
            version='1.0',
            created_by=self.teacher,
        )
        self.course_competency = CourseCompetency.objects.create(
            course=self.course,
            competency=self.root,
            target_level='competent',
            criterion='Entrega un trabajo con estructura, claridad y evidencia.',
            created_by=self.teacher,
        )

    def test_competency_requires_evidence_to_achieve_mastery(self):
        Evidence.objects.create(
            course=self.course,
            user=self.student,
            competency=self.root,
            source_type='quiz',
            source_label='Prueba 1',
            score=Decimal('0.60'),
            max_score=Decimal('1.00'),
            description='Resultado inicial del quiz.',
            evidence_date=timezone.now(),
        )
        achievement = CompetencyAchievement.objects.create(
            course=self.course,
            user=self.student,
            competency=self.root,
        )
        achievement.refresh_from_evidence()

        self.assertEqual(achievement.mastery_level, 'desarrollando')
        self.assertFalse(achievement.is_achieved)

        Evidence.objects.create(
            course=self.course,
            user=self.student,
            competency=self.root,
            source_type='rubric',
            source_label='Rúbrica final',
            score=Decimal('0.90'),
            max_score=Decimal('1.00'),
            description='Rúbrica con evidencia demostrada.',
            evidence_date=timezone.now(),
        )
        achievement.refresh_from_evidence()

        self.assertEqual(achievement.mastery_level, 'dominado')
        self.assertTrue(achievement.is_achieved)
        self.assertIn('dominado', achievement.evidence_summary.lower())

    def test_competency_tree_and_course_binding_remain_consistent(self):
        child = Competency.objects.create(
            framework=self.framework,
            parent=self.root,
            code='DIG-1.1',
            title='Narración técnica',
            description='Explica soluciones con lenguaje técnico claro.',
            criterion='Explica el problema y la solución con ejemplos apropiados.',
            mastery_levels=[
                {'label': 'Básico', 'min_score': 0.0},
                {'label': 'Competente', 'min_score': 0.7},
            ],
            mastery_threshold=0.7,
            version='1.0',
            created_by=self.teacher,
        )
        self.assertEqual(child.parent, self.root)
        self.assertEqual(child.framework_id, self.framework.pk)
        self.assertIn('competent', self.course_competency.target_level)


class LmsCompetencyTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('competency_teacher', password='password')
        self.student = User.objects.create_user('competency_student', password='password')
        self.course = make_course('Competencias')
        CourseMembership.objects.create(
            course=self.course,
            user=self.teacher,
            role=CourseMembership.Role.TEACHER,
            status=CourseMembership.Status.ACTIVE,
        )
        CourseMembership.objects.create(
            course=self.course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )
        self.framework = CompetencyFramework.objects.create(
            course=self.course,
            name='Marco digital',
            code='DIGITAL-01',
            version='1.0',
            created_by=self.teacher,
        )
        self.root = Competency.objects.create(
            framework=self.framework,
            code='DIG-1',
            title='Comunicación digital',
            description='Capacidad de comunicar con claridad en entornos digitales.',
            criterion='Debe demostrar una entrega con criterios claros y uso correcto de lenguaje técnico.',
            mastery_levels=[
                {'label': 'Básico', 'min_score': 0.0},
                {'label': 'Desarrollando', 'min_score': 0.50},
                {'label': 'Competente', 'min_score': 0.75},
                {'label': 'Dominado', 'min_score': 0.90},
            ],
            mastery_threshold=0.75,
            version='1.0',
            created_by=self.teacher,
        )
        self.course_competency = CourseCompetency.objects.create(
            course=self.course,
            competency=self.root,
            target_level='competent',
            criterion='Entrega un trabajo con estructura, claridad y evidencia.',
            created_by=self.teacher,
        )

    def test_competency_requires_evidence_to_achieve_mastery(self):
        Evidence.objects.create(
            course=self.course,
            user=self.student,
            competency=self.root,
            source_type='quiz',
            source_label='Prueba 1',
            score=Decimal('0.60'),
            max_score=Decimal('1.00'),
            description='Resultado inicial del quiz.',
            evidence_date=timezone.now(),
        )
        achievement = CompetencyAchievement.objects.create(
            course=self.course,
            user=self.student,
            competency=self.root,
        )
        achievement.refresh_from_evidence()

        self.assertEqual(achievement.mastery_level, 'desarrollando')
        self.assertFalse(achievement.is_achieved)

        Evidence.objects.create(
            course=self.course,
            user=self.student,
            competency=self.root,
            source_type='rubric',
            source_label='Rúbrica final',
            score=Decimal('0.90'),
            max_score=Decimal('1.00'),
            description='Rúbrica con evidencia demostrada.',
            evidence_date=timezone.now(),
        )
        achievement.refresh_from_evidence()

        self.assertEqual(achievement.mastery_level, 'dominado')
        self.assertTrue(achievement.is_achieved)
        self.assertIn('dominado', achievement.evidence_summary.lower())

    def test_competency_tree_and_course_binding_remain_consistent(self):
        child = Competency.objects.create(
            framework=self.framework,
            parent=self.root,
            code='DIG-1.1',
            title='Narración técnica',
            description='Explica soluciones con lenguaje técnico claro.',
            criterion='Explica el problema y la solución con ejemplos apropiados.',
            mastery_levels=[
                {'label': 'Básico', 'min_score': 0.0},
                {'label': 'Competente', 'min_score': 0.7},
            ],
            mastery_threshold=0.7,
            version='1.0',
            created_by=self.teacher,
        )
        self.assertEqual(child.parent, self.root)
        self.assertEqual(child.framework_id, self.framework.pk)
        self.assertIn('competent', self.course_competency.target_level)


class LmsNotificationAndCalendarTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('notify_teacher', password='password')
        self.student_a = User.objects.create_user('notify_student_a', password='password')
        self.student_b = User.objects.create_user('notify_student_b', password='password')
        self.course = make_course('Notificaciones')
        CourseMembership.objects.create(
            course=self.course,
            user=self.teacher,
            role=CourseMembership.Role.TEACHER,
            status=CourseMembership.Status.ACTIVE,
        )
        for user in (self.student_a, self.student_b):
            CourseMembership.objects.create(
                course=self.course,
                user=user,
                role=CourseMembership.Role.STUDENT,
                status=CourseMembership.Status.ACTIVE,
            )

    def test_grade_publication_is_targeted_only_to_the_recipient(self):
        NotificationPreference.objects.create(
            user=self.student_a,
            course=self.course,
            in_app_enabled=True,
            email_enabled=True,
        )
        NotificationPreference.objects.create(
            user=self.student_b,
            course=self.course,
            in_app_enabled=True,
            email_enabled=True,
        )

        event = queue_notification_event(
            event_type=NotificationEvent.Type.NOTE_PUBLISHED,
            actor=self.teacher,
            course=self.course,
            title='Nota publicada',
            message='Tu nota ya está disponible.',
            target_user=self.student_a,
            dedupe_key='grade-note-001',
        )

        self.assertEqual(Notification.objects.filter(event=event, recipient=self.student_a).count(), 1)
        self.assertEqual(Notification.objects.filter(event=event, recipient=self.student_b).count(), 0)
        self.assertTrue(NotificationOutbox.objects.filter(event=event, recipient=self.student_a, channel='in_app').exists())

    def test_duplicate_delivery_and_opt_out_are_respected(self):
        pref = NotificationPreference.objects.create(
            user=self.student_a,
            course=self.course,
            in_app_enabled=True,
            email_enabled=False,
        )

        first = queue_notification_event(
            event_type=NotificationEvent.Type.WELCOME,
            actor=self.teacher,
            course=self.course,
            title='Bienvenida',
            message='Bienvenida al curso.',
            target_user=self.student_a,
            dedupe_key='welcome-note-001',
        )
        second = queue_notification_event(
            event_type=NotificationEvent.Type.WELCOME,
            actor=self.teacher,
            course=self.course,
            title='Bienvenida',
            message='Bienvenida al curso.',
            target_user=self.student_a,
            dedupe_key='welcome-note-001',
        )

        self.assertEqual(NotificationEvent.objects.filter(dedupe_key='welcome-note-001').count(), 1)
        self.assertEqual(NotificationOutbox.objects.filter(event=first, recipient=self.student_a, channel='email').count(), 0)
        self.assertFalse(pref.email_enabled)
        self.assertEqual(Notification.objects.filter(event=second, recipient=self.student_a).count(), 1)

    def test_calendar_event_window_uses_course_timezone_and_ignores_out_of_range_events(self):
        event = CalendarEvent.objects.create(
            course=self.course,
            user=self.student_a,
            title='Entrega de proyecto',
            event_type='assessment',
            starts_at=timezone.datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc),
            ends_at=timezone.datetime(2026, 10, 12, 10, 30, tzinfo=timezone.utc),
            time_zone='Europe/Madrid',
            all_day=False,
            description='Plazo de entrega',
        )

        visible = CalendarEvent.objects.for_user(self.student_a, start=timezone.datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc), end=timezone.datetime(2026, 10, 13, 0, 0, tzinfo=timezone.utc))
        hidden = CalendarEvent.objects.for_user(self.student_a, start=timezone.datetime(2026, 10, 13, 8, 0, tzinfo=timezone.utc), end=timezone.datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc))

        self.assertIn(event, visible)
        self.assertNotIn(event, hidden)


class LmsQuestionBankTests(TestCase):
    def test_groups_bank_levels_without_merging_distinct_names(self):
        from types import SimpleNamespace
        from .selectors import group_question_banks

        banks = [SimpleNamespace(title=f'Python I {level}/5') for level in range(1, 6)]
        banks += [SimpleNamespace(title='Python II 1/5'), SimpleNamespace(title='Banco sin nivel')]
        groups = group_question_banks(banks)
        self.assertEqual([group['title'] for group in groups], ['Python I', 'Python II', 'Banco sin nivel'])
        self.assertEqual(groups[0]['banks'], banks[:5])

    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_bank_area_tech_design_preserves_actions_and_permissions(self):
        CourseMembership.objects.create(course=self.course, user=self.teacher, role='owner')
        self.client.force_login(self.teacher)
        url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'item-bank'})
        response = self.client.get(url)
        self.assertContains(response, 'lms/knowledge-tech.css')
        self.assertContains(response, 'billing-app knowledge-tech')
        from bs4 import BeautifulSoup

        accordion = BeautifulSoup(response.content, 'html.parser').select_one('details.knowledge-bank-accordion')
        self.assertIsNotNone(accordion)
        self.assertFalse(accordion.has_attr('open'))
        self.assertIn(self.bank.title, accordion.summary.get_text())
        self.assertIsNotNone(accordion.select_one('.knowledge-bank-body'))
        group = accordion.find_parent('details', class_='knowledge-bank-group')
        self.assertIsNotNone(group)
        self.assertFalse(group.has_attr('open'))
        self.assertContains(response, 'course-context-grid-full')
        self.assertNotContains(response, '<aside class="upcoming-panel">')
        self.assertNotContains(response, 'Próximas entregas')
        self.assertContains(response, self.bank.title)
        for route, kwargs in (
            ('lms:knowledge_create', {'course_id': self.course.pk}),
            ('lms:knowledge_bank', {'course_id': self.course.pk, 'bank_id': self.bank.pk}),
            ('lms:knowledge_delete', {'course_id': self.course.pk, 'bank_id': self.bank.pk}),
        ):
            self.assertContains(response, reverse(route, kwargs=kwargs))
        self.bank.delete()
        self.assertContains(self.client.get(url), 'Crea un banco e importa tu CSV')
        CourseMembership.objects.create(course=self.course, user=self.student, role='student')
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(url).status_code, 403)

    def setUp(self):
        self.teacher = User.objects.create_user('bank_teacher', password='password')
        self.student = User.objects.create_user('bank_student', password='password')
        self.course = make_course('Banco de preguntas')
        self.bank = QuestionBank.objects.create(course=self.course, owner=self.teacher, title='Banco D1')
        self.tag = QuestionTag.objects.create(bank=self.bank, name='django')

    def test_single_choice_scoring_and_validation(self):
        question = Question.objects.create(
            bank=self.bank,
            created_by=self.teacher,
            question_type=Question.Type.SINGLE_CHOICE,
            title='Pregunta 1',
            points=10,
            difficulty=2,
        )
        version = question.create_version(
            prompt='<p>¿Qué valor devuelve 2 + 2?</p>',
            options=['3', '4', '5'],
            correct_answers=['4'],
            explanation='Suma de dos más dos.',
            published=True,
        )
        self.assertTrue(question.current_version == version)
        self.assertEqual(question.evaluate_response('4')['score'], 10)
        self.assertEqual(question.evaluate_response('3')['score'], 0)

        with self.assertRaises(ValidationError):
            QuestionVersion(question=question, version_number=2, prompt='x', options=[]).full_clean()

    def test_short_text_answers_are_case_insensitive(self):
        question = Question.objects.create(
            bank=self.bank,
            created_by=self.teacher,
            question_type=Question.Type.SHORT_TEXT,
            title='Texto breve',
            points=5,
        )
        question.create_version(
            prompt='Escribe la palabra',
            correct_answers=['Straße'],
            published=True,
        )

        self.assertTrue(question.evaluate_response('STRASSE')['correct'])

    def test_multiple_choice_and_numeric_scoring_rules(self):
        multi = Question.objects.create(
            bank=self.bank,
            created_by=self.teacher,
            question_type=Question.Type.MULTIPLE_CHOICE,
            title='Multi',
            points=8,
        )
        multi.create_version(
            prompt='Elige los primos',
            options=['2', '3', '4'],
            correct_answers=['2', '3'],
            published=True,
        )
        self.assertEqual(multi.evaluate_response(['2', '3'])['score'], 8)
        self.assertEqual(multi.evaluate_response(['2'])['score'], 0)

        numeric = Question.objects.create(
            bank=self.bank,
            created_by=self.teacher,
            question_type=Question.Type.NUMERIC,
            title='Num',
            points=5,
        )
        numeric.create_version(
            prompt='¿Cuánto es 10 / 2?',
            options=[],
            correct_answers=['5'],
            tolerance='0.5',
            published=True,
        )
        self.assertEqual(numeric.evaluate_response('5.3')['score'], 5)
        self.assertEqual(numeric.evaluate_response('6')['score'], 0)

    def test_essay_and_matching_are_manual_review_and_immutable_versioning(self):
        essay = Question.objects.create(
            bank=self.bank,
            created_by=self.teacher,
            question_type=Question.Type.ESSAY,
            title='Ensayo',
            points=6,
        )
        essay.create_version(
            prompt='Explica la diferencia entre diseño y desarrollo',
            correct_answers=['Se evaluará la reflexión'],
            published=True,
        )
        result = essay.evaluate_response('Respuesta humana')
        self.assertTrue(result['manual_review'])
        self.assertEqual(result['max_score'], 6)

        matching = Question.objects.create(
            bank=self.bank,
            created_by=self.teacher,
            question_type=Question.Type.MATCHING,
            title='Emparejar',
            points=7,
        )
        matching.create_version(
            prompt='Relaciona conceptos',
            correct_answers={'a': 'uno', 'b': 'dos'},
            published=True,
        )
        self.assertEqual(matching.evaluate_response({'a': 'uno', 'b': 'dos'})['score'], 7)

        original_version_count = QuestionVersion.objects.filter(question=essay).count()
        essay.current_version.prompt = 'Texto modificado'
        essay.current_version.save(update_fields=['prompt'])
        self.assertEqual(QuestionVersion.objects.filter(question=essay).count(), original_version_count)
        question_attempt = QuestionAttempt.objects.create(
            question=essay,
            user=self.student,
            version=essay.current_version,
            response={'text': 'respuesta'},
            score=0,
            is_correct=False,
            status=QuestionAttempt.Status.SUBMITTED,
        )
        self.assertEqual(question_attempt.version.pk, essay.current_version.pk)


class LmsQuizTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('quiz_teacher', password='password')
        self.student = User.objects.create_user('quiz_student', password='password')
        self.course = make_course('Curso de quiz')
        CourseMembership.objects.create(
            course=self.course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )
        self.bank = QuestionBank.objects.create(course=self.course, owner=self.teacher, title='Banco quiz')
        self.question = Question.objects.create(
            bank=self.bank,
            created_by=self.teacher,
            question_type=Question.Type.SINGLE_CHOICE,
            title='Pregunta simple',
            points=10,
        )
        self.version = self.question.create_version(
            prompt='¿Cuánto es 2 + 2?',
            options=['3', '4', '5'],
            correct_answers=['4'],
            published=True,
        )
        self.quiz = Quiz.objects.create(
            course=self.course,
            created_by=self.teacher,
            title='Quiz de prueba',
            status=Quiz.Status.PUBLISHED,
            available_from=timezone.now() - timedelta(hours=1),
            available_until=timezone.now() + timedelta(days=1),
            due_at=timezone.now() + timedelta(days=1),
            duration_minutes=30,
            max_attempts=2,
            allow_resume=True,
            allow_time_extension=False,
            passing_score=60,
            publish_answers_at=timezone.now() + timedelta(days=1),
            publish_grade_at=timezone.now() + timedelta(days=1),
        )
        self.quiz_question = QuizQuestion.objects.create(
            quiz=self.quiz,
            question_version=self.version,
            order=1,
            points=10,
        )

    def test_quiz_attempt_lifecycle_and_duplicate_submission_protection(self):
        attempt = self.quiz.start_attempt(self.student)
        self.assertEqual(attempt.status, QuizAttempt.Status.IN_PROGRESS)
        self.assertTrue(self.quiz.can_attempt(self.student))

        attempt.save_answer(self.quiz_question, {'value': '4'}, autosave=True)
        self.assertTrue(QuizAnswer.objects.filter(attempt=attempt, question=self.quiz_question).exists())

        attempt.submit()
        self.assertEqual(attempt.status, QuizAttempt.Status.SUBMITTED)

        with self.assertRaises(ValueError):
            attempt.submit()

        published = self.quiz.evaluate_attempt(attempt)
        self.assertEqual(published.score, 10)
        grade_entry = GradeEntry.objects.filter(attempt=attempt).first()
        self.assertIsNotNone(grade_entry)
        self.assertEqual(grade_entry.grade, 10)

        with self.assertRaises(ValueError):
            self.quiz.start_attempt(self.student)

    def test_quiz_rejects_expired_or_non_course_attempts(self):
        self.quiz.status = Quiz.Status.CLOSED
        self.quiz.save(update_fields=['status'])
        with self.assertRaises(ValueError):
            self.quiz.start_attempt(self.student)

        other_course = make_course('Otro curso')
        other_teacher = User.objects.create_user('other_teacher', password='password')
        other_bank = QuestionBank.objects.create(course=other_course, owner=other_teacher, title='Banco ajeno')
        other_question = Question.objects.create(
            bank=other_bank,
            created_by=other_teacher,
            question_type=Question.Type.SINGLE_CHOICE,
            title='Sí no',
            points=5,
        )
        other_version = other_question.create_version(
            prompt='¿Es esto válido?',
            options=['sí', 'no'],
            correct_answers=['sí'],
            published=True,
        )
        self.quiz.status = Quiz.Status.PUBLISHED
        self.quiz.save(update_fields=['status'])
        with self.assertRaises(ValueError):
            self.quiz.start_attempt(other_teacher)


class LmsAssignmentTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('assignment_teacher', password='password')
        self.student = User.objects.create_user('assignment_student', password='password')
        self.other_student = User.objects.create_user('assignment_other', password='password')
        self.course = make_course('Curso de tareas')
        self.section = Section.objects.create(course=self.course, title='Sección A')
        CourseMembership.objects.create(
            course=self.course,
            user=self.teacher,
            role=CourseMembership.Role.TEACHER,
            status=CourseMembership.Status.ACTIVE,
        )
        CourseMembership.objects.create(
            course=self.course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
            section=self.section,
        )
        # El alumno ajeno no pertenece a la tarea, por lo que no puede entregarla ni verla.
        self.assignment = Assignment.objects.create(
            course=self.course,
            created_by=self.teacher,
            title='PEC 1',
            instructions='Entrega un documento con la reflexión.',
            points=10,
            available_at=timezone.now() - timedelta(days=1),
            due_at=timezone.now() + timedelta(days=1),
            lock_at=timezone.now() + timedelta(days=2),
            submission_types=[Assignment.SubmissionType.FILE, Assignment.SubmissionType.TEXT],
            max_attempts=2,
            late_submission_policy='penalize',
            late_penalty_percent=10,
            status=Assignment.Status.PUBLISHED,
        )

    def test_assignment_accepts_private_submission_and_late_policy(self):
        self.assertTrue(self.assignment.can_submit(self.student))

        submission = self.assignment.create_submission(self.student)
        submission.text_answer = 'Resumen entregado.'
        submission.submit()

        self.assertEqual(submission.status, Submission.Status.SUBMITTED)
        self.assertEqual(submission.version, 1)
        self.assertTrue(submission.receipt_identifier)

        attachment = submission.add_attachment(
            file=SimpleUploadedFile('tarea.pdf', b'%PDF-1.4 test', content_type='application/pdf'),
            file_name='tarea.pdf',
            mime_type='application/pdf',
        )
        self.assertTrue(SubmissionAttachment.objects.filter(pk=attachment.pk, submission=submission).exists())
        self.assertEqual(submission.attachments.count(), 1)

        submission.grade = 9
        submission.feedback = 'Buena entrega.'
        submission.returned_at = timezone.now()
        submission.status = Submission.Status.RETURNED
        submission.save(update_fields=['grade', 'feedback', 'returned_at', 'status', 'updated_at'])
        self.assertEqual(submission.grade, 9)

    def test_assignment_rejects_other_course_access_and_private_visibility(self):
        other_course = make_course('Otro curso')
        with self.assertRaises(ValueError):
            self.assignment.create_submission(self.other_student, course=other_course)

        self.assertFalse(self.assignment.can_submit(self.other_student))
        submission = self.assignment.create_submission(self.student)
        submission.submit()
        self.assertFalse(submission.is_visible_to_user(self.other_student))

    def test_pec_has_rubric_gradebook_and_public_feedback_flow(self):
        rubric = Rubric.objects.create(course=self.course, created_by=self.teacher, name='PEC · Rúbrica', description='Criterios de evaluación')
        criterion = RubricCriterion.objects.create(rubric=rubric, title='Originalidad', description='Uso de fuentes', points=5, weight=1, order=1)
        rubric_level = RubricLevel.objects.create(criterion=criterion, label='Excelente', description='Muy bien', points=5, order=1)
        self.assignment.is_pec = True
        self.assignment.pec_statement = 'Analiza la práctica de evaluación continua.'
        self.assignment.learning_objectives = 'Aplicar método y argumentar.'
        self.assignment.competencies = 'Razonamiento crítico.'
        self.assignment.resource_links = ['https://example.com/pec']
        self.assignment.rubric = rubric
        self.assignment.feedback_published_at = timezone.now()
        self.assignment.save(update_fields=['is_pec', 'pec_statement', 'learning_objectives', 'competencies', 'resource_links', 'rubric', 'feedback_published_at', 'updated_at'])

        submission = self.assignment.create_submission(self.student)
        submission.submit()
        submission.grade = 4.5
        submission.feedback = 'Buen análisis con detalle.'
        submission.feedback_published = True
        submission.feedback_published_at = timezone.now()
        submission.status = Submission.Status.RETURNED
        submission.save(update_fields=['grade', 'feedback', 'feedback_published', 'feedback_published_at', 'status', 'updated_at'])

        assessment = RubricAssessment.objects.create(
            rubric=rubric,
            assignment=self.assignment,
            submission=submission,
            user=self.student,
            snapshot=rubric.get_snapshot(),
            criterion_scores={
                str(criterion.pk): {
                    'level_id': rubric_level.pk,
                    'label': rubric_level.label,
                    'points': '4.50',
                    'feedback': 'Buena originalidad y buen uso de fuentes.',
                }
            },
            feedback='Buen análisis con detalle.',
        )
        assessment.calculate_score()
        assessment.publish()

        self.assertTrue(self.assignment.rec_rows(user=self.student)[0]['visible'])
        self.assertTrue(submission.is_feedback_visible_to_user(self.student))
        self.assertFalse(submission.is_feedback_visible_to_user(self.other_student))

        row = self.assignment.rec_rows(user=self.teacher)[0]
        self.assertEqual(row['status'], Submission.Status.RETURNED)
        self.assertEqual(row['grade'], Decimal('4.50'))
        self.assertEqual(assessment.score, Decimal('4.50'))
        self.assertEqual(assessment.max_score, Decimal('5.00'))

        criterion.points = Decimal('6.00')
        criterion.save(update_fields=['points', 'updated_at'])
        self.assertEqual(assessment.snapshot['criteria'][0]['points'], 5.0)


class LmsGroupProjectTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('group_teacher', password='password')
        self.student_one = User.objects.create_user('group_student_1', password='password')
        self.student_two = User.objects.create_user('group_student_2', password='password')
        self.student_three = User.objects.create_user('group_student_3', password='password')
        self.course = make_course('Proyecto grupal')
        self.section = Section.objects.create(course=self.course, title='Sección A')

        CourseMembership.objects.create(
            course=self.course,
            user=self.teacher,
            role=CourseMembership.Role.TEACHER,
            status=CourseMembership.Status.ACTIVE,
        )
        for user in [self.student_one, self.student_two]:
            CourseMembership.objects.create(
                course=self.course,
                user=user,
                role=CourseMembership.Role.STUDENT,
                status=CourseMembership.Status.ACTIVE,
                section=self.section,
            )
        self.group = CourseGroup.objects.create(
            course=self.course,
            title='Equipo Alpha',
            section=self.section,
            max_members=2,
            created_by=self.teacher,
        )
        GroupMembership.objects.create(
            group=self.group,
            user=self.student_one,
            role=GroupMembership.Role.LEADER,
            status=GroupMembership.Status.ACTIVE,
        )
        self.assignment = Assignment.objects.create(
            course=self.course,
            created_by=self.teacher,
            title='Proyecto final',
            status=Assignment.Status.PUBLISHED,
            available_at=timezone.now() - timedelta(days=2),
            due_at=timezone.now() + timedelta(days=7),
            lock_at=timezone.now() + timedelta(days=10),
            submission_types=['text'],
            allow_group_submission=True,
            max_attempts=1,
        )
        self.project = GroupProject.objects.create(
            assignment=self.assignment,
            course=self.course,
            group=self.group,
            title='Proyecto de innovación',
            created_by=self.teacher,
        )

    def test_group_membership_respects_course_section_and_capacity(self):
        self.group.max_members = 1
        self.group.full_clean()

        other_group = CourseGroup.objects.create(
            course=self.course,
            title='Equipo Beta',
            section=self.section,
            max_members=2,
            created_by=self.teacher,
        )
        duplicate = GroupMembership(group=other_group, user=self.student_one, role=GroupMembership.Role.MEMBER)
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

        another_course = make_course('Otro curso')
        bad_group = CourseGroup(course=another_course, title='Equipo externo', section=self.section, created_by=self.teacher)
        with self.assertRaises(ValidationError):
            bad_group.full_clean()

    def test_group_submission_is_private_to_group_members_and_traceable(self):
        submission = GroupSubmission.objects.create(
            project=self.project,
            group=self.group,
            submitted_by=self.student_one,
            grade=Decimal('8.50'),
            status=GroupSubmission.Status.SUBMITTED,
            text_answer='Entrega del equipo',
            feedback='Muy bien',
        )

        self.assertTrue(submission.is_visible_to_user(self.student_one))
        self.assertFalse(submission.is_visible_to_user(self.student_three))
        self.assertEqual(submission.grade, Decimal('8.50'))

    def test_group_project_reuses_assignment_rules_for_teamwork(self):
        self.assertTrue(self.assignment.allow_group_submission)
        self.assertEqual(self.project.assignment_id, self.assignment.pk)
        self.assertEqual(self.project.group_id, self.group.pk)


class LmsServiceTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('teacher', password='password')
        self.student = User.objects.create_user('student', password='password')
        self.add_course_permission = Permission.objects.get(
            content_type__app_label='courses',
            codename='add_curso',
        )
        self.publish_permission = Permission.objects.get(
            content_type__app_label='lms',
            codename='publish_course',
        )

    def grant(self, user, permission):
        user.user_permissions.add(permission)

    def make_owned_course(self, name='Aula'):
        self.grant(self.teacher, self.add_course_permission)
        return create_course(actor=self.teacher, course=make_course(name))

    def test_course_creation_assigns_owner_settings_and_audit(self):
        course = self.make_owned_course()
        membership = CourseMembership.objects.get(course=course, user=self.teacher)

        self.assertFalse(course.activo)
        self.assertEqual(membership.role, CourseMembership.Role.OWNER)
        self.assertTrue(CourseSettingsLMS.objects.filter(course=course).exists())
        self.assertTrue(
            AuditEvent.objects.filter(
                course=course,
                actor=self.teacher,
                action=AuditEvent.Action.COURSE_CREATED,
            ).exists()
        )

    def test_course_creation_requires_existing_course_permission(self):
        with self.assertRaises(PermissionDenied):
            create_course(actor=self.student, course=make_course('No autorizado'))

    def test_same_user_can_be_student_in_a_and_teacher_in_b(self):
        course_a = self.make_owned_course('Curso A')
        course_b = self.make_owned_course('Curso B')
        CourseMembership.objects.create(
            course=course_a,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
        )
        CourseMembership.objects.create(
            course=course_b,
            user=self.student,
            role=CourseMembership.Role.TEACHER,
        )

        self.assertFalse(can_edit_course(self.student, course_a))
        self.assertTrue(can_edit_course(self.student, course_b))
        self.assertEqual(
            self.student.course_memberships.get(course=course_a).role,
            CourseMembership.Role.STUDENT,
        )
        self.assertEqual(
            self.student.course_memberships.get(course=course_b).role,
            CourseMembership.Role.TEACHER,
        )
        self.assertFalse(is_platform_admin(self.student))

    def test_course_owner_can_publish_and_enrollments_are_idempotent(self):
        course = self.make_owned_course()
        publish_course(actor=self.teacher, course=course)
        first = enroll_student(actor=self.student, course=course)
        second = enroll_student(actor=self.student, course=course)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.role, CourseMembership.Role.STUDENT)
        self.assertEqual(CourseMembership.objects.filter(course=course, user=self.student).count(), 1)
        self.assertTrue(InscripcionCurso.objects.filter(curso=course, usuario=self.student).exists())
        self.assertEqual(
            AuditEvent.objects.filter(
                course=course,
                target_user=self.student,
                action=AuditEvent.Action.MEMBER_ENROLLED,
            ).count(),
            1,
        )

    def test_closed_or_archived_course_rejects_new_enrollment(self):
        course = self.make_owned_course()
        publish_course(actor=self.teacher, course=course)
        course.lms_settings.enrollment_open = False
        course.lms_settings.save(update_fields=['enrollment_open'])
        with self.assertRaises(EnrollmentUnavailable):
            enroll_student(actor=self.student, course=course)

        course.lms_settings.enrollment_open = True
        course.lms_settings.archived = True
        course.lms_settings.save(update_fields=['enrollment_open', 'archived'])
        with self.assertRaises(EnrollmentUnavailable):
            enroll_student(actor=self.student, course=course)

    def test_legacy_enrollment_is_linked_instead_of_duplicated(self):
        course = self.make_owned_course()
        publish_course(actor=self.teacher, course=course)
        enrollment = InscripcionCurso.objects.create(
            usuario=self.student,
            curso=course,
        )
        membership = enroll_student(actor=self.student, course=course)

        self.assertEqual(membership.role, CourseMembership.Role.STUDENT)
        self.assertEqual(
            CourseMembership.objects.filter(course=course, user=self.student).count(),
            1,
        )
        self.assertEqual(enrollment.pk, InscripcionCurso.objects.get(curso=course, usuario=self.student).pk)

    def test_dropping_a_student_revokes_legacy_course_access(self):
        course = self.make_owned_course()
        membership = CourseMembership.objects.create(
            course=course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
        )
        InscripcionCurso.objects.create(usuario=self.student, curso=course)

        set_membership_status(
            actor=self.teacher,
            membership=membership,
            status=CourseMembership.Status.DROPPED,
        )

        self.assertFalse(is_course_member(self.student, course))
        self.assertFalse(InscripcionCurso.objects.filter(usuario=self.student, curso=course).exists())
        membership.refresh_from_db()
        self.assertIsNotNone(membership.dropped_at)

    def test_invalid_course_date_range_is_rejected_by_service(self):
        course = self.make_owned_course()
        with self.assertRaises(ValidationError):
            update_course(
                actor=self.teacher,
                course=course,
                changes={
                    'fecha_inicio': date(2025, 2, 1),
                    'fecha_fin': date(2025, 1, 1),
                },
            )


class LmsOptionalAITests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('teacher_ai', password='password')
        self.student = User.objects.create_user('student_ai', password='password')
        self.other_student = User.objects.create_user('other_student_ai', password='password')
        self.course = make_course('Curso IA')
        self.other_course = make_course('Curso IA 2')
        CourseMembership.objects.create(
            course=self.course,
            user=self.teacher,
            role=CourseMembership.Role.TEACHER,
            status=CourseMembership.Status.ACTIVE,
        )
        CourseMembership.objects.create(
            course=self.course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )
        CourseMembership.objects.create(
            course=self.other_course,
            user=self.other_student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )

    @override_settings(LMS_ENABLE_AI=False, LMS_AI_PROVIDER='disabled', LMS_AI_API_KEY='')
    def test_ai_is_disabled_by_default_and_app_remains_usable(self):
        provider = get_active_ai_provider()
        self.assertIsInstance(provider, DisabledAIProvider)
        with self.assertRaises(AIRequestRejected):
            provider.generate(
                user=self.student,
                course=self.course,
                action='student_tutor',
                prompt='Explica este tema de forma guiada.',
            )

    @override_settings(LMS_ENABLE_AI=True, LMS_AI_PROVIDER='openai', LMS_AI_API_KEY='')
    def test_ai_requires_configuration_when_enabled(self):
        with self.assertRaises(AIProviderConfigurationError):
            get_active_ai_provider()

    @override_settings(LMS_ENABLE_AI=True, LMS_AI_PROVIDER='openai', LMS_AI_API_KEY='sk-secret-value', LMS_AI_MAX_MONTHLY_COST=0.0)
    def test_ai_redacts_sensitive_data_and_rejects_cross_course_context(self):
        provider = get_active_ai_provider()
        sanitized = provider.sanitize_text('Token secreto: sk-secret-value y password=abc123')
        self.assertNotIn('sk-secret-value', sanitized)
        self.assertNotIn('abc123', sanitized)

        with self.assertRaises(PermissionDenied):
            provider.generate(
                user=self.student,
                course=self.course,
                action='student_tutor',
                prompt='Explica la materia del otro curso.',
                source_course_ids=[self.other_course.pk],
            )

    @override_settings(LMS_ENABLE_AI=True, LMS_AI_PROVIDER='openai', LMS_AI_API_KEY='sk-test-key', LMS_AI_MAX_TOKENS=2000)
    def test_teacher_publication_requires_human_approval(self):
        provider = get_active_ai_provider()
        with self.assertRaises(PermissionDenied):
            provider.generate(
                user=self.teacher,
                course=self.course,
                action='draft_program',
                prompt='Borrador de la asignatura.',
                approved_by=None,
                require_human_approval=True,
            )


class LearningActivityStructureTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('teacher_activities', password='password')
        self.student = User.objects.create_user('student_activities', password='password')
        self.course = make_course('NoTodoEsCódigo')
        CourseMembership.objects.create(
            course=self.course,
            user=self.teacher,
            role=CourseMembership.Role.OWNER,
            status=CourseMembership.Status.ACTIVE,
        )
        CourseMembership.objects.create(
            course=self.course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )
        self.module = Tema.objects.create(
            curso=self.course,
            titulo='Módulo 01',
            contenido='Contenido del módulo',
            orden=1,
        )

    def test_activity_timeline_state_uses_release_windows(self):
        now = timezone.now()
        activity = LearningActivity.objects.create(
            course=self.course,
            module=self.module,
            title='Actividad de diagnóstico',
            description='Diagnóstico inicial',
            starts_at=now - timedelta(hours=2),
            due_at=now + timedelta(hours=2),
            feedback_at=now + timedelta(days=1),
            solution_release_at=now + timedelta(days=2),
            status=LearningActivity.Status.OPEN,
        )

        self.assertEqual(activity.timeline_state(now=now), LearningActivity.TimelineState.IN_PROGRESS)
        self.assertEqual(activity.is_available(now=now), True)

    def test_NoTodoEsCódigo_structure_builds_ordered_modules_and_activities(self):
        created = build_NoTodoEsCódigo_structure(self.course)

        self.assertTrue(created)
        self.assertGreaterEqual(self.course.lms_modules.count(), 1)
        self.assertGreaterEqual(self.course.learning_activities.count(), 1)
        module = self.course.lms_modules.filter(slug='NoTodoEsCódigo-campus').first() or self.course.lms_modules.first()
        self.assertGreaterEqual(ModuleItem.objects.filter(module=module).count(), 1)
        self.assertGreaterEqual(module.items.filter(item_type=ModuleItem.ItemType.ACTIVITY).count(), 1)

    def test_activities_area_supports_teacher_management_and_hides_drafts_from_students(self):
        create_url = reverse('lms:course_activity_create', kwargs={'course_id': self.course.pk})
        area_url = reverse('lms:course_area', kwargs={
            'course_id': self.course.pk,
            'area': 'activities',
        })
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(create_url).status_code, 200)
        response = self.client.post(create_url, {
            'title': 'Actividad abierta',
            'module': self.module.pk,
            'description': 'Descripción de prueba',
            'instructions': 'Entrega el ejercicio',
            'status': LearningActivity.Status.OPEN,
            'points': 10,
            'is_evaluable': 'on',
        })
        self.assertRedirects(response, area_url, fetch_redirect_response=False)
        activity = LearningActivity.objects.get(title='Actividad abierta')
        self.assertEqual(activity.course, self.course)
        self.assertEqual(activity.module, self.module)

        edit_url = reverse('lms:course_activity_edit', kwargs={
            'course_id': self.course.pk,
            'activity_id': activity.pk,
        })
        response = self.client.post(edit_url, {
            'title': 'Actividad actualizada',
            'module': self.module.pk,
            'description': 'Descripción actualizada',
            'instructions': '',
            'status': LearningActivity.Status.OPEN,
            'points': 12,
            'is_evaluable': 'on',
        })
        self.assertRedirects(response, area_url, fetch_redirect_response=False)
        activity.refresh_from_db()
        self.assertEqual(activity.title, 'Actividad actualizada')
        self.assertEqual(activity.points, 12)

        draft = LearningActivity.objects.create(
            course=self.course,
            title='Actividad en borrador',
            status=LearningActivity.Status.DRAFT,
        )
        self.client.force_login(self.student)
        response = self.client.get(area_url)
        self.assertContains(response, 'Actividad actualizada')
        self.assertNotContains(response, 'Actividad en borrador')
        self.assertNotContains(response, 'Crear actividad')
        self.assertEqual(self.client.get(create_url).status_code, 403)

        self.client.force_login(self.teacher)
        delete_url = reverse('lms:course_activity_delete', kwargs={
            'course_id': self.course.pk,
            'activity_id': draft.pk,
        })
        self.assertRedirects(
            self.client.post(delete_url),
            area_url,
            fetch_redirect_response=False,
        )
        self.assertFalse(LearningActivity.objects.filter(pk=draft.pk).exists())

    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_activity_form_uses_course_topics_and_page_editors(self):
        from django_prose_editor.widgets import ProseEditorWidget

        other_course = make_course('Otro curso')
        other_module = Tema.objects.create(curso=other_course, titulo='Módulo ajeno', contenido='')
        self.client.force_login(self.teacher)
        url = reverse('lms:course_activity_create', kwargs={'course_id': self.course.pk})
        response = self.client.get(url)
        form = response.context['form']
        self.assertEqual(list(form.fields['module'].queryset), [self.module])
        for field_name in ('description', 'instructions'):
            self.assertIsInstance(form.fields[field_name].widget, ProseEditorWidget)
        self.assertContains(response, 'data-create-option-label=')
        self.assertContains(response, 'data-rich-editor-shell', count=3)
        self.assertContains(response, 'module-create-form')
        self.assertContains(response, 'lms/modules.js')
        self.assertNotContains(response, 'Módulo ajeno')
        response = self.client.post(url, {
            'title': 'Actividad inválida', 'module': other_module.pk,
            'status': LearningActivity.Status.OPEN, 'points': 10,
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('module', response.context['form'].errors)
        self.assertFalse(LearningActivity.objects.filter(title='Actividad inválida').exists())

    def test_new_module_is_shared_by_pages_and_activities(self):
        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse('lms:course_module_create', kwargs={'course_id': self.course.pk}),
            {'titulo': 'Módulo compartido', 'contenido': 'Resumen', 'orden': 2},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        topic = self.course.temas.get(pk=response.json()['id'])
        from .forms import LearningActivityForm, PageForm

        self.assertIn(topic, LearningActivityForm(course=self.course).fields['module'].queryset)
        self.assertIn(topic, PageForm(course=self.course).fields['module'].queryset)
        response = self.client.post(
            reverse('lms:course_activity_create', kwargs={'course_id': self.course.pk}),
            {'title': 'Actividad del módulo nuevo', 'module': topic.pk,
             'status': LearningActivity.Status.OPEN, 'points': 0},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LearningActivity.objects.get(title='Actividad del módulo nuevo').module, topic)
        self.client.force_login(self.student)
        response = self.client.post(
            reverse('lms:course_module_create', kwargs={'course_id': self.course.pk}),
            {'titulo': 'No permitido', 'orden': 3},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.course.temas.filter(titulo='No permitido').exists())

    def test_activity_rich_text_is_preserved_sanitized_and_rendered(self):
        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse('lms:course_activity_create', kwargs={'course_id': self.course.pk}),
            {'title': 'Actividad con formato', 'module': self.module.pk,
             'description': '<p><strong>Descripción</strong></p><script>alert(1)</script>',
             'instructions': '<ul><li>Primer paso</li></ul><img src="javascript:alert(1)" onerror="alert(1)">',
             'status': LearningActivity.Status.OPEN, 'points': 10},
        )
        self.assertEqual(response.status_code, 302)
        activity = LearningActivity.objects.get(title='Actividad con formato')
        self.assertIn('<strong>Descripción</strong>', activity.description)
        self.assertIn('<li>Primer paso</li>', activity.instructions)
        self.assertNotIn('<script', activity.description)
        self.assertNotIn('javascript:', activity.instructions)
        self.assertNotIn('onerror', activity.instructions)
        # Also sanitize legacy content that bypassed model save.
        LearningActivity.objects.filter(pk=activity.pk).update(
            instructions='<p>Texto anterior</p><script>alert(1)</script>',
        )
        self.client.force_login(self.student)
        response = self.client.get(reverse('lms:course_area', kwargs={
            'course_id': self.course.pk, 'area': 'activities',
        }))
        self.assertContains(response, '<strong>Descripción</strong>')
        self.assertContains(response, '<p>Texto anterior</p>')
        self.assertContains(response, self.module.titulo)
        self.assertNotContains(response, 'alert(1)')


class LmsFileAssetTests(TestCase):
    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_m4a_upload_and_private_download(self):
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from .forms import FileAssetForm
        from .storage import PrivateMediaStorage

        self.assertIn('.m4a', FileAssetForm().fields['file'].widget.attrs['accept'].split(','))
        create_url = reverse('lms:course_file_create', kwargs={'course_id': self.course.pk})
        content = b'\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00M4A '
        with TemporaryDirectory() as folder, patch.object(
            FileAsset._meta.get_field('file'), 'storage', PrivateMediaStorage(location=folder),
        ):
            self.client.force_login(self.teacher)
            for name in ('leccion.m4a', 'LECCION.M4A'):
                with self.subTest(name=name):
                    response = self.client.post(create_url, {
                        'title': name, 'visibility': 'students', 'access_policy': 'course',
                        'allow_download': 'on',
                        'file': SimpleUploadedFile(name, content, content_type='audio/mp4'),
                    })
                    self.assertEqual(response.status_code, 302)
                    asset = FileAsset.objects.get(title=name)
                    self.assertEqual(asset.file_name, name)
                    self.assertTrue(asset.mime_type.startswith('audio/'))
                    download = reverse('lms:course_file_download', kwargs={
                        'course_id': self.course.pk, 'file_id': asset.pk,
                    })
                    self.client.force_login(self.student)
                    response = self.client.get(download)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(b''.join(response.streaming_content), content)
                    response.close()
                    self.client.force_login(self.outsider)
                    self.assertEqual(self.client.get(download).status_code, 403)
                    self.client.force_login(self.teacher)

    def test_file_directory_search_folders_sort_and_visibility(self):
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from .storage import PrivateMediaStorage

        url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'files'})
        with TemporaryDirectory() as folder, patch.object(
            FileAsset._meta.get_field('file'), 'storage', PrivateMediaStorage(location=folder),
        ):
            small = FileAsset.objects.create(
                course=self.course, owner=self.teacher, title='Guía pequeña', folder='Materiales & notas',
                file=SimpleUploadedFile('guia.pdf', b'%PDF-small'), visibility='students',
            )
            large = FileAsset.objects.create(
                course=self.course, owner=self.teacher, title='Vídeo grande', folder='Vídeos',
                file=SimpleUploadedFile('clase.mp4', b'x' * 200), visibility='course',
            )
            hidden = FileAsset.objects.create(
                course=self.course, owner=self.teacher, title='Notas docentes', folder='Privados',
                file=SimpleUploadedFile('notas.txt', b'privado'), visibility='teachers',
            )
            self.client.force_login(self.teacher)
            response = self.client.get(url, {'sort': 'size'})
            self.assertEqual(list(response.context['files']), [large, small, hidden])
            self.assertContains(response, 'directory-file-table')
            self.assertContains(response, 'course-context-grid-full')
            self.assertNotContains(response, 'class="upcoming-panel"')
            self.assertContains(response, 'Materiales%20%26%20notas')
            response = self.client.get(url, {'folder': small.folder})
            self.assertEqual(list(response.context['files']), [small])
            create = reverse('lms:course_file_create', kwargs={'course_id': self.course.pk})
            self.assertEqual(self.client.get(create, {'folder': small.folder}).context['form']['folder'].value(), small.folder)
            self.assertEqual(list(self.client.get(url, {'q': 'guia.pdf'}).context['files']), [small])
            self.assertEqual(list(self.client.get(url, {'q': 'no-coincide'}).context['files']), [])
            self.client.force_login(self.student)
            response = self.client.get(url)
            self.assertNotContains(response, hidden.title)
            self.assertNotIn(hidden.folder, response.context['folders'])
            self.assertEqual(response.context['file_storage_size'], small.size + large.size)
            self.assertNotContains(response, 'Retirar')
            self.assertEqual(list(self.client.get(url, {'folder': hidden.folder}).context['files']), [])
            self.client.force_login(self.outsider)
            self.assertEqual(self.client.get(url).status_code, 403)

    def setUp(self):
        self.teacher = User.objects.create_user('teacher_files', password='password')
        self.student = User.objects.create_user('student_files', password='password')
        self.outsider = User.objects.create_user('outsider_files', password='password')
        self.course = make_course('Curso de archivos')
        CourseMembership.objects.create(
            course=self.course,
            user=self.teacher,
            role=CourseMembership.Role.OWNER,
            status=CourseMembership.Role.OWNER and CourseMembership.Status.ACTIVE,
        )
        CourseMembership.objects.create(
            course=self.course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )

    def test_private_course_asset_blocks_non_members_and_expired_links(self):
        file_obj = SimpleUploadedFile('guia.pdf', b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF', content_type='application/pdf')
        asset = FileAsset.objects.create(
            course=self.course,
            owner=self.teacher,
            title='Guía del curso',
            file=file_obj,
            visibility='private',
        )

        self.assertTrue(asset.user_can_access(self.teacher))
        self.assertTrue(asset.user_can_access(self.student))
        self.assertFalse(asset.user_can_access(self.outsider))

        link = FileAssetLink.objects.create(
            asset=asset,
            created_by=self.teacher,
            expires_at=timezone.now() + timedelta(hours=2),
        )
        self.assertTrue(link.is_valid())

        link.expires_at = timezone.now() - timedelta(minutes=1)
        link.save(update_fields=['expires_at'])
        self.assertFalse(link.is_valid())

    def test_private_course_download_view_rejects_non_members_and_expired_tokens(self):
        file_obj = SimpleUploadedFile('guia.pdf', b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF', content_type='application/pdf')
        asset = FileAsset.objects.create(
            course=self.course,
            owner=self.teacher,
            title='Guía del curso',
            file=file_obj,
            visibility='private',
        )

        self.client.force_login(self.teacher)
        response = self.client.get(reverse('lms:course_file_download', args=[self.course.pk, asset.pk]))
        self.assertEqual(response.status_code, 200)

        self.client.logout()
        self.client.force_login(self.outsider)
        response = self.client.get(reverse('lms:course_file_download', args=[self.course.pk, asset.pk]))
        self.assertEqual(response.status_code, 403)

        expired_link = FileAssetLink.objects.create(
            asset=asset,
            created_by=self.teacher,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        self.client.logout()
        self.client.force_login(self.student)
        response = self.client.get(
            reverse('lms:course_file_download', args=[self.course.pk, asset.pk]),
            {'token': expired_link.token},
        )
        self.assertEqual(response.status_code, 403)

    def test_files_area_allows_teacher_upload_management_and_student_downloads(self):
        area_url = reverse('lms:course_area', kwargs={
            'course_id': self.course.pk,
            'area': 'files',
        })
        create_url = reverse('lms:course_file_create', kwargs={'course_id': self.course.pk})
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(create_url).status_code, 200)
        response = self.client.post(create_url, {
            'title': 'Guía del alumno',
            'folder': 'Materiales',
            'visibility': FileAsset.Visibility.STUDENTS,
            'access_policy': FileAsset.AccessPolicy.COURSE,
            'allow_download': 'on',
            'allow_preview': 'on',
            'file': SimpleUploadedFile('guia.pdf', b'%PDF-1.4\ncontenido', content_type='application/pdf'),
        })
        self.assertRedirects(response, area_url, fetch_redirect_response=False)
        asset = FileAsset.objects.get(title='Guía del alumno')
        self.assertEqual(asset.course, self.course)
        self.assertEqual(asset.owner, self.teacher)

        self.client.force_login(self.student)
        response = self.client.get(area_url)
        self.assertContains(response, 'Guía del alumno')
        self.assertContains(response, 'Descargar')
        self.assertEqual(self.client.get(reverse(
            'lms:course_file_download',
            kwargs={'course_id': self.course.pk, 'file_id': asset.pk},
        )).status_code, 200)
        self.assertEqual(self.client.get(create_url).status_code, 403)

        self.client.force_login(self.teacher)
        edit_url = reverse('lms:course_file_edit', kwargs={
            'course_id': self.course.pk,
            'file_id': asset.pk,
        })
        response = self.client.post(edit_url, {
            'title': 'Guía actualizada',
            'folder': 'Materiales',
            'visibility': FileAsset.Visibility.STUDENTS,
            'access_policy': FileAsset.AccessPolicy.COURSE,
            'allow_download': 'on',
            'allow_preview': 'on',
        })
        self.assertRedirects(response, area_url, fetch_redirect_response=False)
        asset.refresh_from_db()
        self.assertEqual(asset.title, 'Guía actualizada')

        delete_url = reverse('lms:course_file_delete', kwargs={
            'course_id': self.course.pk,
            'file_id': asset.pk,
        })
        self.assertRedirects(
            self.client.post(delete_url),
            area_url,
            fetch_redirect_response=False,
        )
        asset.refresh_from_db()
        self.assertFalse(asset.is_active)
        self.assertEqual(
            self.client.get(reverse(
                'lms:course_file_download',
                kwargs={'course_id': self.course.pk, 'file_id': asset.pk},
            )).status_code,
            404,
        )

    def test_file_download_respects_preview_and_download_permissions(self):
        asset = FileAsset.objects.create(
            course=self.course,
            owner=self.teacher,
            title='Vista previa',
            file=SimpleUploadedFile('preview.pdf', b'%PDF-1.4\ncontenido', content_type='application/pdf'),
            allow_download=False,
            allow_preview=True,
        )
        self.client.force_login(self.student)
        url = reverse('lms:course_file_download', kwargs={
            'course_id': self.course.pk,
            'file_id': asset.pk,
        })
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(url, {'inline': '1'}).status_code, 200)
        asset.allow_preview = False
        asset.save(update_fields=['allow_preview'])
        self.assertEqual(self.client.get(url, {'inline': '1'}).status_code, 403)


class CourseSectionAndPeopleManagementTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('teacher_people', password='password')
        self.student = User.objects.create_user('student_people', password='password')
        self.other_teacher = User.objects.create_user('other_teacher', password='password')
        self.course = make_course('Curso Personas')
        self.section_a = Section.objects.create(course=self.course, title='Grupo A', capacity=1)
        self.section_b = Section.objects.create(course=self.course, title='Grupo B', capacity=0)
        self.other_course = make_course('Curso Otro')
        self.other_section = Section.objects.create(course=self.other_course, title='Grupo X')
        self.course_membership = CourseMembership.objects.create(
            course=self.course,
            user=self.teacher,
            role=CourseMembership.Role.OWNER,
            status=CourseMembership.Status.ACTIVE,
        )

    def test_assign_membership_to_section_requires_matching_course(self):
        membership = CourseMembership.objects.create(
            course=self.course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )

        with self.assertRaises(ValidationError):
            assign_membership_to_section(
                actor=self.teacher,
                membership=membership,
                section=self.other_section,
            )

    def test_capacity_is_enforced_when_assigning_students_to_section(self):
        first = CourseMembership.objects.create(
            course=self.course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )
        second = CourseMembership.objects.create(
            course=self.course,
            user=User.objects.create_user('student_two', password='password'),
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )

        assign_membership_to_section(actor=self.teacher, membership=first, section=self.section_a)

        with self.assertRaises(ValidationError):
            assign_membership_to_section(actor=self.teacher, membership=second, section=self.section_a)

    def test_invitation_reuses_existing_user_without_duplicate_account(self):
        existing = User.objects.create_user('existing-mail', email='student@example.com', password='password')

        result = invite_users_to_course(
            actor=self.teacher,
            course=self.course,
            emails=['student@example.com'],
            role=CourseMembership.Role.STUDENT,
            section=self.section_a,
        )

        self.assertEqual(User.objects.filter(email__iexact='student@example.com').count(), 1)
        self.assertEqual(result['created'], 1)
        self.assertTrue(
            CourseMembership.objects.filter(
                course=self.course,
                user=existing,
                role=CourseMembership.Role.STUDENT,
            ).exists()
        )

    def test_csv_import_reports_bad_rows_and_creates_valid_memberships(self):
        csv_text = (
            'email,role,section\n'
            'new.student@example.com,student,Grupo A\n'
            'duplicate@example.com,teacher,Grupo B\n'
            'bad-row\n'
        )
        duplicate = User.objects.create_user('duplicate_user', email='duplicate@example.com', password='password')
        CourseMembership.objects.create(
            course=self.course,
            user=duplicate,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )

        result = import_course_members_csv(
            actor=self.teacher,
            course=self.course,
            csv_text=csv_text,
        )

        self.assertEqual(result['created'], 1)
        self.assertTrue(result['errors'])
        self.assertTrue(CourseMembership.objects.filter(course=self.course, user__email='new.student@example.com').exists())

    def test_teacher_can_manage_only_own_course_members(self):
        other_course = make_course('Otro curso del profesor')
        other_teacher_membership = CourseMembership.objects.create(
            course=other_course,
            user=self.other_teacher,
            role=CourseMembership.Role.OWNER,
            status=CourseMembership.Status.ACTIVE,
        )

        with self.assertRaises(PermissionDenied):
            assign_membership_to_section(
                actor=self.other_teacher,
                membership=CourseMembership.objects.create(
                    course=self.course,
                    user=User.objects.create_user('foreign_student', password='password'),
                    role=CourseMembership.Role.STUDENT,
                    status=CourseMembership.Status.ACTIVE,
                ),
                section=self.section_b,
            )

        self.assertTrue(other_teacher_membership.course_id == other_course.pk)


class LmsPageCmsTests(TestCase):
    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_syllabus_displays_text_instead_of_html_source(self):
        from bs4 import BeautifulSoup
        from .models import CourseSyllabus

        self.course.creado_por = self.teacher
        self.course.save(update_fields=['creado_por'])
        fields = ('overview', 'objectives', 'methodology', 'competencies',
                  'evaluation_criteria', 'bibliography')
        syllabus = CourseSyllabus.objects.create(
            course=self.course,
            **{field: f'<p>Texto {field}</p><style>.hidden{{color:red}}</style>'
               for field in fields},
        )
        self.course.temas.create(
            titulo='Unidad de prueba', orden=0,
            contenido='<html><head><title>Interno</title><style>.unit{color:red}</style>'
                      '</head><body><p>Introduccion de la unidad.</p></body></html>',
        )
        for user in (self.teacher, self.student):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(reverse('lms:course_area', kwargs={
                    'course_id': self.course.pk, 'area': 'syllabus',
                }))
                self.assertEqual(response.status_code, 200)
                area = BeautifulSoup(response.content, 'html.parser').select_one('.area-main')
                for field in fields:
                    self.assertIn(f'Texto {field}', area.text)
                self.assertIn('Introduccion de la unidad.', area.text)
                for code in ('<p>', '<style>', 'color:red', 'Interno'):
                    self.assertNotIn(code, area.text)
                self.assertIsNone(area.select_one('.prose p p'))
        syllabus.refresh_from_db()
        self.assertIn('<p>', syllabus.overview)

    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_page_cards_are_collapsed_accordions_with_role_actions(self):
        from bs4 import BeautifulSoup

        published = Page.objects.create(
            course=self.course, title='Pagina acordeon',
            content=(
                '<!doctype html><html><head><title>Titulo interno</title>'
                '<style>body { background: red; }</style></head>'
                '<body><p>Resumen <strong>visible</strong></p></body></html>'
            ),
            author=self.teacher, status=Page.Status.PUBLISHED, is_homepage=True,
        )
        draft = Page.objects.create(
            course=self.course, title='Borrador acordeon', author=self.teacher,
            status=Page.Status.DRAFT,
        )
        url = reverse('lms:course_pages', kwargs={'course_id': self.course.pk})
        for user, expected_pages in [
            (self.teacher, [published, draft]),
            (self.student, [published]),
        ]:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                html = BeautifulSoup(response.content, 'html.parser')
                cards = html.select('.pages-accordion > details.page-card')
                self.assertEqual(len(cards), len(expected_pages))
                for page in expected_pages:
                    card = next(
                        card for card in cards
                        if card.select_one('summary .page-card-title').text == page.title
                    )
                    self.assertFalse(card.has_attr('open'))
                    self.assertIn(page.get_status_display(), card.select_one('summary').text)
                    self.assertIsNone(card.select_one('summary a, summary button, summary form'))
                    body = card.select_one('.content-accordion-body')
                    self.assertIsNotNone(body.select_one('.meta-row'))
                    if page == published:
                        self.assertEqual(body.select_one('p').text, 'Resumen visible')
                    detail_url = reverse('lms:course_page_detail', kwargs={
                        'course_id': self.course.pk, 'page_slug': page.slug,
                    })
                    self.assertIsNotNone(body.select_one(f'a[href="{detail_url}"]'))
                    if user == self.teacher:
                        edit_url = reverse('lms:course_page_edit', kwargs={
                            'course_id': self.course.pk, 'page_slug': page.slug,
                        })
                        self.assertIsNotNone(body.select_one(f'a[href="{edit_url}"]'))
                        self.assertEqual(len(body.select('form')), 2 if page == draft else 1)
                    else:
                        self.assertIsNone(body.select_one('form'))
                        self.assertEqual(len(body.select('a')), 1)
                if user == self.student:
                    self.assertNotContains(response, draft.title)

    def test_page_intro_extracts_text_without_document_code(self):
        for content, expected in [
            ('<p>Aprende <strong>Python</strong> &amp; practica.</p><p>Siguiente paso.</p>',
             'Aprende Python & practica. Siguiente paso.'),
            ('<html><head><title>Interno</title><style>body{color:red}</style></head>'
             '<body><p>Introduccion al curso.</p><script>alert(1)</script>'
             '<template><p>Oculto</p></template></body></html>', 'Introduccion al curso.'),
            ('<style>body{color:red}</style>', ''),
            ('', ''),
        ]:
            with self.subTest(content=content):
                self.assertEqual(Page(content=content).content_intro, expected)

    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_page_intro_is_truncated_escaped_and_handles_empty_content(self):
        from bs4 import BeautifulSoup

        Page.objects.create(
            course=self.course, title='Texto largo', author=self.teacher,
            content='<p>' + ' '.join(f'palabra{i}' for i in range(25)) + '</p>',
        )
        Page.objects.create(
            course=self.course, title='Sin texto', author=self.teacher,
            content='<style>body{color:red}</style>',
        )
        Page.objects.create(
            course=self.course, title='Texto escapado', author=self.teacher,
            content='<p>&lt;img src=x onerror=alert(1)&gt;</p>',
        )
        self.client.force_login(self.teacher)
        response = self.client.get(reverse('lms:course_pages', kwargs={'course_id': self.course.pk}))
        self.assertEqual(response.status_code, 200)
        html = BeautifulSoup(response.content, 'html.parser')
        intros = {
            card.select_one('summary .page-card-title').text:
            card.select_one('.content-accordion-body > p')
            for card in html.select('.pages-accordion > details.page-card')
        }
        self.assertIn('palabra19', intros['Texto largo'].text)
        self.assertNotIn('palabra20', intros['Texto largo'].text)
        self.assertTrue(intros['Texto largo'].text.endswith('\u2026'))
        self.assertEqual(intros['Sin texto'].text, 'Sin contenido todav\u00eda.')
        self.assertEqual(intros['Texto escapado'].text, '<img src=x onerror=alert(1)>')
        self.assertIsNone(intros['Texto escapado'].find('img'))

    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_lms_screens_share_scoped_example_theme(self):
        self.client.force_login(self.teacher)
        routes = [
            ('lms:course_detail', {'course_id': self.course.pk}),
            ('lms:course_area', {'course_id': self.course.pk, 'area': 'content'}),
            ('lms:course_page_create', {'course_id': self.course.pk}),
            ('lms:course_announcement_create', {'course_id': self.course.pk}),
            ('lms:course_module_create', {'course_id': self.course.pk}),
            ('lms:course_pages', {'course_id': self.course.pk}),
        ]
        for route, kwargs in routes:
            with self.subTest(route=route):
                response = self.client.get(reverse(route, kwargs=kwargs))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, '<body class="lms-theme">')
                self.assertContains(response, '<div class="lms-content">')
                self.assertContains(response, 'lms/theme.css')
                self.assertContains(response, 'Space+Grotesk')

    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_pages_are_managed_in_the_pages_area_with_student_visibility(self):
        from bs4 import BeautifulSoup

        self.course.temas.create(titulo='Modulo acordeon', contenido='Resumen del modulo', orden=0)
        now = timezone.now()
        for title, changes in [
            ('Pagina visible', {'status': Page.Status.PUBLISHED}),
            ('Pagina borrador', {'status': Page.Status.DRAFT}),
            ('Pagina futura', {'status': Page.Status.PUBLISHED, 'publish_at': now + timedelta(days=1)}),
            ('Pagina caducada', {'status': Page.Status.PUBLISHED, 'expires_at': now - timedelta(days=1)}),
            ('Pagina profesores', {'status': Page.Status.PUBLISHED, 'visibility': Page.Visibility.TEACHERS}),
        ]:
            Page.objects.create(course=self.course, title=title, author=self.teacher, **changes)
        content_url = reverse('lms:course_area', kwargs={
            'course_id': self.course.pk, 'area': 'content',
        })
        pages_url = reverse('lms:course_pages', kwargs={'course_id': self.course.pk})
        self.client.force_login(self.teacher)
        response = self.client.get(content_url)
        content = BeautifulSoup(response.content, 'html.parser')
        module_section = content.select_one('.area-main > section')
        self.assertEqual(module_section.select_one('h2').text, 'Módulo')
        self.assertIsNotNone(module_section.select_one('[data-module-create-panel] > summary'))
        module_group = module_section.select_one('details.content-accordion-group')
        self.assertIsNotNone(module_group)
        self.assertFalse(module_group.has_attr('open'))
        self.assertIn('Todos los módulos (1)', module_group.select_one('summary').text)
        self.assertIsNotNone(module_group.select_one('.content-accordion-list > details.content-accordion-item'))
        self.assertNotContains(response, 'Crear página')
        self.assertNotContains(response, 'Pagina visible')

        response = self.client.get(pages_url)
        self.assertEqual(response.status_code, 200)
        pages = BeautifulSoup(response.content, 'html.parser')
        self.assertEqual(pages.select_one('.area-main h1').text, 'Páginas del aula')
        active_link = pages.select_one('.course-nav-link[aria-current="page"]')
        self.assertEqual(active_link.text.strip(), 'Páginas')
        self.assertEqual(active_link['href'], pages_url)
        self.assertContains(response, 'Nueva página')
        for title in [
            'Pagina visible',
            'Pagina borrador',
            'Pagina futura',
            'Pagina caducada',
            'Pagina profesores',
        ]:
            self.assertContains(response, title)
        self.assertRedirects(
            self.client.get(reverse('lms:course_area', kwargs={
                'course_id': self.course.pk, 'area': 'pages',
            })),
            pages_url,
            fetch_redirect_response=False,
        )

        self.client.force_login(self.student)
        response = self.client.get(content_url)
        self.assertNotContains(response, 'Pagina visible')
        self.assertNotContains(response, 'Crear página')
        response = self.client.get(pages_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pagina visible')
        for title in [
            'Pagina borrador',
            'Pagina futura',
            'Pagina caducada',
            'Pagina profesores',
        ]:
            self.assertNotContains(response, title)
        self.assertNotContains(response, 'Nueva página')
        self.assertNotContains(response, 'Borrar</button>')

    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_inline_module_creation_is_available_to_teachers_without_nested_forms(self):
        from bs4 import BeautifulSoup

        self.client.force_login(self.teacher)
        for route, kwargs in [
            ('lms:course_page_create', {'course_id': self.course.pk}),
            ('lms:course_area', {'course_id': self.course.pk, 'area': 'content'}),
        ]:
            response = self.client.get(reverse(route, kwargs=kwargs))
            self.assertEqual(response.status_code, 200)
            html = BeautifulSoup(response.content, 'html.parser')
            self.assertIsNotNone(html.select_one('details[data-module-create-panel]'))
            module_form = html.select_one('#module-create-form')
            self.assertIsNotNone(module_form)
            self.assertIsNone(module_form.find_parent('form'))
            for name in ['titulo', 'contenido', 'orden']:
                self.assertEqual(html.select_one(f'[name="{name}"]')['form'], 'module-create-form')
            self.assertNotContains(response, 'data-module-modal-open')
        self.client.force_login(self.student)
        response = self.client.get(reverse('lms:course_area', kwargs={
            'course_id': self.course.pk, 'area': 'content',
        }))
        self.assertNotContains(response, 'data-module-create-panel')

    def test_module_panel_creation_validates_fields_and_permissions(self):
        url = reverse('lms:course_module_create', kwargs={'course_id': self.course.pk})
        self.client.force_login(self.teacher)
        data = {'titulo': 'Modulo nuevo', 'contenido': 'Descripcion', 'orden': 4}
        response = self.client.post(url, data, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        module = self.course.temas.get(pk=response.json()['id'])
        self.assertEqual(module.titulo, data['titulo'])
        self.assertEqual(module.contenido, data['contenido'])
        self.assertEqual(module.orden, 4)
        for changes in [{'titulo': ''}, {'orden': -1}]:
            invalid = self.client.post(url, {**data, **changes}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
            self.assertEqual(invalid.status_code, 400)
            self.assertFalse(invalid.json()['ok'])
        self.assertEqual(self.course.temas.count(), 1)
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(url, data).status_code, 403)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.teacher)
        self.assertEqual(csrf_client.post(url, data).status_code, 403)

    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_prose_editor_forms_have_html_toolbar_and_no_external_upload_buttons(self):
        from .forms import AnnouncementForm, PageForm
        from django_prose_editor.widgets import ProseEditorWidget

        self.client.force_login(self.teacher)
        for form_class, route in [
            (PageForm, 'lms:course_page_create'),
            (AnnouncementForm, 'lms:course_announcement_create'),
        ]:
            with self.subTest(form=form_class.__name__):
                widget = form_class(course=self.course).fields['content'].widget
                self.assertIsInstance(widget, ProseEditorWidget)
                extensions = widget.get_config()['extensions']
                for extension in ['HTML', 'Image', 'Table', 'TableRow', 'ListItem']:
                    self.assertTrue(extensions[extension])
                response = self.client.get(reverse(route, kwargs={'course_id': self.course.pk}))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'data-django-prose-editor-configurable')
                self.assertContains(response, 'django_prose_editor/configurable.js')
                self.assertContains(response, 'type="importmap"')
                self.assertNotContains(response, 'ckeditor')
                self.assertNotContains(response, 'data-editor-image-upload')
                self.assertNotContains(response, 'data-editor-file-upload')

    def test_prose_editor_admin_only_replaces_content_widget(self):
        from django.contrib.admin.sites import AdminSite
        from django_prose_editor.widgets import AdminProseEditorWidget
        from .admin import AnnouncementAdmin, PageAdmin

        for model, admin_class in [(Page, PageAdmin), (Announcement, AnnouncementAdmin)]:
            model_admin = admin_class(model, AdminSite())
            widget = model_admin.formfield_for_dbfield(model._meta.get_field('content'), None).widget
            self.assertIsInstance(widget, AdminProseEditorWidget)
            title_widget = model_admin.formfield_for_dbfield(model._meta.get_field('title'), None).widget
            self.assertNotIsInstance(title_widget, AdminProseEditorWidget)

    def setUp(self):
        self.teacher = User.objects.create_user('page_teacher', password='password')
        self.student = User.objects.create_user('page_student', password='password')
        self.course = make_course('Curso CMS')
        CourseMembership.objects.create(
            course=self.course,
            user=self.teacher,
            role=CourseMembership.Role.OWNER,
            status=CourseMembership.Status.ACTIVE,
        )
        CourseMembership.objects.create(
            course=self.course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )

    def test_page_sanitizes_html_and_keeps_revision_history(self):
        page = Page.objects.create(
            course=self.course,
            title='Bienvenida',
            slug='bienvenida',
            content='<script>alert("x")</script><h1>Hola</h1><p>Bienvenido</p><a href="javascript:alert(1)">click</a>',
            author=self.teacher,
            status=Page.Status.PUBLISHED,
        )
        page.create_revision(user=self.teacher, summary='Primera versión')

        self.assertNotIn('<script', page.content)
        self.assertIn('<h1>Hola</h1>', page.content)
        self.assertEqual(page.revisions.count(), 1)
        self.assertTrue(PageRevision.objects.filter(page=page).exists())

    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_full_html_keeps_styles_through_form_revisions_and_isolated_rendering(self):
        from bs4 import BeautifulSoup
        from .forms import PageForm

        html = '''<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">
        <style>:root { --forest: #092e20; } body { background: var(--forest); }
        .hero { display: grid; } @media (max-width: 600px) { .hero { display: block; } }</style>
        </head><body><main id="intro" class="hero" style="color: white">
        <h1>Introducción HTML</h1><a href="#intro">Inicio</a></main>
        <script>alert(1)</script><img src="javascript:alert(1)" onerror="alert(1)">
        <meta http-equiv="refresh" content="0;url=https://example.com"></body></html>'''
        self.client.force_login(self.teacher)
        response = self.client.post(reverse('lms:course_page_create', kwargs={'course_id': self.course.pk}), {
            'title': 'HTML propio', 'slug': 'html-propio', 'status': 'published',
            'visibility': 'course', 'content': '<p>Editor visual</p>', 'html_content': html,
        })
        self.assertEqual(response.status_code, 302)
        page = Page.objects.get(course=self.course, slug='html-propio')
        self.assertTrue(page.has_own_styles)
        for value in (page.content, page.current_revision.content):
            self.assertIn('--forest: #092e20', value)
            self.assertIn('@media (max-width: 600px)', value)
            self.assertIn('id="intro"', value)
            self.assertIn('style="color: white"', value)
            self.assertNotIn('<script', value)
            self.assertNotIn('onerror', value)
            self.assertNotIn('javascript:', value)
            self.assertNotIn('http-equiv', value)
            self.assertNotIn('Editor visual', value)
        form = PageForm(instance=page, course=self.course)
        self.assertEqual(form['html_content'].value(), page.content)
        self.assertEqual(form['content'].value(), '')
        revision = page.current_revision
        page.create_revision(user=self.teacher, content='<p>Otro contenido</p>')
        page.restore_revision(revision, user=self.teacher)
        page.refresh_from_db()
        self.assertIn('--forest: #092e20', page.content)
        self.client.force_login(self.student)
        detail = self.client.get(reverse('lms:course_page_detail', kwargs={
            'course_id': self.course.pk, 'page_slug': page.slug,
        }))
        soup = BeautifulSoup(detail.content, 'html.parser')
        frame = soup.find('iframe', srcdoc=True)
        self.assertIsNotNone(frame)
        self.assertEqual(frame['sandbox'], [])
        self.assertEqual(frame['srcdoc'], page.content)
        self.assertIsNone(soup.find('main', id='intro'))
        page.is_homepage = True
        page.save()
        home = self.client.get(reverse('lms:course_detail', kwargs={'course_id': self.course.pk}))
        self.assertContains(home, 'srcdoc=')

    def test_blur_validation_returns_field_errors_without_saving(self):
        self.client.force_login(self.teacher)
        for form_type, field in [('course', 'nombre'), ('page', 'title'), ('announcement', 'title')]:
            with self.subTest(form_type=form_type):
                url = reverse('lms:form_field_validate', kwargs={'form_type': form_type})
                data = {'_course_id': self.course.pk, '_field': field, field: ''}
                invalid = self.client.post(url, data)
                self.assertEqual(invalid.status_code, 200)
                self.assertTrue(invalid.json()['errors'])
                data[field] = 'Titulo valido'
                valid = self.client.post(url, data)
                self.assertEqual(valid.status_code, 200)
                self.assertEqual(valid.json()['errors'], [])
        self.assertFalse(Page.objects.filter(course=self.course).exists())
        self.assertFalse(Announcement.objects.filter(course=self.course).exists())
        self.course.refresh_from_db()
        self.assertEqual(self.course.nombre, 'Curso CMS')

    def test_blur_validation_enforces_permissions_and_known_fields(self):
        url = reverse('lms:form_field_validate', kwargs={'form_type': 'announcement'})
        data = {'_course_id': self.course.pk, '_field': 'title', 'title': 'Titulo'}
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(url, data).status_code, 403)
        self.client.force_login(self.teacher)
        data['_field'] = 'unknown'
        self.assertEqual(self.client.post(url, data).status_code, 400)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.teacher)
        self.assertEqual(csrf_client.post(url, data).status_code, 403)

    def test_announcement_slug_is_generated_from_title_and_keeps_manual_slug(self):
        from .forms import AnnouncementForm

        for slug, expected in [('', 'nuevo-anuncio-del-curso'), ('mi-enlace', 'mi-enlace')]:
            with self.subTest(slug=slug):
                form = AnnouncementForm({
                    'title': 'Nuevo anuncio del curso', 'slug': slug,
                    'status': Announcement.Status.DRAFT, 'content': 'Texto',
                }, course=self.course)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data['slug'], expected)

    def test_page_slug_is_generated_from_title_and_keeps_manual_slug(self):
        from .forms import PageForm

        for slug, expected in [('', 'introduccion-a-la-pagina'), ('mi-enlace', 'mi-enlace')]:
            with self.subTest(slug=slug):
                form = PageForm({
                    'title': 'Introducción a la página', 'slug': slug,
                    'status': Page.Status.DRAFT, 'visibility': Page.Visibility.COURSE,
                    'content': '<p>Texto</p>',
                }, course=self.course)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data['slug'], expected)

    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_publishing_announcement_notifies_student_and_updates_home_badge(self):
        from unittest.mock import patch

        self.client.force_login(self.teacher)
        with patch('apps.lms.views.broadcast_course_update'):
            response = self.client.post(reverse('lms:course_announcement_create', kwargs={
                'course_id': self.course.pk,
            }), {
                'title': 'Nuevo aviso', 'slug': 'nuevo-aviso',
                'content': 'Contenido', 'status': Announcement.Status.PUBLISHED,
            })
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.student)
        count_url = reverse('lms:course_announcement_count', kwargs={'course_id': self.course.pk})
        with translation.override('es'):
            self.assertEqual(self.client.get(count_url).json(), {
                'count': 1, 'label': '1 anuncio pendiente',
            })
            home = self.client.get(reverse('lms:course_detail', kwargs={'course_id': self.course.pk}))
            self.assertContains(home, 'data-announcement-alert')
            self.assertContains(home, '1 anuncio pendiente')
        Notification.objects.filter(recipient=self.student).update(status=Notification.Status.READ)
        self.assertEqual(self.client.get(count_url).json()['count'], 0)

    def test_publishing_draft_notifies_once_and_draft_does_not_increase_badge(self):
        from unittest.mock import patch

        announcement = Announcement.objects.create(
            course=self.course, author=self.teacher, title='Borrador', slug='borrador',
        )
        self.client.force_login(self.student)
        count_url = reverse('lms:course_announcement_count', kwargs={'course_id': self.course.pk})
        self.assertEqual(self.client.get(count_url).json()['count'], 0)
        self.client.force_login(self.teacher)
        url = reverse('lms:course_announcement_edit', kwargs={
            'course_id': self.course.pk, 'announcement_id': announcement.pk,
        })
        with patch('apps.lms.views.broadcast_course_update'):
            for _ in range(2):
                response = self.client.post(url, {
                    'title': 'Borrador', 'slug': 'borrador',
                    'content': 'Publicado', 'status': Announcement.Status.PUBLISHED,
                })
                self.assertEqual(response.status_code, 302)
        self.assertEqual(Notification.objects.filter(
            recipient=self.student, event__event_type=NotificationEvent.Type.ANNOUNCEMENT,
        ).count(), 1)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(count_url).json()['count'], 1)

    def test_announcement_notifications_include_legacy_students_but_not_inactive_members(self):
        from .services import notify_announcement_published

        legacy_student = User.objects.create_user('legacy_announcement_student')
        inactive_student = User.objects.create_user('inactive_announcement_student')
        for user in [legacy_student, inactive_student]:
            InscripcionCurso.objects.create(usuario=user, curso=self.course)
        CourseMembership.objects.create(
            course=self.course, user=inactive_student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.SUSPENDED,
        )
        announcement = Announcement.objects.create(
            course=self.course, author=self.teacher,
            title='Aviso', slug='aviso', status=Announcement.Status.PUBLISHED,
        )
        notify_announcement_published(announcement, self.teacher)
        self.assertTrue(Notification.objects.filter(recipient=legacy_student).exists())
        self.assertFalse(Notification.objects.filter(recipient=inactive_student).exists())
        self.client.force_login(legacy_student)
        count_url = reverse('lms:course_announcement_count', kwargs={'course_id': self.course.pk})
        self.assertEqual(self.client.get(count_url).json()['count'], 1)

    def test_draft_pages_are_hidden_from_students(self):
        draft = Page.objects.create(
            course=self.course,
            title='Secreto',
            slug='secreto',
            content='<p>Contenido interno</p>',
            author=self.teacher,
            status=Page.Status.DRAFT,
        )
        self.assertFalse(draft.user_can_view(self.student))

    def test_published_pages_can_be_selected_as_course_homepage(self):
        other_course = make_course('Curso paralelo')
        CourseMembership.objects.create(
            course=other_course,
            user=self.teacher,
            role=CourseMembership.Role.OWNER,
            status=CourseMembership.Status.ACTIVE,
        )

        home_page = Page.objects.create(
            course=self.course,
            title='Inicio del curso',
            slug='inicio-del-curso',
            content='<p>Bienvenida</p>',
            author=self.teacher,
            status=Page.Status.PUBLISHED,
            is_homepage=True,
        )
        other_home = Page.objects.create(
            course=other_course,
            title='Inicio paralelo',
            slug='inicio-paralelo',
            content='<p>Otra bienvenida</p>',
            author=self.teacher,
            status=Page.Status.PUBLISHED,
            is_homepage=True,
        )

        self.assertEqual(Page.get_homepage_for_course(self.course).pk, home_page.pk)
        self.assertEqual(Page.get_homepage_for_course(other_course).pk, other_home.pk)

    def test_draft_and_expired_pages_are_not_selected_as_homepage(self):
        draft = Page.objects.create(
            course=self.course,
            title='Borrador',
            slug='borrador',
            content='<p>Oculto</p>',
            author=self.teacher,
            status=Page.Status.DRAFT,
            is_homepage=True,
        )
        expired = Page.objects.create(
            course=self.course,
            title='Caducada',
            slug='caducada',
            content='<p>Caducada</p>',
            author=self.teacher,
            status=Page.Status.PUBLISHED,
            publish_at='2020-01-01T00:00:00Z',
            expires_at='2020-01-02T00:00:00Z',
            is_homepage=False,
        )

        self.assertIsNone(Page.get_homepage_for_course(self.course))
        self.assertFalse(draft.is_currently_published())
        self.assertFalse(expired.is_currently_published())

    def test_teacher_can_set_course_homepage_via_post(self):
        page = Page.objects.create(
            course=self.course,
            title='Página principal',
            slug='pagina-principal',
            content='<p>Contenido principal</p>',
            author=self.teacher,
            status=Page.Status.PUBLISHED,
        )

        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse('lms:course_page_set_homepage', kwargs={'course_id': self.course.pk, 'page_slug': page.slug}),
        )

        self.assertEqual(response.status_code, 302)
        page.refresh_from_db()
        self.assertTrue(page.is_homepage)
        self.assertEqual(Page.get_homepage_for_course(self.course).pk, page.pk)

    def test_teacher_can_edit_course_page_via_post(self):
        page = Page.objects.create(
            course=self.course,
            title='Guía del curso',
            slug='guia-del-curso',
            content='<p>Texto inicial</p>',
            author=self.teacher,
            status=Page.Status.DRAFT,
        )

        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse('lms:course_page_edit', kwargs={'course_id': self.course.pk, 'page_slug': page.slug}),
            {
                'title': 'Guía del curso actualizada',
                'slug': 'guia-del-curso-actualizada',
                'status': Page.Status.DRAFT,
                'visibility': Page.Visibility.COURSE,
                'content': '<p>Texto actualizado</p>',
                'seo_title': '',
                'seo_description': '',
                'seo_keywords': '',
                'allow_pdf_export': 'on',
            },
        )

        self.assertEqual(response.status_code, 302)
        page.refresh_from_db()
        self.assertEqual(page.title, 'Guía del curso actualizada')
        self.assertEqual(page.slug, 'guia-del-curso-actualizada')
        self.assertIn('Texto actualizado', page.content)

    def test_teacher_can_delete_course_page_via_post(self):
        page = Page.objects.create(
            course=self.course,
            title='Página a borrar',
            slug='pagina-a-borrar',
            content='<p>Se va a eliminar</p>',
            author=self.teacher,
            status=Page.Status.DRAFT,
        )

        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse('lms:course_page_delete', kwargs={'course_id': self.course.pk, 'page_slug': page.slug}),
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Page.objects.filter(pk=page.pk).exists())

    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_teacher_can_create_course_forum_and_students_can_only_view(self):
        create_url = reverse('lms:course_forum_create', kwargs={'course_id': self.course.pk})
        area_url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'discussion'})
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(create_url).status_code, 200)
        response = self.client.get(area_url)
        self.assertContains(response, create_url)
        self.assertNotContains(response, '>Ver páginas</a>')
        response = self.client.post(create_url, {'title': '', 'content': 'Descripcion'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors.get('title'))
        self.assertFalse(DiscussionTopic.objects.filter(course=self.course).exists())
        other_course = make_course('Aula ajena')
        response = self.client.post(create_url, {
            'title': 'Foro del aula', 'content': '<p>Descripcion del foro</p>',
            'course': other_course.pk, 'author': self.student.pk,
        })
        self.assertRedirects(response, area_url, fetch_redirect_response=False)
        topic = DiscussionTopic.objects.get(title='Foro del aula')
        self.assertEqual(topic.course, self.course)
        self.assertEqual(topic.author, self.teacher)
        self.assertEqual(topic.status, DiscussionTopic.Status.OPEN)
        self.assertContains(self.client.get(area_url), topic.title)
        DiscussionTopic.objects.create(course=other_course, title='Foro ajeno')
        DiscussionTopic.objects.create(course=self.course, title='Foro borrador', status=DiscussionTopic.Status.DRAFT)
        self.client.force_login(self.student)
        response = self.client.get(area_url)
        self.assertContains(response, topic.title)
        self.assertNotContains(response, create_url)
        self.assertNotContains(response, 'Foro ajeno')
        self.assertNotContains(response, 'Foro borrador')
        self.assertEqual(self.client.get(create_url).status_code, 403)
        self.assertEqual(self.client.post(create_url, {'title': 'No permitido'}).status_code, 403)
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(reverse('lms:course_forum_create', kwargs={'course_id': other_course.pk})).status_code, 403)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.teacher)
        self.assertEqual(csrf_client.post(create_url, {'title': 'Sin CSRF'}).status_code, 403)
        validation_url = reverse('lms:form_field_validate', kwargs={'form_type': 'discussion'})
        self.assertEqual(self.client.post(validation_url, {
            '_course_id': self.course.pk, '_field': 'title', 'title': 'Nombre correcto',
        }).json()['errors'], [])

    def test_teacher_can_delete_topic_and_keep_linked_page(self):
        topic = Tema.objects.create(curso=self.course, titulo='Tema', contenido='Texto')
        page = Page.objects.create(
            course=self.course, module=topic, title='Guia', slug='guia',
            author=self.teacher,
        )
        self.client.force_login(self.teacher)
        response = self.client.post(reverse('lms:course_topic_delete', kwargs={
            'course_id': self.course.pk, 'topic_id': topic.pk,
        }))
        self.assertRedirects(
            response,
            reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'content'}),
            fetch_redirect_response=False,
        )
        self.assertFalse(Tema.objects.filter(pk=topic.pk).exists())
        page.refresh_from_db()
        self.assertIsNone(page.module_id)

    def test_deleting_topics_and_pages_requires_teacher_and_post(self):
        topic = Tema.objects.create(curso=self.course, titulo='Tema', contenido='Texto')
        page = Page.objects.create(
            course=self.course, title='Guia', slug='guia', author=self.teacher,
        )
        urls = [
            reverse('lms:course_topic_delete', kwargs={
                'course_id': self.course.pk, 'topic_id': topic.pk,
            }),
            reverse('lms:course_page_delete', kwargs={
                'course_id': self.course.pk, 'page_slug': page.slug,
            }),
        ]
        self.client.force_login(self.student)
        for url in urls:
            self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.teacher)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 405)
        other_course = make_course('Otro curso')
        other_topic = Tema.objects.create(
            curso=other_course, titulo='Otro tema', contenido='Texto',
        )
        self.assertEqual(self.client.post(reverse('lms:course_topic_delete', kwargs={
            'course_id': self.course.pk, 'topic_id': other_topic.pk,
        })).status_code, 404)
        self.assertTrue(Tema.objects.filter(pk=topic.pk).exists())
        self.assertTrue(Page.objects.filter(pk=page.pk).exists())
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.teacher)
        for url in urls:
            self.assertEqual(csrf_client.post(url).status_code, 403)

    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_scheduled_announcement_badge_at_publication_and_accordion(self):
        from unittest.mock import patch
        from bs4 import BeautifulSoup
        from django.template.defaultfilters import date as template_date
        from django.utils.timezone import localtime

        due_at = timezone.now() + timedelta(hours=1)
        announcement = Announcement.objects.create(
            course=self.course, author=self.teacher, title='Aviso programado',
            status=Announcement.Status.SCHEDULED, publish_at=due_at,
            content='<p>Contenido del aviso</p>', requires_acknowledgement=True,
        )
        expired = Announcement.objects.create(
            course=self.course, author=self.teacher, title='Aviso caducado',
            status=Announcement.Status.SCHEDULED,
            publish_at=due_at - timedelta(hours=2),
            expire_at=due_at - timedelta(hours=1),
        )
        count_url = reverse('lms:course_announcement_count', kwargs={'course_id': self.course.pk})
        self.client.force_login(self.student)
        with patch('apps.lms.services.timezone.now', return_value=due_at - timedelta(seconds=1)):
            self.assertEqual(self.client.get(count_url).json()['count'], 0)
            self.assertFalse(Notification.objects.filter(payload__announcement_id=announcement.pk).exists())
        with patch('apps.lms.services.timezone.now', return_value=due_at):
            for _ in range(2):
                self.assertEqual(self.client.get(count_url).json()['count'], 1)
            announcement.refresh_from_db()
            self.assertEqual(announcement.status, Announcement.Status.PUBLISHED)
            notices = Notification.objects.filter(recipient=self.student, payload__announcement_id=announcement.pk)
            self.assertEqual(notices.count(), 1)
            self.assertFalse(Notification.objects.filter(payload__announcement_id=expired.pk).exists())
            area_url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'announcements'})
            for user in (self.student, self.teacher):
                self.client.force_login(user)
                response = self.client.get(area_url)
                self.assertEqual(response.status_code, 200)
                accordion = BeautifulSoup(response.content, 'html.parser').select_one('details.announcement-item')
                self.assertIsNotNone(accordion)
                self.assertFalse(accordion.has_attr('open'))
                self.assertIn(announcement.title, accordion.select_one('summary').text)
                header_meta = accordion.select_one('summary .quiet-note')
                self.assertIsNotNone(header_meta)
                self.assertIn(self.teacher.get_full_name() or self.teacher.username, header_meta.text)
                self.assertIn(template_date(localtime(due_at), 'j M Y, H:i'), header_meta.text)
                self.assertIsNone(accordion.select_one('.announcement-body > .quiet-note'))
                self.assertIn('Contenido del aviso', accordion.select_one('.announcement-body').text)
            self.client.force_login(self.student)
            self.client.post(reverse('lms:course_announcement_acknowledge', kwargs={
                'course_id': self.course.pk, 'announcement_id': announcement.pk,
            }), {'acknowledged': 'on'})
            self.assertEqual(self.client.get(count_url).json()['count'], 0)
            self.assertEqual(notices.count(), 1)

    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_expiry_hides_announcement_but_preserves_teacher_archive(self):
        from unittest.mock import patch
        from .services import notify_announcement_published

        expires_at = timezone.now() + timedelta(hours=1)
        announcement = Announcement.objects.create(
            course=self.course, author=self.teacher, title='Anuncio conservado',
            status=Announcement.Status.PUBLISHED, expire_at=expires_at,
            requires_acknowledgement=True,
        )
        comment = announcement.comments.create(author=self.student, content='Comentario conservado')
        acknowledgement = announcement.acknowledgements.create(user=self.student, acknowledged_at=timezone.now())
        notify_announcement_published(announcement, self.teacher)
        count_url = reverse('lms:course_announcement_count', kwargs={'course_id': self.course.pk})
        area_url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'announcements'})
        self.client.force_login(self.student)
        with patch('apps.lms.services.timezone.now', return_value=expires_at - timedelta(seconds=1)):
            self.assertEqual(self.client.get(count_url).json()['count'], 1)
        with patch('apps.lms.services.timezone.now', return_value=expires_at):
            self.assertEqual(self.client.get(count_url).json()['count'], 0)
            self.assertNotContains(self.client.get(area_url), announcement.title)
            announcement.refresh_from_db()
            self.assertEqual(announcement.status, Announcement.Status.EXPIRED)
            self.assertTrue(announcement.comments.filter(pk=comment.pk).exists())
            self.assertTrue(announcement.acknowledgements.filter(pk=acknowledgement.pk).exists())
            self.client.force_login(self.teacher)
            response = self.client.get(area_url)
            self.assertContains(response, announcement.title)
            self.assertContains(response, 'Caducado')
            self.assertContains(response, reverse('lms:course_announcement_edit', kwargs={
                'course_id': self.course.pk, 'announcement_id': announcement.pk,
            }))

    def test_pending_announcements_ignore_deleted_hidden_and_duplicate_notices(self):
        from .ui import pending_announcements_count

        visible = Announcement.objects.create(
            course=self.course, author=self.teacher, title='Visible', slug='visible',
            status=Announcement.Status.PUBLISHED,
        )
        hidden = Announcement.objects.create(
            course=self.course, author=self.teacher, title='Borrador', slug='borrador',
        )
        deleted = Announcement.objects.create(
            course=self.course, author=self.teacher, title='Eliminado', slug='eliminado',
            status=Announcement.Status.PUBLISHED,
        )
        for index, announcement_id in enumerate([
            visible.pk, str(visible.pk), hidden.pk, deleted.pk,
        ]):
            event = NotificationEvent.objects.create(
                course=self.course, event_type=NotificationEvent.Type.ANNOUNCEMENT,
                title='Aviso', message='Texto', dedupe_key=f'pending-test-{index}',
                metadata={'announcement_id': announcement_id},
            )
            Notification.objects.create(
                course=self.course, event=event, recipient=self.student,
                title='Aviso', message='Texto', payload=event.metadata,
            )
        deleted.delete()
        self.assertEqual(pending_announcements_count(self.course, self.student), 1)
        visible.delete()
        self.assertEqual(pending_announcements_count(self.course, self.student), 0)
        hidden.delete()
        self.assertEqual(pending_announcements_count(self.course, self.student), 0)


class LmsAnnouncementTests(TestCase):
    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_announcements_tech_design_keeps_authorless_cards_and_actions(self):
        announcement = Announcement.objects.create(
            course=self.course, author=None, title='Aviso del equipo',
            content='Contenido del aviso', status='published',
        )
        self.client.force_login(self.teacher)
        url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'announcements'})
        response = self.client.get(url)
        self.assertContains(response, 'announcements-tech')
        self.assertContains(response, 'lms/knowledge-tech.css')
        self.assertContains(response, 'Equipo docente')
        self.assertContains(response, f'id="announcement-{announcement.pk}"')
        self.assertContains(response, reverse('lms:course_announcement_create', args=[self.course.pk]))
        self.client.force_login(self.student)
        response = self.client.get(url)
        self.assertContains(response, 'Contenido del aviso')
        self.assertNotContains(response, reverse('lms:course_announcement_create', args=[self.course.pk]))

    def test_opening_an_announcement_marks_only_its_own_notification_read(self):
        from .services import notify_announcement_published

        announcements = [
            Announcement.objects.create(
                course=self.course, author=self.teacher, title='Mismo título',
                status='published', content='Contenido', requires_acknowledgement=True,
            )
            for _ in range(2)
        ]
        for announcement in announcements:
            notify_announcement_published(announcement, self.teacher)
        self.client.force_login(self.student)
        url = reverse('lms:course_announcement_read', kwargs={
            'course_id': self.course.pk, 'announcement_id': announcements[0].pk,
        })
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url).json()['count'], 1)
        self.assertEqual(self.client.post(url).json()['count'], 1)
        self.assertFalse(AnnouncementAcknowledgement.objects.filter(
            announcement=announcements[0], user=self.student,
        ).exists())
        self.assertTrue(Notification.objects.filter(
            recipient=self.other_student, payload__announcement_id=announcements[0].pk,
            status=Notification.Status.UNREAD,
        ).exists())
        self.assertTrue(Notification.objects.filter(
            recipient=self.student, payload__announcement_id=announcements[1].pk,
            status=Notification.Status.UNREAD,
        ).exists())
        final_url = reverse('lms:course_announcement_read', kwargs={
            'course_id': self.course.pk, 'announcement_id': announcements[1].pk,
        })
        self.assertEqual(self.client.post(final_url).json()['count'], 0)
        count_url = reverse('lms:course_announcement_count', kwargs={'course_id': self.course.pk})
        self.assertEqual(self.client.get(count_url).json()['count'], 0)
        response = self.client.get(reverse('lms:course_area', kwargs={
            'course_id': self.course.pk, 'area': 'announcements',
        }))
        self.assertNotContains(response, 'data-announcement-pending')
        self.assertContains(response, 'Pendiente de confirmar', count=2)

    def test_marking_announcements_read_enforces_visibility_membership_and_csrf(self):
        announcement = Announcement.objects.create(
            course=self.course, author=self.teacher, title='Privado',
            status='draft', content='Contenido',
        )
        url = reverse('lms:course_announcement_read', kwargs={
            'course_id': self.course.pk, 'announcement_id': announcement.pk,
        })
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(url).status_code, 403)
        announcement.status = 'published'
        announcement.save(update_fields=['status'])
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.student)
        self.assertEqual(csrf.post(url).status_code, 403)
        foreign_url = reverse('lms:course_announcement_read', kwargs={
            'course_id': make_course('Curso ajeno').pk, 'announcement_id': announcement.pk,
        })
        self.assertIn(self.client.post(foreign_url).status_code, (403, 404))
        outsider = User.objects.create_user('announcement_read_outsider')
        self.client.force_login(outsider)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 302)

    def test_student_list_marks_only_own_unread_announcements_without_marking_them_read(self):
        from bs4 import BeautifulSoup
        from .services import notify_announcement_published

        pending = Announcement.objects.create(
            course=self.course, author=self.teacher, title='Pendiente propio',
            status=Announcement.Status.PUBLISHED, content='Contenido',
            requires_acknowledgement=True,
        )
        read = Announcement.objects.create(
            course=self.course, author=self.teacher, title='Ya leído',
            status=Announcement.Status.PUBLISHED, content='Contenido',
        )
        for announcement in (pending, read):
            notify_announcement_published(announcement, self.teacher)
        Notification.objects.filter(
            recipient=self.student, payload__announcement_id=read.pk,
        ).update(status=Notification.Status.READ)
        self.client.force_login(self.student)
        url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'announcements'})
        response = self.client.get(url)
        self.assertEqual(response.context['pending_announcement_ids'], {pending.pk})
        cards = BeautifulSoup(response.content, 'html.parser').select('.announcement-pending')
        self.assertEqual(len(cards), 1)
        self.assertIn(pending.title, cards[0].get_text())
        self.assertContains(response, 'Pendiente de leer', count=1)
        self.assertContains(response, 'Pendiente de confirmar', count=1)
        self.assertTrue(Notification.objects.filter(
            recipient=self.student, payload__announcement_id=pending.pk, status='unread',
        ).exists())
        self.client.post(
            reverse('lms:course_announcement_acknowledge', kwargs={
                'course_id': self.course.pk, 'announcement_id': pending.pk,
            }), {'acknowledged': 'on'},
        )
        self.assertNotContains(self.client.get(url), 'Pendiente de leer')
        self.client.force_login(self.other_student)
        response = self.client.get(url)
        self.assertEqual(response.context['pending_announcement_ids'], {pending.pk, read.pk})
        self.client.force_login(self.teacher)
        self.assertNotContains(self.client.get(url), 'Pendiente de leer')

    def setUp(self):
        self.teacher = User.objects.create_user('announcement_teacher', password='password')
        self.student = User.objects.create_user('announcement_student', password='password')
        self.other_student = User.objects.create_user('announcement_other_student', password='password')
        self.course = make_course('Curso de anuncios')
        self.section_a = Section.objects.create(course=self.course, title='Grupo A')
        self.section_b = Section.objects.create(course=self.course, title='Grupo B')
        CourseMembership.objects.create(
            course=self.course,
            user=self.teacher,
            role=CourseMembership.Role.OWNER,
            status=CourseMembership.Status.ACTIVE,
        )
        CourseMembership.objects.create(
            course=self.course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
            section=self.section_a,
        )
        CourseMembership.objects.create(
            course=self.course,
            user=self.other_student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
            section=self.section_b,
        )

    def test_scheduled_announcement_only_visible_inside_open_window(self):
        now = timezone.now()
        announcement = Announcement.objects.create(
            course=self.course,
            author=self.teacher,
            title='Aviso importante',
            content='<p>Contenido</p>',
            status=Announcement.Status.SCHEDULED,
            publish_at=now - timedelta(minutes=30),
            expire_at=now + timedelta(minutes=30),
        )

        self.assertTrue(announcement.is_visible_to_user(self.teacher, now=now))
        self.assertTrue(announcement.is_visible_to_user(self.student, now=now))

        announcement.expire_at = now - timedelta(minutes=5)
        announcement.save(update_fields=['expire_at'])
        self.assertFalse(announcement.is_visible_to_user(self.student, now=now))

    def test_section_targeted_announcement_is_hidden_from_other_sections(self):
        now = timezone.now()
        announcement = Announcement.objects.create(
            course=self.course,
            author=self.teacher,
            title='Tarea para grupo A',
            content='<p>Revisión</p>',
            status=Announcement.Status.PUBLISHED,
            publish_at=now - timedelta(minutes=5),
        )
        announcement.targets.create(
            target_type='section',
            section=self.section_a,
        )

        self.assertTrue(announcement.is_visible_to_user(self.student, now=now))
        self.assertFalse(announcement.is_visible_to_user(self.other_student, now=now))


class LmsHttpTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('teacher', password='password')
        self.student = User.objects.create_user('student', password='password')
        self.admin = User.objects.create_superuser(
            username='platform-admin',
            email='admin@example.test',
            password='password',
        )
        self.add_course_permission = Permission.objects.get(
            content_type__app_label='courses',
            codename='add_curso',
        )
        self.teacher.user_permissions.add(self.add_course_permission)

    def make_owned_course(self, name='Aula'):
        return create_course(actor=self.teacher, course=make_course(name))

    def test_teacher_creates_category_from_course_form(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse('lms:course_create'))
        self.assertContains(response, 'data-category-dialog')
        self.assertContains(response, 'id="category_nombre"')
        response = self.client.post(reverse('lms:course_category_create'), {
            'nombre': 'Programación web', 'descripcion': 'Cursos de desarrollo web.',
        })
        self.assertEqual(response.status_code, 201)
        category = CourseCategory.objects.get(pk=response.json()['id'])
        self.assertEqual(category.slug, 'programacion-web')
        response = self.client.post(reverse('lms:course_create'), {
            'nombre': 'Curso con categoría', 'descripcion': 'Contenido.',
            'fecha_inicio': '2026-10-13', 'categoria': category.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Curso.objects.get(nombre='Curso con categoría').categoria, category)

    def test_course_home_shows_only_recent_visible_published_announcements(self):
        self.client.force_login(self.teacher)
        course = self.make_owned_course('Portada con anuncios')
        now = timezone.now()
        for index in range(4):
            Announcement.objects.create(
                course=course, author=self.teacher, title=f'Publicado {index}',
                content='Contenido del anuncio.', status=Announcement.Status.PUBLISHED,
                publish_at=now - timedelta(hours=index + 1),
            )
        Announcement.objects.create(
            course=course, author=self.teacher, title='Borrador privado',
            content='Contenido privado.', status=Announcement.Status.DRAFT,
        )
        response = self.client.get(reverse('lms:course_detail', kwargs={'course_id': course.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['recent_announcements']), 3)
        self.assertNotContains(response, 'Publicado 3')
        self.assertNotContains(response, 'Borrador privado')
        self.assertContains(response, 'course-home-sidebar')
        self.assertContains(response, 'course-mini-calendar')
        self.assertContains(response, 'course-summary-table')

    def test_category_creation_rejects_duplicate_names_and_slugs(self):
        self.client.force_login(self.teacher)
        CourseCategory.objects.create(nombre='Programación')
        for name in ['Programación', 'programación', 'Programacion', '!!!']:
            with self.subTest(name=name):
                response = self.client.post(reverse('lms:course_category_create'), {'nombre': name})
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.json()['errors'])
        self.assertEqual(CourseCategory.objects.count(), 1)

    def test_category_creation_requires_course_creation_permission_and_csrf(self):
        self.client.force_login(self.student)
        url = reverse('lms:course_category_create')
        self.assertEqual(self.client.post(url, {'nombre': 'No autorizada'}).status_code, 403)
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(url).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.teacher)
        self.assertEqual(csrf_client.post(url, {'nombre': 'Sin CSRF'}).status_code, 403)
        self.assertFalse(CourseCategory.objects.exists())

    def test_urls_reverse_and_keep_old_slug_routes_available(self):
        course = self.make_owned_course()
        CourseSettingsLMS.objects.filter(course=course).update(legacy_slug='aula-anterior')
        self.assertEqual(reverse('lms:dashboard'), '/lms/')
        self.assertEqual(reverse('lms:course_create'), '/lms/courses/create/')
        self.assertEqual(
            reverse('lms:course_detail', kwargs={'course_id': course.pk}),
            f'/lms/courses/{course.pk}/',
        )
        self.assertEqual(
            reverse('lms:legacy_course_detail', kwargs={'course_slug': 'aula-anterior'}),
            '/lms/courses/aula-anterior/',
        )

    def test_existing_login_protects_lms_dashboard_and_first_visit_creates_profile(self):
        response = self.client.get(reverse('lms:dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

        self.client.force_login(self.student)
        response = self.client.get(reverse('lms:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'lms/dashboard.html')
        self.assertTrue(UserProfileLMS.objects.filter(user=self.student).exists())
        self.assertContains(response, 'Tu siguiente aprendizaje empieza aquí')

    def test_student_dashboard_shows_all_allowed_membership_states_and_prioritizes_deadlines(self):
        active_course = self.make_owned_course('Diseño de interfaces')
        suspended_course = self.make_owned_course('Programación avanzada')
        completed_course = self.make_owned_course('Fundamentos de IA')
        dropped_course = self.make_owned_course('No visible')
        aula = Aula.objects.create(
            nombre='Aula campus',
            direccion='Piso 1',
            horario='Lunes a viernes',
        )

        for course in [active_course, suspended_course, completed_course, dropped_course]:
            CourseMembership.objects.create(
                course=course,
                user=self.student,
                role=CourseMembership.Role.STUDENT,
                status=(
                    CourseMembership.Status.ACTIVE
                    if course == active_course
                    else CourseMembership.Status.SUSPENDED
                    if course == suspended_course
                    else CourseMembership.Status.COMPLETED
                    if course == completed_course
                    else CourseMembership.Status.DROPPED
                ),
            )

        active_eval = Evaluacion.objects.create(
            titulo='Entrega de wireframes',
            aula=aula,
            alcance_tipo=Evaluacion.ALCANCE_UF,
            curso=active_course,
            fecha_cierre=timezone.now() + timedelta(days=1),
            estado=Evaluacion.ESTADO_PUBLICADA,
        )
        Evaluacion.objects.create(
            titulo='Prueba de optimización',
            aula=aula,
            alcance_tipo=Evaluacion.ALCANCE_UF,
            curso=suspended_course,
            fecha_cierre=timezone.now() + timedelta(days=3),
            estado=Evaluacion.ESTADO_PUBLICADA,
        )
        Evaluacion.objects.create(
            titulo='Proyecto final',
            aula=aula,
            alcance_tipo=Evaluacion.ALCANCE_UF,
            curso=completed_course,
            fecha_cierre=timezone.now() + timedelta(days=5),
            estado=Evaluacion.ESTADO_PUBLICADA,
        )

        self.client.force_login(self.student)
        response = self.client.get(reverse('lms:student_dashboard'))
        suspended_detail = self.client.get(
            reverse('lms:course_detail', kwargs={'course_id': suspended_course.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(suspended_detail.status_code, 200)
        self.assertContains(response, active_course.nombre)
        self.assertContains(response, suspended_course.nombre)
        self.assertContains(response, completed_course.nombre)
        self.assertNotContains(response, dropped_course.nombre)
        self.assertContains(response, 'Próximas entregas')
        self.assertContains(response, active_eval.titulo)
        self.assertContains(response, 'Suspendido')
        self.assertContains(response, 'Completado')

    def test_existing_platform_navigation_links_to_lms(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse('users:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Campus LMS')
        self.assertContains(response, 'href="/lms/"')

    def test_teacher_course_create_edit_publish_flow(self):
        response = self.client.get(reverse('lms:course_create'))
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.teacher)
        response = self.client.get(reverse('lms:course_create'))
        self.assertContains(response, 'data-auto-slug-source="nombre"')
        self.assertContains(response, static('lms/slug.js'))
        response = self.client.post(
            reverse('lms:course_create'),
            {
                'nombre': 'Aula de Python',
                'descripcion': 'Introducción al lenguaje.',
                'fecha_inicio': '2025-01-01',
                'fecha_fin': '',
                'horas_duracion': 10,
            },
        )
        course = Curso.objects.get(nombre='Aula de Python')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse('lms:course_detail', kwargs={'course_id': course.pk}),
        )
        self.assertFalse(course.activo)
        self.assertEqual(course.slug, 'aula-de-python')

        response = self.client.post(
            reverse('lms:course_edit', kwargs={'course_id': course.pk}),
            {
                'nombre': 'Aula de Python actualizado',
                'descripcion': 'Contenido actualizado.',
                'fecha_inicio': '2025-01-01',
                'fecha_fin': '',
                'horas_duracion': 12,
            },
        )
        self.assertEqual(response.status_code, 302)
        course.refresh_from_db()
        self.assertEqual(course.nombre, 'Aula de Python actualizado')
        self.client.post(reverse('lms:course_publish', kwargs={'course_id': course.pk}))
        course.refresh_from_db()
        self.assertTrue(course.activo)
        self.assertTrue(
            AuditEvent.objects.filter(
                course=course,
                action=AuditEvent.Action.COURSE_PUBLISHED,
            ).exists()
        )

    def test_teacher_dashboard_shows_core_kpis_and_quick_actions(self):
        course = self.make_owned_course('Curso del docente')
        CourseMembership.objects.create(
            course=course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )
        self.client.force_login(self.teacher)
        response = self.client.get(reverse('lms:teacher_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Alumnos activos')
        self.assertContains(response, 'Entregas sin evaluar')
        self.assertContains(response, 'Pruebas abiertas')
        self.assertContains(response, 'Progreso de clase')
        self.assertContains(response, 'Crear anuncio')
        self.assertContains(response, 'Crear página')
        self.assertContains(response, 'Añadir módulo')
        self.assertContains(response, 'Crear actividad')
        self.assertContains(response, 'Crear prueba')

    def test_teacher_can_create_module_from_page_form(self):
        course = self.make_owned_course('Curso con módulo desde modal')
        self.client.force_login(self.teacher)

        response = self.client.post(
            reverse('lms:course_module_create', kwargs={'course_id': course.pk}),
            {
                'titulo': 'Nuevo módulo',
                'contenido': '<p>Contenido del nuevo módulo</p>',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['title'], 'Nuevo módulo')
        self.assertTrue(Tema.objects.filter(curso=course, titulo='Nuevo módulo').exists())

    def test_teacher_can_create_course_announcement(self):
        course = self.make_owned_course('Curso con anuncio')
        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse('lms:course_announcement_create', kwargs={'course_id': course.pk}),
            {
                'title': 'Novedad del curso',
                'slug': 'novedad-del-curso',
                'status': Announcement.Status.PUBLISHED,
                'content': '<p>Hola compañeros</p>',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Announcement.objects.filter(
                course=course,
                title='Novedad del curso',
                author=self.teacher,
            ).exists()
        )

    def test_teacher_can_upload_editor_asset_and_preview_image(self):
        course = self.make_owned_course('Curso con contenido multimedia')
        self.client.force_login(self.teacher)
        image = SimpleUploadedFile(
            'cover.png',
            b'\x89PNG\r\n\x1a\n' + b'0' * 30,
            content_type='image/png',
        )

        response = self.client.post(
            reverse('lms:course_editor_upload', kwargs={'course_id': course.pk}),
            {'file': image, 'upload_type': 'image'},
            format='multipart',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertIn('url', payload)
        self.assertTrue(FileAsset.objects.filter(course=course, file_name='cover.png').exists())

        inline_response = self.client.get(payload['url'] + '?inline=1')
        self.assertEqual(inline_response.status_code, 200)
        self.assertEqual(inline_response.get('Content-Type'), 'image/png')

    def test_teacher_announcement_creates_unread_notification_for_students(self):
        course = self.make_owned_course('Curso con aviso para alumnos')
        CourseMembership.objects.create(
            course=course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )
        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse('lms:course_announcement_create', kwargs={'course_id': course.pk}),
            {
                'title': 'Nuevo aviso del profesor',
                'slug': 'nuevo-aviso-del-profesor',
                'status': Announcement.Status.PUBLISHED,
                'content': '<p>Se publica una nueva actualización.</p>',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Notification.objects.filter(
                course=course,
                recipient=self.student,
                status=Notification.Status.UNREAD,
                title__icontains='Nuevo anuncio',
            ).exists()
        )

    def test_teacher_sees_draft_and_scheduled_announcements(self):
        course = self.make_owned_course('Curso con avisos pendientes')
        self.client.force_login(self.teacher)
        draft = Announcement.objects.create(
            course=course,
            author=self.teacher,
            title='Borrador docente',
            slug='borrador-docente',
            status=Announcement.Status.DRAFT,
            content='<p>Borrador visible solo para el profesor.</p>',
        )
        scheduled = Announcement.objects.create(
            course=course,
            author=self.teacher,
            title='Programado docente',
            slug='programado-docente',
            status=Announcement.Status.SCHEDULED,
            publish_at=timezone.now() + timedelta(days=1),
            content='<p>Programado para el futuro.</p>',
        )

        response = self.client.get(
            reverse('lms:course_area', kwargs={'course_id': course.pk, 'area': 'announcements'})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Borrador docente')
        self.assertContains(response, 'Programado docente')
        self.assertIn(draft, Announcement.visible_for_course(course, self.teacher))
        self.assertIn(scheduled, Announcement.visible_for_course(course, self.teacher))

    def test_teacher_can_create_announcement_requiring_acknowledgement(self):
        course = self.make_owned_course('Curso con aviso firmado')
        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse('lms:course_announcement_create', kwargs={'course_id': course.pk}),
            {
                'title': 'Aviso con acuse de recibo',
                'slug': 'aviso-con-acuse-de-recibo',
                'status': Announcement.Status.PUBLISHED,
                'content': '<p>Debe confirmarse la lectura.</p>',
                'requires_acknowledgement': 'on',
            },
        )
        self.assertEqual(response.status_code, 302)
        announcement = Announcement.objects.get(course=course, title='Aviso con acuse de recibo')
        self.assertTrue(announcement.requires_acknowledgement)

    def test_student_can_acknowledge_announcement_and_record_seen_at(self):
        course = self.make_owned_course('Curso con acuse de lectura')
        CourseMembership.objects.create(
            course=course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )
        announcement = Announcement.objects.create(
            course=course,
            author=self.teacher,
            title='Aviso con confirmación',
            slug='aviso-con-confirmacion',
            status=Announcement.Status.PUBLISHED,
            content='<p>Debes confirmar lectura.</p>',
            requires_acknowledgement=True,
        )
        self.client.force_login(self.student)
        response = self.client.post(
            reverse('lms:course_announcement_acknowledge', kwargs={'course_id': course.pk, 'announcement_id': announcement.pk}),
            {'acknowledged': 'on'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            AnnouncementAcknowledgement.objects.filter(
                announcement=announcement,
                user=self.student,
            ).exists()
        )
        ack = AnnouncementAcknowledgement.objects.get(announcement=announcement, user=self.student)
        self.assertIsNotNone(ack.acknowledged_at)

    def test_student_acknowledging_announcement_marks_related_notification_as_read(self):
        course = self.make_owned_course('Curso con badge de lectura')
        CourseMembership.objects.create(
            course=course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )
        announcement = Announcement.objects.create(
            course=course,
            author=self.teacher,
            title='Aviso con badge pendiente',
            slug='aviso-con-badge-pendiente',
            status=Announcement.Status.PUBLISHED,
            content='<p>El badge debe desaparecer después de leerlo.</p>',
            requires_acknowledgement=True,
        )
        event = NotificationEvent.objects.create(
            course=course,
            actor=self.teacher,
            target_user=self.student,
            event_type=NotificationEvent.Type.ANNOUNCEMENT,
            title=f'Nuevo anuncio: {announcement.title}',
            message='Mensaje de prueba',
            metadata={'announcement_id': announcement.pk},
            dedupe_key=f'announcement:{course.pk}:{announcement.pk}:{self.student.pk}',
        )
        notification = Notification.objects.create(
            event=event,
            recipient=self.student,
            course=course,
            title=event.title,
            message=event.message,
            status=Notification.Status.UNREAD,
            payload=event.metadata,
        )

        self.client.force_login(self.student)
        response = self.client.post(
            reverse('lms:course_announcement_acknowledge', kwargs={'course_id': course.pk, 'announcement_id': announcement.pk}),
            {'acknowledged': 'on'},
        )

        self.assertEqual(response.status_code, 302)
        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.READ)
        self.assertIsNotNone(notification.read_at)
        self.assertFalse(
            Notification.objects.filter(
                recipient=self.student,
                course=course,
                status=Notification.Status.UNREAD,
                event__event_type=NotificationEvent.Type.ANNOUNCEMENT,
                payload__announcement_id=announcement.pk,
            ).exists()
        )

    def test_teacher_can_open_course_announcement_form(self):
        course = self.make_owned_course('Curso con formulario de anuncio')
        self.client.force_login(self.teacher)
        response = self.client.get(
            reverse('lms:course_announcement_create', kwargs={'course_id': course.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Crear anuncio del curso')

    def test_expired_announcements_are_not_visible_and_comments_are_respected(self):
        course = self.make_owned_course('Curso con avisos caducados')
        CourseMembership.objects.create(
            course=course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )
        expired = Announcement.objects.create(
            course=course,
            author=self.teacher,
            title='Aviso expirado',
            slug='aviso-expirado',
            content='<p>Ya no debería verse</p>',
            status=Announcement.Status.PUBLISHED,
            allow_comments=True,
            publish_at=timezone.now() - timedelta(days=8),
            expire_at=timezone.now() - timedelta(days=1),
        )
        active = Announcement.objects.create(
            course=course,
            author=self.teacher,
            title='Aviso vigente',
            slug='aviso-vigente',
            content='<p>Sí debería verse</p>',
            status=Announcement.Status.PUBLISHED,
            allow_comments=True,
            publish_at=timezone.now() - timedelta(days=1),
            expire_at=timezone.now() + timedelta(days=3),
        )

        self.client.force_login(self.student)
        response = self.client.get(reverse('lms:course_area', kwargs={'course_id': course.pk, 'area': 'announcements'}))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, expired.title)
        self.assertContains(response, active.title)

        response = self.client.post(
            reverse('lms:course_announcement_comment', kwargs={'course_id': course.pk, 'announcement_id': active.pk}),
            {'content': 'Comentario válido'},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Comentario publicado.')
        self.assertTrue(active.comments.filter(author=self.student, content='Comentario válido').exists())

        active.allow_comments = False
        active.save(update_fields=['allow_comments'])
        response = self.client.post(
            reverse('lms:course_announcement_comment', kwargs={'course_id': course.pk, 'announcement_id': active.pk}),
            {'content': 'No debería permitirse'},
            follow=True,
        )
        self.assertContains(response, 'Este anuncio no admite comentarios.')
        self.assertFalse(active.comments.filter(content='No debería permitirse').exists())

    def test_comment_author_and_teacher_can_delete_comments(self):
        course = self.make_owned_course('Curso con comentarios editables')
        CourseMembership.objects.create(
            course=course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.ACTIVE,
        )
        announcement = Announcement.objects.create(
            course=course,
            author=self.teacher,
            title='Aviso con comentarios',
            slug='aviso-con-comentarios',
            content='<p>Texto</p>',
            status=Announcement.Status.PUBLISHED,
            allow_comments=True,
        )
        comment = announcement.comments.create(author=self.student, content='Comentario del alumno')

        self.client.force_login(self.student)
        response = self.client.post(
            reverse('lms:course_announcement_comment_delete', kwargs={'course_id': course.pk, 'comment_id': comment.pk}),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Comentario eliminado.')
        self.assertFalse(announcement.comments.filter(pk=comment.pk).exists())

        comment = announcement.comments.create(author=self.student, content='Comentario de prueba para profesor')
        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse('lms:course_announcement_comment_delete', kwargs={'course_id': course.pk, 'comment_id': comment.pk}),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Comentario eliminado.')
        self.assertFalse(announcement.comments.filter(pk=comment.pk).exists())

    def test_teacher_can_edit_and_delete_course_announcement(self):
        course = self.make_owned_course('Curso con anuncios editables')
        announcement = Announcement.objects.create(
            course=course,
            author=self.teacher,
            title='Anuncio inicial',
            slug='anuncio-inicial',
            content='<p>Texto inicial</p>',
            status=Announcement.Status.PUBLISHED,
        )

        self.client.force_login(self.teacher)

        response = self.client.get(
            reverse('lms:course_announcement_edit', kwargs={'course_id': course.pk, 'announcement_id': announcement.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Editar anuncio del curso')

        response = self.client.post(
            reverse('lms:course_announcement_edit', kwargs={'course_id': course.pk, 'announcement_id': announcement.pk}),
            {
                'title': 'Anuncio actualizado',
                'slug': 'anuncio-actualizado',
                'status': Announcement.Status.PUBLISHED,
                'content': '<p>Texto actualizado</p>',
            },
        )
        self.assertEqual(response.status_code, 302)
        announcement.refresh_from_db()
        self.assertEqual(announcement.title, 'Anuncio actualizado')
        self.assertEqual(announcement.slug, 'anuncio-actualizado')

        response = self.client.post(
            reverse('lms:course_announcement_delete', kwargs={'course_id': course.pk, 'announcement_id': announcement.pk})
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Announcement.objects.filter(pk=announcement.pk).exists())

    def test_first_course_wizard_creates_real_course_structure(self):
        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse('lms:first_course_wizard'),
            {
                'nombre': 'Mi primer curso',
                'descripcion': 'Curso de prueba para el asistente.',
                'template': 'basics',
            },
        )
        self.assertEqual(response.status_code, 302)
        course = Curso.objects.get(nombre='Mi primer curso')
        self.assertEqual(course.creado_por_id, self.teacher.pk)
        self.assertTrue(CourseMembership.objects.filter(course=course, user=self.teacher, role=CourseMembership.Role.OWNER).exists())
        self.assertTrue(course.lms_modules.filter(title='Módulo 1').exists())
        self.assertEqual(course.lms_modules.filter(title='Módulo 1').count(), 1)
        self.assertEqual(Page.objects.filter(course=course).count(), 9)
        self.assertTrue(course.lms_settings is not None)

    def test_student_enrollment_requires_csrf_and_renders_real_course(self):
        course = self.make_owned_course('Seguridad')
        publish_course(actor=self.teacher, course=course)
        self.client.force_login(self.student)
        detail = self.client.get(reverse('lms:course_detail', kwargs={'course_id': course.pk}))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Inscribirme en el curso')
        self.assertContains(detail, 'csrfmiddlewaretoken')

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.student)
        denied = csrf_client.post(
            reverse('lms:course_enroll', kwargs={'course_id': course.pk}),
        )
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(CourseMembership.objects.filter(course=course, user=self.student).exists())

        response = self.client.post(
            reverse('lms:course_enroll', kwargs={'course_id': course.pk}),
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(CourseMembership.objects.filter(course=course, user=self.student).exists())

    def test_student_and_teacher_views_have_course_scoped_navigation(self):
        course_a = self.make_owned_course('Curso A')
        course_b = self.make_owned_course('Curso B')
        CourseMembership.objects.create(
            course=course_a,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
        )
        CourseMembership.objects.create(
            course=course_b,
            user=self.student,
            role=CourseMembership.Role.TEACHER,
        )
        self.client.force_login(self.student)

        student_page = self.client.get(
            reverse('lms:course_detail', kwargs={'course_id': course_a.pk})
        )
        teacher_page = self.client.get(
            reverse('lms:course_detail', kwargs={'course_id': course_b.pk})
        )
        self.assertEqual(student_page.status_code, 200)
        self.assertNotContains(student_page, 'Banco de ítems')
        self.assertContains(student_page, 'Contenido')
        self.assertEqual(teacher_page.status_code, 200)
        self.assertContains(teacher_page, 'Banco de ítems')
        self.assertContains(teacher_page, 'Editar aula')

    def test_student_cannot_view_teacher_only_course_area_or_other_teacher_dashboard(self):
        course = self.make_owned_course()
        CourseMembership.objects.create(
            course=course,
            user=self.student,
            role=CourseMembership.Role.STUDENT,
        )
        self.client.force_login(self.student)
        settings_area = self.client.get(
            reverse(
                'lms:course_area',
                kwargs={'course_id': course.pk, 'area': 'settings'},
            ),
        )
        self.assertEqual(settings_area.status_code, 403)

        denied = self.client.get(
            reverse('lms:teacher_dashboard'),
            {'course': course.pk},
        )
        self.assertEqual(denied.status_code, 403)

    def test_course_area_and_teacher_dashboard_http_smoke(self):
        course = self.make_owned_course()
        Tema.objects.create(
            curso=course,
            titulo='Primer tema',
            contenido='<p>Contenido de prueba</p>',
        )
        self.client.force_login(self.teacher)
        detail = self.client.get(reverse('lms:course_detail', kwargs={'course_id': course.pk}))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Primer tema')
        self.assertContains(detail, 'Actividad actual')

        content = self.client.get(
            reverse(
                'lms:course_area',
                kwargs={'course_id': course.pk, 'area': 'content'},
            ),
        )
        self.assertEqual(content.status_code, 200)
        self.assertContains(content, 'Contenido y temas de aprendizaje')

        teacher_dashboard = self.client.get(reverse('lms:teacher_dashboard'))
        self.assertEqual(teacher_dashboard.status_code, 200)
        self.assertContains(teacher_dashboard, 'BACK OFFICE DOCENTE')

    def test_admin_dashboard_requires_platform_admin(self):
        self.client.force_login(self.student)
        self.assertEqual(
            self.client.get(reverse('lms:admin_dashboard')).status_code,
            403,
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse('lms:admin_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Administración de plataforma')

    def test_admin_portal_routes_are_available_for_platform_admins(self):
        self.client.force_login(self.admin)
        for name, url in (
            ('admin_dashboard', reverse('lms:admin_dashboard')),
            ('admin_courses', reverse('lms:admin_courses')),
            ('admin_users', reverse('lms:admin_users')),
            ('admin_reports', reverse('lms:admin_reports')),
            ('admin_audit', reverse('lms:admin_audit')),
        ):
            with self.subTest(name=name):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Administración')

    def test_legacy_slug_detail_redirects_to_canonical_course(self):
        course = self.make_owned_course()
        CourseSettingsLMS.objects.filter(course=course).update(legacy_slug='curso-viejo')
        self.client.force_login(self.teacher)
        response = self.client.get('/lms/courses/curso-viejo/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse('lms:course_detail', kwargs={'course_id': course.pk}),
        )

    def test_calendar_and_inbox_routes_render_states(self):
        self.client.force_login(self.student)
        for area in ('calendar', 'inbox'):
            with self.subTest(area=area):
                response = self.client.get(reverse('lms:global_area', kwargs={'area': area}))
                self.assertEqual(response.status_code, 200)

    def test_course_area_links_are_filtered_for_student(self):
        course = make_course()
        student_items = course_nav(course, teacher_view=False, active_key='home')
        teacher_items = course_nav(course, teacher_view=True, active_key='home')
        self.assertNotIn('item-bank', {item['key'] for item in student_items})
        self.assertIn('item-bank', {item['key'] for item in teacher_items})
        self.assertEqual(
            {str(label) for _key, label, _icon in COURSE_AREAS},
            {
                'Página de inicio', 'Anuncios', 'Contenido', 'Foro de discusión',
                'Notas', 'Personas', 'Páginas', 'Programa de la asignatura',
                'Pruebas', 'Banco de ítems', 'Actividades', 'Rúbricas',
                'Colaboraciones', 'Archivos', 'Competencias', 'Configuraciones',
            },
        )


class LmsConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_two_concurrent_enrollments_produce_one_membership(self):
        teacher = User.objects.create_user('concurrent-teacher', password='password')
        student = User.objects.create_user('concurrent-student', password='password')
        teacher.user_permissions.add(
            Permission.objects.get(
                content_type__app_label='courses',
                codename='add_curso',
            )
        )
        course = create_course(actor=teacher, course=make_course('Concurrencia'))
        publish_course(actor=teacher, course=course)
        barrier = Barrier(2)

        def enroll():
            close_old_connections()
            try:
                user = User.objects.get(pk=student.pk)
                selected_course = Curso.objects.get(pk=course.pk)
                barrier.wait(timeout=5)
                return enroll_student(actor=user, course=selected_course).pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: enroll(), range(2)))

        self.assertEqual(results[0], results[1])
        self.assertEqual(
            CourseMembership.objects.filter(user=student, course=course).count(),
            1,
        )
