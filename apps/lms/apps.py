from django.apps import AppConfig


class LmsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.lms'
    verbose_name = 'LMS'

    def ready(self):
        from . import signals  # noqa: F401
