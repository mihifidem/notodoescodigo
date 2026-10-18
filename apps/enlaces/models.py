from django.conf import settings
from django.db import models


class EnlaceImportante(models.Model):
    titulo = models.CharField(max_length=150)
    url = models.URLField(max_length=500)
    categoria = models.CharField(max_length=80, default='General')
    comentario = models.TextField(help_text='Comentario visible para los usuarios')
    activo = models.BooleanField(default=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='enlaces_creados',
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Enlace importante'
        verbose_name_plural = 'Enlaces importantes'
        ordering = ['-creado_en']

    def __str__(self):
        return self.titulo


class AccesoEnlaceUsuario(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='accesos_enlaces',
    )
    enlace = models.ForeignKey(
        EnlaceImportante,
        on_delete=models.CASCADE,
        related_name='accesos',
    )
    accedido_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Acceso a enlace'
        verbose_name_plural = 'Accesos a enlaces'
        unique_together = ('usuario', 'enlace')
        ordering = ['-accedido_en']

    def __str__(self):
        return f'{self.usuario.username} -> {self.enlace.titulo}'
