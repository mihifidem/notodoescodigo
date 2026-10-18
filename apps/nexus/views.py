import re
from pathlib import Path
from django.conf import settings
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib import messages
from django.views.generic import (
    ListView, DetailView, CreateView, UpdateView, DeleteView, TemplateView,
)
from django.urls import reverse_lazy, reverse


class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Allows access only to staff/admin users."""

    def test_func(self):
        return self.request.user.is_staff

from .models import (
    NexusModule, Question, AnswerOption, Assessment,
    AssessmentSession, ModuleResult, DNAProfile,
    TestPlantilla, SesionTest, RecursoTest, InformeTest, InformePago,
)
from .forms import (
    NexusModuleForm, QuestionForm, AnswerOptionFormSet,
    AssessmentForm, DNAProfileForm, TestPlantillaForm, RecursoTestForm,
)


# ─── Dashboard ────────────────────────────────────────────────────────────────

class NexusDashboardView(AdminRequiredMixin, TemplateView):
    template_name = 'nexus/dashboard.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            # Tests HTML
            'total_tests': TestPlantilla.objects.filter(activo=True).count(),
            'sesiones_completadas': SesionTest.objects.filter(estado=SesionTest.COMPLETADO).count(),
            'recent_tests': TestPlantilla.objects.order_by('-creado_en')[:8],
            'recent_sesiones': SesionTest.objects.select_related('alumno', 'test').order_by('-creado_en')[:6],
            # Módulos DNA
            'total_modules': NexusModule.objects.filter(active=True).count(),
            'foundation_modules': NexusModule.objects.filter(layer=NexusModule.LAYER_FOUNDATION, active=True),
            'talent_modules': NexusModule.objects.filter(layer=NexusModule.LAYER_TALENT, active=True),
            'human_modules': NexusModule.objects.filter(layer=NexusModule.LAYER_HUMAN, active=True),
            # Perfiles DNA
            'total_profiles': DNAProfile.objects.count(),
            'top_profiles': DNAProfile.objects.select_related('user').order_by('-nexus_score')[:5],
        })
        return ctx


# ─── Modules ──────────────────────────────────────────────────────────────────

class ModuleListView(AdminRequiredMixin, ListView):
    model = NexusModule
    template_name = 'nexus/modulos/list.html'
    context_object_name = 'modules'

    def get_queryset(self):
        return NexusModule.objects.all()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['foundation'] = NexusModule.objects.filter(layer=NexusModule.LAYER_FOUNDATION)
        ctx['talent'] = NexusModule.objects.filter(layer=NexusModule.LAYER_TALENT)
        ctx['human'] = NexusModule.objects.filter(layer=NexusModule.LAYER_HUMAN)
        ctx['total_weight'] = sum(float(m.weight) for m in NexusModule.objects.filter(active=True))
        return ctx


class ModuleDetailView(AdminRequiredMixin, DetailView):
    model = NexusModule
    template_name = 'nexus/modulos/detail.html'
    context_object_name = 'module'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['questions'] = self.object.questions.all().prefetch_related('options')
        return ctx


class ModuleCreateView(AdminRequiredMixin, CreateView):
    model = NexusModule
    form_class = NexusModuleForm
    template_name = 'nexus/modulos/form.html'
    success_url = reverse_lazy('nexus:module_list')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['action'] = 'Crear'
        ctx['page_title'] = 'Nuevo Módulo'
        return ctx

    def form_valid(self, form):
        messages.success(self.request, f'Módulo "{form.instance.name}" creado correctamente.')
        return super().form_valid(form)


class ModuleUpdateView(AdminRequiredMixin, UpdateView):
    model = NexusModule
    form_class = NexusModuleForm
    template_name = 'nexus/modulos/form.html'

    def get_success_url(self):
        return reverse('nexus:module_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['action'] = 'Editar'
        ctx['page_title'] = f'Editar Módulo – {self.object.name}'
        return ctx

    def form_valid(self, form):
        messages.success(self.request, f'Módulo "{form.instance.name}" actualizado correctamente.')
        return super().form_valid(form)


class ModuleDeleteView(AdminRequiredMixin, DeleteView):
    model = NexusModule
    template_name = 'nexus/modulos/confirm_delete.html'
    success_url = reverse_lazy('nexus:module_list')
    context_object_name = 'module'

    def form_valid(self, form):
        name = self.object.name
        messages.success(self.request, f'Módulo "{name}" eliminado.')
        return super().form_valid(form)


# ─── Questions ────────────────────────────────────────────────────────────────

class QuestionCreateView(AdminRequiredMixin, CreateView):
    model = Question
    form_class = QuestionForm
    template_name = 'nexus/preguntas/form.html'

    def get_module(self):
        return get_object_or_404(NexusModule, pk=self.kwargs['module_pk'])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['module'] = self.get_module()
        ctx['action'] = 'Crear'
        ctx['page_title'] = 'Nueva Pregunta'
        if self.request.POST:
            ctx['option_formset'] = AnswerOptionFormSet(self.request.POST)
        else:
            ctx['option_formset'] = AnswerOptionFormSet()
        return ctx

    def form_valid(self, form):
        module = self.get_module()
        form.instance.module = module
        ctx = self.get_context_data()
        formset = ctx['option_formset']
        if formset.is_valid():
            self.object = form.save()
            formset.instance = self.object
            formset.save()
            messages.success(self.request, 'Pregunta creada correctamente.')
            return redirect(reverse('nexus:module_detail', kwargs={'pk': module.pk}))
        return self.render_to_response(ctx)

    def form_invalid(self, form):
        ctx = self.get_context_data(form=form)
        return self.render_to_response(ctx)


class QuestionUpdateView(AdminRequiredMixin, UpdateView):
    model = Question
    form_class = QuestionForm
    template_name = 'nexus/preguntas/form.html'

    def get_module(self):
        return get_object_or_404(NexusModule, pk=self.kwargs['module_pk'])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['module'] = self.get_module()
        ctx['action'] = 'Editar'
        ctx['page_title'] = 'Editar Pregunta'
        if self.request.POST:
            ctx['option_formset'] = AnswerOptionFormSet(self.request.POST, instance=self.object)
        else:
            ctx['option_formset'] = AnswerOptionFormSet(instance=self.object)
        return ctx

    def form_valid(self, form):
        ctx = self.get_context_data()
        formset = ctx['option_formset']
        if formset.is_valid():
            self.object = form.save()
            formset.instance = self.object
            formset.save()
            messages.success(self.request, 'Pregunta actualizada correctamente.')
            return redirect(reverse('nexus:module_detail', kwargs={'pk': self.object.module.pk}))
        return self.render_to_response(ctx)


class QuestionDeleteView(AdminRequiredMixin, DeleteView):
    model = Question
    template_name = 'nexus/preguntas/confirm_delete.html'
    context_object_name = 'question'

    def get_module(self):
        return get_object_or_404(NexusModule, pk=self.kwargs['module_pk'])

    def get_success_url(self):
        return reverse('nexus:module_detail', kwargs={'pk': self.object.module.pk})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['module'] = self.get_module()
        return ctx

    def form_valid(self, form):
        messages.success(self.request, 'Pregunta eliminada.')
        return super().form_valid(form)


# ─── Assessments ──────────────────────────────────────────────────────────────

class AssessmentListView(AdminRequiredMixin, ListView):
    model = Assessment
    template_name = 'nexus/tests/list.html'
    context_object_name = 'assessments'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['draft_count'] = Assessment.objects.filter(status=Assessment.STATUS_DRAFT).count()
        ctx['active_count'] = Assessment.objects.filter(status=Assessment.STATUS_ACTIVE).count()
        ctx['closed_count'] = Assessment.objects.filter(status=Assessment.STATUS_CLOSED).count()
        return ctx


class AssessmentDetailView(AdminRequiredMixin, DetailView):
    model = Assessment
    template_name = 'nexus/tests/detail.html'
    context_object_name = 'assessment'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['modules'] = self.object.modules.all()
        ctx['sessions'] = self.object.sessions.select_related('candidate').order_by('-created_at')[:10]
        return ctx


class AssessmentCreateView(AdminRequiredMixin, CreateView):
    model = Assessment
    form_class = AssessmentForm
    template_name = 'nexus/tests/form.html'
    success_url = reverse_lazy('nexus:assessment_list')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['action'] = 'Crear'
        ctx['page_title'] = 'Nuevo Assessment'
        ctx['foundation_modules'] = NexusModule.objects.filter(layer=NexusModule.LAYER_FOUNDATION, active=True)
        ctx['talent_modules'] = NexusModule.objects.filter(layer=NexusModule.LAYER_TALENT, active=True)
        ctx['human_modules'] = NexusModule.objects.filter(layer=NexusModule.LAYER_HUMAN, active=True)
        return ctx

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, f'Assessment "{form.instance.title}" creado.')
        return super().form_valid(form)


class AssessmentUpdateView(AdminRequiredMixin, UpdateView):
    model = Assessment
    form_class = AssessmentForm
    template_name = 'nexus/tests/form.html'

    def get_success_url(self):
        return reverse('nexus:assessment_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['action'] = 'Editar'
        ctx['page_title'] = f'Editar Assessment – {self.object.title}'
        ctx['foundation_modules'] = NexusModule.objects.filter(layer=NexusModule.LAYER_FOUNDATION, active=True)
        ctx['talent_modules'] = NexusModule.objects.filter(layer=NexusModule.LAYER_TALENT, active=True)
        ctx['human_modules'] = NexusModule.objects.filter(layer=NexusModule.LAYER_HUMAN, active=True)
        return ctx

    def form_valid(self, form):
        messages.success(self.request, f'Assessment "{form.instance.title}" actualizado.')
        return super().form_valid(form)


class AssessmentDeleteView(AdminRequiredMixin, DeleteView):
    model = Assessment
    template_name = 'nexus/tests/confirm_delete.html'
    success_url = reverse_lazy('nexus:assessment_list')
    context_object_name = 'assessment'

    def form_valid(self, form):
        messages.success(self.request, 'Assessment eliminado.')
        return super().form_valid(form)


# ─── DNA Profiles ─────────────────────────────────────────────────────────────

class DNAProfileListView(AdminRequiredMixin, ListView):
    model = DNAProfile
    template_name = 'nexus/resultados/list.html'
    context_object_name = 'profiles'

    def get_queryset(self):
        return DNAProfile.objects.select_related('user').order_by('-nexus_score')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        profiles = DNAProfile.objects.all()
        if profiles.exists():
            scores = [float(p.nexus_score) for p in profiles]
            ctx['avg_score'] = round(sum(scores) / len(scores), 1)
            ctx['max_score'] = round(max(scores), 1)
            ctx['exceptional'] = profiles.filter(nexus_score__gte=90).count()
        else:
            ctx['avg_score'] = ctx['max_score'] = ctx['exceptional'] = 0
        return ctx


class DNAProfileDetailView(AdminRequiredMixin, DetailView):
    model = DNAProfile
    template_name = 'nexus/resultados/detail.html'
    context_object_name = 'profile'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if self.object.session:
            ctx['module_results'] = (
                self.object.session.module_results
                .select_related('module')
                .order_by('-score')
            )
        ctx['master_indices'] = [
            ('Leadership', float(self.object.leadership_score), '#DC2626'),
            ('Innovation', float(self.object.innovation_score), '#8B5CF6'),
            ('Technology', float(self.object.technology_score), '#06B6D4'),
            ('Future', float(self.object.future_score), '#22C55E'),
            ('Career', float(self.object.career_score), '#F59E0B'),
            ('Executive', float(self.object.executive_score), '#EC4899'),
            ('Motivation', float(self.object.motivation_score), '#2563EB'),
            ('Cognitive', float(self.object.cognitive_score), '#6366F1'),
        ]
        return ctx


class DNAProfileCreateView(AdminRequiredMixin, CreateView):
    model = DNAProfile
    form_class = DNAProfileForm
    template_name = 'nexus/resultados/form.html'
    success_url = reverse_lazy('nexus:profile_list')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['action'] = 'Crear'
        ctx['page_title'] = 'Nuevo Perfil DNA'
        from django.contrib.auth import get_user_model
        User = get_user_model()
        ctx['available_users'] = User.objects.exclude(dna_profile__isnull=False)
        return ctx

    def form_valid(self, form):
        user_id = self.request.POST.get('profile_user')
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = get_object_or_404(User, pk=user_id)
        form.instance.user = user
        messages.success(self.request, f'Perfil DNA creado para {user}.')
        return super().form_valid(form)


class DNAProfileUpdateView(AdminRequiredMixin, UpdateView):
    model = DNAProfile
    form_class = DNAProfileForm
    template_name = 'nexus/resultados/form.html'

    def get_success_url(self):
        return reverse('nexus:profile_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['action'] = 'Editar'
        ctx['page_title'] = f'Editar Perfil DNA – {self.object.user}'
        return ctx

    def form_valid(self, form):
        messages.success(self.request, 'Perfil DNA actualizado.')
        return super().form_valid(form)


class DNAProfileDeleteView(AdminRequiredMixin, DeleteView):
    model = DNAProfile
    template_name = 'nexus/resultados/confirm_delete.html'
    success_url = reverse_lazy('nexus:profile_list')
    context_object_name = 'profile'

    def form_valid(self, form):
        messages.success(self.request, 'Perfil DNA eliminado.')
        return super().form_valid(form)


# =============================================================================
# ADMIN CRUD — TestPlantilla (subida de archivos HTML)
# =============================================================================

class TestPlantillaListView(AdminRequiredMixin, ListView):
    model = TestPlantilla
    template_name = 'nexus/test_plantilla/list.html'
    context_object_name = 'tests'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['total_sesiones'] = SesionTest.objects.count()
        ctx['completadas'] = SesionTest.objects.filter(estado=SesionTest.COMPLETADO).count()
        return ctx


class TestPlantillaCreateView(AdminRequiredMixin, CreateView):
    model = TestPlantilla
    form_class = TestPlantillaForm
    template_name = 'nexus/test_plantilla/form.html'
    success_url = reverse_lazy('nexus:test_list')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['accion'] = 'Crear'
        ctx['page_title'] = 'Nuevo Test'
        return ctx

    def form_valid(self, form):
        form.instance.creado_por = self.request.user
        messages.success(self.request, f'Test "{form.instance.nombre}" creado correctamente.')
        return super().form_valid(form)


class TestPlantillaUpdateView(AdminRequiredMixin, UpdateView):
    model = TestPlantilla
    form_class = TestPlantillaForm
    template_name = 'nexus/test_plantilla/form.html'
    success_url = reverse_lazy('nexus:test_list')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['accion'] = 'Editar'
        ctx['page_title'] = f'Editar Test — {self.object.nombre}'
        return ctx

    def form_valid(self, form):
        messages.success(self.request, f'Test "{form.instance.nombre}" actualizado.')
        return super().form_valid(form)


class TestPlantillaDeleteView(AdminRequiredMixin, DeleteView):
    model = TestPlantilla
    template_name = 'nexus/test_plantilla/confirm_delete.html'
    success_url = reverse_lazy('nexus:test_list')
    context_object_name = 'test'

    def form_valid(self, form):
        messages.success(self.request, 'Test eliminado.')
        return super().form_valid(form)


# =============================================================================
# VISTAS ALUMNOS — Login requerido (no staff)
# =============================================================================

from django.views import View
from django.http import HttpResponse, JsonResponse
from django.conf import settings
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
import json
import requests
import stripe


def _version_capture_script():
    """Patch window.fetch to inject extra metadata into the resultados_json payload.

    Adds: version, total_preguntas, num_likert, num_abcd,
          tiempo_total_seg, tiempo_promedio_seg,
          speed_score, pattern_score, mirror_score, fast_count, flags
    from state + any global results variable (_tmResultados / _cnResultados).
    """
    return """
<script>
(function () {
  var _origFetch = window.fetch;
  window.fetch = function (url, opts) {
    if (opts && opts.body instanceof FormData) {
      var rj = opts.body.get('resultados_json');
      if (rj) {
        try {
          var data = JSON.parse(rj);
          /* ── version ── */
          if (!data.version && typeof state !== 'undefined' && state.version) {
            data.version = state.version;
          }
          /* ── question counts ── */
          if (typeof state !== 'undefined' && state.questions && state.questions.length) {
            var qs = state.questions;
            data.total_preguntas = qs.length;
            var nLikert = 0, nAbcd = 0;
            for (var i = 0; i < qs.length; i++) {
              if (qs[i].type === 'likert') { nLikert++; } else { nAbcd++; }
            }
            data.num_likert  = nLikert;
            data.num_abcd    = nAbcd;
          }
          /* ── timings ── */
          if (typeof state !== 'undefined' && state.timings) {
            var timings = state.timings;
            var tKeys = Object.keys(timings);
            if (tKeys.length) {
              var totalMs = 0;
              for (var k = 0; k < tKeys.length; k++) { totalMs += timings[tKeys[k]]; }
              data.tiempo_total_seg    = Math.round(totalMs / 1000);
              data.tiempo_promedio_seg = parseFloat((totalMs / tKeys.length / 1000).toFixed(1));
            }
          }
          /* ── psychometric validation from global results var ── */
          var _res = (typeof _tmResultados !== 'undefined' && _tmResultados)
                  || (typeof _cnResultados !== 'undefined' && _cnResultados)
                  || (typeof _csResultados !== 'undefined' && _csResultados)
                  || (typeof _dcResultados !== 'undefined' && _dcResultados)
                  || (typeof _imResultados !== 'undefined' && _imResultados)
                  || null;
          if (_res && _res.val) {
            var v = _res.val;
            data.speed_score   = v.speedScore   !== undefined ? v.speedScore   : null;
            data.pattern_score = v.patternScore  !== undefined ? v.patternScore : null;
            data.mirror_score  = v.mirrorScore   !== undefined ? v.mirrorScore  : null;
            data.fast_count    = v.fastCount      !== undefined ? v.fastCount   : null;
            data.avg_time_seg  = v.avgTimeSec     !== undefined ? parseFloat(v.avgTimeSec) : null;
            data.dominancia    = v.dominanceRatio !== undefined ? v.dominanceRatio : null;
            data.flags_psico   = (v.flags && v.flags.length) ? v.flags.map(function(f){ return f.text || f.type; }) : [];
          }
          opts.body.set('resultados_json', JSON.stringify(data));
        } catch (e) {}
      }
    }
    return _origFetch.apply(this, arguments);
  };
})();
</script>"""


# ⚠️ DEV ONLY — eliminar antes de subir a producción ⚠️
def _dev_autofill_script():
    """Floating button that randomly fills all test answers. Injected only when DEBUG=True."""
    return """
<!-- ⚠️ DEV ONLY - REMOVE BEFORE PROD ⚠️ -->
<style id="dev-autofill-style">
@keyframes dev-pulse{0%,100%{box-shadow:0 4px 20px rgba(124,58,237,.5)}50%{box-shadow:0 6px 32px rgba(236,72,153,.9)}}
</style>
<script>
/* ⚠️ DEV ONLY - REMOVE BEFORE PROD ⚠️ */
(function () {
  function devAutoFill() {
    if (typeof state === 'undefined' || !state.questions || !state.questions.length) {
      alert('Primero selecciona una variante del test (Essential / Advanced / Premium) e inícialo.');
      return;
    }
    for (var i = 0; i < state.questions.length; i++) {
      var q = state.questions[i];
      if (q.type === 'likert') {
        state.answers[i] = Math.floor(Math.random() * 5) + 1; /* 1-5 */
      } else {
        var opts = q.options || q.opts || [];
        var n = opts.length > 0 ? opts.length : 4;
        state.answers[i] = Math.floor(Math.random() * n); /* 0-(n-1) */
      }
    }
    /* Jump to last question so nextQ() can fire, then trigger analysis */
    state.current = state.questions.length - 1;
    if (typeof renderQ === 'function') { renderQ(); }
    setTimeout(function () {
      if (typeof runAnalysis === 'function') { runAnalysis(); }
    }, 200);
  }

  document.addEventListener('DOMContentLoaded', function () {
    var btn = document.createElement('button');
    btn.id = 'dev-autofill-btn';
    btn.innerHTML = '&#x26A1;&nbsp;DEV: Auto-rellenar';
    btn.title = 'Rellena todas las preguntas aleatoriamente (solo desarrollo)';
    btn.style.cssText =
      'position:fixed;bottom:22px;right:22px;z-index:99999;'
      + 'background:linear-gradient(135deg,#7C3AED,#EC4899);'
      + 'color:#fff;border:none;border-radius:99px;'
      + 'padding:10px 20px;font-size:13px;font-weight:700;'
      + 'cursor:pointer;animation:dev-pulse 2s ease-in-out infinite;'
      + 'font-family:monospace;letter-spacing:.03em;';
    btn.onclick = devAutoFill;
    document.body.appendChild(btn);
  });
})();
/* ⚠️ DEV ONLY END ⚠️ */
</script>
<!-- ⚠️ DEV ONLY END ⚠️ -->"""
# ⚠️ DEV ONLY END ⚠️


def _version_lock_script(role):
    """Return a <script> block that locks version cards the user can't access.

    alumno can access the premium variant, but the paid report still requires checkout.
    basic and unknown roles keep premium locked.
    premium → no script injected (caller skips this function).
    """
    if role in {'premium', 'alumno'}:
        return ''
    return """
<script>
(function () {
  function lockVersionCards() {
    var prem = document.getElementById('card-premium');
    if (!prem) return;
    prem.style.opacity = '0.45';
    prem.style.cursor  = 'not-allowed';
    prem.style.pointerEvents = 'none';
    if (!document.getElementById('prem-lock-badge')) {
      var badge = document.createElement('span');
      badge.id = 'prem-lock-badge';
      badge.style.cssText = 'position:absolute;top:12px;right:12px;font-size:10px;'
        + 'background:#F59E0B;color:#fff;padding:2px 8px;border-radius:99px;font-weight:700;letter-spacing:.04em;';
      badge.textContent = '\\uD83D\\uDD12 Solo Premium';
      prem.style.position = 'relative';
      prem.appendChild(badge);
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', lockVersionCards);
  } else {
    lockVersionCards();
  }
  setTimeout(lockVersionCards, 300);
  setTimeout(lockVersionCards, 800);
})();
</script>"""


def _auto_select_test_version_script(test):
    """No fuerza la variante del test; el usuario elige Essential/Advanced/Premium."""
    return ""


def _usuario_puede_acceder(user, test):
    """True si user tiene nivel suficiente para realizar el test.

    essential  → cualquiera (incluso anónimo vía demo)
    advanced   → cualquier usuario autenticado
    premium    → cualquier usuario autenticado puede elegirlo; se paga 49 EUR al generar el informe
    """
    nivel = test.nivel_acceso
    if nivel == 'essential':
        return True
    if nivel == 'advanced':
        return user.is_authenticated
    if nivel == 'premium':
        return user.is_authenticated
    return True

from django.utils import timezone
from django.template import engines as template_engines


def _build_dna_snapshot(resultados: dict) -> SimpleNamespace:
    """Build a session-specific DNA-like object from stored resultados_json."""
    def _dec(key):
        try:
            return Decimal(str(resultados.get(key, 0))).quantize(Decimal('0.01'))
        except (InvalidOperation, TypeError):
            return Decimal('0')

    nexus_score = _dec('nexus_score')
    s = float(nexus_score)

    if s >= 90:
        level, level_class = 'Exceptional Talent', 'success'
    elif s >= 80:
        level, level_class = 'High Potential Talent', 'primary'
    elif s >= 70:
        level, level_class = 'Strong Professional', 'info'
    elif s >= 60:
        level, level_class = 'Developing Professional', 'warning'
    else:
        level, level_class = 'Growth Required', 'danger'

    tii = float(_dec('tii') or _dec('ti_index') or nexus_score)
    dimensiones = resultados.get('dimensiones', [])
    if not isinstance(dimensiones, list):
        dimensiones = []

    return SimpleNamespace(
        nexus_score=nexus_score,
        leadership_score=_dec('leadership_score'),
        innovation_score=_dec('innovation_score'),
        technology_score=_dec('technology_score'),
        future_score=_dec('future_score'),
        career_score=_dec('career_score'),
        executive_score=_dec('executive_score'),
        motivation_score=_dec('motivation_score'),
        cognitive_score=_dec('cognitive_score'),
        emotional_score=_dec('emotional_score'),
        reliability=_dec('reliability'),
        primary_archetype=str(resultados.get('primary_archetype', ''))[:100],
        secondary_archetype=str(resultados.get('secondary_archetype', ''))[:100],
        tertiary_archetype=str(resultados.get('tertiary_archetype', ''))[:100],
        level=level,
        level_class=level_class,
        score_gauge_percent=round(s * 396 / 100, 2),
        # TechMind extra fields (TM01-TM10)
        tii=tii,
        tii_gauge_percent=round(tii * 396 / 100, 2),
        tm01=int(_dec('TM01')), tm02=int(_dec('TM02')),
        tm03=int(_dec('TM03')), tm04=int(_dec('TM04')),
        tm05=int(_dec('TM05')), tm06=int(_dec('TM06')),
        tm07=int(_dec('TM07')), tm08=int(_dec('TM08')),
        tm09=int(_dec('TM09')), tm10=int(_dec('TM10')),
        # BehaviorMatrix extra fields (BM01-BM08)
        bis=float(_dec('bis') or _dec('tii') or nexus_score),
        bm01=int(_dec('BM01')), bm02=int(_dec('BM02')),
        bm03=int(_dec('BM03')), bm04=int(_dec('BM04')),
        bm05=int(_dec('BM05')), bm06=int(_dec('BM06')),
        bm07=int(_dec('BM07')), bm08=int(_dec('BM08')),
        # ConflictSync extra fields (CS01-CS08)
        cii=float(_dec('conflict_intelligence_index') or nexus_score),
        cs01=int(_dec('CS01')), cs02=int(_dec('CS02')),
        cs03=int(_dec('CS03')), cs04=int(_dec('CS04')),
        cs05=int(_dec('CS05')), cs06=int(_dec('CS06')),
        cs07=int(_dec('CS07')), cs08=int(_dec('CS08')),
        # DecisionCore extra fields (DC01-DC10)
        dii=float(_dec('dii') or nexus_score),
        dc01=int(_dec('DC01')), dc02=int(_dec('DC02')),
        dc03=int(_dec('DC03')), dc04=int(_dec('DC04')),
        dc05=int(_dec('DC05')), dc06=int(_dec('DC06')),
        dc07=int(_dec('DC07')), dc08=int(_dec('DC08')),
        dc09=int(_dec('DC09')), dc10=int(_dec('DC10')),
        decision_style=str(resultados.get('decision_style', '')),
        # IdentityMap extra fields (IM01-IM09)
        mii=float(_dec('mii') or nexus_score),
        im01=int(_dec('IM01')), im02=int(_dec('IM02')),
        im03=int(_dec('IM03')), im04=int(_dec('IM04')),
        im05=int(_dec('IM05')), im06=int(_dec('IM06')),
        im07=int(_dec('IM07')), im08=int(_dec('IM08')),
        im09=int(_dec('IM09')),
        centro_motivacional=str(resultados.get('centro_motivacional', '')),
        dimensiones=dimensiones,
    )


def _prompt_file_candidates(tipo: str = None):
    """Devuelve la lista de archivos de prompt válidos para cada tipo de test."""
    mapping = {
        InformeTest.TIPO_BASE: ['nexus_essential.txt', 'nexus_esencial.txt', 'nexus_esssential.txt'],
        InformeTest.TIPO_AI_PERSONAL: ['nexus_advanced.txt', 'nexus_avanced.txt'],
        InformeTest.TIPO_AI_COMPLETO: ['nexus_premium.txt', 'nexus_premiium.txt'],
        'essential': ['nexus_essential.txt', 'nexus_esencial.txt', 'nexus_esssential.txt'],
        'advanced': ['nexus_advanced.txt', 'nexus_avanced.txt'],
        'premium': ['nexus_premium.txt', 'nexus_premiium.txt'],
    }
    return mapping.get(tipo, [])


def _read_prompt_for_type(tipo: str = None):
    """Lee el prompt del tipo de test y devuelve el contenido o None si está vacío o no existe.

    La carpeta media/nexus/prompts tiene prioridad: si el archivo principal del tipo existe pero está vacío,
    la generación del informe se bloquea en lugar de caer en una copia alternativa o legacy.
    """
    prompt_dirs = [
        Path(settings.MEDIA_ROOT) / 'nexus' / 'prompts',
        Path(settings.MEDIA_ROOT) / 'prompts',
    ]
    seen = set()
    for prompt_dir in prompt_dirs:
        for candidate in _prompt_file_candidates(tipo):
            prompt_path = prompt_dir / candidate
            key = str(prompt_path)
            if key in seen:
                continue
            seen.add(key)
            if not prompt_path.exists():
                continue
            try:
                content = prompt_path.read_text(encoding='utf-8')
            except OSError:
                continue
            cleaned = content.strip()
            if cleaned:
                return cleaned
            if prompt_dir == Path(settings.MEDIA_ROOT) / 'nexus' / 'prompts':
                return None
    return None


def _load_prompt_variant_for_ai_coach(tipo: str = None):
    """Carga la variante del prompt según el tipo de informe solicitado."""
    content = _read_prompt_for_type(tipo)
    if not content:
        return 'PROMPT NO DISPONIBLE'

    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return 'PROMPT NO DISPONIBLE'
    return lines[0]


def _rank_dimensiones(resultados: dict):
    """Normaliza una lista de dimensiones para el análisis IA."""
    dims = resultados.get('dimensiones', [])
    if isinstance(dims, list) and dims:
        normalized = []
        for item in dims:
            if not isinstance(item, dict):
                continue
            label = str(item.get('label') or item.get('nombre') or '').strip()
            score = item.get('score')
            if not label:
                continue
            try:
                normalized.append((label, float(score)))
            except (TypeError, ValueError):
                continue
        if normalized:
            return sorted(normalized, key=lambda x: x[1], reverse=True)

    dimension_keys = [
        ('TM01', 'Curiosidad Digital'), ('TM02', 'Aprendizaje Digital'), ('TM03', 'Agilidad de Aprendizaje'),
        ('TM04', 'Intel. de IA'), ('TM05', 'Automatización'), ('TM06', 'Resolución de Problemas'),
        ('TM07', 'Iniciativa'), ('TM08', 'Visión Estratégica'), ('TM09', 'Confianza Digital'), ('TM10', 'Colaboración'),
        ('BM01', 'Ejecución'), ('BM02', 'Responsabilidad'), ('BM03', 'Adaptabilidad'), ('BM04', 'Colaboración'),
        ('BM05', 'Iniciativa'), ('BM06', 'Toma de Decisiones'), ('BM07', 'Consistencia'), ('BM08', 'Actitud bajo presión'),
    ]
    ranks = []
    for key, label in dimension_keys:
        value = resultados.get(key)
        try:
            score = float(value)
        except (TypeError, ValueError):
            continue
        ranks.append((label, score))
    if ranks:
        return sorted(ranks, key=lambda x: x[1], reverse=True)
    return []


def _resolver_version_test(sesion: SesionTest) -> str:
    """Devuelve la versión real del test, priorizando la sesión y luego el JSON guardado."""
    for candidate in (
        getattr(sesion, 'version_test', None),
        (sesion.resultados_json or {}).get('version') if hasattr(sesion, 'resultados_json') else None,
        getattr(sesion.test, 'nivel_acceso', None),
    ):
        if candidate:
            return str(candidate).lower()
    return str(getattr(sesion.test, 'nivel_acceso', TestPlantilla.NIVEL_ADVANCED) or TestPlantilla.NIVEL_ADVANCED).lower()


def _resolver_tipo_informe_por_sesion(sesion: SesionTest) -> str:
    """Devuelve el tipo de informe según la variante real del test."""
    nivel = _resolver_version_test(sesion)

    tipo_por_nivel = {
        TestPlantilla.NIVEL_ESSENTIAL: InformeTest.TIPO_BASE,
        TestPlantilla.NIVEL_ADVANCED: InformeTest.TIPO_AI_PERSONAL,
        TestPlantilla.NIVEL_PREMIUM: InformeTest.TIPO_AI_COMPLETO,
    }
    return tipo_por_nivel.get(nivel, InformeTest.TIPO_AI_PERSONAL)


def _operational_error_html(message: str = 'No puedo crear informe por un error operativo') -> str:
    """Devuelve un HTML estándar para los casos operativos en los que no se puede crear el informe."""
    return f"""
    <div style="max-width:720px; margin:24px auto; border:1px solid #FCA5A5; border-radius:16px; background:#FEF2F2; padding:24px; font-family: 'Inter', sans-serif; color:#7F1D1D;">
      <h2 style="margin:0 0 12px; font-size:1.5rem;">{message}</h2>
      <p style="margin:0; line-height:1.6;">No se pudo completar el informe porque el prompt del tipo de test solicitado está vacío, no existe o no se pudo leer.</p>
    </div>
    """


def _score_to_level(score_value):
    """Convierte una puntuación TechMind a nivel oficial."""
    try:
        score = float(score_value)
    except (TypeError, ValueError):
        return 'Muy Bajo'
    if score >= 90:
        return 'Excelente'
    if score >= 75:
        return 'Alto'
    if score >= 60:
        return 'Moderado'
    if score >= 40:
        return 'Bajo'
    return 'Muy Bajo'


def _has_real_prompt_structure(prompt_text: str) -> bool:
    """Comprueba si el prompt es el documento completo del informe y no solo una línea de cabecera."""
    if not prompt_text:
        return False
    upper = prompt_text.upper()
    required_markers = [
        'PORTADA EJECUTIVA',
        'RESUMEN EJECUTIVO',
        'ARQUETIPO PRINCIPAL',
        'MAPA DE RESULTADOS',
        'FORTALEZAS',
        'ÁREAS DE DESARROLLO',
        'RIESGOS Y FRENOS',
        'ACCIONES PRIORITARIAS',
        'PLAN DE DESARROLLO',
        'CONCLUSIÓN FINAL',
    ]
    return any(marker in upper for marker in required_markers)


def _render_prompt_template(prompt_text: str, values: dict) -> str:
    """Rellena placeholders del prompt y devuelve una versión HTML con estructura de documento Premium."""
    rendered = prompt_text
    for key, value in values.items():
        rendered = rendered.replace(f'{{{{{key}}}}}', str(value))

    if not rendered.strip():
        return '<p>Sin contenido disponible.</p>'

    lines = rendered.splitlines()
    html_parts = []
    current_list = []
    current_paragraph = []

    def flush_paragraph():
        nonlocal current_paragraph
        if current_paragraph:
            text = ' '.join(part.strip() for part in current_paragraph if part and part.strip())
            if text:
                html_parts.append(f'<p>{text}</p>')
            current_paragraph = []

    def flush_list():
        nonlocal current_list
        if current_list:
            html_parts.append('<ul>' + ''.join(f'<li>{item}</li>' for item in current_list if item) + '</ul>')
            current_list = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            flush_list()
            continue

        if re.match(r'^\d+[\.)]', line):
            flush_paragraph()
            flush_list()
            heading = re.sub(r'^\d+[\.)]\s*', '', line)
            html_parts.append(f'<h3>{heading}</h3>')
            continue

        if re.match(r'^[-*]\s+', line):
            flush_paragraph()
            current_list.append(re.sub(r'^[-*]\s*', '', line))
            continue

        if re.match(r'^[A-ZÁÉÍÓÚÑ].*:$', line):
            flush_paragraph()
            flush_list()
            html_parts.append(f'<h4>{line[:-1]}</h4>')
            continue

        current_paragraph.append(line)

    flush_paragraph()
    flush_list()

    if not html_parts:
        return f'<div>{rendered}</div>'
    return '<div style="font-family: Arial, sans-serif; line-height:1.7; color:#1e293b;">' + ''.join(html_parts) + '</div>'


def _build_structured_report_fallback(sesion: SesionTest, tipo: str, prompt_text: str, score: float, primary: str, secondary: str, leadership: float, technology: float, future: float) -> str:
    """Genera un documento estructurado con las secciones clave del informe cuando el prompt no contiene el contenido real."""
    score_value = int(float(score or 0))
    leadership_value = int(float(leadership or 0))
    technology_value = int(float(technology or 0))
    future_value = int(float(future or 0))

    summary = (
        f'El score global de {score_value}/100 muestra un perfil con base funcional, potencial de desarrollo y margen claro de mejora. '
        f'El arquetipo principal identificado es {primary} y el secundario {secondary}. '
        f'La dimensión de liderazgo alcanza {leadership_value}/100 y la transformación digital {technology_value}/100, con una visión de futuro de {future_value}/100. '
        'La clave está en convertir este potencial en ejecución disciplinada, foco estratégico y evidencia de progreso real.'
    )

    if tipo == InformeTest.TIPO_BASE:
        title = 'Informe básico'
    elif tipo == InformeTest.TIPO_AI_COMPLETO:
        title = 'Informe premium'
    else:
        title = 'Informe advanced'

    return f"""
    <div style="font-family: Arial, sans-serif; line-height:1.7; color:#1e293b;">
      <div style="background: linear-gradient(135deg, #0F172A 0%, #1D4ED8 45%, #0EA5E9 100%); color: white; border-radius: 18px; padding: 1.5rem; margin-bottom:1.2rem;">
        <div style="font-size:11px; letter-spacing:.12em; text-transform:uppercase; opacity:.8;">Nexus AI Coach™</div>
        <h2 style="margin:.5rem 0 0; font-size: 2rem; color: white;">{title}</h2>
      </div>

      <h3>Resumen Ejecutivo</h3>
      <p>{summary}</p>
      <p>El perfil refleja una base útil para la acción, pero aún no está completamente desplegado como ventaja competitiva. La diferencia entre potencial y rendimiento real suele depender de la calidad del enfoque, la disciplina de ejecución y la capacidad para convertir aprendizaje en resultados medibles.</p>
      <p>Desde la perspectiva de un mentor o responsable de equipo, este resultado sugiere un perfil con capacidad de adaptación, sensibilidad tecnológica y oportunidades claras para avanzar hacia un nivel más alto de influencia y valor profesional.</p>

      <h3>Lectura profesional del score global</h3>
      <p>Con {score_value}/100, el usuario se sitúa en una zona de desempeño funcional con un nivel de madurez todavía suficiente para mejorar de forma tangible. No se trata de un perfil sin potencial, sino de un perfil que necesita mayor estructura para transformar capacidad en resultados más consistentes y visibles.</p>
      <p>En términos ejecutivos, esto significa que las fortalezas ya existen, pero aún requieren funcionar bajo una estrategia clara. La diferencia entre un perfil medio y excepcional no viene solo del talento inicial, sino de la consistencia con la que se aplica, se supervisa y se mejora.</p>
      <p>Se observa una capacidad útil para liderar decisiones, colaborar con otros y aplicar herramientas digitales, aunque la ejecución todavía puede verse afectada por la dispersión, la falta de priorización o la ausencia de metas concretas a 90 días.</p>

      <h3>Arquetipo principal: {primary}</h3>
      <p>El arquetipo principal describe una forma de pensar orientada a la adaptación, al cambio y a la utilidad práctica de la tecnología. Este perfil suele reaccionar bien a entornos dinámicos, proyectos cambiantes y tareas en las que se requiere rapidez de comprensión y capacidad de conectar ideas con resultados concretos.</p>
      <p>En su mejor versión, esta persona aporta iniciativa, capacidad de aprendizaje y una actitud favorable al progreso. En su versión menos estructurada, puede caer en exceso de actividad sin dirección suficiente, generando movimiento pero no siempre impacto medible.</p>
      <p>La clave para este perfil es convertir su agilidad en claridad mediante objetivos, revisión semanal, decisiones con criterio y un mapa de prioridades que evite la dispersión.</p>

      <h3>Arquetipo secundario: {secondary}</h3>
      <p>El arquetipo secundario aporta una dimensión de colaboración, intercambio y capacidad para funcionar bien dentro de equipos digitales. Se trata de una competencia valiosa porque permite conectar a personas, herramientas y procesos bajo una lógica de progreso compartido.</p>
      <p>Cuando se usa bien, esta capacidad mejora la coordinación, la comunicación y la concreción de proyectos. Cuando se desaprovecha, puede quedar como un esfuerzo útil pero poco visible, sin traducirse en impulso real ni influencia suficiente.</p>
      <p>La evolución natural de este componente es pasar de colaborar bien a liderar con mayor claridad, especialmente cuando se combina con criterio, previsión y una agenda de trabajo más enfocada.</p>

      <h3>Mapa de resultados</h3>
      <table style="width:100%; border-collapse:collapse; margin: 12px 0 20px;">
        <tr><th style="border:1px solid #E2E8F0; padding:8px; text-align:left;">Dimensión</th><th style="border:1px solid #E2E8F0; padding:8px; text-align:left;">Score</th><th style="border:1px solid #E2E8F0; padding:8px; text-align:left;">Nivel</th><th style="border:1px solid #E2E8F0; padding:8px; text-align:left;">Prioridad</th></tr>
        <tr><td style="border:1px solid #E2E8F0; padding:8px;">Liderazgo</td><td style="border:1px solid #E2E8F0; padding:8px;">{leadership_value}</td><td style="border:1px solid #E2E8F0; padding:8px;">Funcional</td><td style="border:1px solid #E2E8F0; padding:8px;">Alta</td></tr>
        <tr><td style="border:1px solid #E2E8F0; padding:8px;">Transformación digital</td><td style="border:1px solid #E2E8F0; padding:8px;">{technology_value}</td><td style="border:1px solid #E2E8F0; padding:8px;">Base sólida</td><td style="border:1px solid #E2E8F0; padding:8px;">Alta</td></tr>
        <tr><td style="border:1px solid #E2E8F0; padding:8px;">Visión de futuro</td><td style="border:1px solid #E2E8F0; padding:8px;">{future_value}</td><td style="border:1px solid #E2E8F0; padding:8px;">Moderada</td><td style="border:1px solid #E2E8F0; padding:8px;">Media</td></tr>
      </table>
      <p>El mapa sugiere un perfil con buena capacidad de adaptación y conciencia de cambio, pero todavía con margen para estructurar mejor la ejecución y convertir más oportunidades en proyectos con resultado visible.</p>

      <h3>Fortalezas</h3>
      <ul>
        <li><strong>Liderazgo funcional:</strong> con {leadership_value}/100, este perfil ya cuenta con cierta capacidad para orientar, sugerir decisiones y aportar criterio en contextos digitales.</li>
        <li><strong>Adaptabilidad:</strong> la flexibilidad del perfil permite aprender rápido, probar nuevas herramientas y ajustar el enfoque ante cambios.</li>
        <li><strong>Visión de mejora:</strong> la orientación hacia la evolución y la optimización es un activo importante para trabajar en entornos complejos.</li>
        <li><strong>Colaboración:</strong> puede aportar valor en equipos, proyectos compartidos y procesos que necesiten coordinación y dinamismo.</li>
      </ul>
      <p>Aunque estas fortalezas son reales, no deben interpretarse como un nivel máximo. Su valor está en decir que el perfil ya tiene base para escalar, siempre que la estructura y el enfoque acompañen el potencial.</p>

      <h3>Áreas de desarrollo</h3>
      <ul>
        <li><strong>Preparación para tecnologías inteligentes:</strong> existe una base inicial, pero aún falta profundidad y práctica aplicada para consolidar un dominio más robusto.</li>
        <li><strong>Automatización:</strong> la persona puede mejorar su capacidad para identificar tareas repetitivas y automatizarlas o simplificarlas con criterio.</li>
        <li><strong>Liderazgo estratégico:</strong> el crecimiento más importante es pasar de un liderazgo funcional a un liderazgo con más impacto, dirección clara y mayor capacidad de influencia.</li>
        <li><strong>Prioridad y focalización:</strong> el perfil avanzará más rápido si reduce la cantidad de objetivos activos y trabaja con unos pocos resultados de alto valor.</li>
      </ul>
      <p>Las áreas de desarrollo no son una crítica negativa; son oportunidades de mejora que permiten convertir un buen perfil en un perfil más efectivo y más consistente.</p>

      <h3>Diagnóstico estratégico</h3>
      <p>Este perfil puede aportar valor a un equipo, un proyecto o una organización si se le da estructura y una dirección clara. La mayor barrera no es la falta de capacidad, sino la dificultad para convertir la energía en decisiones y la curiosidad en ejecución sostenida.</p>
      <p>La diferencia entre un rendimiento medio y un rendimiento superior no depende sólo del talento inicial, sino de la capacidad para definir prioridades, mantener una agenda viable y medirse con indicadores concretos. El enfoque debe ser práctico, realista y sostenido en el tiempo.</p>
      <p>La recomendación estratégica es trabajar una hoja de ruta acotada, con tres objetivos claros, una revisión semanal y una mejora disciplinada de las competencias que más impactan en el entorno profesional.</p>

      <h3>Riesgos y frenos</h3>
      <ul>
        <li>Dispersión de energía: el esfuerzo se reparte demasiado y se diluye el impacto.</li>
        <li>Falta de priorización: se trabajan muchas cosas a la vez y pocas con profundidad.</li>
        <li>Aprendizaje sin aplicación: el conocimiento aumenta, pero no siempre se traduce en resultados visibles.</li>
        <li>Uso superficial de herramientas digitales: se manejan herramientas, pero no siempre se integran con estrategia y criterio.</li>
        <li>Dependencia de iniciativas externas: la progresión se vuelve excesivamente reactiva en vez de proactiva.</li>
        <li>Falta de seguimiento: sin revisión periódica, el potencial no se traduce en evidencia de avance.</li>
      </ul>
      <p>La prevención de estos riesgos pasa por establecer una agenda de trabajo concreta, revisar avances cada semana y distinguir lo urgente de lo realmente importante.</p>

      <h3>Acciones prioritarias</h3>
      <ol>
        <li><strong>Definir tres objetivos concretos para los próximos 90 días.</strong> Esto reduce la dispersión y permite medir el progreso con criterio.</li>
        <li><strong>Mejorar la priorización y la disciplina de ejecución.</strong> Una agenda clara aumenta el valor real del trabajo realizado.</li>
        <li><strong>Fortalecer la comunicación y la coordinación digital.</strong> Mejorar la forma de trabajar con otras personas supone un salto importante en equipos y proyectos complejos.</li>
        <li><strong>Diseñar un proyecto pequeño de automatización.</strong> Esto permite poner en práctica la comprensión tecnológica y convertirla en evidencia real.</li>
        <li><strong>Revisar el aprendizaje.</strong> Documentar qué se ha aprendido, cómo se aplicó y qué resultados obtuvo ayuda a consolidar el progreso.</li>
        <li><strong>Crear un mapa personal de herramientas digitales.</strong> Identificar qué herramientas usa, qué dominio tiene y qué necesita reforzar es una práctica útil y clara.</li>
      </ol>
      <p>Estas acciones no son genéricas ni abstractas: son mecanismos concretos para transformar el potencial en evidencia, ritmo y crecimiento profesional sostenido.</p>

      <h3>Plan de desarrollo</h3>
      <p><strong>Fase 1: primeros 30 días.</strong> Definir objetivos, centrarse en una sola iniciativa clave y revisar semanalmente la ejecución. Se recomienda elegir una tarea concreta que pueda medirse con claridad.</p>
      <p><strong>Fase 2: días 31 a 60.</strong> Reforzar las competencias de automatización, coordinación y priorización. Durante esta etapa se recomienda trabajar con una hoja de ruta ligera y un conjunto pequeño de indicadores.</p>
      <p><strong>Fase 3: días 61 a 90.</strong> Aplicar un proyecto práctico con seguimiento real, evidencias y feedback. Este ciclo es crítico para convertir la capacidad en convicción y resultados.</p>
      <p><strong>Fase 4: meses 4 a 6.</strong> Consolidar un patrón de trabajo más estratégico, con mayor liderazgo y más capacidad para aportar valor en contexto colectivo.</p>
      <p><strong>Fase 5: meses 7 a 12.</strong> Escalar hacia un nivel más robusto de influencia, ejecución y orientación tecnológica, con un plan sostenible y mejor medido.</p>

      <h3>Objetivos a alcanzar</h3>
      <ul>
        <li>Completar una formación aplicada en tecnologías inteligentes.</li>
        <li>Automatizar una tarea repetitiva en el trabajo o en un proyecto personal.</li>
        <li>Liderar una mejora digital de pequeño alcance.</li>
        <li>Crear un repositorio con herramientas y recursos clave.</li>
        <li>Coordinar una iniciativa colaborativa con indicaciones claras.</li>
        <li>Reducir el número de prioridades activas a tres objetivos esenciales.</li>
        <li>Medir avances y registrar evidencia en un formato simple.</li>
        <li>Reforzar la conducta de aprendizaje aplicado y revisión periódica.</li>
      </ul>

      <h3>Línea de tiempo</h3>
      <p><strong>Mes 1:</strong> poner foco en tres objetivos, revisar la agenda y documentar el punto de partida de desarrollo. Resultado esperado: enfoque más limpio y menos dispersión.</p>
      <p><strong>Mes 2:</strong> trabajar en automatización o simplificación de tareas repetitivas y reforzar la disciplina de ejecución. Resultado esperado: menos carga mental y más productividad útil.</p>
      <p><strong>Mes 3:</strong> ejecutar un pequeño proyecto con criterio, seguimiento y evidencia. Resultado esperado: valorar la diferencia entre esfuerzo y impacto.</p>
      <p><strong>Meses 4-6:</strong> convertir la colaboración y la visión de mejora en prácticas diarias de trabajo. Resultado esperado: más influencia y más claridad estratégica.</p>
      <p><strong>Meses 7-12:</strong> consolidar una posición más sólida en liderazgo, tecnología y coordinación. Resultado esperado: un perfil más maduro, visible y capaz de escalar.</p>

      <h3>Conclusión</h3>
      <p>Este perfil combina potencial con capacidad real de evolución. La diferencia entre un rendimiento medio y un rendimiento notable no dependerá solo del talento, sino de la estructura aplicada a la ejecución. Si se organiza bien el foco, la persona puede convertirse en un motor de cambio dentro de su entorno, equipo o proyecto.</p>
      <p>La recomendación final es clara: no reducir el talento, sino canalizarlo. Con una estrategia de enfoque, seguimiento y liderazgo proactivo, este perfil puede escalar de forma notable en contextos exigentes y con alta ambición profesional.</p>
    </div>
    """


def _generar_informe_ai_html(sesion: SesionTest, tipo: str = None) -> str:
    """Genera un informe HTML usando el prompt del tipo de test indicado."""
    tipo = tipo or _resolver_tipo_informe_por_sesion(sesion)
    prompt_text = _read_prompt_for_type(tipo)
    if not prompt_text:
        return _operational_error_html()

    resultados = sesion.resultados_json or {}
    dna = _build_dna_snapshot(resultados)
    primary = getattr(dna, 'primary_archetype', '') or resultados.get('primary_archetype', 'Perfil principal')
    secondary = getattr(dna, 'secondary_archetype', '') or resultados.get('secondary_archetype', 'Perfil secundario')
    tertiary = getattr(dna, 'tertiary_archetype', '') or resultados.get('tertiary_archetype', 'Perfil terciario')
    score = getattr(dna, 'nexus_score', 0) or resultados.get('nexus_score', 0)
    leadership = getattr(dna, 'leadership_score', 0) or resultados.get('leadership_score', 0)
    innovation = getattr(dna, 'innovation_score', 0) or resultados.get('innovation_score', 0)
    technology = getattr(dna, 'technology_score', 0) or resultados.get('technology_score', 0)
    future = getattr(dna, 'future_score', 0) or resultados.get('future_score', 0)
    career = getattr(dna, 'career_score', 0) or resultados.get('career_score', 0)
    reliability = resultados.get('reliability', 0)
    tii_score = resultados.get('tii') or resultados.get('TII') or score or 0

    technology_vector = {
        'digital_curiosity': resultados.get('TM01', 0),
        'technology_adoption': resultados.get('TM02', 0),
        'learning_agility': resultados.get('TM03', 0),
        'ai_readiness': resultados.get('TM04', 0),
        'automation_mindset': resultados.get('TM05', 0),
        'digital_problem_solving': resultados.get('TM06', 0),
        'innovation_orientation': resultados.get('TM07', 0),
        'future_readiness': resultados.get('TM08', 0),
        'technology_confidence': resultados.get('TM09', 0),
        'digital_collaboration': resultados.get('TM10', 0),
    }

    competency_vector = {
        'ai_readiness': resultados.get('comp_ai_readiness', resultados.get('TM04', 0)),
        'automation': resultados.get('comp_automation', resultados.get('TM05', 0)),
        'digital_adaptability': resultados.get('comp_digital_adaptability', resultados.get('TM03', 0)),
        'innovation': resultados.get('comp_innovation', resultados.get('TM07', 0)),
        'digital_leadership': resultados.get('comp_digital_leadership', leadership),
        'future_readiness': resultados.get('comp_future_readiness', future),
        'learning_capacity': resultados.get('comp_learning_capacity', resultados.get('TM03', 0)),
        'transformation_readiness': resultados.get('comp_transformation_readiness', technology),
    }

    prompt_values = {
        'score_global': int(float(score or 0)),
        'arquetipo_principal': primary,
        'arquetipo_secundario': secondary,
        'arquetipo_terciario': tertiary,
        'score_tii': int(float(tii_score or 0)),
        'nivel_tii': _score_to_level(tii_score or 0),
        'score_liderazgo': int(float(leadership or 0)),
        'score_colaboracion': int(float(resultados.get('TM10', 0) or 0)),
        'score_transformacion': int(float(technology or 0)),
        'score_ai_readiness': int(float(resultados.get('TM04', 0) or 0)),
        'score_automatizacion': int(float(resultados.get('TM05', 0) or 0)),
        'score_prep_tecnologias': int(float(resultados.get('TM04', 0) or 0)),
        'score_confiabilidad': int(float(reliability or 0)),
        'dimensiones': '\n'.join([
            f'- {label}: {int(float(value))} ({_score_to_level(value)})'
            for label, value in _rank_dimensiones(resultados)[:10]
        ]),
        'competencias': '\n'.join([
            f'- {label.replace("_", " ").title()}: {int(float(value))} ({_score_to_level(value)})'
            for label, value in sorted(competency_vector.items(), key=lambda item: item[1], reverse=True)
        ]),
        'percentiles': '\n'.join([
            f'- {label}: {int(float(value))}'
            for label, value in [
                ('Global', resultados.get('global_percentile', 0)),
                ('AI', resultados.get('percentile_ai_readiness', 0)),
                ('Innovación', resultados.get('percentile_innovation', 0)),
                ('Liderazgo digital', resultados.get('percentile_digital_leadership', 0)),
                ('Futuro', resultados.get('percentile_future_readiness', 0)),
                ('Automatización', resultados.get('percentile_automation', 0)),
            ]
        ]),
        'validacion': (
            'El perfil presenta coherencia interna en los datos principales y no hay indicios de variabilidad extrema. '
            f'La fiabilidad general estimada es {int(float(reliability or 0))}/100.'
        ),
        'resultados_adicionales': '\n'.join([
            f'- TII: {int(float(tii_score or 0))} ({_score_to_level(tii_score or 0)}), '
            f'Arquetipo terciario: {tertiary}, '
            f'Curiosidad digital: {int(float(technology_vector["digital_curiosity"] or 0))}, '
            f'Colaboración digital: {int(float(technology_vector["digital_collaboration"] or 0))}, '
            f'Aprendizaje: {int(float(technology_vector["learning_agility"] or 0))}'
        ]),
    }

    rendered_prompt = _render_prompt_template(prompt_text, prompt_values)
    if _has_real_prompt_structure(prompt_text):
        return rendered_prompt

    structured_fallback = _build_structured_report_fallback(
        sesion,
        tipo,
        prompt_text,
        score,
        primary,
        secondary,
        leadership,
        technology,
        future,
    )
    if 'Resumen Ejecutivo' in structured_fallback or 'Riesgos y frenos' in structured_fallback:
        return structured_fallback
    return rendered_prompt



def _actualizar_dna_desde_resultados(user, resultados: dict):
    """Extrae campos estándar del JSON del test y actualiza/crea el DNAProfile."""
    from decimal import Decimal, InvalidOperation

    def _dec(key):
        try:
            return Decimal(str(resultados.get(key, 0))).quantize(Decimal('0.01'))
        except (InvalidOperation, TypeError):
            return Decimal('0')

    campos = {'vector': resultados}

    campos_numericos = [
        'nexus_score', 'leadership_score', 'innovation_score', 'technology_score',
        'future_score', 'career_score', 'executive_score', 'motivation_score',
        'cognitive_score', 'emotional_score', 'reliability',
    ]
    campos_texto = ['primary_archetype', 'secondary_archetype', 'tertiary_archetype']

    for c in campos_numericos:
        if c in resultados:
            campos[c] = _dec(c)
    for c in campos_texto:
        if c in resultados:
            campos[c] = str(resultados[c])[:100]

    DNAProfile.objects.update_or_create(user=user, defaults=campos)


class AlumnoListaTestsView(LoginRequiredMixin, ListView):
    """Lista de TestPlantilla activos para el alumno."""
    model = TestPlantilla
    template_name = 'nexus/student/list.html'
    context_object_name = 'tests'

    def get_queryset(self):
        return TestPlantilla.objects.filter(activo=True)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sesiones_qs = SesionTest.objects.filter(alumno=self.request.user).order_by('-creado_en')

        # Mapa test_id → lista de sesiones (ordenadas por fecha desc)
        from collections import defaultdict
        sesiones_por_test = defaultdict(list)
        for s in sesiones_qs:
            sesiones_por_test[s.test_id].append(s)

        tests_enriquecidos = []
        for t in ctx['tests']:
            sesiones = sesiones_por_test.get(t.pk, [])
            completadas = [s for s in sesiones if s.estado == SesionTest.COMPLETADO]
            en_progreso = next((s for s in sesiones if s.estado == SesionTest.EN_PROGRESO), None)
            ultima_completada = completadas[0] if completadas else None
            tests_enriquecidos.append({
                'test': t,
                'sesiones': sesiones,
                'completadas': completadas,
                'en_progreso': en_progreso,
                'ultima_completada': ultima_completada,
                'num_completadas': len(completadas),
                'accesible': _usuario_puede_acceder(self.request.user, t),
            })
        ctx['tests_enriquecidos'] = tests_enriquecidos

        try:
            ctx['mi_dna'] = DNAProfile.objects.get(user=self.request.user)
        except DNAProfile.DoesNotExist:
            ctx['mi_dna'] = None
        return ctx


class RealizarTestView(LoginRequiredMixin, View):
    """
    Crea/recupera la sesión del alumno y renderiza el archivo HTML del test
    usando el motor de templates de Django, pasando user + sesion + urls en el contexto.
    """

    def _get_o_crear_sesion(self, request, test):
        # Continuar sesión en progreso si existe; si no, crear una nueva
        sesion = SesionTest.objects.filter(
            test=test, alumno=request.user, estado=SesionTest.EN_PROGRESO
        ).first()
        if sesion is None:
            sesion = SesionTest.objects.create(
                test=test,
                alumno=request.user,
                estado=SesionTest.EN_PROGRESO,
                iniciado_en=timezone.now(),
            )
        return sesion

    def get(self, request, pk):
        test = get_object_or_404(TestPlantilla, pk=pk, activo=True)

        if not _usuario_puede_acceder(request.user, test):
            nivel_label = test.get_nivel_acceso_display()
            messages.error(
                request,
                f'Este test es {nivel_label}. '
                + ('Necesitas una membresía premium para acceder.' if test.nivel_acceso == 'premium'
                   else 'Inicia sesión para acceder.'),
            )
            return redirect('nexus:student_list')

        sesion = self._get_o_crear_sesion(request, test)

        # Leer el archivo HTML subido y renderizarlo como template Django
        try:
            with open(test.archivo_html.path, 'r', encoding='utf-8') as f:
                contenido_html = f.read()
        except (FileNotFoundError, ValueError):
            messages.error(request, 'El archivo del test no se encontró. Contacta al administrador.')
            print('El archivo del test no se encontró: ', test.archivo_html.path)
            return redirect('nexus:student_list')

        motor = template_engines['django']
        template_obj = motor.from_string(contenido_html)

        contexto = {
            'user': request.user,
            'test': test,
            'sesion': sesion,
            'url_enviar': reverse('nexus:student_submit', kwargs={'pk': sesion.pk}),
            'url_guardar_informe': reverse('nexus:guardar_informe', kwargs={'pk': sesion.pk}),
            'url_lista': reverse('nexus:student_list'),
        }
        html = template_obj.render(contexto, request)

        user_role = getattr(request.user, 'role', 'basic')
        if user_role not in {'premium', 'alumno'}:
            html = html.replace('</body>', _version_lock_script(user_role) + '\n</body>', 1)

        html = html.replace('</body>', _version_capture_script() + '\n</body>', 1)
        html = html.replace('</body>', _auto_select_test_version_script(test) + '\n</body>', 1)

        if settings.DEBUG:
            html = html.replace('</body>', _dev_autofill_script() + '\n</body>', 1)

        return HttpResponse(html)


class EnviarTestView(LoginRequiredMixin, View):
    """
    Recibe el POST del test HTML.
    Espera el campo 'resultados_json' con un JSON de resultados.
    Actualiza SesionTest y DNAProfile del alumno.
    """

    def post(self, request, pk):
        sesion = get_object_or_404(SesionTest, pk=pk, alumno=request.user)

        if sesion.estado == SesionTest.COMPLETADO:
            return redirect(reverse('nexus:student_results', kwargs={'pk': sesion.pk}))

        # Parsear resultados enviados por el test HTML
        import json
        raw_json = request.POST.get('resultados_json', '{}')
        try:
            resultados = json.loads(raw_json)
        except json.JSONDecodeError:
            resultados = {}

        # Guardar también cualquier campo POST extra (por si el HTML no usa JSON)
        if not resultados:
            resultados = {k: v for k, v in request.POST.items() if k != 'csrfmiddlewaretoken'}

        # Actualizar sesión
        from decimal import Decimal, InvalidOperation
        sesion.resultados_json = resultados
        try:
            sesion.nexus_score = Decimal(str(resultados.get('nexus_score', 0))).quantize(Decimal('0.01'))
        except (InvalidOperation, TypeError):
            sesion.nexus_score = None
        version_recibida = resultados.get('version', '').lower()
        if version_recibida in ('essential', 'advanced', 'premium'):
            sesion.version_test = version_recibida
        sesion.estado = SesionTest.COMPLETADO
        sesion.completado_en = timezone.now()
        sesion.save()

        # Actualizar/crear DNAProfile
        _actualizar_dna_desde_resultados(request.user, resultados)

        messages.success(request, '¡Test completado! Aquí están tus resultados Nexus DNA.')
        return redirect(reverse('nexus:student_results', kwargs={'pk': sesion.pk}))

    def get(self, request, pk):
        return redirect('nexus:student_list')


class ResultadosTestView(LoginRequiredMixin, DetailView):
    """Página de resultados tras completar el test."""
    model = SesionTest
    template_name = 'nexus/student/results.html'
    context_object_name = 'sesion'

    def dispatch(self, request, *args, **kwargs):
        sesion = get_object_or_404(SesionTest, pk=kwargs['pk'], alumno=request.user)
        if sesion.estado != SesionTest.COMPLETADO:
            messages.warning(request, 'Este test aún no ha sido completado. Puedes continuarlo desde aquí.')
            return redirect(reverse('nexus:student_take', kwargs={'pk': sesion.test.pk}))
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return get_object_or_404(
            SesionTest,
            pk=self.kwargs['pk'],
            alumno=self.request.user,
            estado=SesionTest.COMPLETADO,
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sesion = self.object

        nombre = sesion.test.nombre or ''
        nombre_lower = nombre.lower()
        resultado = sesion.resultados_json or {}
        ctx['is_techmind'] = (
            'techmind' in nombre_lower or
            resultado.get('test_tipo') == 'techmind'
        )
        ctx['is_careernav'] = (
            'career' in nombre_lower or
            'navigator' in nombre_lower or
            resultado.get('test_tipo') == 'careernav'
        )
        ctx['is_behaviormatrix'] = (
            'behavior' in nombre_lower or
            'matrix' in nombre_lower or
            resultado.get('test_tipo') == 'behaviormatrix'
        )
        ctx['is_conflictsync'] = (
            'conflict' in nombre_lower or
            resultado.get('test_tipo') == 'conflictsync'
        )
        ctx['is_personasphere'] = (
            'personasphere' in nombre_lower or
            'persona' in nombre_lower and 'sphere' in nombre_lower or
            resultado.get('test_tipo') == 'personasphere'
        )
        ctx['is_decisioncore'] = (
            'decisioncore' in nombre_lower or
            'decision' in nombre_lower and 'core' in nombre_lower or
            resultado.get('test_tipo') == 'decisioncore'
        )
        ctx['is_identitymap'] = (
            'identitymap' in nombre_lower or
            'identity' in nombre_lower and 'map' in nombre_lower or
            resultado.get('test_tipo') == 'identitymap'
        )

        if sesion.resultados_json:
            ctx['dna'] = _build_dna_snapshot(sesion.resultados_json)
            ctx['dna_sin_datos'] = False
        else:
            ctx['dna'] = None
            ctx['dna_sin_datos'] = True

        indices_labels = [
            ('Leadership',  'leadership_score',  '#DC2626'),
            ('Innovation',  'innovation_score',  '#8B5CF6'),
            ('Technology',  'technology_score',  '#06B6D4'),
            ('Future',      'future_score',      '#22C55E'),
            ('Career',      'career_score',      '#F59E0B'),
            ('Executive',   'executive_score',   '#EC4899'),
            ('Motivation',  'motivation_score',  '#2563EB'),
            ('Cognitive',   'cognitive_score',   '#6366F1'),
        ]
        ctx['indices_labels'] = indices_labels

        # Cargar definiciones de dimensiones desde la base de datos
        import json as _json
        from .models import DimensionDefinicion
        if ctx['is_techmind']:
            test_tipo = 'techmind'
        elif ctx['is_careernav']:
            test_tipo = 'careernav'
        elif ctx['is_behaviormatrix']:
            test_tipo = 'behaviormatrix'
        elif ctx['is_conflictsync']:
            test_tipo = 'conflictsync'
        elif ctx['is_decisioncore']:
            test_tipo = 'decisioncore'
        elif ctx['is_identitymap']:
            test_tipo = 'identitymap'
        else:
            test_tipo = ''

        defs = DimensionDefinicion.objects.filter(
            test_tipo__in=[test_tipo, '']
        ).prefetch_related('rangos').order_by('test_tipo', 'orden', 'label')

        dim_info_db = {}
        for d in defs:
            dim_info_db[d.label] = {
                'descripcion': d.descripcion,
                'icono': d.icono,
                'rangos': [
                    {
                        'min': r.rango_min,
                        'max': r.rango_max,
                        'titulo': r.titulo,
                        'explicacion': r.explicacion,
                        'recomendacion': r.recomendacion,
                        'color': r.color_nivel,
                    }
                    for r in d.rangos.all()
                ],
            }
        ctx['dim_info_db_json'] = _json.dumps(dim_info_db, ensure_ascii=False)
        ctx['informes'] = sesion.informes.all()
        return ctx


class MiDNAView(LoginRequiredMixin, View):
    """Perfil DNA propio del alumno."""

    def get(self, request):
        import json as _json

        try:
            dna = DNAProfile.objects.get(user=request.user)
        except DNAProfile.DoesNotExist:
            messages.info(request, 'Aún no tienes un perfil DNA. Completa un test primero.')
            return redirect('nexus:student_list')

        indices = [
            ('Leadership',  float(dna.leadership_score),  '#DC2626'),
            ('Innovation',  float(dna.innovation_score),  '#8B5CF6'),
            ('Technology',  float(dna.technology_score),  '#06B6D4'),
            ('Future',      float(dna.future_score),      '#22C55E'),
            ('Career',      float(dna.career_score),      '#F59E0B'),
            ('Executive',   float(dna.executive_score),   '#EC4899'),
            ('Motivation',  float(dna.motivation_score),  '#2563EB'),
            ('Cognitive',   float(dna.cognitive_score),   '#6366F1'),
        ]

        # ── Build per-test chart data ──────────────────────────────────────────
        # Config: (test_tipo, color principal, color fondo, etiqueta display)
        _TEST_META = {
            'behaviormatrix': {
                'color': '#F97316', 'bg': 'rgba(249,115,22,.15)', 'label': 'BehaviorMatrix™',
                'dims': [
                    ('BM01', 'Execution Drive', '#F97316'),
                    ('BM02', 'Accountability',  '#1E3A8A'),
                    ('BM03', 'Adaptability',    '#8B5CF6'),
                    ('BM04', 'Collaboration',   '#14B8A6'),
                    ('BM05', 'Initiative',      '#38BDF8'),
                    ('BM06', 'Decision Behavior','#EF4444'),
                    ('BM07', 'Consistency',     '#22C55E'),
                    ('BM08', 'Pressure Response','#3B82F6'),
                ],
            },
            'techmind': {
                'color': '#00C4FF', 'bg': 'rgba(0,196,255,.12)', 'label': 'TechMind™',
                'dims': [
                    ('TM01','Aprendizaje','#06B6D4'), ('TM02','Adaptabilidad','#0EA5E9'),
                    ('TM03','Learning Agility','#3B82F6'), ('TM04','Juicio','#8B5CF6'),
                    ('TM05','Impulso Ejec.','#7C3AED'), ('TM06','Resolución','#EC4899'),
                    ('TM07','Iniciativa','#F59E0B'), ('TM08','Visión Estrat.','#10B981'),
                    ('TM09','Autoconfianza','#22C55E'), ('TM10','Colaboración','#00C4FF'),
                ],
            },
            'careernav': {
                'color': '#22C55E', 'bg': 'rgba(34,197,94,.12)', 'label': 'CareerNavigator™',
                'dims': [],  # usa dimensiones dinámicas del JSON
            },
            'conflictsync': {
                'color': '#F97316', 'bg': 'rgba(249,115,22,.12)', 'label': 'ConflictSync™',
                'dims': [
                    ('CS01', 'Conflict Awareness',      '#F97316'),
                    ('CS02', 'Emotional Regulation',    '#EF4444'),
                    ('CS03', 'Assertive Communication', '#F59E0B'),
                    ('CS04', 'Negotiation Style',       '#10B981'),
                    ('CS05', 'Conflict Resolution',     '#6366F1'),
                    ('CS06', 'Empathy Under Tension',   '#EC4899'),
                    ('CS07', 'Fairness Orientation',    '#0EA5E9'),
                    ('CS08', 'Post-Conflict Recovery',  '#8B5CF6'),
                ],
            },
            'decisioncore': {
                'color': '#DC2626', 'bg': 'rgba(220,38,38,.12)', 'label': 'DecisionCore™',
                'dims': [
                    ('DC01', 'Análisis',              '#00D4FF'),
                    ('DC02', 'Intuición',             '#7C3AED'),
                    ('DC03', 'Orientación a Datos',   '#10B981'),
                    ('DC04', 'Velocidad Decisional',  '#F59E0B'),
                    ('DC05', 'Gestión del Riesgo',    '#EF4444'),
                    ('DC06', 'P. Estratégico',        '#6366F1'),
                    ('DC07', 'Colaboración Dec.',      '#06B6D4'),
                    ('DC08', 'Innovación',            '#EC4899'),
                    ('DC09', 'Adaptabilidad',         '#0EA5E9'),
                    ('DC10', 'Confianza Decisional',  '#1D4ED8'),
                ],
            },
            'identitymap': {
                'color': '#2563EB', 'bg': 'rgba(37,99,235,.12)', 'label': 'IdentityMap™',
                'dims': [
                    ('IM01', 'Reformador',      '#10B981'),
                    ('IM02', 'Protector',       '#EC4899'),
                    ('IM03', 'Constructor',     '#F59E0B'),
                    ('IM04', 'Individualista',  '#8B5CF6'),
                    ('IM05', 'Investigador',    '#06B6D4'),
                    ('IM06', 'Precavido',       '#0EA5E9'),
                    ('IM07', 'Explorador',      '#3B82F6'),
                    ('IM08', 'Desafiador',      '#EF4444'),
                    ('IM09', 'Pacificador',     '#14B8A6'),
                ],
            },
        }

        sesiones = (
            SesionTest.objects
            .filter(alumno=request.user, estado=SesionTest.COMPLETADO)
            .select_related('test')
            .prefetch_related('informes')
            .order_by('-completado_en')
        )

        tests_chart = []
        for sesion in sesiones:
            r = sesion.resultados_json or {}
            nombre_lower = (sesion.test.nombre or '').lower()
            tipo = r.get('test_tipo') or (
                'behaviormatrix' if 'behavior' in nombre_lower or 'matrix' in nombre_lower else
                'techmind'       if 'techmind' in nombre_lower else
                'careernav'      if 'career' in nombre_lower or 'navigator' in nombre_lower else
                'conflictsync'   if 'conflict' in nombre_lower else
                'decisioncore'   if 'decision' in nombre_lower and 'core' in nombre_lower else
                'identitymap'    if 'identity' in nombre_lower and 'map' in nombre_lower else
                'otro'
            )
            meta = _TEST_META.get(tipo, {'color': '#64748B', 'bg': 'rgba(100,116,139,.12)', 'label': sesion.test.nombre, 'dims': []})

            # Para tests con dims estáticas (BM, TM)
            if meta['dims']:
                dims_data = [
                    {'key': k, 'label': lbl, 'color': col, 'score': int(r.get(k, 0) or 0)}
                    for k, lbl, col in meta['dims']
                ]
            else:
                # CareerNavigator y otros: usar dimensiones del JSON
                dims_data = [
                    {'key': str(i), 'label': d.get('label',''), 'color': d.get('color','#64748B'), 'score': int(d.get('score', 0) or 0)}
                    for i, d in enumerate(r.get('dimensiones', []))
                ]

            score_val = float(r.get('nexus_score') or sesion.nexus_score or 0)
            informes_list = [
                {'pk': inf.pk, 'tipo': inf.tipo, 'tipo_display': inf.get_tipo_display(), 'icono': inf.icono, 'creado_en': inf.creado_en.strftime('%d/%m/%Y %H:%M') if inf.creado_en else ''}
                for inf in sesion.informes.all()
            ]
            tests_chart.append({
                'pk':         sesion.pk,
                'nombre':     sesion.test.nombre,
                'tipo':       tipo,
                'label':      meta['label'],
                'color':      meta['color'],
                'bg':         meta['bg'],
                'score':      round(score_val, 1),
                'fecha':      sesion.completado_en.strftime('%d/%m/%Y') if sesion.completado_en else '—',
                'dimensiones': dims_data,
                'primary_archetype':   r.get('primary_archetype', ''),
                'secondary_archetype': r.get('secondary_archetype', ''),
                'informes':   informes_list,
            })

        tests_chart_json = _json.dumps(tests_chart, ensure_ascii=False)

        all_informes = (
            InformeTest.objects
            .filter(sesion__alumno=request.user, sesion__estado=SesionTest.COMPLETADO)
            .select_related('sesion__test')
            .order_by('-creado_en')
        )

        return render(request, 'nexus/student/mi_dna.html', {
            'dna': dna,
            'indices': indices,
            'tests_chart': tests_chart,
            'tests_chart_json': tests_chart_json,
            'informes': all_informes,
        })


# Mapeo de índices DNA → (nombre skill, categoría curriculum)
_NEXUS_SKILL_MAP = [
    ('leadership_score',  'Liderazgo',               's_liderazgo'),
    ('executive_score',   'Gestión Ejecutiva',        's_liderazgo'),
    ('emotional_score',   'Inteligencia Emocional',   's_comunicacion'),
    ('motivation_score',  'Motivación e Influencia',  's_comunicacion'),
    ('cognitive_score',   'Pensamiento Crítico',       's_eficacia'),
    ('innovation_score',  'Innovación y Creatividad', 's_adaptabilidad'),
    ('future_score',      'Visión de Futuro',         's_adaptabilidad'),
    ('technology_score',  'Mentalidad Digital',       's_adaptabilidad'),
    ('career_score',      'Visión de Negocio',        's_negocio'),
]


def _score_to_nivel(score_decimal):
    """Convierte un score 0–100 a nivel 1–5."""
    s = float(score_decimal)
    if s >= 80:
        return 5
    elif s >= 60:
        return 4
    elif s >= 40:
        return 3
    elif s >= 20:
        return 2
    return 1


class GuardarEnPerfilView(LoginRequiredMixin, View):
    """
    POST: guarda los resultados de la sesión en la sección Nexus DNA del CV
    del usuario y también sincroniza los índices como soft skills.
    """

    def post(self, request, pk):
        from apps.curriculum.models import CurriculumVitae, Skill, NexusDNAEntry

        sesion = get_object_or_404(SesionTest, pk=pk, alumno=request.user, estado=SesionTest.COMPLETADO)

        if not sesion.resultados_json:
            messages.error(
                request,
                'Esta sesión no tiene datos DNA propios. Vuelve a realizar el test para obtener resultados guardables.',
            )
            return redirect(reverse('nexus:student_results', kwargs={'pk': sesion.pk}))

        cv, _ = CurriculumVitae.objects.get_or_create(user=request.user)

        # Build DNA snapshot from this specific session's stored JSON
        dna = _build_dna_snapshot(sesion.resultados_json)

        dimensiones = sesion.resultados_json.get('dimensiones', [])

        # Save/update Nexus DNA entry for this session
        r = sesion.resultados_json or {}
        NexusDNAEntry.objects.update_or_create(
            cv=cv,
            sesion_id=sesion.pk,
            defaults={
                'test_nombre': sesion.test.nombre,
                'completado_en': sesion.completado_en or sesion.creado_en,
                'nexus_score': dna.nexus_score,
                'primary_archetype': dna.primary_archetype,
                'secondary_archetype': dna.secondary_archetype,
                'tertiary_archetype': dna.tertiary_archetype,
                'leadership_score': dna.leadership_score,
                'innovation_score': dna.innovation_score,
                'technology_score': dna.technology_score,
                'future_score': dna.future_score,
                'career_score': dna.career_score,
                'executive_score': dna.executive_score,
                'motivation_score': dna.motivation_score,
                'cognitive_score': dna.cognitive_score,
                'emotional_score': dna.emotional_score,
                'dimensiones': dimensiones,
                'version_test': sesion.version_test or r.get('version', ''),
                'total_preguntas': r.get('total_preguntas'),
                'num_likert': r.get('num_likert'),
                'num_abcd': r.get('num_abcd'),
            },
        )

        # Also sync as soft skills (backward compat)
        for field_name, nombre_skill, categoria in _NEXUS_SKILL_MAP:
            nivel = _score_to_nivel(getattr(dna, field_name))
            Skill.objects.update_or_create(
                cv=cv,
                tipo='soft',
                nombre=nombre_skill,
                defaults={'categoria': categoria, 'nivel': nivel},
            )

        sesion.guardado_en_cv = True
        sesion.save(update_fields=['guardado_en_cv'])

        messages.success(
            request,
            '¡Resultados guardados en tu sección Nexus DNA! Puedes gestionar qué tests mostrar desde tu Currículum.',
        )
        return redirect(reverse('curriculum:nexus_dna_list'))

    def get(self, request, pk):
        return redirect(reverse('nexus:student_results', kwargs={'pk': pk}))


_NEXUS_SKILL_NOMBRES = [nombre for _, nombre, _ in _NEXUS_SKILL_MAP]


class HistorialTestView(LoginRequiredMixin, View):
    """Lista de todas las sesiones completadas del alumno para un test."""

    def get(self, request, pk):
        test = get_object_or_404(TestPlantilla, pk=pk, activo=True)
        sesiones = SesionTest.objects.filter(
            test=test, alumno=request.user, estado=SesionTest.COMPLETADO
        ).order_by('-completado_en')
        mejor_sesion = (
            SesionTest.objects.filter(
                test=test, alumno=request.user, estado=SesionTest.COMPLETADO,
                nexus_score__isnull=False,
            ).order_by('-nexus_score').first()
        )
        return render(request, 'nexus/student/historial_test.html', {
            'test': test,
            'sesiones': sesiones,
            'mejor_sesion': mejor_sesion,
        })


class BorrarSesionView(LoginRequiredMixin, View):
    """
    Permite al alumno borrar su sesión de test (y opcionalmente las soft skills
    guardadas en CV). Si era la única sesión completada, borra también el DNAProfile.
    """

    def get(self, request, pk):
        sesion = get_object_or_404(SesionTest, pk=pk, alumno=request.user)
        return render(request, 'nexus/student/confirm_delete_session.html', {'sesion': sesion})

    def post(self, request, pk):
        sesion = get_object_or_404(SesionTest, pk=pk, alumno=request.user)
        test_nombre = sesion.test.nombre
        borrar_skills = request.POST.get('borrar_skills') == '1'

        if borrar_skills and sesion.guardado_en_cv:
            from apps.curriculum.models import CurriculumVitae, Skill
            try:
                cv = CurriculumVitae.objects.get(user=request.user)
                Skill.objects.filter(cv=cv, tipo='soft', nombre__in=_NEXUS_SKILL_NOMBRES).delete()
            except CurriculumVitae.DoesNotExist:
                pass

        sesion.delete()

        otras = SesionTest.objects.filter(alumno=request.user, estado=SesionTest.COMPLETADO).exists()
        if not otras:
            DNAProfile.objects.filter(user=request.user).delete()

        messages.success(
            request,
            f'Resultados de "{test_nombre}" eliminados. Puedes repetir el test cuando quieras.',
        )
        return redirect('nexus:student_list')


# =============================================================================
# TestInfoView — página de información del test con recursos adjuntos
# =============================================================================

class TestInfoView(LoginRequiredMixin, DetailView):
    """Página pública del test: descripción + recursos para el alumno."""
    model = TestPlantilla
    template_name = 'nexus/student/test_info.html'
    context_object_name = 'test'

    def get_queryset(self):
        return TestPlantilla.objects.filter(activo=True)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        test = self.object
        ctx['recursos'] = test.recursos.filter(activo=True)
        # Sesión activa del alumno para este test
        ctx['sesion_completada'] = SesionTest.objects.filter(
            test=test, alumno=self.request.user, estado=SesionTest.COMPLETADO
        ).order_by('-completado_en').first()
        ctx['sesion_en_progreso'] = SesionTest.objects.filter(
            test=test, alumno=self.request.user, estado=SesionTest.EN_PROGRESO
        ).first()
        return ctx


# =============================================================================
# CRUD de Recursos — solo admin/staff
# =============================================================================

class RecursoCreateView(AdminRequiredMixin, CreateView):
    model = RecursoTest
    form_class = RecursoTestForm
    template_name = 'nexus/recursos/form.html'

    def get_test(self):
        return get_object_or_404(TestPlantilla, pk=self.kwargs['test_pk'])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['test'] = self.get_test()
        ctx['action'] = 'Añadir recurso'
        return ctx

    def form_valid(self, form):
        form.instance.test = self.get_test()
        messages.success(self.request, 'Recurso añadido correctamente.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('nexus:test_info', kwargs={'pk': self.object.test.pk})


class RecursoUpdateView(AdminRequiredMixin, UpdateView):
    model = RecursoTest
    form_class = RecursoTestForm
    template_name = 'nexus/recursos/form.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['test'] = self.object.test
        ctx['action'] = 'Editar recurso'
        return ctx

    def form_valid(self, form):
        messages.success(self.request, 'Recurso actualizado.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('nexus:test_info', kwargs={'pk': self.object.test.pk})


class RecursoDeleteView(AdminRequiredMixin, DeleteView):
    model = RecursoTest
    template_name = 'nexus/recursos/confirm_delete.html'

    def get_success_url(self):
        return reverse('nexus:test_info', kwargs={'pk': self.object.test.pk})

    def form_valid(self, form):
        messages.success(self.request, 'Recurso eliminado.')
        return super().form_valid(form)


# =============================================================================
# DEMO / ACCESO LIBRE — test esencial sin login, sin almacenamiento
# =============================================================================

class DemoListView(View):
    """Landing pública: lista los tests marcados como es_demo=True."""

    def get(self, request):
        tests = TestPlantilla.objects.filter(
            activo=True, es_demo=True, nivel_acceso=TestPlantilla.NIVEL_ESSENTIAL,
        )
        return render(request, 'nexus/demo/index.html', {'tests': tests})


class DemoTestView(View):
    """Renderiza el test demo sin crear sesión en base de datos."""

    def get(self, request, pk):
        test = get_object_or_404(
            TestPlantilla, pk=pk, activo=True,
            es_demo=True, nivel_acceso=TestPlantilla.NIVEL_ESSENTIAL,
        )
        try:
            with open(test.archivo_html.path, 'r', encoding='utf-8') as f:
                contenido_html = f.read()
        except (FileNotFoundError, ValueError):
            messages.error(request, 'El archivo del test no está disponible. Inténtalo más tarde.')
            return redirect('nexus:demo_list')

        motor = template_engines['django']
        template_obj = motor.from_string(contenido_html)

        # Sesión ficticia — los templates del test solo leen sesion.pk para
        # embeber el pk en url_enviar, que aquí proveemos explícitamente.
        fake_sesion = SimpleNamespace(pk=0)

        contexto = {
            'user': request.user,
            'test': test,
            'sesion': fake_sesion,
            'url_enviar': reverse('nexus:demo_submit', kwargs={'pk': pk}),
            'url_lista': reverse('nexus:demo_list'),
            'es_demo': True,
        }
        html = template_obj.render(contexto, request)

        # Inyectar bloqueo de versiones advanced/premium en modo demo.
        # Los archivos HTML tienen card-essential/card-advanced/card-premium;
        # ocultamos las dos superiores y forzamos la selección a essential.
        demo_lock = """
<script>
(function () {
  function lockDemoVersion() {
    var adv  = document.getElementById('card-advanced');
    var prem = document.getElementById('card-premium');
    if (adv)  { adv.style.display  = 'none'; }
    if (prem) { prem.style.display = 'none'; }

    // Añadir nota bajo la card essential
    var ess = document.getElementById('card-essential');
    if (ess && !document.getElementById('demo-note')) {
      var note = document.createElement('p');
      note.id = 'demo-note';
      note.style.cssText = 'font-size:11px;color:#6B7280;margin:6px 0 0;text-align:center;';
      note.innerHTML = '&#x1F512; Modo demo &middot; s&oacute;lo versi&oacute;n Essential disponible sin cuenta. '
        + '<a href="/nexus/demo/" style="color:#0090cc;text-decoration:none;font-weight:600;">Ver todos los tests &rarr;</a>';
      ess.insertAdjacentElement('afterend', note);
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', lockDemoVersion);
  } else {
    lockDemoVersion();
  }
  // Re-ejecutar por si la navegación interna del test re-pinta la pantalla
  setTimeout(lockDemoVersion, 300);
  setTimeout(lockDemoVersion, 800);
})();
</script>
"""
        html = html.replace('</body>', demo_lock + '\n</body>', 1)

        if settings.DEBUG:
            html = html.replace('</body>', _dev_autofill_script() + '\n</body>', 1)

        return HttpResponse(html)


class DemoEnviarView(View):
    """Recibe el POST del test demo, guarda resultados en sesión Django y redirige."""

    def post(self, request, pk):
        import json as _json
        test = get_object_or_404(
            TestPlantilla, pk=pk, activo=True,
            es_demo=True, nivel_acceso=TestPlantilla.NIVEL_ESSENTIAL,
        )

        raw = request.POST.get('resultados_json', '{}')
        try:
            resultados = _json.loads(raw)
        except _json.JSONDecodeError:
            resultados = {}
        if not resultados:
            resultados = {k: v for k, v in request.POST.items() if k != 'csrfmiddlewaretoken'}

        nombre_lower = (test.nombre or '').lower()
        tipo = resultados.get('test_tipo', '')
        request.session['nexus_demo_result'] = {
            'resultados': resultados,
            'test_nombre': test.nombre,
            'is_techmind': tipo == 'techmind' or 'techmind' in nombre_lower,
            'is_careernav': tipo == 'careernav' or 'career' in nombre_lower or 'navigator' in nombre_lower,
            'is_behaviormatrix': tipo == 'behaviormatrix' or 'behavior' in nombre_lower or 'matrix' in nombre_lower,
        }
        return redirect(reverse('nexus:demo_results'))

    def get(self, request, pk):
        return redirect('nexus:demo_list')


class DemoResultadosView(View):
    """Muestra los resultados del test demo leídos de la sesión Django."""

    def get(self, request):
        demo_data = request.session.get('nexus_demo_result')
        if not demo_data:
            return redirect('nexus:demo_list')

        resultados = demo_data.get('resultados', {})
        dna = _build_dna_snapshot(resultados) if resultados else None

        indices_labels = [
            ('Leadership',  'leadership_score',  '#DC2626'),
            ('Innovation',  'innovation_score',  '#8B5CF6'),
            ('Technology',  'technology_score',  '#06B6D4'),
            ('Future',      'future_score',      '#22C55E'),
            ('Career',      'career_score',      '#F59E0B'),
            ('Executive',   'executive_score',   '#EC4899'),
            ('Motivation',  'motivation_score',  '#2563EB'),
            ('Cognitive',   'cognitive_score',   '#6366F1'),
        ]
        return render(request, 'nexus/demo/results.html', {
            'dna': dna,
            'dna_sin_datos': dna is None,
            'is_techmind': demo_data.get('is_techmind', False),
            'is_careernav': demo_data.get('is_careernav', False),
            'is_behaviormatrix': demo_data.get('is_behaviormatrix', False),
            'test_nombre': demo_data.get('test_nombre', ''),
            'indices_labels': indices_labels,
        })


# =============================================================================
# INFORMES — guardar y consultar informes generados
# =============================================================================

import json as _json
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST as require_post_method


class GuardarInformeView(LoginRequiredMixin, View):
    """POST AJAX: guarda el HTML de un informe asociado a una sesión."""

    def post(self, request, pk):
        sesion = get_object_or_404(SesionTest, pk=pk, alumno=request.user, estado=SesionTest.COMPLETADO)

        try:
            data = _json.loads(request.body)
        except (_json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'JSON inválido.'}, status=400)

        tipo = data.get('tipo', '')
        contenido = data.get('contenido_html', '')

        tipos_validos = dict(InformeTest.TIPO_CHOICES)
        if tipo not in tipos_validos:
            return JsonResponse({'error': f'Tipo inválido: {tipo}'}, status=400)
        if not contenido or len(contenido) < 20:
            return JsonResponse({'error': 'Contenido vacío.'}, status=400)

        informe, created = InformeTest.objects.update_or_create(
            sesion=sesion,
            tipo=tipo,
            defaults={'contenido_html': contenido},
        )

        return JsonResponse({
            'ok': True,
            'created': created,
            'informe_id': informe.pk,
            'tipo_label': informe.get_tipo_display(),
        })


class GenerarInformeAIView(LoginRequiredMixin, View):
    """Genera un informe IA profesional a partir del JSON del test y lo guarda."""

    def post(self, request, pk):
        sesion = get_object_or_404(SesionTest, pk=pk, alumno=request.user, estado=SesionTest.COMPLETADO)
        tipo = request.POST.get('tipo') or _resolver_tipo_informe_por_sesion(sesion)

        if tipo not in dict(InformeTest.TIPO_CHOICES):
            tipo = _resolver_tipo_informe_por_sesion(sesion)

        html = _generar_informe_ai_html(sesion, tipo=tipo)
        if 'No puedo crear informe por un error operativo' in html:
            return JsonResponse({
                'ok': False,
                'error': 'No puedo crear informe por un error operativo',
            }, status=500)

        informe, created = InformeTest.objects.update_or_create(
            sesion=sesion,
            tipo=tipo,
            defaults={'contenido_html': html},
        )

        return JsonResponse({
            'ok': True,
            'created': created,
            'informe_id': informe.pk,
            'tipo': tipo,
            'tipo_label': informe.get_tipo_display(),
            'html': html,
            'redirect_url': reverse('nexus:ver_informe', kwargs={'pk': informe.pk}),
        })


def _usuario_tiene_acceso_informe(usuario, informe):
    if not usuario.is_authenticated:
        return False
    if getattr(usuario, 'role', '') == 'premium':
        return True
    return InformePago.objects.filter(
        informe=informe,
        usuario=usuario,
        estado=InformePago.ESTADO_PAGADO,
    ).exists()


class CheckoutInformeView(LoginRequiredMixin, DetailView):
    model = InformeTest
    template_name = 'nexus/student/checkout_informe.html'
    context_object_name = 'informe'

    def get_queryset(self):
        return InformeTest.objects.filter(sesion__alumno=self.request.user).select_related('sesion__test')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pago = InformePago.objects.filter(
            informe=self.object,
            usuario=self.request.user,
        ).order_by('-creado_en').first()
        ctx['precio'] = self.object.precio
        ctx['moneda'] = 'EUR'
        ctx['debug'] = settings.DEBUG
        ctx['stripe_url'] = reverse('nexus:checkout_informe_stripe', kwargs={'pk': self.object.pk})
        ctx['paypal_url'] = reverse('nexus:checkout_informe_paypal', kwargs={'pk': self.object.pk})
        ctx['ultimo_pago'] = pago
        ctx['estado_pago_label'] = 'Pendiente de pago' if pago and pago.estado != InformePago.ESTADO_PAGADO else ('Pagado' if pago else 'Pendiente de pago')
        ctx['estado_pago_clase'] = 'warning' if pago and pago.estado != InformePago.ESTADO_PAGADO else ('success' if pago else 'warning')
        return ctx


class StripeCheckoutView(LoginRequiredMixin, View):
    def post(self, request, pk):
        informe = get_object_or_404(InformeTest, pk=pk, sesion__alumno=request.user)
        if getattr(request.user, 'role', '') == 'premium' or _usuario_tiene_acceso_informe(request.user, informe):
            return redirect('nexus:ver_informe', pk=informe.pk)

        stripe.api_key = settings.STRIPE_SECRET_KEY
        if not stripe.api_key:
            messages.error(request, 'Stripe no está configurado. Añade STRIPE_SECRET_KEY para activar el pago real.')
            return redirect('nexus:checkout_informe', pk=informe.pk)

        precio = informe.precio
        try:
            session = stripe.checkout.Session.create(
                mode='payment',
                line_items=[{
                    'price_data': {
                        'currency': 'eur',
                        'product_data': {'name': f'Informe Nexus - {informe.get_tipo_display()}'},
                        'unit_amount': int((precio * 100).quantize(Decimal('1'))),
                    },
                    'quantity': 1,
                }],
                success_url=request.build_absolute_uri(reverse('nexus:checkout_informe_stripe_success', kwargs={'pk': informe.pk})),
                cancel_url=request.build_absolute_uri(reverse('nexus:checkout_informe_cancel', kwargs={'pk': informe.pk})),
                customer_email=request.user.email or None,
                metadata={'informe_id': str(informe.pk), 'usuario_id': str(request.user.pk)},
            )
            pago = InformePago.objects.create(
                informe=informe,
                usuario=request.user,
                metodo=InformePago.METODO_STRIPE,
                estado=InformePago.ESTADO_PENDIENTE,
                monto=precio,
                moneda='EUR',
                external_id=session.id,
                provider_response={'checkout_session_id': session.id},
            )
            return redirect(session.url)
        except Exception as exc:
            messages.error(request, f'No se pudo iniciar el pago con Stripe: {exc}')
            return redirect('nexus:checkout_informe', pk=informe.pk)


class StripeSuccessView(LoginRequiredMixin, View):
    def get(self, request, pk):
        informe = get_object_or_404(InformeTest, pk=pk, sesion__alumno=request.user)
        pago = InformePago.objects.filter(informe=informe, usuario=request.user, metodo=InformePago.METODO_STRIPE).order_by('-creado_en').first()
        if pago:
            pago.estado = InformePago.ESTADO_PAGADO
            pago.pagado_en = timezone.now()
            pago.save(update_fields=['estado', 'pagado_en'])
        messages.success(request, 'Pago confirmado con Stripe. El informe ya está desbloqueado.')
        return redirect('nexus:ver_informe', pk=informe.pk)


class PayPalCheckoutView(LoginRequiredMixin, View):
    def _access_token(self):
        client_id = settings.PAYPAL_CLIENT_ID
        secret = settings.PAYPAL_CLIENT_SECRET
        if not client_id or not secret:
            raise ValueError('PayPal no está configurado.')
        response = requests.post(
            f'{settings.PAYPAL_BASE_URL}/v1/oauth2/token',
            auth=(client_id, secret),
            data={'grant_type': 'client_credentials'},
            headers={'Accept': 'application/json'},
        )
        response.raise_for_status()
        return response.json()['access_token']

    def post(self, request, pk):
        informe = get_object_or_404(InformeTest, pk=pk, sesion__alumno=request.user)
        if getattr(request.user, 'role', '') == 'premium' or _usuario_tiene_acceso_informe(request.user, informe):
            return redirect('nexus:ver_informe', pk=informe.pk)

        precio = informe.precio
        try:
            access_token = self._access_token()
            response = requests.post(
                f'{settings.PAYPAL_BASE_URL}/v2/checkout/orders',
                headers={
                    'Authorization': f'Bearer {access_token}',
                    'Content-Type': 'application/json',
                },
                data=json.dumps({
                    'intent': 'CAPTURE',
                    'purchase_units': [{
                        'amount': {'currency_code': 'EUR', 'value': str(precio)},
                        'description': f'Informe Nexus - {informe.get_tipo_display()}',
                    }],
                    'application_context': {
                        'brand_name': 'Nexus AI Coach',
                        'landing_page': 'LOGIN',
                        'user_action': 'PAY_NOW',
                        'return_url': request.build_absolute_uri(reverse('nexus:checkout_informe_paypal_success', kwargs={'pk': informe.pk})),
                        'cancel_url': request.build_absolute_uri(reverse('nexus:checkout_informe_cancel', kwargs={'pk': informe.pk})),
                    }
                })
            )
            response.raise_for_status()
            payload = response.json()
            approve_link = next((link['href'] for link in payload.get('links', []) if link.get('rel') == 'approve'), None)
            if not approve_link:
                raise ValueError('PayPal no devolvió un enlace de aprobación.')

            pago = InformePago.objects.create(
                informe=informe,
                usuario=request.user,
                metodo=InformePago.METODO_PAYPAL,
                estado=InformePago.ESTADO_PENDIENTE,
                monto=precio,
                moneda='EUR',
                external_id=payload.get('id', ''),
                provider_response=payload,
            )
            return redirect(approve_link)
        except Exception as exc:
            messages.error(request, f'No se pudo iniciar el pago con PayPal: {exc}')
            return redirect('nexus:checkout_informe', pk=informe.pk)


class PayPalSuccessView(LoginRequiredMixin, View):
    def get(self, request, pk):
        informe = get_object_or_404(InformeTest, pk=pk, sesion__alumno=request.user)
        pago = InformePago.objects.filter(informe=informe, usuario=request.user, metodo=InformePago.METODO_PAYPAL).order_by('-creado_en').first()
        if pago:
            pago.estado = InformePago.ESTADO_PAGADO
            pago.pagado_en = timezone.now()
            pago.save(update_fields=['estado', 'pagado_en'])
        messages.success(request, 'Pago confirmado con PayPal. El informe ya está desbloqueado.')
        return redirect('nexus:ver_informe', pk=informe.pk)


class InformeCheckoutCancelView(LoginRequiredMixin, View):
    def get(self, request, pk):
        informe = get_object_or_404(InformeTest, pk=pk, sesion__alumno=request.user)
        messages.warning(request, 'El pago se canceló. Puedes volver a intentarlo cuando quieras.')
        return redirect('nexus:checkout_informe', pk=informe.pk)


class DevSkipInformeCheckoutView(LoginRequiredMixin, View):
    """Botón temporal de desarrollo para saltarse el pago y seguir revisando el informe."""
    def post(self, request, pk):
        if not settings.DEBUG:
            messages.error(request, 'Esta opción solo está disponible en desarrollo.')
            return redirect('nexus:checkout_informe', pk=pk)

        informe = get_object_or_404(InformeTest, pk=pk, sesion__alumno=request.user)
        if getattr(request.user, 'role', '') == 'premium' or _usuario_tiene_acceso_informe(request.user, informe):
            return redirect('nexus:ver_informe', pk=informe.pk)

        InformePago.objects.create(
            informe=informe,
            usuario=request.user,
            metodo='dev_skip',
            estado=InformePago.ESTADO_PAGADO,
            monto=informe.precio,
            moneda='EUR',
            external_id='dev_skip',
            provider_response={'dev_skip': True},
            pagado_en=timezone.now(),
        )
        messages.warning(request, 'Modo desarrollo: has saltado el pago y el informe ya está desbloqueado.')
        return redirect('nexus:ver_informe', pk=informe.pk)


class BorrarInformeView(LoginRequiredMixin, View):
    def post(self, request, pk):
        informe = get_object_or_404(InformeTest, pk=pk, sesion__alumno=request.user)
        sesion_pk = informe.sesion.pk
        nombre = informe.get_tipo_display()
        informe.delete()
        messages.success(request, f'Informe "{nombre}" eliminado correctamente.')
        return redirect('nexus:student_results', pk=sesion_pk)


class VerInformeView(LoginRequiredMixin, DetailView):
    """Muestra un informe guardado."""
    model = InformeTest
    template_name = 'nexus/student/ver_informe.html'
    context_object_name = 'informe'

    def get_queryset(self):
        return InformeTest.objects.filter(sesion__alumno=self.request.user).select_related('sesion__test')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pago = InformePago.objects.filter(
            informe=self.object,
            usuario=self.request.user,
        ).order_by('-creado_en').first()
        ctx['ultimo_pago'] = pago
        ctx['estado_pago_label'] = 'Pagado' if pago and pago.esta_pagado else 'Pendiente de pago'
        ctx['estado_pago_clase'] = 'success' if pago and pago.esta_pagado else 'warning'
        return ctx

    def dispatch(self, request, *args, **kwargs):
        informe = self.get_object()
        if not _usuario_tiene_acceso_informe(request.user, informe):
            ultimo_pago = InformePago.objects.filter(
                informe=informe,
                usuario=request.user,
            ).order_by('-creado_en').first()
            if ultimo_pago and ultimo_pago.esta_pagado:
                messages.info(request, 'Este informe ya está pagado, pero aun así puedes revisarlo en el proceso de desbloqueo.')
            else:
                messages.warning(request, 'Este informe está pendiente de pago. Completa la compra para ver el análisis completo.')
            return redirect('nexus:checkout_informe', pk=informe.pk)
        return super().dispatch(request, *args, **kwargs)
