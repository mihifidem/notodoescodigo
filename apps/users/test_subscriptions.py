import copy
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone as datetime_timezone
from decimal import Decimal
from unittest.mock import patch

import stripe
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .access import effective_plan
from .billing_models import (
    BillingConfiguration, BillingOrder, BillingSubscription, Charge,
    ConfirmedPayment, MembershipContract, PaymentAttempt, PaymentProviderConfiguration,
)
from .billing_services import BillingUnavailable, TERMS_VERSION
from .billing_views import quote_token
from . import stripe_billing as service


@override_settings(
    STRIPE_SECRET_KEY='sk_test_subscription_fixture',
    STRIPE_BILLING_WEBHOOK_SECRET='whsec_subscription_fixture',
    BILLING_PUBLIC_BASE_URL='https://portal.example.test',
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
)
class SubscriptionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='subscriber', password='test-password')
        cls.other = get_user_model().objects.create_user(username='other-subscriber')
        cls.prices = BillingConfiguration.objects.create(
            version=1, status='active', approved_by=cls.user, approved_at=timezone.now(),
            plus_monthly='10.00', plus_semiannual='50.00', plus_annual='90.00',
            pro_monthly='20.00', pro_semiannual='100.00', pro_annual='180.00',
        )
        cls.provider = PaymentProviderConfiguration.objects.create(
            provider='stripe', enabled=True, supports_recurring=True,
            methods=['card'], currencies=['EUR'], environment='sandbox',
        )

    def setUp(self):
        self.session_create = self.patch('stripe.checkout.Session.create')
        self.session_create.return_value = {'id': 'cs_fixture', 'url': 'https://checkout.stripe.com/c/pay/fixture'}
        self.remote_get = self.patch('stripe.Subscription.retrieve')
        self.invoice_get = self.patch('stripe.Invoice.retrieve')
        self.remote_cancel = self.patch('stripe.Subscription.cancel')
        self.remote_modify = self.patch('stripe.Subscription.modify')
        self.session_get = self.patch('stripe.checkout.Session.retrieve')
        self.session_expire = self.patch('stripe.checkout.Session.expire')

    def patch(self, target):
        patcher = patch(target)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def prepare(self, plan='plus', period='monthly', key='subscription-fixture', user=None):
        return service.prepare_subscription(
            user=user or self.user, plan=plan, period=period,
            expected_price=getattr(self.prices, f'{plan}_{period}'), idempotency_key=key,
            accepted_terms={'accepted': True, 'version': TERMS_VERSION, 'accepted_at': timezone.now().isoformat()},
        )

    def event(self, subscription, invoice_id='in_first', start=None, end=None):
        order = subscription.initial_order
        start = start or datetime(2026, 1, 1, tzinfo=datetime_timezone.utc)
        end = end or service._period_end(start, order.billing_period)
        remote = {
            'id': f'sub_{subscription.pk}', 'status': 'active', 'cancel_at_period_end': False,
            'billing_cycle_anchor': int(start.timestamp()),
            'metadata': {'billing_subscription': str(subscription.pk), 'billing_order': str(order.pk)},
        }
        invoice = {
            'id': invoice_id, 'status': 'paid', 'currency': 'eur', 'livemode': False,
            'parent': {'subscription_details': {'subscription': remote['id']}},
            'total': int(order.total * 100), 'amount_paid': int(order.total * 100),
            'billing_reason': 'subscription_create' if invoice_id == 'in_first' else 'subscription_cycle',
            'lines': {'has_more': False, 'data': [{
                'amount': int(order.total * 100), 'quantity': 1,
                'period': {'start': int(start.timestamp()), 'end': int(end.timestamp())},
            }]},
        }
        self.remote_get.return_value = remote
        self.invoice_get.return_value = invoice
        return {'id': f'evt_{invoice_id}', 'type': 'invoice.payment_succeeded', 'livemode': False,
                'data': {'object': invoice}}

    def activate(self, subscription):
        event = self.event(subscription)
        service.process_event(event)
        subscription.refresh_from_db()
        return event

    def test_payment_lock_does_not_join_nullable_replacement(self):
        subscription = self.prepare()
        event = self.event(subscription)
        with CaptureQueriesContext(connection) as queries:
            service.process_event(event)
        subscription_reads = [
            query['sql'] for query in queries.captured_queries
            if query['sql'].startswith('SELECT')
            and 'FROM "users_billingsubscription"' in query['sql']
        ]
        self.assertTrue(subscription_reads)
        for query in subscription_reads:
            self.assertNotIn('LEFT OUTER JOIN', query)
        subscription.refresh_from_db()
        self.assertEqual(subscription.status, 'active')

    def test_provider_configuration_fails_closed(self):
        self.provider.full_clean()
        with override_settings(STRIPE_SECRET_KEY='sk_live_wrong_environment'):
            with self.assertRaises(ValidationError):
                self.provider.full_clean()
            with self.assertRaises(BillingUnavailable):
                self.prepare()
        with override_settings(STRIPE_BILLING_WEBHOOK_SECRET=''):
            with self.assertRaises(BillingUnavailable):
                self.prepare()
        with override_settings(BILLING_PUBLIC_BASE_URL='https://portal.example.test@evil.test/?x=1'):
            with self.assertRaises(ValidationError):
                self.provider.full_clean()
        self.assertFalse(BillingOrder.objects.exists())

    def test_checkout_is_idempotent_and_never_activates_plan(self):
        subscription = self.prepare()
        self.session_create.return_value = stripe.checkout.Session.construct_from(
            self.session_create.return_value, 'sk_test_fixture',
        )
        self.assertEqual(self.prepare().pk, subscription.pk)
        self.assertEqual(service.checkout(subscription), 'https://checkout.stripe.com/c/pay/fixture')
        self.assertEqual(service.checkout(subscription), 'https://checkout.stripe.com/c/pay/fixture')
        self.session_create.assert_called_once()
        parameters = self.session_create.call_args.kwargs
        self.assertEqual(parameters['mode'], 'subscription')
        self.assertEqual(parameters['line_items'][0]['price_data']['unit_amount'], 1000)
        self.assertEqual(effective_plan(self.user), 'free')
        self.assertFalse(MembershipContract.objects.exists())
        self.assertFalse(ConfirmedPayment.objects.exists())

    def test_each_billing_period_has_correct_stripe_interval_and_total(self):
        for period, months, total in [('monthly', 1, 1000), ('semiannual', 6, 5000), ('annual', 12, 9000)]:
            with self.subTest(period=period):
                user = get_user_model().objects.create_user(username=f'period-{period}')
                subscription = self.prepare(period=period, key=period, user=user)
                service.checkout(subscription)
                price = self.session_create.call_args.kwargs['line_items'][0]['price_data']
                self.assertEqual(price['recurring'], {'interval': 'month', 'interval_count': months})
                self.assertEqual(price['unit_amount'], total)

    def test_webhook_success_activates_plan_and_duplicate_events_do_not_extend_it(self):
        subscription = self.prepare()
        event = self.activate(subscription)
        self.assertEqual(effective_plan(self.user), 'free')  # Fixture's paid period has already expired.
        contract = MembershipContract.objects.get()
        self.assertEqual(contract.starts_at, datetime(2026, 1, 1, tzinfo=datetime_timezone.utc))
        self.assertEqual(contract.period_ends_at, datetime(2026, 2, 1, tzinfo=datetime_timezone.utc))
        self.assertTrue(contract.auto_renew)
        event['id'] = 'evt_duplicate_different_delivery'
        service.process_event(event)
        self.assertEqual(ConfirmedPayment.objects.count(), 1)
        self.assertEqual(MembershipContract.objects.count(), 1)

    def test_current_paid_period_upgrades_user(self):
        subscription = self.prepare()
        start = timezone.now().replace(microsecond=0)
        event = self.event(subscription, start=start)
        self.remote_get.return_value = stripe.Subscription.construct_from(self.remote_get.return_value, 'sk_test_fixture')
        self.invoice_get.return_value = stripe.Invoice.construct_from(self.invoice_get.return_value, 'sk_test_fixture')
        service.process_event(event)
        self.assertEqual(effective_plan(self.user), 'plus')
        self.assertEqual(BillingOrder.objects.get().status, 'paid')

    def test_renewals_use_provider_period_not_webhook_delivery_time(self):
        subscription = self.prepare()
        self.activate(subscription)
        service.process_event(self.event(
            subscription, 'in_renewal', start=datetime(2026, 2, 1, tzinfo=datetime_timezone.utc),
            end=datetime(2026, 3, 1, tzinfo=datetime_timezone.utc),
        ))
        self.assertEqual(ConfirmedPayment.objects.count(), 2)
        self.assertEqual(BillingOrder.objects.count(), 2)
        self.assertEqual(MembershipContract.objects.latest('pk').period_ends_at,
                         datetime(2026, 3, 1, tzinfo=datetime_timezone.utc))

    def test_renewal_preserves_month_end_anchor(self):
        subscription = self.prepare()
        start = datetime(2026, 1, 31, tzinfo=datetime_timezone.utc)
        self.activate_at(subscription, start)
        event = self.event(
            subscription, 'in_february', start=datetime(2026, 2, 28, tzinfo=datetime_timezone.utc),
            end=datetime(2026, 3, 31, tzinfo=datetime_timezone.utc),
        )
        self.remote_get.return_value['billing_cycle_anchor'] = int(start.timestamp())
        service.process_event(event)
        self.assertEqual(MembershipContract.objects.latest('pk').period_ends_at.day, 31)

    def activate_at(self, subscription, start):
        service.process_event(self.event(subscription, start=start))
        subscription.refresh_from_db()

    def test_invalid_invoice_does_not_grant_any_access(self):
        subscription = self.prepare()
        original = self.event(subscription)
        invoice = copy.deepcopy(self.invoice_get.return_value)
        for field, invalid in [('amount_paid', 1), ('currency', 'usd'), ('total', 1), ('status', 'open')]:
            with self.subTest(field=field):
                changed = copy.deepcopy(invoice)
                changed[field] = invalid
                self.invoice_get.return_value = changed
                with self.assertRaises(ValidationError):
                    service.process_event(original)
                self.assertFalse(ConfirmedPayment.objects.exists())
                self.assertFalse(MembershipContract.objects.exists())

    def test_failed_payment_and_old_failure_do_not_extend_or_revoke_paid_period(self):
        subscription = self.prepare()
        event = self.activate(subscription)
        event['type'] = 'invoice.payment_failed'
        self.remote_get.return_value['status'] = 'past_due'
        service.process_event(event)
        subscription.refresh_from_db()
        self.assertEqual(subscription.status, 'past_due')
        self.assertEqual(MembershipContract.objects.count(), 1)
        self.remote_get.return_value['status'] = 'active'
        service.process_event(event)
        self.assertEqual(MembershipContract.objects.count(), 1)

    def test_cancel_renewal_preserves_paid_access(self):
        subscription = self.prepare()
        self.activate_at(subscription, timezone.now().replace(microsecond=0))
        service.cancel_subscription(user=self.user, pk=subscription.pk)
        self.remote_modify.assert_called_once()
        subscription.refresh_from_db()
        self.assertTrue(subscription.cancel_at_period_end)
        self.assertEqual(effective_plan(self.user), 'plus')
        self.assertFalse(MembershipContract.objects.get().auto_renew)

    def renewal_fixture(self):
        subscription = self.prepare()
        self.activate_at(subscription, timezone.now().replace(microsecond=0))
        service.cancel_subscription(user=self.user, pk=subscription.pk)
        subscription.refresh_from_db()
        self.remote_get.return_value['cancel_at_period_end'] = True
        self.remote_modify.reset_mock()
        self.remote_modify.return_value = {
            **self.remote_get.return_value, 'cancel_at_period_end': False,
        }
        return subscription

    def test_reactivate_renewal_preserves_contract_period_and_never_charges(self):
        subscription = self.renewal_fixture()
        contract = MembershipContract.objects.get()
        original_period = contract.period_ends_at
        original_counts = (BillingOrder.objects.count(), ConfirmedPayment.objects.count())
        service.reactivate_renewal(user=self.user, pk=subscription.pk)
        self.remote_modify.assert_called_once()
        self.assertIs(self.remote_modify.call_args.kwargs['cancel_at_period_end'], False)
        subscription.refresh_from_db()
        contract.refresh_from_db()
        self.assertFalse(subscription.cancel_at_period_end)
        self.assertTrue(contract.auto_renew)
        self.assertFalse(contract.cancel_at_period_end)
        self.assertEqual(contract.period_ends_at, original_period)
        self.assertEqual((BillingOrder.objects.count(), ConfirmedPayment.objects.count()), original_counts)
        self.session_create.assert_not_called()
        self.assertEqual(effective_plan(self.user), 'plus')
        self.assertTrue(self.user.billing_events.filter(action='subscription_renewal_reactivated').exists())

    def test_cancel_reactivate_cancel_uses_distinct_stripe_keys(self):
        subscription = self.prepare()
        self.activate_at(subscription, timezone.now().replace(microsecond=0))
        service.cancel_subscription(user=self.user, pk=subscription.pk)
        first_key = self.remote_modify.call_args.kwargs['idempotency_key']
        self.remote_modify.return_value = {**self.remote_get.return_value, 'cancel_at_period_end': False}
        service.reactivate_renewal(user=self.user, pk=subscription.pk)
        service.cancel_subscription(user=self.user, pk=subscription.pk)
        self.assertNotEqual(first_key, self.remote_modify.call_args.kwargs['idempotency_key'])
        self.assertTrue(MembershipContract.objects.get().cancel_at_period_end)

    def test_reactivate_rejects_ineligible_local_and_remote_states(self):
        subscription = self.renewal_fixture()
        for changes in (
            {'status': 'canceled'}, {'status': 'pending'}, {'status': 'past_due'},
            {'period_ends_at': timezone.now()}, {'period_ends_at': None},
            {'stripe_id': ''}, {'cancel_at_period_end': False},
        ):
            original = {key: getattr(subscription, key) for key in changes}
            BillingSubscription.objects.filter(pk=subscription.pk).update(**changes)
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                service.reactivate_renewal(user=self.user, pk=subscription.pk)
            BillingSubscription.objects.filter(pk=subscription.pk).update(**original)
        for state in ('canceled', 'past_due', 'unpaid'):
            self.remote_get.return_value['status'] = state
            with self.subTest(remote=state), self.assertRaises(ValidationError):
                service.reactivate_renewal(user=self.user, pk=subscription.pk)
        self.remote_modify.assert_not_called()

    def test_reactivation_failure_does_not_report_local_success(self):
        subscription = self.renewal_fixture()
        self.remote_modify.side_effect = stripe.APIConnectionError('Fixture network failure')
        with self.assertRaises(stripe.APIConnectionError):
            service.reactivate_renewal(user=self.user, pk=subscription.pk)
        subscription.refresh_from_db()
        self.assertTrue(subscription.cancel_at_period_end)
        self.assertFalse(MembershipContract.objects.get().auto_renew)
        self.remote_modify.side_effect = None
        self.remote_modify.return_value['cancel_at_period_end'] = True
        with self.assertRaises(ValidationError):
            service.reactivate_renewal(user=self.user, pk=subscription.pk)
        self.assertFalse(MembershipContract.objects.get().auto_renew)

    def test_reactivate_endpoint_requires_owner_post_csrf_and_consent(self):
        subscription = self.renewal_fixture()
        url = reverse('users:subscription_reactivate', args=[subscription.pk])
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url, {'accept_renewal': 'yes'}).status_code, 404)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertRedirects(self.client.post(url), reverse('users:billing'))
        self.remote_modify.assert_not_called()
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        self.assertEqual(strict.post(url, {'accept_renewal': 'yes'}).status_code, 403)
        self.assertContains(self.client.get(reverse('users:billing')), 'Reactivar renovación automática')
        self.assertRedirects(self.client.post(url, {'accept_renewal': 'yes'}), reverse('users:billing'))
        self.assertNotContains(self.client.get(reverse('users:billing')), 'Reactivar renovación automática')

    def test_canceled_subscription_never_reactivated_by_delayed_event(self):
        subscription = self.prepare()
        self.activate(subscription)
        event = self.event(subscription, 'in_renewal', start=datetime(2026, 2, 1, tzinfo=datetime_timezone.utc))
        self.remote_get.return_value['status'] = 'canceled'
        event['type'] = 'customer.subscription.deleted'
        event['data']['object'] = self.remote_get.return_value
        service.process_event(event)
        event['type'] = 'invoice.payment_succeeded'
        event['data']['object'] = self.invoice_get.return_value
        service.process_event(event)
        subscription.refresh_from_db()
        self.assertEqual(subscription.status, 'canceled')

    def test_upgrade_cancels_previous_subscription_only_after_payment(self):
        previous = self.prepare()
        self.activate_at(previous, timezone.now().replace(microsecond=0))
        upgrade = self.prepare(plan='pro', key='upgrade-pro')
        service.checkout(upgrade)
        self.remote_cancel.assert_not_called()
        event = self.event(upgrade, 'in_upgrade', start=timezone.now().replace(microsecond=0))
        service.process_event(event)
        self.remote_cancel.assert_called_once()
        self.assertEqual(self.remote_cancel.call_args.args, (previous.stripe_id,))
        self.assertFalse(self.remote_cancel.call_args.kwargs['prorate'])
        self.assertEqual(effective_plan(self.user), 'pro')
        previous.refresh_from_db()
        self.assertEqual(previous.status, 'canceled')

    def test_quote_validation_owner_and_missing_consent(self):
        self.client.force_login(self.user)
        url = reverse('users:subscribe')
        token = quote_token(self.user, 'plus', 'monthly', '10.00')
        self.client.post(url, {'quote': token})
        self.assertFalse(BillingOrder.objects.exists())
        self.client.post(url, {'quote': 'tampered', 'accept_terms': 'yes'})
        self.assertFalse(BillingOrder.objects.exists())
        self.client.post(url, {'quote': quote_token(self.other, 'plus', 'monthly', '10.00'), 'accept_terms': 'yes'})
        self.assertFalse(BillingOrder.objects.exists())
        self.client.post(url, {'quote': quote_token(self.user, 'plus', 'monthly', '1.00'), 'accept_terms': 'yes'})
        self.assertFalse(BillingOrder.objects.exists())
        response = self.client.post(url, {'quote': token, 'accept_terms': 'yes'})
        self.assertEqual(response.url, 'https://checkout.stripe.com/c/pay/fixture')
        self.assertEqual(effective_plan(self.user), 'free')

    def test_browser_return_and_cross_user_actions_never_grant_access(self):
        subscription = self.prepare()
        self.client.force_login(self.user)
        response = self.client.get(reverse('users:subscription_return', args=[subscription.pk]))
        self.assertContains(response, 'esperando la confirmación')
        self.assertFalse(ConfirmedPayment.objects.exists())
        self.client.force_login(self.other)
        for name in ('subscription_resume', 'subscription_cancel'):
            self.assertEqual(self.client.post(reverse(f'users:{name}', args=[subscription.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('users:subscription_return', args=[subscription.pk])).status_code, 404)

    def test_checkout_api_failure_leaves_retryable_pending_order(self):
        subscription = self.prepare()
        self.session_create.side_effect = stripe.APIConnectionError('Fixture network failure')
        with self.assertRaises(stripe.APIConnectionError):
            service.checkout(subscription)
        self.assertEqual(BillingOrder.objects.get().status, 'pending')
        self.session_create.side_effect = None
        service.checkout(subscription)
        self.assertEqual(BillingOrder.objects.count(), 1)

    def test_webhook_rejects_unsigned_events_and_accepts_valid_signature(self):
        subscription = self.prepare()
        event = self.event(subscription)
        body = json.dumps(event).encode()
        client = Client(enforce_csrf_checks=True)
        url = reverse('users:stripe_billing_webhook')
        self.assertEqual(client.post(url, body, content_type='application/json').status_code, 400)
        timestamp = int(time.time())
        digest = hmac.new(
            b'whsec_subscription_fixture', str(timestamp).encode() + b'.' + body, hashlib.sha256,
        ).hexdigest()
        response = client.post(
            url, body, content_type='application/json', HTTP_STRIPE_SIGNATURE=f't={timestamp},v1={digest}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ConfirmedPayment.objects.count(), 1)

    def test_expired_checkout_releases_pending_order(self):
        subscription = self.prepare()
        service.checkout(subscription)
        service.process_event({
            'type': 'checkout.session.expired', 'livemode': False, 'data': {'object': {'id': 'cs_fixture'}},
        })
        subscription.refresh_from_db()
        self.assertEqual(subscription.status, 'canceled')
        self.assertEqual(subscription.initial_order.status, 'canceled')
        self.assertEqual(service.prepare_subscription(
            user=self.user, plan='pro', period='monthly', expected_price='20.00', idempotency_key='after-expiry',
            accepted_terms={'accepted': True, 'version': TERMS_VERSION, 'accepted_at': timezone.now().isoformat()},
        ).status, 'pending')

    def test_dashboard_exposes_checkout_only_when_configured_and_eligible(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('users:billing'))
        self.assertContains(response, 'Subir a PLUS y pagar con Stripe')
        with override_settings(STRIPE_BILLING_WEBHOOK_SECRET=''):
            response = self.client.get(reverse('users:billing'))
            self.assertNotContains(response, 'Subir a PLUS y pagar con Stripe')
            self.assertContains(response, 'checkout no está disponible')
