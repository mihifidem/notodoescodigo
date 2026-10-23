from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from apps.users.models import AccessMembership

from .models import InformeTest, InformePago, SesionTest, TestPlantilla
from . import views


class NexusAICoachReportTests(TestCase):
    def setUp(self):
        self.media_directory = TemporaryDirectory(prefix='nexus-tests-')
        self.addCleanup(self.media_directory.cleanup)
        media_settings = override_settings(MEDIA_ROOT=self.media_directory.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        User = get_user_model()
        self.user = User.objects.create_user(username='alumno', password='testpass123')
        self.test = TestPlantilla.objects.create(
            nombre='TechMind',
            descripcion='Test de ejemplo para IA',
            archivo_html=SimpleUploadedFile('techmind.html', b'<html><body>Test</body></html>', content_type='text/html'),
            activo=True,
        )
        self.sesion = SesionTest.objects.create(
            test=self.test,
            alumno=self.user,
            estado=SesionTest.COMPLETADO,
            completado_en=timezone.now(),
            resultados_json={
                'test_tipo': 'techmind',
                'nexus_score': 82,
                'primary_archetype': 'Future Navigator',
                'secondary_archetype': 'Adaptive Innovator',
                'leadership_score': 74,
                'innovation_score': 88,
                'technology_score': 90,
                'future_score': 83,
                'career_score': 76,
                'executive_score': 72,
                'motivation_score': 79,
                'cognitive_score': 81,
                'emotional_score': 77,
                'reliability': 86,
                'TM01': 74,
                'TM02': 81,
                'TM03': 79,
                'TM04': 72,
                'TM05': 83,
                'TM06': 77,
                'TM07': 88,
                'TM08': 86,
                'TM09': 80,
                'TM10': 75,
            },
        )

    def test_uploaded_fixture_is_isolated_from_portal_media(self):
        uploaded_path = Path(self.test.archivo_html.path)
        self.assertEqual(Path(settings.MEDIA_ROOT), Path(self.media_directory.name))
        self.assertEqual(uploaded_path.parent, Path(self.media_directory.name) / 'nexus' / 'tests')
        self.assertEqual(uploaded_path.read_bytes(), b'<html><body>Test</body></html>')
        self.assertEqual(uploaded_path.name, 'techmind.html')

    def test_catalogo_global_de_precios_de_informes(self):
        self.assertEqual(InformeTest.PRECIOS[InformeTest.TIPO_BASE], Decimal('9.99'))
        self.assertEqual(InformeTest.PRECIOS[InformeTest.TIPO_AI_PERSONAL], Decimal('19.99'))
        self.assertEqual(InformeTest.PRECIOS[InformeTest.TIPO_AI_COMPLETO], Decimal('29.99'))

    def test_tipos_de_informe_tienen_nombres_y_precios_publicos(self):
        self.assertEqual(dict(InformeTest.TIPO_CHOICES)[InformeTest.TIPO_BASE], 'Básico (9,99€)')
        self.assertEqual(dict(InformeTest.TIPO_CHOICES)[InformeTest.TIPO_AI_PERSONAL], 'Advanced (19,99€)')
        self.assertEqual(dict(InformeTest.TIPO_CHOICES)[InformeTest.TIPO_AI_COMPLETO], 'Premium (29,99€)')

    def test_generar_informe_ai_html_includes_summary_sections(self):
        html = views._generar_informe_ai_html(self.sesion)

        self.assertIn('Resumen Ejecutivo', html)
        self.assertIn('Future Navigator', html)
        self.assertIn('Liderazgo', html)
        self.assertIn('Nexus AI Coach', html)

    def test_generar_informe_ai_uses_basic_prompt_for_basic_report(self):
        html_basic = views._generar_informe_ai_html(self.sesion, tipo=InformeTest.TIPO_BASE)
        html_premium = views._generar_informe_ai_html(self.sesion, tipo=InformeTest.TIPO_AI_COMPLETO)

        self.assertIn('Informe básico', html_basic)
        self.assertIn('Informe premium', html_premium)
        self.assertLess(len(html_basic), len(html_premium))

    def test_generar_informe_ai_view_creates_report(self):
        self.client.force_login(self.user)
        url = reverse('nexus:generar_informe_ai', kwargs={'pk': self.sesion.pk})

        response = self.client.post(url, {'tipo': InformeTest.TIPO_AI_PERSONAL}, content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(InformeTest.objects.filter(sesion=self.sesion, tipo=InformeTest.TIPO_AI_PERSONAL).exists())
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertIn('Resumen Ejecutivo', payload['html'])

    def test_generar_informe_ai_html_includes_deeper_sections(self):
        html = views._generar_informe_ai_html(self.sesion)

        self.assertIn('Riesgos y frenos', html)
        self.assertIn('Acciones prioritarias', html)
        self.assertIn('Plan de desarrollo', html)
        self.assertGreater(len(html), 4000)

    def test_non_premium_user_is_redirected_to_checkout_for_report(self):
        self.user.role = 'usuario'
        self.user.save()
        informe = InformeTest.objects.create(
            sesion=self.sesion,
            tipo=InformeTest.TIPO_AI_PERSONAL,
            contenido_html='<h1>Informe de prueba</h1>',
        )

        self.client.force_login(self.user)
        url = reverse('nexus:ver_informe', kwargs={'pk': informe.pk})

        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/checkout/', response.url)

    def test_premium_user_can_view_report_without_paywall(self):
        AccessMembership.objects.filter(user=self.user).update(plan='plus', billing_period='monthly')
        informe = InformeTest.objects.create(
            sesion=self.sesion,
            tipo=InformeTest.TIPO_AI_PERSONAL,
            contenido_html='<h1>Informe premium</h1>',
        )

        self.client.force_login(self.user)
        url = reverse('nexus:ver_informe', kwargs={'pk': informe.pk})

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Informe premium')

    def test_premium_tests_use_plan_not_professional_role(self):
        self.test.nivel_acceso = 'premium'
        for role, _ in self.user.ROLE_CHOICES:
            self.user.role = role
            self.user.save(update_fields=['role'])
            for plan in ('free', 'plus', 'pro'):
                with self.subTest(role=role, plan=plan):
                    AccessMembership.objects.filter(user=self.user).update(
                        plan=plan, status='active', billing_period='monthly',
                    )
                    self.assertEqual(
                        views._usuario_puede_acceder(self.user, self.test),
                        plan in ('plus', 'pro'),
                    )
        AccessMembership.objects.filter(user=self.user).update(status='expired')
        self.assertFalse(views._usuario_puede_acceder(self.user, self.test))

    def test_plus_and_pro_report_access_is_owner_scoped(self):
        informe = InformeTest.objects.create(
            sesion=self.sesion, tipo=InformeTest.TIPO_AI_PERSONAL,
            contenido_html='<h1>Private report</h1>',
        )
        other = get_user_model().objects.create_user('other-report-user')
        AccessMembership.objects.filter(user=other).update(plan='pro', billing_period='monthly')
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse('nexus:ver_informe', args=[informe.pk])).status_code, 404)
        self.client.force_login(self.user)
        for plan in ('plus', 'pro'):
            AccessMembership.objects.filter(user=self.user).update(plan=plan, billing_period='monthly')
            response = self.client.get(reverse('nexus:ver_informe', args=[informe.pk]))
            self.assertContains(response, 'Private report')
        AccessMembership.objects.filter(user=self.user).update(status='expired')
        self.assertEqual(self.client.get(reverse('nexus:ver_informe', args=[informe.pk])).status_code, 302)

    def test_paid_access_unblocks_report(self):
        informe = InformeTest.objects.create(
            sesion=self.sesion,
            tipo=InformeTest.TIPO_AI_PERSONAL,
            contenido_html='<h1>Informe pagado</h1>',
        )
        InformePago.objects.create(
            informe=informe,
            usuario=self.user,
            metodo=InformePago.METODO_STRIPE,
            estado=InformePago.ESTADO_PAGADO,
            monto='19.99',
            moneda='EUR',
            external_id='cs_test_123',
        )

        self.client.force_login(self.user)
        url = reverse('nexus:ver_informe', kwargs={'pk': informe.pk})

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Informe pagado')

    def test_checkout_shows_pending_payment_text(self):
        informe = InformeTest.objects.create(
            sesion=self.sesion,
            tipo=InformeTest.TIPO_AI_PERSONAL,
            contenido_html='<h1>Informe pendiente</h1>',
        )
        InformePago.objects.create(
            informe=informe,
            usuario=self.user,
            metodo=InformePago.METODO_STRIPE,
            estado=InformePago.ESTADO_PENDIENTE,
            monto='19.99',
            moneda='EUR',
            external_id='cs_test_pending',
        )

        self.client.force_login(self.user)
        url = reverse('nexus:checkout_informe', kwargs={'pk': informe.pk})

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pendiente de pago')

    def test_paid_report_page_shows_paid_status_text(self):
        informe = InformeTest.objects.create(
            sesion=self.sesion,
            tipo=InformeTest.TIPO_AI_PERSONAL,
            contenido_html='<h1>Informe pagado</h1>',
        )
        InformePago.objects.create(
            informe=informe,
            usuario=self.user,
            metodo=InformePago.METODO_PAYPAL,
            estado=InformePago.ESTADO_PAGADO,
            monto='19.99',
            moneda='EUR',
            external_id='pp_test_paid',
            pagado_en=timezone.now(),
        )

        self.client.force_login(self.user)
        url = reverse('nexus:ver_informe', kwargs={'pk': informe.pk})

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Informe pagado')

    def test_report_template_keeps_content_visible_for_print(self):
        informe = InformeTest.objects.create(
            sesion=self.sesion,
            tipo=InformeTest.TIPO_AI_PERSONAL,
            contenido_html='<h1>Informe para imprimir</h1>',
        )
        InformePago.objects.create(
            informe=informe,
            usuario=self.user,
            metodo=InformePago.METODO_STRIPE,
            estado=InformePago.ESTADO_PAGADO,
            monto='19.99',
            moneda='EUR',
            external_id='cs_test_print',
            pagado_en=timezone.now(),
        )

        self.client.force_login(self.user)
        url = reverse('nexus:ver_informe', kwargs={'pk': informe.pk})

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '.no-print')
        self.assertContains(response, '.informe-container')

    @override_settings(DEBUG=True)
    def test_dev_bypass_grants_access_without_payment(self):
        informe = InformeTest.objects.create(
            sesion=self.sesion,
            tipo=InformeTest.TIPO_AI_PERSONAL,
            contenido_html='<h1>Informe a saltar</h1>',
        )

        self.client.force_login(self.user)
        url = reverse('nexus:checkout_informe_dev_skip', kwargs={'pk': informe.pk})

        response = self.client.post(url)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(InformePago.objects.filter(
            informe=informe,
            usuario=self.user,
            estado=InformePago.ESTADO_PAGADO,
            metodo='dev_skip',
        ).exists())
        self.assertIn('/nexus/informe/', response.url)

    def test_user_can_delete_saved_report(self):
        informe = InformeTest.objects.create(
            sesion=self.sesion,
            tipo=InformeTest.TIPO_AI_PERSONAL,
            contenido_html='<h1>Informe a borrar</h1>',
        )

        self.client.force_login(self.user)
        url = reverse('nexus:delete_informe', kwargs={'pk': informe.pk})

        response = self.client.post(url)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(InformeTest.objects.filter(pk=informe.pk).exists())
