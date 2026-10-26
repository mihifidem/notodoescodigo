import json
import uuid

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.cache import patch_cache_control
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from apps.users.access import can
from .ats_forms import ATSCompareForm, ATSReviewForm, ATSSourceForm
from .models import ATSComparison, ATSReport, ATSSource
from .services import ats
from .services.ats_rules import analyze_text
from .services.pdf import build_text_pdf


def _access(request):
    if not can(request.user, 'view_registered_content').allowed:
        raise PermissionDenied('Se requiere una cuenta activa.')


def _private(response):
    patch_cache_control(response, private=True, no_store=True, no_cache=True, max_age=0)
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def _report(request, pk):
    _access(request)
    return get_object_or_404(
        ATSReport.objects.select_related('source', 'configuration').defer('source__original_pdf'),
        pk=pk, user=request.user,
    )


def _error(request, error, status=400):
    return _private(render(request, 'curriculum/ats/error.html', {'error': str(error)}, status=status))


@never_cache
@require_GET
def ats_demo(request):
    config = ats.configuration()
    text = 'Perfil profesional\nAdministración de documentación.\nFormación académica\nCurso ficticio.\nCompetencias\nOrganización.'
    result = analyze_text(text, 'Deseable: organización de documentación', config, [])
    return render(request, 'curriculum/ats/demo.html', {'result': result, 'text': text})


@never_cache
@login_required
@require_GET
def ats_list(request):
    _access(request)
    reports = ATSReport.objects.filter(user=request.user).select_related('source').defer('source__original_pdf')
    query = request.GET.get('q', '')[:200]
    if query:
        reports = reports.filter(source__title__icontains=query)
    sources = ATSSource.objects.filter(user=request.user, reports__isnull=True).defer('original_pdf')[:20]
    return _private(render(request, 'curriculum/ats/list.html', {
        'page_obj': Paginator(reports, 20).get_page(request.GET.get('page')),
        'sources': sources, 'query': query,
    }))


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def ats_new(request):
    _access(request)
    config = ats.configuration()
    if not config.enabled:
        return _error(request, 'Nuevos análisis desactivados; los informes guardados siguen disponibles.', 503)
    form = ATSSourceForm(
        request.POST if request.method == 'POST' else None, request.FILES or None,
        user=request.user, configuration=config,
    )
    status = 200
    if request.method == 'POST' and len(request.FILES.getlist('pdf')) > 1:
        form.add_error('pdf', 'Solo se admite un PDF por solicitud.')
    if request.method == 'POST' and form.is_valid():
        try:
            source = ats.create_source(request.user, form.cleaned_data)
        except (ValidationError, ats.ATSRateLimited) as error:
            form.add_error(None, str(error))
            status = 429 if isinstance(error, ats.ATSRateLimited) else 400
        else:
            return redirect('curriculum:ats_review', pk=source.pk)
    return _private(render(request, 'curriculum/ats/new.html', {'form': form, 'config': config}, status=status))


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def ats_review(request, pk):
    _access(request)
    source = get_object_or_404(ATSSource.objects.defer('original_pdf'), pk=pk, user=request.user)
    config = source.configuration
    form = ATSReviewForm(
        request.POST if request.method == 'POST' else None, configuration=config,
        scopes=ats.available_scopes(request.user, ats.configuration()),
        initial={'reviewed_text': source.extracted_text, 'scope': 'basic'},
    )
    status = 200
    if request.method == 'POST' and form.is_valid():
        try:
            report = ats.run_analysis(request.user, source, form.cleaned_data)
        except (ValidationError, ats.ATSConflict, ats.ATSRateLimited) as error:
            form.add_error(None, str(error))
            status = 429 if isinstance(error, ats.ATSRateLimited) else 409
        else:
            return redirect('curriculum:ats_detail', pk=report.pk)
    return _private(render(request, 'curriculum/ats/review.html', {
        'source': source, 'form': form, 'ai_message': ats.ai_availability(request.user)['message'],
    }, status=status))


@never_cache
@login_required
@require_GET
def ats_detail(request, pk):
    report = _report(request, pk)
    scope = ats.report_scope(request.user, report)
    findings = report.result['findings']
    if scope == 'pro':
        findings = sorted(findings, key=lambda item: item['severity'] != 'warning')
    return _private(render(request, 'curriculum/ats/detail.html', {
        'report': report, 'scope': scope, 'findings': findings, 'ai_message': ats.ai_availability(request.user)['message'],
    }))


def _report_text(report, full):
    result = report.result
    lines = [
        'Verificar CV — revisión orientativa, no certificación ATS',
        f'Fuente: {report.source.title} / {report.source.source_label}',
        f'Huella fuente: {report.source.original_hash}',
        f'Huella texto revisado: {report.content_hash}',
        f'Fecha: {report.created_at.isoformat()}',
        f'Criterios: {report.configuration.criteria_version} / configuración {report.configuration.version}',
        f'Alcance: {report.scope} / cobertura: {result["coverage"]}',
        f'Estado: {report.status}. IA utilizada: no.',
        f'Oferta: {"Aportada como texto" if report.offer_text.strip() else "No aportada"}',
        result['summary'],
    ]
    if full and report.offer_text:
        lines += ['Oferta utilizada:', report.offer_text]
    if report.offer_url:
        lines += ['URL solo de referencia, no leída: ' + report.offer_url]
    lines += ['Hallazgos y recomendaciones (reglas):']
    for finding in result['findings']:
        lines += [
            f'{finding["severity"]} — {finding["description"]}', finding['recommendation'],
        ]
        if full:
            lines += ['Evidencia: ' + (finding['evidence'] or 'No identificada'), finding['limitation']]
    counts = result['requirement_counts']
    lines += ['Coincidencias textuales de oferta: ' + json.dumps(counts, ensure_ascii=False)]
    if full:
        for requirement in result['requirements']:
            lines += [
                requirement['text'], requirement['type'] + ' / ' + requirement['state'],
                requirement['explanation'], 'Evidencia: ' + (requirement['evidence'] or 'No encontrada'),
                'Términos encontrados: ' + ', '.join(requirement['matched_keywords']),
                requirement['recommendation'],
            ]
    else:
        lines += ['Requisitos sin evidencia textual o no evaluables (no equivale a incumplimiento):']
        lines += [
            requirement['text'] + ' — ' + requirement['explanation']
            for requirement in result['requirements'] if requirement['state'] in ('not_found', 'not_evaluable')
        ]
    return '\n\n'.join(lines + ['Limitaciones:'] + result['limitations'])


@never_cache
@login_required
@require_GET
def ats_download(request, pk, extent):
    report = _report(request, pk)
    if extent not in ('summary', 'full'):
        return _error(request, 'Formato de informe no admitido.')
    if extent == 'full' and ats.report_scope(request.user, report) == 'basic':
        raise PermissionDenied('El informe detallado requiere PLUS, PRO o un derecho compatible.')
    return _private(build_text_pdf(
        _report_text(report, extent == 'full'), f'informe-{report.pk}-{extent}.pdf',
    ))


@never_cache
@login_required
@require_GET
def ats_original(request, pk):
    _access(request)
    source = get_object_or_404(ATSSource, pk=pk, user=request.user)
    if source.original_pdf is None:
        return _error(request, 'Esta fuente no contiene un PDF original.', 404)
    response = HttpResponse(bytes(source.original_pdf), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="cv-{source.pk}.pdf"'
    response['Content-Security-Policy'] = "sandbox; default-src 'none'"
    return _private(response)


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def ats_compare(request):
    _access(request)
    if ats.access_scope(request.user, ats.configuration())[0] != 'pro':
        return _error(request, 'Comparar versiones requiere PRO o un derecho individual vigente configurado.', 403)
    reports = ATSReport.objects.filter(user=request.user).select_related('source', 'configuration').defer('source__original_pdf')
    form = ATSCompareForm(request.POST if request.method == 'POST' else None, reports=reports)
    status = 200
    if request.method == 'POST' and form.is_valid():
        try:
            comparison = ats.compare(
                request.user, form.cleaned_data['before'], form.cleaned_data['after'], form.cleaned_data['same_cv'],
            )
        except ValidationError as error:
            form.add_error(None, error)
            status = 400
        else:
            return redirect('curriculum:ats_comparison', pk=comparison.pk)
    return _private(render(request, 'curriculum/ats/compare.html', {'form': form}, status=status))


@never_cache
@login_required
@require_GET
def ats_comparison(request, pk):
    _access(request)
    comparison = get_object_or_404(
        ATSComparison.objects.select_related('before__source', 'after__source', 'before__configuration', 'after__configuration'),
        pk=pk, user=request.user,
    )
    if request.GET.get('download') == 'pdf':
        result = comparison.result
        lines = [
            'Comparativa determinista orientativa — no certificación ATS',
            'Antes: ' + comparison.before.source.source_label,
            'Después: ' + comparison.after.source.source_label,
            f'Informes: {comparison.before_id} / {comparison.after_id}',
            f'Fecha: {comparison.created_at.isoformat()}',
            f'Criterios: {comparison.before.configuration.criteria_version}',
            f'Configuraciones: {comparison.before.configuration.version} / {comparison.after.configuration.version}',
            f'Cobertura antes: {comparison.before.result["coverage"]}',
            f'Cobertura después: {comparison.after.result["coverage"]}',
        ]
        for key, label in (('resolved', 'No detectados ahora'), ('new', 'Nuevos'), ('pending', 'Pendientes')):
            lines += [label] + [item['description'] + ' ' + item['recommendation'] for item in result[key]]
        lines += ['Evidencias textuales añadidas:'] + result['evidence_added']
        lines += ['Evidencias textuales retiradas:'] + result['evidence_removed']
        lines += ['Limitaciones:'] + result['limitations']
        return _private(build_text_pdf('\n\n'.join(lines), f'comparativa-{comparison.pk}.pdf'))
    return _private(render(request, 'curriculum/ats/comparison.html', {'comparison': comparison}))


@never_cache
@login_required
@require_POST
def ats_ai(request, pk):
    report = _report(request, pk)
    try:
        key = uuid.UUID(request.POST.get('idempotency_key', ''))
    except ValueError:
        return _error(request, 'Clave de operación no válida.')
    try:
        operation = ats.request_ai(request.user, report, key, request.POST.get('task', 'semantic'))
    except (ValidationError, ats.ATSConflict, ats.ATSRateLimited) as error:
        return _error(request, error, 429 if isinstance(error, ats.ATSRateLimited) else 409)
    gate = ats.ai_availability(request.user, operation.task)
    return _private(JsonResponse({
        'ok': False, 'status': operation.status, 'operation_id': str(operation.pk),
        'error': operation.error_code, 'message': gate['message'],
        'energy_cost': None if gate['energy_cost'] is None else str(gate['energy_cost']),
    }, status=503))


@never_cache
@login_required
@require_POST
def ats_action(request, pk, action):
    report = _report(request, pk)
    if action == 'archive':
        ATSReport.objects.filter(pk=report.pk, user=request.user).update(archived=True)
    elif action == 'restore':
        ATSReport.objects.filter(pk=report.pk, user=request.user).update(archived=False)
    elif action == 'delete':
        try:
            ats.delete_report(request.user, report)
        except ValidationError as error:
            return _error(request, error, 409)
        return redirect('curriculum:ats_list')
    else:
        return _error(request, 'Acción no admitida.')
    return redirect('curriculum:ats_detail', pk=report.pk)


@never_cache
@login_required
@require_POST
def ats_discard_source(request, pk):
    _access(request)
    source = get_object_or_404(ATSSource.objects.defer('original_pdf'), user=request.user, pk=pk)
    if source.reports.exists():
        return _error(request, 'La fuente ya tiene informes; gestiona su eliminación desde el informe.', 409)
    ats.remove_source(request.user, source)
    return redirect('curriculum:ats_list')
