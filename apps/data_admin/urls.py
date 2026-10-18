from django.urls import path
from . import views

app_name = 'data_admin'

urlpatterns = [
    path('', views.DataAdminDashboardView.as_view(), name='dashboard'),
    path('backup/', views.backup_csv, name='backup_csv'),
    path('download/<str:model_name>/', views.download_csv, name='download_csv'),
    path('restore/', views.restore_csv, name='restore_csv'),
    path('clear/<str:model_name>/', views.clear_table, name='clear_table'),
]
