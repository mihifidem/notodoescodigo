import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import TemplateView, View

from .config import DUCKY_TYPES
from .battle_challenges import create_challenge, respond_to_challenge, visible_challenges
from .core.attributes import official_characteristics
from .core.ecomotor_progress import get_progress_snapshot_or_none
from .forms import BattleChallengeActionForm, BattleChallengeForm, DuckyOnboardingForm
from .models import (
    ArenaItem,
    ArenaRewardTransaction,
    BattleChallenge,
    ProfessionAssignment,
    UserArenaInventory,
)
from .services import DuckyArenasProfileService, calculate_effective_stats, calculate_power, get_characteristics, get_level_progress


class DuckyArenasAccessMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        profile = DuckyArenasProfileService.get_profile_for_user(request.user)
        onboarding_path = reverse('ducky_arenas:onboarding')

        if request.path == onboarding_path:
            if profile and profile.onboardingCompleted:
                return redirect('ducky_arenas:home')
            return super().dispatch(request, *args, **kwargs)

        if profile is None or not profile.onboardingCompleted:
            return redirect('ducky_arenas:onboarding')

        return super().dispatch(request, *args, **kwargs)


class DuckyArenasHomeView(DuckyArenasAccessMixin, TemplateView):
    template_name = 'ducky_arenas/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = DuckyArenasProfileService.get_profile_for_user(self.request.user)
        context['profile'] = profile
        context['level_progress'] = get_level_progress(profile.xp) if profile else {'current_level': 1, 'next_level': 2, 'xp_into_current_level': 0, 'xp_needed_for_next_level': 100, 'progress_percent': 0}
        context['avatar_config'] = DUCKY_TYPES[profile.avatarType]
        context['characteristics'] = get_characteristics(profile)
        return context


class DuckyArenasProfileView(DuckyArenasAccessMixin, TemplateView):
    template_name = 'ducky_arenas/profile.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = DuckyArenasProfileService.get_profile_for_user(self.request.user)
        context['profile'] = profile
        context['effective_stats'] = calculate_effective_stats(profile)
        context['characteristics'] = get_characteristics(profile)
        context['power'] = calculate_power(profile)
        context['inventory_items'] = DuckyArenasProfileService.get_inventory_for_profile(profile)
        context['catalog_items'] = list(ArenaItem.objects.filter(isActive=True).order_by('rarity', 'name'))
        progress_snapshot = get_progress_snapshot_or_none(self.request.user)
        context['profession_assignment'] = (
            ProfessionAssignment.objects.select_related('profession').filter(
                profile=profile,
            ).first()
            if profile is not None
            else None
        )
        context['official_characteristics'] = official_characteristics(progress_snapshot)
        context['official_xp'] = (
            progress_snapshot.xp_total if progress_snapshot is not None else None
        )
        context['official_progress_available'] = progress_snapshot is not None
        context['official_progress_snapshot'] = progress_snapshot
        return context


class DuckyArenasInventoryView(DuckyArenasAccessMixin, TemplateView):
    template_name = 'ducky_arenas/inventory.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = DuckyArenasProfileService.get_profile_for_user(self.request.user)
        context['profile'] = profile
        context['inventory_items'] = DuckyArenasProfileService.get_inventory_for_profile(profile)
        context['catalog_items'] = list(ArenaItem.objects.filter(isActive=True).order_by('rarity', 'name'))
        context['effective_stats'] = calculate_effective_stats(profile)
        context['power'] = calculate_power(profile)
        return context


class DuckyInventoryEquipView(DuckyArenasAccessMixin, View):
    def post(self, request, *args, **kwargs):
        profile = DuckyArenasProfileService.get_profile_for_user(request.user)
        item_code = request.POST.get('item_code')
        if not item_code:
            return redirect('ducky_arenas:inventory')
        DuckyArenasProfileService.equip_item(profile, item_code)
        return redirect('ducky_arenas:inventory')


class DuckyInventoryUnequipView(DuckyArenasAccessMixin, View):
    def post(self, request, *args, **kwargs):
        profile = DuckyArenasProfileService.get_profile_for_user(request.user)
        slot = request.POST.get('slot')
        DuckyArenasProfileService.unequip_item(profile, slot)
        return redirect('ducky_arenas:inventory')


class DuckyRewardsView(DuckyArenasAccessMixin, TemplateView):
    template_name = 'ducky_arenas/rewards.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = DuckyArenasProfileService.get_profile_for_user(self.request.user)
        reward_id = self.request.GET.get('reward_id')
        transaction = None
        if reward_id:
            transaction = ArenaRewardTransaction.objects.filter(pk=reward_id, profile=profile).select_related('profile').first()
        if transaction is None:
            transaction = ArenaRewardTransaction.objects.filter(profile=profile).order_by('-createdAt').first()
        context['profile'] = profile
        context['reward'] = transaction
        context['level_progress'] = get_level_progress(profile.xp) if profile else {'current_level': 1, 'next_level': 2, 'xp_into_current_level': 0, 'xp_needed_for_next_level': 100, 'progress_percent': 0}
        return context


class QuizArenasView(DuckyArenasAccessMixin, TemplateView):
    template_name = 'ducky_arenas/quiz_arenas.html'


class BattleArenasView(DuckyArenasAccessMixin, TemplateView):
    template_name = 'ducky_arenas/battle_arenas.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['profile'] = DuckyArenasProfileService.get_profile_for_user(self.request.user)
        context.setdefault('challenge_form', BattleChallengeForm(actor=self.request.user))
        page = Paginator(
            visible_challenges(self.request.user).filter(clash_offers__isnull=True), 20,
        ).get_page(self.request.GET.get('page'))
        for challenge in page:
            challenge.can_cancel = (
                challenge.status == BattleChallenge.Status.PENDING and not challenge.is_expired and
                challenge.creator_id == self.request.user.pk
            )
            challenge.can_respond = (
                challenge.status == BattleChallenge.Status.PENDING and not challenge.is_expired and
                challenge.proposed_by_id != self.request.user.pk
            )
            challenge.can_decline = challenge.can_respond and challenge.opponent_id is not None
        context['challenges_page'] = page
        return context

    def post(self, request, *args, **kwargs):
        if request.POST.get('action') == 'create':
            form = BattleChallengeForm(request.POST, actor=request.user)
            if form.is_valid():
                try:
                    create_challenge(
                        actor=request.user, opponent=form.cleaned_data['opponent'],
                        is_public=form.cleaned_data['visibility'] == 'public',
                        stake=form.cleaned_data['stake'], lifetime=form.cleaned_data['lifetime'],
                    )
                except ValidationError as exc:
                    form.add_error(None, exc)
                else:
                    messages.success(request, 'Reto creado. Las monedas no se han reservado ni descontado.')
                    return redirect('ducky_arenas:battle')
            return self.render_to_response(self.get_context_data(challenge_form=form), status=400)
        form = BattleChallengeActionForm(request.POST)
        if form.is_valid():
            try:
                challenge = respond_to_challenge(actor=request.user, **form.cleaned_data)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                text = (
                    'Contrapropuesta enviada. El rival debe aceptar las nuevas condiciones.'
                    if form.cleaned_data['action'] == 'counter'
                    else f'Reto {challenge.display_status.lower()}.'
                )
                messages.success(request, text)
                return redirect('ducky_arenas:battle')
        return self.render_to_response(
            self.get_context_data(action_errors=form.errors, action_values=request.POST), status=400,
        )


class ChallengeArenasView(DuckyArenasAccessMixin, TemplateView):
    template_name = 'ducky_arenas/challenge_arenas.html'


class EscapeArenasView(DuckyArenasAccessMixin, TemplateView):
    template_name = 'ducky_arenas/escape_arenas.html'


class HackArenasView(DuckyArenasAccessMixin, TemplateView):
    template_name = 'ducky_arenas/hack_arenas.html'


class DuckyOnboardingView(LoginRequiredMixin, TemplateView):
    template_name = 'ducky_arenas/onboarding.html'

    def dispatch(self, request, *args, **kwargs):
        profile = DuckyArenasProfileService.get_profile_for_user(request.user)
        if profile and profile.onboardingCompleted:
            return redirect('ducky_arenas:home')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['ducky_types'] = DUCKY_TYPES
        context['current_step'] = 1
        return context


class DuckyProfileMeView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        profile = DuckyArenasProfileService.get_profile_for_user(request.user)
        return JsonResponse({
            'profile': DuckyArenasProfileService.serialize_profile(profile),
            'requiresOnboarding': DuckyArenasProfileService.requires_onboarding(request.user),
        })


class OnboardingCompleteView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        data = request.POST or json.loads(request.body.decode('utf-8')) if request.body else {}
        form = DuckyOnboardingForm(data=data)

        if not form.is_valid():
            return JsonResponse({'errors': form.errors.get_json_data()}, status=400)

        nickname = form.cleaned_data['nickname']
        avatar_type = form.cleaned_data['avatarType']
        gender = form.cleaned_data.get('gender') or 'BOY'

        try:
            profile = DuckyArenasProfileService.complete_onboarding(request.user, nickname, avatar_type, gender)
        except ValueError as exc:
            return JsonResponse({'errors': {'avatarType': [{'message': str(exc), 'code': 'invalid'}]}}, status=400)

        return JsonResponse({
            'ok': True,
            'profile': DuckyArenasProfileService.serialize_profile(profile),
            'redirectUrl': reverse('ducky_arenas:home'),
        })
