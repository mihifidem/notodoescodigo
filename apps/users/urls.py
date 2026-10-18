"""URL configuration for the users app."""
from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    path('landing/', views.LandingView.as_view(), name='landing'),
    path('/', views.DashboardView.as_view(), name='dashboard'),
    path('registro/', views.RegistroView.as_view(), name='registro'),
    path('perfil/editar/', views.EditarPerfilView.as_view(), name='editar_perfil'),
    path('perfil/borrar-cuenta/', views.BorrarCuentaView.as_view(), name='borrar_cuenta'),
    path('perfil/generar-avatar-ducky/', views.generar_avatar_ducky, name='generar_avatar_ducky'),
    path('perfil/confirmar-avatar-ducky/', views.confirmar_avatar_ducky, name='confirmar_avatar_ducky'),
    path('perfil/<str:username>/', views.PerfilView.as_view(), name='perfil'),
    path('notificaciones/', views.NotificacionesView.as_view(), name='notificaciones'),
    path('notificaciones/leidas/', views.MarcarNotificacionLeidaView.as_view(), name='marcar_todas_leidas'),
    path('notificaciones/<int:pk>/leida/', views.MarcarNotificacionLeidaView.as_view(), name='marcar_leida'),
    path('admin/csv-manager/', views.admin_csv_manager, name='admin_csv_manager'),
]
