"""Energía Ducky: monedero compartido para las herramientas IA de la plataforma.

Los saldos usan precisión decimal fija (3 decimales). Ningún módulo modifica estos
modelos directamente: todas las variaciones pasan por ``apps.energy.services``.
"""
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

ENERGY_DIGITS = {'max_digits': 14, 'decimal_places': 3}
ZERO = Decimal('0')


class Plan(models.TextChoices):
    FREE = 'free', 'FREE'
    PLUS = 'plus', 'PLUS'
    PRO = 'pro', 'PRO'


class EnergyPolicy(models.Model):
    """Capacidad y recuperación por nivel. Sin configuración no hay energía recuperable."""

    plan = models.CharField(max_length=8, choices=Plan.choices, unique=True)
    enabled = models.BooleanField(default=False)
    capacity = models.DecimalField(
        'capacidad recuperable', null=True, blank=True, validators=[MinValueValidator(ZERO)], **ENERGY_DIGITS,
    )
    recovery_amount = models.DecimalField(
        'recuperación por intervalo', null=True, blank=True,
        validators=[MinValueValidator(ZERO)], **ENERGY_DIGITS,
    )
    recovery_interval_minutes = models.PositiveIntegerField(
        'intervalo de recuperación (min)', null=True, blank=True, validators=[MinValueValidator(1)],
    )
    activity_daily_percent = models.DecimalField(
        '% diario máximo por actividad', max_digits=5, decimal_places=2,
        validators=[MinValueValidator(ZERO), MaxValueValidator(Decimal('100'))],
    )
    max_concurrent_operations = models.PositiveIntegerField(
        'operaciones IA simultáneas', null=True, blank=True,
        help_text='Sin valor se aplica 1 operación simultánea (criterio conservador).',
    )
    activity_recent_minutes = models.PositiveIntegerField(
        'duración del indicador «Recargando» tras actividad (min)', null=True, blank=True,
        help_text='Sin valor, la actividad no muestra «Recargando».',
    )
    version = models.PositiveIntegerField(default=1, editable=False)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
        editable=False,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Política de energía por nivel'
        verbose_name_plural = 'Políticas de energía por nivel'
        ordering = ['pk']

    def __str__(self):
        return f'Energía {self.get_plan_display()} v{self.version}'

    @property
    def is_configured(self):
        return self.enabled and self.capacity is not None and self.capacity > 0

    @property
    def recovery_configured(self):
        return bool(
            self.is_configured and self.recovery_amount and self.recovery_amount > 0
            and self.recovery_interval_minutes,
        )

    def clean(self):
        if self.enabled and not self.capacity:
            raise ValidationError({'capacity': 'Configura una capacidad antes de habilitar la política.'})
        if bool(self.recovery_amount) != bool(self.recovery_interval_minutes):
            raise ValidationError('La recuperación periódica necesita cantidad e intervalo, o ninguno de los dos.')


class EnergyToolCost(models.Model):
    class ReleasePolicy(models.TextChoices):
        RELEASE_ON_FAILURE = 'release_on_failure', 'Liberar todo si no hay resultado utilizable'

    code = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=160)
    module = models.CharField(max_length=80)
    required_access = models.CharField(
        'prestación requerida', max_length=160,
        help_text='Descripción del derecho que comprueba el módulo (plan o derecho comprado).',
    )
    service_entitlement_code = models.SlugField(
        'derecho de servicio que cubre la ejecución', max_length=100, blank=True,
        help_text='Si el usuario tiene este derecho vigente, se usa en lugar de energía.',
    )
    cost = models.DecimalField(
        'coste energético base', null=True, blank=True, validators=[MinValueValidator(Decimal('0.001'))],
        **ENERGY_DIGITS,
    )
    variants = models.JSONField(
        default=dict, blank=True, help_text='Coste por variante: {"variante": "12.5"}. Sustituye al coste base.',
    )
    max_input_chars = models.PositiveIntegerField(null=True, blank=True)
    max_output_chars = models.PositiveIntegerField(null=True, blank=True)
    max_duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    release_policy = models.CharField(
        max_length=30, choices=ReleasePolicy.choices, default=ReleasePolicy.RELEASE_ON_FAILURE,
    )
    enabled = models.BooleanField(default=False)
    paused = models.BooleanField(
        'pausada', default=False, help_text='Pausa operativa (por ejemplo, presupuesto agotado).',
    )
    provider_budget = models.DecimalField(
        'presupuesto del proveedor (EUR)', max_digits=12, decimal_places=4, null=True, blank=True,
        help_text='Sin presupuesto la herramienta no se ejecuta.',
    )
    budget_period_days = models.PositiveIntegerField(null=True, blank=True)
    budget_alert_percent = models.PositiveIntegerField(
        null=True, blank=True, validators=[MaxValueValidator(100)],
    )
    version = models.PositiveIntegerField(default=1, editable=False)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
        editable=False,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Coste de herramienta IA'
        verbose_name_plural = 'Costes de herramientas IA'
        ordering = ['module', 'code']

    def __str__(self):
        return f'{self.name} ({self.code})'

    def cost_for(self, variant=''):
        if variant and variant in (self.variants or {}):
            return Decimal(str(self.variants[variant]))
        return self.cost

    def clean(self):
        if not isinstance(self.variants, dict):
            raise ValidationError({'variants': 'Las variantes deben ser un objeto.'})
        for key, value in (self.variants or {}).items():
            try:
                if Decimal(str(value)) <= 0:
                    raise ValidationError({'variants': f'El coste de «{key}» debe ser positivo.'})
            except ArithmeticError as error:
                raise ValidationError({'variants': f'Coste no válido en «{key}».'}) from error
        if self.enabled and self.cost is None and not self.variants:
            raise ValidationError({'cost': 'Configura el coste antes de habilitar la herramienta.'})


class EnergyRewardRule(models.Model):
    class Source(models.TextChoices):
        COURSE_TOPIC_COMPLETED = 'courses.topic_completed', 'Cursos: tema completado'
        COURSE_QUIZ_PASSED = 'courses.quiz_passed', 'Cursos: quiz aprobado'
        LMS_QUIZ_PASSED = 'lms.quiz_passed', 'Campus LMS: evaluación aprobada (corregida en servidor)'
        LMS_MODULE_COMPLETED = 'lms.module_completed', 'Campus LMS: módulo completado'

    class Frequency(models.TextChoices):
        ONCE_PER_OBJECT = 'once_per_object', 'Una vez por objeto y usuario'

    code = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=160)
    source = models.CharField('fuente autorizada', max_length=60, choices=Source.choices, unique=True)
    requirements = models.TextField('requisitos', blank=True)
    amount = models.DecimalField(
        'cantidad concedida', null=True, blank=True, validators=[MinValueValidator(Decimal('0.001'))],
        **ENERGY_DIGITS,
    )
    frequency = models.CharField(max_length=30, choices=Frequency.choices, default=Frequency.ONCE_PER_OBJECT)
    max_events_per_day = models.PositiveIntegerField(
        'límite adicional de eventos por día', null=True, blank=True,
    )
    enabled = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1, editable=False)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
        editable=False,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Regla de recompensa por actividad'
        verbose_name_plural = 'Reglas de recompensa por actividad'
        ordering = ['code']

    def __str__(self):
        return self.name

    def clean(self):
        if self.enabled and not self.amount:
            raise ValidationError({'amount': 'Configura la cantidad antes de habilitar la regla.'})


class EnergyRechargeProduct(models.Model):
    """Vincula un servicio del 00B con las unidades de energía que acredita."""

    service = models.OneToOneField(
        'users.ServiceProduct', on_delete=models.PROTECT, related_name='energy_recharge',
    )
    energy_units = models.DecimalField(
        'energía por unidad comprada', validators=[MinValueValidator(Decimal('0.001'))], **ENERGY_DIGITS,
    )
    enabled = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1, editable=False)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
        editable=False,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Producto de recarga'
        verbose_name_plural = 'Productos de recarga'

    def __str__(self):
        return f'{self.service.name}: {self.energy_units} energía'


class EnergyConfigVersion(models.Model):
    """Histórico inmutable de cambios de configuración (responsable, fecha, instantánea)."""

    kind = models.CharField(max_length=30)
    object_key = models.CharField(max_length=100)
    version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    changed_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Versión de configuración'
        verbose_name_plural = 'Versiones de configuración'
        ordering = ['-changed_at', '-pk']
        constraints = [
            models.UniqueConstraint(fields=['kind', 'object_key', 'version'], name='energy_config_version_unique'),
        ]


class EnergyWallet(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='energy_wallet')
    recoverable_available = models.DecimalField(default=ZERO, **ENERGY_DIGITS)
    recoverable_reserved = models.DecimalField(default=ZERO, **ENERGY_DIGITS)
    recovery_anchor = models.DateTimeField(null=True, blank=True)
    last_activity_grant_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Monedero de energía'
        verbose_name_plural = 'Monederos de energía'
        permissions = [('adjust_energy', 'Puede ajustar energía con motivo')]
        constraints = [
            models.CheckConstraint(
                check=models.Q(recoverable_available__gte=0, recoverable_reserved__gte=0),
                name='energy_wallet_non_negative',
            ),
        ]

    def __str__(self):
        return f'Monedero de {self.user}'


class EnergyPurchasedLot(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Activo'
        EXPIRED = 'expired', 'Caducado'
        REFUNDED = 'refunded', 'Devuelto'
        REVIEW = 'review', 'Devolución en revisión'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(EnergyWallet, on_delete=models.PROTECT, related_name='lots')
    quantity = models.DecimalField(**ENERGY_DIGITS)
    available = models.DecimalField(**ENERGY_DIGITS)
    reserved = models.DecimalField(default=ZERO, **ENERGY_DIGITS)
    consumed = models.DecimalField(default=ZERO, **ENERGY_DIGITS)
    removed = models.DecimalField('caducada o retirada', default=ZERO, **ENERGY_DIGITS)
    expires_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    order = models.ForeignKey('users.BillingOrder', null=True, blank=True, on_delete=models.PROTECT)
    order_line = models.ForeignKey('users.BillingOrderLine', null=True, blank=True, on_delete=models.PROTECT)
    product_code = models.CharField(max_length=100, blank=True)
    conditions = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=180, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Lote de energía comprada'
        verbose_name_plural = 'Lotes de energía comprada'
        ordering = ['created_at']
        constraints = [
            models.CheckConstraint(
                check=models.Q(available__gte=0, reserved__gte=0, consumed__gte=0, removed__gte=0),
                name='energy_lot_non_negative',
            ),
        ]

    def __str__(self):
        return f'Lote {self.quantity} ({self.get_status_display()})'


class EnergyOperation(models.Model):
    class Status(models.TextChoices):
        RESERVED = 'reserved', 'Reservada'
        CONFIRMED = 'confirmed', 'Consumida'
        RELEASED = 'released', 'Liberada'
        COVERED = 'covered', 'Cubierta por derecho de servicio'
        REVIEW = 'review', 'Pendiente de conciliación'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='energy_operations')
    tool = models.ForeignKey(EnergyToolCost, on_delete=models.PROTECT)
    variant = models.CharField(max_length=60, blank=True)
    amount = models.DecimalField(**ENERGY_DIGITS)
    cost_version = models.PositiveIntegerField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RESERVED)
    idempotency_key = models.CharField(max_length=180)
    related_object = models.CharField('trabajo relacionado', max_length=180, blank=True)
    entitlement = models.ForeignKey(
        'users.PurchasedEntitlement', null=True, blank=True, on_delete=models.PROTECT,
    )
    failure_reason = models.CharField(max_length=200, blank=True)
    provider = models.CharField(max_length=60, blank=True)
    provider_model = models.CharField(max_length=100, blank=True)
    provider_usage = models.JSONField('consumo técnico facturado', default=dict, blank=True)
    provider_cost_estimated = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    provider_cost_confirmed = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Operación IA con energía'
        verbose_name_plural = 'Operaciones IA con energía'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'idempotency_key'], name='energy_operation_key'),
        ]


class EnergyReservation(models.Model):
    """Reparto de una operación por componente, para devolver cada unidad a su procedencia."""

    class Status(models.TextChoices):
        RESERVED = 'reserved', 'Reservada'
        CONSUMED = 'consumed', 'Consumida'
        RELEASED = 'released', 'Liberada'

    operation = models.ForeignKey(EnergyOperation, on_delete=models.PROTECT, related_name='reservations')
    lot = models.ForeignKey(EnergyPurchasedLot, null=True, blank=True, on_delete=models.PROTECT)
    amount = models.DecimalField(**ENERGY_DIGITS)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RESERVED)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Reserva de energía'
        verbose_name_plural = 'Reservas de energía'

    @property
    def component(self):
        return 'purchased' if self.lot_id else 'recoverable'


class EnergyActivityGrant(models.Model):
    """Evento de actividad procesado (con o sin energía concedida). Impide reclamar dos veces."""

    class Result(models.TextChoices):
        GRANTED = 'granted', 'Concedida'
        PARTIAL = 'partial', 'Concedida parcialmente'
        DAILY_LIMIT = 'daily_limit', 'Límite diario alcanzado'
        FULL = 'full', 'Energía completa'
        UNAVAILABLE = 'unavailable', 'Política no configurada'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='energy_grants')
    rule = models.ForeignKey(EnergyRewardRule, on_delete=models.PROTECT)
    rule_version = models.PositiveIntegerField()
    event_key = models.CharField(max_length=180)
    day = models.DateField('día (Europe/Madrid)')
    plan = models.CharField(max_length=8)
    daily_limit = models.DecimalField(**ENERGY_DIGITS)
    requested = models.DecimalField(**ENERGY_DIGITS)
    granted = models.DecimalField(**ENERGY_DIGITS)
    result = models.CharField(max_length=12, choices=Result.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Recompensa por actividad'
        verbose_name_plural = 'Recompensas por actividad'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'rule', 'event_key'], name='energy_activity_event_once'),
        ]
        indexes = [models.Index(fields=['user', 'day'], name='energy_grant_user_day')]


class EnergyLedgerEntry(models.Model):
    class Kind(models.TextChoices):
        RECOVERY = 'recovery', 'Recuperación periódica'
        ACTIVITY = 'activity', 'Recuperación por actividad'
        PURCHASE = 'purchase', 'Recarga comprada'
        RESERVE = 'reserve', 'Reserva'
        CONSUME = 'consume', 'Consumo'
        RELEASE = 'release', 'Liberación'
        ADJUST = 'adjust', 'Ajuste'
        REVERSAL = 'reversal', 'Reversión'
        EXPIRY = 'expiry', 'Caducidad'

    class Component(models.TextChoices):
        RECOVERABLE = 'recoverable', 'Recuperable'
        PURCHASED = 'purchased', 'Comprada'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='energy_ledger')
    kind = models.CharField(max_length=10, choices=Kind.choices)
    component = models.CharField(max_length=12, choices=Component.choices)
    lot = models.ForeignKey(EnergyPurchasedLot, null=True, blank=True, on_delete=models.PROTECT)
    available_delta = models.DecimalField(default=ZERO, **ENERGY_DIGITS)
    reserved_delta = models.DecimalField(default=ZERO, **ENERGY_DIGITS)
    reason = models.CharField(max_length=240)
    tool_code = models.CharField(max_length=80, blank=True)
    activity_code = models.CharField(max_length=80, blank=True)
    operation = models.ForeignKey(EnergyOperation, null=True, blank=True, on_delete=models.PROTECT)
    rule_reference = models.CharField('regla y versión', max_length=120, blank=True)
    idempotency_key = models.CharField(max_length=200, unique=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+',
    )
    process = models.CharField('proceso responsable', max_length=60)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Movimiento de energía'
        verbose_name_plural = 'Movimientos de energía'
        ordering = ['-created_at', '-pk']
        indexes = [models.Index(fields=['user', '-created_at'], name='energy_ledger_user_date')]

    @property
    def signed_amount(self):
        """Cantidad visible para el usuario: variación de su energía utilizable o reservada."""
        if self.kind == self.Kind.CONSUME:
            return self.reserved_delta
        return self.available_delta
