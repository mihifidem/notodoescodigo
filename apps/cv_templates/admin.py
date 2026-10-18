from django.contrib import admin
from .models import TemplateCV

@admin.register(TemplateCV)
class TemplateCVAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'archivo', 'fecha_creacion', 'fecha_actualizacion')
    search_fields = ('nombre', 'archivo')
