from io import BytesIO

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import ValidationError
from pypdf import PdfReader

from apps.curriculum.models import (
    CVProfile,
    CVProfileRevision,
    CurriculumVitae,
    ProfileTemplate,
    ProfileVisit,
)
from apps.curriculum.forms import CVProfileForm
from apps.curriculum.services.profiles import (
    create_profile,
    duplicate_profile,
    profile_limit,
    publish_profile,
    reconcile_published_profiles,
    snapshot_profile,
    template_access,
)
from apps.users.models import AccessMembership, PurchasedEntitlement, RamaProfesional, User


class CVProfileFeatureTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='cv-owner', email='owner@example.test', password='test-password-123',
        )
        self.cv = CurriculumVitae.objects.create(user=self.user)
        self.basic = ProfileTemplate.objects.get(code='basic', version=1)
        self.theme = ProfileTemplate.objects.get(code='dev-studio', version=1)

    def set_plan(self, plan):
        membership, _ = AccessMembership.objects.get_or_create(user=self.user)
        membership.plan = plan
        membership.status = AccessMembership.Status.ACTIVE
        membership.billing_period = '' if plan == AccessMembership.Plan.FREE else 'monthly'
        membership.starts_at = timezone.now()
        membership.ends_at = None
        membership.save()

    def make_profile(self, name='Mi CV', template=None):
        return CVProfile(
            user=self.user,
            cv=self.cv,
            nombre=name,
            slug='',
            profile_template=template or self.basic,
        )

    def test_migration_seed_has_ten_versioned_designs(self):
        self.assertEqual(
            set(ProfileTemplate.objects.filter(version=1).values_list('code', flat=True)),
            {
                'basic', 'minimalist', 'professional', 'classic', 'dev-studio',
                'conversation', 'showcase', 'crm-workspace', 'superoffer', 'matrix',
            },
        )
        self.assertEqual(
            ProfileTemplate.objects.filter(version=1, status=ProfileTemplate.Status.PUBLISHED).count(),
            10,
        )

    def test_matrix_is_premium_and_respects_publication_snapshot(self):
        matrix = ProfileTemplate.objects.get(code='matrix', version=1)
        self.assertEqual(matrix.allowed_plans, ['pro'])
        for plan in ('free', 'plus'):
            self.set_plan(plan)
            self.assertFalse(template_access(self.user, matrix).allowed)
        self.set_plan('pro')
        self.assertTrue(template_access(self.user, matrix).allowed)
        profile = self.make_profile(template=matrix)
        profile.display_name = 'Nombre publicado'
        profile.contact_email = 'private@example.test'
        profile.show_email = False
        profile = create_profile(profile, self.user)
        profile = publish_profile(profile, self.user)
        profile.display_name = 'Nombre borrador'
        profile.save()
        response = self.client.get(reverse('curriculum:public_cv', kwargs={'slug': profile.slug}))
        self.assertTemplateUsed(response, 'curriculum/cv_templates/matrix.html')
        self.assertContains(response, 'Nombre publicado')
        self.assertNotContains(response, 'Nombre borrador')
        self.assertNotContains(response, 'private@example.test')
        self.set_plan('free')
        response = self.client.get(reverse('curriculum:public_cv', kwargs={'slug': profile.slug}))
        self.assertTemplateUsed(response, 'curriculum/cv_templates/catalog_profile.html')

    def test_general_templates_are_free_and_themes_require_plus(self):
        self.set_plan(AccessMembership.Plan.FREE)
        general = ProfileTemplate.objects.get(code='classic', version=1)
        self.assertTrue(template_access(self.user, general).allowed)
        self.assertFalse(template_access(self.user, self.theme).allowed)
        self.set_plan(AccessMembership.Plan.PLUS)
        self.assertTrue(template_access(self.user, self.theme).allowed)

    def test_tampered_template_form_cannot_apply_a_theme_or_publish(self):
        self.set_plan(AccessMembership.Plan.FREE)
        form = CVProfileForm(
            data={
                'nombre': 'Perfil manipulado',
                'slug': 'perfil-manipulado',
                'profile_template': self.theme.pk,
                'brand_color': '#1d4ed8',
                'is_public': 'on',
            },
            user=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('profile_template', form.errors)
        self.assertNotIn('is_public', form.fields)

    def test_purchased_theme_right_does_not_change_membership_plan(self):
        self.set_plan(AccessMembership.Plan.FREE)
        PurchasedEntitlement.objects.create(
            user=self.user,
            code='profile-template-dev-studio',
            source='test',
            source_reference='theme-order-1',
            idempotency_key='theme-order-1',
        )
        self.assertTrue(template_access(self.user, self.theme).allowed)
        self.assertEqual(self.user.access_membership.plan, AccessMembership.Plan.FREE)

    def test_profile_limits_and_purchased_extra_slot(self):
        self.set_plan(AccessMembership.Plan.FREE)
        self.assertEqual(profile_limit(self.user), 1)
        create_profile(self.make_profile('Primero'), self.user)
        with self.assertRaises(OverflowError):
            create_profile(self.make_profile('Segundo'), self.user)
        PurchasedEntitlement.objects.create(
            user=self.user,
            code='cv-profile-slot',
            source='test',
            source_reference='slot-order-1',
            idempotency_key='slot-order-1',
        )
        self.assertEqual(profile_limit(self.user), 2)
        create_profile(self.make_profile('Segundo'), self.user)

    def test_authenticated_profile_creation_preserves_free_limit(self):
        self.set_plan(AccessMembership.Plan.FREE)
        self.client.force_login(self.user)
        data = {
            'nombre': 'Perfil desde formulario',
            'slug': 'perfil-desde-formulario',
            'profile_template': self.basic.pk,
            'brand_color': '#1d4ed8',
        }
        response = self.client.post(reverse('curriculum:profile_create'), data)
        self.assertEqual(response.status_code, 302)
        profile = CVProfile.objects.get(user=self.user)
        self.assertRedirects(
            response,
            reverse('curriculum:profile_edit_items', kwargs={'pk': profile.pk}),
            fetch_redirect_response=False,
        )
        self.assertEqual(profile.cv_id, self.cv.pk)
        self.assertFalse(profile.is_public)
        response = self.client.post(reverse('curriculum:profile_create'), data)
        self.assertContains(response, 'Has alcanzado el límite de perfiles de tu plan.')
        self.assertEqual(CVProfile.objects.filter(user=self.user).count(), 1)

    def test_authenticated_profile_duplicate_publish_and_reconcile(self):
        self.set_plan(AccessMembership.Plan.PLUS)
        profile = create_profile(self.make_profile(), self.user)
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('curriculum:profile_duplicate', kwargs={'pk': profile.pk}),
        )
        self.assertRedirects(
            response, reverse('curriculum:profile_list'), fetch_redirect_response=False,
        )
        clone = CVProfile.objects.exclude(pk=profile.pk).get(user=self.user)
        self.assertFalse(clone.is_public)
        response = self.client.post(
            reverse('curriculum:profile_publish', kwargs={'pk': clone.pk}),
        )
        self.assertRedirects(
            response, reverse('curriculum:profile_list'), fetch_redirect_response=False,
        )
        clone.refresh_from_db()
        self.assertTrue(clone.is_public)
        self.assertEqual(CVProfileRevision.objects.filter(profile=clone).count(), 1)
        response = self.client.post(
            reverse('curriculum:profile_reconcile'), {'profiles': []},
        )
        self.assertRedirects(
            response, reverse('curriculum:profile_list'), fetch_redirect_response=False,
        )
        clone.refresh_from_db()
        self.assertFalse(clone.is_public)
        self.assertEqual(clone.status, CVProfile.Status.DRAFT)

    def test_plus_and_pro_profile_limits(self):
        for plan, expected in (
            (AccessMembership.Plan.PLUS, 3),
            (AccessMembership.Plan.PRO, 10),
        ):
            self.set_plan(plan)
            self.assertEqual(profile_limit(self.user), expected)
            for number in range(expected):
                create_profile(self.make_profile(f'{plan}-{number}'), self.user)
            with self.assertRaises(OverflowError):
                create_profile(self.make_profile('Fuera de límite'), self.user)
            CVProfile.objects.filter(user=self.user).delete()

    def test_duplication_is_private_independent_and_within_owner_limit(self):
        self.set_plan(AccessMembership.Plan.PLUS)
        source = create_profile(self.make_profile(), self.user)
        copy = duplicate_profile(source, self.user)
        self.assertNotEqual(source.pk, copy.pk)
        self.assertEqual(copy.status, CVProfile.Status.DRAFT)
        self.assertFalse(copy.is_public)
        self.assertEqual(copy.profile_template_id, source.profile_template_id)

    def test_published_version_uses_only_opted_in_contact_data(self):
        self.set_plan(AccessMembership.Plan.FREE)
        from apps.curriculum.models import PersonalInfo

        PersonalInfo.objects.create(
            cv=self.cv,
            nombre='Lucía',
            apellidos='Martín',
            telefono1='600000001',
            email_profesional1='private@example.test',
            ciudad='Madrid',
            pais='España',
        )
        profile = create_profile(self.make_profile(), self.user)
        profile.status = CVProfile.Status.PUBLISHED
        profile.is_public = True
        profile.published_snapshot = snapshot_profile(profile)
        profile.save()
        response = self.client.get(reverse('curriculum_public_cv', kwargs={'slug': profile.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Lucía Martín')
        self.assertNotContains(response, 'private@example.test')
        self.assertNotContains(response, '600000001')
        self.assertEqual(ProfileVisit.objects.filter(profile=profile).count(), 1)
        pdf_response = self.client.get(reverse('curriculum_cv_pdf', kwargs={'slug': profile.slug}))
        pdf_text = '\n'.join(page.extract_text() or '' for page in PdfReader(
            BytesIO(pdf_response.content),
        ).pages)
        self.assertNotIn('private@example.test', pdf_text)
        self.assertNotIn('600000001', pdf_text)

    def test_publish_freezes_a_separate_revision_until_republished(self):
        self.set_plan(AccessMembership.Plan.FREE)
        profile = self.make_profile()
        profile.display_name = 'Nombre inicial'
        profile = create_profile(profile, self.user)
        published = publish_profile(profile, self.user)
        self.assertEqual(published.published_snapshot['name'], 'Nombre inicial')
        self.assertEqual(CVProfileRevision.objects.filter(profile=profile).count(), 1)
        published.display_name = 'Nombre editado'
        published.save()
        response = self.client.get(reverse('curriculum_public_cv', kwargs={'slug': profile.slug}))
        self.assertContains(response, 'Nombre inicial')
        self.assertNotContains(response, 'Nombre editado')
        publish_profile(published, self.user)
        self.assertEqual(CVProfileRevision.objects.filter(profile=profile).count(), 2)

    def test_published_template_versions_are_immutable(self):
        original = self.basic
        original.name = 'Cambio no versionado'
        with self.assertRaises(ValidationError):
            original.save()
        version = ProfileTemplate.objects.create(
            code='basic',
            version=2,
            name='Básica renovada',
            description='Nueva versión.',
            category=ProfileTemplate.Category.GENERAL,
            schema_version=1,
            renderer='basic',
            allowed_plans=['free', 'plus', 'pro'],
            status=ProfileTemplate.Status.DRAFT,
        )
        self.assertEqual(version.version, 2)

    def test_visitors_can_view_catalog_and_fake_demo_without_creating_profiles(self):
        response = self.client.get(reverse('curriculum:template_catalog'))
        self.assertEqual(response.status_code, 200)
        for code in ProfileTemplate.objects.filter(status=ProfileTemplate.Status.PUBLISHED).values_list(
            'code', flat=True,
        ).distinct():
            demo = self.client.get(reverse('curriculum:template_preview', kwargs={'code': code}))
            self.assertEqual(demo.status_code, 200)
            self.assertContains(demo, 'Datos de ejemplo ficticios')
        self.assertEqual(CVProfile.objects.count(), 0)

    def test_ai_summary_is_unavailable_until_shared_ducky_energy_exists(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('curriculum:suggest_resumen_ia'))
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()['ok'])
        self.assertIn('Energía Ducky', response.json()['warning'])
        self.assertEqual(CurriculumVitae.objects.filter(user=self.user).count(), 1)

    def test_profile_edits_are_scoped_to_the_authenticated_owner(self):
        profile = create_profile(self.make_profile(), self.user)
        other = User.objects.create_user(username='other-cv', password='test-password-123')
        self.client.force_login(other)
        response = self.client.get(reverse('curriculum:profile_edit', kwargs={'pk': profile.pk}))
        self.assertEqual(response.status_code, 404)

    def test_private_profile_has_no_public_slug_or_pdf(self):
        profile = create_profile(self.make_profile(), self.user)
        self.assertEqual(
            self.client.get(reverse('curriculum_public_cv', kwargs={'slug': profile.slug})).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse('curriculum_cv_pdf', kwargs={'slug': profile.slug})).status_code,
            404,
        )
        owner_pdf = reverse('curriculum:profile_private_pdf', kwargs={'pk': profile.pk})
        self.assertEqual(self.client.get(owner_pdf).status_code, 302)
        self.client.force_login(self.user)
        private_pdf = self.client.get(owner_pdf)
        self.assertEqual(private_pdf.status_code, 200)
        self.assertEqual(private_pdf['Content-Type'], 'application/pdf')

    def test_pdf_is_a_real_document_from_the_published_snapshot(self):
        self.set_plan(AccessMembership.Plan.FREE)
        profile = create_profile(self.make_profile(), self.user)
        profile.status = CVProfile.Status.PUBLISHED
        profile.is_public = True
        profile.published_snapshot = {
            'name': 'Lucía Español',
            'profession': 'Desarrolladora',
            'summary': 'Diseño y programación.',
            'location': '', 'email': '', 'phone': '', 'photo': '',
            'experiences': [], 'education': [], 'training': [], 'skills': [],
            'languages': [], 'projects': [], 'achievements': [],
            'volunteering': [], 'links': [],
        }
        profile.save()
        response = self.client.get(reverse('curriculum_cv_pdf', kwargs={'slug': profile.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF-'))
        reader = PdfReader(BytesIO(response.content))
        self.assertIn('Lucía Español', reader.pages[0].extract_text())

    def test_long_pdf_is_paginated_and_keeps_spanish_text_and_links(self):
        profile = create_profile(self.make_profile(), self.user)
        profile.is_public = True
        profile.status = CVProfile.Status.PUBLISHED
        profile.published_snapshot = {
            'name': 'Lucía Español',
            'profession': 'Desarrolladora',
            'summary': 'Presentación profesional con acentos: programación, diseño y experiencia.',
            'location': '', 'email': '', 'phone': '', 'photo': '',
            'experiences': [
                {
                    'puesto': f'Puesto {index}',
                    'empresa': 'Empresa de ejemplo',
                    'fecha_inicio': '2020-01-01',
                    'fecha_fin': '2022-01-01',
                    'descripcion': 'Desarrollo de aplicaciones y documentación técnica. ' * 6,
                }
                for index in range(45)
            ],
            'education': [], 'training': [], 'skills': [], 'languages': [],
            'projects': [{'nombre': 'Proyecto', 'descripcion': 'Descripción',
                          'tecnologias': 'Django', 'link': 'https://example.test/proyecto'}],
            'achievements': [], 'volunteering': [],
            'links': [{'label': 'Portfolio', 'url': 'https://example.test'}],
        }
        profile.save()
        response = self.client.get(reverse('curriculum_cv_pdf', kwargs={'slug': profile.slug}))
        reader = PdfReader(BytesIO(response.content))
        text = '\n'.join(page.extract_text() or '' for page in reader.pages)
        self.assertGreaterEqual(len(reader.pages), 2)
        self.assertIn('Lucía Español', text)
        self.assertIn('programación', text)

    def test_downgrade_keeps_unselected_profiles_as_private_drafts(self):
        self.set_plan(AccessMembership.Plan.PLUS)
        profiles = []
        for number in range(3):
            profile = create_profile(self.make_profile(f'Perfil {number}'), self.user)
            profile.status = CVProfile.Status.PUBLISHED
            profile.is_public = True
            profile.save()
            profiles.append(profile)
        self.set_plan(AccessMembership.Plan.FREE)
        reconcile_published_profiles(self.user, [profiles[0].pk])
        profiles[0].refresh_from_db()
        profiles[1].refresh_from_db()
        profiles[2].refresh_from_db()
        self.assertTrue(profiles[0].is_public)
        self.assertEqual(profiles[1].status, CVProfile.Status.DRAFT)
        self.assertFalse(profiles[1].is_public)
        self.assertEqual(CVProfile.objects.filter(user=self.user).count(), 3)

    def test_changing_account_branch_does_not_change_profile_branch(self):
        informatica = RamaProfesional.objects.get(code='informatica')
        marketing = RamaProfesional.objects.get(code='marketing')
        profile = create_profile(self.make_profile(), self.user)
        profile.rama_principal = informatica
        profile.save()
        self.user.rama_principal = marketing
        self.user.save(update_fields=['rama_principal'])
        profile.refresh_from_db()
        self.assertEqual(profile.rama_principal_id, informatica.pk)
        self.assertEqual(self.user.access_membership.plan, AccessMembership.Plan.FREE)
