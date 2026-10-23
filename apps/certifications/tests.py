from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.base import ContentFile
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import UserCredentialForm
from .models import (
    CredentialAuditEvent,
    CredentialEvidence,
    CredentialReviewRequest,
    CredentialType,
    CredentialVerification,
    ReviewStatus,
    UserCredential,
)
from .services import (
    add_evidence,
    claim_review,
    decide_review,
    request_verification,
    save_user_credential,
)


class CredentialWorkflowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username='credential_owner',
            password='test-password-1',
        )
        self.other = user_model.objects.create_user(
            username='credential_other',
            password='test-password-2',
        )
        self.reviewer = user_model.objects.create_user(
            username='credential_reviewer',
            password='test-password-3',
        )
        permission = Permission.objects.get(
            content_type__app_label='certifications',
            codename='review_credential',
        )
        self.reviewer.user_permissions.add(permission)

    def _form(self, **overrides):
        data = {
            'definition': '',
            'name': 'Python Fundamentals',
            'issuer_name': 'Example Learning Institute',
            'credential_type': CredentialType.INDEPENDENT,
            'area': 'Desarrollo',
            'external_id': '',
            'awarded_on': '',
            'expires_on': '',
            'verification_url': '',
            'visibility': 'private',
            'publish_external_id': '',
            'publish_verification_url': '',
        }
        data.update(overrides)
        form = UserCredentialForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def _create_credential(self, **overrides):
        return save_user_credential(self.owner, self._form(**overrides))

    def _grant_reviewer_permission(self, user):
        permission = Permission.objects.get(
            content_type__app_label='certifications',
            codename='review_credential',
        )
        user.user_permissions.add(permission)

    def test_can_register_without_url_or_expiry_and_default_is_private(self):
        credential = self._create_credential()

        self.assertEqual(credential.review_status, ReviewStatus.DECLARED)
        self.assertEqual(credential.visibility, 'private')
        self.assertEqual(credential.validity_status, 'no_expiry')
        self.assertFalse(credential.is_verified_for_current_revision)
        revision = credential.revisions.get(version=1)
        self.assertEqual(revision.snapshot['name'], 'Python Fundamentals')

    def test_owner_scoped_detail_and_public_visibility(self):
        credential = self._create_credential(
            visibility='public',
            external_id='PUBLIC-123',
            verification_url='https://verify.example.org/123',
            publish_external_id='on',
            publish_verification_url='on',
        )
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(reverse('certifications:credential_detail', args=[credential.pk])).status_code,
            404,
        )

        response = self.client.get(reverse('certifications:credential_public', args=[credential.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'PUBLIC-123')
        self.assertContains(response, 'https://verify.example.org/123')
        self.assertContains(response, 'no es una verificación oficial')

        credential.visibility = 'private'
        credential.save(update_fields=['visibility'])
        self.assertEqual(
            self.client.get(reverse('certifications:credential_public', args=[credential.pk])).status_code,
            404,
        )

    def test_editing_relevant_data_invalidates_prior_decision_and_request(self):
        credential = self._create_credential(
            verification_url='https://verify.example.org/123',
        )
        review = request_verification(self.owner, credential)
        self._grant_reviewer_permission(self.reviewer)
        claim_review(self.reviewer, review)
        decide_review(
            self.reviewer,
            review,
            method=CredentialVerification.Method.ISSUER_CONFIRMATION,
            result=CredentialVerification.Result.VERIFIED,
            source='Confirmación en el portal oficial del emisor, referencia REF-123',
            reason='Nombre e identificador coinciden con la respuesta documentada del emisor.',
        )
        credential.refresh_from_db()
        self.assertTrue(credential.is_verified_for_current_revision)

        updated = save_user_credential(
            self.owner,
            self._form(name='Python Advanced'),
            credential,
        )

        self.assertEqual(updated.revision, 2)
        self.assertEqual(updated.review_status, ReviewStatus.DECLARED)
        self.assertFalse(updated.is_verified_for_current_revision)
        self.assertEqual(
            CredentialReviewRequest.objects.get(pk=review.pk).status,
            CredentialReviewRequest.Status.COMPLETED,
        )
        self.assertEqual(updated.revisions.count(), 2)

    def test_review_permissions_are_separate_from_plan_and_owner_cannot_review(self):
        credential = self._create_credential(
            verification_url='https://verify.example.org/123',
        )
        review = request_verification(self.owner, credential)

        with self.assertRaises(PermissionDenied):
            claim_review(self.other, review)
        with self.assertRaises(PermissionDenied):
            claim_review(self.owner, review)

        claim_review(self.reviewer, review)
        verification = decide_review(
            self.reviewer,
            review,
            method=CredentialVerification.Method.ISSUER_CONFIRMATION,
            result=CredentialVerification.Result.REJECTED,
            source='Portal del emisor consultado; el identificador no figura.',
            reason='No se encontró coincidencia en la fuente consultada.',
        )
        credential.refresh_from_db()

        self.assertEqual(credential.review_status, ReviewStatus.REJECTED)
        self.assertEqual(verification.scope, 'Confirmación documentada del emisor.')
        self.assertTrue(CredentialAuditEvent.objects.filter(
            credential=credential,
            action=CredentialAuditEvent.Action.REVIEW_DECIDED,
        ).exists())

    def test_review_queue_is_not_available_to_unprivileged_users(self):
        self.client.force_login(self.other)
        response = self.client.get(reverse('certifications:review_queue'))
        self.assertEqual(response.status_code, 403)

    def test_repeated_review_request_is_idempotent_and_version_bound(self):
        credential = self._create_credential(
            verification_url='https://verify.example.org/123',
        )

        first = request_verification(self.owner, credential)
        second = request_verification(self.owner, credential)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(credential.review_requests.count(), 1)
        self.assertEqual(first.version, credential.revision)

    def test_expiry_state_is_independent_from_review(self):
        future = self._create_credential(
            name='Future credential',
            awarded_on=(timezone.localdate() + timedelta(days=1)).isoformat(),
            expires_on=(timezone.localdate() + timedelta(days=5)).isoformat(),
        )
        expired = self._create_credential(
            name='Expired credential',
            awarded_on=(timezone.localdate() - timedelta(days=30)).isoformat(),
            expires_on=(timezone.localdate() - timedelta(days=1)).isoformat(),
        )
        self.assertEqual(future.validity_status, 'current')
        self.assertEqual(expired.validity_status, 'expired')
        self.assertEqual(expired.review_status, ReviewStatus.DECLARED)
        self.assertEqual(future.review_status, ReviewStatus.DECLARED)

    def test_https_urls_reject_local_targets(self):
        data = {
            'definition': '',
            'name': 'Python Fundamentals',
            'issuer_name': 'Example Learning Institute',
            'credential_type': CredentialType.INDEPENDENT,
            'verification_url': 'https://127.0.0.1/private',
            'visibility': 'private',
        }
        form = UserCredentialForm(data)
        self.assertFalse(form.is_valid())

    def test_manual_evidence_decision_requires_evidence_for_current_version(self):
        credential = self._create_credential(
            verification_url='https://verify.example.org/123',
        )
        review = request_verification(self.owner, credential)
        claim_review(self.reviewer, review)

        with self.assertRaisesMessage(
            ValidationError,
            'La revisión manual de evidencia requiere un archivo de esta versión.',
        ):
            decide_review(
                self.reviewer,
                review,
                method=CredentialVerification.Method.MANUAL_EVIDENCE,
                result=CredentialVerification.Result.VERIFIED,
                source='',
                reason='Se revisó el documento.',
            )


@override_settings(
    CERTIFICATIONS_EVIDENCE_MAX_BYTES=2048,
    CERTIFICATIONS_ALLOWED_EVIDENCE_MIME_TYPES=['application/pdf'],
)
class CredentialEvidenceTests(CredentialWorkflowTests):
    def tearDown(self):
        for evidence in CredentialEvidence.objects.all():
            if evidence.file:
                evidence.file.delete(save=False)
        super().tearDown()

    def test_uploaded_pdf_is_private_and_download_requires_owner_or_assigned_reviewer(self):
        credential = self._create_credential()
        uploaded = SimpleUploadedFile(
            'evidence.pdf',
            b'%PDF-1.7\nexample evidence',
            content_type='application/pdf',
        )
        evidence = add_evidence(
            self.owner,
            credential,
            uploaded,
            CredentialEvidence.EvidenceType.CERTIFICATE,
        )
        review = request_verification(self.owner, credential)
        self._grant_reviewer_permission(self.reviewer)
        claim_review(self.reviewer, review)

        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(reverse('certifications:evidence_download', args=[evidence.pk])).status_code,
            403,
        )
        self.client.force_login(self.reviewer)
        response = self.client.get(reverse('certifications:evidence_download', args=[evidence.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(b''.join(response.streaming_content), b'%PDF-1.7\nexample evidence')
        response.close()
        with self.assertRaises(ValueError):
            _ = evidence.file.url

    def test_invalid_file_content_is_rejected(self):
        credential = self._create_credential()
        uploaded = SimpleUploadedFile(
            'evidence.pdf',
            b'not a pdf',
            content_type='application/pdf',
        )

        with self.assertRaises(ValidationError):
            add_evidence(
                self.owner,
                credential,
                uploaded,
                CredentialEvidence.EvidenceType.CERTIFICATE,
            )


class LmsCourseCertificateIntegrationTests(TestCase):
    def setUp(self):
        from apps.courses.models import Curso
        from apps.lms.models import CourseCertificate

        user_model = get_user_model()
        self.owner = user_model.objects.create_user('lms_cert_owner', password='test-password')
        self.other = user_model.objects.create_user('lms_cert_other', password='test-password')
        self.course = Curso.objects.create(
            nombre='Curso certificado',
            descripcion='Curso de prueba.',
            fecha_inicio=date(2025, 1, 1),
        )
        self.certificate = CourseCertificate.objects.create(
            user=self.owner,
            course=self.course,
        )

    def tearDown(self):
        if self.certificate.pdf_file:
            self.certificate.pdf_file.delete(save=False)

    def test_lms_emission_appears_in_list_profile_and_dashboard(self):
        from apps.users.dashboard_services import _credential_data

        self.client.force_login(self.owner)
        listing = self.client.get(reverse('certifications:list'))
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, 'Curso certificado')
        self.assertEqual(listing.context['summary']['issued_internal'], 1)

        profile = self.client.get(reverse('users:perfil', args=[self.owner.username]))
        self.assertEqual(profile.status_code, 200)
        self.assertContains(profile, 'Curso certificado')
        self.assertEqual(profile.context['total_certificados'], 1)

        data, has_data = _credential_data(self.owner)
        self.assertTrue(has_data)
        self.assertEqual(data['internal'], 1)
        self.assertEqual(data['total'], 1)

    def test_lms_validation_route_matches_original_emission_contract(self):
        response = self.client.get(
            reverse('certifications:lms_validate', args=[self.certificate.certificate_code])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Certificado emitido')
        self.assertContains(response, self.certificate.certificate_code)
        self.assertEqual(
            self.certificate.get_validation_url().split('://', 1)[-1].split('/', 1)[-1],
            reverse('certifications:lms_validate', args=[self.certificate.certificate_code]).lstrip('/'),
        )

    def test_lms_pdf_download_is_owner_scoped(self):
        self.certificate.pdf_file.save(
            'certificate.pdf',
            ContentFile(b'%PDF-1.7\ncertificate'),
            save=True,
        )
        self.client.force_login(self.other)
        denied = self.client.get(
            reverse('certifications:lms_download', args=[self.certificate.pk])
        )
        self.assertEqual(denied.status_code, 404)

        self.client.force_login(self.owner)
        response = self.client.get(
            reverse('certifications:lms_download', args=[self.certificate.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(b''.join(response.streaming_content), b'%PDF-1.7\ncertificate')
        response.close()

    def test_course_certificate_api_only_returns_current_user_records(self):
        from apps.lms.models import CourseCertificate

        CourseCertificate.objects.create(user=self.other, course=self.course)
        self.client.force_login(self.owner)
        response = self.client.get('/api/v1/my-course-certificates/')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        results = payload['results'] if isinstance(payload, dict) else payload
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], self.certificate.pk)

    def test_declared_credential_api_only_returns_current_user_records(self):
        from apps.certifications.services import save_user_credential

        credentials = []
        for owner, name in ((self.owner, 'Python'), (self.other, 'Java')):
            form = UserCredentialForm({
                'definition': '',
                'name': name,
                'issuer_name': 'Academia',
                'credential_type': CredentialType.INDEPENDENT,
                'visibility': 'private',
            })
            self.assertTrue(form.is_valid(), form.errors)
            credentials.append(save_user_credential(owner, form))

        self.client.force_login(self.owner)
        response = self.client.get('/api/v1/my-credentials/')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        results = payload['results'] if isinstance(payload, dict) else payload
        self.assertEqual([row['id'] for row in results], [credentials[0].pk])
        self.assertNotIn(credentials[1].pk, [row['id'] for row in results])
