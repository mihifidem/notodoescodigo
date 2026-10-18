from django.urls import path

from . import views

app_name = 'prompts'

urlpatterns = [
    path('', views.PromptListView.as_view(), name='list'),
    path('crear/', views.PromptCreateView.as_view(), name='create'),
    path('mis-favoritos/', views.MisFavoritosView.as_view(), name='favoritos'),
    path('<slug:slug>/', views.PromptDetailView.as_view(), name='detail'),
    path('<slug:slug>/favorito/', views.ToggleFavoritoView.as_view(), name='toggle_favorito'),
]
