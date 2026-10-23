# Facturación, membresías y prestaciones

## Estado implementado

- `BillingConfiguration` guarda versiones de precios en EUR. Una versión debe
  nacer como borrador, pasar a aprobada y activarse en un cambio posterior.
  Los precios empiezan vacíos; una versión aprobada/activa no se edita.
- `MembershipContract` concede el nivel únicamente durante su vigencia. El
  resolvedor de acceso combina contratos vigentes, concesiones administrativas
  auditables y la membresía heredada sin conceder roles profesionales.
- Pedidos, líneas, cargos, intentos, pagos confirmados, facturas, devoluciones,
  bonos y eventos tienen registros separados. La interfaz solo consulta
  registros cuyo propietario es la cuenta autenticada.
- Los pedidos sin coste solo se completan cuando la tarifa configurada es
  explícitamente cero o un bono concedido cubre el 100 %. Los servicios sin
  coste requieren entrega automática y un derecho de prestación configurado.
  Las claves idempotentes se conservan en el pedido, cargo y derecho emitido.
- La página **Membresías y facturación** informa tarifas activas, contratos,
  pedidos, cargos, pagos verificados, facturas y bonos. No expone datos de otras
  cuentas. Incluye mejoras a PLUS/PRO mediante suscripciones Stripe, únicamente
  cuando hay tarifas aprobadas y un proveedor configurado.

## Suscripciones y mejoras de plan

La página utiliza el diseño Tech Modern Blue, con cabecera de plan efectivo,
navegación por secciones, tablas adaptables con desplazamiento horizontal y
soporte para el tema claro/oscuro del portal. Los estilos están limitados a
`.billing-app` en `static/css/billing.css`. La presentación no altera las
condiciones, permisos, consentimiento ni formularios de contratación.

- FREE puede mejorar a PLUS o PRO; PLUS puede mejorar a PRO. No se ofrecen
  descensos ni compras repetidas del mismo plan vigente.
- Periodos: mensual, semestral y anual, siempre con renovación automática y
  aceptación explícita del importe y condiciones. Las ofertas firmadas caducan a
  los 30 minutos; el servidor comprueba propietario, tarifa vigente y plan.
- Checkout alojado en Stripe: solo tarjeta y EUR. No se almacenan datos de
  tarjetas. La vuelta del navegador muestra el estado, pero nunca confirma pagos.
- Una mejora cobra el periodo completo, sin prorrateo ni devolución automática.
  Tras confirmar el cobro se cancela la suscripción anterior del portal sin
  emitir prorrateos. Los contratos anteriores conservan su periodo pagado.
- Cada factura pagada genera un pago confirmado y un contrato para las fechas
  facturadas por Stripe. Un webhook retrasado no desplaza la vigencia. Duplicados
  con distintos IDs de evento no duplican el pago de una misma factura.
- Los fallos no prolongan el acceso. Cancelar la renovación conserva la vigencia
  pagada. Antes del vencimiento, una suscripción activa con cancelación programada
  permite **Reactivar renovación automática**, con aceptación explícita del importe
  y periodicidad. Se retira `cancel_at_period_end` en Stripe y se actualiza el contrato
  solo tras su confirmación; no se crea un checkout, cobro ni periodo nuevo. No se
  reactivan suscripciones vencidas, canceladas definitivamente o con pago pendiente.
  Cancelar/reactivar/cancelar usa claves Stripe distintas por transición.
  Se pueden continuar o cancelar contrataciones pendientes. Un checkout
  expirado libera la solicitud mediante webhook. Ante errores de red, se reintenta
  la misma solicitud, no se crea otro pedido. Solicitudes sin referencia remota
  con más de 23 horas requieren conciliación administrativa para no reutilizar
  una clave que Stripe pudiera haber purgado.
- No se aplican bonos a suscripciones ni se admiten créditos, descuentos,
  impuestos añadidos o modificaciones manuales de tarifas en Stripe: una factura
  distinta del contrato se rechaza con un error registrado para conciliación.

### Configuración de Stripe

1. Aplicar `users.0017_billing_subscriptions`.
2. Configurar `STRIPE_SECRET_KEY`, `STRIPE_BILLING_WEBHOOK_SECRET` y
   `BILLING_PUBLIC_BASE_URL` (origen HTTPS del portal; HTTP local solo con DEBUG).
   Las claves van en el entorno, nunca en la base de datos ni en el repositorio.
3. Crear, aprobar y activar una versión de `BillingConfiguration` con los
   importes comerciales finales de PLUS/PRO. No se precargan precios.
4. En `PaymentProviderConfiguration`, seleccionar Stripe y el entorno
   correspondiente a la clave, habilitar `supports_recurring`, establecer
   `methods=["card"]`, `currencies=["EUR"]` y activar `enabled`. Mantener
   `supports_one_time` y `supports_refunds` desactivados.
5. Registrar en la cuenta Stripe el endpoint público obtenido con la ruta Django
   `users:stripe_billing_webhook` (`facturacion/stripe/webhook/` bajo el prefijo
   de usuarios) y estos eventos:
   `invoice.payment_succeeded`, `invoice.payment_failed`,
   `customer.subscription.updated`, `customer.subscription.deleted` y
   `checkout.session.expired`. Usar el secreto de ese endpoint.
6. Verificar primero con la cuenta de pruebas: primera compra, renovación,
   autenticación bancaria, mejora, cancelación, expiración y reintentos.
   Las pruebas automáticas simulan Stripe y comprueban firmas reales de
   webhook; no equivalen a una transacción bancaria certificada.

El webhook verifica firma, entorno, referencia, moneda, importe y periodo;
consulta además la factura y la suscripción actuales por API. Los errores del
proveedor responden 503 para reintento, y las discrepancias se registran y
responden 400. Las suscripciones se consultan en administración, sin edición
manual de referencias. Cambios de entorno con suscripciones existentes requieren
un procedimiento de conciliación, no alternar claves en una cuenta activa.

En PostgreSQL, la consulta bloqueada de la suscripción no une la relación
opcional `replaces`: `FOR UPDATE` no admite bloquear el lado nullable de un
`OUTER JOIN`. La suscripción anterior se consulta por separado, manteniendo
el bloqueo por usuario. Si un evento falló por este error, debe reenviarse o
conciliarse desde Stripe tras actualizar el código; no debe repetirse el pago.

## Bloqueos comerciales y técnicos

- PayPal, Bizum y los pagos únicos positivos permanecen desactivados. El adaptador
  Stripe implementado aquí es exclusivamente para suscripciones de membresía.
  Sin credenciales, webhook, tarifas y activación administrativa, tampoco se
  permite iniciar esas suscripciones.
- Los retornos del navegador de Stripe/PayPal de Nexus ya no marcan un pago como
  confirmado. Sus checkouts están temporalmente deshabilitados para evitar
  iniciar cobros sin conciliación server-to-server. Los registros de pago
  anteriores no se alteran. El acceso de prueba de Nexus no crea pagos
  simulados persistentes.
- `confirm_verified_payment` sigue siendo un servicio interno. El webhook de
  Stripe lo invoca después de validar y conciliar la factura. No hay devolución
  automatizada, cambio de tarjeta ni herramienta general de conciliación.
- La emisión de factura fiscal está desactivada. No se inventan impuestos,
  datos del emisor, series ni numeración.
- Servicios de entrega humana o IA quedan sin checkout: requieren un flujo de
  ejecución, coste y, para IA, cuota aprobados. Las ofertas comerciales no se
  precargan.
- No se creó ni ejecutó un comando destructivo para reiniciar cuentas de
  prueba. La lista y el alcance exactos de las trece cuentas referidas en el
  requerimiento no están disponibles en el repositorio/historial accesible; el
  comando no debe apuntar a la base de datos actual ni borrar datos financieros.

## Administración y migración

El permiso Django `users.manage_billing` controla la configuración económica y
los registros de facturación; `users.manage_access_grants` controla
concesiones administrativas, y `users.grant_vouchers` permite asignar bonos.
Los cambios administrativos generan `BillingEvent`. Los registros financieros
confirmados no se pueden eliminar desde el administrador. La migración
`users.0013_billingorder_charge_confirmedpayment_and_more` es incremental y no
borra las tablas existentes.

Aplicar en cada entorno mediante el procedimiento normal:

```powershell
.\.venv\Scripts\python.exe manage.py migrate users
```

Antes de configurar una tarifa se debe aprobar su importe comercial final.
La validación administrativa solo permite activar la configuración Stripe
recurrente descrita arriba. No activa proveedores ni cambia tarifas por defecto.

```powershell
.\.venv\Scripts\python.exe manage.py test apps.users.test_subscriptions apps.users.test_billing --noinput
```
