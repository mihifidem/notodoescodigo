from django import forms

from .models import Prompt


class PromptForm(forms.ModelForm):
    class Meta:
        model = Prompt
        fields = [
            'titulo',
            'descripcion',
            'contenido',
            'variables_json',
            'categoria',
            'subcategoria',
            'hashtags',
            'visibilidad',
            'imagen_prompt',
            'imagen_muestra',
            'publicado',
            'destacado',
            'publicado_en',
        ]
        widgets = {
            'titulo': forms.TextInput(attrs={'class': 'form-control'}),
            'descripcion': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'contenido': forms.Textarea(attrs={'class': 'form-control', 'rows': 8}),
            'variables_json': forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
            'categoria': forms.Select(attrs={'class': 'form-select'}),
            'subcategoria': forms.Select(attrs={'class': 'form-select'}),
            'hashtags': forms.SelectMultiple(attrs={'class': 'form-select', 'size': 8}),
            'visibilidad': forms.Select(attrs={'class': 'form-select'}),
            'imagen_prompt': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'imagen_muestra': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'publicado': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'destacado': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'publicado_en': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
        }
