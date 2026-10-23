import json
import logging
from datetime import datetime
from pathlib import PurePosixPath

from django.apps import apps
from django.contrib.admin.models import CHANGE, LogEntry
from django.core import serializers
from django.core.exceptions import ValidationError
from django.core.management.color import no_style
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.db.models import FileField
from django.utils import timezone


logger = logging.getLogger(__name__)
FORMAT = 'portal-content-backup'
MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 10000
SECTIONS = {
    'prompts': ('Prompts', (
        'prompts.categoriaprompt', 'prompts.subcategoriaprompt',
        'prompts.hashtagprompt', 'prompts.prompt',
    )),
    'blog': ('Blog', (
        'blog.categoriablog', 'blog.subcategoriablog', 'blog.hashtagblog', 'blog.postblog',
    )),
    'enlaces': ('Enlaces', ('enlaces.enlaceimportante',)),
}


class BackupJSONEncoder(DjangoJSONEncoder):
    def default(self, value):
        if isinstance(value, datetime):
            return value.isoformat()
        return super().default(value)


def _models(section):
    if section not in SECTIONS:
        raise ValidationError('Sección de copia no válida.')
    return [apps.get_model(label) for label in SECTIONS[section][1]]


def _audit(actor, section, action, counts):
    LogEntry.objects.log_action(
        user_id=actor.pk, content_type_id=None, object_id=None,
        object_repr=f'Copia de contenido: {section}', action_flag=CHANGE,
        change_message=json.dumps({'action': action, 'section': section, 'counts': counts}),
    )
    logger.info('Content backup %s section=%s actor=%s counts=%s', action, section, actor.pk, counts)


@transaction.atomic
def export_backup(section, actor):
    models = _models(section)
    objects = []
    counts = {}
    for model in models:
        queryset = model.objects.order_by('pk')
        counts[model._meta.label_lower] = queryset.count()
        if sum(counts.values()) > MAX_ROWS:
            raise ValidationError('La sección supera 10.000 registros; utiliza el procedimiento de copia del servidor.')
        objects.extend(json.loads(serializers.serialize('json', queryset, cls=BackupJSONEncoder)))
    payload = {
        'format': FORMAT, 'version': 1, 'section': section,
        'created_at': timezone.now().isoformat(), 'files_included': False,
        'models': list(SECTIONS[section][1]), 'objects': objects,
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
    if len(content) > MAX_BYTES:
        raise ValidationError('La copia supera 10 MB; utiliza el procedimiento de copia del servidor.')
    _audit(actor, section, 'download', counts)
    return content


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f'El JSON contiene una clave duplicada: {key}.')
        result[key] = value
    return result


def parse_backup(content, section):
    models = _models(section)
    if len(content) > MAX_BYTES:
        raise ValidationError('El archivo supera el límite de 10 MB.')
    try:
        payload = json.loads(content.decode('utf-8-sig'), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValidationError('El archivo no contiene un JSON UTF-8 válido.') from error
    expected = list(SECTIONS[section][1])
    if (
        not isinstance(payload, dict) or payload.get('format') != FORMAT
        or type(payload.get('version')) is not int or payload['version'] != 1
        or payload.get('section') != section or payload.get('models') != expected
        or payload.get('files_included') is not False
    ):
        raise ValidationError('La copia no corresponde a esta sección o versión de formato.')
    rows = payload.get('objects')
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValidationError('La copia debe contener una lista de hasta 10.000 registros.')
    model_map = {model._meta.label_lower: model for model in models}
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'model', 'pk', 'fields'}:
            raise ValidationError('Estructura de registro no válida.')
        label, pk, fields = row['model'], row['pk'], row['fields']
        if not isinstance(label, str) or label not in model_map:
            raise ValidationError('La copia contiene una tabla no permitida.')
        if type(pk) is not int or not 0 < pk <= 9223372036854775807 or (label, pk) in seen:
            raise ValidationError('La copia contiene IDs no válidos o duplicados.')
        seen.add((label, pk))
        model = model_map[label]
        expected_fields = {
            field.name for field in model._meta.local_fields + model._meta.local_many_to_many
            if field.serialize
        }
        if not isinstance(fields, dict) or set(fields) != expected_fields:
            raise ValidationError(f'Los campos de {label} no coinciden con el esquema actual.')
        for field in model._meta.local_fields + model._meta.local_many_to_many:
            if not field.serialize:
                continue
            value = fields[field.name]
            if field.many_to_many:
                if not isinstance(value, list) or any(type(pk) is not int or pk <= 0 for pk in value):
                    raise ValidationError(f'Relación múltiple no válida: {label}.{field.name}.')
            elif field.is_relation:
                if value is not None and (type(value) is not int or value <= 0):
                    raise ValidationError(f'Referencia no válida: {label}.{field.name}.')
            elif isinstance(field, FileField):
                if value is None or value == '':
                    continue
                if not isinstance(value, str) or '\\' in value or ':' in value:
                    raise ValidationError('Ruta de archivo no válida.')
                path = PurePosixPath(value)
                if path.is_absolute() or '..' in path.parts or not value.startswith(field.upload_to):
                    raise ValidationError('La ruta del archivo debe pertenecer a su carpeta de medios.')
    order = {label: index for index, label in enumerate(expected)}
    return sorted(rows, key=lambda row: (order[row['model']], row['pk']))


@transaction.atomic
def restore_backup(content, section, actor):
    rows = parse_backup(content, section)
    models = _models(section)
    counts = {'inserted': 0, 'updated': 0}
    for model in models:
        # Lock existing rows while applying this section, without deleting absent IDs.
        list(model.objects.select_for_update().values_list('pk', flat=True))
    try:
        restored = list(serializers.deserialize('json', json.dumps(rows)))
        for item in restored:
            exists = type(item.object).objects.filter(pk=item.object.pk).exists()
            # Fixture saves preserve timestamps and bypass content save overrides.
            item.save()
            counts['updated' if exists else 'inserted'] += 1
        for item in restored:
            obj = item.object
            obj.full_clean()
            if hasattr(obj, 'subcategoria_id') and obj.subcategoria_id:
                if obj.subcategoria.categoria_id != obj.categoria_id:
                    raise ValidationError('La subcategoría no pertenece a la categoría del contenido.')
        connection.check_constraints()
        with connection.cursor() as cursor:
            for statement in connection.ops.sequence_reset_sql(no_style(), models):
                cursor.execute(statement)
    except serializers.base.DeserializationError as error:
        raise ValidationError('No se pueden interpretar los valores o relaciones de la copia.') from error
    _audit(actor, section, 'restore', counts)
    return counts
