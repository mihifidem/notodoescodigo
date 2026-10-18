from django.contrib import admin
from .models import Certificado


@admin.register(Certificado)
class CertificadoAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'curso', 'codigo_unico', 'fecha_emision')
    search_fields = ('usuario__username', 'curso__nombre', 'codigo_unico')
    readonly_fields = ('codigo_unico', 'fecha_emision', 'pdf')
    date_hierarchy = 'fecha_emision'
