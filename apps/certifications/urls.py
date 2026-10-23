from django.urls import path
from . import views

app_name = 'certifications'

urlpatterns = [
    path('', views.CertificadoListView.as_view(), name='list'),
    path('<int:pk>/descargar/', views.DescargarCertificadoView.as_view(), name='descargar'),
    path('cursos/<int:pk>/descargar/', views.DescargarCertificadoLmsView.as_view(), name='lms_download'),
    path('credenciales/nueva/', views.UserCredentialFormView.as_view(), name='credential_create'),
    path('credenciales/<int:pk>/', views.UserCredentialDetailView.as_view(), name='credential_detail'),
    path('credenciales/<int:pk>/editar/', views.UserCredentialFormView.as_view(), name='credential_edit'),
    path('credenciales/<int:pk>/solicitar-revision/', views.RequestCredentialReviewView.as_view(), name='credential_request_review'),
    path('credenciales/<int:pk>/archivar/', views.ArchiveCredentialView.as_view(), name='credential_archive'),
    path('evidencias/<int:pk>/descargar/', views.CredentialEvidenceDownloadView.as_view(), name='evidence_download'),
    path('revision/', views.CredentialReviewQueueView.as_view(), name='review_queue'),
    path('revision/<int:pk>/asumir/', views.ClaimCredentialReviewView.as_view(), name='review_claim'),
    path('revision/<int:pk>/', views.CredentialReviewDetailView.as_view(), name='review_detail'),
    path('credenciales/<int:pk>/revocar/', views.CredentialRevocationView.as_view(), name='credential_revoke'),
    path('publica/<int:pk>/', views.PublicCredentialView.as_view(), name='credential_public'),
    path('validar/<uuid:codigo>/', views.ValidarCertificadoView.as_view(), name='validar'),
    path('verificar/<uuid:codigo>/', views.ValidarCertificadoLmsView.as_view(), name='lms_validate'),
]
