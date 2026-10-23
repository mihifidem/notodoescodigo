from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, ListView, UpdateView

from apps.users.access import can
from apps.users.models import AccessRoleAssignment
from apps.users.models import Notificacion

from .forms import BugReportCreateForm, BugReportReviewForm
from .models import BugReport


class AlumnoOnlyMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        user = self.request.user
        return can(user, 'access_role', AccessRoleAssignment.Role.ALUMNO).allowed


class AdminOnlyMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        user = self.request.user
        return user.is_superuser or user.has_perm('bug_reports.change_bugreport')



# Nueva vista: lista pública de bugs para consulta
from django.db.models import Q

class BugReportPublicListView(ListView):
    template_name = 'bug_reports/public_list.html'
    context_object_name = 'bugs'
    paginate_by = 25

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            raise PermissionDenied
        if not can(request.user, 'access_role', AccessRoleAssignment.Role.ALUMNO).allowed:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        qs = BugReport.objects.all()
        estado = (self.request.GET.get('estado') or '').strip()
        q = (self.request.GET.get('q') or '').strip()
        if estado in {
            BugReport.ESTADO_PENDIENTE,
            BugReport.ESTADO_VALIDADO,
            BugReport.ESTADO_RECHAZADO,
        }:
            qs = qs.filter(estado=estado)
        if q:
            qs = qs.filter(
                Q(titulo__icontains=q) |
                Q(descripcion__icontains=q) |
                Q(pasos_reproduccion__icontains=q) |
                Q(url_afectada__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['estado_actual'] = (self.request.GET.get('estado') or '').strip()
        context['q'] = (self.request.GET.get('q') or '').strip()
        return context


class BugReportCreateView(AlumnoOnlyMixin, CreateView):
    form_class = BugReportCreateForm
    template_name = 'bug_reports/create.html'
    def get_success_url(self):
        return reverse_lazy('bug_reports:list')

    def form_valid(self, form):
        form.instance.alumno = self.request.user
        self.object = form.save()
        messages.success(self.request, 'Bug registrado correctamente. Gracias por ayudar a mejorar la plataforma.')
        return redirect(self.get_success_url())


class AdminBugReportListView(AdminOnlyMixin, ListView):
    template_name = 'bug_reports/admin_list.html'
    context_object_name = 'bugs'
    paginate_by = 25

    def get_queryset(self):
        estado = (self.request.GET.get('estado') or '').strip()
        queryset = BugReport.objects.select_related('alumno', 'validado_por')
        if estado in {
            BugReport.ESTADO_PENDIENTE,
            BugReport.ESTADO_VALIDADO,
            BugReport.ESTADO_RECHAZADO,
        }:
            queryset = queryset.filter(estado=estado)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['estado_actual'] = (self.request.GET.get('estado') or '').strip()
        return context


class AdminBugReportReviewView(AdminOnlyMixin, UpdateView):
    model = BugReport
    form_class = BugReportReviewForm
    template_name = 'bug_reports/admin_review.html'
    context_object_name = 'bug'

    def get_success_url(self):
        return reverse_lazy('bug_reports:admin_list')

    @transaction.atomic
    def form_valid(self, form):
        bug = get_object_or_404(BugReport.objects.select_for_update(), pk=self.object.pk)
        puntos_asignados_antes = bug.puntos_asignados
        bug.estado = form.cleaned_data['estado']
        bug.puntos_premio = form.cleaned_data['puntos_premio']
        bug.comentarios_admin = form.cleaned_data['comentarios_admin']

        now = timezone.now()

        if bug.estado == BugReport.ESTADO_VALIDADO:
            bug.validado_por = self.request.user
            bug.validado_en = now
        elif bug.estado == BugReport.ESTADO_RECHAZADO:
            bug.validado_por = self.request.user
            bug.validado_en = now
            Notificacion.objects.create(
                usuario=bug.alumno,
                tipo='sistema',
                titulo='Bug revisado',
                mensaje=(
                    f'Tu reporte "{bug.titulo}" fue revisado y marcado como no valido. '
                    'Gracias por colaborar.'
                ),
            )

        bug.save()

        if (
            bug.estado == BugReport.ESTADO_VALIDADO
            and not puntos_asignados_antes
            and bug.puntos_asignados
            and bug.puntos_premio > 0
        ):
            Notificacion.objects.create(
                usuario=bug.alumno,
                tipo='sistema',
                titulo='Bug validado',
                mensaje=(
                    f'Tu reporte "{bug.titulo}" fue validado por el equipo admin. '
                    f'Recibiste {bug.puntos_premio} puntos de recompensa.'
                ),
            )

        if bug.estado == BugReport.ESTADO_VALIDADO:
            messages.success(self.request, 'Reporte validado y puntos asignados (si correspondia).')
        elif bug.estado == BugReport.ESTADO_RECHAZADO:
            messages.info(self.request, 'Reporte marcado como rechazado.')
        else:
            messages.success(self.request, 'Reporte actualizado.')

        return redirect(self.get_success_url())
