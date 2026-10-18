from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models.deletion import RestrictedError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View

from apps.courses.models import Tema
from .forms import FormativeUnitForm, ModuleContentBatchForm, ModuleContentForm, ModuleScheduleForm
from .models import AuditEvent, FormativeUnit, ModuleContent, ModuleContentProgress, ModuleSchedule
from .module_progression import content_lock_reason
from .policies import can_edit_course, is_course_member
from .views import CourseContextMixin


class ModuleContentsView(CourseContextMixin, View):
    def handle(self, request):
        course = self.get_course()
        if not can_edit_course(request.user, course):
            raise PermissionDenied
        module = get_object_or_404(Tema, curso=course, pk=self.kwargs['module_id'])
        unit = None
        if self.kwargs.get('unit_id'):
            unit = get_object_or_404(FormativeUnit, module=module, pk=self.kwargs['unit_id'])
        schedule = ModuleSchedule.objects.filter(module=module).first()
        content = None
        if request.GET.get('edit'):
            content = get_object_or_404(ModuleContent, module=module, pk=request.GET['edit'])
            if unit and content.unit_id != unit.pk:
                raise PermissionDenied
        action = request.POST.get('action') if request.method == 'POST' else None
        panel = (
            request.GET.get('panel') in ('content', 'schedule')
            and request.headers.get('x-requested-with') == 'XMLHttpRequest'
        )
        schedule_form = ModuleScheduleForm(request.POST if action == 'schedule' else None, instance=schedule)
        content_data = request.POST.copy() if action == 'content' else None
        if content_data is not None and unit and not content_data.get('unit'):
            content_data['unit'] = unit.pk
        content_form = (
            ModuleContentForm(content_data, module=module, instance=content)
            if content else ModuleContentBatchForm(content_data, module=module, initial={'unit': unit.pk} if unit else None)
        )
        if action in ('schedule', 'content'):
            form = schedule_form if action == 'schedule' else content_form
            if form.is_valid():
                with transaction.atomic():
                    if action == 'content':
                        form.save()
                    else:
                        obj = form.save(commit=False)
                        obj.module = module
                        obj.save()
                    AuditEvent.objects.create(actor=request.user, course=course, action=AuditEvent.Action.COURSE_UPDATED)
                messages.success(request, _('La configuración del módulo se ha guardado.'))
                if panel and request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({'ok': True})
                return redirect(request.path)
        elif action == 'remove':
            item = get_object_or_404(ModuleContent, module=module, pk=request.POST.get('content_id'))
            if unit and item.unit_id != unit.pk:
                raise PermissionDenied
            try:
                item.delete()
            except RestrictedError:
                messages.error(request, _('El contenido es un prerrequisito. Cambia primero los contenidos que dependen de él.'))
            else:
                AuditEvent.objects.create(actor=request.user, course=course, action=AuditEvent.Action.COURSE_UPDATED)
                messages.success(request, _('Contenido retirado del módulo; el recurso original se conserva.'))
            return redirect(request.path)
        elif request.method == 'POST':
            raise PermissionDenied(_('Acción del módulo no válida.'))
        return render(request, 'lms/module_content_panel.html' if panel else 'lms/module_contents.html', {
            **self.get_course_context(course), 'area_key': 'content', 'module': module,
            'unit': unit, 'units': module.formative_units.all(),
            'schedule_form': schedule_form, 'content_form': content_form, 'editing_content': content,
            'panel_kind': request.GET.get('panel'),
            'contents': module.learning_contents.select_related('unit', 'page', 'file', 'quiz', 'activity', 'prerequisite').filter(
                **({'unit': unit} if unit else {}),
            ),
        }, status=400 if panel and request.method == 'POST' else 200)

    def get(self, request, *args, **kwargs):
        return self.handle(request)

    def post(self, request, *args, **kwargs):
        return self.handle(request)


class FormativeUnitView(CourseContextMixin, View):
    def handle(self, request):
        course = self.get_course()
        if not can_edit_course(request.user, course):
            raise PermissionDenied
        module = get_object_or_404(Tema, curso=course, pk=self.kwargs['module_id'])
        unit = (get_object_or_404(FormativeUnit, module=module, pk=self.kwargs['unit_id'])
                if self.kwargs.get('unit_id') else None)
        panel = request.headers.get('x-requested-with') == 'XMLHttpRequest'
        form = FormativeUnitForm(request.POST if request.method == 'POST' else None, module=module, instance=unit)
        if request.method == 'POST':
            if request.POST.get('action') == 'delete':
                if unit is None:
                    raise PermissionDenied
                try:
                    with transaction.atomic():
                        unit.delete()
                        AuditEvent.objects.create(actor=request.user, course=course, action=AuditEvent.Action.COURSE_UPDATED)
                except RestrictedError:
                    messages.error(request, _('La unidad tiene recursos. Muévelos a otra unidad o retíralos antes de eliminarla.'))
                else:
                    messages.success(request, _('Unidad formativa eliminada.'))
                return redirect('lms:module_contents', course_id=course.pk, module_id=module.pk)
            if form.is_valid():
                try:
                    with transaction.atomic():
                        form.save()
                        AuditEvent.objects.create(actor=request.user, course=course, action=AuditEvent.Action.COURSE_UPDATED)
                except IntegrityError:
                    form.add_error(None, _('No se pudo guardar la unidad. Comprueba si ya existe otra con el mismo nombre.'))
                else:
                    messages.success(request, _('Unidad formativa guardada.'))
                    if panel:
                        return JsonResponse({'ok': True})
                    return redirect('lms:course_area', course_id=course.pk, area='content')
        return render(request, 'lms/formative_unit_panel.html' if panel else 'lms/formative_unit_form.html', {
            **self.get_course_context(course), 'area_key': 'content',
            'module': module, 'unit': unit, 'form': form,
        }, status=400 if request.method == 'POST' else 200)

    def get(self, request, *args, **kwargs):
        return self.handle(request)

    def post(self, request, *args, **kwargs):
        return self.handle(request)


class ModuleContentCompleteView(CourseContextMixin, View):
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        course = self.get_course()
        item = get_object_or_404(ModuleContent, pk=self.kwargs['content_id'], module__curso=course)
        reason = content_lock_reason(item, request.user)
        if reason:
            raise PermissionDenied(reason)
        if not is_course_member(request.user, course) or can_edit_course(request.user, course) or item.criterion != ModuleContent.Criterion.MANUAL:
            raise PermissionDenied(_('Este contenido no admite finalización manual para este usuario.'))
        progress = ModuleContentProgress.objects.filter(content=item, user=request.user, opened_at__isnull=False).first()
        if not progress:
            messages.error(request, _('Abre el contenido antes de marcarlo como completado.'))
        else:
            if not progress.completed_at:
                progress.completed_at = timezone.now()
                progress.save(update_fields=['completed_at'])
            messages.success(request, _('Contenido completado.'))
        return redirect('lms:course_area', course_id=course.pk, area='content')
