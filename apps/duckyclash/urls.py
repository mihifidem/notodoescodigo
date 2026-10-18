from django.urls import path
from .question_views import AnswerQuestionView, CurrentQuestionView, EngineActionView, RingStateView, UseItemView, OpenChallengeStateView

from .views import (
    ChallengeDetailView, ClashHomeView, PrivateChallengeActionView,
    PrivateChallengeCreateView, RingView,
    OpenChallengeAcceptView, OpenChallengeCreateView, OpenChallengeListView,
    DuelCancelView, ChallengeManageView,
    CreateClashView, InvitationListView, DuelListView, DuelDetailView, WaitingStateView, CombatHistoryView,
    DuckySpecialtyView,
)

app_name = 'duckyclash'

urlpatterns = [
    path('', ClashHomeView.as_view(), name='home'),
    path('specialty/', DuckySpecialtyView.as_view(), name='specialty'),
    path('new/', CreateClashView.as_view(), name='create'),
    path('invitations/received/', InvitationListView.as_view(), {'folder': 'received'}, name='invitations_received'),
    path('invitations/sent/', InvitationListView.as_view(), {'folder': 'sent'}, name='invitations_sent'),
    path('invitations/counteroffers/', InvitationListView.as_view(), {'folder': 'counteroffers'}, name='counteroffers'),
    path('duels/', DuelListView.as_view(), name='duel_list'),
    path('history/', CombatHistoryView.as_view(), name='history'),
    path('duels/<int:pk>/', DuelDetailView.as_view(), name='duel_detail'),
    path('duels/<int:pk>/waiting/', DuelDetailView.as_view(template_name='duckyclash/waiting.html'), name='waiting'),
    path('duels/<int:pk>/waiting/state/', WaitingStateView.as_view(), name='waiting_state'),
    path('duels/<int:pk>/results/', DuelDetailView.as_view(template_name='duckyclash/results.html'), name='results'),
    path('open/', OpenChallengeListView.as_view(), name='open_list'),
    path('open/state/', OpenChallengeStateView.as_view(), name='open_state'),
    path('open/new/', OpenChallengeCreateView.as_view(), name='open_create'),
    path('open/<int:pk>/accept/', OpenChallengeAcceptView.as_view(), {'action': 'accept'}, name='open_accept'),
    path('challenges/new/', PrivateChallengeCreateView.as_view(), name='challenge_create'),
    path('challenges/<int:pk>/', ChallengeDetailView.as_view(), name='challenge_detail'),
    path('challenges/<int:pk>/edit/', ChallengeManageView.as_view(), {'action': 'edit'}, name='challenge_edit'),
    path('challenges/<int:pk>/delete/', ChallengeManageView.as_view(), {'action': 'delete'}, name='challenge_delete'),
    path('challenges/<int:pk>/accept/', PrivateChallengeActionView.as_view(), {'action': 'accept'}, name='challenge_accept'),
    path('challenges/<int:pk>/decline/', PrivateChallengeActionView.as_view(), {'action': 'decline'}, name='challenge_decline'),
    path('challenges/<int:pk>/counter/', PrivateChallengeActionView.as_view(), {'action': 'counter'}, name='challenge_counter'),
    path('duels/<int:pk>/ring/', RingView.as_view(), name='ring'),
    path('duels/<int:pk>/cancel/', DuelCancelView.as_view(), name='duel_cancel'),
    path('duels/<int:pk>/question/', CurrentQuestionView.as_view(), name='current_question'),
    path('duels/<int:pk>/state/', RingStateView.as_view(), name='ring_state'),
    path('duels/<int:pk>/ready/', EngineActionView.as_view(), {'action': 'ready'}, name='duel_ready'),
    path('duels/<int:pk>/presence/', EngineActionView.as_view(), {'action': 'presence'}, name='duel_presence'),
    path('questions/<int:pk>/answer/', AnswerQuestionView.as_view(), name='question_answer'),
    path('questions/<int:pk>/item/', UseItemView.as_view(), name='question_item'),
]
