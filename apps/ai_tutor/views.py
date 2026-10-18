"""Views for the AI tutor app."""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView, View
from django.shortcuts import get_object_or_404
from django.http import JsonResponse
import json

from .models import ConversacionIA, MensajeIA
from .services import obtener_respuesta_ia, recomendar_contenido


class ChatView(LoginRequiredMixin, TemplateView):
    template_name = 'ai_tutor/chat.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        conv_id = self.kwargs.get('conv_id')
        if conv_id:
            conversacion = get_object_or_404(
                ConversacionIA, pk=conv_id, usuario=self.request.user
            )
        else:
            conversacion = None

        context['conversacion'] = conversacion
        context['historial_convs'] = ConversacionIA.objects.filter(
            usuario=self.request.user
        )[:15]
        context['recomendaciones'] = recomendar_contenido(self.request.user)
        return context


class EnviarMensajeView(LoginRequiredMixin, View):
    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'JSON inválido.'}, status=400)

        pregunta = data.get('mensaje', '').strip()
        if not pregunta:
            return JsonResponse({'error': 'El mensaje no puede estar vacío.'}, status=400)

        conv_id = data.get('conversacion_id')
        if conv_id:
            conversacion = get_object_or_404(
                ConversacionIA, pk=conv_id, usuario=request.user
            )
        else:
            conversacion = ConversacionIA.objects.create(
                usuario=request.user,
                titulo=pregunta[:60],
            )

        # Save user message
        MensajeIA.objects.create(conversacion=conversacion, rol='user', contenido=pregunta)

        # Build history (last 10 exchanges to stay within token limits)
        historial = conversacion.mensajes.order_by('-timestamp')[:20][::-1]

        # Get AI response
        respuesta = obtener_respuesta_ia(request.user, pregunta, historial)

        # Save assistant message
        MensajeIA.objects.create(conversacion=conversacion, rol='assistant', contenido=respuesta)

        return JsonResponse({
            'respuesta': respuesta,
            'conversacion_id': conversacion.pk,
        })
