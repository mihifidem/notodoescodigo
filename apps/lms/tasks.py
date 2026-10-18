from celery import shared_task

from .lifecycle import process_lms_lifecycle


@shared_task
def advance_lms_lifecycle():
    return process_lms_lifecycle()
