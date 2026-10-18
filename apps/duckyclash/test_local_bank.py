from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .bank import BankOutcome, BankUnavailable, UserPointsBalanceGateway, require_balance
from .economy import (
    claim_operation, next_operation, operation_request, plan_operation, reconcile_duel,
    request_cancellation,
)
from .history import publish_result
from .local_bank import LOCAL_GATEWAY, LocalPointsBankGateway, points_movement_total
from .models import Duel, DuelResolution, EconomicOperation, LocalBankReceipt
from .services import accept_offer
from . import test_private_challenges as private


@override_settings(
    DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.bank.UserPointsBalanceGateway',
    DUCKYCLASH_PAYMENT_GATEWAY=LOCAL_GATEWAY,
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class LocalPointsBankTests(TransactionTestCase):
    create = private.PrivateChallengeTests.create

    def setUp(self):
        private.PrivateChallengeTests.setUp(self)
        get_user_model().objects.filter(pk__in=[self.creator.pk, self.opponent.pk, self.outsider.pk]).update(
            puntos=100, puntos_totales=500,
        )
        self.gateway = LocalPointsBankGateway()
        self.duel = self.accept_pending()

    def accept_pending(self):
        challenge = self.create()
        with patch('apps.duckyclash.economy.enqueue_local_reconciliation'):
            return accept_offer(actor=self.opponent, challenge_id=challenge.pk, version=1, revision=0)

    def balance(self, player):
        return self.gateway.available_balance(player)

    def resolve(self, draw=False):
        Duel.objects.filter(pk=self.duel.pk).update(status=Duel.Status.SETTLEMENT_PENDING)
        self.duel.refresh_from_db()
        return DuelResolution.objects.create(
            duel=self.duel, winner=None if draw else self.creator, is_draw=draw, reason='Resultado del servidor',
        )

    def test_balance_uses_fresh_spendable_points_not_profile_or_accumulated(self):
        self.assertEqual(self.creator.puntos, 0)
        self.assertEqual(UserPointsBalanceGateway().available_balance(self.creator), 100)
        get_user_model().objects.filter(pk=self.creator.pk).update(puntos=7)
        with self.assertRaises(ValidationError):
            require_balance(self.creator, 20)
        self.assertEqual(self.creator.user_profile_arenas.duckCoins, 200)
        self.assertEqual(LocalBankReceipt.objects.count(), 0)

    def test_acceptance_reserves_both_without_celery_and_double_acceptance_is_rejected(self):
        challenge = self.create()
        duel = accept_offer(actor=self.opponent, challenge_id=challenge.pk, version=1, revision=0)
        duel.refresh_from_db()
        self.assertEqual(duel.status, Duel.Status.SCHEDULED)
        self.assertEqual((self.balance(self.creator), self.balance(self.opponent)), (80, 80))
        with self.assertRaises(ValidationError):
            accept_offer(actor=self.opponent, challenge_id=challenge.pk, version=1, revision=0)
        reconcile_duel(duel.pk)
        self.assertEqual(LocalBankReceipt.objects.count(), 2)

    def test_insufficient_points_during_acceptance_never_moves_money(self):
        get_user_model().objects.filter(pk=self.opponent.pk).update(puntos=19)
        challenge = self.create()
        with self.assertRaises(ValidationError):
            accept_offer(actor=self.opponent, challenge_id=challenge.pk, version=1, revision=0)
        self.assertEqual((self.balance(self.creator), self.balance(self.opponent)), (100, 19))
        self.assertFalse(LocalBankReceipt.objects.exists())

    def test_partial_reservation_compensates_if_second_player_spent_points(self):
        get_user_model().objects.filter(pk=self.opponent.pk).update(puntos=0)
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.CANCELED)
        self.assertEqual((self.balance(self.creator), self.balance(self.opponent)), (100, 0))
        self.assertEqual(LocalBankReceipt.objects.filter(hold_state='released').count(), 1)
        reconcile_duel(self.duel.pk)
        self.assertEqual(self.balance(self.creator), 100)

    def test_first_reserve_rejection_never_debits_opponent(self):
        get_user_model().objects.filter(pk=self.creator.pk).update(puntos=0)
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.CANCELED)
        self.assertEqual(self.balance(self.opponent), 100)
        self.assertEqual(LocalBankReceipt.objects.count(), 1)

    def test_receipt_survives_crash_between_debit_and_saga_record(self):
        operation = next_operation(self.duel.pk)
        request, _token, _lookup = claim_operation(operation.pk)
        result = self.gateway.reserve(request)
        self.assertEqual(result.outcome, BankOutcome.CONFIRMED)
        EconomicOperation.objects.filter(pk=operation.pk).update(
            lease_until=timezone.now() - timedelta(seconds=1), next_attempt_at=timezone.now(),
        )
        self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.SCHEDULED)
        operation.refresh_from_db()
        self.assertEqual(operation.attempts, 1)
        self.assertEqual(operation.reconciliation_attempts, 1)
        self.assertEqual((self.balance(self.creator), self.balance(self.opponent)), (80, 80))
        self.assertEqual(self.gateway.reserve(request), result)
        self.assertEqual(self.gateway.lookup(request), result)

    def test_cancellation_refunds_once_immediately(self):
        reconcile_duel(self.duel.pk)
        request_cancellation(actor=self.creator, duel_id=self.duel.pk)
        self.duel.refresh_from_db()
        self.assertEqual(self.duel.status, Duel.Status.CANCELED)
        request_cancellation(actor=self.creator, duel_id=self.duel.pk)
        reconcile_duel(self.duel.pk)
        self.assertEqual((self.balance(self.creator), self.balance(self.opponent)), (100, 100))
        self.assertEqual(LocalBankReceipt.objects.filter(hold_state='released').count(), 2)
        self.assertEqual(points_movement_total(self.creator), 0)

    def test_draw_refunds_and_winner_payout_preserve_history_and_legacy_coins(self):
        for draw, expected in ((True, (100, 100)), (False, (120, 80))):
            with self.subTest(draw=draw):
                if not draw:
                    self.duel = self.accept_pending()
                reconcile_duel(self.duel.pk)
                resolution = self.resolve(draw)
                publish_result(resolution)
                self.duel.refresh_from_db()
                self.assertEqual(self.duel.status, Duel.Status.FINISHED)
                publish_result(resolution)
                reconcile_duel(self.duel.pk)
                self.assertEqual((self.balance(self.creator), self.balance(self.opponent)), expected)
                for player in (self.creator, self.opponent):
                    player.refresh_from_db()
                    player.user_profile_arenas.refresh_from_db()
                    self.assertEqual(player.puntos_totales, 500)
                    self.assertEqual(player.nivel, 'noob')
                    self.assertEqual(player.user_profile_arenas.duckCoins, 200)
        operation = self.duel.economic_operations.get(kind='settle')
        request = operation_request(operation)
        self.assertEqual(self.gateway.settle(request).outcome, BankOutcome.CONFIRMED)
        self.assertEqual(self.balance(self.creator), 120)
        self.assertEqual(points_movement_total(self.creator), 20)
        self.assertEqual(points_movement_total(self.opponent), -20)

    def test_atomic_payout_rolls_back_hold_consumption_when_receipt_creation_fails(self):
        reconcile_duel(self.duel.pk)
        self.resolve()
        from .economy import request_settlement
        request_settlement(self.duel.pk)
        request = operation_request(next_operation(self.duel.pk))
        with patch.object(LocalBankReceipt.objects, 'create', side_effect=RuntimeError('receipt failure')):
            with self.assertRaises(RuntimeError):
                self.gateway.settle(request)
        self.assertEqual((self.balance(self.creator), self.balance(self.opponent)), (80, 80))
        self.assertEqual(LocalBankReceipt.objects.filter(hold_state='held').count(), 2)
        self.gateway.settle(request)
        self.assertEqual(self.balance(self.creator), 120)

    def test_forged_payload_key_player_winner_and_hold_references_are_rejected(self):
        operation = next_operation(self.duel.pk)
        request = operation_request(operation)
        for forged in (
            replace(request, amount=1), replace(request, player=str(self.outsider.pk)),
            replace(request, winner=str(self.outsider.pk)), replace(request, duel='other-duel'),
            replace(request, key='invalid-key'),
        ):
            with self.subTest(request=forged), self.assertRaises(BankUnavailable):
                self.gateway.reserve(forged)
        self.assertEqual(self.balance(self.creator), 100)
        reconcile_duel(self.duel.pk)
        self.resolve()
        from .economy import request_settlement
        request_settlement(self.duel.pk)
        settlement = operation_request(next_operation(self.duel.pk))
        for forged in (
            replace(settlement, winner=str(self.outsider.pk)),
            replace(settlement, reservation_references=('foreign-bank:1', 'foreign-bank:2')),
        ):
            with self.assertRaises(BankUnavailable):
                self.gateway.settle(forged)
        self.assertEqual(LocalBankReceipt.objects.filter(hold_state='held').count(), 2)

    def test_previous_unidentified_provider_is_not_reinterpreted_as_points(self):
        operation = next_operation(self.duel.pk)
        EconomicOperation.objects.filter(pk=operation.pk).update(attempts=1)
        Duel.objects.filter(pk=self.duel.pk).update(payment_gateway='')
        with self.assertRaises(BankUnavailable):
            reconcile_duel(self.duel.pk)
        self.assertEqual(self.balance(self.creator), 100)

    def test_provider_is_pinned_after_settings_change(self):
        with override_settings(DUCKYCLASH_PAYMENT_GATEWAY='apps.duckyclash.test_economy.FakePaymentBank'):
            self.assertEqual(reconcile_duel(self.duel.pk).status, Duel.Status.SCHEDULED)
        self.assertEqual(self.balance(self.creator), 80)

    def test_worker_uses_frozen_provider_even_if_current_default_is_disabled(self):
        from .tasks import reconcile_bank_operations
        with override_settings(DUCKYCLASH_PAYMENT_GATEWAY=''):
            self.assertEqual(reconcile_bank_operations.run(), 1)
        self.assertEqual(self.balance(self.creator), 80)

    def test_cancel_before_submission_blocks_delayed_debit(self):
        operation = next_operation(self.duel.pk)
        request = operation_request(operation)
        request_cancellation(actor=self.creator, duel_id=self.duel.pk)
        self.assertEqual(self.gateway.reserve(request).outcome, BankOutcome.REJECTED)
        self.assertEqual(self.balance(self.creator), 100)

    def test_cancel_after_claim_without_debit_conclusively_rejects_the_local_reserve(self):
        operation = next_operation(self.duel.pk)
        request, _token, _lookup = claim_operation(operation.pk)
        EconomicOperation.objects.filter(pk=operation.pk).update(
            lease_until=timezone.now() - timedelta(seconds=1), next_attempt_at=timezone.now(),
        )
        request_cancellation(actor=self.creator, duel_id=self.duel.pk)
        self.duel.refresh_from_db()
        self.assertEqual(self.duel.status, Duel.Status.CANCELED)
        self.assertEqual(self.gateway.reserve(request).outcome, BankOutcome.REJECTED)
        self.assertEqual(self.balance(self.creator), 100)

    def test_concurrent_different_duels_cannot_overspend_the_same_balance(self):
        other_duel = self.accept_pending()
        get_user_model().objects.filter(pk=self.creator.pk).update(puntos=30)
        requests = [operation_request(plan_operation(duel, 'reserve', self.creator.pk))
                    for duel in (self.duel, other_duel)]
        barrier = Barrier(2)

        def worker(request):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                return self.gateway.reserve(request)
            except BankUnavailable:
                return None
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, requests))
        results = [result or self.gateway.reserve(request) for result, request in zip(results, requests)]
        self.assertEqual(sum(result.outcome == BankOutcome.CONFIRMED for result in results), 1)
        self.assertEqual(self.balance(self.creator), 10)

    def test_concurrent_same_key_never_debits_twice(self):
        request = operation_request(next_operation(self.duel.pk))
        barrier = Barrier(2)

        def worker():
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                return self.gateway.reserve(request)
            except BankUnavailable:
                return None
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        self.assertEqual(self.gateway.reserve(request).outcome, BankOutcome.CONFIRMED)
        self.assertEqual(self.balance(self.creator), 80)
        self.assertEqual(LocalBankReceipt.objects.count(), 1)
        for result in results:
            if result:
                self.assertEqual(result.outcome, BankOutcome.CONFIRMED)

    def test_earning_points_from_stale_instance_does_not_restore_reserved_points(self):
        self.creator.refresh_from_db()
        reconcile_duel(self.duel.pk)
        self.creator.agregar_puntos(5)
        self.assertEqual(self.balance(self.creator), 85)
        self.assertEqual(self.creator.puntos_totales, 505)
        from apps.gamification.utils import asignar_puntos_usuario
        self.opponent.puntos = 100
        asignar_puntos_usuario(self.opponent, 7)
        self.assertEqual(self.balance(self.opponent), 87)

    def test_reward_redemption_cannot_spend_points_already_reserved(self):
        from django.test import RequestFactory
        from django.contrib.messages.storage.fallback import FallbackStorage
        from apps.rewards.models import Recompensa, CanjeRecompensa
        from apps.rewards.views import CanjeView, RecompensaViewSet
        from rest_framework.test import APIRequestFactory, force_authenticate
        reward = Recompensa.objects.create(nombre='Objeto', puntos_necesarios=90)
        self.creator.refresh_from_db()
        reconcile_duel(self.duel.pk)
        request = RequestFactory().post('/rewards/')
        request.user = self.creator
        request.session = {}
        request._messages = FallbackStorage(request)
        self.assertEqual(CanjeView.as_view()(request, pk=reward.pk).status_code, 302)
        self.assertFalse(CanjeRecompensa.objects.exists())
        api_request = APIRequestFactory().post('/rewards/')
        force_authenticate(api_request, user=self.creator)
        response = RecompensaViewSet.as_view({'post': 'canjear'})(api_request, pk=reward.pk)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.balance(self.creator), 80)
        reward.puntos_necesarios = 30
        reward.save()
        api_request = APIRequestFactory().post('/rewards/')
        force_authenticate(api_request, user=self.creator)
        response = RecompensaViewSet.as_view({'post': 'canjear'})(api_request, pk=reward.pk)
        self.assertEqual(response.status_code, 201)
        request_cancellation(actor=self.creator, duel_id=self.duel.pk)
        self.assertEqual(self.balance(self.creator), 70)
        self.creator.refresh_from_db()
        self.assertEqual(self.creator.puntos_totales, 500)

    def test_admin_rebuild_preserves_holds_and_paid_duel_movements(self):
        from apps.analytics.views import DashboardAdminView
        from apps.gamification.models import Logro, LogroUsuario
        logro = Logro.objects.create(nombre='Puntos originales')
        LogroUsuario.objects.create(usuario=self.creator, logro=logro, puntos_asignados=100)
        reconcile_duel(self.duel.pk)
        _before, after = DashboardAdminView._recalcular_puntos_usuario(self.creator)
        self.assertEqual(after['puntos'], 80)
        self.assertEqual(after['puntos_totales'], 100)
        publish_result(self.resolve())
        _before, after = DashboardAdminView._recalcular_puntos_usuario(self.creator)
        self.assertEqual(after['puntos'], 120)
        self.assertEqual(after['puntos_totales'], 100)

    def test_admin_historical_counter_rebuild_does_not_overwrite_spendable_balance(self):
        from apps.analytics.models import RegistroActividad
        RegistroActividad.objects.create(usuario=self.creator, tipo='tema_completado', puntos_ganados=100)
        reconcile_duel(self.duel.pk)
        self.outsider.is_staff = True
        self.outsider.save(update_fields=['is_staff'])
        self.client.force_login(self.outsider)
        response = self.client.post(
            reverse('analytics:admin_alumno_puntos', args=[self.creator.pk]), {'accion': 'recalcular_acumulado'},
        )
        self.assertEqual(response.status_code, 302)
        self.creator.refresh_from_db()
        self.assertEqual(self.creator.puntos, 80)
        self.assertEqual(self.creator.puntos_totales, 100)
