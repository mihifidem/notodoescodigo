import json

from django.contrib.admin.models import LogEntry
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.blog.models import CategoriaBlog, HashtagBlog, LecturaPostUsuario, PostBlog, SubcategoriaBlog
from apps.enlaces.models import AccesoEnlaceUsuario, EnlaceImportante
from apps.prompts.models import CategoriaPrompt, HashtagPrompt, Prompt
from apps.users.models import AccessRoleAssignment, User
from .content_backups import MAX_BYTES, MAX_ROWS, export_backup, restore_backup


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class ContentBackupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username='backup-admin')
        AccessRoleAssignment.objects.create(user=cls.admin, role='admin')
        cls.user = User.objects.create_user(username='backup-reader')
        cls.staff = User.objects.create_user(username='backup-staff', is_staff=True)
        cls.blog_category = CategoriaBlog.objects.create(nombre='Blog category', slug='blog-category')
        cls.post = PostBlog.objects.create(
            titulo='Original post', slug='original-post', categoria=cls.blog_category,
            contenido_publico='Contenido público', contenido_privado='Contenido privado',
            imagen='blog/portada.png',
        )
        cls.tag = HashtagBlog.objects.create(nombre='blog-tag', slug='blog-tag')
        cls.post.hashtags.add(cls.tag)
        cls.subcategory = SubcategoriaBlog.objects.create(
            nombre='Subcategory', slug='subcategory', categoria=cls.blog_category,
        )
        cls.post.subcategoria = cls.subcategory
        cls.post.save()
        cls.reading = LecturaPostUsuario.objects.create(usuario=cls.user, post=cls.post)
        cls.prompt_category = CategoriaPrompt.objects.create(nombre='Prompt category', slug='prompt-category')
        cls.prompt = Prompt.objects.create(
            titulo='Original prompt', slug='original-prompt', categoria=cls.prompt_category,
            contenido='Explica {tema}', variables_json=[{'nombre': 'tema'}],
        )
        cls.prompt_tag = HashtagPrompt.objects.create(nombre='prompt-tag', slug='prompt-tag')
        cls.prompt.hashtags.add(cls.prompt_tag)
        cls.link = EnlaceImportante.objects.create(
            titulo='Original link', url='https://example.test', comentario='Comentario', creado_por=cls.admin,
        )
        cls.access = AccesoEnlaceUsuario.objects.create(usuario=cls.user, enlace=cls.link)

    def upload(self, content):
        return SimpleUploadedFile('backup.json', content, content_type='application/json')

    def test_admin_role_without_staff_can_download_each_section(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('data_admin:content_backups')).status_code, 200)
        for section, label in [('prompts', 'prompts.prompt'), ('blog', 'blog.postblog'), ('enlaces', 'enlaces.enlaceimportante')]:
            response = self.client.post(reverse('data_admin:content_backup_download', args=[section]))
            self.assertEqual(response.status_code, 200)
            self.assertIn('attachment;', response['Content-Disposition'])
            self.assertIn('no-store', response['Cache-Control'])
            payload = json.loads(response.content)
            self.assertIn(label, payload['models'])
            self.assertFalse(payload['files_included'])
            self.assertFalse(any(row['model'].startswith('users.') for row in payload['objects']))
        self.assertEqual(LogEntry.objects.filter(user=self.admin).count(), 3)

    def test_non_admins_and_staff_cannot_read_or_restore(self):
        for actor in (self.user, self.staff):
            self.client.force_login(actor)
            self.assertEqual(self.client.get(reverse('data_admin:content_backups')).status_code, 403)
            self.assertEqual(self.client.post(reverse('data_admin:content_backups'), {}).status_code, 403)
            self.assertEqual(self.client.post(reverse('data_admin:content_backup_download', args=['blog'])).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('data_admin:content_backups')).status_code, 302)

    def test_round_trip_restores_content_tags_dates_and_preserves_reading(self):
        content = export_backup('blog', self.admin)
        created_at = self.post.creado_en
        PostBlog.objects.filter(pk=self.post.pk).update(titulo='Modified', contenido_privado='Changed')
        self.post.hashtags.clear()
        extra = PostBlog.objects.create(
            titulo='Keep this', slug='keep-this', categoria=self.blog_category, contenido_publico='Keep',
        )
        counts = restore_backup(content, 'blog', self.admin)
        self.assertEqual(counts['inserted'], 0)
        self.post.refresh_from_db()
        self.assertEqual(self.post.titulo, 'Original post')
        self.assertEqual(self.post.contenido_privado, 'Contenido privado')
        self.assertEqual(self.post.creado_en, created_at)
        self.assertEqual(self.post.imagen.name, 'blog/portada.png')
        self.assertEqual(self.post.subcategoria_id, self.subcategory.pk)
        self.assertEqual(list(self.post.hashtags.values_list('pk', flat=True)), [self.tag.pk])
        self.assertTrue(PostBlog.objects.filter(pk=extra.pk).exists())
        self.assertTrue(LecturaPostUsuario.objects.filter(pk=self.reading.pk).exists())

    def test_restore_inserts_deleted_rows_and_is_repeatable(self):
        for section, model, original in [
            ('prompts', Prompt, self.prompt), ('enlaces', EnlaceImportante, self.link),
        ]:
            content = export_backup(section, self.admin)
            pk = original.pk
            model.objects.filter(pk=pk).delete()
            counts = restore_backup(content, section, self.admin)
            self.assertEqual(counts['inserted'], 1)
            self.assertTrue(model.objects.filter(pk=pk).exists())
            counts = restore_backup(content, section, self.admin)
            self.assertEqual(counts['inserted'], 0)
        self.assertEqual(list(self.prompt.hashtags.values_list('pk', flat=True)), [self.prompt_tag.pk])

    def test_links_preserve_access_records_and_existing_author(self):
        content = export_backup('enlaces', self.admin)
        EnlaceImportante.objects.filter(pk=self.link.pk).update(titulo='Edited')
        restore_backup(content, 'enlaces', self.admin)
        self.link.refresh_from_db()
        self.assertEqual(self.link.titulo, 'Original link')
        self.assertEqual(self.link.creado_por_id, self.admin.pk)
        self.assertTrue(AccesoEnlaceUsuario.objects.filter(pk=self.access.pk).exists())

    def test_invalid_row_rolls_back_all_previous_updates(self):
        payload = json.loads(export_backup('blog', self.admin))
        for row in payload['objects']:
            if row['model'] == 'blog.categoriablog':
                row['fields']['nombre'] = 'Must roll back'
            if row['model'] == 'blog.postblog':
                row['fields']['categoria'] = 999999
        with self.assertRaises((ValidationError, IntegrityError)):
            restore_backup(json.dumps(payload).encode(), 'blog', self.admin)
        self.blog_category.refresh_from_db()
        self.assertEqual(self.blog_category.nombre, 'Blog category')
        self.assertFalse(LogEntry.objects.filter(change_message__contains='"restore"').exists())

    def test_unique_conflict_does_not_overwrite_another_id(self):
        payload = json.loads(export_backup('blog', self.admin))
        category = next(row for row in payload['objects'] if row['model'] == 'blog.categoriablog')
        category['pk'] = 999999
        with self.assertRaises(IntegrityError):
            restore_backup(json.dumps(payload).encode(), 'blog', self.admin)
        self.assertFalse(CategoriaBlog.objects.filter(pk=999999).exists())
        self.assertTrue(CategoriaBlog.objects.filter(pk=self.blog_category.pk).exists())

    def test_missing_m2m_target_rolls_back_and_keeps_original_tags(self):
        payload = json.loads(export_backup('blog', self.admin))
        post = next(row for row in payload['objects'] if row['model'] == 'blog.postblog')
        post['fields']['hashtags'] = [999999]
        with self.assertRaises((ValidationError, IntegrityError)):
            restore_backup(json.dumps(payload).encode(), 'blog', self.admin)
        self.assertEqual(list(self.post.hashtags.values_list('pk', flat=True)), [self.tag.pk])

    def test_mismatched_subcategory_is_rejected(self):
        other = CategoriaBlog.objects.create(nombre='Other', slug='other')
        payload = json.loads(export_backup('blog', self.admin))
        post = next(row for row in payload['objects'] if row['model'] == 'blog.postblog')
        post['fields']['categoria'] = other.pk
        with self.assertRaises(ValidationError):
            restore_backup(json.dumps(payload).encode(), 'blog', self.admin)
        self.post.refresh_from_db()
        self.assertEqual(self.post.categoria_id, self.blog_category.pk)

    def test_missing_author_does_not_create_users(self):
        payload = json.loads(export_backup('enlaces', self.admin))
        payload['objects'][0]['fields']['creado_por'] = 999999
        users_before = User.objects.count()
        with self.assertRaises((ValidationError, IntegrityError)):
            restore_backup(json.dumps(payload).encode(), 'enlaces', self.admin)
        self.assertEqual(User.objects.count(), users_before)

    def test_row_limit_and_upload_size_are_enforced(self):
        payload = json.loads(export_backup('enlaces', self.admin))
        payload['objects'] = payload['objects'] * (MAX_ROWS + 1)
        with self.assertRaises(ValidationError):
            restore_backup(json.dumps(payload).encode(), 'enlaces', self.admin)
        self.client.force_login(self.admin)
        response = self.client.post(reverse('data_admin:content_backups'), {
            'section': 'enlaces', 'backup': self.upload(b'x' * (MAX_BYTES + 1)), 'confirm': 'on',
        })
        self.assertContains(response, '10 MB', status_code=400)

    def test_rejects_wrong_sections_tables_ids_and_missing_fields(self):
        original = json.loads(export_backup('enlaces', self.admin))
        changes = [
            lambda p: p.update(section='blog'),
            lambda p: p['objects'][0].update(model='users.user'),
            lambda p: p['objects'][0].update(pk=True),
            lambda p: p['objects'].append(p['objects'][0]),
            lambda p: p['objects'][0]['fields'].pop('url'),
            lambda p: p['objects'][0]['fields'].update(password='not-allowed'),
        ]
        for change in changes:
            payload = json.loads(json.dumps(original))
            change(payload)
            with self.assertRaises(ValidationError):
                restore_backup(json.dumps(payload).encode(), 'enlaces', self.admin)
        for invalid in (b'not json', b'[]', b'{"version":1,"version":2}', b'x' * (MAX_BYTES + 1)):
            with self.assertRaises(ValidationError):
                restore_backup(invalid, 'blog', self.admin)

    def test_rejects_path_traversal_in_file_references(self):
        payload = json.loads(export_backup('blog', self.admin))
        post = next(row for row in payload['objects'] if row['model'] == 'blog.postblog')
        for value in ('../secret', '/etc/passwd', 'blog/../../secret', 'https://example.test/pic.png'):
            post['fields']['imagen'] = value
            with self.assertRaises(ValidationError):
                restore_backup(json.dumps(payload).encode(), 'blog', self.admin)

    def test_restore_requires_confirmation_and_csrf(self):
        self.client.force_login(self.admin)
        content = export_backup('enlaces', self.admin)
        url = reverse('data_admin:content_backups')
        response = self.client.post(url, {'section': 'enlaces', 'backup': self.upload(content)})
        self.assertEqual(response.status_code, 400)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(url, {}).status_code, 403)
        response = self.client.post(url, {
            'section': 'enlaces', 'backup': self.upload(content), 'confirm': 'on',
        }, follow=True)
        self.assertContains(response, 'Copia restaurada')
        self.assertEqual(self.client.get(reverse('data_admin:content_backup_download', args=['enlaces'])).status_code, 405)

    def test_invalid_upload_displays_error_without_modifying_content(self):
        self.client.force_login(self.admin)
        url = reverse('data_admin:content_backups')
        with self.assertLogs('apps.data_admin.backup_views', level='WARNING'):
            response = self.client.post(url, {
                'section': 'enlaces', 'backup': self.upload(b'not-json'), 'confirm': 'on',
            })
        self.assertContains(response, 'JSON UTF-8 válido', status_code=400)
        payload = json.loads(export_backup('enlaces', self.admin))
        payload['objects'][0]['fields']['creado_por'] = 999999
        with self.assertLogs('apps.data_admin.backup_views', level='WARNING'):
            response = self.client.post(url, {
                'section': 'enlaces', 'backup': self.upload(json.dumps(payload).encode()), 'confirm': 'on',
            })
        self.assertEqual(response.status_code, 400)
        self.link.refresh_from_db()
        self.assertEqual(self.link.creado_por_id, self.admin.pk)

    def test_dashboard_link_is_admin_only(self):
        for actor, expected in ((self.admin, True), (self.user, False), (self.staff, False)):
            self.client.force_login(actor)
            response = self.client.get(reverse('users:dashboard'))
            self.assertEqual(response.status_code, 200)
            if expected:
                self.assertContains(response, reverse('data_admin:content_backups'))
            else:
                self.assertNotContains(response, reverse('data_admin:content_backups'))

    def test_sidebar_backup_link_uses_platform_admin_permission(self):
        for actor, expected in ((self.admin, True), (self.user, False), (self.staff, False)):
            with self.subTest(actor=actor.username):
                self.client.force_login(actor)
                response = self.client.get(reverse('users:dashboard'))
                if expected:
                    self.assertContains(response, 'bi-database-down me-2"></i> Copias JSON')
                else:
                    self.assertNotContains(response, 'bi-database-down me-2"></i> Copias JSON')

    def test_sidebar_marks_backup_page_as_current(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('data_admin:content_backups'))
        self.assertContains(response, 'aria-current="page"')
        self.assertContains(response, 'bi-database-down me-2"></i> Copias JSON')
