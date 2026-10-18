from django import forms

from .models import BugReport


class BugReportCreateForm(forms.ModelForm):
    class Meta:
        model = BugReport
        fields = ['titulo', 'descripcion', 'pasos_reproduccion', 'url_afectada']
        widgets = {
            'titulo': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej: Error al abrir evaluaciones'}),
            'descripcion': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'pasos_reproduccion': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'url_afectada': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '/evaluaciones/123/'}),
        }


class BugReportReviewForm(forms.ModelForm):
    class Meta:
        model = BugReport
        fields = ['estado', 'puntos_premio', 'comentarios_admin']
        widgets = {
            'estado': forms.Select(attrs={'class': 'form-select'}),
            'puntos_premio': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'comentarios_admin': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
        }

    def clean(self):
        cleaned_data = super().clean()
        estado = cleaned_data.get('estado')
        puntos_premio = cleaned_data.get('puntos_premio')

        if estado == BugReport.ESTADO_VALIDADO and (puntos_premio is None or puntos_premio < 0):
            raise forms.ValidationError('Debes indicar puntos de premio validos para aprobar el bug.')

        return cleaned_data
