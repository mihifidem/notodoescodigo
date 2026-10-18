import csv
import html

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View

from .knowledge import BankCreateForm, BankForm, BankQuestionForm, CSV_COLUMNS, CsvQuestionForm, parse_question_csv, publish_bank_questions, question_initial, save_bank_question
from .models import AuditEvent, Question, QuestionBank
from .policies import can_edit_course
from .views import CourseContextMixin, CoursePageMixin


class KnowledgeMixin(CourseContextMixin, CoursePageMixin):
    def get_bank(self):
        self.course = self.get_course()
        if not can_edit_course(self.request.user, self.course):
            raise PermissionDenied
        return get_object_or_404(QuestionBank, pk=self.kwargs['bank_id'], course=self.course)

    def context(self, **extra):
        return {**self.get_course_context(self.course), 'area_key': 'item-bank', **extra}

    def set_import_preview(self, bank, rows):
        self.request.session['knowledge_preview'] = {
            'bank_id': bank.pk, 'actor_id': self.request.user.pk,
            'created_at': timezone.now().timestamp(), 'rows': rows,
        }

    def get_import_preview(self, bank):
        preview = self.request.session.get('knowledge_preview', {})
        if (preview.get('bank_id') == bank.pk and preview.get('actor_id') == self.request.user.pk
                and preview.get('rows') and 0 <= timezone.now().timestamp() - preview.get('created_at', 0) <= 1800):
            return preview
        return None


class BankEditView(KnowledgeMixin, View):
    def dispatch(self, request, *args, **kwargs):
        self.course = self.get_course()
        if not can_edit_course(request.user, self.course):
            raise PermissionDenied
        self.bank = self.get_bank() if 'bank_id' in kwargs else None
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        return render(request, 'lms/knowledge_form.html', self.context(
            form=BankForm(instance=self.bank) if self.bank else BankCreateForm(),
            heading=_('Editar banco') if self.bank else _('Crear banco'),
        ))

    def post(self, request, *args, **kwargs):
        form = (BankForm(request.POST, instance=self.bank) if self.bank
                else BankCreateForm(request.POST, request.FILES))
        if form.is_valid():
            with transaction.atomic():
                bank = form.save(commit=False)
                bank.course = self.course
                if not bank.pk:
                    bank.owner = request.user
                if isinstance(form, BankCreateForm):
                    bank.import_errors = getattr(form, 'csv_errors', [])
                bank.save()
                AuditEvent.objects.create(actor=request.user, course=self.course, action=AuditEvent.Action.COURSE_UPDATED,
                                          metadata={'knowledge_action': 'bank_saved', 'bank_id': bank.pk})
            if isinstance(form, BankCreateForm) and form.cleaned_data.get('file'):
                if form.csv_errors:
                    request.session.pop('knowledge_preview', None)
                    messages.warning(request, _('El banco se ha guardado, pero no se ha podido leer el CSV. Revisa los errores indicados.'))
                    return redirect('lms:knowledge_bank', course_id=self.course.pk, bank_id=bank.pk)
                self.set_import_preview(bank, form.csv_rows)
                messages.success(request, _('El banco se ha creado. Revisa el CSV y confirma la importación de preguntas.'))
                return redirect('lms:knowledge_import', course_id=self.course.pk, bank_id=bank.pk)
            return redirect('lms:knowledge_bank', course_id=self.course.pk, bank_id=bank.pk)
        return render(request, 'lms/knowledge_form.html', self.context(form=form, heading=_('Guardar banco')))


class BankDetailView(KnowledgeMixin, View):
    def get(self, request, *args, **kwargs):
        bank = self.get_bank()
        questions = bank.questions.select_related('current_version')
        search = request.GET.get('q', '').strip()
        if search:
            questions = questions.filter(Q(title__icontains=search) | Q(current_version__prompt__icontains=search))
        status = request.GET.get('status', '')
        if status in Question.Status.values:
            questions = questions.filter(status=status)
        difficulty = request.GET.get('difficulty', '')
        if difficulty in ('1', '2', '3'):
            questions = questions.filter(difficulty=int(difficulty))
        for question in questions:
            version = question.current_version
            question.display_prompt = (
                html.unescape(version.prompt) if version and version.metadata.get('plain_text')
                else version.prompt if version else ''
            )
        return render(request, 'lms/knowledge_bank.html', self.context(
            bank=bank, questions=questions, search=search, status=status, difficulty=difficulty,
            statuses=Question.Status.choices,
            review_count=bank.questions.filter(current_version__metadata__has_key='csv_review').count(),
        ))


class BankDeleteView(KnowledgeMixin, View):
    http_method_names = ['get', 'post']

    def get(self, request, *args, **kwargs):
        bank = self.get_bank()
        return render(request, 'lms/knowledge_delete.html', self.context(
            bank=bank, question_count=bank.questions.count(),
        ))

    def post(self, request, *args, **kwargs):
        bank = self.get_bank()
        if request.POST.get('confirm') != 'delete':
            messages.error(request, _('Debes confirmar la eliminación del banco.'))
            return redirect('lms:knowledge_delete', course_id=self.course.pk, bank_id=bank.pk)
        bank_id = bank.pk
        try:
            with transaction.atomic():
                bank = QuestionBank.objects.select_for_update().get(pk=bank_id, course=self.course)
                metadata = {
                    'knowledge_action': 'bank_deleted', 'bank_id': bank_id,
                    'title': bank.title, 'question_count': bank.questions.count(),
                }
                bank.delete()
                AuditEvent.objects.create(
                    actor=request.user, course=self.course,
                    action=AuditEvent.Action.COURSE_UPDATED, metadata=metadata,
                )
        except ProtectedError:
            messages.error(request, _(
                'No se puede eliminar este banco porque contiene preguntas vinculadas a pruebas o registros históricos. '
                'Puedes archivarlo desde Editar banco.'
            ))
            return redirect('lms:knowledge_bank', course_id=self.course.pk, bank_id=bank_id)
        if request.session.get('knowledge_preview', {}).get('bank_id') == bank_id:
            request.session.pop('knowledge_preview', None)
        messages.success(request, _('Se han eliminado el banco y sus preguntas no utilizadas.'))
        return redirect('lms:course_area', course_id=self.course.pk, area='item-bank')


class BankPublishQuestionsView(KnowledgeMixin, View):
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        bank = self.get_bank()
        try:
            count = publish_bank_questions(bank=bank, actor=request.user, question_ids=request.POST.getlist('questions'))
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
        else:
            messages.success(request, _('Se han publicado %(count)s preguntas.') % {'count': count})
            if bank.status != QuestionBank.Status.PUBLISHED:
                messages.info(request, _('Publica también el banco desde Editar banco para utilizar sus preguntas en Pruebas.'))
        return redirect('lms:knowledge_bank', course_id=self.course.pk, bank_id=bank.pk)


class QuestionEditView(KnowledgeMixin, View):
    def get(self, request, *args, **kwargs):
        bank = self.get_bank()
        question = get_object_or_404(bank.questions, pk=kwargs['question_id']) if 'question_id' in kwargs else None
        try:
            initial = question_initial(question) if question else {}
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
            return redirect('lms:knowledge_bank', course_id=self.course.pk, bank_id=bank.pk)
        return render(request, 'lms/knowledge_form.html', self.context(
            bank=bank, form=BankQuestionForm(initial=initial), heading=_('Editar pregunta') if question else _('Crear pregunta'),
            csv_review=question.current_version.metadata.get('csv_review') if question and question.current_version else None,
        ))

    def post(self, request, *args, **kwargs):
        bank = self.get_bank()
        question = get_object_or_404(bank.questions, pk=kwargs['question_id']) if 'question_id' in kwargs else None
        if question:
            try:
                question_initial(question)
            except ValidationError as error:
                messages.error(request, ' '.join(error.messages))
                return redirect('lms:knowledge_bank', course_id=self.course.pk, bank_id=bank.pk)
        form = BankQuestionForm(request.POST)
        if form.is_valid():
            try:
                save_bank_question(bank=bank, actor=request.user, data=form.cleaned_data, question=question)
            except ValidationError as error:
                form.add_error(None, ' '.join(error.messages))
            else:
                messages.success(request, _('La pregunta se ha guardado con una nueva versión.'))
                return redirect('lms:knowledge_bank', course_id=self.course.pk, bank_id=bank.pk)
        return render(request, 'lms/knowledge_form.html', self.context(bank=bank, form=form, heading=_('Guardar pregunta')))


class QuestionActionView(KnowledgeMixin, View):
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        bank = self.get_bank()
        question = get_object_or_404(bank.questions, pk=kwargs['question_id'])
        action = request.POST.get('action')
        try:
            if bank.status == QuestionBank.Status.ARCHIVED:
                raise ValidationError(_('El banco está archivado.'))
            if action == 'duplicate':
                data = question_initial(question)
                data['status'] = Question.Status.DRAFT
                save_bank_question(bank=bank, actor=request.user, data=data)
            elif action == 'archive':
                with transaction.atomic():
                    question.status = Question.Status.ARCHIVED
                    question.save(update_fields=['status', 'updated_at'])
                    AuditEvent.objects.create(actor=request.user, course=self.course, action=AuditEvent.Action.COURSE_UPDATED,
                                              metadata={'knowledge_action': 'question_archived', 'question_id': question.pk})
            else:
                raise ValidationError(_('Acción de pregunta no válida.'))
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
        else:
            messages.success(request, _('La pregunta se ha actualizado.'))
        return redirect('lms:knowledge_bank', course_id=self.course.pk, bank_id=bank.pk)


class BankImportView(KnowledgeMixin, View):
    def get(self, request, *args, **kwargs):
        bank = self.get_bank()
        preview = self.get_import_preview(bank)
        return render(request, 'lms/knowledge_import.html', self.context(
            bank=bank, form=CsvQuestionForm(), preview=preview['rows'] if preview else None,
        ))

    def post(self, request, *args, **kwargs):
        bank = self.get_bank()
        if request.POST.get('action') == 'cancel':
            if self.get_import_preview(bank):
                request.session.pop('knowledge_preview', None)
            messages.info(request, _('Importación cancelada. El banco se conserva sin importar estas preguntas.'))
            return redirect('lms:knowledge_bank', course_id=self.course.pk, bank_id=bank.pk)
        form = CsvQuestionForm(request.POST, request.FILES)
        if request.POST.get('action') == 'confirm':
            preview = self.get_import_preview(bank)
            if not preview:
                form.add_error(None, _('La vista previa ha caducado o no corresponde a este banco. Vuelve a cargar el CSV.'))
            else:
                try:
                    with transaction.atomic():
                        for data in preview['rows']:
                            save_bank_question(bank=bank, actor=request.user, data=data)
                        bank.import_errors = []
                        bank.save(update_fields=['import_errors', 'updated_at'])
                except ValidationError as error:
                    form.add_error(None, ' '.join(error.messages))
                else:
                    request.session.pop('knowledge_preview', None)
                    messages.success(request, _('Se han importado %(count)s preguntas como borradores.') % {'count': len(preview['rows'])})
                    review_count = sum(bool(row.get('csv_review')) for row in preview['rows'])
                    if review_count:
                        messages.warning(request, _(
                            '%(count)s preguntas tienen errores y se han marcado Revisar. Corrígelas antes de publicarlas.'
                        ) % {'count': review_count})
                    return redirect('lms:knowledge_bank', course_id=self.course.pk, bank_id=bank.pk)
        else:
            request.session.pop('knowledge_preview', None)
            if form.is_valid():
                try:
                    rows = parse_question_csv(form.cleaned_data['file'])
                except ValidationError as error:
                    bank.import_errors = error.messages
                    bank.save(update_fields=['import_errors', 'updated_at'])
                    form.add_error('file', error)
                else:
                    self.set_import_preview(bank, rows)
                    return render(request, 'lms/knowledge_import.html', self.context(bank=bank, form=CsvQuestionForm(), preview=rows))
        return render(request, 'lms/knowledge_import.html', self.context(bank=bank, form=form))


class BankExportView(KnowledgeMixin, View):
    def get(self, request, *args, **kwargs):
        bank = self.get_bank()
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="banco-{bank.pk}.csv"'
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        writer.writerow(CSV_COLUMNS)
        for question in bank.questions.select_related('current_version'):
            try:
                data = question_initial(question)
            except ValidationError as error:
                messages.error(request, _('No se puede exportar la pregunta %(id)s: %(error)s') % {'id': question.pk, 'error': ' '.join(error.messages)})
                return redirect('lms:knowledge_bank', course_id=self.course.pk, bank_id=bank.pk)
            values = [data['prompt'], *(data['option_' + letter.lower()] for letter in 'ABCD'), data['correct']]
            writer.writerow(["'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value for value in values])
        return response
