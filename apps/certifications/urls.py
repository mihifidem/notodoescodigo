from django.urls import path
from . import views

app_name = 'certifications'

urlpatterns = [
    path('', views.CertificadoListView.as_view(), name='list'),
    path('<int:pk>/descargar/', views.DescargarCertificadoView.as_view(), name='descargar'),
    path('validar/<uuid:codigo>/', views.ValidarCertificadoView.as_view(), name='validar'),
]
