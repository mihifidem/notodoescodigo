"""Models for the AI tutor app."""
from django.db import models
from django.conf import settings


class ConversacionIA(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='conversaciones_ia'
    )
    titulo = models.CharField(max_length=200, blank=True)
    creada_en = models.DateTimeField(auto_now_add=True)
    actualizada_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Conversación IA'
        verbose_name_plural = 'Conversaciones IA'
        ordering = ['-actualizada_en']

    def __str__(self):
        return self.titulo or f'Conversación #{self.pk}'


class MensajeIA(models.Model):
    ROL_CHOICES = [
        ('user', 'Usuario'),
        ('assistant', 'Asistente'),
    ]

    conversacion = models.ForeignKey(
        ConversacionIA, on_delete=models.CASCADE, related_name='mensajes'
    )
    rol = models.CharField(max_length=10, choices=ROL_CHOICES)
    contenido = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']
        verbose_name = 'Mensaje IA'
        verbose_name_plural = 'Mensajes IA'

    def __str__(self):
        return f'[{self.rol}] {self.contenido[:60]}'
