from django import forms
from .models import TemplateCV

class TemplateCVForm(forms.ModelForm):
    class Meta:
        model = TemplateCV
        fields = ['nombre', 'descripcion', 'archivo']
