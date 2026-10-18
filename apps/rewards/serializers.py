from rest_framework import serializers
from .models import Recompensa, CanjeRecompensa


class RecompensaSerializer(serializers.ModelSerializer):
    disponible = serializers.BooleanField(read_only=True)

    class Meta:
        model = Recompensa
        fields = ['id', 'nombre', 'descripcion', 'imagen', 'puntos_necesarios', 'stock', 'disponible']


class CanjeSerializer(serializers.ModelSerializer):
    recompensa = RecompensaSerializer(read_only=True)

    class Meta:
        model = CanjeRecompensa
        fields = ['id', 'recompensa', 'fecha_canje', 'puntos_gastados', 'estado']
