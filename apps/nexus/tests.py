from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import InformeTest, InformePago, SesionTest, TestPlantilla
from . import views


class NexusAICoachReportTests(TestCase):
    def setUp(self):
        self._ensure_prompt_files()

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

    def _ensure_prompt_files(self):
        prompt_root = Path(settings.MEDIA_ROOT) / 'prompts'
        prompt_root.mkdir(parents=True, exist_ok=True)
        aliases = {
            'nexus_essential.txt': 'PROMPT 1 - BÁSICO\n',
            'nexus_advanced.txt': 'PROMPT 2 - TÉCNICO / PROFESIONAL\n',
            'nexus_premium.txt': 'PROMPT PREMIUM - INFORME TÉCNICO / EJECUTIVO\n',
            'nexus_premiium.txt': 'PROMPT PREMIUM - INFORME TÉCNICO / EJECUTIVO\n',
        }
        for filename, content in aliases.items():
            (prompt_root / filename).write_text(content, encoding='utf-8')

        legacy_root = Path(settings.MEDIA_ROOT) / 'nexus' / 'prompts'
        legacy_root.mkdir(parents=True, exist_ok=True)
        for filename, content in aliases.items():
            (legacy_root / filename).write_text(content, encoding='utf-8')

    def test_catalogo_global_de_precios_de_informes(self):
        self.assertEqual(InformeTest.PRECIOS[InformeTest.TIPO_BASE], Decimal('9.99'))
        self.assertEqual(InformeTest.PRECIOS[InformeTest.TIPO_AI_PERSONAL], Decimal('19.99'))
        self.assertEqual(InformeTest.PRECIOS[InformeTest.TIPO_AI_COMPLETO], Decimal('49.00'))

    def test_tipos_de_informe_tienen_nombres_y_precios_publicos(self):
        self.assertEqual(dict(InformeTest.TIPO_CHOICES)[InformeTest.TIPO_BASE], 'Básico (9,99€)')
        self.assertEqual(dict(InformeTest.TIPO_CHOICES)[InformeTest.TIPO_AI_PERSONAL], 'Advanced (19,99€)')
        self.assertEqual(dict(InformeTest.TIPO_CHOICES)[InformeTest.TIPO_AI_COMPLETO], 'Premium (49,00€)')

    def test_alumno_can_access_premium_test_variant_but_must_pay_for_report(self):
        self.user.role = 'alumno'
        self.user.save()
        premium_test = TestPlantilla.objects.create(
            nombre='TechMind Premium',
            descripcion='Premium test',
            archivo_html=SimpleUploadedFile('premium.html', b'<html><body>Premium</body></html>', content_type='text/html'),
            activo=True,
            nivel_acceso=TestPlantilla.NIVEL_PREMIUM,
        )

        self.assertTrue(views._usuario_puede_acceder(self.user, premium_test))
        self.assertNotIn('Solo Premium', views._version_lock_script('alumno'))

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

    @override_settings(MEDIA_ROOT='/tmp')
    def test_generar_informe_ai_returns_operational_error_when_prompt_file_is_empty(self):
        prompt_dir = Path(settings.MEDIA_ROOT) / 'nexus' / 'prompts'
        prompt_dir.mkdir(parents=True, exist_ok=True)
        (prompt_dir / 'nexus_premium.txt').write_text('', encoding='utf-8')

        html = views._generar_informe_ai_html(self.sesion, tipo=InformeTest.TIPO_AI_COMPLETO)

        self.assertIn('No puedo crear informe por un error operativo', html)

    def test_generar_informe_ai_returns_operational_error_when_advanced_main_prompt_is_empty_even_if_fallback_exists(self):
        main_dir = Path(settings.MEDIA_ROOT) / 'nexus' / 'prompts'
        fallback_dir = Path(settings.MEDIA_ROOT) / 'prompts'
        main_dir.mkdir(parents=True, exist_ok=True)
        fallback_dir.mkdir(parents=True, exist_ok=True)
        (main_dir / 'nexus_advanced.txt').write_text('', encoding='utf-8')
        (fallback_dir / 'nexus_advanced.txt').write_text('PROMPT DE RESERVA - NO DEBE USARSE', encoding='utf-8')

        html = views._generar_informe_ai_html(self.sesion, tipo=InformeTest.TIPO_AI_PERSONAL)

        self.assertIn('No puedo crear informe por un error operativo', html)

    def test_load_prompt_variant_for_ai_coach_uses_media_premium_prompt(self):
        premium_path = Path(settings.MEDIA_ROOT) / 'nexus' / 'prompts' / 'nexus_premium.txt'
        if not premium_path.exists():
            self.skipTest('No existe el prompt premium en media/nexus/prompts.')

        expected = next(
            line.strip() for line in premium_path.read_text(encoding='utf-8').splitlines() if line.strip()
        )

        self.assertEqual(views._load_prompt_variant_for_ai_coach(InformeTest.TIPO_AI_COMPLETO), expected)

    def test_resolver_tipo_informe_uses_resultados_json_version_when_session_version_is_empty(self):
        self.sesion.version_test = ''
        self.sesion.resultados_json = {'version': 'premium'}
        self.sesion.save()

        self.assertEqual(views._resolver_tipo_informe_por_sesion(self.sesion), InformeTest.TIPO_AI_COMPLETO)
        self.assertIn('Informe premium', views._generar_informe_ai_html(self.sesion))

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

    def test_rendered_test_keeps_manual_version_selection_available(self):
        self.test.nombre = 'TechMind Essential'
        self.test.nivel_acceso = TestPlantilla.NIVEL_ESSENTIAL
        self.test.archivo_html = SimpleUploadedFile(
            'techmind-version-picker.html',
            '<html><body><section id="version"><div>Selecciona tu evaluación</div><div id="card-essential"></div><div id="card-advanced"></div><div id="card-premium"></div></section></body></html>'.encode('utf-8'),
            content_type='text/html',
        )
        self.test.save()
        self.client.force_login(self.user)
        url = reverse('nexus:student_take', kwargs={'pk': self.test.pk})

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8')
        self.assertNotIn("var forcedVersion = 'essential'", html)
        self.assertIn('Selecciona tu evaluación', html)
        self.assertIn('card-essential', html)
        self.assertIn('card-advanced', html)
        self.assertIn('card-premium', html)

    def test_non_premium_user_is_redirected_to_checkout_for_report(self):
        self.user.role = 'basic'
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
        self.user.role = 'premium'
        self.user.save()
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
