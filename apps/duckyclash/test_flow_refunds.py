"""Compensation, unknown outcomes and simultaneous KO without real payments."""
from django.core.exceptions import ValidationError
from django.test import TransactionTestCase, override_settings

from . import test_economy as economy_tests
from . import test_engine as engine_tests
from .economy import reconcile_duel, request_cancellation
from .engine import Phase
from .models import CombatSession, Duel, DuelResolution, EconomicOperation, ParticipantState
from .questions import submit_answer
from .services import accept_offer
from .test_economy import FakePaymentBank


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.test_private_challenges.FakeBankGateway',
    DUCKYCLASH_PAYMENT_GATEWAY='apps.duckyclash.test_economy.FakePaymentBank',
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
)
class ReservationFailureJourneyTests(TransactionTestCase):
    setUp = economy_tests.BankSagaTests.setUp
    create = economy_tests.BankSagaTests.create
    due = economy_tests.BankSagaTests.due

    def test_balance_rejection_then_partial_reservation_compensates_without_starting(self):
        second = self.create()
        FakePaymentBank.balances[str(self.opponent.pk)] = 0
        with self.assertRaises(ValidationError):
            accept_offer(actor=self.opponent, challenge_id=second.pk, version=1, revision=0)
        self.assertFalse(Duel.objects.filter(agreement__challenge=second).exists())
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.CANCELED)
        reconcile_duel(self.duel.pk)
        self.assertEqual(FakePaymentBank.balances[str(self.creator.pk)], 100)
        self.assertEqual(FakePaymentBank.balances[str(self.opponent.pk)], 0)
        self.assertFalse(FakePaymentBank.holds)
        self.assertEqual(self.duel.economic_operations.filter(kind='release', status='confirmed').count(), 1)
        self.assertFalse(CombatSession.objects.filter(duel=self.duel).exists())

    def test_unknown_reservation_cancel_reconciles_and_returns_without_double_charge(self):
        FakePaymentBank.faults[('reserve', str(self.creator.pk))] = 'timeout_after'
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.PENDING_RESERVATION)
        request_cancellation(actor=self.opponent, duel_id=self.duel.pk)
        self.assertFalse(self.duel.economic_operations.filter(kind='release').exists())
        self.due()
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.CANCELED)
        reconcile_duel(self.duel.pk)
        reserve = self.duel.economic_operations.get(kind='reserve')
        self.assertEqual(reserve.attempts, 1)
        self.assertEqual(reserve.reconciliation_attempts, 1)
        self.assertEqual(FakePaymentBank.balances[str(self.creator.pk)], 100)
        self.assertEqual(FakePaymentBank.balances[str(self.opponent.pk)], 100)
        self.assertFalse(FakePaymentBank.holds)


@override_settings(
    DEBUG=True,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    DUCKYCLASH_PAYMENT_GATEWAY='',
    DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.bank.ProfileBalanceGateway',
    DUCKYCLASH_KNOWLEDGE_GATEWAY='apps.duckyclash.knowledge.LMSKnowledgeGateway',
    DUCKYCLASH_ECOMOTOR_GATEWAY='apps.duckyclash.ecomotor.DevelopmentFakeEcoMotor',
    DUCKYCLASH_DOUBLE_KO='draw',
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
)
class DoubleKORefundJourneyTests(TransactionTestCase):
    setUp = engine_tests.DuelEngineTests.setUp
    create = engine_tests.DuelEngineTests.create
    accept = engine_tests.DuelEngineTests.accept
    publish = engine_tests.DuelEngineTests.publish
    at = engine_tests.DuelEngineTests.at
    begin = engine_tests.DuelEngineTests.begin
    option = engine_tests.DuelEngineTests.option

    def test_simultaneous_answers_double_ko_returns_both_reservations_once(self):
        for number in range(2, 5):
            self.publish(title=f'Ronda de doble KO {number}')
        question = self.begin()
        for number in range(1, 5):
            opens = (question.opens_at - self.start).total_seconds()
            with self.at(opens):
                self.engine.advance()
                self.assertEqual(self.engine.snapshot(self.creator)['phase'], Phase.QUESTION_ACTIVE)
                for actor in (self.creator, self.opponent):
                    submit_answer(actor=actor, question_id=question.pk, option_id=self.option(question))
            closes = (question.closes_at - self.start).total_seconds()
            with self.at(closes):
                self.engine.advance()
                self.engine.advance()
            self.assertEqual(
                list(ParticipantState.objects.filter(duel=self.duel).order_by('seat').values_list('hp', flat=True)),
                [100 - 25 * number] * 2,
            )
            if number < 4:
                next_at = (CombatSession.objects.get(duel=self.duel).deadline - self.start).total_seconds()
                with self.at(next_at):
                    self.engine.advance()
                question = self.duel.questions.get(number=number + 1)
        resolution = DuelResolution.objects.get(duel=self.duel)
        self.assertTrue(resolution.is_draw)
        self.assertIsNone(resolution.winner_id)
        with self.at(closes + 1), override_settings(DUCKYCLASH_PAYMENT_GATEWAY='apps.duckyclash.test_economy.FakePaymentBank'):
            self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.FINISHED)
            reconcile_duel(self.duel.pk)
        self.assertEqual(FakePaymentBank.balances[str(self.creator.pk)], 200)
        self.assertEqual(FakePaymentBank.balances[str(self.opponent.pk)], 200)
        self.assertEqual(self.duel.economic_operations.filter(kind=EconomicOperation.Kind.RELEASE).count(), 2)
        self.assertFalse(FakePaymentBank.holds)
        self.assertEqual(DuelResolution.objects.filter(duel=self.duel).count(), 1)
