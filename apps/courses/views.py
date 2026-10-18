import csv
import random
from pathlib import Path

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import ListView, DetailView, View
from django.http import JsonResponse
from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone
from apps.evaluations.models import Evaluacion
from apps.lms.policies import is_course_member
from apps.lms.services import EnrollmentUnavailable, enroll_student
from apps.lms.module_progression import module_complete, module_lock_reason, sequential_enabled

from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import (
    Curso, Tema, TemaRecurso, TemaRecursoVisualizacion, InscripcionCurso, Progreso,
    Quiz, Pregunta, Respuesta, ResultadoQuiz,
)
from .serializers import (
    CursoSerializer, TemaSerializer, InscripcionSerializer,
    ProgresoSerializer, QuizSerializer, ResultadoQuizSerializer,
)


def get_cursos_permitidos(usuario):
    return usuario.get_cursos_disponibles().annotate(total_inscritos=Count('inscripciones'))


# ---------------------------------------------------------------------------
# HTML Views
# ---------------------------------------------------------------------------

class CursoListView(LoginRequiredMixin, ListView):
    model = Curso
    template_name = 'courses/list.html'
    context_object_name = 'cursos'

    def get_queryset(self):
        return get_cursos_permitidos(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        ids_inscritos = InscripcionCurso.objects.filter(
            usuario=self.request.user
        ).values_list('curso_id', flat=True)
        context['ids_inscritos'] = set(ids_inscritos)
        context['sin_aula'] = not self.request.user.aula_id
        return context


class CursoDetailView(LoginRequiredMixin, DetailView):
    model = Curso
    template_name = 'courses/detail.html'
    context_object_name = 'curso'

    def get_queryset(self):
        return self.request.user.get_cursos_disponibles()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        curso = self.get_object()

        inscripcion = InscripcionCurso.objects.filter(usuario=user, curso=curso).first()
        context['inscripcion'] = inscripcion

        temas = curso.temas.all()
        progresos = {
            p.tema_id: p for p in Progreso.objects.filter(usuario=user, tema__in=temas)
        }
        context['temas_con_progreso'] = [(t, progresos.get(t.id)) for t in temas]

        total = temas.count()
        completados = sum(1 for p in progresos.values() if p.completado)
        context['porcentaje_progreso'] = int((completados / total) * 100) if total else 0
        context['temas_completados'] = completados
        context['total_temas'] = total

        quizzes_curso = []
        for tema in temas:
            quiz = getattr(tema, 'quiz', None)
            if not quiz:
                continue
            csv_path = QuizView._csv_path_from_quiz(quiz)
            preguntas = QuizView._load_all_csv_questions(quiz)
            quizzes_curso.append({
                'tema': tema,
                'quiz': quiz,
                'csv_filename': quiz.csv_filename,
                'csv_existe': bool(quiz.csv_filename and csv_path and csv_path.exists()),
                'preguntas_cargadas': len(preguntas),
            })
        context['quizzes_curso'] = quizzes_curso
        context['hay_quizzes_curso'] = bool(quizzes_curso)

        evaluaciones_base = Evaluacion.objects.filter(estado=Evaluacion.ESTADO_PUBLICADA)
        if user.aula_id:
            evaluaciones_base = evaluaciones_base.filter(aula_id=user.aula_id)
        else:
            evaluaciones_base = evaluaciones_base.none()

        evaluaciones_ud = evaluaciones_base.filter(
            alcance_tipo=Evaluacion.ALCANCE_UD,
            tema__curso=curso,
        ).distinct().order_by('-fecha_prueba', '-creada_en')

        evaluaciones_uf = evaluaciones_base.filter(
            alcance_tipo=Evaluacion.ALCANCE_UF,
            curso=curso,
        ).distinct().order_by('-fecha_prueba', '-creada_en')

        evaluaciones_mf = evaluaciones_base.filter(
            alcance_tipo=Evaluacion.ALCANCE_MF,
            cursos=curso,
        ).distinct().order_by('-fecha_prueba', '-creada_en')

        context['evaluaciones_ud'] = evaluaciones_ud
        context['evaluaciones_uf'] = evaluaciones_uf
        context['evaluaciones_mf'] = evaluaciones_mf
        context['evaluaciones_total'] = (
            evaluaciones_ud.count() + evaluaciones_uf.count() + evaluaciones_mf.count()
        )
        return context


class InscribirseView(LoginRequiredMixin, View):
    def post(self, request, pk):
        curso = get_object_or_404(Curso, pk=pk, activo=True)
        if not request.user.puede_inscribirse_en_curso(curso):
            messages.error(request, 'No puedes inscribirte en este curso con el aula que tienes asignada.')
            return redirect('courses:list')
        created = not InscripcionCurso.objects.filter(usuario=request.user, curso=curso).exists()
        try:
            enroll_student(actor=request.user, course=curso)
        except EnrollmentUnavailable as error:
            messages.error(request, str(error))
            return redirect('courses:detail', pk=pk)
        if created:
            messages.success(request, f'¡Te has inscrito en «{curso.nombre}»!')
        else:
            messages.info(request, 'Ya estás inscrito en este curso.')
        return redirect('courses:detail', pk=pk)


class TemaDetailView(LoginRequiredMixin, DetailView):
    model = Tema
    template_name = 'courses/tema_detail.html'
    context_object_name = 'tema'

    def dispatch(self, request, *args, **kwargs):
        tema = self.get_object()
        if not is_course_member(request.user, tema.curso):
            messages.warning(request, 'Inscríbete en el curso para acceder a sus temas.')
            return redirect('courses:detail', pk=tema.curso.pk)
        reason = module_lock_reason(tema, request.user)
        if reason:
            raise PermissionDenied(reason)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        tema = self.get_object()
        progreso, _ = Progreso.objects.get_or_create(usuario=user, tema=tema)
        context['progreso'] = progreso
        recursos = list(tema.recursos.select_related('tipo_recurso').order_by('orden', 'titulo'))
        recursos_activas = [recurso for recurso in recursos if getattr(recurso, 'activo', True)]
        recursos_visualizados_ids = set(
            TemaRecursoVisualizacion.objects.filter(
                usuario=user,
                recurso__in=recursos,
            ).values_list('recurso_id', flat=True)
        )
        context['recursos_con_estado'] = [
            (recurso, recurso.id in recursos_visualizados_ids) for recurso in recursos_activas
        ]
        context['hay_recursos'] = bool(recursos_activas)
        context['puntos_recurso'] = TemaRecurso.PUNTOS_VISUALIZACION
        context['tiene_quiz'] = hasattr(tema, 'quiz')
        context['quiz_aprobado'] = False
        context['quiz_porcentaje_aprobacion'] = None

        if hasattr(tema, 'quiz'):
            context['quiz_porcentaje_aprobacion'] = tema.quiz.porcentaje_aprobacion
            context['quiz_aprobado'] = ResultadoQuiz.objects.filter(
                usuario=user,
                quiz=tema.quiz,
                aprobado=True,
            ).exists()

        temas = list(tema.curso.temas.order_by('orden'))
        idx = next((i for i, t in enumerate(temas) if t.id == tema.id), None)
        context['tema_anterior'] = temas[idx - 1] if idx and idx > 0 else None
        context['tema_siguiente'] = temas[idx + 1] if idx is not None and idx < len(temas) - 1 else None
        return context


class TemaRecursoOnlineView(LoginRequiredMixin, DetailView):
    model = TemaRecurso
    template_name = 'courses/recurso_online.html'
    context_object_name = 'recurso'

    def dispatch(self, request, *args, **kwargs):
        recurso = self.get_object()
        if not is_course_member(request.user, recurso.tema.curso):
            messages.warning(request, 'Inscríbete en el curso para acceder a los recursos del tema.')
            return redirect('courses:detail', pk=recurso.tema.curso.pk)
        reason = module_lock_reason(recurso.tema, request.user)
        if reason:
            raise PermissionDenied(reason)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        recurso = self.get_object()
        visualizacion, created = TemaRecursoVisualizacion.objects.get_or_create(
            usuario=self.request.user,
            recurso=recurso,
            defaults={'puntos_otorgados': TemaRecurso.PUNTOS_VISUALIZACION},
        )
        if created:
            self.request.user.agregar_puntos(TemaRecurso.PUNTOS_VISUALIZACION)
            from apps.analytics.models import RegistroActividad
            RegistroActividad.objects.create(
                usuario=self.request.user,
                tipo='recurso_visualizado',
                descripcion=f'Visualizó el recurso: {recurso.titulo}',
                puntos_ganados=TemaRecurso.PUNTOS_VISUALIZACION,
            )
            messages.success(
                self.request,
                f'Has ganado {TemaRecurso.PUNTOS_VISUALIZACION} puntos por visualizar este recurso.',
            )
        context['tema'] = recurso.tema
        context['visualizacion'] = visualizacion
        context['puntos_recurso'] = TemaRecurso.PUNTOS_VISUALIZACION
        context['puntos_otorgados_ahora'] = created
        return context


class TemaRecursoAccessMixin:
    def _otorgar_puntos_si_corresponde(self, recurso):
        visualizacion, created = TemaRecursoVisualizacion.objects.get_or_create(
            usuario=self.request.user,
            recurso=recurso,
            defaults={'puntos_otorgados': TemaRecurso.PUNTOS_VISUALIZACION},
        )
        if created:
            self.request.user.agregar_puntos(TemaRecurso.PUNTOS_VISUALIZACION)
            from apps.analytics.models import RegistroActividad
            RegistroActividad.objects.create(
                usuario=self.request.user,
                tipo='recurso_visualizado',
                descripcion=f'Visualizó el recurso: {recurso.titulo}',
                puntos_ganados=TemaRecurso.PUNTOS_VISUALIZACION,
            )
        return visualizacion, created

    def dispatch(self, request, *args, **kwargs):
        recurso = get_object_or_404(TemaRecurso, pk=kwargs['pk'])
        if not is_course_member(request.user, recurso.tema.curso):
            messages.warning(request, 'Inscríbete en el curso para acceder a los recursos del tema.')
            return redirect('courses:detail', pk=recurso.tema.curso.pk)
        reason = module_lock_reason(recurso.tema, request.user)
        if reason:
            raise PermissionDenied(reason)
        return super().dispatch(request, *args, **kwargs)


class TemaRecursoAbrirView(LoginRequiredMixin, TemaRecursoAccessMixin, View):
    def get(self, request, pk):
        recurso = get_object_or_404(TemaRecurso, pk=pk)
        self._otorgar_puntos_si_corresponde(recurso)
        return redirect(recurso.archivo.url)


class TemaRecursoDescargarView(LoginRequiredMixin, TemaRecursoAccessMixin, View):
    def get(self, request, pk):
        recurso = get_object_or_404(TemaRecurso, pk=pk)
        self._otorgar_puntos_si_corresponde(recurso)
        return redirect(recurso.archivo.url)


class MarcarCompletadoView(LoginRequiredMixin, View):
    def post(self, request, pk):
        tema = get_object_or_404(Tema, pk=pk)
        if not is_course_member(request.user, tema.curso):
            return JsonResponse({'error': 'No tienes acceso a este tema.'}, status=403)
        reason = module_lock_reason(tema, request.user)
        if reason:
            return JsonResponse({'ok': False, 'error': reason}, status=403)
        if sequential_enabled(tema.curso) and tema.learning_contents.exists() and not module_complete(tema, request.user):
            return JsonResponse({'ok': False, 'error': 'Completa los contenidos obligatorios del módulo.'}, status=400)

        if hasattr(tema, 'quiz'):
            aprobado = ResultadoQuiz.objects.filter(
                usuario=request.user,
                quiz=tema.quiz,
                aprobado=True,
            ).exists()
            if not aprobado:
                return JsonResponse({
                    'ok': False,
                    'error': (
                        f'Debes aprobar el quiz (mínimo {tema.quiz.porcentaje_aprobacion}%) '
                        'antes de marcar este tema como completado.'
                    ),
                }, status=400)

        progreso, _ = Progreso.objects.get_or_create(usuario=request.user, tema=tema)
        if not progreso.completado:
            progreso.marcar_completado()
            return JsonResponse({
                'ok': True,
                'puntos_ganados': tema.puntos_otorgados,
                'puntos_totales': request.user.puntos_totales,
                'nivel': request.user.nivel,
            })
        return JsonResponse({'ok': False, 'message': 'Tema ya marcado como completado.'})


class QuizView(LoginRequiredMixin, View):
    @staticmethod
    def _csv_path_from_quiz(quiz):
        if not quiz.csv_filename:
            return None
        return Path(settings.BASE_DIR) / 'static' / 'quiz' / quiz.csv_filename

    @staticmethod
    def _load_all_csv_questions(quiz):
        """Carga TODAS las preguntas del CSV (sin seleccionar)"""
        csv_path = QuizView._csv_path_from_quiz(quiz)
        if not csv_path or not csv_path.exists():
            return []

        questions = []
        with csv_path.open('r', encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f, delimiter=';')
            for i, row in enumerate(reader, start=1):
                raw = {str(k).strip().lower(): (v or '').strip() for k, v in row.items() if k}

                texto = raw.get('question') or raw.get('pregunta')
                if not texto:
                    continue

                opciones = {
                    'A': raw.get('option_a') or raw.get('opciona') or raw.get('opcion1') or raw.get('option1'),
                    'B': raw.get('option_b') or raw.get('opcionb') or raw.get('opcion2') or raw.get('option2'),
                    'C': raw.get('option_c') or raw.get('opcionc') or raw.get('opcion3') or raw.get('option3'),
                    'D': raw.get('option_d') or raw.get('opciond') or raw.get('opcion4') or raw.get('option4'),
                }
                opciones = {k: v for k, v in opciones.items() if v}
                if len(opciones) < 2:
                    continue

                correcta = (
                    raw.get('correct_answer')
                    or raw.get('correct_option')
                    or raw.get('respuesta_correcta')
                    or raw.get('respuestacorrecta')
                    or raw.get('letra opcion correcta')
                    or raw.get('letra_opcion_correcta')
                )
                if not correcta:
                    continue

                correcta = correcta.strip()

                # Formato 1: Es una letra (A, B, C, D)
                if len(correcta) == 1 and correcta.upper() in opciones:
                    correcta = correcta.upper()
                # Formato 2: Es el texto completo de la respuesta
                elif correcta in opciones.values():
                    # Encontrar la letra correspondiente al texto
                    for letter, text in opciones.items():
                        if text == correcta:
                            correcta = letter
                            break
                else:
                    # Intenta coincidir parcialmente (primeras palabras)
                    correcta_parts = correcta.lower().split()[:2]
                    correcta_found = False
                    for letter, text in opciones.items():
                        text_parts = text.lower().split()[:2]
                        if correcta_parts == text_parts:
                            correcta = letter
                            correcta_found = True
                            break
                    if not correcta_found:
                        continue

                questions.append({
                    'id': f'csv_{i}',
                    'texto': texto,
                    'opciones': [{'key': key, 'texto': value} for key, value in opciones.items()],
                    'correcta': correcta,
                })
        return questions

    @staticmethod
    def _get_quiz_questions(quiz, num_preguntas=20):
        """Carga todas las preguntas y selecciona 'num_preguntas' aleatorias"""
        all_questions = QuizView._load_all_csv_questions(quiz)
        if not all_questions:
            return []
        # Seleccionar preguntas aleatorias (máximo num_preguntas)
        num = min(num_preguntas, len(all_questions))
        return random.sample(all_questions, num)

    @staticmethod
    def _require_csv_questions(quiz, num_preguntas=20):
        """Return CSV questions or an explanatory error message if unavailable/invalid."""
        csv_path = QuizView._csv_path_from_quiz(quiz)
        if not quiz.csv_filename:
            return None, 'Este quiz no tiene archivo CSV asignado.'
        if not csv_path or not csv_path.exists():
            return None, f'No se encontró el archivo CSV: {quiz.csv_filename} en static/quiz.'

        preguntas = QuizView._get_quiz_questions(quiz, num_preguntas)
        if not preguntas:
            return None, 'El archivo CSV no contiene preguntas válidas.'
        return preguntas, None

    def get(self, request, pk):
        quiz = get_object_or_404(Quiz, pk=pk)
        if not is_course_member(request.user, quiz.tema.curso):
            messages.warning(request, 'Inscríbete en el curso para acceder al quiz.')
            return redirect('courses:detail', pk=quiz.tema.curso.pk)
        reason = module_lock_reason(quiz.tema, request.user)
        if reason:
            raise PermissionDenied(reason)

        preguntas, error_csv = self._require_csv_questions(quiz, num_preguntas=20)
        if error_csv:
            messages.error(request, error_csv)
            return redirect('courses:tema_detail', pk=quiz.tema.pk)

        # Guardar los IDs de las preguntas mostradas para que el POST
        # corrija exactamente las mismas preguntas (no otro random.sample).
        request.session[f'quiz_{quiz.pk}_ids'] = [p['id'] for p in preguntas]
        request.session.modified = True

        ultimo = ResultadoQuiz.objects.filter(usuario=request.user, quiz=quiz).order_by('-fecha').first()
        return render(request, 'courses/quiz.html', {
            'quiz': quiz,
            'preguntas': preguntas,
            'ultimo_resultado': ultimo,
        })

    def post(self, request, pk):
        quiz = get_object_or_404(Quiz, pk=pk)
        if not is_course_member(request.user, quiz.tema.curso):
            raise PermissionDenied
        reason = module_lock_reason(quiz.tema, request.user)
        if reason:
            raise PermissionDenied(reason)

        # Recuperar las preguntas exactas que vio el alumno (guardadas en sesión).
        session_key = f'quiz_{quiz.pk}_ids'
        stored_ids = request.session.get(session_key)

        if stored_ids:
            all_questions = self._load_all_csv_questions(quiz)
            id_map = {q['id']: q for q in all_questions}
            preguntas = [id_map[qid] for qid in stored_ids if qid in id_map]
            error_csv = None if preguntas else 'No se pudieron recuperar las preguntas de la sesión.'
        else:
            # Fallback si no hay sesión (tab cerrada, sesión expirada, etc.)
            preguntas, error_csv = self._require_csv_questions(quiz, num_preguntas=20)

        if error_csv:
            messages.error(request, error_csv)
            return redirect('courses:tema_detail', pk=quiz.tema.pk)

        correctas = 0
        total = len(preguntas)
        detalle = []

        for pregunta in preguntas:
            elegida = (request.POST.get(f'pregunta_{pregunta["id"]}') or '').strip().upper()
            correcta = (pregunta.get('correcta') or '').strip().upper()
            acerto = bool(elegida and correcta and elegida == correcta)
            if acerto:
                correctas += 1

            opciones_dict = {op['key']: op['texto'] for op in pregunta.get('opciones', [])}
            detalle.append({
                'pregunta': pregunta.get('texto'),
                'elegida': opciones_dict.get(elegida),
                'correcta': opciones_dict.get(correcta),
                'acerto': acerto,
            })

        aprobado = (correctas / total) * 100 >= quiz.porcentaje_aprobacion if total else False
        tiempo_empleado = request.POST.get('tiempo_empleado_segundos')
        try:
            tiempo_empleado = int(tiempo_empleado) if tiempo_empleado not in (None, '') else 0
        except (TypeError, ValueError):
            tiempo_empleado = 0

        resultado = ResultadoQuiz.objects.create(
            usuario=request.user,
            quiz=quiz,
            puntuacion=correctas,
            total_preguntas=total,
            tiempo_empleado=tiempo_empleado,
            aprobado=aprobado,
        )

        # Limpiar la sesión una vez corregido
        request.session.pop(f'quiz_{quiz.pk}_ids', None)

        return render(request, 'courses/quiz_resultado.html', {
            'quiz': quiz,
            'resultado': resultado,
            'detalle': detalle,
        })


# ---------------------------------------------------------------------------
# DRF ViewSets
# ---------------------------------------------------------------------------

class CursoViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = CursoSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['activo', 'destacado']
    search_fields = ['nombre', 'descripcion']

    def get_queryset(self):
        return self.request.user.get_cursos_disponibles()

    @action(detail=True, methods=['post'])
    def inscribirse(self, request, pk=None):
        curso = self.get_object()
        if not request.user.puede_inscribirse_en_curso(curso):
            return Response(
                {'inscrito': False, 'error': 'Tu aula no tiene acceso a este curso.'},
                status=403,
            )
        created = not InscripcionCurso.objects.filter(usuario=request.user, curso=curso).exists()
        try:
            enroll_student(actor=request.user, course=curso)
        except EnrollmentUnavailable as error:
            return Response({'inscrito': False, 'error': str(error)}, status=403)
        return Response({'inscrito': True, 'nuevo': created})

    @action(detail=True, methods=['get'])
    def progreso(self, request, pk=None):
        curso = self.get_object()
        temas = curso.temas.all()
        progresos = Progreso.objects.filter(usuario=request.user, tema__in=temas)
        total = temas.count()
        completados = progresos.filter(completado=True).count()
        return Response({
            'total': total,
            'completados': completados,
            'porcentaje': int((completados / total) * 100) if total else 0,
        })


class TemaViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TemaSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['curso']

    def get_queryset(self):
        queryset = Tema.objects.filter(curso__activo=True).select_related('curso')
        return queryset.exclude(pk__in=[
            module.pk for module in queryset
            if sequential_enabled(module.curso) and module_lock_reason(module, self.request.user)
        ])

    @action(detail=True, methods=['post'])
    def completar(self, request, pk=None):
        tema = self.get_object()
        if sequential_enabled(tema.curso):
            if not is_course_member(request.user, tema.curso):
                raise PermissionDenied
            if tema.learning_contents.exists() and not module_complete(tema, request.user):
                return Response({'ok': False, 'error': 'Completa los contenidos obligatorios del módulo.'}, status=400)

        if hasattr(tema, 'quiz'):
            aprobado = ResultadoQuiz.objects.filter(
                usuario=request.user,
                quiz=tema.quiz,
                aprobado=True,
            ).exists()
            if not aprobado:
                return Response({
                    'ok': False,
                    'error': (
                        f'Debes aprobar el quiz (mínimo {tema.quiz.porcentaje_aprobacion}%) '
                        'antes de completar este tema.'
                    ),
                }, status=400)

        progreso, _ = Progreso.objects.get_or_create(usuario=request.user, tema=tema)
        if not progreso.completado:
            progreso.marcar_completado()
        return Response(ProgresoSerializer(progreso).data)


class ResultadoQuizViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ResultadoQuizSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ResultadoQuiz.objects.filter(usuario=self.request.user)
