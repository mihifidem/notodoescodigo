import csv
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.blog.models import CategoriaBlog, PostBlog, SubcategoriaBlog
from apps.users.models import User


class DataAdminAccessTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='staffuser',
            email='staff@example.com',
            password='secret123',
            is_staff=True,
        )

    def test_staff_user_can_access_dashboard(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse('data_admin:dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_restore_csv_rebuilds_related_fk_values(self):
        self.client.force_login(self.staff_user)
        categoria = CategoriaBlog.objects.create(nombre='Inteligencia Artificial', slug='inteligencia-artificial')
        PostBlog.objects.create(
            titulo='Post original',
            slug='post-original',
            categoria=categoria,
            contenido_publico='Texto de prueba',
        )

        csv_buffer = io.StringIO()
        writer = csv.DictWriter(
            csv_buffer,
            fieldnames=['titulo', 'slug', 'resumen', 'contenido_publico', 'contenido_privado', 'categoria', 'subcategoria', 'visibilidad', 'publicado', 'destacado', 'puntos_lectura'],
        )
        writer.writeheader()
        writer.writerow({
            'titulo': 'Post restaurado',
            'slug': 'post-restaurado',
            'resumen': 'Resumen',
            'contenido_publico': 'Contenido restaurado',
            'contenido_privado': '',
            'categoria': 'Inteligencia Artificial',
            'subcategoria': 'Inteligencia Artificial / OpenAI',
            'visibilidad': 'publico',
            'publicado': 'True',
            'destacado': 'False',
            'puntos_lectura': '20',
        })

        file = SimpleUploadedFile('restore.csv', csv_buffer.getvalue().encode('utf-8'), content_type='text/csv')
        response = self.client.post(
            reverse('data_admin:restore_csv'),
            {'model_name': 'blog.postblog', 'csv_file': file},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(PostBlog.objects.filter(slug='post-restaurado').exists())
        restored = PostBlog.objects.get(slug='post-restaurado')
        self.assertEqual(restored.categoria.nombre, 'Inteligencia Artificial')
        self.assertTrue(SubcategoriaBlog.objects.filter(nombre='Inteligencia Artificial / OpenAI').exists())
