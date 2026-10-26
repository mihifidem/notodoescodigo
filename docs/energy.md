# Energía Ducky (00C)

Sistema común de energía para herramientas IA. Vive en `apps/energy`; **solo `apps/energy/services.py` modifica saldos**. Ningún módulo debe tocar `EnergyWallet`, lotes o movimientos directamente.

## Componentes

| Modelo | Uso |
| --- | --- |
| `EnergyPolicy` | Política por plan (`free`, `plus`, `pro`): capacidad, recuperación periódica (cantidad + intervalo), límite diario por actividad (% de capacidad: 10/15/25). |
| `EnergyToolCost` | Coste por herramienta (`code` tipo slug, p. ej. `letters-generate`), variantes, pausa, presupuesto interno del proveedor, duración máxima, concurrencia y entitlement de servicio que la cubre. |
| `EnergyRewardRule` | Recompensa por actividad (tema completado, quiz aprobado, módulo LMS…). |
| `EnergyRechargeProduct` | Recarga vinculada a un `ServiceProduct` de 00B (`energy_units`, caducidad). |
| `EnergyConfigVersion` | Instantánea versionada de cada cambio de configuración (quién, cuándo, motivo). |
| `EnergyWallet` | Saldo recuperable disponible/reservado y ancla de recuperación. |
| `EnergyPurchasedLot` | Lote comprado: cantidad, disponible, reservado, consumido, retirado, caducidad. |
| `EnergyOperation` / `EnergyReservation` | Operación de herramienta y su reserva por componente (recuperable o lote). |
| `EnergyActivityGrant` | Recompensa de actividad concedida (idempotente por evento). Resultado: `granted`, `partial`, `daily_limit`, `full`, `unavailable`. |
| `EnergyLedgerEntry` | Libro de movimientos inmutable con clave idempotente única. Es la fuente de verdad para conciliar. |

El seed (`0002_seed_configuration`) crea las políticas **desactivadas y sin capacidad**, las reglas **desactivadas y sin importe** y 6 herramientas **desactivadas y sin coste**. No se inventan precios ni capacidades: la administración debe configurarlos.

## Reglas principales

- La energía recuperable vuelve a la capacidad del plan por recuperación periódica; nunca la supera. Si está llena o la política no está configurada no se acumula atraso.
- La actividad recupera energía con un tope diario (10 % free, 15 % plus, 25 % pro) calculado en el día de `ENERGY_DAY_TIMEZONE` (Europe/Madrid).
- La energía comprada se consume después de la recuperable, por orden de caducidad más próxima. No caduca la recuperable; los lotes caducan según su producto.
- El coste se reserva antes de ejecutar y solo se consume si hay un resultado utilizable guardado; si falla, se libera.
- Un servicio comprado que cubre la herramienta (entitlement) la ejecuta sin consumir energía (`covered`).
- Coste desconocido **no es cero**: una herramienta sin coste está bloqueada.

## API de servicio

```python
from apps.energy import services

services.get_status(user)                 # dict completo (status_payload() para JSON)
services.estimate(user, 'ats-rephrase')   # coste, motivo, cobertura
services.module_gate(user, 'nexus-plan')  # {'available', 'reason', 'message', 'energy_cost'}
services.run_tool(user, code, idempotency_key=key, execute=fn)  # reservar → ejecutar → confirmar/liberar
services.emit_activity_on_commit(user.pk, source, event_key)    # recompensa tras commit
services.reconcile_operations(); services.reconcile_wallet(user)
services.credit_purchase(order, line)     # lo llama 00B tras pago verificado
services.revert_lot_for_refund(lot_id, actor=admin, reason='...')
services.adjust(actor=admin, user=u, amount=Decimal('5'), reason='...', key='...')
```

### Contrato para módulos

1. Antes de ofrecer IA, llamar a `module_gate(user, tool_code)` y mostrar `message` si `available` es `False`.
2. Ejecutar con `run_tool(...)`; `execute(operation)` debe **guardar el resultado** y devolver `ToolResult(usable=True|False, payload, provider={...})`. Un reintento con la misma `idempotency_key` no cobra dos veces.
3. Registrar el ejecutor en `settings.ENERGY_TOOL_EXECUTORS = {'tool-code': 'ruta.al.ejecutor'}`. Sin ejecutor la herramienta queda en `provider_unavailable`.
4. Las recompensas se emiten con `emit_activity_on_commit` (ya conectado por señales a temas, quizzes de cursos, quizzes LMS y módulos LMS).

Orden de comprobación de una herramienta: no existe → desactivada → sin coste → en pausa → sin proveedor → sin presupuesto → presupuesto agotado → ok.

## Integraciones actuales

- **07 Cartas**, **08 ATS**, **09 Nexus**: consultan `module_gate` y siguen bloqueados con el motivo real (hoy `tool_disabled`), sin reservar energía. Cuando exista proveedor, la ejecución debe pasar por `run_tool`; mientras tanto `module_gate` devuelve `module_execution_pending` aunque la configuración sea válida.
- **10 Tech DNA**: solo informativo (`techdna-help` sembrado), sin conexión.
- **00B Facturación**: las líneas de recarga se acreditan con `credit_purchase` al confirmar pago; los pedidos de coste cero de recarga se bloquean.
- **UI**: indicador en la barra superior (`templates/energy/_widget.html`), página `/energia/`, bloque en el dashboard y tarjeta en Mi cuenta.

## Rutas

| Ruta | Descripción |
| --- | --- |
| `/energia/` | Detalle: saldo, actividad de hoy, reglas, herramientas, recargas, lotes, movimientos. |
| `/energia/estado/` | JSON del indicador. |
| `/energia/estimar/?tool=…&variant=…` | JSON de estimación. |
| `/energia/admin/ajustar/` | Ajuste manual (admin de plataforma o permiso `energy.adjust_energy`). |
| `/admin/energy/` | Configuración versionada y consulta de saldos/movimientos. |

## Guía de configuración (admin)

1. **Políticas**: rellenar capacidad, cantidad e intervalo de recuperación y activar. Cada guardado pide motivo y crea versión.
2. **Herramientas**: definir coste (y variantes), presupuesto interno y periodo, duración máxima, concurrencia; activar cuando haya ejecutor.
3. **Reglas de actividad**: fijar importe y activar.
4. **Recargas**: crear `ServiceProduct` en 00B con precio real y enlazar `EnergyRechargeProduct` con sus unidades.
5. Acciones: liberar operaciones atascadas (operaciones), revertir lote por devolución (lotes), ajustar/conciliar (monederos).

## Conciliación

- Tarea Celery `apps.energy.tasks.reconcile_energy` cada 300 s (`energy-reconciliation` en `CELERY_BEAT_SCHEDULE`): libera operaciones reservadas que superan 2× la duración máxima.
- Comando: `python manage.py energy_reconcile` (libera operaciones y verifica saldos contra el libro).

## Bloqueos conocidos

- No existe checkout de pago único: las recargas no se pueden comprar aún (Stripe solo gestiona suscripciones).
- No hay proveedores IA para 07–09: siguen bloqueados.
- Pendiente de migrar a energía: tutor IA, avatar, evaluaciones, generador del blog, IA del campus LMS y `/api/ai/generate`.
- Las devoluciones de recargas se revierten manualmente desde admin (no hay servicio de confirmación de devolución que lo dispare).
