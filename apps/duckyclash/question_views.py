import logging

from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError
from django.http import JsonResponse
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.utils import timezone

from .knowledge import KnowledgeUnavailable
from .models import CombatSession, Duel, DuelQuestion
from .policies import require_participant
from .questions import public_question, submit_answer
from .items import apply_item
from .engine import DuelEngine
from .selectors import open_challenges
from .access import DuckyClashAccessMixin

logger = logging.getLogger(__name__)


def private_json(payload, status=200):
    response = JsonResponse(payload, status=status)
    response['Cache-Control'] = 'no-store, private'
    response['Pragma'] = 'no-cache'
    response['Vary'] = 'Cookie'
    return response


class OpenChallengeStateView(DuckyClashAccessMixin, View):
    def get(self, request):
        ids = request.GET.get('ids', '').split(',')
        if len(ids) > 12 or any(not value.isascii() or not value.isdecimal() or len(value) > 18 for value in ids):
            return private_json({'detail': 'Identificadores de retos no válidos.'}, status=400)
        now = timezone.now()
        return private_json({
            'server_time': now.isoformat(),
            'available': [
                {'id': challenge.pk, 'remaining': max(0, int((challenge.expires_at - now).total_seconds()))}
                for challenge in open_challenges().prefetch_related(None).filter(pk__in=[int(value) for value in ids])
            ],
        })


class CurrentQuestionView(DuckyClashAccessMixin, View):
    def get(self, request, pk):
        duel = get_object_or_404(Duel.objects.select_related('agreement'), pk=pk)
        require_participant(request.user, duel.agreement)
        question = duel.questions.select_related('duel__agreement').order_by('-number').first()
        if question is None:
            return private_json({'detail': 'No hay una pregunta abierta.'}, status=404)
        try:
            payload = public_question(actor=request.user, question=question)
        except ValidationError:
            return private_json({'detail': 'No hay una pregunta abierta.'}, status=409)
        return private_json(payload)


class RingStateView(DuckyClashAccessMixin, View):
    def get(self, request, pk):
        duel = get_object_or_404(Duel.objects.select_related('agreement'), pk=pk)
        require_participant(request.user, duel.agreement)
        if CombatSession.objects.filter(duel=duel).exists() or duel.status == Duel.Status.SCHEDULED:
            cursor = request.GET.get('after', '0')
            if len(cursor) > 18 or not cursor.isascii() or not cursor.isdecimal():
                return private_json({'detail': 'Cursor de eventos no válido.'}, status=400)
            return private_json(DuelEngine(duel.pk).snapshot(request.user, after=int(cursor)))
        question = duel.questions.select_related('duel__agreement').order_by('-number').first()
        payload = None
        if question and duel.status == Duel.Status.IN_PROGRESS:
            try:
                payload = public_question(actor=request.user, question=question)
            except ValidationError:
                pass  # A closed individual deadline is a normal Ring state.
        return private_json({
            'status': duel.get_status_display(), 'server_time': timezone.now().isoformat(),
            'phase': duel.status,
            'participants': list(duel.participants.order_by('seat').values('player_id', 'hp', 'max_hp')),
            'question': payload,
            'answered': bool(question and question.answers.filter(participant__player=request.user).exists()),
        })


class EngineActionView(DuckyClashAccessMixin, View):
    def post(self, request, pk, action):
        duel = get_object_or_404(Duel.objects.select_related('agreement'), pk=pk)
        require_participant(request.user, duel.agreement)
        length = request.META.get('CONTENT_LENGTH', '0') or '0'
        if not length.isdecimal() or int(length) > 2048:
            return private_json({'detail': 'La acción supera el tamaño permitido.'}, status=400)
        if set(request.POST) - {'csrfmiddlewaretoken'}:
            return private_json({'detail': 'La acción no acepta estado ni tiempos del cliente.'}, status=400)
        if not request.content_type.startswith(('multipart/form-data', 'application/x-www-form-urlencoded')) and int(length):
            return private_json({'detail': 'Formato de acción no admitido.'}, status=400)
        engine = DuelEngine(duel.pk)
        try:
            if action == 'ready':
                engine.ready(request.user)
            else:
                engine.enter(request.user)
                engine.advance()
        except ValidationError as exc:
            if action == 'ready':
                messages.error(request, ' '.join(exc.messages))
                return redirect('duckyclash:ring', pk=duel.pk)
            return private_json({'detail': ' '.join(exc.messages)}, status=409)
        except OperationalError as exc:
            if 'locked' not in str(exc).lower() and 'deadlock' not in str(exc).lower():
                raise
            logger.warning('Concurrent DuelEngine action for duel %s', pk)
            return private_json({'detail': 'Otra acción está en curso. Recupera el estado antes de reenviar.'}, status=409)
        if action == 'ready':
            # Provider I/O and countdown preparation happen after readiness committed.
            try:
                engine.advance()
            except ValidationError:
                logger.exception('DuelEngine advancement deferred for duel %s', pk)
                messages.warning(request, 'Preparación registrada; el servidor debe completar el inicio.')
            except OperationalError as exc:
                if 'locked' not in str(exc).lower() and 'deadlock' not in str(exc).lower():
                    raise
                logger.warning('Concurrent DuelEngine advance for duel %s', pk)
                messages.warning(request, 'Preparación registrada; el servidor está procesando el inicio.')
            return redirect('duckyclash:ring', pk=duel.pk)
        return private_json({'accepted': True})


class AnswerQuestionView(DuckyClashAccessMixin, View):
    def post(self, request, pk):
        question = get_object_or_404(DuelQuestion.objects.select_related('duel__agreement'), pk=pk)
        require_participant(request.user, question.duel.agreement)
        try:
            submit_answer(actor=request.user, question_id=question.pk, option_id=request.POST.get('option_id'))
        except ValidationError:
            return private_json({'detail': 'Respuesta no admitida: revisa la opción, el plazo o si ya respondiste.'}, status=409)
        except KnowledgeUnavailable:
            logger.warning('DuckyClash question validation unavailable for question %s', pk)
            return private_json({'detail': 'No se pudo verificar la respuesta. Inténtalo dentro del plazo.'}, status=503)
        except IntegrityError:
            logger.warning('Concurrent DuckyClash answer for question %s', pk)
            return private_json({'detail': 'La respuesta ya está registrada o está en curso.'}, status=409)
        except OperationalError as exc:
            if 'locked' not in str(exc).lower():
                raise
            logger.warning('DuckyClash question database busy for question %s', pk)
            return private_json({'detail': 'Otra operación está en curso. Revisa antes de reenviar.'}, status=409)
        try:
            DuelEngine(question.duel_id).advance()
        except ValidationError:
            logger.exception('Answer committed; round advancement requires recovery: %s', question.duel_id)
        except OperationalError as exc:
            if 'locked' not in str(exc).lower() and 'deadlock' not in str(exc).lower():
                raise
            logger.warning('Answer committed; concurrent round advancement: %s', question.duel_id)
        # Identical acknowledgement for right/wrong answers; no oracle before close.
        return private_json({'accepted': True})


class UseItemView(DuckyClashAccessMixin, View):
    def post(self, request, pk):
        question = get_object_or_404(DuelQuestion.objects.select_related('duel__agreement'), pk=pk)
        require_participant(request.user, question.duel.agreement)
        try:
            apply_item(
                actor=request.user, question_id=question.pk,
                code=request.POST.get('code'), action_id=request.POST.get('action_id'),
            )
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        except KnowledgeUnavailable:
            logger.warning('DuckyClash certified item options unavailable for question %s', pk)
            messages.error(request, 'No hay un proveedor verificable para este efecto. No se ha consumido el objeto.')
        except IntegrityError:
            logger.warning('Concurrent DuckyClash item action for question %s', pk)
            messages.error(request, 'La acción ya está registrada o en curso. Recarga el Ring.')
        except OperationalError as exc:
            if 'locked' not in str(exc).lower() and 'deadlock' not in str(exc).lower():
                raise
            logger.warning('Concurrent DuckyClash item update for question %s', pk)
            messages.error(request, 'Otra operación está en curso. Recarga antes de reenviar.')
        else:
            messages.success(request, 'Objeto activado. El servidor ha actualizado su efecto.')
        return redirect('duckyclash:ring', pk=question.duel_id)
