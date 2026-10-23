from django.apps import AppConfig


class NexusConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.nexus'
    verbose_name = 'Nexus 2026'

    def ready(self):
        from . import versioned_signals  # noqa: F401
