import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.users.billing_models import (
    BillingOrder, BillingOrderLine, Charge, PaymentAttempt, Refund, ServiceProduct,
)
from apps.users.billing_services import (
    TERMS_VERSION, BillingUnavailable, confirm_verified_payment, create_zero_cost_service_order,
)
from apps.users.models import AccessMembership, PurchasedEntitlement

from . import services, signals
from .models import (
    EnergyActivityGrant, EnergyLedgerEntry, EnergyOperation, EnergyPolicy, EnergyPurchasedLot,
    EnergyRechargeProduct, EnergyRewardRule, EnergyToolCost, EnergyWallet,
)

D = Decimal
TOOL = 'letters-generate'
EXECUTORS = {TOOL: 'apps.energy.tests.fake_executor'}
T0 = datetime(2026, 3, 10, 9, 0, tzinfo=dt_timezone.utc)


def fake_executor(*args, **kwargs):
    return None


def make_user(username, plan=None, **extra):
    user = get_user_model().objects.create_user(username=username, password='Valid-pass-123', **extra)
    if plan:
        AccessMembership.objects.update_or_create(user=user, defaults={'plan': plan})
    return user


def configure_policy(plan='free', capacity='100', recovery_amount=None, interval=None, **extra):
    EnergyPolicy.objects.filter(plan=plan).update(
        enabled=True, capacity=D(capacity), recovery_amount=recovery_amount and D(recovery_amount),
        recovery_interval_minutes=interval, **extra,
    )


def configure_rule(source=EnergyRewardRule.Source.COURSE_TOPIC_COMPLETED, amount='4', **extra):
    EnergyRewardRule.objects.filter(source=source).update(enabled=True, amount=D(amount), **extra)
    return EnergyRewardRule.objects.get(source=source)


def configure_tool(cost='3', **extra):
    values = {
        'enabled': True, 'paused': False, 'cost': D(cost), 'provider_budget': D('100'), 'budget_period_days': 30,
        'max_duration_seconds': 60,
    }
    values.update(extra)
    EnergyToolCost.objects.filter(code=TOOL).update(**values)
    return EnergyToolCost.objects.get(code=TOOL)


def wallet(user):
    return EnergyWallet.objects.get(user=user)


class SeedConfigurationTests(TestCase):
    def test_seeded_configuration_is_disabled_and_has_no_invented_values(self):
        self.assertEqual(
            {p.plan: p.activity_daily_percent for p in EnergyPolicy.objects.all()},
            {'free': D('10'), 'plus': D('15'), 'pro': D('25')},
        )
        self.assertFalse(EnergyPolicy.objects.filter(enabled=True).exists())
        self.assertFalse(EnergyPolicy.objects.filter(capacity__isnull=False).exists())
        self.assertFalse(EnergyRewardRule.objects.filter(enabled=True).exists())
        self.assertFalse(EnergyRewardRule.objects.filter(amount__isnull=False).exists())
        self.assertFalse(EnergyToolCost.objects.filter(enabled=True).exists())
        self.assertFalse(EnergyToolCost.objects.filter(cost__isnull=False).exists())

    def test_unconfigured_policy_reports_unconfigured_never_zero_energy(self):
        user = make_user('seed-user')
        status = services.get_status(user)
        self.assertTrue(status['ok'])
        self.assertFalse(status['configured'])
        self.assertIsNone(status['recoverable']['percent'])
        self.assertEqual(status['level'], 'unconfigured')


class ActivityRecoveryTests(TestCase):
    def setUp(self):
        for plan in ('free', 'plus', 'pro'):
            configure_policy(plan, capacity='100')
        self.rule = configure_rule(amount='4')

    def test_daily_limits_are_10_15_25_percent_of_capacity(self):
        for plan, limit in (('free', D('10')), ('plus', D('15')), ('pro', D('25'))):
            user = make_user(f'user-{plan}', plan=None if plan == 'free' else plan)
            status = services.get_status(user, now=T0)
            self.assertEqual(status['activity']['limit'], limit, plan)

    def test_grants_are_partial_at_limit_and_then_blocked_for_the_day(self):
        user = make_user('activity')
        results = [services.reward_activity(user, self.rule.source, f'topic:{i}', now=T0) for i in range(4)]
        self.assertEqual([g.granted for g in results], [D('4'), D('4'), D('2'), D('0')])
        self.assertEqual(
            [g.result for g in results],
            ['granted', 'granted', 'partial', 'daily_limit'],
        )
        self.assertEqual(wallet(user).recoverable_available, D('10'))
        status = services.get_status(user, now=T0)
        self.assertTrue(status['activity']['limit_reached'])
        self.assertIn('toda la energía disponible por actividad de hoy', status['message'])

    def test_same_event_never_grants_twice(self):
        user = make_user('idempotent')
        first = services.reward_activity(user, self.rule.source, 'topic:1', now=T0)
        again = services.reward_activity(user, self.rule.source, 'topic:1', now=T0 + timedelta(days=2))
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(EnergyActivityGrant.objects.filter(user=user).count(), 1)
        self.assertEqual(wallet(user).recoverable_available, D('4'))

    @override_settings(ENERGY_DAY_TIMEZONE='Europe/Madrid')
    def test_daily_limit_resets_at_midnight_in_configured_timezone(self):
        user = make_user('timezone')
        before_midnight = datetime(2026, 1, 1, 22, 30, tzinfo=dt_timezone.utc)  # 23:30 Madrid
        after_midnight = datetime(2026, 1, 1, 23, 30, tzinfo=dt_timezone.utc)  # 00:30 Madrid (día 2)
        for i in range(3):
            services.reward_activity(user, self.rule.source, f'topic:{i}', now=before_midnight)
        late = services.reward_activity(user, self.rule.source, 'topic:late', now=after_midnight)
        self.assertEqual(late.granted, D('4'))
        self.assertEqual(late.day.isoformat(), '2026-01-02')

    def test_activity_never_exceeds_capacity(self):
        configure_policy('free', capacity='100')
        user = make_user('near-full')
        services.refresh_recovery(user, now=T0)
        admin = make_user('admin-near', is_superuser=True, is_staff=True)
        services.adjust(actor=admin, user=user, amount='98', reason='Prueba', key=uuid.uuid4())
        grant = services.reward_activity(user, self.rule.source, 'topic:x', now=T0)
        self.assertEqual(grant.granted, D('2'))
        self.assertEqual(wallet(user).recoverable_available, D('100'))

    def test_disabled_rule_or_unconfigured_policy_grants_nothing(self):
        EnergyRewardRule.objects.filter(pk=self.rule.pk).update(enabled=False)
        user = make_user('disabled-rule')
        self.assertIsNone(services.reward_activity(user, self.rule.source, 'topic:1', now=T0))
        configure_rule(amount='4')
        EnergyPolicy.objects.filter(plan='free').update(enabled=False)
        grant = services.reward_activity(user, self.rule.source, 'topic:2', now=T0)
        self.assertEqual(grant.granted, D('0'))
        self.assertEqual(grant.result, 'unavailable')

    def test_verified_progress_signal_rewards_once_after_commit(self):
        user = make_user('signal')
        instance = SimpleNamespace(completado=True, usuario_id=user.pk, tema_id=77)
        with self.captureOnCommitCallbacks(execute=True):
            signals.topic_completed(sender=None, instance=instance)
            signals.topic_completed(sender=None, instance=instance)
        self.assertEqual(EnergyActivityGrant.objects.get(user=user).event_key, 'topic:77')
        self.assertEqual(wallet(user).recoverable_available, D('4'))

    def test_incomplete_progress_does_not_emit(self):
        user = make_user('signal-incomplete')
        with self.captureOnCommitCallbacks(execute=True):
            signals.topic_completed(sender=None, instance=SimpleNamespace(completado=False, usuario_id=user.pk, tema_id=1))
        self.assertFalse(EnergyActivityGrant.objects.exists())


class PeriodicRecoveryTests(TestCase):
    def setUp(self):
        configure_policy('free', capacity='10', recovery_amount='5', interval=60)
        self.user = make_user('periodic')
        self.admin = make_user('periodic-admin', is_superuser=True, is_staff=True)

    def test_recovers_by_whole_intervals_and_is_not_duplicated_by_repeated_refresh(self):
        services.refresh_recovery(self.user, now=T0)
        services.refresh_recovery(self.user, now=T0 + timedelta(minutes=90))
        services.refresh_recovery(self.user, now=T0 + timedelta(minutes=90))
        self.assertEqual(wallet(self.user).recoverable_available, D('5'))
        services.refresh_recovery(self.user, now=T0 + timedelta(minutes=120))
        self.assertEqual(wallet(self.user).recoverable_available, D('10'))
        self.assertEqual(EnergyLedgerEntry.objects.filter(user=self.user, kind='recovery').count(), 2)

    def test_no_backlog_is_accumulated_while_full(self):
        services.refresh_recovery(self.user, now=T0)
        services.refresh_recovery(self.user, now=T0 + timedelta(hours=2))
        services.refresh_recovery(self.user, now=T0 + timedelta(hours=12))
        services.adjust(actor=self.admin, user=self.user, amount='-5', reason='Consumo simulado', key=uuid.uuid4())
        services.refresh_recovery(self.user, now=T0 + timedelta(hours=12, minutes=30))
        self.assertEqual(wallet(self.user).recoverable_available, D('5'))
        services.refresh_recovery(self.user, now=T0 + timedelta(hours=13))
        self.assertEqual(wallet(self.user).recoverable_available, D('10'))

    def test_status_shows_recharging_only_while_recovering(self):
        services.refresh_recovery(self.user, now=T0)
        status = services.get_status(self.user, now=T0 + timedelta(minutes=1))
        self.assertTrue(status['recharging'])
        self.assertEqual(status['recharging_source'], 'periodic')
        full = services.get_status(self.user, now=T0 + timedelta(hours=3))
        self.assertTrue(full['full'])
        self.assertFalse(full['recharging'])
        self.assertEqual(full['message'], services.MSG_FULL)

    def test_downgrade_keeps_surplus_without_recovering_above_new_capacity(self):
        AccessMembership.objects.update_or_create(user=self.user, defaults={'plan': 'pro'})
        configure_policy('pro', capacity='100')
        services.adjust(actor=self.admin, user=self.user, amount='80', reason='Saldo PRO', key=uuid.uuid4())
        AccessMembership.objects.filter(user=self.user).update(plan='free')
        status = services.get_status(self.user, now=T0 + timedelta(hours=5))
        self.assertEqual(status['recoverable']['percent'], D('100'))
        self.assertEqual(status['recoverable']['surplus'], D('70'))
        self.assertFalse(status['recharging'])
        self.assertEqual(wallet(self.user).recoverable_available, D('80'))


@override_settings(ENERGY_TOOL_EXECUTORS=EXECUTORS)
class ToolOperationTests(TestCase):
    def setUp(self):
        configure_policy('free', capacity='10')
        self.tool = configure_tool(cost='3')
        self.user = make_user('tools')
        self.admin = make_user('tools-admin', is_superuser=True, is_staff=True)
        services.adjust(actor=self.admin, user=self.user, amount='5', reason='Saldo inicial', key=uuid.uuid4())

    def test_reserve_then_confirm_consumes_once_and_ledger_reconciles(self):
        key = uuid.uuid4()
        operation = services.reserve(self.user, TOOL, idempotency_key=key, expected_cost='3')
        self.assertEqual(wallet(self.user).recoverable_available, D('2'))
        self.assertEqual(wallet(self.user).recoverable_reserved, D('3'))
        status = services.get_status(self.user)
        self.assertEqual(status['total_available'], D('2'))
        services.confirm(operation.pk)
        services.confirm(operation.pk)
        self.assertEqual(wallet(self.user).recoverable_reserved, D('0'))
        self.assertEqual(wallet(self.user).recoverable_available, D('2'))
        self.assertEqual(services.reconcile_wallet(self.user), [])
        replay = services.reserve(self.user, TOOL, idempotency_key=key)
        self.assertEqual(replay.pk, operation.pk)
        self.assertEqual(EnergyLedgerEntry.objects.filter(user=self.user, kind='consume').count(), 1)

    def test_release_returns_energy_without_exceeding_capacity(self):
        operation = services.reserve(self.user, TOOL, idempotency_key=uuid.uuid4())
        services.release(operation.pk, 'Fallo del proveedor')
        services.release(operation.pk, 'Fallo del proveedor')
        self.assertEqual(wallet(self.user).recoverable_available, D('5'))
        self.assertEqual(wallet(self.user).recoverable_reserved, D('0'))
        self.assertEqual(services.reconcile_wallet(self.user), [])

    def test_insufficient_energy_creates_no_operation_or_negative_balance(self):
        EnergyToolCost.objects.filter(pk=self.tool.pk).update(cost=D('6'))
        with self.assertRaises(services.InsufficientEnergy):
            services.reserve(self.user, TOOL, idempotency_key=uuid.uuid4())
        self.assertFalse(EnergyOperation.objects.exists())
        self.assertEqual(wallet(self.user).recoverable_available, D('5'))

    def test_concurrency_limit_defaults_to_one(self):
        services.reserve(self.user, TOOL, idempotency_key=uuid.uuid4())
        with self.assertRaises(services.ConcurrencyLimit):
            services.reserve(self.user, TOOL, idempotency_key=uuid.uuid4())

    def test_changed_cost_requires_new_acceptance(self):
        with self.assertRaises(services.CostChanged):
            services.reserve(self.user, TOOL, idempotency_key=uuid.uuid4(), expected_cost='2')
        self.assertFalse(EnergyOperation.objects.exists())

    def test_run_tool_releases_on_exception_and_unusable_result(self):
        def boom(operation):
            raise RuntimeError('proveedor caído')

        with self.assertRaises(RuntimeError):
            services.run_tool(self.user, TOOL, idempotency_key=uuid.uuid4(), execute=boom)
        operation, _ = services.run_tool(
            self.user, TOOL, idempotency_key=uuid.uuid4(),
            execute=lambda op: services.ToolResult(usable=False),
        )
        self.assertEqual(operation.status, EnergyOperation.Status.RELEASED)
        self.assertEqual(wallet(self.user).recoverable_available, D('5'))

    def test_run_tool_confirms_usable_result_and_retry_does_not_charge_again(self):
        key = uuid.uuid4()
        calls = []

        def ok(operation):
            calls.append(operation.pk)
            return services.ToolResult(usable=True, payload='texto', provider={'provider': 'fake'})

        operation, result = services.run_tool(self.user, TOOL, idempotency_key=key, execute=ok)
        self.assertEqual(operation.status, EnergyOperation.Status.CONFIRMED)
        self.assertEqual(result.payload, 'texto')
        again, _ = services.run_tool(self.user, TOOL, idempotency_key=key, execute=ok)
        self.assertEqual(again.pk, operation.pk)
        self.assertEqual(len(calls), 1)
        self.assertEqual(wallet(self.user).recoverable_available, D('2'))

    def test_late_result_after_release_goes_to_review_without_charge(self):
        operation = services.reserve(self.user, TOOL, idempotency_key=uuid.uuid4())
        services.release(operation.pk, 'Tiempo agotado')
        late = services.confirm(operation.pk)
        self.assertEqual(late.status, EnergyOperation.Status.REVIEW)
        self.assertEqual(wallet(self.user).recoverable_available, D('5'))

    def test_reconcile_releases_interrupted_operations(self):
        operation = services.reserve(self.user, TOOL, idempotency_key=uuid.uuid4())
        released = services.reconcile_operations(now=timezone.now() + timedelta(minutes=5))
        self.assertEqual([op.pk for op in released], [operation.pk])
        self.assertEqual(wallet(self.user).recoverable_available, D('5'))

    def test_purchased_lots_used_after_recoverable_earliest_expiry_first(self):
        far = EnergyPurchasedLot.objects.create(
            wallet=wallet(self.user), quantity=D('5'), available=D('5'), idempotency_key='lot-far',
            expires_at=timezone.now() + timedelta(days=30),
        )
        soon = EnergyPurchasedLot.objects.create(
            wallet=wallet(self.user), quantity=D('5'), available=D('5'), idempotency_key='lot-soon',
            expires_at=timezone.now() + timedelta(days=1),
        )
        EnergyToolCost.objects.filter(pk=self.tool.pk).update(cost=D('7'))
        operation = services.reserve(self.user, TOOL, idempotency_key=uuid.uuid4())
        allocation = {r.lot_id: r.amount for r in operation.reservations.all()}
        self.assertEqual(allocation, {None: D('5'), soon.pk: D('2')})
        far.refresh_from_db()
        self.assertEqual(far.available, D('5'))

    def test_covering_entitlement_runs_without_consuming_energy(self):
        EnergyToolCost.objects.filter(pk=self.tool.pk).update(service_entitlement_code='carta-revisada')
        PurchasedEntitlement.objects.create(
            user=self.user, code='carta-revisada', source='billing_order', source_reference='x',
            idempotency_key='ent-1',
        )
        estimate = services.estimate(self.user, TOOL)
        self.assertTrue(estimate['covered_by_service'])
        operation = services.reserve(self.user, TOOL, idempotency_key=uuid.uuid4())
        self.assertEqual(operation.amount, D('0'))
        self.assertEqual(services.confirm(operation.pk).status, EnergyOperation.Status.COVERED)
        self.assertEqual(wallet(self.user).recoverable_available, D('5'))

    def test_tool_states_block_without_reserving(self):
        cases = [
            ({'enabled': False}, 'tool_disabled'),
            ({'paused': True}, 'tool_paused'),
            ({'provider_budget': None}, 'budget_not_configured'),
        ]
        for changes, reason in cases:
            configure_tool(cost='3', **changes)
            with self.assertRaises(services.EnergyUnavailable) as raised:
                services.reserve(self.user, TOOL, idempotency_key=uuid.uuid4())
            self.assertEqual(raised.exception.code, reason)
        self.assertFalse(EnergyOperation.objects.exists())

    def test_budget_exhausted_pauses_tool(self):
        configure_tool(cost='3', provider_budget=D('0.01'))
        operation = services.reserve(self.user, TOOL, idempotency_key=uuid.uuid4())
        services.confirm(operation.pk, provider={'provider_cost_confirmed': D('0.02')})
        self.assertEqual(services.estimate(self.user, TOOL)['reason'], 'budget_exhausted')

    @override_settings(ENERGY_TOOL_EXECUTORS={})
    def test_without_provider_tool_is_unavailable(self):
        self.assertEqual(services.estimate(self.user, TOOL)['reason'], 'provider_unavailable')
        with self.assertRaises(services.EnergyUnavailable):
            services.reserve(self.user, TOOL, idempotency_key=uuid.uuid4())

    def test_adjust_requires_permission_reason_and_never_goes_negative(self):
        with self.assertRaises(services.EnergyError):
            services.adjust(actor=self.user, user=self.user, amount='1', reason='x', key=uuid.uuid4())
        with self.assertRaises(services.EnergyError):
            services.adjust(actor=self.admin, user=self.user, amount='1', reason=' ', key=uuid.uuid4())
        with self.assertRaises(services.EnergyError):
            services.adjust(actor=self.admin, user=self.user, amount='-6', reason='Error', key=uuid.uuid4())
        key = uuid.uuid4()
        services.adjust(actor=self.admin, user=self.user, amount='1', reason='Compensación', key=key)
        services.adjust(actor=self.admin, user=self.user, amount='1', reason='Compensación', key=key)
        self.assertEqual(wallet(self.user).recoverable_available, D('6'))


class PurchaseTests(TestCase):
    def setUp(self):
        self.user = make_user('buyer')
        self.admin = make_user('billing-admin', is_superuser=True, is_staff=True)
        self.product = ServiceProduct.objects.create(
            code='energy-pack', name='Recarga de energía', is_active=True, base_price=D('5.00'),
            fulfillment=ServiceProduct.Fulfillment.AUTOMATIC,
        )
        EnergyRechargeProduct.objects.create(service=self.product, energy_units=D('50'), enabled=True)

    def pending_order(self, key='order-1'):
        order = BillingOrder.objects.create(
            user=self.user, kind=BillingOrder.Kind.SERVICE, status=BillingOrder.Status.PENDING,
            subtotal=D('5.00'), total=D('5.00'), idempotency_key=key,
        )
        line = BillingOrderLine.objects.create(
            order=order, product_code='energy-pack', description='Recarga de energía', unit_price=D('5.00'),
            line_total=D('5.00'), product_snapshot={'energy_units': '50'},
        )
        charge = Charge.objects.create(
            user=self.user, order=order, concept='Recarga', currency='EUR', subtotal=D('5.00'), total=D('5.00'),
            status=Charge.Status.PENDING, idempotency_key=f'charge-{key}',
        )
        attempt = PaymentAttempt.objects.create(
            charge=charge, provider='stripe', method='card', external_reference=f'ref-{key}', amount=D('5.00'),
            currency='EUR', status=PaymentAttempt.Status.PENDING, idempotency_key=f'attempt-{key}',
        )
        return order, line, attempt

    def pay(self, attempt, key='order-1'):
        return confirm_verified_payment(
            attempt_id=attempt.pk, provider_event_key=f'event-{key}', external_reference=f'ref-{key}',
            amount=D('5.00'), currency='EUR',
        )

    def test_pending_order_is_not_credited(self):
        order, line, _attempt = self.pending_order()
        self.assertIsNone(services.credit_purchase(order, line))
        self.assertFalse(EnergyPurchasedLot.objects.exists())

    def test_confirmed_payment_credits_lot_once(self):
        order, line, attempt = self.pending_order()
        self.pay(attempt)
        self.pay(attempt)
        order.refresh_from_db()
        services.credit_purchase(order, line)
        lot = EnergyPurchasedLot.objects.get()
        self.assertEqual(lot.quantity, D('50'))
        self.assertEqual(EnergyLedgerEntry.objects.filter(kind='purchase').count(), 1)
        self.assertFalse(PurchasedEntitlement.objects.filter(user=self.user).exists())
        self.assertEqual(services.get_status(self.user)['purchased']['available'], D('50'))
        self.assertEqual(services.reconcile_wallet(self.user), [])

    def test_zero_cost_energy_recharge_is_blocked(self):
        ServiceProduct.objects.filter(pk=self.product.pk).update(base_price=D('0.00'))
        with self.assertRaises(BillingUnavailable):
            create_zero_cost_service_order(
                user=self.user, service_code='energy-pack', idempotency_key='zero-energy',
                accepted_terms={'accepted': True, 'version': TERMS_VERSION, 'accepted_at': timezone.now().isoformat()},
            )
        self.assertFalse(EnergyPurchasedLot.objects.exists())

    def refund(self, order):
        payment = order.charges.get().payments.get()
        return Refund.objects.create(
            payment=payment, amount=D('5.00'), currency='EUR', reason='Devolución', status=Refund.Status.CONFIRMED,
            idempotency_key=f'refund-{order.pk}', created_by=self.admin,
        )

    def test_refund_requires_confirmed_refund_and_removes_unused_energy(self):
        order, _line, attempt = self.pending_order()
        self.pay(attempt)
        lot = EnergyPurchasedLot.objects.get()
        with self.assertRaises(services.EnergyError):
            services.revert_lot_for_refund(lot.pk, actor=self.admin, reason='Sin devolución')
        self.refund(order)
        reverted = services.revert_lot_for_refund(lot.pk, actor=self.admin, reason='Cliente')
        self.assertEqual(reverted.status, EnergyPurchasedLot.Status.REFUNDED)
        self.assertEqual(reverted.available, D('0'))
        self.assertEqual(services.reconcile_wallet(self.user), [])

    @override_settings(ENERGY_TOOL_EXECUTORS=EXECUTORS)
    def test_partially_consumed_lot_goes_to_review_without_negative_balance(self):
        configure_policy('free', capacity='10')
        configure_tool(cost='3')
        order, _line, attempt = self.pending_order()
        self.pay(attempt)
        operation = services.reserve(self.user, TOOL, idempotency_key=uuid.uuid4())
        services.confirm(operation.pk)
        self.refund(order)
        lot = services.revert_lot_for_refund(EnergyPurchasedLot.objects.get().pk, actor=self.admin, reason='Cliente')
        self.assertEqual(lot.status, EnergyPurchasedLot.Status.REVIEW)
        self.assertEqual(lot.consumed, D('3'))
        self.assertEqual(lot.available, D('0'))
        self.assertEqual(services.reconcile_wallet(self.user), [])


class EnergyViewTests(TestCase):
    def setUp(self):
        self.user = make_user('viewer')

    def test_status_requires_login_and_is_private(self):
        response = self.client.get(reverse('energy:status'))
        self.assertEqual(response.status_code, 401)
        self.client.force_login(self.user)
        response = self.client.get(reverse('energy:status'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertFalse(response.json()['configured'])

    def test_status_payload_for_configured_policy(self):
        configure_policy('free', capacity='10')
        admin = make_user('view-admin', is_superuser=True, is_staff=True)
        services.adjust(actor=admin, user=self.user, amount='6', reason='Saldo', key=uuid.uuid4())
        self.client.force_login(self.user)
        data = self.client.get(reverse('energy:status')).json()
        self.assertEqual(data['recoverable']['available'], '6.000')
        self.assertEqual(data['recoverable']['percent'], '60.000')
        self.assertEqual(data['level'], 'high')

    def test_estimate_reports_unknown_cost_for_unconfigured_tool(self):
        self.client.force_login(self.user)
        data = self.client.get(reverse('energy:estimate'), {'tool': TOOL}).json()
        self.assertIsNone(data['energy_cost'])
        self.assertFalse(data['available'])
        self.assertEqual(data['reason'], 'tool_disabled')

    def test_detail_page_for_visitor_and_member_with_header_widget(self):
        response = self.client.get(reverse('energy:detail'))
        self.assertContains(response, 'Regístrate para obtener tu energía')
        self.client.force_login(self.user)
        response = self.client.get(reverse('energy:detail'))
        self.assertContains(response, 'Historial de movimientos')
        self.assertContains(response, 'id="energyWidget"')
        self.assertContains(response, 'role="progressbar"')

    def test_admin_adjust_requires_permission(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('energy:admin_adjust')).status_code, 403)

    def test_admin_adjust_records_compensating_entry(self):
        admin = make_user('adjust-admin', is_superuser=True, is_staff=True)
        self.client.force_login(admin)
        response = self.client.post(reverse('energy:admin_adjust'), {
            'username': 'viewer', 'amount': '3', 'reason': 'Incidencia', 'key': str(uuid.uuid4()),
        })
        self.assertEqual(response.status_code, 302)
        entry = EnergyLedgerEntry.objects.get(user=self.user)
        self.assertEqual(entry.kind, 'adjust')
        self.assertEqual(entry.actor, admin)


class ModuleGateTests(TestCase):
    def test_letters_ats_and_nexus_expose_real_energy_reason_and_unknown_cost(self):
        from apps.curriculum.services import ats
        from apps.nexus import versioned_services

        user = make_user('modules')
        for gate in (
            services.module_gate(user, 'letters-generate'),
            ats.ai_availability(user, 'rephrase'),
            versioned_services.ai_availability(user, 'plan'),
        ):
            self.assertFalse(gate['available'])
            self.assertEqual(gate['reason'], 'tool_disabled')
            self.assertIsNone(gate['energy_cost'])

    @override_settings(ENERGY_TOOL_EXECUTORS=EXECUTORS)
    def test_module_never_claims_execution_without_module_executor(self):
        configure_policy('free', capacity='10')
        configure_tool(cost='3')
        user = make_user('modules-ok')
        admin = make_user('modules-admin', is_superuser=True, is_staff=True)
        services.adjust(actor=admin, user=user, amount='5', reason='Saldo', key=uuid.uuid4())
        gate = services.module_gate(user, TOOL)
        self.assertFalse(gate['available'])
        self.assertEqual(gate['reason'], 'module_execution_pending')
        self.assertEqual(gate['energy_cost'], D('3'))
        self.assertFalse(EnergyOperation.objects.exists())
