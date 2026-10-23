if __name__ == '__main__':
    raise SystemExit(
        'Este archivo es una vista de Django, no un script ejecutable.\n'
        'Desde la raiz del proyecto:\n'
        '  Menu de preguntas: .\\scripts\\generar_url_banco_preguntas.ps1\n'
        '  Servidor API: .\\.venv\\Scripts\\python.exe manage.py runserver\n'
        'Si el servidor ya esta funcionando, no necesitas iniciarlo de nuevo.'
    )

import html

from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils.cache import patch_cache_control
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema, extend_schema_field
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.courses.models import Tema
from .models import FormativeUnit, Question, QuestionBank
from .policies import can_edit_course


MAX_QUESTIONS = 100


class QuestionSelectionParameters(serializers.Serializer):
    modulo = serializers.IntegerField(min_value=1, required=False, help_text='ID del módulo (courses.Tema).')
    unidad_formativa = serializers.IntegerField(min_value=1, required=False, help_text='ID de una unidad del módulo.')
    categoria = serializers.IntegerField(min_value=1, required=False, help_text='ID de CourseCategory.')
    subcategoria = serializers.CharField(
        max_length=120, required=False, allow_blank=True,
        help_text='Nombre exacto de la subcategoría, sin distinguir mayúsculas, o su ID.',
    )
    dificultad = serializers.IntegerField(min_value=1, max_value=5)
    numero_preguntas = serializers.IntegerField(min_value=1, max_value=MAX_QUESTIONS)

    def validate_subcategoria(self, value):
        if not value:
            raise serializers.ValidationError('Indica un nombre o ID de subcategoría.')
        if value.lstrip('+-').isdecimal() and int(value) <= 0:
            raise serializers.ValidationError('El ID debe ser un entero positivo.')
        return value

    def validate(self, attrs):
        taxonomy = 'categoria' in attrs or 'subcategoria' in attrs
        if taxonomy and ('modulo' in attrs or 'unidad_formativa' in attrs):
            raise serializers.ValidationError(
                'Usa categoría/subcategoría o módulo/unidad formativa, no ambos filtros.'
            )
        required = ('categoria',) if taxonomy else ('modulo', 'unidad_formativa')
        missing = {key: ['Este campo es obligatorio.'] for key in required if key not in attrs}
        if missing:
            raise serializers.ValidationError(missing)
        return attrs


class SelectedQuestionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    banco = serializers.IntegerField(source='bank_id')
    modulo = serializers.IntegerField(source='topic_id')
    unidad_formativa = serializers.IntegerField(source='formative_unit_id')
    dificultad = serializers.IntegerField(source='difficulty')
    tipo = serializers.CharField(source='question_type')
    puntos = serializers.IntegerField(source='points')
    version_id = serializers.IntegerField(source='current_version_id')
    version = serializers.IntegerField(source='current_version.version_number')
    enunciado = serializers.SerializerMethodField()
    formato_enunciado = serializers.SerializerMethodField()
    opciones = serializers.JSONField(source='current_version.options')
    respuestas_correctas = serializers.JSONField(source='current_version.correct_answers')
    explicacion = serializers.CharField(source='current_version.explanation')
    tolerancia = serializers.DecimalField(source='current_version.tolerance', max_digits=10, decimal_places=4)

    @extend_schema_field(serializers.CharField)
    def get_enunciado(self, question):
        version = question.current_version
        return html.unescape(version.prompt) if version.metadata.get('plain_text') else version.prompt

    @extend_schema_field(serializers.CharField)
    def get_formato_enunciado(self, question):
        return 'text' if question.current_version.metadata.get('plain_text') else 'html'


class QuestionSelectionResult(serializers.Serializer):
    filtros = QuestionSelectionParameters()
    disponibles = serializers.IntegerField()
    devueltas = serializers.IntegerField()
    preguntas = SelectedQuestionSerializer(many=True)


class InsufficientQuestionsSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()
    solicitadas = serializers.IntegerField()
    disponibles = serializers.IntegerField()


class KnowledgeQuestionSelectionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        patch_cache_control(response, private=True, no_store=True, no_cache=True, max_age=0)
        response['X-Content-Type-Options'] = 'nosniff'
        return response

    @extend_schema(
        summary='Seleccionar preguntas y soluciones del banco de conocimiento',
        description='Selección aleatoria sin repetición, solo de bancos, preguntas y versiones publicadas. '
                    'Filtra por módulo/unidad o categoría de DuckyClash, con subcategoría opcional por nombre o ID. '
                    'Requiere permiso de edición de los cursos de origen. No devuelve una selección parcial.',
        parameters=[QuestionSelectionParameters],
        responses={200: QuestionSelectionResult, 409: InsufficientQuestionsSerializer},
        tags=['Base de conocimiento'],
    )
    def get(self, request):
        parameters = QuestionSelectionParameters(data=request.query_params)
        parameters.is_valid(raise_exception=True)
        filters = parameters.validated_data
        if not request.user.is_active:
            raise PermissionDenied(_('No tienes permiso para consultar las soluciones de este curso.'))
        queryset = Question.objects.filter(
            difficulty=filters['dificultad'], bank__status=QuestionBank.Status.PUBLISHED,
            status=Question.Status.PUBLISHED,
            current_version__is_published=True, current_version__question_id=F('pk'),
        ).exclude(current_version__metadata__has_key='csv_review')
        if 'categoria' in filters:
            from apps.courses.models import CourseCategory
            from apps.duckyclash.knowledge import LMSKnowledgeGateway
            from apps.duckyclash.models import ChallengeSubcategory

            category = get_object_or_404(CourseCategory, pk=filters['categoria'], activa=True)
            subcategory = None
            if 'subcategoria' in filters:
                value = filters['subcategoria']
                subcategories = ChallengeSubcategory.objects.filter(category=category, active=True)
                if value.lstrip('+-').isdecimal():
                    subcategory = get_object_or_404(subcategories, pk=int(value))
                else:
                    matches = list(subcategories.filter(name__iexact=value)[:2])
                    if not matches:
                        raise NotFound(_('No existe una subcategoría activa con ese nombre en la categoría.'))
                    if len(matches) > 1:
                        raise serializers.ValidationError({
                            'subcategoria': _('El nombre es ambiguo. Indica el ID de la subcategoría.'),
                        })
                    subcategory = matches[0]
            candidates = LMSKnowledgeGateway().candidates(
                category_id=category.pk, subcategory_id=subcategory.pk if subcategory else None,
            )
            queryset = queryset.filter(current_version_id__in={
                item.local_version_id for item in candidates if item.difficulty == filters['dificultad']
            })
            courses = {
                bank.course_id: bank.course
                for bank in QuestionBank.objects.filter(questions__in=queryset).select_related('course')
            }
            if any(not can_edit_course(request.user, course) for course in courses.values()):
                raise PermissionDenied(_('No tienes permiso para consultar las soluciones de los cursos de origen.'))
        else:
            module = get_object_or_404(Tema.objects.select_related('curso'), pk=filters['modulo'])
            if not can_edit_course(request.user, module.curso):
                raise PermissionDenied(_('No tienes permiso para consultar las soluciones de este curso.'))
            unit = get_object_or_404(FormativeUnit, pk=filters['unidad_formativa'], module=module)
            queryset = queryset.filter(topic=module, formative_unit=unit, bank__course_id=module.curso_id)
        available = queryset.count()
        requested = filters['numero_preguntas']
        selected = list(queryset.select_related('current_version').order_by('?')[:requested]) if available >= requested else []
        if available < requested or len(selected) < requested:
            return Response({
                'code': 'insufficient_questions',
                'detail': _('No hay suficientes preguntas publicadas para los filtros solicitados.'),
                'solicitadas': requested,
                'disponibles': available if available < requested else len(selected),
            }, status=status.HTTP_409_CONFLICT)
        return Response({
            'filtros': filters, 'disponibles': available, 'devueltas': len(selected),
            'preguntas': SelectedQuestionSerializer(selected, many=True).data,
        })
