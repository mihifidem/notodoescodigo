from django.urls import path
from . import views

app_name = 'rewards'

urlpatterns = [
    path('', views.RecompensaListView.as_view(), name='list'),
    path('<int:pk>/canjear/', views.CanjeView.as_view(), name='canjear'),
    path('historial/', views.HistorialCanjesView.as_view(), name='historial'),
]
