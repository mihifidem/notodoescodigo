from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.prompts.models import extract_variables, normalize_variables, render_prompt


class PromptCreationAccessTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.admin = self.user_model.objects.create_user(
            username='admin_prompt',
            password='Test1234!',
            is_staff=True,
            is_superuser=True,
        )
        self.normal_user = self.user_model.objects.create_user(
            username='plain_user',
            password='Test1234!',
        )

    def test_staff_user_can_access_prompt_create_view(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse('prompts:create'))

        self.assertEqual(response.status_code, 200)

    def test_non_staff_user_is_blocked_from_prompt_create_view(self):
        self.client.force_login(self.normal_user)

        response = self.client.get(reverse('prompts:create'))

        self.assertEqual(response.status_code, 403)


class PromptVariableCompatibilityTests(TestCase):
    def test_extract_variables_supports_any_name_and_syntax(self):
        template = 'Hola {{nombre}} y {apellido}. {edad_objetivo} es clave.'

        self.assertEqual(
            extract_variables(template),
            ['nombre', 'apellido', 'edad_objetivo'],
        )

    def test_normalize_variables_accepts_legacy_dict_form(self):
        data = {
            'nombre': 'Juan',
            'ciudad': {'label': 'Ciudad', 'type': 'text', 'default': 'Madrid'},
        }

        normalized = normalize_variables(data)

        self.assertEqual(normalized['nombre']['default'], 'Juan')
        self.assertEqual(normalized['ciudad']['default'], 'Madrid')
        self.assertEqual(normalized['ciudad']['type'], 'text')

    def test_normalize_variables_accepts_legacy_list_of_objects(self):
        data = [
            {'nombre': 'tema', 'descripcion': 'Tema principal', 'valor_defecto': 'marketing'},
            {
                'nombre': 'tono',
                'tipo': 'select',
                'opciones': ['formal', 'amigable'],
                'valor_defecto': 'formal',
            },
        ]

        normalized = normalize_variables(data)

        self.assertEqual(normalized['tema']['default'], 'marketing')
        self.assertEqual(normalized['tono']['type'], 'select')
        self.assertEqual(normalized['tono']['options'][0]['value'], 'formal')

    def test_render_prompt_replaces_any_variable_name(self):
        template = 'Hola {{nombre}}. {cargo} trabaja en {{ciudad}}.'

        rendered = render_prompt(template, {
            'nombre': 'Ana',
            'cargo': 'Senior',
            'ciudad': 'Madrid',
        })

        self.assertEqual(rendered, 'Hola Ana. Senior trabaja en Madrid.')

    def test_render_prompt_replaces_square_bracket_variables(self):
        template = 'Sitúalos en [PAISAJE_DESTINO], con clima [CLIMA] y luz de [MOMENTO_DIA].'

        rendered = render_prompt(template, {
            'PAISAJE_DESTINO': 'una playa tropical en Bali',
            'CLIMA': 'soleado',
            'MOMENTO_DIA': 'atardecer',
        })

        self.assertEqual(
            rendered,
            'Sitúalos en una playa tropical en Bali, con clima soleado y luz de atardecer.',
        )
