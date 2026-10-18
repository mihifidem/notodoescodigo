"""Views for the users app — both HTML and API ViewSets."""
import base64
import csv
import io
import mimetypes
import os
import re
import uuid
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile, File
from django.db import OperationalError
from django.db.models import Avg, Count, Q
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.views.generic import (
    CreateView, DetailView, UpdateView, TemplateView, View, ListView
)

from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import User, Notificacion
from .notifications import platform_notifications, set_platform_read
from .forms import RegistroForm, PerfilForm
from .serializers import UserSerializer, NotificacionSerializer


DUCKY_AVATAR_PROMPT = """Genera un avatar personalizado de \"Ducky\", la mascota de noNoTodoEsCódigo.com.

REFERENCIAS:
- Imagen A: fotografía del usuario. Utilízala únicamente para identificar y trasladar rasgos físicos visibles.
- Imagen B: imagen oficial de Ducky. Esta imagen es la referencia PRINCIPAL y OBLIGATORIA para el estilo visual, diseño, composición, proporciones, colores y personalidad del personaje.

OBJETIVO
Crear una versión personalizada de Ducky que mantenga exactamente la misma identidad visual que la mascota original, pero incorporando algunos rasgos físicos reconocibles del usuario.

El resultado debe parecer un miembro de una colección oficial de avatares de noNoTodoEsCódigo.com.

Ducky debe seguir siendo claramente un pato ilustrado.
NO crear un retrato humano.
NO transformar literalmente la cara humana en un pato.
NO alterar la identidad visual esencial de Ducky.

ESTILO VISUAL
Seguir fielmente la Imagen B.
Mantener:
- ilustración cartoon/vectorial limpia
- formas simples y redondeadas
- contornos gruesos y oscuros
- colores sólidos y vivos
- sombreado mínimo y suave
- apariencia moderna de mascota tecnológica
- composición frontal y simétrica
- diseño amigable y reconocible incluso a tamaño pequeño

Evitar:
- realismo
- estilo fotográfico
- render 3D
- aspecto Pixar
- textura realista de plumas
- exceso de detalle
- anatomía humana
- caricatura exagerada

DUCKY
Conservar siempre:
- cuerpo amarillo de pato
- cabeza redondeada
- pico naranja
- ojos grandes y expresivos
- expresión simpática
- gafas cuando formen parte del diseño base
- cuerpo compacto
- contorno oscuro característico
- portátil delante del personaje
- símbolo de programación </> visible y centrado en el portátil

El portátil y el símbolo </> forman parte de la identidad visual del avatar y deben mantenerse en todas las generaciones.

PERSONALIZACIÓN DEL USUARIO
Analiza únicamente los rasgos físicos claramente visibles en la Imagen A.
Traslada de forma simplificada al personaje:
CABELLO
- color
- longitud
- volumen
- textura
- peinado
- raya
- flequillo
- rizos u ondas
- entradas o ausencia de cabello cuando sean evidentes

El cabello debe interpretarse utilizando formas gráficas simples compatibles con el estilo de Ducky.

OJOS
- adaptar el color de ojos cuando sea claramente visible
- mantener siempre el diseño grande, simple y expresivo de los ojos de Ducky

GAFAS
Si el usuario lleva gafas:
- reproducir aproximadamente la forma
- grosor
- color
- estilo general

Simplificarlas para mantener el estilo cartoon de Ducky.

Si el usuario no lleva gafas, no añadir gafas salvo que sean una característica imprescindible e invariable del diseño oficial de Ducky.

CEJAS Y VELLO FACIAL
Si son claramente visibles y ayudan a reconocer al usuario:
- incorporar cejas
- barba
- bigote
- perilla

Representarlos mediante formas muy simples.
No crear pelo facial realista.

TONALIDAD DE PIEL
Utilizar la tonalidad visible de la piel únicamente como referencia secundaria para pequeñas zonas antropomórficas si el diseño lo permite.
No sustituir el color amarillo característico de Ducky.
Ducky debe continuar siendo principalmente amarillo.

RASGOS FACIALES
Se pueden trasladar de forma muy sutil:
- forma general de la mirada
- expresión
- cejas
- sonrisa
- algunos rasgos visuales distintivos

Nunca reproducir una cara humana sobre el pato.

REGLA DE PRIORIDAD
Cuando exista conflicto entre parecerse al usuario y mantener la identidad de Ducky, priorizar SIEMPRE la identidad visual de Ducky.

Orden de prioridad:
1. Identidad visual de Ducky.
2. Estilo gráfico de la Imagen B.
3. Silueta, proporciones y composición.
4. Cabello del usuario.
5. Gafas.
6. Ojos.
7. Cejas o vello facial.
8. Otros rasgos físicos secundarios.

COMPOSICIÓN
- formato cuadrado 1:1
- personaje centrado
- vista frontal
- cabeza completamente visible
- parte superior del cuerpo visible
- portátil en primer plano
- símbolo </> centrado en el portátil
- fondo blanco, gris muy claro o fondo simple idéntico al estilo de referencia
- suficiente margen alrededor del personaje para permitir un recorte circular
- sin elementos innecesarios alrededor

CONSISTENCIA ENTRE USUARIOS
Todas las imágenes generadas deben parecer variantes del MISMO personaje.

Entre usuarios solo deberían cambiar principalmente:
- cabello
- ojos
- gafas
- cejas
- barba o bigote
- pequeños rasgos visuales distintivos

No cambiar entre generaciones:
- anatomía de Ducky
- color amarillo principal
- forma del pico
- proporciones generales
- estilo gráfico
- portátil
- símbolo </>
- grosor de los contornos
- composición general
- lenguaje visual

RESTRICCIONES
No inferir ni representar:
- etnia
- nacionalidad
- religión
- profesión
- personalidad
- orientación sexual
- ideología
- edad exacta
- género

salvo que exista algún elemento visual estrictamente necesario para reproducir el aspecto visible del usuario.

No exagerar rasgos físicos.
No generar caricaturas ofensivas.
No añadir accesorios que no aparezcan en la foto.
No añadir textos, nombres, logotipos ni marcas.

RESULTADO FINAL
Un avatar limpio, simpático y reconocible de Ducky personalizado con los rasgos visuales principales del usuario, manteniendo una identidad gráfica completamente consistente con el resto de avatares de noNoTodoEsCódigo.com.

Debe dar la sensación de:
“Este es Ducky, pero personalizado para este usuario”.

No:
“Esta persona ha sido convertida literalmente en un pato”.

Usuario: {nombre_usuario}
Nombre para la referencia visual: {nombre_usuario}
"""


def admin_csv_manager(request):
    if not request.user.is_authenticated or not request.user.is_staff:
        raise PermissionDenied

    model_path = request.GET.get('model') or request.POST.get('model')
    if not model_path:
        return HttpResponse('Falta el parámetro model.', status=400)

    try:
        app_label, model_name = model_path.split('.', 1)
        model_class = apps.get_model(app_label, model_name)
    except Exception:
        return HttpResponse('Modelo no válido.', status=400)

    action = (request.GET.get('action') or request.POST.get('action') or 'export').lower()

    if request.method == 'POST' and action == 'import':
        csv_file = request.FILES.get('csv_file')
        if not csv_file:
            return HttpResponse('Falta el archivo CSV.', status=400)

        text = csv_file.read().decode('utf-8-sig', errors='replace')
        text = re.sub(r'[\x00-\x1f\x7f]+', '', text)
        rows = list(csv.DictReader(io.StringIO(text)))
        model_class.objects.all().delete()

        created = 0
        normalized_fields = [field for field in (rows[0].keys() if rows else [])]
        for row in rows:
            if not row:
                continue
            clean_row = {}
            for field_name, field_value in row.items():
                if not field_name:
                    continue
                value = '' if field_value is None else str(field_value)
                value = re.sub(r'[\x00-\x1f\x7f]+', '', value).strip()
                if field_name == 'horario' and value.lower().replace('ñ', 'n') == 'mana':
                    value = 'Mañana'
                clean_row[field_name] = value
            if not any(clean_row.values()):
                continue
            model_class.objects.create(**clean_row)
            created += 1

        return JsonResponse({'ok': True, 'count': created, 'model': model_path})

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{model_name}.csv"'

    fields = [field.name for field in model_class._meta.fields]
    writer = csv.writer(response)
    writer.writerow(fields)

    for instance in model_class.objects.all():
        row = []
        for field_name in fields:
            value = getattr(instance, field_name)
            if callable(value):
                value = value()
            row.append('' if value is None else value)
        writer.writerow(row)

    return response


def _generar_avatar_ducky_temporal(bytes_imagen: bytes) -> str:
    media_dir = Path(settings.MEDIA_ROOT) / 'avatars' / 'ducky_ai'
    media_dir.mkdir(parents=True, exist_ok=True)
    filename = f'ducky_{uuid.uuid4().hex}.png'
    temp_path = media_dir / filename
    temp_path.write_bytes(bytes_imagen)
    return str(temp_path)


def _limpiar_avatar_ducky_pendiente(request):
    temp_path = request.session.get('pending_ducky_avatar')
    if temp_path and os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except OSError:
            pass

    request.session.pop('pending_ducky_avatar', None)
    request.session.pop('pending_ducky_avatar_nonce', None)
    request.session.modified = True


def generar_avatar_ducky_con_ia(usuario, archivo=None):
    """Genera una imagen de avatar Ducky basada en la foto del usuario."""
    if not getattr(settings, 'OPENAI_API_KEY', ''):
        raise RuntimeError('La IA no está configurada. Añade OPENAI_API_KEY al archivo .env.')

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError('La librería openai no está instalada.') from exc

    nombre_usuario = usuario.get_full_name() or usuario.username
    prompt = DUCKY_AVATAR_PROMPT.format(nombre_usuario=nombre_usuario)

    try:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        request_kwargs = {
            'model': 'gpt-image-1',
            'prompt': prompt,
            'size': '1024x1024',
        }

        if archivo is not None:
            if hasattr(archivo, 'read'):
                archivo_bytes = archivo.read()
                archivo_name = getattr(archivo, 'name', 'avatar') or 'avatar'
                archivo_content_type = getattr(archivo, 'content_type', None) or mimetypes.guess_type(archivo_name)[0] or 'image/jpeg'
                if hasattr(archivo, 'seek'):
                    archivo.seek(0)
            else:
                archivo_bytes = archivo
                archivo_name = 'avatar'
                archivo_content_type = 'image/jpeg'

            request_kwargs['image'] = (archivo_name, archivo_bytes, archivo_content_type)
            response = client.images.edit(**request_kwargs)
        else:
            response = client.images.generate(**request_kwargs)

        image_record = response.data[0]
        if getattr(image_record, 'b64_json', None):
            return base64.b64decode(image_record.b64_json)

        image_url = getattr(image_record, 'url', None)
        if not image_url:
            raise RuntimeError('La respuesta de la IA no devolvió una imagen válida.')

        import urllib.request
        with urllib.request.urlopen(image_url) as resp:  # noqa: S310
            return resp.read()
    except Exception as exc:  # pragma: no cover - depends on external API
        raise RuntimeError(f'No se pudo generar el avatar con IA: {exc}') from exc


@login_required
@require_http_methods(['POST'])
def _limpiar_avatar_ducky_pendiente(request):
    temp_path = request.session.get('pending_ducky_avatar')
    if temp_path and os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except OSError:
            pass
    request.session.pop('pending_ducky_avatar', None)


@login_required
@require_http_methods(['POST'])
def generar_avatar_ducky(request):
    archivo = request.FILES.get('avatar')
    if not archivo:
        return JsonResponse({'ok': False, 'error': 'Debes subir una foto para generar el avatar Ducky.'}, status=400)

    _limpiar_avatar_ducky_pendiente(request)

    try:
        imagen_bytes = generar_avatar_ducky_con_ia(request.user, archivo)
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)

    temp_path = _generar_avatar_ducky_temporal(imagen_bytes)
    request.session['pending_ducky_avatar'] = temp_path
    request.session['pending_ducky_avatar_nonce'] = uuid.uuid4().hex
    request.session.modified = True

    encoded = base64.b64encode(imagen_bytes).decode('utf-8')
    return JsonResponse({
        'ok': True,
        'preview_url': f'data:image/png;base64,{encoded}',
        'preview_nonce': request.session['pending_ducky_avatar_nonce'],
    })


@login_required
@require_http_methods(['POST'])
def confirmar_avatar_ducky(request):
    temp_path = request.session.get('pending_ducky_avatar')
    if not temp_path or not os.path.exists(temp_path):
        return JsonResponse({'ok': False, 'error': 'No hay un avatar generado pendiente de confirmación.'}, status=400)

    try:
        with open(temp_path, 'rb') as generated_file:
            filename = f'avatars/ducky_{request.user.pk}_{uuid.uuid4().hex}.png'
            request.user.avatar.save(filename, File(generated_file), save=False)
            request.user.save(update_fields=['avatar'])
        os.remove(temp_path)
        request.session.pop('pending_ducky_avatar', None)
        return JsonResponse({'ok': True, 'avatar_url': request.user.avatar.url})
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)


class LandingView(TemplateView):
    """Public landing page with blog highlights."""
    template_name = 'landing.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.blog.models import PostBlog

        context['posts_destacados'] = (
            PostBlog.objects.filter(
                publicado=True,
                visibilidad=PostBlog.VISIBILIDAD_PUBLICA,
            )
            .exclude(categoria__nombre='News Lite')
            .select_related('categoria', 'subcategoria')
            .prefetch_related('hashtags')
            .annotate(promedio_rating=Avg('valoraciones__valor'), total_valoraciones=Count('valoraciones'))
            .order_by('-destacado', '-publicado_en')[:3]
        )

        news_lite_qs = (
            PostBlog.objects.filter(
                publicado=True,
                publicado_en__gte=timezone.now() - timedelta(days=7),
                visibilidad=PostBlog.VISIBILIDAD_PUBLICA,
                categoria__nombre='News Lite',
            )
            .select_related('categoria', 'subcategoria')
            .prefetch_related('hashtags')
            .annotate(promedio_rating=Avg('valoraciones__valor'), total_valoraciones=Count('valoraciones'))
            .order_by('-destacado', '-publicado_en')
        )
        context['news_lite_posts'] = list(news_lite_qs[:12])
        context['news_lite_categoria_slug'] = 'news-lite'

        from apps.prompts.models import Prompt
        context['prompts_recientes'] = list(
            Prompt.objects.filter(
                publicado=True,
                visibilidad=Prompt.VISIBILIDAD_PUBLICA,
            )
            .select_related('categoria', 'subcategoria')
            .prefetch_related('hashtags')
            .order_by('-publicado_en')[:3]
        )

        from apps.courses.models import Curso
        if self.request.user.is_authenticated:
            try:
                context['cursos_disponibles'] = (
                    self.request.user.get_cursos_disponibles()
                    .select_related('tipo_curso')
                    .order_by('-destacado', '-creado_en')[:3]
                )
            except OperationalError:
                context['cursos_disponibles'] = []
        else:
            context['cursos_disponibles'] = []

        from apps.enlaces.models import EnlaceImportante
        context['ultimos_enlaces'] = (
            EnlaceImportante.objects.filter(activo=True)
            .select_related('creado_por')
            .order_by('-creado_en')[:3]
        )
        return context


# ---------------------------------------------------------------------------
# HTML Views
# ---------------------------------------------------------------------------

class RegistroView(CreateView):
    """User registration page."""
    form_class = RegistroForm
    template_name = 'users/register.html'
    success_url = reverse_lazy('users:dashboard')

    def form_valid(self, form):
        user = form.save()
        login(self.request, user, backend='django.contrib.auth.backends.ModelBackend')
        messages.success(self.request, f'¡Bienvenido/a {user.first_name}! Tu cuenta ha sido creada.')
        return redirect(self.success_url)

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('users:dashboard')
        return super().dispatch(request, *args, **kwargs)


class DashboardView(LoginRequiredMixin, TemplateView):
    """Main user dashboard."""
    template_name = 'dashboard/index.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        from apps.analytics.models import RegistroActividad
        from apps.bug_reports.models import BugReport
        from apps.blog.models import LecturaPostUsuario
        from apps.courses.models import InscripcionCurso, Progreso
        from apps.ducky_arenas.models import UserProfileArenas
        from apps.gamification.models import InsigniaUsuario, LogroUsuario, MisionUsuario
        from apps.nexus.models import DNAProfile
        from apps.rewards.models import CanjeRecompensa

        inscripciones = InscripcionCurso.objects.filter(
            usuario=user
        ).select_related('curso').order_by('-fecha_inscripcion')[:4]

        cursos_en_marcha = []
        total_temas_completados = Progreso.objects.filter(usuario=user, completado=True).count()
        for inscripcion in inscripciones:
            total_temas = inscripcion.curso.temas.count()
            temas_completados = Progreso.objects.filter(
                usuario=user,
                tema__curso=inscripcion.curso,
                completado=True,
            ).count()
            cursos_en_marcha.append({
                'curso': inscripcion.curso,
                'total_temas': total_temas,
                'temas_completados': temas_completados,
                'porcentaje': int((temas_completados / total_temas) * 100) if total_temas else 0,
            })

        insignias_recientes = InsigniaUsuario.objects.filter(
            usuario=user
        ).select_related('insignia').order_by('-fecha_obtenida')[:6]

        misiones_activas = MisionUsuario.objects.filter(
            usuario=user, completada=False
        ).select_related('mision').order_by('-fecha_asignada')[:3]

        logros_recientes = LogroUsuario.objects.filter(
            usuario=user
        ).select_related('logro').order_by('-fecha_obtenido')[:6]

        canjes_recientes = CanjeRecompensa.objects.filter(
            usuario=user
        ).select_related('recompensa').order_by('-fecha_canje')[:4]

        lecturas_posts = LecturaPostUsuario.objects.filter(
            usuario=user
        ).select_related('post').order_by('-iniciada_en')[:8]

        total_posts_vistos = LecturaPostUsuario.objects.filter(usuario=user).count()
        total_posts_con_puntos = LecturaPostUsuario.objects.filter(
            usuario=user,
            puntos_otorgados=True,
        ).count()

        actividades = RegistroActividad.objects.filter(usuario=user).order_by('-timestamp')[:5]

        mis_bugs_total = BugReport.objects.filter(alumno=user).count()
        mis_bugs_validados = BugReport.objects.filter(
            alumno=user,
            estado=BugReport.ESTADO_VALIDADO,
        ).count()

        perfil_ducky = getattr(user, 'user_profile_arenas', None)
        perfil_dna = getattr(user, 'dna_profile', None)

        progreso_overall = 0
        if total_temas_completados or user.puntos_totales:
            total_temas_disponibles = Progreso.objects.filter(usuario=user).values_list('tema_id', flat=True)
            total_temas_total = len(set(total_temas_disponibles))
            if total_temas_total:
                progreso_overall = int((total_temas_completados / total_temas_total) * 100)
            else:
                progreso_overall = min(100, int((user.puntos_totales / max(user.puntos_totales or 1, 300)) * 100))

        next_mission = misiones_activas[0] if misiones_activas else None
        next_course = cursos_en_marcha[0] if cursos_en_marcha else None

        ranking_qs = User.objects.filter(is_active=True, is_staff=False).order_by('-puntos_totales', 'username')
        ranking_posicion = None
        for idx, ranked_user in enumerate(ranking_qs, start=1):
            if ranked_user.pk == user.pk:
                ranking_posicion = idx
                break

        summary_cards = [
            {
                'title': 'Ranking',
                'value': f'#{ranking_posicion}' if ranking_posicion else 'Sin ranking',
                'meta': f'{user.puntos_totales} pts acumulados',
                'icon': 'bi-trophy',
                'tone': 'primary',
                'href': reverse('gamification:ranking'),
            },
            {
                'title': 'Ducky Coins',
                'value': perfil_ducky.duckCoins if perfil_ducky else 0,
                'meta': 'Arena + recompensas',
                'icon': 'bi-coin',
                'tone': 'warning',
                'href': reverse('ducky_arenas:rewards'),
            },
            {
                'title': 'Insignias',
                'value': insignias_recientes.count(),
                'meta': 'Logros de tu camino',
                'icon': 'bi-award',
                'tone': 'success',
                'href': reverse('gamification:insignias'),
            },
            {
                'title': 'Puntos',
                'value': user.puntos_totales,
                'meta': 'Total acumulado',
                'icon': 'bi-star-fill',
                'tone': 'secondary',
                'href': reverse('gamification:ranking'),
            },
        ]

        if next_mission:
            next_action = {
                'title': next_mission.mision.titulo,
                'description': next_mission.mision.descripcion,
                'href': reverse('gamification:misiones'),
                'badge': f'{next_mission.progreso}/{next_mission.mision.requisito_cantidad}',
            }
        elif next_course:
            next_action = {
                'title': next_course['curso'].nombre,
                'description': 'Sigue avanzando en tu curso actual para mantener el ritmo.',
                'href': reverse('courses:detail', args=[next_course['curso'].pk]),
                'badge': f'{next_course["porcentaje"]}% completado',
            }
        else:
            next_action = {
                'title': 'Explora cursos nuevos',
                'description': 'Encuentra nuevos contenidos para continuar tu aprendizaje y ganar puntos.',
                'href': reverse('courses:list'),
                'badge': 'Nuevo reto',
            }

        context.update({
            'inscripciones': inscripciones,
            'cursos_en_marcha': cursos_en_marcha,
            'insignias_recientes': insignias_recientes,
            'logros_recientes': logros_recientes,
            'canjes_recientes': canjes_recientes,
            'misiones_activas': misiones_activas,
            'actividades': actividades,
            'lecturas_posts': lecturas_posts,
            'total_posts_vistos': total_posts_vistos,
            'total_posts_con_puntos': total_posts_con_puntos,
            'mis_bugs_total': mis_bugs_total,
            'mis_bugs_validados': mis_bugs_validados,
            'ducky_summary_cards': summary_cards,
            'proxima_accion': next_action,
            'ducky_profile': perfil_ducky,
            'dna_profile': perfil_dna,
            'dna_profile_url': reverse('nexus:student_my_dna') if perfil_dna else reverse('nexus:student_list'),
            'results_url': reverse('nexus:student_list'),
        })
        return context


class PerfilView(LoginRequiredMixin, DetailView):
    """Public profile page."""
    model = User
    template_name = 'users/profile.html'
    context_object_name = 'perfil'

    def get_object(self, queryset=None):
        from django.core.exceptions import PermissionDenied
        from django.shortcuts import get_object_or_404
        username = self.kwargs.get('username', self.request.user.username)
        user = get_object_or_404(User, username=username)
        # Solo el dueño del perfil o admin/staff puede acceder
        if self.request.user != user and not (self.request.user.is_staff or self.request.user.is_superuser):
            raise PermissionDenied("No tienes permiso para ver este perfil.")
        return user

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        perfil = self.get_object()
        from apps.gamification.utils import get_insignias_activas_ids, get_insignias_visibles_para_usuario
        from apps.certifications.models import Certificado

        insignias_list = list(get_insignias_visibles_para_usuario(perfil))
        activas_ids = get_insignias_activas_ids(perfil)
        context['insignias_con_estado'] = [(insignia, insignia.id in activas_ids) for insignia in insignias_list]
        context['total_insignias'] = len(insignias_list)
        context['total_insignias_activas'] = sum(1 for insignia in insignias_list if insignia.id in activas_ids)
        context['certificados'] = Certificado.objects.filter(usuario=perfil)
        return context


class BorrarCuentaView(LoginRequiredMixin, View):
    """Soft delete the current user's account and log them out."""
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        user = request.user
        user.is_active = False
        user.save(update_fields=['is_active'])
        logout(request)
        messages.warning(request, 'Tu cuenta ha sido desactivada correctamente.')
        return redirect('users:landing')


class EditarPerfilView(LoginRequiredMixin, UpdateView):
    """Edit own profile."""
    model = User
    form_class = PerfilForm
    template_name = 'users/edit_profile.html'

    def get_object(self, queryset=None):
        return self.request.user

    def get_success_url(self):
        return reverse_lazy('users:perfil', kwargs={'username': self.request.user.username})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        # Indicar si el aula está protegida (alumno con aula ya asignada)
        context['aula_protegida'] = (
            user.role == 'alumno' and
            user.aula is not None and
            not user.is_staff
        )

        return context

    def form_valid(self, form):
        messages.success(self.request, 'Perfil actualizado correctamente.')
        return super().form_valid(form)


class NotificacionesView(LoginRequiredMixin, ListView):
    """Notification centre."""
    model = Notificacion
    template_name = 'users/notificaciones.html'
    context_object_name = 'notificaciones'
    paginate_by = 20

    def get(self, request, *args, **kwargs):
        state = request.GET.get('estado', '')
        course = request.GET.get('curso', '')
        if state not in ('', 'pendientes', 'leidas') or (course and not course.isdecimal()):
            return HttpResponseBadRequest('Filtro de notificaciones no válido.')
        if len(request.GET.get('buscar', '')) > 200:
            return HttpResponseBadRequest('La búsqueda no puede superar 200 caracteres.')
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        self.all_notifications = platform_notifications(self.request.user)
        queryset = self.all_notifications
        state = self.request.GET.get('estado', '')
        if state:
            queryset = queryset.filter(leida=state == 'leidas')
        course = self.request.GET.get('curso', '')
        if course:
            queryset = queryset.filter(lms_notification__course_id=int(course))
        search = self.request.GET.get('buscar', '').strip()
        if search:
            queryset = queryset.filter(Q(titulo__icontains=search) | Q(mensaje__icontains=search))
        return queryset.order_by('-creada_en', '-pk')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.lms.notification_state import notification_area, notification_target
        from apps.lms.ui import COURSE_AREAS
        areas = {key: (label, icon) for key, label, icon in COURSE_AREAS}
        icons = {
            'logro': 'bi-trophy', 'insignia': 'bi-award', 'mision': 'bi-lightning-charge',
            'recompensa': 'bi-gift', 'certificado': 'bi-patch-check',
            'sistema': 'bi-bell', 'recordatorio': 'bi-clock',
        }
        for notice in context['notificaciones']:
            notice.notification_day = timezone.localdate(notice.creada_en)
            notice.area_label = notice.get_tipo_display()
            notice.icon = icons.get(notice.tipo, 'bi-bell')
            if notice.lms_notification_id:
                notice.target_url = notification_target(notice.lms_notification)
                notice.area = notification_area(notice.lms_notification)
                notice.area_label, notice.icon = areas.get(notice.area, (notice.area_label, notice.icon))
        stats = self.all_notifications.aggregate(
            total=Count('pk'), pending=Count('pk', filter=Q(leida=False)),
        )
        context.update({
            'notification_stats': {**stats, 'read': stats['total'] - stats['pending']},
            'notification_courses': self.all_notifications.exclude(lms_notification__course__isnull=True)
                .values('lms_notification__course_id', 'lms_notification__course__nombre')
                .order_by('lms_notification__course__nombre').distinct(),
            'notification_state': self.request.GET.get('estado', ''),
            'notification_search': self.request.GET.get('buscar', '').strip(),
            'notification_course': self.request.GET.get('curso', ''),
            'notification_filter_query': urlencode({
                key: self.request.GET[key] for key in ('estado', 'buscar', 'curso') if self.request.GET.get(key)
            }),
            'notification_tabs_query': urlencode({
                key: self.request.GET[key] for key in ('buscar', 'curso') if self.request.GET.get(key)
            }),
            'today': timezone.localdate(), 'yesterday': timezone.localdate() - timedelta(days=1),
        })
        return context


class MarcarNotificacionLeidaView(LoginRequiredMixin, View):
    def post(self, request, pk=None):
        if pk:
            notif = platform_notifications(request.user).filter(pk=pk).first()
            if not notif:
                from django.http import Http404
                raise Http404
            value = request.POST.get('read')
            if value is not None and value not in ('0', '1'):
                return HttpResponseBadRequest('Estado de lectura no válido.')
            read = value == '1' if value is not None else not notif.leida
            set_platform_read(platform_notifications(request.user).filter(pk=pk), read=read)
        else:
            set_platform_read(platform_notifications(request.user).filter(leida=False), read=True)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'ok': True, 'read': read if pk else True,
                'count': platform_notifications(request.user).filter(leida=False).count(),
            })
        filters = urlencode({
            key: request.POST[key] for key in ('estado', 'buscar', 'curso') if request.POST.get(key)
        })
        destination = reverse('users:notificaciones')
        return redirect(f'{destination}?{filters}' if filters else destination)


# ---------------------------------------------------------------------------
# DRF ViewSets (used via apps/api/urls.py)
# ---------------------------------------------------------------------------

class UserViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return User.objects.filter(is_active=True)

    @action(detail=False, methods=['get'])
    def me(self, request):
        return Response(UserSerializer(request.user).data)


class NotificacionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificacionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return platform_notifications(self.request.user)

    @action(detail=True, methods=['post'])
    def marcar_leida(self, request, pk=None):
        notif = self.get_object()
        set_platform_read(self.get_queryset().filter(pk=notif.pk), read=True)
        return Response({'ok': True})
