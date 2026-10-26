from django.urls import path

from . import views

app_name = 'energy'

urlpatterns = [
    path('', views.detail, name='detail'),
    path('estado/', views.status, name='status'),
    path('estimar/', views.estimate, name='estimate'),
    path('admin/ajustar/', views.admin_adjust, name='admin_adjust'),
]
