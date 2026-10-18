from rest_framework import serializers
from .models import Insignia, InsigniaUsuario, Logro, LogroUsuario, Mision, MisionUsuario


class InsigniaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Insignia
        fields = ['id', 'nombre', 'descripcion', 'imagen', 'tipo', 'requisito_valor', 'visible']


class InsigniaUsuarioSerializer(serializers.ModelSerializer):
    insignia = InsigniaSerializer(read_only=True)

    class Meta:
        model = InsigniaUsuario
        fields = ['id', 'insignia', 'fecha_obtenida']


class LogroSerializer(serializers.ModelSerializer):
    class Meta:
        model = Logro
        fields = ['id', 'nombre', 'descripcion', 'puntos_recompensa', 'oculto']


class MisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Mision
        fields = [
            'id', 'titulo', 'descripcion', 'tipo',
            'puntos_recompensa', 'requisito_tipo', 'requisito_cantidad',
        ]


class MisionUsuarioSerializer(serializers.ModelSerializer):
    mision = MisionSerializer(read_only=True)

    class Meta:
        model = MisionUsuario
        fields = ['id', 'mision', 'progreso', 'completada', 'fecha_completada', 'fecha_asignada']
