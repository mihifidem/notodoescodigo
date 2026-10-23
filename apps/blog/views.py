from datetime import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Avg, Case, Count, IntegerField, Q, Value, When
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView

from apps.analytics.models import RegistroActividad
from apps.users.access import can
from apps.users.models import AccessMembership

from .models import (
    CategoriaBlog,
    HashtagBlog,
    LecturaPostUsuario,
    PostBlog,
    SubcategoriaBlog,
    ValoracionPost,
)


def _es_admin(usuario):
    if not usuario.is_authenticated:
        return False
    return usuario.is_superuser or usuario.has_perm('blog.change_postblog')


def _puede_ver_contenido_privado(usuario):
    """El contenido premium requiere una membresía PLUS vigente o superior."""
    return can(
        usuario,
        'view_plan_content',
        required_plan=AccessMembership.Plan.PLUS,
    ).allowed


def _es_premium(usuario):
    """Puede ver contenido_privado y posts privados."""
    if not usuario.is_authenticated:
        return False
    return _puede_ver_contenido_privado(usuario)


def _es_usuario_registrado_valido(usuario):
    """Puede ver posts semipúblicos (cualquier usuario autenticado)."""
    if not usuario.is_authenticated:
        return False
    return True


def _puede_ver_post(usuario, post):
    if post.visibilidad == PostBlog.VISIBILIDAD_PUBLICA:
        return True
    if post.visibilidad == PostBlog.VISIBILIDAD_SEMIPUBLICA:
        return usuario.is_authenticated
    if post.visibilidad == PostBlog.VISIBILIDAD_PRIVADA:
        return _es_premium(usuario)
    return False


def _posts_visibles_para_usuario(usuario, include_news_lite=False):
    queryset = PostBlog.objects.filter(
        publicado=True,
        publicado_en__lte=timezone.now(),
    )
    if not include_news_lite:
        queryset = queryset.exclude(categoria__nombre='News Lite')
    if not usuario.is_authenticated:
        return queryset.filter(visibilidad=PostBlog.VISIBILIDAD_PUBLICA)
    if _es_premium(usuario):
        return queryset
    return queryset.filter(
        visibilidad__in=[PostBlog.VISIBILIDAD_PUBLICA, PostBlog.VISIBILIDAD_SEMIPUBLICA]
    )


def _posts_recomendados_para_usuario(usuario, post, limite=4):
    base_queryset = (
        _posts_visibles_para_usuario(usuario)
        .exclude(id=post.id)
        .select_related('categoria', 'subcategoria')
        .prefetch_related('hashtags')
        .annotate(
            promedio_rating=Avg('valoraciones__valor'),
            total_valoraciones=Count('valoraciones'),
        )
    )

    if not usuario.is_authenticated:
        return base_queryset.filter(categoria=post.categoria).order_by('-promedio_rating', '-total_valoraciones', '-publicado_en')[:limite]

    categorias_favoritas = list(
        ValoracionPost.objects.filter(usuario=usuario)
        .values_list('post__categoria_id', flat=True)
    )
    hashtags_vistos = list(
        ValoracionPost.objects.filter(usuario=usuario)
        .values_list('post__hashtags__id', flat=True)
    )
    hashtags_vistos = [tag_id for tag_id in hashtags_vistos if tag_id]

    lecturas_recientes = list(
        LecturaPostUsuario.objects.filter(usuario=usuario)
        .order_by('-iniciada_en')
        .values_list('post__id', flat=True)[:20]
    )

    queryset = base_queryset
    if categorias_favoritas:
        queryset = queryset.filter(categoria_id__in=categorias_favoritas)

    if hashtags_vistos:
        queryset = queryset.filter(hashtags__id__in=hashtags_vistos)

    if lecturas_recientes:
        queryset = queryset.exclude(id__in=lecturas_recientes)

    if not queryset.exists():
        queryset = base_queryset.filter(categoria=post.categoria)

    if hashtags_vistos:
        queryset = queryset.annotate(
            prioridad_hashtag=Case(
                When(hashtags__id__in=hashtags_vistos, then=Value(2)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
    else:
        queryset = queryset.annotate(prioridad_hashtag=Value(0, output_field=IntegerField()))

    if categorias_favoritas:
        queryset = queryset.annotate(
            prioridad_categoria=Case(
                When(categoria_id__in=categorias_favoritas, then=Value(3)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
    else:
        queryset = queryset.annotate(prioridad_categoria=Value(1, output_field=IntegerField()))

    return queryset.distinct().order_by('-prioridad_categoria', '-prioridad_hashtag', '-promedio_rating', '-total_valoraciones', '-publicado_en')[:limite]


class BlogListView(ListView):
    template_name = 'blog/list.html'
    context_object_name = 'posts'
    paginate_by = 9

    def get_queryset(self):
        q = (self.request.GET.get('q') or '').strip()
        categoria = (self.request.GET.get('categoria') or '').strip()
        include_news_lite = categoria.lower() == 'news-lite'

        queryset = (
            _posts_visibles_para_usuario(self.request.user, include_news_lite=include_news_lite)
            .select_related('categoria', 'subcategoria')
            .prefetch_related('hashtags')
            .annotate(promedio_rating=Avg('valoraciones__valor'), total_valoraciones=Count('valoraciones'))
            .order_by('-destacado', '-publicado_en')
        )

        subcategoria = (self.request.GET.get('subcategoria') or '').strip()
        hashtag = (self.request.GET.get('hashtag') or '').strip()
        fecha_desde = (self.request.GET.get('fecha_desde') or '').strip()
        fecha_hasta = (self.request.GET.get('fecha_hasta') or '').strip()

        if q:
            queryset = queryset.filter(
                Q(titulo__icontains=q)
                | Q(resumen__icontains=q)
                | Q(contenido_publico__icontains=q)
                | Q(contenido_privado__icontains=q)
            )
        if categoria:
            queryset = queryset.filter(categoria__slug=categoria)
        if subcategoria:
            queryset = queryset.filter(subcategoria__slug=subcategoria)
        if hashtag:
            queryset = queryset.filter(hashtags__slug=hashtag)
        if fecha_desde:
            queryset = queryset.filter(publicado_en__date__gte=fecha_desde)
        if fecha_hasta:
            queryset = queryset.filter(publicado_en__date__lte=fecha_hasta)

        return queryset.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['q_actual'] = (self.request.GET.get('q') or '').strip()
        context['categoria_actual'] = (self.request.GET.get('categoria') or '').strip()
        context['subcategoria_actual'] = (self.request.GET.get('subcategoria') or '').strip()
        context['hashtag_actual'] = (self.request.GET.get('hashtag') or '').strip()
        context['fecha_desde_actual'] = (self.request.GET.get('fecha_desde') or '').strip()
        context['fecha_hasta_actual'] = (self.request.GET.get('fecha_hasta') or '').strip()
        context['mostrar_todas_subcategorias'] = self.request.GET.get('mostrar_todas_subcategorias') == '1'

        visibles = _posts_visibles_para_usuario(self.request.user)

        context['categorias'] = (
            CategoriaBlog.objects.filter(activa=True)
            .exclude(nombre='News Lite')
            .annotate(total_posts=Count('posts', filter=Q(posts__in=visibles)))
            .filter(total_posts__gt=0)
            .order_by('nombre')
        )
        context['subcategorias'] = (
            SubcategoriaBlog.objects.filter(activa=True)
            .exclude(categoria__nombre='News Lite')
            .annotate(total_posts=Count('posts', filter=Q(posts__in=visibles)))
            .filter(total_posts__gt=0)
            .order_by('nombre')
        )
        context['hashtags'] = HashtagBlog.objects.annotate(total_posts=Count('posts', filter=Q(posts__in=visibles))).filter(total_posts__gt=0).order_by('nombre')

        categoria_actual = (self.request.GET.get('categoria') or '').strip()
        mostrar_todas_subcategorias = self.request.GET.get('mostrar_todas_subcategorias') == '1'

        categorias = list(
            CategoriaBlog.objects.filter(activa=True)
            .exclude(nombre='News Lite')
            .annotate(total_posts=Count('posts', filter=Q(posts__in=visibles)))
            .filter(total_posts__gt=0)
            .order_by('nombre')
        )
        grupos_categorias = []
        for categoria in categorias:
            subcategorias_totales = list(
                categoria.subcategorias.filter(activa=True)
                .annotate(total_posts=Count('posts', filter=Q(posts__in=visibles)))
                .filter(total_posts__gt=0)
                .order_by('nombre')
            )
            if mostrar_todas_subcategorias and categoria.slug == categoria_actual:
                subcategorias_mostradas = subcategorias_totales
            else:
                subcategorias_mostradas = subcategorias_totales
            grupos_categorias.append({
                'slug': categoria.slug,
                'nombre': categoria.nombre,
                'total_posts': categoria.total_posts or 0,
                'subcategorias': subcategorias_mostradas,
                'subcategorias_todas': subcategorias_totales,
            })

        context['grupos_categorias'] = grupos_categorias
        context['grupos_subcategorias'] = (
            SubcategoriaBlog.objects.filter(activa=True)
            .exclude(categoria__nombre='News Lite')
            .annotate(total_posts=Count('posts', filter=Q(posts__in=visibles)))
            .filter(total_posts__gt=0)
            .select_related('categoria')
            .order_by('categoria__nombre', 'nombre')
        )
        context['grupos_hashtags'] = (
            HashtagBlog.objects.annotate(total_posts=Count('posts', filter=Q(posts__in=visibles)))
            .filter(total_posts__gt=0)
            .order_by('-total_posts', 'nombre')
        )
        context['es_premium'] = _es_premium(self.request.user)
        return context


class BlogDetailView(DetailView):
    template_name = 'blog/detail.html'
    context_object_name = 'post'

    def get_queryset(self):
        return (
            _posts_visibles_para_usuario(self.request.user)
            .select_related('categoria', 'subcategoria')
            .prefetch_related('hashtags')
            .annotate(promedio_rating=Avg('valoraciones__valor'), total_valoraciones=Count('valoraciones'))
        )

    def get_object(self, queryset=None):
        return get_object_or_404(self.get_queryset(), slug=self.kwargs['slug'])

    def dispatch(self, request, *args, **kwargs):
        post = self.get_object()
        if not _puede_ver_post(request.user, post):
            if request.user.is_authenticated:
                messages.error(request, 'No tienes permisos para ver este post.')
            else:
                messages.error(request, 'Debes iniciar sesion para ver este contenido.')
            return redirect('blog:list')
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.error(request, 'Debes iniciar sesion para valorar posts.')
            return redirect('account_login')

        post = self.get_object()
        try:
            valor = int(request.POST.get('valor', 0))
        except ValueError:
            valor = 0

        if valor < 1 or valor > 5:
            messages.error(request, 'La valoracion debe estar entre 1 y 5 duckies.')
            return redirect('blog:detail', slug=post.slug)

        ValoracionPost.objects.update_or_create(
            usuario=request.user,
            post=post,
            defaults={'valor': valor},
        )
        messages.success(request, 'Tu valoracion se guardo correctamente.')
        return redirect('blog:detail', slug=post.slug)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        post = context['post']
        usuario = self.request.user
        context['es_premium'] = _es_premium(usuario)
        context['puede_ver_privado'] = _puede_ver_contenido_privado(usuario)

        if post.contenido_privado and not context['puede_ver_privado']:
            if not usuario.is_authenticated:
                messages.warning(
                    self.request,
                    'Debes iniciar sesion y tener acceso autorizado para ver este contenido privado.',
                )
            elif not _puede_ver_contenido_privado(usuario):
                messages.warning(
                    self.request,
                    'No tienes autorización para ver el contenido privado de este post.',
                )
            else:
                messages.warning(
                    self.request,
                    'No tienes permisos para ver el contenido privado de este post.',
                )

        if usuario.is_authenticated:
            valoracion_usuario = ValoracionPost.objects.filter(usuario=usuario, post=post).first()
            context['valoracion_usuario'] = valoracion_usuario.valor if valoracion_usuario else 0

            lectura = LecturaPostUsuario.objects.filter(usuario=usuario, post=post).first()
            context['lectura_completada'] = bool(lectura and lectura.puntos_otorgados)
        else:
            context['valoracion_usuario'] = 0
            context['lectura_completada'] = False

        context['puntos_lectura'] = post.puntos_lectura
        context['segundos_requeridos'] = post.segundos_objetivo
        context['posts_recomendados'] = _posts_recomendados_para_usuario(usuario, post)

        if usuario.is_authenticated:
            self.request.session[f'blog_post_inicio_{post.id}'] = timezone.now().isoformat()
        return context


class RegistrarLecturaCompletaView(LoginRequiredMixin, View):
    def post(self, request, slug):
        post = get_object_or_404(_posts_visibles_para_usuario(request.user), slug=slug)

        if post.visibilidad == PostBlog.VISIBILIDAD_PRIVADA and not _es_premium(request.user):
            return JsonResponse({'ok': False, 'error': 'No autorizado.'}, status=403)

        session_key = f'blog_post_inicio_{post.id}'
        inicio_raw = request.session.get(session_key)
        if not inicio_raw:
            return JsonResponse({'ok': False, 'error': 'No se registro inicio de lectura.'}, status=400)

        try:
            inicio = datetime.fromisoformat(inicio_raw)
            if timezone.is_naive(inicio):
                inicio = timezone.make_aware(inicio, timezone.get_current_timezone())
        except ValueError:
            return JsonResponse({'ok': False, 'error': 'Marca de tiempo invalida.'}, status=400)

        transcurridos = int((timezone.now() - inicio).total_seconds())
        segundos_minimos = post.segundos_objetivo
        if transcurridos < segundos_minimos:
            return JsonResponse(
                {
                    'ok': False,
                    'completo': False,
                    'faltan_segundos': segundos_minimos - transcurridos,
                },
                status=400,
            )

        lectura, creada = LecturaPostUsuario.objects.get_or_create(
            usuario=request.user,
            post=post,
            defaults={'iniciada_en': inicio},
        )

        if not lectura.puntos_otorgados:
            request.user.agregar_puntos(post.puntos_lectura)
            lectura.puntos_otorgados = True
            lectura.completada_en = timezone.now()
            if creada:
                lectura.iniciada_en = inicio
            lectura.save(update_fields=['iniciada_en', 'completada_en', 'puntos_otorgados'])
            RegistroActividad.objects.create(
                usuario=request.user,
                tipo='acceso',
                descripcion=f'Lectura completada del post: {post.titulo}',
                puntos_ganados=post.puntos_lectura,
            )
            request.session.pop(session_key, None)
            return JsonResponse(
                {
                    'ok': True,
                    'completo': True,
                    'puntos_ganados': post.puntos_lectura,
                    'puntos_totales': request.user.puntos_totales,
                }
            )

        return JsonResponse({'ok': True, 'completo': True, 'puntos_ganados': 0})


# ---------------------------------------------------------------------------
# API ViewSet (DRF)
# ---------------------------------------------------------------------------

from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from .serializers import PostBlogDetailSerializer, PostBlogListSerializer


class PostBlogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /api/posts/         — listado paginado de posts publicados.
    GET /api/posts/{slug}/  — detalle de un post (contenido_privado solo para premium).
    """
    lookup_field = 'slug'
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        return (
            _posts_visibles_para_usuario(self.request.user)
            .select_related('categoria', 'subcategoria')
            .prefetch_related('hashtags')
            .order_by('-destacado', '-publicado_en')
        )

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return PostBlogDetailSerializer
        return PostBlogListSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
