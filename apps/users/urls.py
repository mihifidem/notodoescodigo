"""URL configuration for the users app."""
from django.urls import path
from . import views
from . import billing_views

app_name = 'users'

urlpatterns = [
    path('landing/', views.LandingView.as_view(), name='landing'),
    path('/', views.DashboardView.as_view(), name='dashboard'),
    path('registro/', views.RegistroView.as_view(), name='registro'),
    path('accesos/', views.AccessSummaryView.as_view(), name='access_summary'),
    path('facturacion/', views.BillingDashboardView.as_view(), name='billing'),
    path('facturacion/suscribir/', billing_views.subscribe, name='subscribe'),
    path('facturacion/suscripcion/<int:pk>/continuar/', billing_views.resume, name='subscription_resume'),
    path('facturacion/suscripcion/<int:pk>/cancelar/', billing_views.cancel, name='subscription_cancel'),
    path('facturacion/suscripcion/<int:pk>/reactivar-renovacion/', billing_views.reactivate_renewal, name='subscription_reactivate'),
    path('facturacion/suscripcion/<int:pk>/retorno/', billing_views.payment_return, name='subscription_return'),
    path('facturacion/stripe/webhook/', billing_views.stripe_webhook, name='stripe_billing_webhook'),
    path('facturacion/servicios/gratis/', views.FreeServiceOrderView.as_view(), name='free_service_order'),
    path('progreso/', views.PersonalProgressView.as_view(), name='progress'),
    path('progreso/exportar/', views.PersonalProgressExportView.as_view(), name='progress_export'),
    path('objetivos/nuevo/', views.PersonalGoalCreateView.as_view(), name='goal_create'),
    path('objetivos/exportar/', views.PersonalGoalExportView.as_view(), name='goal_export'),
    path('objetivos/<int:pk>/editar/', views.PersonalGoalUpdateView.as_view(), name='goal_update'),
    path('objetivos/<int:pk>/<str:action>/', views.PersonalGoalStateView.as_view(), name='goal_state'),
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
