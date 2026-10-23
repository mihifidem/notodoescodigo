"""Unified API router — registers all app ViewSets."""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)

from .views import ai_generate

# Import ViewSets from each app
from apps.users.views import (
    AccessSnapshotAPIView,
    NotificacionViewSet,
    RamaProfesionalListAPIView,
    UserViewSet,
)
from apps.courses.views import CursoViewSet, TemaViewSet, ResultadoQuizViewSet
from apps.gamification.views import InsigniaUsuarioViewSet, InsigniaViewSet, MisionUsuarioViewSet
from apps.rewards.views import RecompensaViewSet, CanjeViewSet
from apps.certifications.views import (
    CertificadoViewSet,
    LmsCourseCertificateViewSet,
    UserCredentialViewSet,
)
from apps.blog.views import PostBlogViewSet
from apps.evaluations.views import TipoExamenViewSet, EvaluacionViewSet, EntregaEvaluacionViewSet
from apps.lms.knowledge_api import KnowledgeQuestionSelectionAPIView

router = DefaultRouter()
router.register(r'users', UserViewSet, basename='user')
router.register(r'notifications', NotificacionViewSet, basename='notificacion')
router.register(r'courses', CursoViewSet, basename='curso')
router.register(r'topics', TemaViewSet, basename='tema')
router.register(r'quiz-results', ResultadoQuizViewSet, basename='resultado_quiz')
router.register(r'badges', InsigniaViewSet, basename='insignia')
router.register(r'my-badges', InsigniaUsuarioViewSet, basename='my-badge')
router.register(r'missions', MisionUsuarioViewSet, basename='mision_usuario')
router.register(r'rewards', RecompensaViewSet, basename='recompensa')
router.register(r'redemptions', CanjeViewSet, basename='canje')
router.register(r'certificates', CertificadoViewSet, basename='certificado')
router.register(r'my-credentials', UserCredentialViewSet, basename='my-credential')
router.register(
    r'my-course-certificates',
    LmsCourseCertificateViewSet,
    basename='my-course-certificate',
)
router.register(r'posts', PostBlogViewSet, basename='post')
router.register(r'exam-types', TipoExamenViewSet, basename='tipo_examen')
router.register(r'evaluations', EvaluacionViewSet, basename='evaluacion')
router.register(r'submissions', EntregaEvaluacionViewSet, basename='entrega_evaluacion')

urlpatterns = [
    path('knowledge/questions/', KnowledgeQuestionSelectionAPIView.as_view(), name='knowledge-question-selection'),
    path('', include(router.urls)),
    path('access/', AccessSnapshotAPIView.as_view(), name='access-snapshot'),
    path(
        'professional-branches/',
        RamaProfesionalListAPIView.as_view(),
        name='professional-branches',
    ),
    path('auth/', include('rest_framework.urls')),
    path('ai/generate', ai_generate, name='ai-generate'),
    # OpenAPI schema & docs
    path('schema/', SpectacularAPIView.as_view(), name='api-schema'),
    path('docs/', SpectacularSwaggerView.as_view(url_name='api-schema'), name='swagger-ui'),
    path('redoc/', SpectacularRedocView.as_view(url_name='api-schema'), name='redoc'),
]
