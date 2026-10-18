"""Adapter to the real local Arenas inventory, not a second inventory."""

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import F
from django.utils import timezone

from apps.ducky_arenas.models import ArenaItem, UserArenaInventory, UserProfileArenas


@dataclass(frozen=True)
class Stock:
    item_id: int
    quantity: int


class ShopGateway:
    def stock(self, player_id: int, code: str) -> Stock:
        entry = UserArenaInventory.objects.select_related('item').filter(
            profile__user_id=player_id, item__code=code, item__isActive=True,
            item__itemType=ArenaItem.ArenaItemType.COMPLEMENT, isEquipped=False,
        ).first()
        if entry is None or entry.quantity < 1:
            raise ValidationError('No posees existencias disponibles de este objeto.')
        return Stock(item_id=entry.item_id, quantity=entry.quantity)

    def consume(self, player_id: int, code: str) -> int:
        if not connection.in_atomic_block:
            raise RuntimeError('El consumo debe compartir transacción con su efecto y recibo idempotente.')
        # Reward grants already serialize on this same profile lock.
        UserProfileArenas.objects.select_for_update().get(user_id=player_id)
        stock = self.stock(player_id, code)
        changed = UserArenaInventory.objects.filter(
            profile__user_id=player_id, item_id=stock.item_id, quantity__gte=1,
            isEquipped=False, item__isActive=True,
        ).update(quantity=F('quantity') - 1, updatedAt=timezone.now())
        if changed != 1:
            raise ValidationError('El stock ha cambiado; recarga el inventario.')
        return stock.item_id
