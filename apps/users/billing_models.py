from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class BillingConfiguration(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Borrador'
        APPROVED = 'approved', 'Aprobada'
        ACTIVE = 'active', 'Activa'
        RETIRED = 'retired', 'Retirada'

    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name='approved_billing_configurations',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    currency = models.CharField(max_length=3, default='EUR')
    plus_monthly = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    plus_semiannual = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    plus_annual = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    pro_monthly = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    pro_semiannual = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    pro_annual = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    issuer_details = models.JSONField(default=dict, blank=True)
    invoice_prefix = models.CharField(max_length=20, blank=True)
    next_invoice_number = models.PositiveBigIntegerField(default=1)
    tax_configuration = models.JSONField(default=dict, blank=True)
    invoice_issuance_enabled = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Configuración de facturación'
        verbose_name_plural = 'Configuración de facturación'
        ordering = ['-version']
        constraints = [
            models.UniqueConstraint(fields=['version'], name='users_unique_billing_config_version'),
            models.UniqueConstraint(
                fields=['status'], condition=Q(status='active'),
                name='users_one_active_billing_config',
            ),
        ]
        permissions = [('manage_billing', 'Puede administrar configuración y registros de facturación')]

    def clean(self):
        if self.currency != 'EUR':
            raise ValidationError({'currency': 'Solo se ha establecido EUR como moneda inicial.'})
        for field in (
            'plus_monthly', 'plus_semiannual', 'plus_annual',
            'pro_monthly', 'pro_semiannual', 'pro_annual',
        ):
            value = getattr(self, field)
            if value is not None and value < 0:
                raise ValidationError({field: 'El precio no puede ser negativo.'})
        if self.invoice_issuance_enabled:
            raise ValidationError(
                {'invoice_issuance_enabled': 'La emisión fiscal está pendiente de configuración y verificación.'}
            )
        if self.status in {self.Status.APPROVED, self.Status.ACTIVE, self.Status.RETIRED}:
            if not self.approved_by_id or not self.approved_at:
                raise ValidationError('La configuración debe quedar aprobada por una persona identificada.')
        if self.pk:
            previous = type(self).objects.get(pk=self.pk)
            economic_fields = (
                'version', 'currency', 'plus_monthly', 'plus_semiannual', 'plus_annual',
                'pro_monthly', 'pro_semiannual', 'pro_annual', 'issuer_details',
                'invoice_prefix', 'next_invoice_number', 'tax_configuration',
                'invoice_issuance_enabled',
            )
            if previous.status in {self.Status.APPROVED, self.Status.ACTIVE, self.Status.RETIRED} and any(
                getattr(previous, field) != getattr(self, field) for field in economic_fields
            ):
                raise ValidationError('Una configuración aprobada es inmutable; crea una nueva versión.')
            if previous.status in {self.Status.APPROVED, self.Status.ACTIVE, self.Status.RETIRED}:
                allowed_transitions = {
                    self.Status.APPROVED: {self.Status.APPROVED, self.Status.ACTIVE},
                    self.Status.ACTIVE: {self.Status.ACTIVE, self.Status.RETIRED},
                    self.Status.RETIRED: {self.Status.RETIRED},
                }
                if self.status not in allowed_transitions[previous.status]:
                    raise ValidationError('La transición de estado de la configuración no está permitida.')
            if self.status == self.Status.ACTIVE and previous.status not in {
                self.Status.APPROVED, self.Status.ACTIVE,
            }:
                raise ValidationError('La configuración debe aprobarse en un cambio separado antes de activarse.')
        else:
            if self.status != self.Status.DRAFT:
                raise ValidationError('Crea la configuración en borrador antes de aprobarla o activarla.')
            latest_version = type(self).objects.order_by('-version').values_list('version', flat=True).first()
            expected_version = (latest_version or 0) + 1
            if self.version != expected_version:
                raise ValidationError({'version': f'La siguiente versión debe ser {expected_version}.'})

    def __str__(self):
        return f'Facturación ({self.currency})'

    @classmethod
    def current(cls):
        return cls.objects.filter(status=cls.Status.ACTIVE).order_by('-version').first()


class PaymentProviderConfiguration(models.Model):
    class Provider(models.TextChoices):
        STRIPE = 'stripe', 'Stripe'
        PAYPAL = 'paypal', 'PayPal'
        BIZUM = 'bizum', 'Bizum / adquirente'

    class Environment(models.TextChoices):
        SANDBOX = 'sandbox', 'Pruebas'
        PRODUCTION = 'production', 'Producción'

    provider = models.CharField(max_length=16, choices=Provider.choices, unique=True)
    enabled = models.BooleanField(default=False)
    environment = models.CharField(max_length=12, choices=Environment.choices, default=Environment.SANDBOX)
    currencies = models.JSONField(default=list, blank=True)
    methods = models.JSONField(default=list, blank=True)
    supports_one_time = models.BooleanField(default=False)
    supports_recurring = models.BooleanField(default=False)
    supports_refunds = models.BooleanField(default=False)
    configuration_reference = models.CharField(max_length=160, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuración de proveedor de pago'
        verbose_name_plural = 'Configuración de proveedores de pago'

    def clean(self):
        if not isinstance(self.currencies, list) or not all(
            isinstance(currency, str) and len(currency) == 3
            for currency in self.currencies
        ):
            raise ValidationError({'currencies': 'Indica una lista de códigos de moneda de tres letras.'})
        if not isinstance(self.methods, list) or not all(isinstance(method, str) for method in self.methods):
            raise ValidationError({'methods': 'Indica una lista de métodos de pago.'})
        if self.enabled or self.supports_one_time or self.supports_recurring or self.supports_refunds:
            from .stripe_billing import configuration_error
            error = configuration_error(self)
            if error:
                raise ValidationError(error)

    def __str__(self):
        return f'{self.get_provider_display()} — {"activo" if self.enabled else "pendiente"}'


class AdministrativeAccessGrant(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Activa'
        REVOKED = 'revoked', 'Retirada'
        EXPIRED = 'expired', 'Finalizada'

    class Plan(models.TextChoices):
        PLUS = 'plus', 'PLUS'
        PRO = 'pro', 'PRO'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='administrative_access_grants',
    )
    plan = models.CharField(max_length=8, choices=Plan.choices)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='issued_access_grants',
    )
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='revoked_access_grants',
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['user', 'status', 'ends_at'], name='users_grant_effective')]
        permissions = [('manage_access_grants', 'Puede gestionar concesiones administrativas de acceso')]

    def clean(self):
        if self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({'ends_at': 'La concesión debe terminar después de empezar.'})
        if self.status == self.Status.REVOKED and (not self.revoked_by_id or not self.revoked_at):
            raise ValidationError('La retirada requiere responsable y fecha.')
        if self.status != self.Status.REVOKED and (self.revoked_by_id or self.revoked_at):
            raise ValidationError('Solo una concesión retirada puede tener datos de retirada.')

    @property
    def is_current(self):
        now = timezone.now()
        return (
            self.status == self.Status.ACTIVE
            and self.starts_at <= now
            and (self.ends_at is None or self.ends_at > now)
        )

    def __str__(self):
        return f'{self.user} — {self.get_plan_display()}'


class ServiceProduct(models.Model):
    class Fulfillment(models.TextChoices):
        AUTOMATIC = 'automatic', 'Automática'
        AI = 'ai', 'IA'
        HUMAN = 'human', 'Humana'

    code = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    module = models.CharField(max_length=80, blank=True)
    is_active = models.BooleanField(default=False)
    base_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default='EUR')
    plus_discount = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0'),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    pro_discount = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0'),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    fulfillment = models.CharField(max_length=12, choices=Fulfillment.choices, default=Fulfillment.HUMAN)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    validity_days = models.PositiveIntegerField(null=True, blank=True)
    conditions = models.TextField(blank=True)
    entitlement_code = models.SlugField(max_length=100, blank=True)
    package_components = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'pk']

    def clean(self):
        if self.base_price is not None and self.base_price < 0:
            raise ValidationError({'base_price': 'El precio base no puede ser negativo.'})
        if self.currency != 'EUR':
            raise ValidationError({'currency': 'Solo se ha establecido EUR como moneda inicial.'})
        if not isinstance(self.package_components, list):
            raise ValidationError({'package_components': 'Los componentes deben ser una lista.'})
        if self.is_active and self.base_price is None:
            raise ValidationError({'base_price': 'Configura una tarifa antes de publicar el servicio.'})
        if self.quantity < 1:
            raise ValidationError({'quantity': 'La cantidad debe ser al menos uno.'})
        if self.fulfillment == self.Fulfillment.AI and self.is_active:
            raise ValidationError(
                {'fulfillment': 'La prestación con IA requiere proveedor, coste y cuota aprobados.'}
            )
    def price_for(self, plan):
        if self.base_price is None:
            return None
        discount = Decimal('0')
        if plan == 'plus':
            discount = self.plus_discount
        elif plan == 'pro':
            discount = self.pro_discount
        raw_price = self.base_price * (Decimal('100') - discount) / Decimal('100')
        return raw_price.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def __str__(self):
        return self.name


class MembershipContract(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        ACTIVE = 'active', 'Activa'
        RENEWAL_PENDING = 'renewal_pending', 'Renovación pendiente'
        RENEWAL_FAILED = 'renewal_failed', 'Renovación fallida'
        CANCEL_AT_PERIOD_END = 'cancel_at_period_end', 'Cancelación programada'
        CANCELED = 'canceled', 'Cancelada'
        EXPIRED = 'expired', 'Finalizada'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='membership_contracts',
    )
    plan = models.CharField(max_length=8, choices=AdministrativeAccessGrant.Plan.choices)
    billing_period = models.CharField(max_length=12, choices=(
        ('monthly', 'Mensual'), ('semiannual', 'Semestral'), ('annual', 'Anual'),
    ))
    contracted_price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='EUR')
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    starts_at = models.DateTimeField(null=True, blank=True)
    period_ends_at = models.DateTimeField(null=True, blank=True)
    auto_renew = models.BooleanField(default=False)
    cancel_at_period_end = models.BooleanField(default=False)
    provider = models.CharField(max_length=16, blank=True)
    provider_reference = models.CharField(max_length=200, blank=True)
    idempotency_key = models.CharField(max_length=160, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['user', 'status', 'period_ends_at'], name='users_contract_current')]

    def clean(self):
        if self.contracted_price < 0:
            raise ValidationError({'contracted_price': 'El precio no puede ser negativo.'})
        if self.currency != 'EUR':
            raise ValidationError({'currency': 'Solo se ha establecido EUR como moneda inicial.'})
        if self.period_ends_at and self.starts_at and self.period_ends_at <= self.starts_at:
            raise ValidationError({'period_ends_at': 'El periodo debe terminar después de empezar.'})
        if self.auto_renew and (self.provider != 'stripe' or not self.provider_reference):
            raise ValidationError(
                {'auto_renew': 'No hay proveedor configurado para una autorización recurrente.'}
            )

    @property
    def grants_access(self):
        now = timezone.now()
        return (
            self.status in {self.Status.ACTIVE, self.Status.RENEWAL_PENDING, self.Status.RENEWAL_FAILED,
                            self.Status.CANCEL_AT_PERIOD_END}
            and self.starts_at is not None
            and self.starts_at <= now
            and (self.period_ends_at is None or self.period_ends_at > now)
        )


class BillingEvent(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='billing_events',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='billing_history',
    )
    action = models.CharField(max_length=48)
    object_type = models.CharField(max_length=80)
    object_id = models.CharField(max_length=100, blank=True)
    idempotency_key = models.CharField(max_length=180, unique=True, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['object_type', 'object_id', 'created_at'], name='users_bill_event_obj')]


class BillingSubscription(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendiente de pago'
        ACTIVE = 'active', 'Activa'
        PAST_DUE = 'past_due', 'Pago pendiente'
        CANCELED = 'canceled', 'Cancelada'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='billing_subscriptions')
    initial_order = models.OneToOneField('users.BillingOrder', on_delete=models.PROTECT)
    replaces = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT)
    stripe_id = models.CharField(max_length=200, blank=True)
    checkout_id = models.CharField(max_length=200, blank=True)
    checkout_url = models.URLField(max_length=2048, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    cancel_at_period_end = models.BooleanField(default=False)
    period_ends_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def can_reactivate_renewal(self):
        return bool(
            self.status == self.Status.ACTIVE and self.cancel_at_period_end and self.stripe_id
            and self.period_ends_at and self.period_ends_at > timezone.now()
        )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['stripe_id'], condition=~Q(stripe_id=''), name='users_stripe_subscription_id'),
            models.UniqueConstraint(fields=['user'], condition=Q(status='pending'), name='users_one_pending_subscription'),
        ]


class BillingOrder(models.Model):
    class Kind(models.TextChoices):
        MEMBERSHIP = 'membership', 'Membresía'
        SERVICE = 'service', 'Servicio'

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Borrador'
        PENDING = 'pending', 'Pendiente'
        PAID = 'paid', 'Pagado'
        FREE = 'free', 'Coste cero'
        CANCELED = 'canceled', 'Cancelado'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='billing_orders')
    kind = models.CharField(max_length=12, choices=Kind.choices)
    membership_plan = models.CharField(
        max_length=8,
        choices=(('plus', 'PLUS'), ('pro', 'PRO')),
        blank=True,
    )
    billing_period = models.CharField(
        max_length=12,
        choices=(('monthly', 'Mensual'), ('semiannual', 'Semestral'), ('annual', 'Anual')),
        blank=True,
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    currency = models.CharField(max_length=3, default='EUR')
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    accepted_terms = models.JSONField(default=dict, blank=True)
    idempotency_key = models.CharField(max_length=160, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-pk']

    def clean(self):
        if self.kind == self.Kind.MEMBERSHIP and (not self.membership_plan or not self.billing_period):
            raise ValidationError('Un pedido de membresía requiere plan y periodo de facturación.')
        if self.kind == self.Kind.SERVICE and (self.membership_plan or self.billing_period):
            raise ValidationError('Un pedido de servicio no puede declarar una membresía.')


class BillingOrderLine(models.Model):
    order = models.ForeignKey(BillingOrder, on_delete=models.PROTECT, related_name='lines')
    product_code = models.CharField(max_length=100)
    description = models.CharField(max_length=240)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    period = models.CharField(max_length=12, blank=True)
    entitlement_code = models.CharField(max_length=100, blank=True)
    product_snapshot = models.JSONField(default=dict, blank=True)


class Charge(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        PARTIAL = 'partial', 'Parcialmente pagado'
        PAID = 'paid', 'Pagado'
        CANCELED = 'canceled', 'Cancelado'
        FREE = 'free', 'Coste cero confirmado'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='charges')
    order = models.ForeignKey(BillingOrder, on_delete=models.PROTECT, related_name='charges')
    concept = models.CharField(max_length=240)
    period_start = models.DateTimeField(null=True, blank=True)
    period_end = models.DateTimeField(null=True, blank=True)
    currency = models.CharField(max_length=3, default='EUR')
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    total = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    due_at = models.DateTimeField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=160, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [models.Index(fields=['user', 'status', 'due_at'], name='users_charge_state_due')]

    @property
    def amount_paid(self):
        return self.payments.aggregate(total=models.Sum('amount'))['total'] or Decimal('0')

    @property
    def amount_refunded(self):
        return self.refunds.filter(status=Refund.Status.CONFIRMED).aggregate(
            total=models.Sum('amount'),
        )['total'] or Decimal('0')

    @property
    def amount_pending(self):
        if self.status in {self.Status.CANCELED, self.Status.FREE}:
            return Decimal('0')
        return max(self.total - self.amount_paid, Decimal('0'))

    @property
    def amount_net(self):
        return max(self.amount_paid - self.amount_refunded, Decimal('0'))


class PaymentAttempt(models.Model):
    class Status(models.TextChoices):
        CREATED = 'created', 'Iniciado'
        PENDING = 'pending', 'Pendiente'
        FAILED = 'failed', 'Fallido'
        CANCELED = 'canceled', 'Cancelado'
        SUCCEEDED = 'succeeded', 'Verificado'

    charge = models.ForeignKey(Charge, on_delete=models.PROTECT, related_name='attempts')
    provider = models.CharField(max_length=16, choices=PaymentProviderConfiguration.Provider.choices)
    method = models.CharField(max_length=40)
    external_reference = models.CharField(max_length=200, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    idempotency_key = models.CharField(max_length=180, unique=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.CREATED)
    failure_code = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['provider', 'external_reference'],
                condition=~Q(external_reference=''),
                name='users_unique_payment_attempt_ref',
            ),
        ]


class ConfirmedPayment(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='confirmed_payments')
    charge = models.ForeignKey(Charge, on_delete=models.PROTECT, related_name='payments')
    attempt = models.OneToOneField(PaymentAttempt, on_delete=models.PROTECT, related_name='confirmed_payment')
    provider = models.CharField(max_length=16, choices=PaymentProviderConfiguration.Provider.choices)
    method = models.CharField(max_length=40)
    external_reference = models.CharField(max_length=200)
    idempotency_key = models.CharField(max_length=180, unique=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    confirmed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['provider', 'external_reference'], name='users_unique_confirmed_payment_ref'),
        ]


class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Borrador'
        ISSUED = 'issued', 'Emitida'
        VOID = 'void', 'Anulada mediante documento corrector'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='billing_invoices')
    charge = models.ForeignKey(Charge, on_delete=models.PROTECT, related_name='invoices')
    series = models.CharField(max_length=20, blank=True)
    number = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.DRAFT)
    issued_at = models.DateTimeField(null=True, blank=True)
    issuer_snapshot = models.JSONField(default=dict)
    recipient_snapshot = models.JSONField(default=dict)
    lines_snapshot = models.JSONField(default=list)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    discount_total = models.DecimalField(max_digits=12, decimal_places=2)
    tax_total = models.DecimalField(max_digits=12, decimal_places=2)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default='EUR')
    pdf = models.FileField(upload_to='billing/invoices/', null=True, blank=True)
    external_reference = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['series', 'number'],
                condition=~Q(number=''),
                name='users_unique_invoice_number',
            ),
            models.UniqueConstraint(fields=['charge'], name='users_one_invoice_per_charge'),
        ]
        ordering = ['-created_at', '-pk']

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.get(pk=self.pk)
            if previous.status == self.Status.ISSUED and any(
                getattr(previous, field) != getattr(self, field)
                for field in (
                    'user_id', 'charge_id', 'series', 'number', 'issued_at',
                    'issuer_snapshot', 'recipient_snapshot', 'lines_snapshot',
                    'subtotal', 'discount_total', 'tax_total', 'total', 'currency', 'pdf',
                )
            ):
                raise ValidationError('Una factura emitida no se puede editar; registre un documento corrector.')
        super().save(*args, **kwargs)


class Refund(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        CONFIRMED = 'confirmed', 'Confirmada'
        FAILED = 'failed', 'Fallida'

    payment = models.ForeignKey(ConfirmedPayment, on_delete=models.PROTECT, related_name='refunds')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    reason = models.TextField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    provider_reference = models.CharField(max_length=200, blank=True)
    idempotency_key = models.CharField(max_length=180, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_refunds',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    def clean(self):
        if self.amount <= 0:
            raise ValidationError({'amount': 'El importe de devolución debe ser positivo.'})
        if self.currency != self.payment.currency:
            raise ValidationError({'currency': 'La devolución debe usar la moneda del pago.'})
        other_refunds = Refund.objects.filter(
            payment_id=self.payment_id,
            status__in=[self.Status.PENDING, self.Status.CONFIRMED],
        )
        if self.pk:
            other_refunds = other_refunds.exclude(pk=self.pk)
        reserved = other_refunds.aggregate(total=models.Sum('amount'))['total'] or Decimal('0')
        if reserved + self.amount > self.payment.amount:
            raise ValidationError({'amount': 'La devolución supera el importe disponible del pago.'})


class PromotionalVoucher(models.Model):
    code = models.SlugField(max_length=80, unique=True)
    description = models.CharField(max_length=240, blank=True)
    discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01')), MaxValueValidator(100)],
    )
    is_active = models.BooleanField(default=False)
    starts_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    allowed_plans = models.JSONField(default=list, blank=True)
    allowed_periods = models.JSONField(default=list, blank=True)
    global_limit = models.PositiveIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_vouchers',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['code']

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def clean(self):
        if self.expires_at and self.starts_at and self.expires_at <= self.starts_at:
            raise ValidationError({'expires_at': 'El bono debe vencer después de su inicio.'})
        if not isinstance(self.allowed_plans, list) or set(self.allowed_plans) - {'plus', 'pro'}:
            raise ValidationError({'allowed_plans': 'Selecciona planes PLUS o PRO.'})
        periods = {'monthly', 'semiannual', 'annual'}
        if not isinstance(self.allowed_periods, list) or set(self.allowed_periods) - periods:
            raise ValidationError({'allowed_periods': 'Hay una periodicidad no válida.'})

    def __str__(self):
        return self.code


class UserVoucher(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = 'available', 'Disponible'
        RESERVED = 'reserved', 'Reservado'
        USED = 'used', 'Utilizado'
        EXPIRED = 'expired', 'Caducado'
        REVOKED = 'revoked', 'Revocado'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='vouchers')
    voucher = models.ForeignKey(PromotionalVoucher, on_delete=models.PROTECT, related_name='grants')
    granted_at = models.DateTimeField(auto_now_add=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='issued_vouchers',
    )
    reason = models.TextField(blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.AVAILABLE)
    reserved_at = models.DateTimeField(null=True, blank=True)
    reservation_reference = models.CharField(max_length=180, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)
    charge = models.ForeignKey(Charge, null=True, blank=True, on_delete=models.PROTECT, related_name='vouchers')

    class Meta:
        indexes = [models.Index(fields=['user', 'status', 'expires_at'], name='users_voucher_owner_state')]
        permissions = [('grant_vouchers', 'Puede conceder bonos a usuarios')]
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'voucher'], condition=Q(status__in=['available', 'reserved']),
                name='users_no_duplicate_active_voucher',
            ),
        ]

    @property
    def effective_expiry(self):
        dates = [date for date in (self.expires_at, self.voucher.expires_at) if date is not None]
        return min(dates) if dates else None
