from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .config import EquipmentSlot


class UserProfileArenas(models.Model):
    class AvatarType(models.TextChoices):
        PREHISTORIC = 'PREHISTORIC', 'Prehistórico'
        ROMAN = 'ROMAN', 'Romano'
        MILITARY = 'MILITARY', 'Militar'
        GAMER = 'GAMER', 'Gamer'
        DEVELOPER = 'DEVELOPER', 'Developer'
        CYBERSECURITY = 'CYBERSECURITY', 'Cyberseguridad'
        SYSTEMS = 'SYSTEMS', 'Systems'

    class EvolutionStage(models.TextChoices):
        BASE = 'BASE', 'Base'
        ROOKIE = 'ROOKIE', 'Rookie'
        ELITE = 'ELITE', 'Elite'
        LEGENDARY = 'LEGENDARY', 'Legendary'

    class ArenaRank(models.TextChoices):
        UNRANKED = 'UNRANKED', 'Unranked'
        BRONZE = 'BRONZE', 'Bronze'
        SILVER = 'SILVER', 'Silver'
        GOLD = 'GOLD', 'Gold'
        PLATINUM = 'PLATINUM', 'Platinum'
        DIAMOND = 'DIAMOND', 'Diamond'
        LEGENDARY = 'LEGENDARY', 'Legendary'

    class Gender(models.TextChoices):
        BOY = 'BOY', 'Chico'
        GIRL = 'GIRL', 'Chica'

    class ExplorationDirection(models.TextChoices):
        IDLE = 'idle', 'Quieto'
        UP = 'up', 'Arriba'
        DOWN = 'down', 'Abajo'
        LEFT = 'left', 'Izquierda'
        RIGHT = 'right', 'Derecha'

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='user_profile_arenas',
        unique=True,
    )
    nickname = models.CharField(max_length=30)
    level = models.PositiveIntegerField(default=1)
    xp = models.PositiveIntegerField(default=0)
    prestigeLevel = models.PositiveIntegerField(default=0)
    avatarType = models.CharField(
        max_length=32,
        choices=AvatarType.choices,
        default=AvatarType.GAMER,
    )
    gender = models.CharField(
        max_length=10,
        choices=Gender.choices,
        default=Gender.BOY,
    )
    evolutionStage = models.CharField(
        max_length=16,
        choices=EvolutionStage.choices,
        default=EvolutionStage.BASE,
    )
    equipped = models.JSONField(default=dict, blank=True)
    stats = models.JSONField(default=dict, blank=True)
    duckCoins = models.PositiveIntegerField(default=0)
    arenaTokens = models.PositiveIntegerField(default=0)
    rating = models.PositiveIntegerField(default=1000)
    rank = models.CharField(
        max_length=16,
        choices=ArenaRank.choices,
        default=ArenaRank.UNRANKED,
    )
    gamesPlayed = models.PositiveIntegerField(default=0)
    wins = models.PositiveIntegerField(default=0)
    losses = models.PositiveIntegerField(default=0)
    draws = models.PositiveIntegerField(default=0)
    currentWinStreak = models.PositiveIntegerField(default=0)
    bestWinStreak = models.PositiveIntegerField(default=0)
    selectedBadgeId = models.PositiveIntegerField(null=True, blank=True)
    selectedFrameId = models.PositiveIntegerField(null=True, blank=True)
    activeSetId = models.PositiveIntegerField(null=True, blank=True)
    onboardingCompleted = models.BooleanField(default=False)
    onboardingCompletedAt = models.DateTimeField(null=True, blank=True)
    createdAt = models.DateTimeField(auto_now_add=True)
    updatedAt = models.DateTimeField(auto_now=True)
    lastPlayedAt = models.DateTimeField(null=True, blank=True)
    selected_character = models.ForeignKey(
        'CharacterAsset',
        on_delete=models.SET_NULL,
        related_name='selected_by_profiles',
        null=True,
        blank=True,
    )
    current_map = models.ForeignKey(
        'WorldMap',
        on_delete=models.SET_NULL,
        related_name='active_profiles',
        null=True,
        blank=True,
    )
    position_x = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    position_y = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    facing_direction = models.CharField(
        max_length=8,
        choices=ExplorationDirection.choices,
        default=ExplorationDirection.DOWN,
    )
    exploration_preferences = models.JSONField(default=dict, blank=True)
    last_access = models.DateTimeField(null=True, blank=True)
    exploration_version = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = 'UserProfileArenas'
        verbose_name_plural = 'UserProfilesArenas'
        indexes = [models.Index(fields=['user'], name='ducky_user_profile_unique')]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(position_x__isnull=True, position_y__isnull=True)
                    | models.Q(position_x__isnull=False, position_y__isnull=False)
                ),
                name='arena_profile_position_pair',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(position_x__isnull=True)
                    | models.Q(position_x__gte=0)
                ),
                name='arena_profile_x_bounds',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(position_y__isnull=True)
                    | models.Q(position_y__gte=0)
                ),
                name='arena_profile_y_bounds',
            ),
        ]

    def __str__(self):
        return f'{self.nickname} ({self.user.username})'

    def clean(self):
        super().clean()
        if self.current_map_id:
            if self.position_x is not None and self.position_x > self.current_map.width:
                raise ValidationError({'position_x': 'La posición X está fuera del mapa actual.'})
            if self.position_y is not None and self.position_y > self.current_map.height:
                raise ValidationError({'position_y': 'La posición Y está fuera del mapa actual.'})
        if (
            self.current_map_id
            and self.selected_character_id
            and self.current_map.era_id
            and self.current_map.era_id != self.selected_character.era_id
        ):
            raise ValidationError({
                'selected_character': 'El personaje seleccionado no pertenece a la época del mapa actual.',
            })

    @property
    def userId(self):
        return self.user_id

    @property
    def current_era(self):
        if self.current_map_id is None:
            return None
        return self.current_map.era


class HistoricalEra(models.Model):
    slug = models.SlugField(max_length=48, unique=True)
    code = models.CharField(max_length=8, unique=True)
    name = models.CharField(max_length=80)
    order = models.PositiveSmallIntegerField(unique=True)
    description = models.TextField(blank=True)
    required_xp = models.PositiveIntegerField(default=0)
    unlock_requirements = models.JSONField(default=dict, blank=True)
    resource_config = models.JSONField(default=dict, blank=True)
    is_available = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order']
        verbose_name = 'Época histórica'
        verbose_name_plural = 'Épocas históricas'

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if not isinstance(self.unlock_requirements, dict):
            raise ValidationError({'unlock_requirements': 'Los requisitos deben ser un objeto JSON.'})
        if not isinstance(self.resource_config, dict):
            raise ValidationError({'resource_config': 'La configuración de recursos debe ser un objeto JSON.'})


class WorldMap(models.Model):
    era = models.ForeignKey(
        HistoricalEra,
        on_delete=models.PROTECT,
        related_name='maps',
        null=True,
        blank=True,
    )
    code = models.CharField(max_length=24, unique=True)
    name = models.CharField(max_length=100)
    static_path = models.CharField(max_length=255)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    spawn_x = models.DecimalField(max_digits=7, decimal_places=6, null=True, blank=True)
    spawn_y = models.DecimalField(max_digits=7, decimal_places=6, null=True, blank=True)
    is_available = models.BooleanField(default=False)
    collision_config = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['era__order', 'name']
        constraints = [
            models.CheckConstraint(check=models.Q(width__gt=0), name='arena_map_width_positive'),
            models.CheckConstraint(check=models.Q(height__gt=0), name='arena_map_height_positive'),
            models.CheckConstraint(
                check=(
                    models.Q(spawn_x__isnull=True, spawn_y__isnull=True)
                    | models.Q(spawn_x__isnull=False, spawn_y__isnull=False)
                ),
                name='arena_map_spawn_pair',
            ),
            models.CheckConstraint(
                check=models.Q(spawn_x__isnull=True) | models.Q(spawn_x__gte=0, spawn_x__lte=1),
                name='arena_map_spawn_x_bounds',
            ),
            models.CheckConstraint(
                check=models.Q(spawn_y__isnull=True) | models.Q(spawn_y__gte=0, spawn_y__lte=1),
                name='arena_map_spawn_y_bounds',
            ),
        ]
        indexes = [models.Index(fields=['era', 'is_available'], name='arena_map_era_avail')]
        verbose_name = 'Mapa del mundo'
        verbose_name_plural = 'Mapas del mundo'

    def __str__(self):
        era_name = self.era.code if self.era_id else 'Mapa genérico'
        return f'{era_name}: {self.name}'

    def clean(self):
        super().clean()
        if not isinstance(self.collision_config, list):
            raise ValidationError({'collision_config': 'Las zonas de colisión deben ser una lista.'})


class GameDestination(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Próximamente'
        AVAILABLE = 'available', 'Disponible'
        DISABLED = 'disabled', 'Deshabilitado'

    class EntryBehavior(models.TextChoices):
        SAME_TAB = 'same_tab', 'Misma pestaña'
        NEW_TAB = 'new_tab', 'Nueva pestaña'

    class ReturnBehavior(models.TextChoices):
        CORE_CHECKPOINT = 'core_checkpoint', 'Volver al punto guardado en el Core'

    code = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    owner_app = models.CharField(max_length=64)
    entry_url_name = models.CharField(max_length=128, blank=True, default='')
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    access_requirements = models.JSONField(default=dict, blank=True)
    icon_path = models.CharField(max_length=255, blank=True, default='')
    entry_behavior = models.CharField(
        max_length=16,
        choices=EntryBehavior.choices,
        default=EntryBehavior.SAME_TAB,
    )
    return_behavior = models.CharField(
        max_length=32,
        choices=ReturnBehavior.choices,
        default=ReturnBehavior.CORE_CHECKPOINT,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Destino de videojuego'
        verbose_name_plural = 'Destinos de videojuegos'
        indexes = [models.Index(fields=['status', 'owner_app'], name='arena_dest_status_app')]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if not isinstance(self.access_requirements, dict):
            raise ValidationError({
                'access_requirements': 'Los requisitos deben ser un objeto JSON.',
            })
        if self.status == self.Status.AVAILABLE and not self.entry_url_name:
            raise ValidationError({
                'entry_url_name': 'Un destino disponible requiere una ruta Django.',
            })
        if self.entry_url_name:
            namespace, separator, _route = self.entry_url_name.partition(':')
            if not separator or namespace != self.owner_app:
                raise ValidationError({
                    'entry_url_name': 'La ruta debe pertenecer a la aplicación responsable.',
                })


class Portal(models.Model):
    world_map = models.ForeignKey(WorldMap, on_delete=models.CASCADE, related_name='portals')
    code = models.SlugField(max_length=64)
    name = models.CharField(max_length=100)
    destination = models.ForeignKey(
        GameDestination,
        on_delete=models.PROTECT,
        related_name='portals',
    )
    x = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    y = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    area_width = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    area_height = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    return_x = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    return_y = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    message = models.CharField(max_length=240, blank=True)
    access_requirements = models.JSONField(default=dict, blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['world_map__era__order', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['world_map', 'code'],
                name='arena_portal_map_code_uniq',
            ),
            models.CheckConstraint(
                check=(models.Q(x__isnull=True, y__isnull=True) | models.Q(x__isnull=False, y__isnull=False)),
                name='arena_portal_position_pair',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(area_width__isnull=True, area_height__isnull=True)
                    | models.Q(area_width__isnull=False, area_height__isnull=False)
                ),
                name='arena_portal_area_pair',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(x__isnull=True, y__isnull=True)
                    | models.Q(x__gte=0, y__gte=0)
                ),
                name='arena_portal_position_nonnegative',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(area_width__isnull=True, area_height__isnull=True)
                    | models.Q(area_width__gt=0, area_height__gt=0)
                ),
                name='arena_portal_area_positive',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(return_x__isnull=True, return_y__isnull=True)
                    | models.Q(return_x__gte=0, return_y__gte=0)
                ),
                name='arena_portal_return_nonnegative',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(is_active=False)
                    | models.Q(
                        x__isnull=False,
                        y__isnull=False,
                        area_width__isnull=False,
                        area_height__isnull=False,
                    )
                ),
                name='arena_active_portal_geometry',
            ),
        ]
        indexes = [
            models.Index(fields=['world_map', 'is_active'], name='arena_portal_map_active'),
            models.Index(fields=['destination', 'is_active'], name='arena_portal_dest_active'),
        ]
        verbose_name = 'Portal'
        verbose_name_plural = 'Portales'

    def __str__(self):
        return f'{self.name} ({self.world_map.code})'

    def clean(self):
        super().clean()
        if not isinstance(self.access_requirements, dict):
            raise ValidationError({
                'access_requirements': 'Los requisitos deben ser un objeto JSON.',
            })
        if self.destination_id:
            if self.is_active and self.destination.status != GameDestination.Status.AVAILABLE:
                raise ValidationError({
                    'is_active': 'El destino debe estar disponible antes de activar el portal.',
                })
            coordinates = (self.x, self.y, self.area_width, self.area_height)
            if (
                (self.x is not None and self.x > self.world_map.width)
                or (self.y is not None and self.y > self.world_map.height)
            ):
                raise ValidationError({
                    'x': 'La posición del portal debe estar dentro de los límites del mapa.',
                })
            if self.is_active and any(value is None for value in coordinates):
                raise ValidationError({
                    'is_active': 'Un portal activo requiere coordenadas y un área de interacción.',
                })
            if all(value is not None for value in coordinates):
                if (
                    self.x + self.area_width > self.world_map.width
                    or self.y + self.area_height > self.world_map.height
                ):
                    raise ValidationError({
                        'area_width': 'El área del portal debe estar dentro de los límites del mapa.',
                    })
            if (self.return_x is None) != (self.return_y is None):
                raise ValidationError({
                    'return_x': 'El punto de retorno debe incluir ambas coordenadas.',
                })
            if self.return_x is not None and (
                self.return_x < 0 or self.return_y < 0
                or self.return_x > self.world_map.width
                or self.return_y > self.world_map.height
            ):
                raise ValidationError({
                    'return_x': 'El punto de retorno debe estar dentro del mapa.',
                })


class Profession(models.Model):
    ACCESS_REQUIREMENT_KEYS = ('min_xp',)
    ATTRIBUTE_CODES = (
        'attack',
        'defense',
        'speed',
        'intelligence',
        'logic',
        'quack',
    )
    ATTRIBUTE_PRIORITIES = (
        'low',
        'low_medium',
        'medium',
        'medium_high',
        'high',
        'very_high',
    )

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    attribute_recommendations = models.JSONField(default=dict, blank=True)
    access_requirements = models.JSONField(default=dict, blank=True)
    icon_path = models.CharField(max_length=255, blank=True, default='')
    display_order = models.PositiveSmallIntegerField(default=0)
    is_available = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name = 'Profesión'
        verbose_name_plural = 'Profesiones'

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if not isinstance(self.access_requirements, dict):
            raise ValidationError({'access_requirements': 'Los requisitos de acceso deben ser un objeto JSON.'})
        unsupported_requirements = (
            set(self.access_requirements) - set(self.ACCESS_REQUIREMENT_KEYS)
        )
        if unsupported_requirements:
            raise ValidationError({
                'access_requirements': (
                    'Los requisitos de acceso no son compatibles: '
                    f'{", ".join(sorted(unsupported_requirements))}.'
                ),
            })
        minimum_xp = self.access_requirements.get('min_xp', 0)
        if type(minimum_xp) is not int or minimum_xp < 0:
            raise ValidationError({
                'access_requirements': 'min_xp debe ser un entero no negativo.',
            })
        if not isinstance(self.attribute_recommendations, dict):
            raise ValidationError({
                'attribute_recommendations': 'Las recomendaciones deben ser un objeto JSON.'
            })
        invalid_attributes = set(self.attribute_recommendations) - set(self.ATTRIBUTE_CODES)
        if invalid_attributes:
            raise ValidationError({
                'attribute_recommendations': (
                    'Las recomendaciones contienen atributos desconocidos: '
                    f'{", ".join(sorted(invalid_attributes))}.'
                ),
            })
        invalid_priorities = {
            code: priority
            for code, priority in self.attribute_recommendations.items()
            if priority not in self.ATTRIBUTE_PRIORITIES
        }
        if invalid_priorities:
            raise ValidationError({
                'attribute_recommendations': (
                    'Cada prioridad debe ser low, low_medium, medium, medium_high, '
                    'high o very_high.'
                ),
            })


class ProfessionSelectionPolicy(models.Model):
    key = models.CharField(max_length=32, primary_key=True, default='default', editable=False)
    allow_reassignment = models.BooleanField(default=False)
    free_reassignment_count = models.PositiveSmallIntegerField(default=0)
    require_ecomotor_confirmation = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Política de selección profesional'
        verbose_name_plural = 'Políticas de selección profesional'

    def __str__(self):
        return 'Política de profesiones'

    @classmethod
    def get_current(cls):
        policy, _ = cls.objects.get_or_create(key='default')
        return policy


class ProfessionAssignment(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Borrador'
        PENDING = 'pending', 'Pendiente de EcoMotor'
        CONFIRMED = 'confirmed', 'Confirmada'
        FAILED = 'failed', 'Fallida'

    profile = models.OneToOneField(
        UserProfileArenas,
        on_delete=models.CASCADE,
        related_name='profession_assignment',
    )
    profession = models.ForeignKey(
        Profession,
        on_delete=models.PROTECT,
        related_name='assignments',
    )
    operation_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    provider_error = models.TextField(blank=True, default='')
    reassignments_used = models.PositiveSmallIntegerField(default=0)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        verbose_name = 'Selección profesional'
        verbose_name_plural = 'Selecciones profesionales'
        indexes = [
            models.Index(
                fields=['status', 'updated_at'],
                name='arena_prof_status_updated',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(status='confirmed', confirmed_at__isnull=False)
                    | models.Q(
                        ~models.Q(status='confirmed'),
                        confirmed_at__isnull=True,
                    )
                ),
                name='arena_prof_confirmed_at_state',
            ),
        ]

    def __str__(self):
        return f'{self.profession.name} — {self.profile.nickname}'

    def clean(self):
        super().clean()
        if self.status == self.Status.CONFIRMED and self.confirmed_at is None:
            raise ValidationError({
                'confirmed_at': 'Una selección solo puede confirmarse con confirmación oficial.',
            })
        if self.status != self.Status.CONFIRMED and self.confirmed_at is not None:
            raise ValidationError({
                'confirmed_at': 'Una selección no confirmada no puede tener fecha de confirmación.',
            })


class CharacterAsset(models.Model):
    class Variant(models.TextChoices):
        BOY = 'BOY', 'Chico'
        GIRL = 'GIRL', 'Chica'

    era = models.ForeignKey(
        HistoricalEra,
        on_delete=models.PROTECT,
        related_name='characters',
    )
    variant = models.CharField(max_length=8, choices=Variant.choices)
    name = models.CharField(max_length=100)
    static_image_path = models.CharField(max_length=255, blank=True, default='')
    movement_resources = models.JSONField(default=list, blank=True)
    is_available = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['era__order', 'variant']
        constraints = [
            models.UniqueConstraint(fields=['era', 'variant'], name='arena_character_era_variant_uniq'),
        ]
        indexes = [models.Index(fields=['era', 'is_available'], name='arena_char_era_available')]
        verbose_name = 'Recurso de personaje'
        verbose_name_plural = 'Recursos de personajes'

    def __str__(self):
        return f'{self.name} ({self.era.code})'

    def clean(self):
        super().clean()
        if not isinstance(self.movement_resources, list):
            raise ValidationError({'movement_resources': 'Los recursos de movimiento deben ser una lista.'})
        if not self.static_image_path and not self.movement_resources:
            raise ValidationError('El personaje debe incluir al menos un recurso gráfico.')


class PlayerEraProgress(models.Model):
    profile = models.ForeignKey(
        UserProfileArenas,
        on_delete=models.CASCADE,
        related_name='era_progress',
    )
    era = models.ForeignKey(
        HistoricalEra,
        on_delete=models.PROTECT,
        related_name='player_progress',
    )
    unlocked_at = models.DateTimeField(null=True, blank=True)
    evolution_notified_at = models.DateTimeField(null=True, blank=True)
    first_visited_at = models.DateTimeField(null=True, blank=True)
    last_visited_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['era__order']
        constraints = [
            models.UniqueConstraint(fields=['profile', 'era'], name='arena_player_era_uniq'),
            models.CheckConstraint(
                check=(
                    models.Q(first_visited_at__isnull=True, last_visited_at__isnull=True)
                    | models.Q(
                        unlocked_at__isnull=False,
                        first_visited_at__isnull=False,
                        last_visited_at__isnull=False,
                    )
                ),
                name='arena_progress_visit_requires_unlock',
            ),
        ]
        indexes = [models.Index(fields=['profile', 'unlocked_at'], name='arena_progress_unlocked')]
        verbose_name = 'Progreso de época'
        verbose_name_plural = 'Progreso de épocas'

    @property
    def is_unlocked(self):
        return self.unlocked_at is not None

    @property
    def is_visited(self):
        return self.last_visited_at is not None

    def __str__(self):
        return f'{self.profile.nickname}: {self.era.name}'


class PlayerMapCheckpoint(models.Model):
    profile = models.ForeignKey(
        UserProfileArenas,
        on_delete=models.CASCADE,
        related_name='map_checkpoints',
    )
    world_map = models.ForeignKey(
        WorldMap,
        on_delete=models.PROTECT,
        related_name='player_checkpoints',
    )
    position_x = models.DecimalField(max_digits=12, decimal_places=2)
    position_y = models.DecimalField(max_digits=12, decimal_places=2)
    facing_direction = models.CharField(
        max_length=8,
        choices=UserProfileArenas.ExplorationDirection.choices,
        default=UserProfileArenas.ExplorationDirection.DOWN,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['world_map__era__order', 'world_map__code']
        constraints = [
            models.UniqueConstraint(
                fields=['profile', 'world_map'],
                name='arena_map_checkpoint_profile_map_uniq',
            ),
            models.CheckConstraint(
                check=models.Q(position_x__gte=0, position_y__gte=0),
                name='arena_map_checkpoint_nonnegative',
            ),
        ]
        verbose_name = 'Punto guardado por mapa'
        verbose_name_plural = 'Puntos guardados por mapa'

    def __str__(self):
        return f'{self.profile.nickname}: {self.world_map.code}'

    def clean(self):
        super().clean()
        if self.world_map_id and (
            self.position_x > self.world_map.width
            or self.position_y > self.world_map.height
        ):
            raise ValidationError('La posición guardada está fuera de los límites del mapa.')


class ArenaItem(models.Model):
    class ArenaItemType(models.TextChoices):
        EQUIPMENT = 'EQUIPMENT', 'Equipamiento'
        COMPLEMENT = 'COMPLEMENT', 'Complemento'

    class Rarity(models.TextChoices):
        COMMON = 'COMMON', 'Común'
        RARE = 'RARE', 'Raro'
        EPIC = 'EPIC', 'Épico'
        LEGENDARY = 'LEGENDARY', 'Legendario'

    code = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    itemType = models.CharField(max_length=20, choices=ArenaItemType.choices, default=ArenaItemType.EQUIPMENT)
    slot = models.CharField(max_length=20, choices=EquipmentSlot.choices, blank=True, null=True)
    rarity = models.CharField(max_length=20, choices=Rarity.choices, default=Rarity.COMMON)
    avatarType = models.CharField(max_length=32, choices=UserProfileArenas.AvatarType.choices, blank=True, null=True)
    statBonuses = models.JSONField(default=dict, blank=True)
    icon = models.CharField(max_length=200, blank=True, default='')
    isActive = models.BooleanField(default=True)
    createdAt = models.DateTimeField(auto_now_add=True)
    updatedAt = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'ArenaItem'
        verbose_name_plural = 'ArenaItems'
        ordering = ['rarity', 'name']

    @property
    def is_main_equipment(self):
        return self.itemType == self.ArenaItemType.EQUIPMENT

    def __str__(self):
        return f'{self.name} ({self.itemType} / {self.slot or "complement"})'


class UserArenaInventory(models.Model):
    profile = models.ForeignKey(UserProfileArenas, on_delete=models.CASCADE, related_name='inventory_items')
    item = models.ForeignKey(ArenaItem, on_delete=models.CASCADE, related_name='inventory_entries')
    quantity = models.PositiveIntegerField(default=1)
    isEquipped = models.BooleanField(default=False)
    createdAt = models.DateTimeField(auto_now_add=True)
    updatedAt = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('profile', 'item')
        verbose_name = 'UserArenaInventory'
        verbose_name_plural = 'UserArenaInventories'

    def __str__(self):
        return f'{self.profile.nickname}: {self.item.name} x{self.quantity}'


class ArenaRewardTransaction(models.Model):
    class ArenaType(models.TextChoices):
        QUIZ = 'QUIZ', 'Quiz'
        BATTLE = 'BATTLE', 'Battle'
        CHALLENGE = 'CHALLENGE', 'Challenge'
        ESCAPE = 'ESCAPE', 'Escape'
        HACK = 'HACK', 'Hack'

    profile = models.ForeignKey(UserProfileArenas, on_delete=models.CASCADE, related_name='arena_reward_transactions')
    arenaType = models.CharField(max_length=20, choices=ArenaType.choices)
    sourceId = models.CharField(max_length=200)
    xpAwarded = models.PositiveIntegerField(default=0)
    duckCoinsAwarded = models.PositiveIntegerField(default=0)
    previousLevel = models.PositiveIntegerField(default=1)
    newLevel = models.PositiveIntegerField(default=1)
    createdAt = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['profile', 'arenaType', 'sourceId'], name='unique_reward_transaction_per_profile_arena_source'),
        ]
        verbose_name = 'ArenaRewardTransaction'
        verbose_name_plural = 'ArenaRewardTransactions'
        ordering = ['-createdAt']

    def __str__(self):
        return f'{self.profile.nickname} - {self.arenaType} - {self.sourceId}'


class BaseArena(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, max_length=120)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def __str__(self):
        return self.title


class QuizArena(BaseArena):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Borrador'
        OPEN = 'open', 'Abierto'
        CLOSED = 'closed', 'Cerrado'

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='quiz_arenas_owned',
    )
    participants = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='quiz_arenas',
        blank=True,
    )
    questions = models.JSONField(default=list, blank=True)
    duration_minutes = models.PositiveIntegerField(default=20)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        verbose_name = 'QuizArenas'
        verbose_name_plural = 'QuizArenas'


class BattleArena(BaseArena):
    class Status(models.TextChoices):
        WAITING = 'waiting', 'Esperando'
        IN_PROGRESS = 'in_progress', 'En curso'
        FINISHED = 'finished', 'Finalizado'

    player_one = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='battle_arenas_player_one',
    )
    player_two = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='battle_arenas_player_two',
    )
    winner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='battle_arenas_won',
    )
    challenge_data = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.WAITING)

    class Meta:
        verbose_name = 'BattleArenas'
        verbose_name_plural = 'BattleArenas'


class BattleChallenge(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        ACCEPTED = 'accepted', 'Aceptado · pendiente de reservas'
        DECLINED = 'declined', 'Rechazado'
        CANCELLED = 'cancelled', 'Cancelado'
        EXPIRED = 'expired', 'Caducado'

    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='battle_challenges_created',
    )
    opponent = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
        related_name='battle_challenges_received',
    )
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='battle_challenges_proposed',
    )
    is_public = models.BooleanField(default=False)
    stake = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    revision = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [
            models.CheckConstraint(
                check=~models.Q(creator=models.F('opponent')),
                name='battle_challenge_distinct_players',
            ),
            models.CheckConstraint(
                check=models.Q(opponent__isnull=False) | models.Q(is_public=True),
                name='battle_challenge_direct_opponent',
            ),
            models.CheckConstraint(
                check=models.Q(proposed_by=models.F('creator')) |
                models.Q(opponent__isnull=False, proposed_by=models.F('opponent')),
                name='battle_challenge_proposer_player',
            ),
            models.CheckConstraint(
                check=~models.Q(status='accepted') | models.Q(opponent__isnull=False),
                name='battle_challenge_accepted_rival',
            ),
            models.CheckConstraint(check=models.Q(stake__lte=1000000), name='battle_challenge_stake_limit'),
        ]
        indexes = [
            models.Index(fields=['status', 'expires_at'], name='battle_challenge_expiry'),
        ]

    @property
    def is_expired(self):
        return self.status == self.Status.EXPIRED or (
            self.status == self.Status.PENDING and self.expires_at <= timezone.now()
        )

    @property
    def display_status(self):
        return 'Caducado' if self.is_expired else self.get_status_display()


class ChallengeArena(BaseArena):
    class Difficulty(models.TextChoices):
        EASY = 'easy', 'Fácil'
        MEDIUM = 'medium', 'Media'
        HARD = 'hard', 'Difícil'

    class Category(models.TextChoices):
        LOGIC = 'logic', 'Lógica'
        MEMORY = 'memory', 'Memoria'
        CODING = 'coding', 'Desarrollo'
        SPEED = 'speed', 'Velocidad'

    difficulty = models.CharField(max_length=20, choices=Difficulty.choices, default=Difficulty.EASY)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.LOGIC)
    objective = models.CharField(max_length=200, blank=True)
    task_data = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = 'ChallengeArenas'
        verbose_name_plural = 'ChallengeArenas'


class EscapeArena(BaseArena):
    class Difficulty(models.TextChoices):
        EASY = 'easy', 'Fácil'
        MEDIUM = 'medium', 'Media'
        HARD = 'hard', 'Difícil'

    difficulty = models.CharField(max_length=20, choices=Difficulty.choices, default=Difficulty.MEDIUM)
    estimated_time_minutes = models.PositiveIntegerField(default=30)
    narrative = models.TextField(blank=True)
    rooms = models.JSONField(default=list, blank=True)
    clues = models.JSONField(default=list, blank=True)

    class Meta:
        verbose_name = 'EscapeArenas'
        verbose_name_plural = 'EscapeArenas'
