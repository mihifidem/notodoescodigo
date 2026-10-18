from django import forms

from .models import Insignia


class InsigniaForm(forms.ModelForm):
    class Meta:
        model = Insignia
        fields = [
            'nombre',
            'descripcion',
            'imagen',
            'tipo',
            'requisito_valor',
            'curso_objetivo',
            'tema_objetivo',
            'visible',
        ]
        widgets = {
            'nombre': forms.TextInput(attrs={'class': 'form-control'}),
            'descripcion': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'imagen': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'tipo': forms.Select(attrs={'class': 'form-select'}),
            'requisito_valor': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'curso_objetivo': forms.Select(attrs={'class': 'form-select'}),
            'tema_objetivo': forms.Select(attrs={'class': 'form-select'}),
            'visible': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
