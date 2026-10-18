from django.urls import path

from .views import EvaluacionAlumnoListView, EvaluacionAlumnoDetailView, EntregaEditView, EntregaDeleteView


app_name = 'evaluations'

urlpatterns = [
    path('', EvaluacionAlumnoListView.as_view(), name='list'),
    path('<int:pk>/', EvaluacionAlumnoDetailView.as_view(), name='detail'),
    path('entregas/<int:pk>/editar/', EntregaEditView.as_view(), name='entrega-edit'),
    path('entregas/<int:pk>/borrar/', EntregaDeleteView.as_view(), name='entrega-delete'),
]
