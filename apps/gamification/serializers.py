from rest_framework import serializers
from .models import Insignia, InsigniaUsuario, Logro, LogroUsuario, Mision, MisionUsuario


class InsigniaSerializer(serializers.ModelSerializer):
    current_version = serializers.SerializerMethodField()

    class Meta:
        model = Insignia
        fields = [
            'id',
            'code',
            'nombre',
            'descripcion',
            'imagen',
            'tipo',
            'category',
            'issuer_name',
            'requisito_valor',
            'visible',
            'current_version',
        ]

    def get_current_version(self, obj):
        from .badge_services import current_badge_version

        version = current_badge_version(obj)
        if version is None:
            return None
        return {
            'version': version.version,
            'rule_type': version.rule_type,
            'requirements': version.public_requirements,
            'threshold': version.threshold,
        }


class InsigniaUsuarioSerializer(serializers.ModelSerializer):
    insignia = InsigniaSerializer(read_only=True)

    class Meta:
        model = InsigniaUsuario
        fields = ['id', 'insignia', 'fecha_obtenida', 'achieved_at', 'issuer_name', 'status']


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
