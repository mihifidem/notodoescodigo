from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.text import slugify

from .models import CategoriaPrompt, Prompt, SubcategoriaPrompt


class PromptForm(forms.ModelForm):
    nueva_categoria = forms.CharField(
        label='Nueva categoría', max_length=120, required=False,
        help_text='Deja el selector de categoría vacío para crear una nueva al guardar.',
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    nueva_subcategoria = forms.CharField(
        label='Nueva subcategoría', max_length=120, required=False,
        help_text='Se creará dentro de la categoría seleccionada o nueva.',
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['categoria'].required = False

    def clean(self):
        data = super().clean()
        category = data.get('categoria')
        subcategory = data.get('subcategoria')
        new_category = data.get('nueva_categoria')
        new_subcategory = data.get('nueva_subcategoria')
        if new_category and category:
            self.add_error('nueva_categoria', 'Selecciona una categoría existente o crea una nueva, no ambas.')
        elif new_category:
            candidate = CategoriaPrompt(nombre=new_category, slug=slugify(new_category))
            try:
                candidate.full_clean()
            except ValidationError as error:
                self.add_error('nueva_categoria', forms.ValidationError(error.messages))
            if CategoriaPrompt.objects.filter(nombre__iexact=new_category).exists():
                self.add_error('nueva_categoria', 'Ya existe esta categoría. Selecciónala en el listado.')
        elif not category:
            self.add_error('categoria', 'Selecciona una categoría o indica una nueva.')
        if new_subcategory and subcategory:
            self.add_error('nueva_subcategoria', 'Selecciona una subcategoría existente o crea una nueva, no ambas.')
        if subcategory and (new_category or category and subcategory.categoria_id != category.pk):
            self.add_error('subcategoria', 'La subcategoría debe pertenecer a la categoría seleccionada.')
        if new_subcategory:
            slug = slugify(new_subcategory)
            if not slug:
                self.add_error('nueva_subcategoria', 'Indica un nombre con letras o números.')
            if category and SubcategoriaPrompt.objects.filter(categoria=category).filter(
                Q(nombre__iexact=new_subcategory) | Q(slug=slug)
            ).exists():
                self.add_error('nueva_subcategoria', 'Ya existe esta subcategoría en la categoría seleccionada.')
        return data

    def create_taxonomy(self):
        if self.cleaned_data.get('nueva_categoria'):
            self.instance.categoria = CategoriaPrompt.objects.create(
                nombre=self.cleaned_data['nueva_categoria'],
            )
        if self.cleaned_data.get('nueva_subcategoria'):
            self.instance.subcategoria = SubcategoriaPrompt.objects.create(
                categoria=self.instance.categoria,
                nombre=self.cleaned_data['nueva_subcategoria'],
            )

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
