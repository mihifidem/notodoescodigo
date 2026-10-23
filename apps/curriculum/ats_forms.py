import uuid

from django import forms
from django.core.exceptions import ValidationError

from .letter_forms import PROFILE_SECTIONS, validate_offer_url
from .models import CVProfile, CVProfileRevision


class ATSRevisionChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.profile.nombre} — revisión {obj.revision} — {obj.created_at:%Y-%m-%d %H:%M}'


class ATSSourceForm(forms.Form):
    title = forms.CharField(label='Nombre de la fuente', max_length=200)
    profile = forms.ModelChoiceField(
        label='Perfil propio (o elige un PDF/texto)', queryset=CVProfile.objects.none(), required=False,
    )
    revision = ATSRevisionChoice(
        label='Revisión publicada (opcional)', queryset=CVProfileRevision.objects.none(), required=False,
        help_text='Sin revisión se captura el borrador actual. La revisión elegida debe pertenecer al perfil.',
    )
    sections = forms.MultipleChoiceField(
        label='Datos autorizados del perfil', choices=PROFILE_SECTIONS,
        widget=forms.CheckboxSelectMultiple, required=False,
        initial=['name', 'profession', 'summary', 'experiences', 'education', 'training', 'skills', 'languages', 'projects'],
    )
    pdf = forms.FileField(
        label='CV PDF con texto (un archivo)', required=False,
        widget=forms.FileInput(attrs={'accept': '.pdf,application/pdf'}),
    )
    pasted_text = forms.CharField(
        label='O pega el texto de tu CV', required=False, widget=forms.Textarea(attrs={'rows': 8}),
        help_text='Alternativa si tu PDF no tiene texto. No se ejecuta OCR.',
    )

    def __init__(self, *args, user, configuration, **kwargs):
        super().__init__(*args, **kwargs)
        self.configuration = configuration
        self.fields['profile'].queryset = CVProfile.objects.filter(user=user).exclude(status='archived')
        self.fields['revision'].queryset = CVProfileRevision.objects.filter(
            profile__user=user,
        ).select_related('profile')

    def clean_pdf(self):
        pdf = self.cleaned_data.get('pdf')
        if pdf:
            if not pdf.name.lower().endswith('.pdf') or pdf.content_type not in (
                'application/pdf', 'application/octet-stream',
            ):
                raise ValidationError('Solo se admite PDF, con extensión y tipo de archivo válidos.')
            if pdf.size > self.configuration.max_pdf_bytes:
                raise ValidationError(f'El PDF supera el límite de {self.configuration.max_pdf_bytes} bytes.')
            if pdf.read(5) != b'%PDF-':
                raise ValidationError('El contenido no es un PDF válido.')
            pdf.seek(0)
        return pdf

    def clean_pasted_text(self):
        text = self.cleaned_data['pasted_text']
        if len(text) > self.configuration.max_text_chars:
            raise ValidationError('El texto supera el límite de caracteres configurado.')
        return text

    def clean(self):
        data = super().clean()
        if sum(bool(data.get(key)) for key in ('profile', 'pdf', 'pasted_text')) != 1:
            raise ValidationError('Elige una sola fuente: perfil propio, PDF o texto pegado.')
        if data.get('revision') and (
            not data.get('profile') or data['revision'].profile_id != data['profile'].pk
        ):
            raise ValidationError('La revisión elegida no pertenece al perfil seleccionado.')
        return data


class ATSReviewForm(forms.Form):
    reviewed_text = forms.CharField(
        label='Texto autorizado para analizar', strip=False, widget=forms.Textarea(attrs={'rows': 18}),
    )
    offer_text = forms.CharField(
        label='Oferta opcional (texto)', required=False, strip=False, widget=forms.Textarea(attrs={'rows': 6}),
    )
    offer_url = forms.URLField(
        label='URL de referencia opcional (no se leerá)', max_length=2000, required=False,
        validators=[validate_offer_url],
    )
    scope = forms.ChoiceField(label='Alcance')
    confirmed = forms.BooleanField(label='He revisado el texto y autorizo este análisis privado sin IA.')
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)

    def __init__(self, *args, configuration, scopes, **kwargs):
        super().__init__(*args, **kwargs)
        self.configuration = configuration
        self.fields['scope'].choices = scopes

    def clean_reviewed_text(self):
        text = self.cleaned_data['reviewed_text']
        if not text.strip():
            raise ValidationError('No se encontró texto. Pega contenido legible o aporta otra fuente.')
        if len(text) > self.configuration.max_text_chars:
            raise ValidationError('El texto supera el límite configurado.')
        if any(ord(char) < 32 and char not in '\r\n\t\x00' for char in text):
            raise ValidationError('El texto contiene caracteres de control no admitidos.')
        return text

    def clean_offer_text(self):
        text = self.cleaned_data['offer_text']
        if len(text) > 24000:
            raise ValidationError('La oferta supera 24 000 caracteres.')
        return text

    def clean(self):
        data = super().clean()
        if data.get('offer_url') and not data.get('offer_text', '').strip():
            raise ValidationError('La extracción por URL no está disponible. Pega la oferta o elimina la URL para analizar sin oferta.')
        return data


class ATSCompareForm(forms.Form):
    before = forms.ModelChoiceField(label='Informe anterior', queryset=None)
    after = forms.ModelChoiceField(label='Informe posterior', queryset=None)
    same_cv = forms.BooleanField(
        label='Confirmo que los archivos son versiones del mismo CV', required=False,
    )

    def __init__(self, *args, reports, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['before'].queryset = reports
        self.fields['after'].queryset = reports
