from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
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


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class PromptTaxonomyCreationTests(PromptCreationAccessTests):
    def data(self, **changes):
        return {
            'titulo': 'Prompt nuevo', 'contenido': 'Contenido del prompt',
            'visibilidad': 'publico', 'publicado_en': '2026-10-23T18:00',
            **changes,
        }

    def test_create_category_and_subcategory_with_prompt(self):
        from .models import Prompt
        self.client.force_login(self.admin)
        response = self.client.post(reverse('prompts:create'), self.data(
            nueva_categoria='Categoria prueba nueva', nueva_subcategoria='Python',
        ))
        self.assertEqual(response.status_code, 302, getattr(response, 'context_data', {}))
        prompt = Prompt.objects.get(titulo='Prompt nuevo')
        self.assertEqual(prompt.categoria.nombre, 'Categoria prueba nueva')
        self.assertEqual(prompt.subcategoria.nombre, 'Python')
        self.assertEqual(prompt.subcategoria.categoria_id, prompt.categoria_id)

    def test_existing_category_with_new_subcategory(self):
        from .models import CategoriaPrompt, Prompt
        category = CategoriaPrompt.objects.create(nombre='Existente')
        initial_count = CategoriaPrompt.objects.count()
        self.client.force_login(self.admin)
        response = self.client.post(reverse('prompts:create'), self.data(
            categoria=category.pk, nueva_subcategoria='Nueva',
        ))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Prompt.objects.get(titulo='Prompt nuevo').subcategoria.categoria_id, category.pk)
        self.assertEqual(CategoriaPrompt.objects.count(), initial_count)

    def test_invalid_prompt_does_not_create_taxonomy(self):
        from .models import CategoriaPrompt, SubcategoriaPrompt
        self.client.force_login(self.admin)
        response = self.client.post(reverse('prompts:create'), self.data(
            titulo='', nueva_categoria='No guardar', nueva_subcategoria='Tampoco',
        ))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CategoriaPrompt.objects.filter(nombre='No guardar').exists())
        self.assertFalse(SubcategoriaPrompt.objects.filter(nombre='Tampoco').exists())

    def test_duplicates_mismatched_categories_and_conflicts_are_rejected(self):
        from .forms import PromptForm
        from .models import CategoriaPrompt, SubcategoriaPrompt
        category = CategoriaPrompt.objects.create(nombre='Python')
        other = CategoriaPrompt.objects.create(nombre='Otra')
        sub = SubcategoriaPrompt.objects.create(categoria=other, nombre='Basico')
        for changes, field in [
            ({'nueva_categoria': 'python'}, 'nueva_categoria'),
            ({'categoria': category.pk, 'nueva_categoria': 'Nueva'}, 'nueva_categoria'),
            ({'categoria': category.pk, 'subcategoria': sub.pk}, 'subcategoria'),
            ({'categoria': other.pk, 'nueva_subcategoria': 'basico'}, 'nueva_subcategoria'),
            ({'categoria': other.pk, 'subcategoria': sub.pk, 'nueva_subcategoria': 'Nueva'}, 'nueva_subcategoria'),
            ({'nueva_categoria': '!!!'}, 'nueva_categoria'),
            ({'categoria': category.pk, 'nueva_subcategoria': '!!!'}, 'nueva_subcategoria'),
        ]:
            with self.subTest(changes=changes):
                form = PromptForm(self.data(**changes))
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)

    def test_non_admin_cannot_create_taxonomy_by_post(self):
        from .models import CategoriaPrompt
        self.client.force_login(self.normal_user)
        response = self.client.post(reverse('prompts:create'), self.data(
            nueva_categoria='Prohibida', nueva_subcategoria='Prohibida',
        ))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(CategoriaPrompt.objects.filter(nombre='Prohibida').exists())


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

@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class EstilosNotebookLMViewTests(TestCase):
    def test_estilos_page_renders_styles_from_json(self):
        response = self.client.get(reverse('prompts:estilos'))

        self.assertEqual(response.status_code, 200)
        estilos = response.context['estilos']
        self.assertGreater(len(estilos), 0)
        primero = estilos[0]
        self.assertEqual(len(primero['previews']), 3)
        self.assertTrue(primero['previews'][0].endswith('_1.webp'))
        self.assertIn('prompts/preview/', primero['previews'][0])
        self.assertTrue(primero['prompt'])
        self.assertContains(response, 'id="nlm-data"')
