import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, JsonResponse
from django.http import Http404
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.cache import patch_cache_control
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.users.access import can, effective_plan
from .letter_forms import EditLetterForm, GenerateLetterForm, LetterActionForm, NewLetterForm, ReviewLetterForm
from .models import CoverLetter, LetterVersion
from .services.letters import (
    LetterConflict, LetterRateLimited, UNAVAILABLE_MESSAGE, change_status, create_letter,
    current_configuration, delete_letter, duplicate_letter, generation_availability,
    prepare_review, request_generation, save_version,
)
from .services.pdf import build_letter_pdf


def _access(request):
    if not can(request.user, 'view_registered_content').allowed:
        raise PermissionDenied('Se requiere una cuenta activa.')


def _owned_letter(request, pk):
    _access(request)
    return get_object_or_404(CoverLetter.objects.select_related('offer', 'profile', 'configuration'), pk=pk, user=request.user)


def _private(response):
    patch_cache_control(response, private=True, no_store=True, no_cache=True, max_age=0)
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@never_cache
@require_GET
def letter_demo(request):
    return render(request, 'curriculum/letters/demo.html')


@never_cache
@login_required
@require_GET
def letter_list(request):
    _access(request)
    letters = CoverLetter.objects.filter(user=request.user).select_related('profile', 'offer')
    query = request.GET.get('q', '')[:200]
    if query:
        letters = letters.filter(title__icontains=query)
    page = Paginator(letters, 20).get_page(request.GET.get('page'))
    return _private(render(request, 'curriculum/letters/list.html', {
        'page_obj': page, 'query': query, 'availability': generation_availability(current_configuration()),
    }))


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def letter_new(request):
    _access(request)
    configuration = current_configuration()
    if not configuration.available:
        return _private(render(request, 'curriculum/letters/unavailable.html', status=503))
    form = NewLetterForm(
        request.POST if request.method == 'POST' else None,
        user=request.user, configuration=configuration,
    )
    if request.method == 'POST' and form.is_valid():
        try:
            payload, token, warnings, sections = prepare_review(request.user, form.cleaned_data, configuration)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            return _private(render(request, 'curriculum/letters/review.html', {
                'payload': payload, 'sections': sections, 'warnings': warnings,
                'form': ReviewLetterForm(initial={'token': token}),
                'availability': generation_availability(configuration),
            }))
    return _private(render(request, 'curriculum/letters/new.html', {
        'form': form, 'profiles': form.fields['profile'].queryset,
        'plan': effective_plan(request.user), 'configuration': configuration,
    }))


@never_cache
@login_required
@require_POST
def letter_confirm(request):
    _access(request)
    form = ReviewLetterForm(request.POST)
    status = 400
    if form.is_valid():
        try:
            letter = create_letter(request.user, form.cleaned_data['token'])
        except (ValidationError, LetterConflict, LetterRateLimited) as error:
            form.add_error(None, str(error))
            status = 429 if isinstance(error, LetterRateLimited) else 409
        else:
            return redirect('curriculum:letter_detail', pk=letter.pk)
    return _private(render(request, 'curriculum/letters/confirmation_error.html', {'form': form}, status=status))


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def letter_detail(request, pk):
    letter = _owned_letter(request, pk)
    configuration = current_configuration()
    selected = letter.versions.first()
    if 'version' in request.GET:
        try:
            version_id = int(request.GET['version'])
        except ValueError as error:
            raise Http404('Versión no válida.') from error
        selected = get_object_or_404(letter.versions, pk=version_id)
    form = EditLetterForm(
        request.POST if request.method == 'POST' else None,
        configuration=configuration,
        initial={'title': letter.title, 'content': selected.content, 'revision': letter.revision},
    )
    status = 200
    if request.method == 'POST' and form.is_valid():
        try:
            save_version(
                letter, request.user, title=form.cleaned_data['title'], content=form.cleaned_data['content'],
                expected_revision=form.cleaned_data['revision'],
            )
        except (ValidationError, LetterConflict, LetterRateLimited) as error:
            form.add_error(None, str(error))
            status = 429 if isinstance(error, LetterRateLimited) else 409
        else:
            messages.success(request, 'Versión guardada sin consumo de energía.')
            return redirect('curriculum:letter_detail', pk=letter.pk)
    return _private(render(request, 'curriculum/letters/detail.html', {
        'letter': letter, 'form': form, 'selected': selected, 'versions': letter.versions.all(),
        'availability': generation_availability(configuration),
        'generation_key': uuid.uuid4(),
    }, status=status))


@never_cache
@login_required
@require_POST
def letter_action(request, pk, action):
    letter = _owned_letter(request, pk)
    form = LetterActionForm(request.POST)
    if not form.is_valid():
        return _private(JsonResponse({'ok': False, 'error': 'invalid_revision'}, status=400))
    revision = form.cleaned_data['revision']
    try:
        if action == 'duplicate':
            clone = duplicate_letter(letter, request.user, revision)
            return redirect('curriculum:letter_detail', pk=clone.pk)
        if action in {'archive', 'restore'}:
            change_status(
                letter, request.user, revision,
                CoverLetter.Status.ARCHIVED if action == 'archive' else CoverLetter.Status.DRAFT,
            )
        elif action == 'delete':
            delete_letter(letter, request.user, revision)
            return redirect('curriculum:letter_list')
        else:
            return _private(JsonResponse({'ok': False, 'error': 'invalid_action'}, status=400))
    except (ValidationError, LetterConflict, LetterRateLimited) as error:
        return _private(render(
            request, 'curriculum/letters/action_error.html', {'error': str(error), 'letter': letter},
            status=429 if isinstance(error, LetterRateLimited) else 409,
        ))
    return redirect('curriculum:letter_detail', pk=letter.pk)


@never_cache
@login_required
@require_POST
def letter_generate(request, pk):
    letter = _owned_letter(request, pk)
    form = GenerateLetterForm(request.POST)
    if not form.is_valid():
        return _private(JsonResponse({'ok': False, 'error': 'invalid_request', 'details': form.errors}, status=400))
    try:
        operation = request_generation(
            letter, request.user, key=form.cleaned_data['idempotency_key'],
            expected_revision=form.cleaned_data['revision'],
        )
    except (LetterConflict, LetterRateLimited) as error:
        return _private(JsonResponse(
            {'ok': False, 'error': str(error)}, status=429 if isinstance(error, LetterRateLimited) else 409,
        ))
    return _private(JsonResponse({
        'ok': False, 'operation_id': str(operation.pk), 'status': operation.status,
        'error': operation.error_code, 'message': UNAVAILABLE_MESSAGE, 'energy_cost': None,
    }, status=503))


@never_cache
@login_required
@require_GET
def letter_download(request, pk, version, format):
    letter = _owned_letter(request, pk)
    selected = get_object_or_404(LetterVersion, letter=letter, number=version)
    if format == 'pdf':
        response = build_letter_pdf(letter, selected)
    elif format == 'txt':
        response = HttpResponse(selected.content, content_type='text/plain; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="carta-{letter.pk}-v{version}.txt"'
    else:
        return _private(JsonResponse({'ok': False, 'error': 'unsupported_format'}, status=400))
    return _private(response)
