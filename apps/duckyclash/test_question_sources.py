from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections, IntegrityError, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from apps.courses.models import CourseCategory
from apps.lms.knowledge import parse_question_csv, publish_bank_questions, save_bank_question
from apps.lms.models import Question, QuestionBank, QuestionVersion

from .knowledge import KnowledgeUnavailable, LMSKnowledgeGateway
from .models import ChallengeSubcategory, KnowledgePublication, SubcategoryQuestionSource, SubcategoryQuestionLevel
from . import test_knowledge as knowledge


class QuestionLevelMigrationTests(TransactionTestCase):
    def test_existing_sources_become_levels_without_losing_banks_or_activity(self):
        alias = 'question_level_migration'
        connections.databases[alias] = {
            **connections['default'].settings_dict,
            'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:', 'OPTIONS': {},
        }
        connection = connections[alias]
        read_router = patch('django.db.router.db_for_read', return_value=alias)
        write_router = patch('django.db.router.db_for_write', return_value=alias)
        read_router.start()
        write_router.start()
        try:
            executor = MigrationExecutor(connection)
            target = [('duckyclash', '0011_subcategory_level_banks')]
            executor.migrate(target)
            apps = executor.loader.project_state(target).apps
            User = apps.get_model('users', 'User')
            Course = apps.get_model('courses', 'Curso')
            Category = apps.get_model('courses', 'CourseCategory')
            Subcategory = apps.get_model('duckyclash', 'ChallengeSubcategory')
            Bank = apps.get_model('lms', 'QuestionBank')
            Source = apps.get_model('duckyclash', 'SubcategoryQuestionSource')
            approver = User.objects.using(alias).create(username='migration_curator', is_active=True)
            course = Course.objects.using(alias).create(nombre='Curso migración', fecha_inicio='2026-01-01')
            category = Category.objects.using(alias).create(nombre='Migración', slug='migration')
            subcategory = Subcategory.objects.using(alias).create(category_id=category.pk, name='Python')
            bank_one = Bank.objects.using(alias).create(course_id=course.pk, title='Banco inicial', slug='initial')
            bank_two = Bank.objects.using(alias).create(course_id=course.pk, title='Banco avanzado', slug='advanced')
            Source.objects.using(alias).create(
                subcategory_id=subcategory.pk, bank_id=bank_one.pk, level=1, active=False, approved_by_id=approver.pk,
            )
            Source.objects.using(alias).create(
                subcategory_id=subcategory.pk, bank_id=bank_two.pk, level=2, active=True, approved_by_id=approver.pk,
            )
            executor = MigrationExecutor(connection)
            target = [('duckyclash', '0012_question_level_model')]
            executor.migrate(target)
            apps = executor.loader.project_state(target).apps
            Source = apps.get_model('duckyclash', 'SubcategoryQuestionSource')
            Level = apps.get_model('duckyclash', 'SubcategoryQuestionLevel')
            source = Source.objects.using(alias).get(subcategory_id=subcategory.pk)
            self.assertTrue(source.active)
            self.assertEqual(
                set(Level.objects.using(alias).filter(source_id=source.pk).values_list('reference', 'bank_id', 'active', 'approved_by_id')),
                {('1/5', bank_one.pk, False, approver.pk), ('2/5', bank_two.pk, True, approver.pk)},
            )
        finally:
            read_router.stop()
            write_router.stop()
            connection.close()
            del connections[alias]
            del connections.databases[alias]


@override_settings(
    DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.bank.ProfileBalanceGateway',
    DUCKYCLASH_PAYMENT_GATEWAY='',
    DUCKYCLASH_KNOWLEDGE_GATEWAY='apps.duckyclash.knowledge.LMSKnowledgeGateway',
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class QuestionSourceTests(TestCase):
    create = knowledge.KnowledgeTests.create
    accept = knowledge.KnowledgeTests.accept
    publish = knowledge.KnowledgeTests.publish
    prepare = knowledge.KnowledgeTests.prepare
    close = knowledge.KnowledgeTests.close
    payload = knowledge.KnowledgeTests.payload
    answer = knowledge.KnowledgeTests.answer

    def setUp(self):
        knowledge.KnowledgeTests.setUp(self)
        KnowledgePublication.objects.all().delete()
        self.source = SubcategoryQuestionSource.objects.create(
            subcategory=self.subcategory, approved_by=self.curator,
        )
        self.level = SubcategoryQuestionLevel.objects.create(
            source=self.source, reference='1/5', bank=self.bank, approved_by=self.curator,
        )

    def candidates(self, subcategory=None):
        return LMSKnowledgeGateway().candidates(
            category_id=self.category.pk, subcategory_id=(subcategory or self.subcategory).pk,
        )

    def test_source_has_many_levels_with_free_references_and_different_banks(self):
        for level in range(2, 8):
            bank = QuestionBank.objects.create(
                course=self.bank.course, title=f'Banco nivel {level}', status='published',
            )
            question = save_bank_question(bank=bank, actor=self.curator, data={
                'prompt': f'Pregunta nivel {level}', 'option_a': 'A', 'option_b': 'B',
                'option_c': 'C', 'option_d': 'D', 'correct': 'A',
                'difficulty': 1, 'points': 1, 'status': 'published',
            })
            SubcategoryQuestionLevel.objects.create(
                source=self.source, reference=f'Avanzado {level}', bank=bank, approved_by=self.curator,
            )
            self.assertIn(question.current_version_id, {item.local_version_id for item in self.candidates()})
        levels = self.source.levels.all()
        self.assertEqual(levels.count(), 7)
        self.assertEqual(len(set(levels.values_list('bank_id', flat=True))), 7)
        self.assertEqual(self.subcategory.question_sources, self.source)

    def test_duplicate_reference_or_bank_is_rejected_by_model_and_database(self):
        other_bank = QuestionBank.objects.create(course=self.bank.course, title='Otro banco')
        for reference, bank in (('1/5', other_bank), ('Avanzado', self.bank)):
            data = {
                'source': self.source, 'reference': reference,
                'bank': bank, 'approved_by': self.curator,
            }
            with self.subTest(reference=reference, bank=bank.pk):
                with self.assertRaises(ValidationError):
                    SubcategoryQuestionLevel.objects.create(**data)
                with self.assertRaises(IntegrityError), transaction.atomic():
                    SubcategoryQuestionLevel.objects.bulk_create([SubcategoryQuestionLevel(**data)])
        other = ChallengeSubcategory.objects.create(category=self.category, name='Otra subcategoría')
        source = SubcategoryQuestionSource.objects.create(
            subcategory=other, approved_by=self.curator,
        )
        SubcategoryQuestionLevel.objects.create(
            source=source, reference='1/5', bank=self.bank, approved_by=self.curator,
        )

    def add_question(self, prompt='Pregunta del banco', status=Question.Status.PUBLISHED):
        return save_bank_question(bank=self.bank, actor=self.curator, data={
            'prompt': prompt, 'option_a': 'Correcta', 'option_b': 'Falsa B',
            'option_c': 'Falsa C', 'option_d': 'Falsa D', 'correct': 'A',
            'explanation': 'SOLUCION-PRIVADA', 'difficulty': 2, 'points': 1, 'status': status,
        })

    def test_bank_questions_appear_without_individual_publications_and_validate_on_server(self):
        question = self.add_question()
        self.assertFalse(KnowledgePublication.objects.exists())
        self.assertEqual({item.local_version_id for item in self.candidates()}, {self.version.pk, question.current_version_id})
        with patch('apps.duckyclash.questions.secrets.SystemRandom') as random:
            random.return_value.shuffle.side_effect = lambda items: items.reverse()
            round_question = self.prepare()
        self.assertEqual(round_question.source_id, str(question.pk))
        self.assertTrue(self.answer(round_question, text='Correcta').is_correct)
        self.assertFalse(self.answer(round_question, actor=self.opponent, text='Falsa B').is_correct)

    def test_filters_drafts_archived_versions_and_invalid_options(self):
        draft = self.add_question(status=Question.Status.DRAFT)
        published = self.add_question()
        old_version = published.current_version
        published.create_version(
            prompt='Nueva revision', options=['E', 'F', 'G', 'H'], correct_answers=['E'], published=True,
        )
        ids = {item.local_version_id for item in self.candidates()}
        self.assertIn(published.current_version_id, ids)
        self.assertNotIn(old_version.pk, ids)
        self.assertNotIn(draft.current_version_id, ids)
        for options, answers in ((['A', 'B'], ['A']), (['A', ' a ', 'C', 'D'], ['A']),
                                  (['A', 'B', 'C', 'D'], ['A', 'B'])):
            with transaction.atomic():
                QuestionVersion.objects.filter(pk=self.version.pk).update(options=options, correct_answers=answers)
                self.assertNotIn(self.version.pk, {item.local_version_id for item in self.candidates()})
                transaction.set_rollback(True)

    def test_csv_questions_become_candidates_only_after_lms_publication(self):
        rows = parse_question_csv(SimpleUploadedFile('preguntas.csv', (
            'Pregunta;OpcionA;OpcionB;OpcionC;OpcionD;RespuestaCorrecta\n'
            'Pregunta importada;Primera;Segunda;Tercera;Cuarta;B\n'
        ).encode('utf-8')))
        question = save_bank_question(bank=self.bank, actor=self.curator, data=rows[0])
        self.assertNotIn(str(question.pk), {item.identity for item in self.candidates()})
        publish_bank_questions(bank=self.bank, actor=self.curator, question_ids=[question.pk])
        question.refresh_from_db()
        item = next(item for item in self.candidates() if item.identity == str(question.pk))
        self.assertEqual(item.revision, str(question.current_version_id))
        self.assertTrue(LMSKnowledgeGateway().validate(item, 'Segunda'))
        self.assertFalse(KnowledgePublication.objects.exists())

    def test_subcategories_are_isolated_and_category_queries_include_sources(self):
        other_sub = ChallengeSubcategory.objects.create(category=self.category, name='Otro tema')
        other_bank = QuestionBank.objects.create(
            course=self.course, owner=self.curator, title='Otro banco', status=QuestionBank.Status.PUBLISHED,
        )
        source = SubcategoryQuestionSource.objects.create(
            subcategory=other_sub, approved_by=self.curator,
        )
        SubcategoryQuestionLevel.objects.create(
            source=source, reference='1/5', bank=other_bank, approved_by=self.curator,
        )
        self.assertEqual(self.candidates(other_sub), [])
        self.assertEqual(len(self.candidates()), 1)
        items = LMSKnowledgeGateway().candidates(category_id=self.category.pk, subcategory_id=None)
        self.assertEqual([item.local_version_id for item in items], [self.version.pk])
        other_category = CourseCategory.objects.create(nombre='Otra categoria')
        self.assertEqual(LMSKnowledgeGateway().candidates(
            category_id=other_category.pk, subcategory_id=self.subcategory.pk,
        ), [])

    def test_disabled_source_never_falls_back_to_individual_publications(self):
        KnowledgePublication.objects.create(
            version=self.version, category=self.category, subcategory=self.subcategory, approved_by=self.curator,
        )
        for model, pk, values in (
            (SubcategoryQuestionSource, self.source.pk, {'active': False}),
            (SubcategoryQuestionLevel, self.level.pk, {'active': False}),
            (ChallengeSubcategory, self.subcategory.pk, {'active': False}),
            (CourseCategory, self.category.pk, {'activa': False}),
            (QuestionBank, self.bank.pk, {'status': QuestionBank.Status.ARCHIVED}),
            (QuestionBank, self.bank.pk, {'status': QuestionBank.Status.DRAFT}),
            (Question, self.version.question_id, {'status': Question.Status.ARCHIVED}),
            (QuestionVersion, self.version.pk, {'is_published': False}),
            (get_user_model(), self.curator.pk, {'is_active': False}),
            (get_user_model(), self.curator.pk, {'is_superuser': False}),
        ):
            with self.subTest(model=model, values=values), transaction.atomic():
                model.objects.filter(pk=pk).update(**values)
                self.assertEqual(self.candidates(), [])
                transaction.set_rollback(True)

    def test_source_requires_source_course_permission_and_is_unique_per_subcategory(self):
        self.level.approved_by = self.outsider
        with self.assertRaises(ValidationError):
            self.level.save()
        with self.assertRaises(ValidationError):
            SubcategoryQuestionSource.objects.create(
                subcategory=self.subcategory, approved_by=self.curator,
            )

    def test_random_selection_without_repetition_and_explicit_exhaustion(self):
        another = self.add_question()
        with patch('apps.duckyclash.questions.secrets.SystemRandom') as random:
            random.return_value.shuffle.side_effect = lambda items: items.reverse()
            first = self.prepare()
        random.return_value.shuffle.assert_called_once()
        self.assertEqual(first.version_id, another.current_version_id)
        self.assertEqual(self.prepare().pk, first.pk)
        self.close(first)
        second = self.prepare(2)
        self.assertEqual(second.version_id, self.version.pk)
        self.close(second)
        with self.assertRaises(KnowledgeUnavailable):
            self.prepare(3)

    def test_presented_round_keeps_its_private_revision_after_editor_changes_bank(self):
        round_question = self.prepare()
        question = self.version.question
        question.create_version(
            prompt='Cambio posterior', options=['E', 'F', 'G', 'H'], correct_answers=['E'], published=True,
        )
        self.assertTrue(self.answer(round_question, text='A').is_correct)
        self.assertEqual(round_question.source_version, str(self.version.pk))
        self.assertNotIn('validation', self.payload(round_question))
        self.client.force_login(self.creator)
        response = self.client.get(reverse('duckyclash:current_question', args=[self.duel.pk]))
        self.assertEqual(response.status_code, 200)
        for private_text in ('correct_answers', 'PRIVATE-EXPLANATION-SECRET', 'PRIVATE-VALIDATION-KEY'):
            self.assertNotContains(response, private_text)

    def test_admin_approval_is_restricted_and_reuses_lms_editor(self):
        url = reverse('admin:duckyclash_subcategoryquestionsource_change', args=[self.source.pk])
        self.client.force_login(self.curator)
        response = self.client.get(url)
        self.assertContains(response, reverse('lms:knowledge_bank', kwargs={
            'course_id': self.course.pk, 'bank_id': self.bank.pk,
        }))
        self.outsider.is_staff = True
        self.outsider.save(update_fields=['is_staff'])
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(url).status_code, 403)
        change = reverse('admin:duckyclash_subcategoryquestionsource_change', args=[self.source.pk])
        self.assertEqual(self.client.post(change, {
            'subcategory': self.subcategory.pk, 'bank': self.bank.pk, 'active': 'on',
        }).status_code, 403)

    def test_superuser_can_link_a_new_subcategory_from_admin_with_implicit_approval(self):
        subcategory = ChallengeSubcategory.objects.create(category=self.category, name='Nueva fuente')
        self.client.force_login(self.curator)
        response = self.client.post(reverse('admin:duckyclash_subcategoryquestionsource_add'), {
            'subcategory': subcategory.pk, 'active': 'on', '_save': 'Guardar',
            'levels-TOTAL_FORMS': '1', 'levels-INITIAL_FORMS': '0',
            'levels-MIN_NUM_FORMS': '0', 'levels-MAX_NUM_FORMS': '1000',
            'levels-0-reference': '1/5', 'levels-0-bank': self.bank.pk, 'levels-0-active': 'on',
        })
        self.assertEqual(response.status_code, 302)
        source = SubcategoryQuestionSource.objects.get(subcategory=subcategory)
        self.assertEqual(source.approved_by, self.curator)
        self.assertEqual(len(self.candidates(subcategory)), 1)
