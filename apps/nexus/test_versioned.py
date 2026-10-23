import copy
import importlib
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import connection, transaction
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import SimpleLazyObject

from apps.users.dashboard_models import PersonalGoal
from apps.users.models import AccessMembership, PurchasedEntitlement
from . import versioned_services as service
from .original_assessments import import_original
from .original_sources import MODES, SOURCES
from .models import (
    AnswerOption, Assessment, AssessmentSession, InformeTest, ModuleResult, NexusAIOperation,
    NexusEditorialEvent, NexusFamily, NexusGoalLink, NexusModality, NexusModule, NexusVersion,
    Question, SesionTest, UserAnswer,
)


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class VersionedNexusTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.free = User.objects.create_user(username='alumno1', password='only-test-password')
        cls.plus = User.objects.create_user(username='alumno2')
        cls.pro = User.objects.create_user(username='alumno3')
        cls.teacher = User.objects.create_user(username='profesor1', role='profesor')
        cls.editor = User.objects.create_superuser(username='admin1', email='admin@example.test', password='only-test-password')
        AccessMembership.objects.update_or_create(user=cls.plus, defaults={'plan': 'plus'})
        AccessMembership.objects.update_or_create(user=cls.pro, defaults={'plan': 'pro'})
        cls.module = NexusModule.objects.create(code='fixture-only', name='Fixture técnica, no instrumento', layer='FOUNDATION')
        cls.assessment = Assessment.objects.create(title='Fixture aislada: suma ponderada')
        cls.assessment.modules.add(cls.module)
        cls.questions = []
        cls.options = []
        for index in range(2):
            question = Question.objects.create(
                module=cls.module, text=f'Pregunta aritmética ficticia {index + 1}',
                question_type=Question.TYPE_MULTIPLE, order=index,
            )
            cls.questions.append(question)
            cls.options.append([
                AnswerOption.objects.create(question=question, text='Opción cero', value=0),
                AnswerOption.objects.create(question=question, text='Opción cien', value=100),
            ])

    def version(self, mode='basic', number=1, **extra):
        values = {
            'code': 'fixture-' + mode, 'version': number, 'mode': mode,
            'family': NexusFamily.objects.get(code='techmind'), 'assessment': self.assessment,
            'title': 'Fixture aislada de pruebas (no validada)',
            'purpose': 'Prueba técnica, sin finalidad psicométrica',
            'audience': 'Solo base de datos de pruebas',
            'instructions': 'Elige una opción y guarda o entrega.',
            'authorship': 'Contenido técnico sintético',
            'provenance': 'Fixture escrita para pruebas aisladas',
            'authorization': 'Contenido original de prueba',
            'foundation': 'Aritmética, no teoría psicológica',
            'limitations': 'No permite ninguna inferencia sobre una persona.',
            'reviewed_on': date.today(), 'editor': self.editor,
            'change_reason': 'Probar motor en DB aislada',
            'definition': {
                'method': 'weighted_mean_0_100_v1',
                'dimensions': {'example': {
                    'label': 'Dimensión ficticia', 'minimum_answers': 2,
                    'brief': 'Explicación breve de prueba.',
                    'explanation': 'Explicación ampliada de prueba.',
                    'recommendation': 'Revisar este ejemplo técnico.',
                    'steps': '1. Abrir ejemplo.\n2. Revisar aritmética.',
                }},
                'questions': {
                    str(self.questions[0].pk): {'dimension': 'example', 'weight': '1', 'reverse': False, 'required': True},
                    str(self.questions[1].pk): {'dimension': 'example', 'weight': '3', 'reverse': True, 'required': True},
                },
            },
            'status': 'published',
        }
        values.update(extra)
        return NexusVersion.objects.create(**values)

    def answers(self, session, first=100, second=100):
        return {
            'revision': session.edit_revision,
            f'q_{self.questions[0].pk}': str(self.options[0][int(first == 100)].pk),
            f'q_{self.questions[1].pk}': str(self.options[1][int(second == 100)].pk),
        }

    def completed(self, actor=None, version=None):
        actor = actor or self.pro
        session = service.start(actor, version or self.version())
        return service.save_answers(actor, session, self.answers(session), submit=True)

    def url(self, name, session):
        return reverse('nexus:' + name, kwargs={'pk': session.pk})

    def test_seed_is_idempotent_and_has_no_live_content(self):
        seed = importlib.import_module('apps.nexus.migrations.0020_seed_editorial_catalog').seed_catalog
        initial = Assessment.objects.count()
        editor = SimpleNamespace(connection=connection)
        seed(apps, editor)
        seed(apps, editor)
        self.assertEqual(NexusFamily.objects.count(), 7)
        self.assertEqual(NexusModality.objects.count(), 4)
        self.assertEqual(NexusVersion.objects.filter(status='draft').count(), 28)
        self.assertEqual(NexusVersion.objects.filter(status='published').count(), 0)
        self.assertEqual(Assessment.objects.count(), initial)

    def test_original_import_command_preserves_framework_version_flag(self):
        from io import StringIO
        output = StringIO()
        call_command(
            'import_nexus_originals', '--family', 'techmind', '--content-version', '2',
            stdout=output,
        )
        versions = NexusVersion.objects.filter(code__startswith='techmind-', version=2)
        self.assertEqual(versions.count(), 3)
        self.assertEqual(set(versions.values_list('status', flat=True)), {'draft'})
        self.assertFalse(versions.exclude(snapshot={}).exists())
        before = (Assessment.objects.count(), Question.objects.count())
        call_command(
            'import_nexus_originals', '--family', 'techmind', '--content-version', '2',
            stdout=output,
        )
        self.assertEqual((Assessment.objects.count(), Question.objects.count()), before)

    def test_original_can_be_published_answered_and_reported(self):
        for family in SOURCES:
            for mode in MODES:
                with self.subTest(family=family, mode=mode):
                    version = import_original(
                        family, mode, editor=self.editor, publish=True,
                        authorization='Contenido del proyecto autorizado exclusivamente para esta prueba aislada.',
                    )
                    actor = self.plus if mode == 'essential' else self.pro
                    self.client.force_login(actor)
                    session = service.start(actor, version)
                    response = self.client.get(reverse('nexus:versioned_info', kwargs={'pk': version.pk}))
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, 'nexus-info-layout')
                    self.assertContains(response, 'Comenzar o continuar')
                    questionnaire = self.client.get(self.url('versioned_take', session))
                    self.assertContains(questionnaire, 'nexus-progress-panel')
                    self.assertContains(questionnaire, 'id="nexus-questionnaire"')
                    self.assertContains(questionnaire, 'id="save-state"')
                    answers = {
                        f'q_{question["id"]}': str(question['options'][0]['id'])
                        for question in session.frozen_definition['questions']
                    }
                    answers['revision'] = session.edit_revision
                    completed = service.save_answers(actor, session, answers, submit=True)
                    self.assertEqual(completed.status, AssessmentSession.STATUS_COMPLETED)
                    self.assertTrue(completed.calculated_report['dimensions'])
                    report = self.client.get(self.url('versioned_report', completed))
                    self.assertContains(report, 'nexus-dimensions-grid')
                    self.assertContains(report, 'Descargar mi informe PDF')
                    self.assertEqual(
                        self.client.get(self.url('versioned_report', completed), {'download': 'pdf'}).status_code,
                        200,
                    )

    def test_blue_catalog_preserves_navigation_and_locked_access(self):
        version = self.version('essential')
        self.client.force_login(self.free)
        catalog = self.client.get(reverse('nexus:catalog'))
        self.assertContains(catalog, 'nexus-catalog-grid')
        self.assertContains(catalog, 'nexus/assessment.css')
        self.assertContains(catalog, 'aria-current="page"')
        self.assertContains(catalog, reverse('nexus:versioned_info', kwargs={'pk': version.pk}))
        info = self.client.get(reverse('nexus:versioned_info', kwargs={'pk': version.pk}))
        self.assertContains(info, 'Sin acceso a nuevos intentos')
        self.assertNotContains(info, 'Comenzar o continuar')
        self.assertFalse(AssessmentSession.objects.filter(candidate=self.free).exists())

    def test_catalog_groups_modalities_in_one_card_per_family(self):
        versions = [self.version(mode) for mode in ('premium', 'essential', 'advanced')]
        other = self.version(
            family=NexusFamily.objects.get(code='personasphere'), code='other-family',
        )
        self.client.force_login(self.plus)
        response = self.client.get(reverse('nexus:catalog'))
        self.assertContains(response, 'class="card nexus-evaluation"', count=2)
        groups = response.context['evaluation_groups']
        techmind = next(group for group in groups if group['family'].code == 'techmind')
        self.assertEqual(
            [item['version'].mode for item in techmind['items']],
            ['essential', 'advanced', 'premium'],
        )
        self.assertEqual([item['access'][0] for item in techmind['items']], [True, False, False])
        for version in versions + [other]:
            self.assertContains(response, reverse('nexus:versioned_info', kwargs={'pk': version.pk}))
        other_group = next(group for group in groups if group['family'].code == 'personasphere')
        self.assertEqual([item['version'].mode for item in other_group['items']], ['basic'])

    def test_incomplete_editorial_version_cannot_publish(self):
        with self.assertRaises(ValidationError):
            self.version(instructions='')
        with self.assertRaises(ValidationError):
            self.version(definition={})

    def test_editorial_rules_reject_missing_options_and_undefined_policy(self):
        self.options[1][0].delete()
        self.options[1][1].delete()
        with self.assertRaisesMessage(ValidationError, 'dos opciones'):
            self.version()

    def test_publication_freezes_content_and_records_responsible(self):
        version = self.version()
        event = NexusEditorialEvent.objects.get(version=version)
        self.assertEqual(event.actor, self.editor)
        version.instructions = 'Cambio silencioso'
        with self.assertRaisesMessage(ValidationError, 'inmutable'):
            version.save()

    def test_family_and_modality_codes_are_immutable(self):
        family = NexusFamily.objects.first()
        family.code = 'modified'
        with self.assertRaises(ValidationError):
            family.save()
        mode = NexusModality.objects.first()
        mode.code = 'premium'
        with self.assertRaises(ValidationError):
            mode.save()

    def test_plan_matrix(self):
        for mode, permitted in (
            ('basic', [True, True, True]), ('essential', [False, True, True]),
            ('advanced', [False, False, True]), ('premium', [False, False, True]),
        ):
            version = self.version(mode)
            self.assertEqual([service.access(user, version)[0] for user in (self.free, self.plus, self.pro)], permitted)

    def test_third_party_requires_explicit_access_right(self):
        version = self.version(own_content=False, entitlement_code='nexus_access_fixture')
        self.assertFalse(service.access(self.pro, version)[0])
        PurchasedEntitlement.objects.create(
            user=self.pro, code='nexus_access_fixture', source='test',
            source_reference='test-only', idempotency_key='test-nexus-access',
        )
        self.assertTrue(service.access(self.pro, version)[0])

    def test_quantity_service_units_cannot_be_unlimited_access(self):
        with self.assertRaisesMessage(ValidationError, 'nexus_access_'):
            self.version(entitlement_code='nexus_report_ai')

    def test_access_purchase_expired_or_revoked_is_rejected(self):
        version = self.version(own_content=False, entitlement_code='nexus_access_fixture')
        right = PurchasedEntitlement.objects.create(
            user=self.free, code='nexus_access_fixture', source='test',
            source_reference='test-only', idempotency_key='test-nexus-expired',
            expires_at=timezone.now(),
        )
        self.assertFalse(service.access(self.free, version)[0])
        right.expires_at = None
        right.revoked_at = timezone.now()
        right.save()
        self.assertFalse(service.access(self.free, version)[0])

    def test_fixture_not_public_or_startable(self):
        version = self.version(is_fixture=True)
        self.assertFalse(service.access(self.pro, version)[0])
        self.assertNotContains(self.client.get(reverse('nexus:catalog')), version.title)
        self.assertEqual(self.client.get(reverse('nexus:versioned_info', args=[version.pk])).status_code, 404)

    def test_lazy_user_start_and_continue_one_attempt(self):
        version = self.version()
        actor = SimpleLazyObject(lambda: self.free)
        session = service.start(actor, version)
        self.assertEqual(service.start(actor, version).pk, session.pk)
        self.assertEqual(AssessmentSession.objects.filter(editorial_version=version).count(), 1)

    def test_save_resume_then_reject_stale_tab(self):
        session = service.start(self.free, self.version())
        saved = service.save_answers(self.free, session, self.answers(session))
        self.assertEqual(saved.answers.count(), 2)
        self.assertEqual(saved.status, AssessmentSession.STATUS_IN_PROGRESS)
        form = service.questionnaire_form(saved)
        self.assertEqual(form.initial[f'q_{self.questions[0].pk}'], str(self.options[0][1].pk))
        with self.assertRaises(service.NexusConflict):
            service.save_answers(self.free, session, self.answers(session))

    def test_weighted_inversion_and_submit_idempotence(self):
        session = self.completed()
        self.assertEqual(session.calculated_report['dimensions'][0]['score'], '25.00')
        self.assertEqual(session.module_results.get().score, Decimal('25.00'))
        repeated = service.save_answers(self.pro, session, {'revision': 1}, submit=True)
        self.assertEqual(repeated.calculated_report, session.calculated_report)
        self.assertEqual(ModuleResult.objects.filter(session=session).count(), 1)
        self.assertEqual(UserAnswer.objects.filter(session=session).count(), 2)
        from apps.analytics.models import RegistroActividad
        event = RegistroActividad.objects.get(usuario=self.pro, tipo='nexus_completed')
        self.assertEqual(event.puntos_ganados, 0)
        self.assertNotIn('25.00', event.descripcion)
        with self.assertRaises(service.NexusConflict):
            service.save_answers(self.pro, session, self.answers(session))

    def test_missing_answers_no_final_result(self):
        session = service.start(self.free, self.version())
        with self.assertRaisesMessage(ValidationError, 'Faltan respuestas'):
            service.save_answers(self.free, session, {'revision': 1}, submit=True)
        session.refresh_from_db()
        self.assertFalse(session.calculated_report)
        self.assertEqual(session.status, AssessmentSession.STATUS_IN_PROGRESS)

    def test_option_from_another_question_rejected(self):
        session = service.start(self.free, self.version())
        data = self.answers(session)
        data[f'q_{self.questions[0].pk}'] = str(self.options[1][0].pk)
        with self.assertRaisesMessage(ValidationError, 'no permitida'):
            service.save_answers(self.free, session, data, submit=True)
        self.assertEqual(session.answers.count(), 0)

    def test_client_cannot_set_scores_or_send_unknown_question(self):
        session = service.start(self.free, self.version())
        self.client.force_login(self.free)
        data = self.answers(session)
        data.update({'action': 'submit', 'nexus_score': '100', 'score': '100'})
        response = self.client.post(self.url('versioned_take', session), data)
        self.assertEqual(response.status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.calculated_report['dimensions'][0]['score'], '25.00')
        second = service.start(self.free, session.editorial_version)
        data = self.answers(second)
        data.update({'action': 'save', 'q_999999': '1'})
        self.assertEqual(self.client.post(self.url('versioned_take', second), data).status_code, 400)

    def test_editorial_edits_do_not_change_active_or_historical_scores(self):
        version = self.version()
        session = service.start(self.free, version)
        original = copy.deepcopy(session.frozen_definition)
        option = self.options[0][1]
        AnswerOption.objects.filter(pk=option.pk).update(value=12, text='Etiqueta modificada')
        Question.objects.filter(pk=self.questions[0].pk).update(text='Pregunta modificada')
        completed = service.save_answers(self.free, session, self.answers(session), submit=True)
        self.assertEqual(completed.frozen_definition, original)
        self.assertEqual(completed.calculated_report['dimensions'][0]['score'], '25.00')
        self.assertEqual(service.calculate(original, {str(q.pk): '100' for q in self.questions}), completed.calculated_report)

    def test_published_references_cannot_be_deleted_even_unanswered(self):
        version = self.version()
        version.assessment.modules.clear()
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.questions[0].delete()
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.options[0][1].delete()

    def test_downgrade_keeps_report_scope_and_allows_completion_only(self):
        version = self.version('premium')
        session = service.start(self.pro, version)
        AccessMembership.objects.filter(user=self.pro).update(plan='free')
        completed = service.save_answers(self.pro, session, self.answers(session), submit=True)
        self.assertEqual(completed.report_scope, 'extended')
        with self.assertRaises(PermissionDenied):
            service.start(self.pro, version)
        self.client.force_login(self.pro)
        self.assertEqual(self.client.get(self.url('versioned_report', completed) + '?download=pdf').status_code, 200)

    def test_archive_and_deactivate_preserve_history_and_pending(self):
        version = self.version()
        session = service.start(self.free, version)
        version.status = 'archived'
        version.change_reason = 'Retirada editorial'
        version.save()
        self.assertFalse(service.access(self.free, version)[0])
        self.assertEqual(service.start(self.free, version).pk, session.pk)
        completed = service.save_answers(self.free, session, self.answers(session), submit=True)
        self.assertTrue(completed.calculated_report)
        with self.assertRaises(PermissionDenied):
            service.start(self.free, version)

    def test_modality_deactivation_and_completion_do_not_modify_identity_or_plan(self):
        version = self.version()
        role, points = self.free.role, self.free.puntos
        membership = AccessMembership.objects.get(user=self.free)
        plan = membership.plan
        session = service.start(self.free, version)
        NexusModality.objects.filter(code='basic').update(active=False)
        self.assertFalse(service.access(self.free, version)[0])
        self.assertEqual(service.start(self.free, version).pk, session.pk)
        service.save_answers(self.free, session, self.answers(session), submit=True)
        self.free.refresh_from_db()
        membership.refresh_from_db()
        self.assertEqual((self.free.role, self.free.puntos, membership.plan), (role, points, plan))
        with self.assertRaises(PermissionDenied):
            service.start(self.free, version)

    def test_other_account_teacher_and_admin_cannot_read_or_edit(self):
        session = self.completed(actor=self.free)
        for actor in (self.plus, self.teacher, self.editor):
            self.client.force_login(actor)
            for name in ('versioned_report', 'versioned_take'):
                self.assertEqual(self.client.get(self.url(name, session)).status_code, 404)
            self.assertEqual(self.client.post(self.url('versioned_goal', session), {'dimension': 'example'}).status_code, 404)
            self.assertEqual(self.client.post(self.url('versioned_ai', session), {'idempotency_key': str(uuid.uuid4())}).status_code, 404)
            with self.assertRaises(PermissionDenied):
                service.require_owner(actor, session)

    def test_basic_report_does_not_expose_extended_content_or_pdf(self):
        session = self.completed(actor=self.free)
        self.client.force_login(self.free)
        response = self.client.get(self.url('versioned_report', session))
        self.assertContains(response, 'Explicación breve de prueba')
        self.assertNotContains(response, 'Explicación ampliada de prueba')
        self.assertEqual(self.client.get(self.url('versioned_report', session) + '?download=pdf').status_code, 403)

    def test_delete_pending_attempt_requires_explicit_confirmation_and_post(self):
        version = self.version()
        session = service.start(self.free, version)
        session = service.save_answers(self.free, session, self.answers(session))
        self.client.force_login(self.free)
        url = self.url('versioned_delete', session)
        history = self.client.get(reverse('nexus:versioned_history'))
        self.assertContains(history, url)
        self.assertContains(self.client.get(self.url('versioned_take', session)), url)
        confirmation = self.client.get(url)
        self.assertContains(confirmation, 'borrado es definitivo')
        self.assertIn('no-store', confirmation['Cache-Control'])
        self.assertTrue(AssessmentSession.objects.filter(pk=session.pk).exists())
        self.assertEqual(self.client.post(url).status_code, 400)
        self.assertEqual(self.client.put(url).status_code, 405)
        self.assertTrue(UserAnswer.objects.filter(session=session).exists())
        response = self.client.post(url, {'confirm_delete': 'yes'}, follow=True)
        self.assertContains(response, 'Intento eliminado definitivamente')
        self.assertFalse(AssessmentSession.objects.filter(pk=session.pk).exists())
        self.assertFalse(UserAnswer.objects.filter(session_id=session.pk).exists())
        self.assertTrue(NexusVersion.objects.filter(pk=version.pk).exists())
        self.assertEqual(Question.objects.filter(pk__in=[q.pk for q in self.questions]).count(), 2)
        replacement = service.start(self.free, version)
        self.assertNotEqual(replacement.pk, session.pk)

    def test_delete_completed_attempt_removes_report_and_preserves_personal_goals(self):
        from apps.analytics.models import RegistroActividad
        session = self.completed()
        goal = service.create_goal(self.pro, session, 'example')
        description = goal.description
        operation = service.request_ai(self.pro, session, uuid.uuid4(), 'plan')
        self.client.force_login(self.pro)
        delete_url = self.url('versioned_delete', session)
        self.assertContains(self.client.get(self.url('versioned_report', session)), delete_url)
        self.assertContains(self.client.get(delete_url), 'Conservarás 1 objetivo personal')
        self.assertTrue(ModuleResult.objects.filter(session=session).exists())
        self.assertEqual(self.client.post(delete_url, {'confirm_delete': 'yes'}).status_code, 302)
        self.assertFalse(AssessmentSession.objects.filter(pk=session.pk).exists())
        self.assertFalse(UserAnswer.objects.filter(session_id=session.pk).exists())
        self.assertFalse(ModuleResult.objects.filter(session_id=session.pk).exists())
        self.assertFalse(NexusGoalLink.objects.filter(session_id=session.pk).exists())
        goal.refresh_from_db()
        self.assertEqual(goal.description, description)
        operation.refresh_from_db()
        self.assertIsNone(operation.session_id)
        self.assertTrue(RegistroActividad.objects.filter(usuario=self.pro, tipo='nexus_completed').exists())
        for name in ('versioned_take', 'versioned_report', 'versioned_delete'):
            self.assertEqual(self.client.get(self.url(name, session)).status_code, 404)
        self.assertEqual(self.client.get(self.url('versioned_report', session), {'download': 'pdf'}).status_code, 404)
        self.assertEqual(self.client.post(delete_url, {'confirm_delete': 'yes'}).status_code, 404)

    def test_delete_attempt_is_owner_only_even_for_admin_and_teacher(self):
        session = self.completed()
        for actor in (self.free, self.teacher, self.editor):
            self.client.force_login(actor)
            url = self.url('versioned_delete', session)
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.post(url, {'confirm_delete': 'yes'}).status_code, 404)
            with self.assertRaises(PermissionDenied):
                service.delete_attempt(actor, session)
        self.assertTrue(AssessmentSession.objects.filter(pk=session.pk).exists())

    def test_delete_attempt_requires_login_and_csrf(self):
        session = service.start(self.free, self.version())
        url = self.url('versioned_delete', session)
        self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.client.post(url, {'confirm_delete': 'yes'}).status_code, 302)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.free)
        self.assertEqual(client.post(url, {'confirm_delete': 'yes'}).status_code, 403)
        self.assertTrue(AssessmentSession.objects.filter(pk=session.pk).exists())
        response = client.get(url)
        self.assertEqual(response.status_code, 200)
        token = client.cookies['csrftoken'].value
        self.assertEqual(client.post(url, {'confirm_delete': 'yes', 'csrfmiddlewaretoken': token}).status_code, 302)
        self.assertFalse(AssessmentSession.objects.filter(pk=session.pk).exists())

    def test_delete_attempt_after_downgrade_does_not_grant_new_access(self):
        version = self.version('premium')
        session = self.completed(version=version)
        AccessMembership.objects.filter(user=self.pro).update(plan='free')
        self.client.force_login(self.pro)
        self.assertEqual(self.client.post(
            self.url('versioned_delete', session), {'confirm_delete': 'yes'},
        ).status_code, 302)
        with self.assertRaises(PermissionDenied):
            service.start(self.pro, version)

    def test_pdf_is_private_and_contains_correct_version(self):
        session = self.completed()
        self.client.force_login(self.pro)
        response = self.client.get(self.url('versioned_report', session) + '?download=pdf')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'%PDF-'))
        self.assertIn(f'nexus-{session.pk}-v1.pdf', response['Content-Disposition'])
        self.assertIn('no-store', response['Cache-Control'])
        from io import BytesIO
        from pypdf import PdfReader
        text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
        self.assertIn('25.00', text)
        self.assertIn('IA utilizada: no', text)
        self.assertNotIn('Pregunta aritmética', text)

    def test_evolution_requires_same_version_and_owner(self):
        version = self.version()
        first = self.completed(version=version)
        second = self.completed(version=version)
        self.assertEqual(service.compare(self.pro, first, second)[0]['change'], '0.00')
        third = self.completed(version=self.version(number=2))
        with self.assertRaises(ValidationError):
            service.compare(self.pro, first, third)
        with self.assertRaises(PermissionDenied):
            service.compare(self.free, first, second)

    def test_goal_reuses_existing_manual_model_without_auto_progress(self):
        session = self.completed()
        goal = service.create_goal(self.pro, session, 'example', date(2027, 1, 1))
        repeated = service.create_goal(self.pro, session, 'example')
        self.assertEqual(goal.pk, repeated.pk)
        self.assertEqual(PersonalGoal.objects.filter(user=self.pro).count(), 1)
        self.assertEqual(NexusGoalLink.objects.count(), 1)
        self.assertEqual(goal.tracking_type, 'manual')
        self.assertIsNone(goal.current_value)
        self.assertIn('Pasos aprobados', goal.description)

    def test_ai_block_is_idempotent_and_does_not_change_result(self):
        session = self.completed()
        original = session.calculated_report
        self.client.force_login(self.pro)
        key = str(uuid.uuid4())
        for _ in range(2):
            response = self.client.post(self.url('versioned_ai', session), {
                'idempotency_key': key, 'task': 'interpretation',
            })
            self.assertEqual(response.status_code, 503)
            self.assertIsNone(response.json()['energy_cost'])
        self.assertEqual(NexusAIOperation.objects.count(), 1)
        self.assertEqual(NexusAIOperation.objects.get().status, 'blocked')
        session.refresh_from_db()
        self.assertEqual(session.calculated_report, original)

    def test_ai_reused_key_for_other_task_conflicts(self):
        session = self.completed()
        key = uuid.uuid4()
        service.request_ai(self.pro, session, key, 'interpretation')
        with self.assertRaises(service.NexusConflict):
            service.request_ai(self.pro, session, key, 'plan')

    def test_demo_is_ephemeral_and_creates_no_assessment_or_user(self):
        users = get_user_model().objects.count()
        response = self.client.post(reverse('nexus:demo_list'), {'value': 'yes'})
        self.assertContains(response, 'Has elegido')
        self.assertEqual(AssessmentSession.objects.count(), 0)
        self.assertEqual(get_user_model().objects.count(), users)
        self.assertNotContains(self.client.get(reverse('nexus:demo_list')), 'Has elegido')

    def test_private_models_are_not_available_to_editorial_admin(self):
        self.assertNotIn(AssessmentSession, admin.site._registry)
        self.assertNotIn(InformeTest, admin.site._registry)

    def test_dashboard_and_progress_show_private_versioned_attempt(self):
        from apps.users.dashboard_services import _goal_metric, _nexus_data
        from apps.users.progress_services import personal_progress
        session = self.completed()
        self.assertEqual(_nexus_data(self.pro)[0]['versioned'][0].pk, session.pk)
        self.assertEqual(_goal_metric(self.pro, PersonalGoal.SourceMetric.COMPLETED_NEXUS_TESTS), 1)
        progress = personal_progress(self.pro, area='competencies')
        self.assertEqual(progress['summary']['completed_nexus_tests'], 1)
        self.assertTrue(any(row['url'] == f'/nexus/intento/{session.pk}/informe/' for row in progress['rows']))

    def test_http_pages_and_keyboard_form_hide_scoring_keys(self):
        version = self.version()
        self.client.force_login(self.free)
        started = self.client.post(reverse('nexus:versioned_start', args=[version.pk]))
        self.assertEqual(started.status_code, 302)
        response = self.client.get(started.url)
        self.assertContains(response, '<fieldset')
        self.assertContains(response, 'Sin responder')
        self.assertNotContains(response, '"reverse"')
        self.assertEqual(self.client.get(reverse('nexus:versioned_history')).status_code, 200)
        self.assertEqual(self.client.get(reverse('nexus:catalog')).status_code, 200)
        self.assertEqual(self.client.get(reverse('nexus:versioned_info', args=[version.pk])).status_code, 200)

    def test_autosave_returns_revision_and_real_progress(self):
        session = service.start(self.free, self.version())
        self.client.force_login(self.free)
        response = self.client.post(
            self.url('versioned_take', session),
            {**self.answers(session), 'action': 'save'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'revision': 2, 'answered': 2, 'total': 2})
        session.refresh_from_db()
        self.assertEqual(session.answers.count(), 2)
        self.assertEqual(session.status, 'IN_PROGRESS')

    def test_editorial_admin_cannot_inspect_private_attempts(self):
        self.client.force_login(self.editor)
        self.assertEqual(self.client.get(reverse('admin:nexus_nexusversion_changelist')).status_code, 200)
        self.assertEqual(self.client.get('/admin/nexus/assessmentsession/').status_code, 404)

    def test_csrf_and_login_enforced(self):
        self.assertEqual(self.client.get(reverse('nexus:versioned_history')).status_code, 302)
        version = self.version()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.free)
        self.assertEqual(client.post(reverse('nexus:versioned_start', args=[version.pk])).status_code, 403)
