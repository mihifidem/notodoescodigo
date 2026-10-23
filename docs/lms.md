# NoTodoEsCódigo Campus LMS

El LMS es una aplicación integrada en el proyecto Django existente (`apps.lms`). No define otro proyecto, login, ni modelo de usuario. Su perfil `UserProfileLMS` guarda únicamente preferencias de formación y se relaciona con `settings.AUTH_USER_MODEL`.

## Descripción larga con editor visual

Crear y editar cursos, incluida su configuración y el administrador Django,
reutiliza `django-prose-editor` para la descripción larga: títulos, negrita,
cursiva, listas, enlaces, tablas y deshacer/rehacer. El formulario carga los
recursos locales del editor y avisa si no consigue iniciarlo.
El contenido se sanea al guardar desde el formulario y siempre antes de
renderizarlo como HTML en la ficha. Los listados y resúmenes muestran texto,
sin etiquetas. No cambia el campo almacenado ni requiere migraciones.

## Estilo del Campus

Contenido (`/lms/courses/<id>/areas/content/`) utiliza `.content-tech`:
cabecera azul, módulos y unidades en tarjetas, controles y paneles laterales
con temas claro/oscuro. Se conservan los desplegables, formularios docentes,
requisitos de acceso y acciones de progreso del alumno.

Foro de discusión usa la base Tech Modern compartida de `knowledge-tech.css`
y `.discussion-tech`: cabecera azul, foros en acordeones y botones adaptables.
Se mantienen la visibilidad de los foros y los permisos de edición y participación.

Notas utiliza `.grades-tech`, con filtros y tabla en tarjetas claro/oscuro.
La tabla conserva el desplazamiento y sus cabeceras fijas, y no se alteran
el cálculo, la edición ni la visibilidad de calificaciones por usuario.

Personas y Páginas del curso comparten Tech Modern mediante `.people-tech` y
`.pages-tech`: filtros, directorio y tarjetas adaptables con temas claro/oscuro.
Se conservan los datos restringidos a docentes, permisos de notificación,
búsqueda y acciones de publicación/edición de páginas.
En Páginas, las tarjetas se presentan en una lista de acordeones cerrados
inicialmente. El título y el estado permanecen visibles; al desplegar cada
tarjeta aparecen el resumen, los metadatos y «Abrir», junto con las acciones
de gestión disponibles solo para docentes. Se pueden abrir con ratón o teclado,
sin JavaScript.
La introducción se obtiene del texto del documento, excluyendo la cabecera,
estilos, scripts y plantillas HTML, y se limita a 20 palabras. El documento
original y su diseño se conservan al abrir la página.

Programa de la asignatura muestra el texto de sus campos y unidades sin
etiquetas HTML ni código de estilos, conservando el contenido almacenado.
Usa `.syllabus-tech`, con cabecera azul y secciones
en tarjetas adaptables claro/oscuro. Ocupa todo el ancho sin el bloque de
próximas entregas; se conservan sus datos y permisos.

Pruebas y Actividades usan `.evaluations-tech` con cabecera Tech Modern y
tarjetas de evaluaciones y paneles de información claro/oscuro. Conservan
fechas, estados en directo, entregas, soluciones e intentos con sus permisos.

Archivos y Competencias comparten Tech Modern para directorios, filtros,
tarjetas de insignias y formularios docentes, conservando permisos y operaciones.
Rúbricas y Colaboraciones tienen la misma cabecera y estado vacío tematizados:
siguen siendo secciones pendientes de habilitar y exclusivas de docentes.

Configuraciones usa `.settings-tech` para cabecera, pestañas, campos, botón
de guardado y resumen lateral, compatible con móvil y claro/oscuro.
Conserva el editor enriquecido, validaciones y permisos docentes.

Pruebas ocupa todo el ancho y no muestra el bloque lateral de próximas entregas.
Las fechas propias de cada prueba y los avisos de otras secciones se mantienen.

Rúbricas también ocupa todo el ancho, sin el bloque de próximas entregas.
Colaboraciones y Competencias tampoco muestran ese bloque y ocupan todo
el ancho disponible; no se eliminan datos ni se cambia la navegación.

Anuncios comparte la base Tech Modern de `knowledge-tech.css`, con el ámbito
adicional `.announcements-tech`: cabecera azul, avisos desplegables, estados
de lectura y calendario en tarjetas claro/oscuro. No cambian los permisos,
las confirmaciones, comentarios ni reacciones. Los avisos sin autor muestran
«Equipo docente».

La Base del conocimiento (`/lms/courses/<id>/areas/item-bank/`) utiliza
`.knowledge-tech`: cabecera azul, bancos en tarjetas adaptables, botones y
avisos CSV con soporte claro/oscuro. No cambia consultas, permisos ni acciones.
Esta sección ocupa todo el ancho y no muestra el bloque de próximas entregas;
las demás áreas conservan su planificación.
Los bancos se presentan en acordeones cerrados inicialmente: la cabecera muestra
el título, estado y cantidad de preguntas; al desplegarlos aparecen la descripción,
los avisos CSV y las acciones de abrir y eliminar. Funcionan también sin JavaScript.
Un acordeón exterior, también cerrado, reúne los bancos que comparten nombre
y terminan en nivel `1/5` a `5/5` (por ejemplo, `Python I`). Los nombres sin
ese sufijo se conservan completos como grupo propio; no se modifica ningún banco.

La portada `/lms/courses/<id>/` usa Tech Modern Blue mediante
`.course-detail-tech`: cabecera destacada, anuncios y contenido en tarjetas,
herramientas y calendario con colores claro/oscuro y adaptación móvil.
Se mantienen los permisos de edición, matrícula, vista previa, anuncios y
el recorrido del alumno.

Crear y editar cursos usan Tech Modern Blue: cabecera docente azul, formulario
con tarjetas, controles adaptables y temas claro/oscuro. Los estilos se limitan
a `.course-form-tech`; se conservan el editor visual, el slug automatico,
los errores, la creacion de categorias y los permisos del servidor.

### Generador de URL del Banco del Conocimiento

Desde la raiz del repositorio, ejecuta en PowerShell:

```powershell
.\scripts\generar_url_banco_preguntas.ps1
```

Solicita URL base (por defecto `http://127.0.0.1:8000`), modo de filtro,
ID de categoria (subcategoria opcional) o modulo/unidad formativa, dificultad 1-5 y
cantidad 1-100. Muestra la URL completa lista para copiar. Usa los IDs reales
del portal; el script valida el formato, pero no consulta si existen.

Tambien se pueden proporcionar todos los valores sin preguntas interactivas:

```powershell
.\scripts\generar_url_banco_preguntas.ps1 -BaseUrl http://127.0.0.1:8000 -Modo Categoria -Categoria 1 -Subcategoria 2 -Dificultad 3 -Cantidad 10
.\scripts\generar_url_banco_preguntas.ps1 -BaseUrl http://127.0.0.1:8000 -Modo Modulo -Modulo 4 -UnidadFormativa 5 -Dificultad 2 -Cantidad 20
```

No envia peticiones ni solicita tokens. Para abrir la URL se necesita sesion
autenticada con permisos de edicion de los cursos de origen; las soluciones
no son publicas. El servidor devuelve 409 si no hay suficientes preguntas.

`/lms/campus/` utiliza Tech Modern Blue: cabecera azul, plan efectivo,
accesos personales, tarjetas de cursos y barras de progreso, con temas
claro/oscuro y adaptación móvil. Las reglas de `lms/campus-tech.css` están
limitadas a `.campus-tech`. Se conservan permisos, matrícula, progreso,
historial, motivos de acceso y ausencia de datos personales para visitantes.

`/lms/` (Mis aulas) comparte estos estilos mediante `.classrooms-tech`:
cabecera personal, contador de aulas, navegación por secciones, tarjetas
adaptables y bienvenida con soporte claro/oscuro. Conserva las membresías,
inscripciones anteriores, cursos publicados y el permiso para crear aulas.

## Campus personal (prompt 11)

La entrada **Ducky Academy → Campus · Mis cursos** está en `/lms/campus/`.
Para los cursos activados explícitamente, matrícula y derecho vigente se
comprueban por separado; los requisitos y condiciones se congelan en la matrícula.
La [guía de Campus](campus.md) describe activación, permisos, reanudación,
entregas, certificados privados y verificaciones. Incluye los contratos todavía
pendientes de catálogo/organizaciones y Energía Ducky. Los cursos sin activar
conservan el flujo descrito en las secciones siguientes.

## Acceso y roles

Todas las pantallas LMS heredan el tema compartido inspirado en `ejemplo_home.html`: fondo gris claro, tarjetas blancas, cabeceras azul oscuro, botones principales amarillos y navegación por iconos. La tipografía usa DM Sans y Space Grotesk con fuentes alternativas locales. Los estilos se limitan a `body.lms-theme`, sin cambiar el aspecto de las demás aplicaciones ni la navegación global del sitio.

- El panel requiere una sesión existente y está en `/lms/`.
- Crear cursos requiere el permiso `courses.add_curso`; publicar requiere `lms.publish_course`. Los permisos se asignan a usuarios/grupos desde la administración de Django.
- Para habilitar un profesor: en **Usuarios**, selecciona la cuenta, establece
  **Rol → Profesor** y añade en **Permisos de usuario** el permiso
  **courses | curso | Can add curso** (`courses.add_curso`). Puede asignarse
  también mediante un grupo de profesores. No requiere marcar staff ni
  superusuario; el acceso de creación está en `/lms/courses/create/`.
  El administrador que cambia el rol necesita `users.assign_access_roles`
  o ser superusuario. El rol por sí solo no concede gestión de cursos ajenos.
- Quien crea un curso queda como profesor de ese curso. Profesor, asistente y estudiante son membresías propias de cada curso; no se deducen de `User.role` ni de `is_staff`.
- Al crear un aula, el slug se completa mientras se escribe el nombre del curso, en minúsculas, sin tildes y con guiones entre palabras (por ejemplo, «Introducción a Python» → `introduccion-a-python`). Puede editarse manualmente. Si se deja vacío, el servidor lo genera; al editar un curso, se conserva su slug existente.
- El formulario de creación permite «Añadir categoría» sin abandonar el aula ni perder los datos escritos. Se guarda su nombre y descripción, se genera su slug y se selecciona la categoría recién creada. Requiere el mismo permiso `courses.add_curso` que crear aulas; los duplicados se muestran como errores.
- Las aulas sin publicar solo son visibles a quienes pueden editarlas. Las inscripciones se crean por POST con CSRF y únicamente si el curso está publicado y abierto.
- Editar/publicar/inscribirse se vuelve a validar en el servidor; las descripciones se muestran escapadas como texto, no como HTML confiable.

## Rutas disponibles

| Ruta | Uso |
| --- | --- |
| `/lms/` | Campus personal y cursos publicados disponibles |
| `/lms/courses/create/` | Crear aula como borrador, con permiso `lms.add_course` |
| `/lms/courses/<slug>/` | Ver aula y, si corresponde, inscribirse |
| `/lms/courses/<slug>/edit/` | Editar aula como profesor, asistente autorizado o gestor |
| `/lms/courses/<slug>/publish/` | Publicar mediante POST y permiso `lms.publish_course` |
| `/lms/courses/<slug>/enroll/` | Inscripción mediante POST |

## Auditoría y datos

### Migraciones en PostgreSQL

La publicación automática de anuncios al abrir un curso bloquea únicamente
los anuncios programados, sin unir el autor opcional en la consulta
`SELECT FOR UPDATE`. Así se evita el error de PostgreSQL sobre el lado
anulable de un outer join, incluso si el curso todavía no tiene anuncios.
Se conservan las fechas de publicación/caducidad y las notificaciones.
Esta corrección no requiere migraciones.

La reparación histórica de un esquema SQLite parcial en `lms.0004` solo se
ejecuta con el backend SQLite. En PostgreSQL se omite esa reparación y se
aplican normalmente las operaciones de esquema de la migración.
Si una instalación se detuvo con `no existe la relación «sqlite_master»`,
actualiza el código y vuelve a ejecutar `python manage.py migrate`.
No requiere borrar tablas ni marcar migraciones con `--fake`.
El aviso `urls.W002` de la ruta del dashboard es independiente de este error.
Las restricciones `lms_member_valid_role` y `lms_member_valid_status` se crean
una sola vez en `0003_alter_section_options_alter_userprofilelms_options_and_more`.
La rama `0003_membership_constraints_and_profiles` depende de esa migración y
solo completa los perfiles que faltan, conservando las preferencias existentes.
Si PostgreSQL se detuvo con una restricción duplicada en esa rama, actualiza
el código y repite `python manage.py migrate`, sin borrar restricciones ni
usar `--fake`.
Aplicar migraciones crea o actualiza el esquema; no transfiere el contenido
de una base SQLite anterior a PostgreSQL.

### Contenidos, módulos y progresión

La estructura de Contenido es **Curso → Módulo (`Tema`) → Unidad formativa
(`FormativeUnit`) → Recursos (`ModuleContent`)**. Un módulo puede tener varias
unidades y cada unidad puede reunir páginas, ficheros, pruebas y actividades.
Los docentes crean, editan y ordenan las unidades desde el módulo; «Añadir
recursos» abre la selección múltiple en esa unidad. Editar un recurso permite
moverlo a otra unidad del mismo módulo sin recrearlo ni perder su progreso.
La eliminación de una unidad con recursos se rechaza: hay que mover o retirar
sus asociaciones primero. Retirar la asociación conserva el recurso original.
Los alumnos ven los recursos disponibles agrupados por unidad, respetando los
mismos permisos, fechas y prerrequisitos del módulo.

La migración `lms.0035_formative_units` crea una unidad «General» por módulo y
asigna a ella sus recursos ya asociados en `ModuleContent`. Conserva sus
identificadores, criterios, prerrequisitos, notas y progreso. Los recursos aún no
asociados a Contenido se siguen añadiendo desde sus apartados habituales; no se
duplican ni se cambia su publicación. Los clientes antiguos que agreguen un
recurso sin indicar unidad usan «General». No se añade bloqueo secuencial entre
unidades; la progresión continúa siendo por módulo. Revertir la migración
conserva recursos y progreso pero elimina la organización en unidades.

Aplicar en el entorno correspondiente, con copia de seguridad previa:
`python manage.py migrate lms`. Las pruebas usan una base aislada; no se deben
aplicar cambios a producción automáticamente.

Las páginas admiten documentos HTML completos en «Documento HTML con estilos propios». Este campo sustituye al editor visual cuando tiene contenido y conserva `head`, bloques `style`, clases, identificadores y estilos en línea, también en revisiones y restauraciones. Las páginas con estilos propios se muestran en un iframe aislado, tanto en su detalle como en la portada del curso: el CSS del campus no modifica su diseño y el suyo no modifica el campus. No se ejecutan scripts ni formularios; se eliminan atributos de eventos y direcciones JavaScript. El marco dispone de desplazamiento propio. Las páginas de texto enriquecido sin estilos mantienen su presentación habitual. Los estilos eliminados antes de esta mejora deben pegarse de nuevo desde el documento original.

Contenido presenta módulos con cabeceras turquesa desplegables y filas compactas de páginas, ficheros, pruebas y actividades, con iconos de tipo y estados reales. «Mostrar todo» y «Ocultar todo» controlan los desplegables. Para docentes, el botón «+» abre un panel lateral para asociar un recurso existente y definir orden, obligatoriedad, criterio de finalización y un prerrequisito del mismo módulo. El menú del módulo permite editarlo, planificar fechas y duración, gestionar contenidos o borrarlo. Sin JavaScript se conserva el formulario completo.

Cada unidad formativa de Contenido es un acordeón inicialmente cerrado,
operable con ratón o teclado sin JavaScript. Al abrirlo muestra su descripción,
recursos y acciones autorizadas. «Mostrar todo» y «Ocultar todo» también
despliegan o pliegan las unidades, además de los módulos.

Al añadir contenido se pueden marcar varios recursos de cada tipo, incluso combinar páginas, ficheros, pruebas y actividades en un mismo guardado. Se aplican las condiciones comunes a todos y el orden indicado aumenta consecutivamente. La selección se valida completa antes de guardarse en una única transacción; si hay errores, se muestran y se conservan las selecciones sin añadir un lote parcial. Un criterio específico (como finalizar una prueba) exige seleccionar únicamente tipos compatibles. El orden vacío utiliza cero y el porcentaje vacío utiliza 50; este último solo se aplica a actividades con criterio de aprobado. Las condiciones se pueden editar después individualmente.

Al abrir un fichero desde Contenido se usa la vista previa cuando está permitida; si no, se usa la descarga autorizada. Si ambas están desactivadas, la fila explica el bloqueo y no ofrece un enlace. Se mantienen los permisos y prerrequisitos del módulo.

Cada recurso agregado pertenece a un único módulo. No se permiten prerrequisitos entre módulos ni ciclos. El docente puede exigir apertura o finalización manual; las actividades también permiten entrega o aprobado por porcentaje, y las pruebas permiten finalizar o aprobar según su nota mínima. La finalización manual requiere abrir el recurso antes. Las notas de actividades deben estar publicadas para desbloquear contenidos por aprobado.

La progresión secuencial se activa en Configuraciones y está desactivada por defecto para conservar los cursos existentes. Al activarla, deben completarse los contenidos obligatorios de los módulos anteriores y respetarse la apertura y cierre exactos del módulo. Los accesos directos y las entregas también comprueban los bloqueos. La duración estimada en minutos es informativa, no un contador obligatorio. Los módulos sin contenidos asociados conservan su criterio de finalización del tema existente.

### Calendario de fechas

El calendario mensual de la portada del curso (también visible en Anuncios) marca con un círculo los días con fechas o actividades de ese curso. Agrupa todos los eventos del mismo día en una única marca, muestra sus títulos al pasar el cursor y enlaza al primer evento en el calendario completo. Incluye inicio y fin del curso, módulos, pruebas, actividades, páginas y eventos del curso visibles para el usuario; los borradores se muestran solo a docentes. Cada fecha se asigna al día según su zona horaria. El día actual conserva su indicador aunque coincida con una fecha marcada.

El calendario reúne inicio y fin de cursos, apertura y cierre de módulos, inicio/cierre/entrega de pruebas, publicación de notas y soluciones, inicio/entrega/feedback/solución de actividades, publicación/caducidad de páginas, evaluaciones existentes y eventos personales. Se calcula a partir de las fechas actuales, sin duplicar registros ni conservar fechas antiguas tras editarlas. Los cursos sin hora exacta aparecen como eventos de todo el día; las horas se muestran con su zona horaria. Se respetan la matrícula, la publicación y las secciones de las pruebas; los docentes también ven sus borradores. Esta agenda no añade correos ni recordatorios externos.

El procesamiento de pruebas bloquea los intentos sin `DISTINCT`, compatible
con PostgreSQL. Las respuestas pendientes de revisión manual se excluyen con
una subconsulta `EXISTS`, que no duplica los intentos seleccionados.

El detalle de cada prueba utiliza Tech Modern (`.quiz-detail-tech`): cabecera
azul con estados, resumen de referencia y fechas, información en acordeones,
datos en cuadrícula y registro de intentos con desplazamiento horizontal.
Admite temas claro/oscuro y pantallas móviles sin cambiar las acciones,
permisos ni la actualización en vivo.

Cuando una prueba no tiene fecha de publicación de notas, el profesor puede
usar «Enviar resultado y solución» en el registro de cada intento calificado.
La acción publica la nota ya calculada en el historial y en Notas, habilita
la solución solo para ese alumno y le envía una notificación. Se registra
quién la publicó y cuándo; repetir el envío no duplica la notificación.
No modifica las fechas de la prueba ni publica los resultados del resto.
«Cambiar mínimo de aprobado» permite al docente ajustar el umbral incluso
con intentos existentes o la prueba cerrada. «Guardar y actualizar resultados»
recalcula Aprobado / No aprobado de todos los intentos calificados sin cambiar
sus puntuaciones, respuestas ni publicación. Los intentos pendientes y futuros
utilizan el nuevo mínimo. El cambio queda registrado en auditoría; la edición
de preguntas y del resto de configuración continúa bloqueada tras un intento.
No permite enviar intentos sin calificar, pendientes de revisión manual,
notas ocultas ni resultados de alumnos fuera del ámbito docente autorizado.

El detalle de actividades utiliza `.activity-detail-tech` con cabecera azul,
estados y referencia, tarjetas de instrucciones y entregas, adjuntos y formulario
adaptables a móvil y a los temas claro/oscuro. Conserva los plazos, la privacidad
del historial y las fechas de publicación de soluciones y evaluaciones.

El repositorio de archivos del LMS y los adjuntos de entregas admiten audio
`.m4a`, también con la extensión en mayúsculas, además de `.mp3` y `.wav`.
Se mantienen los permisos de acceso privado y los límites de carga existentes.

### Insignias en Competencias

Los docentes pueden crear y editar insignias del curso desde Competencias, asociarlas a un tema y subir su imagen (PNG, JPEG, GIF o WebP, hasta 5 MB). La imagen y el tema son obligatorios. Se reutilizan `Badge` y `UserBadge`; las insignias de tema no se conceden por las reglas de finalización general del curso.

El alumno recibe automáticamente la insignia activa al completar todos los contenidos obligatorios del tema según los criterios definidos; para temas sin contenidos asociados se utiliza la finalización de `Progreso`. Se comprueba al abrir o completar contenido, finalizar pruebas, entregar o calificar actividades y completar temas antiguos. Competencias reconcilia además los logros al consultarse, incluyendo notas cuya publicación programada ya haya llegado. Crear una insignia también reconoce alumnos que ya habían completado el tema. No se conceden insignias a docentes ni a usuarios no matriculados.

Competencias es visible para alumnos, pero solo los docentes pueden modificar insignias. El alumno ve sus logros y las insignias pendientes; el docente ve las concesiones. Desactivar una insignia impide nuevas concesiones y conserva las obtenidas. No se cambia el tema de una insignia ya concedida: se debe crear otra. Las revocaciones existentes se respetan y no se vuelven a conceder automáticamente.

### Configuraciones del curso

Configuraciones muestra un formulario compacto de detalles de la asignatura, con imagen, nombre, código, categoría, estado, zona horaria, fechas, idioma, visibilidad y descripción. Las opciones de matriculación, capacidad, finalización y SEO se editan en el mismo formulario; el botón inferior guarda todos los campos mediante POST con CSRF y registra el cambio en la auditoría existente. Las fechas inválidas muestran errores sin guardar cambios. Solo pueden acceder quienes tienen permiso de edición del curso; publicar o programar conserva su permiso adicional.

La pestaña Secciones consulta las secciones existentes del curso y enlaza a sus participantes. El lateral muestra recuentos reales de estudiantes y profesores y accesos a la vista previa, calendario, notas y organización/historial. No se muestran opciones ficticias de Commons, integraciones, cuotas de almacenamiento o importación/exportación del curso.

### Libro de notas

Notas muestra una matriz de alumnos en filas y pruebas en columnas, con cabecera y nombres fijos al desplazarse. El docente también ve los ítems activos no asociados a pruebas del libro de notas. Se puede filtrar por nombre de alumno y actividad, y exportar la vista filtrada a CSV. La matriz muestra el último intento, no calcula una nota final ni sustituye el historial de intentos y feedback situado debajo. Un guion significa que no hay nota disponible. El alumno solo accede a sus resultados autorizados y publicados según las fechas y permisos existentes; los ítems adicionales del docente no se exponen al alumno. No se incluye importación de notas en esta pantalla.

La portada presenta hasta tres anuncios publicados y visibles para el usuario, la bienvenida desplegada, la portada del curso si existe y una tabla de temas y próximas entregas. La columna lateral incluye acciones autorizadas y el calendario del mes actual. El recorrido de actividades sigue disponible en un desplegable. Los enlaces de anuncios recientes abren el anuncio correspondiente en la lista compacta de Anuncios; consultar la portada no los marca como leídos. Se conservan los comentarios, acuses, contadores y permisos de cada anuncio.

Los profesores con permiso de edición del curso pueden borrar páginas desde su listado o detalle, y temas desde el área Contenido. El borrado requiere confirmación y POST con CSRF; el servidor verifica los permisos y que el objeto pertenezca al curso. Al borrar un tema se eliminan sus recursos y progreso asociados; las páginas vinculadas se conservan sin módulo.

El contador de anuncios del alumno cuenta anuncios existentes y visibles con notificaciones sin leer, sin duplicar un anuncio por varias notificaciones. Las notificaciones históricas de anuncios eliminados u ocultos no incrementan la insignia. Al abrir Anuncios, cada aviso pendiente muestra «Pendiente de leer» en su cabecera; si exige acuse y todavía no se ha confirmado, muestra también «Pendiente de confirmar». Abrir el listado no cambia el estado de lectura. Desplegar un anuncio lo marca como leído mediante POST con CSRF, únicamente para ese alumno: desaparece «Pendiente de leer» y se actualiza el contador sin recargar. Si no quedan anuncios pendientes, desaparecen la insignia y el aviso. La confirmación formal de lectura sigue siendo independiente; si falla el guardado de lectura se informa en el anuncio y no se ocultan sus pendientes.

Publicar un anuncio nuevo o pasar un borrador a publicado genera las notificaciones del curso automáticamente, incluyendo alumnos con matrícula antigua sin membresía LMS. La portada del alumno muestra un enlace con el número de anuncios pendientes. Las insignias se actualizan directamente por WebSocket al publicar, sin recargar; la consulta cada 5 segundos queda como respaldo mientras la pestaña está visible. Los borradores no incrementan el contador.

El badge de Anuncios del profesor cuenta alumnos distintos que tienen algún anuncio visible pendiente de leer, no el número de anuncios ni sus avisos personales. Cada anuncio publicado muestra además alumnos pendientes y el resumen de lecturas. No se cuentan docentes, alumnos suspendidos ni anuncios caducados; se respetan los destinatarios del anuncio. La lectura desde el anuncio o el centro actualiza también el seguimiento del profesor. El acuse formal sigue siendo una operación diferente.

### Centro unificado de notificaciones

Anuncios, estados de actividades y pruebas, notas, feedback, foro y mensajes de Personas comparten el centro de notificaciones de la plataforma. Cada notificación LMS tiene una única entrada enlazada en la plataforma: el aviso a pie de página, la campana general y los badges por sección consultan los mismos datos. La migración `users.0010` enlaza avisos antiguos cuando existe una coincidencia única de destinatario, título, mensaje y fecha próxima; los registros ambiguos se conservan sin fusionar para evitar pérdida de datos.

Abrir una sección no marca todo como leído. Desplegar un anuncio, una nota de una prueba o un mensaje de Personas marca únicamente sus avisos; abrir el detalle de una actividad o prueba marca los avisos de ese elemento mediante POST con CSRF. La lectura del foro respeta su regla de participación previa. Marcar leído o pendiente desde el centro también actualiza los contadores del curso. El acuse formal de un anuncio permanece separado.

Los avisos privados solo aparecen al destinatario autorizado; no se cuentan anuncios expirados, contenidos eliminados ni pruebas de otra sección. Los nuevos avisos aparecen a pie de página con enlaces al elemento y controles para marcar como leído u ocultar sin leer. WebSocket envía el estado y los contadores privados después de confirmar las transacciones, sin otra petición HTTP. Si falla una consulta HTTP se informa y se detienen las consultas, sin reintentos ni reconexiones automáticos; la recepción por WebSocket sigue actualizando los badges. El aviso de error se oculta automáticamente a los 5 segundos, sin reanudar las consultas, y el error permanece registrado en la consola. Mientras está visible, «Volver a conectar» reanuda las consultas manualmente; después de ocultarse, se puede recargar la página para reanudarlas.

El centro utiliza tarjetas agrupadas por fecha, indicadores de pendientes y leídas, iconos por sección y enlaces al elemento. Permite filtrar por lectura y curso, buscar por título o mensaje y paginar conservando los filtros. Sus acciones de lectura son explícitas e idempotentes y sincronizan el LMS; la búsqueda solo utiliza avisos autorizados. Los contadores se actualizan en vivo y los avisos nuevos muestran un enlace para actualizar la lista sin interrumpir la lectura o los filtros. El diseño se adapta a móviles y al tema oscuro.

El navbar del curso se renderiza una sola vez en la plantilla común, incluidos formularios, detalles y evaluaciones. Permanece visible al desplazarse, debajo de la cabecera de la plataforma; en pantallas estrechas dispone de desplazamiento horizontal.

### Eliminación de cursos

Solo un usuario activo con `is_staff=True` puede eliminar un curso. Ser propietario, profesor o tener el permiso de edición/eliminación del modelo no basta. Desde el curso, su edición o el catálogo administrativo se accede a una confirmación que exige escribir el nombre exacto y aceptar el borrado irreversible. GET nunca borra; POST requiere CSRF y vuelve a comprobar el permiso.

El borrado elimina los datos dependientes del curso, conserva las cuentas de los alumnos y registra la acción en auditoría. Si preguntas protegidas del curso se utilizan en otro curso, se bloquea la operación y se revierte completa, sin eliminaciones parciales. Los archivos físicos se conservan conforme al comportamiento de almacenamiento de Django; esta operación no ejecuta limpieza del disco.

La app registra creación, cambios, publicación e inscripción en `AuditEvent`. Los eventos son de solo lectura desde el admin. Las membresías son únicas por usuario y curso, y las secciones pertenecen a un curso. No se almacenan respuestas de evaluación, calificaciones ni entregas en esta primera vertical.

## Validación local

### Actividades: módulos y editor visual

La actividad admite una Referencia opcional (hasta 64 caracteres), sin generación
automática ni obligación de unicidad. Aparece en el listado, detalle y evaluación
cuando está informada. Las actividades existentes conservan la referencia vacía
mediante la migración `0032_learningactivity_reference_code`.

Crear y editar una actividad utiliza los mismos módulos del curso que Contenido y
Páginas. El desplegable solo ofrece módulos de ese curso y permite elegir «Crear
nuevo módulo»: se abre el panel de nombre, descripción y orden, sin abandonar la
actividad ni perder sus datos. Al guardar el módulo se selecciona automáticamente.
La creación exige permisos de edición del curso y CSRF.

Descripción e Instrucciones utilizan el mismo editor visual configurable que el
contenido de las páginas. El formato se conserva al guardar y al mostrar la
actividad, con el saneamiento HTML compartido del LMS.

La migración `0030_learningactivity_course_modules` conserva las actividades:
reutiliza el tema del mismo curso y título cuando existe; en caso contrario crea
un tema con el título, contenido y orden del módulo anterior. Las actividades sin
módulo siguen sin módulo. Aplicarla con `python manage.py migrate`.

La migración `0031_activity_student_workflow` habilita las entregas privadas
mediante los modelos de tareas y entregas existentes. El alumno matriculado
activamente puede entregar texto y/o un archivo una sola vez por actividad.
Tras el envío se oculta el formulario y el servidor rechaza nuevos envíos,
incluso si la tarea tenía habilitadas las reentregas. Las entregas e historiales
existentes se conservan. Repetir una petición con el mismo identificador y
contenido devuelve la entrega original sin duplicarla. No se aceptan entregas en borradores,
antes de la apertura, después del plazo ni en actividades cerradas. Los archivos
se sirven como descarga privada solo al autor y a docentes autorizados.

El profesor consulta las entregas y evalúa la última versión con nota y feedback.
La nota respeta el máximo de puntos y se muestra al alumno desde la fecha de
feedback (inmediatamente si no hay fecha). La solución tiene su propio editor y
solo se muestra al alumno desde la fecha de publicación de solución.

Las tarjetas de actividades y pruebas se actualizan cada 5 segundos sin recargar
la página. Los estados creada, abierta, cerrada, solución disponible y evaluada
generan avisos sin duplicarlos. Los borradores creados se notifican únicamente a
los docentes; las evaluaciones se notifican solo a su alumno. Las entregas en
curso no se reemplazan al actualizar los estados. Para transiciones automáticas
sin navegadores abiertos, ejecutar el comando existente `process_lms_lifecycle`
de forma periódica.

«Nuevo módulo» despliega un panel hacia abajo en el formulario de página y en el bloque Módulos de Contenido, con nombre, descripción y orden. Crear el módulo desde una página no recarga el formulario: se selecciona automáticamente y se conserva el contenido escrito. Cancelar cierra el panel; los errores se muestran dentro de él. La creación sigue requiriendo permisos de edición del curso y CSRF.

En Contenido, el bloque Módulos aparece encima del bloque Páginas. El profesor ve también borradores y controles de gestión; el alumno solo ve páginas publicadas dentro de su período de disponibilidad y no reservadas a profesores.

La sección presenta título y descripción, «Módulo», creación y lista acordeón, y después «Páginas», creación y lista desplegable. Ambas listas tienen fondo gris claro; cada elemento se abre mediante su título y conserva los enlaces y acciones autorizadas.

«Todos los módulos» muestra el número de módulos y permite plegar o desplegar la lista completa, inicialmente cerrada, tanto para profesores como para alumnos. Cada módulo mantiene su propio acordeón dentro de esa lista.

«Todas las páginas» permite plegar y desplegar la lista completa de páginas disponibles, inicialmente cerrada. Cada página conserva su acordeón individual y las acciones autorizadas.

En Foro de discusión, «Crear foro» sustituye a «Ver páginas» para quienes pueden editar el aula. El formulario solicita nombre y descripción y asigna el curso y el autor en el servidor. Los foros creados aparecen en el listado del aula; los alumnos no pueden crearlos ni ver borradores, archivados o foros de secciones ajenas.

«Editar foro» está disponible para el profesor en el listado y dentro del foro. Permite cambiar nombre y descripción sin modificar su autor, curso, estado ni las consultas y respuestas existentes. Cancelar la edición vuelve al foro sin guardar.

El foro utiliza cabecera azul oscuro, acción principal amarilla, tarjetas blancas y panel informativo adaptable a móvil. Cada hilo muestra su contador de respuestas, incluidas las anidadas; no cuenta publicaciones ocultas ni eliminadas. El contador de la conversación respeta la restricción de responder antes de ver las respuestas. Los contadores se actualizan junto con el contenido mediante las actualizaciones en vivo existentes.

En «Personas», el profesor puede consultar nombre, usuario, correo, rol, estado, sección y fecha de inscripción de los participantes del aula. Se incluyen matrículas activas, suspendidas y completadas, además de inscripciones antiguas sin pertenencia LMS, sin duplicar usuarios. Las invitaciones y bajas no se incluyen. Los alumnos con acceso al curso solo reciben nombres y roles, sin correos ni datos de inscripción.

La vista usa una tabla compacta con avatares, búsqueda y filtro por rol. El docente puede consultar las secciones existentes en «Grupos» y los participantes sin grupo. La última actividad corresponde a eventos o progreso del curso, no al último inicio de sesión global. «Actividad total» suma solo las duraciones registradas en eventos de aprendizaje, en formato horas:minutos:segundos; sin registros se muestra un guion. El identificador mostrado es el ID interno, no un SIS. Al filtrar, la selección de alumnos para notificaciones solo abarca los mostrados; sin filtros se conserva el envío a todos.

«Archivos» muestra carpetas lógicas navegables y archivos en una tabla con fechas, propietario, tamaño, visibilidad y menú de acciones. Permite buscar por título o nombre de archivo y ordenar por nombre, creación, modificación o tamaño. La vista raíz muestra todos los archivos disponibles; abrir una carpeta restringe el listado. Las carpetas se crean al indicar su nombre al subir o editar un archivo; no se almacenan carpetas vacías. El tamaño total corresponde a los archivos visibles para ese usuario, sin inventar una cuota de almacenamiento. Descarga, vista previa, edición y retirada conservan sus permisos.

Solo los profesores o propietarios con membresía activa en el curso pueden enviar notificaciones desde «Personas», seleccionando uno o varios alumnos o todos. Se utiliza el centro global de notificaciones de la plataforma y se guarda una copia LMS identificada con origen `people`. El historial de Personas solo muestra esos envíos del curso: el profesor ve los envíos y cada alumno solo los dirigidos a él. No se importan otros avisos globales ni se modifica el funcionamiento de avisos automáticos de foros y anuncios.

Los destinatarios se seleccionan con casillas en sus filas del listado. La casilla de cabecera selecciona o desmarca todos los alumnos y muestra un estado parcial cuando solo hay algunos seleccionados. Los docentes no son destinatarios. Si el formulario tiene errores, conserva la selección.

### Base del conocimiento: primera fase de evaluaciones

Dentro de un banco no archivado, cada pregunta en borrador sin errores pendientes dispone de una casilla. La casilla superior selecciona todas las preguntas seleccionables mostradas por los filtros actuales; «Publicar seleccionadas» publica solo las marcadas, en una transacción y creando versiones nuevas. Las publicadas, archivadas y marcadas «Revisar» no son seleccionables. El servidor valida permisos, CSRF, pertenencia al banco, estado y errores pendientes; cualquier error cancela todo el lote. Publicar preguntas no publica automáticamente el banco: si sigue en borrador, se informa de que debe publicarse desde «Editar banco» para usarlo en Pruebas.

La entrada «Base del conocimiento» conserva la ruta de curso `areas/item-bank/` y está reservada a docentes autorizados. Reutiliza `QuestionBank`, `Question` y `QuestionVersion`. Los errores de archivo se conservan en `QuestionBank.import_errors`; los errores por fila y sus datos originales se conservan en los metadatos de la versión. Permite crear y editar bancos, crear y editar preguntas de cuatro alternativas distintas A–D, duplicarlas como borradores, archivarlas y buscar por texto, estado y dificultad.

El profesor puede importar CSV UTF-8, con BOM opcional y separador `;` o `,`, usando exactamente la cabecera `Pregunta;OpcionA;OpcionB;OpcionC;OpcionD;RespuestaCorrecta`. Límites: 1 MB y 1000 preguntas por archivo. Todos los registros se validan antes de mostrar la vista previa; las filas inválidas se señalan con «Revisar», su número y sus errores, pero no bloquean las demás. La confirmación guarda todas las filas como borradores en una transacción, incluidas las incompletas o con respuestas duplicadas (también si solo cambian mayúsculas/minúsculas). Las preguntas marcadas no pueden publicarse hasta editarlas y guardarlas con datos válidos; la corrección crea una nueva versión y retira la marca sin modificar el historial. El banco muestra el aviso tanto en el listado como en su detalle. La vista previa queda vinculada al banco, usuario y sesión y caduca a los 30 minutos; el archivo original no se almacena. El CSV de ejemplo `static/quiz/0201QZ.csv` tiene 94 preguntas compatibles.

El formulario «Crear banco» incluye un CSV opcional. Los errores del CSV no impiden crear el banco. Si el archivo no se puede leer (extensión, tamaño, codificación, cabecera, sintaxis o límite de filas), se conserva el banco vacío con el aviso persistente «Revisar CSV» y el detalle del error; no se importan fragmentos de un archivo ilegible. Si es legible se abre la vista previa, incluidas las filas con errores; las preguntas solo se guardan tras «Confirmar importación». «Cancelar» descarta la vista previa, pero conserva el banco creado. Una importación confirmada correctamente retira los errores de archivo anteriores, aunque puede dejar preguntas pendientes de revisión. Sin archivo, el banco se crea vacío. Los archivos se siguen adjuntando desde «Importar CSV» en bancos existentes, no al editar su nombre o estado.

Los enunciados y opciones de estas preguntas son texto: ejemplos como `<body>` permanecen visibles sin interpretarse como HTML. Cada edición crea una nueva versión, con autor y fecha; no cambia las versiones referenciadas por cuestionarios anteriores. El exportador CSV previene fórmulas de hoja de cálculo prefijando con apóstrofo las celdas que comienzan por `=`, `+`, `-` o `@`. Los bancos archivados no admiten cambios en sus preguntas hasta desarchivarlos.

No se añaden todavía Excel/JSON. La marca «Revisar» mantiene la pregunta en borrador; no añade un estado nuevo. La inmovilidad de las reglas de corrección por intento debe completarse antes de habilitar exámenes; las versiones históricas por sí solas no congelan todos los campos actuales del modelo `Question`.

### API de preguntas y respuestas de la base de conocimiento

`GET /api/v1/knowledge/questions/` selecciona preguntas existentes al azar, sin
repetir y sin generar contenido. Acepta cuatro parámetros obligatorios:

| Parámetro | Valor |
|---|---|
| `modulo` | ID del módulo de contenidos (`courses.Tema`), no de `CourseModule` |
| `unidad_formativa` | ID de una `FormativeUnit` del módulo indicado |
| `dificultad` | Entero entre 1 y 5, coincidencia exacta |
| `numero_preguntas` | Entero entre 1 y 100 |

Como alternativa a `modulo` y `unidad_formativa`, acepta `categoria` (ID de
`courses.CourseCategory`). `subcategoria` (nombre o ID de `duckyclash.ChallengeSubcategory`)
es opcional: si se omite, incluye las preguntas asociadas a toda la categoría.
No se pueden mezclar los filtros de categoría con módulo/unidad:

```http
GET /api/v1/knowledge/questions/?categoria=12&dificultad=5&numero_preguntas=10
Authorization: Token <token-del-docente>
Accept: application/json
```

Este modo utiliza las asociaciones de bancos y las publicaciones explícitas de
DuckyClash, no todos los cursos que comparten categoría. Una fuente configurada
tiene prioridad sobre las publicaciones individuales, incluso si está desactivada.
La categoría debe estar activa; si se indica subcategoría, debe estar activa y
pertenecer a la categoría; en caso contrario se devuelve 404.
La dificultad es la de la pregunta (1–5), no el nombre libre del
nivel del banco. Se mantiene la exigencia de versión actual publicada, sin revisión
CSV pendiente, y se eliminan duplicados. Quien consulta necesita permiso de edición
en **todos** los cursos de origen de las preguntas candidatas de esa dificultad;
si falta alguno, se devuelve 403 sin soluciones ni selección parcial.

Las preguntas se almacenan en `lms.Question`, agrupadas mediante `Question.bank`
en `lms.QuestionBank`. La dificultad está en `Question.difficulty`; el enunciado,
las opciones, las soluciones y la explicación están en `lms.QuestionVersion`,
seleccionada por `Question.current_version`. La categoría se resuelve mediante
las asociaciones/publicaciones de DuckyClash, no es un campo directo de Question.

El generador puede ejecutarse sin pedir subcategoría:

```powershell
.\scripts\generar_url_banco_preguntas.ps1 -BaseUrl http://127.0.0.1:8000 -Modo Categoria -Categoria 12 -Dificultad 3 -Cantidad 10
```

Para restringirlo, añade `-Subcategoria Python` (también admite `-Subcategoria 34`).
La API acepta `categoria=2&subcategoria=Python&dificultad=3&numero_preguntas=10`.
El nombre se busca exactamente dentro de la categoría, sin distinguir mayúsculas
y recortando espacios exteriores. Si no existe o está inactivo devuelve 404; si
varios nombres solo difieren en mayúsculas, devuelve 400 y pide usar el ID.
El nombre del parámetro de cantidad
en la API sigue siendo `numero_preguntas`, para preservar compatibilidad.

Al ejecutar el generador de forma interactiva en modo Categoría, muestra menús
numerados para categoría y subcategoría (0 = todas). Los nombres e IDs proceden
de las categorías y subcategorías activas de la base configurada en Django,
mediante el Python de `.venv`, sin modificar datos. No se consulta el catálogo
de `BaseUrl`: si apunta a otro portal, comprueba que sus IDs coinciden.
Requiere la conexión de base de datos del proyecto. Los errores de conexión
o un catálogo sin categorías activas detienen la generación explícitamente.
Con `Categoria`, `Dificultad` y `Cantidad` especificados no se abren menús y la
subcategoría sigue siendo opcional. El menú usa IDs para evitar nombres ambiguos.

Ejemplo:

```http
GET /api/v1/knowledge/questions/?modulo=12&unidad_formativa=34&dificultad=3&numero_preguntas=10
Authorization: Token <token-del-docente>
Accept: application/json
```

También admite la sesión Django existente. Solo pueden consultar soluciones los
administradores y docentes autorizados para editar ese curso, según la política
central de acceso. Un token no concede permisos adicionales. No es una API para
alumnos ni un endpoint público de soluciones. Las respuestas, incluidos errores,
llevan `Cache-Control: private, no-store, no-cache, max-age=0`.

El resultado contiene `filtros`, `disponibles`, `devueltas` y `preguntas`. Cada
pregunta incluye `id`, `banco`, `modulo`, `unidad_formativa`, `dificultad`, `tipo`,
`puntos`, `version_id`, `version`, `enunciado`, `formato_enunciado`, `opciones`,
`respuestas_correctas`, `explicacion` y `tolerancia`. Se entrega exclusivamente la
versión actual publicada de cada pregunta publicada en un banco publicado del
curso. Se excluyen preguntas sin versión, versiones de otra pregunta, contenido
archivado o borrador y preguntas con marca de revisión CSV pendiente.

`formato_enunciado` distingue `text` de `html`; los enunciados de texto recuperan
sus caracteres originales (por ejemplo, `<html>`). Las opciones y soluciones
conservan el formato JSON de cada tipo: listas para elección/texto/números y mapa
para emparejamientos. Las preguntas de desarrollo pueden carecer de solución
automática. Un cliente debe tratar los campos de texto como texto, no insertarlos
como HTML sin sanitizar.

Si hay menos preguntas que las solicitadas, devuelve **409**, sin selección
parcial:

```json
{
  "code": "insufficient_questions",
  "detail": "No hay suficientes preguntas publicadas para los filtros solicitados.",
  "solicitadas": 10,
  "disponibles": 4
}
```

Parámetros ausentes o fuera de rango devuelven **400**; falta de autenticación o
permisos, **403**; módulo/unidad inexistente o unidad ajena al módulo, **404**.
El contrato está incluido en `/api/v1/schema/` y `/api/v1/docs/`.

La migración `lms.0037_question_formative_unit_difficulty` añade la unidad
opcional y restringe la dificultad a 1–5. No atribuye unidades a preguntas
históricas: deben clasificarse desde «Editar pregunta», mediante el selector
«Módulo / unidad formativa». Al elegir unidad se asigna su módulo; no se permiten
unidades de otro curso. Los niveles 1–3 mantienen sus etiquetas y se añaden
4 (Experta) y 5 (Muy experta). Duplicar o publicar preguntas conserva la
clasificación; las versiones guardan también los IDs de módulo y unidad en sus
metadatos. El CSV existente conserva su formato y no asigna unidades
automáticamente.

### Pruebas: configuración y publicación docente

El listado de Pruebas y su detalle ofrecen «Editar prueba» para borradores y pruebas publicadas sin intentos, y «Eliminar prueba» para docentes autorizados. Al editar una publicada, «Guardar cambios» conserva su estado publicado; no es necesario publicarla de nuevo. Las programadas requieren cancelar su programación antes de editar y las cerradas no se editan. Cualquier intento, incluso iniciado o borrador, bloquea la edición; se comprueba de nuevo al guardar bajo bloqueo transaccional. Eliminar requiere una pantalla de confirmación y POST con CSRF. Se bloquea si existe cualquier intento para preservar el historial académico. Las preguntas y sus versiones en los bancos se conservan; la eliminación queda registrada en auditoría.

«Descontar por fallo» activa el campo «Cantidad a descontar por fallo (sobre 10)», obligatorio cuando está marcado. Admite valores mayores que 0 y hasta 10, con cuatro decimales. El importe se conserva en la configuración de las preguntas de la prueba; cada error resta esa cantidad. Si se desmarca, los fallos no penalizan. El descuento por blancas se configura por separado. Esto reemplaza el cálculo automático de un tercio para las nuevas configuraciones; las configuraciones anteriores conservan la regla que tenían.

Desde «Pruebas» se ofrece «Crear prueba objetiva»; las prácticas se crean desde «Actividades». La práctica reutiliza las actividades evaluables existentes: entrega de texto (incluidos enlaces escritos en la respuesta) o archivo, fechas, corrección manual y publicación del feedback. También aparece en Pruebas, sin duplicar entregas. Las prácticas evaluables se incluyen en el libro de notas y la exportación CSV; el alumno solo ve su última entrega y su nota cuando el feedback está publicado y ha llegado su fecha. El docente ve las notas pendientes de publicación.

La prueba objetiva conserva selección manual o aleatoria del banco, cantidad de preguntas y descuento por fallo. Añade un descuento independiente por pregunta en blanco de 0 a 10, con cuatro decimales y valor inicial 0. Incluye tanto respuestas explícitamente vacías como preguntas sin respuesta guardada. Ambos descuentos se fijan en la configuración del intento; la nota final continúa limitada a 0–10. Los tests anteriores sin descuento por blancas siguen sin penalizarlas.

La información de la prueba y la lista de preguntas docentes están plegadas en acordeones; cada pregunta también puede desplegarse individualmente. Los nuevos borradores guardados usan nota sobre 10, con valor uniforme `10 / número de preguntas`. La nota mínima se introduce entre 0 y 10. El profesor puede activar penalización e indicar el descuento fijo por fallo; dejar en blanco no penaliza por defecto. La corrección suma con precisión decimal y redondea la nota final a dos decimales, limitada a 0–10. Las pruebas antiguas sin estos metadatos mantienen su corrección anterior; no se recalifican historiales automáticamente.

Las pruebas LMS publicadas aparecen en «Próximas entregas» del panel del alumno, en su calendario y en la portada del curso desde su apertura y hasta el cierre/entrega. Se respetan la matrícula activa, el aula/clase y las inscripciones antiguas sin membresía LMS. No se publican automáticamente los borradores al llegar la fecha. La visibilidad se recalcula al cargar estas pantallas; no implica que esté habilitada la realización de intentos.

Si falla el guardado, el formulario muestra un resumen «La prueba no se ha guardado» con los errores y enlaces a los campos afectados, conservando los datos. Si no hay preguntas publicadas disponibles, explica que los bancos y preguntas deben publicarse y que la importación CSV crea borradores.

El formulario de creación y edición de pruebas permite elegir un único banco publicado por prueba, sin listados ni botones «Editar banco». Tanto la selección manual como la aleatoria utilizan exclusivamente preguntas de ese banco; el servidor rechaza preguntas de otro banco. Los bancos y sus preguntas se gestionan desde Base del conocimiento. Publicar sus preguntas no publica automáticamente el banco.

Aula/clase es opcional: representa un grupo organizado por el profesor, no un plan comercial. Un alumno puede estar matriculado sin pertenecer a un grupo. Las pruebas sin aula/clase se dirigen a todo el curso; asignar un grupo restringe su acceso a los miembros de ese grupo. Esta opción no implementa ni modifica las reglas de membresía de pago.

Cuando se completan «Disponible desde» y «Fecha de entrega», la duración en minutos se calcula automáticamente entre ambas fechas, tanto en el formulario como en el servidor al guardar. Los minutos parciales se redondean hacia arriba. La entrega debe ser posterior a la apertura; sin ambas fechas se conserva la duración manual (0 significa sin límite). Este intervalo no sustituye la futura validación del vencimiento de cada intento en el servidor.

El formulario de creación y edición de borradores incluye «Añadir aula/clase» junto al selector «Aula/clase». Escribir un nombre dejando el selector vacío crea un grupo del curso y lo asigna a la prueba al guardar. Internamente sigue utilizando el modelo `Section`, sin cambios en los datos o permisos. No permite nombres duplicados dentro del mismo curso ni seleccionar y crear simultáneamente. Si la prueba contiene errores o falla su guardado, tampoco se guarda el aula/clase; los datos del formulario se conservan al mostrar errores.

Los docentes autorizados para editar el curso pueden eliminar bancos desde el listado o su detalle. «Eliminar banco» abre una pantalla de confirmación y «Cancelar» vuelve sin cambios. La eliminación exige POST con CSRF y borra también preguntas y versiones no utilizadas; queda registrada en auditoría. Si una versión, incluso antigua, está vinculada a una prueba o registro protegido, se bloquea la operación completa sin borrar parcialmente el banco. En ese caso debe archivarse desde «Editar banco». No se elimina ningún cuestionario ni historial académico.

Desde Pruebas, el docente autorizado puede crear un cuestionario como borrador, editarlo, revisarlo, publicarlo, programarlo y cerrarlo. Reutiliza `Quiz` y `QuizQuestion`. Se configuran instrucciones, aula/clase opcional del curso, apertura, cierre, entrega, duración, intentos, reanudación, orden aleatorio y fechas de publicación de respuestas/notas. Las nuevas configuraciones se califican sobre 10; las antiguas conservan su escala original.

### Automatización de cursos y pruebas

- Un curso con estado **Programado** necesita `fecha_publicacion`. Una prueba se activa con **Programar prueba**, después de validar las preguntas y una apertura futura. Los borradores no se publican automáticamente. Cancelar la programación de una prueba la devuelve a borrador.
- Al alcanzar la apertura, se publica y se notifica a los alumnos inscritos. Las pruebas de un aula/clase notifican solo a ese grupo. El cierre se produce en la primera fecha entre cierre, entrega y fin del curso.
- Los cursos se archivan al alcanzar el fin exacto configurado en LMS; si solo hay `fecha_fin`, se cierran al empezar el día siguiente en `zona_horaria` (el último día sigue disponible). Cerrar un curso no aprueba a los alumnos ni elimina sus historiales.
- Los intentos en curso se corrigen al vencer su tiempo o cerrarse la prueba; los enviados también se corrigen. Los casos pendientes de revisión manual no se aprueban automáticamente.
- Las respuestas se habilitan en su fecha configurada. Solo las consultan alumnos con un intento calificado, dentro del aula/clase correspondiente. Las notas se publican en su propia fecha y aparecen en **Notas** y en el detalle. Sin fecha configurada no se liberan automáticamente.
- Cada alumno completa el curso al completar todos los módulos LMS publicados y temas existentes, y aprobar todas las pruebas publicadas/cerradas que le correspondan. Un curso vacío nunca se completa por defecto. Se sincroniza la matrícula LMS con la inscripción anterior, sin cambiar suspensiones ni bajas; las certificaciones mantienen su política previa.
- Las notificaciones se guardan en el LMS y en la campana global, sin duplicados por ejecución. Las notas se notifican únicamente al alumno destinatario. La campana y los avisos se actualizan sin recargar mediante consulta autenticada cada 5 segundos. Esto es actualización casi en tiempo real, no una garantía de latencia cero; no envía correo ni push externos.

**Operación:** aplicar las migraciones con `manage.py migrate` y reconstruir los recursos con `manage.py collectstatic --noinput`. Mantener **un** planificador activo incluso sin visitas al sitio: Celery worker + beat usando la entrada `lms-lifecycle` cada 5 segundos, o el comando `manage.py process_lms_lifecycle --watch --interval 5` como servicio supervisado. No ejecutar ambos planificadores a la vez. El comando sin `--watch` procesa una pasada; errores de configuración detienen la ejecución y se muestran al operador, no se ocultan. Una interrupción del proceso retrasa eventos hasta la siguiente ejecución, conservando fechas y deduplicación en la base de datos.

Las preguntas se eligen manualmente o mediante una selección aleatoria de un banco publicado, con filtro de dificultad. Solo se admiten preguntas publicadas de selección única A–D. Si faltan preguntas, el guardado se rechaza completo con un mensaje, sin reducir silenciosamente el número solicitado. La selección aleatoria se fija al guardar; todavía no se generan selecciones ni opciones distintas por alumno.

Cada pregunta seleccionada conserva su versión concreta, puntos y metadatos de tipo, dificultad y penalización. Los borradores con intentos, las pruebas publicadas y las cerradas no pueden editarse desde esta interfaz. Publicar requiere preguntas válidas y puntuación coherente; las transiciones admitidas son borrador → publicado → cerrado. Las operaciones usan POST, CSRF, permisos del curso y auditoría. No hay borrado ni reapertura que elimine historial.

El alumno accede a las pruebas publicadas/cerradas de su curso y aula/clase, sin controles docentes. Puede comenzar una prueba publicada de opción única sobre 10 cuando está abierta y tiene intentos disponibles. El inicio es POST con CSRF y no se genera al visitar la ficha. El contador combina duración, entrega, cierre y fin del curso; la hora del servidor es la autoridad.

Las opciones se muestran sin soluciones. Cada cambio se guarda progresivamente; también hay botones **Guardar respuestas** y **Entregar prueba**, utilizables sin JavaScript. La entrega es atómica e idempotente: guarda opciones válidas, fija la fecha/hora de entrega, corrige y registra aciertos, fallos, preguntas en blanco y nota. Las preguntas omitidas cuentan en blanco y no penalizan. Las respuestas fuera de plazo no se aceptan; se corrigen las ya guardadas. El planificador y la vuelta al intento también finalizan intentos vencidos. Se respeta el máximo de intentos, se conserva el orden aleatorio por intento y no se modifica una entrega cerrada.

El profesor ve el **Registro de intentos** con alumno, fecha/hora de inicio y entrega (con segundos), estado, aciertos/fallos/blancos y nota. El alumno ve solo su historial: la nota y los recuentos se ocultan hasta la publicación configurada; las soluciones siguen su propia fecha. Los recuentos antiguos sin calcular se muestran como «—», no como ceros ficticios. El listado y detalle docentes muestran **Realizándose (abierta)**, **Cerrada**, **Solución publicada** y **Notas publicadas**, pudiendo combinar cierre con solución/notas. Una prueba publicada antes de su apertura muestra **Pendiente de apertura**.

Las evaluaciones anteriores permanecen en el apartado. La interfaz de realización se limita a las nuevas configuraciones de opción única sobre 10; pruebas antiguas incompatibles necesitan reconfiguración en un borrador. La asociación a módulos/lecciones y modalidades PEC/exámenes siguen pendientes.

En la vista del alumno, el listado de Pruebas y la ficha también muestran el estado junto a cada prueba: pendiente de apertura, abierta o cerrada, y solución publicada cuando corresponde. **Notas publicadas** aparece únicamente cuando el alumno tiene una calificación propia publicada y no oculta, nunca por las notas de otros estudiantes.

El profesor puede **Reabrir prueba** desde el listado o detalle de una prueba cerrada, indicando la fecha/hora de fin del periodo de gracia. Se abre inmediatamente y el cierre/entrega quedan fijados a ese fin, sin superar el fin del curso ni reabrir cursos archivados. Se conservan preguntas, duración por intento, máximo de intentos, notas y entregas previas. Antes de ampliar el plazo se finalizan los intentos pendientes con sus respuestas guardadas; solo alumnos con intentos disponibles pueden comenzar otro. La reapertura se audita y notifica a los alumnos del curso/aula; el planificador vuelve a cerrar y notificar al terminar la gracia. Las soluciones y notas ya publicadas siguen visibles y sus fechas no se modifican; la pantalla de confirmación advierte de ello. El planificador sigue requiriendo activación para procesar plazos sin visitas.

El formulario de creación exige **Banco de preguntas** y **Número total de preguntas** en ambas modalidades. Estos campos siempre están visibles. El bloque de preguntas no se despliega inicialmente: con **Manual** se abre el acordeón y se muestran solo las preguntas publicadas del banco elegido; deben marcarse exactamente tantas como el total indicado. Al cambiar de banco se desmarcan las preguntas ajenas. Con **Aleatoria del banco** se ocultan/deshabilitan las casillas y aparece el filtro de dificultad. El servidor comprueba banco, cantidad y pertenencia incluso sin JavaScript. Los errores de validación mantienen visible el bloque correspondiente; al editar se recuperan banco y total de la selección existente.

El listado y detalle de Pruebas se actualizan sin recargar cada **5 segundos**, tanto para profesores como para alumnos. La web solicita primero un POST autenticado con CSRF para procesar los plazos del curso y después recupera su propia vista, manteniendo los permisos y la privacidad de notas. Se actualizan estados, botones, nuevas pruebas abiertas, soluciones e historial; se conservan los acordeones abiertos y no se reemplaza un control mientras tiene el foco. Los fallos de conexión se muestran y se reintentan; las pestañas ocultas se pausan hasta volver a estar visibles. No se activa ningún planificador externo: sin páginas de Pruebas o Notas visibles ni un planificador independiente, los cambios pendientes se procesan al volver a entrar. Las fechas del servidor son la autoridad.

Ambas vistas muestran las cinco fechas programadas con hora y segundos: **Inicio/apertura**, **Cierre**, **Entrega**, **Publicación de soluciones** y **Publicación de notas**. Las fechas se muestran en la zona horaria activa; si no hay una fecha configurada se indica **Sin fecha programada**. Son fechas de la prueba, no los tiempos individuales de cada intento (estos siguen en el registro de intentos).

En el apartado **Notas**, tanto profesores como alumnos ven el contenido a todo el ancho disponible, sin el lateral **Próximas entregas**. La navegación del curso se conserva y los otros apartados mantienen su lateral.

Notas no muestra **Ver páginas**. Los resultados se agrupan en acordeones por prueba (nombre y referencia), con todos sus intentos, fechas, recuentos y calificaciones. El profesor ve los alumnos asignados, incluidos los que no la han realizado; cada alumno ve únicamente su información y las notas/recuentos ya publicados. Antes del cierre, la ausencia de intentos se muestra como **Pendiente de realización**; después, como **No realizada**, sin inventar una nota de cero.

Al publicarse las notas se envía una notificación privada por calificación. Una vez cerrada la prueba y alcanzada la fecha de publicación de notas, los alumnos asignados que no la realizaron reciben **Prueba no realizada**, sin duplicados en sucesivas comprobaciones. Se respetan los grupos y las matrículas activas/completadas. El sondeo web de cinco segundos también funciona en Notas; sin una página de Pruebas o Notas visible, el procesamiento espera a la siguiente visita.

El profesor puede **Guardar y enviar feedback** a cada alumno asignado en las pruebas publicadas o cerradas, incluso sin intento. Cada comentario queda conservado con autor, fecha y prueba en el historial de notificaciones y se muestra privadamente en Notas al profesor y al destinatario. Los comentarios se muestran como texto, no HTML ejecutable. El sondeo no reemplaza formularios con feedback sin enviar.

El listado de Pruebas y su ficha dejan visibles nombre, **Código de referencia**, inicio/apertura, fin efectivo y estado. El resto de fechas e información se conserva en un acordeón inicialmente plegado. El fin efectivo es el primero entre cierre, entrega y fin del curso. La referencia es obligatoria en el formulario y única en toda la plataforma. Las pruebas preexistentes reciben `QZ-` seguido de su identificador con seis cifras durante la migración. Las creaciones programáticas antiguas que omiten el campo reciben una referencia UUID para conservar compatibilidad; el formulario no la rellena automáticamente.

Tras la publicación de soluciones aparece el icono **Soluciones** en listado y detalle. Abre el acordeón de preguntas y respuestas correctas; el profesor ve el solucionario y el alumno solo accede si tiene un intento corregido propio. Se respetan las versiones fijadas en la prueba y no se muestran soluciones antes de su publicación. El icono aparece también mediante actualización automática.

El icono **Estadísticas** abre el registro de intentos existente, inicialmente plegado, sin cálculos adicionales: el profesor ve todos los intentos y el alumno solo los suyos con resultados sujetos a publicación. Aparece cuando existe historial accesible. El botón **Editar aula** no se muestra en el encabezado de Pruebas.

Todos los bloques plegables del LMS muestran una flecha a la derecha: hacia abajo al estar cerrados y hacia arriba al estar abiertos. Se conserva el control nativo con teclado y se evita duplicar flechas existentes; la animación respeta la preferencia de movimiento reducido.

La página de portada del curso aparece plegada por defecto para profesores y alumnos, tanto si es una página personalizada como la bienvenida automática. Su título queda visible; al desplegarla se muestran el contenido y sus botones.

### Consultas y respuestas del foro

Los participantes inscritos pueden entrar al foro y usar «Abrir hilo / Nueva consulta» para publicar un título y mensaje. Las consultas se ordenan de más reciente a más antigua; las respuestas aparecen cronológicamente, con respuestas anidadas hasta cinco niveles, autor y fecha/hora. Se admite HTML saneado y código con `<pre><code>`; adjuntos, menciones, buscador y push quedan fuera de esta primera versión.

Los formularios del foro muestran «Cancelar» junto a publicar o guardar. Cancelar una consulta vuelve al listado de foros; cancelar una respuesta vuelve al foro y cancelar una respuesta anidada o una edición vuelve a la consulta, sin guardar el texto.

El autor y los profesores pueden editar y eliminar publicaciones. La eliminación es lógica; una respuesta borrada deja un marcador para conservar el contexto de sus respuestas. Solo el autor del hilo y los profesores con permiso de edición pueden aceptar una respuesta como solución o retirarla. Aceptar una solución cierra el hilo a nuevas respuestas; retirar la solución lo reabre. Editar o eliminar la respuesta aceptada devuelve la consulta al estado sin resolver. Los foros cerrados, bloqueados o sin respuestas habilitadas no admiten publicaciones; los alumnos suspendidos tampoco pueden publicar.

Cada consulta, respuesta y solución crea avisos persistentes deduplicados para los participantes activos del curso con acceso al foro, excluyendo al autor de la acción. La campana «Avisos del foro» está disponible en las pantallas del aula y enlaza a la conversación. Abrir una consulta marca sus avisos como leídos; las actualizaciones automáticas no marcan avisos nuevos como leídos sin la acción del usuario. Para los foros que exigen responder antes de ver otras respuestas, se preserva esa restricción también en los fragmentos y resúmenes de notificación.

Los eventos WebSocket se envían solo después de confirmar la transacción y requieren matrícula o permiso docente del curso, comprobado tanto al conectar como al recibir eventos. Las consultas, respuestas y la campana se actualizan sin recargar. Si se está escribiendo dentro de una respuesta anidada, aparece un botón de novedades en lugar de descartar el texto; el formulario principal permanece fuera del área actualizada.

Se reutiliza Django Channels con el backend configurado en `CHANNEL_LAYERS`. El backend en memoria de desarrollo distribuye eventos en un único proceso; para despliegues con varios procesos debe usarse un backend compartido como Redis. Un sondeo cada cinco segundos mantiene las actualizaciones si WebSockets no están disponibles, con aviso de error explícito si falla también la consulta HTTP.

La migración `0026_discussion_queries` añade título y solución aceptada a las publicaciones existentes y reúne las dos ramas previas de migración de LMS sin cambiar ni borrar sus operaciones.

Los anuncios se muestran como acordeones inicialmente plegados. La cabecera muestra título, autor y fecha y hora de publicación (o creación si aún no tiene fecha de publicación), también estando plegados. Su título abre contenido, comentarios y confirmaciones de lectura sin marcar automáticamente el aviso como leído.

Los anuncios programados se publican y generan su notificación al consultar los anuncios o el contador del curso una vez alcanzada la fecha y hora, sin duplicados. El sondeo del badge del alumno (cada 15 segundos mientras el aula está abierta) activa esta comprobación; no necesita recargar la página. Los anuncios futuros o ya caducados no generan ese aviso. Sin visitas al aula, se procesan en la siguiente consulta.

Al alcanzar la fecha de caducidad, el anuncio se oculta al alumno y sale de su contador de pendientes. Se conserva para el profesor con estado «Caducado» y sus acciones de edición y borrado, sin eliminar comentarios ni confirmaciones de lectura.

Las páginas y anuncios usan `django-prose-editor` tanto en los formularios del aula como en Django Admin, con recursos locales, barra de formato, tablas y edición del código HTML. No se muestran los botones externos de imagen ni de adjunto. El HTML sigue pasando por el saneamiento existente del modelo al guardar; el editor solo conserva las etiquetas admitidas por su configuración. La validación al perder el foco también funciona en el editor visual.

Los botones de iconos del editor conservan la fuente local Material Icons; el tema del LMS no les aplica la tipografía general de los formularios.

La caja de contenido ocupa todo el ancho de su columna y tiene una altura mínima de 600 px (400 px en móvil); crece con el contenido.

Los formularios de cursos, páginas y anuncios revalidan el campo al perder el foco usando Django, sin guardar datos. Solo se retiran sus errores cuando la validación es correcta; los fallos de conexión se muestran explícitamente. El slug de una página o anuncio nuevo se genera a partir del título con palabras separadas por guiones, hasta que se edita manualmente. Los slugs existentes se conservan. Si no se envía el slug, Django también lo genera a partir del título.

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test apps.lms
```

Aplicar la migración reversible con `.\.venv\Scripts\python.exe manage.py migrate`. El rollback de esquema es `.\.venv\Scripts\python.exe manage.py migrate lms zero`; al hacerlo se eliminan los datos LMS creados, por lo que debe respaldarse la base de datos antes.
