import logging

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, DataError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from apps.users.access import is_platform_admin
from .content_backups import MAX_BYTES, SECTIONS, export_backup, restore_backup


logger = logging.getLogger(__name__)


class RestoreForm(forms.Form):
    section = forms.ChoiceField(label='Sección a restaurar', choices=[(key, value[0]) for key, value in SECTIONS.items()])
    backup = forms.FileField(label='Copia JSON', widget=forms.ClearableFileInput(attrs={'accept': '.json,application/json'}))
    confirm = forms.BooleanField(label='Confirmo que se actualizarán los registros con los mismos IDs y se insertarán los que falten.')

    def clean_backup(self):
        uploaded = self.cleaned_data['backup']
        if uploaded.size > MAX_BYTES:
            raise forms.ValidationError('El archivo supera el límite de 10 MB.')
        return uploaded


def _admin(request):
    if not is_platform_admin(request.user):
        raise PermissionDenied('Solo ADMIN de plataforma puede gestionar estas copias.')


def _page(request, form, status=200):
    response = render(request, 'data_admin/content_backups.html', {
        'form': form, 'sections': [{'key': key, 'name': value[0]} for key, value in SECTIONS.items()],
    }, status=status)
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def dashboard(request):
    _admin(request)
    form = RestoreForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        try:
            counts = restore_backup(
                form.cleaned_data['backup'].read(MAX_BYTES + 1), form.cleaned_data['section'], request.user,
            )
        except ValidationError as error:
            logger.warning('Content restore rejected actor=%s: %s', request.user.pk, error)
            form.add_error(None, ' '.join(error.messages))
        except (IntegrityError, DataError):
            logger.exception('Content restore failed actor=%s', request.user.pk)
            form.add_error(None, 'No se ha restaurado ninguna fila: hay IDs, relaciones o valores incompatibles con los datos actuales.')
        else:
            messages.success(request, f'Copia restaurada: {counts["updated"]} registros actualizados y {counts["inserted"]} insertados. No se han borrado registros adicionales.')
            return redirect('data_admin:content_backups')
    return _page(request, form, status=400 if request.method == 'POST' else 200)


@never_cache
@login_required
@require_POST
def download(request, section):
    _admin(request)
    try:
        content = export_backup(section, request.user)
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
        return redirect('data_admin:content_backups')
    response = HttpResponse(content, content_type='application/json; charset=utf-8')
    stamp = timezone.now().strftime('%Y%m%d-%H%M%S')
    response['Content-Disposition'] = f'attachment; filename="backup-{section}-{stamp}.json"'
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
