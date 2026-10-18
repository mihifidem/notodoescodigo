"""Forms for analytics admin actions."""
from django import forms

from apps.gamification.models import Logro, Mision
from apps.users.models import User


class AsignarLogroForm(forms.Form):
    usuario = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True, is_staff=False).order_by('username'),
        label='Alumno',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    logro = forms.ModelChoiceField(
        queryset=Logro.objects.all().order_by('nombre'),
        label='Logro',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    puntos_asignados = forms.IntegerField(
        min_value=0,
        label='Puntos asignados tras correccion',
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['usuario'].empty_label = 'Selecciona un alumno'
        self.fields['logro'].empty_label = 'Selecciona un logro'


class EnviarMensajeForm(forms.Form):
    usuario = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True).order_by('username'),
        label='Usuario destino',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    titulo = forms.CharField(
        max_length=200,
        label='Titulo',
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    mensaje = forms.CharField(
        label='Mensaje',
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
    )

    def __init__(self, *args, request_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = User.objects.filter(is_active=True).order_by('username')
        if request_user and request_user.is_authenticated:
            queryset = queryset.exclude(id=request_user.id)
        self.fields['usuario'].queryset = queryset
        self.fields['usuario'].empty_label = 'Selecciona un usuario'


class AsignarMisionForm(forms.Form):
    usuarios = forms.ModelMultipleChoiceField(
        queryset=User.objects.filter(is_active=True, is_staff=False).order_by('username'),
        label='Alumnos',
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'size': 8}),
    )
    mision = forms.ModelChoiceField(
        queryset=Mision.objects.filter(activa=True).order_by('titulo'),
        label='Mision',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    aplicar_puntos_ahora = forms.BooleanField(
        required=False,
        initial=True,
        label='Aplicar puntos de la mision al asignarla',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['mision'].empty_label = 'Selecciona una mision'


class RecalcularPuntosForm(forms.Form):
    usuario = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True, is_staff=False).order_by('username'),
        required=False,
        label='Alumno (opcional)',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    recalcular_todos = forms.BooleanField(
        required=False,
        initial=False,
        label='Recalcular todos los alumnos',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['usuario'].empty_label = 'Selecciona un alumno'

    def clean(self):
        cleaned_data = super().clean()
        usuario = cleaned_data.get('usuario')
        recalcular_todos = cleaned_data.get('recalcular_todos')

        if not usuario and not recalcular_todos:
            raise forms.ValidationError('Selecciona un alumno o marca la opción de recalcular todos.')

        return cleaned_data


class LimpiarDuplicadosPuntosForm(forms.Form):
    usuario = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True, is_staff=False).order_by('username'),
        required=False,
        label='Alumno (opcional)',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    limpiar_todos = forms.BooleanField(
        required=False,
        initial=False,
        label='Limpiar duplicados de todos los alumnos',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['usuario'].empty_label = 'Selecciona un alumno'

    def clean(self):
        cleaned_data = super().clean()
        usuario = cleaned_data.get('usuario')
        limpiar_todos = cleaned_data.get('limpiar_todos')

        if not usuario and not limpiar_todos:
            raise forms.ValidationError('Selecciona un alumno o marca la opción de limpiar todos.')

        return cleaned_data
