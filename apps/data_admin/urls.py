from django.urls import path
from . import views
from . import backup_views

app_name = 'data_admin'

urlpatterns = [
    path('copias-json/', backup_views.dashboard, name='content_backups'),
    path('copias-json/<str:section>/descargar/', backup_views.download, name='content_backup_download'),
    path('', views.DataAdminDashboardView.as_view(), name='dashboard'),
    path('backup/', views.backup_csv, name='backup_csv'),
    path('download/<str:model_name>/', views.download_csv, name='download_csv'),
    path('restore/', views.restore_csv, name='restore_csv'),
    path('clear/<str:model_name>/', views.clear_table, name='clear_table'),
]
