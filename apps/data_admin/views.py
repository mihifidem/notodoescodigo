import csv
import io
from pathlib import Path

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.utils.text import slugify
from django.views.decorators.http import require_POST
from django.apps import apps
from django.views.generic import TemplateView


SECURE_MODELS = [
    'users.user',
    'courses.curso',
    'courses.tema',
    'courses.quiz',
    'courses.resultadoquiz',
    'blog.postblog',
    'prompts.prompt',
    'gamification.insignia',
    'rewards.recompensa',
    'bug_reports.bugreport',
]


class DataAdminDashboardView(TemplateView):
    template_name = 'data_admin/dashboard.html'

    @method_decorator(staff_member_required)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        models_info = []

        for app_label, model_name in sorted(self._get_model_names()):
            model = apps.get_model(app_label, model_name)
            try:
                total = model.objects.count()
            except Exception:
                total = 0
            models_info.append({
                'app_label': app_label,
                'model_name': model_name,
                'label': model._meta.label,
                'verbose_name': model._meta.verbose_name_plural.title(),
                'count': total,
            })

        context['models_info'] = models_info
        context['safe_models'] = [m for m in models_info if m['label'] in SECURE_MODELS]
        return context

    @staticmethod
    def _get_model_names():
        names = []
        for app_config in apps.get_app_configs():
            for model in app_config.get_models():
                label = model._meta.label_lower
                if label in SECURE_MODELS:
                    names.append((model._meta.app_label, model._meta.model_name))
        return names


@staff_member_required
@require_POST
def backup_csv(request):
    model_name = request.POST.get('model_name')
    if not model_name:
        messages.error(request, 'No se indicó ningún modelo.')
        return redirect('data_admin:dashboard')

    label = model_name
    try:
        model = apps.get_model(label.split('.')[0], label.split('.')[-1])
    except Exception:
        messages.error(request, 'Modelo no válido.')
        return redirect('data_admin:dashboard')

    export_path = Path('tmp') / f'{model._meta.model_name}_{model.objects.count()}.csv'
    export_path.parent.mkdir(exist_ok=True)

    fieldnames = [field.name for field in model._meta.fields]
    with export_path.open('w', newline='', encoding='utf-8') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for obj in model.objects.all():
            row = {}
            for field in fieldnames:
                value = getattr(obj, field)
                if hasattr(value, 'all'):
                    row[field] = ','.join(str(v) for v in value.all())
                elif value is None:
                    row[field] = ''
                else:
                    row[field] = value
            writer.writerow(row)

    messages.success(request, f'Copia de seguridad creada en {export_path}.')
    return redirect('data_admin:dashboard')


@staff_member_required
def download_csv(request, model_name):
    try:
        app_label, model_model = model_name.split('.')
        model = apps.get_model(app_label, model_model)
    except Exception:
        return HttpResponse('Modelo no válido', status=400)

    output = io.StringIO()
    writer = csv.writer(output)
    fieldnames = [field.name for field in model._meta.fields]
    writer.writerow(fieldnames)
    for obj in model.objects.all():
        row = []
        for field in fieldnames:
            value = getattr(obj, field)
            if hasattr(value, 'all'):
                value = ','.join(str(v) for v in value.all())
            elif value is None:
                value = ''
            row.append(str(value))
        writer.writerow(row)

    response = HttpResponse(output.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{model._meta.model_name}.csv"'
    return response


@staff_member_required
@require_POST
def restore_csv(request):
    uploaded_file = request.FILES.get('csv_file')
    model_name = request.POST.get('model_name')
    if not uploaded_file or not model_name:
        messages.error(request, 'Debes seleccionar un archivo CSV y un modelo.')
        return redirect('data_admin:dashboard')

    try:
        app_label, model_model = model_name.split('.')
        model = apps.get_model(app_label, model_model)
    except Exception:
        messages.error(request, 'Modelo no válido.')
        return redirect('data_admin:dashboard')

    try:
        file_data = uploaded_file.read().decode('utf-8-sig')
    except Exception:
        messages.error(request, 'El archivo no es válido.')
        return redirect('data_admin:dashboard')

    reader = csv.DictReader(io.StringIO(file_data))
    fields = [field for field in reader.fieldnames or []]
    if not fields:
        messages.error(request, 'El CSV no tiene columnas válidas.')
        return redirect('data_admin:dashboard')

    def coerce_value(field, raw_value):
        if raw_value is None:
            return None if field.null else ''

        raw_text = str(raw_value).strip()
        if raw_text == '':
            if field.many_to_many:
                return []
            if field.is_relation and not field.many_to_many:
                return None if field.null else ''
            if field.get_internal_type() in {'BooleanField', 'NullBooleanField'}:
                return False
            if field.get_internal_type() in {'IntegerField', 'BigIntegerField', 'PositiveIntegerField', 'PositiveSmallIntegerField', 'SmallIntegerField', 'FloatField', 'DecimalField'}:
                return 0 if not field.null else None
            return ''

        if field.many_to_many:
            return []

        if field.is_relation and not field.many_to_many:
            related_model = field.related_model
            if not raw_text:
                return None if field.null else ''

            candidates = []
            if hasattr(related_model, 'pk') and raw_text.isdigit():
                candidates.append({'pk': int(raw_text)})
            if hasattr(related_model, 'slug'):
                candidates.append({'slug__iexact': raw_text})
            if hasattr(related_model, 'nombre'):
                candidates.append({'nombre__iexact': raw_text})
            if hasattr(related_model, 'slug'):
                candidates.append({'slug__iexact': slugify(raw_text)})

            for lookup in candidates:
                related_obj = related_model.objects.filter(**lookup).first()
                if related_obj is not None:
                    return related_obj

            create_payload = {}
            if hasattr(related_model, 'categoria'):
                parent_field = related_model._meta.get_field('categoria')
                parent_model = parent_field.related_model
                parent_name = raw_text
                if ' / ' in raw_text:
                    parent_name = raw_text.split(' / ', 1)[0].strip()
                elif '/' in raw_text:
                    parent_name = raw_text.split('/', 1)[0].strip()

                if parent_name and parent_name != raw_text:
                    parent_obj = parent_model.objects.filter(nombre__iexact=parent_name).first()
                    if parent_obj is None:
                        parent_obj = parent_model.objects.create(
                            nombre=parent_name,
                            slug=slugify(parent_name),
                        )
                    create_payload['categoria'] = parent_obj

            if hasattr(related_model, 'nombre'):
                create_payload['nombre'] = raw_text
            if hasattr(related_model, 'slug'):
                create_payload['slug'] = slugify(raw_text)
            return related_model.objects.create(**create_payload)

        if field.get_internal_type() in {'BooleanField', 'NullBooleanField'}:
            return raw_text.lower() in {'1', 'true', 'yes', 'y'}
        if field.get_internal_type() in {'IntegerField', 'BigIntegerField', 'PositiveIntegerField', 'PositiveSmallIntegerField', 'SmallIntegerField', 'FloatField', 'DecimalField'}:
            return field.to_python(raw_text)
        return raw_text

    model.objects.all().delete()
    for row in reader:
        payload = {}
        for field in model._meta.fields:
            if field.name not in row:
                continue
            payload[field.name] = coerce_value(field, row.get(field.name))
        instance = model(**payload)
        instance.save()

        for field in model._meta.many_to_many:
            raw_value = row.get(field.name, '')
            if not raw_value:
                continue
            related_values = [item.strip() for item in str(raw_value).split(',') if item.strip()]
            related_objs = []
            for item in related_values:
                lookup = {'pk': item}
                if hasattr(field.related_model, 'slug'):
                    lookup = {'slug': item}
                elif hasattr(field.related_model, 'nombre'):
                    lookup = {'nombre': item}
                related_obj, _ = field.related_model.objects.get_or_create(**lookup)
                related_objs.append(related_obj)
            getattr(instance, field.name).set(related_objs)

    messages.success(request, f'Datos restaurados en {model._meta.verbose_name}.')
    return redirect('data_admin:dashboard')


@staff_member_required
@require_POST
def clear_table(request, model_name):
    try:
        app_label, model_model = model_name.split('.')
        model = apps.get_model(app_label, model_model)
    except Exception:
        messages.error(request, 'Modelo no válido.')
        return redirect('data_admin:dashboard')

    model.objects.all().delete()
    messages.success(request, f'La tabla {model._meta.verbose_name} ha sido limpiada.')
    return redirect('data_admin:dashboard')
