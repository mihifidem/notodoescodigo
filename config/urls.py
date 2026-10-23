"""Main URL configuration."""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from apps.users.views import LandingView
from apps.curriculum.views import public_cv, cv_pdf
from apps.api.views import ai_generate

urlpatterns = [
    path('admin/', admin.site.urls),

    # Public landing page
    path('', LandingView.as_view(), name='landing'),

    # App URLs
    path('dashboard/', include('apps.users.urls', namespace='users')),
    path('cursos/', include('apps.courses.urls', namespace='courses')),
    path('blog/', include('apps.blog.urls', namespace='blog')),
    path('gamificacion/', include('apps.gamification.urls', namespace='gamification')),
    path('recompensas/', include('apps.rewards.urls', namespace='rewards')),
    path('analytics/', include('apps.analytics.urls', namespace='analytics')),
    path('certificados/', include('apps.certifications.urls', namespace='certifications')),
    path('evaluaciones/', include('apps.evaluations.urls', namespace='evaluations')),
    path('tutor/', include('apps.ai_tutor.urls', namespace='ai_tutor')),
    path('enlaces/', include('apps.enlaces.urls', namespace='enlaces')),
    path('prompts/', include('apps.prompts.urls', namespace='prompts')),
    path('cv/', include('apps.curriculum.urls', namespace='curriculum')),
    path('bugs/', include('apps.bug_reports.urls', namespace='bug_reports')),
    path('nexus/', include('apps.nexus.urls', namespace='nexus')),
    path('juegos/', include('apps.minigames.urls', namespace='minigames')),
    path('ducky-arenas/', include('apps.ducky_arenas.urls', namespace='ducky_arenas')),
    path('clash/', include('apps.duckyclash.urls', namespace='duckyclash')),
    path('api/ducky-arenas/', include('apps.ducky_arenas.api_urls')),
    path('cv-templates/', include('apps.cv_templates.urls', namespace='cv_templates')),
    path('data-admin/', include('apps.data_admin.urls', namespace='data_admin')),
    path('devtools/', include('apps.devtools.urls', namespace='devtools')),
    path('lms/', include('apps.lms.urls', namespace='lms')),
    path('actualizaciones/', include('apps.updates.urls', namespace='updates')),

    # Public CV URLs (opentowork.es/slug style)
    path('opentowork/<slug:slug>/', public_cv, name='curriculum_public_cv'),
    path('opentowork/<slug:slug>/pdf/', cv_pdf, name='curriculum_cv_pdf'),

    # API
    path('api/v1/', include('apps.api.urls')),
    path('api/ai/generate', ai_generate, name='ai-generate-root'),
    path('api/posts/', include('apps.blog.urls', namespace='blog_posts')),

    # django-allauth (login, register, social auth)
    path('accounts/', include('allauth.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
