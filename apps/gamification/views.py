"""Views for the gamification app."""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.generic import ListView, TemplateView, CreateView, View

from rest_framework import viewsets, permissions

from .models import (
    Insignia,
    InsigniaAuditEvent,
    InsigniaUsuario,
    InsigniaVersion,
    Logro,
    LogroUsuario,
    Mision,
    MisionUsuario,
    RankingDefinition,
    RankingEntry,
    RankingParticipation,
    RankingSeason,
)
from .serializers import (
    InsigniaSerializer,
    InsigniaUsuarioSerializer,
    LogroSerializer,
    MisionUsuarioSerializer,
)
from .forms import InsigniaDefinitionForm, InsigniaGrantForm, InsigniaReasonForm
from apps.courses.models import Curso, TipoCurso
from apps.users.access import has_access_role, is_platform_admin
from apps.users.models import AccessRoleAssignment, Organization, OrganizationMembership, User
from .badge_services import (
    PROGRESS_EARNED,
    badge_progress,
    can_grant_badge,
    current_badge_version,
    grant_manual_badge,
    is_badge_manager,
    revoke_badge,
    restore_badge,
    set_award_visibility,
)
from .ranking_services import ranking_rows, update_participation


# ---------------------------------------------------------------------------
# HTML Views
# ---------------------------------------------------------------------------

class InsigniasView(TemplateView):
    template_name = 'gamification/insignias.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        owned_awards = InsigniaUsuario.objects.none()
        if user.is_authenticated:
            owned_awards = InsigniaUsuario.objects.filter(usuario=user).select_related(
                'insignia', 'version', 'granted_by'
            )

        badges = Insignia.objects.filter(
            status=Insignia.Status.PUBLISHED,
            visible=True,
        )
        if user.is_authenticated:
            badges = badges.distinct()
            badges = (
                badges | Insignia.objects.filter(pk__in=owned_awards.values('insignia_id')).distinct()
            ).distinct()
            if is_platform_admin(user):
                badges = (badges | Insignia.objects.all()).distinct()
            else:
                managed_organization_ids = Organization.objects.filter(
                    kind='academy',
                    is_active=True,
                    memberships__user=user,
                    memberships__status=OrganizationMembership.Status.ACTIVE,
                    role_assignments__user=user,
                    role_assignments__role=AccessRoleAssignment.Role.ACADEMIA,
                    role_assignments__is_active=True,
                ).values('pk')
                managed_badges = Insignia.objects.filter(
                    Q(organization_id__in=managed_organization_ids) | Q(created_by=user)
                ).distinct()
                badges = (badges | managed_badges).distinct()
        category = self.request.GET.get('category', '')
        if category in Insignia.Category.values:
            badges = badges.filter(category=category)
        elif category:
            messages.error(self.request, 'La categoría seleccionada no es válida.')
        search = self.request.GET.get('q', '').strip()
        if search:
            badges = badges.filter(Q(nombre__icontains=search) | Q(descripcion__icontains=search))
        badges = badges.select_related('organization').prefetch_related('versions').order_by('category', 'nombre')

        origin = self.request.GET.get('origin', '')
        date_from_raw = self.request.GET.get('from', '')
        date_to_raw = self.request.GET.get('to', '')
        date_from = parse_date(date_from_raw or '')
        date_to = parse_date(date_to_raw or '')
        if date_from_raw and not date_from:
            messages.error(self.request, 'La fecha inicial no tiene un formato válido.')
        if date_to_raw and not date_to:
            messages.error(self.request, 'La fecha final no tiene un formato válido.')
        if date_from and date_to and date_from > date_to:
            messages.error(self.request, 'La fecha inicial debe ser anterior o igual a la fecha final.')
            date_from = date_to = None
        if origin and origin not in InsigniaUsuario.Origin.values:
            messages.error(self.request, 'El origen seleccionado no es válido.')
        history = user.is_authenticated and self.request.GET.get('history') == '1'
        if user.is_authenticated:
            if not history:
                owned_awards = owned_awards.filter(status=InsigniaUsuario.Status.VALID)
            if origin in InsigniaUsuario.Origin.values:
                owned_awards = owned_awards.filter(origin=origin)
            if date_from:
                owned_awards = owned_awards.filter(fecha_obtenida__date__gte=date_from)
            if date_to:
                owned_awards = owned_awards.filter(fecha_obtenida__date__lte=date_to)

        awards_by_badge = {award.insignia_id: award for award in owned_awards}
        if user.is_authenticated and (origin or date_from or date_to):
            badges = badges.filter(pk__in=awards_by_badge)
        badge_cards = []
        for badge in badges:
            award = awards_by_badge.get(badge.pk)
            progress = (
                {
                    'status': award.status,
                    'award': award,
                    'version': award.version,
                    'current': None,
                    'target': None,
                    'percent': None,
                    'action_url': None,
                }
                if award else (
                    badge_progress(user, badge)
                    if user.is_authenticated else {
                        'status': 'not_available',
                        'award': None,
                        'version': current_badge_version(badge),
                        'current': None,
                        'target': None,
                        'percent': None,
                        'action_url': None,
                    }
                )
            )
            if not user.is_authenticated and not current_badge_version(badge):
                continue
            badge_cards.append({
                'badge': badge,
                'progress': progress,
                'detail_url': reverse('gamification:insignia_detalle', args=[badge.pk]),
            })

        current_award_count = InsigniaUsuario.objects.filter(
            usuario=user,
            status=InsigniaUsuario.Status.VALID,
        ).count() if user.is_authenticated else 0
        managed_organizations = []
        if user.is_authenticated:
            managed_organizations = [
                membership.organization
                for membership in OrganizationMembership.objects.filter(
                    user=user,
                    status=OrganizationMembership.Status.ACTIVE,
                    organization__is_active=True,
                    organization__kind='academy',
                    organization__role_assignments__user=user,
                    organization__role_assignments__role=AccessRoleAssignment.Role.ACADEMIA,
                    organization__role_assignments__is_active=True,
                ).select_related('organization').distinct()
            ]
        can_manage_catalog = user.is_authenticated and (
            is_platform_admin(user) or bool(managed_organizations)
        )
        context.update({
            'badge_cards': badge_cards,
            'insignias_con_estado': [
                (
                    item['badge'],
                    item['progress']['status'] in {
                        PROGRESS_EARNED,
                        InsigniaUsuario.Status.VALID,
                    },
                )
                for item in badge_cards
            ],
            'total_obtenidas': current_award_count,
            'total_insignias': len(badge_cards),
            'total_vigentes': current_award_count,
            'categories': Insignia.Category.choices,
            'origin_choices': InsigniaUsuario.Origin.choices,
            'selected_category': category,
            'selected_search': search,
            'selected_origin': origin,
            'selected_from': self.request.GET.get('from', ''),
            'selected_to': self.request.GET.get('to', ''),
            'show_history': history,
            'is_visitor': not user.is_authenticated,
            'can_manage_catalog': can_manage_catalog,
            'can_manage_global_catalog': user.is_authenticated and is_platform_admin(user),
            'can_manage_organizations': managed_organizations,
            'login_url': settings.LOGIN_URL,
            'tipos_curso': TipoCurso.objects.filter(activo=True),
            'cursos': Curso.objects.filter(activo=True).select_related('tipo_curso'),
        })
        return context


class InsigniaDetailView(TemplateView):
    template_name = 'gamification/insignia_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        badge = get_object_or_404(Insignia.objects.select_related('organization'), pk=self.kwargs['pk'])
        user = self.request.user
        can_manage = user.is_authenticated and is_badge_manager(user, badge)
        own_award = None
        if user.is_authenticated:
            own_award = InsigniaUsuario.objects.filter(
                usuario=user,
                insignia=badge,
            ).select_related('version').first()
        if not (
            badge.status == Insignia.Status.PUBLISHED and badge.visible
        ) and own_award is None and not can_manage:
            raise PermissionDenied
        context['badge'] = badge
        context['version'] = own_award.version if own_award else current_badge_version(badge)
        context['award'] = own_award
        context['progress'] = badge_progress(user, badge) if user.is_authenticated else None
        context['can_manage'] = can_manage
        return context


class PublicBadgeCollectionView(TemplateView):
    template_name = 'gamification/public_badge_collection.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        owner = get_object_or_404(User, username=self.kwargs['username'], is_active=True)
        context['owner'] = owner
        context['awards'] = InsigniaUsuario.objects.filter(
            usuario=owner,
            status=InsigniaUsuario.Status.VALID,
            is_public=True,
            insignia__status=Insignia.Status.PUBLISHED,
            insignia__visible=True,
        ).select_related('insignia', 'version').order_by('-fecha_obtenida')
        return context


class InsigniaVisibilityView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request, pk, *args, **kwargs):
        award = get_object_or_404(InsigniaUsuario, pk=pk, usuario=request.user)
        value = request.POST.get('is_public')
        if value not in {'true', 'false'}:
            return HttpResponseBadRequest('Valor de visibilidad no válido.')
        try:
            set_award_visibility(request.user, award, is_public=value == 'true')
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        else:
            messages.success(request, 'Visibilidad actualizada.')
        return redirect('gamification:insignias')


class LogrosView(LoginRequiredMixin, TemplateView):
    template_name = 'gamification/logros.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        obtenidos_ids = set(
            LogroUsuario.objects.filter(usuario=user).values_list('logro_id', flat=True)
        )
        logros_visibles = Logro.objects.filter(oculto=False).select_related('insignia')
        context['insignias_ganadas_por_logros'] = [
            logro.insignia
            for logro in logros_visibles
            if logro.id in obtenidos_ids and logro.insignia_id
        ]
        context['logros_con_estado'] = [(l, l.id in obtenidos_ids) for l in logros_visibles]
        return context


class MisionesView(LoginRequiredMixin, ListView):
    template_name = 'gamification/misiones.html'
    context_object_name = 'misiones_usuario'

    def get_queryset(self):
        return MisionUsuario.objects.filter(
            usuario=self.request.user
        ).select_related('mision').order_by('completada', '-fecha_asignada')


class RankingView(TemplateView):
    template_name = 'gamification/ranking.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        rankings = list(
            RankingDefinition.objects.filter(
                status=RankingDefinition.Status.PUBLISHED,
                scope='public',
            ).prefetch_related('seasons__rule_version')
        )
        requested_slug = self.request.GET.get('ranking', '')
        definition = next(
            (item for item in rankings if item.slug == requested_slug),
            rankings[0] if rankings else None,
        )
        season = None
        rows = []
        if definition:
            requested_season = self.request.GET.get('season', '')
            seasons = list(definition.seasons.filter(
                status__in=[RankingSeason.Status.ACTIVE, RankingSeason.Status.CLOSED]
            ).select_related('rule_version').order_by('-starts_on'))
            season = next(
                (item for item in seasons if str(item.pk) == requested_season),
                next((item for item in seasons if item.status == RankingSeason.Status.ACTIVE), seasons[0] if seasons else None),
            )
            if season:
                source_rows = ranking_rows(season)
                rows = [
                    {
                        'user_id': row.user_id if isinstance(row, RankingEntry) else row['user'].pk,
                        'alias': row.alias if isinstance(row, RankingEntry) else row['alias'],
                        'score': row.score if isinstance(row, RankingEntry) else row['score'],
                        'position': row.position if isinstance(row, RankingEntry) else row['position'],
                    }
                    for row in source_rows
                ]
        page = Paginator(rows, 25).get_page(self.request.GET.get('page'))
        participation = (
            RankingParticipation.objects.filter(ranking=definition, user=self.request.user).first()
            if definition and self.request.user.is_authenticated else None
        )
        context.update({
            'rankings': rankings,
            'selected_ranking': definition,
            'seasons': list(definition.seasons.filter(
                status__in=[RankingSeason.Status.ACTIVE, RankingSeason.Status.CLOSED]
            ).order_by('-starts_on')) if definition else [],
            'selected_season': season,
            'ranking_rows': page.object_list,
            'page_obj': page,
            'is_paginated': page.has_other_pages(),
            'my_position': next(
                (row['position'] for row in rows if self.request.user.is_authenticated and row['user_id'] == self.request.user.pk),
                None,
            ),
            'participation': participation,
            'can_withdraw': bool(participation and participation.is_active),
            'can_manage_participation': bool(
                participation and participation.is_active
                or self.request.user.is_authenticated
                and definition
                and definition.seasons.filter(
                    status__in=[RankingSeason.Status.PREPARED, RankingSeason.Status.ACTIVE]
                ).exists()
            ),
            'can_join': bool(
                self.request.user.is_authenticated
                and definition
                and definition.seasons.filter(
                    status__in=[RankingSeason.Status.PREPARED, RankingSeason.Status.ACTIVE]
                ).exists()
            ),
            'metric_label': dict(RankingDefinition.Metric.choices).get(definition.metric, '')
            if definition else '',
        })
        return context


class RankingParticipationView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request, slug, *args, **kwargs):
        ranking = get_object_or_404(
            RankingDefinition,
            slug=slug,
            status=RankingDefinition.Status.PUBLISHED,
            scope='public',
        )
        enabled_value = request.POST.get('enabled')
        if enabled_value not in {'true', 'false'}:
            return HttpResponseBadRequest('La opción de participación no es válida.')
        try:
            update_participation(
                request.user,
                ranking,
                enabled=enabled_value == 'true',
                alias=request.POST.get('alias', ''),
            )
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        else:
            messages.success(
                request,
                'Participación actualizada.' if enabled_value == 'true'
                else 'Has salido del ranking. Tus resultados personales se conservan.',
            )
        if request.POST.get('source') == 'dashboard':
            return redirect(f"{reverse('users:dashboard')}#ranking-participation")
        return redirect(f"{reverse('gamification:ranking')}?ranking={ranking.slug}")


class ComoGanarPuntosView(LoginRequiredMixin, TemplateView):
    template_name = 'gamification/como_ganar_puntos.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        from apps.courses.models import TemaRecurso

        context['formas_automaticas'] = [
            {
                'titulo': 'Completar temas del curso',
                'detalle': 'Cada tema completado suma los puntos configurados en ese tema.',
                'valor': 'Variable segun tema',
            },
            {
                'titulo': 'Aprobar quizzes',
                'detalle': 'Cada quiz aprobado otorga su bonus de puntos.',
                'valor': 'Variable segun quiz',
            },
            {
                'titulo': 'Visualizar recursos de tema por primera vez',
                'detalle': 'Solo puntua la primera visualizacion de cada recurso.',
                'valor': f'+{TemaRecurso.PUNTOS_VISUALIZACION} puntos por recurso',
            },
            {
                'titulo': 'Completar lectura de posts del blog',
                'detalle': 'Al completar el tiempo de lectura requerido de un post.',
                'valor': 'Variable segun post',
            },
            {
                'titulo': 'Completar secciones del CV',
                'detalle': (
                    'Datos personales (+25), perfil profesional (+25), redes (+15), otros datos (+10), '
                    'experiencia (+30), educacion (+25), formacion complementaria (+20), '
                    'habilidades (+20), idiomas (+20), proyectos (+25), logros (+20), '
                    'voluntariado (+20), intereses (+10).'
                ),
                'valor': 'Hasta +265 puntos (primera vez por seccion)',
            },
            {
                'titulo': 'Completar misiones',
                'detalle': 'Las misiones activas otorgan su recompensa al completarlas.',
                'valor': 'Variable segun mision',
            },
            {
                'titulo': 'Abrir enlaces importantes por primera vez',
                'detalle': 'Cada enlace puntua una sola vez por usuario.',
                'valor': '+1 punto por enlace',
            },
        ]

        context['formas_admin'] = [
            {
                'titulo': 'Logros asignados por admin',
                'detalle': 'El equipo puede asignar logros con una cantidad concreta de puntos.',
                'valor': 'Variable segun logro',
            },
            {
                'titulo': 'Misiones asignadas por admin con puntos inmediatos',
                'detalle': 'En algunos casos, el admin puede aplicar puntos al asignar la mision.',
                'valor': 'Variable segun mision',
            },
            {
                'titulo': 'Bug validado por admin',
                'detalle': 'Cuando un bug reportado se valida, recibes los puntos de recompensa.',
                'valor': 'Variable segun bug',
            },
        ]

        context['formas_gastar'] = [
            'Canjear recompensas descuenta puntos disponibles.',
            'Tu nivel depende de puntos totales acumulados, no del saldo disponible.',
        ]
        return context


class BadgeDefinitionAccessMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not self._can_manage_any_badge(request.user):
            raise PermissionDenied('No tienes permisos para administrar insignias.')
        return super().dispatch(request, *args, **kwargs)

    @staticmethod
    def _can_manage_any_badge(user):
        if is_platform_admin(user):
            return True
        return any(
            has_access_role(user, AccessRoleAssignment.Role.ACADEMIA, organization=organization)
            for organization in Organization.objects.filter(
                kind='academy',
                is_active=True,
                memberships__user=user,
                memberships__status=OrganizationMembership.Status.ACTIVE,
                role_assignments__user=user,
                role_assignments__role=AccessRoleAssignment.Role.ACADEMIA,
                role_assignments__is_active=True,
            )
        )


class InsigniaCreateView(BadgeDefinitionAccessMixin, CreateView):
    model = Insignia
    form_class = InsigniaDefinitionForm
    template_name = 'gamification/insignia_form.html'
    success_url = reverse_lazy('gamification:insignias')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['actor'] = self.request.user
        return kwargs

    def form_valid(self, form):
        with transaction.atomic():
            badge = form.save(commit=False)
            badge.tipo = self._legacy_type(form.cleaned_data['rule_type'])
            badge.requisito_valor = form.cleaned_data['threshold']
            badge.curso_objetivo = form.cleaned_data.get('course') if badge.tipo == 'curso' else None
            badge.tema_objetivo = form.cleaned_data.get('topic') if badge.tipo == 'tema' else None
            badge.created_by = self.request.user
            if is_platform_admin(self.request.user):
                badge.status = Insignia.Status.DRAFT
            else:
                badge.organization = form.cleaned_data.get('organization')
                if not badge.organization or not is_badge_manager(
                    self.request.user,
                    organization=badge.organization,
                ):
                    form.add_error('organization', 'Selecciona una organización que administras.')
                    return self.form_invalid(form)
                badge.status = Insignia.Status.REVIEW
            badge.full_clean()
            badge.save()
            self._save_version(form.cleaned_data, badge, self.request.user, version=1)
            InsigniaAuditEvent.objects.create(
                actor=self.request.user,
                badge=badge,
                action=InsigniaAuditEvent.Action.CREATED,
                reason='Definición inicial de insignia.',
            )
            messages.success(
                self.request,
                'Propuesta enviada a revisión.' if badge.status == Insignia.Status.REVIEW
                else 'Borrador de insignia creado.',
            )
            return super().form_valid(form)

    @staticmethod
    def _legacy_type(rule_type):
        return {
            InsigniaVersion.RuleType.COURSE_COMPLETED: 'curso',
            InsigniaVersion.RuleType.TOPIC_COMPLETED: 'tema',
            InsigniaVersion.RuleType.TOPICS_COUNT: 'especial',
            InsigniaVersion.RuleType.STREAK_DAYS: 'streak',
            InsigniaVersion.RuleType.TOTAL_POINTS: 'puntos',
            InsigniaVersion.RuleType.QUIZ_PASSED: 'especial',
        }[rule_type]

    @staticmethod
    def _save_version(cleaned_data, badge, actor, *, version):
        from .models import InsigniaVersion

        requirement = InsigniaVersion(
            badge=badge,
            version=version,
            rule_type=cleaned_data['rule_type'],
            course=cleaned_data.get('course'),
            topic=cleaned_data.get('topic'),
            quiz=cleaned_data.get('quiz'),
            threshold=cleaned_data['threshold'],
            public_requirements=cleaned_data['public_requirements'],
            source_description=cleaned_data['source_description'],
            verification_description=cleaned_data['verification_description'],
            effective_at=timezone.now(),
            created_by=actor,
        )
        requirement.full_clean()
        requirement.save()


class InsigniaUpdateView(BadgeDefinitionAccessMixin, View):
    template_name = 'gamification/insignia_form.html'

    def get_object(self):
        queryset = Insignia.objects.select_for_update() if self.request.method == 'POST' else Insignia.objects.all()
        badge = get_object_or_404(queryset, pk=self.kwargs['pk'])
        if not is_badge_manager(self.request.user, badge):
            raise PermissionDenied
        if badge.status == Insignia.Status.ARCHIVED:
            raise PermissionDenied('Una insignia archivada no se puede editar.')
        return badge

    def get(self, request, *args, **kwargs):
        badge = self.get_object()
        form = InsigniaDefinitionForm(instance=badge, actor=request.user)
        return self.render_form(form, badge)

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        badge = self.get_object()
        form = InsigniaDefinitionForm(request.POST, request.FILES, instance=badge, actor=request.user)
        if not form.is_valid():
            return self.render_form(form, badge)
        from .models import InsigniaVersion

        is_platform = is_platform_admin(request.user)
        updated_badge = form.save(commit=False)
        updated_badge.tipo = InsigniaCreateView._legacy_type(form.cleaned_data['rule_type'])
        updated_badge.requisito_valor = form.cleaned_data['threshold']
        updated_badge.curso_objetivo = form.cleaned_data.get('course') if updated_badge.tipo == 'curso' else None
        updated_badge.tema_objetivo = form.cleaned_data.get('topic') if updated_badge.tipo == 'tema' else None
        if is_platform:
            updated_badge.organization = form.cleaned_data.get('organization')
        else:
            updated_badge.organization = badge.organization
            updated_badge.status = Insignia.Status.REVIEW
        updated_badge.full_clean()
        updated_badge.save()
        latest_version = badge.versions.order_by('-version').first()
        InsigniaCreateView._save_version(
            form.cleaned_data,
            updated_badge,
            request.user,
            version=(latest_version.version + 1) if latest_version else 1,
        )
        InsigniaAuditEvent.objects.create(
            actor=request.user,
            badge=updated_badge,
            action=InsigniaAuditEvent.Action.UPDATED,
            reason='Se añadió una nueva versión de requisitos.',
            metadata={'previous_version': latest_version.version if latest_version else None},
        )
        messages.success(request, 'Insignia actualizada y requisitos versionados.')
        return redirect('gamification:insignias')

    def render_form(self, form, badge):
        return render(
            self.request,
            self.template_name,
            {'form': form, 'object': badge, 'is_proposal': badge.status == Insignia.Status.REVIEW},
        )


class InsigniaStatusView(LoginRequiredMixin, View):
    http_method_names = ['post']

    @transaction.atomic
    def post(self, request, pk, action, *args, **kwargs):
        badge = get_object_or_404(Insignia, pk=pk)
        if action not in {'publish', 'archive'}:
            return HttpResponseBadRequest('Acción no válida.')
        if action == 'publish' and not is_platform_admin(request.user):
            raise PermissionDenied('Solo ADMIN puede publicar definiciones.')
        if action == 'archive' and not is_badge_manager(request.user, badge):
            raise PermissionDenied('No puedes archivar esta definición.')
        if action == 'publish':
            if not badge.versions.exists():
                raise ValidationError('No se puede publicar una insignia sin una versión de requisitos.')
            badge.status = Insignia.Status.PUBLISHED
            badge.reviewed_by = request.user
            event = InsigniaAuditEvent.Action.PUBLISHED
            detail = 'Insignia publicada.'
        else:
            badge.status = Insignia.Status.ARCHIVED
            event = InsigniaAuditEvent.Action.ARCHIVED
            detail = 'Insignia archivada. Las concesiones existentes se conservan.'
        badge.save(update_fields=['status', 'reviewed_by', 'updated_at'])
        InsigniaAuditEvent.objects.create(
            actor=request.user,
            badge=badge,
            action=event,
            reason=request.POST.get('reason', '').strip(),
        )
        messages.success(request, detail)
        return redirect('gamification:insignias')


class InsigniaGrantView(LoginRequiredMixin, TemplateView):
    template_name = 'gamification/insignia_grant.html'

    def get_badge(self):
        badge = get_object_or_404(Insignia, pk=self.kwargs['pk'])
        if (
            badge.issue_method != Insignia.IssueMethod.MANUAL
            or badge.status != Insignia.Status.PUBLISHED
        ):
            raise PermissionDenied
        if is_badge_manager(self.request.user, badge):
            return badge
        version = current_badge_version(badge)
        course = version.course if version else None
        if version and version.rule_type == InsigniaVersion.RuleType.TOPIC_COMPLETED:
            course = version.topic.curso
        if course is None:
            raise PermissionDenied
        from apps.lms.models import CourseMembership

        if not CourseMembership.objects.filter(
            user=self.request.user,
            course=course,
            role__in=[
                CourseMembership.Role.OWNER,
                CourseMembership.Role.TEACHER,
                CourseMembership.Role.ASSISTANT,
            ],
            status=CourseMembership.Status.ACTIVE,
        ).exists():
            raise PermissionDenied
        return badge

    def get_form(self, data=None):
        badge = self.get_badge()
        if is_platform_admin(self.request.user):
            recipients = User.objects.filter(is_active=True).exclude(pk=self.request.user.pk).order_by('username')
        elif badge.organization_id and is_badge_manager(self.request.user, badge):
            recipients = User.objects.filter(
                is_active=True,
            ).exclude(
                pk=self.request.user.pk,
            ).filter(
                organization_memberships__organization=badge.organization,
                organization_memberships__status=OrganizationMembership.Status.ACTIVE,
            ).distinct().order_by('username')
        else:
            from apps.lms.models import CourseMembership
            from apps.courses.models import InscripcionCurso

            version = current_badge_version(badge)
            course = version.course if version else None
            if version and version.rule_type == InsigniaVersion.RuleType.TOPIC_COMPLETED:
                course = version.topic.curso
            if course is None:
                recipients = User.objects.none()
            else:
                is_assigned_teacher = CourseMembership.objects.filter(
                    user=self.request.user,
                    course=course,
                    role__in=[
                        CourseMembership.Role.OWNER,
                        CourseMembership.Role.TEACHER,
                        CourseMembership.Role.ASSISTANT,
                    ],
                    status=CourseMembership.Status.ACTIVE,
                ).exists()
                recipient_ids = set(CourseMembership.objects.filter(
                    course=course,
                    role=CourseMembership.Role.STUDENT,
                    status__in=[CourseMembership.Status.ACTIVE, CourseMembership.Status.COMPLETED],
                ).values_list('user_id', flat=True))
                recipient_ids.update(InscripcionCurso.objects.filter(
                    curso=course,
                ).values_list('usuario_id', flat=True))
                recipients = User.objects.filter(
                    pk__in=recipient_ids,
                    is_active=True,
                ).exclude(
                    pk=self.request.user.pk,
                ).order_by('username') if is_assigned_teacher else User.objects.none()
        return InsigniaGrantForm(data, recipient_queryset=recipients)

    def get(self, request, *args, **kwargs):
        badge = self.get_badge()
        return self.render_form(self.get_form(), badge)

    def post(self, request, *args, **kwargs):
        badge = self.get_badge()
        form = self.get_form(request.POST)
        if form.is_valid():
            try:
                grant_manual_badge(
                    request.user,
                    form.cleaned_data['recipient'],
                    badge,
                    reason=form.cleaned_data['reason'],
                    evidence=form.cleaned_data['evidence'],
                )
            except (PermissionDenied, ValidationError) as exc:
                message = exc.messages[0] if isinstance(exc, ValidationError) else str(exc)
                form.add_error(None, message)
            else:
                messages.success(request, 'Insignia concedida y acción auditada.')
                return redirect('gamification:insignias')
        return self.render_form(form, badge)

    def render_form(self, form, badge):
        return render(
            self.request,
            self.template_name,
            {'form': form, 'badge': badge},
        )


class InsigniaRevokeView(LoginRequiredMixin, View):
    http_method_names = ['get', 'post']
    template_name = 'gamification/insignia_reason.html'

    def dispatch(self, request, *args, **kwargs):
        award = get_object_or_404(
            InsigniaUsuario.objects.select_related('insignia'),
            pk=kwargs['pk'],
        )
        if not is_badge_manager(request.user, award.insignia):
            raise PermissionDenied
        self.award = award
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        return render(
            request,
            self.template_name,
            {'form': InsigniaReasonForm(), 'badge': self.award.insignia, 'action': 'revocar'},
        )

    def post(self, request, *args, **kwargs):
        form = InsigniaReasonForm(request.POST)
        if form.is_valid():
            try:
                revoke_badge(request.user, self.award, reason=form.cleaned_data['reason'])
            except (PermissionDenied, ValidationError) as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, 'Concesión revocada y registrada en auditoría.')
                return redirect('gamification:insignias')
        return render(
            request,
            self.template_name,
            {'form': form, 'badge': self.award.insignia, 'action': 'revocar'},
        )


class InsigniaRestoreView(InsigniaRevokeView):
    def post(self, request, *args, **kwargs):
        form = InsigniaReasonForm(request.POST)
        if form.is_valid():
            try:
                restore_badge(request.user, self.award, reason=form.cleaned_data['reason'])
            except (PermissionDenied, ValidationError) as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, 'Concesión restaurada y registrada en auditoría.')
                return redirect('gamification:insignias')
        return render(
            request,
            self.template_name,
            {'form': form, 'badge': self.award.insignia, 'action': 'restaurar'},
        )


class InsigniaAuditView(LoginRequiredMixin, ListView):
    template_name = 'gamification/insignia_audit.html'
    context_object_name = 'events'
    paginate_by = 50

    def dispatch(self, request, *args, **kwargs):
        if not is_platform_admin(request.user):
            raise PermissionDenied('La auditoría general requiere rol ADMIN.')
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return InsigniaAuditEvent.objects.select_related('actor', 'badge', 'award').order_by('-created_at')



# ---------------------------------------------------------------------------
# DRF ViewSets
# ---------------------------------------------------------------------------

class InsigniaViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = InsigniaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Insignia.objects.filter(
            visible=True,
            status=Insignia.Status.PUBLISHED,
        ).select_related('organization')


class InsigniaUsuarioViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = InsigniaUsuarioSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return InsigniaUsuario.objects.filter(
            usuario=self.request.user,
            status=InsigniaUsuario.Status.VALID,
        ).select_related('insignia', 'version')


class MisionUsuarioViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MisionUsuarioSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return MisionUsuario.objects.filter(usuario=self.request.user)
