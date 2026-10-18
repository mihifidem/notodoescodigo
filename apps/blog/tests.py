from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.blog.models import CategoriaBlog, HashtagBlog, PostBlog, ValoracionPost

User = get_user_model()


class BlogRecommendationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='usuario1', email='u1@test.com', password='12345678')
        self.categoria_tecnologia = CategoriaBlog.objects.create(nombre='Tecnología', slug='tecnologia')
        self.categoria_marketing = CategoriaBlog.objects.create(nombre='Marketing', slug='marketing')

        self.post_actual = PostBlog.objects.create(
            titulo='Post principal',
            slug='post-principal',
            categoria=self.categoria_tecnologia,
            contenido_publico='Contenido principal',
            publicado=True,
            visibilidad=PostBlog.VISIBILIDAD_PUBLICA,
        )
        self.post_votado = PostBlog.objects.create(
            titulo='Post votado',
            slug='post-votado',
            categoria=self.categoria_tecnologia,
            contenido_publico='Contenido votado',
            publicado=True,
            visibilidad=PostBlog.VISIBILIDAD_PUBLICA,
        )
        self.post_recomendado = PostBlog.objects.create(
            titulo='Post recomendado',
            slug='post-recomendado',
            categoria=self.categoria_tecnologia,
            contenido_publico='Contenido recomendado',
            publicado=True,
            visibilidad=PostBlog.VISIBILIDAD_PUBLICA,
        )
        self.post_otro = PostBlog.objects.create(
            titulo='Post de marketing',
            slug='post-marketing',
            categoria=self.categoria_marketing,
            contenido_publico='Contenido de marketing',
            publicado=True,
            visibilidad=PostBlog.VISIBILIDAD_PUBLICA,
        )

        ValoracionPost.objects.create(usuario=self.user, post=self.post_votado, valor=5)
        ValoracionPost.objects.create(usuario=self.user, post=self.post_otro, valor=1)

    def test_se_recomiendan_posts_similares_segundo_usuario_y_votos(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('blog:detail', args=[self.post_actual.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertIn('posts_recomendados', response.context)
        recomendaciones = list(response.context['posts_recomendados'])
        self.assertIn(self.post_recomendado, recomendaciones)
        self.assertNotIn(self.post_actual, recomendaciones)
        self.assertTrue(any(post.id in {self.post_recomendado.id, self.post_otro.id} for post in recomendaciones))

    def test_se_recomiendan_posts_por_hashtag_y_lecturas_recientes(self):
        hashtag = HashtagBlog.objects.create(nombre='django', slug='django')
        self.post_recomendado.hashtags.add(hashtag)
        self.post_votado.hashtags.add(hashtag)

        self.client.force_login(self.user)
        response = self.client.get(reverse('blog:detail', args=[self.post_actual.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.post_recomendado, response.context['posts_recomendados'])


class BlogNewsLiteFilterTests(TestCase):
    def setUp(self):
        self.categoria_news_lite = CategoriaBlog.objects.create(
            nombre='News Lite',
            slug='news-lite',
        )
        self.categoria_otros = CategoriaBlog.objects.create(
            nombre='Tecnología',
            slug='tecnologia',
        )

    def test_news_lite_category_filter_returns_posts(self):
        post = PostBlog.objects.create(
            titulo='Post News Lite',
            slug='post-news-lite',
            categoria=self.categoria_news_lite,
            contenido_publico='Contenido para News Lite',
            publicado=True,
            visibilidad=PostBlog.VISIBILIDAD_PUBLICA,
        )
        PostBlog.objects.create(
            titulo='Otro post',
            slug='otro-post',
            categoria=self.categoria_otros,
            contenido_publico='Contenido normal',
            publicado=True,
            visibilidad=PostBlog.VISIBILIDAD_PUBLICA,
        )

        response = self.client.get(reverse('blog:list'), {'categoria': 'news-lite'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, post.titulo)
        self.assertNotContains(response, 'Otro post')

    def test_default_blog_listing_excludes_news_lite(self):
        PostBlog.objects.create(
            titulo='Post News Lite oculto',
            slug='post-news-lite-oculto',
            categoria=self.categoria_news_lite,
            contenido_publico='Contenido oculto',
            publicado=True,
            visibilidad=PostBlog.VISIBILIDAD_PUBLICA,
        )

        response = self.client.get(reverse('blog:list'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Post News Lite oculto')
