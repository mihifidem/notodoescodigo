from rest_framework import serializers
from .models import Certificado, UserCredential


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


class UserCredentialSerializer(serializers.ModelSerializer):
    credential_type_label = serializers.CharField(source='get_credential_type_display', read_only=True)
    review_status_label = serializers.CharField(source='get_review_status_display', read_only=True)
    validity_status = serializers.CharField(read_only=True)
    verified_for_current_revision = serializers.BooleanField(
        source='is_verified_for_current_revision',
        read_only=True,
    )

    class Meta:
        model = UserCredential
        fields = [
            'id',
            'name',
            'issuer_name',
            'credential_type',
            'credential_type_label',
            'area',
            'external_id',
            'awarded_on',
            'expires_on',
            'verification_url',
            'visibility',
            'review_status',
            'review_status_label',
            'validity_status',
            'verified_for_current_revision',
            'revision',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class LmsCourseCertificateSerializer(serializers.ModelSerializer):
    course_name = serializers.CharField(source='course.nombre', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    validation_url = serializers.SerializerMethodField()
    pdf_available = serializers.SerializerMethodField()

    class Meta:
        from apps.lms.models import CourseCertificate

        model = CourseCertificate
        fields = [
            'id',
            'course',
            'course_name',
            'certificate_code',
            'status',
            'status_label',
            'issued_at',
            'revoked_at',
            'validation_url',
            'pdf_available',
        ]
        read_only_fields = fields

    def get_validation_url(self, obj):
        return obj.get_validation_url()

    def get_pdf_available(self, obj):
        return bool(obj.pdf_file)
