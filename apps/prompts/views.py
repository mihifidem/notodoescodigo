import json
import re

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Avg, Count, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView

from .forms import PromptForm
from .models import (
    CategoriaPrompt,
    HashtagPrompt,
    Prompt,
    PromptFavorito,
    SubcategoriaPrompt,
    ValoracionPrompt,
)


# ---------------------------------------------------------------------------
# Helpers de visibilidad
# ---------------------------------------------------------------------------

def _es_premium(usuario):
    if not usuario.is_authenticated:
        return False
    return usuario.is_staff or getattr(usuario, 'role', None) == 'premium'


def _es_registrado_valido(usuario):
    if not usuario.is_authenticated:
        return False
    if usuario.is_staff:
        return True
    return getattr(usuario, 'role', '') in {'alumno', 'basic', 'freemium', 'premium'}


def _puede_ver_prompt(usuario, prompt):
    if prompt.visibilidad == Prompt.VISIBILIDAD_PUBLICA:
        return True
    if prompt.visibilidad == Prompt.VISIBILIDAD_SEMIPUBLICA:
        return _es_registrado_valido(usuario)
    if prompt.visibilidad == Prompt.VISIBILIDAD_PRIVADA:
        return _es_premium(usuario)
    return False


def _prompts_visibles(usuario):
    qs = Prompt.objects.filter(
        publicado=True,
        publicado_en__lte=timezone.now(),
    )
    if _es_premium(usuario):
        return qs
    if _es_registrado_valido(usuario):
        return qs.filter(visibilidad__in=[Prompt.VISIBILIDAD_PUBLICA, Prompt.VISIBILIDAD_SEMIPUBLICA])
    return qs.filter(visibilidad=Prompt.VISIBILIDAD_PUBLICA)


# ---------------------------------------------------------------------------
# Listado
# ---------------------------------------------------------------------------

class PromptListView(ListView):
    template_name = 'prompts/list.html'
    context_object_name = 'prompts'
    paginate_by = 12

    def get_queryset(self):
        qs = (
            _prompts_visibles(self.request.user)
            .select_related('categoria', 'subcategoria')
            .prefetch_related('hashtags')
            .annotate(
                promedio_rating=Avg('valoraciones__valor'),
                total_valoraciones=Count('valoraciones', distinct=True),
                total_favoritos=Count('favoritos', distinct=True),
            )
            .order_by('-destacado', '-publicado_en')
        )

        q = (self.request.GET.get('q') or '').strip()
        categoria = (self.request.GET.get('categoria') or '').strip()
        subcategoria = (self.request.GET.get('subcategoria') or '').strip()
        hashtag = (self.request.GET.get('hashtag') or '').strip()
        solo_con_vars = self.request.GET.get('con_variables') == '1'
        solo_favoritos = self.request.GET.get('solo_favoritos') == '1'

        if q:
            qs = qs.filter(
                Q(titulo__icontains=q)
                | Q(descripcion__icontains=q)
                | Q(contenido__icontains=q)
            )
        if categoria:
            qs = qs.filter(categoria__slug=categoria)
        if subcategoria:
            qs = qs.filter(subcategoria__slug=subcategoria)
        if hashtag:
            qs = qs.filter(hashtags__slug=hashtag)
        if solo_con_vars:
            qs = qs.filter(contenido__regex=r'\{[^}]+\}')
        if solo_favoritos and self.request.user.is_authenticated:
            qs = qs.filter(favoritos__usuario=self.request.user)

        return qs.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['q_actual'] = (self.request.GET.get('q') or '').strip()
        context['categoria_actual'] = (self.request.GET.get('categoria') or '').strip()
        context['subcategoria_actual'] = (self.request.GET.get('subcategoria') or '').strip()
        context['hashtag_actual'] = (self.request.GET.get('hashtag') or '').strip()
        context['con_variables_activo'] = self.request.GET.get('con_variables') == '1'
        context['solo_favoritos_activo'] = self.request.GET.get('solo_favoritos') == '1'

        visibles = _prompts_visibles(self.request.user)

        context['categorias'] = (
            CategoriaPrompt.objects.filter(activa=True)
            .annotate(total=Count('prompts', filter=Q(prompts__in=visibles)))
            .filter(total__gt=0)
            .order_by('nombre')
        )
        context['subcategorias'] = (
            SubcategoriaPrompt.objects.filter(activa=True)
            .annotate(total=Count('prompts', filter=Q(prompts__in=visibles)))
            .filter(total__gt=0)
            .order_by('nombre')
        )
        context['hashtags'] = HashtagPrompt.objects.all().order_by('nombre')

        subcategorias_con_prompts = Prefetch(
            'subcategorias',
            queryset=(
                SubcategoriaPrompt.objects.filter(activa=True)
                .annotate(total=Count('prompts', filter=Q(prompts__in=visibles)))
                .filter(total__gt=0)
                .order_by('nombre')
            ),
            to_attr='subcategorias_con_prompts',
        )

        context['grupos_categorias'] = (
            CategoriaPrompt.objects.filter(activa=True)
            .annotate(total=Count('prompts', filter=Q(prompts__in=visibles)))
            .filter(total__gt=0)
            .prefetch_related(subcategorias_con_prompts)
            .order_by('nombre')
        )
        context['grupos_hashtags'] = (
            HashtagPrompt.objects
            .annotate(total=Count('prompts', filter=Q(prompts__in=visibles)))
            .filter(total__gt=0)
            .order_by('-total', 'nombre')[:20]
        )

        if self.request.user.is_authenticated:
            context['favoritos_ids'] = set(
                PromptFavorito.objects.filter(usuario=self.request.user)
                .values_list('prompt_id', flat=True)
            )
            context['total_favoritos'] = len(context['favoritos_ids'])
        else:
            context['favoritos_ids'] = set()
            context['total_favoritos'] = 0

        return context


# ---------------------------------------------------------------------------
# Detalle
# ---------------------------------------------------------------------------

class PromptDetailView(DetailView):
    template_name = 'prompts/detail.html'
    context_object_name = 'prompt'

    def get_queryset(self):
        return (
            _prompts_visibles(self.request.user)
            .select_related('categoria', 'subcategoria')
            .prefetch_related('hashtags')
            .annotate(
                promedio_rating=Avg('valoraciones__valor'),
                total_valoraciones=Count('valoraciones', distinct=True),
                total_favoritos=Count('favoritos', distinct=True),
            )
        )

    def get_object(self, queryset=None):
        return get_object_or_404(self.get_queryset(), slug=self.kwargs['slug'])

    def dispatch(self, request, *args, **kwargs):
        prompt = self.get_object()
        if not _puede_ver_prompt(request.user, prompt):
            if request.user.is_authenticated:
                messages.error(request, 'No tienes permisos para ver este prompt.')
            else:
                messages.error(request, 'Debes iniciar sesion para ver este contenido.')
            return redirect('prompts:list')
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        """Valora el prompt con duckies."""
        if not request.user.is_authenticated:
            messages.error(request, 'Debes iniciar sesion para valorar prompts.')
            return redirect('account_login')

        prompt = self.get_object()
        try:
            valor = int(request.POST.get('valor', 0))
        except ValueError:
            valor = 0

        if valor < 1 or valor > 5:
            messages.error(request, 'La valoracion debe estar entre 1 y 5 duckies.')
            return redirect('prompts:detail', slug=prompt.slug)

        ValoracionPrompt.objects.update_or_create(
            usuario=request.user,
            prompt=prompt,
            defaults={'valor': valor},
        )
        messages.success(request, 'Tu valoracion se guardo correctamente.')
        return redirect('prompts:detail', slug=prompt.slug)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        prompt = context['prompt']
        usuario = self.request.user

        if usuario.is_authenticated:
            val = ValoracionPrompt.objects.filter(usuario=usuario, prompt=prompt).first()
            context['valoracion_usuario'] = val.valor if val else 0
            context['es_favorito'] = PromptFavorito.objects.filter(
                usuario=usuario, prompt=prompt
            ).exists()
        else:
            context['valoracion_usuario'] = 0
            context['es_favorito'] = False

        definiciones = prompt.variables_definidas
        nombres = list(dict.fromkeys(prompt.nombres_variables + list(definiciones.keys())))

        variables = []
        for nombre in nombres:
            defn = definiciones.get(nombre, {})
            tipo = str(defn.get('type') or defn.get('tipo') or 'text').lower()
            aliases = {
                'texto': 'text',
                'string': 'text',
                'textarea': 'textarea',
                'text-area': 'textarea',
                'opcion': 'select',
                'opciones': 'select',
                'choice': 'select',
                'select': 'select',
                'imagen': 'image',
            }
            tipo = aliases.get(tipo, tipo)
            if tipo not in {'text', 'textarea', 'select', 'image'}:
                tipo = 'text'

            opciones = defn.get('options') or defn.get('opciones') or []
            valor_defecto = defn.get('default')
            if valor_defecto is None and 'valor_defecto' in defn:
                valor_defecto = defn.get('valor_defecto')
            if valor_defecto is None and 'value' in defn:
                valor_defecto = defn.get('value')
            if valor_defecto is None:
                valor_defecto = ''

            variables.append({
                'nombre': nombre,
                'descripcion': defn.get('label') or defn.get('descripcion') or nombre.replace('_', ' ').title(),
                'valor_defecto': valor_defecto,
                'tipo': tipo,
                'opciones': opciones,
            })

        context['variables'] = variables
        context['variables_json'] = json.dumps(variables, ensure_ascii=False)
        context['tiene_variables'] = bool(variables)
        return context


# ---------------------------------------------------------------------------
# Mis favoritos
# ---------------------------------------------------------------------------

class AdminPromptRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_staff


class PromptCreateView(AdminPromptRequiredMixin, CreateView):
    model = Prompt
    form_class = PromptForm
    template_name = 'prompts/create.html'
    success_url = '/prompts/'

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Prompt creado correctamente.')
        return response


class MisFavoritosView(LoginRequiredMixin, ListView):
    template_name = 'prompts/favoritos.html'
    context_object_name = 'favoritos'
    paginate_by = 12

    def get_queryset(self):
        return (
            PromptFavorito.objects.filter(usuario=self.request.user)
            .select_related('prompt__categoria', 'prompt__subcategoria')
            .prefetch_related('prompt__hashtags')
            .annotate(
                promedio_rating=Avg('prompt__valoraciones__valor'),
            )
            .order_by('-agregado_en')
        )


# ---------------------------------------------------------------------------
# Toggle favorito (AJAX)
# ---------------------------------------------------------------------------

class ToggleFavoritoView(LoginRequiredMixin, View):
    def post(self, request, slug):
        prompt = get_object_or_404(_prompts_visibles(request.user), slug=slug)
        fav, created = PromptFavorito.objects.get_or_create(
            usuario=request.user,
            prompt=prompt,
        )
        if not created:
            fav.delete()
            return JsonResponse({'ok': True, 'favorito': False})
        return JsonResponse({'ok': True, 'favorito': True})
