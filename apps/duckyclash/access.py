from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse

from apps.ducky_arenas.services import DuckyArenasProfileService

from .characters import era_for_xp


class DuckyClashAccessMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        profile = DuckyArenasProfileService.get_profile_for_user(request.user)
        if profile is None or not profile.onboardingCompleted:
            return redirect('ducky_arenas:onboarding')

        specialty_path = reverse('duckyclash:specialty')
        if (request.path != specialty_path
                and era_for_xp(request.user.puntos_totales).code == 'ACT'
                and not request.user.duck_specialty):
            return redirect('duckyclash:specialty')

        return super().dispatch(request, *args, **kwargs)
