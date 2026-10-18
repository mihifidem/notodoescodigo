from django.apps import AppConfig


class DataAdminConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.data_admin'
    verbose_name = 'Gestión de datos'
