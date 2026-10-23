from django import forms
from django.conf import settings
from django.utils import timezone

from .models import (
    CredentialDefinition,
    CredentialEvidence,
    CredentialType,
    CredentialVisibility,
    CredentialVerification,
    UserCredential,
)


PUBLIC_CREDENTIAL_TYPES = [
    choice for choice in CredentialType.choices
    if choice[0] != CredentialType.INTERNAL
]


class UserCredentialForm(forms.ModelForm):
    evidence_file = forms.FileField(required=False, label='Archivo de evidencia')
    evidence_type = forms.ChoiceField(
        choices=CredentialEvidence.EvidenceType.choices,
        required=False,
        label='Tipo de evidencia',
    )

    class Meta:
        model = UserCredential
        fields = [
            'definition',
            'name',
            'issuer_name',
            'credential_type',
            'area',
            'external_id',
            'awarded_on',
            'expires_on',
            'verification_url',
            'visibility',
            'publish_external_id',
            'publish_verification_url',
        ]
        widgets = {
            'awarded_on': forms.DateInput(attrs={'type': 'date'}),
            'expires_on': forms.DateInput(attrs={'type': 'date'}),
            'verification_url': forms.URLInput(attrs={'placeholder': 'https://'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['definition'].queryset = CredentialDefinition.objects.filter(
            is_active=True,
            is_validated=True,
            issuer__is_active=True,
        ).select_related('issuer')
        self.fields['definition'].required = False
        self.fields['credential_type'].choices = PUBLIC_CREDENTIAL_TYPES
        self.fields['visibility'].choices = CredentialVisibility.choices
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault('class', 'form-check-input')
            else:
                field.widget.attrs.setdefault('class', 'form-control')
        allowed = getattr(settings, 'CERTIFICATIONS_ALLOWED_EVIDENCE_MIME_TYPES', [])
        max_bytes = getattr(settings, 'CERTIFICATIONS_EVIDENCE_MAX_BYTES', 0)
        self.evidence_upload_enabled = bool(allowed and max_bytes > 0)
        if not self.evidence_upload_enabled:
            self.fields.pop('evidence_file')
            self.fields.pop('evidence_type')

    def clean(self):
        cleaned = super().clean()
        definition = cleaned.get('definition')
        credential_type = cleaned.get('credential_type')
        if definition:
            if credential_type and credential_type != definition.credential_type:
                self.add_error('credential_type', 'El tipo debe coincidir con la definición del catálogo.')
            cleaned['issuer_name'] = definition.issuer.name
            cleaned['name'] = definition.name
        if cleaned.get('publish_external_id') and not cleaned.get('external_id'):
            self.add_error('publish_external_id', 'No hay identificador externo que publicar.')
        if cleaned.get('publish_verification_url') and not cleaned.get('verification_url'):
            self.add_error('publish_verification_url', 'No hay URL de verificación que publicar.')
        evidence = cleaned.get('evidence_file')
        if evidence and not cleaned.get('evidence_type'):
            self.add_error('evidence_type', 'Selecciona el tipo de evidencia.')
        return cleaned


class CredentialReviewDecisionForm(forms.Form):
    method = forms.ChoiceField(choices=(
        (CredentialVerification.Method.MANUAL_EVIDENCE, 'Revisión manual de evidencia'),
        (CredentialVerification.Method.ISSUER_CONFIRMATION, 'Confirmación documentada del emisor'),
    ))
    result = forms.ChoiceField(choices=CredentialVerification.Result.choices)
    source = forms.CharField(
        required=False,
        max_length=500,
        label='Fuente consultada',
        help_text='Obligatoria para confirmar directamente con el emisor.',
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 4}),
        label='Motivo y alcance',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')


class CredentialRevocationForm(forms.Form):
    revoked_on = forms.DateField(
        label='Fecha de revocación',
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        initial=timezone.localdate,
    )
    source = forms.CharField(
        label='Fuente del emisor',
        max_length=300,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    reason = forms.CharField(
        label='Motivo',
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
    )
