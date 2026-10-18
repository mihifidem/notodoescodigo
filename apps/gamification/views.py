"""Views for the gamification app."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.urls import reverse_lazy
from django.views.generic import ListView, TemplateView, CreateView
from django.db.models import Count

from rest_framework import viewsets, permissions

from .models import Insignia, InsigniaUsuario, Logro, LogroUsuario, Mision, MisionUsuario
from .serializers import InsigniaSerializer, LogroSerializer, MisionUsuarioSerializer
from .forms import InsigniaForm
from apps.courses.models import Curso, TipoCurso
from apps.users.models import User
from .utils import get_insignias_activas_ids, get_insignias_visibles_para_usuario


# ---------------------------------------------------------------------------
# HTML Views
# ---------------------------------------------------------------------------

class InsigniasView(LoginRequiredMixin, TemplateView):
    template_name = 'gamification/insignias.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        activas_ids = get_insignias_activas_ids(user)

        if user.is_staff:
            # Staff: all visible insignias with optional GET filters
            insignias_qs = get_insignias_visibles_para_usuario(user)
            tipo_curso_id = self.request.GET.get('tipo_curso') or ''
            curso_id = self.request.GET.get('curso') or ''
            if tipo_curso_id:
                insignias_qs = insignias_qs.filter(
                    curso_objetivo__tipo_curso_id=tipo_curso_id
                ) | insignias_qs.filter(
                    tema_objetivo__curso__tipo_curso_id=tipo_curso_id
                )
            if curso_id:
                insignias_qs = insignias_qs.filter(
                    curso_objetivo_id=curso_id
                ) | insignias_qs.filter(
                    tema_objetivo__curso_id=curso_id
                )
            context['tipos_curso'] = TipoCurso.objects.filter(activo=True)
            context['cursos'] = Curso.objects.filter(activo=True).select_related('tipo_curso')
            context['filtro_tipo_curso_id'] = tipo_curso_id
            context['filtro_curso_id'] = curso_id
        else:
            insignias_qs = get_insignias_visibles_para_usuario(user)

        insignias_list = list(insignias_qs)
        visible_ids = {ins.id for ins in insignias_list}
        context['insignias_con_estado'] = [(ins, ins.id in activas_ids) for ins in insignias_list]
        context['total_obtenidas'] = len(activas_ids & visible_ids)
        context['total_insignias'] = len(insignias_list)
        return context


class LogrosView(LoginRequiredMixin, TemplateView):
    template_name = 'gamification/logros.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        obtenidos_ids = set(
            LogroUsuario.objects.filter(usuario=user).values_list('logro_id', flat=True)
        )
        logros_visibles = Logro.objects.filter(oculto=False).select_related('insignia')
        context['insignias_ganadas_por_logros'] = [
            logro.insignia
            for logro in logros_visibles
            if logro.id in obtenidos_ids and logro.insignia_id
        ]
        context['logros_con_estado'] = [(l, l.id in obtenidos_ids) for l in logros_visibles]
        return context


class MisionesView(LoginRequiredMixin, ListView):
    template_name = 'gamification/misiones.html'
    context_object_name = 'misiones_usuario'

    def get_queryset(self):
        return MisionUsuario.objects.filter(
            usuario=self.request.user
        ).select_related('mision').order_by('completada', '-fecha_asignada')


class RankingView(LoginRequiredMixin, ListView):
    template_name = 'gamification/ranking.html'
    context_object_name = 'ranking'
    paginate_by = 25

    def get_queryset(self):
        return User.objects.filter(
            is_active=True, is_staff=False
        ).order_by('-puntos_totales').only('username', 'first_name', 'avatar', 'puntos', 'puntos_totales', 'nivel', 'streak')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Current user position
        all_ids = list(
            User.objects.filter(is_active=True, is_staff=False)
            .order_by('-puntos_totales')
            .values_list('id', flat=True)
        )
        try:
            context['mi_posicion'] = all_ids.index(self.request.user.id) + 1
        except ValueError:
            context['mi_posicion'] = None
        return context


class ComoGanarPuntosView(LoginRequiredMixin, TemplateView):
    template_name = 'gamification/como_ganar_puntos.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        from apps.courses.models import TemaRecurso

        context['formas_automaticas'] = [
            {
                'titulo': 'Completar temas del curso',
                'detalle': 'Cada tema completado suma los puntos configurados en ese tema.',
                'valor': 'Variable segun tema',
            },
            {
                'titulo': 'Aprobar quizzes',
                'detalle': 'Cada quiz aprobado otorga su bonus de puntos.',
                'valor': 'Variable segun quiz',
            },
            {
                'titulo': 'Visualizar recursos de tema por primera vez',
                'detalle': 'Solo puntua la primera visualizacion de cada recurso.',
                'valor': f'+{TemaRecurso.PUNTOS_VISUALIZACION} puntos por recurso',
            },
            {
                'titulo': 'Completar lectura de posts del blog',
                'detalle': 'Al completar el tiempo de lectura requerido de un post.',
                'valor': 'Variable segun post',
            },
            {
                'titulo': 'Completar secciones del CV',
                'detalle': (
                    'Datos personales (+25), perfil profesional (+25), redes (+15), otros datos (+10), '
                    'experiencia (+30), educacion (+25), formacion complementaria (+20), '
                    'habilidades (+20), idiomas (+20), proyectos (+25), logros (+20), '
                    'voluntariado (+20), intereses (+10).'
                ),
                'valor': 'Hasta +265 puntos (primera vez por seccion)',
            },
            {
                'titulo': 'Completar misiones',
                'detalle': 'Las misiones activas otorgan su recompensa al completarlas.',
                'valor': 'Variable segun mision',
            },
            {
                'titulo': 'Abrir enlaces importantes por primera vez',
                'detalle': 'Cada enlace puntua una sola vez por usuario.',
                'valor': '+1 punto por enlace',
            },
        ]

        context['formas_admin'] = [
            {
                'titulo': 'Logros asignados por admin',
                'detalle': 'El equipo puede asignar logros con una cantidad concreta de puntos.',
                'valor': 'Variable segun logro',
            },
            {
                'titulo': 'Misiones asignadas por admin con puntos inmediatos',
                'detalle': 'En algunos casos, el admin puede aplicar puntos al asignar la mision.',
                'valor': 'Variable segun mision',
            },
            {
                'titulo': 'Bug validado por admin',
                'detalle': 'Cuando un bug reportado se valida, recibes los puntos de recompensa.',
                'valor': 'Variable segun bug',
            },
        ]

        context['formas_gastar'] = [
            'Canjear recompensas descuenta puntos disponibles.',
            'Tu nivel depende de puntos totales acumulados, no del saldo disponible.',
        ]
        return context


class InsigniaCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Insignia
    form_class = InsigniaForm
    template_name = 'gamification/insignia_form.html'
    success_url = reverse_lazy('gamification:crear_insignia')

    def test_func(self):
        return self.request.user.is_staff

    def form_valid(self, form):
        messages.success(self.request, 'Insignia creada correctamente.')
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# DRF ViewSets
# ---------------------------------------------------------------------------

class InsigniaViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = InsigniaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Insignia.objects.filter(visible=True)


class MisionUsuarioViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MisionUsuarioSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return MisionUsuario.objects.filter(usuario=self.request.user)
