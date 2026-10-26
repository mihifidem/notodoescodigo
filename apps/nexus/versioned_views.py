import uuid

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.cache import patch_cache_control
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from apps.curriculum.services.pdf import build_text_pdf
from apps.users.access import can, effective_plan
from . import versioned_services as service
from .models import AssessmentSession, NexusFamily, NexusModality, NexusVersion


def _access(request):
    if not can(request.user, 'view_registered_content').allowed:
        raise PermissionDenied('Se requiere una cuenta activa.')


def _private(response):
    patch_cache_control(response, private=True, no_store=True, no_cache=True, max_age=0)
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def _session(request, pk, completed=False):
    _access(request)
    queryset = AssessmentSession.objects.filter(candidate=request.user, editorial_version__isnull=False)
    if completed:
        queryset = queryset.filter(status=AssessmentSession.STATUS_COMPLETED)
    return get_object_or_404(queryset.select_related('editorial_version'), pk=pk)


def _error(request, error, status=400):
    return _private(render(request, 'nexus/versioned/error.html', {'error': str(error)}, status=status))


@never_cache
@require_GET
def catalog(request):
    versions = NexusVersion.objects.filter(
        status='published', family__active=True, is_fixture=False,
    ).select_related('family').order_by('family__order', 'family_id', 'code', '-version')
    mode_order = {'basic': 0, 'essential': 1, 'advanced': 2, 'premium': 3}
    groups = {}
    for version in versions:
        group = groups.setdefault(version.family_id, {'family': version.family, 'items': []})
        group['items'].append({'version': version, 'access': service.access(request.user, version)})
    for group in groups.values():
        group['items'].sort(key=lambda item: mode_order[item['version'].mode])
    return _private(render(request, 'nexus/versioned/catalog.html', {
        'families': NexusFamily.objects.filter(active=True), 'evaluation_groups': list(groups.values()),
        'modalities': NexusModality.objects.filter(active=True),
    }))


@never_cache
@require_GET
def info(request, pk):
    version = get_object_or_404(
        NexusVersion.objects.select_related('family'),
        pk=pk, status='published', is_fixture=False, family__active=True,
    )
    allowed, reason = service.access(request.user, version)
    return _private(render(request, 'nexus/versioned/info.html', {
        'version': version, 'allowed': allowed, 'reason': reason,
    }))


@never_cache
@login_required
@require_POST
def start(request, pk):
    _access(request)
    version = get_object_or_404(NexusVersion, pk=pk)
    try:
        session = service.start(request.user, version)
    except PermissionDenied:
        return _error(request, 'La evaluación requiere acceso vigente o contenido editorial publicado.', 403)
    return redirect('nexus:versioned_take', pk=session.pk)


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def take(request, pk):
    session = _session(request, pk)
    if session.status == AssessmentSession.STATUS_COMPLETED:
        return redirect('nexus:versioned_report', pk=pk)
    form = service.questionnaire_form(session, request.POST if request.method == 'POST' else None)
    status = 400 if request.method == 'POST' else 200
    if request.method == 'POST' and form.is_valid():
        expected = {f'q_{q["id"]}' for q in session.frozen_definition['questions']}
        extras = {key for key in request.POST if key.startswith('q_')} - expected
        if extras or request.POST.get('action') not in ('save', 'submit'):
            form.add_error(None, 'La solicitud contiene preguntas o acciones ajenas al intento.')
            status = 400
        else:
            try:
                updated = service.save_answers(
                    request.user, session, form.cleaned_data, submit=request.POST['action'] == 'submit',
                )
            except AssessmentSession.DoesNotExist:
                raise Http404('El intento ya no está disponible.')
            except (ValidationError, service.NexusConflict) as error:
                form.add_error(None, str(error))
                status = 409 if isinstance(error, service.NexusConflict) else 400
            else:
                if updated.status == AssessmentSession.STATUS_COMPLETED:
                    return redirect('nexus:versioned_report', pk=pk)
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return _private(JsonResponse({
                        'revision': updated.edit_revision, 'answered': updated.answers.count(),
                        'total': len(updated.frozen_definition['questions']),
                    }))
                messages.success(request, 'Respuestas guardadas. Puedes continuar más tarde sin consumo de energía.')
                return redirect('nexus:versioned_take', pk=pk)
    total = len(session.frozen_definition['questions'])
    answered = session.answers.count()
    return _private(render(request, 'nexus/versioned/take.html', {
        'session': session, 'form': form, 'answered': answered, 'total': total,
        'progress': int(100 * answered / total) if total else 0,
    }, status=status))


@never_cache
@login_required
@require_GET
def history(request):
    _access(request)
    sessions = AssessmentSession.objects.filter(
        candidate=request.user, editorial_version__isnull=False,
    ).select_related('editorial_version').order_by('-created_at')
    return _private(render(request, 'nexus/versioned/history.html', {
        'page_obj': Paginator(sessions, 20).get_page(request.GET.get('page')),
    }))


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def delete_attempt(request, pk):
    session = _session(request, pk)
    context = {
        'session': session,
        'linked_goals': session.nexusgoallink_set.count(),
    }
    if request.method == 'POST':
        if request.POST.get('confirm_delete') != 'yes':
            context['error'] = 'Debes confirmar el borrado definitivo del intento.'
            return _private(render(request, 'nexus/versioned/confirm_delete.html', context, status=400))
        try:
            service.delete_attempt(request.user, session)
        except AssessmentSession.DoesNotExist:
            raise Http404('El intento ya no está disponible.')
        messages.success(request, 'Intento eliminado definitivamente. Tus objetivos personales se conservan.')
        return redirect('nexus:versioned_history')
    return _private(render(request, 'nexus/versioned/confirm_delete.html', context))


@never_cache
@login_required
@require_GET
def report(request, pk):
    session = _session(request, pk, completed=True)
    if request.GET.get('download') == 'pdf':
        if session.report_scope == 'basic':
            return _error(request, 'La exportación estándar requiere un informe estándar o ampliado obtenido legítimamente.', 403)
        snapshot = session.frozen_definition
        lines = [
            'Nexus DNA — evaluación orientativa, no diagnóstico',
            f'{snapshot["title"]} / {snapshot["family"]} / {snapshot["mode"]} / v{snapshot["version"]}',
            f'Fecha: {session.completed_at.isoformat()}',
            'Método: ' + snapshot['method'], 'IA utilizada: no',
        ]
        if snapshot.get('original_family'):
            result = session.calculated_report
            if result['overall_score'] is not None:
                lines += [f'Índice global original: {result["overall_score"]} / 100']
            else:
                lines += ['Índice global no calculable: faltan dimensiones evaluadas.']
            lines += [f'{a["name"]}: {a["score"]} / 100 de afinidad orientativa'
                      for a in result['archetypes'][:2]]
            lines += [f'{name}: {score} / 100' for name, score in result['competencies'].items()]
            lines += [f'Control {name}: {score if score is not None else "Sin datos"}'
                      for name, score in result['validation'].items()]
        for dimension in session.calculated_report['dimensions']:
            lines += [
                dimension['label'] + ': ' + dimension['score'] + ' / 100',
                dimension['brief'], dimension['explanation'], dimension['recommendation'],
            ]
            if session.report_scope == 'extended':
                lines += [dimension['steps']]
        lines += [
            session.calculated_report['notice'], snapshot['limitations'],
            'Fundamento declarado: ' + snapshot['foundation'],
            'Evidencia declarada: ' + (snapshot['validation_evidence'] or 'No aportada; no se afirma validación científica.'),
        ]
        return _private(build_text_pdf('\n\n'.join(lines), f'nexus-{session.pk}-v{snapshot["version"]}.pdf'))
    return _private(render(request, 'nexus/versioned/report.html', {
        'session': session, 'result': session.calculated_report, 'ai_message': service.ai_availability(request.user)['message'],
        'is_pro': effective_plan(request.user) == 'pro',
    }))


class CompareForm(forms.Form):
    first = forms.ModelChoiceField(label='Intento anterior', queryset=AssessmentSession.objects.none())
    second = forms.ModelChoiceField(label='Intento posterior', queryset=AssessmentSession.objects.none())

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        sessions = AssessmentSession.objects.filter(
            candidate=user, editorial_version__isnull=False, status=AssessmentSession.STATUS_COMPLETED,
        ).select_related('editorial_version', 'assessment')
        self.fields['first'].queryset = sessions
        self.fields['second'].queryset = sessions


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def evolution(request):
    _access(request)
    if effective_plan(request.user) not in ('plus', 'pro'):
        return _error(request, 'La evolución requiere PLUS o PRO.', 403)
    form = CompareForm(request.POST if request.method == 'POST' else None, user=request.user)
    differences = None
    status = 200
    if request.method == 'POST' and form.is_valid():
        try:
            differences = service.compare(request.user, form.cleaned_data['first'], form.cleaned_data['second'])
        except ValidationError as error:
            form.add_error(None, error)
            status = 400
    return _private(render(request, 'nexus/versioned/evolution.html', {
        'form': form, 'differences': differences,
        'first': form.cleaned_data.get('first') if form.is_bound and form.is_valid() else None,
        'second': form.cleaned_data.get('second') if form.is_bound and form.is_valid() else None,
    }, status=status))


class GoalForm(forms.Form):
    dimension = forms.CharField(max_length=80)
    target_date = forms.DateField(required=False)


@never_cache
@login_required
@require_POST
def goal(request, pk):
    session = _session(request, pk, completed=True)
    form = GoalForm(request.POST)
    if not form.is_valid():
        return _error(request, 'Dimensión o fecha no válidas.')
    try:
        service.create_goal(
            request.user, session, form.cleaned_data['dimension'], form.cleaned_data['target_date'],
        )
    except ValidationError as error:
        return _error(request, error)
    messages.success(request, 'Objetivo manual creado en tu dashboard, sin consumo de energía.')
    return redirect('nexus:versioned_report', pk=pk)


@never_cache
@login_required
@require_POST
def ai(request, pk):
    session = _session(request, pk, completed=True)
    try:
        key = uuid.UUID(request.POST.get('idempotency_key', ''))
    except ValueError:
        return _error(request, 'Clave de operación no válida.')
    try:
        operation = service.request_ai(request.user, session, key, request.POST.get('task', 'interpretation'))
    except (ValidationError, service.NexusConflict) as error:
        return _error(request, error, 409)
    gate = service.ai_availability(request.user, operation.task)
    return _private(JsonResponse({
        'ok': False, 'status': operation.status, 'operation_id': str(operation.pk),
        'error': operation.error, 'message': gate['message'],
        'energy_cost': None if gate['energy_cost'] is None else str(gate['energy_cost']),
    }, status=503))


@never_cache
@require_http_methods(['GET', 'POST'])
def demo(request, pk=None):
    class DemoForm(forms.Form):
        value = forms.ChoiceField(label='Ejemplo ficticio: ¿has reservado un momento para reflexionar hoy?',
                                 choices=[('yes', 'Sí'), ('no', 'No')], widget=forms.RadioSelect)
    form = DemoForm(request.POST if request.method == 'POST' else None)
    answer = None
    if request.method == 'POST' and form.is_valid():
        answer = 'Has elegido «Sí».' if form.cleaned_data['value'] == 'yes' else 'Has elegido «No».'
    return _private(render(request, 'nexus/versioned/demo.html', {'form': form, 'answer': answer}))


@never_cache
@login_required
@require_POST
def legacy_submit_unavailable(request, pk):
    from .models import SesionTest
    session = get_object_or_404(SesionTest, pk=pk, alumno=request.user)
    if session.estado == SesionTest.COMPLETADO:
        return redirect('nexus:student_results', pk=pk)
    return _error(request, 'Este test HTML necesita una definición y cálculo aprobados en servidor. '
                  'No se aceptan puntuaciones calculadas por el navegador. Tu historial se conserva.', 503)


@never_cache
@login_required
@require_GET
def legacy_take_unavailable(request, pk):
    return _error(request, 'La realización de tests HTML está pendiente de revisión editorial y migración '
                  'al cálculo versionado. Consulta el catálogo nuevo; tus resultados anteriores se conservan.', 503)


@never_cache
@login_required
@require_POST
def legacy_ai_unavailable(request, pk):
    from .models import SesionTest
    get_object_or_404(SesionTest, pk=pk, alumno=request.user, estado=SesionTest.COMPLETADO)
    gate = service.ai_availability(request.user, 'interpretation')
    return _private(JsonResponse({
        'ok': False, 'error': gate['reason'], 'message': gate['message'],
        'energy_cost': None if gate['energy_cost'] is None else str(gate['energy_cost']),
    }, status=503))


@never_cache
@login_required
@require_POST
def legacy_report_unavailable(request, pk):
    from .models import SesionTest
    get_object_or_404(SesionTest, pk=pk, alumno=request.user)
    return _error(request, 'No se aceptan informes calculados o generados en el navegador. '
                  'Los informes ya guardados permanecen en el historial.', 503)
