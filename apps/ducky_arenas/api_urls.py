from django.urls import path

from .views import DuckyProfileMeView, OnboardingCompleteView

urlpatterns = [
    path('profile/me', DuckyProfileMeView.as_view(), name='profile_me'),
    path('onboarding/complete', OnboardingCompleteView.as_view(), name='onboarding_complete'),
]
