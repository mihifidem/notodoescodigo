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
from .forms import LandscapeCreateForm, SubcategoryCreateForm, SubcategoryAdminForm
from .models import Challenge, ChallengeSubcategory, Landscape


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
