import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.ducky_arenas.models import BattleChallenge
from .rules import default_healing_windows, default_items


class ValidatedModel(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ImmutableModel(ValidatedModel):
    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        if not self._state.adding:
            previous = type(self).objects.get(pk=self.pk)
            if any(
                getattr(previous, field.attname) != getattr(self, field.attname)
                for field in self._meta.concrete_fields
            ):
                raise ValidationError('Este registro es inmutable. Crea una nueva versión.')


class Challenge(BattleChallenge):
    class Meta:
        proxy = True
        verbose_name = 'Reto DuckyClash'
        verbose_name_plural = 'Retos DuckyClash'


class ChallengeSubcategory(ValidatedModel):
    category = models.ForeignKey('courses.CourseCategory', on_delete=models.PROTECT, related_name='clash_subcategories')
    name = models.CharField('nombre', max_length=120)
    active = models.BooleanField('activa', default=True)

    class Meta:
        ordering = ['category__nombre', 'name']
        constraints = [
            models.UniqueConstraint(fields=['category', 'name'], name='clash_subcategory_unique'),
        ]

    def __str__(self):
        return self.name

    @property
    def sector(self):
        return self.category.sector


class Landscape(ValidatedModel):
    class Era(models.TextChoices):
        PRE = 'PRE', 'Prehistoria'
        GRE = 'GRE', 'Grecia'
        ROM = 'ROM', 'Roma'
        MED = 'MED', 'Medieval'
        REN = 'REN', 'Renacimiento'
        REV = 'REV', 'Revolución industrial'
        SXX = 'SXX', 'Siglo XX'
        ACT = 'ACT', 'Actualidad'

    code = models.SlugField('código', unique=True)
    name = models.CharField('nombre', max_length=120)
    image = models.ImageField('imagen', upload_to='duckyclash/landscapes/', blank=True)
    era = models.CharField('época', max_length=3, choices=Era.choices, blank=True, default='')
    active = models.BooleanField('activo', default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class ChallengeOffer(ImmutableModel):
    challenge = models.ForeignKey(BattleChallenge, on_delete=models.PROTECT, related_name='clash_offers')
    version = models.PositiveIntegerField('versión')
    proposed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='clash_offers')
    category = models.ForeignKey('courses.CourseCategory', on_delete=models.PROTECT)
    subcategory = models.ForeignKey(ChallengeSubcategory, on_delete=models.PROTECT, null=True, blank=True)
    landscape = models.ForeignKey(Landscape, on_delete=models.PROTECT)
    scheduled_at = models.DateTimeField('inicio programado')
    time_zone = models.CharField('zona horaria', max_length=64, default='Europe/Madrid')
    expires_at = models.DateTimeField('vencimiento')
    stake = models.PositiveIntegerField('DuckyCoins por jugador')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['challenge_id', '-version']
        constraints = [
            models.UniqueConstraint(fields=['challenge', 'version'], name='clash_offer_version_unique'),
            models.CheckConstraint(check=models.Q(version__gte=1), name='clash_offer_version_positive'),
            models.CheckConstraint(check=models.Q(stake__gte=1, stake__lte=1000000), name='clash_offer_stake_range'),
            models.CheckConstraint(check=models.Q(expires_at__lt=models.F('scheduled_at')), name='clash_offer_expiry_before_start'),
        ]
        indexes = [models.Index(fields=['scheduled_at', 'expires_at'], name='clash_offer_schedule')]

    def clean(self):
        super().clean()
        errors = {}
        if self.challenge_id and self.proposed_by_id not in (self.challenge.creator_id, self.challenge.opponent_id):
            errors['proposed_by'] = 'Solo los participantes pueden proponer condiciones.'
        if self.subcategory_id and self.subcategory.category_id != self.category_id:
            errors['subcategory'] = 'La subcategoría no pertenece a esta categoría.'
        try:
            ZoneInfo(self.time_zone)
        except (ZoneInfoNotFoundError, ValueError):
            errors['time_zone'] = 'Selecciona una zona horaria IANA válida.'
        for field in ('scheduled_at', 'expires_at'):
            value = getattr(self, field)
            if value and timezone.is_naive(value):
                errors[field] = 'La fecha debe incluir zona horaria.'
            elif value and self._state.adding and value <= timezone.now():
                errors[field] = 'La fecha debe ser futura.'
        if (self.scheduled_at and self.expires_at
                and timezone.is_aware(self.scheduled_at) and timezone.is_aware(self.expires_at)
                and self.expires_at >= self.scheduled_at):
            errors['expires_at'] = 'El vencimiento debe ser anterior al duelo.'
        if self._state.adding:
            if self.category_id and not self.category.activa:
                errors['category'] = 'La categoría está inactiva.'
            if self.subcategory_id and not self.subcategory.active:
                errors['subcategory'] = 'La subcategoría está inactiva.'
            if self.landscape_id and not self.landscape.active:
                errors['landscape'] = 'El paisaje está inactivo.'
        if errors:
            raise ValidationError(errors)


class ChallengeAgreement(ImmutableModel):
    challenge = models.OneToOneField(BattleChallenge, on_delete=models.PROTECT, related_name='clash_agreement')
    offer = models.OneToOneField(ChallengeOffer, on_delete=models.PROTECT, related_name='agreement')
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='clash_agreements_created')
    opponent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='clash_agreements_received')
    accepted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.CheckConstraint(check=~models.Q(creator=models.F('opponent')), name='clash_agreement_distinct_players'),
        ]

    def clean(self):
        super().clean()
        if self.creator_id == self.opponent_id:
            raise ValidationError('No puedes retarte a ti mismo.')
        if self.challenge_id:
            if (self.creator_id, self.opponent_id) != (self.challenge.creator_id, self.challenge.opponent_id):
                raise ValidationError('Los jugadores no coinciden con los participantes del reto.')
            if self.challenge.status != BattleChallenge.Status.ACCEPTED:
                raise ValidationError('El reto debe estar aceptado.')
        if self.offer_id:
            if self.offer.challenge_id != self.challenge_id:
                raise ValidationError('La oferta pertenece a otro reto.')
            if self._state.adding:
                if self.offer.expires_at <= timezone.now():
                    raise ValidationError('La oferta ha vencido.')
                latest = ChallengeOffer.objects.filter(challenge_id=self.challenge_id).order_by('-version').first()
                if latest is None or latest.pk != self.offer_id:
                    raise ValidationError('Solo se puede aceptar la última versión de la oferta.')


class Duel(ValidatedModel):
    class BankMode(models.TextChoices):
        RESERVE = 'reserve', 'Reservar ambos participantes'
        CANCEL = 'cancel', 'Cancelar y liberar'
        DRAW = 'draw', 'Empate con devolución'
        PAYOUT = 'payout', 'Pagar al ganador'

    class Status(models.TextChoices):
        PENDING_RESERVATION = 'pending_reservation', 'Pendiente de reservas'
        SCHEDULED = 'scheduled', 'Programado'
        READY = 'ready', 'Preparado'
        IN_PROGRESS = 'in_progress', 'En curso'
        SETTLEMENT_PENDING = 'settlement_pending', 'Pendiente de liquidación'
        FINISHED = 'finished', 'Finalizado'
        CANCELED = 'canceled', 'Cancelado'
        NO_SHOW = 'no_show', 'Ausencia'

    agreement = models.OneToOneField(ChallengeAgreement, on_delete=models.PROTECT, related_name='duel')
    session_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING_RESERVATION)
    initial_hp = models.PositiveIntegerField(default=100)
    rules_version = models.CharField(max_length=64, default='foundation-v1')
    rules_snapshot = models.JSONField(default=dict, blank=True, editable=False)
    revision = models.PositiveIntegerField(default=0)
    bank_mode = models.CharField(max_length=12, choices=BankMode.choices, default=BankMode.RESERVE)
    bank_reason = models.CharField(max_length=200, blank=True)
    payment_gateway = models.CharField(max_length=255, blank=True, editable=False)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(initial_hp__gte=1), name='clash_duel_initial_hp_positive'),
            models.CheckConstraint(
                check=models.Q(finished_at__isnull=True) | models.Q(started_at__isnull=True) |
                models.Q(finished_at__gte=models.F('started_at')),
                name='clash_duel_finish_after_start',
            ),
        ]
        indexes = [models.Index(fields=['status', 'created_at'], name='clash_duel_status')]

    def save(self, *args, **kwargs):
        if self._state.adding and not self.rules_snapshot:
            from .rules import current_rules
            self.rules_snapshot = current_rules()
            self.rules_snapshot['initial_hp'] = self.initial_hp
            self.rules_version = self.rules_snapshot['version']
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if not self._state.adding:
            previous = type(self).objects.get(pk=self.pk)
            if (previous.rules_snapshot != self.rules_snapshot
                    or previous.initial_hp != self.initial_hp or previous.rules_version != self.rules_version
                    or previous.payment_gateway != self.payment_gateway):
                raise ValidationError('Las reglas y HP iniciales de un duelo son inmutables.')
        # Activation is owned by bank/engine services, never a generic model save.
        if self.status not in (self.Status.PENDING_RESERVATION, self.Status.CANCELED):
            raise ValidationError({'status': 'La activación solo puede realizarse mediante los servicios bancarios y DuelEngine.'})
        if self.started_at or self.finished_at:
            raise ValidationError('Las fechas del combate solo las modifican los servicios autoritativos.')


class ParticipantState(ValidatedModel):
    duel = models.ForeignKey(Duel, on_delete=models.PROTECT, related_name='participants')
    player = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='clash_participations')
    seat = models.PositiveSmallIntegerField()
    max_hp = models.PositiveIntegerField(default=100)
    hp = models.PositiveIntegerField(default=100)
    ready_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    online = models.BooleanField(default=False)
    stats_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['duel', 'player'], name='clash_participant_unique'),
            models.UniqueConstraint(fields=['duel', 'seat'], name='clash_seat_unique'),
            models.CheckConstraint(check=models.Q(seat__in=[1, 2]), name='clash_seat_range'),
            models.CheckConstraint(check=models.Q(max_hp__gte=1, hp__gte=0, hp__lte=models.F('max_hp')), name='clash_hp_bounds'),
        ]

    def clean(self):
        super().clean()
        if self.duel_id and self.player_id:
            expected = {1: self.duel.agreement.creator_id, 2: self.duel.agreement.opponent_id}
            if expected.get(self.seat) != self.player_id:
                raise ValidationError('El jugador no ocupa esta posición en el acuerdo.')


class DuelQuestion(ImmutableModel):
    duel = models.ForeignKey(Duel, on_delete=models.PROTECT, related_name='questions')
    number = models.PositiveIntegerField()
    version = models.ForeignKey('lms.QuestionVersion', on_delete=models.PROTECT, related_name='clash_questions', null=True, blank=True)
    provider = models.CharField(max_length=80, blank=True)
    source_id = models.CharField(max_length=200, blank=True)
    source_version = models.CharField(max_length=100, blank=True)
    opens_at = models.DateTimeField()
    closes_at = models.DateTimeField()
    snapshot = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['duel', 'number'], name='clash_question_number_unique'),
            models.UniqueConstraint(
                fields=['duel', 'provider', 'source_id'], condition=~models.Q(source_id=''),
                name='clash_question_source_once',
            ),
            models.CheckConstraint(check=models.Q(number__gte=1), name='clash_question_number_positive'),
            models.CheckConstraint(check=models.Q(closes_at__gt=models.F('opens_at')), name='clash_question_time_order'),
        ]

    def clean(self):
        super().clean()
        if self.opens_at and self.closes_at:
            if timezone.is_naive(self.opens_at) or timezone.is_naive(self.closes_at):
                raise ValidationError('Las fechas deben incluir zona horaria.')
            if self.opens_at >= self.closes_at:
                raise ValidationError('La pregunta debe cerrar después de abrirse.')
        if self._state.adding and self.version_id and not self.version.is_published:
            raise ValidationError('La versión de la pregunta debe estar publicada.')


class DuelAnswer(ImmutableModel):
    question = models.ForeignKey(DuelQuestion, on_delete=models.PROTECT, related_name='answers')
    participant = models.ForeignKey(ParticipantState, on_delete=models.PROTECT, related_name='answers')
    answer = models.JSONField()
    received_at = models.DateTimeField(default=timezone.now, editable=False)
    is_correct = models.BooleanField(null=True, blank=True, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['question', 'participant'], name='clash_answer_once'),
        ]

    def clean(self):
        super().clean()
        if self.question_id and self.participant_id:
            if self.question.duel_id != self.participant.duel_id:
                raise ValidationError('El jugador no participa en esta pregunta.')
            if self._state.adding:
                from .questions import effective_deadline
                options = DuelQuestionOptions.objects.filter(
                    question_id=self.question_id, participant_id=self.participant_id,
                ).first()
                deadline = effective_deadline(self.question, options) if options else self.question.closes_at
                now = timezone.now()
                if (not self.question.opens_at <= now < deadline
                        or (options and options.hidden_until and options.hidden_until > now)):
                    raise ValidationError('La pregunta no admite respuestas en este momento.')
        if self._state.adding:
            self.received_at = timezone.now()


class SubcategoryQuestionSource(ValidatedModel):
    subcategory = models.OneToOneField(
        ChallengeSubcategory, on_delete=models.PROTECT, related_name='question_sources', verbose_name='subcategoría',
    )
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, editable=False)
    active = models.BooleanField(
        'activa', default=True,
        help_text='Desactivar impide nuevas selecciones; no cambia las preguntas ya presentadas.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'fuente de preguntas por subcategoría'
        verbose_name_plural = 'fuentes de preguntas por subcategoría'
        ordering = ['subcategory_id']

    def __str__(self):
        return str(self.subcategory)

    def clean(self):
        super().clean()
        if self.approved_by_id and not self.approved_by.is_active:
            raise ValidationError('La fuente requiere un editor activo.')


class SubcategoryQuestionLevel(ValidatedModel):
    source = models.ForeignKey(
        SubcategoryQuestionSource, on_delete=models.CASCADE, related_name='levels', verbose_name='fuente',
    )
    reference = models.CharField('referencia', max_length=120, help_text='Por ejemplo: 1/5, 2/5 o Avanzado.')
    bank = models.ForeignKey(
        'lms.QuestionBank', on_delete=models.PROTECT, related_name='clash_levels', verbose_name='banco del LMS',
    )
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, editable=False)
    active = models.BooleanField('activo', default=True)

    class Meta:
        verbose_name = 'nivel de preguntas'
        verbose_name_plural = 'niveles de preguntas'
        ordering = ['pk']
        constraints = [
            models.UniqueConstraint(
                fields=['source', 'reference'], name='clash_level_source_reference',
            ),
            models.UniqueConstraint(
                fields=['source', 'bank'], name='clash_level_source_bank',
            ),
        ]

    def __str__(self):
        return f'{self.reference}: {self.bank}'

    def clean(self):
        super().clean()
        from apps.lms.policies import can_edit_course
        if self.bank_id and self.approved_by_id:
            if not self.approved_by.is_active or not can_edit_course(self.approved_by, self.bank.course):
                raise ValidationError('El nivel requiere autorización de un editor activo del curso de origen.')


class KnowledgePublication(ValidatedModel):
    version = models.OneToOneField('lms.QuestionVersion', on_delete=models.PROTECT, related_name='clash_publication')
    category = models.ForeignKey('courses.CourseCategory', on_delete=models.PROTECT)
    subcategory = models.ForeignKey(ChallengeSubcategory, on_delete=models.PROTECT, null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()
        from apps.lms.knowledge import question_version_initial
        from apps.lms.models import Question, QuestionBank
        from apps.lms.policies import can_edit_course

        if self.subcategory_id and self.subcategory.category_id != self.category_id:
            raise ValidationError('La subcategoría no pertenece a la categoría.')
        if self.version_id:
            version = self.version
            question_version_initial(version)
            if (not version.is_published or version.question.status != Question.Status.PUBLISHED
                    or version.question.bank.status != QuestionBank.Status.PUBLISHED
                    or version.question.question_type != Question.Type.SINGLE_CHOICE):
                raise ValidationError('Solo se pueden compartir versiones publicadas de opción única.')
            if self.approved_by_id and not can_edit_course(self.approved_by, version.question.bank.course):
                raise ValidationError('La publicación requiere permiso de edición del banco de origen.')


class DuelQuestionOptions(ValidatedModel):
    question = models.ForeignKey(DuelQuestion, on_delete=models.PROTECT, related_name='player_options')
    participant = models.ForeignKey(ParticipantState, on_delete=models.PROTECT)
    options = models.JSONField(default=list)
    evidence = models.JSONField(default=list, blank=True)
    deadline = models.DateTimeField(null=True, blank=True)
    hidden_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['question', 'participant'], name='clash_player_question_options'),
        ]

    def clean(self):
        super().clean()
        if self.question_id and self.participant_id and self.question.duel_id != self.participant.duel_id:
            raise ValidationError('Las opciones no corresponden a este participante.')


class DuelItemUse(ImmutableModel):
    question = models.ForeignKey(DuelQuestion, on_delete=models.PROTECT, related_name='item_uses')
    participant = models.ForeignKey(ParticipantState, on_delete=models.PROTECT, related_name='item_uses')
    item = models.ForeignKey('ducky_arenas.ArenaItem', on_delete=models.PROTECT)
    operation_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    action_id = models.UUIDField(null=True, blank=True)
    consumption_reference = models.CharField(max_length=200, unique=True)
    effect_snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['participant', 'action_id'], condition=models.Q(action_id__isnull=False),
                name='clash_item_action_once',
            ),
        ]

    def clean(self):
        super().clean()
        if self.question_id and self.participant_id and self.question.duel_id != self.participant.duel_id:
            raise ValidationError('El objeto se intenta usar en otro duelo.')
        if self._state.adding and self.participant_id and self.item_id:
            from apps.ducky_arenas.models import UserArenaInventory
            if not UserArenaInventory.objects.filter(
                profile__user_id=self.participant.player_id, item_id=self.item_id, quantity__gt=0,
            ).exists():
                raise ValidationError('El jugador no posee este objeto.')


class RoundResolution(ImmutableModel):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        APPLIED = 'applied', 'Aplicada'

    question = models.OneToOneField(DuelQuestion, on_delete=models.PROTECT, related_name='damage_resolution')
    operation_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    gateway = models.CharField(max_length=200, blank=True)
    request_payload = models.JSONField()
    response_payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_error = models.CharField(max_length=100, blank=True)
    outcome = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)


class HealthChange(ImmutableModel):
    participant = models.ForeignKey(ParticipantState, on_delete=models.PROTECT, related_name='health_changes')
    question = models.ForeignKey(DuelQuestion, on_delete=models.PROTECT)
    source_key = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, choices=[
        ('damage', 'Daño'), ('healing', 'Curación'), ('tiebreak', 'Reinicio de desempate'),
    ])
    before = models.PositiveIntegerField()
    after = models.PositiveIntegerField()
    requested = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['participant', 'source_key'], name='clash_health_change_once'),
        ]

    def clean(self):
        super().clean()
        if self.participant_id and self.question_id:
            if self.participant.duel_id != self.question.duel_id:
                raise ValidationError('El cambio de HP pertenece a otro duelo.')
            if max(self.before, self.after) > min(self.participant.max_hp, self.participant.duel.initial_hp):
                raise ValidationError('El cambio de HP excede el máximo inicial.')


class CombatSession(ValidatedModel):
    class Phase(models.TextChoices):
        WAITING_PLAYERS = 'waiting_players', 'Esperando jugadores'
        READY = 'ready', 'Ambos preparados'
        COUNTDOWN = 'countdown', 'Cuenta atrás'
        QUESTION_ACTIVE = 'question_active', 'Pregunta activa'
        ROUND_RESOLVING = 'round_resolving', 'Resolviendo ronda'
        ROUND_RESULT = 'round_result', 'Resultado de ronda'
        FINISHED = 'finished', 'Combate finalizado'

    duel = models.OneToOneField(Duel, on_delete=models.PROTECT, related_name='engine')
    phase = models.CharField(max_length=24, choices=Phase.choices, default=Phase.WAITING_PLAYERS)
    question = models.ForeignKey(DuelQuestion, on_delete=models.PROTECT, null=True, blank=True)
    deadline = models.DateTimeField(null=True, blank=True)
    rules = models.JSONField(default=dict)
    last_error = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()
        if self.question_id and self.question.duel_id != self.duel_id:
            raise ValidationError('La ronda del motor pertenece a otro duelo.')


class DuelEvent(ImmutableModel):
    duel = models.ForeignKey(Duel, on_delete=models.PROTECT, related_name='events')
    sequence = models.PositiveIntegerField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    kind = models.CharField(max_length=80)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sequence']
        constraints = [
            models.UniqueConstraint(fields=['duel', 'sequence'], name='clash_event_sequence_unique'),
            models.CheckConstraint(check=models.Q(sequence__gte=1), name='clash_event_sequence_positive'),
        ]

    def clean(self):
        super().clean()
        if self.duel_id and self.actor_id not in (None, self.duel.agreement.creator_id, self.duel.agreement.opponent_id):
            raise ValidationError('El actor no participa en este duelo.')


class EconomicOperation(ValidatedModel):
    class Kind(models.TextChoices):
        RESERVE = 'reserve', 'Reserva'
        RELEASE = 'release', 'Liberación'
        SETTLE = 'settle', 'Liquidación'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        CONFIRMED = 'confirmed', 'Confirmada'
        UNCERTAIN = 'uncertain', 'Resultado incierto'
        FAILED = 'failed', 'Fallida'

    duel = models.ForeignKey(Duel, on_delete=models.PROTECT, related_name='economic_operations')
    player = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    amount = models.PositiveIntegerField()
    operation_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    bank_reference = models.CharField(max_length=200, blank=True)
    last_error = models.TextField(blank=True)
    request_payload = models.JSONField(default=dict, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    reconciliation_attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    lease_token = models.UUIDField(null=True, blank=True, editable=False)
    lease_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(amount__gte=1), name='clash_operation_amount_positive'),
            models.CheckConstraint(
                check=models.Q(kind='settle', player__isnull=True) |
                models.Q(kind__in=['reserve', 'release'], player__isnull=False),
                name='clash_operation_player_kind',
            ),
            models.UniqueConstraint(fields=['duel', 'player', 'kind'], name='clash_player_operation_unique'),
            models.UniqueConstraint(fields=['duel'], condition=models.Q(kind='settle'), name='clash_settlement_once'),
            models.CheckConstraint(
                check=~models.Q(status='confirmed') | ~models.Q(bank_reference=''),
                name='clash_confirmed_bank_reference',
            ),
        ]
        indexes = [models.Index(fields=['status', 'kind'], name='clash_operation_status')]

    def clean(self):
        super().clean()
        if not self._state.adding:
            previous = type(self).objects.get(pk=self.pk)
            if any(getattr(previous, name) != getattr(self, name) for name in (
                'duel_id', 'player_id', 'kind', 'amount', 'operation_key', 'request_payload',
            )):
                raise ValidationError('La identidad y las condiciones de una operación bancaria son inmutables.')
        if self.duel_id:
            agreement = self.duel.agreement
            if self.kind == self.Kind.SETTLE:
                if self.player_id is not None or self.amount != agreement.offer.stake * 2:
                    raise ValidationError('La liquidación debe referirse al bote completo y no a un jugador.')
            elif self.player_id not in (agreement.creator_id, agreement.opponent_id) or self.amount != agreement.offer.stake:
                raise ValidationError('La operación debe coincidir con el jugador y la apuesta acordada.')
        if self.status == self.Status.CONFIRMED and not self.bank_reference.strip():
            raise ValidationError('Una operación confirmada necesita referencia bancaria.')


class LocalBankReceipt(models.Model):
    class HoldState(models.TextChoices):
        HELD = 'held', 'Reservada'
        RELEASED = 'released', 'Devuelta'
        SETTLED = 'settled', 'Liquidada'

    operation = models.OneToOneField(EconomicOperation, on_delete=models.PROTECT, related_name='local_receipt')
    request_payload = models.JSONField()
    outcome = models.CharField(max_length=12, choices=[('confirmed', 'Confirmada'), ('rejected', 'Rechazada')])
    hold_state = models.CharField(max_length=12, choices=HoldState.choices, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(outcome='confirmed') | models.Q(outcome='rejected', hold_state=''),
                name='clash_local_receipt_outcome',
            ),
        ]


class DuelResolution(ImmutableModel):
    duel = models.OneToOneField(Duel, on_delete=models.PROTECT, related_name='resolution')
    winner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    is_draw = models.BooleanField(default=False)
    reason = models.CharField(max_length=200)
    resolved_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(is_draw=True, winner__isnull=True) | models.Q(is_draw=False, winner__isnull=False),
                name='clash_resolution_winner_or_draw',
            ),
        ]

    def clean(self):
        super().clean()
        if self.duel_id and self.winner_id not in (None, self.duel.agreement.creator_id, self.duel.agreement.opponent_id):
            raise ValidationError('El ganador no participa en el duelo.')
        if self.duel_id and self.duel.status not in (Duel.Status.SETTLEMENT_PENDING, Duel.Status.FINISHED):
            raise ValidationError('El duelo no está preparado para resolverse.')


class RuleConfiguration(ValidatedModel):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    version = models.UUIDField(default=uuid.uuid4, editable=False)
    initial_hp = models.PositiveIntegerField('HP iniciales', default=100)
    question_seconds = models.PositiveIntegerField('segundos por pregunta', default=30)
    result_seconds = models.PositiveIntegerField('segundos de resultado', default=3)
    presence_seconds = models.PositiveIntegerField('segundos para detectar desconexión', default=20)
    waiting_seconds = models.PositiveIntegerField('plazo de preparación/incomparecencia', default=300)
    max_rounds = models.PositiveIntegerField('máximo de rondas', default=20)
    max_stake = models.PositiveIntegerField('máxima apuesta por jugador', default=1000000)
    min_expiry_seconds = models.PositiveIntegerField('caducidad mínima (segundos)', default=1)
    max_expiry_seconds = models.PositiveIntegerField('caducidad máxima (segundos)', default=2592000)
    reminder_seconds = models.PositiveIntegerField('aviso previo al inicio (segundos)', default=300)
    ring_access = models.CharField('acceso al Ring', max_length=16, default='scheduled', choices=[
        ('scheduled', 'Desde la fecha pactada, con reservas'), ('disabled', 'Deshabilitado para nuevos duelos'),
    ])
    no_show = models.CharField('incomparecencia', max_length=16, default='refund', choices=[
        ('refund', 'Cancelar y devolver; sin premio ni penalización'),
    ])
    abandonment = models.CharField('abandono/desconexión', max_length=16, default='wait', choices=[
        ('wait', 'Permitir reconexión; los relojes siguen'),
        ('refund', 'Cancelar y devolver tras ausencia continuada'),
    ])
    abandonment_seconds = models.PositiveIntegerField('ausencia continuada antes de devolución', default=300)
    double_ko = models.CharField('doble KO', max_length=16, default='draw', choices=[
        ('draw', 'Empate con devolución'), ('tiebreak', 'Desempate con reinicio de HP'),
    ])
    max_tiebreaks = models.PositiveSmallIntegerField('máximo de desempates', default=1)
    healing_windows = models.JSONField('ventanas del Botiquín', default=default_healing_windows)
    item_rules = models.JSONField('valores, usos y cooldown de los seis objetos', default=default_items)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuración de DuckyClash'
        verbose_name_plural = 'Configuración de DuckyClash'
        permissions = [('manage_rules', 'Administrar reglas auditadas de DuckyClash')]
        constraints = [models.CheckConstraint(check=models.Q(id=1), name='clash_rules_singleton')]

    def clean(self):
        super().clean()
        from .rules import validate_items
        for name, lower, upper in (
            ('initial_hp', 1, 10000), ('question_seconds', 5, 300), ('result_seconds', 1, 30),
            ('presence_seconds', 10, 120), ('waiting_seconds', 30, 3600), ('max_rounds', 1, 100),
            ('max_stake', 1, 1000000), ('min_expiry_seconds', 1, 2592000),
            ('max_expiry_seconds', 60, 2592000), ('reminder_seconds', 1, 86400),
            ('abandonment_seconds', 30, 3600), ('max_tiebreaks', 0, 10),
        ):
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper:
                raise ValidationError({name: 'Valor fuera de los límites admitidos.'})
        if self.min_expiry_seconds > self.max_expiry_seconds:
            raise ValidationError({'max_expiry_seconds': 'Debe ser mayor o igual a la caducidad mínima.'})
        if (self.min_expiry_seconds + 59) // 60 > self.max_expiry_seconds // 60:
            raise ValidationError({'max_expiry_seconds': 'El intervalo debe admitir al menos un plazo entero en minutos para los retos privados.'})
        if (not isinstance(self.healing_windows, list) or not self.healing_windows
                or any(window not in ('countdown', 'question_active') for window in self.healing_windows)):
            raise ValidationError({'healing_windows': 'Usa countdown y/o question_active.'})
        try:
            validate_items(self.item_rules)
        except ValidationError as exc:
            raise ValidationError({'item_rules': exc.messages}) from exc

    def snapshot(self):
        from copy import deepcopy
        return {
            **{name: getattr(self, name) for name in (
                'initial_hp', 'max_stake', 'min_expiry_seconds', 'max_expiry_seconds',
                'reminder_seconds', 'ring_access', 'no_show', 'abandonment',
                'abandonment_seconds', 'double_ko', 'max_tiebreaks',
            )},
            'version': str(self.version), 'items': deepcopy(self.item_rules),
            'engine': {
                **{name: getattr(self, name) for name in (
                    'question_seconds', 'result_seconds', 'presence_seconds', 'waiting_seconds', 'max_rounds',
                )}, 'countdown_seconds': 3, 'healing_windows': deepcopy(self.healing_windows),
            },
        }

    def save(self, *args, **kwargs):
        self.version = uuid.uuid4()
        if kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = set(kwargs['update_fields']) | {'version', 'updated_at'}
        return super().save(*args, **kwargs)

    def __str__(self):
        return 'Reglas para nuevas ofertas y duelos'


class NotificationReceipt(models.Model):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    key = models.CharField(max_length=160)
    notification = models.OneToOneField('users.Notificacion', on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['recipient', 'key'], name='clash_notice_once')]
        verbose_name = 'Entrega de aviso Clash'


class ProfileResultUpdate(models.Model):
    resolution = models.OneToOneField(DuelResolution, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Actualización del perfil por resultado'
