from rest_framework import serializers
from .models import Certificado


class CertificadoSerializer(serializers.ModelSerializer):
    curso_nombre = serializers.CharField(source='curso.nombre', read_only=True)
    url_validacion = serializers.SerializerMethodField()

    class Meta:
        model = Certificado
        fields = [
            'id', 'curso', 'curso_nombre', 'codigo_unico',
            'fecha_emision', 'pdf', 'url_validacion',
        ]

    def get_url_validacion(self, obj):
        return obj.get_validation_url()
