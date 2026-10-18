# Revision integral de DuckyClash

## Sectores, categorias y subcategorias

Cada fuente de preguntas por subcategoria (`SubcategoryQuestionSource`)
agrupa muchos niveles del nuevo modelo `SubcategoryQuestionLevel`.
Cada nivel contiene una **Referencia** de texto libre (por ejemplo `1/5`,
`2/5` o `Avanzado`), un banco y un indicador de actividad. No hay limite
de cinco niveles. En la ficha administrativa de la fuente se pueden
crear, editar y eliminar niveles en una tabla, con un enlace al editor
LMS de cada banco. La referencia y el banco no pueden repetirse dentro
de una fuente; un banco si puede compartirse entre subcategorias.

La migracion `0012_question_level_model` agrupa las fuentes existentes
de cada subcategoria y traslada sus bancos a niveles con referencias
como `1/5`, conservando aprobador y actividad por nivel. No elimina
bancos ni preguntas. Es una migracion de datos irreversible: antes de
aplicarla debe disponerse de una copia de seguridad si se necesita
volver al esquema anterior. La relacion es
`subcategory.question_sources.levels`.

El nivel no cambia la dificultad individual de las preguntas LMS.
La seleccion actual sigue considerando los bancos activos y publicados
de los niveles de la subcategoria, con aprobadores autorizados; no se
anade seleccion de nivel en los retos ni se modifican duelos existentes.

El catalogo compartido con cursos usa la jerarquia **Sector -> Categoria ->
Subcategoria**. `courses.CourseSector` contiene los sectores y
`CourseCategory.sector` vincula cada categoria a un unico sector; un sector
puede tener muchas categorias. Las subcategorias conservan su relacion con
una categoria y heredan su sector, sin guardar otra relacion que pueda
contradecirla.

Los sectores se crean desde la administracion de cursos y se asignan en
la ficha de cada categoria. El alta de subcategorias en retos privados o
abiertos y su formulario administrativo muestran Sector y Categoria;
el servidor rechaza combinaciones que no correspondan. Las opciones de
categoria indican tambien su sector. La lista administrativa permite
buscar y filtrar subcategorias por sector.

La ficha administrativa de subcategorias incluye botones **+** junto a
Sector y Categoria para crearlos en las ventanas relacionadas de Django
sin abandonar la subcategoria. Al guardar, la nueva opcion queda
seleccionada. Cada boton requiere el permiso de alta correspondiente de
cursos; crear subcategorias no concede permiso para crear sectores o
categorias. Al crear una categoria se debe elegir el mismo sector
seleccionado en la subcategoria.

La migracion `courses/0012_course_sector` conserva las categorias existentes
sin sector; no inventa asignaciones ni cambia los retos historicos. Esas
categorias y sus subcategorias siguen funcionando. Para crear o editar
una subcategoria mediante los formularios hay que asignar primero un sector
a su categoria. Eliminar un sector con categorias o una categoria con
subcategorias esta protegido.

## Animaciones del Ring

Los dos personajes usan los WebP PRE1/PRE2 de `static/duckyarenas/DAR_moves`.
Los originales de espera se llaman `01_ilde.webp`; se conservan esos nombres.
Las tiras estaticas lossless en
`apps/duckyclash/static/duckyclash/character-frames` contienen los cuatro
fotogramas originales en horizontal. Canvas selecciona el fotograma por tiempo:
380 ms por fotograma, salvo idle de PRE2 (580 ms). Dibujar directamente un
WebP animado en Canvas no garantiza que avance su animacion.

`idle` es el estado inicial. Cambiar una respuesta o enviarla activa `attack`
localmente, sin decidir aciertos ni causar dano; `answer.locked` permite
animar al rival tras confirmacion. Las disminuciones de HP del servidor
activan `hit` y `dealDamage` (si ambos reciben dano, prevalece `hit`).
Estas acciones duran un ciclo y vuelven a `idle`. Solo un resultado explicito
selecciona `win`/`lose`, que siguen en bucle; un empate no inventa vencedor.
No se reproducen eventos historicos al reconectar.

Las transiciones funden los fotogramas durante 160 ms y mantienen anclaje,
escala y proporcion. Las imagenes se precargan; si una falla se notifica y
se conserva la imagen anterior o el retrato provisional. Con movimiento
reducido se muestra el primer fotograma de cada estado, sin fundidos.
Al sustituir los originales, hay que regenerar las tiras componiendo sus
fotogramas RGBA en horizontal y revisar los tiempos publicados en `RingView`.

## Dictamen y alcance

Revision local del proyecto existente, limitada a DuckyClash. Se anaden siete
pruebas de recorridos completos y este informe; no se cambia autenticacion,
estilos, modelos de usuario, servicios de otras aplicaciones ni contratos de
produccion. No se han realizado pagos reales, despliegues ni migraciones sobre
la base de datos habitual. La demostracion de navegador utiliza otra SQLite,
cuentas sinteticas y un ledger fake en un proceso ASGI aislado.

**Funcionalidad verificada localmente; no constituye autorizacion para activar
apuestas en produccion.** Faltan contratos bancarios y EcoMotor reales y
validacion de concurrencia con PostgreSQL/infraestructura compartida.

## Arquitectura comprobada

- Identidad: `AUTH_USER_MODEL = users.User`, basado en `AbstractUser`.
  Perfil real: `ducky_arenas.UserProfileArenas`, reverso
  `user_profile_arenas`. `UserProfileClash`: **no localizado en el repositorio**.
  No hace falta otro usuario ni otro perfil.
- [Modelos](../apps/duckyclash/models.py): `Challenge` es proxy de
  `BattleChallenge`. Ofertas versionadas, acuerdo inmutable, duelo,
  participantes, preguntas/respuestas, recibos de objetos, operaciones
  economicas, resoluciones, eventos y reglas congeladas.
- [Servicios](../apps/duckyclash/services.py): negociacion y aceptacion;
  comprobacion de saldo fuera de la transaccion, seguida de confirmacion
  transaccional que revalida participante, version, revision y caducidad.
- [Economia](../apps/duckyclash/economy.py): saga persistente con claves
  idempotentes, reservas, compensacion, resultados inciertos, conciliacion,
  cancelacion y liquidacion unica. No replica saldo como fuente de verdad.
- [Motor](../apps/duckyclash/engine.py), [preguntas](../apps/duckyclash/questions.py),
  [objetos](../apps/duckyclash/items.py) y [combate](../apps/duckyclash/combat.py):
  Django decide ventanas, tiempos, stock, correccion, dano, HP y vencedor.
  Espera/preparacion, cuenta atras de tres segundos, pregunta, resolucion,
  resultado de ronda y final son fases explicitas.
- [Sala privada](../apps/duckyclash/consumers.py): Channels reutiliza sesiones,
  middleware ASGI y validacion de origen existentes. Revalida permisos y
  sesion en mensajes y snapshots; el canal de grupo solo transporta avisos.
- [Ring web](../templates/duckyclash/ring.html): Canvas/JavaScript y controles
  HTML sobre el estilo existente. Pygame nativo no esta incrustado en HTML:
  un cliente de escritorio sigue siendo opcional/no implementado.
- [Historial](../apps/duckyclash/history.py), [avisos](../apps/duckyclash/notifications.py)
  y [Admin](../apps/duckyclash/admin.py): notificaciones existentes,
  proyeccion idempotente del perfil, estadisticas propias y auditoria
  administrativa. Los duelos aceptados conservan su snapshot de reglas.
- [Tareas](../apps/duckyclash/tasks.py): Celery/Beat ya existentes; avance cada
  segundo, conciliacion y caducidad cada treinta segundos segun configuracion.
  No se requiere un segundo scheduler.

## Matriz de cumplimiento

Los endpoints HTTP se encuentran en [urls.py](../apps/duckyclash/urls.py), bajo
`/clash/`; las salas usan `/ws/clash/duels/<id>/`.

| Requisito | Implementacion / endpoint | Evidencia de pruebas | Estado |
|---|---|---|---|
| Usuario/perfil reutilizados, integridad SQL | `models.py`, `policies.py` | `tests.py`, `test_integration.py` | Implementado |
| Privado, contrapropuesta y oferta vigente | `services.py`; `challenges/<id>/counter/`, `accept/` | `test_private_challenges.py`, `test_flow_private.py` | Implementado |
| Publico: un aceptante entre dos candidatos | `confirm_acceptance`; `open/<id>/accept/` | `test_open_challenges.py`, `test_flow_public.py` (dos conexiones) | Implementado; PostgreSQL pendiente |
| Expiracion y exclusion sin depender del job | `selectors.py`, `expire_open_challenges` | `test_open_challenges.py`, `test_flow_public.py` | Implementado |
| Saldo insuficiente, dos reservas, fallo parcial | `bank.py`, `economy.py` | `test_economy.py`, `test_flow_refunds.py` | Motor implementado; banco real pendiente |
| Timeout incierto, compensacion y cancelacion | `lookup`, `request_cancellation` | `test_economy.py`, `test_flow_refunds.py`, `test_flow_public.py` | Implementado con fake |
| Preparacion de ambos y cuenta atras 3/2/1 | `DuelEngine`; `duels/<id>/ready/` | `test_engine.py`, `test_flow_private.py`, navegador | Implementado |
| Misma pregunta y respuesta inmutable | `questions.py`; `questions/<id>/answer/` | `test_knowledge.py`, `test_engine.py`, nuevos recorridos | Implementado |
| Seis objetos, limites, stock y consumo unico | `items.py`, `ShopGateway`; `questions/<id>/item/` | `test_combat.py`, `test_engine.py`, `test_flow_private.py` | Implementado con inventario local |
| Multiplicador sin falsas opciones inventadas | `KnowledgeGateway.distractors` | `test_knowledge.py`, `test_combat.py` | Parcial: proveedor certificado real pendiente |
| Curacion, dano simultaneo, HP acotados | `combat.py`, `EcoMotorGateway` | `test_combat.py`, nuevos recorridos | Motor implementado; EcoMotor real pendiente |
| Victoria/doble KO y liquidacion idempotente | `DuelResolution`, `economy.py` | `test_flow_private.py`, `test_flow_refunds.py`, `test_economy.py` | Implementado con fake |
| Autenticacion, autorizacion y CSRF | mixin existente, politicas, middleware | `test_private_challenges.py`, `test_combat.py`, `test_flow_private.py` | Implementado |
| Sala ajena, revocacion, reenvios y reconexion | `DuelConsumer` | `test_realtime.py` | Implementado |
| Tipos, tamano y frecuencia WebSocket | `DuelConsumer` (2048 bytes; cuotas local/por usuario) | `test_realtime.py` | Implementado; cache compartida pendiente de validacion |
| Rate limit HTTP equivalente en respuestas/objetos | `question_views.py` | validacion de plazo, unicidad y stock; no cuota general comprobada | Parcial; no afirmar proteccion volumetrica HTTP |
| Sin soluciones anticipadas ni opciones bajo Nube | serializacion privada personalizada | `test_knowledge.py`, `test_realtime.py`, `test_engine.py` | Implementado |
| Reloj/HP/ganador enviados por cliente no mandan | `engine.py`, `questions.py`; listas de campos WS/ready | `test_engine.py`, `test_realtime.py`, `test_flow_private.py` | Implementado |
| Historial, avisos y reglas congeladas auditadas | `history.py`, `notifications.py`, `rules.py`, Admin | `test_integration.py` | Implementado |
| Ring navegador, movil y placeholders | templates y scripts de Clash | 31 pruebas Node; dos sesiones de navegador, una a 390x844 | Implementado; assets definitivos pendientes |

## Evidencia ejecutada

| Verificacion | Resultado |
|---|---|
| Regresion inicial Django, antes de los nuevos archivos | 245 tests; OK; exit 0 |
| Siete nuevos recorridos, despues de corregir fixtures | 7 tests; OK; exit 0 |
| Regresion final `manage.py test apps.duckyclash --noinput` | 252 tests; OK; 331.779 s; exit 0 |
| Node: `test_ring.js`, `test_ring_scene.js`, `test_pages.js` | 31 tests; 31 aprobados; exit 0 |
| `manage.py check` | exit 0; un aviso existente `urls.W002` en dashboard, fuera de Clash |
| `makemigrations duckyclash --check --dry-run` | sin cambios; exit 0 |
| `showmigrations duckyclash --plan` | 0001-0005 aplicadas en la BD local consultada; 0006 pendiente |
| Diagnosticos del editor en los tres nuevos archivos Python | sin errores |
| Linters/pytest/CI | no se localizo configuracion de linter/pytest ni workflows; no se instalo tooling nuevo |
| Capacidad local | Python 3.10.4, Django 4.2.16, Node 24.14.1; Docker CLI disponible pero daemon inaccesible |
| Inicio aislado Daphne | HTTP 200 en login; SQLite independiente y datos sinteticos |
| Navegador: dos sesiones, retador/rival | preparacion, pregunta de cuatro opciones, envio de A/B, bloqueo, final y mismo ganador; HP 25/0 |
| Navegador: vista movil del rival | 390x844, sin overflow horizontal observado |

Los recorridos nuevos estan en
[test_flow_private.py](../apps/duckyclash/test_flow_private.py),
[test_flow_public.py](../apps/duckyclash/test_flow_public.py) y
[test_flow_refunds.py](../apps/duckyclash/test_flow_refunds.py).
Incluyen negociacion, dos rondas y los seis objetos, curacion tras dano real de
la ronda anterior, preguntas sin repetir, victoria/pago unico, CSRF y campos
forjados, competencia/expiracion, compensacion, cancelacion incierta y cuatro
rondas con doble KO/devolucion unica.

Se utilizaron `FakePaymentBank`, `DevelopmentFakeEcoMotor` y distractores
certificados **de prueba**. Preguntas e inventario usan las implementaciones
locales reales sobre datos aislados, no APIs externas ni monedas reales.
El hasher rapido de los nuevos tests es exclusivo de esas clases; no cambia
el hasher de la aplicacion. No se mide cobertura porcentual ni se afirma una
auditoria de penetracion exhaustiva.

La validacion grafica autentico dos cuentas mediante el login existente,
separando cookies entre `127.0.0.1` y `localhost`. El navegador integrado no
permitio crear un contexto arbitrario adicional. Algunos controles se
activaron mediante su evento DOM por limitaciones de hit-testing del visor.
La cuenta atras exacta se mide en tests, no por una estimacion visual.
La demo no ejecutaba el worker economico: mostro correctamente combate
finalizado separado de liquidacion pendiente. El pago y su replay se verifican
en los recorridos Django.

## Instalacion y comandos locales

Desde la raiz del repositorio, en PowerShell, utilizando el entorno existente:

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe manage.py check
& .\.venv\Scripts\python.exe manage.py makemigrations duckyclash --check --dry-run
& .\.venv\Scripts\python.exe manage.py migrate --plan
& .\.venv\Scripts\python.exe manage.py test apps.duckyclash --noinput
node --test apps\duckyclash\test_ring.js apps\duckyclash\test_ring_scene.js apps\duckyclash\test_pages.js
```

La instalacion solo es necesaria al preparar un entorno o ante dependencias
ausentes; no se ha vuelto a instalar en esta revision. Si no existe `.venv`,
crearlo con el Python compatible seleccionado antes de instalar requisitos.
En una **BD local/de ensayo respaldada**, aplicar el plan revisado:

```powershell
& .\.venv\Scripts\python.exe manage.py migrate
& .\.venv\Scripts\python.exe manage.py seed_clash_items
& .\.venv\Scripts\daphne.exe -b 127.0.0.1 -p 8000 config.asgi:application
```

El seed crea/actualiza catalogo, no regala inventario. En terminales separadas,
con Redis y una BD de ensayo configurados:

```powershell
& .\.venv\Scripts\celery.exe -A config worker --pool=solo --loglevel=info
& .\.venv\Scripts\celery.exe -A config beat --loglevel=info
```

`solo` es una opcion local Windows, no una recomendacion de capacidad para
produccion. No activar adaptadores de pago reales en una demo.

## Variables y configuracion sin secretos

| Nombre | Uso |
|---|---|
| `DJANGO_SETTINGS_MODULE` | settings existentes: desarrollo o `config.settings.production2`; verificar el destino antes de migrar |
| `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` | configuracion general; nunca usar defaults inseguros en produccion |
| `DATABASE_URL` | BD para `production2`; desarrollo usa SQLite local |
| `REDIS_URL` | cache compartida; desarrollo usa LocMem |
| `CELERY_BROKER_URL` | broker existente para worker/Beat |
| `CORS_ALLOWED_ORIGINS`, `SITE_URL` | origenes y enlaces del sitio; CORS no sustituye validacion de origen WS/CSRF |
| `DUCKYCLASH_BANK_GATEWAY` | consulta de saldo; proveedor local de puntos lee `User.puntos` |
| `DUCKYCLASH_PAYMENT_GATEWAY` | `TransactionalBankGateway`; desarrollo usa `apps.duckyclash.local_bank.LocalPointsBankGateway` salvo proveedor configurado; fuera de desarrollo vacia falla cerrado |
| `DUCKYCLASH_KNOWLEDGE_GATEWAY` | proveedor de preguntas; por defecto LMS autorizado |
| `DUCKYCLASH_ECOMOTOR_GATEWAY` | clase que implementa el contrato de dano; no hay default de produccion |
| `DUCKYCLASH_DOUBLE_KO`, `DUCKYCLASH_MAX_TIEBREAKS` | defaults anteriores al snapshot si no hay configuracion Admin |

`DUCKYCLASH_ENGINE_RULES` y `DUCKYCLASH_ITEM_RULES` son diccionarios de settings,
no variables de entorno JSON ya implementadas. Admin tiene precedencia y cada
aceptacion congela sus valores. No hay variables de URL/token bancario
documentadas como reales: se definiran tras confirmar el contrato externo.

## Migraciones

**Actualizacion posterior:** se añade
[0007](../apps/duckyclash/migrations/0007_local_points_bank.py) para conservar el
proveedor económico del duelo y los recibos locales. No mueve saldos existentes.
El proveedor local usa puntos canjeables reales y no sustituye la API externa de
DuckyBank. La suite específica está en
[test_local_bank.py](../apps/duckyclash/test_local_bank.py).
La revisión original que sigue es histórica; su recuento de migraciones/pruebas
no incluye esta ampliación. No revertir recibos tras mover puntos: un rollback
de esquema no devuelve apuestas ni deshace premios.

Validación de puntos locales (14/10/2026): los 277 tests de Clash, incluidos
21 de economía local, pasaron en la ejecución conjunta de 297 tests con usuarios
y gamificación. Dos tests de usuarios fallaron: importación CSV de aulas y texto
«Última actividad» del dashboard. Se reprodujeron aislados con el método anterior
de altas de puntos y configuración bancaria de solo consulta; no son fallos de
esta integración y no se modificaron esas funcionalidades. Las contraseñas rápidas
se configuraron solo en el proceso de pruebas, nunca en settings del proyecto.
`makemigrations --check --dry-run` no detectó cambios y `manage.py check` conserva
solo el aviso previo `urls.W002`.

0007 aplicada únicamente en SQLite de desarrollo tras respaldo. Se verificaron
plan de migración vacío, proveedor `LocalPointsBankGateway` y cero recibos locales:
la validación no movió puntos de usuarios reales. Producción no consultada ni
modificada.

No se genera una migracion nueva en esta revision. Las seis de Clash son
aditivas respecto a los usuarios existentes; la inicial del usuario no cambia.
La BD local consultada tiene pendiente
[0006](../apps/duckyclash/migrations/0006_notificationreceipt_profileresultupdate_and_more.py).
Las bases de tests y de preview recibieron la cadena completa, incluida 0006.
El estado de una BD de produccion no se ha consultado.

0006 conserva HP y fusiona reglas de sesiones antiguas con defaults al congelar
duelos historicos. No puede reconstruir valores historicos nunca almacenados.
Una reversion elimina tablas/campos de esa entrega, **no deshace** operaciones
bancarias ni contadores ya proyectados. Respaldar recibos y marcadores y detener
workers antes de un rollback: borrarlos y reaplicar puede duplicar proyecciones.

## Integracion manual de proveedores reales

1. **DuckyBank: no localizado en el repositorio.** Confirmar autenticacion,
   enteros DuckyCoins, saldo disponible, reserva/liberacion y liquidacion
   atomica de ambos fondos. Implementar el contrato de
   [bank.py](../apps/duckyclash/bank.py), con claves permanentes por operacion,
   eco de solicitud completa y referencias verificables. Establecer timeouts
   acotados; mapear transportes fallidos a resultado incierto, no a rechazo.
   `lookup` debe ser autoritativo; `NOT_FOUND` no demuestra que una solicitud
   retrasada no pueda llegar, y `REJECTED` debe ser terminal. Ensayar reserva A,
   fallo B y liberacion compensatoria; reconciliar operaciones agotadas antes
   de habilitar apuestas. Configurar secretos solo en el entorno/gestor existente.
2. **DuckyShop externo: no localizado en el repositorio.** El inventario local
   ya se consulta y consume de forma atomica; no crear otro. El adaptador actual
   es local y no configurable por una variable de entorno. Una API remota
   necesitaria primero un contrato y saga propia: no sustituir el consumo por
   una llamada lenta dentro de la transaccion de efectos.
3. **Banco del Conocimiento:** la integracion real localizada es el LMS,
   mediante publicaciones Clash expresamente aprobadas. Cargar categorias/
   subcategorias y suficientes preguntas publicadas, cuatro opciones y una
   solucion. No se siembran contenidos de produccion inventados. Para una API
   externa, implementar `KnowledgeGateway` con identidad/revision inmutables.
   Certificar distractores con procedencia; hasta entonces Multiplicador falla
   explicitamente sin consumir stock. No enviar validacion/soluciones privadas.
4. **EcoMotor: no localizado en el repositorio.** Implementar
   [EcoMotorGateway](../apps/duckyclash/ecomotor.py), devolver ambos danos en
   orden de asiento con la solicitud y version. Confirmar que soporta
   dificultad, tiempo y modificadores antes de afirmar que influyen.
   Django conserva/aplica HP. `DevelopmentFakeEcoMotor` solo ilustra 25 de dano
   por acierto y esta prohibido con `DEBUG=False`.

## Prueba manual con dos usuarios

1. Preparar exclusivamente ensayo: migraciones, perfiles Arenas completos,
   categorias/subcategorias, paisaje, preguntas aprobadas e inventario local.
   Configurar proveedores de prueba apropiados y arrancar ASGI, worker y Beat.
   `FakePaymentBank` es un ledger en memoria de tests: **no es** un servicio
   de demo multiproceso listo para conectar con Celery.
2. Abrir dos navegadores/perfiles privados con usuarios distintos. Entrar en
   DuckyClash desde la navegacion; crear privado con nombre real del rival.
   Contraofertar fecha/hora/apuesta y aceptar la version vigente.
3. Comprobar ambas reservas verificadas en espera. Antes del horario el Ring
   esta bloqueado; despues, ambos pulsan Estoy listo. Observar 3/2/1.
4. Preparar ataques durante cuenta atras; usar defensa/pocima/botiquin solo
   dentro de su ventana y sin haber respondido. Nube debe ocultar opciones al
   rival; al expirar vuelven. Responder una sola vez por jugador.
5. Comprobar HP/resultados del servidor, reconectar y verificar que no se
   repiten consumos. El final del combate no implica pago confirmado:
   esperar conciliacion y consultar resultados/historial/avisos.
6. Repetir con un publico y un tercer usuario en otra sesion; aceptar
   simultaneamente. Debe existir un unico acuerdo/duelo y el perdedor de la
   carrera no debe reservar fondos. Comprobar caducidad y cancelacion preinicio.

## Limitaciones y pendientes antes de produccion

- Docker Desktop no tenia daemon accesible; no se ejecutaron PostgreSQL,
  Redis ni pruebas de varios workers reales. SQLite puede rechazar ambos
  escritores concurrentes; los tests permiten una nueva solicitud explicita
  y comprueban que nunca se crean dos acuerdos.
- `CHANNEL_LAYERS` sigue siendo InMemory en los settings existentes, tambien
  heredado por `production2`. El polling privado recupera commits entre procesos,
  pero no sustituye una capa de canales compartida validada. `channels_redis`
  no figura entre las dependencias actuales: no se introdujo sin necesidad.
- Rate limit WS por conexion y usuario implementado; la cuota compartida
  necesita cache compartida. No hay evidencia de una cuota general equivalente
  en POST HTTP de respuestas/objetos. Requiere hardening de trafico antes de
  exposicion competitiva, sin confundir unicidad/stock con throttling.
- APIs bancarias/EcoMotor y distractores certificados reales pendientes. El
  saldo local de lectura no acredita reservas ni permite iniciar duelos legitimos.
- Pool de preguntas insuficiente/proveedor caido deja una fase recuperable
  pendiente: vigilar `last_error`, conciliacion y alertas operativas.
- Assets son placeholders; cliente Pygame de escritorio no implementado.
  No se hizo certificacion completa de accesibilidad ni prueba de carga.
- El aviso existente `urls.W002` se materializo en el redirect post-login a
  `/dashboard/` (404). Navegar directamente a `/clash/` funciona. No se modifico
  esa ruta ajena a Clash.
- Antes de habilitar economia real: cerrar contratos, validar infraestructura
  compartida, revisar hardening HTTP, aplicar migraciones en ensayo respaldado
  y repetir las pruebas con dos usuarios y los proveedores sandbox reales.
