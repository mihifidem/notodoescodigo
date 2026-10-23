"""Private knowledge contract and the real, opt-in LMS integration."""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import F
from django.utils.module_loading import import_string

from apps.lms.knowledge import question_version_initial
from apps.lms.models import Question, QuestionBank, QuestionVersion

from apps.lms.policies import can_edit_course

from .models import KnowledgePublication, SubcategoryQuestionSource

logger = logging.getLogger(__name__)


class KnowledgeUnavailable(Exception):
    pass


class DistractorsUnavailable(KnowledgeUnavailable):
    pass


@dataclass(frozen=True)
class KnowledgeQuestion:
    provider: str
    identity: str
    revision: str
    category_id: int
    subcategory_id: int | None
    prompt: str
    difficulty: int
    options: tuple[str, ...]
    validation: dict
    local_version_id: int | None = None


@dataclass(frozen=True)
class VerifiedDistractor:
    text: str
    evidence: str


class KnowledgeGateway(ABC):
    @abstractmethod
    def candidates(self, *, category_id: int, subcategory_id: int | None) -> list[KnowledgeQuestion]:
        """Server-only candidates. Explicitly authorized, published, single-answer questions."""

    @abstractmethod
    def validate(self, question: KnowledgeQuestion, selected: str) -> bool:
        """Use immutable revision/private data. Bounded I/O; raise KnowledgeUnavailable
        on transport failure, never return False for an unavailable validator.
        """

    def distractors(self, question: KnowledgeQuestion, count: int) -> list[VerifiedDistractor]:
        """Only authoritative, reviewed false alternatives with provenance.

        A failed answer comparison alone is NOT certification of semantic falsity.
        Do not generate plausible alternatives using an LLM or other questions.
        """
        raise DistractorsUnavailable('El proveedor no dispone de distractores certificados.')


class LMSKnowledgeGateway(KnowledgeGateway):
    def candidates(self, *, category_id, subcategory_id):
        sources = SubcategoryQuestionSource.objects.filter(
            subcategory__category_id=category_id,
        ).select_related('subcategory__category', 'approved_by').prefetch_related(
            'levels__bank__course', 'levels__approved_by',
        )
        if subcategory_id is not None:
            sources = sources.filter(subcategory_id=subcategory_id)
        sources = list(sources)
        publications = KnowledgePublication.objects.filter(
            active=True, category_id=category_id, category__activa=True,
            version__is_published=True, version__question__status=Question.Status.PUBLISHED,
            version__question__bank__status=QuestionBank.Status.PUBLISHED,
            version__question__question_type=Question.Type.SINGLE_CHOICE,
        ).exclude(version__metadata__has_key='csv_review').select_related('version__question', 'subcategory')
        if subcategory_id is not None:
            publications = publications.filter(subcategory_id=subcategory_id, subcategory__active=True)
        # An explicit source is authoritative, including when disabled or empty.
        publications = publications.exclude(subcategory_id__in=[source.subcategory_id for source in sources])
        items = []
        for publication in publications:
            if publication.subcategory_id and not publication.subcategory.active:
                continue
            item = self.candidate(publication.version, publication.category_id, publication.subcategory_id)
            if item:
                items.append(item)
        for source in sources:
            if (not source.active or not source.subcategory.active or not source.approved_by.is_active
                    or not source.subcategory.category.activa):
                continue
            for level in source.levels.all():
                if (not level.active or not level.approved_by.is_active
                        or level.bank.status != QuestionBank.Status.PUBLISHED
                        or not can_edit_course(level.approved_by, level.bank.course)):
                    continue
                versions = QuestionVersion.objects.filter(
                    question__bank=level.bank, question__status=Question.Status.PUBLISHED,
                    question__question_type=Question.Type.SINGLE_CHOICE,
                    question__current_version_id=F('pk'), is_published=True,
                ).exclude(metadata__has_key='csv_review').select_related('question')
                for version in versions:
                    item = self.candidate(version, category_id, source.subcategory_id)
                    if item:
                        items.append(item)
        return items

    def candidate(self, version, category_id, subcategory_id):
        try:
            data = question_version_initial(version)
        except ValidationError:
            logger.warning('Invalid published Clash knowledge version %s', version.pk)
            return None
        return KnowledgeQuestion(
            provider='lms', identity=str(version.question_id), revision=str(version.pk),
            category_id=category_id, subcategory_id=subcategory_id,
            prompt=data['prompt'], difficulty=version.question.difficulty,
            options=tuple(version.options),
            validation={'correct_answers': version.correct_answers},
            local_version_id=version.pk,
        )

    def validate(self, question, selected):
        if question.provider != 'lms':
            raise KnowledgeUnavailable('La pregunta pertenece a otro proveedor.')
        # Freeze grading to the presented revision, even if an editor later changes LMS data.
        version = QuestionVersion(correct_answers=question.validation['correct_answers'])
        source = Question(question_type=Question.Type.SINGLE_CHOICE, points=1)
        return version.evaluate_response(selected, question=source)['correct']


def get_knowledge_gateway() -> KnowledgeGateway:
    path = getattr(settings, 'DUCKYCLASH_KNOWLEDGE_GATEWAY', '')
    if not path:
        raise KnowledgeUnavailable('El Banco del Conocimiento no está configurado.')
    try:
        gateway = import_string(path)()
    except (ImportError, AttributeError) as exc:
        logger.error('Invalid DuckyClash knowledge gateway configuration')
        raise KnowledgeUnavailable('El Banco del Conocimiento no está disponible.') from exc
    if not isinstance(gateway, KnowledgeGateway):
        raise KnowledgeUnavailable('El proveedor no implementa el contrato seguro de preguntas.')
    return gateway


def validate_candidate(item: KnowledgeQuestion, gateway: KnowledgeGateway) -> None:
    if (not isinstance(item, KnowledgeQuestion)
            or not all(isinstance(value, str) and value.strip() and len(value) <= limit for value, limit in (
                (item.provider, 80), (item.identity, 200), (item.revision, 100), (item.prompt, 10000),
            ))
            or type(item.difficulty) is not int or item.difficulty not in (1, 2, 3)
            or not isinstance(item.options, tuple) or len(item.options) != 4
            or not all(isinstance(option, str) and option.strip() and len(option) <= 2000 for option in item.options)
            or len({option.strip().casefold() for option in item.options}) != 4
            or not isinstance(item.validation, dict)):
        raise KnowledgeUnavailable('El proveedor devolvió una pregunta no válida.')
    checks = [gateway.validate(item, option) for option in item.options]
    if any(type(correct) is not bool for correct in checks) or sum(checks) != 1:
        raise KnowledgeUnavailable('La pregunta no tiene una única respuesta correcta verificable.')
