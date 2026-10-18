from django.urls import path
from .views import RecruiterDashboardView, OfferCandidatesView

app_name = 'jobs'

urlpatterns = [
    path('dashboard/', RecruiterDashboardView.as_view(), name='recruiter_dashboard'),
    path('offer/<int:pk>/candidates/', OfferCandidatesView.as_view(), name='offer_candidates'),
]
