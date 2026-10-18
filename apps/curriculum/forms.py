"""Forms for the Curriculum Vitae builder app."""
from django import forms
from django.forms import inlineformset_factory
from django.utils.text import slugify
from .models import (
    CurriculumVitae, PersonalInfo, ProfessionalProfile, WorkExperience,
    Education, ComplementaryTraining, Skill, Language, Project, Achievement,
    Volunteering, SocialNetwork, Interest, OtherInfo, CVProfile,
)

MAX_CV_PDF_SIZE = 5 * 1024 * 1024


class CvPdfUploadForm(forms.Form):
    pdf = forms.FileField(
        label='Archivo PDF',
        widget=forms.ClearableFileInput(attrs={
            'class': 'form-control',
            'accept': '.pdf,application/pdf',
        }),
    )

    def clean_pdf(self):
        pdf = self.cleaned_data['pdf']
        if not pdf.name.lower().endswith('.pdf'):
            raise forms.ValidationError('El archivo debe tener extensión .pdf.')
        if pdf.size > MAX_CV_PDF_SIZE:
            raise forms.ValidationError('El PDF no puede superar los 5 MB.')
        signature = pdf.read(5)
        pdf.seek(0)
        if signature != b'%PDF-':
            raise forms.ValidationError('El archivo seleccionado no parece ser un PDF válido.')
        return pdf


_DATE_WIDGET = forms.DateInput(attrs={'type': 'date', 'class': 'form-control'})
_TEXT_WIDGET = forms.Textarea(attrs={'class': 'form-control', 'rows': 3})


def _fc(widget_class='form-control', **kwargs):
    return {'class': widget_class, **kwargs}


class PersonalInfoForm(forms.ModelForm):
    class Meta:
        model = PersonalInfo
        exclude = ['cv']
        widgets = {
            'nombre': forms.TextInput(attrs=_fc()),
            'apellidos': forms.TextInput(attrs=_fc()),
            'telefono1': forms.TextInput(attrs=_fc()),
            'telefono2': forms.TextInput(attrs=_fc()),
            'email_profesional1': forms.EmailInput(attrs=_fc()),
            'email_profesional2': forms.EmailInput(attrs=_fc()),
            'ciudad': forms.TextInput(attrs=_fc()),
            'pais': forms.TextInput(attrs=_fc()),
            'codigo_postal': forms.TextInput(attrs=_fc()),
            'fecha_nacimiento': _DATE_WIDGET,
            'nacionalidad': forms.TextInput(attrs=_fc()),
            'carnet_conducir': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'disponibilidad': forms.Select(attrs=_fc()),
            'foto': forms.ClearableFileInput(attrs=_fc()),
        }


class ProfessionalProfileForm(forms.ModelForm):
    class Meta:
        model = ProfessionalProfile
        exclude = ['cv']
        widgets = {
            'profesion': forms.TextInput(attrs=_fc()),
            'objetivo': forms.TextInput(attrs=_fc()),
            'resumen': forms.Textarea(attrs={**_fc(), 'rows': 5}),
        }


class WorkExperienceForm(forms.ModelForm):
    class Meta:
        model = WorkExperience
        exclude = ['cv', 'orden']
        widgets = {
            'puesto': forms.TextInput(attrs=_fc()),
            'empresa': forms.TextInput(attrs=_fc()),
            'ubicacion': forms.TextInput(attrs=_fc()),
            'fecha_inicio': _DATE_WIDGET,
            'fecha_fin': _DATE_WIDGET,
            'trabajo_actual': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'descripcion': forms.Textarea(attrs={**_fc(), 'rows': 4}),
            'logros': forms.Textarea(attrs={**_fc(), 'rows': 3,
                                           'placeholder': 'Un logro por línea…'}),
        }

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('trabajo_actual') and not cleaned.get('fecha_fin'):
            # fecha_fin is optional (might still be current job without checking the box)
            pass
        return cleaned


class EducationForm(forms.ModelForm):
    class Meta:
        model = Education
        exclude = ['cv', 'orden']
        widgets = {
            'titulo': forms.TextInput(attrs=_fc()),
            'centro': forms.TextInput(attrs=_fc()),
            'ubicacion': forms.TextInput(attrs=_fc()),
            'fecha_inicio': _DATE_WIDGET,
            'fecha_fin': _DATE_WIDGET,
            'en_curso': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class ComplementaryTrainingForm(forms.ModelForm):
    class Meta:
        model = ComplementaryTraining
        exclude = ['cv', 'orden']
        widgets = {
            'tipo': forms.Select(attrs=_fc()),
            'nombre': forms.TextInput(attrs=_fc()),
            'entidad': forms.TextInput(attrs=_fc()),
            'fecha': _DATE_WIDGET,
            'descripcion': forms.TextInput(attrs=_fc()),
        }



class SkillForm(forms.ModelForm):
    class Meta:
        model = Skill
        exclude = ['cv', 'orden']
        widgets = {
            'tipo': forms.Select(attrs=_fc(id='id_tipo')),
            'categoria': forms.Select(attrs=_fc(id='id_categoria')),
            'nombre': forms.TextInput(attrs=_fc()),
            'nivel': forms.NumberInput(attrs={**_fc(), 'min': 1, 'max': 5}),
        }

    # El filtrado de categorías se realiza solo en el frontend con JavaScript

# ---
# Para el filtrado dinámico en el frontend, añade este JS en la plantilla donde se use el formulario:
# <script>
#   const tipoField = document.getElementById('id_tipo');
#   const categoriaField = document.getElementById('id_categoria');
#   const allOptions = Array.from(categoriaField.options);
#   tipoField.addEventListener('change', function() {
#     const tipo = tipoField.value;
#     categoriaField.innerHTML = '';
#     let filtered = allOptions.filter(opt =>
#       tipo === 'soft' ? opt.value.startsWith('s_') || opt.value === '' :
#       tipo === 'hard' ? opt.value.startsWith('h_') || opt.value === '' : true
#     );
#     filtered.forEach(opt => categoriaField.appendChild(opt.cloneNode(true)));
#   });
# </script>


class LanguageForm(forms.ModelForm):
    class Meta:
        model = Language
        exclude = ['cv', 'orden']
        widgets = {
            'idioma': forms.TextInput(attrs=_fc()),
            'nivel': forms.Select(attrs=_fc()),
            'certificacion': forms.TextInput(attrs=_fc()),
        }


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        exclude = ['cv', 'orden']
        widgets = {
            'nombre': forms.TextInput(attrs=_fc()),
            'descripcion': forms.Textarea(attrs={**_fc(), 'rows': 3}),
            'tecnologias': forms.TextInput(attrs=_fc()),
            'link': forms.URLInput(attrs=_fc()),
        }


class AchievementForm(forms.ModelForm):
    class Meta:
        model = Achievement
        exclude = ['cv', 'orden']
        widgets = {
            'titulo': forms.TextInput(attrs=_fc()),
            'tipo': forms.Select(attrs=_fc()),
            'descripcion': forms.Textarea(attrs={**_fc(), 'rows': 3}),
            'fecha': _DATE_WIDGET,
        }


class VolunteeringForm(forms.ModelForm):
    class Meta:
        model = Volunteering
        exclude = ['cv', 'orden']
        widgets = {
            'organizacion': forms.TextInput(attrs=_fc()),
            'funcion': forms.TextInput(attrs=_fc()),
            'impacto': forms.Textarea(attrs={**_fc(), 'rows': 3}),
            'fecha_inicio': _DATE_WIDGET,
            'fecha_fin': _DATE_WIDGET,
            'en_curso': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class SocialNetworkForm(forms.ModelForm):
    class Meta:
        model = SocialNetwork
        exclude = ['cv']
        widgets = {
            'linkedin': forms.URLInput(attrs={**_fc(), 'placeholder': 'https://linkedin.com/in/…'}),
            'github': forms.URLInput(attrs={**_fc(), 'placeholder': 'https://github.com/…'}),
            'portfolio': forms.URLInput(attrs={**_fc(), 'placeholder': 'https://miportfolio.com'}),
            'twitter': forms.URLInput(attrs={**_fc(), 'placeholder': 'https://twitter.com/…'}),
            'otras': forms.TextInput(attrs=_fc()),
        }


class InterestForm(forms.ModelForm):
    class Meta:
        model = Interest
        exclude = ['cv', 'orden']
        widgets = {
            'nombre': forms.TextInput(attrs=_fc()),
        }


class OtherInfoForm(forms.ModelForm):
    class Meta:
        model = OtherInfo
        exclude = ['cv']
        widgets = {
            'disponibilidad_viajar': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'teletrabajo': forms.Select(attrs=_fc()),
            'expectativa_salarial': forms.TextInput(attrs=_fc()),
            'notas': forms.Textarea(attrs={**_fc(), 'rows': 3}),
        }


from apps.cv_templates.models import TemplateCV

class CVProfileForm(forms.ModelForm):
    class Meta:
        model = CVProfile
        exclude = ['user', 'cv', 'created_at', 'experiencias', 'educaciones', 'formaciones',
                   'habilidades', 'idiomas', 'proyectos', 'logros', 'voluntariados', 'intereses', 'template']
        widgets = {
            'nombre': forms.TextInput(attrs=_fc()),
            'slug': forms.TextInput(attrs={**_fc(), 'placeholder': 'NombreApellido_perfil'}),
            'templatecv': forms.Select(attrs=_fc()),
            'is_public': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'mostrar_perfil_profesional': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'mostrar_redes': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'mostrar_intereses': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'mostrar_otros': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'mostrar_voluntariado': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'mostrar_logros': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        self.user = user
        super().__init__(*args, **kwargs)
        from apps.cv_templates.models import TemplateCV
        qs = TemplateCV.objects.all()
        if user and hasattr(user, 'role') and user.role == 'basic':
            qs = qs.filter(exclusivo_membresia=False)
        self.fields['templatecv'].queryset = qs

    def _ensure_unique_slug(self, base_slug, user=None):
        effective_user = user or self.user
        if effective_user is None and getattr(self.instance, 'user_id', None) is not None:
            effective_user = self.instance.user
        if effective_user is None:
            effective_user = self.data.get('user') if self.data else None

        candidate = slugify(base_slug).strip('-') or 'perfil'
        if not candidate:
            candidate = 'perfil'

        final_slug = candidate
        suffix = 2
        while effective_user is not None and CVProfile.objects.filter(user=effective_user, slug__iexact=final_slug).exclude(pk=self.instance.pk if self.instance and self.instance.pk else None).exists():
            final_slug = f'{candidate}-{suffix}'
            suffix += 1
        return final_slug

    def clean_slug(self):
        slug = self.cleaned_data.get('slug') or ''
        slug = slugify(slug).strip('-')
        if not slug:
            name = self.cleaned_data.get('nombre') or 'perfil'
            slug = slugify(name)
        return self._ensure_unique_slug(slug, self.user)

    def save(self, commit=True):
        obj = super().save(commit=False)

        user = self.user
        if user is None and getattr(obj, 'user_id', None) is not None:
            user = obj.user
        if user is not None:
            obj.user = user
        elif getattr(self.instance, 'user_id', None) is not None:
            obj.user = self.instance.user
        elif self.data and self.data.get('user'):
            obj.user_id = self.data.get('user')
        else:
            raise ValueError('CVProfile.user is required before saving.')

        if not obj.slug:
            obj.slug = self._ensure_unique_slug(obj.nombre or 'perfil', obj.user)
        else:
            obj.slug = self._ensure_unique_slug(obj.slug, obj.user)

        if commit:
            obj.save()
        return obj
