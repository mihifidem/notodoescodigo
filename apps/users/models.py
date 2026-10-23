"""Custom user model and notifications."""
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone


class Aula(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    direccion = models.CharField(max_length=255)
    horario = models.CharField(max_length=150)
    cursos = models.ManyToManyField(
        'courses.Curso',
        blank=True,
        related_name='aulas_disponibles',
        verbose_name='Cursos disponibles',
    )

    class Meta:
        verbose_name = 'Aula'
        verbose_name_plural = 'Aulas'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class RamaProfesional(models.Model):
    code = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'name']
        verbose_name = 'Rama profesional'
        verbose_name_plural = 'Ramas profesionales'

    def save(self, *args, **kwargs):
        if self.pk:
            original_code = type(self).objects.filter(pk=self.pk).values_list('code', flat=True).first()
            if original_code is not None and self.code != original_code:
                raise ValidationError({'code': 'El código de una rama profesional no se puede cambiar.'})
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class User(AbstractUser):
    GENERO_CHOICES = [
        ('hombre', 'Hombre'),
        ('mujer', 'Mujer'),
    ]

    DUCK_SPECIALTY_CHOICES = [
        ('developer', 'Developer'),
        ('systems', 'Sistemas'),
        ('cybersecurity', 'Ciberseguridad'),
        ('gamer', 'Gamer'),
        ('data_ia', 'Data IA'),
    ]

    ROLE_CHOICES = [
        ('usuario', 'Usuario'),
        ('profesor', 'Profesor'),
        ('alumno', 'Alumno'),
        ('empresa', 'Empresa'),
        ('academia', 'Academia'),
        ('headhunter', 'Headhunter'),
    ]

    NIVEL_CHOICES = [
        ('noob', 'Noob'),
        ('youngling', 'Youngling'),
        ('padawan', 'Padawan'),
        ('caballero_codigo', 'Caballero del codigo'),
        ('maestro_codigo', 'Maestro del codigo'),
        ('gran_maestro_jedi', 'Gran Maestro Jedi'),
    ]

    # Minimum total points required to reach each level.
    NIVEL_UMBRAL_PUNTOS = [
        ('noob', 0),
        ('youngling', 1000),
        ('padawan', 2500),
        ('caballero_codigo', 5000),
        ('maestro_codigo', 9000),
        ('gran_maestro_jedi', 15000),
    ]

    role = models.CharField(
        'Rol', max_length=20, choices=ROLE_CHOICES, default='usuario',
        help_text='Tipo de usuario y rol de acceso. No modifica el plan FREE/PLUS/PRO.',
    )
    puntos = models.IntegerField(default=0, help_text='Saldo canjeables actual')
    puntos_totales = models.IntegerField(default=0, help_text='Puntos acumulados históricos (nunca decrementan)')
    nivel = models.CharField(max_length=20, choices=NIVEL_CHOICES, default='noob')
    streak = models.IntegerField(default=0, help_text='Racha de días consecutivos de acceso')
    fecha_ultimo_acceso = models.DateField(null=True, blank=True)
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True)
    bio = models.TextField(blank=True)
    fecha_nacimiento = models.DateField(null=True, blank=True, verbose_name='Fecha de nacimiento')
    genero = models.CharField(max_length=10, choices=GENERO_CHOICES, blank=True, default='')
    duck_specialty = models.CharField(
        max_length=20, choices=DUCK_SPECIALTY_CHOICES, blank=True, default='',
        verbose_name='Especialidad Ducky',
    )
    aula = models.ForeignKey(
        Aula,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='usuarios',
        verbose_name='Aula',
    )
    rama_principal = models.ForeignKey(
        RamaProfesional,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='usuarios',
        verbose_name='Rama profesional principal',
    )

    class Meta:
        verbose_name = 'Usuario'
        verbose_name_plural = 'Usuarios'

    def clean(self):
        super().clean()
        if not self.rama_principal_id:
            return
        branch = self.rama_principal
        if branch.is_active:
            return
        previous_branch_id = None
        if self.pk:
            previous_branch_id = type(self).objects.filter(pk=self.pk).values_list(
                'rama_principal_id',
                flat=True,
            ).first()
        if previous_branch_id != branch.pk:
            raise ValidationError({
                'rama_principal': 'La rama seleccionada está desactivada y no admite nuevas asignaciones.',
            })

    def __str__(self):
        return self.username

    def save(self, *args, **kwargs):
        from .role_services import sync_user_role

        using = kwargs.get('using') or self._state.db or 'default'
        update_fields = kwargs.get('update_fields')
        changes_role = update_fields is None or 'role' in update_fields
        if changes_role:
            self._meta.get_field('role').clean(self.role, self)
        with transaction.atomic(using=using):
            previous_role = None
            if self.pk and changes_role:
                previous_role = type(self).objects.using(using).select_for_update().filter(
                    pk=self.pk,
                ).values_list('role', flat=True).first()
            super().save(*args, **kwargs)
            if changes_role and previous_role != self.role:
                sync_user_role(self, previous_role=previous_role, using=using)

    @property
    def userProfileArenas(self):
        return getattr(self, 'user_profile_arenas', None)

    def get_cursos_disponibles(self):
        from apps.courses.models import Curso

        cursos_inscritos_ids = self.inscripciones.values_list('curso_id', flat=True)
        filtros = Q(pk__in=cursos_inscritos_ids)

        if self.aula_id:
            filtros |= Q(aulas_disponibles=self.aula)
        else:
            filtros = Q(activo=True)

        return Curso.objects.filter(filtros, activo=True).distinct()

    def puede_inscribirse_en_curso(self, curso) -> bool:
        if not self.aula_id or not curso.activo:
            return False
        return self.aula.cursos.filter(pk=curso.pk, activo=True).exists()

    # ------------------------------------------------------------------
    # Gamification helpers
    # ------------------------------------------------------------------
    def agregar_puntos(self, cantidad: int) -> None:
        """Add points to both spendable balance and cumulative total, then update level."""
        type(self).objects.filter(pk=self.pk).update(
            puntos=F('puntos') + cantidad, puntos_totales=F('puntos_totales') + cantidad,
        )
        self.refresh_from_db(fields=['puntos', 'puntos_totales'])
        self.actualizar_nivel()
        from apps.gamification.utils import verificar_insignias

        verificar_insignias(self)

    def actualizar_nivel(self) -> None:
        """Set level based on total accumulated points (never affected by redemptions)."""
        nuevo = 'noob'
        for nivel, umbral in self.NIVEL_UMBRAL_PUNTOS:
            if self.puntos_totales >= umbral:
                nuevo = nivel
        if nuevo != self.nivel:
            self.nivel = nuevo
            self.save(update_fields=['nivel'])

    def actualizar_streak(self) -> None:
        """Increment or reset the daily streak."""
        today = timezone.localdate()
        if self.fecha_ultimo_acceso:
            diff = (today - self.fecha_ultimo_acceso).days
            if diff == 1:
                self.streak += 1
            elif diff > 1:
                self.streak = 1
            # diff == 0 means same day – do nothing
        else:
            self.streak = 1
        self.fecha_ultimo_acceso = today
        self.save(update_fields=['streak', 'fecha_ultimo_acceso'])
        from apps.gamification.utils import verificar_insignias

        verificar_insignias(self)

    @property
    def progreso_nivel(self) -> int:
        """Return progress percentage towards the next level (based on total accumulated points)."""
        niveles = self.NIVEL_UMBRAL_PUNTOS
        for index, (nivel, umbral_actual) in enumerate(niveles):
            if nivel != self.nivel:
                continue
            if index == len(niveles) - 1:
                return 100
            _, siguiente_umbral = niveles[index + 1]
            avance = self.puntos_totales - umbral_actual
            tramo = max(siguiente_umbral - umbral_actual, 1)
            return min(max(int((avance / tramo) * 100), 0), 100)
        return 100


class Organization(models.Model):
    class Kind(models.TextChoices):
        ACADEMY = 'academy', 'Academia'
        COMPANY = 'company', 'Empresa'

    name = models.CharField(max_length=160)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['kind', 'name'], name='users_unique_org_kind_name'),
        ]
        verbose_name = 'Organización'
        verbose_name_plural = 'Organizaciones'

    def __str__(self):
        return self.name


class OrganizationMembership(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        ACTIVE = 'active', 'Activa'
        SUSPENDED = 'suspended', 'Suspendida'
        ENDED = 'ended', 'Finalizada'

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name='memberships',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='organization_memberships',
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['organization', 'user'], name='users_unique_org_member'),
        ]
        indexes = [models.Index(fields=['user', 'status'], name='users_org_user_status')]
        verbose_name = 'Membresía de organización'
        verbose_name_plural = 'Membresías de organización'

    def __str__(self):
        return f'{self.user} — {self.organization}'


class AccessRoleAssignment(models.Model):
    class Role(models.TextChoices):
        ADMIN = 'admin', 'Administrador'
        USUARIO = 'usuario', 'Usuario'
        ALUMNO = 'alumno', 'Alumno'
        PROFESOR = 'profesor', 'Profesor'
        EMPRESA = 'empresa', 'Empresa'
        HEADHUNTER = 'headhunter', 'Headhunter'
        ACADEMIA = 'academia', 'Academia'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='access_role_assignments',
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, null=True, blank=True, related_name='role_assignments',
    )
    is_active = models.BooleanField(default=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assigned_access_roles',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'role'], condition=Q(organization__isnull=True),
                name='users_unique_global_access_role',
            ),
            models.UniqueConstraint(
                fields=['user', 'role', 'organization'], condition=Q(organization__isnull=False),
                name='users_unique_scoped_access_role',
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'role', 'is_active'], name='users_access_role_lookup'),
            models.Index(fields=['organization', 'role', 'is_active'], name='users_access_org_role'),
        ]
        permissions = [('assign_access_roles', 'Puede asignar roles de acceso')]
        verbose_name = 'Asignación de rol'
        verbose_name_plural = 'Asignaciones de roles'

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.role in {self.Role.ADMIN, self.Role.USUARIO, self.Role.ALUMNO, self.Role.PROFESOR}:
            if self.organization_id and self.role != self.Role.PROFESOR:
                raise ValidationError({'organization': 'Este rol debe asignarse globalmente.'})
        if self.role in {self.Role.EMPRESA, self.Role.HEADHUNTER}:
            if not self.organization_id or self.organization.kind != Organization.Kind.COMPANY:
                raise ValidationError({'organization': 'Empresa y Headhunter requieren una empresa.'})
        if self.role == self.Role.ACADEMIA:
            if not self.organization_id or self.organization.kind != Organization.Kind.ACADEMY:
                raise ValidationError({'organization': 'ACADEMIA requiere una academia.'})
        if self.role == self.Role.ADMIN and self.organization_id:
            raise ValidationError({'organization': 'ADMIN es un rol global.'})
        if self.organization_id and self.is_active and not OrganizationMembership.objects.filter(
            organization_id=self.organization_id,
            user_id=self.user_id,
            status=OrganizationMembership.Status.ACTIVE,
        ).exists():
            raise ValidationError({'organization': 'El usuario necesita una membresía activa en la organización.'})

    def __str__(self):
        return f'{self.user} — {self.get_role_display()}'


class AccessMembership(models.Model):
    class Plan(models.TextChoices):
        FREE = 'free', 'FREE'
        PLUS = 'plus', 'PLUS'
        PRO = 'pro', 'PRO'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Activa'
        CANCELED = 'canceled', 'Cancelada'
        EXPIRED = 'expired', 'Finalizada'

    class BillingPeriod(models.TextChoices):
        MONTHLY = 'monthly', 'Mensual'
        SEMIANNUAL = 'semiannual', 'Semestral'
        ANNUAL = 'annual', 'Anual'

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='access_membership',
    )
    plan = models.CharField(max_length=8, choices=Plan.choices, default=Plan.FREE)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    billing_period = models.CharField(max_length=12, choices=BillingPeriod.choices, blank=True, default='')
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Membresía de acceso'
        verbose_name_plural = 'Membresías de acceso'
        permissions = [('manage_access_memberships', 'Puede administrar membresías de acceso')]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.plan == self.Plan.FREE and self.billing_period:
            raise ValidationError({'billing_period': 'FREE no tiene periodo de facturación.'})
        if self.plan != self.Plan.FREE and not self.billing_period:
            raise ValidationError({'billing_period': 'PLUS y PRO requieren un periodo de facturación.'})
        if self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({'ends_at': 'La vigencia debe terminar después de empezar.'})

    @property
    def effective_plan(self):
        if self.status != self.Status.ACTIVE or self.starts_at > timezone.now():
            return self.Plan.FREE
        if self.ends_at and self.ends_at <= timezone.now():
            return self.Plan.FREE
        return self.plan

    def __str__(self):
        return f'{self.user} — {self.get_plan_display()}'


class PurchasedEntitlement(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='purchased_entitlements',
    )
    code = models.SlugField(max_length=100)
    source = models.CharField(max_length=40)
    source_reference = models.CharField(max_length=160)
    idempotency_key = models.CharField(max_length=160, unique=True)
    starts_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['source', 'source_reference'], name='users_unique_entitlement_source_ref',
            ),
        ]
        indexes = [models.Index(fields=['user', 'code', 'expires_at'], name='users_entitlement_lookup')]
        verbose_name = 'Derecho comprado'
        verbose_name_plural = 'Derechos comprados'

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.expires_at and self.expires_at <= self.starts_at:
            raise ValidationError({'expires_at': 'La vigencia debe terminar después de empezar.'})

    @property
    def is_current(self):
        now = timezone.now()
        return (
            self.revoked_at is None
            and self.starts_at <= now
            and (self.expires_at is None or self.expires_at > now)
        )

    def __str__(self):
        return f'{self.user} — {self.code}'


class AccessPolicyConfiguration(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Borrador'
        APPROVED = 'approved', 'Aprobada'
        ACTIVE = 'active', 'Activa'
        RETIRED = 'retired', 'Retirada'

    key = models.SlugField(max_length=80)
    version = models.PositiveIntegerField()
    configuration = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='approved_access_policies',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['key', 'version'], name='users_unique_access_policy_version'),
            models.UniqueConstraint(
                fields=['key'], condition=Q(status='active'), name='users_one_active_access_policy',
            ),
        ]
        ordering = ['key', '-version']
        permissions = [
            ('manage_access_policy', 'Puede administrar políticas de acceso'),
            ('issue_purchased_entitlements', 'Puede registrar derechos comprados verificados'),
        ]
        verbose_name = 'Configuración de acceso'
        verbose_name_plural = 'Configuraciones de acceso'

    def clean(self):
        from django.core.exceptions import ValidationError

        if not isinstance(self.configuration, dict):
            raise ValidationError({'configuration': 'La configuración debe ser un objeto JSON.'})
        if self.key == 'access':
            rules = self.configuration.get('rules', {})
            supported_rules = {'access_plan', 'view_plan_content'}
            if not isinstance(rules, dict) or set(rules) - supported_rules:
                raise ValidationError({'configuration': 'La política contiene reglas de acceso no reconocidas.'})
            for rule in rules.values():
                if not isinstance(rule, dict):
                    raise ValidationError({'configuration': 'Cada regla debe ser un objeto JSON.'})
                if 'enabled' in rule and not isinstance(rule['enabled'], bool):
                    raise ValidationError({'configuration': 'enabled debe ser booleano.'})
                if rule.get('minimum_plan') not in {None, 'free', 'plus', 'pro'}:
                    raise ValidationError({'configuration': 'minimum_plan debe ser FREE, PLUS o PRO.'})
        previous = None
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
        if previous:
            if previous.status != self.Status.DRAFT and (
                previous.key != self.key
                or previous.version != self.version
                or previous.configuration != self.configuration
            ):
                raise ValidationError('Solo se puede editar una configuración en borrador.')
            allowed_transitions = {
                self.Status.DRAFT: {self.Status.DRAFT, self.Status.APPROVED},
                self.Status.APPROVED: {self.Status.APPROVED, self.Status.ACTIVE, self.Status.DRAFT},
                self.Status.ACTIVE: {self.Status.ACTIVE, self.Status.RETIRED},
                self.Status.RETIRED: {self.Status.RETIRED},
            }
            if self.status not in allowed_transitions[previous.status]:
                raise ValidationError({'status': 'La configuración no puede volver a un estado anterior.'})
            if self.status == self.Status.DRAFT and previous.status == self.Status.APPROVED:
                self.approved_by = None
        elif self.status != self.Status.DRAFT:
            raise ValidationError({'status': 'Una configuración nueva debe crearse como borrador.'})
        if self.status in {self.Status.APPROVED, self.Status.ACTIVE} and self.approved_by_id is None:
            raise ValidationError({'approved_by': 'La configuración requiere aprobación explícita.'})
        if self.status == self.Status.ACTIVE and self.approved_by_id is None:
            raise ValidationError({'approved_by': 'Una configuración debe aprobarse antes de activarse.'})
        if self.key.startswith('econom') and self.status == self.Status.ACTIVE and not self.approved_by_id:
            raise ValidationError({'status': 'La configuración económica requiere aprobación explícita.'})

    def __str__(self):
        return f'{self.key} v{self.version} — {self.get_status_display()}'


class AccessAuditEvent(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='access_audit_events',
    )
    action = models.CharField(max_length=40)
    target_type = models.CharField(max_length=80)
    target_id = models.CharField(max_length=80, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['target_type', 'target_id', 'created_at'], name='users_access_audit_target')]
        verbose_name = 'Evento de auditoría de acceso'
        verbose_name_plural = 'Eventos de auditoría de acceso'

    def __str__(self):
        return f'{self.action} — {self.target_type}#{self.target_id}'


class Notificacion(models.Model):
    TIPO_CHOICES = [
        ('logro', 'Logro'),
        ('insignia', 'Insignia'),
        ('mision', 'Misión'),
        ('recompensa', 'Recompensa'),
        ('certificado', 'Certificado'),
        ('sistema', 'Sistema'),
        ('recordatorio', 'Recordatorio'),
    ]

    usuario = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='notificaciones'
    )
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    titulo = models.CharField(max_length=200)
    mensaje = models.TextField()
    leida = models.BooleanField(default=False)
    creada_en = models.DateTimeField(auto_now_add=True)
    lms_notification = models.OneToOneField(
        'lms.Notification', null=True, blank=True, on_delete=models.CASCADE,
        related_name='platform_notification',
    )

    class Meta:
        verbose_name = 'Notificación'
        verbose_name_plural = 'Notificaciones'
        ordering = ['-creada_en']

    def __str__(self):
        return f'{self.titulo} — {self.usuario.username}'

    @property
    def dias_antiguedad(self) -> int:
        """Full days elapsed since notification creation."""
        return max((timezone.now().date() - self.creada_en.date()).days, 0)


from .billing_models import (  # noqa: E402
    AdministrativeAccessGrant,
    BillingConfiguration,
    BillingEvent,
    BillingOrder,
    BillingOrderLine,
    BillingSubscription,
    Charge,
    ConfirmedPayment,
    Invoice,
    MembershipContract,
    PaymentAttempt,
    PaymentProviderConfiguration,
    PromotionalVoucher,
    Refund,
    ServiceProduct,
    UserVoucher,
)
from .dashboard_models import PersonalGoal  # noqa: E402
