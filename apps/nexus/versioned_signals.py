from django.db.models.deletion import ProtectedError
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from .models import AnswerOption, NexusVersion, Question


@receiver(pre_delete, sender=Question)
@receiver(pre_delete, sender=AnswerOption)
def preserve_editorial_references(sender, instance, **kwargs):
    question = instance if sender is Question else instance.question
    versions = NexusVersion.objects.filter(published_at__isnull=False)
    for version in versions.iterator():
        questions = version.snapshot.get('questions', [])
        if any(q['id'] == question.pk for q in questions):
            raise ProtectedError(
                'Referencia de una versión publicada: desactiva o crea otra versión; no elimines su contenido.',
                [instance],
            )
