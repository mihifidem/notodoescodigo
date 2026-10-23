from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ExistingCVProfileMigrationTests(TransactionTestCase):
    migrate_from = [('curriculum', '0007_cvprofile_rama_principal')]
    migrate_to = [('curriculum', '0013_ats_verification')]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        user = get_user_model().objects.create_user(
            username='legacy-cv-user',
            email='legacy@example.test',
            password='test-password-123',
        )
        LegacyCV = old_apps.get_model('curriculum', 'CurriculumVitae')
        LegacyPersonalInfo = old_apps.get_model('curriculum', 'PersonalInfo')
        LegacyProfile = old_apps.get_model('curriculum', 'CVProfile')
        cv = LegacyCV.objects.create(user_id=user.pk)
        LegacyPersonalInfo.objects.create(
            cv_id=cv.pk,
            nombre='Lucía',
            apellidos='Legado',
            telefono1='611222333',
            email_profesional1='privado@example.test',
            ciudad='Madrid',
            pais='España',
        )
        self.legacy_profile_pk = LegacyProfile.objects.create(
            user_id=user.pk,
            cv_id=cv.pk,
            nombre='Perfil legado',
            slug='perfil-legado',
            template='tech',
            is_public=True,
        ).pk
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_public_profile_keeps_content_and_gets_immutable_published_revision(self):
        apps = MigrationExecutor(connection).loader.project_state(self.migrate_to).apps
        Profile = apps.get_model('curriculum', 'CVProfile')
        ProfileTemplate = apps.get_model('curriculum', 'ProfileTemplate')
        Revision = apps.get_model('curriculum', 'CVProfileRevision')
        profile = Profile.objects.get(pk=self.legacy_profile_pk)
        self.assertTrue(profile.is_public)
        self.assertEqual(profile.status, 'published')
        self.assertEqual(profile.published_snapshot['name'], 'Lucía Legado')
        self.assertEqual(profile.published_snapshot['email'], '')
        self.assertEqual(profile.published_snapshot['phone'], '')
        self.assertEqual(profile.published_template_id, profile.profile_template_id)
        self.assertEqual(
            ProfileTemplate.objects.get(pk=profile.published_template_id).code,
            'dev-studio',
        )
        self.assertEqual(Revision.objects.filter(profile_id=profile.pk).count(), 1)
        Configuration = apps.get_model('curriculum', 'LetterConfiguration')
        Letter = apps.get_model('curriculum', 'CoverLetter')
        self.assertEqual(Configuration.objects.filter(version=1).count(), 1)
        self.assertFalse(Letter.objects.exists())
        ATSConfiguration = apps.get_model('curriculum', 'ATSConfiguration')
        ATSReport = apps.get_model('curriculum', 'ATSReport')
        self.assertEqual(ATSConfiguration.objects.filter(version=1).count(), 1)
        self.assertFalse(ATSReport.objects.exists())
