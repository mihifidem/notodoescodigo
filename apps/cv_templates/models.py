from django.db import models
from django.contrib.auth import get_user_model


class TemplateCV(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    descripcion = models.TextField(blank=True)
    archivo = models.CharField(max_length=255, help_text='Nombre del archivo HTML en templates/curriculum/cv_templates/')
    exclusivo_membresia = models.BooleanField(default=False, verbose_name='Uso exclusivo de membresía', help_text='Solo usuarios con membresía pueden usar este template')
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.nombre
