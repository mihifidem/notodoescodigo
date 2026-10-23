from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .access import effective_plan
from .billing_models import (
    AdministrativeAccessGrant,
    BillingConfiguration,
    BillingOrder,
    Charge,
    MembershipContract,
    PaymentAttempt,
    ServiceProduct,
)
from .billing_services import (
    BillingUnavailable,
    TERMS_VERSION,
    confirm_verified_payment,
    create_membership_order,
    create_zero_cost_service_order,
    membership_price,
    money,
)
from .models import AccessMembership, AccessRoleAssignment, PurchasedEntitlement


class BillingServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='billing-user',
            password='Valid-pass-123',
        )

    def accepted_terms(self):
        return {
            'accepted': True,
            'version': TERMS_VERSION,
            'accepted_at': timezone.now().isoformat(),
        }

    def active_configuration(self, **prices):
        return BillingConfiguration.objects.create(
            version=1,
            status=BillingConfiguration.Status.ACTIVE,
            approved_by=self.user,
            approved_at=timezone.now(),
            **prices,
        )

    def test_money_rounds_half_up_to_minor_units(self):
        self.assertEqual(money(Decimal('1.005')), Decimal('1.01'))

    def test_membership_prices_remain_unavailable_without_active_approved_configuration(self):
        self.assertEqual(
            membership_price(AccessMembership.Plan.PLUS, 'monthly'),
            (None, 'not_configured'),
        )

        self.active_configuration(plus_monthly=Decimal('0.00'))

        self.assertEqual(
            membership_price(AccessMembership.Plan.PLUS, 'monthly'),
            (Decimal('0.00'), 'configured'),
        )

    def test_billing_configuration_requires_draft_before_activation(self):
        config = BillingConfiguration(
            version=1,
            status=BillingConfiguration.Status.ACTIVE,
            approved_by=self.user,
            approved_at=timezone.now(),
        )

        with self.assertRaises(ValidationError):
            config.full_clean()

    def test_free_membership_order_is_idempotent_and_grants_plan(self):
        self.active_configuration(plus_monthly=Decimal('0.00'))
        terms = self.accepted_terms()
        arguments = {
            'user': self.user,
            'plan': AccessMembership.Plan.PLUS,
            'period': 'monthly',
            'idempotency_key': 'free-plus-monthly-001',
            'accepted_terms': terms,
        }

        first = create_membership_order(**arguments)
        duplicate = create_membership_order(**arguments)

        self.assertEqual(first.pk, duplicate.pk)
        self.assertEqual(first.status, BillingOrder.Status.FREE)
        self.assertEqual(first.total, Decimal('0.00'))
        self.assertEqual(BillingOrder.objects.filter(user=self.user).count(), 1)
        self.assertEqual(effective_plan(self.user), AccessMembership.Plan.PLUS)
        self.assertEqual(MembershipContract.objects.filter(user=self.user).count(), 1)

    def test_unavailable_paid_provider_creates_no_order_or_charge(self):
        self.active_configuration(plus_monthly=Decimal('4.90'))

        with self.assertRaises(BillingUnavailable):
            create_membership_order(
                user=self.user,
                plan=AccessMembership.Plan.PLUS,
                period='monthly',
                idempotency_key='paid-plus-monthly-001',
                accepted_terms=self.accepted_terms(),
                provider='stripe',
                method='card',
            )

        self.assertFalse(BillingOrder.objects.filter(user=self.user).exists())
        self.assertFalse(Charge.objects.filter(user=self.user).exists())

    def test_zero_cost_automatic_service_creates_owned_entitlement_once(self):
        product = ServiceProduct.objects.create(
            code='free-course-tool',
            name='Herramienta gratuita',
            is_active=True,
            base_price=Decimal('0.00'),
            fulfillment=ServiceProduct.Fulfillment.AUTOMATIC,
            entitlement_code='free-course-tool',
            quantity=1,
        )
        arguments = {
            'user': self.user,
            'service_code': product.code,
            'idempotency_key': 'free-service-001',
            'accepted_terms': self.accepted_terms(),
        }

        order = create_zero_cost_service_order(**arguments)
        replay = create_zero_cost_service_order(**arguments)

        self.assertEqual(order.pk, replay.pk)
        self.assertEqual(order.status, BillingOrder.Status.FREE)
        self.assertEqual(
            PurchasedEntitlement.objects.filter(user=self.user, code='free-course-tool').count(),
            1,
        )

    def test_paid_service_never_creates_order_without_provider(self):
        product = ServiceProduct.objects.create(
            code='paid-tool',
            name='Herramienta de pago',
            is_active=True,
            base_price=Decimal('10.00'),
            fulfillment=ServiceProduct.Fulfillment.AUTOMATIC,
            entitlement_code='paid-tool',
        )

        with self.assertRaises(BillingUnavailable):
            create_zero_cost_service_order(
                user=self.user,
                service_code=product.code,
                idempotency_key='paid-service-001',
                accepted_terms=self.accepted_terms(),
            )

        self.assertFalse(BillingOrder.objects.filter(user=self.user).exists())

    def test_verified_payment_callback_is_idempotent_and_activates_membership(self):
        order = BillingOrder.objects.create(
            user=self.user,
            kind=BillingOrder.Kind.MEMBERSHIP,
            status=BillingOrder.Status.PENDING,
            currency='EUR',
            subtotal=Decimal('4.90'),
            total=Decimal('4.90'),
            idempotency_key='pending-membership-001',
            membership_plan=AccessMembership.Plan.PLUS,
            billing_period='monthly',
        )
        charge = Charge.objects.create(
            user=self.user,
            order=order,
            concept='Membresía PLUS',
            currency='EUR',
            subtotal=Decimal('4.90'),
            total=Decimal('4.90'),
            status=Charge.Status.PENDING,
            idempotency_key='pending-charge-001',
        )
        attempt = PaymentAttempt.objects.create(
            charge=charge,
            provider='stripe',
            method='card',
            external_reference='provider-ref-001',
            amount=Decimal('4.90'),
            currency='EUR',
            status=PaymentAttempt.Status.PENDING,
            idempotency_key='attempt-001',
        )
        arguments = {
            'attempt_id': attempt.pk,
            'provider_event_key': 'provider-event-001',
            'external_reference': 'provider-ref-001',
            'amount': Decimal('4.90'),
            'currency': 'EUR',
        }

        payment = confirm_verified_payment(**arguments)
        replay = confirm_verified_payment(**arguments)

        self.assertEqual(payment.pk, replay.pk)
        self.assertEqual(Charge.objects.get(pk=charge.pk).status, Charge.Status.PAID)
        self.assertEqual(MembershipContract.objects.filter(user=self.user).count(), 1)
        self.assertEqual(effective_plan(self.user), AccessMembership.Plan.PLUS)

    def test_expired_contract_does_not_outlive_its_billing_period(self):
        MembershipContract.objects.create(
            user=self.user,
            plan=AccessMembership.Plan.PRO,
            billing_period='monthly',
            contracted_price=Decimal('10.00'),
            status=MembershipContract.Status.ACTIVE,
            starts_at=timezone.now() - timedelta(days=60),
            period_ends_at=timezone.now() - timedelta(days=30),
            idempotency_key='expired-pro-contract',
        )

        self.assertEqual(effective_plan(self.user), AccessMembership.Plan.FREE)

    def test_administrative_grant_adds_level_without_changing_role(self):
        administrator = get_user_model().objects.create_user(
            username='billing-admin',
            password='Valid-pass-456',
        )
        AdministrativeAccessGrant.objects.create(
            user=self.user,
            plan=AdministrativeAccessGrant.Plan.PRO,
            reason='Concesión autorizada para pruebas de acceso',
            granted_by=administrator,
        )

        self.assertEqual(effective_plan(self.user), AccessMembership.Plan.PRO)
        self.assertFalse(AccessRoleAssignment.objects.filter(
            user=self.user,
            role=AccessRoleAssignment.Role.PROFESOR,
            is_active=True,
        ).exists())


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class BillingDashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='billing-owner',
            password='Valid-pass-123',
        )
        self.client.force_login(self.user)

    def test_dashboard_only_displays_authenticated_users_financial_records(self):
        other_user = get_user_model().objects.create_user(
            username='other-billing-owner',
            password='Valid-pass-123',
        )
        order = BillingOrder.objects.create(
            user=other_user,
            kind=BillingOrder.Kind.SERVICE,
            status=BillingOrder.Status.FREE,
            currency='EUR',
            subtotal=Decimal('0'),
            total=Decimal('0'),
            idempotency_key='private-other-order',
        )
        Charge.objects.create(
            user=other_user,
            order=order,
            concept='INFORME-PRIVADO-8472',
            currency='EUR',
            subtotal=Decimal('0'),
            total=Decimal('0'),
            status=Charge.Status.FREE,
            idempotency_key='private-other-charge',
        )

        response = self.client.get(reverse('users:billing'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'INFORME-PRIVADO-8472')
        self.assertNotContains(response, 'private-other-order')
        self.assertContains(response, 'No hay tarifas aprobadas activas')
        self.assertContains(response, 'css/billing.css')
        self.assertContains(response, 'billing-hero')
        for target in ('mejorar-plan', 'billing-services', 'billing-contracts', 'billing-orders'):
            self.assertContains(response, f'id="{target}"')

    def test_free_service_requires_explicit_terms_and_does_not_trust_posted_price(self):
        product = ServiceProduct.objects.create(
            code='free-automatic-feature',
            name='Prestación automática gratuita',
            is_active=True,
            base_price=Decimal('0.00'),
            fulfillment=ServiceProduct.Fulfillment.AUTOMATIC,
            entitlement_code='free-automatic-feature',
        )
        url = reverse('users:free_service_order')

        rejected = self.client.post(url, {
            'service_code': product.code,
            'idempotency_key': 'free-service-view-1',
            'price': '0.00',
        })
        self.assertEqual(rejected.status_code, 302)
        self.assertFalse(BillingOrder.objects.filter(user=self.user).exists())

        accepted = self.client.post(url, {
            'service_code': product.code,
            'idempotency_key': 'free-service-view-2',
            'price': '999.99',
            'accept_terms': 'yes',
        })

        self.assertEqual(accepted.status_code, 302)
        order = BillingOrder.objects.get(user=self.user)
        self.assertEqual(order.total, Decimal('0.00'))
        self.assertEqual(order.lines.get().unit_price, Decimal('0.00'))
