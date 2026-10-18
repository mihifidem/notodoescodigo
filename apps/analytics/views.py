"""Views for the analytics app."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import TemplateView
from django.db.models import Count, Sum, Q, Avg, F, ExpressionWrapper, DurationField
from django.db import transaction
from django.db.models.functions import Greatest
from django.core.paginator import Paginator
from django.utils import timezone
from datetime import timedelta

from apps.users.models import User, Notificacion, Aula
from apps.courses.models import Curso, Progreso, ResultadoQuiz, InscripcionCurso, TemaRecursoVisualizacion
from apps.gamification.models import InsigniaUsuario, Logro, LogroUsuario, Mision, MisionUsuario
from apps.rewards.models import CanjeRecompensa
from apps.blog.models import PostBlog, LecturaPostUsuario, ValoracionPost
from .forms import (
    AsignarLogroForm,
    EnviarMensajeForm,
    AsignarMisionForm,
    RecalcularPuntosForm,
    LimpiarDuplicadosPuntosForm,
)
from .models import RegistroActividad


class DashboardAdminView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """Admin-only analytics dashboard."""
    template_name = 'analytics/dashboard_admin.html'

    def test_func(self):
        return self.request.user.is_staff

    @staticmethod
    @transaction.atomic
    def _recalcular_puntos_usuario(usuario):
        usuario = User.objects.select_for_update().get(pk=usuario.pk)
        puntos_temas = (
            Progreso.objects.filter(usuario=usuario, completado=True)
            .aggregate(total=Sum('tema__puntos_otorgados'))['total'] or 0
        )
        puntos_quiz = (
            ResultadoQuiz.objects.filter(usuario=usuario, aprobado=True)
            .aggregate(total=Sum('quiz__puntos_bonus'))['total'] or 0
        )
        puntos_recursos = (
            TemaRecursoVisualizacion.objects.filter(usuario=usuario)
            .aggregate(total=Sum('puntos_otorgados'))['total'] or 0
        )
        puntos_logros = (
            LogroUsuario.objects.filter(usuario=usuario)
            .aggregate(total=Sum('puntos_asignados'))['total'] or 0
        )
        puntos_misiones = (
            MisionUsuario.objects.filter(usuario=usuario, completada=True)
            .aggregate(total=Sum('mision__puntos_recompensa'))['total'] or 0
        )

        puntos_totales_recalculados = (
            puntos_temas + puntos_quiz + puntos_recursos + puntos_logros + puntos_misiones
        )
        puntos_gastados = (
            CanjeRecompensa.objects.filter(usuario=usuario)
            .aggregate(total=Sum('puntos_gastados'))['total'] or 0
        )
        from apps.duckyclash.local_bank import points_movement_total
        puntos_recalculados = max(puntos_totales_recalculados - puntos_gastados + points_movement_total(usuario), 0)

        nuevo_nivel = 'noob'
        for nivel, umbral in User.NIVEL_UMBRAL_PUNTOS:
            if puntos_totales_recalculados >= umbral:
                nuevo_nivel = nivel

        cambios = {
            'puntos': usuario.puntos,
            'puntos_totales': usuario.puntos_totales,
            'nivel': usuario.nivel,
        }

        usuario.puntos = puntos_recalculados
        usuario.puntos_totales = puntos_totales_recalculados
        usuario.nivel = nuevo_nivel
        usuario.save(update_fields=['puntos', 'puntos_totales', 'nivel'])

        return cambios, {
            'puntos': puntos_recalculados,
            'puntos_totales': puntos_totales_recalculados,
            'nivel': nuevo_nivel,
        }

    @staticmethod
    def _duracion_a_segundos(valor):
        if not valor:
            return 0
        return int(valor.total_seconds())

    @staticmethod
    def _limpiar_duplicados_puntos(usuarios_qs):
        duplicados = (
            RegistroActividad.objects.filter(
                usuario__in=usuarios_qs,
                puntos_ganados__gt=0,
                tipo='acceso',
                descripcion__startswith='Lectura completada del post:',
            )
            .values('usuario_id', 'tipo', 'descripcion', 'puntos_ganados')
            .annotate(total=Count('id'))
            .filter(total__gt=1)
        )

        total_registros_eliminados = 0
        total_puntos_ajustados = 0

        for grupo in duplicados:
            actividades = RegistroActividad.objects.filter(
                usuario_id=grupo['usuario_id'],
                tipo=grupo['tipo'],
                descripcion=grupo['descripcion'],
                puntos_ganados=grupo['puntos_ganados'],
            ).order_by('timestamp', 'id')

            ids_a_eliminar = list(actividades.values_list('id', flat=True))[1:]
            if not ids_a_eliminar:
                continue

            puntos_a_retirar = grupo['puntos_ganados'] * len(ids_a_eliminar)
            usuario = User.objects.filter(pk=grupo['usuario_id']).first()
            if usuario:
                User.objects.filter(pk=usuario.pk).update(
                    puntos=Greatest(F('puntos') - puntos_a_retirar, 0),
                    puntos_totales=Greatest(F('puntos_totales') - puntos_a_retirar, 0),
                )
                usuario.refresh_from_db(fields=['puntos', 'puntos_totales'])
                usuario.actualizar_nivel()

            borrados, _ = RegistroActividad.objects.filter(id__in=ids_a_eliminar).delete()
            total_registros_eliminados += borrados
            total_puntos_ajustados += puntos_a_retirar

        return total_registros_eliminados, total_puntos_ajustados

    def post(self, request, *args, **kwargs):
        form_type = request.POST.get('form_type')

        if form_type == 'asignar_mision':
            form = AsignarMisionForm(request.POST)
            if form.is_valid():
                usuarios = form.cleaned_data['usuarios']
                mision = form.cleaned_data['mision']
                aplicar_puntos_ahora = form.cleaned_data['aplicar_puntos_ahora']

                total_asignados = 0
                for usuario in usuarios:
                    MisionUsuario.objects.create(usuario=usuario, mision=mision)
                    total_asignados += 1

                    detalle = f'Se te asigno la mision "{mision.titulo}".'
                    if aplicar_puntos_ahora and mision.puntos_recompensa != 0:
                        usuario.agregar_puntos(mision.puntos_recompensa)
                        if mision.puntos_recompensa > 0:
                            detalle += f' Recibiste {mision.puntos_recompensa} puntos.'
                        else:
                            detalle += f' Se aplico una penalizacion de {abs(mision.puntos_recompensa)} puntos.'

                    Notificacion.objects.create(
                        usuario=usuario,
                        tipo='mision',
                        titulo='Nueva mision asignada',
                        mensaje=detalle,
                    )

                messages.success(
                    request,
                    f'Mision "{mision.titulo}" asignada a {total_asignados} alumno(s).',
                )
                return redirect('analytics:dashboard_admin')

            context = self.get_context_data()
            context['asignar_mision_form'] = form
            context['asignar_logro_form'] = AsignarLogroForm()
            context['enviar_mensaje_form'] = EnviarMensajeForm(request_user=request.user)
            return self.render_to_response(context)

        if form_type == 'asignar_logro':
            form = AsignarLogroForm(request.POST)
            if form.is_valid():
                usuario = form.cleaned_data['usuario']
                logro = form.cleaned_data['logro']
                puntos_asignados = form.cleaned_data['puntos_asignados']

                logro_usuario, created = LogroUsuario.objects.get_or_create(
                    usuario=usuario,
                    logro=logro,
                    defaults={'puntos_asignados': puntos_asignados},
                )

                if created:
                    usuario.agregar_puntos(puntos_asignados)
                    messages.success(
                        request,
                        f'Logro "{logro.nombre}" asignado a {usuario.username} con {puntos_asignados} puntos.',
                    )
                else:
                    messages.warning(
                        request,
                        f'{usuario.username} ya tiene el logro "{logro.nombre}" asignado.',
                    )
                return redirect('analytics:dashboard_admin')

            context = self.get_context_data()
            context['asignar_logro_form'] = form
            context['asignar_mision_form'] = AsignarMisionForm()
            context['enviar_mensaje_form'] = EnviarMensajeForm(request_user=request.user)
            return self.render_to_response(context)

        if form_type == 'enviar_mensaje':
            form = EnviarMensajeForm(request.POST, request_user=request.user)
            if form.is_valid():
                usuario = form.cleaned_data['usuario']
                titulo = form.cleaned_data['titulo']
                mensaje_texto = form.cleaned_data['mensaje']

                Notificacion.objects.create(
                    usuario=usuario,
                    tipo='sistema',
                    titulo=titulo,
                    mensaje=mensaje_texto,
                )
                messages.success(request, f'Mensaje enviado a {usuario.username}.')
                return redirect('analytics:dashboard_admin')

            context = self.get_context_data()
            context['asignar_logro_form'] = AsignarLogroForm()
            context['asignar_mision_form'] = AsignarMisionForm()
            context['enviar_mensaje_form'] = form
            context['recalcular_puntos_form'] = RecalcularPuntosForm()
            return self.render_to_response(context)

        if form_type == 'recalcular_puntos':
            form = RecalcularPuntosForm(request.POST)
            if form.is_valid():
                usuario = form.cleaned_data['usuario']
                recalcular_todos = form.cleaned_data['recalcular_todos']

                if recalcular_todos:
                    usuarios = User.objects.filter(is_active=True, is_staff=False)
                else:
                    usuarios = User.objects.filter(pk=usuario.pk)

                total = 0
                for alumno in usuarios:
                    self._recalcular_puntos_usuario(alumno)
                    total += 1

                if recalcular_todos:
                    messages.success(request, f'Se recalcularon los puntos de {total} alumno(s).')
                else:
                    messages.success(request, f'Se recalcularon los puntos de {usuario.username}.')
                return redirect('analytics:dashboard_admin')

            context = self.get_context_data()
            context['asignar_logro_form'] = AsignarLogroForm()
            context['asignar_mision_form'] = AsignarMisionForm()
            context['enviar_mensaje_form'] = EnviarMensajeForm(request_user=request.user)
            context['recalcular_puntos_form'] = form
            context['limpiar_duplicados_form'] = LimpiarDuplicadosPuntosForm()
            return self.render_to_response(context)

        if form_type == 'limpiar_duplicados_puntos':
            form = LimpiarDuplicadosPuntosForm(request.POST)
            if form.is_valid():
                usuario = form.cleaned_data['usuario']
                limpiar_todos = form.cleaned_data['limpiar_todos']

                if limpiar_todos:
                    usuarios_qs = User.objects.filter(is_active=True, is_staff=False)
                else:
                    usuarios_qs = User.objects.filter(pk=usuario.pk)

                total_registros_eliminados, total_puntos_ajustados = self._limpiar_duplicados_puntos(usuarios_qs)
                messages.success(
                    request,
                    f'Limpieza completada. Registros eliminados: {total_registros_eliminados}. '
                    f'Puntos ajustados: {total_puntos_ajustados}.',
                )
                return redirect('analytics:dashboard_admin')

            context = self.get_context_data()
            context['asignar_logro_form'] = AsignarLogroForm()
            context['asignar_mision_form'] = AsignarMisionForm()
            context['enviar_mensaje_form'] = EnviarMensajeForm(request_user=request.user)
            context['recalcular_puntos_form'] = RecalcularPuntosForm()
            context['limpiar_duplicados_form'] = form
            return self.render_to_response(context)

        if form_type == 'generar_posts_ia':
            keyword = request.POST.get('keyword', 'python').strip() or 'python'
            max_items = max(1, min(int(request.POST.get('max_items', 3) or 3), 10))
            categoria = request.POST.get('categoria', 'Tecnología').strip() or 'Tecnología'
            publicar = request.POST.get('publicar') == '1'
            con_imagen = request.POST.get('con_imagen') == '1'
            try:
                from django.core.management import call_command
                call_command(
                    'generar_posts',
                    keyword=keyword,
                    max=max_items,
                    categoria=categoria,
                    publicar=publicar,
                    con_imagen=con_imagen,
                )
                messages.success(
                    request,
                    f'✅ Generación lanzada: keyword="{keyword}", {max_items} artículo(s). '
                    f'Revisa el blog {"(publicados)" if publicar else "(borradores)"}.',
                )
            except Exception as exc:
                messages.error(request, f'❌ Error al generar posts: {exc}')
            return redirect('analytics:dashboard_admin')

        messages.error(request, 'No se pudo procesar la solicitud.')
        return redirect('analytics:dashboard_admin')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now()
        last_30 = now - timedelta(days=30)

        context['total_usuarios'] = User.objects.filter(is_active=True, is_staff=False).count()
        context['total_cursos'] = Curso.objects.filter(activo=True).count()
        context['total_inscripciones'] = InscripcionCurso.objects.count()
        context['nuevos_usuarios_30d'] = User.objects.filter(
            date_joined__gte=last_30, is_staff=False
        ).count()

        context['total_posts_publicados'] = PostBlog.objects.filter(publicado=True).count()
        context['total_posts_borradores'] = PostBlog.objects.filter(publicado=False).count()
        context['total_lecturas_blog'] = LecturaPostUsuario.objects.count()
        context['total_likes_blog'] = ValoracionPost.objects.count()
        context['lecturas_con_premio'] = LecturaPostUsuario.objects.filter(puntos_otorgados=True).count()

        duracion_media_blog = LecturaPostUsuario.objects.filter(completada_en__isnull=False).aggregate(
            promedio=Avg(
                ExpressionWrapper(
                    F('completada_en') - F('iniciada_en'),
                    output_field=DurationField(),
                )
            )
        )['promedio']
        context['duracion_media_blog_segundos'] = self._duracion_a_segundos(duracion_media_blog)

        # Most difficult topics (highest failure rate in quizzes)
        context['temas_dificiles'] = (
            ResultadoQuiz.objects
            .values('quiz__tema__titulo')
            .annotate(
                total=Count('id'),
                fallos=Count('id', filter=Q(aprobado=False)),
            )
            .filter(total__gte=3)
            .order_by('-fallos')[:5]
        )

        # Student ranking with filters and pagination for positions 11+
        filtro_alumno = (self.request.GET.get('alumno') or '').strip()
        filtro_aula = (self.request.GET.get('aula') or '').strip()
        page_number = self.request.GET.get('page') or '1'

        ranking_qs = User.objects.filter(is_active=True, is_staff=False).select_related('aula')
        if filtro_alumno:
            ranking_qs = ranking_qs.filter(
                Q(username__icontains=filtro_alumno)
                | Q(first_name__icontains=filtro_alumno)
                | Q(last_name__icontains=filtro_alumno)
                | Q(email__icontains=filtro_alumno)
            )
        if filtro_aula == 'sin_aula':
            ranking_qs = ranking_qs.filter(aula__isnull=True)
        elif filtro_aula.isdigit():
            ranking_qs = ranking_qs.filter(aula_id=int(filtro_aula))

        ranking_qs = ranking_qs.order_by('-puntos', 'username')
        total_alumnos_ranking = ranking_qs.count()

        context['alumnos_ranking'] = ranking_qs
        context['top_alumnos'] = ranking_qs[:10]
        context['total_alumnos_ranking'] = total_alumnos_ranking

        resto_alumnos_page = None
        if total_alumnos_ranking > 10:
            resto_qs = ranking_qs[10:]
            paginator = Paginator(resto_qs, 10)
            resto_alumnos_page = paginator.get_page(page_number)

        context['resto_alumnos_page'] = resto_alumnos_page
        context['filtro_alumno'] = filtro_alumno
        context['filtro_aula'] = filtro_aula
        context['aulas_filtro'] = Aula.objects.order_by('nombre')
        context['mostrar_resto_expandido'] = bool(
            filtro_alumno or filtro_aula or (resto_alumnos_page and resto_alumnos_page.number > 1)
        )

        # Activity last 30 days
        context['actividades_recientes'] = RegistroActividad.objects.filter(
            timestamp__gte=last_30
        ).select_related('usuario').order_by('-timestamp')[:30]

        # Courses by enrollment
        context['cursos_populares'] = (
            Curso.objects.annotate(inscritos=Count('inscripciones'))
            .order_by('-inscritos')[:5]
        )

        usuarios_base = User.objects.filter(is_active=True, is_staff=False).order_by('-puntos_totales', 'username')[:30]

        estadisticas_blog_por_usuario = []
        estadisticas_curso_por_usuario = []
        for usuario in usuarios_base:
            lecturas_qs = LecturaPostUsuario.objects.filter(usuario=usuario)
            lecturas_completas_qs = lecturas_qs.filter(completada_en__isnull=False)
            tiempo_total = lecturas_completas_qs.aggregate(
                total=Sum(
                    ExpressionWrapper(
                        F('completada_en') - F('iniciada_en'),
                        output_field=DurationField(),
                    )
                )
            )['total']

            estadisticas_blog_por_usuario.append(
                {
                    'usuario': usuario,
                    'posts_vistos': lecturas_qs.count(),
                    'premios_obtenidos': lecturas_qs.filter(puntos_otorgados=True).count(),
                    'me_gusta': ValoracionPost.objects.filter(usuario=usuario).count(),
                    'tiempo_total_segundos': self._duracion_a_segundos(tiempo_total),
                }
            )

            puntos_temas = (
                Progreso.objects.filter(usuario=usuario, completado=True)
                .aggregate(total=Sum('tema__puntos_otorgados'))['total'] or 0
            )
            puntos_quiz = (
                ResultadoQuiz.objects.filter(usuario=usuario, aprobado=True)
                .aggregate(total=Sum('quiz__puntos_bonus'))['total'] or 0
            )
            puntos_recursos = (
                TemaRecursoVisualizacion.objects.filter(usuario=usuario)
                .aggregate(total=Sum('puntos_otorgados'))['total'] or 0
            )

            estadisticas_curso_por_usuario.append(
                {
                    'usuario': usuario,
                    'inscripciones': InscripcionCurso.objects.filter(usuario=usuario).count(),
                    'cursos_completados': InscripcionCurso.objects.filter(usuario=usuario, completado=True).count(),
                    'temas_completados': Progreso.objects.filter(usuario=usuario, completado=True).count(),
                    'quiz_aprobados': ResultadoQuiz.objects.filter(usuario=usuario, aprobado=True).count(),
                    'puntos_curso': puntos_temas + puntos_quiz + puntos_recursos,
                }
            )

        context['estadisticas_blog_por_usuario'] = sorted(
            estadisticas_blog_por_usuario,
            key=lambda item: (item['posts_vistos'], item['premios_obtenidos'], item['me_gusta']),
            reverse=True,
        )[:20]
        context['estadisticas_curso_por_usuario'] = sorted(
            estadisticas_curso_por_usuario,
            key=lambda item: (item['puntos_curso'], item['temas_completados'], item['quiz_aprobados']),
            reverse=True,
        )[:20]

        context['logros_creados'] = Logro.objects.select_related('insignia').order_by('nombre')
        context['asignar_logro_form'] = context.get('asignar_logro_form') or AsignarLogroForm()
        context['asignar_mision_form'] = context.get('asignar_mision_form') or AsignarMisionForm()
        context['misiones_activas_catalogo'] = Mision.objects.filter(activa=True).order_by('titulo')[:8]
        context['enviar_mensaje_form'] = context.get('enviar_mensaje_form') or EnviarMensajeForm(
            request_user=self.request.user
        )
        context['recalcular_puntos_form'] = context.get('recalcular_puntos_form') or RecalcularPuntosForm()
        context['limpiar_duplicados_form'] = context.get('limpiar_duplicados_form') or LimpiarDuplicadosPuntosForm()

        return context


class AdminAlumnoPuntosView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """Detail page to inspect and manage point records for a single student."""

    template_name = 'analytics/admin_alumno_puntos.html'
    paginate_by = 30

    def test_func(self):
        return self.request.user.is_staff

    def dispatch(self, request, *args, **kwargs):
        self.alumno = get_object_or_404(
            User.objects.select_related('aula'),
            pk=kwargs['usuario_id'],
            is_staff=False,
        )
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if request.POST.get('accion') == 'recalcular_acumulado':
            total_acumulado = (
                RegistroActividad.objects.filter(
                    usuario=self.alumno,
                    puntos_ganados__gt=0,
                ).aggregate(total=Sum('puntos_ganados'))['total'] or 0
            )
            self.alumno.puntos_totales = total_acumulado
            self.alumno.save(update_fields=['puntos_totales'])
            self.alumno.actualizar_nivel()
            messages.success(
                request,
                f'Contador acumulado actualizado: {total_acumulado} puntos. El saldo canjeable no se modifica.',
            )
            return redirect('analytics:admin_alumno_puntos', usuario_id=self.alumno.pk)

        registro_id = request.POST.get('registro_id')
        if not registro_id:
            messages.error(request, 'No se indico el registro a eliminar.')
            return redirect('analytics:admin_alumno_puntos', usuario_id=self.alumno.pk)

        registro = RegistroActividad.objects.filter(
            pk=registro_id,
            usuario=self.alumno,
        ).first()

        if not registro:
            messages.error(request, 'El registro seleccionado no existe para este alumno.')
            return redirect('analytics:admin_alumno_puntos', usuario_id=self.alumno.pk)

        puntos_a_retirar = max(registro.puntos_ganados, 0)
        registro.delete()

        if puntos_a_retirar > 0:
            User.objects.filter(pk=self.alumno.pk).update(
                puntos=Greatest(F('puntos') - puntos_a_retirar, 0),
                puntos_totales=Greatest(F('puntos_totales') - puntos_a_retirar, 0),
            )
            self.alumno.refresh_from_db(fields=['puntos', 'puntos_totales'])
            self.alumno.actualizar_nivel()

        messages.success(
            request,
            f'Registro eliminado. Puntos ajustados: {puntos_a_retirar}.',
        )
        return redirect('analytics:admin_alumno_puntos', usuario_id=self.alumno.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        actividades_qs = (
            RegistroActividad.objects.filter(
                usuario=self.alumno,
                puntos_ganados__gt=0,
            )
            .order_by('-timestamp', '-id')
        )

        paginator = Paginator(actividades_qs, self.paginate_by)
        page_number = self.request.GET.get('page') or '1'
        actividades_page = paginator.get_page(page_number)

        context['alumno'] = self.alumno
        context['actividades_page'] = actividades_page
        context['total_registros_puntos'] = actividades_qs.count()
        context['total_puntos_registrados'] = (
            actividades_qs.aggregate(total=Sum('puntos_ganados'))['total'] or 0
        )
        return context


class ProgresoAlumnoView(LoginRequiredMixin, TemplateView):
    """Personal progress analytics for the logged-in student."""
    template_name = 'analytics/progreso_alumno.html'

    def get_context_data(self, **kwargs):
        import json
        from apps.evaluations.models import EntregaEvaluacion
        from apps.nexus.models import AssessmentSession, DNAProfile

        context = super().get_context_data(**kwargs)
        user = self.request.user

        # Course progress
        inscripciones = InscripcionCurso.objects.filter(usuario=user).select_related('curso')
        progreso_por_curso = []
        for insc in inscripciones:
            total = insc.curso.temas.count()
            completados = Progreso.objects.filter(
                usuario=user, tema__curso=insc.curso, completado=True
            ).count()
            pct = int((completados / total) * 100) if total else 0
            progreso_por_curso.append({
                'curso': insc.curso,
                'completados': completados,
                'total': total,
                'porcentaje': pct,
                'completado': insc.completado,
            })

        context['progreso_cursos'] = progreso_por_curso
        context['actividades'] = RegistroActividad.objects.filter(usuario=user)[:20]
        context['resultados_quiz'] = ResultadoQuiz.objects.filter(
            usuario=user
        ).select_related('quiz__tema__curso').order_by('-fecha')[:10]

        # Evaluaciones corregidas
        entregas = (
            EntregaEvaluacion.objects
            .filter(alumno=user, estado=EntregaEvaluacion.ESTADO_CORREGIDA)
            .select_related('evaluacion', 'correccion')
            .order_by('fecha_entrega')
        )
        eval_history = []
        for entrega in entregas:
            if not hasattr(entrega, 'correccion'):
                continue
            max_pts = float(entrega.evaluacion.max_puntuacion)
            pts = float(entrega.correccion.puntuacion_total)
            pct = round((pts / max_pts) * 100, 1) if max_pts else 0
            eval_history.append({
                'titulo': entrega.evaluacion.titulo,
                'fecha': entrega.fecha_entrega,
                'puntuacion': pts,
                'max_puntuacion': max_pts,
                'porcentaje': pct,
                'aprobado': pts >= max_pts * 0.5,
                'tipo': entrega.evaluacion.get_tipo_prueba_display(),
            })
        context['eval_history'] = eval_history

        aprobadas = sum(1 for e in eval_history if e['aprobado'])
        media_pct = round(sum(e['porcentaje'] for e in eval_history) / len(eval_history), 1) if eval_history else 0
        context['eval_stats'] = {
            'total': len(eval_history),
            'aprobadas': aprobadas,
            'reprobadas': len(eval_history) - aprobadas,
            'media_pct': media_pct,
        }

        context['eval_labels_json'] = json.dumps([e['titulo'][:28] for e in eval_history])
        context['eval_scores_json'] = json.dumps([e['porcentaje'] for e in eval_history])
        context['eval_colors_json'] = json.dumps([
            'rgba(25,135,84,0.85)' if e['aprobado'] else 'rgba(220,53,69,0.85)'
            for e in eval_history
        ])

        # Nexus assessment sessions
        nexus_sessions = (
            AssessmentSession.objects
            .filter(candidate=user, status=AssessmentSession.STATUS_COMPLETED)
            .select_related('assessment')
            .prefetch_related('module_results__module')
            .order_by('-completed_at')
        )
        context['nexus_sessions'] = list(nexus_sessions)

        latest = nexus_sessions.first()
        context['nexus_latest_session'] = latest
        module_labels, module_scores, module_colors = [], [], []
        if latest:
            for mr in latest.module_results.select_related('module').order_by('module__order'):
                module_labels.append(mr.module.name)
                module_scores.append(float(mr.score))
                module_colors.append(mr.module.color or '#1B3C59')

        context['nexus_module_labels_json'] = json.dumps(module_labels)
        context['nexus_module_scores_json'] = json.dumps(module_scores)
        context['nexus_module_colors_json'] = json.dumps(module_colors)

        # DNA Profile master indices
        try:
            dna = user.dna_profile
            context['dna_profile'] = dna
            context['dna_indices_json'] = json.dumps([
                float(dna.leadership_score), float(dna.innovation_score),
                float(dna.technology_score), float(dna.future_score),
                float(dna.career_score), float(dna.executive_score),
                float(dna.motivation_score), float(dna.cognitive_score),
                float(dna.emotional_score),
            ])
        except Exception:
            context['dna_profile'] = None
            context['dna_indices_json'] = json.dumps([])

        return context


class CVTrackerView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """Admin view: track CV completion for all students."""
    template_name = 'analytics/cv_tracker.html'

    def test_func(self):
        return self.request.user.is_staff

    # Section keys, labels and max points (matches curriculum/signals.py)
    SECCIONES = [
        ('personal',    'Datos personales',          25),
        ('perfil',      'Perfil profesional',         25),
        ('experiencia', 'Experiencia laboral',        30),
        ('educacion',   'Formación académica',        25),
        ('proyectos',   'Proyectos',                  25),
        ('formacion',   'Formación complementaria',   20),
        ('habilidades', 'Habilidades',                20),
        ('idiomas',     'Idiomas',                    20),
        ('logros',      'Logros',                     20),
        ('voluntariado','Voluntariado',                20),
        ('redes',       'Redes profesionales',        15),
        ('otros',       'Otros datos',                10),
        ('intereses',   'Intereses',                  10),
    ]
    PUNTOS_TOTAL_CV = sum(pts for _, _, pts in SECCIONES)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.curriculum.models import CurriculumVitae
        from apps.users.models import Aula

        filtro_alumno = (self.request.GET.get('alumno') or '').strip()
        filtro_aula = (self.request.GET.get('aula') or '').strip()

        alumnos_qs = (
            User.objects.filter(is_active=True, is_staff=False)
            .select_related('aula')
            .order_by('username')
        )
        if filtro_alumno:
            alumnos_qs = alumnos_qs.filter(
                Q(username__icontains=filtro_alumno)
                | Q(first_name__icontains=filtro_alumno)
                | Q(last_name__icontains=filtro_alumno)
            )
        if filtro_aula == 'sin_aula':
            alumnos_qs = alumnos_qs.filter(aula__isnull=True)
        elif filtro_aula.isdigit():
            alumnos_qs = alumnos_qs.filter(aula_id=int(filtro_aula))

        # Pre-fetch all CVs and related data in as few queries as possible
        cvs = {
            cv.user_id: cv
            for cv in CurriculumVitae.objects.filter(user__in=alumnos_qs)
            .prefetch_related(
                'personal_info', 'professional_profile',
                'work_experiences', 'educations', 'trainings',
                'skills', 'languages', 'projects', 'achievements',
                'volunteerings', 'social_networks', 'interests', 'other_info',
            )
        }

        rows = []
        seccion_totals = {key: 0 for key, _, _ in self.SECCIONES}

        for alumno in alumnos_qs:
            cv = cvs.get(alumno.pk)
            if cv is None:
                secciones_alumno = {key: False for key, _, _ in self.SECCIONES}
            else:
                secciones_alumno = {
                    'personal':    hasattr(cv, 'personal_info'),
                    'perfil':      hasattr(cv, 'professional_profile'),
                    'experiencia': cv.work_experiences.exists(),
                    'educacion':   cv.educations.exists(),
                    'formacion':   cv.trainings.exists(),
                    'habilidades': cv.skills.exists(),
                    'idiomas':     cv.languages.exists(),
                    'proyectos':   cv.projects.exists(),
                    'logros':      cv.achievements.exists(),
                    'voluntariado':cv.volunteerings.exists(),
                    'redes':       hasattr(cv, 'social_networks'),
                    'intereses':   cv.interests.exists(),
                    'otros':       hasattr(cv, 'other_info'),
                }

            for key, _, _ in self.SECCIONES:
                if secciones_alumno.get(key):
                    seccion_totals[key] += 1

            done = sum(1 for v in secciones_alumno.values() if v)
            total = len(self.SECCIONES)
            pct = int(done / total * 100) if total else 0
            puntos_cv = sum(
                pts for key, _, pts in self.SECCIONES if secciones_alumno.get(key)
            )

            rows.append({
                'alumno': alumno,
                'secciones': secciones_alumno,
                'done': done,
                'total': total,
                'pct': pct,
                'puntos_cv': puntos_cv,
            })

        # Sort: most complete first
        rows.sort(key=lambda r: (-r['pct'], r['alumno'].username))

        total_alumnos = len(rows)
        cv_completo = sum(1 for r in rows if r['pct'] == 100)
        cv_iniciado = sum(1 for r in rows if 0 < r['pct'] < 100)
        cv_vacio = sum(1 for r in rows if r['pct'] == 0)

        paginator = Paginator(rows, 25)
        page_number = self.request.GET.get('page') or '1'
        page_obj = paginator.get_page(page_number)

        context.update({
            'page_obj': page_obj,
            'secciones_meta': self.SECCIONES,
            'seccion_totals': seccion_totals,
            'total_alumnos': total_alumnos,
            'cv_completo': cv_completo,
            'cv_iniciado': cv_iniciado,
            'cv_vacio': cv_vacio,
            'puntos_total_cv': self.PUNTOS_TOTAL_CV,
            'filtro_alumno': filtro_alumno,
            'filtro_aula': filtro_aula,
            'aulas_filtro': Aula.objects.order_by('nombre'),
        })
        return context
