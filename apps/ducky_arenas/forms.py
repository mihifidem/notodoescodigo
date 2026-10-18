import re

from django import forms
from django.core.exceptions import ValidationError

from .config import DUCKY_ALLOWED_AVATAR_TYPES, DUCKY_TYPES
from .models import UserProfileArenas


class DuckyOnboardingForm(forms.Form):
    nickname = forms.CharField(
        required=True,
        min_length=2,
        max_length=30,
        strip=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej: DuckyNova'}),
    )
    avatarType = forms.ChoiceField(
        required=True,
        choices=[(key, DUCKY_TYPES[key]['label']) for key in DUCKY_ALLOWED_AVATAR_TYPES],
        widget=forms.HiddenInput(),
    )
    gender = forms.ChoiceField(
        required=True,
        choices=UserProfileArenas.Gender.choices,
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}),
    )

    def clean_nickname(self):
        nickname = self.cleaned_data['nickname'].strip()
        if not nickname:
            raise ValidationError('El nombre del Ducky es obligatorio.')
        if len(nickname) < 2:
            raise ValidationError('El nickname debe tener al menos 2 caracteres.')
        if len(nickname) > 30:
            raise ValidationError('El nickname no puede superar 30 caracteres.')
        if not re.fullmatch(r"[A-Za-z0-9À-ÿ _-]+", nickname):
            raise ValidationError('Solo puedes usar letras, números, espacios, guiones y guiones bajos.')
        return nickname

    def clean_avatarType(self):
        value = self.cleaned_data['avatarType']
        if value != 'PREHISTORIC':
            raise ValidationError('Tu Ducky empieza en la época prehistórica.')
        return value

    def clean_gender(self):
        value = (self.cleaned_data.get('gender') or '').strip().upper()
        if not value:
            raise ValidationError('Selecciona hombre o mujer para tu pato o pata.')
        if value not in dict(UserProfileArenas.Gender.choices):
            raise ValidationError('Selecciona un género válido: Chico o Chica.')
        return value


class BattleChallengeForm(forms.Form):
    visibility = forms.ChoiceField(
        label='Tipo de reto', choices=[('public', 'Público'), ('direct', 'Directo')],
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    opponent = forms.CharField(
        label='Usuario rival', required=False, max_length=150,
        help_text='Solo para retos directos: nombre de usuario de un jugador con Ducky creado.',
        widget=forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'off'}),
    )
    stake = forms.IntegerField(
        label='Apuesta propuesta por jugador (DuckyCoins)', min_value=0, max_value=1000000,
        initial=0, help_text='No se reservan ni descuentan monedas en esta fase.',
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
    )
    lifetime = forms.TypedChoiceField(
        label='Caducidad', coerce=int, initial=30,
        choices=[(15, '15 minutos'), (30, '30 minutos'), (60, '1 hora')],
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor

    def clean(self):
        data = super().clean()
        username = data.get('opponent', '')
        if data.get('visibility') == 'direct':
            profile = UserProfileArenas.objects.select_related('user').filter(
                user__username=username, user__is_active=True, onboardingCompleted=True,
            ).first()
            if profile is None:
                self.add_error('opponent', 'No hay un jugador disponible con ese nombre de usuario.')
            elif profile.user_id == self.actor.pk:
                self.add_error('opponent', 'No puedes retarte a ti mismo.')
            else:
                data['opponent'] = profile.user
        elif data.get('visibility') == 'public':
            if username:
                self.add_error('opponent', 'Un reto público no debe indicar un rival.')
            data['opponent'] = None
        return data


class BattleChallengeActionForm(forms.Form):
    action = forms.ChoiceField(choices=[(key, key) for key in ('accept', 'counter', 'decline', 'cancel')])
    challenge_id = forms.IntegerField(min_value=1)
    revision = forms.IntegerField(min_value=0)
    stake = forms.IntegerField(min_value=0, max_value=1000000, required=False)

    def clean(self):
        data = super().clean()
        if data.get('action') == 'counter' and data.get('stake') is None:
            self.add_error('stake', 'Indica una apuesta entera para la contrapropuesta.')
        return data
