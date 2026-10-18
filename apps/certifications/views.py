"""Views for the certifications app."""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView, View
from django.shortcuts import get_object_or_404
from django.http import FileResponse, HttpResponse
from django.template.response import TemplateResponse

from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Certificado
from .serializers import CertificadoSerializer
from .utils import generar_y_guardar_certificado


def _ensure_certificado_pdf(cert):
    cert.ensure_pdf_exists()


# ---------------------------------------------------------------------------
# HTML Views
# ---------------------------------------------------------------------------

class CertificadoListView(LoginRequiredMixin, ListView):
    model = Certificado
    template_name = 'certifications/list.html'
    context_object_name = 'certificados'

    def get_queryset(self):
        return Certificado.objects.filter(usuario=self.request.user).select_related('curso')


class DescargarCertificadoView(LoginRequiredMixin, View):
    def get(self, request, pk):
        cert = get_object_or_404(Certificado, pk=pk, usuario=request.user)
        _ensure_certificado_pdf(cert)
        return FileResponse(
            cert.pdf.open('rb'),
            as_attachment=True,
            filename=f'certificado_{cert.curso.nombre.replace(" ", "_")}.pdf',
        )


class ValidarCertificadoView(View):
    """Public endpoint — no login required."""
    def get(self, request, codigo):
        cert = Certificado.objects.filter(codigo_unico=codigo).select_related('usuario', 'curso').first()
        if cert is not None:
            _ensure_certificado_pdf(cert)
        return TemplateResponse(request, 'certifications/validar.html', {'certificado': cert})


# ---------------------------------------------------------------------------
# DRF ViewSet
# ---------------------------------------------------------------------------

class CertificadoViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = CertificadoSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Certificado.objects.filter(usuario=self.request.user)

    @action(detail=True, methods=['get'])
    def descargar(self, request, pk=None):
        cert = self.get_object()
        _ensure_certificado_pdf(cert)
        return FileResponse(
            cert.pdf.open('rb'),
            as_attachment=True,
            filename=f'certificado_{cert.codigo_unico}.pdf',
        )
