from django.views.generic import ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, render
from apps.jobs.models import JobOffer, Application
 # from apps.softskills.models import TestResult
from apps.jobs.services import calcular_match_candidato_oferta
from django.db.models import Prefetch

class RecruiterDashboardView(LoginRequiredMixin, ListView):
    model = JobOffer
    template_name = 'jobs/recruiter_dashboard.html'
    context_object_name = 'offers'

    def get_queryset(self):
        return JobOffer.objects.filter(recruiter=self.request.user).prefetch_related(
            Prefetch('applications', queryset=Application.objects.select_related('user')),
            'assigned_tests', 'skills'
        )

class OfferCandidatesView(LoginRequiredMixin, DetailView):
    model = JobOffer
    template_name = 'jobs/offer_candidates.html'
    context_object_name = 'offer'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        offer = self.object
        # Prefetch applications and related users
        applications = offer.applications.select_related('user').all()
        # Prefetch test results for each candidate
        user_ids = [app.user_id for app in applications]
        test_results = TestResult.objects.filter(user_id__in=user_ids, test__in=offer.assigned_tests.values('test'))
        # Map user_id to test results
        results_map = {}
        for tr in test_results:
            results_map.setdefault(tr.user_id, []).append(tr)
        # Calculate match (dummy experiencia)
        candidates = []
        for app in applications:
            user_skills = set(app.user.skills.values_list('name', flat=True)) if hasattr(app.user, 'skills') else set()
            offer_skills = set(offer.skills.values_list('name', flat=True))
            softskills_result = {}  # Eliminado: dependía de softskills
            if app.user_id in results_map:
                # Usar el primer resultado de test como ejemplo
                tr = results_map[app.user_id][0]
                # Suponiendo que tienes un JSONField o CategoryScore
                if hasattr(tr, 'result_data') and tr.result_data:
                    softskills_result = {}  # Eliminado: dependía de softskills
                else:
                    softskills_result = {}  # Eliminado: dependía de softskills
            experiencia = getattr(app.user, 'experiencia_anos', 0)
            match = calcular_match_candidato_oferta(user_skills, offer_skills, softskills_result, experiencia)
            candidates.append({
                'application': app,
                'test_results': results_map.get(app.user_id, []),
                'match': match,
            })
        # Ordenar por match o por parámetro GET
        order = self.request.GET.get('order', 'match')
        candidates.sort(key=lambda x: x.get(order, 0), reverse=True)
        context['candidates'] = candidates
        context['order'] = order
        return context
