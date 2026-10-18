from dataclasses import replace
from datetime import timedelta
from io import StringIO
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import OperationalError, close_old_connections, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .bank import (
    BankOutcome, BankResult, BankUnavailable, TransactionalBankGateway,
    get_payment_gateway,
    require_balance,
)
from .economy import (
    MAX_RECONCILIATIONS, claim_operation, next_operation, operation_request, reconcile_duel, record_result,
    request_cancellation, request_settlement,
)
from .models import Duel, DuelResolution, EconomicOperation
from .services import accept_offer
from .tasks import reconcile_bank_operations
from . import test_private_challenges as private


class FakePaymentBank(TransactionalBankGateway):
    """Test-only ledger. It never changes a Django balance."""

    balances = {}
    receipts = {}
    holds = {}
    actions = []
    faults = {}

    def available_balance(self, player):
        return self.balances[str(player.pk)]

    def lookup(self, request):
        if connection.in_atomic_block:
            raise AssertionError('Bank lookup inside DB transaction')
        self.actions.append(('lookup', request.key))
        return self.receipts.get(request.key, BankResult(request, BankOutcome.NOT_FOUND))

    def apply(self, request):
        if connection.in_atomic_block:
            raise AssertionError('Bank mutation inside DB transaction')
        self.actions.append((request.kind, request.key))
        if request.key in self.receipts:
            receipt = self.receipts[request.key]
            if receipt.request != request:
                raise AssertionError('Idempotency key reused with different terms')
            return receipt
        fault = self.faults.pop((request.kind, request.player), None)
        if fault == 'timeout_before':
            raise TimeoutError()
        if fault == 'unknown':
            return BankResult(request, BankOutcome.UNKNOWN)
        if fault == 'reject' or (
            request.kind == 'reserve' and self.balances[request.player] < request.amount
        ):
            receipt = BankResult(request, BankOutcome.REJECTED)
        else:
            if request.kind == 'reserve':
                self.balances[request.player] -= request.amount
                self.holds[request.key] = request
            elif request.kind == 'release':
                hold = self.holds.pop(request.reservation_keys[0])
                self.balances[hold.player] += hold.amount
            elif request.kind == 'settle':
                holds = [self.holds.pop(key) for key in request.reservation_keys]
                if sum(hold.amount for hold in holds) != request.amount:
                    raise AssertionError('Wrong pot')
                self.balances[request.winner] += request.amount
            receipt = BankResult(request, BankOutcome.CONFIRMED, 'fake:' + request.key)
        self.receipts[request.key] = receipt
        if fault == 'timeout_after':
            raise TimeoutError()
        return receipt

    reserve = apply
    release = apply
    settle = apply


@override_settings(
    DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.test_private_challenges.FakeBankGateway',
    DUCKYCLASH_PAYMENT_GATEWAY='apps.duckyclash.test_economy.FakePaymentBank',
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
)
class BankSagaTests(TransactionTestCase):
    create = private.PrivateChallengeTests.create

    def setUp(self):
        private.PrivateChallengeTests.setUp(self)
        FakePaymentBank.balances = {str(player.pk): 100 for player in (self.creator, self.opponent, self.outsider)}
        FakePaymentBank.receipts = {}
        FakePaymentBank.holds = {}
        FakePaymentBank.actions = []
        FakePaymentBank.faults = {}
        self.challenge = self.create()
        self.duel = accept_offer(
            actor=self.opponent, challenge_id=self.challenge.pk, version=1, revision=0,
        )

    def due(self):
        EconomicOperation.objects.update(next_attempt_at=timezone.now() - timedelta(seconds=1))

    def balance(self, player):
        return FakePaymentBank.balances[str(player.pk)]

    def resolve(self, *, draw=False):
        # The future combat service owns this transition, not any browser endpoint.
        Duel.objects.filter(pk=self.duel.pk).update(status=Duel.Status.SETTLEMENT_PENDING)
        self.duel.refresh_from_db()
        return DuelResolution.objects.create(
            duel=self.duel, winner=None if draw else self.creator, is_draw=draw, reason='Resultado del servidor',
        )

    def test_reserve_both_once_double_acceptance_and_worker_replay(self):
        with self.assertRaises(ValidationError):
            accept_offer(actor=self.opponent, challenge_id=self.challenge.pk, version=1, revision=0)
        self.assertEqual(reconcile_bank_operations.run(), 1)
        self.duel.refresh_from_db()
        self.assertEqual(self.duel.status, Duel.Status.SCHEDULED)
        reconcile_duel(self.duel.pk)
        self.assertEqual(EconomicOperation.objects.count(), 2)
        self.assertEqual(len(FakePaymentBank.receipts), 2)
        self.assertEqual(self.balance(self.creator), 80)
        self.assertEqual(self.balance(self.opponent), 80)
        for player in (self.creator, self.opponent):
            player.user_profile_arenas.refresh_from_db()
            self.assertEqual(player.user_profile_arenas.duckCoins, 200)

    def test_insufficient_first_reservation_never_charges_second_player(self):
        FakePaymentBank.balances[str(self.creator.pk)] = 0
        duel = reconcile_duel(self.duel.pk)
        self.assertEqual(duel.status, Duel.Status.CANCELED)
        self.assertEqual(self.balance(self.opponent), 100)
        self.assertFalse(FakePaymentBank.holds)
        self.assertEqual(EconomicOperation.objects.count(), 1)

    def test_partial_reservation_compensates_and_cannot_activate(self):
        FakePaymentBank.balances[str(self.opponent.pk)] = 0
        duel = reconcile_duel(self.duel.pk)
        self.assertEqual(duel.status, Duel.Status.CANCELED)
        self.assertEqual(self.balance(self.creator), 100)
        self.assertFalse(FakePaymentBank.holds)
        release = EconomicOperation.objects.get(kind='release')
        self.assertEqual(release.player_id, self.creator.pk)
        self.assertEqual(release.status, EconomicOperation.Status.CONFIRMED)
        reconcile_duel(self.duel.pk)
        self.assertEqual(self.balance(self.creator), 100)

    def test_timeout_after_reserve_is_looked_up_not_recharged(self):
        FakePaymentBank.faults[('reserve', str(self.creator.pk))] = 'timeout_after'
        duel = reconcile_duel(self.duel.pk)
        self.assertEqual(duel.status, Duel.Status.PENDING_RESERVATION)
        operation = EconomicOperation.objects.get()
        self.assertEqual(operation.status, EconomicOperation.Status.UNCERTAIN)
        self.assertEqual(self.balance(self.creator), 80)
        self.due()
        duel = reconcile_duel(self.duel.pk)
        operation.refresh_from_db()
        self.assertEqual(operation.attempts, 1)
        self.assertEqual(operation.reconciliation_attempts, 1)
        self.assertEqual(duel.status, Duel.Status.SCHEDULED)
        self.assertEqual(self.balance(self.creator), 80)

    def test_timeout_before_reserve_only_retries_after_authoritative_not_found(self):
        FakePaymentBank.faults[('reserve', str(self.creator.pk))] = 'timeout_before'
        reconcile_duel(self.duel.pk)
        self.due()
        reconcile_duel(self.duel.pk)
        operation = EconomicOperation.objects.get()
        self.assertEqual(operation.status, EconomicOperation.Status.PENDING)
        self.assertEqual(operation.attempts, 1)
        self.due()
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.SCHEDULED)
        operation.refresh_from_db()
        self.assertEqual(operation.attempts, 2)
        self.assertEqual(len(FakePaymentBank.receipts), 2)

    def test_unknown_reservation_blocks_cancellation_until_reconciled(self):
        FakePaymentBank.faults[('reserve', str(self.creator.pk))] = 'timeout_after'
        reconcile_duel(self.duel.pk)
        request_cancellation(actor=self.opponent, duel_id=self.duel.pk)
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.PENDING_RESERVATION)
        self.assertFalse(EconomicOperation.objects.filter(kind='release').exists())
        self.due()
        duel = reconcile_duel(self.duel.pk)
        self.assertEqual(duel.status, Duel.Status.CANCELED)
        self.assertEqual(self.balance(self.creator), 100)
        self.assertEqual(self.balance(self.opponent), 100)

    def test_cancel_unknown_not_found_never_submits_after_cancel(self):
        FakePaymentBank.faults[('reserve', str(self.creator.pk))] = 'timeout_before'
        reconcile_duel(self.duel.pk)
        request_cancellation(actor=self.creator, duel_id=self.duel.pk)
        self.due()
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.PENDING_RESERVATION)
        self.assertFalse(FakePaymentBank.receipts)
        self.assertFalse(FakePaymentBank.holds)
        operation = EconomicOperation.objects.get()
        request = operation_request(operation)
        # A bank-side terminal rejection/tombstone is proof that no late hold can apply.
        FakePaymentBank.receipts[request.key] = BankResult(request, BankOutcome.REJECTED)
        self.due()
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.CANCELED)

    def test_cancel_scheduled_releases_both_once(self):
        reconcile_duel(self.duel.pk)
        request_cancellation(actor=self.creator, duel_id=self.duel.pk)
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.CANCELED)
        request_cancellation(actor=self.opponent, duel_id=self.duel.pk)
        reconcile_duel(self.duel.pk)
        self.assertEqual(self.balance(self.creator), 100)
        self.assertEqual(self.balance(self.opponent), 100)
        self.assertEqual(EconomicOperation.objects.filter(kind='release').count(), 2)

    def test_cancellation_permissions_timing_and_csrf(self):
        with self.assertRaises(PermissionDenied):
            request_cancellation(actor=self.outsider, duel_id=self.duel.pk)
        url = reverse('duckyclash:duel_cancel', args=[self.duel.pk])
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.creator)
        self.assertEqual(client.get(url).status_code, 405)
        self.assertEqual(client.post(url).status_code, 403)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(url).status_code, 403)
        with patch('apps.duckyclash.economy.timezone.now', return_value=self.schedule):
            with self.assertRaises(ValidationError):
                request_cancellation(actor=self.creator, duel_id=self.duel.pk)
        self.client.force_login(self.creator)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.duel.refresh_from_db()
        self.assertEqual(self.duel.status, Duel.Status.CANCELED)

    def test_winner_paid_full_pot_once_including_duplicate_worker(self):
        reconcile_duel(self.duel.pk)
        self.resolve()
        request_settlement(self.duel.pk)
        request_settlement(self.duel.pk)
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.FINISHED)
        reconcile_duel(self.duel.pk)
        self.assertEqual(self.balance(self.creator), 120)
        self.assertEqual(self.balance(self.opponent), 80)
        settlement = EconomicOperation.objects.get(kind='settle')
        self.assertEqual(settlement.amount, 40)
        self.assertEqual(len(settlement.request_payload['reservation_references']), 2)
        self.assertFalse(FakePaymentBank.holds)

    def test_payment_timeout_reconciles_without_duplicate_payout_or_refund(self):
        reconcile_duel(self.duel.pk)
        self.resolve()
        FakePaymentBank.faults[('settle', '')] = 'timeout_after'
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.SETTLEMENT_PENDING)
        with self.assertRaises(ValidationError):
            request_cancellation(actor=self.creator, duel_id=self.duel.pk)
        self.assertEqual(self.balance(self.creator), 120)
        self.due()
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.FINISHED)
        self.assertEqual(self.balance(self.creator), 120)
        self.assertEqual(EconomicOperation.objects.get(kind='settle').attempts, 1)
        self.assertFalse(EconomicOperation.objects.filter(kind='release').exists())

    def test_draw_refunds_both_without_settlement(self):
        reconcile_duel(self.duel.pk)
        self.resolve(draw=True)
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.FINISHED)
        self.assertEqual(self.balance(self.creator), 100)
        self.assertEqual(self.balance(self.opponent), 100)
        self.assertFalse(EconomicOperation.objects.filter(kind='settle').exists())
        self.assertEqual(EconomicOperation.objects.filter(kind='release').count(), 2)

    def test_failed_release_does_not_pretend_refund_and_unknown_release_is_reconciled(self):
        reconcile_duel(self.duel.pk)
        request_cancellation(actor=self.creator, duel_id=self.duel.pk)
        FakePaymentBank.faults[('release', str(self.creator.pk))] = 'timeout_after'
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.SCHEDULED)
        self.due()
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.CANCELED)
        self.assertEqual(self.balance(self.creator), 100)
        self.assertEqual(self.balance(self.opponent), 100)

    def test_rejected_release_requires_review_and_never_marks_canceled(self):
        reconcile_duel(self.duel.pk)
        request_cancellation(actor=self.creator, duel_id=self.duel.pk)
        FakePaymentBank.faults[('release', str(self.creator.pk))] = 'reject'
        duel = reconcile_duel(self.duel.pk)
        self.assertNotEqual(duel.status, Duel.Status.CANCELED)
        self.assertEqual(EconomicOperation.objects.get(kind='release', player=self.creator).last_error, 'bank_rejected')
        self.assertEqual(self.balance(self.creator), 80)
        self.assertEqual(self.balance(self.opponent), 100)

    def test_no_browser_supplied_result_or_payout_without_both_holds(self):
        with self.assertRaises(ValidationError):
            request_settlement(self.duel.pk)
        self.resolve()
        with self.assertRaises(ValidationError):
            request_settlement(self.duel.pk)
        self.assertFalse(EconomicOperation.objects.exists())

    def test_crash_after_submission_uses_lease_recovery_and_ignores_stale_response(self):
        operation = next_operation(self.duel.pk)
        request, old_token, lookup = claim_operation(operation.pk)
        self.assertFalse(lookup)
        receipt = FakePaymentBank().reserve(request)
        self.assertIsNone(claim_operation(operation.pk))
        EconomicOperation.objects.filter(pk=operation.pk).update(
            lease_until=timezone.now() - timedelta(seconds=1),
        )
        _, new_token, lookup = claim_operation(operation.pk)
        self.assertTrue(lookup)
        record_result(operation.pk, old_token, receipt)
        operation.refresh_from_db()
        self.assertEqual(operation.status, EconomicOperation.Status.UNCERTAIN)
        record_result(operation.pk, new_token, FakePaymentBank().lookup(request))
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.SCHEDULED)
        self.assertEqual(self.balance(self.creator), 80)

    def test_cancellation_between_plan_and_claim_prevents_bank_submission(self):
        operation = next_operation(self.duel.pk)
        request_cancellation(actor=self.creator, duel_id=self.duel.pk)
        self.assertIsNone(claim_operation(operation.pk))
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.CANCELED)
        self.assertFalse(FakePaymentBank.actions)

    def test_reconciliation_and_submission_retries_are_bounded(self):
        with patch.object(FakePaymentBank, 'reserve', side_effect=TimeoutError), patch.object(
            FakePaymentBank, 'lookup', side_effect=TimeoutError,
        ):
            for _ in range(MAX_RECONCILIATIONS + 3):
                self.due()
                reconcile_duel(self.duel.pk)
        operation = EconomicOperation.objects.get()
        self.assertEqual(operation.attempts, 1)
        self.assertEqual(operation.reconciliation_attempts, MAX_RECONCILIATIONS)
        self.assertEqual(operation.status, EconomicOperation.Status.UNCERTAIN)
        self.assertEqual(self.balance(self.creator), 100)

    def test_repeated_authoritative_absence_exhausts_submission_budget(self):
        with patch.object(FakePaymentBank, 'reserve', side_effect=TimeoutError):
            for _ in range(8):
                self.due()
                reconcile_duel(self.duel.pk)
        operation = EconomicOperation.objects.get()
        self.assertEqual(operation.attempts, 3)
        self.assertEqual(operation.status, EconomicOperation.Status.UNCERTAIN)
        self.duel.refresh_from_db()
        self.assertEqual(self.duel.status, Duel.Status.PENDING_RESERVATION)

    def test_not_found_during_cancel_does_not_ignore_a_late_remote_reservation(self):
        FakePaymentBank.faults[('reserve', str(self.creator.pk))] = 'timeout_before'
        reconcile_duel(self.duel.pk)
        request_cancellation(actor=self.creator, duel_id=self.duel.pk)
        self.due()
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.PENDING_RESERVATION)
        request = operation_request(EconomicOperation.objects.get())
        # The original request reaches the bank after an earlier lookup returned absent.
        FakePaymentBank().reserve(request)
        self.assertEqual(self.balance(self.creator), 80)
        self.due()
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.CANCELED)
        self.assertEqual(self.balance(self.creator), 100)

    def test_cancel_after_retry_was_planned_still_reconciles_the_original_submission(self):
        FakePaymentBank.faults[('reserve', str(self.creator.pk))] = 'timeout_before'
        reconcile_duel(self.duel.pk)
        self.due()
        reconcile_duel(self.duel.pk)
        operation = EconomicOperation.objects.get()
        self.assertEqual(operation.status, EconomicOperation.Status.PENDING)
        request_cancellation(actor=self.creator, duel_id=self.duel.pk)
        self.due()
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.PENDING_RESERVATION)
        self.assertEqual(sum(kind == 'reserve' for kind, _ in FakePaymentBank.actions), 1)
        FakePaymentBank().reserve(operation_request(operation))
        self.due()
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.CANCELED)
        self.assertEqual(self.balance(self.creator), 100)

    def test_wrong_receipt_cannot_activate_duel(self):
        def wrong(request):
            return BankResult(replace(request, amount=request.amount + 1), BankOutcome.CONFIRMED, 'wrong')

        with patch.object(FakePaymentBank, 'reserve', side_effect=wrong):
            reconcile_duel(self.duel.pk)
        self.duel.refresh_from_db()
        self.assertEqual(self.duel.status, Duel.Status.PENDING_RESERVATION)
        self.assertEqual(EconomicOperation.objects.get().status, EconomicOperation.Status.UNCERTAIN)

    def test_operator_can_resume_only_lookup_budget_and_request_identity_is_immutable(self):
        FakePaymentBank.faults[('reserve', str(self.creator.pk))] = 'timeout_after'
        reconcile_duel(self.duel.pk)
        operation = EconomicOperation.objects.get()
        operation.request_payload = dict(operation.request_payload, amount=999)
        with self.assertRaises(ValidationError):
            operation.save()
        EconomicOperation.objects.filter(pk=operation.pk).update(
            reconciliation_attempts=MAX_RECONCILIATIONS,
        )
        call_command('reconcile_clash_bank', duel=self.duel.pk, resume_lookups=True, stdout=StringIO())
        operation.refresh_from_db()
        self.assertEqual(operation.attempts, 1)
        self.assertEqual(operation.reconciliation_attempts, 1)
        self.duel.refresh_from_db()
        self.assertEqual(self.duel.status, Duel.Status.SCHEDULED)

    def test_cancel_while_network_reserve_is_inflight_compensates_late_success(self):
        reserve = FakePaymentBank.reserve

        def slow(bank, request):
            request_cancellation(actor=self.opponent, duel_id=self.duel.pk)
            return reserve(bank, request)

        with patch.object(FakePaymentBank, 'reserve', slow):
            duel = reconcile_duel(self.duel.pk)
        self.assertEqual(duel.status, Duel.Status.CANCELED)
        self.assertEqual(self.balance(self.creator), 100)
        self.assertEqual(self.balance(self.opponent), 100)
        self.assertEqual(EconomicOperation.objects.filter(kind='reserve').count(), 1)

    def test_unfinished_reservations_expire_at_scheduled_start_and_are_compensated(self):
        FakePaymentBank.faults[('reserve', str(self.opponent.pk))] = 'timeout_after'
        reconcile_duel(self.duel.pk)
        self.due()
        with patch('apps.duckyclash.economy.timezone.now', return_value=self.schedule):
            duel = reconcile_duel(self.duel.pk)
        self.assertEqual(duel.status, Duel.Status.CANCELED)
        self.assertEqual(self.balance(self.creator), 100)
        self.assertEqual(self.balance(self.opponent), 100)

    def test_rejected_payout_never_claims_finished_or_refunds_unilaterally(self):
        reconcile_duel(self.duel.pk)
        self.resolve()
        FakePaymentBank.faults[('settle', '')] = 'reject'
        duel = reconcile_duel(self.duel.pk)
        self.assertEqual(duel.status, Duel.Status.SETTLEMENT_PENDING)
        self.assertEqual(EconomicOperation.objects.get(kind='settle').status, EconomicOperation.Status.FAILED)
        self.assertFalse(EconomicOperation.objects.filter(kind='release').exists())
        self.assertEqual(self.balance(self.creator), 80)
        self.assertEqual(self.balance(self.opponent), 80)

    def test_unconfigured_or_balance_only_gateway_fails_closed(self):
        # Legacy duels without a frozen provider still require explicit configuration.
        Duel.objects.filter(pk=self.duel.pk).update(payment_gateway='')
        for path in ('', 'apps.duckyclash.bank.ProfileBalanceGateway', 'missing.bank.Gateway'):
            with self.subTest(path=path), override_settings(DUCKYCLASH_PAYMENT_GATEWAY=path):
                with self.assertRaises(BankUnavailable):
                    get_payment_gateway()
                with self.assertRaises(BankUnavailable):
                    reconcile_duel(self.duel.pk)
        self.assertFalse(EconomicOperation.objects.exists())

    def test_payment_gateway_is_balance_source_not_local_profile(self):
        profile = self.creator.user_profile_arenas
        profile.duckCoins = 0
        profile.save()
        require_balance(self.creator, 20)
        FakePaymentBank.balances[str(self.creator.pk)] = 0
        profile.duckCoins = 1000
        profile.save()
        with self.assertRaises(ValidationError):
            require_balance(self.creator, 20)

    def test_legacy_reference_is_not_treated_as_verified_bank_hold(self):
        EconomicOperation.objects.create(
            duel=self.duel, kind='reserve', player=self.creator, amount=20,
            status=EconomicOperation.Status.CONFIRMED, bank_reference='legacy-reference',
        )
        request_cancellation(actor=self.creator, duel_id=self.duel.pk)
        with self.assertRaises(ValidationError):
            reconcile_duel(self.duel.pk)
        self.assertFalse(FakePaymentBank.actions)
        self.assertFalse(EconomicOperation.objects.filter(kind='release').exists())

    def test_additive_migration_roundtrip_preserves_agreement_and_identity(self):
        leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
        try:
            MigrationExecutor(connection).migrate([('duckyclash', '0001_initial')])
            old_apps = MigrationExecutor(connection).loader.project_state(
                [('duckyclash', '0001_initial')],
            ).apps
            old_duel = old_apps.get_model('duckyclash', 'Duel').objects.get(pk=self.duel.pk)
            self.assertEqual(old_duel.agreement_id, self.duel.agreement_id)
        finally:
            MigrationExecutor(connection).migrate(leaves)
        self.duel.refresh_from_db()
        self.assertEqual(self.duel.bank_mode, Duel.BankMode.RESERVE)
        self.creator.user_profile_arenas.refresh_from_db()
        self.assertEqual(self.creator.user_profile_arenas.duckCoins, 200)

    def test_two_workers_cannot_claim_same_operation(self):
        operation = next_operation(self.duel.pk)
        barrier = Barrier(2)

        def worker():
            close_old_connections()
            barrier.wait(timeout=15)
            try:
                return claim_operation(operation.pk)
            except OperationalError as exc:
                if connection.vendor != 'sqlite' or 'locked' not in str(exc).lower():
                    raise
                return None
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: worker(), range(2)))
        self.assertLessEqual(sum(claim is not None for claim in claims), 1)
        if not any(claims):
            claims = [claim_operation(operation.pk)]
        request, token, _ = next(claim for claim in claims if claim)
        record_result(operation.pk, token, FakePaymentBank().reserve(request))
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.SCHEDULED)
        self.assertEqual(self.balance(self.creator), 80)
