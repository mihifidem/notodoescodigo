from django.urls import path

from . import views

app_name = 'enlaces'

urlpatterns = [
    path('', views.EnlacesImportantesView.as_view(), name='list'),
    path('<int:pk>/acceder/', views.AccederEnlaceView.as_view(), name='acceder'),
]
