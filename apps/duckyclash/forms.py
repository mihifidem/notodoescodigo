from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django import forms
from django.contrib.auth import get_user_model
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.validators import MaxLengthValidator
from django.utils import timezone

from apps.courses.models import CourseCategory, CourseSector

from .models import ChallengeSubcategory, Landscape
from .selectors import resolve_player
from .rules import current_rules, validate_offer_limits
from .characters import ERAS, era_for_xp


class SectorCategoryChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.sector} / {obj.nombre}' if obj.sector_id else f'Sin sector / {obj.nombre}'


class DependentSelect(forms.Select):
    def __init__(self, parent_field, **kwargs):
        self.parent_field = parent_field
        super().__init__(**kwargs)

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value and hasattr(value, 'instance'):
            option['attrs']['data-parent'] = str(getattr(value.instance, self.parent_field) or '')
        return option


class CategoryCreateForm(forms.ModelForm):
    use_required_attribute = False

    class Meta:
        model = CourseCategory
        fields = ['sector', 'nombre', 'descripcion']
        widgets = {
            'sector': forms.Select(attrs={'class': 'form-select'}),
            'nombre': forms.TextInput(attrs={'class': 'form-control'}),
            'descripcion': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['sector'].required = True
        self.fields['sector'].label = 'Ramo / sector'


class SubcategoryCreateForm(forms.ModelForm):
    use_required_attribute = False
    sector = forms.ModelChoiceField(
        label='Sector', queryset=CourseSector.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text='Selecciona el ramo y después una de sus categorías.',
    )
    category = SectorCategoryChoiceField(
        label='Categoría de la nueva subcategoría', queryset=CourseCategory.objects.none(),
        widget=DependentSelect('sector_id', attrs={'class': 'form-select'}),
    )

    class Meta:
        model = ChallengeSubcategory
        fields = ['sector', 'category', 'name']
        widgets = {
            'category': forms.Select(attrs={'class': 'form-select'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = CourseCategory.objects.filter(activa=True).select_related('sector')
        if self.instance.pk:
            self.initial.setdefault('sector', self.instance.category.sector_id)

    def clean(self):
        data = super().clean()
        sector, category = data.get('sector'), data.get('category')
        if sector and category and category.sector_id != sector.pk:
            self.add_error('category', 'La categoría no pertenece al sector seleccionado.')
        return data


class SubcategoryAdminForm(SubcategoryCreateForm):
    class Meta(SubcategoryCreateForm.Meta):
        fields = ['sector', 'category', 'name', 'active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = CourseCategory.objects.select_related('sector')
        self.fields['sector'].help_text = (
            'Usa el botón + para crear un sector. Al crear una categoría, asígnale ese mismo sector.'
        )


class LandscapeCreateForm(forms.ModelForm):
    use_required_attribute = False

    era = forms.ChoiceField(
        label='Época del paisaje', choices=[(era.code, era.label) for era in ERAS],
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    image = forms.ImageField(
        label='Imagen del paisaje',
        widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': 'image/png,image/jpeg,image/webp'}),
    )

    class Meta:
        model = Landscape
        fields = ['name', 'era', 'image']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def clean_image(self):
        image = self.cleaned_data['image']
        if image.size > 5 * 1024 * 1024:
            raise forms.ValidationError('La imagen no puede superar 5 MB.')
        if image.image.format not in {'PNG', 'JPEG', 'WEBP'}:
            raise forms.ValidationError('Utiliza una imagen PNG, JPEG o WebP.')
        if image.image.width > 4096 or image.image.height > 4096:
            raise forms.ValidationError('La imagen no puede superar 4096 píxeles por lado.')
        return image


class DuckySpecialtyForm(forms.Form):
    specialty = forms.ChoiceField(
        label='Personaje de la época actual',
        choices=get_user_model().DUCK_SPECIALTY_CHOICES,
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}),
    )


class ScheduleForm(forms.Form):
    date = forms.DateField(label='Fecha', widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}))
    time = forms.TimeField(label='Hora', widget=forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}))
    stake = forms.IntegerField(
        label='DuckyCoins por jugador', min_value=1, max_value=1000000,
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
    )

    def __init__(self, *args, time_zone=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.time_zone = time_zone or settings.TIME_ZONE
        self.fields['stake'].max_value = current_rules()['max_stake']
        self.fields['stake'].widget.attrs['max'] = self.fields['stake'].max_value

    def clean(self):
        data = super().clean()
        data['evaluated_at'] = timezone.now()
        if data.get('stake') is not None:
            try:
                validate_offer_limits(stake=data['stake'])
            except forms.ValidationError as exc:
                self.add_error('stake', exc)
        if data.get('date') and data.get('time'):
            local = datetime.combine(data['date'], data['time'])
            with timezone.override(ZoneInfo(self.time_zone)):
                try:
                    scheduled_at = forms.DateTimeField().clean(local)
                except forms.ValidationError as exc:
                    self.add_error('time', exc)
                else:
                    if scheduled_at <= data['evaluated_at']:
                        self.add_error('date', 'La fecha y hora deben ser futuras.')
                    else:
                        data['scheduled_at'] = scheduled_at
        return data


class PrivateChallengeForm(ScheduleForm):
    sector = forms.ModelChoiceField(
        label='Ramo / sector', queryset=CourseSector.objects.all(), required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    opponent = forms.CharField(
        label='Nombre de usuario del rival', strip=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'off'}),
    )
    category = forms.ModelChoiceField(
        label='Categoría', queryset=CourseCategory.objects.none(),
        widget=DependentSelect('sector_id', attrs={'class': 'form-select'}),
    )
    subcategory = forms.ModelChoiceField(
        label='Subcategoría', queryset=ChallengeSubcategory.objects.none(), required=False,
        widget=DependentSelect('category_id', attrs={'class': 'form-select'}),
    )
    landscape = forms.ModelChoiceField(
        label='Paisaje', queryset=Landscape.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    lifetime = forms.TypedChoiceField(
        label='Plazo para responder', coerce=int, initial=60,
        choices=[(15, '15 minutos'), (30, '30 minutos'), (60, '1 hora')],
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.fields['opponent'].max_length = get_user_model()._meta.get_field(get_user_model().USERNAME_FIELD).max_length
        self.fields['opponent'].validators.append(MaxLengthValidator(self.fields['opponent'].max_length))
        self.fields['category'].queryset = CourseCategory.objects.filter(activa=True).select_related('sector')
        self.fields['subcategory'].queryset = ChallengeSubcategory.objects.filter(active=True, category__activa=True)
        era = era_for_xp(actor.puntos_totales)
        self.fields['landscape'].queryset = Landscape.objects.filter(active=True, era=era.code)
        rules = current_rules()
        lower, upper = rules['min_expiry_seconds'], rules['max_expiry_seconds']
        choices = [(minutes, label) for minutes, label in self.fields['lifetime'].choices
                   if lower <= minutes * 60 <= upper]
        if not choices:
            choices = [(minutes, f'{minutes} minutos') for minutes in sorted({
                (lower + 59) // 60, upper // 60,
            }) if minutes and lower <= minutes * 60 <= upper]
        self.fields['lifetime'].choices = choices
        if choices and self.fields['lifetime'].initial not in {minutes for minutes, _ in choices}:
            self.fields['lifetime'].initial = choices[-1][0]
        if not self.is_bound and self.initial.get('category'):
            self.initial.setdefault('sector', CourseCategory.objects.filter(
                pk=self.initial['category'],
            ).values_list('sector_id', flat=True).first())
        self.order_fields(['opponent', 'sector', 'category', 'subcategory', 'date', 'time', 'stake', 'landscape', 'lifetime'])

    def clean_opponent(self):
        username = self.cleaned_data['opponent']
        if username == self.actor.get_username():
            raise forms.ValidationError('No puedes retarte a ti mismo.')
        player = resolve_player(self.actor, username)
        if player is None:
            raise forms.ValidationError('No hay un jugador disponible con ese nombre de usuario.')
        return player

    def clean(self):
        data = super().clean()
        category, subcategory = data.get('category'), data.get('subcategory')
        if data.get('sector') and category and category.sector_id != data['sector'].pk:
            self.add_error('category', 'La categoría no pertenece al ramo seleccionado.')
        if category and subcategory and subcategory.category_id != category.pk:
            self.add_error('subcategory', 'La subcategoría no pertenece a esta categoría.')
        if data.get('scheduled_at') and data.get('lifetime'):
            now = data['evaluated_at']
            expires_at = min(now + timedelta(minutes=data['lifetime']), data['scheduled_at'] - timedelta(minutes=1))
            if expires_at <= now:
                self.add_error('time', 'Deja al menos un minuto para responder antes del duelo.')
            else:
                data['expires_at'] = expires_at
                try:
                    validate_offer_limits(stake=data.get('stake', 1), expires_at=expires_at, evaluated_at=now)
                except forms.ValidationError as exc:
                    self.add_error('lifetime', exc)
        return data


class OfferActionForm(forms.Form):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput())
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput())


class CounterOfferForm(ScheduleForm):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput())
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput())

    def __init__(self, *args, offer, **kwargs):
        self.offer = offer
        super().__init__(*args, time_zone=offer.time_zone, **kwargs)

    def clean(self):
        data = super().clean()
        if data.get('scheduled_at') and data['scheduled_at'] <= self.offer.expires_at:
            self.add_error('date', 'El duelo debe comenzar después del vencimiento original del reto.')
        return data


class PlayerSearchForm(forms.Form):
    q = forms.CharField(
        label='Buscar por nombre de usuario', min_length=2, max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'off'}),
    )


class OpenChallengeForm(PrivateChallengeForm):
    expiry_date = forms.DateField(
        label='Fecha de vencimiento', widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )
    expiry_time = forms.TimeField(
        label='Hora de vencimiento', widget=forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
    )

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, actor=actor, **kwargs)
        del self.fields['opponent']
        del self.fields['lifetime']
        self.order_fields(['sector', 'category', 'subcategory', 'date', 'time', 'stake', 'landscape', 'expiry_date', 'expiry_time'])

    def clean(self):
        data = super().clean()
        if data.get('expiry_date') and data.get('expiry_time'):
            local = datetime.combine(data['expiry_date'], data['expiry_time'])
            with timezone.override(ZoneInfo(self.time_zone)):
                try:
                    expires_at = forms.DateTimeField().clean(local)
                except forms.ValidationError as exc:
                    self.add_error('expiry_time', exc)
                else:
                    if expires_at <= timezone.now():
                        self.add_error('expiry_date', 'El vencimiento debe ser futuro.')
                    elif data.get('scheduled_at') and expires_at >= data['scheduled_at']:
                        self.add_error('expiry_time', 'El reto debe vencer antes del duelo.')
                    else:
                        data['expires_at'] = expires_at
                        try:
                            validate_offer_limits(
                                stake=data.get('stake', 1), expires_at=expires_at, evaluated_at=data['evaluated_at'],
                            )
                        except forms.ValidationError as exc:
                            self.add_error('expiry_date', exc)
        return data


class ChallengeEditForm(OpenChallengeForm):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput())
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput())


class OpenChallengeFilterForm(forms.Form):
    category = forms.ModelChoiceField(
        label='Categoría', queryset=CourseCategory.objects.filter(activa=True), required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    date = forms.DateField(
        label='Fecha del duelo', required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )
    max_stake = forms.IntegerField(
        label='Apuesta máxima', min_value=1, max_value=1000000, required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
    )
