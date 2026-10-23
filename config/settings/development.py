"""Development settings."""
from .base import *  # noqa

DEBUG = True

# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.sqlite3',
#         'NAME': BASE_DIR / 'db.sqlite3',
#     }
# }

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv(
            "POSTGRES_DB",
            "ntec_db",
        ),
        "USER": os.getenv(
            "POSTGRES_USER",
            "ntec_user",
        ),
        "PASSWORD": os.getenv(
            "POSTGRES_PASSWORD",
            "",
        ),
        "HOST": os.getenv(
            "POSTGRES_HOST",
            "localhost",
        ),
        "PORT": os.getenv(
            "POSTGRES_PORT",
            "5433",
        ),
    }
}


# Use console backend in dev so emails appear in the terminal
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# Allow all in dev
CORS_ALLOW_ALL_ORIGINS = True

# Use in-memory cache in dev (no Redis required)
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }
}

INTERNAL_IPS = ['127.0.0.1']

# Local spendable points are real balances, not a fake external DuckyBank.
if not DUCKYCLASH_BANK_GATEWAY:
    DUCKYCLASH_PAYMENT_GATEWAY = DUCKYCLASH_PAYMENT_GATEWAY or 'apps.duckyclash.local_bank.LocalPointsBankGateway'
DUCKYCLASH_BANK_GATEWAY = DUCKYCLASH_BANK_GATEWAY or 'apps.duckyclash.bank.UserPointsBalanceGateway'

# Explicitly authorized local demo; production never inherits this setting.
DUCKYCLASH_ECOMOTOR_GATEWAY = DUCKYCLASH_ECOMOTOR_GATEWAY or 'apps.duckyclash.ecomotor.DevelopmentFakeEcoMotor'
DUCKY_ARENAS_ECOMOTOR_PROGRESS_PROVIDER = (
    DUCKY_ARENAS_ECOMOTOR_PROGRESS_PROVIDER
    or 'apps.ducky_arenas.core.ecomotor_progress.DevelopmentProgressionSimulator'
)
DUCKY_ARENAS_ECOMOTOR_SIMULATED_PROGRESS = {}

# Django debug toolbar (optional – install separately if desired)
# INSTALLED_APPS += ['debug_toolbar']
# MIDDLEWARE.insert(0, 'debug_toolbar.middleware.DebugToolbarMiddleware')
