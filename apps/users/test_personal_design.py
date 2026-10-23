from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class PersonalTechDesignTests(TestCase):
    def test_personal_pages_load_tech_styles_without_changing_routes(self):
        user = get_user_model().objects.create_user(username='personal-design')
        self.client.force_login(user)
        pages = (
            ('users:dashboard', 'dashboard-title', 'dashboard-tech.css'),
            ('gamification:insignias', 'badges-heading', 'badges.css'),
            ('users:progress', 'progress-heading', 'personal-tech.css'),
            ('gamification:ranking', 'rankings-heading', 'personal-tech.css'),
            ('certifications:list', 'certifications-heading', 'personal-tech.css'),
            ('curriculum:dashboard', 'cv-heading', 'personal-tech.css'),
            ('curriculum:profile_list', 'profiles-heading', 'personal-tech.css'),
            ('curriculum:template_catalog', 'templates-heading', 'personal-tech.css'),
            ('curriculum:letter_list', 'Mis cartas de', 'personal-tech.css'),
            ('curriculum:ats_list', 'Verificar', 'personal-tech.css'),
        )
        for route, heading, stylesheet in pages:
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'billing-hero')
                self.assertContains(response, heading)
                self.assertContains(response, 'css/billing.css')
                self.assertContains(response, f'css/{stylesheet}')

    def test_public_catalogs_keep_visitor_explanations(self):
        for route, explanation in (
            ('users:dashboard', 'Vista de demostración para visitantes'),
            ('gamification:insignias', 'Catálogo público'),
            ('gamification:ranking', 'Tu progreso personal no depende de participar'),
            ('users:progress', 'Demostración fija'),
            ('curriculum:template_catalog', 'La demostración no guarda datos'),
        ):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, explanation)
                self.assertContains(response, 'billing-hero')

    def test_dashboard_section_navigation_keeps_existing_destinations(self):
        user = get_user_model().objects.create_user(username='dashboard-design')
        self.client.force_login(user)
        response = self.client.get(reverse('users:dashboard'))
        self.assertContains(response, 'ducky-dashboard')
        self.assertContains(response, 'aria-label="Secciones de Ducky Me"')
        for section in ('my-progress', 'courses-title', 'achievements-title', 'results-title', 'personal-goals', 'ranking-participation'):
            with self.subTest(section=section):
                self.assertContains(response, f'href="#{section}"')
                self.assertContains(response, f'id="{section}"')

    def test_profile_tech_design_preserves_private_access_and_account_actions(self):
        user = get_user_model().objects.create_user(username='profile-design')
        other = get_user_model().objects.create_user(username='other-profile-design')
        url = reverse('users:perfil', kwargs={'username': user.username})
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(other)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(user)
        response = self.client.get(url)
        self.assertContains(response, 'profile-tech.css')
        self.assertContains(response, 'billing-hero')
        self.assertContains(response, 'id="profile-heading"')
        self.assertContains(response, 'id="deleteAccountConfirm"')
        self.assertContains(response, 'id="deleteAccountTrigger"')
        self.assertContains(response, f'action="{reverse("users:borrar_cuenta")}"')
        self.assertContains(response, reverse('users:editar_perfil'))

    def test_public_collection_tech_design_and_empty_state(self):
        user = get_user_model().objects.create_user(username='public-design')
        response = self.client.get(reverse('gamification:coleccion_publica', args=[user.username]))
        self.assertContains(response, 'billing-hero')
        self.assertContains(response, 'css/badges.css')
        self.assertContains(response, 'id="collection-heading"')
        self.assertContains(response, 'badges-empty')
        self.assertContains(response, 'Evidencias y motivos permanecen privados.')

    def test_edit_profile_tech_design_keeps_form_and_avatar_controls(self):
        user = get_user_model().objects.create_user(username='edit-profile-design')
        url = reverse('users:editar_perfil')
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(user)
        response = self.client.get(url)
        self.assertContains(response, 'profile-tech.css')
        self.assertContains(response, 'billing-hero')
        self.assertContains(response, 'id="edit-profile-heading"')
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        for control in ('select-avatar-btn', 'generar-ducky-btn', 'confirmar-ducky-btn', 'ducky-preview-image'):
            self.assertContains(response, f'id="{control}"')
        self.assertContains(response, reverse('users:perfil', kwargs={'username': user.username}))

    def test_profile_shows_owners_effective_plan_instead_of_legacy_role(self):
        from datetime import timedelta
        from django.utils import timezone
        from apps.users.billing_models import MembershipContract

        owner = get_user_model().objects.create_user(username='profile-plan')
        viewer = get_user_model().objects.create_user(username='profile-admin', is_staff=True)
        self.client.force_login(viewer)
        url = reverse('users:perfil', kwargs={'username': owner.username})
        self.assertContains(self.client.get(url), 'aria-label="Plan del perfil FREE"')
        contract = MembershipContract.objects.create(
            user=owner, plan='plus', status='active', billing_period='monthly',
            contracted_price='6.90', starts_at=timezone.now() - timedelta(minutes=1),
            period_ends_at=timezone.now() + timedelta(days=30),
            idempotency_key='profile-plan-test',
        )
        for plan in ('plus', 'pro'):
            contract.plan = plan
            contract.save()
            response = self.client.get(url)
            self.assertContains(response, f'aria-label="Plan del perfil {plan.upper()}"')
            self.assertNotContains(response, 'Rol:')
            self.assertContains(response, f'>{owner.nivel}</span>')
        contract.period_ends_at = timezone.now() - timedelta(seconds=1)
        contract.save()
        self.assertContains(self.client.get(url), 'aria-label="Plan del perfil FREE"')
