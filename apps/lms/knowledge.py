import csv
import html
import io

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import AuditEvent, Question, QuestionBank, QuestionVersion
from .policies import can_edit_course


CSV_COLUMNS = ('Pregunta', 'OpcionA', 'OpcionB', 'OpcionC', 'OpcionD', 'RespuestaCorrecta')
MAX_CSV_BYTES = 1024 * 1024
MAX_CSV_ROWS = 1000


class PublishQuestionsForm(forms.Form):
    questions = forms.ModelMultipleChoiceField(queryset=Question.objects.none(), required=True)

    def __init__(self, *args, bank, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['questions'].queryset = bank.questions.filter(status=Question.Status.DRAFT)


@transaction.atomic
def publish_bank_questions(*, bank, actor, question_ids):
    if not can_edit_course(actor, bank.course):
        raise PermissionDenied
    bank = QuestionBank.objects.select_for_update().get(pk=bank.pk)
    if bank.status == QuestionBank.Status.ARCHIVED:
        raise ValidationError(_('No se pueden publicar preguntas de un banco archivado.'))
    form = PublishQuestionsForm({'questions': question_ids}, bank=bank)
    if not form.is_valid():
        raise ValidationError(_('Selecciona preguntas en borrador de este banco. Ninguna pregunta se ha publicado.'))
    questions = list(bank.questions.select_for_update().filter(pk__in=question_ids).order_by('pk'))
    for question in questions:
        if question.status != Question.Status.DRAFT:
            raise ValidationError(_('Alguna pregunta ya no está en borrador. Revisa la selección.'))
        if question.current_version and question.current_version.metadata.get('csv_review'):
            raise ValidationError(_('Corrige las preguntas marcadas Revisar antes de publicarlas. Ninguna pregunta se ha publicado.'))
        data = question_initial(question)
        data['status'] = Question.Status.PUBLISHED
        save_bank_question(bank=bank, actor=actor, data=data, question=question)
    return len(questions)


class BankForm(forms.ModelForm):
    class Meta:
        model = QuestionBank
        fields = ['title', 'description', 'status']
        labels = {'title': _('Nombre'), 'description': _('Descripción'), 'status': _('Estado')}


class BankQuestionForm(forms.Form):
    prompt = forms.CharField(label=_('Enunciado'), max_length=10000, widget=forms.Textarea(attrs={'rows': 4}))
    option_a = forms.CharField(label=_('Respuesta A'), max_length=2000)
    option_b = forms.CharField(label=_('Respuesta B'), max_length=2000)
    option_c = forms.CharField(label=_('Respuesta C'), max_length=2000)
    option_d = forms.CharField(label=_('Respuesta D'), max_length=2000)
    correct = forms.ChoiceField(label=_('Respuesta correcta'), choices=[(letter, letter) for letter in 'ABCD'])
    explanation = forms.CharField(label=_('Explicación'), required=False, max_length=10000, widget=forms.Textarea)
    difficulty = forms.TypedChoiceField(label=_('Dificultad'), coerce=int, choices=[(1, _('Básica')), (2, _('Media')), (3, _('Avanzada'))], initial=1)
    points = forms.IntegerField(label=_('Puntos'), min_value=1, max_value=1000, initial=1)
    status = forms.ChoiceField(label=_('Estado'), choices=Question.Status.choices, initial=Question.Status.DRAFT)

    def clean(self):
        data = super().clean()
        options = [data.get('option_' + letter.lower(), '') for letter in 'ABCD']
        if all(options) and len({option.casefold() for option in options}) != 4:
            raise forms.ValidationError(_('Las cuatro respuestas deben ser diferentes.'))
        return data


class CsvQuestionForm(forms.Form):
    file = forms.FileField(label=_('Archivo CSV'))

    def clean_file(self):
        upload = self.cleaned_data['file']
        if not upload.name.lower().endswith('.csv'):
            raise forms.ValidationError(_('Selecciona un archivo .csv.'))
        if upload.size > MAX_CSV_BYTES:
            raise forms.ValidationError(_('El CSV no puede superar 1 MB.'))
        return upload


class BankCreateForm(BankForm):
    file = forms.FileField(
        label=_('Preguntas y respuestas (CSV)'), required=False,
        widget=forms.ClearableFileInput(attrs={'accept': '.csv,text/csv'}),
        help_text=_('Opcional. UTF-8, hasta 1 MB y 1000 preguntas. Después de crear el banco podrás revisar y confirmar la importación.'),
    )

    def clean_file(self):
        upload = self.cleaned_data.get('file')
        if not upload:
            return None
        self.csv_rows = []
        self.csv_errors = []
        csv_form = CsvQuestionForm(files={'file': upload})
        if not csv_form.is_valid():
            self.csv_errors = [str(error) for error in csv_form.errors['file']]
        else:
            try:
                self.csv_rows = parse_question_csv(upload)
            except ValidationError as error:
                self.csv_errors = error.messages
        return upload

    def clean(self):
        data = super().clean()
        if data.get('file') and data.get('status') == QuestionBank.Status.ARCHIVED:
            self.add_error('status', _('Para importar preguntas, crea el banco en borrador o publicado, no archivado.'))
        return data


def question_initial(question):
    version = question.current_version
    if question.question_type != Question.Type.SINGLE_CHOICE or not version:
        raise ValidationError(_('Esta pregunta no tiene un formato de cuatro opciones editable en esta fase.'))
    return {
        **question_version_initial(version),
        'difficulty': question.difficulty, 'points': question.points, 'status': question.status,
    }


def question_version_initial(version):
    if version.metadata.get('csv_review'):
        return {
            **version.metadata['csv_review']['data'],
            'csv_review': version.metadata['csv_review'],
            'explanation': version.explanation,
        }
    options = version.options
    if (not isinstance(options, list) or len(options) != 4
            or not all(isinstance(item, str) and item.strip() for item in options)
            or len({item.strip().casefold() for item in options}) != 4):
        raise ValidationError(_('Esta pregunta no tiene cuatro opciones de texto.'))
    answers = version.correct_answers
    if not isinstance(answers, list) or len(answers) != 1 or answers[0] not in options:
        raise ValidationError(_('La pregunta no tiene una única respuesta correcta válida.'))
    return {
        'prompt': html.unescape(version.prompt) if version.metadata.get('plain_text') else version.prompt,
        **{'option_' + letter.lower(): value for letter, value in zip('ABCD', options)},
        'correct': 'ABCD'[options.index(answers[0])], 'explanation': version.explanation,
    }


def parse_question_csv(upload):
    try:
        text = upload.read(MAX_CSV_BYTES + 1).decode('utf-8-sig')
    except UnicodeDecodeError as error:
        raise ValidationError(_('El CSV debe estar codificado en UTF-8.')) from error
    if len(text.encode('utf-8')) > MAX_CSV_BYTES:
        raise ValidationError(_('El CSV no puede superar 1 MB.'))
    delimiter = ';' if ';' in text.partition('\n')[0] else ','
    reader = csv.DictReader(io.StringIO(text, newline=''), delimiter=delimiter, strict=True)
    rows = []
    try:
        if reader.fieldnames != list(CSV_COLUMNS):
            raise ValidationError(_('La cabecera debe ser: %(columns)s') % {'columns': ';'.join(CSV_COLUMNS)})
        for number, row in enumerate(reader, start=2):
            if number > MAX_CSV_ROWS + 1:
                raise ValidationError(_('Importa como máximo 1000 preguntas por archivo.'))
            data = {
                'prompt': row['Pregunta'] or '',
                **{'option_' + letter.lower(): row['Opcion' + letter] or '' for letter in 'ABCD'},
                'correct': (row['RespuestaCorrecta'] or '').strip().upper(),
                'points': 1, 'difficulty': 1, 'status': Question.Status.DRAFT,
            }
            form = BankQuestionForm(data)
            form.is_valid()
            errors = [str(error) for values in form.errors.values() for error in values]
            if None in row or any(value is None for value in row.values()):
                errors.append(str(_('Número de columnas incorrecto.')))
            if errors:
                data['csv_review'] = {
                    'row': number, 'errors': errors, 'data': data.copy(),
                    'raw_row': {key: value for key, value in row.items() if key is not None},
                    'extra_columns': row.get(None, []),
                }
                rows.append(data)
            else:
                rows.append(form.cleaned_data)
    except csv.Error as error:
        raise ValidationError(_('CSV mal formado: %(error)s') % {'error': str(error)}) from error
    if not rows:
        raise ValidationError(_('El CSV no contiene preguntas.'))
    return rows


@transaction.atomic
def save_bank_question(*, bank, actor, data, question=None):
    if not can_edit_course(actor, bank.course):
        raise PermissionDenied
    if bank.status == QuestionBank.Status.ARCHIVED:
        raise ValidationError(_('No se pueden modificar preguntas de un banco archivado.'))
    review = data.get('csv_review')
    if review:
        if data['status'] != Question.Status.DRAFT:
            raise ValidationError(_('Corrige la pregunta marcada Revisar antes de publicarla.'))
    else:
        form = BankQuestionForm(data)
        if not form.is_valid():
            raise ValidationError([str(error) for values in form.errors.values() for error in values])
        data = form.cleaned_data
    if question:
        question = Question.objects.select_for_update().get(pk=question.pk, bank=bank)
    else:
        question = Question(bank=bank, created_by=actor)
    question.title = data['prompt'][:200]
    question.question_type = Question.Type.SINGLE_CHOICE
    question.difficulty = data['difficulty']
    question.points = data['points']
    question.status = data['status']
    question.full_clean(exclude=['current_version', 'tags'])
    question.save()
    options = [data['option_' + letter.lower()] for letter in 'ABCD']
    version = QuestionVersion(
        question=question,
        version_number=(question.versions.order_by('-version_number').values_list('version_number', flat=True).first() or 0) + 1,
        prompt=html.escape(data['prompt']), options=options,
        correct_answers=[options['ABCD'.index(data['correct'])]] if data['correct'] in tuple('ABCD') else [],
        explanation=data.get('explanation', ''), created_by=actor,
        is_published=question.status == Question.Status.PUBLISHED,
        published_at=timezone.now() if question.status == Question.Status.PUBLISHED else None,
        metadata={'plain_text': True, 'points': question.points, 'difficulty': question.difficulty},
    )
    if review:
        version.metadata['csv_review'] = review
    version.full_clean(exclude=['prompt'] if review else None)
    version.save()
    question.current_version = version
    question.save(update_fields=['current_version', 'updated_at'])
    AuditEvent.objects.create(actor=actor, course=bank.course, action=AuditEvent.Action.COURSE_UPDATED,
                              metadata={'knowledge_action': 'question_saved', 'question_id': question.pk, 'version_id': version.pk})
    return question
