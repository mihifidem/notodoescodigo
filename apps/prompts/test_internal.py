import base64
import importlib
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.ai_tutor.services import obtener_respuesta_ia
from apps.blog.services.ai_generator import generar_contenido, generar_imagen_dall_e
from apps.evaluations.models import EntregaEvaluacion, Evaluacion, PromptCorreccion
from apps.evaluations.services import _prompt_json, _resolve_system_prompt, grade_submission
from .internal import get_internal_prompt, render_internal_prompt
from .models import InternalPrompt, Prompt


class InternalPromptTests(TestCase):
    def test_seeded_prompts_are_private_and_render_without_changing_json(self):
        expected = {
            'ai_tutor.system', 'blog.system', 'blog.user', 'blog.image', 'users.ducky_avatar',
            'evaluations.fallback.default', 'evaluations.fallback.OBJ_TEST',
            'evaluations.rules', 'evaluations.precomputed_rules', 'evaluations.response_schema',
        }
        self.assertTrue(expected <= set(InternalPrompt.objects.values_list('key', flat=True)))
        for prompt in InternalPrompt.objects.all():
            prompt.full_clean()
        self.assertEqual(render_internal_prompt('blog.system'), get_internal_prompt('blog.system').content)
        self.assertEqual(
            json.loads(render_internal_prompt('evaluations.response_schema'))['overall_score'], 'number',
        )
        self.assertFalse(Prompt.objects.filter(titulo='Tutor educativo').exists())

    def test_strict_template_variables_and_no_recursive_substitution(self):
        InternalPrompt.objects.create(
            key='test.render', title='Test', content='{nombre}: {texto}', variables=['nombre', 'texto'], active=True,
        )
        self.assertEqual(
            render_internal_prompt('test.render', nombre='{texto}', texto='Hola'),
            '{texto}: Hola',
        )
        with self.assertRaises(ImproperlyConfigured), self.assertLogs('apps.prompts.internal', level='ERROR'):
            render_internal_prompt('test.render', nombre='Ana')
        for variables in ({'nombre': 'x'}, ['nombre', 'nombre'], [3], [['nombre']]):
            prompt = InternalPrompt(key='test.invalid', title='Test', content='Test', variables=variables)
            with self.assertRaises(ValidationError):
                prompt.full_clean()
        prompt = InternalPrompt(key='test.invalid', title='Test', content='{otro}', variables=['nombre'])
        with self.assertRaises(ValidationError):
            prompt.full_clean()
        prompt.content = ' '
        with self.assertRaises(ValidationError):
            prompt.full_clean()

    def test_missing_or_inactive_prompt_is_explicit_and_edits_are_immediate(self):
        for key in ('test.missing', 'blog.image'):
            InternalPrompt.objects.filter(key=key).update(active=False)
            with self.assertRaises(ImproperlyConfigured), self.assertLogs('apps.prompts.internal', level='ERROR'):
                get_internal_prompt(key)
        InternalPrompt.objects.filter(key='blog.image').update(active=True, content='Imagen: {titulo}')
        self.assertEqual(render_internal_prompt('blog.image', titulo='Python'), 'Imagen: Python')

    def test_database_constraints_enforce_versions_and_single_active(self):
        original = get_internal_prompt('blog.image')
        for version in (original.version, '2.0'):
            with self.assertRaises(IntegrityError), transaction.atomic():
                InternalPrompt.objects.create(
                    key=original.key, title='Duplicado', version=version, content='Texto', active=True,
                )
        replacement = InternalPrompt.objects.create(
            key=original.key, title='Nueva version', version='2.0', content='Texto', active=False,
        )
        original.active = False
        original.save()
        replacement.active = True
        replacement.save()
        self.assertEqual(get_internal_prompt(original.key).pk, replacement.pk)

    def test_admin_requires_model_permission(self):
        user = get_user_model().objects.create_user('prompt_staff', is_staff=True)
        self.client.force_login(user)
        url = reverse('admin:prompts_internalprompt_changelist')
        self.assertEqual(self.client.get(url).status_code, 403)
        admin = get_user_model().objects.create_superuser('prompt_admin', 'admin@example.test', 'test-only-password')
        self.client.force_login(admin)
        self.assertContains(self.client.get(url), 'ai_tutor.system')

    def test_legacy_migration_preserves_history_activation_and_is_idempotent(self):
        seed = importlib.import_module('apps.prompts.migrations.0006_seed_internal_prompts')
        old = PromptCorreccion.objects.create(
            scope='PRACTICO_CODIGO', version='custom', system_prompt='Prompt propio', activo=True,
        )
        PromptCorreccion.objects.create(
            scope=old.scope, version=old.version, system_prompt='Historico', activo=False,
        )
        schema_editor = SimpleNamespace(connection=connection)
        seed.seed_prompts(apps, schema_editor)
        self.assertEqual(_resolve_system_prompt(old.scope), ('Prompt propio', 'custom'))
        archived = InternalPrompt.objects.filter(key=f'evaluations.system.{old.scope}')
        self.assertEqual(archived.count(), 2)
        self.assertEqual(archived.filter(active=True).count(), 1)
        self.assertEqual(PromptCorreccion.objects.count(), 2)
        count = InternalPrompt.objects.count()
        seed.seed_prompts(apps, schema_editor)
        self.assertEqual(InternalPrompt.objects.count(), count)

    def test_grading_prompt_precedence_and_invalid_json(self):
        objective = get_internal_prompt('evaluations.fallback.OBJ_TEST')
        self.assertEqual(_resolve_system_prompt('OBJ_TEST'), (objective.content, objective.version))
        default = InternalPrompt.objects.create(
            key='evaluations.system.DEFAULT', title='Default', content='Personalizado', version='2', active=True,
        )
        self.assertEqual(_resolve_system_prompt('OBJ_TEST'), (default.content, '2'))
        InternalPrompt.objects.create(
            key='evaluations.system.OBJ_TEST', title='Test', content='Solo test', version='3', active=True,
        )
        self.assertEqual(_resolve_system_prompt('OBJ_TEST'), ('Solo test', '3'))
        for invalid in ('invalid JSON', '{}', '[3]'):
            InternalPrompt.objects.filter(key='evaluations.rules').update(content=invalid)
            with self.assertRaises(ImproperlyConfigured), self.assertLogs('apps.evaluations.services', level='ERROR'):
                _prompt_json('evaluations.rules', list)


@override_settings(OPENAI_API_KEY='test-only-not-a-real-key')
class InternalPromptConsumerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('internal_consumer', first_name='Ana')

    def response_client(self, content):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        )
        return client

    def test_tutor_reads_current_database_prompt(self):
        InternalPrompt.objects.filter(key='ai_tutor.system').update(
            content='Hola {nombre}, nivel {nivel}, puntos {puntos}, racha {streak}.',
        )
        client = self.response_client('Respuesta del tutor')
        with patch('apps.ai_tutor.services._get_client', return_value=client):
            self.assertEqual(obtener_respuesta_ia(self.user, 'Pregunta'), 'Respuesta del tutor')
        messages = client.chat.completions.create.call_args.kwargs['messages']
        self.assertEqual(messages[0]['content'], render_internal_prompt(
            'ai_tutor.system', nombre=self.user.get_full_name(), nivel=self.user.get_nivel_display(),
            puntos=self.user.puntos, streak=self.user.streak,
        ))
        self.assertEqual(messages[-1]['content'], 'Pregunta')

    def test_blog_uses_database_system_user_and_image_prompts(self):
        InternalPrompt.objects.filter(key='blog.system').update(content='Sistema personalizado')
        InternalPrompt.objects.filter(key='blog.user').update(content='{titulo_original}: {contenido_original}')
        client = self.response_client('{"titulo": "Resultado"}')
        client.images.generate.return_value = SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(b'image').decode('ascii'))],
        )
        with patch('openai.OpenAI', return_value=client):
            self.assertEqual(generar_contenido('Python', 'x' * 5000), {'titulo': 'Resultado'})
            messages = client.chat.completions.create.call_args.kwargs['messages']
            self.assertEqual(messages[0]['content'], 'Sistema personalizado')
            self.assertEqual(messages[1]['content'], 'Python: ' + 'x' * 4000)
            InternalPrompt.objects.filter(key='blog.image').update(content='Imagen {titulo}')
            self.assertEqual(generar_imagen_dall_e('Python'), b'image')
            self.assertEqual(client.images.generate.call_args.kwargs['prompt'], 'Imagen Python')

    def test_ducky_avatar_reads_database_prompt(self):
        from apps.users.views import generar_avatar_ducky_con_ia
        InternalPrompt.objects.filter(key='users.ducky_avatar').update(content='Ducky {nombre_usuario}')
        client = Mock()
        client.images.generate.return_value = SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(b'avatar').decode('ascii'))],
        )
        with patch('openai.OpenAI', return_value=client):
            self.assertEqual(generar_avatar_ducky_con_ia(self.user), b'avatar')
        self.assertEqual(client.images.generate.call_args.kwargs['prompt'], 'Ducky Ana')

    def test_grading_uses_stored_prompts_rules_and_keeps_audit_version(self):
        evaluation = Evaluacion.objects.create(
            titulo='Test', alcance_tipo='UD', tipo_prueba='OBJ_TEST',
            clave_respuestas={'1': 'A', '2': 'B', '3': 'C'},
        )
        submission = EntregaEvaluacion.objects.create(
            evaluacion=evaluation, alumno=self.user, archivo_respuesta='test.txt',
        )
        client = self.response_client('{"overall_score": 0, "confidence": 0.9, "criteria": []}')
        InternalPrompt.objects.create(
            key='evaluations.system.OBJ_TEST', title='Correction', content='Texto de prueba', version='2', active=True,
        )
        with patch('apps.evaluations.services._get_client', return_value=client):
            result = grade_submission(submission, '1. A\n2. B\n3. C')
        messages = client.chat.completions.create.call_args.kwargs['messages']
        self.assertEqual(messages[0]['content'], 'Texto de prueba')
        payload = json.loads(messages[1]['content'])
        self.assertEqual(payload['rules'], [
            *json.loads(get_internal_prompt('evaluations.rules').content),
            *json.loads(get_internal_prompt('evaluations.precomputed_rules').content),
        ])
        self.assertEqual(payload['response_schema'], json.loads(get_internal_prompt('evaluations.response_schema').content))
        self.assertEqual(result['prompt_version'], '2')
        self.assertEqual(result['_system_prompt_used'], 'Texto de prueba')
        self.assertEqual(result['overall_score'], payload['precomputed_test_stats']['puntuacion_bruta'])
