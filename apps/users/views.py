"""Views for the users app — both HTML and API ViewSets."""
import base64
import csv
import io
import mimetypes
import os
import re
import uuid
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

from allauth.account.views import LoginView, LogoutView
from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile, File
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods, require_POST
from django.views.generic import (
    CreateView, DetailView, UpdateView, TemplateView, View, ListView
)

from rest_framework import permissions, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.response import Response

from .access import access_snapshot, can
from apps.prompts.internal import render_internal_prompt
from .dashboard_models import PersonalGoal
from .models import AccessMembership, RamaProfesional, User, Notificacion
from .notifications import platform_notifications, set_platform_read
from .forms import PersonalGoalForm, RegistroForm, PerfilForm
from .serializers import (
    NotificacionSerializer,
    RamaProfesionalSerializer,
    UserProfessionalBranchSerializer,
    UserSerializer,
)




def admin_csv_manager(request):
    if not request.user.is_authenticated or not request.user.is_staff:
        raise PermissionDenied

    model_path = request.GET.get('model') or request.POST.get('model')
    if not model_path:
        return HttpResponse('Falta el parámetro model.', status=400)

    try:
        app_label, model_name = model_path.split('.', 1)
        model_class = apps.get_model(app_label, model_name)
    except Exception:
        return HttpResponse('Modelo no válido.', status=400)

    action = (request.GET.get('action') or request.POST.get('action') or 'export').lower()

    if request.method == 'POST' and action == 'import':
        csv_file = request.FILES.get('csv_file')
        if not csv_file:
            return HttpResponse('Falta el archivo CSV.', status=400)

        text = csv_file.read().decode('utf-8-sig', errors='replace')
        text = re.sub(r'[\x00-\x1f\x7f]+', '', text)
        rows = list(csv.DictReader(io.StringIO(text)))
        model_class.objects.all().delete()

        created = 0
        normalized_fields = [field for field in (rows[0].keys() if rows else [])]
        for row in rows:
            if not row:
                continue
            clean_row = {}
            for field_name, field_value in row.items():
                if not field_name:
                    continue
                value = '' if field_value is None else str(field_value)
                value = re.sub(r'[\x00-\x1f\x7f]+', '', value).strip()
                if field_name == 'horario' and value.lower().replace('ñ', 'n') == 'mana':
                    value = 'Mañana'
                clean_row[field_name] = value
            if not any(clean_row.values()):
                continue
            model_class.objects.create(**clean_row)
            created += 1

        return JsonResponse({'ok': True, 'count': created, 'model': model_path})

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{model_name}.csv"'

    fields = [field.name for field in model_class._meta.fields]
    writer = csv.writer(response)
    writer.writerow(fields)

    for instance in model_class.objects.all():
        row = []
        for field_name in fields:
            value = getattr(instance, field_name)
            if callable(value):
                value = value()
            row.append('' if value is None else value)
        writer.writerow(row)

    return response


def _generar_avatar_ducky_temporal(bytes_imagen: bytes) -> str:
    media_dir = Path(settings.MEDIA_ROOT) / 'avatars' / 'ducky_ai'
    media_dir.mkdir(parents=True, exist_ok=True)
    filename = f'ducky_{uuid.uuid4().hex}.png'
    temp_path = media_dir / filename
    temp_path.write_bytes(bytes_imagen)
    return str(temp_path)


def _limpiar_avatar_ducky_pendiente(request):
    temp_path = request.session.get('pending_ducky_avatar')
    if temp_path and os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except OSError:
            pass

    request.session.pop('pending_ducky_avatar', None)
    request.session.pop('pending_ducky_avatar_nonce', None)
    request.session.modified = True


def generar_avatar_ducky_con_ia(usuario, archivo=None):
    """Genera una imagen de avatar Ducky basada en la foto del usuario."""
    if not getattr(settings, 'OPENAI_API_KEY', ''):
        raise RuntimeError('La IA no está configurada. Añade OPENAI_API_KEY al archivo .env.')

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError('La librería openai no está instalada.') from exc

    nombre_usuario = usuario.get_full_name() or usuario.username
    prompt = render_internal_prompt('users.ducky_avatar', nombre_usuario=nombre_usuario)

    try:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        request_kwargs = {
            'model': 'gpt-image-1',
            'prompt': prompt,
            'size': '1024x1024',
        }

        if archivo is not None:
            if hasattr(archivo, 'read'):
                archivo_bytes = archivo.read()
                archivo_name = getattr(archivo, 'name', 'avatar') or 'avatar'
                archivo_content_type = getattr(archivo, 'content_type', None) or mimetypes.guess_type(archivo_name)[0] or 'image/jpeg'
                if hasattr(archivo, 'seek'):
                    archivo.seek(0)
            else:
                archivo_bytes = archivo
                archivo_name = 'avatar'
                archivo_content_type = 'image/jpeg'

            request_kwargs['image'] = (archivo_name, archivo_bytes, archivo_content_type)
            response = client.images.edit(**request_kwargs)
        else:
            response = client.images.generate(**request_kwargs)

        image_record = response.data[0]
        if getattr(image_record, 'b64_json', None):
            return base64.b64decode(image_record.b64_json)

        image_url = getattr(image_record, 'url', None)
        if not image_url:
            raise RuntimeError('La respuesta de la IA no devolvió una imagen válida.')

        import urllib.request
        with urllib.request.urlopen(image_url) as resp:  # noqa: S310
            return resp.read()
    except Exception as exc:  # pragma: no cover - depends on external API
        raise RuntimeError(f'No se pudo generar el avatar con IA: {exc}') from exc


@login_required
@require_http_methods(['POST'])
def _limpiar_avatar_ducky_pendiente(request):
    temp_path = request.session.get('pending_ducky_avatar')
    if temp_path and os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except OSError:
            pass
    request.session.pop('pending_ducky_avatar', None)


@login_required
@require_http_methods(['POST'])
def generar_avatar_ducky(request):
    archivo = request.FILES.get('avatar')
    if not archivo:
        return JsonResponse({'ok': False, 'error': 'Debes subir una foto para generar el avatar Ducky.'}, status=400)

    _limpiar_avatar_ducky_pendiente(request)

    try:
        imagen_bytes = generar_avatar_ducky_con_ia(request.user, archivo)
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)

    temp_path = _generar_avatar_ducky_temporal(imagen_bytes)
    request.session['pending_ducky_avatar'] = temp_path
    request.session['pending_ducky_avatar_nonce'] = uuid.uuid4().hex
    request.session.modified = True

    encoded = base64.b64encode(imagen_bytes).decode('utf-8')
    return JsonResponse({
        'ok': True,
        'preview_url': f'data:image/png;base64,{encoded}',
        'preview_nonce': request.session['pending_ducky_avatar_nonce'],
    })


@login_required
@require_http_methods(['POST'])
def confirmar_avatar_ducky(request):
    temp_path = request.session.get('pending_ducky_avatar')
    if not temp_path or not os.path.exists(temp_path):
        return JsonResponse({'ok': False, 'error': 'No hay un avatar generado pendiente de confirmación.'}, status=400)

    try:
        with open(temp_path, 'rb') as generated_file:
            filename = f'avatars/ducky_{request.user.pk}_{uuid.uuid4().hex}.png'
            request.user.avatar.save(filename, File(generated_file), save=False)
            request.user.save(update_fields=['avatar'])
        os.remove(temp_path)
        request.session.pop('pending_ducky_avatar', None)
        return JsonResponse({'ok': True, 'avatar_url': request.user.avatar.url})
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)


class LandingView(TemplateView):
    """Public landing page showcasing platform capabilities."""
    template_name = 'landing.html'


@require_POST
def activate_auth_navigation(request):
    request.session['auth_navigation_active'] = True
    return JsonResponse({'ok': True})


class ActivatedLoginView(LoginView):
    def dispatch(self, request, *args, **kwargs):
        if not request.session.get('auth_navigation_active'):
            return redirect('landing')
        return super().dispatch(request, *args, **kwargs)


class DeactivatingLogoutView(LogoutView):
    def post(self, *args, **kwargs):
        self.request.session.pop('auth_navigation_active', None)
        return super().post(*args, **kwargs)


# ---------------------------------------------------------------------------
# HTML Views
# ---------------------------------------------------------------------------

class RegistroView(CreateView):
    """User registration page."""
    form_class = RegistroForm
    template_name = 'users/register.html'
    success_url = reverse_lazy('users:dashboard')

    def dispatch(self, request, *args, **kwargs):
        if not request.session.get('auth_navigation_active'):
            return redirect('landing')
        if request.user.is_authenticated:
            return redirect('users:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()
        login(self.request, user, backend='django.contrib.auth.backends.ModelBackend')
        messages.success(self.request, f'¡Bienvenido/a {user.first_name}! Tu cuenta ha sido creada.')
        return redirect(self.success_url)

class AccessSummaryView(LoginRequiredMixin, TemplateView):
    template_name = 'users/access_summary.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['access'] = access_snapshot(self.request.user)
        return context


class BillingDashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'users/billing.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from .access import effective_plan
        from .billing_models import (
            BillingConfiguration,
            BillingOrder,
            Charge,
            Invoice,
            MembershipContract,
            ServiceProduct,
            UserVoucher,
        )
        from .billing_services import PERIOD_MONTHS, billing_options, service_price
        from .billing_models import BillingSubscription
        from .billing_views import quote_token
        from .stripe_billing import PLAN_RANK, subscriptions_available

        user = self.request.user
        plan = effective_plan(user)
        options = billing_options()
        subscriptions_enabled = subscriptions_available()
        pending_subscription = BillingSubscription.objects.filter(user=user, status='pending').first()
        context.update({
            'subscriptions_enabled': subscriptions_enabled,
            'pending_subscription': pending_subscription,
            'subscriptions': BillingSubscription.objects.filter(
                user=user, status__in=['active', 'past_due'],
            ).select_related('initial_order').order_by('-created_at'),
            'billing_options': options,
            'membership_periods': PERIOD_MONTHS,
            'billing_plan': plan,
            'billing_configuration': BillingConfiguration.current(),
            'membership_contracts': MembershipContract.objects.filter(
                user=user,
            ).order_by('-created_at')[:20],
            'orders': BillingOrder.objects.filter(
                user=user,
            ).order_by('-created_at')[:20],
            'charges': Charge.objects.filter(
                user=user,
            ).select_related('order').prefetch_related('payments', 'invoices').order_by('-created_at')[:20],
            'invoices': Invoice.objects.filter(
                user=user,
            ).select_related('charge').order_by('-created_at')[:20],
            'vouchers': UserVoucher.objects.filter(
                user=user,
            ).select_related('voucher').order_by('-granted_at')[:20],
            'service_offerings': [
                {
                    'product': product,
                    'price': service_price(product, plan),
                    'can_order_free': (
                        service_price(product, plan) == 0
                        and product.fulfillment == ServiceProduct.Fulfillment.AUTOMATIC
                        and bool(product.entitlement_code)
                        and not hasattr(product, 'energy_recharge')
                    ),
                    'idempotency_key': uuid.uuid4(),
                }
                for product in ServiceProduct.objects.filter(is_active=True, base_price__isnull=False)
            ],
        })
        context['membership_prices'] = [
            {
                'plan': plan,
                'period': period,
                'period_label': {
                    'monthly': 'Mensual',
                    'semiannual': 'Semestral',
                    'annual': 'Anual',
                }[period],
                **options['prices'][f'{plan}_{period}'],
                'is_upgrade': PLAN_RANK[plan] > PLAN_RANK[context['billing_plan']],
                'quote': quote_token(user, plan, period, options['prices'][f'{plan}_{period}']['amount']),
            }
            for plan in ('plus', 'pro')
            for period in PERIOD_MONTHS
        ]
        return context


class FreeServiceOrderView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        from .billing_services import BillingUnavailable, TERMS_VERSION, create_zero_cost_service_order

        if request.POST.get('accept_terms') != 'yes':
            messages.error(request, 'Debes aceptar las condiciones para continuar.')
            return redirect('users:billing')
        accepted_terms = {
            'accepted': True,
            'version': TERMS_VERSION,
            'accepted_at': timezone.now().isoformat(),
        }
        try:
            order = create_zero_cost_service_order(
                user=request.user,
                service_code=request.POST.get('service_code', ''),
                idempotency_key=request.POST.get('idempotency_key', ''),
                accepted_terms=accepted_terms,
            )
        except (BillingUnavailable, ValidationError) as exc:
            detail = exc.messages[0] if isinstance(exc, ValidationError) else str(exc)
            messages.error(request, detail)
        else:
            messages.success(request, f'El servicio se ha registrado con el pedido {order.pk}.')
        return redirect('users:billing')


class DashboardView(TemplateView):
    """Personal dashboard with a data-free public demonstration."""
    template_name = 'dashboard/index.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        from .dashboard_services import dashboard_context, visitor_dashboard_context

        if not user.is_authenticated:
            context.update(visitor_dashboard_context(self.request))
        else:
            context.update(dashboard_context(user))
            from apps.gamification.ranking_services import dashboard_ranking_options
            context['dashboard_rankings'] = dashboard_ranking_options(user)
        return context


class PersonalProgressView(TemplateView):
    template_name = 'dashboard/progress.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        if not user.is_authenticated:
            context.update({
                'is_demo': True,
                'demo_progress': True,
                'is_pro': False,
                'is_plus': False,
                'login_url': settings.LOGIN_URL,
                'rows': [],
                'goals': [],
                'badges': [],
                'summary': None,
                'source_statuses': {
                    'games': 'not_implemented',
                    'employment': 'not_implemented',
                },
            })
            return context

        from .progress_services import personal_progress, compare_personal_progress

        date_from_raw = self.request.GET.get('from', '')
        date_to_raw = self.request.GET.get('to', '')
        date_from = parse_date(date_from_raw) if date_from_raw else None
        date_to = parse_date(date_to_raw) if date_to_raw else None
        if date_from_raw and not date_from:
            messages.error(self.request, 'La fecha inicial no es válida.')
        if date_to_raw and not date_to:
            messages.error(self.request, 'La fecha final no es válida.')
        if date_from and date_to and date_from > date_to:
            messages.error(self.request, 'La fecha inicial debe ser anterior o igual a la final.')
            date_from = date_to = None
        area = self.request.GET.get('area', 'all')
        try:
            progress = personal_progress(
                user,
                date_from=date_from,
                date_to=date_to,
                area=area,
            )
        except ValidationError as exc:
            messages.error(self.request, exc.messages[0])
            area = 'all'
            progress = personal_progress(user, area=area)

        is_pro = can(
            user,
            'view_plan_content',
            required_plan=AccessMembership.Plan.PRO,
        ).allowed
        is_plus = can(
            user,
            'view_plan_content',
            required_plan=AccessMembership.Plan.PLUS,
        ).allowed
        comparison = None
        compare_requested = self.request.GET.get('compare') == '1'
        if compare_requested:
            if not is_pro:
                raise PermissionDenied('La comparación de periodos requiere nivel PRO.')
            if not date_from or not date_to:
                messages.error(self.request, 'Selecciona las fechas del periodo actual para comparar.')
            else:
                comparison = compare_personal_progress(
                    user,
                    current_start=date_from,
                    current_end=date_to,
                )

        context.update(progress)
        context.update({
            'is_demo': False,
            'is_pro': is_pro,
            'is_plus': is_plus,
            'login_url': settings.LOGIN_URL,
            'selected_area': area,
            'selected_from': date_from_raw,
            'selected_to': date_to_raw,
            'comparison': comparison,
            'can_export': is_pro,
        })
        return context


class PersonalProgressExportView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        if not can(
            request.user,
            'view_plan_content',
            required_plan=AccessMembership.Plan.PRO,
        ).allowed:
            raise PermissionDenied('La exportación del resumen requiere nivel PRO.')
        from .progress_services import personal_progress

        date_from = parse_date(request.GET.get('from', '') or '')
        date_to = parse_date(request.GET.get('to', '') or '')
        if (request.GET.get('from') and not date_from) or (request.GET.get('to') and not date_to):
            raise ValidationError('Las fechas de exportación no son válidas.')
        if date_from and date_to and date_from > date_to:
            raise ValidationError('La fecha inicial debe ser anterior o igual a la final.')
        area = request.GET.get('area', 'all')
        try:
            progress = personal_progress(
                request.user,
                date_from=date_from,
                date_to=date_to,
                area=area,
            )
        except ValidationError as exc:
            return HttpResponseBadRequest(exc.messages[0])
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="mi-progreso.csv"'
        response.write('\ufeff')
        writer = csv.writer(response)
        def safe_cell(value):
            text = str(value or '')
            if text[:1] in {'=', '+', '-', '@', '\t', '\r'}:
                return "'" + text
            return text

        writer.writerow(['Resumen personal de progreso', 'No es una certificación oficial'])
        writer.writerow(['Generado', timezone.localtime(progress['generated_at']).isoformat()])
        writer.writerow(['Periodo desde', date_from.isoformat() if date_from else ''])
        writer.writerow(['Periodo hasta', date_to.isoformat() if date_to else ''])
        writer.writerow(['Métrica', 'Valor', 'Unidad', 'Estado'])
        summary = progress['summary']
        metric_rows = [
            ('Temas completados', summary['completed_lessons'], 'temas'),
            ('Cursos completados', summary['completed_courses'], 'cursos'),
            ('Intentos de evaluación', summary['quiz_attempts'], 'intentos'),
            ('Evaluaciones Nexus completadas', summary['completed_nexus_tests'], 'evaluaciones'),
            ('Insignias vigentes', summary['valid_badges'], 'insignias'),
        ]
        for label, value, unit in metric_rows:
            writer.writerow([label, value if value is not None else 'No aplicable', unit])
        writer.writerow([])
        writer.writerow(['Actividad', 'Área', 'Fecha', 'Estado o resultado', 'Regla o fuente'])
        for row in progress['rows']:
            writer.writerow([
                safe_cell(row['title']),
                safe_cell(row['area']),
                timezone.localtime(row['date']).isoformat() if row['date'] else '',
                safe_cell(row['result']),
                safe_cell(row['metric']),
            ])
        writer.writerow([])
        writer.writerow(['Limitaciones', 'Solo fuentes personales disponibles; áreas no integradas se excluyen.'])
        writer.writerow(['Datos privados de terceros', 'No incluidos'])
        return response


class PersonalGoalAccessMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not can(
            request.user,
            'view_plan_content',
            required_plan=AccessMembership.Plan.PRO,
        ).allowed:
            raise PermissionDenied('La gestión de objetivos personales requiere el nivel PRO.')
        return super().dispatch(request, *args, **kwargs)


class PersonalGoalCreateView(PersonalGoalAccessMixin, CreateView):
    model = PersonalGoal
    form_class = PersonalGoalForm
    template_name = 'dashboard/goal_form.html'
    success_url = reverse_lazy('users:dashboard')

    def form_valid(self, form):
        form.instance.user = self.request.user
        messages.success(self.request, 'Objetivo creado.')
        return super().form_valid(form)


class PersonalGoalUpdateView(PersonalGoalAccessMixin, UpdateView):
    model = PersonalGoal
    form_class = PersonalGoalForm
    template_name = 'dashboard/goal_form.html'
    success_url = reverse_lazy('users:dashboard')

    def get_queryset(self):
        return PersonalGoal.objects.filter(user=self.request.user, status=PersonalGoal.Status.ACTIVE)

    def form_valid(self, form):
        messages.success(self.request, 'Objetivo actualizado.')
        return super().form_valid(form)


class PersonalGoalStateView(PersonalGoalAccessMixin, View):
    http_method_names = ['post']

    def post(self, request, pk, action, *args, **kwargs):
        goal = get_object_or_404(
            PersonalGoal,
            pk=pk,
            user=request.user,
            status=PersonalGoal.Status.ACTIVE,
        )
        if action == 'complete':
            goal.status = PersonalGoal.Status.COMPLETED
            goal.completed_at = timezone.now()
            message = 'Objetivo marcado como completado.'
        elif action == 'archive':
            goal.status = PersonalGoal.Status.ARCHIVED
            goal.archived_at = timezone.now()
            message = 'Objetivo archivado.'
        else:
            return HttpResponseBadRequest('Acción no válida.')
        goal.full_clean()
        goal.save(update_fields=['status', 'completed_at', 'archived_at', 'updated_at'])
        messages.success(request, message)
        return redirect('users:dashboard')


class PersonalGoalExportView(PersonalGoalAccessMixin, View):
    @staticmethod
    def _safe_cell(value):
        value = '' if value is None else str(value)
        if value.lstrip()[:1] in {'=', '+', '-', '@', '\t', '\r'}:
            return "'" + value
        return value

    def get(self, request, *args, **kwargs):
        from .dashboard_services import goal_progress

        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="mis-objetivos.csv"'
        response.write('\ufeff')
        writer = csv.writer(response)
        writer.writerow([
            'Título', 'Descripción', 'Seguimiento', 'Métrica', 'Avance actual',
            'Objetivo', 'Etiqueta', 'Fecha objetivo', 'Estado',
        ])
        for goal in PersonalGoal.objects.filter(user=request.user).order_by('created_at'):
            progress = goal_progress(request.user, goal)
            writer.writerow([
                self._safe_cell(goal.title),
                self._safe_cell(goal.description),
                goal.get_tracking_type_display(),
                goal.get_source_metric_display() if goal.source_metric else '',
                progress['current'] if progress['current'] is not None else '',
                progress['target'] if progress['target'] is not None else '',
                self._safe_cell(goal.target_label),
                goal.target_date.isoformat() if goal.target_date else '',
                goal.get_status_display(),
            ])
        return response


class AccessSnapshotAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(access_snapshot(request.user))


class PerfilView(LoginRequiredMixin, DetailView):
    """Public profile page."""
    model = User
    template_name = 'users/profile.html'
    context_object_name = 'perfil'

    def get_object(self, queryset=None):
        from django.core.exceptions import PermissionDenied
        from django.shortcuts import get_object_or_404
        username = self.kwargs.get('username', self.request.user.username)
        user = get_object_or_404(User, username=username)
        # Solo el dueño del perfil o admin/staff puede acceder
        if self.request.user != user and not (self.request.user.is_staff or self.request.user.is_superuser):
            raise PermissionDenied("No tienes permiso para ver este perfil.")
        return user

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        perfil = self.get_object()
        from apps.gamification.utils import get_insignias_activas_ids, get_insignias_visibles_para_usuario
        from apps.certifications.models import Certificado
        from apps.lms.models import CourseCertificate

        insignias_list = list(get_insignias_visibles_para_usuario(perfil))
        activas_ids = get_insignias_activas_ids(perfil)
        context['insignias_con_estado'] = [(insignia, insignia.id in activas_ids) for insignia in insignias_list]
        context['total_insignias'] = len(insignias_list)
        context['total_insignias_activas'] = sum(1 for insignia in insignias_list if insignia.id in activas_ids)
        context['certificados'] = Certificado.objects.filter(usuario=perfil)
        context['lms_certificados'] = CourseCertificate.objects.filter(
            user=perfil,
        ).select_related('course')
        context['total_certificados'] = (
            context['certificados'].count() + context['lms_certificados'].count()
        )
        from apps.certifications.models import UserCredential

        claims = UserCredential.objects.filter(
            user=perfil,
            visibility='public',
            archived_at__isnull=True,
        ).order_by('-updated_at')
        if perfil == self.request.user:
            context['credenciales_publicas'] = UserCredential.objects.filter(
                user=perfil,
                archived_at__isnull=True,
            ).order_by('-updated_at')
        else:
            context['credenciales_publicas'] = claims
        context['es_propietario_perfil'] = perfil == self.request.user
        if context['es_propietario_perfil']:
            from apps.energy.services import get_status

            context['energy'] = get_status(perfil)
        return context


class BorrarCuentaView(LoginRequiredMixin, View):
    """Soft delete the current user's account and log them out."""
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        user = request.user
        user.is_active = False
        user.save(update_fields=['is_active'])
        logout(request)
        messages.warning(request, 'Tu cuenta ha sido desactivada correctamente.')
        return redirect('users:landing')


class EditarPerfilView(LoginRequiredMixin, UpdateView):
    """Edit own profile."""
    model = User
    form_class = PerfilForm
    template_name = 'users/edit_profile.html'

    def get_object(self, queryset=None):
        return self.request.user

    def get_success_url(self):
        return reverse_lazy('users:perfil', kwargs={'username': self.request.user.username})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        from .access import can
        from .models import AccessRoleAssignment

        # Indicar si el aula está protegida (alumno con aula ya asignada)
        context['aula_protegida'] = (
            can(user, 'access_role', AccessRoleAssignment.Role.ALUMNO).allowed and
            user.aula is not None and
            not user.is_staff
        )

        return context

    def form_valid(self, form):
        messages.success(self.request, 'Perfil actualizado correctamente.')
        return super().form_valid(form)


class NotificacionesView(LoginRequiredMixin, ListView):
    """Notification centre."""
    model = Notificacion
    template_name = 'users/notificaciones.html'
    context_object_name = 'notificaciones'
    paginate_by = 20

    def get(self, request, *args, **kwargs):
        state = request.GET.get('estado', '')
        course = request.GET.get('curso', '')
        if state not in ('', 'pendientes', 'leidas') or (course and not course.isdecimal()):
            return HttpResponseBadRequest('Filtro de notificaciones no válido.')
        if len(request.GET.get('buscar', '')) > 200:
            return HttpResponseBadRequest('La búsqueda no puede superar 200 caracteres.')
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        self.all_notifications = platform_notifications(self.request.user)
        queryset = self.all_notifications
        state = self.request.GET.get('estado', '')
        if state:
            queryset = queryset.filter(leida=state == 'leidas')
        course = self.request.GET.get('curso', '')
        if course:
            queryset = queryset.filter(lms_notification__course_id=int(course))
        search = self.request.GET.get('buscar', '').strip()
        if search:
            queryset = queryset.filter(Q(titulo__icontains=search) | Q(mensaje__icontains=search))
        return queryset.order_by('-creada_en', '-pk')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.lms.notification_state import notification_area, notification_target
        from apps.lms.ui import COURSE_AREAS
        areas = {key: (label, icon) for key, label, icon in COURSE_AREAS}
        icons = {
            'logro': 'bi-trophy', 'insignia': 'bi-award', 'mision': 'bi-lightning-charge',
            'recompensa': 'bi-gift', 'certificado': 'bi-patch-check',
            'sistema': 'bi-bell', 'recordatorio': 'bi-clock',
        }
        for notice in context['notificaciones']:
            notice.notification_day = timezone.localdate(notice.creada_en)
            notice.area_label = notice.get_tipo_display()
            notice.icon = icons.get(notice.tipo, 'bi-bell')
            if notice.lms_notification_id:
                notice.target_url = notification_target(notice.lms_notification)
                notice.area = notification_area(notice.lms_notification)
                notice.area_label, notice.icon = areas.get(notice.area, (notice.area_label, notice.icon))
        stats = self.all_notifications.aggregate(
            total=Count('pk'), pending=Count('pk', filter=Q(leida=False)),
        )
        context.update({
            'notification_stats': {**stats, 'read': stats['total'] - stats['pending']},
            'notification_courses': self.all_notifications.exclude(lms_notification__course__isnull=True)
                .values('lms_notification__course_id', 'lms_notification__course__nombre')
                .order_by('lms_notification__course__nombre').distinct(),
            'notification_state': self.request.GET.get('estado', ''),
            'notification_search': self.request.GET.get('buscar', '').strip(),
            'notification_course': self.request.GET.get('curso', ''),
            'notification_filter_query': urlencode({
                key: self.request.GET[key] for key in ('estado', 'buscar', 'curso') if self.request.GET.get(key)
            }),
            'notification_tabs_query': urlencode({
                key: self.request.GET[key] for key in ('buscar', 'curso') if self.request.GET.get(key)
            }),
            'today': timezone.localdate(), 'yesterday': timezone.localdate() - timedelta(days=1),
        })
        return context


class MarcarNotificacionLeidaView(LoginRequiredMixin, View):
    def post(self, request, pk=None):
        if pk:
            notif = platform_notifications(request.user).filter(pk=pk).first()
            if not notif:
                from django.http import Http404
                raise Http404
            value = request.POST.get('read')
            if value is not None and value not in ('0', '1'):
                return HttpResponseBadRequest('Estado de lectura no válido.')
            read = value == '1' if value is not None else not notif.leida
            set_platform_read(platform_notifications(request.user).filter(pk=pk), read=read)
        else:
            set_platform_read(platform_notifications(request.user).filter(leida=False), read=True)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'ok': True, 'read': read if pk else True,
                'count': platform_notifications(request.user).filter(leida=False).count(),
            })
        filters = urlencode({
            key: request.POST[key] for key in ('estado', 'buscar', 'curso') if request.POST.get(key)
        })
        destination = reverse('users:notificaciones')
        return redirect(f'{destination}?{filters}' if filters else destination)


# ---------------------------------------------------------------------------
# DRF ViewSets (used via apps/api/urls.py)
# ---------------------------------------------------------------------------

class UserViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return User.objects.filter(is_active=True)

    @action(detail=False, methods=['get'])
    def me(self, request):
        return Response(UserSerializer(request.user).data)

    @action(detail=False, methods=['patch'], url_path='professional-branch')
    def professional_branch(self, request):
        serializer = UserProfessionalBranchSerializer(
            request.user,
            data=request.data,
            partial=False,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({
            'rama_principal': RamaProfesionalSerializer(
                request.user.rama_principal,
            ).data if request.user.rama_principal_id else None,
        })


class NotificacionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificacionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return platform_notifications(self.request.user)

    @action(detail=True, methods=['post'])
    def marcar_leida(self, request, pk=None):
        notif = self.get_object()
        set_platform_read(self.get_queryset().filter(pk=notif.pk), read=True)
        return Response({'ok': True})


class RamaProfesionalListAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        branches = RamaProfesional.objects.filter(is_active=True)
        return Response(RamaProfesionalSerializer(branches, many=True).data)
