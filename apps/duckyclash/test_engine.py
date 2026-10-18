import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError, close_old_connections, connection
from django.db.migrations.executor import MigrationExecutor
from django.contrib.staticfiles import finders
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.ducky_arenas.models import ArenaItem, UserArenaInventory
from . import test_knowledge as knowledge_tests
from .economy import reconcile_duel
from .engine import DuelEngine, Phase
from .items import DEFAULT_RULES, apply_item
from .knowledge import KnowledgeUnavailable, LMSKnowledgeGateway, VerifiedDistractor
from .models import CombatSession, Duel, DuelAnswer, DuelEvent, DuelItemUse, DuelQuestion, ParticipantState
from .questions import public_question, submit_answer
from .test_economy import FakePaymentBank
from .tasks import advance_duel_engines


@override_settings(
    DEBUG=True,
    DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.bank.ProfileBalanceGateway',
    DUCKYCLASH_PAYMENT_GATEWAY='',
    DUCKYCLASH_KNOWLEDGE_GATEWAY='apps.duckyclash.knowledge.LMSKnowledgeGateway',
    DUCKYCLASH_ECOMOTOR_GATEWAY='apps.duckyclash.ecomotor.DevelopmentFakeEcoMotor',
    DUCKYCLASH_DOUBLE_KO='draw', DUCKYCLASH_MAX_TIEBREAKS=1, DUCKYCLASH_ENGINE_RULES={},
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
)
class DuelEngineTests(TransactionTestCase):
    create = knowledge_tests.KnowledgeTests.create
    accept = knowledge_tests.KnowledgeTests.accept
    publish = knowledge_tests.KnowledgeTests.publish

    def setUp(self):
        knowledge_tests.KnowledgeTests.setUp(self)
        self.start = self.duel.agreement.offer.scheduled_at
        self.engine = DuelEngine(self.duel.pk)
        FakePaymentBank.balances = {str(actor.pk): 200 for actor in (self.creator, self.opponent)}
        FakePaymentBank.receipts, FakePaymentBank.holds = {}, {}
        FakePaymentBank.actions, FakePaymentBank.faults = [], {}
        Duel.objects.filter(pk=self.duel.pk).update(status=Duel.Status.PENDING_RESERVATION)
        with override_settings(DUCKYCLASH_PAYMENT_GATEWAY='apps.duckyclash.test_economy.FakePaymentBank'):
            reconcile_duel(self.duel.pk)
        self.duel.refresh_from_db()
        for code in DEFAULT_RULES:
            item = ArenaItem.objects.create(code=code, name=code, itemType='COMPLEMENT')
            for actor in (self.creator, self.opponent):
                UserArenaInventory.objects.create(profile=actor.user_profile_arenas, item=item, quantity=5)

    def at(self, seconds=0):
        return patch('django.utils.timezone.now', return_value=self.start + timedelta(seconds=seconds))

    def accept_current_rules(self):
        self.duel = self.accept(self.create())
        with override_settings(DUCKYCLASH_PAYMENT_GATEWAY='apps.duckyclash.test_economy.FakePaymentBank'):
            reconcile_duel(self.duel.pk)
        self.duel.refresh_from_db()
        self.start = self.duel.agreement.offer.scheduled_at
        self.engine = DuelEngine(self.duel.pk)

    def begin(self):
        with self.at():
            self.engine.ready(self.creator)
            self.engine.ready(self.opponent)
            self.engine.advance()
        return DuelQuestion.objects.get(duel=self.duel, number=1)

    def active(self):
        question = self.begin()
        with self.at(3):
            self.engine.advance()
        return question

    def use(self, question, code, *, actor=None, action_id=None):
        return apply_item(actor=actor or self.creator, question_id=question.pk, code=code, action_id=action_id or uuid.uuid4())

    def option(self, question, text='A'):
        return question.player_options.get(participant__player=self.creator).options[
            ['A', 'B', 'C', 'D'].index(text)
        ]['id']

    def test_before_schedule_ring_is_locked_and_ready_presence_are_rejected(self):
        with self.at(-1):
            for action in (self.engine.enter, self.engine.ready):
                with self.assertRaises(ValidationError):
                    action(self.creator)
            state = self.engine.snapshot(self.creator)
        self.assertFalse(state['unlocked'])
        self.assertIsNone(state['question'])
        self.assertFalse(CombatSession.objects.exists())
        self.assertIsNone(self.duel.participants.get(player=self.creator).ready_at)

    def test_missing_or_unverified_reserves_never_start(self):
        self.duel.economic_operations.filter(player=self.opponent).update(status='uncertain', bank_reference='')
        with self.at(), self.assertRaises(ValidationError):
            self.engine.ready(self.creator)
        self.assertFalse(CombatSession.objects.exists())
        self.assertFalse(DuelQuestion.objects.exists())

    def test_ring_labels_demo_damage_and_real_point_stakes(self):
        self.client.force_login(self.creator)
        url = reverse('duckyclash:ring', args=[self.duel.pk])
        with self.at():
            self.assertContains(self.client.get(url), 'EcoMotor DEMO de desarrollo')
            with override_settings(DUCKYCLASH_ECOMOTOR_GATEWAY=''):
                self.assertNotContains(self.client.get(url), 'EcoMotor DEMO de desarrollo')

    def test_both_http_answers_resolve_early_once_and_allow_next_round(self):
        question = self.active()
        clients = [Client(), Client()]
        url = reverse('duckyclash:question_answer', args=[question.pk])
        for client, actor in zip(clients, (self.creator, self.opponent)):
            client.force_login(actor)
        with self.at(4):
            for index, (client, actor) in enumerate(zip(clients, (self.creator, self.opponent))):
                options = question.player_options.get(participant__player=actor).options
                response = client.post(url, {'option_id': options[0]['id']})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {'accepted': True})
                if index == 0:
                    self.assertEqual(CombatSession.objects.get(duel=self.duel).phase, Phase.QUESTION_ACTIVE)
                    self.assertFalse(hasattr(DuelQuestion.objects.get(pk=question.pk), 'damage_resolution'))
            session = CombatSession.objects.get(duel=self.duel)
            self.assertEqual(session.phase, Phase.ROUND_RESULT)
            self.assertLess(timezone.now(), question.closes_at)
            resolution = question.damage_resolution
            self.assertEqual(resolution.status, 'applied')
            before = list(self.duel.participants.order_by('seat').values_list('hp', flat=True))
            self.assertEqual(clients[1].post(url, {'option_id': options[0]['id']}).status_code, 200)
            self.assertEqual(list(self.duel.participants.order_by('seat').values_list('hp', flat=True)), before)
        self.publish(title='Otra pregunta', correct='B')
        with patch('django.utils.timezone.now', return_value=session.deadline):
            self.engine.advance()
        self.assertEqual(self.duel.questions.count(), 2)

    def test_http_presence_opens_synchronized_question_without_worker(self):
        clients = [Client(), Client()]
        actors = (self.creator, self.opponent)
        for client, actor in zip(clients, actors):
            client.force_login(actor)
        ready_url = reverse('duckyclash:duel_ready', args=[self.duel.pk])
        presence_url = reverse('duckyclash:duel_presence', args=[self.duel.pk])
        state_url = reverse('duckyclash:ring_state', args=[self.duel.pk])
        with self.at():
            for client in clients:
                self.assertEqual(client.post(ready_url).status_code, 302)
            question = DuelQuestion.objects.get(duel=self.duel)
            state = clients[0].get(state_url).json()
            self.assertEqual(state['phase'], 'countdown')
            self.assertIsNone(state['question'])
        with self.at(2):
            self.assertEqual(clients[0].post(presence_url).status_code, 200)
            self.assertIsNone(clients[0].get(state_url).json()['question'])
        with self.at(3):
            # Reading state alone must not mutate the phase or expose options early.
            self.assertEqual(clients[0].get(state_url).json()['phase'], 'countdown')
            self.assertEqual(clients[0].post(presence_url).status_code, 200)
            states = [client.get(state_url).json() for client in clients]
        for state in states:
            self.assertEqual(state['phase'], 'question_active')
            self.assertEqual(state['question']['id'], question.pk)
            self.assertEqual(len(state['question']['options']), 4)
            self.assertNotIn('correct_answers', str(state))
            self.assertNotIn('validation', str(state))
        self.assertEqual(states[0]['question']['prompt'], states[1]['question']['prompt'])
        self.assertEqual(self.duel.questions.count(), 1)

    def test_visual_snapshot_uses_only_own_stock_and_server_activation_windows(self):
        UserArenaInventory.objects.filter(profile__user=self.opponent, item__code='NUBE').update(quantity=99)
        with self.at(-1):
            initial = self.engine.snapshot(self.creator)
        self.assertEqual(len(initial['items']), 6)
        self.assertTrue(all(item['enabled'] is False for item in initial['items']))
        self.assertEqual(next(item['quantity'] for item in initial['items'] if item['code'] == 'NUBE'), 5)
        question = self.begin()
        with self.at():
            state = self.engine.snapshot(self.creator)
            enabled = {item['code'] for item in state['items'] if item['enabled']}
            self.assertEqual(enabled, {'NUBE', 'ACELERADOR', 'MULTIPLICADOR'})
            self.use(question, 'NUBE')
            state = self.engine.snapshot(self.creator)
            cloud = next(item for item in state['items'] if item['code'] == 'NUBE')
            self.assertEqual(cloud, {'code': 'NUBE', 'quantity': 4, 'enabled': False})
            self.assertIsNone(state['question'])
            self.assertIsNone(state['result'])
        with self.at(3):
            self.engine.advance()
            state = self.engine.snapshot(self.opponent)
            self.assertEqual(state['question']['options'], [])
            self.assertFalse(next(item['enabled'] for item in state['items'] if item['code'] == 'ESCUDO'))
            self.assertTrue(next(item['enabled'] for item in state['items'] if item['code'] == 'POCIMA_TIEMPO'))
            self.assertFalse(next(item['enabled'] for item in state['items'] if item['code'] == 'BOTIQUIN'))

    def test_visual_snapshot_only_reports_persisted_resolution(self):
        with self.at():
            self.engine.ready(self.creator)
            initial = self.engine.snapshot(self.creator)
            self.assertIsNone(initial['result'])
            ParticipantState.objects.filter(duel=self.duel, player=self.creator).update(hp=0)
            self.assertIsNone(self.engine.snapshot(self.creator)['result'])
            from .models import DuelResolution
            Duel.objects.filter(pk=self.duel.pk).update(status=Duel.Status.SETTLEMENT_PENDING)
            self.duel.refresh_from_db()
            DuelResolution.objects.create(duel=self.duel, winner=self.opponent, reason='Resultado autorizado')
            result = self.engine.snapshot(self.creator)
            self.assertEqual(result['result'], {
                'winner_id': self.opponent.pk, 'is_draw': False, 'reason': 'Resultado autorizado',
            })
            self.assertEqual(result['economic_status'], Duel.Status.SETTLEMENT_PENDING)

    def test_graphical_ring_assets_and_controls_do_not_leak_solutions(self):
        self.client.force_login(self.creator)
        with self.at(-1):
            response = self.client.get(reverse('duckyclash:ring', args=[self.duel.pk]))
        self.assertContains(response, 'clash-canvas')
        self.assertContains(response, 'ring-scene.js')
        self.assertContains(response, 'data-canvas-ring="true"')
        self.assertContains(response, 'clash-play-layout')
        self.assertEqual(len(response.context['ring_assets']['fighters']), 2)
        for index, fighter in enumerate(response.context['ring_assets']['fighters'], start=1):
            sprite_path = f'duckyarenas/DAR_personajes/DAR_PRE{index}.png'
            self.assertTrue(fighter['sprite'].endswith(f'/{sprite_path}'))
            self.assertIsNotNone(finders.find(sprite_path))
            self.assertEqual(set(fighter['animations']), {'idle', 'attack', 'hit', 'dealDamage', 'win', 'lose'})
            for animation in fighter['animations'].values():
                self.assertEqual(animation['frameCount'], 4)
                self.assertGreater(animation['frameDuration'], 0)
            for name, filename in (
                ('idle', '01_ilde'), ('attack', '02_attack'), ('hit', '03_hit'),
                ('dealDamage', '04_deal'), ('win', '05_win'), ('lose', '06_lose'),
            ):
                animation_path = f'duckyclash/character-frames/PRE{index}_{filename}.webp'
                self.assertTrue(fighter['animations'][name]['url'].endswith(f'/{animation_path}'))
                self.assertIsNotNone(finders.find(animation_path))
        self.assertContains(response, 'clash-question-panel')
        self.assertContains(response, 'clash-question-close')
        self.assertContains(response, 'clash-question-open')
        self.assertContains(response, 'clash-fighter-video-dialog')
        self.assertContains(response, 'DAR_PRE2_V.MP4')
        self.assertContains(response, 'autoplay muted loop playsinline controls')
        self.assertEqual(response.context['ring_assets']['landscape'], self.landscape.name)
        self.assertEqual(response.context['ring_assets']['background'], '')
        self.assertEqual(len(response.context['ring_inventory']), 6)
        self.assertNotContains(response, 'PRIVATE-VALIDATION-KEY')
        self.assertNotContains(response, 'PRIVATE-EXPLANATION-SECRET')
        self.assertNotContains(response, 'correct_answers')
        question = self.active()
        with self.at(3):
            response = self.client.get(reverse('duckyclash:ring', args=[self.duel.pk]))
        self.assertContains(response, question.snapshot['prompt'])
        self.assertNotContains(response, 'PRIVATE-EXPLANATION-SECRET')
        self.assertNotContains(response, 'correct_answers')

    def test_both_players_must_explicitly_ready_and_duplicate_is_idempotent(self):
        with self.at():
            self.engine.ready(self.creator)
            self.engine.ready(self.creator)
            self.engine.advance()
            self.assertEqual(CombatSession.objects.get().phase, Phase.WAITING_PLAYERS)
            self.assertFalse(DuelQuestion.objects.exists())
            self.engine.ready(self.opponent)
            self.assertEqual(CombatSession.objects.get().phase, Phase.READY)
            self.engine.advance()
            self.engine.ready(self.creator)
            self.engine.advance()
        self.assertEqual(DuelEvent.objects.filter(kind='player.ready').count(), 2)
        self.assertEqual(DuelEvent.objects.filter(kind='engine.countdown').count(), 1)
        self.assertEqual(DuelQuestion.objects.count(), 1)

    def test_countdown_three_two_one_then_same_question_and_no_early_solution(self):
        question = self.begin()
        for offset, remaining in ((0, 3), (1, 2), (2, 1)):
            with self.at(offset):
                self.engine.advance()
                state = self.engine.snapshot(self.creator)
                self.assertEqual(state['phase'], Phase.COUNTDOWN)
                self.assertEqual(timezone.datetime.fromisoformat(state['deadline']) - timezone.now(), timedelta(seconds=remaining))
                self.assertIsNone(state['question'])
                with self.assertRaises(ValidationError):
                    public_question(actor=self.creator, question=DuelQuestion.objects.select_related('duel__agreement').get(pk=question.pk))
        with self.at(3):
            self.engine.advance()
            a, b = self.engine.snapshot(self.creator), self.engine.snapshot(self.opponent)
        self.assertEqual(a['phase'], Phase.QUESTION_ACTIVE)
        self.assertEqual(a['question'], b['question'])
        for secret in ('correct_answers', 'validation', 'PRIVATE-', 'evidence', 'is_correct'):
            self.assertNotIn(secret, str(a))

    def test_attacks_are_prepared_once_and_effects_start_at_question_open(self):
        question = self.begin()
        action = uuid.uuid4()
        with self.at(1):
            first = self.use(question, 'NUBE', action_id=action)
        with self.at(2):
            self.assertEqual(self.use(question, 'NUBE', action_id=action).pk, first.pk)
            self.use(question, 'ACELERADOR')
        row = question.player_options.get(participant__player=self.opponent)
        self.assertEqual(row.hidden_until, self.start + timedelta(seconds=8))
        self.assertEqual(row.deadline, question.closes_at - timedelta(seconds=5))
        with self.at(3):
            self.engine.advance()
            self.assertEqual(self.engine.snapshot(self.opponent)['question']['options'], [])
            with self.assertRaises(ValidationError):
                self.use(question, 'NUBE', actor=self.opponent)
        with self.at(8):
            self.assertEqual(len(self.engine.snapshot(self.opponent)['question']['options']), 4)
        self.assertEqual(DuelItemUse.objects.filter(item__code='NUBE').count(), 1)
        self.assertEqual(DuelEvent.objects.filter(kind='item.prepared').count(), 2)

    def test_multiplier_prepared_privately_and_unavailable_provider_does_not_consume(self):
        question = self.begin()
        with self.at(1), self.assertRaises(KnowledgeUnavailable):
            self.use(question, 'MULTIPLICADOR')
        with self.at(1), patch.object(LMSKnowledgeGateway, 'distractors', return_value=[
            VerifiedDistractor('Extra 1', 'review:1'), VerifiedDistractor('Extra 2', 'review:2'),
        ]):
            self.use(question, 'MULTIPLICADOR')
            self.assertIsNone(self.engine.snapshot(self.opponent)['question'])
        with self.at(3):
            self.engine.advance()
            self.assertEqual(len(self.engine.snapshot(self.opponent)['question']['options']), 6)
            self.assertEqual(len(self.engine.snapshot(self.creator)['question']['options']), 4)
        self.assertEqual(DuelItemUse.objects.filter(item__code='MULTIPLICADOR').count(), 1)

    def test_defense_windows_and_default_healing_require_active_question(self):
        question = self.begin()
        ParticipantState.objects.filter(duel=self.duel, player=self.creator).update(hp=50)
        with self.at(1):
            for code in ('ESCUDO', 'POCIMA_TIEMPO', 'BOTIQUIN'):
                with self.subTest(code=code), self.assertRaises(ValidationError):
                    self.use(question, code)
        with self.at(3):
            self.engine.advance()
            self.use(question, 'ESCUDO')
            self.use(question, 'POCIMA_TIEMPO')
            self.use(question, 'BOTIQUIN')
            submit_answer(actor=self.creator, question_id=question.pk, option_id=self.option(question))
            with self.assertRaises(ValidationError):
                self.use(question, 'BOTIQUIN')
        self.assertEqual(ParticipantState.objects.get(duel=self.duel, player=self.creator).hp, 70)

    @override_settings(DUCKYCLASH_ENGINE_RULES={'healing_windows': ['countdown', 'question_active']})
    def test_healing_countdown_window_is_configurable_and_cannot_revive(self):
        self.accept_current_rules()
        question = self.begin()
        ParticipantState.objects.filter(duel=self.duel, player=self.creator).update(hp=50)
        with self.at(1):
            self.use(question, 'BOTIQUIN')
            ParticipantState.objects.filter(duel=self.duel, player=self.opponent).update(hp=0)
            with self.assertRaises(ValidationError):
                self.use(question, 'BOTIQUIN', actor=self.opponent)
        self.assertEqual(DuelItemUse.objects.count(), 1)

    def test_late_attack_rejected_even_when_worker_has_not_advanced_phase(self):
        question = self.begin()
        with self.at(3), self.assertRaises(ValidationError):
            self.use(question, 'NUBE')
        self.assertFalse(DuelItemUse.objects.exists())

    def test_answer_locked_once_replay_accepted_but_option_cannot_change(self):
        question = self.active()
        with self.at(5):
            first = submit_answer(actor=self.creator, question_id=question.pk, option_id=self.option(question))
            self.assertEqual(submit_answer(
                actor=self.creator, question_id=question.pk, option_id=self.option(question),
            ).pk, first.pk)
            with self.assertRaises(ValidationError):
                submit_answer(actor=self.creator, question_id=question.pk, option_id=self.option(question, 'B'))
        with self.at(99):
            self.engine.advance()
            self.assertEqual(submit_answer(
                actor=self.creator, question_id=question.pk, option_id=self.option(question),
            ).pk, first.pk)
        self.assertEqual(DuelAnswer.objects.count(), 1)
        self.assertEqual(DuelEvent.objects.filter(kind='answer.locked').count(), 1)

    def test_answer_at_exact_deadline_and_client_time_forgery_are_rejected(self):
        question = self.active()
        self.client.force_login(self.creator)
        with self.at(33):
            response = self.client.post(reverse('duckyclash:question_answer', args=[question.pk]), {
                'option_id': self.option(question), 'client_time': '1970-01-01',
                'received_at': self.start.isoformat(), 'remaining': 99999,
            })
        self.assertEqual(response.status_code, 409)
        self.assertFalse(DuelAnswer.objects.exists())
        with self.at(2), self.assertRaises(ValidationError):
            submit_answer(actor=self.creator, question_id=question.pk, option_id=self.option(question))

    def test_deadline_rechecked_after_slow_validation_without_database_lock(self):
        question = self.active()
        with self.at(32), patch('apps.duckyclash.questions.gateway_for') as gateway:
            def validate(contract, selected):
                self.assertFalse(connection.in_atomic_block)
                self.assertEqual(contract.identity, question.source_id)
                self.assertEqual(selected, 'A')
                clock.return_value = self.start + timedelta(seconds=33)
                return True
            gateway.return_value.validate.side_effect = validate
            with patch('django.utils.timezone.now', return_value=self.start + timedelta(seconds=32)) as clock:
                with self.assertRaises(ValidationError):
                    submit_answer(actor=self.creator, question_id=question.pk, option_id=self.option(question))
            gateway.return_value.validate.assert_called_once()
        self.assertFalse(DuelAnswer.objects.exists())

    def test_cloud_blocks_shield_until_options_are_visible_without_consuming(self):
        question = self.begin()
        with self.at(1):
            self.use(question, 'NUBE')
        with self.at(3):
            self.engine.advance()
            with self.assertRaises(ValidationError):
                self.use(question, 'ESCUDO', actor=self.opponent)
        self.assertEqual(UserArenaInventory.objects.get(profile__user=self.opponent, item__code='ESCUDO').quantity, 5)
        with self.at(8):
            self.use(question, 'ESCUDO', actor=self.opponent)
            self.assertEqual(len(self.engine.snapshot(self.opponent)['question']['options']), 2)

    def test_simultaneous_correct_answers_double_ko_finishes_once(self):
        question = self.active()
        ParticipantState.objects.filter(duel=self.duel).update(hp=25)
        with self.at(5):
            for actor in (self.creator, self.opponent):
                submit_answer(actor=actor, question_id=question.pk, option_id=self.option(question))
        with self.at(33):
            self.engine.advance()
            self.engine.advance()
            state = self.engine.snapshot(self.creator)
        self.assertEqual(state['phase'], Phase.FINISHED)
        self.assertEqual([player['hp'] for player in state['participants']], [0, 0])
        self.assertTrue(Duel.objects.get(pk=self.duel.pk).resolution.is_draw)
        self.assertEqual(DuelEvent.objects.filter(kind='round.resolved').count(), 1)
        self.assertEqual(DuelEvent.objects.filter(kind='engine.finished').count(), 1)

    def test_potion_delays_resolution_for_both_but_cannot_reopen_expired_clock(self):
        question = self.active()
        with self.at(5):
            self.use(question, 'POCIMA_TIEMPO')
        with self.at(33):
            self.engine.advance()
            self.assertEqual(CombatSession.objects.get().phase, Phase.QUESTION_ACTIVE)
            self.assertIsNone(self.engine.snapshot(self.opponent)['question'])
        with self.at(38):
            with self.assertRaises(ValidationError):
                self.use(question, 'ESCUDO')
            self.engine.advance()
        self.assertEqual(CombatSession.objects.get().phase, Phase.ROUND_RESULT)

    def test_disconnect_does_not_pause_deadlines_and_reentry_does_not_reset(self):
        question = self.active()
        with self.at(24):
            self.engine.advance()
            self.assertEqual(ParticipantState.objects.filter(duel=self.duel, online=False).count(), 2)
            self.assertEqual(DuelEvent.objects.filter(kind='player.disconnected').count(), 2)
        with self.at(25):
            self.engine.enter(self.creator)
            self.engine.enter(self.creator)
            state = self.engine.snapshot(self.creator)
        self.assertEqual(state['question']['closes_at'], question.closes_at.isoformat())
        self.assertEqual(state['question']['id'], question.pk)
        self.assertEqual(DuelQuestion.objects.count(), 1)
        self.assertEqual(DuelEvent.objects.filter(kind='player.joined', actor=self.creator).count(), 2)
        with self.at(33):
            self.engine.advance()
        self.assertEqual(CombatSession.objects.get().phase, Phase.ROUND_RESULT)

    def test_disconnected_ready_player_must_return_before_countdown(self):
        with self.at():
            self.engine.ready(self.creator)
            self.engine.ready(self.opponent)
        with self.at(21):
            self.engine.advance()
            self.assertFalse(DuelQuestion.objects.exists())
            self.engine.enter(self.creator)
            self.engine.enter(self.opponent)
            self.engine.advance()
        self.assertEqual(DuelQuestion.objects.count(), 1)

    def test_no_show_requests_compensated_refund_not_arbitrary_winner(self):
        with self.at():
            self.engine.ready(self.creator)
        with self.at(300):
            self.engine.advance()
            self.assertEqual(CombatSession.objects.get().phase, Phase.FINISHED)
            with self.assertRaises(ValidationError):
                self.engine.ready(self.opponent)
            with override_settings(DUCKYCLASH_PAYMENT_GATEWAY='apps.duckyclash.test_economy.FakePaymentBank'):
                self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.CANCELED)
        self.assertFalse(self.duel.questions.exists())
        self.assertFalse(hasattr(Duel.objects.get(pk=self.duel.pk), 'resolution'))
        self.assertEqual(FakePaymentBank.balances, {str(self.creator.pk): 200, str(self.opponent.pk): 200})

    @override_settings(DUCKYCLASH_ENGINE_RULES={'max_rounds': 1})
    def test_round_result_window_then_bounded_draw_feeds_bank_saga(self):
        self.accept_current_rules()
        self.active()
        with self.at(33):
            self.engine.advance()
        self.assertEqual(CombatSession.objects.get().phase, Phase.ROUND_RESULT)
        with self.at(35):
            self.engine.advance()
            self.assertEqual(CombatSession.objects.get().phase, Phase.ROUND_RESULT)
        with self.at(36):
            self.engine.advance()
            self.assertEqual(CombatSession.objects.get().phase, Phase.FINISHED)
            with override_settings(DUCKYCLASH_PAYMENT_GATEWAY='apps.duckyclash.test_economy.FakePaymentBank'):
                self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.FINISHED)
        self.assertTrue(Duel.objects.get(pk=self.duel.pk).resolution.is_draw)

    def test_next_round_starts_new_countdown_without_repeated_question(self):
        question = self.active()
        self.publish(title='Segunda pregunta')
        with self.at(33):
            self.engine.advance()
        with self.at(36):
            self.engine.advance()
            self.engine.advance()
            state = self.engine.snapshot(self.creator)
        self.assertEqual(state['phase'], Phase.COUNTDOWN)
        self.assertEqual(state['round'], 2)
        self.assertNotEqual(state['question_id'], question.pk)
        self.assertEqual(DuelQuestion.objects.count(), 2)
        with self.at(39):
            self.engine.advance()
            self.assertEqual(self.engine.snapshot(self.creator)['question']['round'], 2)

    def test_knowledge_failure_preserves_readiness_and_has_explicit_recoverable_error(self):
        with self.at():
            self.engine.ready(self.creator)
            self.engine.ready(self.opponent)
            with patch.object(LMSKnowledgeGateway, 'candidates', side_effect=KnowledgeUnavailable()):
                self.engine.advance()
            state = self.engine.snapshot(self.creator)
            self.assertEqual(state['phase'], Phase.READY)
            self.assertEqual(state['error'], 'knowledge_unavailable')
            self.assertFalse(DuelQuestion.objects.exists())
            self.engine.advance()
            self.assertEqual(CombatSession.objects.get().phase, Phase.COUNTDOWN)

    def test_ecomotor_failure_blocks_next_round_without_inventing_hp(self):
        self.active()
        with self.at(33), patch('apps.duckyclash.combat.get_ecomotor') as gateway:
            gateway.return_value.calculate.side_effect = TimeoutError()
            self.engine.advance()
            self.assertEqual(self.engine.snapshot(self.creator)['phase'], Phase.ROUND_RESOLVING)
            self.assertEqual(CombatSession.objects.get().last_error, 'ecomotor_pending')
        self.assertEqual(list(self.duel.participants.values_list('hp', flat=True)), [100, 100])
        self.assertEqual(DuelQuestion.objects.count(), 1)

    def test_ordered_events_cursor_and_secret_whitelist(self):
        question = self.active()
        with self.at(5):
            submit_answer(actor=self.creator, question_id=question.pk, option_id=self.option(question))
            first = self.engine.snapshot(self.creator)
            again = self.engine.snapshot(self.creator, after=first['cursor'])
        self.assertEqual(again['events'], [])
        sequences = [event['sequence'] for event in first['events']]
        self.assertEqual(sequences, sorted(set(sequences)))
        self.assertEqual(sequences, list(range(1, len(sequences) + 1)))
        self.assertNotIn('option_id', str(first['events']))
        event = DuelEvent.objects.filter(kind='answer.locked').get()
        DuelEvent.objects.filter(pk=event.pk).update(payload={'round': 1, 'correct_answers': ['SECRET']})
        with self.at(6):
            self.assertNotIn('SECRET', str(self.engine.snapshot(self.opponent)['events']))

    def test_outsider_and_deactivated_user_rejected_for_every_engine_operation(self):
        self.begin()
        for operation in (self.engine.ready, self.engine.enter, self.engine.snapshot):
            with self.at(1), self.subTest(operation=operation), self.assertRaises(PermissionDenied):
                operation(self.outsider)
        get_user_model().objects.filter(pk=self.creator.pk).update(is_active=False)
        with self.at(1), self.assertRaises(PermissionDenied):
            self.engine.snapshot(self.creator)

    def test_http_actions_reject_state_injection_get_csrf_and_oversized_payload(self):
        self.client.force_login(self.creator)
        url = reverse('duckyclash:duel_ready', args=[self.duel.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.creator)
        with self.at():
            self.assertEqual(strict.post(url).status_code, 403)
            self.assertEqual(self.client.post(url, {'phase': 'finished', 'clock': 0}).status_code, 400)
            self.assertEqual(self.client.post(url, 'x' * 3000, content_type='text/plain').status_code, 400)
            self.assertEqual(self.client.post(url).status_code, 302)
            self.assertEqual(self.client.get(reverse('duckyclash:ring_state', args=[self.duel.pk]), {'after': '-1'}).status_code, 400)
        self.assertEqual(ParticipantState.objects.filter(duel=self.duel, ready_at__isnull=False).count(), 1)

    def test_worker_advances_without_browser_and_invalid_rules_do_not_start(self):
        with self.at(), override_settings(DUCKYCLASH_ENGINE_RULES={'countdown_seconds': 0}):
            with self.assertRaises(ValidationError):
                self.create()
        question = self.begin()
        with self.at(3):
            advance_duel_engines()
        self.assertEqual(CombatSession.objects.get().phase, Phase.QUESTION_ACTIVE)
        self.assertEqual(DuelQuestion.objects.get().pk, question.pk)

    def test_valid_csrf_multipart_readiness_and_presence_are_accepted(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.creator)
        with self.at():
            self.assertEqual(strict.get(reverse('duckyclash:ring', args=[self.duel.pk])).status_code, 200)
            token = strict.cookies['csrftoken'].value
            self.assertEqual(strict.post(reverse('duckyclash:duel_ready', args=[self.duel.pk]), {
                'csrfmiddlewaretoken': token,
            }).status_code, 302)
            for _ in range(3):
                self.assertEqual(strict.post(reverse('duckyclash:duel_presence', args=[self.duel.pk]), {
                    'csrfmiddlewaretoken': token,
                }).status_code, 200)
        self.assertEqual(DuelEvent.objects.filter(kind='player.ready').count(), 1)
        self.assertEqual(DuelEvent.objects.filter(kind='player.joined').count(), 1)

    def parallel(self, actors, action):
        barrier = Barrier(len(actors))
        def call(actor):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                action(actor)
            except (ValidationError, IntegrityError):
                pass
            except OperationalError as exc:
                if connection.vendor != 'sqlite' or 'locked' not in str(exc).lower():
                    raise
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(actors)) as pool:
            list(pool.map(call, actors))

    def test_competing_readiness_countdown_and_worker_ticks_create_one_round(self):
        with self.at():
            self.parallel([self.creator, self.opponent], self.engine.ready)
            self.engine.ready(self.creator)
            self.engine.ready(self.opponent)
            self.parallel([self.creator, self.opponent], lambda _: self.engine.advance())
            self.engine.advance()
        self.assertEqual(DuelQuestion.objects.count(), 1)
        self.assertEqual(DuelEvent.objects.filter(kind='engine.countdown').count(), 1)
        self.assertEqual(DuelEvent.objects.filter(kind='player.ready').count(), 2)
        self.assertEqual(DuelEvent.objects.count(), len(set(DuelEvent.objects.values_list('sequence', flat=True))))

    def test_competing_attacks_same_action_consume_once(self):
        question = self.begin()
        action = uuid.uuid4()
        with self.at(1):
            self.parallel([self.creator, self.creator], lambda actor: self.use(question, 'NUBE', actor=actor, action_id=action))
            self.use(question, 'NUBE', action_id=action)
        self.assertEqual(DuelItemUse.objects.count(), 1)
        self.assertEqual(UserArenaInventory.objects.get(profile__user=self.creator, item__code='NUBE').quantity, 4)

    def test_competing_answer_retries_lock_one_answer_and_one_event(self):
        question = self.active()
        option = self.option(question)
        with self.at(5):
            self.parallel([self.creator, self.creator], lambda actor: submit_answer(
                actor=actor, question_id=question.pk, option_id=option,
            ))
            submit_answer(actor=self.creator, question_id=question.pk, option_id=option)
        self.assertEqual(DuelAnswer.objects.count(), 1)
        self.assertEqual(DuelEvent.objects.filter(kind='answer.locked').count(), 1)

    def test_schema_roundtrip_preserves_users_inventory_and_previous_duel(self):
        self.begin()
        leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
        try:
            MigrationExecutor(connection).migrate([('duckyclash', '0004_healthchange_roundresolution_duelitemuse_action_id_and_more')])
        finally:
            MigrationExecutor(connection).migrate(leaves)
        self.assertTrue(Duel.objects.filter(pk=self.duel.pk).exists())
        self.assertTrue(get_user_model().objects.filter(pk=self.creator.pk).exists())
        self.assertEqual(UserArenaInventory.objects.filter(profile__user=self.creator).count(), 6)
        self.assertFalse(CombatSession.objects.exists())
