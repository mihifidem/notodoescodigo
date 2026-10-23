import tempfile
from io import BytesIO

from PIL import Image
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.deletion import ProtectedError
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from . import test_private_challenges as private_tests
from apps.courses.models import CourseCategory, CourseSector
from .forms import LandscapeCreateForm, SubcategoryCreateForm, SubcategoryAdminForm, PrivateChallengeForm
from .models import Challenge, ChallengeSubcategory, Landscape
from .characters import era_for_xp


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class ChallengeCatalogTests(TestCase):
    post_data = private_tests.PrivateChallengeTests.post_data

    def setUp(self):
        private_tests.PrivateChallengeTests.setUp(self)
        self.sector = CourseSector.objects.create(nombre='Tecnología')
        self.category.sector = self.sector
        self.category.save(update_fields=['sector'])

    def grant(self, codename):
        self.creator.user_permissions.add(Permission.objects.get(
            content_type__app_label='duckyclash', codename=codename,
        ))

    def image(self, name='scene.jpg', size=(10, 10), format='PNG'):
        stream = BytesIO()
        Image.new('RGB', size).save(stream, format=format)
        return SimpleUploadedFile(name, stream.getvalue(), content_type='image/png')

    def test_admin_creates_category_inline_and_keeps_challenge_inputs(self):
        from apps.users.models import AccessRoleAssignment
        AccessRoleAssignment.objects.create(user=self.creator, role='admin')
        response = self.client.post(self.create_url, self.post_data(
            catalog_action='category', **{
                'new_category-sector': self.sector.pk,
                'new_category-nombre': 'Nueva categoría de retos',
                'new_category-descripcion': 'Catálogo compartido',
            },
        ))
        self.assertEqual(response.status_code, 200)
        category = CourseCategory.objects.get(nombre='Nueva categoría de retos')
        self.assertEqual(category.sector_id, self.sector.pk)
        self.assertEqual(response.context['form'].data['category'], str(category.pk))
        self.assertEqual(response.context['form'].data['sector'], str(self.sector.pk))
        self.assertEqual(str(response.context['form'].data['stake']), '20')
        self.assertFalse(Challenge.objects.exists())
        self.assertEqual(private_tests.FakeBankGateway.calls, [])
        self.assertContains(response, 'Guardar categoría')
        self.assertContains(response, 'catalog-selects.js')

    def test_non_admin_cannot_create_category(self):
        response = self.client.post(self.create_url, {
            'catalog_action': 'category', 'new_category-sector': self.sector.pk,
            'new_category-nombre': 'Forbidden',
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(CourseCategory.objects.filter(nombre='Forbidden').exists())

    def test_admin_creates_subcategory_without_model_permission(self):
        from apps.users.models import AccessRoleAssignment
        AccessRoleAssignment.objects.create(user=self.creator, role='admin')
        response = self.client.post(reverse('duckyclash:open_create'), self.post_data(
            catalog_action='subcategory', **{
                'new_subcategory-sector': self.sector.pk,
                'new_subcategory-category': self.category.pk,
                'new_subcategory-name': 'Subcategoría ADMIN',
            },
        ))
        self.assertEqual(response.status_code, 200)
        entry = ChallengeSubcategory.objects.get(name='Subcategoría ADMIN')
        self.assertEqual(response.context['form'].data['subcategory'], str(entry.pk))
        self.assertEqual(response.context['form'].data['sector'], str(self.sector.pk))
        self.assertEqual(private_tests.FakeBankGateway.calls, [])
        self.assertFalse(Challenge.objects.exists())

    def test_category_duplicate_or_missing_sector_is_not_saved(self):
        from apps.users.models import AccessRoleAssignment
        AccessRoleAssignment.objects.create(user=self.creator, role='admin')
        for sector, name, field in [
            ('', 'Sin ramo', 'sector'),
            (self.sector.pk, self.category.nombre, 'nombre'),
        ]:
            with self.subTest(field=field):
                response = self.client.post(self.create_url, self.post_data(
                    catalog_action='category', **{
                        'new_category-sector': sector, 'new_category-nombre': name,
                    },
                ))
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.context['category_form'].errors)
                self.assertEqual(CourseCategory.objects.count(), 1)
                self.assertFalse(Challenge.objects.exists())
                self.assertEqual(private_tests.FakeBankGateway.calls, [])

    def test_challenge_rejects_category_from_another_sector(self):
        other = CourseSector.objects.create(nombre='Other sector')
        form = PrivateChallengeForm(self.post_data(sector=other.pk), actor=self.creator)
        self.assertFalse(form.is_valid())
        self.assertIn('category', form.errors)

    def test_option_parents_allow_browser_to_filter_hierarchy(self):
        response = self.client.get(self.create_url)
        self.assertContains(response, f'data-parent="{self.sector.pk}"')
        self.assertContains(response, f'data-parent="{self.category.pk}"')
        self.assertContains(response, 'id_sector')
        response = self.client.get(reverse('duckyclash:open_create'))
        self.assertContains(response, 'catalog-selects.js')

    @override_settings(
        DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.test_private_challenges.FakeBankGateway',
        DUCKYCLASH_PAYMENT_GATEWAY='',
    )
    def test_edit_infers_sector_and_loads_dependent_selects(self):
        challenge = private_tests.PrivateChallengeTests.create(self)
        response = self.client.get(reverse('duckyclash:challenge_edit', args=[challenge.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form'].initial['sector'], self.sector.pk)
        self.assertEqual(response.context['form']['category'].value(), self.category.pk)
        self.assertContains(response, 'catalog-selects.js')

    def test_permissions_and_csrf(self):
        for action, permission in [('subcategory', 'add_challengesubcategory'), ('landscape', 'add_landscape')]:
            self.assertEqual(self.client.post(self.create_url, {'catalog_action': action}).status_code, 403)
            self.grant(permission)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.creator)
        self.assertEqual(strict.post(self.create_url, {'catalog_action': 'subcategory'}).status_code, 403)
        self.assertContains(self.client.get(self.create_url), 'Guardar subcategoría')
        self.assertContains(self.client.get(reverse('duckyclash:open_create')), 'Guardar paisaje')
        self.assertEqual(self.client.post(self.create_url, {'catalog_action': 'unknown'}).status_code, 400)

    def test_subcategory_saved_selected_without_challenge_or_bank_call(self):
        self.grant('add_challengesubcategory')
        response = self.client.post(self.create_url, self.post_data(
            catalog_action='subcategory', **{
                'new_subcategory-category': self.category.pk, 'new_subcategory-name': 'Nueva rama',
                'new_subcategory-sector': self.sector.pk,
            },
        ))
        self.assertEqual(response.status_code, 200)
        entry = ChallengeSubcategory.objects.get(name='Nueva rama')
        self.assertEqual(response.context['form'].data['subcategory'], str(entry.pk))
        self.assertEqual(str(response.context['form'].data['stake']), '20')
        self.assertEqual(private_tests.FakeBankGateway.calls, [])
        self.assertFalse(Challenge.objects.exists())
        duplicate = SubcategoryCreateForm({'sector': self.sector.pk, 'category': self.category.pk, 'name': 'Nueva rama'})
        self.assertFalse(duplicate.is_valid())

    def test_sector_category_subcategory_hierarchy_and_protected_deletion(self):
        second = CourseCategory.objects.create(nombre='Otra categoría', sector=self.sector)
        branch = ChallengeSubcategory.objects.create(category=second, name='Otra rama')
        self.assertEqual(set(self.sector.categories.all()), {self.category, second})
        self.assertEqual(branch.sector, self.sector)
        self.assertEqual(self.subcategory.sector, self.sector)
        with self.assertRaises(ProtectedError):
            self.sector.delete()
        with self.assertRaises(ProtectedError):
            second.delete()
        other_sector = CourseSector.objects.create(nombre='Salud')
        second.sector = other_sector
        second.save(update_fields=['sector'])
        branch.refresh_from_db()
        self.assertEqual(branch.sector, other_sector)

    def test_subcategory_forms_require_matching_sector(self):
        other_sector = CourseSector.objects.create(nombre='Salud')
        for form_class in (SubcategoryCreateForm, SubcategoryAdminForm):
            for sector in ('', other_sector.pk):
                form = form_class({'sector': sector, 'category': self.category.pk, 'name': 'Rama'})
                self.assertFalse(form.is_valid())
                self.assertIn('sector' if sector == '' else 'category', form.errors)
            form = form_class({'sector': self.sector.pk, 'category': self.category.pk, 'name': 'Rama'})
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form_class(instance=self.subcategory).initial['sector'], self.sector.pk)
        legacy = CourseCategory.objects.create(nombre='Sin asignar')
        form = SubcategoryCreateForm({'sector': self.sector.pk, 'category': legacy.pk, 'name': 'Rama'})
        self.assertFalse(form.is_valid())
        self.assertIn('category', form.errors)

    def test_admin_related_add_buttons_respect_catalog_permissions(self):
        model_admin = admin.site._registry[ChallengeSubcategory]
        request = RequestFactory().get(reverse('admin:duckyclash_challengesubcategory_add'))
        request.user = self.creator
        form = model_admin.get_form(request)()
        self.assertNotIn('add_id_sector', str(form['sector']))
        self.assertNotIn('add_id_category', str(form['category']))
        self.creator.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='courses',
            codename__in=['add_coursesector', 'add_coursecategory'],
        ))
        request.user = type(self.creator).objects.get(pk=self.creator.pk)
        form = model_admin.get_form(request)()
        self.assertIn('add_id_sector', str(form['sector']))
        self.assertIn('add_id_category', str(form['category']))
        self.assertIn(reverse('admin:courses_coursesector_add'), str(form['sector']))
        self.assertIn(reverse('admin:courses_coursecategory_add'), str(form['category']))
        request.user = self.creator
        form = model_admin.get_form(request)()
        self.assertNotIn('add_id_sector', str(form['sector']))

    def test_mismatched_sector_cannot_create_subcategory_through_view(self):
        self.grant('add_challengesubcategory')
        other_sector = CourseSector.objects.create(nombre='Salud')
        response = self.client.post(self.create_url, self.post_data(
            catalog_action='subcategory', **{
                'new_subcategory-sector': other_sector.pk,
                'new_subcategory-category': self.category.pk,
                'new_subcategory-name': 'No guardar',
            },
        ))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'La categoría no pertenece al sector seleccionado.', status_code=400)
        self.assertFalse(ChallengeSubcategory.objects.filter(name='No guardar').exists())

    def test_landscape_upload_validates_and_uses_safe_filename(self):
        self.grant('add_landscape')
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            response = self.client.post(reverse('duckyclash:open_create'), {
                'catalog_action': 'landscape', 'new_landscape-name': 'Bosque',
                'new_landscape-era': era_for_xp(self.creator.puntos_totales).code,
                'new_landscape-image': self.image(),
            })
            self.assertEqual(response.status_code, 200)
            entry = Landscape.objects.get(name='Bosque')
            self.assertTrue(entry.image.name.endswith('.png'))
            self.assertNotIn('.html', entry.image.name)
            self.assertTrue(entry.image.storage.exists(entry.image.name))
            self.assertEqual(response.context['form'].data['landscape'], str(entry.pk))
        invalid = LandscapeCreateForm({'name': 'Invalid'}, {
            'image': SimpleUploadedFile('image.png', b'not an image'),
        })
        self.assertFalse(invalid.is_valid())
        oversized = LandscapeCreateForm({'name': 'Large'}, {'image': self.image(size=(4097, 1))})
        self.assertFalse(oversized.is_valid())
        self.assertIn('image', oversized.errors)

    def test_catalog_fields_do_not_require_browser_input_for_publishing(self):
        self.assertNotIn('required', SubcategoryCreateForm()['name'].as_widget())
        self.assertNotIn('required', LandscapeCreateForm()['image'].as_widget())
