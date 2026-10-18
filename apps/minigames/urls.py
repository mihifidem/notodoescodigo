from django.urls import path
from .views import (
    MinigamesHubView, NexusMindView, CodeFlowView, SynapseShotView,
    GridMindView, StroopXView, MathBlitzView,
    WordSprintView, ReactionPulseView, EchoGridView,
    NBackFlowView, ShadowMatchView, EstimaRapidView,
)

app_name = 'minigames'

urlpatterns = [
    path('',            MinigamesHubView.as_view(),  name='hub'),
    path('memoria/',    NexusMindView.as_view(),     name='memory'),
    path('logica/',     CodeFlowView.as_view(),       name='logic'),
    path('habilidad/',  SynapseShotView.as_view(),    name='skill'),
    path('atencion/',   GridMindView.as_view(),        name='gridmind'),
    path('stroop/',     StroopXView.as_view(),         name='stroop'),
    path('calculo/',    MathBlitzView.as_view(),       name='mathblitz'),
    path('verbal/',     WordSprintView.as_view(),      name='wordsprint'),
    path('reaccion/',   ReactionPulseView.as_view(),   name='reactionpulse'),
    path('secuencia/',  EchoGridView.as_view(),        name='echogrid'),
    path('nback/',      NBackFlowView.as_view(),       name='nback'),
    path('cambio/',     ShadowMatchView.as_view(),     name='shadowmatch'),
    path('estima/',     EstimaRapidView.as_view(),     name='estimarapid'),
]
