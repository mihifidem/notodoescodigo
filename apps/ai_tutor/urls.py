from django.urls import path
from . import views

app_name = 'ai_tutor'

urlpatterns = [
    path('', views.ChatView.as_view(), name='chat'),
    path('<int:conv_id>/', views.ChatView.as_view(), name='chat_conv'),
    path('enviar/', views.EnviarMensajeView.as_view(), name='enviar'),
]
