from django.apps import AppConfig


class GamificationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.gamification'
    verbose_name = 'Gamificación'

    def ready(self):
        import apps.gamification.signals  # noqa
