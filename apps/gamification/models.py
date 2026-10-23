"""Models for the gamification app."""
import uuid

from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone


class Insignia(models.Model):
    class Category(models.TextChoices):
        FORMATION = 'formation', 'Formación'
        SOFT_SKILLS = 'soft_skills', 'Soft skills'
        HARD_SKILLS = 'hard_skills', 'Hard skills'
        GAMES = 'games', 'Juegos y retos'
        PARTICIPATION = 'participation', 'Participación'
        COMMUNITY = 'community', 'Contribuciones a la comunidad'

    class IssueMethod(models.TextChoices):
        AUTOMATIC = 'automatic', 'Automática'
        MANUAL = 'manual', 'Manual'

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Borrador'
        REVIEW = 'review', 'Pendiente de revisión'
        PUBLISHED = 'published', 'Publicada'
        ARCHIVED = 'archived', 'Archivada'

    TIPO_CHOICES = [
        ('curso', 'Completar Curso'),
        ('tema', 'Completar Tema'),
        ('streak', 'Racha'),
        ('puntos', 'Puntos Acumulados'),
        ('mision', 'Completar Misión'),
        ('especial', 'Especial'),
    ]

    nombre = models.CharField(max_length=100)
    code = models.UUIDField(default=uuid.uuid4, null=True, blank=True, editable=False)
    descripcion = models.TextField()
    imagen = models.ImageField(upload_to='insignias/', null=True, blank=True)
    texto_alternativo = models.CharField(max_length=160, blank=True)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.FORMATION)
    issuer_name = models.CharField(max_length=160, default='Distintivo interno de la plataforma')
    organization = models.ForeignKey(
        'users.Organization',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='badges',
    )
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    requisito_valor = models.PositiveIntegerField(
        default=0,
        help_text='Valor numérico del requisito (p.ej. racha de 7 días, 500 puntos, etc.)'
    )
    curso_objetivo = models.ForeignKey(
        'courses.Curso',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='insignias_asociadas',
        help_text='Curso específico que activa esta insignia (opcional).',
    )
    tema_objetivo = models.ForeignKey(
        'courses.Tema',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='insignias_asociadas',
        help_text='Tema específico que activa esta insignia (opcional).',
    )
    visible = models.BooleanField(default=True, help_text='Falso = insignia oculta/sorpresa')
    issue_method = models.CharField(
        max_length=12,
        choices=IssueMethod.choices,
        default=IssueMethod.AUTOMATIC,
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_badges',
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_badges',
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Insignia'
        verbose_name_plural = 'Insignias'
        ordering = ['tipo', 'requisito_valor']
        constraints = [
            models.UniqueConstraint(fields=['code'], name='gamification_unique_badge_code'),
        ]

    def clean(self):
        super().clean()

        if self.tipo == 'curso':
            if not self.curso_objetivo_id:
                raise ValidationError({'curso_objetivo': 'Para insignias de tipo curso, debes seleccionar un curso objetivo.'})
            if self.tema_objetivo_id:
                raise ValidationError({'tema_objetivo': 'Para insignias de tipo curso, no debes seleccionar tema objetivo.'})

        elif self.tipo == 'tema':
            if not self.tema_objetivo_id:
                raise ValidationError({'tema_objetivo': 'Para insignias de tipo tema, debes seleccionar un tema objetivo.'})
            if self.curso_objetivo_id:
                raise ValidationError({'curso_objetivo': 'Para insignias de tipo tema, no debes seleccionar curso objetivo.'})

        else:
            if self.curso_objetivo_id:
                raise ValidationError({'curso_objetivo': 'Este tipo de insignia no admite curso objetivo.'})
            if self.tema_objetivo_id:
                raise ValidationError({'tema_objetivo': 'Este tipo de insignia no admite tema objetivo.'})

        if self.status == self.Status.PUBLISHED and not self.visible:
            return
        if self.organization_id and self.issuer_name == 'Notodoescodigo.com':
            raise ValidationError({'issuer_name': 'Una organización no puede atribuirse el emisor de la plataforma.'})

    def save(self, *args, **kwargs):
        if not self.texto_alternativo and self.imagen:
            self.texto_alternativo = f'Insignia: {self.nombre}'
        super().save(*args, **kwargs)

    def __str__(self):
        if self.tema_objetivo_id:
            return f'{self.nombre} (tema: {self.tema_objetivo.titulo})'
        if self.curso_objetivo_id:
            return f'{self.nombre} (curso: {self.curso_objetivo.nombre})'
        return self.nombre


class InsigniaVersion(models.Model):
    class RuleType(models.TextChoices):
        COURSE_COMPLETED = 'course_completed', 'Completar un curso'
        TOPIC_COMPLETED = 'topic_completed', 'Completar un tema'
        QUIZ_PASSED = 'quiz_passed', 'Aprobar una evaluación'
        TOPICS_COUNT = 'topics_count', 'Completar un número de temas'
        STREAK_DAYS = 'streak_days', 'Mantener una racha'
        TOTAL_POINTS = 'total_points', 'Acumular puntos'

    badge = models.ForeignKey(Insignia, on_delete=models.CASCADE, related_name='versions')
    version = models.PositiveIntegerField()
    rule_type = models.CharField(max_length=24, choices=RuleType.choices)
    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='badge_requirement_versions',
    )
    topic = models.ForeignKey(
        'courses.Tema',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='badge_requirement_versions',
    )
    quiz = models.ForeignKey(
        'courses.Quiz',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='badge_requirement_versions',
    )
    threshold = models.PositiveIntegerField(default=1)
    public_requirements = models.CharField(max_length=300)
    source_description = models.CharField(max_length=160)
    verification_description = models.CharField(max_length=160)
    effective_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_badge_versions',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['badge_id', '-version']
        constraints = [
            models.UniqueConstraint(
                fields=['badge', 'version'],
                name='gamification_unique_badge_version',
            ),
        ]

    def clean(self):
        if self.rule_type == self.RuleType.COURSE_COMPLETED and not self.course_id:
            raise ValidationError({'course': 'Selecciona el curso que debe completarse.'})
        if self.rule_type == self.RuleType.TOPIC_COMPLETED and not self.topic_id:
            raise ValidationError({'topic': 'Selecciona el tema que debe completarse.'})
        if self.rule_type == self.RuleType.QUIZ_PASSED and not self.quiz_id:
            raise ValidationError({'quiz': 'Selecciona la evaluación que debe aprobarse.'})
        if self.rule_type != self.RuleType.COURSE_COMPLETED and self.course_id:
            raise ValidationError({'course': 'Esta regla no utiliza un curso objetivo.'})
        if self.rule_type != self.RuleType.TOPIC_COMPLETED and self.topic_id:
            raise ValidationError({'topic': 'Esta regla no utiliza un tema objetivo.'})
        if self.rule_type != self.RuleType.QUIZ_PASSED and self.quiz_id:
            raise ValidationError({'quiz': 'Esta regla no utiliza una evaluación objetivo.'})
        if self.threshold < 1:
            raise ValidationError({'threshold': 'El requisito debe ser al menos uno.'})

    def __str__(self):
        return f'{self.badge} — v{self.version}'


class InsigniaUsuario(models.Model):
    class Origin(models.TextChoices):
        AUTOMATIC = 'automatic', 'Evento automático'
        MANUAL = 'manual', 'Concesión manual'
        HISTORICAL = 'historical', 'Revisión histórica'

    class Status(models.TextChoices):
        VALID = 'valid', 'Vigente'
        REVOKED = 'revoked', 'Revocada'
        SUPERSEDED = 'superseded', 'Sustituida'

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='insignias_obtenidas'
    )
    insignia = models.ForeignKey(Insignia, on_delete=models.CASCADE)
    fecha_obtenida = models.DateTimeField(auto_now_add=True)
    version = models.ForeignKey(
        InsigniaVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='awards',
    )
    achieved_at = models.DateTimeField(null=True, blank=True)
    origin = models.CharField(max_length=12, choices=Origin.choices, default=Origin.HISTORICAL)
    evidence_type = models.CharField(max_length=40, blank=True)
    evidence_id = models.CharField(max_length=80, blank=True)
    issuer_name = models.CharField(max_length=160, blank=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='granted_badges',
    )
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.VALID)
    is_public = models.BooleanField(default=False)
    idempotency_key = models.UUIDField(default=uuid.uuid4, null=True, blank=True, editable=False)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='revoked_badges',
    )
    revocation_reason = models.TextField(blank=True)

    class Meta:
        unique_together = ('usuario', 'insignia')
        verbose_name = 'Insignia de Usuario'
        verbose_name_plural = 'Insignias de Usuarios'
        ordering = ['-fecha_obtenida']
        constraints = [
            models.UniqueConstraint(
                fields=['idempotency_key'],
                name='gamification_unique_badge_idempotency',
            ),
        ]

    def __str__(self):
        return f'{self.usuario.username} — {self.insignia.nombre}'


class InsigniaAuditEvent(models.Model):
    class Action(models.TextChoices):
        CREATED = 'created', 'Creada'
        UPDATED = 'updated', 'Actualizada'
        PUBLISHED = 'published', 'Publicada'
        ARCHIVED = 'archived', 'Archivada'
        GRANTED = 'granted', 'Concedida'
        REVOKED = 'revoked', 'Revocada'
        RESTORED = 'restored', 'Restaurada'
        VISIBILITY_CHANGED = 'visibility_changed', 'Visibilidad modificada'

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='badge_audit_events',
    )
    badge = models.ForeignKey(
        Insignia,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_events',
    )
    award = models.ForeignKey(
        InsigniaUsuario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_events',
    )
    action = models.CharField(max_length=24, choices=Action.choices)
    reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']

    def __str__(self):
        return f'{self.get_action_display()} — {self.created_at:%Y-%m-%d %H:%M}'


class Logro(models.Model):
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField()
    puntos_recompensa = models.PositiveIntegerField(default=50)
    oculto = models.BooleanField(default=False)
    insignia = models.ForeignKey(
        Insignia, on_delete=models.SET_NULL, null=True, blank=True, related_name='logros'
    )

    class Meta:
        verbose_name = 'Logro'
        verbose_name_plural = 'Logros'

    def __str__(self):
        return self.nombre


class LogroUsuario(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='logros_obtenidos'
    )
    logro = models.ForeignKey(Logro, on_delete=models.CASCADE)
    puntos_asignados = models.PositiveIntegerField(default=0)
    fecha_obtenido = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('usuario', 'logro')
        verbose_name = 'Logro de Usuario'
        verbose_name_plural = 'Logros de Usuarios'

    def __str__(self):
        return f'{self.usuario.username} — {self.logro.nombre}'


class Mision(models.Model):
    TIPO_CHOICES = [
        ('diaria', 'Diaria'),
        ('semanal', 'Semanal'),
    ]
    REQUISITO_CHOICES = [
        ('completar_temas', 'Completar temas'),
        ('quiz_aprobado', 'Aprobar quizzes'),
        ('racha_dias', 'Mantener racha de días'),
        ('puntos_ganados', 'Ganar puntos'),
    ]

    titulo = models.CharField(max_length=100)
    descripcion = models.TextField()
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    puntos_recompensa = models.IntegerField(default=30)
    requisito_tipo = models.CharField(max_length=30, choices=REQUISITO_CHOICES)
    requisito_cantidad = models.PositiveIntegerField(default=1)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Misión'
        verbose_name_plural = 'Misiones'

    def __str__(self):
        return f'[{self.tipo}] {self.titulo}'


class MisionUsuario(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='misiones'
    )
    mision = models.ForeignKey(Mision, on_delete=models.CASCADE, related_name='asignaciones')
    progreso = models.PositiveIntegerField(default=0)
    completada = models.BooleanField(default=False)
    fecha_completada = models.DateTimeField(null=True, blank=True)
    fecha_asignada = models.DateField(auto_now_add=True)

    class Meta:
        verbose_name = 'Misión de Usuario'
        verbose_name_plural = 'Misiones de Usuarios'
        ordering = ['completada', '-fecha_asignada']

    def __str__(self):
        return f'{self.usuario.username} — {self.mision.titulo}'

    def avanzar(self, cantidad=1):
        self.progreso = min(self.progreso + cantidad, self.mision.requisito_cantidad)
        if self.progreso >= self.mision.requisito_cantidad and not self.completada:
            self.completada = True
            self.fecha_completada = timezone.now()
            self.save()
            # Award points
            self.usuario.agregar_puntos(self.mision.puntos_recompensa)
            return True  # Newly completed
        self.save(update_fields=['progreso'])
        return False


class RankingDefinition(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Borrador'
        PUBLISHED = 'published', 'Publicado'
        ARCHIVED = 'archived', 'Archivado'

    class Metric(models.TextChoices):
        COMPLETED_COURSES = 'completed_courses', 'Cursos completados'
        COMPLETED_LESSONS = 'completed_lessons', 'Lecciones completadas'

    class TiePolicy(models.TextChoices):
        SHARED = 'shared', 'Posiciones compartidas por puntuación igual'

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField()
    metric = models.CharField(max_length=32, choices=Metric.choices)
    unit = models.CharField(max_length=40)
    scope = models.CharField(max_length=12, default='public', editable=False)
    tie_policy = models.CharField(
        max_length=12,
        choices=TiePolicy.choices,
        default=TiePolicy.SHARED,
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_rankings',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'pk']
        permissions = [('manage_ranking_definitions', 'Puede gestionar definiciones y temporadas de rankings')]

    def clean(self):
        if self.scope != 'public':
            raise ValidationError({'scope': 'Solo se ha implementado el ámbito público.'})
        if not self.unit.strip():
            raise ValidationError({'unit': 'Define la unidad de la clasificación.'})
        if self.pk and self.rule_versions.exists():
            original_metric = type(self).objects.filter(pk=self.pk).values_list('metric', flat=True).first()
            if original_metric and original_metric != self.metric:
                raise ValidationError({'metric': 'La métrica no se puede cambiar después de versionar sus reglas.'})
        if self.status == self.Status.PUBLISHED and (
            not self.pk or not self.rule_versions.exists()
        ):
            raise ValidationError({'status': 'Publica una definición solo después de crear su regla versionada.'})

    def __str__(self):
        return self.name


class RankingRuleVersion(models.Model):
    FORMULAS = {
        RankingDefinition.Metric.COMPLETED_COURSES:
            'Cuenta matrículas completadas durante la temporada; cada curso inscrito aporta como máximo un registro.',
        RankingDefinition.Metric.COMPLETED_LESSONS:
            'Cuenta temas completados durante la temporada; cada tema del usuario aporta como máximo un registro.',
    }

    ranking = models.ForeignKey(
        RankingDefinition,
        on_delete=models.CASCADE,
        related_name='rule_versions',
    )
    version = models.PositiveIntegerField()
    metric = models.CharField(max_length=32, choices=RankingDefinition.Metric.choices)
    formula_description = models.CharField(max_length=300, blank=True)
    selection_policy = models.CharField(
        max_length=20,
        default='period_count',
        editable=False,
    )
    tie_policy = models.CharField(
        max_length=12,
        choices=RankingDefinition.TiePolicy.choices,
        default=RankingDefinition.TiePolicy.SHARED,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_ranking_rule_versions',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['ranking_id', '-version']
        constraints = [
            models.UniqueConstraint(
                fields=['ranking', 'version'],
                name='gamification_unique_ranking_rule_version',
            ),
        ]

    def clean(self):
        if self.ranking_id and self.metric != self.ranking.metric:
            raise ValidationError({'metric': 'La regla debe usar la métrica definida por el ranking.'})
        expected_formula = self.FORMULAS.get(self.metric)
        if expected_formula is None:
            raise ValidationError({'metric': 'No hay una fórmula verificable habilitada para esta métrica.'})
        if self.formula_description and self.formula_description != expected_formula:
            raise ValidationError({'formula_description': 'La fórmula debe coincidir con la fuente y métrica implementadas.'})
        self.formula_description = expected_formula
        if self.tie_policy != RankingDefinition.TiePolicy.SHARED:
            raise ValidationError({'tie_policy': 'La única política habilitada asigna posiciones compartidas.'})
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and (
                previous.ranking_id != self.ranking_id
                or previous.version != self.version
                or previous.metric != self.metric
                or previous.formula_description != self.formula_description
                or previous.tie_policy != self.tie_policy
            ):
                raise ValidationError('Las versiones de reglas existentes son inmutables.')

    def __str__(self):
        return f'{self.ranking} — v{self.version}'


class RankingSeason(models.Model):
    class Status(models.TextChoices):
        PREPARED = 'prepared', 'Preparada'
        ACTIVE = 'active', 'Activa'
        CLOSED = 'closed', 'Cerrada'
        ARCHIVED = 'archived', 'Archivada'

    ranking = models.ForeignKey(
        RankingDefinition,
        on_delete=models.PROTECT,
        related_name='seasons',
    )
    rule_version = models.ForeignKey(
        RankingRuleVersion,
        on_delete=models.PROTECT,
        related_name='seasons',
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=120)
    starts_on = models.DateField()
    ends_on = models.DateField()
    timezone_name = models.CharField(max_length=64, default='Europe/Madrid')
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PREPARED)
    calculated_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_ranking_seasons',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-starts_on', 'name']
        constraints = [
            models.UniqueConstraint(fields=['ranking', 'name'], name='gamification_unique_ranking_season'),
        ]

    def clean(self):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValidationError({'ends_on': 'La temporada debe terminar el mismo día o después de su inicio.'})
        try:
            ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValidationError({'timezone_name': 'Selecciona una zona horaria IANA válida.'})
        if self.rule_version_id and self.ranking_id and self.rule_version.ranking_id != self.ranking_id:
            raise ValidationError({'rule_version': 'La regla debe pertenecer a este ranking.'})
        if self.status in {self.Status.ACTIVE, self.Status.CLOSED, self.Status.ARCHIVED}:
            if not self.rule_version_id:
                raise ValidationError({'rule_version': 'Selecciona la versión de reglas de la temporada.'})
            if self.status == self.Status.CLOSED and not self.closed_at:
                raise ValidationError({'closed_at': 'La temporada cerrada necesita fecha de cierre.'})

    def __str__(self):
        return f'{self.ranking} — {self.name}'


class RankingParticipation(models.Model):
    ranking = models.ForeignKey(
        RankingDefinition,
        on_delete=models.CASCADE,
        related_name='participations',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='ranking_participations',
    )
    alias = models.CharField(max_length=40)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['ranking', 'user'], name='gamification_unique_ranking_participation'),
        ]
        indexes = [models.Index(fields=['ranking', 'is_active'], name='gamification_rank_particip')]

    def clean(self):
        if not self.alias.strip():
            raise ValidationError({'alias': 'El alias público es obligatorio.'})
        if self.ranking_id and self.ranking.status != RankingDefinition.Status.PUBLISHED:
            raise ValidationError({'ranking': 'Solo se puede participar en un ranking publicado.'})

    def __str__(self):
        return f'{self.alias} — {self.ranking}'


class RankingEntry(models.Model):
    season = models.ForeignKey(
        RankingSeason,
        on_delete=models.PROTECT,
        related_name='entries',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='ranking_entries',
    )
    alias = models.CharField(max_length=40)
    score = models.PositiveIntegerField()
    position = models.PositiveIntegerField()
    source_signature = models.CharField(max_length=64)
    calculated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['position', 'alias']
        constraints = [
            models.UniqueConstraint(fields=['season', 'user'], name='gamification_unique_ranking_entry'),
        ]

    def __str__(self):
        return f'{self.position}. {self.alias} — {self.score}'


class RankingAuditEvent(models.Model):
    class Action(models.TextChoices):
        PARTICIPATION_ENABLED = 'participation_enabled', 'Participación activada'
        PARTICIPATION_DISABLED = 'participation_disabled', 'Participación retirada'
        ALIAS_CHANGED = 'alias_changed', 'Alias modificado'
        SEASON_ACTIVATED = 'season_activated', 'Temporada activada'
        SEASON_CLOSED = 'season_closed', 'Temporada cerrada'
        SEASON_RECALCULATED = 'season_recalculated', 'Temporada recalculada'
        RESULT_REVIEWED = 'result_reviewed', 'Resultado revisado'

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='ranking_audit_events',
    )
    ranking = models.ForeignKey(
        RankingDefinition,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_events',
    )
    season = models.ForeignKey(
        RankingSeason,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_events',
    )
    action = models.CharField(max_length=32, choices=Action.choices)
    reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']


class RankingAdjustment(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendiente de revisión'
        VALID = 'valid', 'Válido'
        EXCLUDED = 'excluded', 'Excluido'

    source_model = models.CharField(max_length=80)
    source_record_id = models.CharField(max_length=80)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    reason = models.TextField()
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_ranking_adjustments',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['source_model', 'source_record_id'],
                name='gamification_unique_ranking_adjustment_source',
            ),
        ]
        ordering = ['status', '-created_at']

    def clean(self):
        if not self.reason.strip():
            raise ValidationError({'reason': 'El motivo de revisión es obligatorio.'})
        if self.status == self.Status.PENDING:
            if self.reviewed_by_id or self.reviewed_at:
                raise ValidationError('Una incidencia pendiente todavía no puede tener revisión.')
        elif not self.reviewed_by_id or not self.reviewed_at:
            raise ValidationError('Una incidencia revisada requiere responsable y fecha.')
        supported_sources = {
            'courses.InscripcionCurso': ('courses', 'InscripcionCurso'),
            'courses.Progreso': ('courses', 'Progreso'),
        }
        source = supported_sources.get(self.source_model)
        if not source:
            raise ValidationError({'source_model': 'Esta fuente no está habilitada para revisión de rankings.'})
        from django.apps import apps

        model = apps.get_model(*source)
        if not model.objects.filter(pk=self.source_record_id, completado=True).exists():
            raise ValidationError({'source_record_id': 'No se encontró un resultado completado verificable.'})

    def __str__(self):
        return f'{self.source_model} #{self.source_record_id} — {self.get_status_display()}'
