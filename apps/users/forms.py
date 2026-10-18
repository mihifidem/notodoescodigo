"""Forms for the users app."""
from django import forms
from django.contrib.auth.forms import UserCreationForm
from allauth.account.forms import ResetPasswordForm, ResetPasswordKeyForm

from .models import Aula, User


CODIGO_ACCESO_ALUMNO = '010819'


class RegistroForm(UserCreationForm):
    email = forms.EmailField(required=True, label='Correo electrónico')
    first_name = forms.CharField(max_length=30, required=True, label='Nombre')
    last_name = forms.CharField(max_length=30, required=True, label='Apellidos')
    codigo_acceso = forms.CharField(
        required=True,
        label='Código de acceso',
        help_text='Introduce el código que te haya facilitado el centro.',
    )
    aula = forms.ModelChoiceField(
        queryset=Aula.objects.order_by('nombre'),
        required=False,
        label='Aula',
        empty_label='Selecciona un aula',
    )

    class Meta:
        model = User
        fields = (
            'username', 'first_name', 'last_name', 'email',
            'codigo_acceso', 'aula', 'password1', 'password2',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            css = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'
            field.widget.attrs['class'] = css
        self.fields['aula'].widget.attrs['disabled'] = 'disabled'

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        codigo_acceso = (self.cleaned_data.get('codigo_acceso') or '').strip()

        if codigo_acceso == CODIGO_ACCESO_ALUMNO:
            user.role = 'alumno'
            user.aula = self.cleaned_data.get('aula')
        else:
            user.role = 'basic'
            user.aula = None

        if commit:
            user.save()
        return user


class PerfilForm(forms.ModelForm):
    codigo_acceso_alumno = forms.CharField(
        required=False,
        label='Codigo de validacion alumno',
        help_text='Introduce 010819 para habilitar el aula y validar el rol alumno.',
    )

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email', 'bio', 'avatar', 'fecha_nacimiento', 'aula')
        widgets = {
            'bio': forms.Textarea(attrs={'rows': 3}),
            'fecha_nacimiento': forms.DateInput(attrs={'type': 'date'}),
            'aula': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_aula = getattr(self.instance, 'aula', None)
        self._original_role = getattr(self.instance, 'role', None)
        self._user = kwargs.get('instance')

        is_alumno = getattr(self.instance, 'role', None) == 'alumno'
        has_aula = self._original_aula is not None

        if is_alumno and has_aula:
            self.fields.pop('codigo_acceso_alumno', None)

        for field_name, field in self.fields.items():
            if field_name == 'aula':
                field.widget.attrs['class'] = 'form-select'
            else:
                field.widget.attrs['class'] = field.widget.attrs.get('class', 'form-control')

        if 'codigo_acceso_alumno' in self.fields:
            self.fields['codigo_acceso_alumno'].widget.attrs.update(
                {'placeholder': 'Codigo alumno'}
            )

        # Control de disponibilidad del campo aula
        is_staff = getattr(self.instance, 'is_staff', False)

        if is_staff:
            # Admin siempre puede cambiar el aula
            self.fields['aula'].widget.attrs['data-alumno'] = 'true'
        elif is_alumno and has_aula:
            # Alumno con aula asignada: no puede cambiarla
            self.fields['aula'].widget.attrs['disabled'] = 'disabled'
            self.fields['aula'].widget.attrs['title'] = 'Tu aula no puede ser modificada. Contacta al administrador si necesitas cambiarla.'
        elif is_alumno and not has_aula:
            # Alumno sin aula: puede seleccionar al introducir código
            self.fields['aula'].widget.attrs['data-alumno'] = 'true'
        else:
            # No-alumno (basic/premium): deshabilitado hasta introducir código
            self.fields['aula'].widget.attrs['disabled'] = 'disabled'

    def clean(self):
        cleaned_data = super().clean()
        codigo = (cleaned_data.get('codigo_acceso_alumno') or '').strip()

        if codigo and codigo != CODIGO_ACCESO_ALUMNO:
            self.add_error('codigo_acceso_alumno', 'Codigo incorrecto.')

        # Lógica de aula según rol y estado actual
        is_alumno = getattr(self.instance, 'role', None) == 'alumno'
        is_staff = getattr(self.instance, 'is_staff', False)
        has_aula = self._original_aula is not None

        if is_staff:
            # Admin puede cambiar siempre
            pass
        elif is_alumno and has_aula:
            # Alumno con aula asignada: preservar el aula original
            cleaned_data['aula'] = self._original_aula
        elif is_alumno and not has_aula:
            # Alumno sin aula: permitir si código es válido
            if codigo == CODIGO_ACCESO_ALUMNO:
                cleaned_data['aula'] = cleaned_data.get('aula') or self._original_aula
            else:
                cleaned_data['aula'] = self._original_aula
        else:
            # No-alumno: solo permitir si código es válido
            if codigo == CODIGO_ACCESO_ALUMNO:
                cleaned_data['aula'] = cleaned_data.get('aula') or self._original_aula
            else:
                cleaned_data['aula'] = self._original_aula

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        codigo = (self.cleaned_data.get('codigo_acceso_alumno') or '').strip()
        is_staff = getattr(user, 'is_staff', False)
        has_aula = self._original_aula is not None

        if is_staff:
            # Admin puede cambiar el rol y aula
            user.aula = self.cleaned_data.get('aula') or self._original_aula
        elif hasattr(self.instance, 'role') and self.instance.role == 'alumno' and has_aula:
            # Alumno con aula asignada: NO cambiar el aula
            user.aula = self._original_aula
        elif codigo == CODIGO_ACCESO_ALUMNO:
            # Código válido: permitir cambios
            user.role = 'alumno'
            user.aula = self.cleaned_data.get('aula') or self._original_aula
        else:
            # Preservar valores originales
            user.role = self._original_role or user.role
            user.aula = self._original_aula

        if commit:
            user.save()
        return user


def _add_bootstrap_classes(form):
    """Add form-control / form-select CSS classes to all widgets."""
    for field in form.fields.values():
        css = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'
        field.widget.attrs.setdefault('class', css)


class BootstrapResetPasswordForm(ResetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _add_bootstrap_classes(self)


class BootstrapResetPasswordKeyForm(ResetPasswordKeyForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _add_bootstrap_classes(self)
