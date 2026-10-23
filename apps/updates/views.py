from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404
from django.views.generic import DetailView

from .models import ProximaActualizacion


class TechDNAUpdateView(LoginRequiredMixin, DetailView):
    model = ProximaActualizacion
    template_name = 'updates/tech_dna.html'
    context_object_name = 'actualizacion'
    reference = 'DPR_001'
    section_title = 'Tech DNA'

    def get_object(self, queryset=None):
        return get_object_or_404(ProximaActualizacion, referencia=self.reference)

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), 'section_title': self.section_title}
