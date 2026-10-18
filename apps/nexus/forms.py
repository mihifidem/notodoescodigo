from django import forms
from django.forms import inlineformset_factory
from .models import NexusModule, Question, AnswerOption, Assessment, DNAProfile, TestPlantilla, RecursoTest

_ctrl = 'form-control'
_sel = 'form-select'
_chk = 'form-check-input'


class NexusModuleForm(forms.ModelForm):
    class Meta:
        model = NexusModule
        fields = ['code', 'name', 'layer', 'description', 'weight', 'color', 'active', 'order']
        widgets = {
            'code': forms.TextInput(attrs={'class': _ctrl, 'placeholder': 'e.g. IDENTITY_MAP'}),
            'name': forms.TextInput(attrs={'class': _ctrl, 'placeholder': 'e.g. IdentityMap'}),
            'layer': forms.Select(attrs={'class': _sel}),
            'description': forms.Textarea(attrs={'class': _ctrl, 'rows': 3}),
            'weight': forms.NumberInput(attrs={'class': _ctrl, 'step': '0.0001', 'min': '0', 'max': '1'}),
            'color': forms.TextInput(attrs={'class': 'form-control form-control-color', 'type': 'color'}),
            'active': forms.CheckboxInput(attrs={'class': _chk}),
            'order': forms.NumberInput(attrs={'class': _ctrl, 'min': '0'}),
        }

    def clean_weight(self):
        w = self.cleaned_data['weight']
        if w < 0 or w > 1:
            raise forms.ValidationError('Weight must be between 0 and 1.')
        return w


class QuestionForm(forms.ModelForm):
    class Meta:
        model = Question
        fields = ['text', 'question_type', 'order', 'active']
        widgets = {
            'text': forms.Textarea(attrs={'class': _ctrl, 'rows': 3, 'placeholder': 'Enter the question text...'}),
            'question_type': forms.Select(attrs={'class': _sel}),
            'order': forms.NumberInput(attrs={'class': _ctrl, 'min': '0'}),
            'active': forms.CheckboxInput(attrs={'class': _chk}),
        }


class AnswerOptionForm(forms.ModelForm):
    class Meta:
        model = AnswerOption
        fields = ['text', 'value', 'order']
        widgets = {
            'text': forms.TextInput(attrs={'class': _ctrl, 'placeholder': 'Option text'}),
            'value': forms.NumberInput(attrs={'class': _ctrl, 'step': '0.01', 'min': '0', 'max': '100'}),
            'order': forms.NumberInput(attrs={'class': _ctrl, 'min': '0', 'style': 'width:80px'}),
        }


AnswerOptionFormSet = inlineformset_factory(
    Question,
    AnswerOption,
    form=AnswerOptionForm,
    extra=3,
    can_delete=True,
    min_num=0,
    validate_min=False,
)


class AssessmentForm(forms.ModelForm):
    class Meta:
        model = Assessment
        fields = ['title', 'description', 'modules', 'status']
        widgets = {
            'title': forms.TextInput(attrs={'class': _ctrl, 'placeholder': 'Assessment title'}),
            'description': forms.Textarea(attrs={'class': _ctrl, 'rows': 3, 'placeholder': 'Optional description'}),
            'status': forms.Select(attrs={'class': _sel}),
            'modules': forms.CheckboxSelectMultiple(),
        }


class DNAProfileForm(forms.ModelForm):
    class Meta:
        model = DNAProfile
        fields = [
            'nexus_score', 'primary_archetype', 'secondary_archetype', 'tertiary_archetype',
            'leadership_score', 'innovation_score', 'technology_score', 'future_score',
            'career_score', 'executive_score', 'motivation_score', 'cognitive_score',
            'emotional_score', 'reliability',
        ]
        widgets = {f: forms.NumberInput(attrs={'class': _ctrl, 'step': '0.01', 'min': '0', 'max': '100'})
                   for f in ['nexus_score', 'leadership_score', 'innovation_score', 'technology_score',
                              'future_score', 'career_score', 'executive_score', 'motivation_score',
                              'cognitive_score', 'emotional_score', 'reliability']}
        widgets['primary_archetype'] = forms.Select(attrs={'class': _sel})
        widgets['secondary_archetype'] = forms.Select(attrs={'class': _sel})
        widgets['tertiary_archetype'] = forms.Select(attrs={'class': _sel})


class TestPlantillaForm(forms.ModelForm):
    class Meta:
        model = TestPlantilla
        fields = ['nombre', 'descripcion', 'archivo_html', 'activo']
        widgets = {
            'nombre': forms.TextInput(attrs={
                'class': _ctrl,
                'placeholder': 'Ej: Test de Liderazgo Nexus DNA',
            }),
            'descripcion': forms.Textarea(attrs={
                'class': _ctrl, 'rows': 3,
                'placeholder': 'Descripción breve visible para los alumnos',
            }),
            'archivo_html': forms.ClearableFileInput(attrs={
                'class': 'form-control',
                'accept': '.html,.htm',
            }),
            'activo': forms.CheckboxInput(attrs={'class': _chk}),
        }

    def clean_archivo_html(self):
        f = self.cleaned_data.get('archivo_html')
        if f:
            nombre = f.name.lower()
            if not (nombre.endswith('.html') or nombre.endswith('.htm')):
                raise forms.ValidationError('Solo se aceptan archivos .html o .htm')
        return f


class RecursoTestForm(forms.ModelForm):
    class Meta:
        model = RecursoTest
        fields = ['tipo', 'titulo', 'descripcion', 'archivo', 'duracion_o_paginas', 'url_externa', 'orden', 'activo']
        widgets = {
            'tipo':              forms.Select(attrs={'class': _sel}),
            'titulo':            forms.TextInput(attrs={'class': _ctrl, 'placeholder': 'Título del recurso'}),
            'descripcion':       forms.Textarea(attrs={'class': _ctrl, 'rows': 2, 'placeholder': 'Descripción breve (opcional)'}),
            'archivo':           forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'duracion_o_paginas': forms.TextInput(attrs={'class': _ctrl, 'placeholder': 'Ej.: 4:30 o 12 páginas'}),
            'url_externa':       forms.URLInput(attrs={'class': _ctrl, 'placeholder': 'https://...'}),
            'orden':             forms.NumberInput(attrs={'class': _ctrl, 'min': '0', 'style': 'width:80px'}),
            'activo':            forms.CheckboxInput(attrs={'class': _chk}),
        }

    def clean(self):
        cleaned = super().clean()
        archivo = cleaned.get('archivo')
        url = cleaned.get('url_externa')
        if not archivo and not url:
            raise forms.ValidationError('Debes proporcionar un archivo o una URL externa.')
        return cleaned
