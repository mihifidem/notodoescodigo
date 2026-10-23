import random
import math
from datetime import timezone as datetime_timezone
from decimal import Decimal

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .knowledge import question_initial, question_version_initial
from .models import AuditEvent, Question, QuestionBank, Quiz, QuizAttempt, QuizQuestion, Section
from .policies import can_edit_course


def available_questions(course):
    return Question.objects.filter(
        bank__course=course, bank__status=QuestionBank.Status.PUBLISHED,
        status=Question.Status.PUBLISHED, question_type=Question.Type.SINGLE_CHOICE,
        current_version__is_published=True,
    ).select_related('bank', 'current_version').order_by('bank__title', 'pk')


class BankQuestionCheckboxes(forms.CheckboxSelectMultiple):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        if hasattr(value, 'instance'):
            option['attrs']['data-bank-id'] = value.instance.bank_id
        return option


class QuizConfigurationForm(forms.ModelForm):
    reference_code = forms.CharField(
        label=_('Código de referencia'), max_length=64,
        help_text=_('Obligatorio y único en toda la plataforma.'),
    )
    penalize_errors = forms.BooleanField(
        label=_('Descontar por fallo'), required=False,
        help_text=_('La penalización de respuestas en blanco se configura por separado.'),
    )
    error_deduction = forms.DecimalField(
        label=_('Cantidad a descontar por fallo (sobre 10)'), required=False,
        min_value=Decimal('0.0001'), max_value=Decimal('10'), max_digits=6, decimal_places=4,
        help_text=_('Obligatorio si activas Descontar por fallo. Se resta esta cantidad por cada respuesta errónea.'),
    )
    blank_deduction = forms.DecimalField(
        label=_('Cantidad a descontar por respuesta en blanco (sobre 10)'), required=False,
        min_value=Decimal('0'), max_value=Decimal('10'), max_digits=6, decimal_places=4,
        initial=Decimal('0'),
        help_text=_('Se descuenta por cada pregunta sin responder. 0 significa sin penalización.'),
    )
    new_section_title = forms.CharField(
        label=_('Añadir aula/clase'), required=False, max_length=160,
        help_text=_('Para crear un aula/clase del curso, escribe su nombre y deja vacío el selector de aula/clase existente. Se creará al guardar la prueba.'),
    )
    selection = forms.ChoiceField(
        label=_('Selección de preguntas'), choices=[('', _('Elige el tipo de selección')), ('manual', _('Manual')), ('random', _('Aleatoria del banco'))],
        help_text=_('La selección aleatoria se realiza al guardar y queda fija para esta prueba.'),
    )
    selected_questions = forms.ModelMultipleChoiceField(
        label=_('Preguntas publicadas'), queryset=Question.objects.none(), required=False,
        widget=BankQuestionCheckboxes,
    )
    bank = forms.ModelChoiceField(label=_('Banco de preguntas'), queryset=QuestionBank.objects.none())
    difficulty = forms.TypedChoiceField(
        label=_('Dificultad para selección aleatoria'), required=False, coerce=int, empty_value=None,
        choices=[('', _('Todas')), *Question.DIFFICULTY_CHOICES],
    )
    question_count = forms.IntegerField(
        label=_('Número total de preguntas'), min_value=1, max_value=1000,
        help_text=_('En selección manual debes marcar exactamente esta cantidad de preguntas del banco elegido.'),
    )

    class Meta:
        model = Quiz
        fields = [
            'title', 'reference_code', 'description', 'section', 'available_from', 'available_until', 'due_at',
            'duration_minutes', 'max_attempts', 'allow_resume', 'shuffle_questions',
            'passing_score', 'publish_answers_at', 'publish_grade_at',
        ]
        labels = {'title': _('Título'), 'description': _('Instrucciones'), 'passing_score': _('Puntos mínimos para superar')}
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            **{name: forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'})
               for name in ('available_from', 'available_until', 'due_at', 'publish_answers_at', 'publish_grade_at')},
        }

    def __init__(self, *args, course, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.course = course
        self.fields['section'].queryset = Section.objects.filter(course=course)
        self.fields['section'].label = _('Aula/clase')
        self.fields['section'].required = False
        self.fields['section'].help_text = _(
            'Opcional. Selecciona un grupo organizado por el profesor. '
            'Si lo dejas vacío, la prueba es para todos los alumnos del curso, tengan clase o no.'
        )
        self.fields['selected_questions'].queryset = available_questions(course)
        self.fields['bank'].queryset = QuestionBank.objects.filter(course=course, status=QuestionBank.Status.PUBLISHED)
        self.fields['max_attempts'].min_value = 1
        self.fields['max_attempts'].validators.append(MinValueValidator(1))
        self.fields['passing_score'].min_value = Decimal('0')
        self.fields['passing_score'].max_value = Decimal('10')
        self.fields['passing_score'].label = _('Nota mínima para aprobar (sobre 10)')
        self.fields['passing_score'].validators.append(
            MaxValueValidator(Decimal('10'))
        )
        self.fields['duration_minutes'].required = False
        self.fields['duration_minutes'].help_text = _(
            'Se calcula automáticamente entre Disponible desde y Fecha de entrega. '
            'Sin ambas fechas, indica los minutos manualmente (0 sin límite).'
        )
        self.order_fields(['title', 'reference_code', 'description', 'section', 'new_section_title', 'bank', 'question_count', 'selection'])
        if not self.instance.pk:
            self.initial['reference_code'] = ''
        if self.instance.pk:
            self.initial['selection'] = 'manual'
            self.initial['penalize_errors'] = self.instance.questions.filter(metadata__penalize_errors=True).exists()
            metadata = self.instance.questions.values_list('metadata', flat=True).first() or {}
            self.initial['error_deduction'] = metadata.get('error_deduction')
            self.initial['blank_deduction'] = metadata.get('blank_deduction', '0')
            self.initial['selected_questions'] = list(
                self.instance.questions.values_list('question_version__question_id', flat=True)
            )
            banks = list(self.instance.questions.values_list('question_version__question__bank_id', flat=True).distinct())
            if len(set(banks)) == 1:
                self.initial['bank'] = banks[0]
            self.initial['question_count'] = len(self.initial['selected_questions'])

    def clean_duration_minutes(self):
        start = self.cleaned_data.get('available_from')
        due = self.cleaned_data.get('due_at')
        if start and due and due > start:
            return math.ceil((due.astimezone(datetime_timezone.utc) - start.astimezone(datetime_timezone.utc)).total_seconds() / 60)
        duration = self.cleaned_data.get('duration_minutes')
        if duration is None:
            raise forms.ValidationError(_('Indica la duración o completa las fechas de apertura y entrega.'))
        return duration

    def clean(self):
        data = super().clean()
        if data.get('penalize_errors') and data.get('error_deduction') is None:
            self.add_error('error_deduction', _('Indica la cantidad a descontar por fallo.'))
        new_section_title = data.get('new_section_title')
        if new_section_title:
            if data.get('section'):
                self.add_error('new_section_title', _('Elige un aula/clase existente o crea una nueva, no ambas.'))
            elif Section.objects.filter(course=self.instance.course, title=new_section_title).exists():
                self.add_error('new_section_title', _('Ya existe un aula/clase con este nombre en el curso. Selecciónala en el listado.'))
        start, end, due = (data.get(name) for name in ('available_from', 'available_until', 'due_at'))
        if start and end and end <= start:
            self.add_error('available_until', _('El cierre debe ser posterior a la apertura.'))
        if due and ((start and due <= start) or (end and due > end)):
            self.add_error('due_at', _('La entrega debe estar dentro del periodo disponible.'))
        if data.get('passing_score') is not None and data['passing_score'] < 0:
            self.add_error('passing_score', _('La nota mínima no puede ser negativa.'))
        for name in ('publish_answers_at', 'publish_grade_at'):
            if data.get(name) and (end or due) and data[name] < (end or due):
                self.add_error(name, _('La publicación de resultados no puede ser anterior al cierre o entrega.'))
        if data.get('selection') == 'manual' and not data.get('selected_questions'):
            self.add_error('selected_questions', _('Selecciona al menos una pregunta.'))
        if data.get('selection') == 'manual' and data.get('selected_questions'):
            questions = data['selected_questions']
            if data.get('bank') and any(question.bank_id != data['bank'].pk for question in questions):
                self.add_error('selected_questions', _('Todas las preguntas deben pertenecer al banco seleccionado.'))
            if data.get('question_count') and len(questions) != data['question_count']:
                self.add_error('selected_questions', _('Selecciona exactamente %(count)s preguntas.') % {'count': data['question_count']})
        if data.get('selection') == 'random':
            if not data.get('bank'):
                self.add_error('bank', _('Selecciona un banco.'))
            if not data.get('question_count'):
                self.add_error('question_count', _('Indica el número de preguntas.'))
        return data


def check_editable(quiz, *, allow_published=False):
    statuses = (Quiz.Status.DRAFT, Quiz.Status.PUBLISHED) if allow_published else (Quiz.Status.DRAFT,)
    if quiz.status not in statuses or quiz.attempts.exists():
        if allow_published:
            raise ValidationError(_('Solo se pueden editar borradores o pruebas publicadas sin intentos.'))
        raise ValidationError(_('Solo se pueden editar borradores sin intentos. Las pruebas publicadas conservan su configuración.'))


def record_action(quiz, actor, action):
    AuditEvent.objects.create(
        actor=actor, course=quiz.course, action=AuditEvent.Action.COURSE_UPDATED,
        metadata={'quiz_action': action, 'quiz_id': quiz.pk},
    )


class QuizPassingScoreForm(forms.Form):
    passing_score = forms.DecimalField(
        label=_('Nota mínima para aprobar'), min_value=Decimal('0'),
        max_digits=8, decimal_places=2,
    )

    def __init__(self, *args, quiz, **kwargs):
        super().__init__(*args, **kwargs)
        items = list(quiz.questions.all())
        maximum = Decimal('10') if items and all(
            item.metadata.get('grading_scale') == 10 for item in items
        ) else sum((item.points for item in items), Decimal('0'))
        self.fields['passing_score'].max_value = maximum
        self.fields['passing_score'].validators.append(MaxValueValidator(maximum))
        self.fields['passing_score'].help_text = _('Valor entre 0 y %(maximum)s.') % {'maximum': maximum}


@transaction.atomic
def update_quiz_passing_score(*, course, actor, quiz_id, passing_score):
    if not can_edit_course(actor, course):
        raise PermissionDenied
    quiz = Quiz.objects.select_for_update().get(pk=quiz_id, course=course)
    form = QuizPassingScoreForm({'passing_score': passing_score}, quiz=quiz)
    if not form.is_valid():
        raise ValidationError(form.errors['passing_score'])
    threshold = form.cleaned_data['passing_score']
    previous = quiz.passing_score
    if previous == threshold:
        return quiz
    quiz.passing_score = threshold
    quiz.save(update_fields=['passing_score', 'updated_at'])
    count = 0
    for attempt in quiz.attempts.select_for_update().all():
        attempt.configuration_snapshot = {**attempt.configuration_snapshot, 'passing_score': str(threshold)}
        fields = ['configuration_snapshot', 'updated_at']
        if attempt.status == QuizAttempt.Status.GRADED:
            attempt.is_passed = attempt.score >= threshold
            fields.append('is_passed')
            count += 1
        attempt.save(update_fields=fields)
    AuditEvent.objects.create(
        actor=actor, course=course, action=AuditEvent.Action.COURSE_UPDATED,
        metadata={
            'quiz_action': 'passing_score_updated', 'quiz_id': quiz.pk,
            'previous': str(previous), 'passing_score': str(threshold),
            'updated_results': count,
        },
    )
    from .consumers import broadcast_course_update
    transaction.on_commit(lambda: broadcast_course_update(course, 'updated', item_type='lifecycle'))
    return quiz


@transaction.atomic
def delete_quiz(*, course, actor, quiz_id):
    if not can_edit_course(actor, course):
        raise PermissionDenied
    quiz = Quiz.objects.select_for_update().get(pk=quiz_id, course=course)
    if quiz.attempts.exists():
        raise ValidationError(_('No se puede eliminar una prueba con intentos. Ciérrala para conservar el historial.'))
    metadata = {'quiz_action': 'deleted', 'quiz_id': quiz.pk, 'title': quiz.title}
    quiz.delete()
    AuditEvent.objects.create(
        actor=actor, course=course, action=AuditEvent.Action.COURSE_UPDATED, metadata=metadata,
    )


@transaction.atomic
def save_quiz_configuration(*, course, actor, form, quiz=None):
    if not can_edit_course(actor, course):
        raise PermissionDenied
    if not form.is_valid():
        raise ValidationError(_('La configuración contiene errores.'))
    if quiz:
        quiz = Quiz.objects.select_for_update().get(pk=quiz.pk, course=course)
        check_editable(quiz, allow_published=True)
    else:
        quiz = Quiz(course=course, created_by=actor)
    data = form.cleaned_data
    pool = available_questions(course).select_for_update(of=('self',))
    if data['selection'] == 'random':
        pool = pool.filter(bank=data['bank'])
        if data.get('difficulty'):
            pool = pool.filter(difficulty=data['difficulty'])
        questions = list(pool)
        if len(questions) < data['question_count']:
            raise ValidationError(_('No hay suficientes preguntas publicadas para los filtros seleccionados.'))
        questions = random.SystemRandom().sample(questions, data['question_count'])
    else:
        ids = [question.pk for question in data['selected_questions']]
        by_id = {question.pk: question for question in pool.filter(bank=data['bank'], pk__in=ids)}
        if len(by_id) != len(ids):
            raise ValidationError(_('Alguna pregunta ya no está disponible. Revisa la selección.'))
        questions = [by_id[pk] for pk in ids]
    for question in questions:
        question_initial(question)
    maximum = 10
    if data['passing_score'] > maximum:
        raise ValidationError(_('La nota mínima debe estar entre 0 y 10.'))
    for name in QuizConfigurationForm.Meta.fields:
        setattr(quiz, name, data[name])
    if data.get('new_section_title'):
        section = Section(course=course, title=data['new_section_title'])
        section.full_clean()
        section.save()
        quiz.section = section
    quiz.full_clean()
    quiz.save()
    quiz.questions.all().delete()
    for order, question in enumerate(questions, start=1):
        QuizQuestion.objects.create(
            quiz=quiz, question_version=question.current_version, order=order, points=question.points,
            metadata={'question_type': question.question_type, 'penalty': str(question.penalty),
                      'difficulty': question.difficulty, 'grading_scale': 10,
                      'penalize_errors': data['penalize_errors'],
                      'blank_deduction': str(data['blank_deduction'] or Decimal('0')),
                      'error_deduction': str(data['error_deduction']) if data['penalize_errors'] else '0'},
        )
    record_action(quiz, actor, 'saved')
    from .lifecycle import notify_once, teacher_recipients
    from django.urls import reverse
    notify_once(
        course=course, key=f'quiz:{quiz.pk}:created', title=_('Prueba creada'),
        message=quiz.title, recipients=teacher_recipients(course),
        url=reverse('lms:quiz_detail', kwargs={'course_id': course.pk, 'quiz_id': quiz.pk}),
    )
    return quiz


class QuizReopenForm(forms.Form):
    grace_until = forms.DateTimeField(
        label=_('Fin del periodo de gracia'),
        widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}),
        help_text=_('La prueba se abrirá ahora y cerrará en esta fecha y hora.'),
    )

    def clean_grace_until(self):
        end = self.cleaned_data['grace_until']
        if end <= timezone.now():
            raise forms.ValidationError(_('El fin del periodo de gracia debe ser futuro.'))
        return end


@transaction.atomic
def reopen_quiz(*, course, actor, quiz_id, grace_until, now=None):
    from .lifecycle import course_end, notify_once, process_quiz, student_recipients
    from .quiz_status import quiz_is_closed
    from django.urls import reverse

    if not can_edit_course(actor, course):
        raise PermissionDenied
    now = now or timezone.now()
    quiz = Quiz.objects.select_for_update().select_related('course').get(pk=quiz_id, course=course)
    if not quiz_is_closed(quiz, now=now):
        raise ValidationError(_('Solo se pueden reabrir pruebas cerradas.'))
    if quiz.course.status != 'published':
        raise ValidationError(_('El curso debe estar publicado para reabrir una prueba.'))
    end = course_end(quiz.course)
    if grace_until <= now or (end and grace_until > end):
        raise ValidationError(_('El periodo de gracia debe ser futuro y no superar el fin del curso.'))
    if not quiz.questions.exists():
        raise ValidationError(_('La prueba necesita al menos una pregunta.'))
    # Finalize expired work against the original deadline before extending availability.
    quiz.status = Quiz.Status.CLOSED
    quiz.save(update_fields=['status', 'updated_at'])
    process_quiz(quiz.pk, now=now)
    quiz.status = Quiz.Status.PUBLISHED
    quiz.available_from = now
    quiz.available_until = quiz.due_at = grace_until
    quiz.full_clean()
    quiz.save(update_fields=['status', 'available_from', 'available_until', 'due_at', 'updated_at'])
    AuditEvent.objects.create(
        actor=actor, course=course, action=AuditEvent.Action.COURSE_UPDATED,
        metadata={'quiz_action': 'reopened', 'quiz_id': quiz.pk, 'grace_until': grace_until.isoformat()},
    )
    notify_once(
        course=course, key=f'quiz:{quiz.pk}:reopened:{now.isoformat()}',
        title=_('Prueba reabierta'),
        message=_('Se ha reabierto %(quiz)s hasta %(end)s. Se mantiene el máximo de intentos.') % {
            'quiz': quiz.title, 'end': timezone.localtime(grace_until).strftime('%d/%m/%Y %H:%M'),
        },
        recipients=student_recipients(course, section_id=quiz.section_id),
        url=reverse('lms:quiz_detail', kwargs={'course_id': course.pk, 'quiz_id': quiz.pk}),
    )
    return quiz


@transaction.atomic
def change_quiz_status(*, course, actor, quiz_id, action):
    if not can_edit_course(actor, course):
        raise PermissionDenied
    quiz = Quiz.objects.select_for_update().get(pk=quiz_id, course=course)
    if action in ('publish', 'schedule'):
        check_editable(quiz)
        if action == 'schedule' and (not quiz.available_from or quiz.available_from <= timezone.now()):
            raise ValidationError(_('Para programar la prueba, indica una apertura futura.'))
        items = list(quiz.questions.select_related('question_version__question__bank'))
        if not items:
            raise ValidationError(_('La prueba necesita al menos una pregunta.'))
        for item in items:
            version = item.question_version
            if (not version.is_published or version.question.status != Question.Status.PUBLISHED
                    or version.question.bank.course_id != course.pk
                    or version.question.bank.status != QuestionBank.Status.PUBLISHED):
                raise ValidationError(_('La prueba contiene preguntas o bancos no publicados.'))
            if version.question.question_type != Question.Type.SINGLE_CHOICE or item.points < 1:
                raise ValidationError(_('La prueba contiene preguntas incompatibles o sin puntos.'))
            question_version_initial(version)
        maximum = 10 if all(item.metadata.get('grading_scale') == 10 for item in items) else sum(item.points for item in items)
        if quiz.max_attempts < 1 or not 0 <= quiz.passing_score <= maximum:
            raise ValidationError(_('Revisa el número de intentos y la puntuación mínima.'))
        quiz.status = Quiz.Status.SCHEDULED if action == 'schedule' else Quiz.Status.PUBLISHED
    elif action == 'cancel_schedule' and quiz.status == Quiz.Status.SCHEDULED:
        if quiz.attempts.exists():
            raise ValidationError(_('No se puede cancelar una programación con intentos.'))
        quiz.status = Quiz.Status.DRAFT
    elif action == 'close' and quiz.status == Quiz.Status.PUBLISHED:
        quiz.status = Quiz.Status.CLOSED
    else:
        raise ValidationError(_('La transición de estado no está permitida.'))
    quiz.full_clean()
    quiz.save(update_fields=['status', 'updated_at'])
    record_action(quiz, actor, action)
    from .lifecycle import process_quiz
    process_quiz(quiz.pk, now=timezone.now())
    return quiz
