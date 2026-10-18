import json
from uuid import uuid4
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.templatetags.static import static
from django.urls import reverse
from django.views import View
from django.views.generic import FormView, TemplateView

from apps.ducky_arenas.core.destinations import authorize_portal
from apps.ducky_arenas.models import (
    CharacterAsset,
    HistoricalEra,
    Profession,
    ProfessionAssignment,
    UserProfileArenas,
    WorldMap,
)
from apps.ducky_arenas.services import DuckyArenasProfileService
from apps.ducky_arenas.views import DuckyArenasAccessMixin

from .ecomotor_progress import get_progress_snapshot_or_none
from .attributes import official_characteristics
from .exploration import validate_position
from .forms import DuckyProfessionForm
from .player_state import (
    ExplorationStateConflict,
    ExplorationStateRateLimited,
    acknowledge_evolution_notice,
    pending_evolution_notices,
    persist_exploration_state,
    synchronize_era_progress,
    transition_exploration_map,
)
from .progression import (
    configured_eras,
    current_era_for_user,
    get_era,
    next_era_for_xp,
    unlocked_eras_for_user,
)
from .professions import attribute_priority_rows, profession_access_error
from .worlds import map_catalog, world_config


class WorldMapView(DuckyArenasAccessMixin, TemplateView):
    template_name = 'ducky_arenas/world.html'

    def _available_unlocked_eras(self):
        return [
            era
            for era in unlocked_eras_for_user(
                self.request.user,
                progress_snapshot=self.progress_snapshot,
            )
            if WorldMap.objects.filter(
                era__code=era.code,
                era__is_available=True,
                is_available=True,
            ).exists()
        ]

    def _resolve_selection(self):
        checkpoint_result = self.request.GET.get('checkpoint')
        if checkpoint_result == 'restored':
            messages.info(self.request, 'Se restauró tu última posición en este mapa.')
        elif checkpoint_result == 'spawn':
            messages.info(
                self.request,
                'No había una posición válida guardada; apareces en un punto seguro.',
            )
        self.progress_snapshot = get_progress_snapshot_or_none(self.request.user)
        try:
            self.progression_era = current_era_for_user(
                self.request.user,
                progress_snapshot=self.progress_snapshot,
            )
        except ValidationError as exc:
            messages.error(self.request, exc.messages[0])
            return None

        self.profile = DuckyArenasProfileService.get_profile_for_user(self.request.user)
        self.profession_assignment = (
            ProfessionAssignment.objects.select_related('profession').filter(
                profile=self.profile,
            ).first()
            if self.profile is not None
            else None
        )
        available_eras = self._available_unlocked_eras()
        if not available_eras:
            raise Http404('No hay mapas disponibles para las épocas desbloqueadas.')

        saved_era = None
        if (
            self.profile
            and self.profile.current_map_id
            and self.profile.current_map.is_available
            and self.profile.current_map.era_id
            and self.profile.current_map.era.is_available
        ):
            try:
                saved_era = get_era(self.profile.current_map.era.code)
            except ValidationError:
                saved_era = None
            if saved_era not in available_eras:
                saved_era = None

        default_era = saved_era or next(
            (era for era in available_eras if era.code == 'PRE'),
            available_eras[0],
        )
        requested_fallback = saved_era or default_era
        requested = self.kwargs.get('era')
        if requested:
            try:
                requested_era = get_era(requested)
            except ValidationError as exc:
                raise Http404 from exc
            if requested_era not in available_eras:
                messages.info(self.request, 'Esa época aún no está desbloqueada o disponible.')
                return redirect(
                    'ducky_arenas:world_era',
                    era=requested_fallback.code.lower(),
                )
            if (
                (saved_era is not None and requested_era.code != saved_era.code)
                or (saved_era is None and requested_era.code != 'PRE')
            ):
                return redirect(
                    'ducky_arenas:world_era',
                    era=(saved_era or default_era).code.lower(),
                )
            self.era = requested_era
        else:
            self.era = default_era

        if self.era.code == 'ACT' and self.profession_assignment is None:
            return redirect('ducky_arenas:profession')
        return None

    def get(self, request, *args, **kwargs):
        response = self._resolve_selection()
        if response is not None:
            return response
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        position = None
        if (
            self.profile
            and self.profile.current_map_id
            and self.profile.current_map.era_id
            and self.profile.current_map.era.code == self.era.code
            and self.profile.position_x is not None
            and self.profile.position_y is not None
        ):
            try:
                validated = validate_position(
                    self.request.user,
                    self.era.code,
                    self.profile.position_x,
                    self.profile.position_y,
                    world_map=self.profile.current_map,
                )
            except ValidationError:
                position = None
            else:
                position = {'x': validated['x'], 'y': validated['y']}
        if self.profile:
            if self.progress_snapshot is not None:
                synchronize_era_progress(
                    self.profile,
                    self.progress_snapshot.xp_total,
                    visited_era_code=self.era.code,
                )
        config = world_config(self.era, self.profile, position)
        unlocked_codes = {
            item.code
            for item in unlocked_eras_for_user(
                self.request.user,
                progress_snapshot=self.progress_snapshot,
            )
        }
        if self.progress_snapshot is None:
            progress_notice = (
                'EcoMotor no está disponible. Se conservan los desbloqueos '
                'confirmados y no se conceden accesos nuevos.'
            )
        elif self.progress_snapshot.simulated:
            progress_notice = 'Progreso simulado solo para desarrollo; no representa XP oficial.'
        else:
            progress_notice = ''
        next_era = (
            next_era_for_xp(self.progress_snapshot.xp_total)
            if self.progress_snapshot is not None
            else None
        )
        map_catalog_by_era = map_catalog()
        available_map_codes = {
            code for code in map_catalog_by_era
        }
        evolution_notices = []
        if self.profile is not None:
            pending_notices = list(pending_evolution_notices(self.profile))
            characters_by_era = {
                character.era_id: character
                for character in CharacterAsset.objects.filter(
                    era_id__in=[notice.era_id for notice in pending_notices],
                    variant=self.profile.gender,
                    is_available=True,
                )
            }
            for progress in pending_notices:
                character = characters_by_era.get(progress.era_id)
                evolution_notices.append({
                    'code': progress.era.code,
                    'name': progress.era.name,
                    'portrait_url': (
                        static(character.static_image_path)
                        if character and character.static_image_path
                        else ''
                    ),
                })
        xp_progress = None
        if self.progress_snapshot is not None:
            lower_xp = self.progression_era.minimum_xp
            upper_xp = next_era.minimum_xp if next_era is not None else lower_xp
            xp_progress = (
                100
                if upper_xp <= lower_xp
                else min(
                    100,
                    max(
                        0,
                        round(
                            (self.progress_snapshot.xp_total - lower_xp)
                            * 100
                            / (upper_xp - lower_xp)
                        ),
                    ),
                )
            )
        context.update(
            world_config=config,
            current_era=self.progression_era,
            progress_snapshot=self.progress_snapshot,
            progress_notice=progress_notice,
            next_era=next_era,
            xp_progress=xp_progress,
            unlocked_eras=unlocked_codes,
            profession_unlocked='ACT' in unlocked_codes,
            era_navigation=[
                {
                    **map_catalog_by_era.get(item.code, {
                        'code': item.code,
                        'label': item.label,
                        'map_url': '',
                        'width': 0,
                        'height': 0,
                    }),
                    'url': reverse(
                        'ducky_arenas:world_era',
                        kwargs={'era': item.code.lower()},
                    ) if item.code in available_map_codes else None,
                    'transition_url': (
                        reverse(
                            'ducky_arenas:map_transition',
                            kwargs={'era': item.code.lower()},
                        )
                        if item.code in available_map_codes and item.code in unlocked_codes
                        else None
                    ),
                    'unlocked': item.code in unlocked_codes,
                    'map_available': item.code in available_map_codes,
                    'required_xp': item.minimum_xp,
                }
                for item in configured_eras()
            ],
            evolution_notices=evolution_notices,
            profession_assignment=self.profession_assignment,
            evolution_notice_ack_url=reverse(
                'ducky_arenas:evolution_notice_ack',
            ),
            characteristics=official_characteristics(self.progress_snapshot),
        )
        return context


class DuckyProfessionView(DuckyArenasAccessMixin, FormView):
    template_name = 'ducky_arenas/profession.html'
    form_class = DuckyProfessionForm

    def _validate_access(self, request):
        self.progress_snapshot = get_progress_snapshot_or_none(request.user)
        unlocked = unlocked_eras_for_user(
            request.user,
            progress_snapshot=self.progress_snapshot,
        )
        if not any(era.code == 'ACT' for era in unlocked):
            messages.error(
                request,
                'La selección profesional se habilita al desbloquear la Actualidad.',
            )
            return redirect('ducky_arenas:world')

        self.profile = DuckyArenasProfileService.get_profile_for_user(request.user)
        self.assignment = (
            ProfessionAssignment.objects.select_related('profession').filter(
                profile=self.profile,
            ).first()
            if self.profile is not None
            else None
        )
        if self.assignment and self.assignment.status != ProfessionAssignment.Status.DRAFT:
            messages.info(request, 'Esta selección ya no se puede modificar desde el Core.')
            return redirect('ducky_arenas:world')
        return None

    def get(self, request, *args, **kwargs):
        response = self._validate_access(request)
        if response is not None:
            return response
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        response = self._validate_access(request)
        if response is not None:
            return response
        return super().post(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        professions = list(
            Profession.objects.filter(is_available=True).order_by(
                'display_order',
                'name',
            )
        )
        context.update(
            professions=professions,
            profession_cards=[
                {
                    'profession': profession,
                    'priorities': attribute_priority_rows(profession),
                    'access_error': profession_access_error(
                        profession,
                        self.progress_snapshot,
                    ),
                }
                for profession in professions
            ],
            selected_profession_id=(
                str(self.assignment.profession_id) if self.assignment else ''
            ),
            profession_assignment=self.assignment,
            allocation_available=False,
            current_characteristics=official_characteristics(self.progress_snapshot),
            available_points=None,
            progress_snapshot=self.progress_snapshot,
        )
        return context

    def form_valid(self, form):
        if self._validate_access(self.request) is not None:
            return redirect('ducky_arenas:world')
        profession = form.cleaned_data['profession']
        access_error = profession_access_error(
            profession,
            self.progress_snapshot,
        )
        if access_error:
            form.add_error('profession', access_error)
            return self.form_invalid(form)
        with transaction.atomic():
            profile = UserProfileArenas.objects.select_for_update().get(
                pk=self.profile.pk,
            )
            assignment, created = ProfessionAssignment.objects.get_or_create(
                profile=profile,
                defaults={
                    'profession': profession,
                    'status': ProfessionAssignment.Status.DRAFT,
                },
            )
            if not created:
                assignment = ProfessionAssignment.objects.select_for_update().get(
                    pk=assignment.pk,
                )
                if assignment.status != ProfessionAssignment.Status.DRAFT:
                    messages.error(
                        self.request,
                        'No se puede cambiar una selección enviada a EcoMotor.',
                    )
                    return redirect('ducky_arenas:world')
                if assignment.profession_id != profession.pk:
                    assignment.profession = profession
                    assignment.operation_id = uuid4()
                    assignment.save(update_fields=[
                        'profession',
                        'operation_id',
                        'updated_at',
                    ])
        messages.success(
            self.request,
            'Profesión guardada como borrador. No se han aplicado puntos.',
        )
        return redirect('ducky_arenas:world_era', era='act')


class ExplorationApiView(View):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'authentication_required'}, status=401)
        return super().dispatch(request, *args, **kwargs)

    @staticmethod
    def read_payload(request):
        try:
            payload = json.loads(request.body.decode('utf-8'))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValidationError('El cuerpo JSON no es válido.') from exc
        if not isinstance(payload, dict):
            raise ValidationError('Se esperaba un objeto JSON.')
        return payload

    def save_request_position(
        self,
        request,
        payload=None,
        state=None,
        allow_rapid=False,
    ):
        payload = payload if payload is not None else self.read_payload(request)
        state = state or validate_position(
            request.user,
            payload.get('era'),
            payload.get('x'),
            payload.get('y'),
        )
        profile = DuckyArenasProfileService.get_profile_for_user(request.user)
        if profile is None:
            raise ValidationError('El perfil de exploración no está disponible.')
        persist_exploration_state(
            profile=profile,
            world_map=state['world_map'],
            x=state['x'],
            y=state['y'],
            direction=payload.get('direction', profile.facing_direction),
            expected_version=payload.get('version'),
            allow_rapid=allow_rapid,
        )
        progress_snapshot = get_progress_snapshot_or_none(request.user)
        if progress_snapshot is not None:
            synchronize_era_progress(
                profile,
                progress_snapshot.xp_total,
                visited_era_code=state['era'],
            )
        state.update(
            direction=profile.facing_direction,
            version=profile.exploration_version,
        )
        state.pop('world_map', None)
        return state


class ExplorationStateView(ExplorationApiView):
    def get(self, request, *args, **kwargs):
        profile = DuckyArenasProfileService.get_profile_for_user(request.user)
        if (
            profile
            and profile.current_map_id
            and profile.current_map.era_id
            and profile.position_x is not None
            and profile.position_y is not None
        ):
            era = profile.current_map.era
            unlocked_codes = {
                unlocked.code for unlocked in unlocked_eras_for_user(request.user)
            }
            if era.code in unlocked_codes:
                try:
                    state = validate_position(
                        request.user,
                        era.code,
                        profile.position_x,
                        profile.position_y,
                        world_map=profile.current_map,
                    )
                except ValidationError:
                    pass
                else:
                    return JsonResponse({
                        'exploration': {
                            'era': era.code,
                            'x': state['x'],
                            'y': state['y'],
                            'direction': profile.facing_direction,
                            'version': profile.exploration_version,
                        },
                    })
        return JsonResponse({'exploration': None})

    def post(self, request, *args, **kwargs):
        try:
            state = self.save_request_position(request)
        except ExplorationStateConflict as exc:
            return JsonResponse({'error': str(exc)}, status=409)
        except ExplorationStateRateLimited as exc:
            response = JsonResponse({'error': str(exc)}, status=429)
            response['Retry-After'] = str(exc.retry_after)
            return response
        except ValidationError as exc:
            return JsonResponse({'error': exc.messages[0]}, status=400)
        return JsonResponse({'ok': True, 'exploration': state})


class MapTransitionView(ExplorationApiView):
    def post(self, request, era, *args, **kwargs):
        try:
            payload = self.read_payload(request)
            profile = DuckyArenasProfileService.get_profile_for_user(request.user)
            if profile is None:
                raise ValidationError('El perfil de exploración no está disponible.')
            target_era = get_era(era)
            target_map = WorldMap.objects.select_related('era').filter(
                era__code=target_era.code,
                era__is_available=True,
                is_available=True,
            ).first()
            if target_map is None:
                raise ValidationError('El mapa de destino no está disponible.')
            outcome = transition_exploration_map(
                profile=profile,
                target_map=target_map,
                source_era_code=payload.get('era'),
                x=payload.get('x'),
                y=payload.get('y'),
                direction=payload.get('direction', profile.facing_direction),
                expected_version=payload.get('version'),
            )
            progress_snapshot = get_progress_snapshot_or_none(request.user)
            if progress_snapshot is not None:
                synchronize_era_progress(
                    profile,
                    progress_snapshot.xp_total,
                    visited_era_code=target_era.code,
                )
        except ExplorationStateConflict as exc:
            return JsonResponse({'error': str(exc)}, status=409)
        except ValidationError as exc:
            return JsonResponse({'error': exc.messages[0]}, status=400)

        return JsonResponse({
            'ok': True,
            'redirectUrl': (
                reverse(
                    'ducky_arenas:world_era',
                    kwargs={'era': target_era.code.lower()},
                )
                + ('?checkpoint=restored' if outcome.restored_checkpoint else '?checkpoint=spawn')
            ),
            'restoredCheckpoint': outcome.restored_checkpoint,
            'exploration': {
                'era': target_era.code,
                'x': float(profile.position_x),
                'y': float(profile.position_y),
                'direction': profile.facing_direction,
                'version': profile.exploration_version,
            },
        })


class EvolutionNoticeAcknowledgeView(ExplorationApiView):
    def post(self, request, *args, **kwargs):
        try:
            payload = self.read_payload(request)
            profile = DuckyArenasProfileService.get_profile_for_user(request.user)
            if profile is None:
                raise ValidationError('El perfil de exploración no está disponible.')
            acknowledged = acknowledge_evolution_notice(
                profile,
                payload.get('era_code'),
            )
        except ValidationError as exc:
            return JsonResponse({'error': exc.messages[0]}, status=400)
        return JsonResponse({'ok': True, 'acknowledged': acknowledged})


class PortalReturnView(DuckyArenasAccessMixin, View):
    def get(self, request, *args, **kwargs):
        profile = DuckyArenasProfileService.get_profile_for_user(request.user)
        progress_snapshot = get_progress_snapshot_or_none(
            request.user,
            force_refresh=True,
        )
        if profile is not None and progress_snapshot is not None:
            synchronize_era_progress(profile, progress_snapshot.xp_total)
        if (
            profile
            and profile.current_map_id
            and profile.current_map.is_available
            and profile.current_map.era_id
            and profile.current_map.era.is_available
            and profile.position_x is not None
            and profile.position_y is not None
            and profile.current_map.era.code in {
                era.code
                for era in unlocked_eras_for_user(
                    request.user,
                    progress_snapshot=progress_snapshot,
                )
            }
        ):
            return redirect(
                'ducky_arenas:world_era',
                era=profile.current_map.era.code.lower(),
            )
        return redirect('ducky_arenas:world')


class PortalLaunchView(ExplorationApiView):
    def post(self, request, portal_id, *args, **kwargs):
        try:
            payload = self.read_payload(request)
            state = validate_position(
                request.user,
                payload.get('era'),
                payload.get('x'),
                payload.get('y'),
            )
            _portal, destination_url = authorize_portal(
                portal_id,
                state['era'],
                state['x'],
                state['y'],
                request.user,
            )
            self.save_request_position(
                request,
                payload,
                state,
                allow_rapid=True,
            )
            return_url = reverse('ducky_arenas:portal_return')
            destination_url = f'{destination_url}?{urlencode({"return_url": return_url})}'
        except ExplorationStateConflict as exc:
            return JsonResponse({'error': str(exc)}, status=409)
        except ExplorationStateRateLimited as exc:
            response = JsonResponse({'error': str(exc)}, status=429)
            response['Retry-After'] = str(exc.retry_after)
            return response
        except ValidationError as exc:
            return JsonResponse({'error': exc.messages[0]}, status=400)

        return JsonResponse({
            'ok': True,
            'redirectUrl': destination_url,
            'returnUrl': return_url,
        })
