"""Negotiation through authoritative combat and fake-only settlement."""
import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.ducky_arenas.models import ArenaItem, UserArenaInventory
from . import test_engine as engine_tests
from . import test_knowledge as knowledge_tests
from .economy import reconcile_duel
from .engine import DuelEngine, Phase
from .items import DEFAULT_RULES
from .models import (
    CombatSession, Duel, DuelAnswer, DuelItemUse, DuelQuestion, DuelResolution,
    EconomicOperation, ParticipantState, RuleConfiguration,
)
from .questions import submit_answer
from .services import accept_offer, counter_offer
from .test_economy import FakePaymentBank


@override_settings(
    DEBUG=True,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    DUCKYCLASH_PAYMENT_GATEWAY='',
    DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.bank.ProfileBalanceGateway',
    DUCKYCLASH_KNOWLEDGE_GATEWAY='apps.duckyclash.test_knowledge.CertifiedTestGateway',
    DUCKYCLASH_ECOMOTOR_GATEWAY='apps.duckyclash.ecomotor.DevelopmentFakeEcoMotor',
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
)
class PrivateCombatJourneyTests(TransactionTestCase):
    create = knowledge_tests.KnowledgeTests.create
    accept = knowledge_tests.KnowledgeTests.accept
    publish = knowledge_tests.KnowledgeTests.publish
    at = engine_tests.DuelEngineTests.at
    begin = engine_tests.DuelEngineTests.begin
    use = engine_tests.DuelEngineTests.use
    option = engine_tests.DuelEngineTests.option

    def setUp(self):
        knowledge_tests.KnowledgeTests.setUp(self)
        RuleConfiguration.objects.create(initial_hp=50)
        FakePaymentBank.balances = {str(p.pk): 200 for p in (self.creator, self.opponent, self.outsider)}
        FakePaymentBank.receipts, FakePaymentBank.holds = {}, {}
        FakePaymentBank.actions, FakePaymentBank.faults = [], {}
        for code in DEFAULT_RULES:
            item = ArenaItem.objects.create(code=code, name=code, itemType='COMPLEMENT')
            for actor in (self.creator, self.opponent):
                UserArenaInventory.objects.create(profile=actor.user_profile_arenas, item=item, quantity=5)

    def negotiated_duel(self):
        challenge = self.create()
        offer = counter_offer(
            actor=self.opponent, challenge_id=challenge.pk, version=1, revision=0,
            scheduled_at=challenge.clash_offers.get().scheduled_at + timedelta(minutes=10), stake=30,
        )
        challenge.refresh_from_db()
        with self.assertRaises(ValidationError):
            accept_offer(actor=self.creator, challenge_id=challenge.pk, version=1, revision=0)
        self.duel = accept_offer(
            actor=self.creator, challenge_id=challenge.pk, version=offer.version, revision=challenge.revision,
        )
        self.assertEqual(self.duel.agreement.offer_id, offer.pk)
        self.assertEqual(challenge.clash_offers.count(), 2)
        self.assertFalse(self.duel.economic_operations.exists())
        reconcile_duel(self.duel.pk)
        self.duel.refresh_from_db()
        self.assertEqual(self.duel.status, Duel.Status.SCHEDULED)
        self.assertEqual(self.duel.economic_operations.filter(kind='reserve', status='confirmed').count(), 2)
        self.start = offer.scheduled_at
        self.engine = DuelEngine(self.duel.pk)
        return challenge

    @override_settings(DUCKYCLASH_PAYMENT_GATEWAY='apps.duckyclash.test_economy.FakePaymentBank')
    def test_negotiation_two_rounds_six_items_victory_and_unique_payout(self):
        self.negotiated_duel()
        self.publish(title='Segunda pregunta del recorrido')
        first = self.begin()
        for offset, remaining in ((0, 3), (1, 2), (2, 1)):
            with self.at(offset):
                state = self.engine.snapshot(self.creator)
                self.assertEqual(state['phase'], Phase.COUNTDOWN)
                self.assertIsNone(state['question'])
                self.assertEqual(
                    timezone.datetime.fromisoformat(state['deadline']) - timezone.now(),
                    timedelta(seconds=remaining),
                )
        with self.at(3):
            self.engine.advance()
            self.assertEqual(self.engine.snapshot(self.creator)['question'], self.engine.snapshot(self.opponent)['question'])
            for actor in (self.creator, self.opponent):
                submit_answer(actor=actor, question_id=first.pk, option_id=self.option(first))
        with self.at(33):
            self.engine.advance()
        self.assertEqual(list(self.duel.participants.order_by('seat').values_list('hp', flat=True)), [25, 25])
        with self.at(36):
            self.engine.advance()
        second = DuelQuestion.objects.get(duel=self.duel, number=2)
        self.assertNotEqual(first.source_id, second.source_id)
        action = uuid.uuid4()
        with self.at(37):
            used = self.use(second, 'NUBE', action_id=action)
            self.assertEqual(self.use(second, 'NUBE', action_id=action).pk, used.pk)
            self.use(second, 'ACELERADOR')
            self.use(second, 'MULTIPLICADOR')
        self.assertEqual(
            second.player_options.get(participant__player=self.opponent).deadline,
            second.closes_at - timedelta(seconds=5),
        )
        with self.at(39):
            self.engine.advance()
            self.assertEqual(self.engine.snapshot(self.opponent)['question']['options'], [])
            self.use(second, 'ESCUDO')
            self.use(second, 'POCIMA_TIEMPO')
            self.use(second, 'BOTIQUIN')
        self.assertEqual(len(second.player_options.get(participant__player=self.creator).options), 2)
        self.assertEqual(
            second.player_options.get(participant__player=self.creator).deadline,
            second.closes_at + timedelta(seconds=5),
        )
        self.assertEqual(ParticipantState.objects.get(duel=self.duel, player=self.creator).hp, 45)
        with self.at(44):
            state = self.engine.snapshot(self.opponent)
            self.assertEqual(len(state['question']['options']), 6)
            for secret in ('correct_answers', 'PRIVATE-', 'is_correct', 'evidence'):
                self.assertNotIn(secret, str(state))
            submit_answer(actor=self.creator, question_id=second.pk, option_id=self.option(second))
            wrong = next(option['id'] for option in state['question']['options'] if option['text'] == 'B')
            submit_answer(actor=self.opponent, question_id=second.pk, option_id=wrong)
            with self.assertRaises(ValidationError):
                submit_answer(actor=self.creator, question_id=second.pk, option_id=self.option(second, 'B'))
        with self.at(74):
            self.engine.advance()
            self.engine.advance()
            reconcile_duel(self.duel.pk)
            reconcile_duel(self.duel.pk)
        self.duel.refresh_from_db()
        self.assertEqual(self.duel.status, Duel.Status.FINISHED)
        self.assertEqual(CombatSession.objects.get(duel=self.duel).phase, Phase.FINISHED)
        self.assertEqual(DuelResolution.objects.get(duel=self.duel).winner_id, self.creator.pk)
        self.assertEqual(DuelAnswer.objects.filter(question__duel=self.duel).count(), 4)
        self.assertEqual(set(DuelItemUse.objects.filter(question__duel=self.duel).values_list('item__code', flat=True)), set(DEFAULT_RULES))
        self.assertEqual(DuelItemUse.objects.filter(question__duel=self.duel).count(), 6)
        self.assertEqual(
            list(UserArenaInventory.objects.filter(profile__user=self.creator).values_list('quantity', flat=True)),
            [4] * 6,
        )
        self.assertEqual(self.duel.economic_operations.filter(kind=EconomicOperation.Kind.SETTLE).count(), 1)
        self.assertEqual(FakePaymentBank.balances[str(self.creator.pk)], 230)
        self.assertEqual(FakePaymentBank.balances[str(self.opponent.pk)], 170)
        self.assertFalse(FakePaymentBank.holds)
        self.creator.user_profile_arenas.refresh_from_db()
        self.assertEqual(self.creator.user_profile_arenas.duckCoins, 150)
        self.assertEqual(self.creator.user_profile_arenas.wins, 1)

    @override_settings(DUCKYCLASH_PAYMENT_GATEWAY='apps.duckyclash.test_economy.FakePaymentBank')
    def test_http_csrf_authorization_and_forged_state_do_not_change_journey(self):
        self.negotiated_duel()
        ready = reverse('duckyclash:duel_ready', args=[self.duel.pk])
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.creator)
        with self.at():
            self.assertEqual(client.post(ready).status_code, 403)
            ring = client.get(reverse('duckyclash:ring', args=[self.duel.pk]))
            self.assertEqual(ring.status_code, 200)
            token = client.cookies['csrftoken'].value
            forged = client.post(ready, {'csrfmiddlewaretoken': token, 'hp': 999, 'winner': self.creator.pk})
            self.assertEqual(forged.status_code, 400)
            self.assertIsNone(self.duel.participants.get(player=self.creator).ready_at)
            self.assertEqual(client.post(ready, {'csrfmiddlewaretoken': token}).status_code, 302)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse('duckyclash:ring_state', args=[self.duel.pk])).status_code, 403)
        self.assertFalse(DuelResolution.objects.filter(duel=self.duel).exists())
