from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.certifications.models import CredentialIssuer
from .campus import issue_certificate
from .models import AuditEvent, CourseCertificate, CourseMembership, Section
from .test_campus_access import CampusFixture


class CampusCertificateTests(CampusFixture):
    def setUp(self):
        super().setUp()
        self.issuer = CredentialIssuer.objects.create(name='Emisor de pruebas aislado', is_active=True)
        self.settings.campus_certificate_included = True
        self.settings.campus_issuer = self.issuer
        self.settings.save()

    def test_full_flow_idempotent_issuance_own_list_and_private_pdf(self):
        membership = self.enroll()
        with self.assertRaises(ValidationError):
            issue_certificate(self.student, self.course, actor=self.student)
        self.complete_lesson()
        certificate = issue_certificate(self.student, self.course, actor=self.student)
        replay = issue_certificate(self.student, self.course, actor=self.student)
        self.assertEqual(certificate.pk, replay.pk)
        self.assertEqual(certificate.metadata['issuer_name'], self.issuer.name)
        self.assertEqual(certificate.metadata['course_version'], 'fixture-v1')
        self.assertEqual(CourseCertificate.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(metadata__campus_action='certificate_issued').count(), 1)
        response = self.client.get(reverse('certifications:list'))
        self.assertContains(response, self.issuer.name)
        url = reverse('certifications:lms_download', args=[certificate.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF'))
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        membership.refresh_from_db()
        self.assertIsNotNone(membership.passed_at)

    def test_no_certificate_when_not_included_and_empty_course_never_passes(self):
        self.settings.campus_certificate_included = False
        self.settings.save()
        self.enroll()
        self.complete_lesson()
        with self.assertRaises(ValidationError):
            issue_certificate(self.student, self.course)
        self.assertEqual(CourseCertificate.objects.count(), 0)

    def test_history_and_certificate_survive_loss_of_material_access(self):
        self.settings.campus_free = False
        self.settings.campus_entitlement_code = 'campus-fixture-course'
        self.settings.save()
        right = self.entitlement()
        membership = self.enroll()
        self.complete_lesson()
        certificate = issue_certificate(self.student, self.course)
        right.revoked_at = timezone.now()
        right.save()
        self.assertEqual(self.client.get(self.page_url).status_code, 403)
        self.assertEqual(self.client.get(reverse('certifications:lms_download', args=[certificate.pk])).status_code, 200)
        certificate.refresh_from_db()
        membership.refresh_from_db()
        self.assertTrue(certificate.is_valid)
        self.assertIsNotNone(membership.passed_at)

    def test_revocation_is_not_undone_by_reissue(self):
        self.enroll()
        self.complete_lesson()
        certificate = issue_certificate(self.student, self.course)
        certificate.revoke(reason='Revisión de prueba')
        with self.assertRaises(ValidationError):
            issue_certificate(self.student, self.course)
        self.assertEqual(self.client.get(reverse('certifications:lms_download', args=[certificate.pk])).status_code, 403)

    def test_administrative_revocation_requires_reason_and_is_audited(self):
        self.enroll()
        self.complete_lesson()
        group = Section.objects.create(course=self.course, title='Grupo no asignado al alumno')
        CourseMembership.objects.filter(course=self.course, user=self.teacher).update(section=group)
        with self.assertRaises(PermissionDenied):
            issue_certificate(self.student, self.course, actor=self.teacher)
        certificate = issue_certificate(self.student, self.course)
        admin = get_user_model().objects.create_superuser('campus_admin', email='campus-admin@example.invalid')
        self.client.force_login(admin)
        url = reverse('admin:lms_coursecertificate_change', args=[certificate.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url, {'status': 'revoked', 'revocation_reason': ''}).status_code, 200)
        certificate.refresh_from_db()
        self.assertTrue(certificate.is_valid)
        self.assertEqual(self.client.post(url, {
            'status': 'revoked', 'revocation_reason': 'Incidencia de pruebas aisladas',
        }).status_code, 302)
        certificate.refresh_from_db()
        self.assertFalse(certificate.is_valid)
        self.assertTrue(AuditEvent.objects.filter(
            actor=admin, metadata__campus_action='certificate_admin_updated',
        ).exists())
