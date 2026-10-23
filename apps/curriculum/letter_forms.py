import ipaddress
from urllib.parse import urlsplit

from django import forms
from django.core.exceptions import ValidationError

from .models import CVProfile, CoverLetter


PROFILE_SECTIONS = [
    ('name', 'Nombre'), ('profession', 'Titular profesional'),
    ('summary', 'Presentación profesional'), ('experiences', 'Experiencia'),
    ('education', 'Formación'), ('training', 'Formación complementaria'),
    ('skills', 'Habilidades'), ('languages', 'Idiomas'), ('projects', 'Proyectos'),
    ('achievements', 'Logros'), ('volunteering', 'Voluntariado'),
    ('location', 'Ubicación'), ('email', 'Email visible en este perfil'),
    ('phone', 'Teléfono visible en este perfil'),
]


def validate_offer_url(value):
    if not value:
        return value
    parsed = urlsplit(value)
    host = (parsed.hostname or '').lower().rstrip('.')
    if (
        parsed.scheme not in {'http', 'https'} or not host
        or parsed.username or parsed.password
        or host in {'localhost', 'localhost.localdomain'}
        or host.endswith(('.localhost', '.local', '.internal'))
    ):
        raise ValidationError('Utiliza una URL HTTP/HTTPS pública sin credenciales.')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValidationError('No se admiten direcciones de redes internas o locales.')
    return value


class LetterProfileChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.nombre} — {obj.professional_headline or "Sin titular"}'


class NewLetterForm(forms.Form):
    title = forms.CharField(label='Título interno', max_length=200)
    profile = LetterProfileChoice(label='Perfil profesional', queryset=CVProfile.objects.none())
    offer_text = forms.CharField(
        label='Texto de la oferta', required=False, strip=False, widget=forms.Textarea(attrs={'rows': 8}),
        help_text='Pega la oferta completa. No inventaremos datos que no aporte.',
    )
    offer_url = forms.URLField(
        label='URL de referencia (opcional)', max_length=2000, required=False,
        validators=[validate_offer_url],
        help_text='El conector de extracción no está disponible. La URL se guarda solo como referencia.',
    )
    position = forms.CharField(label='Puesto (si se conoce)', max_length=200, required=False)
    company = forms.CharField(label='Empresa (si se conoce)', max_length=200, required=False)
    language = forms.ChoiceField(label='Idioma', choices=CoverLetter._meta.get_field('language').choices)
    tone = forms.ChoiceField(label='Tono', choices=CoverLetter._meta.get_field('tone').choices)
    length = forms.ChoiceField(label='Extensión', choices=CoverLetter._meta.get_field('length').choices)
    recipient = forms.CharField(label='Destinatario (opcional)', max_length=200, required=False)
    highlights = forms.CharField(
        label='Hechos reales que destacar', max_length=2000, required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
    )
    notes = forms.CharField(
        label='Indicaciones adicionales', max_length=2000, required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
    )
    sections = forms.MultipleChoiceField(
        label='Datos autorizados para esta carta', choices=PROFILE_SECTIONS,
        widget=forms.CheckboxSelectMultiple, required=False,
        initial=['name', 'profession', 'summary', 'experiences', 'education', 'skills'],
    )

    def __init__(self, *args, user, configuration, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['profile'].queryset = CVProfile.objects.filter(
            user=user,
        ).exclude(status=CVProfile.Status.ARCHIVED).select_related('rama_principal')
        self.fields['offer_text'].max_length = configuration.max_offer_length
        self.offer_limit = configuration.max_offer_length

    def clean_offer_text(self):
        text = self.cleaned_data['offer_text']
        if not text.strip():
            raise ValidationError('Pega el texto de la oferta: la extracción por URL no está disponible.')
        if len(text) > self.offer_limit:
            raise ValidationError(f'La oferta supera el límite de {self.offer_limit} caracteres.')
        if len(text.split()) < 8:
            raise ValidationError('Incluye al menos ocho palabras con las funciones o requisitos de la oferta.')
        return text


class EditLetterForm(forms.Form):
    title = forms.CharField(label='Título interno', max_length=200)
    content = forms.CharField(
        label='Contenido de la carta', strip=False,
        widget=forms.Textarea(attrs={'rows': 18}),
    )
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)

    def __init__(self, *args, configuration, **kwargs):
        super().__init__(*args, **kwargs)
        self.content_limit = configuration.max_content_length

    def clean_content(self):
        content = self.cleaned_data['content']
        if not content.strip():
            raise ValidationError('Escribe el contenido antes de guardar una versión.')
        if len(content) > self.content_limit:
            raise ValidationError(f'El contenido supera el límite de {self.content_limit} caracteres.')
        if any(ord(char) < 32 and char not in '\r\n\t' for char in content):
            raise ValidationError('El contenido contiene caracteres de control no admitidos.')
        return content


class ReviewLetterForm(forms.Form):
    token = forms.CharField(widget=forms.HiddenInput, max_length=300000)
    confirmed = forms.BooleanField(label='He revisado y autorizo únicamente los datos mostrados.')


class LetterActionForm(forms.Form):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)


class GenerateLetterForm(LetterActionForm):
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)
