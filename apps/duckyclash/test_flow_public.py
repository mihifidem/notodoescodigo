"""Public competition through two real database connections and bank refund."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, local
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.ducky_arenas.models import BattleChallenge
from . import test_open_challenges as open_tests
from . import test_private_challenges as private_tests
from .economy import reconcile_duel, request_cancellation
from .models import ChallengeAgreement, Duel
from .selectors import open_challenges
from .services import accept_offer
from .tasks import expire_open_challenges
from .test_economy import FakePaymentBank


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.test_private_challenges.FakeBankGateway',
    DUCKYCLASH_PAYMENT_GATEWAY='',
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
)
class PublicCompetitionJourneyTests(TransactionTestCase):
    setUp = private_tests.PrivateChallengeTests.setUp
    create = open_tests.OpenChallengeTests.create

    def test_competition_only_winner_reserves_then_cancellation_refunds_once(self):
        challenge = self.create()
        barrier, thread_state = Barrier(2), local()

        def balance(player, stake):
            self.assertFalse(connection.in_atomic_block)
            thread_state.calls = getattr(thread_state, 'calls', 0) + 1
            if thread_state.calls == 1:
                barrier.wait(timeout=15)

        def accept(actor):
            close_old_connections()
            try:
                return accept_offer(actor=actor, challenge_id=challenge.pk, version=1, revision=0).pk
            except (ValidationError, PermissionDenied):
                return None
            except OperationalError as exc:
                if connection.vendor != 'sqlite' or 'locked' not in str(exc).lower():
                    raise
                return None
            finally:
                close_old_connections()

        with patch('apps.duckyclash.services.require_balance', side_effect=balance):
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(accept, [self.opponent, self.outsider]))
        self.assertLessEqual(sum(pk is not None for pk in outcomes), 1)
        if connection.vendor != 'sqlite':
            self.assertEqual(sum(pk is not None for pk in outcomes), 1)
        if not Duel.objects.exists():
            accept_offer(actor=self.opponent, challenge_id=challenge.pk, version=1, revision=0)
        duel = Duel.objects.get()
        self.assertEqual(ChallengeAgreement.objects.count(), 1)
        self.assertFalse(open_challenges().exists())
        winner = duel.agreement.opponent
        loser = self.outsider if winner.pk == self.opponent.pk else self.opponent
        with self.assertRaises((ValidationError, PermissionDenied)):
            accept_offer(actor=loser, challenge_id=challenge.pk, version=1, revision=0)
        FakePaymentBank.balances = {str(p.pk): 100 for p in (self.creator, self.opponent, self.outsider)}
        FakePaymentBank.receipts, FakePaymentBank.holds = {}, {}
        FakePaymentBank.actions, FakePaymentBank.faults = [], {}
        with override_settings(DUCKYCLASH_PAYMENT_GATEWAY='apps.duckyclash.test_economy.FakePaymentBank'):
            self.assertEqual(reconcile_duel(duel.pk).status, Duel.Status.SCHEDULED)
            self.assertEqual(set(duel.economic_operations.values_list('player_id', flat=True)), {self.creator.pk, winner.pk})
            self.assertEqual(FakePaymentBank.balances[str(loser.pk)], 100)
            request_cancellation(actor=winner, duel_id=duel.pk)
            self.assertEqual(reconcile_duel(duel.pk).status, Duel.Status.CANCELED)
            reconcile_duel(duel.pk)
        self.assertEqual(list(FakePaymentBank.balances.values()), [100, 100, 100])
        self.assertEqual(duel.economic_operations.filter(kind='release').count(), 2)
        self.assertFalse(FakePaymentBank.holds)

    def test_expiry_is_hidden_before_worker_and_cannot_be_accepted_or_self_accepted(self):
        challenge = self.create()
        with self.assertRaises(PermissionDenied):
            accept_offer(actor=self.creator, challenge_id=challenge.pk, version=1, revision=0)
        BattleChallenge.objects.filter(pk=challenge.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertFalse(open_challenges().exists())
        with self.assertRaises(ValidationError):
            accept_offer(actor=self.opponent, challenge_id=challenge.pk, version=1, revision=0)
        expire_open_challenges.run()
        challenge.refresh_from_db()
        self.assertEqual(challenge.status, BattleChallenge.Status.EXPIRED)
        self.assertFalse(Duel.objects.exists())
