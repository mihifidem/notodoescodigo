from django.urls import path
from . import views

app_name = 'courses'

urlpatterns = [
    path('', views.CursoListView.as_view(), name='list'),
    path('<int:pk>/', views.CursoDetailView.as_view(), name='detail'),
    path('<int:pk>/inscribirse/', views.InscribirseView.as_view(), name='inscribirse'),
    path('tema/<int:pk>/', views.TemaDetailView.as_view(), name='tema_detail'),
    path('tema/<int:pk>/completar/', views.MarcarCompletadoView.as_view(), name='completar_tema'),
    path('recurso/<int:pk>/online/', views.TemaRecursoOnlineView.as_view(), name='recurso_online'),
    path('recurso/<int:pk>/abrir/', views.TemaRecursoAbrirView.as_view(), name='recurso_abrir'),
    path('recurso/<int:pk>/descargar/', views.TemaRecursoDescargarView.as_view(), name='recurso_descargar'),
    path('quiz/<int:pk>/', views.QuizView.as_view(), name='quiz'),
]
