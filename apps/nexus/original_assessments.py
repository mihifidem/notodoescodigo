import hashlib
import json
from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import AnswerOption, Assessment, NexusFamily, NexusModule, NexusVersion, Question
from .original_scoring import METHOD, PREFIXES
from .original_sources import load_bank, mode_questions


LIMITATIONS = (
    'Migración de los originales aportados al proyecto. Evaluación orientativa, no diagnóstico '
    'ni certificación. No se aportan baremos poblacionales ni evidencia de validación científica. '
    'Se han eliminado el ruido aleatorio y las puntuaciones de relleno. Las dimensiones o índices '
    'sin respuestas suficientes no se calculan. Los tiempos son declarados por el navegador. '
    'PersonaSphere aplica el campo is_reverse del banco: el JavaScript original usaba por error '
    'isReverse, que no existía en sus preguntas. No se trasladan recomendaciones automáticas de contratación.'
)


def bank_digest(bank):
    return hashlib.sha256(json.dumps(bank, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def option_labels(family, question):
    if question['type'] != 'likert':
        return list(enumerate(question['options']))
    labels = {
        'identitymap': ('Nada parecido', 'Poco', 'Variable', 'Bastante', 'Muchísimo'),
        'decissioncore': ('Nada parecido', 'Poco', 'Neutral', 'Bastante', 'Muy parecido'),
        'personasphere': ('Muy bajo', 'Bajo', 'Neutral', 'Alto', 'Muy alto'),
    }.get(family, ('Nunca', 'Raramente', 'A veces', 'Frecuentemente', 'Siempre'))
    return [(index, f'{index} · {label}') for index, label in enumerate(labels, 1)]


def original_snapshot(version):
    definition = version.definition
    family = definition.get('original_family')
    if family != version.family.code:
        raise ValidationError('La familia original no coincide con la versión.')
    bank = load_bank(family)
    if definition.get('source_digest') != bank_digest(bank):
        raise ValidationError('El banco original ha cambiado: importa una nueva versión.')
    expected = mode_questions(bank, version.mode)
    rules = definition.get('questions')
    if not isinstance(rules, dict) or len(rules) != len(expected):
        raise ValidationError('Las referencias no coinciden con la modalidad original.')
    questions = {
        str(q.pk): q for q in Question.objects.filter(
            module__assessments=version.assessment, active=True, module__active=True,
        ).prefetch_related('options').distinct()
    }
    if set(questions) != set(rules):
        raise ValidationError('Las preguntas activas no coinciden con el banco importado.')
    frozen = []
    for original in expected:
        key = [original['dimKey'], original['id']]
        ids = [pk for pk, rule in rules.items() if rule.get('source_key') == key]
        if len(ids) != 1:
            raise ValidationError(f'Referencia original ausente o duplicada: {key}.')
        question = questions[ids[0]]
        if question.text != original['text']:
            raise ValidationError('El texto original fue modificado; crea otra versión de contenido.')
        options = list(question.options.all())
        labels = option_labels(family, original)
        expected_type = Question.TYPE_LIKERT if original['type'] == 'likert' else Question.TYPE_MULTIPLE
        if question.question_type != expected_type or [(int(o.value), o.text) for o in options] != labels:
            raise ValidationError('Las opciones o la escala no coinciden con el original.')
        frozen.append({
            'id': question.pk, 'text': question.text, 'module_id': question.module_id,
            'dimension': original['dimKey'], 'weight': '1', 'reverse': False, 'required': True,
            'original': original,
            'options': [{'id': option.pk, 'text': option.text, 'value': str(option.value)} for option in options],
        })
    prefix, count = PREFIXES[family]
    dimensions = {}
    for index in range(1, count + 1):
        code = f'{prefix}{index:02}'
        dimensions[code] = {
            'label': bank['labels'][code], 'minimum_answers': 1,
            'brief': f'Resultado orientativo de {bank["labels"][code]}.',
            'explanation': 'Se aplican las escalas, inversiones y ponderaciones del banco original, sin ruido aleatorio.',
            'recommendation': 'Contrasta este resultado con ejemplos de tu experiencia y con feedback contextual.',
            'steps': 'Anota un comportamiento observable, practícalo y revisa su evolución.',
        }
    return {
        'schema': 1, 'code': version.code, 'version': version.version, 'mode': version.mode,
        'title': version.title, 'family': version.family.name, 'method': METHOD,
        'original_family': family, 'original_archetypes': bank['archetypes'],
        'source_digest': definition['source_digest'], 'source_files': bank['sources'],
        'dimensions': dimensions, 'questions': frozen,
        **{key: getattr(version, key) for key in (
            'purpose', 'instructions', 'limitations', 'authorship', 'provenance', 'authorization',
            'foundation', 'validation_evidence', 'is_fixture', 'duration_minutes',
        )},
        'reviewed_on': str(version.reviewed_on),
    }


@transaction.atomic
def import_original(family, mode, number=1, editor=None, publish=False, authorization=''):
    bank = load_bank(family)
    digest = bank_digest(bank)
    code = f'{family}-{mode}-original'
    existing = NexusVersion.objects.filter(code=code, version=number).first()
    if existing:
        if existing.definition.get('source_digest') != digest:
            raise ValidationError(f'{code}: cambia --content-version para importar un banco distinto.')
        version = existing
    else:
        family_object = NexusFamily.objects.get(code=family)
        title = f'{family_object.name} {mode.title()}'
        assessment = Assessment.objects.create(title=title, created_by=editor)
        module = NexusModule.objects.create(
            code=f'{code}-v{number}', name=title, layer=NexusModule.LAYER_HUMAN,
            description='Banco original importado, independiente de módulos históricos.',
        )
        assessment.modules.add(module)
        rules = {}
        for index, original in enumerate(mode_questions(bank, mode)):
            question = Question.objects.create(
                module=module, text=original['text'], order=index,
                question_type=Question.TYPE_LIKERT if original['type'] == 'likert' else Question.TYPE_MULTIPLE,
            )
            AnswerOption.objects.bulk_create([
                AnswerOption(question=question, text=label, value=value, order=order)
                for order, (value, label) in enumerate(option_labels(family, original))
            ])
            rules[str(question.pk)] = {'source_key': [original['dimKey'], original['id']]}
        version = NexusVersion.objects.create(
            code=code, version=number, family=family_object, assessment=assessment, mode=mode,
            title=title, editor=editor,
            purpose='Autoconocimiento y reflexión profesional a partir del cuestionario original.',
            audience='Personas usuarias de Nexus con acceso a esta modalidad.',
            instructions='Lee cada pregunta y su contexto. Elige una sola respuesta según tu conducta habitual. '
                         'Puedes guardar y continuar más tarde. Revisa antes de entregar.',
            authorship='Originales aportados al repositorio; no se atribuye autoría externa no acreditada.',
            provenance=json.dumps(bank['sources'], ensure_ascii=False),
            authorization=authorization,
            foundation='Reglas del original portadas a Python, con las correcciones documentadas en limitaciones.',
            limitations=LIMITATIONS, reviewed_on=date.today() if editor else None,
            change_reason='Importación reproducible del banco original; sin ejecución de HTML subido.',
            definition={'method': METHOD, 'original_family': family, 'source_digest': digest, 'questions': rules},
        )
    if publish and not version.published_at:
        version.editor = editor
        version.authorization = authorization
        version.reviewed_on = date.today()
        version.change_reason = 'Publicación explícita de originales importados y correcciones autorizadas.'
        version.status = NexusVersion.Status.PUBLISHED
        version.save(editorial_actor=editor)
    return version
