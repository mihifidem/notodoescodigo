from django.urls import path
from . import views

app_name = 'gamification'

urlpatterns = [
    path('insignias/', views.InsigniasView.as_view(), name='insignias'),
    path('insignias/crear/', views.InsigniaCreateView.as_view(), name='crear_insignia'),
    path('logros/', views.LogrosView.as_view(), name='logros'),
    path('misiones/', views.MisionesView.as_view(), name='misiones'),
    path('ranking/', views.RankingView.as_view(), name='ranking'),
    path('como-ganar-puntos/', views.ComoGanarPuntosView.as_view(), name='como_ganar_puntos'),
]
