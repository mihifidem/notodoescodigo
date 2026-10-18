from django.contrib import admin
from django.db.models import Count

from .models import (
    ArenaItem,
    ArenaRewardTransaction,
    BattleArena,
    CharacterAsset,
    ChallengeArena,
    EscapeArena,
    GameDestination,
    HistoricalEra,
    PlayerEraProgress,
    Portal,
    Profession,
    ProfessionAssignment,
    ProfessionSelectionPolicy,
    QuizArena,
    UserArenaInventory,
    UserProfileArenas,
    WorldMap,
)


@admin.register(QuizArena)
class QuizArenaAdmin(admin.ModelAdmin):
    list_display = ('title', 'status', 'duration_minutes', 'created_at')
    list_filter = ('status', 'is_active')
    search_fields = ('title', 'description', 'slug')


@admin.register(BattleArena)
class BattleArenaAdmin(admin.ModelAdmin):
    list_display = ('title', 'status', 'winner', 'created_at')
    list_filter = ('status', 'is_active')
    search_fields = ('title', 'description', 'slug')


@admin.register(ChallengeArena)
class ChallengeArenaAdmin(admin.ModelAdmin):
    list_display = ('title', 'difficulty', 'category', 'created_at')
    list_filter = ('difficulty', 'category', 'is_active')
    search_fields = ('title', 'description', 'slug')


@admin.register(EscapeArena)
class EscapeArenaAdmin(admin.ModelAdmin):
    list_display = ('title', 'difficulty', 'estimated_time_minutes', 'created_at')
    list_filter = ('difficulty', 'is_active')
    search_fields = ('title', 'description', 'slug')


@admin.register(UserProfileArenas)
class UserProfileArenasAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'nickname',
        'avatarType',
        'gender',
        'current_map',
        'exploration_version',
        'onboardingCompleted',
        'rating',
        'rank',
    )
    list_filter = ('avatarType', 'gender', 'onboardingCompleted', 'rank')
    search_fields = ('nickname', 'user__username')


@admin.register(HistoricalEra)
class HistoricalEraAdmin(admin.ModelAdmin):
    list_display = ('code', 'slug', 'name', 'order', 'required_xp', 'is_available')
    list_filter = ('is_available',)
    search_fields = ('code', 'slug', 'name')
    ordering = ('order',)


@admin.register(WorldMap)
class WorldMapAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'era', 'is_available', 'width', 'height')
    list_filter = ('era', 'is_available')
    search_fields = ('code', 'name', 'static_path')
    list_select_related = ('era',)


@admin.register(Portal)
class PortalAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'world_map', 'destination', 'is_active')
    list_filter = ('destination__status', 'is_active', 'world_map__era')
    search_fields = ('code', 'name', 'destination__code', 'destination__name')
    list_select_related = ('world_map', 'world_map__era', 'destination')


@admin.register(GameDestination)
class GameDestinationAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'owner_app', 'status', 'entry_url_name')
    list_filter = ('status', 'owner_app')
    search_fields = ('code', 'name', 'owner_app', 'entry_url_name')


@admin.register(Profession)
class ProfessionAdmin(admin.ModelAdmin):
    list_display = ('display_order', 'code', 'name', 'is_available', 'assigned_players')
    list_filter = ('is_available',)
    search_fields = ('code', 'name', 'description')
    ordering = ('display_order', 'name')
    fields = (
        'code',
        'name',
        'description',
        'icon_path',
        'display_order',
        'attribute_recommendations',
        'access_requirements',
        'is_available',
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _assigned_players=Count('assignments'),
        )

    @admin.display(ordering='_assigned_players', description='Jugadores asignados')
    def assigned_players(self, obj):
        return obj._assigned_players


@admin.register(ProfessionAssignment)
class ProfessionAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        'profile',
        'profession',
        'status',
        'operation_id',
        'created_at',
        'updated_at',
    )
    list_filter = ('status', 'profession')
    search_fields = ('profile__nickname', 'profile__user__username', 'profession__name')
    list_select_related = ('profile', 'profile__user', 'profession')
    readonly_fields = (
        'profile',
        'profession',
        'operation_id',
        'status',
        'provider_error',
        'reassignments_used',
        'confirmed_at',
        'created_at',
        'updated_at',
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProfessionSelectionPolicy)
class ProfessionSelectionPolicyAdmin(admin.ModelAdmin):
    list_display = (
        'key',
        'allow_reassignment',
        'free_reassignment_count',
        'require_ecomotor_confirmation',
        'updated_at',
    )
    readonly_fields = ('key', 'updated_at')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CharacterAsset)
class CharacterAssetAdmin(admin.ModelAdmin):
    list_display = ('name', 'era', 'variant', 'is_available')
    list_filter = ('era', 'variant', 'is_available')
    search_fields = ('name', 'static_image_path')
    list_select_related = ('era',)


@admin.register(PlayerEraProgress)
class PlayerEraProgressAdmin(admin.ModelAdmin):
    list_display = ('profile', 'era', 'unlocked_at', 'last_visited_at')
    list_filter = ('era',)
    search_fields = ('profile__nickname', 'profile__user__username', 'era__name')
    list_select_related = ('profile', 'profile__user', 'era')


@admin.register(ArenaItem)
class ArenaItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'slot', 'rarity', 'avatarType', 'isActive')
    list_filter = ('slot', 'rarity', 'avatarType', 'isActive')
    search_fields = ('name', 'code', 'description')


@admin.register(UserArenaInventory)
class UserArenaInventoryAdmin(admin.ModelAdmin):
    list_display = ('profile', 'item', 'quantity', 'updatedAt')
    list_filter = ('profile__avatarType',)
    search_fields = ('profile__nickname', 'item__name', 'item__code')


@admin.register(ArenaRewardTransaction)
class ArenaRewardTransactionAdmin(admin.ModelAdmin):
    list_display = ('profile', 'arenaType', 'sourceId', 'xpAwarded', 'duckCoinsAwarded', 'previousLevel', 'newLevel', 'createdAt')
    list_filter = ('arenaType', 'createdAt')
    search_fields = ('profile__user__username', 'profile__nickname', 'sourceId')
    readonly_fields = ('createdAt',)
