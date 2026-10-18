"""Models for the certifications app."""
import uuid
from django.db import models
from django.conf import settings


class Certificado(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='certificados'
    )
    curso = models.ForeignKey(
        'courses.Curso', on_delete=models.CASCADE, related_name='certificados'
    )
    codigo_unico = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    fecha_emision = models.DateTimeField(auto_now_add=True)
    pdf = models.FileField(upload_to='certificados/', null=True, blank=True)

    class Meta:
        unique_together = ('usuario', 'curso')
        verbose_name = 'Certificado'
        verbose_name_plural = 'Certificados'
        ordering = ['-fecha_emision']

    def __str__(self):
        return f'{self.usuario.get_full_name() or self.usuario.username} — {self.curso.nombre}'

    def ensure_pdf_exists(self):
        from .utils import generar_y_guardar_certificado

        if not self.pdf or not self.pdf.storage.exists(self.pdf.name):
            generar_y_guardar_certificado(self)

    def get_validation_url(self):
        from django.conf import settings as conf
        return f'{conf.SITE_URL}/certificados/validar/{self.codigo_unico}/'
