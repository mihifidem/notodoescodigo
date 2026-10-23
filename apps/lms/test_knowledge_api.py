from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import path, reverse
from drf_spectacular.generators import SchemaGenerator
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.courses.models import Curso, Tema
from .knowledge import BankQuestionForm, question_initial, save_bank_question
from .knowledge_api import KnowledgeQuestionSelectionAPIView
from .models import CourseMembership, FormativeUnit, Question, QuestionBank
from .quiz_management import QuizConfigurationForm


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class KnowledgeQuestionAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.teacher = User.objects.create_user('api_teacher')
        cls.student = User.objects.create_user('api_student')
        cls.foreign_teacher = User.objects.create_user('api_foreign_teacher')
        cls.admin = User.objects.create_superuser('api_admin', 'api@example.test', 'test-only-password')
        cls.course = Curso.objects.create(nombre='API preguntas', fecha_inicio=date(2026, 1, 1))
        cls.foreign_course = Curso.objects.create(nombre='API ajeno', fecha_inicio=date(2026, 1, 1))
        cls.module = Tema.objects.create(curso=cls.course, titulo='Módulo API', orden=1)
        cls.second_module = Tema.objects.create(curso=cls.course, titulo='Otro módulo', orden=2)
        cls.foreign_module = Tema.objects.create(curso=cls.foreign_course, titulo='Ajeno', orden=1)
        cls.unit = FormativeUnit.objects.create(module=cls.module, title='Unidad API')
        cls.second_unit = FormativeUnit.objects.create(module=cls.module, title='Otra unidad')
        cls.foreign_unit = FormativeUnit.objects.create(module=cls.foreign_module, title='Ajena')
        cls.other_module_unit = FormativeUnit.objects.create(module=cls.second_module, title='Otro módulo')
        CourseMembership.objects.create(course=cls.course, user=cls.teacher, role='teacher', status='active')
        CourseMembership.objects.create(course=cls.course, user=cls.student, role='student', status='active')
        CourseMembership.objects.create(course=cls.foreign_course, user=cls.foreign_teacher, role='teacher', status='active')
        cls.bank = QuestionBank.objects.create(course=cls.course, title='Publicado', status='published')
        cls.url = reverse('knowledge-question-selection')
        cls.data = {
            'prompt': '¿Cuál es el elemento raíz? <html>',
            'option_a': '<html>', 'option_b': '<body>', 'option_c': '<main>', 'option_d': '<head>',
            'correct': 'A', 'explanation': 'Elemento raíz del documento.', 'difficulty': 5,
            'points': 1, 'status': 'published', 'formative_unit': cls.unit.pk,
        }

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.teacher)

    def create_question(self, **changes):
        return save_bank_question(bank=self.bank, actor=self.teacher, data={**self.data, **changes})

    def select(self, **changes):
        return self.client.get(self.url, {
            'modulo': self.module.pk, 'unidad_formativa': self.unit.pk,
            'dificultad': 5, 'numero_preguntas': 2, **changes,
        })

    def test_exact_selection_returns_solutions_and_persisted_current_versions(self):
        questions = [self.create_question(prompt=f'Pregunta {i}: <html>') for i in range(4)]
        response = self.select()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['disponibles'], 4)
        self.assertEqual(response.data['devueltas'], 2)
        selected = response.data['preguntas']
        self.assertEqual(len({item['id'] for item in selected}), 2)
        self.assertTrue({item['id'] for item in selected} <= {q.pk for q in questions})
        for item in selected:
            question = Question.objects.get(pk=item['id'])
            self.assertEqual(item['version_id'], question.current_version_id)
            self.assertEqual(item['enunciado'], question_initial(question)['prompt'])
            self.assertEqual(item['respuestas_correctas'], ['<html>'])
            self.assertEqual(item['opciones'], ['<html>', '<body>', '<main>', '<head>'])
            self.assertEqual(item['formato_enunciado'], 'text')
            self.assertEqual(item['dificultad'], 5)
            self.assertEqual(item['modulo'], self.module.pk)
            self.assertEqual(item['unidad_formativa'], self.unit.pk)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('private', response['Cache-Control'])
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(Question.objects.filter(bank=self.bank).count(), 4)

    def test_filters_exclude_other_units_levels_and_unpublished_content(self):
        expected = self.create_question()
        self.create_question(difficulty=4)
        self.create_question(formative_unit=self.second_unit.pk)
        self.create_question(formative_unit=self.other_module_unit.pk)
        self.create_question(status='draft')
        self.create_question(status='archived')
        unpublished_version = self.create_question()
        unpublished_version.current_version.is_published = False
        unpublished_version.current_version.save()
        missing_version = self.create_question()
        missing_version.current_version = None
        missing_version.save()
        for bank_status in ('draft', 'archived'):
            bank = QuestionBank.objects.create(course=self.course, title=bank_status, status='published')
            question = self.create_question()
            question.bank = bank
            question.save()
            bank.status = bank_status
            bank.save()
        response = self.select(numero_preguntas=1)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['disponibles'], 1)
        self.assertEqual(response.data['preguntas'][0]['id'], expected.pk)

    def test_insufficient_inventory_is_explicit_and_never_partial(self):
        self.create_question()
        response = self.select()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'insufficient_questions')
        self.assertEqual(response.data['solicitadas'], 2)
        self.assertEqual(response.data['disponibles'], 1)
        self.assertNotIn('preguntas', response.data)
        response = self.select(dificultad=1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['disponibles'], 0)

    def test_authentication_and_course_authorization_prevent_answer_disclosure(self):
        self.create_question()
        for user in (None, self.student, self.foreign_teacher):
            self.client.force_authenticate(user)
            response = self.select(numero_preguntas=1)
            self.assertEqual(response.status_code, 403)
            self.assertNotIn('preguntas', response.data)
            self.assertIn('no-store', response['Cache-Control'])
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.select(numero_preguntas=1).status_code, 200)
        membership = CourseMembership.objects.get(course=self.course, user=self.teacher)
        membership.status = 'suspended'
        membership.save()
        self.client.force_authenticate(self.teacher)
        self.assertEqual(self.select(numero_preguntas=1).status_code, 403)

    def test_token_authentication_uses_the_same_course_permissions(self):
        self.create_question()
        self.client.force_authenticate(None)
        token = Token.objects.create(user=self.teacher)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        self.assertEqual(self.select(numero_preguntas=1).status_code, 200)
        student_token = Token.objects.create(user=self.student)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {student_token.key}')
        self.assertEqual(self.select(numero_preguntas=1).status_code, 403)

    def test_required_parameters_range_and_types(self):
        valid = {'modulo': self.module.pk, 'unidad_formativa': self.unit.pk, 'dificultad': 1, 'numero_preguntas': 1}
        for key in valid:
            with self.subTest(missing=key):
                response = self.client.get(self.url, {k: v for k, v in valid.items() if k != key})
                self.assertEqual(response.status_code, 400)
                self.assertIn(key, response.data)
        for key, values in {
            'modulo': (0, -1, 'abc'), 'unidad_formativa': (0, -1, 'abc'),
            'dificultad': (0, 6, -1, 'abc', '2.5'),
            'numero_preguntas': (0, -1, 101, 'abc', '2.5'),
        }.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    response = self.client.get(self.url, {**valid, key: value})
                    self.assertEqual(response.status_code, 400)
                    self.assertIn(key, response.data)

    def taxonomy(self):
        from apps.courses.models import CourseCategory
        from apps.duckyclash.models import (
            ChallengeSubcategory, SubcategoryQuestionLevel, SubcategoryQuestionSource,
        )
        category = CourseCategory.objects.create(nombre='Categoría API')
        subcategory = ChallengeSubcategory.objects.create(category=category, name='Subcategoría API')
        source = SubcategoryQuestionSource.objects.create(subcategory=subcategory, approved_by=self.teacher)
        level = SubcategoryQuestionLevel.objects.create(
            source=source, reference='Banco completo', bank=self.bank, approved_by=self.teacher,
        )
        return {
            'categoria': category.pk, 'subcategoria': subcategory.pk,
            'dificultad': 5, 'numero_preguntas': 1,
        }, source, level

    def test_taxonomy_selects_exact_difficulty_one_to_five_and_quantity(self):
        filters, source, level = self.taxonomy()
        for difficulty in range(1, 6):
            expected = [self.create_question(difficulty=difficulty) for _ in range(3)]
            response = self.client.get(self.url, {**filters, 'dificultad': difficulty, 'numero_preguntas': 2})
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['disponibles'], 3)
            self.assertEqual(response.data['devueltas'], 2)
            self.assertEqual(len({item['id'] for item in response.data['preguntas']}), 2)
            for item in response.data['preguntas']:
                self.assertIn(item['id'], {question.pk for question in expected})
                self.assertEqual(item['dificultad'], difficulty)
                self.assertEqual(item['respuestas_correctas'], ['<html>'])
            self.assertEqual(response.data['filtros']['categoria'], filters['categoria'])
            self.assertIn('no-store', response['Cache-Control'])
        response = self.client.get(self.url, {**filters, 'numero_preguntas': 4})
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('preguntas', response.data)

    def test_taxonomy_permissions_publication_and_disabled_source(self):
        filters, source, level = self.taxonomy()
        self.create_question()
        for user in (None, self.student, self.foreign_teacher):
            self.client.force_authenticate(user)
            response = self.client.get(self.url, filters)
            self.assertEqual(response.status_code, 403)
            self.assertNotIn('preguntas', response.data)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(self.url, filters).status_code, 200)
        self.client.force_authenticate(self.teacher)
        for entry, field, disabled in (
            (source, 'active', False), (level, 'active', False),
            (self.bank, 'status', 'draft'), (self.teacher, 'is_active', False),
        ):
            original = getattr(entry, field)
            setattr(entry, field, disabled)
            entry.save()
            response = self.client.get(self.url, filters)
            self.assertIn(response.status_code, (403, 409))
            self.assertNotIn('preguntas', response.data)
            setattr(entry, field, original)
            entry.save()

    def test_taxonomy_validates_relationships_parameters_and_active_catalog(self):
        from apps.courses.models import CourseCategory
        from apps.duckyclash.models import ChallengeSubcategory
        filters, source, level = self.taxonomy()
        for key in ('categoria', 'subcategoria'):
            for value in ((0, -1, 'abc') if key == 'categoria' else (0, -1, '', 'x' * 121)):
                response = self.client.get(self.url, {**filters, key: value})
                self.assertEqual(response.status_code, 400)
                self.assertIn(key, response.data)
            if key == 'categoria':
                response = self.client.get(self.url, {k: v for k, v in filters.items() if k != key})
                self.assertEqual(response.status_code, 400)
                self.assertIn(key, response.data)
            self.assertEqual(self.client.get(self.url, {**filters, key: 999999}).status_code, 404)
        self.assertEqual(self.client.get(self.url, {**filters, 'modulo': self.module.pk}).status_code, 400)
        subcategory = ChallengeSubcategory.objects.get(pk=filters['subcategoria'])
        foreign_category = CourseCategory.objects.create(nombre='Otra categoría API')
        foreign_subcategory = ChallengeSubcategory.objects.create(category=foreign_category, name='Ajena')
        self.assertEqual(self.client.get(
            self.url, {**filters, 'subcategoria': foreign_subcategory.pk},
        ).status_code, 404)
        other = ChallengeSubcategory.objects.create(category=subcategory.category, name='Otra')
        subcategory.active = False
        subcategory.save()
        self.assertEqual(self.client.get(self.url, filters).status_code, 404)
        other.category.activa = False
        other.category.save()
        self.assertEqual(self.client.get(self.url, {**filters, 'subcategoria': other.pk}).status_code, 404)

    def test_subcategory_name_lookup_is_exact_case_insensitive_and_scoped(self):
        from apps.courses.models import CourseCategory
        from apps.duckyclash.models import ChallengeSubcategory
        filters, source, level = self.taxonomy()
        subcategory = source.subcategory
        subcategory.name = 'Python'
        subcategory.save()
        expected = self.create_question()
        foreign_category = CourseCategory.objects.create(nombre='Otra categoría Python')
        ChallengeSubcategory.objects.create(category=foreign_category, name='Python')
        for name in ('Python', 'python', 'PYTHON', ' Python '):
            response = self.client.get(self.url, {**filters, 'subcategoria': name})
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['preguntas'][0]['id'], expected.pk)
        for name in ('Pyth', 'Inexistente'):
            self.assertEqual(self.client.get(self.url, {**filters, 'subcategoria': name}).status_code, 404)
        self.client.force_authenticate(self.student)
        self.assertEqual(self.client.get(self.url, {**filters, 'subcategoria': 'Python'}).status_code, 403)
        self.client.force_authenticate(self.teacher)
        subcategory.active = False
        subcategory.save()
        self.assertEqual(self.client.get(self.url, {**filters, 'subcategoria': 'Python'}).status_code, 404)

    def test_ambiguous_subcategory_name_returns_validation_error(self):
        from apps.duckyclash.models import ChallengeSubcategory
        filters, source, level = self.taxonomy()
        subcategory = source.subcategory
        subcategory.name = 'Python'
        subcategory.save()
        ChallengeSubcategory.objects.create(category=subcategory.category, name='PYTHON')
        self.create_question()
        response = self.client.get(self.url, {**filters, 'subcategoria': 'python'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('subcategoria', response.data)
        self.assertEqual(self.client.get(self.url, filters).status_code, 200)

    def test_taxonomy_never_leaks_solutions_from_another_course(self):
        from apps.duckyclash.models import SubcategoryQuestionLevel
        filters, source, level = self.taxonomy()
        self.create_question()
        bank = QuestionBank.objects.create(course=self.foreign_course, title='Banco ajeno', status='published')
        SubcategoryQuestionLevel.objects.create(
            source=source, reference='Otro curso', bank=bank, approved_by=self.foreign_teacher,
        )
        save_bank_question(bank=bank, actor=self.foreign_teacher, data={
            **self.data, 'formative_unit': self.foreign_unit.pk,
        })
        response = self.client.get(self.url, filters)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('preguntas', response.data)
        self.assertNotIn('disponibles', response.data)
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.url, {**filters, 'numero_preguntas': 2})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['disponibles'], 2)

    def test_category_only_selects_across_subcategories_at_each_difficulty(self):
        from apps.duckyclash.models import ChallengeSubcategory, SubcategoryQuestionSource, SubcategoryQuestionLevel
        filters, source, level = self.taxonomy()
        filters.pop('subcategoria')
        other_subcategory = ChallengeSubcategory.objects.create(category_id=filters['categoria'], name='Segunda')
        other_source = SubcategoryQuestionSource.objects.create(subcategory=other_subcategory, approved_by=self.teacher)
        other_bank = QuestionBank.objects.create(course=self.course, title='Segundo banco', status='published')
        SubcategoryQuestionLevel.objects.create(
            source=other_source, reference='Segundo banco', bank=other_bank, approved_by=self.teacher,
        )
        for difficulty in range(1, 6):
            first = self.create_question(difficulty=difficulty)
            second = save_bank_question(bank=other_bank, actor=self.teacher, data={
                **self.data, 'difficulty': difficulty,
            })
            response = self.client.get(self.url, {**filters, 'dificultad': difficulty, 'numero_preguntas': 2})
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['disponibles'], 2)
            self.assertEqual(response.data['devueltas'], 2)
            self.assertEqual({item['id'] for item in response.data['preguntas']}, {first.pk, second.pk})
            self.assertTrue(all(item['dificultad'] == difficulty for item in response.data['preguntas']))
            self.assertEqual(self.client.get(
                self.url, {**filters, 'dificultad': difficulty, 'numero_preguntas': 3},
            ).status_code, 409)
        other_subcategory.active = False
        other_subcategory.save()
        response = self.client.get(self.url, filters)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['disponibles'], 1)

    def test_category_only_preserves_cross_course_permissions(self):
        from apps.duckyclash.models import SubcategoryQuestionLevel
        filters, source, level = self.taxonomy()
        filters.pop('subcategoria')
        bank = QuestionBank.objects.create(course=self.foreign_course, title='Ajeno', status='published')
        SubcategoryQuestionLevel.objects.create(
            source=source, reference='Ajeno', bank=bank, approved_by=self.foreign_teacher,
        )
        save_bank_question(bank=bank, actor=self.foreign_teacher, data={
            **self.data, 'formative_unit': self.foreign_unit.pk,
        })
        self.assertEqual(self.client.get(self.url, filters).status_code, 403)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(self.url, filters).status_code, 200)

    def test_taxonomy_excludes_review_flags_and_unpublished_questions(self):
        filters, source, level = self.taxonomy()
        expected = self.create_question()
        self.create_question(status='draft')
        self.create_question(status='archived')
        review = self.create_question()
        review.current_version.metadata['csv_review'] = {'errors': ['Pendiente']}
        review.current_version.save()
        hidden = self.create_question()
        hidden.current_version.is_published = False
        hidden.current_version.save()
        response = self.client.get(self.url, filters)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['disponibles'], 1)
        self.assertEqual(response.data['preguntas'][0]['id'], expected.pk)

    def test_taxonomy_legacy_publication_and_explicit_source_precedence(self):
        from apps.duckyclash.models import KnowledgePublication
        filters, source, level = self.taxonomy()
        question = self.create_question()
        KnowledgePublication.objects.create(
            version=question.current_version, category_id=filters['categoria'],
            subcategory_id=filters['subcategoria'], approved_by=self.teacher,
        )
        self.assertEqual(self.client.get(self.url, filters).data['disponibles'], 1)
        source.active = False
        source.save()
        self.assertEqual(self.client.get(self.url, filters).status_code, 409)
        source.delete()
        self.assertEqual(self.client.get(self.url, filters).data['preguntas'][0]['id'], question.pk)

    def test_missing_and_mismatched_modules_and_units(self):
        for changes in (
            {'modulo': 999999}, {'unidad_formativa': 999999},
            {'unidad_formativa': self.foreign_unit.pk}, {'unidad_formativa': self.other_module_unit.pk},
        ):
            self.assertEqual(self.select(**changes).status_code, 404)
        self.assertEqual(self.select(modulo=self.foreign_module.pk, unidad_formativa=self.foreign_unit.pk).status_code, 403)
        self.assertEqual(self.client.post(self.url, {}).status_code, 405)
        self.assertEqual(self.select(numero_preguntas=100).status_code, 409)

    def test_classification_roundtrips_through_teacher_form_and_duplication(self):
        create = reverse('lms:knowledge_question_create', kwargs={'course_id': self.course.pk, 'bank_id': self.bank.pk})
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(create), 'Módulo / unidad formativa')
        self.assertEqual(self.client.post(create, self.data).status_code, 302)
        question = self.bank.questions.get()
        self.assertEqual(question.topic_id, self.module.pk)
        self.assertEqual(question.formative_unit_id, self.unit.pk)
        self.assertEqual(question.current_version.metadata['formative_unit_id'], self.unit.pk)
        initial = question_initial(question)
        self.assertEqual(initial['formative_unit'], self.unit.pk)
        duplicate = save_bank_question(bank=self.bank, actor=self.teacher, data={**initial, 'status': 'draft'})
        self.assertEqual(duplicate.formative_unit_id, self.unit.pk)
        self.assertEqual(duplicate.difficulty, 5)
        self.assertEqual(self.select(numero_preguntas=1).data['preguntas'][0]['id'], question.pk)

    def test_foreign_classification_is_rejected_at_form_and_model_layers(self):
        form = BankQuestionForm({**self.data, 'formative_unit': self.foreign_unit.pk}, bank=self.bank)
        self.assertFalse(form.is_valid())
        self.assertIn('formative_unit', form.errors)
        question = self.create_question()
        question.formative_unit = self.foreign_unit
        with self.assertRaises(ValidationError):
            question.save()
        question.formative_unit = self.unit
        question.topic = self.foreign_module
        with self.assertRaises(ValidationError):
            question.save()
        self.unit.module = self.second_module
        with self.assertRaises(ValidationError):
            self.unit.full_clean()

    def test_difficulty_model_and_database_enforce_one_to_five(self):
        for difficulty in range(1, 6):
            question = self.create_question(difficulty=difficulty)
            question.full_clean(exclude=['tags'])
            response = self.select(dificultad=difficulty, numero_preguntas=1)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['preguntas'][0]['id'], question.pk)
        question = self.bank.questions.first()
        for difficulty in (0, 6):
            question.difficulty = difficulty
            with self.assertRaises(ValidationError):
                question.full_clean(exclude=['tags'])
            with self.assertRaises(IntegrityError), transaction.atomic():
                Question.objects.filter(pk=question.pk).update(difficulty=difficulty)

    def test_versions_from_another_question_and_review_flags_are_excluded(self):
        question = self.create_question()
        other = self.create_question()
        question.current_version = other.current_version
        question.save()
        other.current_version.metadata['csv_review'] = {'errors': ['Pendiente']}
        other.current_version.save()
        self.assertEqual(self.select(numero_preguntas=1).status_code, 409)

    def test_selection_really_requests_random_order(self):
        for index in range(3):
            self.create_question(prompt=f'Aleatoria {index}')
        from django.db.models.query import QuerySet
        original = QuerySet.order_by
        calls = []

        def record(queryset, *fields):
            calls.append(fields)
            return original(queryset, *fields)

        with patch.object(QuerySet, 'order_by', record):
            self.assertEqual(self.select().status_code, 200)
        self.assertIn(('?',), calls)

    def test_edit_uses_latest_version_and_keeps_previous_version_unchanged(self):
        question = self.create_question()
        historical = question.current_version
        save_bank_question(
            bank=self.bank, actor=self.teacher, question=question,
            data={**self.data, 'prompt': 'Enunciado actualizado', 'correct': 'B'},
        )
        response = self.select(numero_preguntas=1)
        self.assertEqual(response.status_code, 200)
        item = response.data['preguntas'][0]
        question.refresh_from_db()
        historical.refresh_from_db()
        self.assertEqual(item['version_id'], question.current_version_id)
        self.assertNotEqual(item['version_id'], historical.pk)
        self.assertEqual(item['version'], 2)
        self.assertEqual(item['enunciado'], 'Enunciado actualizado')
        self.assertEqual(item['respuestas_correctas'], ['<body>'])
        self.assertEqual(historical.correct_answers, ['<html>'])

    def test_structured_answers_and_html_remain_typed(self):
        question = Question.objects.create(
            bank=self.bank, topic=self.module, formative_unit=self.unit,
            difficulty=5, question_type=Question.Type.MATCHING,
        )
        version = question.create_version(
            prompt='<p>Relaciona cada concepto.</p>',
            options=['A', 'B'], correct_answers={'A': 'Primero', 'B': 'Segundo'}, published=True,
        )
        response = self.select(numero_preguntas=1)
        self.assertEqual(response.status_code, 200)
        item = response.data['preguntas'][0]
        self.assertEqual(item['formato_enunciado'], 'html')
        self.assertEqual(item['enunciado'], '<p>Relaciona cada concepto.</p>')
        self.assertEqual(item['respuestas_correctas'], version.correct_answers)
        self.assertEqual(item['tipo'], Question.Type.MATCHING)

    def test_levels_four_and_five_are_available_in_bank_and_quiz_filters(self):
        for level in (4, 5):
            question = self.create_question(difficulty=level)
            self.client.force_login(self.teacher)
            url = reverse('lms:knowledge_bank', kwargs={'course_id': self.course.pk, 'bank_id': self.bank.pk})
            response = self.client.get(url, {'difficulty': level})
            self.assertEqual(response.status_code, 200)
            self.assertEqual([q.pk for q in response.context['questions']], [question.pk])
            self.assertContains(response, f'value="{level}" selected')
        self.assertEqual(
            [value for value, label in QuizConfigurationForm.base_fields['difficulty'].choices],
            ['', 1, 2, 3, 4, 5],
        )

    def test_inactive_account_and_unclassified_questions_are_not_eligible(self):
        question = self.create_question()
        question.formative_unit = None
        question.save()
        self.assertEqual(self.select(numero_preguntas=1).status_code, 409)
        self.teacher.is_active = False
        self.assertEqual(self.select(numero_preguntas=1).status_code, 403)

    def test_openapi_declares_filters_ranges_and_solution_response(self):
        schema = SchemaGenerator(patterns=[
            path('knowledge/questions/', KnowledgeQuestionSelectionAPIView.as_view()),
        ]).get_schema(request=None, public=True)
        operation = schema['paths']['/knowledge/questions/']['get']
        parameters = {item['name']: item for item in operation['parameters']}
        self.assertEqual(set(parameters), {
            'modulo', 'unidad_formativa', 'categoria', 'subcategoria', 'dificultad', 'numero_preguntas',
        })
        self.assertTrue(parameters['dificultad']['required'])
        self.assertTrue(parameters['numero_preguntas']['required'])
        for key in ('modulo', 'unidad_formativa', 'categoria', 'subcategoria'):
            self.assertFalse(parameters[key].get('required', False))
        self.assertEqual(parameters['dificultad']['schema']['minimum'], 1)
        self.assertEqual(parameters['dificultad']['schema']['maximum'], 5)
        self.assertEqual(parameters['numero_preguntas']['schema']['maximum'], 100)
        fields = schema['components']['schemas']['SelectedQuestion']['properties']
        self.assertIn('respuestas_correctas', fields)
        self.assertIn('opciones', fields)
        self.assertIn('409', operation['responses'])
