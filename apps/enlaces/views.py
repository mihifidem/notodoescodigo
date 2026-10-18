from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import TemplateView, View

from .models import AccesoEnlaceUsuario, EnlaceImportante


class EnlacesImportantesView(LoginRequiredMixin, TemplateView):
    template_name = 'enlaces/list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        categoria = (self.request.GET.get('categoria') or '').strip()
        palabras = (self.request.GET.get('q') or '').strip()

        enlaces = EnlaceImportante.objects.filter(activo=True)
        if categoria:
            enlaces = enlaces.filter(categoria__iexact=categoria)
        if palabras:
            enlaces = enlaces.filter(
                Q(titulo__icontains=palabras)
                | Q(comentario__icontains=palabras)
                | Q(url__icontains=palabras)
            )

        categorias = (
            EnlaceImportante.objects.filter(activo=True)
            .values_list('categoria', flat=True)
            .distinct()
            .order_by('categoria')
        )

        accesos_ids = set(
            AccesoEnlaceUsuario.objects.filter(usuario=user).values_list('enlace_id', flat=True)
        )
        context['enlaces_estado'] = [(enlace, enlace.id in accesos_ids) for enlace in enlaces]
        context['categorias'] = categorias
        context['categoria_actual'] = categoria
        context['q_actual'] = palabras
        return context


class AccederEnlaceView(LoginRequiredMixin, View):
    def get(self, request, pk):
        enlace = get_object_or_404(EnlaceImportante, pk=pk, activo=True)

        acceso, created = AccesoEnlaceUsuario.objects.get_or_create(
            usuario=request.user,
            enlace=enlace,
        )

        if created and not request.user.is_staff:
            request.user.agregar_puntos(1)
            messages.success(request, 'Ganaste 1 punto por acceder a este enlace por primera vez.')
        elif not created:
            messages.info(request, 'Este enlace ya te otorgo el punto anteriormente.')

        return redirect(enlace.url)
