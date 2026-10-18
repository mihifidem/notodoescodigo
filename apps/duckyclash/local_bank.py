"""Local DuckyCoins ledger backed only by User.puntos, not an external bank API."""
from dataclasses import asdict

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import OperationalError, transaction
from django.db.models import F

from .bank import BankOutcome, BankResult, BankUnavailable, TransactionalBankGateway, UserPointsBalanceGateway
from .models import Duel, DuelResolution, EconomicOperation, LocalBankReceipt

LOCAL_GATEWAY = 'apps.duckyclash.local_bank.LocalPointsBankGateway'


def payload(request):
    data = asdict(request)
    data['reservation_keys'] = list(request.reservation_keys)
    data['reservation_references'] = list(request.reservation_references)
    return data


class LocalPointsBankGateway(UserPointsBalanceGateway, TransactionalBankGateway):
    def reference(self, operation):
        return f'local-points:{operation.operation_key}'

    def operation(self, request):
        from .economy import operation_request
        try:
            operation = EconomicOperation.objects.get(operation_key=request.key)
        except (EconomicOperation.DoesNotExist, ValidationError, ValueError) as exc:
            raise BankUnavailable('No existe una operación económica válida para esta petición.') from exc
        duel = Duel.objects.select_for_update().get(pk=operation.duel_id)
        operation = EconomicOperation.objects.select_for_update().get(pk=operation.pk)
        if operation_request(operation) != request:
            raise BankUnavailable('La petición no coincide con las condiciones económicas guardadas.')
        operation.full_clean()
        if duel.payment_gateway and duel.payment_gateway != LOCAL_GATEWAY:
            raise BankUnavailable('Este duelo pertenece a otro proveedor económico.')
        return operation, duel

    def receipt_result(self, receipt, request):
        if receipt.request_payload != payload(request):
            raise BankUnavailable('La clave económica ya se utilizó con otras condiciones.')
        outcome = BankOutcome(receipt.outcome)
        return BankResult(request, outcome, self.reference(receipt.operation) if outcome == BankOutcome.CONFIRMED else '')

    def lookup(self, request):
        try:
            with transaction.atomic():
                operation, duel = self.operation(request)
                receipt = LocalBankReceipt.objects.filter(operation=operation).first()
                if (receipt is None and request.kind == EconomicOperation.Kind.RESERVE
                        and duel.bank_mode != Duel.BankMode.RESERVE):
                    # A later local reserve rechecks this locked duel and cannot debit after cancellation.
                    receipt = LocalBankReceipt.objects.create(
                        operation=operation, request_payload=payload(request), outcome=BankOutcome.REJECTED.value,
                    )
                return self.receipt_result(receipt, request) if receipt else BankResult(request, BankOutcome.NOT_FOUND)
        except OperationalError as exc:
            if 'locked' not in str(exc).lower() and 'deadlock' not in str(exc).lower():
                raise
            raise BankUnavailable('La consulta económica local está ocupada. Se conciliará de nuevo.') from exc

    def reserve(self, request):
        return self.apply(request, EconomicOperation.Kind.RESERVE)

    def release(self, request):
        return self.apply(request, EconomicOperation.Kind.RELEASE)

    def settle(self, request):
        return self.apply(request, EconomicOperation.Kind.SETTLE)

    def apply(self, request, kind):
        if request.kind != kind:
            raise BankUnavailable('El tipo de operación económica no corresponde.')
        try:
            with transaction.atomic():
                operation, duel = self.operation(request)
                receipt = LocalBankReceipt.objects.filter(operation=operation).first()
                if receipt:
                    return self.receipt_result(receipt, request)
                if kind == EconomicOperation.Kind.RESERVE:
                    outcome, hold_state = self.reserve_points(operation, duel, request)
                else:
                    outcome, hold_state = self.finish_holds(operation, duel, request), ''
                receipt = LocalBankReceipt.objects.create(
                    operation=operation, request_payload=payload(request), outcome=outcome.value, hold_state=hold_state,
                )
                return self.receipt_result(receipt, request)
        except OperationalError as exc:
            if 'locked' not in str(exc).lower() and 'deadlock' not in str(exc).lower():
                raise
            raise BankUnavailable('La operación económica local está ocupada. Se conciliará de nuevo.') from exc

    def reserve_points(self, operation, duel, request):
        if request.reservation_keys or request.reservation_references or request.winner:
            raise BankUnavailable('La reserva contiene condiciones no válidas.')
        if duel.bank_mode != Duel.BankMode.RESERVE or duel.status != Duel.Status.PENDING_RESERVATION:
            return BankOutcome.REJECTED, ''
        changed = get_user_model().objects.filter(
            pk=operation.player_id, is_active=True, puntos__gte=request.amount,
        ).update(puntos=F('puntos') - request.amount)
        return (BankOutcome.CONFIRMED, LocalBankReceipt.HoldState.HELD) if changed else (BankOutcome.REJECTED, '')

    def finish_holds(self, operation, duel, request):
        settle = request.kind == EconomicOperation.Kind.SETTLE
        expected_count = 2 if settle else 1
        if (len(request.reservation_keys) != expected_count
                or len(set(request.reservation_keys)) != expected_count
                or len(request.reservation_references) != expected_count):
            raise BankUnavailable('La operación no identifica las reservas necesarias.')
        holds = list(LocalBankReceipt.objects.select_for_update().select_related('operation').filter(
            operation__operation_key__in=request.reservation_keys,
            operation__duel=duel, operation__kind=EconomicOperation.Kind.RESERVE,
            outcome=BankOutcome.CONFIRMED.value, hold_state=LocalBankReceipt.HoldState.HELD,
        ).order_by('operation__player_id'))
        if len(holds) != expected_count:
            raise BankUnavailable('Faltan reservas locales activas; no se puede devolver ni pagar el bote.')
        references = dict(zip(request.reservation_keys, request.reservation_references))
        if any(references[str(hold.operation.operation_key)] != self.reference(hold.operation) for hold in holds):
            raise BankUnavailable('Las referencias de reservas no corresponden al libro local.')
        if sum(hold.operation.amount for hold in holds) != request.amount:
            raise BankUnavailable('El importe no coincide con las reservas.')
        players = {duel.agreement.creator_id, duel.agreement.opponent_id}
        if settle:
            resolution = DuelResolution.objects.get(duel=duel)
            if (duel.bank_mode != Duel.BankMode.PAYOUT or duel.status != Duel.Status.SETTLEMENT_PENDING
                    or resolution.is_draw or str(resolution.winner_id) != request.winner
                    or {hold.operation.player_id for hold in holds} != players):
                raise BankUnavailable('No hay un resultado autoritativo válido para pagar este bote.')
            recipient = resolution.winner_id
            state = LocalBankReceipt.HoldState.SETTLED
        else:
            if (duel.bank_mode not in (Duel.BankMode.CANCEL, Duel.BankMode.DRAW)
                    or holds[0].operation.player_id != operation.player_id or request.winner):
                raise BankUnavailable('No hay una devolución autorizada para este jugador.')
            recipient = operation.player_id
            state = LocalBankReceipt.HoldState.RELEASED
        claimed = LocalBankReceipt.objects.filter(
            pk__in=[hold.pk for hold in holds], hold_state=LocalBankReceipt.HoldState.HELD,
        ).update(hold_state=state)
        if claimed != expected_count:
            raise BankUnavailable('Otra operación ya ha consumido alguna de las reservas.')
        if get_user_model().objects.filter(pk=recipient).update(puntos=F('puntos') + request.amount) != 1:
            raise BankUnavailable('No se ha podido abonar el saldo al destinatario.')
        return BankOutcome.CONFIRMED


def points_movement_total(player):
    """Audit adjustment for administrative balance rebuilds, never a stored balance."""
    from django.db.models import Q, Sum
    receipts = LocalBankReceipt.objects.filter(outcome=BankOutcome.CONFIRMED.value)
    spent = receipts.filter(
        operation__player=player, operation__kind=EconomicOperation.Kind.RESERVE,
    ).aggregate(total=Sum('operation__amount'))['total'] or 0
    credited = receipts.filter(
        Q(operation__player=player, operation__kind=EconomicOperation.Kind.RELEASE)
        | Q(operation__duel__resolution__winner=player, operation__kind=EconomicOperation.Kind.SETTLE),
    ).aggregate(total=Sum('operation__amount'))['total'] or 0
    return credited - spent
