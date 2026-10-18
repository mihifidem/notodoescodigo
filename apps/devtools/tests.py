from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.devtools.models import DevToolVote

User = get_user_model()


class DevToolsTests(TestCase):
    def test_hub_page_loads(self):
        response = self.client.get(reverse('devtools:hub'))
        self.assertEqual(response.status_code, 200)

    def test_secondary_tools_load(self):
        for name in [
            'devtools:medidas',
            'devtools:colores',
            'devtools:sombras',
            'devtools:espaciado',
            'devtools:slugify',
            'devtools:gradientes',
            'devtools:tipografia',
            'devtools:paleta',
            'devtools:bordes',
            'devtools:rem',
            'devtools:botones',
            'devtools:layout',
            'devtools:json',
            'devtools:base64',
            'devtools:texto',
            'devtools:css_vars',
            'devtools:breakpoints',
            'devtools:metadata_img',
            'devtools:qr',
            'devtools:url_shortener',
            'devtools:hash',
            'devtools:analizador_contraste',
        ]:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)

    def test_registered_user_can_change_vote_for_tool(self):
        user = User.objects.create_user(username='tester', password='Secure123!')
        self.client.force_login(user)

        response = self.client.post(reverse('devtools:votar', args=['medidas']), {'vote': 'like'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DevToolVote.objects.filter(tool='medidas', user=user, vote=1).count(), 1)

        response = self.client.post(reverse('devtools:votar', args=['medidas']), {'vote': 'dislike'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DevToolVote.objects.filter(tool='medidas', user=user).count(), 1)
        self.assertEqual(DevToolVote.objects.get(tool='medidas', user=user).vote, DevToolVote.DISLIKE)

    def test_anonymous_user_cannot_vote(self):
        response = self.client.post(reverse('devtools:votar', args=['medidas']), {'vote': 'like'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DevToolVote.objects.filter(tool='medidas').count(), 0)

    def test_universal_converter_supports_temperature_and_pressure(self):
        from apps.devtools.views import convert_measure

        self.assertAlmostEqual(float(convert_measure('temperatura', 32, 'fahrenheit', 'celsius')), 0, places=4)
        self.assertAlmostEqual(float(convert_measure('presion', 1, 'atm', 'bar')), 1.01325, places=5)

    def test_converter_shows_category_specific_examples(self):
        response = self.client.get(reverse('devtools:medidas'), {'category': 'temperatura', 'value': '32', 'from_unit': 'fahrenheit', 'to_unit': 'celsius'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '32 °F')
        self.assertContains(response, '0 °C')

    def test_hub_shows_user_vote_state_for_tool(self):
        user = User.objects.create_user(username='voter', password='Secure123!')
        DevToolVote.objects.create(user=user, tool='medidas', vote=DevToolVote.LIKE)
        self.client.force_login(user)

        response = self.client.get(reverse('devtools:hub'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '"medidas": "like"')

    def test_size_estimator_accepts_empty_values(self):
        response = self.client.get(reverse('devtools:tamanos'), {
            'width': '',
            'height': '',
            'scale': '',
            'density': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '1440')
        self.assertContains(response, '900')
        self.assertContains(response, '1')
        self.assertContains(response, '2')

    def test_rem_converter_defaults_to_16px_and_handles_empty_values(self):
        response = self.client.get(reverse('devtools:rem'), {
            'px': '',
            'base': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '16')
        self.assertContains(response, '1.0rem')

    def test_color_contrast_analyzer_page_loads(self):
        response = self.client.get(reverse('devtools:analizador_contraste'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Analizador de contraste')

    def test_button_generator_supports_width_and_height(self):
        response = self.client.get(reverse('devtools:botones'), {
            'text': 'Comprar',
            'bg': '#10b981',
            'color': '#ffffff',
            'radius': '18',
            'padding_x': '24',
            'padding_y': '14',
            'width': '220',
            'height': '52',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="width"')
        self.assertContains(response, 'name="height"')
        self.assertContains(response, 'width: 220px;')
        self.assertContains(response, 'height: 52px;')

    def test_button_generator_supports_shadow_options(self):
        response = self.client.get(reverse('devtools:botones'), {
            'text': 'Comprar',
            'bg': '#10b981',
            'color': '#ffffff',
            'radius': '18',
            'padding_x': '24',
            'padding_y': '14',
            'width': '220',
            'height': '52',
            'shadow_enabled': 'on',
            'shadow_x': '0',
            'shadow_y': '12',
            'shadow_blur': '24',
            'shadow_color': '#0f172a',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="shadow_enabled"')
        self.assertContains(response, 'name="shadow_x"')
        self.assertContains(response, 'name="shadow_y"')
        self.assertContains(response, 'name="shadow_blur"')
        self.assertContains(response, 'box-shadow: 0 12px 24px #0f172a;')

    def test_css_snippet_generator_supports_start_and_end_colors(self):
        response = self.client.get(reverse('devtools:css'), {
            'start_color': '#00ff88',
            'end_color': '#2563eb',
            'radius': '28',
            'padding': '24',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="start_color"')
        self.assertContains(response, 'name="end_color"')
        self.assertContains(response, '#00ff88')
        self.assertContains(response, '#2563eb')
