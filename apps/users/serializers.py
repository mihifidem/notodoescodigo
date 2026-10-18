"""Serializers for the users app."""
from rest_framework import serializers
from .models import User, Notificacion


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            'id', 'username', 'first_name', 'last_name', 'email',
            'role', 'puntos', 'nivel', 'streak', 'avatar', 'aula', 'date_joined',
        ]
        read_only_fields = ['id', 'role', 'puntos', 'nivel', 'streak', 'date_joined']


class NotificacionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notificacion
        fields = ['id', 'tipo', 'titulo', 'mensaje', 'leida', 'creada_en']
        read_only_fields = ['id', 'creada_en']
