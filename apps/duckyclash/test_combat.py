import uuid
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, OperationalError, close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.ducky_arenas.models import ArenaItem, UserArenaInventory
from . import test_knowledge as knowledge_tests
from .combat import apply_damage, damage_request, plan_resolution, resolve_round
from .ecomotor import (
    DamageResult, DevelopmentFakeEcoMotor, EcoMotorUnavailable, get_ecomotor,
)
from .items import DEFAULT_RULES, apply_item
from .knowledge import DistractorsUnavailable, LMSKnowledgeGateway, VerifiedDistractor
from .models import (
    Duel, DuelAnswer, DuelItemUse, DuelQuestion, DuelQuestionOptions, DuelResolution,
    HealthChange, ParticipantState, RoundResolution,
)
from .questions import prepare_round, submit_answer
from .tasks import resolve_closed_rounds


class CombatFixtures:
    create = knowledge_tests.KnowledgeTests.create
    accept = knowledge_tests.KnowledgeTests.accept
    publish = knowledge_tests.KnowledgeTests.publish
    prepare = knowledge_tests.KnowledgeTests.prepare
    payload = knowledge_tests.KnowledgeTests.payload
    answer = knowledge_tests.KnowledgeTests.answer

    def setUp(self):
        knowledge_tests.KnowledgeTests.setUp(self)
        self.question = self.prepare()
        self.player = ParticipantState.objects.get(duel=self.duel, player=self.creator)
        self.rival = ParticipantState.objects.get(duel=self.duel, player=self.opponent)
        call_command('seed_clash_items', stdout=StringIO())
        for code in DEFAULT_RULES:
            UserArenaInventory.objects.create(
                profile=self.creator.user_profile_arenas, item=ArenaItem.objects.get(code=code), quantity=5,
            )

    def use(self, code, *, actor=None, action_id=None):
        return apply_item(
            actor=actor or self.creator, question_id=self.question.pk,
            code=code, action_id=action_id or uuid.uuid4(),
        )

    def accept_current_rules(self):
        self.duel = self.accept(self.create())
        Duel.objects.filter(pk=self.duel.pk).update(status=Duel.Status.IN_PROGRESS)
        self.duel.refresh_from_db()
        self.question = self.prepare()
        self.player = ParticipantState.objects.get(duel=self.duel, player=self.creator)
        self.rival = ParticipantState.objects.get(duel=self.duel, player=self.opponent)

    def stock(self, code):
        return UserArenaInventory.objects.get(profile=self.creator.user_profile_arenas, item__code=code)

    def close(self):
        DuelQuestion.objects.filter(pk=self.question.pk).update(
            opens_at=timezone.now() - timedelta(minutes=2), closes_at=timezone.now() - timedelta(minutes=1),
        )
        DuelQuestionOptions.objects.filter(question=self.question).update(deadline=None)
        self.question.refresh_from_db()

    def fake_result(self, row, damage):
        return DamageResult(damage_request(row), damage, 'test-fixed')


@override_settings(
    DEBUG=True,
    DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.bank.ProfileBalanceGateway',
    DUCKYCLASH_PAYMENT_GATEWAY='',
    DUCKYCLASH_KNOWLEDGE_GATEWAY='apps.duckyclash.knowledge.LMSKnowledgeGateway',
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
)
class ItemTests(CombatFixtures, TestCase):
    def test_cloud_hides_options_from_rival_and_rejects_cached_answer(self):
        old = self.payload(self.question, self.opponent)['options'][0]['id']
        self.use('NUBE')
        self.assertEqual(self.payload(self.question, self.opponent)['options'], [])
        self.assertEqual(len(self.payload(self.question)['options']), 4)
        with self.assertRaises(ValidationError):
            submit_answer(actor=self.opponent, question_id=self.question.pk, option_id=old)
        row = DuelQuestionOptions.objects.get(question=self.question, participant=self.rival)
        row.hidden_until = timezone.now() - timedelta(seconds=1)
        row.save()
        self.assertEqual(len(self.payload(self.question, self.opponent)['options']), 4)
        self.assertEqual(self.stock('NUBE').quantity, 4)

    def test_accelerator_shortens_only_rival_clock_and_clamps_to_now(self):
        with override_settings(DUCKYCLASH_ITEM_RULES={'ACELERADOR': {'amount': 1000}}):
            self.accept_current_rules()
            receipt = self.use('ACELERADOR')
        row = DuelQuestionOptions.objects.get(question=self.question, participant=self.rival)
        self.assertLessEqual(row.deadline, timezone.now())
        self.assertIn('deadline', receipt.effect_snapshot)
        with self.assertRaises(ValidationError):
            self.payload(self.question, self.opponent)
        self.assertEqual(len(self.payload(self.question)['options']), 4)

    def test_potion_extends_and_caps_clock(self):
        rules = {'POCIMA_TIEMPO': {'amount': 10, 'max_extra_seconds': 12, 'cooldown': 0, 'max_per_round': 5}}
        with override_settings(DUCKYCLASH_ITEM_RULES=rules):
            self.accept_current_rules()
            self.use('POCIMA_TIEMPO')
            self.use('POCIMA_TIEMPO')
            row = DuelQuestionOptions.objects.get(question=self.question, participant=self.player)
            self.assertEqual(row.deadline, self.question.closes_at + timedelta(seconds=12))
            with self.assertRaises(ValidationError):
                self.use('POCIMA_TIEMPO')
        self.assertEqual(self.stock('POCIMA_TIEMPO').quantity, 3)

    def test_potion_allows_answer_after_original_deadline_but_blocks_next_round(self):
        self.use('POCIMA_TIEMPO')
        self.question.closes_at = timezone.now() - timedelta(seconds=1)
        DuelQuestion.objects.filter(pk=self.question.pk).update(
            opens_at=timezone.now() - timedelta(minutes=1), closes_at=self.question.closes_at,
        )
        self.assertTrue(self.answer(self.question).is_correct)
        self.publish(title='Segunda')
        with self.assertRaises(ValidationError):
            prepare_round(duel_id=self.duel.pk, number=2)
        with self.assertRaises(ValidationError):
            plan_resolution(self.question.pk)

    def test_multiplier_no_certified_provider_never_consumes(self):
        with self.assertRaises(DistractorsUnavailable):
            self.use('MULTIPLICADOR')
        self.assertEqual(self.stock('MULTIPLICADOR').quantity, 5)
        self.assertFalse(DuelItemUse.objects.exists())

    def test_multiplier_and_shield_preserve_one_correct_answer(self):
        with override_settings(DUCKYCLASH_ITEM_RULES={'ESCUDO': {'amount': 8}}):
            self.accept_current_rules()
        with patch.object(LMSKnowledgeGateway, 'distractors', create=True, return_value=[
            VerifiedDistractor('Verified extra 1', 'review:1'), VerifiedDistractor('Verified extra 2', 'review:2'),
        ]):
            self.use('MULTIPLICADOR')
        row = DuelQuestionOptions.objects.get(question=self.question, participant=self.rival)
        self.assertEqual(len(row.options), 6)
        UserArenaInventory.objects.create(
            profile=self.opponent.user_profile_arenas, item=ArenaItem.objects.get(code='ESCUDO'), quantity=1,
        )
        with override_settings(DUCKYCLASH_ITEM_RULES={'ESCUDO': {'amount': 8}}):
            self.use('ESCUDO', actor=self.opponent)
        payload = self.payload(self.question, self.opponent)
        self.assertEqual([option['text'] for option in payload['options']], ['A'])
        self.assertTrue(self.answer(self.question, actor=self.opponent).is_correct)
        self.assertEqual(UserArenaInventory.objects.get(
            profile=self.opponent.user_profile_arenas, item__code='ESCUDO',
        ).quantity, 0)

    def test_multiplier_rejects_duplicate_uncertified_or_correct_distractors(self):
        for extras in (
            [VerifiedDistractor('A', 'review'), VerifiedDistractor('Other', 'review')],
            [VerifiedDistractor('B', 'review'), VerifiedDistractor('Other', 'review')],
            [VerifiedDistractor('Extra', ''), VerifiedDistractor('Other', 'review')],
            [VerifiedDistractor('Extra', 'review')],
        ):
            with self.subTest(extras=extras), patch.object(
                LMSKnowledgeGateway, 'distractors', create=True, return_value=extras,
            ), self.assertRaises(DistractorsUnavailable):
                self.use('MULTIPLICADOR')
        self.assertEqual(self.stock('MULTIPLICADOR').quantity, 5)

    def test_shield_removes_only_false_visible_options(self):
        self.use('ESCUDO')
        payload = self.payload(self.question)
        self.assertEqual(len(payload['options']), 2)
        self.assertIn('A', [option['text'] for option in payload['options']])
        self.assertEqual(len(self.payload(self.question, self.opponent)['options']), 4)

    def test_shield_rejects_inconsistent_validator_without_stock_loss(self):
        with patch.object(LMSKnowledgeGateway, 'validate', return_value=True), self.assertRaises(ValidationError):
            self.use('ESCUDO')
        self.assertEqual(self.stock('ESCUDO').quantity, 5)

    def test_heal_caps_at_initial_max_and_records_before_after(self):
        ParticipantState.objects.filter(pk=self.player.pk).update(hp=95, max_hp=150)
        self.use('BOTIQUIN')
        self.player.refresh_from_db()
        self.assertEqual(self.player.hp, 100)
        audit = HealthChange.objects.get()
        self.assertEqual((audit.before, audit.after, audit.requested, audit.kind), (95, 100, 20, 'healing'))
        with self.assertRaises(ValidationError):
            audit.after = 99
            audit.save()

    def test_heal_full_or_dead_player_never_consumes(self):
        for hp in (100, 0):
            ParticipantState.objects.filter(pk=self.player.pk).update(hp=hp)
            with self.subTest(hp=hp), self.assertRaises(ValidationError):
                self.use('BOTIQUIN')
        self.assertEqual(self.stock('BOTIQUIN').quantity, 5)
        self.assertFalse(HealthChange.objects.exists())

    def test_every_item_requires_owned_active_stock_and_live_round(self):
        for code in DEFAULT_RULES:
            entry = self.stock(code)
            entry.quantity = 0
            entry.save()
            with self.subTest(code=code), self.assertRaises(ValidationError):
                self.use(code)
        self.assertFalse(DuelItemUse.objects.exists())

    def test_inactive_and_equipped_items_are_not_consumable(self):
        entry = self.stock('NUBE')
        for active, equipped in ((False, False), (True, True)):
            ArenaItem.objects.filter(pk=entry.item_id).update(isActive=active)
            UserArenaInventory.objects.filter(pk=entry.pk).update(isEquipped=equipped)
            with self.assertRaises(ValidationError):
                self.use('NUBE')
        self.assertEqual(self.stock('NUBE').quantity, 5)

    def test_last_unit_consumed_once_and_replay_after_close_returns_receipt(self):
        entry = self.stock('NUBE')
        entry.quantity = 1
        entry.save()
        action = uuid.uuid4()
        first = self.use('NUBE', action_id=action)
        self.close()
        self.assertEqual(self.use('NUBE', action_id=action).pk, first.pk)
        self.assertEqual(self.stock('NUBE').quantity, 0)
        self.assertEqual(DuelItemUse.objects.count(), 1)
        with self.assertRaises(ValidationError):
            self.use('ESCUDO', action_id=action)

    def test_replay_of_heal_does_not_heal_twice(self):
        ParticipantState.objects.filter(pk=self.player.pk).update(hp=30)
        action = uuid.uuid4()
        self.use('BOTIQUIN', action_id=action)
        self.use('BOTIQUIN', action_id=action)
        self.player.refresh_from_db()
        self.assertEqual(self.player.hp, 50)
        self.assertEqual(HealthChange.objects.count(), 1)

    def test_round_limit_cooldown_and_total_limit(self):
        self.use('NUBE')
        with self.assertRaises(ValidationError):
            self.use('NUBE')
        with override_settings(DUCKYCLASH_ITEM_RULES={'NUBE': {'max_per_round': 5}}):
            with self.assertRaises(ValidationError):
                self.use('NUBE')
        with override_settings(DUCKYCLASH_ITEM_RULES={'NUBE': {'max_per_round': 5, 'cooldown': 0, 'max_uses': 1}}):
            with self.assertRaises(ValidationError):
                self.use('NUBE')
        self.assertEqual(self.stock('NUBE').quantity, 4)

    def test_answered_players_and_closed_rounds_cannot_activate_effects(self):
        self.answer(self.question, actor=self.opponent)
        with self.assertRaises(ValidationError):
            self.use('NUBE')
        self.answer(self.question)
        with self.assertRaises(ValidationError):
            self.use('ESCUDO')
        self.close()
        with self.assertRaises(ValidationError):
            self.use('POCIMA_TIEMPO')

    def test_options_changed_during_gateway_validation_are_rejected(self):
        original = LMSKnowledgeGateway.validate
        changed = False

        def validate(gateway, contract, selected):
            nonlocal changed
            if not changed:
                changed = True
                row = DuelQuestionOptions.objects.get(question=self.question, participant=self.player)
                row.options = row.options[:3]
                row.save()
            return original(gateway, contract, selected)

        with patch.object(LMSKnowledgeGateway, 'validate', new=validate), self.assertRaises(ValidationError):
            self.use('ESCUDO')
        self.assertEqual(self.stock('ESCUDO').quantity, 5)

    def test_rollback_on_consumption_failure_restores_effect_and_receipt(self):
        with patch('apps.duckyclash.items.ShopGateway.consume', side_effect=ValidationError('stock changed')):
            with self.assertRaises(ValidationError):
                self.use('NUBE')
        self.assertFalse(DuelItemUse.objects.exists())
        self.assertIsNone(DuelQuestionOptions.objects.get(question=self.question, participant=self.rival).hidden_until)

    def test_invalid_action_unknown_item_and_outsider(self):
        with self.assertRaises(ValidationError):
            self.use('NUBE', action_id='not-a-uuid')
        with self.assertRaises(ValidationError):
            self.use('UNTRUSTED')
        with self.assertRaises(PermissionDenied):
            self.use('NUBE', actor=self.outsider)

    def test_http_permissions_csrf_get_and_real_inventory_buttons(self):
        url = reverse('duckyclash:question_item', args=[self.question.pk])
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.creator)
        self.assertEqual(client.get(url).status_code, 405)
        self.assertEqual(client.post(url, {'code': 'NUBE', 'action_id': uuid.uuid4()}).status_code, 403)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(url, {'code': 'NUBE', 'action_id': uuid.uuid4()}).status_code, 403)
        self.client.force_login(self.creator)
        response = self.client.get(reverse('duckyclash:ring', args=[self.duel.pk]))
        self.assertContains(response, 'Nube (5)')
        self.assertContains(response, 'clash-options')
        action = uuid.uuid4()
        for _ in range(2):
            self.assertEqual(self.client.post(url, {'code': 'NUBE', 'action_id': action}).status_code, 302)
        self.assertEqual(self.stock('NUBE').quantity, 4)
        self.client.force_login(self.opponent)
        data = self.client.get(reverse('duckyclash:ring_state', args=[self.duel.pk])).json()
        self.assertEqual(data['question']['options'], [])
        for secret in ('correct_answers', 'validation', 'evidence', 'PRIVATE-'):
            self.assertNotIn(secret, str(data))

    def test_seed_is_idempotent_and_does_not_grant_inventory(self):
        before = UserArenaInventory.objects.count()
        call_command('seed_clash_items', stdout=StringIO())
        self.assertEqual(ArenaItem.objects.filter(code__in=DEFAULT_RULES).count(), 6)
        self.assertEqual(UserArenaInventory.objects.count(), before)


@override_settings(
    DEBUG=True,
    DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.bank.ProfileBalanceGateway',
    DUCKYCLASH_PAYMENT_GATEWAY='',
    DUCKYCLASH_KNOWLEDGE_GATEWAY='apps.duckyclash.knowledge.LMSKnowledgeGateway',
    DUCKYCLASH_ECOMOTOR_GATEWAY='apps.duckyclash.ecomotor.DevelopmentFakeEcoMotor',
    DUCKYCLASH_DOUBLE_KO='draw',
    DUCKYCLASH_MAX_TIEBREAKS=1,
)
class CombatTests(CombatFixtures, TransactionTestCase):
    def test_both_correct_are_processed_simultaneously_and_retry_does_not_damage_twice(self):
        self.answer(self.question)
        self.answer(self.question, actor=self.opponent)
        self.close()
        first = resolve_round(self.question.pk)
        second = resolve_round(self.question.pk)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.outcome, 'continue')
        self.assertEqual(list(self.duel.participants.order_by('seat').values_list('hp', flat=True)), [75, 75])
        self.assertEqual(HealthChange.objects.count(), 2)
        self.assertEqual(first.request_payload['fighters'][0]['correct'], True)

    def test_one_correct_produces_server_winner_only_after_both_results(self):
        ParticipantState.objects.filter(pk=self.rival.pk).update(hp=10)
        self.answer(self.question)
        self.answer(self.question, actor=self.opponent, text='B')
        self.close()
        result = resolve_round(self.question.pk)
        self.assertEqual(result.outcome, 'victory')
        resolution = DuelResolution.objects.get(duel=self.duel)
        self.assertEqual(resolution.winner_id, self.creator.pk)
        self.assertFalse(resolution.is_draw)
        self.duel.refresh_from_db()
        self.assertEqual(self.duel.status, Duel.Status.SETTLEMENT_PENDING)
        self.assertEqual(self.rival.health_changes.get().after, 0)
        self.assertFalse(self.duel.economic_operations.filter(kind='settle').exists())

    def test_double_ko_is_draw_and_single_resolution(self):
        ParticipantState.objects.filter(duel=self.duel).update(hp=10)
        self.answer(self.question)
        self.answer(self.question, actor=self.opponent)
        self.close()
        resolve_round(self.question.pk)
        resolve_round(self.question.pk)
        self.assertTrue(DuelResolution.objects.get(duel=self.duel).is_draw)
        self.assertEqual(list(self.duel.participants.values_list('hp', flat=True)), [0, 0])
        self.assertEqual(HealthChange.objects.count(), 2)

    def test_tiebreak_is_explicit_bounded_reset_not_item_resurrection(self):
        with override_settings(DUCKYCLASH_DOUBLE_KO='tiebreak'):
            self.accept_current_rules()
            self.close()
            row = plan_resolution(self.question.pk)
        result = apply_damage(row.pk, self.fake_result(row, (1000, 1000)))
        self.assertEqual(result.outcome, 'tiebreak')
        self.assertEqual(list(self.duel.participants.values_list('hp', flat=True)), [100, 100])
        self.assertEqual(HealthChange.objects.filter(kind='tiebreak').count(), 2)
        self.assertFalse(DuelResolution.objects.exists())
        self.publish(title='Desempate')
        second = prepare_round(duel_id=self.duel.pk, number=2)
        DuelQuestion.objects.filter(pk=second.pk).update(
            opens_at=timezone.now() - timedelta(seconds=30), closes_at=timezone.now() - timedelta(seconds=1),
        )
        with override_settings(DUCKYCLASH_DOUBLE_KO='tiebreak'):
            other = plan_resolution(second.pk)
        result = apply_damage(other.pk, self.fake_result(other, (1000, 1000)))
        self.assertEqual(result.outcome, 'draw')
        self.assertTrue(DuelResolution.objects.get().is_draw)

    def test_hp_clamp_and_healing_then_damage_are_audited(self):
        ParticipantState.objects.filter(pk=self.player.pk).update(hp=20)
        self.use('BOTIQUIN')
        self.close()
        row = plan_resolution(self.question.pk)
        apply_damage(row.pk, self.fake_result(row, (1000000, 0)))
        audit = list(self.player.health_changes.order_by('pk').values_list('before', 'after', 'kind'))
        self.assertEqual(audit, [(20, 40, 'healing'), (40, 0, 'damage')])
        self.assertEqual(DuelResolution.objects.get().winner_id, self.opponent.pk)

    def test_waits_for_both_deadlines_and_new_round_waits_for_damage(self):
        self.use('POCIMA_TIEMPO')
        DuelQuestion.objects.filter(pk=self.question.pk).update(
            opens_at=timezone.now() - timedelta(minutes=1), closes_at=timezone.now() - timedelta(seconds=1),
        )
        with self.assertRaises(ValidationError):
            resolve_round(self.question.pk)
        self.close()
        self.publish(title='Otra ronda')
        with self.assertRaises(ValidationError):
            prepare_round(duel_id=self.duel.pk, number=2)
        resolve_round(self.question.pk)
        self.assertEqual(prepare_round(duel_id=self.duel.pk, number=2).number, 2)

    def test_temporary_timeout_retries_same_frozen_input_outside_transaction(self):
        self.answer(self.question)
        self.close()
        calls = []

        def calculate(request):
            self.assertFalse(connection.in_atomic_block)
            calls.append(request)
            if len(calls) == 1:
                raise TimeoutError()
            return DamageResult(request, (0, 25), 'test-retry')

        with patch.object(DevelopmentFakeEcoMotor, 'calculate', side_effect=calculate):
            result = resolve_round(self.question.pk)
            self.assertEqual(result.status, RoundResolution.Status.PENDING)
            self.assertFalse(HealthChange.objects.exists())
            self.assertEqual(resolve_round(self.question.pk).attempts, 1)
            RoundResolution.objects.filter(pk=result.pk).update(next_attempt_at=timezone.now())
            result = resolve_round(self.question.pk)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(result.status, RoundResolution.Status.APPLIED)
        self.assertEqual(result.attempts, 2)

    def test_retry_budget_stops_and_reports_error_without_mutating_hp(self):
        self.close()
        with patch.object(DevelopmentFakeEcoMotor, 'calculate', side_effect=EcoMotorUnavailable('test')):
            for _ in range(5):
                RoundResolution.objects.all().update(next_attempt_at=timezone.now())
                result = resolve_round(self.question.pk)
        self.assertEqual(result.attempts, 3)
        self.assertTrue(result.last_error)
        self.assertFalse(HealthChange.objects.exists())
        self.assertEqual(list(self.duel.participants.values_list('hp', flat=True)), [100, 100])

    def test_invalid_or_negative_damage_and_wrong_echo_are_rejected(self):
        self.close()
        for damages in ((-1, 1), (True, 1), (1,), (1.5, 2), (1000001, 0)):
            with self.subTest(damages=damages):
                RoundResolution.objects.all().update(attempts=0, next_attempt_at=timezone.now())
                with patch.object(DevelopmentFakeEcoMotor, 'calculate', side_effect=lambda request: DamageResult(
                    request, damages, 'invalid',
                )):
                    result = resolve_round(self.question.pk)
                self.assertEqual(result.status, RoundResolution.Status.PENDING)
        RoundResolution.objects.all().update(attempts=0, next_attempt_at=timezone.now())
        with patch.object(DevelopmentFakeEcoMotor, 'calculate', side_effect=lambda request: DamageResult(
            replace(request, key=str(uuid.uuid4())), (0, 25), 'wrong-echo',
        )):
            self.assertEqual(resolve_round(self.question.pk).status, RoundResolution.Status.PENDING)
        self.assertFalse(HealthChange.objects.exists())

    def test_fake_is_explicit_and_prohibited_outside_debug(self):
        self.close()
        row = plan_resolution(self.question.pk)
        with override_settings(DEBUG=False), self.assertRaises(EcoMotorUnavailable):
            DevelopmentFakeEcoMotor().calculate(damage_request(row))
        with override_settings(DUCKYCLASH_ECOMOTOR_GATEWAY=''), self.assertRaises(EcoMotorUnavailable):
            get_ecomotor()

    def test_no_answers_are_server_timeouts_not_browser_results(self):
        self.close()
        result = resolve_round(self.question.pk)
        self.assertEqual(result.response_payload['damage'], [0, 0])
        self.assertFalse(DuelAnswer.objects.exists())
        self.assertEqual([fact['correct'] for fact in result.request_payload['fighters']], [False, False])

    def test_frozen_resolution_blocks_items_and_provider_changes(self):
        self.close()
        row = plan_resolution(self.question.pk)
        with self.assertRaises(ValidationError):
            self.use('BOTIQUIN')
        with override_settings(DUCKYCLASH_ECOMOTOR_GATEWAY='different.Gateway'):
            result = resolve_round(self.question.pk)
        self.assertEqual(result.status, RoundResolution.Status.PENDING)
        self.assertEqual(result.gateway, row.gateway)

    def test_damage_rolls_back_if_terminal_resolution_cannot_be_written(self):
        self.close()
        row = plan_resolution(self.question.pk)
        with patch.object(DuelResolution.objects, 'create', side_effect=ValidationError('test failure')):
            with self.assertRaises(ValidationError):
                apply_damage(row.pk, self.fake_result(row, (1000, 0)))
        self.assertFalse(HealthChange.objects.exists())
        self.assertEqual(list(self.duel.participants.values_list('hp', flat=True)), [100, 100])
        self.assertEqual(RoundResolution.objects.get(pk=row.pk).status, RoundResolution.Status.PENDING)

    def test_periodic_task_resolves_closed_round_and_ignores_applied(self):
        self.assertEqual(resolve_closed_rounds(), 0)
        self.close()
        self.assertEqual(resolve_closed_rounds(), 1)
        self.assertEqual(resolve_closed_rounds(), 0)

    def test_competing_calculations_apply_damage_once(self):
        self.close()
        row = plan_resolution(self.question.pk)
        result = self.fake_result(row, (25, 25))
        barrier = Barrier(2)

        def apply():
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                return apply_damage(row.pk, result).pk
            except (IntegrityError, ValidationError):
                return None
            except OperationalError as exc:
                if connection.vendor != 'sqlite' or 'locked' not in str(exc).lower():
                    raise
                return None
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: apply(), range(2)))
        apply_damage(row.pk, result)
        self.assertEqual(HealthChange.objects.count(), 2)
        self.assertEqual(list(self.duel.participants.values_list('hp', flat=True)), [75, 75])

    def test_last_stock_unit_competing_actions_never_double_consume(self):
        entry = self.stock('NUBE')
        entry.quantity = 1
        entry.save()
        barrier = Barrier(2)

        def activate():
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                return self.use('NUBE').pk
            except (IntegrityError, ValidationError):
                return None
            except OperationalError as exc:
                if connection.vendor != 'sqlite' or 'locked' not in str(exc).lower():
                    raise
                return None
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: activate(), range(2)))
        if not DuelItemUse.objects.exists():
            self.use('NUBE')
        self.assertEqual(self.stock('NUBE').quantity, 0)
        self.assertEqual(DuelItemUse.objects.count(), 1)

    def test_knowledge_item_provider_runs_without_write_transaction(self):
        original = LMSKnowledgeGateway.validate

        def validate(gateway, question, selected):
            self.assertFalse(connection.in_atomic_block)
            return original(gateway, question, selected)

        with patch.object(LMSKnowledgeGateway, 'validate', new=validate):
            self.use('ESCUDO')
        self.assertEqual(self.stock('ESCUDO').quantity, 4)

    def test_answer_is_rechecked_when_shield_removes_choice_during_validation(self):
        payload = self.payload(self.question)
        removed = next(option for option in payload['options'] if option['text'] == 'B')
        original = LMSKnowledgeGateway.validate

        def validate(gateway, question, selected):
            row = DuelQuestionOptions.objects.get(question=self.question, participant=self.player)
            row.options = [option for option in row.options if option['id'] != removed['id']]
            row.save()
            return original(gateway, question, selected)

        with patch.object(LMSKnowledgeGateway, 'validate', new=validate), self.assertRaises(ValidationError):
            submit_answer(actor=self.creator, question_id=self.question.pk, option_id=removed['id'])
        self.assertFalse(DuelAnswer.objects.exists())

    def test_simultaneous_opposing_answers_are_both_counted(self):
        ids = {
            actor.pk: next(option['id'] for option in self.payload(self.question, actor)['options'] if option['text'] == 'A')
            for actor in (self.creator, self.opponent)
        }
        barrier = Barrier(2)
        original = LMSKnowledgeGateway.validate

        def validate(gateway, question, selected):
            self.assertFalse(connection.in_atomic_block)
            barrier.wait(timeout=15)
            return original(gateway, question, selected)

        def answer(actor):
            close_old_connections()
            try:
                return submit_answer(actor=actor, question_id=self.question.pk, option_id=ids[actor.pk]).pk
            except (IntegrityError, ValidationError):
                return None
            except OperationalError as exc:
                if connection.vendor != 'sqlite' or 'locked' not in str(exc).lower():
                    raise
                return None
            finally:
                close_old_connections()

        with patch.object(LMSKnowledgeGateway, 'validate', new=validate), ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(answer, (self.creator, self.opponent)))
        for actor in (self.creator, self.opponent):
            if not DuelAnswer.objects.filter(question=self.question, participant__player=actor).exists():
                submit_answer(actor=actor, question_id=self.question.pk, option_id=ids[actor.pk])
        self.assertEqual(DuelAnswer.objects.count(), 2)
        self.close()
        resolve_round(self.question.pk)
        self.assertEqual(list(self.duel.participants.values_list('hp', flat=True)), [75, 75])

    def test_changed_hp_snapshot_blocks_damage_and_round_input_is_immutable(self):
        self.close()
        row = plan_resolution(self.question.pk)
        row.request_payload = {**row.request_payload, 'difficulty': 3}
        with self.assertRaises(ValidationError):
            row.save()
        row.refresh_from_db()
        ParticipantState.objects.filter(pk=self.player.pk).update(hp=50)
        with self.assertRaises(ValidationError):
            apply_damage(row.pk, self.fake_result(row, (25, 25)))
        self.assertFalse(HealthChange.objects.exists())

    def test_operator_retry_keeps_same_key_and_input(self):
        self.close()
        with patch.object(DevelopmentFakeEcoMotor, 'calculate', side_effect=TimeoutError()):
            for _ in range(3):
                RoundResolution.objects.all().update(next_attempt_at=timezone.now())
                result = resolve_round(self.question.pk)
        key, payload = result.operation_key, result.request_payload
        RoundResolution.objects.filter(pk=result.pk).update(next_attempt_at=timezone.now() - timedelta(seconds=1))
        call_command('reconcile_clash_round', self.question.pk, reset_budget=True, stdout=StringIO())
        result.refresh_from_db()
        self.assertEqual(result.status, RoundResolution.Status.APPLIED)
        self.assertEqual(result.operation_key, key)
        self.assertEqual(result.request_payload, payload)

    def verify_bank_outcome(self, *, draw):
        from .economy import reconcile_duel
        from .test_economy import FakePaymentBank
        FakePaymentBank.balances = {str(actor.pk): 100 for actor in (self.creator, self.opponent)}
        FakePaymentBank.receipts, FakePaymentBank.holds = {}, {}
        FakePaymentBank.actions, FakePaymentBank.faults = [], {}
        with override_settings(DUCKYCLASH_PAYMENT_GATEWAY='apps.duckyclash.test_economy.FakePaymentBank'):
            Duel.objects.filter(pk=self.duel.pk).update(status=Duel.Status.PENDING_RESERVATION)
            self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.SCHEDULED)
            Duel.objects.filter(pk=self.duel.pk).update(status=Duel.Status.IN_PROGRESS)
            ParticipantState.objects.filter(pk=self.rival.pk).update(hp=10)
            if draw:
                ParticipantState.objects.filter(pk=self.player.pk).update(hp=10)
            self.answer(self.question)
            self.answer(self.question, actor=self.opponent, text='A' if draw else 'B')
            self.close()
            resolve_round(self.question.pk)
            self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.FINISHED)
            reconcile_duel(self.duel.pk)
        stake = self.duel.agreement.offer.stake
        self.assertEqual(FakePaymentBank.balances[str(self.creator.pk)], 100 if draw else 100 + stake)
        self.assertEqual(FakePaymentBank.balances[str(self.opponent.pk)], 100 if draw else 100 - stake)
        settlements = [receipt for receipt in FakePaymentBank.receipts.values() if receipt.request.kind == 'settle']
        self.assertEqual(len(settlements), 0 if draw else 1)
        self.assertFalse(FakePaymentBank.holds)

    def test_combat_victory_feeds_existing_bank_saga_with_single_pot_payment(self):
        self.verify_bank_outcome(draw=False)

    def test_combat_double_ko_feeds_existing_bank_saga_with_both_refunds(self):
        self.verify_bank_outcome(draw=True)
