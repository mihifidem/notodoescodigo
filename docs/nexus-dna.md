# Nexus DNA — prompt 09

## Estado y auditoría

Se ha reutilizado `Assessment`, `AssessmentSession`, `NexusModule`, `Question`,
`AnswerOption`, `UserAnswer` y `ModuleResult`. No se ha creado un tercer modelo de
preguntas o de respuestas. Se reutilizan los permisos de `apps.users.access`,
`PersonalGoal`, dashboard/Mi progreso y el PDF de texto de currículo.

El otro flujo encontrado (`TestPlantilla`, `SesionTest`, `InformeTest`) almacenaba
JSON y HTML calculados en navegador, sin definición congelada verificable.
Su historial, informes y derechos de informes pagados se conservan. Sus rutas de
realización, envío, guardado de informes HTML y generación IA están bloqueadas con
503 hasta revisión/migración editorial. No se han convertido resultados antiguos
en evaluaciones validadas ni recalculado sus puntuaciones.

Las demos antiguas apuntan al ejemplo ficticio nuevo. No se ejecutan cuestionarios
HTML subidos al servidor desde las rutas nuevas. Las funciones antiguas de
composición de informes se mantienen por compatibilidad, pero no están expuestas
como un servicio de generación. No se ha avanzado al prompt 10.

## Catálogo y contenido

Familias (código inmutable / etiqueta inicial editable):

| Código | Etiqueta |
|---|---|
| personasphere | PersonaSphere |
| identitymap | IdentityMap |
| decissioncore | DecissionCore |
| conglicttsync | ConglicttSync |
| careernavigator | CareerNavigator |
| behaviormatrix | BehaviorMatrix |
| techmind | Techmind |

Las grafías proceden del encargo. No se deducen teorías ni dimensiones de sus
nombres. Modalidades independientes del plan: `basic`, `essential`, `advanced`,
`premium`. Sus etiquetas, descripción y disponibilidad son administrables.

La semilla idempotente crea siete familias, cuatro modalidades y 28 borradores
editoriales vacíos. No toca cuestionarios HTML, respuestas, usuarios, roles,
contraseñas o membresías. No publica contenido. Repetir la semilla conserva
etiquetas editadas y no duplica borradores ni evaluaciones.

La semilla por sí sola no publica contenido. Los originales existentes pueden
importarse y publicarse con autorización explícita mediante el procedimiento de
importación descrito más abajo. Los ejemplos aritméticos de `test_versioned.py`
solo existen en una base de datos aislada de pruebas. Una versión marcada
`is_fixture` no se lista ni puede iniciarse públicamente.

## Modelos y migraciones

- `NexusFamily`: catálogo con código estable y disponibilidad.
- `NexusModality`: cuatro códigos estables y metadatos administrables.
- `NexusVersion`: evaluación/modalidad/versión, procedencia, acceso, estados
  `draft/review/published/archived`, responsable, fecha, motivo y snapshot inmutable.
- `NexusEditorialEvent`: cambios editoriales, publicación/archivo, actor, motivo y
  fecha; no respuestas personales.
- `AssessmentSession`: versión editorial opcional, definición congelada, origen de
  acceso, alcance del informe, revisión de concurrencia, actividad y resultado.
  El FK nullable conserva sesiones anteriores.
- `NexusGoalLink`: vincula una dimensión/resultado a un `PersonalGoal` existente.
- `NexusAIOperation`: operación bloqueada e idempotente. No es un contador de uso
  ni un saldo de energía.
- `UserAnswer.question` pasa a `PROTECT`. Preguntas/opciones referenciadas por
  versiones publicadas tampoco se pueden borrar, incluso sin respuestas.
- Evento `RegistroActividad.tipo=nexus_completed`: acredita únicamente
  participación, sin puntuaciones de rasgos, puntos ni energía.

Migraciones: `nexus.0018_versioned_assessments`,
`0019_modalities_and_session_uniqueness`, `0020_seed_editorial_catalog`;
`analytics.0006_nexus_participation_event`.

Archivos principales:

- [Modelos reutilizados](../apps/nexus/models.py) y
  [modelos editoriales](../apps/nexus/versioned_models.py).
- [Servicios versionados](../apps/nexus/versioned_services.py),
  [vistas](../apps/nexus/versioned_views.py),
  [rutas](../apps/nexus/urls.py),
  [administración](../apps/nexus/admin.py),
  [protección de referencias](../apps/nexus/versioned_signals.py) y
  [registro de señales](../apps/nexus/apps.py).
- [Guardado automático](../static/nexus/versioned.js) y
  [pantallas nuevas](../templates/nexus/versioned/).
- [Pruebas del motor](../apps/nexus/test_versioned.py) y
  [regresión heredada](../apps/nexus/tests.py).
- [Adaptador dashboard](../apps/users/dashboard_services.py),
  [adaptador progreso](../apps/users/progress_services.py),
  [pantalla dashboard](../templates/dashboard/index.html) y
  [navegación](../templates/base.html).
- [Actividad verificable](../apps/analytics/models.py) y
  [protección del servicio legado](../apps/nexus/services.py).

```powershell
.\.venv\Scripts\python.exe manage.py migrate
```

En despliegues con almacenamiento de estáticos por manifiesto, ejecutar el
`collectstatic` habitual para incluir `nexus/versioned.js`.

### Interfaz Tech Modern Blue

El catálogo, ficha, cuestionario e informe comparten el diseño azul de
`static/nexus/assessment.css`, limitado al contenedor `.nexus-app`. Incluye
cabecera con degradado, tarjetas de evaluaciones, condiciones de inicio,
progreso, opciones seleccionadas e informes por dimensiones. Se adapta a móvil
y al tema claro/oscuro del portal, sin animaciones ni bibliotecas adicionales.
La procedencia editorial sigue accesible en un bloque desplegable nativo.
El catálogo agrupa las evaluaciones publicadas en una tarjeta por familia.
Dentro se presentan sus modalidades en orden Basic (si existe publicada),
Essential, Advanced y Premium, cada una con su ficha, versión y acceso propio.
No se crean opciones ficticias para modalidades sin contenido publicado.
No cambia planes, puntuaciones, alcances de informe, formularios, autoguardado
ni permisos. En despliegues con estáticos recopilados, ejecutar también
`collectstatic` para actualizar la hoja de estilos.

## Matriz de acceso

| Acceso | Evaluaciones propias nuevas | Informe capturado al iniciar | Evolución | PDF |
|---|---|---|---|---|
| Visitante | Solo demo ficticia | Ninguno persistido | No | No |
| FREE | Basic | Básico | No | No |
| PLUS | Basic, Essential | Estándar | Misma versión | Estándar |
| PRO | Todas las modalidades | Ampliado y pasos configurados | Misma versión | Ampliado |
| Derecho de acceso adquirido | Solo evaluación con código contractual coincidente | Alcance editorial contractual | Según acceso vigente | Si alcance estándar/ampliado |

Los terceros **no** se incluyen por tener PRO. Los derechos de acceso por vigencia
se consultan mediante `PurchasedEntitlement` y `can('access_entitlement')`, validando
inicio, revocación y caducidad. Solo se admiten códigos explícitos
`nexus_access_*`; no se convierten unidades de generación o informes en acceso
ilimitado. El contrato debe establecer que se trata de acceso por vigencia.
`definition.purchased_report_scope` define `basic`, `standard` o `extended`.

**Bloqueo real:** no hay reserva/consumo de cantidades en 00B; no se ofrecen
compras nuevas por unidades, paquetes consumibles, ampliación de un informe
anterior ni servicios IA individuales. Tampoco se ha creado checkout, producto,
precio ni derecho de compra para cuentas reales. Configurar un código no vende ni
concede por sí mismo ese acceso.

Cada intento guarda `access_basis` y `report_scope`. Se permite terminar un intento
legítimo y leer/exportar el informe obtenido aunque cambie el plan, se archive la
versión o se desactive su familia/modalidad. No permite abrir un intento nuevo fuera
del acceso vigente. Solo se mantiene un intento en curso por usuario y versión.

## Preparación editorial

En `/admin/nexus/`, un ADMIN con permisos del modelo gestiona familias,
modalidades y versiones. Publicar/archivar requiere además
`nexus.publish_nexusversion`. No se conceden esos permisos automáticamente.
La edición de preguntas/opciones reutiliza sus administradores existentes.

1. Seleccionar el borrador y su `Assessment`; asociar módulos/preguntas.
2. Aportar preguntas originales o autorizadas, opciones y sus valores 0–100.
   Tipos implementados: escala de acuerdo con opciones explícitas y elección única.
   El tipo numérico libre legacy no es publicable en este motor.
3. Registrar finalidad, audiencia, instrucciones, autoría, procedencia,
   autorización, fundamento, limitaciones, responsable, revisión y motivo.
   Evidencia de validación es opcional: si falta, no se afirma que exista.
4. Definir dimensiones e interpretaciones aprobadas y reglas por cada pregunta
   activa de la evaluación. No se ejecuta código introducido en administración.
5. Revisar y publicar con el permiso específico. La validación impide publicar
   contenido incompleto. No marcar fixtures como instrumentos reales.
6. Para modificar una versión publicada, crear otra versión con otro número y
   motivo. Publicación congela textos, opciones, valores, reglas y explicaciones.
   Archivar conserva sesiones e informes.

Esquema de `definition` (sustituir los IDs y textos por contenido aprobado; este
esquema no constituye un instrumento psicológico):

```json
{
  "method": "weighted_mean_0_100_v1",
  "purchased_report_scope": "standard",
  "dimensions": {
    "dimension_aprobada": {
      "label": "Etiqueta aprobada",
      "minimum_answers": 1,
      "brief": "Explicación breve aprobada",
      "explanation": "Explicación ampliada aprobada",
      "recommendation": "Recomendación general aprobada",
      "steps": "Pasos de desarrollo aprobados"
    }
  },
  "questions": {
    "123": {
      "dimension": "dimension_aprobada",
      "weight": "1",
      "reverse": false,
      "required": true
    }
  }
}
```

## Cálculo, intentos e informes

La normalización admitida es la escala editorial explícita 0–100 de cada opción;
no se inventan percentiles ni conversiones adicionales. Dentro de cada dimensión:

`resultado = suma(valor * peso) / suma(peso)`

Inversión explícita: `valor = 100 - valor_opcion`. Se redondea a dos decimales
con Decimal y redondeo HALF_EVEN explícito. Las preguntas no respondidas se omiten del denominador únicamente
si son opcionales **y** se cumple el mínimo editorial por dimensión. Las
obligatorias y los mínimos se validan antes de finalizar. No se genera resultado
final para intentos incompletos. No hay Nexus Score global ni arquetipos inferidos
para el motor nuevo.

El servidor recibe IDs de opciones, comprueba pertenencia y versión y calcula
los valores; ignora scores del cliente. Las opciones del formulario solo exponen
etiquetas e IDs, no claves de inversión o ponderación.

El guardado manual funciona sin JS. Con JS se guarda tras cambiar una respuesta,
se serializan solicitudes y se actualizan revisión y progreso real. Se avisa ante
conflicto de otra pestaña o error de red y al abandonar con cambios pendientes.
No se guardan respuestas privadas en localStorage ni en cachés persistentes.
Ante desconexión, mantener la página abierta y reintentar Guardar al recuperar la
conexión. La entrega es explícita e idempotente.

Informes separan cálculo, explicación editorial aprobada y estado de IA. El
básico no expone explicación ampliada, pasos ni exportación. El estándar y
ampliado exportan exclusivamente su alcance capturado, sin respuestas completas.
PDF con texto seleccionable mediante ReportLab, etiquetas, versión, fecha,
limitaciones y aviso no clínico.

Evolución solo entre intentos distintos de **la misma definición congelada**,
en orden temporal. No hay equivalencias entre versiones aprobadas en el proyecto:
se bloquean con explicación, aunque coincida el nombre del test.
No se ofrece recálculo histórico; cualquier futura herramienta deberá preservar
el original y auditar la operación explícitamente.

## Energía Ducky e integraciones

00C no está disponible. Interpretación y planes IA muestran indisponibilidad y
coste desconocido, no cero. El endpoint registra una operación `blocked`, devuelve
503 y reutiliza una clave UUID para reintentos; otra tarea con la misma clave
devuelve conflicto. No llama a proveedores, crea reservas ficticias, debita saldos
ni añade contadores 10/30. Los informes estándar permanecen disponibles.

- Dashboard: últimos intentos propios, resultados y pendientes.
- Mi progreso: finalizaciones verificables y métrica existente
  `completed_nexus_tests`, sumadas a sesiones históricas sin duplicarlas.
- Objetivos: PRO convierte voluntariamente una recomendación en un objetivo
  manual existente, con fuente al resultado, pasos, fecha y estado. No se simula
  progreso. Repetir la acción no duplica el objetivo.
- Mis logros: evento de participación con cero puntos; no hay regla automática
  Nexus aprobada que otorgue insignias por rasgos. No se inventa ninguna.
- My Career/compartición contextual: pendiente; no se envían datos ni se crean
  links públicos. No se excluyen candidaturas ni profesiones.
- Energía por participación: no integrada sin regla autorizada del 00C; no se
  incrementa saldo, ni se crea ciclo de autorrecarga.

Resultados y respuestas nuevos solo están en rutas del propietario. El rol
docente, academia, ADMIN o headhunter no concede lectura. Los modelos privados
de sesiones, DNA e informes no se registran en el administrador editorial; el
panel de incidencias IA no muestra usuario, respuestas ni resultados.
No se ha creado un acceso excepcional sin finalidad/auditoría contextual.
Las rutas privadas y PDF utilizan `private, no-store`. No se añaden resultados
personales a logs generales ni rankings.

## Rutas

| Ruta | Función |
|---|---|
| `/nexus/catalogo/` | Familias, modalidades, evaluaciones publicadas y acceso |
| `/nexus/demo/` | Ejemplo ficticio efímero, sin crear cuenta/historial |
| `/nexus/evaluacion/<id>/` | Ficha, condiciones y privacidad |
| `/nexus/evaluacion/<id>/comenzar/` | POST: crear o continuar intento |
| `/nexus/intento/<id>/` | Cuestionario, guardado, revisión y entrega |
| `/nexus/intento/<id>/borrar/` | GET: confirmación privada; POST con confirmación y CSRF: borrado definitivo propio |
| `/nexus/intento/<id>/informe/` | Resultado e informe privado |
| `/nexus/intento/<id>/informe/?download=pdf` | PDF del alcance adquirido |
| `/nexus/historial/` | Historial paginado y pendientes |
| `/nexus/evolucion/` | Comparación compatible PLUS/PRO |
| `/nexus/intento/<id>/objetivo/` | POST: objetivo manual PRO |
| `/nexus/intento/<id>/ia/` | POST UUID: indisponibilidad IA idempotente |
| `/nexus/mis-tests/` | Historial HTML previo, sin nuevos envíos verificables |

## Validación y guía con cuentas existentes

Pruebas automáticas en DB aislada: cuentas llamadas `alumno1`, `alumno2`,
`alumno3`, `profesor1`, `admin1`, sin alterar sus homónimas reales. Cubren semilla
repetida, matriz de planes, derechos vigentes/caducados/revocados, publicación,
inmutabilidad, pesos/inversión, validación de opciones, guardado, concurrencia,
idempotencia, PDF/alcance, aislamiento, downgrade, comparación, objetivos,
operaciones IA bloqueadas e integración dashboard/progreso.

```powershell
.\.venv\Scripts\python.exe manage.py test apps.nexus.test_versioned apps.nexus.tests apps.users.tests.DashboardDuckyMeTests apps.users.tests.PersonalGoalDashboardTests apps.users.tests.PersonalProgressViewTests --noinput
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py check
```

Prueba manual sin cambiar cuentas reales:

1. Visitante: abrir catálogo y demo. Comprobar aviso de contenido pendiente y
   ejemplo ficticio; no hay puntuación psicométrica ni historial.
2. `admin1`, si ya tiene los permisos necesarios: revisar los 28 borradores y su
   procedencia vacía; intentar publicar uno incompleto debe dar un error legible.
   No añadir fixtures al entorno real ni conceder permisos para superar la prueba.
3. `alumno1`: comprobar plan real, catálogo y acceso a su historial. No habrá un
   test nuevo realizable hasta aprobar contenido.
4. Una vez publicado contenido autorizado en un entorno de prueba: elegir una
   evaluación incluida en el plan real, responder/guardar/continuar/entregar,
   comprobar versión, limitaciones, informe y ausencia de consumo.
5. `alumno2`: una URL del intento de `alumno1` debe devolver 404.
   `profesor1` y `admin1` tampoco reciben acceso por su rol.
6. `alumno3`: comprobar únicamente las modalidades de su plan o contrato real;
   PLUS/PRO pueden comparar dos intentos compatibles; PRO puede crear un objetivo
   voluntario. No elevar su membresía para simular acceso.
7. Confirmar que la IA sigue deshabilitada, sin llamadas externas ni cargos.

No se modifican contraseñas, se vacían cuentas ni se entregan resultados a
destinatarios externos.

### Borrado de intentos por el usuario

Desde **Mi historial y pendientes**, el cuestionario o el informe, el propietario
puede borrar un intento versionado pendiente o completado. Abrir el enlace no
borra nada: se requiere confirmar explícitamente y enviar el formulario POST con
CSRF. Otras cuentas, incluidos docentes y administradores, no pueden usar esta
ruta para borrar intentos ajenos.

Se eliminan el intento, respuestas, tiempos, resultados por módulo y el informe;
las antiguas URL y la descarga PDF dejan de estar disponibles. Los PDF ya
descargados no se pueden retirar del dispositivo. Las definiciones publicadas,
preguntas, planes y derechos no se modifican. Los objetivos personales creados
desde el resultado conservan sus textos y progreso, pero se elimina su vínculo
al intento: cualquier referencia antigua al informe ya no estará disponible.
Las operaciones IA bloqueadas se desvinculan y el registro de participación sin
respuestas ni puntuaciones se conserva. El historial, dashboard y evolución ya
no consultan el intento borrado. Iniciar otro exige el acceso vigente habitual;
borrar no elude una caducidad ni concede un plan.

Las pruebas heredadas de informes aíslan los HTML de ejemplo en un `MEDIA_ROOT`
temporal por prueba, eliminado al finalizar incluso si falla una aserción. No
deben crear archivos `techmind_<sufijo>.html` en los medios del portal. Los
archivos ya existentes no se borran automáticamente: pueden ser cuestionarios
reales y deben revisarse antes de eliminarlos.

### Importación de originales existentes

El extractor compara los siete bancos JSON con los originales sin ejecutar HTML
ni JavaScript. El importador crea 21 borradores (Essential, Advanced y Premium);
no genera una modalidad Basic ni cambia los permisos de los planes.

```powershell
.\.venv\Scripts\python.exe manage.py extract_nexus_originals --check
.\.venv\Scripts\python.exe manage.py import_nexus_originals --content-version 1
```

`--content-version` identifica la versión editorial; `--version` está reservado
por Django para mostrar su versión. Repetir la importación del mismo banco y
versión no duplica contenido. Publicar requiere `--publish`, `--editor` con
permiso de publicación y `--authorization` explícita. Importar borradores no
los hace visibles ni realizables en el catálogo público.

Resultado de validación local:

- **71 pruebas pasan** con el comando combinado anterior.
- `makemigrations --check --dry-run`: sin diferencias.
- `manage.py check`: solo el aviso preexistente `urls.W002` del dashboard.
- Sintaxis Python revisada con Pylance; JavaScript pasa `node --check`.
- Migraciones aplicadas localmente tras backup SQLite en la carpeta privada de
  sesión. Comparación exacta de registros previos: preservados en 85 tablas de
  usuarios, currículo, Nexus y actividad; únicamente se añaden los borradores y
  catálogos previstos.
- Navegador: catálogo y envío nativo de demo ficticia comprobados. Formularios
  privados, permisos y guardado automático comprobados mediante tests HTTP.
  No se publicaron fixtures en la base real para hacer pruebas de navegador.
- A anchura muy estrecha se observa un desbordamiento de la marca de la cabecera
  global preexistente, fuera del componente Nexus. No se modifica aquí. La
  herramienta de navegador no permitió confirmar interacciones físicas de
  teclado/ratón en esa emulación; la demo se verificó mediante activación nativa
  del control y envío del formulario, no como prueba completa de teclado.

### Pendientes reales

Contenido y aprobación editorial; evidencia científica si la hay; contratos de
compra por unidades y ampliaciones; 00C reserva/confirmación/liberación/conciliación;
equivalencias entre versiones; compartición contextual y accesos excepcionales;
reglas de insignias/energía autorizadas; cualquier integración futura de My Career.
