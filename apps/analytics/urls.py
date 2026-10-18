from django.urls import path
from . import views

app_name = 'analytics'

urlpatterns = [
    path('admin/', views.DashboardAdminView.as_view(), name='dashboard_admin'),
    path('admin/alumno/<int:usuario_id>/puntos/', views.AdminAlumnoPuntosView.as_view(), name='admin_alumno_puntos'),
    path('admin/cv-tracker/', views.CVTrackerView.as_view(), name='cv_tracker'),
    path('mi-progreso/', views.ProgresoAlumnoView.as_view(), name='mi_progreso'),
]
