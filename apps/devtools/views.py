import base64
import hashlib
import json
import re
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import redirect
from django.views.generic import TemplateView

from apps.users.access import can
from apps.users.models import AccessRoleAssignment

from .models import DevToolConfig, DevToolVote


def _hex_to_rgb(value):
    value = value.strip().lstrip('#')
    if len(value) == 3:
        value = ''.join(ch * 2 for ch in value)
    if len(value) != 6:
        raise ValueError('Color hexadecimal inválido')
    return tuple(int(value[i:i+2], 16) for i in range(0, 6, 2))


def _rgb_to_hex(rgb):
    return '#%02X%02X%02X' % rgb


def _rgb_to_hsl(r, g, b):
    r, g, b = r / 255, g / 255, b / 255
    cmax = max(r, g, b)
    cmin = min(r, g, b)
    delta = cmax - cmin
    if delta == 0:
        h = 0
    elif cmax == r:
        h = 60 * (((g - b) / delta) % 6)
    elif cmax == g:
        h = 60 * (((b - r) / delta) + 2)
    else:
        h = 60 * (((r - g) / delta) + 4)
    l = (cmax + cmin) / 2
    if delta == 0:
        s = 0
    else:
        s = delta / (1 - abs(2 * l - 1))
    return round(h), round(s * 100), round(l * 100)


def _relative_luminance(hex_color):
    r, g, b = _hex_to_rgb(hex_color)
    channels = []
    for value in (r, g, b):
        value = value / 255
        channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _get_contrast_ratio(bg, fg):
    bg_l = _relative_luminance(bg)
    fg_l = _relative_luminance(fg)
    lighter = max(bg_l, fg_l)
    darker = min(bg_l, fg_l)
    return (lighter + 0.05) / (darker + 0.05)


def _slugify(text):
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'\s+', '-', text.strip())
    text = re.sub(r'-+', '-', text)
    return text


def _get_devtool_config(slug):
    config, _ = DevToolConfig.objects.get_or_create(
        slug=slug,
        defaults={'name': slug.replace('-', ' ').title()},
    )
    return config


def _user_can_access_devtool(user, slug):
    config = _get_devtool_config(slug)
    if not config.is_active:
        return False

    if user and user.is_authenticated and (
        user.is_staff or can(user, 'manage_access').allowed
    ):
        return True

    if config.is_private:
        return (
            can(user, 'view_plan_content', required_plan='pro').allowed
            or can(user, 'access_role', AccessRoleAssignment.Role.ALUMNO).allowed
        )

    return True


def devtool_access_required(tool_slug):
    def decorator(view_obj):
        if hasattr(view_obj, 'dispatch'):
            original_dispatch = view_obj.dispatch

            @wraps(original_dispatch)
            def dispatch(self, request, *args, **kwargs):
                if not _user_can_access_devtool(request.user, tool_slug):
                    messages.warning(request, 'Esta miniapp está desactivada o restringida para tu perfil.')
                    return redirect('devtools:hub')
                return original_dispatch(self, request, *args, **kwargs)

            view_obj.dispatch = dispatch
            return view_obj

        @wraps(view_obj)
        def _wrapped(request, *args, **kwargs):
            if not _user_can_access_devtool(request.user, tool_slug):
                messages.warning(request, 'Esta miniapp está desactivada o restringida para tu perfil.')
                return redirect('devtools:hub')
            return view_obj(request, *args, **kwargs)

        return _wrapped

    return decorator


def _mix_hex_colors(color1, color2, weight=0.5):
    c1 = _hex_to_rgb(color1)
    c2 = _hex_to_rgb(color2)
    w = weight
    mixed = tuple(int(c1[i] * (1 - w) + c2[i] * w) for i in range(3))
    return _rgb_to_hex(mixed)


MEASURE_CATEGORIES = {
    'peso': {
        'label': 'Peso / cocina',
        'units': {
            'gramos': {'label': 'g', 'to_base': Decimal('1')},
            'kilogramos': {'label': 'kg', 'to_base': Decimal('1000')},
            'miligramos': {'label': 'mg', 'to_base': Decimal('0.001')},
            'onzas': {'label': 'oz', 'to_base': Decimal('28.3495')},
            'libras': {'label': 'lb', 'to_base': Decimal('453.592')},
            'cucharas_grandes': {'label': 'cucharadas grandes', 'to_base': Decimal('15')},
            'cucharaditas': {'label': 'cucharaditas', 'to_base': Decimal('5')},
            'tazas': {'label': 'tazas', 'to_base': Decimal('240')},
        },
    },
    'volumen': {
        'label': 'Volumen / líquidos',
        'units': {
            'mililitros': {'label': 'ml', 'to_base': Decimal('1')},
            'litros': {'label': 'l', 'to_base': Decimal('1000')},
            'cucharaditas': {'label': 'cucharaditas', 'to_base': Decimal('5')},
            'cucharas_grandes': {'label': 'cucharadas grandes', 'to_base': Decimal('15')},
            'tazas': {'label': 'tazas', 'to_base': Decimal('240')},
            'onzas_liquidas': {'label': 'fl oz', 'to_base': Decimal('29.5735')},
        },
    },
    'longitud': {
        'label': 'Longitud / diseño',
        'units': {
            'milimetros': {'label': 'mm', 'to_base': Decimal('1')},
            'centimetros': {'label': 'cm', 'to_base': Decimal('10')},
            'metros': {'label': 'm', 'to_base': Decimal('1000')},
            'pulgadas': {'label': 'in', 'to_base': Decimal('25.4')},
            'pixeles': {'label': 'px', 'to_base': Decimal('0.264583')},
            'rem': {'label': 'rem', 'to_base': Decimal('16')},
        },
    },
    'tiempo': {
        'label': 'Tiempo / rendimiento',
        'units': {
            'milisegundos': {'label': 'ms', 'to_base': Decimal('0.001')},
            'segundos': {'label': 's', 'to_base': Decimal('1')},
            'minutos': {'label': 'min', 'to_base': Decimal('60')},
            'horas': {'label': 'h', 'to_base': Decimal('3600')},
            'dias': {'label': 'd', 'to_base': Decimal('86400')},
        },
    },
    'temperatura': {
        'label': 'Temperatura',
        'units': {
            'celsius': {'label': '°C'},
            'fahrenheit': {'label': '°F'},
            'kelvin': {'label': 'K'},
            'rankine': {'label': '°R'},
        },
    },
    'presion': {
        'label': 'Presión / fluidos',
        'units': {
            'pascal': {'label': 'Pa', 'to_base': Decimal('1')},
            'kpa': {'label': 'kPa', 'to_base': Decimal('1000')},
            'bar': {'label': 'bar', 'to_base': Decimal('100000')},
            'atm': {'label': 'atm', 'to_base': Decimal('101325')},
            'psi': {'label': 'psi', 'to_base': Decimal('6894.757')},
            'mmhg': {'label': 'mmHg', 'to_base': Decimal('133.322')},
        },
    },
    'electricidad': {
        'label': 'Electricidad',
        'units': {
            'voltios': {'label': 'V', 'to_base': Decimal('1')},
            'milivoltios': {'label': 'mV', 'to_base': Decimal('0.001')},
            'kilovoltios': {'label': 'kV', 'to_base': Decimal('1000')},
            'amperios': {'label': 'A', 'to_base': Decimal('1')},
            'miliamperios': {'label': 'mA', 'to_base': Decimal('0.001')},
            'kiloamperios': {'label': 'kA', 'to_base': Decimal('1000')},
            'ohmios': {'label': 'Ω', 'to_base': Decimal('1')},
            'kiloohmios': {'label': 'kΩ', 'to_base': Decimal('1000')},
            'vatios': {'label': 'W', 'to_base': Decimal('1')},
            'kilovatios': {'label': 'kW', 'to_base': Decimal('1000')},
        },
    },
    'energia': {
        'label': 'Energía / trabajo',
        'units': {
            'julios': {'label': 'J', 'to_base': Decimal('1')},
            'kilojulios': {'label': 'kJ', 'to_base': Decimal('1000')},
            'calorias': {'label': 'cal', 'to_base': Decimal('4.184')},
            'kilocalorias': {'label': 'kcal', 'to_base': Decimal('4184')},
            'wh': {'label': 'Wh', 'to_base': Decimal('3600')},
            'kwh': {'label': 'kWh', 'to_base': Decimal('3600000')},
        },
    },
    'velocidad': {
        'label': 'Velocidad / movimiento',
        'units': {
            'm_s': {'label': 'm/s', 'to_base': Decimal('1')},
            'km_h': {'label': 'km/h', 'to_base': Decimal('0.2777778')},
            'mph': {'label': 'mph', 'to_base': Decimal('0.44704')},
            'nudos': {'label': 'kn', 'to_base': Decimal('0.514444')},
        },
    },
    'datos': {
        'label': 'Datos / almacenamiento',
        'units': {
            'bytes': {'label': 'B', 'to_base': Decimal('1')},
            'kilobytes': {'label': 'KB', 'to_base': Decimal('1024')},
            'megabytes': {'label': 'MB', 'to_base': Decimal('1048576')},
            'gigabytes': {'label': 'GB', 'to_base': Decimal('1073741824')},
            'terabytes': {'label': 'TB', 'to_base': Decimal('1099511627776')},
        },
    },
}


def _q(value):
    return Decimal(str(value).replace(',', '.'))


def _format(value):
    number = Decimal(value).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
    return format(number.normalize(), 'f').rstrip('0').rstrip('.') if '.' in format(number.normalize(), 'f') else format(number.normalize(), 'f')


def _convert_temperature(value, from_unit, to_unit):
    value = _q(value)
    celsius = {
        'celsius': value,
        'fahrenheit': (value - Decimal('32')) * Decimal('5') / Decimal('9'),
        'kelvin': value - Decimal('273.15'),
        'rankine': (value - Decimal('491.67')) * Decimal('5') / Decimal('9'),
    }.get(from_unit)

    if celsius is None:
        return None

    result = {
        'celsius': celsius,
        'fahrenheit': (celsius * Decimal('9') / Decimal('5')) + Decimal('32'),
        'kelvin': celsius + Decimal('273.15'),
        'rankine': (celsius + Decimal('273.15')) * Decimal('9') / Decimal('5'),
    }.get(to_unit)

    return result


def convert_measure(category_key, value, from_unit, to_unit):
    category = MEASURE_CATEGORIES.get(category_key, MEASURE_CATEGORIES['peso'])
    units = category['units']
    from_value = units.get(from_unit)
    to_value = units.get(to_unit)

    if not from_value or not to_value:
        return None

    if category_key == 'temperatura':
        return _convert_temperature(value, from_unit, to_unit)

    from_base = from_value.get('to_base')
    to_base = to_value.get('to_base')
    if from_base is None or to_base is None:
        return None

    base_amount = _q(value) * from_base
    result = base_amount / to_base
    return result


EXAMPLE_CONVERSIONS = {
    'peso': [
        {'label': '30 g', 'from_unit': 'gramos', 'to_unit': 'cucharas_grandes', 'result': '2 cucharadas grandes'},
        {'label': '500 g', 'from_unit': 'gramos', 'to_unit': 'libras', 'result': '1.10 lb'},
        {'label': '1 kg', 'from_unit': 'kilogramos', 'to_unit': 'gramos', 'result': '1000 g'},
        {'label': '240 ml', 'from_unit': 'mililitros', 'to_unit': 'tazas', 'result': '1 taza'},
    ],
    'volumen': [
        {'label': '250 ml', 'from_unit': 'mililitros', 'to_unit': 'litros', 'result': '0.25 l'},
        {'label': '1 l', 'from_unit': 'litros', 'to_unit': 'mililitros', 'result': '1000 ml'},
        {'label': '15 ml', 'from_unit': 'mililitros', 'to_unit': 'cucharas_grandes', 'result': '1 cucharada grande'},
        {'label': '1 taza', 'from_unit': 'tazas', 'to_unit': 'mililitros', 'result': '240 ml'},
    ],
    'longitud': [
        {'label': '10 cm', 'from_unit': 'centimetros', 'to_unit': 'milimetros', 'result': '100 mm'},
        {'label': '1 m', 'from_unit': 'metros', 'to_unit': 'pulgadas', 'result': '39.37 in'},
        {'label': '16 px', 'from_unit': 'pixeles', 'to_unit': 'rem', 'result': '1 rem'},
        {'label': '2 in', 'from_unit': 'pulgadas', 'to_unit': 'centimetros', 'result': '5.08 cm'},
    ],
    'tiempo': [
        {'label': '30 s', 'from_unit': 'segundos', 'to_unit': 'minutos', 'result': '0.5 min'},
        {'label': '2 h', 'from_unit': 'horas', 'to_unit': 'minutos', 'result': '120 min'},
        {'label': '1 min', 'from_unit': 'minutos', 'to_unit': 'segundos', 'result': '60 s'},
        {'label': '3 d', 'from_unit': 'dias', 'to_unit': 'horas', 'result': '72 h'},
    ],
    'temperatura': [
        {'label': '32 °F', 'from_unit': 'fahrenheit', 'to_unit': 'celsius', 'result': '0 °C'},
        {'label': '100 °C', 'from_unit': 'celsius', 'to_unit': 'fahrenheit', 'result': '212 °F'},
        {'label': '273.15 K', 'from_unit': 'kelvin', 'to_unit': 'celsius', 'result': '0 °C'},
        {'label': '300 K', 'from_unit': 'kelvin', 'to_unit': 'fahrenheit', 'result': '80.33 °F'},
    ],
    'presion': [
        {'label': '1 atm', 'from_unit': 'atm', 'to_unit': 'bar', 'result': '1.01 bar'},
        {'label': '100 kPa', 'from_unit': 'kpa', 'to_unit': 'psi', 'result': '14.5 psi'},
        {'label': '760 mmHg', 'from_unit': 'mmhg', 'to_unit': 'atm', 'result': '1 atm'},
        {'label': '2 bar', 'from_unit': 'bar', 'to_unit': 'kpa', 'result': '200 kPa'},
    ],
    'electricidad': [
        {'label': '230 V', 'from_unit': 'voltios', 'to_unit': 'kilovoltios', 'result': '0.23 kV'},
        {'label': '12 V', 'from_unit': 'voltios', 'to_unit': 'milivoltios', 'result': '12000 mV'},
        {'label': '1 A', 'from_unit': 'amperios', 'to_unit': 'miliamperios', 'result': '1000 mA'},
        {'label': '500 W', 'from_unit': 'vatios', 'to_unit': 'kilovatios', 'result': '0.5 kW'},
    ],
    'energia': [
        {'label': '1 kWh', 'from_unit': 'kwh', 'to_unit': 'wh', 'result': '1000 Wh'},
        {'label': '1000 J', 'from_unit': 'julios', 'to_unit': 'kilocalorias', 'result': '0.24 kcal'},
        {'label': '1 cal', 'from_unit': 'calorias', 'to_unit': 'julios', 'result': '4.18 J'},
        {'label': '2 kcal', 'from_unit': 'kilocalorias', 'to_unit': 'julios', 'result': '8368 J'},
    ],
    'velocidad': [
        {'label': '1 m/s', 'from_unit': 'm_s', 'to_unit': 'km_h', 'result': '3.6 km/h'},
        {'label': '60 mph', 'from_unit': 'mph', 'to_unit': 'km_h', 'result': '96.56 km/h'},
        {'label': '10 kn', 'from_unit': 'nudos', 'to_unit': 'm_s', 'result': '5.14 m/s'},
        {'label': '100 km/h', 'from_unit': 'km_h', 'to_unit': 'm_s', 'result': '27.78 m/s'},
    ],
    'datos': [
        {'label': '1 MB', 'from_unit': 'megabytes', 'to_unit': 'kilobytes', 'result': '1024 KB'},
        {'label': '1 GB', 'from_unit': 'gigabytes', 'to_unit': 'megabytes', 'result': '1024 MB'},
        {'label': '500 KB', 'from_unit': 'kilobytes', 'to_unit': 'bytes', 'result': '512000 B'},
        {'label': '2 TB', 'from_unit': 'terabytes', 'to_unit': 'gigabytes', 'result': '2048 GB'},
    ],
}


class DevToolsHubView(TemplateView):
    template_name = 'devtools/hub.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        votes = DevToolVote.objects.values('tool').annotate(
            likes=Count('id', filter=Q(vote=DevToolVote.LIKE)),
            dislikes=Count('id', filter=Q(vote=DevToolVote.DISLIKE)),
        )
        vote_map = {item['tool']: item for item in votes}
        context['tool_votes'] = vote_map

        user_votes = {}
        if self.request.user.is_authenticated:
            user_votes = {
                vote.tool: 'like' if vote.vote == DevToolVote.LIKE else 'dislike'
                for vote in DevToolVote.objects.filter(user=self.request.user)
            }

        tool_slugs = [
            'medidas', 'colores', 'sombras', 'analizador_contraste', 'espaciado', 'slugify',
            'tamanos', 'css', 'gradientes', 'tipografia', 'paleta', 'bordes', 'rem',
            'botones', 'layout', 'json', 'base64', 'texto', 'text_counter', 'hash',
            'css_vars', 'breakpoints', 'metadata_img', 'qr', 'url_shortener',
        ]
        can_manage = (
            self.request.user.is_authenticated
            and (self.request.user.is_staff or can(self.request.user, 'manage_access').allowed)
        )
        context['can_manage_tools'] = can_manage
        visible_tool_slugs = [
            slug for slug in tool_slugs
            if can_manage or _user_can_access_devtool(self.request.user, slug)
        ]

        context['tool_access'] = {
            slug: _user_can_access_devtool(self.request.user, slug)
            for slug in tool_slugs
        }
        context['tool_configs'] = [
            {
                'slug': slug,
                'name': _get_devtool_config(slug).name,
                'is_active': _get_devtool_config(slug).is_active,
                'is_private': _get_devtool_config(slug).is_private,
            }
            for slug in visible_tool_slugs if can_manage or slug in visible_tool_slugs
        ]
        context['user_votes'] = user_votes
        context['user'] = self.request.user
        return context


@login_required
def vote_tool(request, tool_slug):
    if request.method != 'POST':
        return redirect('devtools:hub')

    vote_value = request.POST.get('vote')
    if vote_value not in {'like', 'dislike'}:
        return redirect('devtools:hub')

    vote_choice = DevToolVote.LIKE if vote_value == 'like' else DevToolVote.DISLIKE
    vote_obj, created = DevToolVote.objects.get_or_create(
        user=request.user,
        tool=tool_slug,
        defaults={'vote': vote_choice},
    )

    if not created:
        vote_obj.vote = vote_choice
        vote_obj.save(update_fields=['vote'])

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        current = tool_votes = DevToolVote.objects.filter(tool=tool_slug).values('vote').annotate(
            total=Count('id')
        )
        likes = sum(item['total'] for item in current if item['vote'] == DevToolVote.LIKE)
        dislikes = sum(item['total'] for item in current if item['vote'] == DevToolVote.DISLIKE)
        return JsonResponse({
            'ok': True,
            'vote': vote_value,
            'likes': likes,
            'dislikes': dislikes,
        })

    return redirect('devtools:hub')


@staff_member_required
def devtool_admin_toggle(request, slug):
    if not request.user.is_staff and not request.user.is_superuser:
        messages.error(request, 'Solo el administrador o el staff puede modificar esta miniapp.')
        return redirect('devtools:hub')

    config = _get_devtool_config(slug)
    if request.method == 'POST':
        config.is_active = request.POST.get('is_active') == 'on'
        config.is_private = request.POST.get('is_private') == 'on'
        config.name = request.POST.get('name', config.name)
        config.save()
        messages.success(request, f'Configuración actualizada para {config.name}.')
    return redirect('devtools:hub')


@devtool_access_required('medidas')
class UniversalMeasureConverterView(TemplateView):
    template_name = 'devtools/converter.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        category_key = self.request.GET.get('category', 'peso')
        category = MEASURE_CATEGORIES.get(category_key, MEASURE_CATEGORIES['peso'])
        units = category['units']

        if category_key == 'temperatura':
            default_from = 'fahrenheit'
            default_to = 'celsius'
            default_value = '32'
        elif category_key == 'presion':
            default_from = 'atm'
            default_to = 'bar'
            default_value = '1'
        elif category_key == 'electricidad':
            default_from = 'voltios'
            default_to = 'kilovoltios'
            default_value = '230'
        else:
            default_from = 'gramos'
            default_to = 'cucharas_grandes'
            default_value = '30'

        from_unit = self.request.GET.get('from_unit', default_from)
        to_unit = self.request.GET.get('to_unit', default_to)
        raw_value = self.request.GET.get('value', default_value)

        if from_unit not in units:
            from_unit = next(iter(units))
        if to_unit not in units:
            to_unit = next(iter(units))
        if from_unit == to_unit and len(units) > 1:
            to_unit = list(units.keys())[1]

        result = None
        try:
            result = convert_measure(category_key, raw_value, from_unit, to_unit)
        except Exception:
            result = None

        from_label = category['units'].get(from_unit, {}).get('label', from_unit)
        to_label = category['units'].get(to_unit, {}).get('label', to_unit)

        context.update({
            'categories': MEASURE_CATEGORIES,
            'category': category,
            'category_key': category_key,
            'units': units,
            'from_unit': from_unit,
            'to_unit': to_unit,
            'from_label': from_label,
            'to_label': to_label,
            'value': raw_value,
            'result': result,
            'result_label': _format(result) if result is not None else '',
            'kitchen_examples': EXAMPLE_CONVERSIONS.get(category_key, EXAMPLE_CONVERSIONS['peso']),
        })
        return context


@devtool_access_required('colores')
class ColorToolView(TemplateView):
    template_name = 'devtools/color_tool.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        color = self.request.GET.get('color', '#2563eb')
        try:
            r, g, b = _hex_to_rgb(color)
            h, s, l = _rgb_to_hsl(r, g, b)
            color_data = {
                'hex': color.upper(),
                'rgb': f'{r}, {g}, {b}',
                'hsl': f'{h}°, {s}%, {l}%',
                'rgb_tuple': (r, g, b),
            }
        except ValueError:
            color_data = {
                'hex': '#2563EB',
                'rgb': '37, 99, 235',
                'hsl': '220°, 91%, 52%',
                'rgb_tuple': (37, 99, 235),
            }
        context['color'] = color_data
        return context


@devtool_access_required('sombras')
class ShadowToolView(TemplateView):
    template_name = 'devtools/shadow_tool.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        offset_x = self.request.GET.get('offset_x', '0')
        offset_y = self.request.GET.get('offset_y', '12')
        blur = self.request.GET.get('blur', '18')
        spread = self.request.GET.get('spread', '0')
        color = self.request.GET.get('color', '#0f172a')
        opacity = self.request.GET.get('opacity', '0.18')
        shadow = f'{offset_x}px {offset_y}px {blur}px {spread}px {color}'
        context.update({
            'shadow_css': shadow,
            'shadow_preview': f'box-shadow: {shadow};',
            'opacity': opacity,
        })
        return context


@devtool_access_required('analizador_contraste')
class ColorContrastAnalyzerView(TemplateView):
    template_name = 'devtools/color_contrast_analyzer.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        bg = self.request.GET.get('bg', '#FFFFFF')
        fg = self.request.GET.get('fg', '#111827')
        text_size = self.request.GET.get('size', 'normal')
        weight = self.request.GET.get('weight', 'normal')
        ratio = _get_contrast_ratio(bg, fg)

        normal_aa = ratio >= 4.5
        normal_aaa = ratio >= 7
        large_aa = ratio >= 3
        large_aaa = ratio >= 4.5

        context.update({
            'bg': bg.upper(),
            'fg': fg.upper(),
            'ratio': round(ratio, 2),
            'size': text_size,
            'weight': weight,
            'normal_aa': normal_aa,
            'normal_aaa': normal_aaa,
            'large_aa': large_aa,
            'large_aaa': large_aaa,
            'status': 'OK' if ratio >= 4.5 else 'Requiere ajuste',
            'suggestions': [
                {'bg': '#111827', 'fg': '#FFFFFF', 'label': 'Texto oscuro sobre fondo claro'},
                {'bg': '#F8FAFC', 'fg': '#0F172A', 'label': 'Base neutra para interfaces'},
                {'bg': '#0F172A', 'fg': '#F8FAFC', 'label': 'Acento oscuro con texto limpio'},
                {'bg': '#E0F2FE', 'fg': '#0C4A6E', 'label': 'Azul claro para callouts'},
            ],
        })
        return context


@devtool_access_required('espaciado')
class SpacingToolView(TemplateView):
    template_name = 'devtools/spacing_tool.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base = int(self.request.GET.get('base', '8'))
        steps = [
            {'name': 'xs', 'value': base * 1},
            {'name': 'sm', 'value': base * 2},
            {'name': 'md', 'value': base * 4},
            {'name': 'lg', 'value': base * 8},
            {'name': 'xl', 'value': base * 12},
            {'name': '2xl', 'value': base * 16},
        ]
        context['base'] = base
        context['steps'] = steps
        return context


@devtool_access_required('slugify')
class SlugifyToolView(TemplateView):
    template_name = 'devtools/slugify_tool.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        raw = self.request.GET.get('text', 'Mi proyecto de frontend moderno')
        context['text'] = raw
        context['slug'] = _slugify(raw)
        return context


@devtool_access_required('tamanos')
class SizeEstimatorView(TemplateView):
    template_name = 'devtools/size_estimator.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        width = self.request.GET.get('width', '1440') or '1440'
        height = self.request.GET.get('height', '900') or '900'
        scale = self.request.GET.get('scale', '1') or '1'
        pixel_density = self.request.GET.get('density', '2') or '2'

        width = int(width)
        height = int(height)
        scale = float(scale)
        pixel_density = float(pixel_density)

        multiplier = (width * height) / 1000000
        context.update({
            'width': width,
            'height': height,
            'scale': scale,
            'density': pixel_density,
            'estimated_mb': round(multiplier * scale * (pixel_density / 1.5) / 2, 2),
        })
        return context


@devtool_access_required('css')
class CssSnippetGeneratorView(TemplateView):
    template_name = 'devtools/css_tool.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        start_color = self.request.GET.get('start_color') or self.request.GET.get('color') or '#2563eb'
        end_color = self.request.GET.get('end_color') or '#1d4ed8'
        text_color = self.request.GET.get('text_color') or '#ffffff'
        radius = self.request.GET.get('radius', '16')
        padding = self.request.GET.get('padding', '16')
        font_size = self.request.GET.get('font_size', '16')
        snippet = (
            f'background: linear-gradient(135deg, {start_color}, {end_color});\n'
            f'border-radius: {radius}px;\n'
            f'padding: {padding}px;\n'
            f'color: {text_color};\n'
            f'font-size: {font_size}px;\n'
            f'box-shadow: 0 12px 24px rgba(37, 99, 235, 0.18);'
        )
        context.update({
            'start_color': start_color,
            'end_color': end_color,
            'text_color': text_color,
            'color': start_color,
            'radius': radius,
            'padding': padding,
            'font_size': font_size,
            'snippet': snippet,
        })
        return context


@devtool_access_required('gradientes')
class GradientToolView(TemplateView):
    template_name = 'devtools/gradient_tool.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        start = self.request.GET.get('start', '#2563eb')
        end = self.request.GET.get('end', '#7c3aed')
        direction = self.request.GET.get('direction', '135deg')
        gradient = f'linear-gradient({direction}, {start}, {end})'
        context.update({
            'start': start,
            'end': end,
            'direction': direction,
            'gradient': gradient,
        })
        return context


@devtool_access_required('tipografia')
class TypographyToolView(TemplateView):
    template_name = 'devtools/typography_tool.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base = float(self.request.GET.get('base', '16'))
        ratio = float(self.request.GET.get('ratio', '1.25'))
        steps = {
            'xs': round(base / ratio / ratio, 2),
            'sm': round(base / ratio, 2),
            'base': round(base, 2),
            'lg': round(base * ratio, 2),
            'xl': round(base * ratio * ratio, 2),
            '2xl': round(base * ratio * ratio * ratio, 2),
        }
        context.update({
            'base': base,
            'ratio': ratio,
            'steps': steps,
        })
        return context


@devtool_access_required('paleta')
class PaletteToolView(TemplateView):
    template_name = 'devtools/palette_tool.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base = self.request.GET.get('base', '#2563eb')
        palette_count = int(self.request.GET.get('palette_count', '6'))
        max_colors = int(self.request.GET.get('max_colors', '5'))
        palette = {
            'base': base.upper(),
            'light': _mix_hex_colors(base, '#ffffff', 0.7).upper(),
            'dark': _mix_hex_colors(base, '#0f172a', 0.7).upper(),
            'soft': _mix_hex_colors(base, '#e0f2fe', 0.4).upper(),
            'accent': _mix_hex_colors(base, '#f59e0b', 0.5).upper(),
        }
        context.update({
            'base': base,
            'palette': palette,
            'palette_count': max(1, min(12, palette_count)),
            'max_colors': max(2, min(10, max_colors)),
        })
        return context


@devtool_access_required('bordes')
class BorderRadiusToolView(TemplateView):
    template_name = 'devtools/border_tool.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        radius = int(self.request.GET.get('radius', '16'))
        snippet = f'border-radius: {radius}px;'
        context.update({
            'radius': radius,
            'snippet': snippet,
        })
        return context


@devtool_access_required('rem')
class RemConverterToolView(TemplateView):
    template_name = 'devtools/rem_tool.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        px_value = self.request.GET.get('px', '16') or '16'
        base = self.request.GET.get('base', '16') or '16'
        px_value = float(px_value)
        base = float(base) if float(base) != 0 else 16.0
        rem_value = px_value / base
        rem_formatted = format(rem_value, '.4f').rstrip('0').rstrip('.')
        if '.' not in rem_formatted:
            rem_formatted += '.0'
        context.update({
            'px': px_value,
            'base': base,
            'rem': rem_formatted,
        })
        return context


@devtool_access_required('botones')
class ButtonGeneratorToolView(TemplateView):
    template_name = 'devtools/button_tool.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        text = self.request.GET.get('text', 'Botón')
        bg = self.request.GET.get('bg', '#2563eb')
        color = self.request.GET.get('color', '#ffffff')
        radius = self.request.GET.get('radius', '12')
        padding_x = self.request.GET.get('padding_x', '20')
        padding_y = self.request.GET.get('padding_y', '12')
        width = self.request.GET.get('width', '180')
        height = self.request.GET.get('height', '48')
        shadow_enabled = self.request.GET.get('shadow_enabled') == 'on'
        shadow_x = self.request.GET.get('shadow_x', '0')
        shadow_y = self.request.GET.get('shadow_y', '12')
        shadow_blur = self.request.GET.get('shadow_blur', '24')
        shadow_color = self.request.GET.get('shadow_color', '#0f172a')
        shadow_style = (
            f'box-shadow: {shadow_x}px {shadow_y}px {shadow_blur}px {shadow_color};'
            if shadow_enabled else 'box-shadow: none;'
        )
        snippet = (
            f'background: {bg};\n'
            f'color: {color};\n'
            f'border: none;\n'
            f'border-radius: {radius}px;\n'
            f'padding: {padding_y}px {padding_x}px;\n'
            f'width: {width}px;\n'
            f'height: {height}px;\n'
            f'{shadow_style}\n'
            f'font-weight: 600;'
        )
        context.update({
            'text': text,
            'bg': bg,
            'color': color,
            'radius': radius,
            'padding_x': padding_x,
            'padding_y': padding_y,
            'width': width,
            'height': height,
            'shadow_enabled': shadow_enabled,
            'shadow_x': shadow_x,
            'shadow_y': shadow_y,
            'shadow_blur': shadow_blur,
            'shadow_color': shadow_color,
            'snippet': snippet,
        })
        return context


@devtool_access_required('layout')
class LayoutCalculatorToolView(TemplateView):
    template_name = 'devtools/layout_tool.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        columns = int(self.request.GET.get('columns', '12'))
        gap = int(self.request.GET.get('gap', '24'))
        container = int(self.request.GET.get('container', '1200'))
        column_width = (container - (gap * (columns - 1))) / columns
        context.update({
            'columns': columns,
            'gap': gap,
            'container': container,
            'column_width': round(column_width, 2),
        })
        return context


@devtool_access_required('json')
class JsonFormatterToolView(TemplateView):
    template_name = 'devtools/json_tool.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        raw_json = self.request.GET.get('json_input', '{\n  "name": "mi-app",\n  "active": true,\n  "items": [1, 2, 3]\n}')
        pretty = raw_json
        error = ''
        try:
            pretty = json.dumps(json.loads(raw_json), ensure_ascii=False, indent=2)
        except Exception as exc:
            error = str(exc)
        context.update({
            'json_input': raw_json,
            'pretty_json': pretty,
            'error': error,
        })
        return context


@devtool_access_required('base64')
class Base64ToolView(TemplateView):
    template_name = 'devtools/base64_tool.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        value = self.request.GET.get('text', 'hola mundo')
        mode = self.request.GET.get('mode', 'encode')
        result = ''
        error = ''
        try:
            if mode == 'decode':
                decoded = base64.b64decode(value.encode('utf-8'))
                result = decoded.decode('utf-8')
            else:
                result = base64.b64encode(value.encode('utf-8')).decode('utf-8')
        except Exception as exc:
            error = str(exc)
            result = ''
        context.update({
            'text': value,
            'mode': mode,
            'result': result,
            'error': error,
        })
        return context


@devtool_access_required('texto')
class TextToolView(TemplateView):
    template_name = 'devtools/text_tool.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        raw = self.request.GET.get('text', 'Mi texto de ejemplo para devtools')
        operation = self.request.GET.get('operation', 'lower')
        result = raw

        if operation == 'upper':
            result = raw.upper()
        elif operation == 'title':
            result = raw.title()
        elif operation == 'trim':
            result = raw.strip()
        elif operation == 'compact':
            result = ' '.join(raw.split())
        elif operation == 'slug':
            result = _slugify(raw)

        context.update({
            'text': raw,
            'operation': operation,
            'result': result,
        })
        return context


@devtool_access_required('text_counter')
class TextCounterToolView(TemplateView):
    template_name = 'devtools/text_counter_tool.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        text = self.request.GET.get('text', 'Escribe aquí tu texto para analizarlo.')
        words = len(text.split()) if text.strip() else 0
        chars = len(text)
        lines = len(text.splitlines()) if text else 0
        read_time = max(1, round(words / 200, 1)) if words else 0

        context.update({
            'text': text,
            'chars': chars,
            'words': words,
            'lines': lines,
            'read_time': read_time,
        })
        return context


@devtool_access_required('hash')
class HashToolView(TemplateView):
    template_name = 'devtools/hash_tool.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        text = self.request.GET.get('text', 'mi texto de ejemplo')
        algorithm = self.request.GET.get('algorithm', 'sha256')
        supported = ['md5', 'sha1', 'sha256', 'sha512']
        digest = ''

        if algorithm in supported:
            digest = hashlib.new(algorithm, text.encode('utf-8')).hexdigest()

        context.update({
            'text': text,
            'algorithm': algorithm,
            'supported': supported,
            'hash_value': digest,
        })
        return context


@devtool_access_required('css_vars')
class CssVariablesToolView(TemplateView):
    template_name = 'devtools/css_vars_tool.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base = self.request.GET.get('base', '#2563eb')
        accent = self.request.GET.get('accent', '#7c3aed')
        radius = self.request.GET.get('radius', '16')
        spacing = self.request.GET.get('spacing', '24')
        snippet = (
            ':root {\n'
            f'  --color-primary: {base};\n'
            f'  --color-accent: {accent};\n'
            f'  --radius-lg: {radius}px;\n'
            f'  --space-md: {spacing}px;\n'
            '}'
        )
        context.update({
            'base': base,
            'accent': accent,
            'radius': radius,
            'spacing': spacing,
            'snippet': snippet,
        })
        return context


@devtool_access_required('breakpoints')
class BreakpointsToolView(TemplateView):
    template_name = 'devtools/breakpoints_tool.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sm = self.request.GET.get('sm', '640')
        md = self.request.GET.get('md', '768')
        lg = self.request.GET.get('lg', '1024')
        xl = self.request.GET.get('xl', '1280')
        snippet = (
            '@media (min-width: 640px) {\n'
            '  /* sm */\n'
            '}\n\n'
            '@media (min-width: 768px) {\n'
            '  /* md */\n'
            '}\n\n'
            '@media (min-width: 1024px) {\n'
            '  /* lg */\n'
            '}\n\n'
            '@media (min-width: 1280px) {\n'
            '  /* xl */\n'
            '}'
        )
        context.update({
            'sm': sm,
            'md': md,
            'lg': lg,
            'xl': xl,
            'snippet': snippet,
        })
        return context


@devtool_access_required('metadata_img')
class MetadataImageToolView(TemplateView):
    template_name = 'devtools/metadata_img_tool.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        src = self.request.GET.get('src', '/static/img/hero.jpg')
        alt = self.request.GET.get('alt', 'Imagen de ejemplo')
        width = self.request.GET.get('width', '1200')
        height = self.request.GET.get('height', '800')
        loading = self.request.GET.get('loading', 'lazy')
        html_snippet = (
            f'<img src="{src}" alt="{alt}" width="{width}" height="{height}" loading="{loading}" />'
        )
        context.update({
            'src': src,
            'alt': alt,
            'width': width,
            'height': height,
            'loading': loading,
            'html_snippet': html_snippet,
        })
        return context


@devtool_access_required('qr')
class QrToolView(TemplateView):
    template_name = 'devtools/qr_tool.html'

    def _build_matrix(self, text):
        size = 21
        matrix = [[0 for _ in range(size)] for _ in range(size)]
        finder_positions = [(0, 0), (0, size - 7), (size - 7, 0)]
        for fx, fy in finder_positions:
            for y in range(fy, fy + 7):
                for x in range(fx, fx + 7):
                    if x in (fx, fx + 6) or y in (fy, fy + 6) or (x >= fx + 2 and x <= fx + 4 and y >= fy + 2 and y <= fy + 4):
                        matrix[y][x] = 1
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        i = 0
        for y in range(size):
            for x in range(size):
                if x < 7 and y < 7 or x < 7 and y >= size - 7 or x >= size - 7 and y < 7:
                    continue
                if x < 7 or y < 7 or x >= size - 7 or y >= size - 7:
                    continue
                if i < len(digest):
                    matrix[y][x] = int(digest[i % len(digest)], 16) % 2
                    i += 1
        return matrix

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        text = self.request.GET.get('text', 'https://example.com')
        matrix = self._build_matrix(text)
        context.update({
            'text': text,
            'matrix': matrix,
            'size': len(matrix),
        })
        return context


@devtool_access_required('url_shortener')
class UrlShortenerToolView(TemplateView):
    template_name = 'devtools/url_shortener_tool.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        original_url = self.request.GET.get('url', 'https://example.com/producto/landing')
        code = hashlib.sha1(original_url.encode('utf-8')).hexdigest()[:8]
        short_url = f'https://mini.dev/{code}'
        context.update({
            'url': original_url,
            'short_url': short_url,
            'code': code,
        })
        return context
