from django.apps import AppConfig


class EnergyConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.energy'
    verbose_name = 'Energía Ducky'

    def ready(self):
        from . import signals  # noqa: F401
