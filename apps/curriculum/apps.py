from django.apps import AppConfig


class CurriculumConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.curriculum'
    verbose_name = 'Curriculum Vitae'

    def ready(self):
        import apps.curriculum.signals  # noqa
