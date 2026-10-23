"""Forms for the users app."""
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.db.models import Q
from allauth.account.forms import ResetPasswordForm, ResetPasswordKeyForm

from .dashboard_models import PersonalGoal
from .models import RamaProfesional, User


class RegistroForm(UserCreationForm):
    email = forms.EmailField(required=True, label='Correo electrónico')
    first_name = forms.CharField(max_length=30, required=True, label='Nombre')
    last_name = forms.CharField(max_length=30, required=True, label='Apellidos')
    rama_principal = forms.ModelChoiceField(
        queryset=RamaProfesional.objects.none(),
        required=False,
        empty_label='Sin seleccionar',
        label='Rama profesional principal',
        help_text='Es una preferencia para personalizar recomendaciones. Inglés no acredita un nivel ni una profesión.',
    )

    class Meta:
        model = User
        fields = (
            'username', 'first_name', 'last_name', 'email', 'password1', 'password2',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['rama_principal'].queryset = RamaProfesional.objects.filter(
            is_active=True,
        )
        for field_name, field in self.fields.items():
            css = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'
            field.widget.attrs['class'] = css

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        user.role = 'usuario'
        user.rama_principal = self.cleaned_data.get('rama_principal')

        if commit:
            user.save()
        return user


class PerfilForm(forms.ModelForm):
    class Meta:
        model = User
        fields = (
            'first_name', 'last_name', 'email', 'bio', 'avatar',
            'fecha_nacimiento', 'rama_principal', 'aula',
        )
        widgets = {
            'bio': forms.Textarea(attrs={'rows': 3}),
            'fecha_nacimiento': forms.DateInput(attrs={'type': 'date'}),
            'aula': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_aula = getattr(self.instance, 'aula', None)
        self._original_role = getattr(self.instance, 'role', None)
        self._can_manage_aula = self.instance.is_superuser or self.instance.has_perm('users.change_user')
        branch_queryset = RamaProfesional.objects.filter(is_active=True)
        if self.instance.rama_principal_id and not self.instance.rama_principal.is_active:
            branch_queryset = RamaProfesional.objects.filter(
                Q(is_active=True) | Q(pk=self.instance.rama_principal_id)
            )
        self.fields['rama_principal'].queryset = branch_queryset
        self.fields['rama_principal'].required = False
        self.fields['rama_principal'].empty_label = 'Sin seleccionar'
        self.fields['rama_principal'].help_text = (
            'La rama solo personaliza recomendaciones; no cambia tu plan ni tus roles. '
            'Inglés no certifica nivel de idioma ni profesión.'
        )

        for field_name, field in self.fields.items():
            if field_name in {'aula', 'rama_principal'}:
                field.widget.attrs['class'] = 'form-select'
            else:
                field.widget.attrs['class'] = field.widget.attrs.get('class', 'form-control')

        if self._can_manage_aula:
            self.fields['aula'].widget.attrs['data-alumno'] = 'true'
        else:
            self.fields['aula'].widget.attrs['disabled'] = 'disabled'

    def clean(self):
        cleaned_data = super().clean()
        if not self._can_manage_aula:
            cleaned_data['aula'] = self._original_aula
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        if self._can_manage_aula:
            user.aula = self.cleaned_data.get('aula') or self._original_aula
        else:
            user.role = self._original_role or user.role
            user.aula = self._original_aula
        user.rama_principal = self.cleaned_data.get('rama_principal')

        if commit:
            user.save()
        return user


class PersonalGoalForm(forms.ModelForm):
    class Meta:
        model = PersonalGoal
        fields = (
            'title',
            'description',
            'tracking_type',
            'source_metric',
            'current_value',
            'target_value',
            'target_label',
            'target_date',
        )
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'current_value': forms.NumberInput(attrs={'min': '0', 'step': '0.01'}),
            'target_value': forms.NumberInput(attrs={'min': '0.01', 'step': '0.01'}),
            'target_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault(
                'class',
                'form-select' if isinstance(field.widget, forms.Select) else 'form-control',
            )

    def clean(self):
        cleaned_data = super().clean()
        return cleaned_data

    def save(self, commit=True):
        goal = super().save(commit=False)
        goal.full_clean(exclude=['user', 'created_at', 'updated_at', 'completed_at', 'archived_at'])
        if commit:
            goal.save()
        return goal


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
