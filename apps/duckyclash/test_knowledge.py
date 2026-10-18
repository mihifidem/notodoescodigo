from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, OperationalError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.courses.models import CourseCategory, Curso
from apps.lms.models import Question, QuestionBank, QuestionVersion

from . import tests as foundation
from .knowledge import (
    DistractorsUnavailable, KnowledgeUnavailable, LMSKnowledgeGateway, VerifiedDistractor,
    get_knowledge_gateway, validate_candidate,
)
from .models import (
    ChallengeSubcategory, Duel, DuelAnswer, DuelQuestion, DuelQuestionOptions, KnowledgePublication,
    ParticipantState,
)
from .questions import add_verified_distractors, prepare_round, public_question, submit_answer


class CertifiedTestGateway(LMSKnowledgeGateway):
    def distractors(self, question, count):
        return [VerifiedDistractor(f'Certified incorrect {index}', f'test-review:{index}') for index in range(count)]


@override_settings(
    DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.bank.ProfileBalanceGateway',
    DUCKYCLASH_PAYMENT_GATEWAY='',
    DUCKYCLASH_KNOWLEDGE_GATEWAY='apps.duckyclash.knowledge.LMSKnowledgeGateway',
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
)
class KnowledgeTests(TestCase):
    create = foundation.ClashFoundationTests.create
    accept = foundation.ClashFoundationTests.accept

    def setUp(self):
        foundation.ClashFoundationTests.setUp(self)
        self.curator = get_user_model().objects.create_superuser(
            username='knowledge_curator', email='curator@example.com', password='test1234',
        )
        self.course = Curso.objects.create(
            nombre='Banco autorizado', slug='banco-autorizado', descripcion='Pruebas',
            fecha_inicio=timezone.localdate(), creado_por=self.curator,
        )
        self.bank = QuestionBank.objects.create(
            course=self.course, owner=self.curator, title='Banco privado',
            status=QuestionBank.Status.PUBLISHED,
        )
        self.duel = self.accept(self.create())
        Duel.objects.filter(pk=self.duel.pk).update(status=Duel.Status.IN_PROGRESS)
        self.duel.refresh_from_db()
        self.version = self.publish()

    def publish(self, *, category=None, subcategory=None, title='Pregunta única', correct='A'):
        question = Question.objects.create(bank=self.bank, title=title)
        version = question.create_version(
            prompt=f'{title}: elige una opción', options=['A', 'B', 'C', 'D'],
            correct_answers=[correct], explanation='PRIVATE-EXPLANATION-SECRET',
            metadata={'validation_key': 'PRIVATE-VALIDATION-KEY', 'plain_text': True}, published=True,
        )
        KnowledgePublication.objects.create(
            version=version, category=category or self.category,
            subcategory=subcategory or self.subcategory, approved_by=self.curator,
        )
        return version

    def prepare(self, number=1):
        return prepare_round(duel_id=self.duel.pk, number=number)

    def payload(self, question, actor=None):
        question.refresh_from_db()
        return public_question(actor=actor or self.creator, question=question)

    def answer(self, question, *, actor=None, text='A'):
        payload = self.payload(question, actor)
        option = next(item for item in payload['options'] if item['text'] == text)
        return submit_answer(actor=actor or self.creator, question_id=question.pk, option_id=option['id'])

    def close(self, question):
        DuelQuestion.objects.filter(pk=question.pk).update(
            opens_at=timezone.now() - timedelta(minutes=2), closes_at=timezone.now() - timedelta(minutes=1),
        )
        # These catalog tests advance rounds without testing a damage policy.
        from .combat import apply_damage, damage_request, plan_resolution
        from .ecomotor import DamageResult
        with override_settings(DUCKYCLASH_ECOMOTOR_GATEWAY='knowledge-test-no-network'):
            row = plan_resolution(question.pk)
        apply_damage(row.pk, DamageResult(damage_request(row), (0, 0), 'knowledge-test-no-damage'))

    def test_real_lms_evaluator_is_used_and_options_are_original(self):
        question = self.prepare()
        self.assertEqual(question.version_id, self.version.pk)
        self.assertEqual(question.source_id, str(self.version.question_id))
        self.assertEqual(question.source_version, str(self.version.pk))
        self.assertEqual([option['text'] for option in self.payload(question)['options']], ['A', 'B', 'C', 'D'])
        self.assertTrue(self.answer(question).is_correct)
        self.assertFalse(self.answer(question, actor=self.opponent, text='B').is_correct)

    def test_filters_category_and_subcategory(self):
        other = CourseCategory.objects.create(nombre='Otra área')
        other_sub = ChallengeSubcategory.objects.create(category=other, name='Otra sub')
        self.publish(category=other, subcategory=other_sub)
        same_category_sub = ChallengeSubcategory.objects.create(category=self.category, name='Otra subcategoría')
        self.publish(subcategory=same_category_sub)
        candidates = LMSKnowledgeGateway().candidates(
            category_id=self.category.pk, subcategory_id=self.subcategory.pk,
        )
        self.assertEqual([item.local_version_id for item in candidates], [self.version.pk])

    def test_unapproved_and_unpublished_questions_never_enter_catalog(self):
        KnowledgePublication.objects.all().delete()
        with self.assertRaises(KnowledgeUnavailable):
            self.prepare()
        publication = KnowledgePublication.objects.create(
            version=self.version, category=self.category, subcategory=self.subcategory, approved_by=self.curator,
        )
        for model, object_id, values in (
            (KnowledgePublication, publication.pk, {'active': False}),
            (QuestionVersion, self.version.pk, {'is_published': False}),
            (Question, self.version.question_id, {'status': Question.Status.ARCHIVED}),
            (QuestionBank, self.bank.pk, {'status': QuestionBank.Status.ARCHIVED}),
        ):
            with transaction.atomic():
                model.objects.filter(pk=object_id).update(**values)
                with self.assertRaises(KnowledgeUnavailable):
                    self.prepare()
                transaction.set_rollback(True)

    def test_publishing_requires_source_permission_and_four_unique_options(self):
        publication = KnowledgePublication.objects.get(version=self.version)
        publication.approved_by = self.outsider
        with self.assertRaises(ValidationError):
            publication.save()
        publication.approved_by = self.curator
        for options in (['A', 'B'], ['A', ' a ', 'C', 'D']):
            QuestionVersion.objects.filter(pk=self.version.pk).update(options=options)
            publication.version.refresh_from_db()
            with self.assertRaises(ValidationError):
                publication.save()

    def test_single_correct_answer_verified_even_for_inconsistent_provider(self):
        item = LMSKnowledgeGateway().candidates(category_id=self.category.pk, subcategory_id=self.subcategory.pk)[0]
        for answers in ([], ['A', 'B']):
            with self.subTest(answers=answers), self.assertRaises(KnowledgeUnavailable):
                validate_candidate(replace(item, validation={'correct_answers': answers}), LMSKnowledgeGateway())

    def test_round_is_idempotent_shared_and_identity_never_repeats_in_duel(self):
        question = self.prepare()
        self.assertEqual(self.prepare().pk, question.pk)
        self.assertEqual(self.payload(question)['options'], self.payload(question, self.opponent)['options'])
        self.assertEqual(DuelQuestionOptions.objects.count(), 2)
        self.close(question)
        with self.assertRaises(KnowledgeUnavailable):
            self.prepare(2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            DuelQuestion.objects.bulk_create([DuelQuestion(
                duel=self.duel, number=2, provider='lms', source_id=question.source_id,
                source_version='new-version', snapshot={}, opens_at=timezone.now(),
                closes_at=timezone.now() + timedelta(seconds=30),
            )])

    def test_prefer_questions_unseen_by_either_player(self):
        first = self.prepare()
        self.close(first)
        another = self.accept(self.create())
        Duel.objects.filter(pk=another.pk).update(status=Duel.Status.IN_PROGRESS)
        new_version = self.publish(title='Nueva pregunta')
        second = prepare_round(duel_id=another.pk, number=1)
        self.assertEqual(second.version_id, new_version.pk)

    def test_no_overlap_and_rounds_must_be_consecutive(self):
        with self.assertRaises(ValidationError):
            self.prepare(2)
        first = self.prepare()
        self.publish(title='Siguiente')
        with self.assertRaises(ValidationError):
            self.prepare(2)
        self.close(first)
        self.assertEqual(self.prepare(2).number, 2)

    def test_round_requires_both_agreed_participants(self):
        ParticipantState.objects.filter(duel=self.duel, player=self.opponent).delete()
        with self.assertRaises(ValidationError):
            self.prepare()
        self.assertFalse(DuelQuestion.objects.exists())

    def test_browser_payload_and_html_never_include_solutions_or_private_metadata(self):
        question = self.prepare()
        payload = self.payload(question)
        self.assertEqual(set(payload), {'id', 'round', 'prompt', 'difficulty', 'opens_at', 'closes_at', 'options'})
        self.client.force_login(self.creator)
        for url in (
            reverse('duckyclash:current_question', args=[self.duel.pk]),
            reverse('duckyclash:ring', args=[self.duel.pk]),
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            for secret in ('correct_answers', 'validation_key', 'PRIVATE-', '"is_correct"', '"validation"', 'original_options'):
                self.assertNotContains(response, secret)
        response = self.client.get(reverse('duckyclash:current_question', args=[self.duel.pk]))
        self.assertIn('no-store', response['Cache-Control'])
        self.assertNotIn('source_id', response.json())

    def test_right_and_wrong_answers_get_identical_acknowledgement(self):
        question = self.prepare()
        url = reverse('duckyclash:question_answer', args=[question.pk])
        replies = []
        for actor, selected in ((self.creator, 'A'), (self.opponent, 'B')):
            self.client.force_login(actor)
            option = next(option for option in self.payload(question, actor)['options'] if option['text'] == selected)
            replies.append(self.client.post(url, {
                'option_id': option['id'], 'is_correct': 'true', 'correct_answers': ['B'],
            }).json())
        self.assertEqual(replies, [{'accepted': True}, {'accepted': True}])
        self.assertEqual(list(DuelAnswer.objects.order_by('pk').values_list('is_correct', flat=True)), [True, False])

    def test_once_per_player_and_invalid_or_cross_round_options_rejected(self):
        question = self.prepare()
        with self.assertRaises(ValidationError):
            submit_answer(actor=self.creator, question_id=question.pk, option_id='A')
        self.answer(question)
        with self.assertRaises(ValidationError):
            self.answer(question, text='B')
        self.assertEqual(DuelAnswer.objects.count(), 1)

    def test_deadline_is_server_authoritative_including_slow_validation(self):
        question = self.prepare()
        option_id = self.payload(question)['options'][0]['id']

        def slow(*args):
            return True

        with patch.object(LMSKnowledgeGateway, 'validate', side_effect=slow):
            # Frozen object retains its deadline; use the server clock to model elapsed I/O.
            with patch('apps.duckyclash.questions.timezone.now', side_effect=[
                question.opens_at, question.closes_at,
            ]):
                with self.assertRaises(ValidationError):
                    submit_answer(actor=self.creator, question_id=question.pk, option_id=option_id)
        self.assertFalse(DuelAnswer.objects.exists())

    def test_closed_questions_and_inactive_duels_do_not_accept_or_publish(self):
        question = self.prepare()
        self.close(question)
        with self.assertRaises(ValidationError):
            self.payload(question)
        Duel.objects.filter(pk=self.duel.pk).update(status=Duel.Status.SCHEDULED)
        with self.assertRaises(ValidationError):
            self.prepare(2)

    def test_grading_uses_frozen_presented_solution_not_later_edit(self):
        question = self.prepare()
        QuestionVersion.objects.filter(pk=self.version.pk).update(
            correct_answers=['B'], options=['new1', 'new2', 'new3', 'new4'],
        )
        self.assertTrue(self.answer(question, text='A').is_correct)

    def test_multiplicador_is_unavailable_with_real_lms(self):
        question = self.prepare()
        before = self.payload(question)
        with self.assertRaises(DistractorsUnavailable):
            add_verified_distractors(question_id=question.pk, target_player_id=self.opponent.pk, count=2)
        self.assertEqual(self.payload(question), before)
        self.assertEqual(len(self.payload(question, self.opponent)['options']), 4)

    @override_settings(DUCKYCLASH_KNOWLEDGE_GATEWAY='apps.duckyclash.test_knowledge.CertifiedTestGateway')
    def test_certified_distractors_only_change_target_and_remain_wrong(self):
        question = self.prepare()
        add_verified_distractors(question_id=question.pk, target_player_id=self.opponent.pk, count=2)
        self.assertEqual(len(self.payload(question)['options']), 4)
        self.assertEqual(len(self.payload(question, self.opponent)['options']), 6)
        self.assertFalse(self.answer(question, actor=self.opponent, text='Certified incorrect 0').is_correct)
        self.client.force_login(self.opponent)
        response = self.client.get(reverse('duckyclash:current_question', args=[self.duel.pk]))
        self.assertNotContains(response, 'test-review')

    @override_settings(DUCKYCLASH_KNOWLEDGE_GATEWAY='apps.duckyclash.test_knowledge.CertifiedTestGateway')
    def test_reject_true_duplicate_or_unverified_distractors(self):
        question = self.prepare()
        for extra in (
            VerifiedDistractor('A', 'claims-wrong-but-true'),
            VerifiedDistractor('B', 'already-exists'),
            VerifiedDistractor('New', ''),
        ):
            with patch.object(CertifiedTestGateway, 'distractors', return_value=[extra]):
                with self.assertRaises(DistractorsUnavailable):
                    add_verified_distractors(question_id=question.pk, target_player_id=self.opponent.pk, count=1)
        self.assertEqual(len(self.payload(question, self.opponent)['options']), 4)

    def test_missing_configuration_or_provider_change_fails_closed(self):
        question = self.prepare()
        for path in ('', 'missing.provider.Gateway', 'apps.duckyclash.bank.ProfileBalanceGateway'):
            with override_settings(DUCKYCLASH_KNOWLEDGE_GATEWAY=path):
                with self.assertRaises(KnowledgeUnavailable):
                    get_knowledge_gateway()
                with self.assertRaises(KnowledgeUnavailable):
                    self.answer(question)
        self.assertFalse(DuelAnswer.objects.exists())

    def test_permissions_and_csrf(self):
        question = self.prepare()
        url = reverse('duckyclash:question_answer', args=[question.pk])
        with self.assertRaises(PermissionDenied):
            self.payload(question, self.outsider)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(url, {'option_id': 'anything'}).status_code, 403)
        self.assertEqual(self.client.get(reverse('duckyclash:current_question', args=[self.duel.pk])).status_code, 403)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.creator)
        self.assertEqual(client.get(url).status_code, 405)
        self.assertEqual(client.post(url).status_code, 403)
        self.assertEqual(client.get(reverse('duckyclash:ring', args=[self.duel.pk])).status_code, 200)
        option_id = self.payload(question)['options'][0]['id']
        response = client.post(url, {
            'option_id': option_id, 'csrfmiddlewaretoken': client.cookies['csrftoken'].value,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'accepted': True})

    def test_grading_failure_does_not_record_an_incorrect_answer(self):
        question = self.prepare()
        option_id = self.payload(question)['options'][0]['id']
        self.client.force_login(self.creator)
        with patch.object(LMSKnowledgeGateway, 'validate', side_effect=KnowledgeUnavailable('PRIVATE-KEY')):
            response = self.client.post(
                reverse('duckyclash:question_answer', args=[question.pk]), {'option_id': option_id},
            )
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, 'PRIVATE-KEY', status_code=503)
        self.assertFalse(DuelAnswer.objects.exists())

    def test_prompt_html_is_rendered_as_text_not_executable_markup(self):
        QuestionVersion.objects.filter(pk=self.version.pk).update(
            prompt='<script>alert("x")</script>', metadata={'plain_text': True},
        )
        self.prepare()
        self.client.force_login(self.creator)
        response = self.client.get(reverse('duckyclash:ring', args=[self.duel.pk]))
        self.assertNotContains(response, '<script>alert("x")</script>')
        self.assertContains(response, '&lt;script&gt;')

    def test_operational_admin_does_not_render_private_validation_or_grades(self):
        question = self.prepare()
        answer = submit_answer(
            actor=self.creator, question_id=question.pk,
            option_id=self.payload(question)['options'][0]['id'],
        )
        self.creator.is_staff = True
        self.creator.is_superuser = True
        self.creator.save(update_fields=['is_staff', 'is_superuser'])
        self.client.force_login(self.creator)
        response = self.client.get(reverse('admin:duckyclash_duelquestion_change', args=[question.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'correct_answers')
        self.assertNotContains(response, 'field-snapshot')
        response = self.client.get(reverse('admin:duckyclash_duelanswer_change', args=[answer.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'field-is_correct')
        options = DuelQuestionOptions.objects.get(question=question, participant__player=self.creator)
        response = self.client.get(reverse('admin:duckyclash_duelquestionoptions_change', args=[options.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'field-evidence')

    def test_old_option_identifiers_cannot_answer_a_new_round(self):
        first = self.prepare()
        old_id = self.payload(first)['options'][0]['id']
        self.close(first)
        self.publish(title='Segunda')
        second = self.prepare(2)
        with self.assertRaises(ValidationError):
            submit_answer(actor=self.creator, question_id=second.pk, option_id=old_id)

    def test_taxonomy_seed_reuses_existing_categories(self):
        call_command('seed_clash_knowledge_categories', stdout=StringIO())
        call_command('seed_clash_knowledge_categories', stdout=StringIO())
        for name in ('Desarrollo web', 'Ciberseguridad', 'Sistemas', 'Gamer', 'Data e IA'):
            category = CourseCategory.objects.get(nombre=name)
            self.assertEqual(category.clash_subcategories.count(), 2)


@override_settings(
    DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.bank.ProfileBalanceGateway',
    DUCKYCLASH_PAYMENT_GATEWAY='',
    DUCKYCLASH_KNOWLEDGE_GATEWAY='apps.duckyclash.knowledge.LMSKnowledgeGateway',
)
class KnowledgeConcurrencyTests(TransactionTestCase):
    setUp = KnowledgeTests.setUp
    create = KnowledgeTests.create
    accept = KnowledgeTests.accept
    publish = KnowledgeTests.publish
    prepare = KnowledgeTests.prepare

    def test_concurrent_answers_are_unique_and_gateway_does_not_hold_db_transaction(self):
        question = self.prepare()
        option_id = public_question(actor=self.creator, question=question)['options'][0]['id']
        barrier = Barrier(2)

        def validate(*args):
            self.assertFalse(connection.in_atomic_block)
            barrier.wait(timeout=15)
            return True

        def answer():
            close_old_connections()
            try:
                return submit_answer(actor=self.creator, question_id=question.pk, option_id=option_id).pk
            except (ValidationError, IntegrityError):
                return None
            except OperationalError as exc:
                if connection.vendor != 'sqlite' or 'locked' not in str(exc).lower():
                    raise
                return None
            finally:
                close_old_connections()

        with patch.object(LMSKnowledgeGateway, 'validate', side_effect=validate):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: answer(), range(2)))
        self.assertLessEqual(sum(result is not None for result in results), 1)
        if not DuelAnswer.objects.exists():
            submit_answer(actor=self.creator, question_id=question.pk, option_id=option_id)
        self.assertEqual(DuelAnswer.objects.count(), 1)

    def test_schema_roundtrip_preserves_presented_local_question(self):
        question = self.prepare()
        leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
        try:
            MigrationExecutor(connection).migrate([('duckyclash', '0002_duel_bank_mode_duel_bank_reason_and_more')])
            state = MigrationExecutor(connection).loader.project_state(
                [('duckyclash', '0002_duel_bank_mode_duel_bank_reason_and_more')],
            ).apps
            self.assertEqual(state.get_model('duckyclash', 'DuelQuestion').objects.get(pk=question.pk).version_id, self.version.pk)
        finally:
            MigrationExecutor(connection).migrate(leaves)
        self.assertTrue(DuelQuestion.objects.filter(pk=question.pk).exists())
