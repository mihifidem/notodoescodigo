"""Views for the rewards app."""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import ListView, View
from django.db import transaction
from django.db.models import F

from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Recompensa, CanjeRecompensa
from .serializers import RecompensaSerializer, CanjeSerializer


# ---------------------------------------------------------------------------
# HTML Views
# ---------------------------------------------------------------------------

class RecompensaListView(LoginRequiredMixin, ListView):
    model = Recompensa
    template_name = 'rewards/list.html'
    context_object_name = 'recompensas'
    queryset = Recompensa.objects.filter(activa=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['puntos_usuario'] = self.request.user.puntos
        context['historial'] = CanjeRecompensa.objects.filter(
            usuario=self.request.user
        ).select_related('recompensa')[:10]
        return context


class CanjeView(LoginRequiredMixin, View):
    @transaction.atomic
    def post(self, request, pk):
        recompensa = get_object_or_404(Recompensa, pk=pk, activa=True)
        user = request.user

        if not recompensa.disponible:
            messages.error(request, 'Esta recompensa no está disponible.')
            return redirect('rewards:list')

        if not type(user).objects.filter(pk=user.pk, puntos__gte=recompensa.puntos_necesarios).update(
            puntos=F('puntos') - recompensa.puntos_necesarios,
        ):
            user.refresh_from_db(fields=['puntos'])
            messages.error(
                request,
                f'No tienes suficientes puntos. Necesitas {recompensa.puntos_necesarios} y tienes {user.puntos}.'
            )
            return redirect('rewards:list')

        # Deduct from spendable balance only (puntos_totales and nivel are never affected)
        user.refresh_from_db(fields=['puntos'])

        # Decrease stock
        if recompensa.stock > 0:
            recompensa.stock -= 1
            recompensa.save(update_fields=['stock'])

        CanjeRecompensa.objects.create(
            usuario=user,
            recompensa=recompensa,
            puntos_gastados=recompensa.puntos_necesarios,
        )

        from apps.analytics.models import RegistroActividad
        RegistroActividad.objects.create(
            usuario=user,
            tipo='recompensa_canjeada',
            descripcion=f'Canjeó la recompensa: {recompensa.nombre}',
        )

        from apps.gamification.utils import crear_notificacion
        crear_notificacion(
            user, 'recompensa',
            '🎁 ¡Recompensa canjeada!',
            f'Has canjeado «{recompensa.nombre}» por {recompensa.puntos_necesarios} puntos.',
        )

        messages.success(request, f'¡Has canjeado «{recompensa.nombre}» exitosamente!')
        return redirect('rewards:list')


class HistorialCanjesView(LoginRequiredMixin, ListView):
    model = CanjeRecompensa
    template_name = 'rewards/historial.html'
    context_object_name = 'canjes'
    paginate_by = 20

    def get_queryset(self):
        return CanjeRecompensa.objects.filter(
            usuario=self.request.user
        ).select_related('recompensa')


# ---------------------------------------------------------------------------
# DRF ViewSets
# ---------------------------------------------------------------------------

class RecompensaViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = RecompensaSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Recompensa.objects.filter(activa=True)

    @action(detail=True, methods=['post'])
    def canjear(self, request, pk=None):
        recompensa = self.get_object()
        user = request.user

        if not recompensa.disponible:
            return Response({'error': 'Recompensa no disponible.'}, status=400)
        with transaction.atomic():
            # Deduct from spendable balance only (puntos_totales and nivel are never affected)
            if not type(user).objects.filter(pk=user.pk, puntos__gte=recompensa.puntos_necesarios).update(
                puntos=F('puntos') - recompensa.puntos_necesarios,
            ):
                return Response({'error': 'Puntos insuficientes.'}, status=400)
            user.refresh_from_db(fields=['puntos'])
            if recompensa.stock > 0:
                recompensa.stock -= 1
                recompensa.save(update_fields=['stock'])
            canje = CanjeRecompensa.objects.create(
                usuario=user,
                recompensa=recompensa,
                puntos_gastados=recompensa.puntos_necesarios,
            )

        return Response(CanjeSerializer(canje).data, status=201)


class CanjeViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = CanjeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return CanjeRecompensa.objects.filter(usuario=self.request.user)
