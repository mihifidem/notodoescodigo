from django.urls import path

from .core.views import (
    DuckyProfessionView,
    ExplorationStateView,
    EvolutionNoticeAcknowledgeView,
    MapTransitionView,
    PortalLaunchView,
    PortalReturnView,
    WorldMapView,
)
from .views import (
    BattleArenasView,
    ChallengeArenasView,
    DuckyArenasHomeView,
    DuckyArenasInventoryView,
    DuckyArenasProfileView,
    DuckyInventoryEquipView,
    DuckyInventoryUnequipView,
    DuckyOnboardingView,
    DuckyRewardsView,
    EscapeArenasView,
    HackArenasView,
    QuizArenasView,
    OnboardingCompleteView,
    DuckyProfileMeView,
)

app_name = 'ducky_arenas'

urlpatterns = [
    path('', DuckyArenasHomeView.as_view(), name='home'),
    path('world/', WorldMapView.as_view(), name='world'),
    path('world/<slug:era>/', WorldMapView.as_view(), name='world_era'),
    path('profession/', DuckyProfessionView.as_view(), name='profession'),
    path('api/exploration/state/', ExplorationStateView.as_view(), name='exploration_state'),
    path(
        'api/evolution/notices/ack/',
        EvolutionNoticeAcknowledgeView.as_view(),
        name='evolution_notice_ack',
    ),
    path(
        'api/exploration/maps/<slug:era>/select/',
        MapTransitionView.as_view(),
        name='map_transition',
    ),
    path('api/portals/<slug:portal_id>/launch/', PortalLaunchView.as_view(), name='portal_launch'),
    path('portal/return/', PortalReturnView.as_view(), name='portal_return'),
    path('onboarding/', DuckyOnboardingView.as_view(), name='onboarding'),
    path('profile/', DuckyArenasProfileView.as_view(), name='profile'),
    path('inventory/', DuckyArenasInventoryView.as_view(), name='inventory'),
    path('inventory/equip/', DuckyInventoryEquipView.as_view(), name='inventory_equip'),
    path('inventory/unequip/', DuckyInventoryUnequipView.as_view(), name='inventory_unequip'),
    path('rewards/', DuckyRewardsView.as_view(), name='rewards'),
    path('quiz/', QuizArenasView.as_view(), name='quiz'),
    path('battle/', BattleArenasView.as_view(), name='battle'),
    path('challenge/', ChallengeArenasView.as_view(), name='challenge'),
    path('escape/', EscapeArenasView.as_view(), name='escape'),
    path('hack/', HackArenasView.as_view(), name='hack'),
    path('api/profile/me/', DuckyProfileMeView.as_view(), name='profile_me'),
    path('api/onboarding/complete/', OnboardingCompleteView.as_view(), name='onboarding_complete'),
]
