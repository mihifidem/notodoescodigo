import logging
import uuid
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings
from django.core.exceptions import PermissionDenied, SuspiciousOperation, ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Q
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView
from django.templatetags.static import static

from apps.ducky_arenas.battle_challenges import visible_challenges
from apps.users.access import is_platform_admin

from .models import Challenge, ChallengeAgreement, Duel, DuelResolution, EconomicOperation
from .policies import can_view_challenge, require_participant, is_clash_staff
from .bank import BankUnavailable
from .forms import (
    CounterOfferForm, OfferActionForm, OpenChallengeFilterForm, OpenChallengeForm,
    PlayerSearchForm, PrivateChallengeForm,
    SubcategoryCreateForm, CategoryCreateForm, LandscapeCreateForm, ChallengeEditForm,
)
from .selectors import open_challenges, private_challenges, search_players
from .services import accept_offer, counter_offer, create_challenge, decline_offer, edit_challenge, delete_challenge
from .question_views import private_json
from .economy import request_cancellation
from .questions import public_question
from .items import DEFAULT_RULES
from .engine import DuelEngine, item_window, verified_reserves
from .models import CombatSession
from apps.ducky_arenas.models import UserArenaInventory, UserProfileArenas
from apps.ducky_arenas.config import DUCKY_TYPES
from .access import DuckyClashAccessMixin
from .characters import character_stats

logger = logging.getLogger(__name__)


def challenge_context(user, challenge, *, action_form=None, counter_form=None):
    offers = list(challenge.clash_offers.select_related('category', 'subcategory', 'landscape', 'proposed_by').order_by('-version'))
    offer = offers[0]
    local = timezone.localtime(offer.scheduled_at, ZoneInfo(offer.time_zone))
    initial = {
        'version': offer.version, 'revision': challenge.revision,
        'date': local.date(), 'time': local.time().replace(tzinfo=None), 'stake': offer.stake,
    }
    can_respond = (
        not challenge.is_public and challenge.opponent_id is not None
        and user.pk in (challenge.creator_id, challenge.opponent_id)
        and user.pk != challenge.proposed_by_id
        and challenge.status == Challenge.Status.PENDING
        and min(challenge.expires_at, offer.expires_at) > timezone.now()
    )
    return {
        'challenge': challenge, 'offer': offer, 'offers': offers,
        'can_respond': can_respond, 'offer_zone': ZoneInfo(offer.time_zone),
        'can_manage': (
            user.pk == challenge.creator_id and challenge.status == Challenge.Status.PENDING
            and min(challenge.expires_at, offer.expires_at, offer.scheduled_at) > timezone.now()
        ),
        'can_delete': (
            (user.pk == challenge.creator_id or is_clash_staff(user))
            and challenge.status == Challenge.Status.PENDING
            and min(challenge.expires_at, offer.expires_at, offer.scheduled_at) > timezone.now()
            and not ChallengeAgreement.objects.filter(challenge=challenge).exists()
        ),
        'can_accept_open': (
            challenge.is_public and challenge.opponent_id is None and user.pk != challenge.creator_id
            and challenge.status == Challenge.Status.PENDING
            and min(challenge.expires_at, offer.expires_at) > timezone.now()
        ),
        'action_form': action_form if action_form is not None else OfferActionForm(initial=initial),
        'counter_form': counter_form if counter_form is not None else CounterOfferForm(offer=offer, initial=initial),
        'duel': Duel.objects.filter(agreement__challenge=challenge).first(),
    }


class DuckySpecialtyView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        return redirect('ducky_arenas:profession')

    def post(self, request, *args, **kwargs):
        return redirect('ducky_arenas:profession')


class ClashHomeView(DuckyClashAccessMixin, TemplateView):
    template_name = 'duckyclash/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['challenges'] = visible_challenges(self.request.user).filter(
            clash_offers__isnull=False,
        ).distinct()[:20]
        context['received_count'] = private_challenges(self.request.user).filter(
            opponent=self.request.user, status=Challenge.Status.PENDING, expires_at__gt=timezone.now(),
        ).count()
        context['sent_count'] = private_challenges(self.request.user).filter(
            creator=self.request.user, status=Challenge.Status.PENDING, expires_at__gt=timezone.now(),
        ).count()
        context['duel_count'] = my_duels(self.request.user).exclude(
            status__in=[Duel.Status.FINISHED, Duel.Status.CANCELED, Duel.Status.NO_SHOW],
        ).count()
        return context


class CreateClashView(DuckyClashAccessMixin, TemplateView):
    template_name = 'duckyclash/create.html'


class InvitationListView(DuckyClashAccessMixin, TemplateView):
    template_name = 'duckyclash/invitations.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        folder = self.kwargs['folder']
        queryset = private_challenges(self.request.user)
        if folder == 'received':
            queryset = queryset.filter(opponent=self.request.user)
        elif folder == 'sent':
            queryset = queryset.filter(creator=self.request.user)
        else:
            queryset = queryset.filter(status=Challenge.Status.PENDING, expires_at__gt=timezone.now()).exclude(
                proposed_by=self.request.user,
            ).filter(clash_offers__version__gt=1)
        page = Paginator(queryset.order_by('-created_at', '-pk'), 12).get_page(self.request.GET.get('page'))
        context.update(folder=folder, page_obj=page, challenges=page.object_list)
        return context


def my_duels(user):
    return Duel.objects.filter(
        Q(agreement__creator=user) | Q(agreement__opponent=user),
    ).select_related('agreement__creator', 'agreement__opponent', 'agreement__offer__landscape')


class DuelListView(DuckyClashAccessMixin, TemplateView):
    template_name = 'duckyclash/duel_list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        queryset = my_duels(self.request.user)
        selected = self.request.GET.get('status', '')
        if selected in Duel.Status.values:
            queryset = queryset.filter(status=selected)
        elif selected:
            queryset = queryset.none()
            context['filter_error'] = 'El estado seleccionado no es válido.'
        context.update(
            page_obj=Paginator(queryset.order_by('-created_at', '-pk'), 12).get_page(self.request.GET.get('page')),
            status_choices=Duel.Status.choices, selected_status=selected,
        )
        query = self.request.GET.copy()
        query.pop('page', None)
        context['filters'] = query.urlencode()
        return context


class CombatHistoryView(DuckyClashAccessMixin, TemplateView):
    template_name = 'duckyclash/history.html'

    def get_context_data(self, **kwargs):
        from .models import ParticipantState
        from .history import participant_metrics, summary
        context = super().get_context_data(**kwargs)
        own = ParticipantState.objects.filter(player=self.request.user)
        categories = list(own.values_list(
            'duel__agreement__offer__category_id', 'duel__agreement__offer__category__nombre',
        ).distinct().order_by('duel__agreement__offer__category__nombre'))
        selected = self.request.GET.get('category', '')
        if selected:
            if selected.isdecimal() and int(selected) in {pk for pk, _ in categories}:
                own = own.filter(duel__agreement__offer__category_id=int(selected))
            else:
                own = own.none()
                context['filter_error'] = 'La categoría no es válida para tu historial.'
        page = Paginator(own.select_related(
            'duel__resolution', 'duel__agreement__offer__category',
        ).prefetch_related(
            'answers__question__damage_resolution', 'health_changes', 'item_uses', 'duel__participants__health_changes',
            'duel__economic_operations',
        ).order_by('-duel__created_at', '-pk'), 12).get_page(self.request.GET.get('page'))
        context.update(
            page_obj=page, rows=[participant_metrics(p) for p in page.object_list],
            totals=summary(own, self.request.user), categories=categories, selected_category=selected,
            breakdown=[{'name': name, 'stats': summary(own.filter(
                duel__agreement__offer__category_id=pk,
            ), self.request.user)} for pk, name in categories if not selected or selected == str(pk)],
            profile=self.request.user.user_profile_arenas,
        )
        query = self.request.GET.copy()
        query.pop('page', None)
        context['filters'] = query.urlencode()
        return context


class DuelDetailView(DuckyClashAccessMixin, TemplateView):
    template_name = 'duckyclash/duel_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        duel = get_object_or_404(my_duels(self.request.user), pk=self.kwargs['pk'])
        require_participant(self.request.user, duel.agreement)
        operations = list(duel.economic_operations.select_related('player').order_by('pk'))
        profiles = {p.user_id: p for p in UserProfileArenas.objects.filter(
            user_id__in=[duel.agreement.creator_id, duel.agreement.opponent_id],
        )}
        participants = {p.player_id: p for p in duel.participants.all()}
        players = []
        for user in (duel.agreement.creator, duel.agreement.opponent):
            profile = profiles.get(user.pk)
            players.append({
                'user': user, 'profile': profile,
                'participant': participants.get(user.pk),
                'avatar': DUCKY_TYPES.get(profile.avatarType) if profile else None,
                'hold': next((operation for operation in operations
                              if operation.player_id == user.pk and operation.kind == EconomicOperation.Kind.RESERVE), None),
                'release': next((operation for operation in operations
                                 if operation.player_id == user.pk and operation.kind == EconomicOperation.Kind.RELEASE), None),
            })
        session = CombatSession.objects.filter(duel=duel).first()
        now = timezone.now()
        can_ring = (
            now >= duel.agreement.offer.scheduled_at and verified_reserves(duel)
            and duel.rules_snapshot.get('ring_access', 'scheduled') == 'scheduled'
            and duel.status in (Duel.Status.SCHEDULED, Duel.Status.IN_PROGRESS)
            and (not session or session.phase != CombatSession.Phase.FINISHED)
        )
        resolution = DuelResolution.objects.select_related('winner').filter(duel=duel).first()
        context.update(
            duel=duel, offer=duel.agreement.offer, players=players, operations=operations,
            can_ring=can_ring, resolution=resolution, session=session, server_time=now,
            is_terminal=bool(resolution or duel.status in (
                Duel.Status.FINISHED, Duel.Status.CANCELED, Duel.Status.NO_SHOW, Duel.Status.SETTLEMENT_PENDING,
            ) or (session and session.phase == CombatSession.Phase.FINISHED)),
        )
        context['opens_at'] = (
            duel.agreement.offer.scheduled_at
            if (not context['is_terminal'] and duel.status == Duel.Status.SCHEDULED
                and verified_reserves(duel)
                and duel.rules_snapshot.get('ring_access', 'scheduled') == 'scheduled')
            else None
        )
        return context


class WaitingStateView(DuelDetailView):
    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return private_json({
            'can_ring': context['can_ring'], 'is_terminal': context['is_terminal'],
            'status': context['duel'].get_status_display(),
            'server_time': context['server_time'].isoformat(),
            'opens_at': context['opens_at'].isoformat() if context['opens_at'] else None,
        })


class ChallengeStaffAccessMixin(DuckyClashAccessMixin):
    def dispatch(self, request, *args, **kwargs):
        if is_clash_staff(request.user):
            return View.dispatch(self, request, *args, **kwargs)
        return super().dispatch(request, *args, **kwargs)


class ChallengeDetailView(ChallengeStaffAccessMixin, TemplateView):
    template_name = 'duckyclash/challenge_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        challenge = get_object_or_404(
            Challenge.objects.filter(clash_offers__isnull=False).distinct(),
            pk=self.kwargs['pk'],
        )
        if not can_view_challenge(self.request.user, challenge):
            raise PermissionDenied
        context.update(challenge_context(self.request.user, challenge))
        return context


class ChallengeManageView(ChallengeStaffAccessMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            queryset = Challenge.objects.filter(clash_offers__isnull=False)
            if kwargs['action'] != 'delete' or not is_clash_staff(request.user):
                queryset = queryset.filter(creator=request.user)
            self.challenge = get_object_or_404(
                queryset.distinct(),
                pk=kwargs['pk'],
            )
        return super().dispatch(request, *args, **kwargs)

    def edit_form(self, data=None):
        offer = self.challenge.clash_offers.order_by('-version').first()
        zone = ZoneInfo(offer.time_zone)
        start, expiry = timezone.localtime(offer.scheduled_at, zone), timezone.localtime(offer.expires_at, zone)
        return ChallengeEditForm(data, actor=self.request.user, time_zone=offer.time_zone, initial={
            'version': offer.version, 'revision': self.challenge.revision,
            'category': offer.category_id, 'subcategory': offer.subcategory_id, 'landscape': offer.landscape_id,
            'date': start.date(), 'time': start.time().replace(tzinfo=None), 'stake': offer.stake,
            'expiry_date': expiry.date(), 'expiry_time': expiry.time().replace(tzinfo=None),
        })

    def get(self, request, *args, **kwargs):
        if kwargs['action'] == 'delete':
            return self.http_method_not_allowed(request, *args, **kwargs)
        context = challenge_context(request.user, self.challenge)
        if not context['can_manage'] or context['duel']:
            messages.error(request, 'Solo puedes editar retos pendientes y no caducados.')
            return redirect('duckyclash:challenge_detail', pk=self.challenge.pk)
        return render(request, 'duckyclash/challenge_edit.html', {
            'challenge': self.challenge, 'form': self.edit_form(),
        })

    def post(self, request, *args, **kwargs):
        deleting = kwargs['action'] == 'delete'
        form = OfferActionForm(request.POST) if deleting else self.edit_form(request.POST)
        status = 400
        if form.is_valid():
            data = form.cleaned_data
            params = dict(actor=request.user, challenge_id=self.challenge.pk,
                          version=data['version'], revision=data['revision'])
            try:
                if deleting:
                    delete_challenge(**params)
                else:
                    edit_challenge(**params, **{key: data[key] for key in (
                        'category', 'subcategory', 'landscape', 'scheduled_at', 'expires_at', 'stake', 'evaluated_at',
                    )}, time_zone=form.time_zone)
            except ValidationError as exc:
                form.add_error(None, exc.messages)
                status = 409
            except BankUnavailable as exc:
                logger.warning('Challenge edit balance check failed: %s', exc)
                form.add_error(None, str(exc))
                status = 503
            except OperationalError as exc:
                if 'locked' not in str(exc).lower():
                    raise
                logger.warning('Concurrent challenge management: %s', exc)
                form.add_error(None, 'Otra operación está actualizando el reto. Recarga antes de volver a intentarlo.')
                status = 409
            else:
                messages.success(request, 'Reto eliminado.' if deleting else 'Reto actualizado. Es necesario aceptar las nuevas condiciones.')
                if deleting and is_clash_staff(request.user):
                    return redirect('admin:duckyclash_challenge_changelist')
                return redirect('duckyclash:home') if deleting else redirect(
                    'duckyclash:challenge_detail', pk=self.challenge.pk,
                )
        if deleting:
            self.challenge = get_object_or_404(Challenge, pk=self.challenge.pk)
            return render(request, 'duckyclash/challenge_detail.html',
                          challenge_context(request.user, self.challenge, action_form=form), status=status)
        return render(request, 'duckyclash/challenge_edit.html',
                      {'challenge': self.challenge, 'form': form}, status=status)


class PrivateChallengeCreateView(DuckyClashAccessMixin, TemplateView):
    template_name = 'duckyclash/challenge_form.html'
    form_class = PrivateChallengeForm
    is_public = False

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        search_form = PlayerSearchForm(self.request.GET if 'q' in self.request.GET else None)
        context['search_form'] = search_form
        context['players'] = search_players(self.request.user, search_form.cleaned_data['q']) if search_form.is_bound and search_form.is_valid() else []
        context.setdefault('form', self.form_class(
            actor=self.request.user, initial={'opponent': self.request.GET.get('opponent', '')},
        ))
        context['can_add_category'] = is_platform_admin(self.request.user)
        context['can_add_subcategory'] = is_platform_admin(self.request.user) or self.request.user.has_perm('duckyclash.add_challengesubcategory')
        context['can_add_landscape'] = self.request.user.has_perm('duckyclash.add_landscape')
        context.setdefault('subcategory_form', SubcategoryCreateForm(prefix='new_subcategory'))
        context.setdefault('category_form', CategoryCreateForm(prefix='new_category'))
        context.setdefault('landscape_form', LandscapeCreateForm(prefix='new_landscape'))
        return context

    def post(self, request, *args, **kwargs):
        if 'catalog_action' in request.POST:
            return self.add_catalog_entry(request)
        form = self.form_class(request.POST, actor=request.user)
        status = 400
        if form.is_valid():
            data = form.cleaned_data
            try:
                challenge = create_challenge(
                    actor=request.user, opponent=data.get('opponent'), is_public=self.is_public,
                    category=data['category'], subcategory=data['subcategory'], landscape=data['landscape'],
                    scheduled_at=data['scheduled_at'], expires_at=data['expires_at'],
                    time_zone=form.time_zone, stake=data['stake'], evaluated_at=data['evaluated_at'],
                )
            except ValidationError as exc:
                form.add_error(None, exc.messages)
            except BankUnavailable as exc:
                logger.warning('Private challenge balance check failed: %s', exc)
                form.add_error(None, str(exc))
                status = 503
            except OperationalError as exc:
                if 'locked' not in str(exc).lower():
                    raise
                logger.warning('Concurrent private challenge creation: %s', exc)
                form.add_error(None, 'Otra operación está en curso. Revisa tus retos antes de volver a enviar.')
                status = 409
            else:
                messages.success(request, 'Reto abierto publicado. Sin monedas reservadas.' if self.is_public else 'Reto privado enviado. El saldo se ha consultado, pero no se han reservado monedas.')
                return redirect('duckyclash:challenge_detail', pk=challenge.pk)
        return self.render_to_response(self.get_context_data(form=form), status=status)

    def add_catalog_entry(self, request):
        action = request.POST['catalog_action']
        if action not in {'category', 'subcategory', 'landscape'}:
            raise SuspiciousOperation('Acción de catálogo no válida.')
        permission = 'add_challengesubcategory' if action == 'subcategory' else 'add_landscape'
        allowed = is_platform_admin(request.user) if action == 'category' else (
            request.user.has_perm(f'duckyclash.{permission}')
            or (action == 'subcategory' and is_platform_admin(request.user))
        )
        if not allowed:
            raise PermissionDenied
        form_classes = {'category': CategoryCreateForm, 'subcategory': SubcategoryCreateForm,
                        'landscape': LandscapeCreateForm}
        form_class = form_classes[action]
        catalog_form = form_class(request.POST, request.FILES, prefix=f'new_{action}')
        data = request.POST.copy()
        status = 400
        if catalog_form.is_valid():
            entry = catalog_form.save(commit=False)
            if action == 'landscape':
                entry.code = f'landscape-{uuid.uuid4().hex}'
                extension = {'PNG': 'png', 'JPEG': 'jpg', 'WEBP': 'webp'}[catalog_form.cleaned_data['image'].image.format]
                entry.image.name = f'{entry.code}.{extension}'
            try:
                with transaction.atomic():
                    entry.save()
            except (IntegrityError, ValidationError) as exc:
                logger.warning('Clash catalog creation conflict: %s', exc)
                catalog_form.add_error(None, 'No se pudo guardar: revisa si ya existe una entrada con estos datos.')
                return self.render_to_response(self.get_context_data(
                    form=self.form_class(data, actor=request.user),
                    **{f'{action}_form': catalog_form},
                ), status=409)
            data[action] = str(entry.pk)
            if action == 'category':
                data['sector'] = str(entry.sector_id)
                data['subcategory'] = ''
            if action == 'subcategory':
                data['category'] = str(entry.category_id)
                data['sector'] = str(entry.category.sector_id or '')
            messages.success(request, {
                'category': 'Categoría añadida y seleccionada.',
                'subcategory': 'Subcategoría añadida y seleccionada.',
                'landscape': 'Paisaje guardado y seleccionado.',
            }[action])
            catalog_form = form_class(prefix=f'new_{action}')
            status = 200
        context = self.get_context_data(
            form=self.form_class(data, actor=request.user),
            **{f'{action}_form': catalog_form},
        )
        return self.render_to_response(context, status=status)


class PrivateChallengeActionView(DuckyClashAccessMixin, View):
    def get_challenge(self, request):
        return get_object_or_404(private_challenges(request.user), pk=self.kwargs['pk'])

    def post(self, request, *args, **kwargs):
        challenge = self.get_challenge(request)
        offer = challenge.clash_offers.order_by('-version').first()
        action = self.kwargs['action']
        form = CounterOfferForm(request.POST, offer=offer) if action == 'counter' else OfferActionForm(request.POST)
        status = 400
        if form.is_valid():
            params = {
                'actor': request.user, 'challenge_id': challenge.pk,
                'version': form.cleaned_data['version'], 'revision': form.cleaned_data['revision'],
            }
            try:
                if action == 'accept':
                    duel = accept_offer(**params)
                elif action == 'decline':
                    decline_offer(**params)
                else:
                    counter_offer(**params, scheduled_at=form.cleaned_data['scheduled_at'], stake=form.cleaned_data['stake'])
            except ValidationError as exc:
                form.add_error(None, exc.messages)
                status = 409
            except BankUnavailable as exc:
                logger.warning('Private challenge balance check failed: %s', exc)
                form.add_error(None, str(exc))
                status = 503
            except OperationalError as exc:
                if 'locked' not in str(exc).lower():
                    raise
                logger.warning('Concurrent private challenge response: %s', exc)
                form.add_error(None, 'Otro jugador está actualizando el reto. Recarga y revisa la oferta.')
                status = 409
            else:
                if action == 'accept':
                    messages.success(request, 'Oferta aceptada. El servidor debe verificar las reservas de ambos participantes antes de confirmar el duelo.')
                    return redirect('duckyclash:ring', pk=duel.pk)
                messages.success(request, 'Contrapropuesta enviada.' if action == 'counter' else 'Reto rechazado.')
                return redirect('duckyclash:challenge_detail', pk=challenge.pk)
        challenge = get_object_or_404(Challenge, pk=challenge.pk)
        if challenge.is_public and not can_view_challenge(request.user, challenge):
            return render(
                request, 'duckyclash/open_conflict.html', {'errors': form.errors}, status=status,
            )
        context = challenge_context(
            request.user, challenge,
            counter_form=form if action == 'counter' else None,
            action_form=form if action != 'counter' else None,
        )
        return render(request, 'duckyclash/challenge_detail.html', context, status=status)


class OpenChallengeCreateView(PrivateChallengeCreateView):
    template_name = 'duckyclash/open_form.html'
    form_class = OpenChallengeForm
    is_public = True


class OpenChallengeListView(DuckyClashAccessMixin, TemplateView):
    template_name = 'duckyclash/open_list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = OpenChallengeFilterForm(self.request.GET)
        queryset = open_challenges()
        if form.is_valid():
            data = form.cleaned_data
            if data.get('category'):
                queryset = queryset.filter(current_category_id=data['category'].pk)
            if data.get('date'):
                queryset = queryset.filter(current_scheduled_at__date=data['date'])
            if data.get('max_stake'):
                queryset = queryset.filter(stake__lte=data['max_stake'])
        else:
            queryset = queryset.none()
        page = Paginator(queryset.distinct(), 12).get_page(self.request.GET.get('page'))
        cards = []
        for challenge in page:
            offer = challenge.clash_offers.all()[0]
            cards.append({
                'challenge': challenge, 'offer': offer,
                'remaining': max(0, int((challenge.expires_at - timezone.now()).total_seconds())),
            })
        query = self.request.GET.copy()
        query.pop('page', None)
        context.update(filter_form=form, cards=cards, page_obj=page, filters=query.urlencode(),
                       filter_time_zone=settings.TIME_ZONE)
        return context


class OpenChallengeAcceptView(PrivateChallengeActionView):
    def post(self, request, *args, **kwargs):
        try:
            return super().post(request, *args, **kwargs)
        except ValidationError as exc:
            return render(request, 'duckyclash/open_conflict.html', {'errors': exc.messages}, status=409)

    def get_challenge(self, request):
        # Include already claimed/expired records so stale POSTs get a conflict.
        challenge = get_object_or_404(
            Challenge.objects.filter(is_public=True, clash_offers__isnull=False).distinct(),
            pk=self.kwargs['pk'],
        )
        if challenge.creator_id == request.user.pk:
            raise PermissionDenied('No puedes aceptar tu propio reto.')
        if challenge.opponent_id is not None or challenge.status != Challenge.Status.PENDING:
            raise ValidationError('El reto ya no está disponible.')
        return challenge


class RingView(DuckyClashAccessMixin, TemplateView):
    template_name = 'duckyclash/ring.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['demo_ecomotor'] = (
            settings.DEBUG and getattr(settings, 'DUCKYCLASH_ECOMOTOR_GATEWAY', '')
            == 'apps.duckyclash.ecomotor.DevelopmentFakeEcoMotor'
        )
        duel = get_object_or_404(Duel.objects.select_related('agreement__offer'), pk=self.kwargs['pk'])
        require_participant(self.request.user, duel.agreement)
        context['duel'] = duel
        context['participants'] = list(duel.participants.select_related('player').order_by('seat'))
        context['operations'] = duel.economic_operations.select_related('player').order_by('pk')
        ring_character_sprites = [
            static('duckyarenas/DAR_personajes/DAR_PRE1.png'),
            static('duckyarenas/DAR_personajes/DAR_PRE2.png'),
        ]
        profiles = {profile.user_id: profile for profile in UserProfileArenas.objects.filter(
            user_id__in=[duel.agreement.creator_id, duel.agreement.opponent_id],
        )}
        fighters = []
        for index, participant in enumerate(context['participants']):
            profile = profiles.get(participant.player_id)
            avatar = DUCKY_TYPES.get(profile.avatarType, {}) if profile else {}
            stats = character_stats(participant.player)
            fighters.append({
                'player_id': participant.player_id,
                'name': profile.nickname if profile and profile.nickname else participant.player.get_username(),
                'nickname': profile.nickname if profile else '', 'accent': avatar.get('accent', '#1d4ed8'),
                'hp': participant.hp, 'max_hp': participant.max_hp,
                'sprite': ring_character_sprites[index % len(ring_character_sprites)],
                'animations': {
                    name: {
                        'url': static(f'duckyclash/character-frames/PRE{index + 1}_{filename}.webp'),
                        'frameCount': 4,
                        'frameDuration': 580 if index == 1 and name == 'idle' else 380,
                    }
                    for name, filename in (
                        ('idle', '01_ilde'), ('attack', '02_attack'), ('hit', '03_hit'),
                        ('dealDamage', '04_deal'), ('win', '05_win'), ('lose', '06_lose'),
                    )
                },
                'stats': {
                    'ATK': stats.atk, 'DEF': stats.defense, 'LOG': stats.logic,
                    'SPE': stats.speed, 'QUA': stats.qua,
                },
            })
            participant.ducky_name = fighters[-1]['name']
            participant.character_stats = fighters[-1]['stats']
            participant.character_sprite = fighters[-1]['sprite']
        landscape = duel.agreement.offer.landscape
        context['ring_assets'] = {
            'fighters': fighters, 'landscape': landscape.name,
            'background': landscape.image.url if landscape.image else '',
        }
        question = duel.questions.select_related('duel__agreement').order_by('-number').first()
        session = CombatSession.objects.filter(duel=duel).first()
        if session or duel.status == Duel.Status.SCHEDULED:
            context['engine_state'] = DuelEngine(duel.pk).snapshot(self.request.user)
            context['engine_ready'] = duel.participants.filter(player=self.request.user, ready_at__isnull=False).exists()
        context['item_question_id'] = question.pk if question else None
        if question and duel.status == Duel.Status.IN_PROGRESS:
            combat_items = []
            for entry in UserArenaInventory.objects.select_related('item').filter(
                profile__user=self.request.user, quantity__gt=0, isEquipped=False,
                item__isActive=True, item__itemType='COMPLEMENT', item__code__in=DEFAULT_RULES,
            ):
                try:
                    item_window(duel, question, entry.item.code)
                except ValidationError:
                    continue
                combat_items.append({
                    'code': entry.item.code, 'name': entry.item.name,
                    'quantity': entry.quantity, 'action_id': uuid.uuid4(),
                })
            context['combat_items'] = combat_items
        labels = {
            'NUBE': 'Nube', 'ACELERADOR': 'Acelerador', 'MULTIPLICADOR': 'Multiplicador',
            'ESCUDO': 'Escudo', 'POCIMA_TIEMPO': 'Pócima de tiempo', 'BOTIQUIN': 'Botiquín',
        }
        stock = {entry.item.code: entry for entry in UserArenaInventory.objects.select_related('item').filter(
            profile__user=self.request.user, isEquipped=False, item__isActive=True,
            item__itemType='COMPLEMENT', item__code__in=DEFAULT_RULES,
        )}
        allowed = {entry['code'] for entry in context.get('combat_items', [])}
        context['ring_inventory'] = [
            {'code': code, 'name': labels[code], 'quantity': stock[code].quantity if code in stock else 0,
             'enabled': code in allowed, 'action_id': uuid.uuid4()}
            for code in DEFAULT_RULES
        ]
        if question is not None and duel.status == Duel.Status.IN_PROGRESS:
            try:
                context['current_question'] = public_question(actor=self.request.user, question=question)
            except ValidationError:
                context['question_closed'] = True
        context['can_cancel'] = (
            duel.bank_mode == Duel.BankMode.RESERVE
            and duel.status in (Duel.Status.PENDING_RESERVATION, Duel.Status.SCHEDULED)
            and duel.agreement.offer.scheduled_at > timezone.now()
        )
        return context


class DuelCancelView(DuckyClashAccessMixin, View):
    def post(self, request, pk):
        duel = get_object_or_404(Duel.objects.select_related('agreement'), pk=pk)
        require_participant(request.user, duel.agreement)
        try:
            request_cancellation(actor=request.user, duel_id=duel.pk)
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        except OperationalError as exc:
            if 'locked' not in str(exc).lower():
                raise
            logger.warning('Concurrent DuckyClash cancellation for duel %s', pk)
            messages.error(request, 'Otra operación está en curso. Recarga antes de volver a cancelar.')
        else:
            messages.success(request, 'Cancelación registrada. Cualquier reserva debe quedar conciliada y liberada antes del cierre.')
        return redirect('duckyclash:ring', pk=pk)
