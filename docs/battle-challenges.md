# Retos de Battle Arenas

La [revision integral de DuckyClash](duckyclash-review.md) recoge la matriz de
cumplimiento, recorridos completos, evidencia ejecutada y pendientes de
integracion/operacion. Distingue pruebas fake de proveedores reales y del estado
de las migraciones locales.

## Catálogo desde Crear reto

Los formularios de reto privado y abierto permiten añadir subcategorías y subir
un nuevo paisaje sin publicar el reto. Se requieren, respectivamente, los permisos
`duckyclash.add_challengesubcategory` y `duckyclash.add_landscape`, asignables desde
Django Admin a usuarios o grupos. El catálogo es compartido.
ADMIN de plataforma también puede crear categorías y subcategorías desde esos
formularios, sin necesitar permisos adicionales por modelo. Las categorías
reutilizan `courses.CourseCategory` y deben asignarse a un ramo (`CourseSector`).
La selección en navegador sigue Ramo/sector → Categoría → Subcategoría:
cambiar un padre elimina las opciones ajenas y limpia la selección incompatible.
El formulario de edición utiliza los mismos filtros y precarga el ramo del reto.
Los nombres no se interpretan como HTML. El servidor comprueba las relaciones,
el estado activo y los permisos también en POST; no basta con ocultar botones.
Las categorías antiguas sin sector siguen accesibles con el selector de ramo vacío.
Crear una categoría conserva las condiciones del reto y la selecciona sin publicarlo.
No se añaden tablas ni migraciones.
La nueva entrada queda seleccionada y se conservan las condiciones escritas.
Las imágenes admitidas son PNG, JPEG y WebP, hasta 5 MB y 4096 píxeles por lado;
se valida su contenido y se guardan con un nombre generado por el servidor.
El almacenamiento utiliza el campo de imagen existente y su configuración de
media. No se sustituyen imágenes de paisajes asociados a duelos anteriores.
Sube la imagen antes de guardar una subcategoría: el navegador no conserva un
archivo seleccionado después de enviar un formulario.

## Alcance de esta entrega

Esta introducción conserva el alcance inicial de Battle Arenas; las secciones
de DuckyClash, economía, conocimiento y consumibles detallan las fases añadidas.

La ruta existente `ducky_arenas:battle` permite crear retos directos y públicos,
aceptar, contraproponer, rechazar y cancelar. Reutiliza `users.User`
(`AUTH_USER_MODEL`) y `ducky_arenas.UserProfileArenas`, cuyo reverso es
`user_profile_arenas`. No existe `UserProfileClash` en este repositorio; se ha
autorizado reutilizar el perfil de Arenas sin renombrarlo ni duplicarlo.

Los retos se guardan en `BattleChallenge`, separados de `BattleArena`: una
negociación no es un combate y no modifica partidas, ganadores ni recompensas.
La migración de creación se puede revertir; revertirla elimina los retos
almacenados, no los usuarios, perfiles ni arenas.

## Reglas y decisiones conservadoras

- Ambos jugadores deben estar activos y haber completado su Ducky.
- Un reto directo necesita el nombre de usuario exacto del rival.
- Un reto público no tiene rival hasta la primera aceptación o contrapropuesta.
  Una contrapropuesta lo vincula a ese jugador y deja de estar disponible para terceros.
- Solo el receptor de la última propuesta puede aceptarla o cambiar la apuesta.
  La contrapropuesta debe ser distinta; no extiende la caducidad original.
- Caducidad: 15, 30 o 60 minutos, calculada por el servidor.
  En el instante de caducidad ya no se admiten acciones. No hace falta un
  proceso periódico: las consultas ocultan retos públicos caducados y el
  servidor valida la fecha en cada acción.
- Apuesta **por jugador**: entero de 0 a 1.000.000 DuckyCoins. El límite es una
  validación técnica de esta entrega, no un contrato bancario ni un límite de saldo.
- Solo el creador cancela. El receptor puede rechazar retos directos y
  contrapropuestas; un tercero no puede cerrar un reto público.
- Aceptado, rechazado y cancelado son estados terminales de negociación.
  Aceptado significa **pendiente de reservas**, no combate legítimo.
- Escrituras transaccionales, bloqueo de fila donde lo soporte la base de
  datos y actualización condicional por versión para evitar aceptar una
  propuesta antigua o adjudicar un reto público a dos rivales.
- Los POST requieren autenticación y CSRF. Los retos privados no son visibles
  ni modificables por terceros. No hay endpoints para establecer saldo,
  ganador, daño o resultados desde el navegador.
- La pantalla lista 20 retos por página. El botón Actualizar obtiene el
  estado actual; esta fase no incluye sincronización en tiempo real.

## Integraciones descubiertas y pendientes

No se han encontrado servicios o contratos llamados DuckyBank, DuckyShop o
EcoMotor. No se inventan URLs, credenciales ni APIs.

- **Banco:** existe `UserProfileArenas.duckCoins`, pero no se ha verificado un
  servicio de reserva/liberación/liquidación. Esta entrega no modifica saldo,
  no comprueba solvencia ni afirma haber reservado monedas.
- **Objetos:** existen `ArenaItem`, `UserArenaInventory` y servicios de
  equipamiento/recompensas. El consumo local real y los efectos de Clash se
  describen en la sección de consumibles; no hay API externa DuckyShop localizada.
- **Preguntas:** existe el banco del LMS (`QuestionBank`, `Question`,
  `QuestionVersion` y `apps.lms.knowledge`), sujeto a permisos de curso.
  La integración DuckyClash descrita más abajo incorpora solo versiones
  aprobadas expresamente y no exporta soluciones ni claves al juego.
- **Daño:** existen estadísticas efectivas y cálculo de poder, pero no un
  contrato de EcoMotor ni un cálculo autoritativo de combate verificado.

La siguiente entrega debe definir adaptadores tipados para reservas
idempotentes, propiedad/consumo de objetos, preguntas autorizadas y daño
determinista. Los adaptadores fake serán explícitos y exclusivos de desarrollo
y pruebas; no se usarán como si fueran integraciones reales. Después podrán
implementarse Ring, preparación de ambos jugadores, preguntas sincronizadas,
validación servidor de tiempos/respuestas/efectos y liquidación.

## Verificación

### Base DuckyClash

`apps.duckyclash` está registrada y expone `/clash/`, el detalle de ofertas
y `/clash/duels/<id>/ring/`. Reutiliza `BattleChallenge` con el proxy `Challenge`:
no crea otra tabla de retos, usuario ni perfil. Las ofertas añaden categoría
(`courses.CourseCategory`), subcategoría opcional, fecha con zona IANA,
vencimiento, DuckyCoins enteros positivos y paisaje. Se han añadido catálogos
de subcategorías y paisajes porque no existían equivalentes de juego.

Las ofertas y los acuerdos son snapshots inmutables al guardar. Un acuerdo
identifica una oferta concreta y dos jugadores; la aceptación transaccional
crea un duelo **pendiente de reservas** con dos estados de participante.
La petición de aceptación no espera al banco: la saga económica posterior verifica
las reservas mediante el adaptador configurado. Los retos con ofertas DuckyClash no
pueden modificarse desde las acciones antiguas de Battle Arenas.

El estado de negociación del proxy conserva `pending`, `accepted`, `declined`
y `cancelled`, con vencimiento calculado: no se reinterpretan registros
históricos ni se inventan fechas para ellos. El duelo declara sus propios
estados. El guardado genérico no activa combates; las transiciones económicas
son exclusivas del servicio servidor que comprueba las operaciones verificadas.

Preguntas enlazan versiones publicadas del LMS mediante `PROTECT`; sus
snapshots privados no se envían a las pantallas. Respuestas son únicas por
pregunta/participante y usan recepción servidor; usos de objetos enlazan
`ArenaItem`, comprueban propiedad y guardan una referencia única de consumo.
**Registrar la referencia por sí sola no consume inventario ni demuestra una
operación externa**: `apply_item` enlaza recibo y consumo mediante ShopGateway
en la misma transacción. Guardar referencias económicas tampoco produce por sí
solo movimientos: la saga necesita confirmación del adaptador bancario real.

Hay checks de apuesta, HP, asientos, orden temporal, participantes distintos,
referencias confirmadas y unicidad de liquidación/resolución. Las relaciones
cruzadas se validan con `full_clean()` en `save()`. `QuerySet.update()`,
`bulk_create()` y SQL directo omiten validaciones e inmutabilidad Python;
solo servicios de confianza deben usarlos, manteniendo los checks SQL.
Las escrituras HTTP de negociación son POST con CSRF y permisos por
participante; no hay endpoints para modificar saldo, HP o resultado.
El admin operativo es de solo lectura y oculta los snapshots privados,
calificaciones y evidencias de distractores. Los catálogos de
paisajes/subcategorías son editables; las publicaciones autorizadas del LMS
se gestionan por separado y solo por superusuarios.

La nueva migración es aditiva y reversible. No cambia migraciones anteriores
ni tablas de identidad; revertirla elimina exclusivamente los datos nuevos
de Clash. Se aplica exclusivamente a la base SQLite de **desarrollo** para
habilitar las pantallas; no se cambia ninguna base de producción. Los tests
usan una base aislada. Para otra instalación de desarrollo:

```powershell
& .\.venv\Scripts\python.exe manage.py migrate duckyclash --settings=config.settings.development
& .\.venv\Scripts\python.exe manage.py test apps.duckyclash --settings=config.settings.development
```

Entrar con Ducky completado y abrir `/clash/`: se muestra el límite funcional
y el acceso a **Enviar reto privado**. Un Ring existente solo es visible para sus participantes;
ser staff no concede acceso como jugador. El admin permite inspeccionar las
entidades, no generar resultados económicos o deportivos.

Pendientes: contrato externo real de DuckyBank,
gateway real de daño, API externa del Banco del Conocimiento,
transiciones completas de preparación/inicio, eventos
WebSocket/outbox y pruebas concurrentes con PostgreSQL.

### Negociación privada

Desde Ducky Arenas → Battle → **DuckyClash · Retos privados con fecha y
paisaje**, abrir **Enviar reto privado**. La búsqueda muestra hasta 20
usuarios activos con onboarding terminado, solo por su `USERNAME_FIELD`;
la resolución del destinatario conserva la búsqueda exacta de Arenas, sin
usar nickname, email alternativo ni nombres visibles como identidad.

El formulario incluye categoría, subcategoría opcional, fecha, hora,
DuckyCoins enteros positivos, paisaje y plazo de respuesta (15/30/60 min).
Fecha/hora se interpretan en la zona del proyecto; los horarios inexistentes
o ambiguos por cambio de hora se rechazan. El vencimiento se limita al plazo
elegido o a un minuto antes del duelo, lo que ocurra primero.

Solo el receptor de la última oferta puede aceptar, rechazar o proponer otra
fecha/hora/apuesta. Se permiten varias rondas, creando ofertas inmutables:
categoría, subcategoría, paisaje, zona y vencimiento originales se conservan.
Los campos extra del POST no pueden cambiarlos. El creador puede responder
a una contrapropuesta y no puede aceptar su propia propuesta.

Se envían notificaciones `sistema` por el mecanismo existente: al recibir
un reto/contraoferta, al rechazo y al acuerdo. Se guardan en la misma
transacción para no notificar operaciones fallidas. Solo los participantes
ven los retos privados y sus historiales; aceptar/rechazar/contraofertar
requiere POST, CSRF, versión y revisión vigentes.

`BankGateway.available_balance()` devuelve DuckyCoins disponibles enteros.
Desarrollo configura `LocalPointsBankGateway`: las DuckyCoins locales son
`User.puntos`, el saldo canjeable compartido con recompensas. No consulta
`UserProfileArenas.duckCoins` ni modifica `puntos_totales` o el nivel por apuestas.
`UserPointsBalanceGateway` permite solo consulta; el gateway transaccional
local reserva, devuelve y paga de verdad en la base de datos, no mediante un fake.
Otros entornos deben configurar `DUCKYCLASH_BANK_GATEWAY` con una clase que
implemente `BankGateway`; sin configuración, con fallo o respuesta inválida,
la operación se bloquea con error explícito, sin fallback. Los tests usan
un fake explícito con saldos y fallos controlados.

Al crear/contraofertar se verifica el saldo del proponente; al aceptar se
vuelven a consultar ambos saldos. La consulta no garantiza fondos futuros,
no reserva y no produce movimientos. La aceptación crea un único acuerdo y
duelo pendiente de reservas. Un doble clic o versión antigua produce
conflicto sin duplicar acuerdo, duelo ni notificaciones. Rechazar no depende
de la disponibilidad bancaria. La actualización condicional comprueba
estado, revisión y vencimiento inmediatamente antes de escribir; si vence
durante la consulta bancaria no se acepta.

### Retos abiertos de DuckyClash

- `/clash/open/`: panel responsive, filtros por categoría, fecha y apuesta máxima,
  paginación y contador informativo calculado desde el tiempo restante del servidor.
- `/clash/open/new/`: publicación sin destinatario, con categoría, subcategoría,
  fecha/hora, DuckyCoins por jugador, paisaje y fecha/hora explícitas de vencimiento.
  El vencimiento debe ser futuro y anterior al inicio; se rechazan horas locales
  ambiguas o inexistentes.
- `/clash/open/<id>/accept/`: solo POST con CSRF, versión y revisión. No admite
  negociación ni cambios de condiciones; el creador no puede aceptarse.

Se reutiliza `BattleChallenge`, sin otro usuario ni perfil. El panel solo consulta
retos públicos pendientes sin aceptante, de un creador activo con Ducky creado,
con oferta e inicio aún vigentes. Al confirmarse se retiran del panel y solo los
dos participantes pueden acceder al detalle y al Ring.

La consulta bancaria de creación, contrapropuesta y aceptación ocurre **fuera de
la transacción de escritura**. Tras consultar ambos saldos se vuelve a comprobar
el estado, versión, participantes y reloj del servidor dentro de una transacción
corta con bloqueo de fila y actualización condicional de revisión/vencimiento.
Solo una petición puede asignar al rival y crear el acuerdo, duelo y notificaciones.
Un fallo bancario deja el reto sin reclamar. SQLite puede devolver conflicto de
escritura incluso a ambos candidatos: deben recargar antes de un nuevo intento;
no se reintenta automáticamente ni se mantiene el bloqueo esperando al banco.

**Confirmar el aceptante no confirma reservas bancarias.** El duelo entra en
`PENDING_RESERVATION`. La saga descrita a continuación solo lo pasa a `SCHEDULED`
cuando ambas reservas están verificadas. No se consume inventario ni se habilita
el combate. Sin adaptador de pagos real permanece pendiente; no se inventan endpoints
ni movimientos económicos.

La tarea existente de Celery se amplía con
`apps.duckyclash.tasks.expire_open_challenges`, programada cada 30 segundos en Beat.
Marca `EXPIRED` e incrementa la revisión únicamente en retos públicos versionados
pendientes sin aceptante. Es idempotente y no altera privados, aceptados ni retos
antiguos de Battle. Hace falta worker y Beat operativos para persistir el estado:
el filtrado por reloj del servidor sigue evitando ofertas caducadas aunque estén parados.
La migración aditiva y reversible `ducky_arenas.0007_alter_battlechallenge_status`
añade la opción de estado; no modifica el usuario ni la migración inicial.

Prueba de interfaz: con un usuario con Ducky, ir a **Battle → DuckyClash → Retos
Abiertos → Publicar reto**. Con otra cuenta, filtrar y aceptar sin modificar las
condiciones. La tercera cuenta no debe poder reclamarlo ni ver el Ring. Comprobar
que desaparece del panel y que las monedas permanecen intactas. Un reto que vence
se oculta incluso sin Beat; con worker/Beat pasa además a `EXPIRED`.

### DuckyBank: reservas, compensación y liquidación

**Inspección:** cliente HTTP, endpoints y autenticación reales de DuckyBank:
**no localizado en el repositorio**. El proveedor local autorizado
`apps.duckyclash.local_bank.LocalPointsBankGateway` usa `User.puntos`, no una API
externa ni el saldo legacy `UserProfileArenas.duckCoins`. No se añade otro saldo.
`LocalBankReceipt` guarda recibos y estado de reservas, no una copia del balance.

Tras aceptar, se descuentan ambas apuestas mediante actualizaciones condicionales
atómicas (`puntos >= apuesta`); solo entonces se programa el duelo. Si falta saldo
al reservar al segundo jugador, se compensa al primero. Un empate o cancelación
válida devuelve cada reserva una sola vez. La victoria consume ambas reservas y
abona el bote completo al ganador en una única transacción local.
Ejemplo: saldos 100/100, apuesta 20, reservas 80/80, victoria 120/80; empate 100/100.
Ni las devoluciones ni el premio aumentan el acumulado académico.

Los recibos tienen identidad única por operación y condiciones verificadas.
`lookup` recupera el resultado después de una interrupción entre el movimiento y
el registro de la saga, sin repetir cargos. Las reservas locales se comprueban
de nuevo bajo bloqueo al cancelar, por lo que una petición retrasada no puede
cargar fondos después de la cancelación. La referencia `local-points:` distingue
estos recibos de los externos. Cada nuevo duelo conserva su proveedor; un cambio
de settings no mueve reservas existentes a otra economía. Duelo anterior sin
proveedor identificado y con operaciones enviadas: revisión explícita, nunca
interpretar un recibo remoto como puntos ni debitar otra vez.

Las altas de puntos y los canjes existentes usan actualizaciones atómicas para
no sobrescribir reservas con instancias antiguas. El recálculo administrativo de
saldo incluye movimientos locales verificados; actualizar solo el contador
histórico no restablece el saldo gastado. Una modificación manual intencionada
del saldo por administración sigue siendo responsabilidad del administrador.

`BankGateway` conserva la consulta de saldo. Su extensión
`TransactionalBankGateway` define `reserve`, `release`, `settle` y `lookup` con
`BankRequest`/`BankResult` tipados. La implementación de producción debe proporcionar:

- Idempotencia permanente por clave, incluso con peticiones simultáneas o en curso.
  Las claves UUID deterministas incluyen duelo/acuerdo, usuario y operación.
- Consulta autoritativa de una operación por su misma clave: `CONFIRMED`, `REJECTED`,
  `UNKNOWN` o `NOT_FOUND`. `REJECTED` es definitivo y garantiza ausencia de efecto;
  `NOT_FOUND` solo procede de consulta y permite reenviar **la misma** clave.
- Confirmación verificada con referencia y eco exacto de la petición (importe,
  usuario, ganador, referencias de reservas y duelo); un HTTP 200 no basta.
- Reservas duraderas hasta liberar o liquidar; no usar este contrato sin adaptarlo
  si el banco caduca reservas automáticamente.
- Liquidación atómica que consume ambas reservas y paga las dos apuestas al ganador.
  Si el banco no garantiza atomicidad, hace falta adaptar el protocolo, no enviar
  dos cargos independientes fingiendo una liquidación.
- Timeouts de red acotados, traducción de fallos de transporte a `BankUnavailable`
  y ausencia de secretos en respuestas, logs o payloads persistidos.

Configurar `DUCKYCLASH_PAYMENT_GATEWAY` en el entorno con la ruta de clase del
adaptador real o del proveedor local anterior. Fuera de desarrollo, vacío falla
cerrado, nunca selecciona un fake. Desarrollo usa puntos por defecto si no hay
otro gateway configurado. Un adaptador explícito de solo consulta se respeta.
Cuando está configurado, **ese mismo gateway consulta también el saldo**, evitando
validar con el perfil local. `DUCKYCLASH_BANK_GATEWAY` conserva la opción de un
adaptador de solo consulta cuando no hay pagos. El adaptador real deberá leer su
autenticación del entorno con el mecanismo existente `env`; no hay URLs, tokens
ni nombres de credenciales inventados porque falta el contrato externo.
`FakePaymentBank` existe exclusivamente en pruebas aisladas.

Con puntos locales, aceptación, cancelación y resultado disparan conciliación
después del commit: funciona sin un broker disponible. Los fallos recuperables se
registran y permanecen pendientes para el worker, sin anunciar pagos confirmados.
Las integraciones externas conservan la ejecución por worker.

La aceptación transaccional persiste un duelo pendiente: actúa como cola duradera,
sin depender de que el broker esté disponible en ese POST. Celery Beat ejecuta
`reconcile_bank_operations` cada 30 segundos. Un ciclo reserva A y después B:

1. Persiste la petición inmutable antes de enviarla.
2. Adquiere lease de 60 segundos y token de cercado, incrementando contadores.
3. Sale de la transacción, llama al banco y verifica la respuesta.
4. Persiste el resultado solo si sigue siendo dueño del token.
5. Solo tras ambas confirmaciones programa el duelo. Si B rechaza, cambia el
   propósito persistente a cancelar y compensa las reservas confirmadas.

Un timeout o una caída tras enviar deja resultado `UNCERTAIN`; no se repite el
movimiento a ciegas. Al vencer el lease se consulta por la clave original.
Un resultado desconocido **no equivale a saldo liberado ni a operación fallida**.
`NOT_FOUND` tampoco prueba que una petición antigua demorada no pueda llegar después:
en cancelación, o agotados los envíos, se mantiene la incertidumbre hasta confirmar
la operación o recibir un rechazo terminal garantizado por el banco. Si el banco
requiere anular la clave (tombstone) para garantizar ese rechazo, debe hacerlo su
adaptador/operador real; Clash no inventa ese endpoint ni cierra prematuramente.
La cancelación se serializa contra las reservas; una reserva que termina después
de solicitar cancelar se concilia y libera. Ningún nuevo envío de reserva empieza
después de la cancelación. Antes del inicio cualquiera de los dos participantes
puede cancelar mediante POST+CSRF; terceros no pueden. Si se alcanza el inicio
sin las dos reservas, el conciliador pasa a compensación.

Los reintentos tienen espera de 30 segundos: máximo 3 envíos y 6 consultas por
operación. No se abre una transacción mientras espera la red. Un rechazo de
liberación/pago o incertidumbre agotada requiere revisión; nunca se marca cancelado
o finalizado como si se hubiera devuelto/pagado. Las devoluciones independientes
siguen procesándose aunque la otra requiera revisión.

La liquidación parte exclusivamente de `DuelResolution` inmutable, creada por un
servicio servidor de combate; ningún endpoint admite un ganador
del navegador. `request_settlement` fija el propósito: pago o empate. En pago se
crea una única operación de bote con dos referencias; en empate se liberan ambas
reservas. `FINISHED` solo se persiste tras el pago confirmado o ambas devoluciones.
Cancelación y liquidación no pueden mezclarse.

Inspección operativa de solo lectura en admin: propósito del duelo, estado,
referencias, intentos, errores sanitizados y leases. Conciliación manual:

```powershell
& .\.venv\Scripts\python.exe manage.py reconcile_clash_bank --duel 123 --settings=config.settings.development
# Solo tras revisión bancaria: renueva consultas, nunca el presupuesto de envíos.
& .\.venv\Scripts\python.exe manage.py reconcile_clash_bank --duel 123 --resume-lookups --settings=config.settings.development
```

No borrar operaciones, cambiar claves o restaurar una copia antigua de la base
mientras existan reservas: antes hay que conciliar con el banco. Los registros de
la base son parte de la garantía de idempotencia. Operaciones heredadas sin petición
verificable requieren revisión, no se reinterpretan como reservas nuevas.

La migración aditiva `duckyclash.0002` añade propósito, petición, contadores y leases;
no cambia identidad ni saldos. Es reversible a nivel de esquema, pero **no se debe
revertir con operaciones bancarias vivas**: primero conciliar y cerrar todas las
reservas. En esta entrega no se conecta ningún banco ni se realizan pagos reales.

Pruebas: doble aceptación, insuficiencia de fondos, reserva parcial, timeouts antes
y después del efecto, crash con recuperación de lease, respuesta antigua, dos workers,
cancelación simultánea al banco, empate con devolución, pago duplicado, recibos
inválidos y límites de reintento. La contabilidad fake no modifica perfiles Django.
En la interfaz, el Ring muestra estado y operaciones y permite cancelar antes del
inicio; sin integración real seguirá pendiente, nunca mostrará un pago simulado.

Validación:

```powershell
& .\.venv\Scripts\python.exe manage.py test apps.duckyclash apps.ducky_arenas --settings=config.settings.development
```

Prueba con dos cuentas: enviar, contraofertar, responder desde la otra,
comprobar las versiones y las notificaciones; aceptar y ver Ring pendiente.
Un tercero no puede abrir/modificar el reto; las monedas deben permanecer
sin cambios. Las carreras reales de PostgreSQL siguen pendientes: las
pruebas incluyen dos conexiones concurrentes, un ganador durante la espera bancaria,
caducidad concurrente y conflictos SQLite; no sustituyen un ensayo multiproceso
sobre PostgreSQL y el banco real.

1. Entrar con dos usuarios distintos con Ducky creado.
2. Menú Ducky Arenas → Battle → crear reto directo con el usuario rival.
3. Con el rival, proponer otra apuesta; con el creador, aceptar o rechazar.
4. Crear un reto público y reclamarlo desde el segundo usuario. Un tercer
   usuario no debe poder intervenir después.
5. Confirmar que las monedas no cambian y no aparece un combate en curso.
6. Tras la caducidad, no deben aparecer controles de respuesta; un POST antiguo
   tampoco debe poder aceptarlo.

Pruebas:

```powershell
& .\.venv\Scripts\python.exe manage.py test apps.ducky_arenas.test_battle_challenges
```

### Banco del Conocimiento: integración segura con el LMS

**API HTTP externa del Banco del Conocimiento: no localizado en el repositorio.**
No se inventan endpoints, tokens, respuestas de una API ni preguntas. El adaptador
por defecto `apps.duckyclash.knowledge.LMSKnowledgeGateway` integra el servicio
local real: `apps.lms.knowledge.question_version_initial` comprueba las cuatro
opciones originales y la solución única; `QuestionVersion.evaluate_response`
realiza la corrección en Django. No se han añadido dependencias.

#### Edición y borrado por el creador

En el detalle del reto, «Editar reto» y «Borrar reto» están disponibles únicamente
para su creador mientras esté pendiente y no haya caducado. Se pueden editar
categoría, subcategoría, paisaje, fecha, hora, apuesta y vencimiento; el rival y
el carácter público/privado no cambian. Editar crea una nueva versión inmutable,
notifica al rival privado y requiere aceptar expresamente las nuevas condiciones.
El panel público utiliza la última versión, también para sus filtros.

El borrado requiere POST con CSRF y confirmación, y elimina el reto pendiente y
sus ofertas, sin tocar saldos. Versión y revisión se comprueban transaccionalmente
contra aceptación y contrapropuestas: una pantalla antigua no puede borrar ni
modificar un reto ya aceptado. Los duelos confirmados conservan su historial y
solo admiten las cancelaciones y devoluciones previstas por el motor económico.

El staff activo también puede ver y borrar retos pendientes no caducados,
sin necesitar un perfil de jugador. En Admin → DuckyClash → Retos DuckyClash,
«Ver reto / borrar pendiente» abre el detalle con la confirmación de borrado.
No concede permiso para editar retos ajenos ni borrar duelos aceptados.
El borrado por staff queda en el registro de administración, con actor, reto,
creador y versión; se avisa a los participantes distintos del actor.

#### Publicación y taxonomía

Publicar un banco para sus cursos **no lo comparte automáticamente con Clash**.
Para utilizar un banco completo por subcategoría, un superusuario puede crear
una `SubcategoryQuestionSource` en **Admin → DuckyClash → Fuentes de preguntas por
subcategoría**, seleccionar subcategoría y banco del LMS y activar la fuente.
La aprobación queda asociada automáticamente al administrador. Cada subcategoría
tiene como máximo una fuente; la categoría se obtiene de la propia subcategoría.
No se copia el banco ni se crea un segundo editor de preguntas.

Desde «Gestionar preguntas del LMS» se reutilizan el editor existente y la
importación CSV con revisión previa. El CSV tiene las columnas
`Pregunta;OpcionA;OpcionB;OpcionC;OpcionD;RespuestaCorrecta`, con letra A–D en la
última columna. Las importaciones quedan en borrador: revisar y publicar las
preguntas y publicar también el banco para que sean seleccionables.

El vínculo autoriza todas las preguntas publicadas de opción única del banco,
incluidas las que se publiquen posteriormente. Se toma únicamente la versión
actual publicada, con cuatro opciones distintas y una respuesta correcta.
Una fuente desactivada, categoría/subcategoría inactiva, banco en borrador o
archivado, o aprobador inactivo/sin permiso sobre el curso no proporciona preguntas.
El vínculo tiene precedencia sobre las publicaciones individuales de esa
subcategoría, incluso si está vacío o desactivado: no se sustituye silenciosamente
por otro banco. Desactivar no altera las rondas ya presentadas.

Para subcategorías sin fuente se mantiene la autorización individual anterior:
un superusuario debe crear una `KnowledgePublication` en el admin, seleccionando
una versión publicada, categoría, subcategoría opcional y usuario aprobador con
permiso real de edición sobre el curso de origen. Se validan el banco y la
pregunta publicados, el tipo de opción única, cuatro opciones distintas y
exactamente una respuesta correcta. Desactivar la publicación impide nuevas
selecciones; no altera rondas que ya tienen su snapshot.

La selección de cada ronda reutiliza el algoritmo aleatorio del servidor:
sin repetición de identidad dentro del duelo y priorizando entre duelos las
preguntas menos vistas por los participantes. Si se agota la fuente se muestra
el error de falta de preguntas nuevas; no se inventan ni se repiten preguntas.
La solución y la revisión privada no se exponen al navegador.

Migración: `0008_subcategory_question_sources`. No enlaza bancos ajenos ni genera
contenido automáticamente. Configuración local inicial solicitada: subcategoría
«django» vinculada a «Banco Django_01»; las demás se configuran desde el admin.

Las categorías reutilizan `courses.CourseCategory`; las subcategorías,
`ChallengeSubcategory`. El comando idempotente siguiente crea las cinco áreas y
dos subcategorías iniciales editables para cada una, sin crear contenido:

```powershell
& .\.venv\Scripts\python.exe manage.py seed_clash_knowledge_categories --settings=config.settings.development
```

- Desarrollo web: Frontend, Backend.
- Ciberseguridad: Seguridad web, Redes seguras.
- Sistemas: Linux, Administración de sistemas.
- Gamer: Historia de videojuegos, Mecánicas de juego.
- Data e IA: Datos, Inteligencia artificial.

Son etiquetas locales iniciales, no una taxonomía atribuida a una API externa.
Los catálogos inactivos y las versiones/bancos no publicados no se seleccionan.
El filtro respeta la categoría y, si se eligió, la subcategoría exacta.

#### Rondas, identidad y corrección

`prepare_round` es un servicio interno utilizado por `DuelEngine`:
requiere un duelo en curso y los dos participantes del acuerdo. No hay un
endpoint para que un jugador inicie rondas, elija preguntas o active el duelo.
Las rondas son consecutivas, no se solapan y repetir la creación del mismo número
devuelve la misma pregunta. Ambos participantes reciben la misma pregunta,
dificultad 1–3 y cuatro textos originales con identificadores opacos.

Se conserva proveedor, identidad original y revisión, además de un snapshot
privado de enunciado, opciones y validación. Una identidad no puede repetirse
en un duelo, ni cambiando de revisión. Entre duelos se priorizan las preguntas
menos vistas por cualquiera de los dos jugadores; los empates son aleatorios.
La falta de preguntas autorizadas/nuevas es un error explícito, nunca una
pregunta inventada ni la repetición silenciosa de una anterior.

Los tiempos de apertura, cierre y recepción son del servidor. Solo se admite
una respuesta por pregunta y participante, protegida con transacción,
bloqueo donde la base lo soporte y unicidad SQL. El identificador debe pertenecer
a las opciones de ese jugador y ronda. Una validación lenta vuelve a comprobar
el plazo antes de guardar. Cambiar la solución del LMS después de abrir una
ronda no cambia su corrección: se usa el snapshot original.

Las llamadas al proveedor se realizan fuera de la transacción que bloquea el
duelo. Para proveedores remotos se exigen tiempos de espera limitados y
`KnowledgeUnavailable` ante un fallo; una indisponibilidad no significa respuesta
incorrecta. Cambiar el gateway configurado con rondas abiertas las bloquea
explícitamente para conciliación; no las corrige con otro proveedor.

#### Superficies públicas sin soluciones

- `GET /clash/duels/<id>/question/`: solo participantes; devuelve `id`, `round`,
  `prompt`, `difficulty`, tiempos ISO y `options` con `id`/`text`, o ninguna
  pregunta abierta. No incluye identidad del proveedor ni datos privados.
- `POST /clash/questions/<id>/answer/`: autenticación, permisos y CSRF. Acepta
  `option_id`; los campos de corrección enviados por el cliente no tienen efecto.
  Aciertos y errores devuelven el mismo `{"accepted": true}`.
- El Ring consume exactamente la presentación pública y escapa los textos.
  No serializa modelos/snapshots. El admin operativo también oculta validación,
  corrección y evidencias.

El JSON usa `Cache-Control: private, no-store`. No se publica solución,
explicación, clave o resultado individual, ni siquiera al cerrar la pregunta
en esta fase. No hay transporte WebSocket ni cliente Pygame nuevo: cualquier
implementación futura debe usar la misma lista permitida de campos.
Los editores del LMS conservan sus permisos propios de autoría; esta integración
no modifica ni abre el acceso al banco privado.

#### Multiplicador y proveedores externos

El LMS no contiene una fuente certificada de distractores adicionales.
Por tanto, **Multiplicador no está disponible con el proveedor real actual**.
No se generan alternativas con IA, no se copian opciones de otras preguntas y
no se considera prueba de falsedad el mero hecho de que un texto difiera de
la respuesta correcta.

`KnowledgeGateway.distractors` y `add_verified_distractors` definen un punto de
integración interno: solo un proveedor confiable puede aportar
`VerifiedDistractor(text, evidence)` con revisión/procedencia. Se comprueba además
que no duplique textos, tenga evidencia y que el validador lo rechace. Solo se
añaden al jugador afectado, con un máximo de doce opciones; la evidencia queda
privada. Estos controles complementan, **no sustituyen**, la certificación
semántica del proveedor.

No hay endpoint público para añadir distractores directamente. Multiplicador
los incorpora mediante `apply_item`, junto al consumo autoritativo e
idempotente de ShopGateway. Los distractores de prueba son fake y exclusivos
de los tests.

Un adaptador externo se configura con `DUCKYCLASH_KNOWLEDGE_GATEWAY`. Debe
implementar `candidates`, `validate` y, opcionalmente, `distractors`, respetando
identidades/revisiones estables y validación privada sobre la versión presentada.
`DuelQuestion.version` admite nulo para no crear versiones LMS ficticias para
una API futura. No se ha configurado ninguna integración externa simulada.

#### Migración y verificación

`duckyclash.0003` es aditiva: publicaciones, opciones por participante, identidad
de origen y corrección privada nullable para respuestas históricas. No modifica
usuarios, perfiles, preguntas LMS ni movimientos económicos.
Puede revertirse con preguntas locales; antes de revertir, exportar/cerrar rondas
y retirar registros externos sin versión LMS, porque el esquema anterior exige
esa relación. La reversión elimina publicaciones y opciones nuevas. No revertir
una instalación con reservas bancarias vivas.

```powershell
& .\.venv\Scripts\python.exe manage.py migrate duckyclash --settings=config.settings.development
& .\.venv\Scripts\python.exe manage.py test apps.duckyclash apps.ducky_arenas apps.lms.test_knowledge --settings=config.settings.development --noinput
```

Para comprobar desde la interfaz: publicar una pregunta de opción única en el
LMS con cuatro opciones y una solución; autorizar su versión en **Knowledge
publications** del admin; crear un reto Clash con la misma categoría/subcategoría.
Sin banco real configurado el Ring continuará pendiente:
no se debe forzar su estado desde la interfaz ni afirmar que existe ya un combate
jugable. Los tests abren rondas de forma controlada y verifican filtrado,
identidad, permisos/CSRF, acuse idéntico, deadlines, respuestas concurrentes,
snapshot estable, escape HTML, no filtración y reversión del esquema.

### Consumibles e inventario real

`apps.duckyclash.shop.ShopGateway` consulta y consume el inventario existente:
`ArenaItem.code`, `UserArenaInventory.quantity` y el perfil autorizado de
Arenas. **No hay un segundo inventario ni una integración DuckyShop externa
simulada**. Solo se admiten complementos activos, no equipados y con stock.
El decremento condicionado a `quantity >= 1` evita negativos; se toma el mismo
bloqueo de perfil que las recompensas de Arenas para no perder incrementos.

`seed_clash_items` registra seis códigos estables en el catálogo real. No
regala existencias, activa objetos desactivados ni convierte equipamiento
existente; una colisión incompatible es un error explícito y revierte el seed.

| Código | Efecto servidor predeterminado |
| --- | --- |
| `NUBE` | Oculta las opciones del rival durante 5 segundos; rechaza también respuestas enviadas con opciones previamente almacenadas. |
| `ACELERADOR` | Resta 5 segundos al reloj rival sin retroceder más allá del instante servidor actual. |
| `MULTIPLICADOR` | Añade 2 distractores certificados exclusivamente al rival; máximo 12 opciones. Sin proveedor fiable, falla sin consumo. |
| `ESCUDO` | Elimina hasta 2 opciones falsas propias, originales o añadidas; comprueba que subsista exactamente una correcta. |
| `POCIMA_TIEMPO` | Añade 5 segundos al reloj propio, hasta 15 segundos adicionales sobre el cierre original. |
| `BOTIQUIN` | Recupera 20 HP hasta el menor de máximo del participante y HP iniciales. Con 0 HP o salud completa, falla sin consumo. |

`DUCKYCLASH_ITEM_RULES` permite sobrescribir `amount`, `max_uses` (por duelo,
predeterminado 2), `max_per_round` (1), `cooldown` (10 segundos) y, para la
pócima, `max_extra_seconds`. Los enteros y límites se validan antes de usar
stock. Cambiar la configuración no altera efectos ya registrados; los parámetros
usados quedan en el recibo. Ejemplo en settings de la instalación:

```python
DUCKYCLASH_ITEM_RULES = {
    'NUBE': {'amount': 4, 'max_uses': 3, 'cooldown': 12},
    'POCIMA_TIEMPO': {'amount': 8, 'max_extra_seconds': 20},
}
```

Decisión conservadora: solo un jugador vivo, sin respuesta registrada, durante
su plazo y en la ronda vigente puede activar objetos. Un ataque tampoco puede
modificar a un rival que ya respondió o cuyo reloj terminó. Las acciones
inválidas no consumen stock. La nube no impide beber una pócima o usar defensa,
pero sí impide responder hasta que venza el ocultamiento.

`POST /clash/questions/<id>/item/` acepta únicamente `code` y `action_id`
UUID estable para el intento. Reenviar el mismo UUID devuelve el recibo anterior
sin consumir ni aplicar de nuevo, incluso tras cerrar la ronda; reutilizarlo
para otro objeto o ronda es un error. La unicidad es por participante y acción,
no solo por conexión. Se comprueban permisos, CSRF y estado bajo el bloqueo del
duelo. Recibo, efecto y decremento se confirman juntos o se revierten juntos.
La certificación de opciones se hace fuera de la transacción; dentro se
recomprueban opciones, reloj y respuestas para rechazar snapshots obsoletos.

El Ring utiliza el mismo Bootstrap y navegación existentes, muestra únicamente
el stock propio y consulta estado seguro cada segundo durante el combate.
Aplica cambios de nube/opciones/reloj y HP sin publicar soluciones; sin
JavaScript permite recargar manualmente. La actualización es informativa:
Django valida cada solicitud contra su reloj, aunque el navegador conserve
una pantalla vieja. Si falla la conexión se detiene la consulta y se muestra
un aviso, sin afirmar que el efecto o la respuesta se hayan confirmado.
`GET /clash/duels/<id>/state/` solo admite participantes y añade estado, HP,
hora servidor y si el propio jugador respondió; nunca corrección ni snapshots.

### EcoMotor, auditoría y resolución de ronda

Una API real EcoMotor está **no localizada en el repositorio**. Las estadísticas
efectivas de Arenas no se reinterpretan como su algoritmo. El contrato tipado
de `apps.duckyclash.ecomotor` contiene:

- `DamageRequest`: clave UUID, sesión, número de ronda, dificultad y dos
  `FighterResult` en orden fijo de asiento. Cada uno contiene jugador, HP,
  máximo, corrección servidor, milisegundos usados y códigos de los efectos
  recibidos durante la ronda. Una ausencia de respuesta es un fallo por tiempo
  servidor, no un resultado aportado por el navegador.
- `EcoMotorGateway.calculate`: función idempotente para la misma clave/entrada,
  con timeouts acotados; no modifica HP ni dinero. Debe documentar cuáles de
  dificultad/tiempo/modificadores soporta e ignorar explícitamente los restantes.
- `DamageResult`: eco de la entrada verificada, pareja de daños **recibidos**
  por asiento, enteros no negativos y versión de motor. Se rechazan floats,
  booleanos, daños negativos/excesivos, formas incorrectas o eco distinto.
- `EcoMotorUnavailable`: fallo temporal/configuración/contrato no verificable.
  No se convierte en cero daño, victoria ni liquidación.

`DUCKYCLASH_ECOMOTOR_GATEWAY` está vacío por defecto y bloquea el cálculo hasta
disponer de proveedor. No se inventan endpoints ni credenciales. El adaptador
real debe añadirse con la autenticación/configuración de entorno existente.
Para experimentar **solo** con `DEBUG=True` se puede seleccionar explícitamente
`apps.duckyclash.ecomotor.DevelopmentFakeEcoMotor`. Esta política de ejemplo
inflige 25 HP al contrario por cada acierto, calculando ambos simultáneamente;
no usa dificultad, rapidez, estadísticas ni modificadores y **no es el
algoritmo de EcoMotor**. Se rechaza con `DEBUG=False`, aunque esté configurada.

En el servidor local se ha autorizado expresamente el demo también para los
duelos con apuestas de puntos del sitio. `config.settings.development` lo usa
por defecto cuando no hay otro adaptador configurado; producción no lo hereda.
El Ring muestra un aviso DEMO con su regla de 25 HP y el efecto sobre apuestas.
Esto no constituye integración real ni autorización para desplegarlo en producción.

`resolve_round` no tiene endpoint de jugador y exige ejecutarse fuera de una
transacción externa. Espera al máximo de ambos cierres individuales (incluidas
pócimas), fija una entrada privada en `RoundResolution`, libera el bloqueo y
llama al motor. Antes de abrir la siguiente pregunta se exige que la ronda
anterior esté aplicada. La aplicación vuelve a bloquear el duelo, verifica HP
contra el snapshot y escribe ambos resultados en una sola transacción.
Cada HP queda entre cero y su máximo inicial; **no se decide el vencedor después
de procesar solo al primer jugador**.

`HealthChange` conserva antes/después/cantidad solicitada/origen para daño,
curación y reinicios explícitos de desempate. Sus claves son únicas por jugador
y origen. Las resoluciones y auditorías no son editables en el admin, y los
payloads privados de EcoMotor se excluyen de las pantallas operativas.

Si queda un jugador vivo, se crea el único `DuelResolution`; con doble KO,
`DUCKYCLASH_DOUBLE_KO=draw` (predeterminado) solicita empate/devolución mediante
la saga bancaria existente. Opcionalmente `tiebreak` reinicia ambos HP de forma
auditada para una pregunta nueva, como máximo `DUCKYCLASH_MAX_TIEBREAKS` veces
(1 por defecto); otro doble KO termina en empate. Esto es una regla explícita
de desempate, no una resurrección con Botiquín. Si ambos siguen vivos, continúa
el duelo. Una victoria/empate pasa a `SETTLEMENT_PENDING`; solo el banco real
conciliado puede pagar/devolver y finalizar, nunca el motor ni el navegador.

Celery Beat ejecuta `resolve_closed_rounds` cada 5 segundos. Hay tres intentos
por captura, ventana de 30 segundos entre cálculos y una clave invariable.
Un timeout conserva la ronda pendiente y los HP intactos; los reintentos
reutilizan la misma entrada. Respuestas concurrentes/tardías del motor no
aplican daño dos veces gracias al bloqueo del duelo y la resolución única.
El motor debe garantizar idempotencia incluso si la primera solicitud tarda
más de 30 segundos. Agotado el presupuesto, queda pendiente de revisión,
con error explícito, sin inventar resultado. Tras revisar al proveedor:

```powershell
& .\.venv\Scripts\python.exe manage.py reconcile_clash_round <question_id> --settings=config.settings.development
# Solo tras agotar la ventana y revisar el fallo:
& .\.venv\Scripts\python.exe manage.py reconcile_clash_round <question_id> --reset-budget --settings=config.settings.development
```

El reinicio de presupuesto no cambia entrada, proveedor ni clave; no se cambia
el proveedor de una ronda capturada mientras esté pendiente.

#### Esquema y comprobación de esta fase

La migración `duckyclash.0004` añade plazos/ocultamiento por jugador, UUID
idempotente de acción, resolución de ronda y auditoría de HP. No modifica
usuarios, perfiles, existencias, saldos, catálogos existentes ni migraciones
anteriores. Es reversible; exportar auditorías antes de revertir y no hacerlo
con duelos o reservas vivos. La reversión elimina exclusivamente los campos y
tablas nuevos de esta fase.

```powershell
& .\.venv\Scripts\python.exe manage.py migrate duckyclash --settings=config.settings.development
& .\.venv\Scripts\python.exe manage.py seed_clash_items --settings=config.settings.development
& .\.venv\Scripts\python.exe manage.py test apps.duckyclash.test_combat --settings=config.settings.development --noinput
node --test .\apps\duckyclash\test_ring.js
```

Para verificar desde la interfaz: con inventario real de complementos y un
duelo legítimamente activo, abrir el Ring con cada jugador en sesiones distintas.
Preparar Nube/Acelerador sobre el rival durante la cuenta atrás, usar
Escudo/Pócima sobre el propio reloj/opciones durante la pregunta
y Botiquín con HP inferiores al máximo. Comprobar que el stock baja una vez,
que reenviar el mismo formulario no duplica el efecto y que un tercero no puede
activar nada. Multiplicador debe avisar de indisponibilidad con el LMS actual,
sin descontar existencias. Cuando ambos jugadores han respondido, la ronda se
cierra sin esperar a agotar el tiempo. Si falta alguna respuesta, espera al
último plazo efectivo (incluidas las modificaciones de objetos). Los envíos HTTP
y WebSocket solicitan el avance después de persistir la respuesta; el motor
procesa ambos resultados conjuntamente e idempotentemente, sin revelar la
corrección mientras falte una respuesta y siga abierto el plazo. El motor aplica
ambos daños y muestra los HP; no muestra respuestas correctas.

**No habilitar apuestas reales todavía:** siguen pendientes el
contrato real DuckyBank, la API real EcoMotor, el proveedor certificado para
Multiplicador. El coordinador de preparación/inicio ya está implementado. No se deben
forzar estados desde el navegador ni usar el fake para apuestas reales. Las
pruebas crean esos estados en una base aislada. Faltan verificación concurrente
en PostgreSQL y cliente Pygame web; una aplicación Pygame de
escritorio no se ejecuta directamente en el navegador.

### Motor autoritativo y ventanas de combate

`apps.duckyclash.engine.DuelEngine` es independiente de HTML, HTTP y gráficos.
Reutiliza el usuario, perfil, inventario, preguntas y saga bancaria anteriores.
`CombatSession` almacena la fase de juego separada del estado económico de
`Duel`; una victoria no significa que el banco haya pagado.

Flujo: `WAITING_PLAYERS` -> `READY` -> `COUNTDOWN` -> `QUESTION_ACTIVE` ->
`ROUND_RESOLVING` -> `ROUND_RESULT` -> nueva `COUNTDOWN` o `FINISHED`.
Ambos jugadores deben pulsar **Estoy listo** después de la hora acordada,
tener presencia reciente y dos reservas verificadas. La preparación se
confirma una vez. La pregunta se selecciona privadamente; hasta la apertura
solo se expone su identificador, nunca el enunciado, opciones o solución.
La cuenta atrás dura exactamente tres segundos según `opens_at`.

Ventanas verificadas otra vez bajo bloqueo, después de cualquier llamada
a proveedores:

| Objeto | Ventana | Efecto |
| --- | --- | --- |
| Nube | `COUNTDOWN` | Ocultamiento desde la apertura, no desde el clic |
| Acelerador | `COUNTDOWN` | Reduce el plazo rival sin anticiparlo a la apertura |
| Multiplicador | `COUNTDOWN` | Añade distractores certificados a las opciones privadas del rival |
| Escudo | `QUESTION_ACTIVE` | Elimina únicamente falsas visibles propias |
| Pócima | `QUESTION_ACTIVE` | Amplía el plazo propio sin reabrir un reloj vencido |
| Botiquín | Configurable, por defecto `QUESTION_ACTIVE` | Cura sin superar HP iniciales ni resucitar |

Defensas y recuperación requieren seguir vivo, no haber respondido y estar
dentro del plazo propio. Las acciones llevan UUID: repetir el mismo recibo no
consume de nuevo, incluso al reconectar. Una respuesta aceptada es inmutable;
repetir la misma opción reconoce el recibo sin volver a corregir, pero cambiarla
se rechaza. La validación y todos los plazos se comprueban en Django antes y
después de consultar al proveedor. Ninguna fecha/hora enviada por el cliente
modifica el estado. Se esperan ambos plazos individuales antes de capturar
los dos resultados y aplicar el daño conjuntamente.

Configuración opcional `DUCKYCLASH_ENGINE_RULES`, congelada por sesión:

```python
DUCKYCLASH_ENGINE_RULES = {
    'countdown_seconds': 3,
    'question_seconds': 30,
    'result_seconds': 3,
    'presence_seconds': 20,
    'waiting_seconds': 300,
    'max_rounds': 20,
    'healing_windows': ['question_active'],
}
```

Para Botiquín durante la preparación, incluir también `'countdown'`.
El máximo de rondas produce empate auditable con devolución a través del banco.
Si no llegan ambos jugadores listos en el plazo de preparación, se solicita
cancelación compensada: no se inventa un ganador ni se confirma una devolución
antes de conciliarla. Se limita la escritura de presencia a una cada dos
segundos por jugador; el navegador envía latidos como máximo cada cinco segundos.
Una desconexión después de iniciar la cuenta atrás **no pausa ni reinicia**
los relojes. Reingresar recupera pregunta, HP, preparación y recibos existentes.

Celery Beat ejecuta `apps.duckyclash.tasks.advance_duel_engines` cada segundo;
el worker mantiene el avance cuando no hay navegadores conectados.
Además, los POST de presencia con CSRF y los mensajes WebSocket autenticados
`enter`/`presence` avanzan el motor después de registrar la presencia, fuera de
la transacción y usando exclusivamente plazos del servidor. Durante la cuenta
atrás el Ring envía presencia cada segundo (en otras fases, cada cinco segundos).
Así una sala conectada no queda bloqueada en `COUNTDOWN` si Beat/worker no está
disponible. Los GET de estado y los mensajes `sync` siguen siendo de solo lectura.
Un fallo del banco no inicia el duelo. Un fallo de preguntas conserva la fase
y muestra `knowledge_unavailable`; no se fabrican preguntas. Un fallo de EcoMotor
conserva `ROUND_RESOLVING`, HP intactos y `ecomotor_pending`; reutiliza los
reintentos limitados de la resolución persistida. Agotados, necesita conciliación
operativa. Nunca se mantiene una transacción mientras se espera al proveedor.

Endpoints de participantes:

- `POST /clash/duels/<id>/ready/`: preparación; CSRF obligatorio.
- `POST /clash/duels/<id>/presence/`: latido; CSRF obligatorio.
- `GET /clash/duels/<id>/state/?after=<sequence>`: snapshot privado y eventos.

El Ring recibe snapshots por WebSocket y recupera el estado por HTTP; calcula la cuenta atrás usando
`server_time` y `deadline`, no `Date.now()`. Recupera hasta 100 eventos por página,
con `cursor`, `has_more` y secuencia monotónica. Los emisores bloquean el mismo
duelo; la serialización permite solo tipos/campos públicos conocidos y nunca
opciones respondidas, corrección ni claves de validación. Las reservas y la
liquidación siguen siendo operaciones exclusivamente servidor.
El consumidor privado descrito abajo consume el mismo motor sin decidir
resultados. No ejecuta Pygame en el navegador.

La migración aditiva y reversible `duckyclash.0005` añade `CombatSession` y
presencia en `ParticipantState`. No modifica identidad, inventario ni saldos.
Revertirla elimina esos datos de coordinación: no hacerlo con combates vivos.

```powershell
& .\.venv\Scripts\python.exe manage.py test apps.duckyclash.test_engine --settings=config.settings.development --noinput
node --test .\apps\duckyclash\test_ring.js
```

Verificación UI: con los proveedores configurados, preguntas aprobadas y
reservas confirmadas, abrir el mismo Ring con los dos usuarios. Antes de la
fecha no permite preparación. Después, pulsar **Estoy listo** en ambos; preparar
ataques en 3/2/1 y comprobar que aparecen la misma pregunta y relojes individuales.
Responder una vez, usar defensas antes de responder, esperar resultado y próxima
ronda. Cerrar/reabrir una sesión debe recuperar el estado sin consumir de nuevo;
cambiar el reloj local no amplía ningún plazo. Sin contratos reales, verificar
estas reglas con las pruebas aisladas, nunca forzando estados o dinero reales.

### Sala privada en tiempo real

Infraestructura comprobada: Channels 4.1, Daphne, `config.asgi`,
`config.routing`, `AuthMiddlewareStack` y `AllowedHostsOriginValidator`.
El LMS ya utiliza consumidores privados. Redis existe como caché y broker
Celery, pero `CHANNEL_LAYERS` usa `InMemoryChannelLayer`, también al heredar
la configuración de producción. No se añade otro framework, autenticación,
dependencia o migración.

Ruta: `ws://<host>/ws/clash/duels/<id>/` (usar `wss://` con HTTPS).
El proxy debe admitir upgrade WebSocket hacia el ASGI existente; no se ha
desplegado ni modificado infraestructura de producción.

`DuelConsumer` exige sesión autenticada, usuario activo, Ducky creado y
participación en ese duelo. Antes de cada acción, aviso de canal o snapshot
periódico vuelve a consultar identidad, permisos y validez actual de la sesión.
Cerrar sesión, desactivar al usuario o perder el perfil permitido revoca la sala.
Los orígenes se filtran con el validador existente; no introducir `*` en hosts.

Mensajes entrantes JSON estrictos, sin campos adicionales:

```json
{"type": "sync", "after": 0}
{"type": "enter"}
{"type": "presence"}
{"type": "ready"}
{"type": "leave"}
{"type": "answer", "question_id": 123, "option_id": "identificador recibido"}
{"type": "item", "question_id": 123, "code": "NUBE", "action_id": "UUID estable"}
```

No se admiten ganador, HP, tiempo cliente, fase, sala ni destinatario elegidos
por el navegador. La pregunta debe pertenecer a la sala; el servicio de objetos
decide el rival o el propio jugador. Se rechazan binarios, mensajes superiores
a 2048 bytes UTF-8, tipos incorrectos y cursores/preguntas booleanos.
Límite: 20 mensajes por conexión en diez segundos y 40 solicitudes/conexiones
por usuario en una ventana de diez segundos, usando la caché existente.
En desarrollo la caché compartida es local al proceso; en producción Redis
comparte este segundo límite. Un fallo de Redis cierra temporalmente la conexión
en lugar de desactivar silenciosamente el control.

Salidas: `connected`/`state` con snapshot individual, `ack` sin corrección de
respuesta y `error` con código explícito. Los snapshots incluyen eventos
ordenados: entrada, salida, preparación, cuenta atrás, apertura de pregunta,
respuesta bloqueada, objeto preparado/utilizado, cambio de reloj/HP,
resolución, final y presencia expirada. `time.changed` y `health.changed` son
avisos; los valores autorizados se leen del snapshot, no del mensaje del cliente.
Los daños y HP finales de ronda figuran en `round.resolved` y participantes.
La salida de una pestaña no fuerza desconexión de otras: la presencia vence
según el plazo del motor y nunca detiene el reloj.

Cada evento persistido emite **después del commit** únicamente
`{"type": "duel.update"}` al grupo privado. No se difunden preguntas ni opciones
en el grupo. El consumidor reconstruye la presentación para cada receptor:
Nube entrega opciones vacías; nunca se serializan solución, validación ni
respuesta seleccionada. Si se revocan permisos, cierra con `4403`.

Como la capa actual es en memoria, los avisos inmediatos alcanzan su proceso.
Un snapshot autorizado cada segundo recupera también commits de workers u
otros procesos, sin depender de que Redis sea una capa Channels. Los eventos
duraderos y cursor permiten reconexión; el Ring descarta snapshots antiguos
para no restaurar opciones que una Nube acaba de ocultar. Los formularios
HTTP con CSRF siguen disponibles para acciones; el socket envía sincronización
y presencia. Se intentan como máximo tres reconexiones de transporte; no se
reenvían automáticamente respuestas ni consumos. En una revocación o límite
de frecuencia no se reconecta ni se encubre el fallo con el fallback.

Pendiente para mayor escala: dimensionar snapshots/latidos y, si se necesita
fan-out inmediato entre procesos, adoptar una capa Redis Channels mediante
un cambio explícito de dependencia y configuración. No es necesario para la
recuperación autoritativa implementada; no se afirma haberla configurado.

```powershell
& .\.venv\Scripts\python.exe manage.py test apps.duckyclash.test_realtime --settings=config.settings.development --noinput
node --test .\apps\duckyclash\test_ring.js
```

Verificación UI: abrir el Ring autenticado en dos sesiones y comprobar en
Network/WebSocket la ruta privada, `connected` y snapshots. Preparar Nube:
el socket del rival no debe recibir sus opciones hasta vencer el ocultamiento.
Cerrar/reabrir la pestaña recupera estado y recibos; cerrar sesión revoca el
socket. Un tercero, otra sala, un origen no permitido o una pregunta ajena
deben ser rechazados. Si falla el transporte, el aviso indica recuperación
HTTP sin repetir apuestas, respuestas ni objetos.

### Páginas integradas de DuckyClash

La navegación lateral y la subnavegación reutilizan la base y Bootstrap del sitio:

- `/clash/`: área de Clash con contadores y retos recientes.
- `/clash/new/`: elección privado/público; conserva las rutas de creación existentes.
- `/clash/invitations/received/` y `/clash/invitations/sent/`: bandejas privadas paginadas.
- `/clash/invitations/counteroffers/`: contrapropuestas vigentes pendientes de respuesta del usuario.
- `/clash/duels/`: duelos del usuario, paginados y filtrables por estado.
- `/clash/duels/<id>/`: condiciones inmutables, perfiles y reservas.
- `/clash/duels/<id>/waiting/`: sala de espera. Solo habilita «Entrar al Ring»
  desde la fecha acordada, con ambas reservas verificadas y estado compatible.
  Entrar no sustituye la preparación explícita de ambos jugadores.
  Cuando el reto está confirmado y programado, muestra una cuenta atrás hasta
  la apertura (días y HH:MM:SS). Usa la hora enviada por el servidor y tiempo
  monotónico del navegador, no su reloj de calendario. Se sincroniza con la
  consulta privada de estado y desaparece si el duelo termina o pierde sus
  reservas verificadas. Llegar a cero solo muestra «Verificando apertura»:
  nunca habilita el Ring sin autorización actual del servidor. Un fallo de
  conexión oculta el contador y solicita actualizar la sala. Sin JavaScript
  permanecen la fecha pactada y la actualización manual.
- `/clash/duels/<id>/results/`: resultado deportivo separado de operaciones económicas;
  una victoria no confirma el pago. Liberaciones confirmadas aparecen como tales.

Las pantallas privadas y `/waiting/state/` requieren autenticación, onboarding y
participación. Un tercero recibe 404. La actualización de espera, cada cinco segundos,
solo entrega acceso/estado/fecha del servidor: no transmite preguntas, claves ni saldo.
No cambia preparación, presencia ni economía. Los proveedores no se consultan al renderizar.

`/clash/open/state/?ids=...` comprueba como máximo los doce IDs visibles contra
el selector de retos disponibles; nunca expone retos privados. Cada diez segundos
el panel retira retos aceptados/caducados sin recargar filtros. La caducidad visual
usa tiempo monotónico y es informativa; el POST vuelve a validar versión y plazo.
Si falla una actualización, se detiene, muestra el error y requiere recarga manual;
no se reenvían acciones. Las confirmaciones de envío, aceptación y rechazo son
mejoras progresivas: CSRF, permisos y concurrencia permanecen en Django.

Se reutiliza exclusivamente `UserProfileArenas.user_profile_arenas`, sus campos
`nickname`, `avatarType`, `level` y los recursos `DUCKY_TYPES.avatarFallback`.
No existe un `UserProfileClash` localizado ni se crea un perfil alternativo.
El paisaje utiliza su imagen real opcional, sin fabricar un recurso inexistente.

Los `<time>` conservan ISO UTC; `Intl.DateTimeFormat` muestra hora y zona del
navegador. Sin JavaScript queda la hora consciente de zona de Django.
Los formularios conservan su zona explícita y validaciones DST; no se reinterpretan
según el navegador. El filtro de fecha pública usa `TIME_ZONE` del sitio.
No se modifican modelos, migraciones, contratos bancarios ni autenticación.

Prueba de interfaz: abrir **DuckyClash** en la navegación lateral → Crear reto →
privado y buscar el usuario real; desde la otra sesión revisar Recibidas y
contraofertar, luego volver a Contrapropuestas. Aceptar y consultar Mis duelos →
Detalle → Sala de espera. Antes de la fecha o sin reservas el acceso está desactivado.
Con proveedores configurados y reservas confirmadas, el servidor habilita el Ring.
Tras el combate, consultar Resultados y verificar por separado la liquidación.
El panel público retira la oferta al aceptarla desde otra sesión. Revisar a
anchura móvil, con teclado y sin JavaScript; no se requieren pagos de producción.

Verificación realizada con una SQLite temporal y usuarios sintéticos, separada
de la base de desarrollo: ocho páginas a 360, 390 y 1280 píxeles (24 comprobaciones,
HTTP 200 y sin desbordamiento horizontal), buscador real, foco de teclado y
presentación ISO UTC → hora local. La corrección de anchura se limita a
`duckyclash-pages`; no cambia el diseño de las otras áreas.

```powershell
& .\.venv\Scripts\python.exe manage.py test apps.duckyclash.test_pages --settings=config.settings.development --noinput
node --test .\apps\duckyclash\test_pages.js
```

## Ring gráfico web: Canvas y controles accesibles

Se adopta la opción A: cliente web Canvas/JavaScript conectado a la sala privada
existente. Pygame, pygbag y una cadena de compilación para navegador **no localizados
en el repositorio**. No se afirma soporte Pygame-HTML ni se añade esa dependencia.
Un cliente Pygame nativo es opcional y no se entrega en esta fase; necesitaría
autenticarse y consumir el mismo contrato autorizado, sin calcular combate.

`ring-scene.js` solo dibuja: no tiene transporte, evaluador, inventario ni
algoritmo de victoria. `ring.js` conserva WebSocket privado, recuperación HTTP,
CSRF y descartado de snapshots antiguos. Los cambios de fase y pregunta no
recargan el Ring. Solo el resultado persistido en Django activa victoria/empate;
HP cero no autoriza al cliente a declarar un ganador. Resultado y liquidación
se muestran por separado.

El paisaje usa la imagen real de la oferta aceptada, si existe. Ambos personajes
usan `duck-front.svg`, un placeholder original frontal de cuerpo entero, con
acentos del perfil real. Los retratos existentes no se hacen pasar por sprites
frontales. Los assets quedan separados del motor; para sustituirlos se cambia
la URL de presentación en `RingView`, no las reglas ni el modelo de usuario.
Ante un recurso ausente se informa y dibuja un fallback, sin bloquear controles.

El snapshot incluye únicamente stock propio y disponibilidad de los seis objetos.
La validación definitiva y el consumo siguen en Django/DuckyShop. Ataques
preparados muestran el aviso de preparación y se animan al abrir la pregunta:
Nube superpone una nube, Acelerador y Multiplicador representan el ataque.
Escudo dibuja protección; Pócima y Botiquín representan recuperación de
tiempo/vida. Cambios de HP confirmados animan daño/curación. Eventos repetidos,
antiguos o históricos de una reconexión no repiten animaciones ni consumos.
Multiplicador requiere distractores certificados y no consume si faltan.

Un objeto enviado queda bloqueado hasta confirmación; stock y efectos no se
actualizan optimistamente. Si hay error, desconexión o confirmación pendiente
durante diez segundos, se indica recarga para conciliar, nunca reenvío automático.
Tras confirmar se genera otro UUID para una nueva acción.

Canvas es decorativo: nombres, barras HP nativas, pregunta, opciones, reloj,
preparación, inventario y resultados también existen como HTML accesible.
`prefers-reduced-motion` evita movimiento; formularios POST/CSRF permiten
alternativa sin WebSocket. Sin JavaScript el estado requiere recarga manual.
Sin reservas reales/proveedores configurados, el Ring no simula un combate.

Se comprobó con Daphne/ASGI y SQLite temporal, usuarios sintéticos y proveedores
fake explícitos: conexión privada, paso a pregunta sin recarga, respuesta
bloqueada, los seis objetos confirmados, Escudo de cuatro a dos opciones,
Botiquín de 75 a 95 HP, y ausencia de claves privadas en el snapshot.
A 360, 390 y 1280 píxeles no hubo desbordamiento horizontal; se verificaron foco
en opciones, formularios con CSRF y Canvas decorativo. Las pruebas automatizadas
cubren victoria explícita, reconexión, duplicados y movimiento reducido.

Verificación: dos sesiones autenticadas → Mis duelos → Sala de espera → Ring.
Pulsar «Estoy listo» con ambos, comprobar 3–2–1, misma pregunta, respuesta
bloqueada y actualización sin recargar. Usar ataques en cuenta atrás y
defensas antes de responder; verificar stock y efectos solo tras confirmación.
Revisar teclado, móvil, movimiento reducido y Resultados para pago/devolución.

```powershell
node --test .\apps\duckyclash\test_ring.js .\apps\duckyclash\test_ring_scene.js
& .\.venv\Scripts\python.exe manage.py test apps.duckyclash.test_engine apps.duckyclash.test_realtime apps.duckyclash.test_pages --settings=config.settings.development --noinput
```

## Avisos, historial y reglas auditadas de DuckyClash

### Reutilización y entregas idempotentes

No se añade ni modifica un usuario o perfil. `users.User` sigue siendo
`AUTH_USER_MODEL`; `UserProfileClash` sigue **no localizado en el repositorio**.
Se reutilizan `ducky_arenas.UserProfileArenas`, el reverso real
`user_profile_arenas`, su nickname/avatar y sus contadores gamesPlayed,
wins/losses/draws, rachas y lastPlayedAt. El servicio existente
`update_match_stats` recibe resultados confirmados bajo bloqueo del perfil.
`ProfileResultUpdate` registra la proyección una sola vez por resolución;
no es un segundo perfil, inventario o saldo.

[notifications.py](../apps/duckyclash/notifications.py) escribe en la bandeja
real `users.Notificacion`. `NotificationReceipt` deduplica por destinatario y
hecho/versionado; si se borra el aviso de la bandeja, el recibo permanece y no
lo vuelve a crear. Aviso y recibo se confirman en la misma transacción.
También se revierten si falla la transición que los genera.

Se notifican reto privado recibido, contrapropuesta, aceptación privada/pública,
rechazo, caducidad, inicio próximo con reservas verificadas, resultado y
liquidación/devolución terminada. El resultado deportivo no afirma que se
haya pagado: la notificación económica se genera únicamente cuando acaba la
saga. Los enlaces llevan a pantallas con permisos de participantes, sin
soluciones, claves ni datos del inventario del rival.

Se reutilizan las tareas de Worker/Beat, sin nuevo broker ni dependencias:

- `expire_open_challenges` mantiene su nombre/entrada existente y ahora
  expira ofertas Clash tanto privadas como públicas; no toca retos Arenas
  antiguos sin ofertas Clash.
- `advance_duel_engines` publica el aviso previo dentro de la ventana congelada,
  avanza motores y recupera resultados históricos todavía sin marcador de
  proyección, en orden de resolución. Los reintentos no duplican estadísticas
  ni avisos; resultados antiguos no sobrescriben una actividad/racha más reciente.
  Un perfil histórico ausente se registra como error pendiente de revisión y
  no impide avanzar los otros motores.
- `reconcile_bank_operations` confirma pagos/devoluciones y sus notificaciones.
  También concilia las cancelaciones por ausencia continuada.

### Historial privado, sin espejo bancario

La navegación Clash incluye **Historial y estadísticas**, `/clash/history/`.
Requiere sesión y onboarding; consulta exclusivamente las participaciones del
usuario autenticado, sin aceptar un ID de otro usuario. Tiene filtro por
categoría, desglose por área, estados vacíos y paginación de 12 combates.
Conserva la plantilla, Bootstrap y presentación horaria local del sitio.

[history.py](../apps/duckyclash/history.py) deriva los detalles de hechos
autoritativos: respuestas, resoluciones, cambios HP, consumos y operaciones
económicas. Las agregaciones independientes evitan multiplicar importes al
unir respuestas/objetos; la página precarga sus registros detallados.

- Victorias/derrotas/empates requieren `DuelResolution`. Las cancelaciones o
  incomparecencias sin ganador no inventan resultados deportivos.
- Respondidas cuenta respuestas persistidas. Aciertos solo cuenta rondas cuya
  resolución de daño ya fue aplicada; una respuesta en curso no revela su
  corrección, tampoco al consultar este historial.
- Daño usa HP realmente descontado (`before - after`), limitado a cero;
  curaciones y reinicios de desempate no se contabilizan como ataques.
- Objetos cuenta consumos confirmados y no solicitudes repetidas.
- DuckyCoins ganadas/perdidas son **netas** y exigen liquidación confirmada:
  ganador = bote menos apuesta propia; perdedor = apuesta gastada.
  Reservas o liquidaciones pendientes no son pérdidas ni ganancias.
  Empates/cancelaciones con devolución terminada tienen neto cero.
  Los contadores del perfil no conceden monedas ni sustituyen al banco.

### Admin y política congelada

El Admin ofrece una única **Configuración de DuckyClash**. Para crear/editar
se necesita permiso de modelo add/change y `duckyclash.manage_rules`
(«Administrar reglas auditadas de DuckyClash»); un superusuario los satisface.
No se admite eliminar la configuración ni crear múltiples filas.
Los modelos operativos/recibos/resultados permanecen de consulta.

Se pueden ajustar HP inicial, segundos de pregunta/resultado, rondas,
máxima apuesta por jugador, límites de caducidad, aviso previo, acceso al Ring,
tiempos de presencia/preparación/abandono, doble KO/desempates y los parámetros
de los seis objetos. El JSON de objetos exige exactamente sus seis códigos;
por objeto admite amount, max_uses, max_per_round y cooldown, y para
POCIMA_TIEMPO también max_extra_seconds. Botiquín admite ventanas
`countdown` y/o `question_active`. La cuenta atrás sigue siendo de tres segundos.
El máximo de caducidad es al menos 60 segundos y el intervalo debe admitir
un plazo entero en minutos, compatible con el formulario privado; sus opciones
se adaptan a los límites. Los formularios y servicios validan las mismas reglas.
La duración mínima se comprueba contra una única hora de recepción calculada
por Django; no se rechaza el mínimo exacto por repetir la comprobación tras
la consulta de saldo, ni se admite una hora aportada por el navegador.

Decisiones conservadoras, sin inventar contratos o penalizaciones bancarias:

- `ring_access=scheduled`: acceso desde la hora pactada y con ambas reservas.
  `disabled` impide entrar/prepararse en **nuevos duelos aceptados**.
- Incomparecencia: cancelar y devolver al terminar el plazo de preparación,
  sin ganador, premio ni sanción.
- Abandono por defecto: permitir reconexión, con relojes servidor que siguen.
  Opcionalmente `refund` cancela/devuelve tras ausencia continuada superior al
  plazo configurado; no convierte una desconexión en una victoria pagada.
- Doble KO: empate/devolución por defecto, o número acotado de desempates con
  reinicio explícito de HP. Al agotarlos, empate.

Cada guardado renueva la versión. Django Admin registra usuario, fecha y
snapshots **antes/después** en su `LogEntry` habitual. El enlace «Historial»
del Admin identifica al actor y la edición; el JSON del registro conserva
los valores completos. Los cambios por consola/SQL no son una vía administrativa
autorizada y no sustituyen esta auditoría.

La aceptación crea `Duel.rules_snapshot`: HP, tiempos, objetos, desempates,
ausencias, Ring y recordatorios quedan congelados **antes del primer acceso**.
El motor, consumos y daño usan esa copia, no los ajustes actuales.
Editar Admin no modifica un duelo programado o en curso ni una oferta aceptada.
Ofertas nuevas/contrapropuestas usan los límites actuales, pero no se rechaza
un acuerdo existente solo porque después se rebaje el máximo de apuestas.
Sin fila de configuración se usa la configuración de entorno validada;
los providers económicos/conocimiento/EcoMotor siguen requiriendo contratos
reales, y esta configuración no los habilita ni los simula.

### Migración y comprobación

[0006](../apps/duckyclash/migrations/0006_notificationreceipt_profileresultupdate_and_more.py)
añade configuración, recibos, marcador de proyección y snapshot por duelo.
No cambia el esquema del usuario/perfil. Congela los duelos existentes
preservando sus HP y los tiempos ya guardados en `CombatSession`; los demás
valores se capturan de los ajustes vigentes al migrar. No puede reconstruir
valores históricos de objetos que nunca se guardaron: revisar esos ajustes
antes de migrar un entorno con duelos activos.
La reversión elimina las columnas/tablas añadidas, no los usuarios, perfiles,
ofertas, resultados o notificaciones existentes. No se han aplicado cambios
en una base de producción.
Si se revierte después de haber procesado resultados, detener Worker/Beat y
respaldar también recibos/marcadores: volver a aplicarla sin restaurarlos
perdería la deduplicación de avisos/proyecciones ya hechas. La reversión de
esquema no deshace estadísticas de perfil ni operaciones bancarias reales.

Las pruebas incluyen migración de ida/vuelta, permisos/auditoría, snapshots,
privacidad de respuestas activas, avisos concurrentes/rollback, perfil
idempotente, netos confirmados, empate y devolución por abandono.

```powershell
& .\.venv\Scripts\python.exe manage.py test apps.duckyclash.test_integration --settings=config.settings.development --noinput
& .\.venv\Scripts\python.exe manage.py test apps.duckyclash --settings=config.settings.development --noinput
& .\.venv\Scripts\python.exe manage.py makemigrations duckyclash --check --dry-run --settings=config.settings.development
```

En un entorno de desarrollo preparado, ejecutar las migraciones revisadas y
mantener Worker/Beat activos. Crear un reto entre dos cuentas de prueba,
contraofertar/aceptar/rechazar y comprobar la bandeja. Con un adaptador bancario
de prueba explícito, comprobar el aviso próximo, finalizar una partida y
comparar Resultado, liquidación e Historial. Desde Admin cambiar HP/tiempos:
un duelo previamente aceptado debe conservarlos y el siguiente usar los nuevos.
Comprobar el registro antes/después en el historial administrativo.
