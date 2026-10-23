"""Views for the certifications app."""
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.template.response import TemplateResponse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from .forms import CredentialReviewDecisionForm, CredentialRevocationForm, UserCredentialForm
from .models import (
    Certificado,
    CredentialAuditEvent,
    CredentialDefinition,
    CredentialEvidence,
    CredentialReviewRequest,
    CredentialType,
    CredentialVerification,
    ReviewStatus,
    UserCredential,
)
from .services import (
    _can_review,
    archive_credential,
    claim_review,
    decide_review,
    may_download_evidence,
    request_verification,
    record_revocation,
    save_user_credential,
)
from .serializers import (
    CertificadoSerializer,
    LmsCourseCertificateSerializer,
    UserCredentialSerializer,
)
from .utils import generar_y_guardar_certificado


def _ensure_certificado_pdf(cert):
    cert.ensure_pdf_exists()


# ---------------------------------------------------------------------------
# HTML Views
# ---------------------------------------------------------------------------

class CertificadoListView(LoginRequiredMixin, TemplateView):
    template_name = 'certifications/list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.lms.models import CourseCertificate

        today = timezone.localdate()
        credentials = UserCredential.objects.filter(
            user=self.request.user,
            archived_at__isnull=True,
        ).select_related('definition', 'definition__issuer')
        credential_type = self.request.GET.get('type', '')
        issuer = self.request.GET.get('issuer', '').strip()
        review = self.request.GET.get('review', '')
        validity = self.request.GET.get('validity', '')
        area = self.request.GET.get('area', '').strip()
        if credential_type in CredentialType.values and credential_type != CredentialType.INTERNAL:
            credentials = credentials.filter(credential_type=credential_type)
        if issuer:
            credentials = credentials.filter(issuer_name__icontains=issuer)
        if review in ReviewStatus.values:
            credentials = credentials.filter(review_status=review)
        if area:
            credentials = credentials.filter(area__icontains=area)
        if validity == 'no_expiry':
            credentials = credentials.filter(expires_on__isnull=True, revoked_on__isnull=True)
        elif validity == 'expired':
            credentials = credentials.filter(expires_on__lt=today)
        elif validity == 'current':
            credentials = credentials.filter(expires_on__gte=today, revoked_on__isnull=True)
        lms_certificates = CourseCertificate.objects.filter(
            user=self.request.user,
        ).select_related('course')
        internal_count = Certificado.objects.filter(usuario=self.request.user).count()
        lms_count = lms_certificates.count()

        warning_days = max(0, getattr(settings, 'CERTIFICATIONS_EXPIRY_WARNING_DAYS', 0))
        upcoming = UserCredential.objects.filter(
            user=self.request.user,
            archived_at__isnull=True,
            revoked_on__isnull=True,
            expires_on__gte=today,
            expires_on__lte=today + timedelta(days=warning_days),
        ) if warning_days else UserCredential.objects.none()
        context.update({
            'credentials': credentials,
            'certificados': Certificado.objects.filter(
                usuario=self.request.user
            ).select_related('curso'),
            'lms_certificates': lms_certificates,
            'upcoming_expiry': upcoming,
            'expiry_warning_days': warning_days,
            'credential_types': [
                choice for choice in CredentialType.choices
                if choice[0] != CredentialType.INTERNAL
            ],
            'review_states': ReviewStatus.choices,
            'selected_type': credential_type,
            'selected_issuer': issuer,
            'selected_review': review,
            'selected_validity': validity,
            'selected_area': area,
            'summary': {
                'total': UserCredential.objects.filter(user=self.request.user, archived_at__isnull=True).count(),
                'verified': UserCredential.objects.filter(
                    user=self.request.user,
                    archived_at__isnull=True,
                    review_status=ReviewStatus.VERIFIED,
                ).count(),
                'pending': UserCredential.objects.filter(
                    user=self.request.user,
                    archived_at__isnull=True,
                    review_status__in=[ReviewStatus.PENDING, ReviewStatus.IN_REVIEW],
                ).count(),
                'issued_internal': internal_count + lms_count,
            },
            'evidence_upload_enabled': bool(
                getattr(settings, 'CERTIFICATIONS_EVIDENCE_MAX_BYTES', 0)
                and getattr(settings, 'CERTIFICATIONS_ALLOWED_EVIDENCE_MIME_TYPES', [])
            ),
            'is_reviewer': _can_review(self.request.user),
            'badges': self.request.user.insignias_obtenidas.filter(
                status='valid',
                insignia__visible=True,
            ).select_related('insignia')[:8],
        })
        return context


class UserCredentialFormView(LoginRequiredMixin, View):
    template_name = 'certifications/credential_form.html'
    credential = None

    def get_credential(self):
        if self.credential is None and self.kwargs.get('pk'):
            self.credential = get_object_or_404(
                UserCredential,
                pk=self.kwargs['pk'],
                user=self.request.user,
                origin=UserCredential.Origin.MANUAL,
                archived_at__isnull=True,
            )
        return self.credential

    def get(self, request, *args, **kwargs):
        credential = self.get_credential()
        form = UserCredentialForm(instance=credential)
        return render(request, self.template_name, {
            'form': form,
            'credential': credential,
            'evidence_upload_enabled': form.evidence_upload_enabled,
        })

    def post(self, request, *args, **kwargs):
        credential = self.get_credential()
        form = UserCredentialForm(request.POST, request.FILES, instance=credential)
        if form.is_valid():
            try:
                saved = save_user_credential(request.user, form, credential)
            except ValidationError as exc:
                form.add_error(None, exc.messages[0])
            else:
                messages.success(request, 'La credencial se guardó como declaración personal.')
                return redirect('certifications:credential_detail', pk=saved.pk)
        return render(request, self.template_name, {
            'form': form,
            'credential': credential,
            'evidence_upload_enabled': form.evidence_upload_enabled,
        }, status=400)


class UserCredentialDetailView(LoginRequiredMixin, TemplateView):
    template_name = 'certifications/credential_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        credential = get_object_or_404(
            UserCredential.objects.select_related('definition', 'definition__issuer'),
            pk=self.kwargs['pk'],
            user=self.request.user,
            archived_at__isnull=True,
        )
        context.update({
            'credential': credential,
            'evidences': credential.evidences.filter(uploaded_by=self.request.user),
            'revisions': credential.revisions.all(),
            'verifications': credential.verifications.select_related('reviewer'),
            'review_requests': credential.review_requests.all(),
            'can_request_review': (
                credential.origin == UserCredential.Origin.MANUAL
                and credential.review_status in {ReviewStatus.DECLARED, ReviewStatus.REJECTED}
            ),
        })
        return context


class RequestCredentialReviewView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request, pk):
        credential = get_object_or_404(UserCredential, pk=pk, user=request.user)
        try:
            review = request_verification(request.user, credential)
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        else:
            messages.success(request, f'Solicitud de revisión #{review.pk} registrada.')
        return redirect('certifications:credential_detail', pk=credential.pk)


class ArchiveCredentialView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request, pk):
        credential = get_object_or_404(UserCredential, pk=pk, user=request.user)
        try:
            archive_credential(request.user, credential)
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        else:
            messages.success(request, 'La credencial se archivó; el historial se conserva.')
        return redirect('certifications:list')


class CredentialEvidenceDownloadView(LoginRequiredMixin, View):
    def get(self, request, pk):
        evidence = get_object_or_404(
            CredentialEvidence.objects.select_related('credential'),
            pk=pk,
        )
        if not may_download_evidence(request.user, evidence):
            raise PermissionDenied('No tienes autorización para consultar esta evidencia.')
        response = FileResponse(
            evidence.file.open('rb'),
            as_attachment=True,
            filename=evidence.original_name,
            content_type='application/octet-stream',
        )
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        response['Referrer-Policy'] = 'no-referrer'
        return response


class PublicCredentialView(TemplateView):
    template_name = 'certifications/public_credential.html'

    def get(self, request, *args, **kwargs):
        credential = get_object_or_404(
            UserCredential.objects.select_related('user', 'definition', 'definition__issuer')
            .prefetch_related('verifications'),
            pk=kwargs['pk'],
            visibility='public',
            archived_at__isnull=True,
        )
        response = render(request, self.template_name, {'credential': credential})
        response['Cache-Control'] = 'no-store'
        return response


class CredentialReviewQueueView(LoginRequiredMixin, TemplateView):
    template_name = 'certifications/review_queue.html'

    def dispatch(self, request, *args, **kwargs):
        if not _can_review(request.user):
            raise PermissionDenied('No tienes permiso para revisar credenciales.')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['reviews'] = CredentialReviewRequest.objects.filter(
            status__in=[
                CredentialReviewRequest.Status.PENDING,
                CredentialReviewRequest.Status.IN_REVIEW,
            ],
        ).select_related('credential', 'credential__user', 'assigned_reviewer')
        return context


class ClaimCredentialReviewView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request, pk):
        review = get_object_or_404(CredentialReviewRequest, pk=pk)
        try:
            review = claim_review(request.user, review)
        except (PermissionDenied, ValidationError) as exc:
            detail = exc.messages[0] if isinstance(exc, ValidationError) else str(exc)
            messages.error(request, detail)
            return redirect('certifications:review_queue')
        if review.status == CredentialReviewRequest.Status.CANCELLED:
            messages.error(request, 'La solicitud era de una versión anterior y quedó cancelada.')
            return redirect('certifications:review_queue')
        return redirect('certifications:review_detail', pk=review.pk)


class CredentialReviewDetailView(LoginRequiredMixin, View):
    template_name = 'certifications/review_detail.html'

    def dispatch(self, request, *args, **kwargs):
        if not _can_review(request.user):
            raise PermissionDenied('No tienes permiso para revisar credenciales.')
        return super().dispatch(request, *args, **kwargs)

    def get_review(self):
        review = get_object_or_404(
            CredentialReviewRequest.objects.select_related('credential', 'credential__user'),
            pk=self.kwargs['pk'],
        )
        if review.assigned_reviewer_id != self.request.user.pk:
            raise PermissionDenied('La solicitud debe estar asignada a tu cuenta.')
        return review

    def get(self, request, *args, **kwargs):
        review = self.get_review()
        return render(request, self.template_name, {
            'review': review,
            'evidences': review.credential.evidences.filter(version=review.version),
            'form': CredentialReviewDecisionForm(),
            'revocation_form': CredentialRevocationForm(),
        })

    def post(self, request, *args, **kwargs):
        review = self.get_review()
        form = CredentialReviewDecisionForm(request.POST)
        if form.is_valid():
            try:
                decide_review(
                    request.user,
                    review,
                    method=form.cleaned_data['method'],
                    result=form.cleaned_data['result'],
                    source=form.cleaned_data['source'],
                    reason=form.cleaned_data['reason'],
                )
            except ValidationError as exc:
                form.add_error(None, exc.messages[0])
            else:
                messages.success(request, 'La decisión quedó registrada en el historial.')
                return redirect('certifications:review_queue')
        return render(request, self.template_name, {
            'review': review,
            'evidences': review.credential.evidences.filter(version=review.version),
            'form': form,
            'revocation_form': CredentialRevocationForm(),
        }, status=400)


class CredentialRevocationView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request, pk):
        credential = get_object_or_404(UserCredential, pk=pk)
        if not _can_review(request.user):
            raise PermissionDenied('No tienes permiso para registrar una revocación.')
        form = CredentialRevocationForm(request.POST)
        if form.is_valid():
            try:
                record_revocation(
                    request.user,
                    credential,
                    revoked_on=form.cleaned_data['revoked_on'],
                    source=form.cleaned_data['source'],
                    reason=form.cleaned_data['reason'],
                )
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
            else:
                messages.success(request, 'La revocación confirmada quedó registrada.')
        else:
            messages.error(request, 'Indica la fecha, fuente y motivo de revocación.')
        return redirect('certifications:review_queue')


class DescargarCertificadoView(LoginRequiredMixin, View):
    def get(self, request, pk):
        cert = get_object_or_404(Certificado, pk=pk, usuario=request.user)
        _ensure_certificado_pdf(cert)
        return FileResponse(
            cert.pdf.open('rb'),
            as_attachment=True,
            filename=f'certificado_{cert.curso.nombre.replace(" ", "_")}.pdf',
        )


class DescargarCertificadoLmsView(LoginRequiredMixin, View):
    def get(self, request, pk):
        from apps.lms.models import CourseCertificate

        cert = get_object_or_404(
            CourseCertificate.objects.select_related('course'),
            pk=pk,
            user=request.user,
        )
        if cert.metadata.get('campus'):
            if not cert.is_valid:
                raise PermissionDenied
            from apps.lms.campus import certificate_pdf
            response = FileResponse(
                certificate_pdf(cert), as_attachment=True,
                filename=f'certificado_curso_{cert.pk}.pdf', content_type='application/pdf',
            )
            response['Cache-Control'] = 'private, no-store'
            response['X-Content-Type-Options'] = 'nosniff'
            return response
        if not cert.pdf_file:
            return HttpResponse('Este certificado no tiene un PDF disponible.', status=404)
        response = FileResponse(
            cert.pdf_file.open('rb'),
            as_attachment=True,
            filename=f'certificado_curso_{cert.pk}.pdf',
        )
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class ValidarCertificadoView(View):
    """Public endpoint — no login required."""
    def get(self, request, codigo):
        cert = Certificado.objects.filter(codigo_unico=codigo).select_related('usuario', 'curso').first()
        if cert is not None:
            _ensure_certificado_pdf(cert)
        return TemplateResponse(request, 'certifications/validar.html', {'certificado': cert})


class ValidarCertificadoLmsView(TemplateView):
    template_name = 'certifications/validar_lms.html'

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        response['Cache-Control'] = 'no-store'
        if response.context_data['certificate'] is None:
            response.status_code = 404
        return response

    def get_context_data(self, **kwargs):
        from apps.lms.models import CourseCertificate

        context = super().get_context_data(**kwargs)
        context['certificate'] = CourseCertificate.objects.filter(
            certificate_code=self.kwargs['codigo'],
        ).select_related('user', 'course').first()
        return context


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


class UserCredentialViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = UserCredentialSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return UserCredential.objects.filter(
            user=self.request.user,
            archived_at__isnull=True,
        ).prefetch_related('verifications')


class LmsCourseCertificateViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = LmsCourseCertificateSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        from apps.lms.models import CourseCertificate

        return CourseCertificate.objects.filter(
            user=self.request.user,
        ).select_related('course')
