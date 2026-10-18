from django.urls import path
from . import views

app_name = 'cv_templates'

urlpatterns = [
    path('', views.templatecv_list, name='list'),
    path('crear/', views.templatecv_create, name='create'),
    path('editar/<int:pk>/', views.templatecv_update, name='update'),
    path('eliminar/<int:pk>/', views.templatecv_delete, name='delete'),
]
