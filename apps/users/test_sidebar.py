from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class SidebarNavigationTests(TestCase):
    def sidebar(self, user):
        self.client.force_login(user)
        response = self.client.get(reverse('users:access_summary'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'css/sidebar-tech.css')
        return BeautifulSoup(response.content, 'html.parser').select_one('#sidebar')

    def test_four_main_blocks_and_accessible_controls(self):
        sidebar = self.sidebar(get_user_model().objects.create_user('sidebar-areas'))
        accordions = sidebar.select('.sidebar-accordion')
        self.assertEqual(len(accordions), 4)
        self.assertEqual(
            [item.select_one('.accordion-button').get_text(strip=True) for item in accordions],
            ['Ducky Profile', 'Ducky Academy', 'Ducky Arenas', 'Ducky Jobs'],
        )
        for item in accordions:
            trigger = item.select_one('button')
            target = sidebar.select_one(trigger['data-bs-target'])
            self.assertIsNotNone(target)
            self.assertEqual(trigger['aria-controls'], target['id'])
            self.assertIn(trigger['aria-expanded'], ('true', 'false'))
        self.assertIsNone(sidebar.select_one('#duckyTalentAccordion'))
        self.assertNotIn('Cursos NTEC', sidebar.get_text())
        self.assertNotIn('Mi Perfil', sidebar.get_text())
        self.assertIn('Formación HUB', sidebar.get_text())

    def test_links_grouped_and_admin_hidden_for_regular_user(self):
        sidebar = self.sidebar(get_user_model().objects.create_user('sidebar-regular'))
        for container, routes in (
            ('#duckyProfileCollapse', ('curriculum:dashboard', 'nexus:catalog')),
            ('#duckyAcademyCollapse', ('lms:dashboard', 'lms:campus', 'certifications:list')),
            ('#duckyArenasCollapse', ('ducky_arenas:home', 'duckyclash:home')),
        ):
            links = [a['href'] for a in sidebar.select(f'{container} a')]
            for route in routes:
                self.assertIn(reverse(route), links)
        links = [a.get('href') for a in sidebar.select('a')]
        self.assertNotIn(reverse('admin:index'), links)
        self.assertNotIn(reverse('data_admin:content_backups'), links)
        self.assertNotIn(reverse('lms:course_create'), links)
        self.assertIn(reverse('users:billing'), links)

    def test_jobs_labels_match_requested_names_and_keep_destinations(self):
        sidebar = self.sidebar(get_user_model().objects.create_user('sidebar-jobs-labels'))
        links = sidebar.select('#duckyJobsCollapse a')
        self.assertEqual(
            [(link.get_text(strip=True), link['href']) for link in links],
            [
                ('Empleos para ti', reverse('updates:empleos_para_ti')),
                ('Impulsa tu carrera', reverse('updates:impulsa_tu_carrera')),
                ('Entrena tu entrevista', reverse('updates:entrena_tu_entrevista')),
                ('Explora oportunidades', reverse('updates:explora_oportunidades')),
                ('Descubre empresas', reverse('updates:descubre_empresas')),
            ],
        )

    def test_profile_labels_match_requested_names_and_keep_destinations(self):
        sidebar = self.sidebar(get_user_model().objects.create_user('sidebar-profile-labels'))
        self.assertEqual(
            [(link.get_text(strip=True), link['href']) for link in sidebar.select('#duckyProfileCollapse a')],
            [
                ('Mi currículum', reverse('curriculum:dashboard')),
                ('CV online', reverse('curriculum:profile_list')),
                ('Plantillas de CV', reverse('curriculum:template_catalog')),
                ('Exportar en PDF', reverse('curriculum:profile_list')),
                ('Carta de presentación', reverse('curriculum:letter_list')),
                ('Analizar mi CV', reverse('curriculum:ats_list')),
                ('Nexus DNA · Soft Skills', reverse('nexus:catalog')),
                ('Tech DNA · Hard Skills', reverse('updates:tech_dna')),
            ],
        )

    def test_academy_labels_match_requested_names_and_keep_destinations(self):
        sidebar = self.sidebar(get_user_model().objects.create_user('sidebar-academy-labels'))
        self.assertEqual(
            [(link.get_text(strip=True), link['href']) for link in sidebar.select('#duckyAcademyCollapse a')],
            [
                ('Mis cursos', reverse('lms:campus')),
                ('Campus virtual', reverse('lms:dashboard')),
                ('Mis certificaciones', reverse('certifications:list')),
                ('Formación HUB', reverse('updates:formacion_hub')),
            ],
        )

    def test_staff_admin_destinations_stay_outside_main_groups(self):
        sidebar = self.sidebar(get_user_model().objects.create_superuser(
            'sidebar-admin', 'sidebar@example.test', 'test-password',
        ))
        for route in ('admin:index', 'data_admin:content_backups', 'nexus:dashboard'):
            link = sidebar.find('a', href=reverse(route))
            self.assertIsNotNone(link)
            self.assertIsNone(link.find_parent(class_='sidebar-accordion'))

    def test_student_bug_link_keeps_role_gate(self):
        sidebar = self.sidebar(get_user_model().objects.create_user('sidebar-student', role='alumno'))
        self.assertIsNotNone(sidebar.find('a', href=reverse('bug_reports:list')))
        sidebar = self.sidebar(get_user_model().objects.create_user('sidebar-user'))
        self.assertIsNone(sidebar.find('a', href=reverse('bug_reports:list')))

    def test_shop_is_in_top_navbar_and_only_ducky_me_button_remains(self):
        user = get_user_model().objects.create_user('sidebar-shop')
        sidebar = self.sidebar(user)
        self.assertNotIn('DuckyShop', sidebar.get_text())
        buttons = sidebar.select('#duckyMePanel > div:first-child button')
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].get_text(strip=True), 'Ducky Me')
        response = self.client.get(reverse('users:access_summary'))
        soup = BeautifulSoup(response.content, 'html.parser')
        shop = soup.select_one('.navbar .ms-auto #navbarDuckyShop')
        self.assertIsNotNone(shop)
        self.assertEqual(shop['href'], reverse('updates:duckyshop'))
        self.assertEqual(len(soup.select('#navbarDuckyShop')), 1)
        tech2hand = shop.find_next_sibling('a')
        self.assertEqual(tech2hand['id'], 'navbarTech2Hand')
        self.assertEqual(tech2hand['href'], reverse('updates:tech2hand'))
        self.assertEqual(tech2hand['class'], shop['class'])
        self.assertEqual(len(soup.select('#navbarTech2Hand')), 1)
        self.assertNotIn('Tech2Hand', sidebar.get_text())

    def test_top_navbar_labels_share_shop_style_and_destinations(self):
        self.sidebar(get_user_model().objects.create_user('navbar-labels'))
        response = self.client.get(reverse('users:access_summary'))
        soup = BeautifulSoup(response.content, 'html.parser')
        links = soup.select('.navbar .navbar-ducky-link')
        self.assertEqual(
            [(link.get_text(strip=True), link['href']) for link in links],
            [
                ('Blog Tips', reverse('blog:list')),
                ('Biblioteca de prompts', reverse('prompts:list')),
                ('NotebookLM', reverse('prompts:estilos')),
                ('Links web', reverse('enlaces:list')),
                ('MiniApps', reverse('devtools:hub')),
                ('DuckyShop', reverse('updates:duckyshop')),
                ('Tech2Hand', reverse('updates:tech2hand')),
            ],
        )
        self.assertNotIn('btn-sm', soup.select_one('#duckyMeToggle')['class'])
