from django import forms

from .models import EntregaEvaluacion


class EntregaAlumnoForm(forms.ModelForm):
    class Meta:
        model = EntregaEvaluacion
        fields = ['archivo_respuesta', 'solicita_revision_exhaustiva', 'motivo_revision_exhaustiva']
        widgets = {
            'archivo_respuesta': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'solicita_revision_exhaustiva': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'motivo_revision_exhaustiva': forms.Textarea(
                attrs={
                    'class': 'form-control',
                    'rows': 3,
                    'placeholder': 'Opcional: explica por qué solicitas una revisión más exhaustiva.',
                }
            ),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('solicita_revision_exhaustiva') and not cleaned.get('motivo_revision_exhaustiva', '').strip():
            self.add_error(
                'motivo_revision_exhaustiva',
                'Indica un motivo cuando solicitas revisión exhaustiva docente.',
            )
        return cleaned


class EntregaEditForm(forms.ModelForm):
    class Meta:
        model = EntregaEvaluacion
        fields = ['archivo_respuesta', 'solicita_revision_exhaustiva', 'motivo_revision_exhaustiva']
        widgets = {
            'archivo_respuesta': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'solicita_revision_exhaustiva': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'motivo_revision_exhaustiva': forms.Textarea(
                attrs={
                    'class': 'form-control',
                    'rows': 3,
                    'placeholder': 'Opcional: explica por qué solicitas una revisión más exhaustiva.',
                }
            ),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('solicita_revision_exhaustiva') and not cleaned.get('motivo_revision_exhaustiva', '').strip():
            self.add_error(
                'motivo_revision_exhaustiva',
                'Indica un motivo cuando solicitas revisión exhaustiva docente.',
            )
        return cleaned
