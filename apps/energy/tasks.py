from celery import shared_task

from . import services
from .models import EnergyWallet


@shared_task
def reconcile_energy():
    """Libera trabajos interrumpidos y aplica recuperación/caducidad. Seguro de repetir."""
    released = services.reconcile_operations()
    for wallet in EnergyWallet.objects.select_related('user').iterator():
        services.refresh_recovery(wallet.user)
    return len(released)
