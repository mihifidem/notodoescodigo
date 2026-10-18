from django.db.models import Max
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import ListView, DetailView, UpdateView, View
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone

from .models import TipoExamen, Evaluacion, EntregaEvaluacion, CorreccionEvaluacion
from .serializers import (
    TipoExamenSerializer,
    EvaluacionSerializer,
    EntregaEvaluacionSerializer,
    EntregaCreateSerializer,
)
from .tasks import procesar_entrega_evaluacion
from .forms import EntregaAlumnoForm, EntregaEditForm


class TipoExamenViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TipoExamenSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return TipoExamen.objects.filter(activo=True)


class EvaluacionViewSet(viewsets.ModelViewSet):
    serializer_class = EvaluacionSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['tipo_examen', 'tipo_prueba', 'alcance_tipo', 'estado', 'tema', 'curso', 'aula']
    search_fields = ['titulo', 'enunciado', 'criterios_a_valorar']

    def get_queryset(self):
        return Evaluacion.objects.select_related('tipo_examen', 'tema', 'curso', 'aula').prefetch_related(
            'cursos',
            'rubrica__criterios'
        )

    @action(detail=True, methods=['post'], url_path='submit')
    def submit(self, request, pk=None):
        evaluacion = self.get_object()
        if not evaluacion.esta_abierta:
            return Response(
                {'detail': 'La evaluacion no esta abierta para entregas.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = EntregaCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        last_attempt = (
            EntregaEvaluacion.objects.filter(evaluacion=evaluacion, alumno=request.user)
            .aggregate(last=Max('intento_numero'))
            .get('last')
            or 0
        )

        entrega = EntregaEvaluacion.objects.create(
            evaluacion=evaluacion,
            alumno=request.user,
            archivo_respuesta=serializer.validated_data['archivo_respuesta'],
            intento_numero=last_attempt + 1,
            solicita_revision_exhaustiva=serializer.validated_data.get('solicita_revision_exhaustiva', False),
            motivo_revision_exhaustiva=serializer.validated_data.get('motivo_revision_exhaustiva', ''),
        )

        if entrega.solicita_revision_exhaustiva:
            entrega.estado = EntregaEvaluacion.ESTADO_REVISION
            entrega.save(update_fields=['estado'])

        out = EntregaEvaluacionSerializer(entrega, context={'request': request})
        return Response(out.data, status=status.HTTP_201_CREATED)


class EntregaEvaluacionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = EntregaEvaluacionSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['evaluacion', 'estado']

    def get_queryset(self):
        queryset = EntregaEvaluacion.objects.select_related(
            'evaluacion', 'correccion'
        )
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(alumno=self.request.user)

    @action(detail=True, methods=['get'], url_path='status')
    def status(self, request, pk=None):
        entrega = self.get_object()
        data = {
            'id': entrega.id,
            'estado': entrega.estado,
            'fecha_entrega': entrega.fecha_entrega,
            'procesada_en': entrega.procesada_en,
        }
        if hasattr(entrega, 'correccion'):
            data['requiere_revision_humana'] = entrega.correccion.requiere_revision_humana
        return Response(data)

    @action(detail=True, methods=['get'], url_path='result')
    def result(self, request, pk=None):
        entrega = self.get_object()
        if not hasattr(entrega, 'correccion'):
            return Response(
                {'detail': 'La entrega aun no tiene correccion disponible.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(EntregaEvaluacionSerializer(entrega, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='regrade')
    def regrade(self, request, pk=None):
        entrega = self.get_object()
        entrega.estado = EntregaEvaluacion.ESTADO_PENDIENTE
        entrega.save(update_fields=['estado'])
        procesar_entrega_evaluacion.delay(entrega.pk)
        return Response({'detail': 'Recorreccion lanzada.', 'estado': entrega.estado})

    @action(detail=True, methods=['post'], url_path='request-exhaustive-review')
    def request_exhaustive_review(self, request, pk=None):
        entrega = self.get_object()
        if entrega.alumno_id != request.user.id and not request.user.is_staff:
            return Response({'detail': 'No autorizado.'}, status=status.HTTP_403_FORBIDDEN)

        motivo = request.data.get('motivo', '')
        entrega.solicita_revision_exhaustiva = True
        entrega.motivo_revision_exhaustiva = motivo
        entrega.estado = EntregaEvaluacion.ESTADO_REVISION
        entrega.save(update_fields=['solicita_revision_exhaustiva', 'motivo_revision_exhaustiva', 'estado'])

        return Response({'detail': 'Solicitud de revision exhaustiva registrada.', 'estado': entrega.estado})

    @action(detail=True, methods=['post'], url_path='manual-grade')
    def manual_grade(self, request, pk=None):
        entrega = self.get_object()
        if not request.user.is_staff:
            return Response({'detail': 'Solo admin/profesor puede realizar revision manual.'}, status=status.HTTP_403_FORBIDDEN)

        puntuacion_total = request.data.get('puntuacion_total', 0)
        feedback_global = request.data.get('feedback_global', '')
        observaciones_docente = request.data.get('observaciones_docente', '')

        correccion, _ = CorreccionEvaluacion.objects.update_or_create(
            entrega=entrega,
            defaults={
                'tipo_correccion': CorreccionEvaluacion.TIPO_DOCENTE,
                'puntuacion_total': puntuacion_total,
                'feedback_global': feedback_global,
                'observaciones_docente': observaciones_docente,
                'revisado_por': request.user,
                'fecha_revision_docente': timezone.now(),
                'requiere_revision_humana': False,
            },
        )

        entrega.estado = EntregaEvaluacion.ESTADO_CORREGIDA
        entrega.procesada_en = timezone.now()
        entrega.save(update_fields=['estado', 'procesada_en'])

        return Response(
            {
                'detail': 'Revision manual guardada.',
                'entrega_estado': entrega.estado,
                'correccion_id': correccion.id,
            }
        )


# ---------------------------------------------------------------------------
# HTML Views (alumno)
# ---------------------------------------------------------------------------


class EvaluacionAlumnoListView(LoginRequiredMixin, ListView):
    model = Evaluacion
    template_name = 'evaluations/list.html'
    context_object_name = 'evaluaciones'

    def get_queryset(self):
        user = self.request.user
        queryset = Evaluacion.objects.filter(estado=Evaluacion.ESTADO_PUBLICADA).select_related(
            'aula', 'tema', 'curso'
        ).prefetch_related('cursos')

        if user.aula_id:
            queryset = queryset.filter(aula_id=user.aula_id)
        else:
            queryset = queryset.none()

        return queryset.order_by('-fecha_prueba', '-creada_en')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        evaluaciones = list(context['evaluaciones'])

        latest_by_eval_id = {}
        entregas = (
            EntregaEvaluacion.objects.filter(
                alumno=self.request.user,
                evaluacion__in=evaluaciones,
            )
            .select_related('correccion')
            .order_by('evaluacion_id', '-intento_numero')
        )
        for entrega in entregas:
            latest_by_eval_id.setdefault(entrega.evaluacion_id, entrega)

        for evaluacion in evaluaciones:
            evaluacion.entrega_alumno = latest_by_eval_id.get(evaluacion.id)

        context['evaluaciones'] = evaluaciones
        return context


class EvaluacionAlumnoDetailView(LoginRequiredMixin, DetailView):
    model = Evaluacion
    template_name = 'evaluations/detail.html'
    context_object_name = 'evaluacion'

    def get_queryset(self):
        user = self.request.user
        queryset = Evaluacion.objects.filter(estado=Evaluacion.ESTADO_PUBLICADA).select_related(
            'aula', 'tema', 'curso'
        ).prefetch_related('cursos')
        if user.aula_id:
            return queryset.filter(aula_id=user.aula_id)
        return queryset.none()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['entrega_form'] = EntregaAlumnoForm()
        context['entregas_previas'] = EntregaEvaluacion.objects.filter(
            evaluacion=self.object,
            alumno=self.request.user,
        ).select_related('correccion').order_by('-intento_numero')
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()

        if not self.object.esta_abierta:
            messages.error(request, 'La evaluación no está abierta para entregas en este momento.')
            return redirect('evaluations:detail', pk=self.object.pk)

        form = EntregaAlumnoForm(request.POST, request.FILES)
        if not form.is_valid():
            context = self.get_context_data()
            context['entrega_form'] = form
            return self.render_to_response(context)

        last_attempt = (
            EntregaEvaluacion.objects.filter(evaluacion=self.object, alumno=request.user)
            .aggregate(last=Max('intento_numero'))
            .get('last')
            or 0
        )

        entrega = EntregaEvaluacion.objects.create(
            evaluacion=self.object,
            alumno=request.user,
            archivo_respuesta=form.cleaned_data['archivo_respuesta'],
            intento_numero=last_attempt + 1,
            solicita_revision_exhaustiva=form.cleaned_data.get('solicita_revision_exhaustiva', False),
            motivo_revision_exhaustiva=form.cleaned_data.get('motivo_revision_exhaustiva', ''),
        )

        if entrega.solicita_revision_exhaustiva:
            entrega.estado = EntregaEvaluacion.ESTADO_REVISION
            entrega.save(update_fields=['estado'])

        messages.success(request, 'Entrega subida correctamente. Queda pendiente de corrección.')
        return redirect('evaluations:detail', pk=self.object.pk)


class EntregaEditView(LoginRequiredMixin, View):
    template_name = 'evaluations/entrega_edit.html'

    def _get_entrega(self, request, pk):
        entrega = get_object_or_404(EntregaEvaluacion, pk=pk, alumno=request.user)
        if not entrega.evaluacion.esta_abierta:
            raise Http404('La evaluación no está abierta.')
        if entrega.estado in (EntregaEvaluacion.ESTADO_CORREGIDA, EntregaEvaluacion.ESTADO_REVISION):
            raise Http404('No puedes modificar una entrega que ya ha sido corregida.')
        return entrega

    def get(self, request, pk):
        entrega = self._get_entrega(request, pk)
        form = EntregaEditForm(instance=entrega)
        return self._render(request, entrega, form)

    def post(self, request, pk):
        entrega = self._get_entrega(request, pk)
        form = EntregaEditForm(request.POST, request.FILES, instance=entrega)
        if not form.is_valid():
            return self._render(request, entrega, form)

        entrega = form.save(commit=False)
        entrega.estado = EntregaEvaluacion.ESTADO_PENDIENTE
        entrega.procesada_en = None
        entrega.save()

        messages.success(request, 'Entrega actualizada correctamente. Queda pendiente de corrección.')
        return redirect('evaluations:detail', pk=entrega.evaluacion_id)

    def _render(self, request, entrega, form):
        from django.shortcuts import render
        return render(request, self.template_name, {'entrega': entrega, 'form': form})


class EntregaDeleteView(LoginRequiredMixin, View):

    def post(self, request, pk):
        entrega = get_object_or_404(EntregaEvaluacion, pk=pk, alumno=request.user)
        if not entrega.evaluacion.esta_abierta:
            messages.error(request, 'No puedes eliminar la entrega fuera del período de validez de la evaluación.')
            return redirect('evaluations:detail', pk=entrega.evaluacion_id)
        if entrega.estado in (EntregaEvaluacion.ESTADO_CORREGIDA, EntregaEvaluacion.ESTADO_REVISION):
            messages.error(request, 'No puedes eliminar una entrega que ya ha sido corregida.')
            return redirect('evaluations:detail', pk=entrega.evaluacion_id)

        evaluacion_pk = entrega.evaluacion_id
        entrega.delete()
        messages.success(request, 'Entrega eliminada correctamente.')
        return redirect('evaluations:detail', pk=evaluacion_pk)

