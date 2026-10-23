from django.urls import path
from . import views

app_name = 'gamification'

urlpatterns = [
    path('insignias/', views.InsigniasView.as_view(), name='insignias'),
    path('insignias/crear/', views.InsigniaCreateView.as_view(), name='crear_insignia'),
    path('insignias/<int:pk>/editar/', views.InsigniaUpdateView.as_view(), name='editar_insignia'),
    path('insignias/<int:pk>/conceder/', views.InsigniaGrantView.as_view(), name='conceder_insignia'),
    path('insignias/concesion/<int:pk>/visibilidad/', views.InsigniaVisibilityView.as_view(), name='visibilidad_insignia'),
    path('insignias/concesion/<int:pk>/revocar/', views.InsigniaRevokeView.as_view(), name='revocar_insignia'),
    path('insignias/concesion/<int:pk>/restaurar/', views.InsigniaRestoreView.as_view(), name='restaurar_insignia'),
    path('insignias/auditoria/', views.InsigniaAuditView.as_view(), name='auditoria_insignias'),
    path('insignias/<int:pk>/<str:action>/', views.InsigniaStatusView.as_view(), name='estado_insignia'),
    path('insignia/<int:pk>/', views.InsigniaDetailView.as_view(), name='insignia_detalle'),
    path('coleccion/<str:username>/', views.PublicBadgeCollectionView.as_view(), name='coleccion_publica'),
    path('logros/', views.LogrosView.as_view(), name='logros'),
    path('misiones/', views.MisionesView.as_view(), name='misiones'),
    path('ranking/', views.RankingView.as_view(), name='ranking'),
    path('ranking/participacion/<slug:slug>/', views.RankingParticipationView.as_view(), name='ranking_participation'),
    path('como-ganar-puntos/', views.ComoGanarPuntosView.as_view(), name='como_ganar_puntos'),
]
