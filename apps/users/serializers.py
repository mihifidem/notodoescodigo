"""Serializers for the users app."""
from rest_framework import serializers
from .models import Notificacion, RamaProfesional, User


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            'id', 'username', 'first_name', 'last_name', 'email',
            'role', 'puntos', 'nivel', 'streak', 'avatar', 'aula', 'date_joined',
        ]
        read_only_fields = ['id', 'role', 'puntos', 'nivel', 'streak', 'date_joined']


class RamaProfesionalSerializer(serializers.ModelSerializer):
    class Meta:
        model = RamaProfesional
        fields = ['id', 'code', 'name', 'description']
        read_only_fields = fields


class UserProfessionalBranchSerializer(serializers.ModelSerializer):
    rama_principal = serializers.PrimaryKeyRelatedField(
        queryset=RamaProfesional.objects.filter(is_active=True),
        allow_null=True,
        required=True,
    )

    class Meta:
        model = User
        fields = ['rama_principal']

    def update(self, instance, validated_data):
        instance.rama_principal = validated_data['rama_principal']
        instance.save(update_fields=['rama_principal'])
        return instance


class NotificacionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notificacion
        fields = ['id', 'tipo', 'titulo', 'mensaje', 'leida', 'creada_en']
        read_only_fields = ['id', 'creada_en']
