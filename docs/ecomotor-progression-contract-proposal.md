# Propuesta de contrato de lectura EcoMotor–Duki Arenas

## Estado

Pendiente de revisión y aprobación por el equipo propietario de EcoMotor. El
proyecto actual contiene un adaptador interno de EcoMotor para calcular daño en
DuckyClash, pero no publica un contrato de lectura de XP ni de atributos. Por
ello, Duki Arenas no configura una URL HTTP ni asume una ruta externa.

## Responsabilidades

- EcoMotor conserva la autoridad sobre XP, atributos y su cálculo.
- Duki Arenas solo consulta y valida una instantánea; no modifica XP ni atributos.
- La identidad consultada es el usuario autenticado de la plataforma, identificado
  por su ID estable. No se aceptan XP, atributos ni revisiones enviados por Phaser.
- Los requisitos de desbloqueo se leen de `HistoricalEra.required_xp`.
- El Core registra desbloqueos confirmados en `PlayerEraProgress`, pero no almacena
  una copia de los totales oficiales.

## Interfaz propuesta

El equipo EcoMotor debe acordar el mecanismo de transporte, la autenticación y la
operación de lectura. La interfaz lógica que espera el adaptador del Core es:

```text
get_player_progress(user_id) -> ProgressSnapshot
```

`ProgressSnapshot` contiene:

| Campo | Tipo | Requisito |
|---|---|---|
| `user_id` | string | Debe coincidir con el usuario consultado. |
| `xp_total` | entero no negativo | XP acumulada oficial usada para determinar épocas. |
| `attributes` | objeto o `null` | Si está presente, contiene los seis valores enteros no negativos `attack`, `defense`, `speed`, `intelligence`, `logic` y `quack`. |
| `revision` | entero no negativo | Monótono por usuario; una respuesta con revisión menor no puede reemplazar una instantánea más nueva. |
| `updated_at` | fecha/hora ISO-8601 con zona horaria | Momento en que EcoMotor calculó la instantánea. |

El adaptador actual implementa esta interfaz mediante la opción Django
`DUCKY_ARENAS_ECOMOTOR_PROGRESS_PROVIDER`. No implica que EcoMotor ofrezca ya una
API concreta: el proveedor aprobado traducirá el mecanismo de transporte
acordado a esta interfaz.

## Comportamiento ante fallos

- El proveedor debe tener timeout finito y no hacer llamadas dentro de una
  transacción de escritura.
- Respuestas incompletas, identificadores discordantes, revisiones inválidas y
  atributos parciales se consideran errores del contrato.
- El Core muestra el estado de indisponibilidad y mantiene los desbloqueos ya
  confirmados. Un fallo nunca concede un nuevo acceso.
- Las lecturas válidas se almacenan temporalmente en la caché Django configurada;
  producción debe usar un backend compartido entre instancias.
- La simulación incluida solo se carga en desarrollo (`DEBUG=True`) y lee datos
  de `DUCKY_ARENAS_ECOMOTOR_SIMULATED_PROGRESS`. Nunca usa los campos históricos
  `UserProfileArenas.xp`, `UserProfileArenas.stats` ni `User.puntos_totales`.

## Temas que EcoMotor debe confirmar

1. Operación y transporte de consulta (sin ruta asumida por el Core).
2. Mecanismo de autenticación servicio-a-servicio y autorización por usuario.
3. Semántica exacta de XP acumulada y garantías de monotonía de `revision`.
4. Si puede devolver los seis atributos en la misma instantánea y con qué
   consistencia.
5. Límites de tasa, timeout acordado, errores recuperables y estrategia de
   reintento.

El proveedor de producción no debe activarse hasta que estos puntos y el esquema
de respuesta estén aprobados por el equipo EcoMotor.

## Profesiones y distribución de atributos

El Core presenta las prioridades cualitativas configuradas en `Profession`.
Estas prioridades no son cantidades, no sustituyen los atributos oficiales y
no conceden ventajas por sí mismas. La página profesional solo guarda una
selección en estado `draft`; no altera `UserProfileArenas.stats`, `duck_specialty`
ni los valores de EcoMotor.

La lectura de progresión existente no informa por sí sola de puntos distribuibles
ni permite enviar una asignación. Por tanto, la consulta de puntos, la propuesta
numérica, la distribución manual y la confirmación permanecen desactivadas. El
Core no marca una selección como confirmada sin respuesta verificable de EcoMotor.

Antes de habilitar ese flujo, ambos equipos deben aprobar un contrato que defina,
como mínimo:

1. La lectura autoritativa de puntos disponibles, valores actuales y máximos por
   atributo, con revisión o versión para evitar asignar contra datos obsoletos.
2. La operación que valida una asignación y la que la aplica, con identidad del
   jugador obtenida del servidor y sin aceptar saldos afirmados por el navegador.
3. Una clave idempotente, estados de operación y errores recuperables para
   reintentar sin duplicar puntos.
4. La respuesta que confirma el resultado y la revisión de atributos resultante.
5. El comportamiento ante fallos y la conciliación entre los datos del Core y
   los de EcoMotor.

No se presupone una URL, transporte ni API para estas operaciones. El adaptador
de asignación solo debe incorporarse después de que el equipo propietario de
EcoMotor apruebe el contrato.
