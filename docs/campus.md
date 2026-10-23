

















# Ducky Academy: Campus (prompt 11)

## Alcance y transición

El Campus reutiliza el LMS; no es un catálogo comercial nuevo. La activación es
explícita por curso, según la transición confirmada por el usuario. La migración
0038 añade campos y **no activa cursos, convierte compras, modifica planes ni
resetea usuarios existentes**. Los cursos sin activar conservan su funcionamiento.

Se implementan Mis cursos, estado de matrícula y acceso, reanudación, materiales,
entregas, correcciones, pruebas, requisitos congelados, finalización, superación y
certificado propio. La autoría comercial y la migración entre versiones educativas
quedan fuera de este prompt.

## Archivos y entidades

| Superficie | Implementación |
|---|---|
| Contrato y operaciones Campus | `apps/lms/campus.py` |
| Vistas personales y docentes | `apps/lms/campus_views.py`, `apps/lms/urls.py`, `apps/lms/templates/lms/campus*.html` |
| Acceso compartido | `apps/users/access.py`, `apps/lms/policies.py`, `apps/lms/module_progression.py` |
| Matrícula, progreso y finalización existentes | `CourseMembership`, `ModuleContentProgress`, `LearningEvent`, `InscripcionCurso`, `apps/lms/services.py`, `signals.py`, `lifecycle.py` |
| Entregas y correcciones existentes | `Assignment`, `Submission`, `SubmissionAttachment`, `activity_workflow.py`, `activity_views.py` |
| Evaluaciones existentes | `QuestionVersion`, `Quiz`, `QuizAttempt`, `QuizAnswer`, `GradeEntry`, `quiz_attempts.py`, `quiz_views.py` |
| Certificación existente | `CourseCertificate`, `CredentialIssuer`, `apps/certifications/views.py`, plantillas de lista y validación |
| Progreso y objetivos existentes | `apps/lms/selectors.py`, `apps/users/dashboard_services.py`, `PersonalGoal`, enlace a Mi progreso |
| Configuración e intervención auditada | `apps/lms/admin.py`, `AuditEvent` |
| Esquema | `apps/lms/migrations/0038_coursemembership_access_origin_and_more.py` |
| Pruebas | `apps/lms/test_campus_access.py`, `test_campus_delivery.py`, `test_campus_certificates.py`, `test_campus_quizzes.py`, `test_campus_browser.py`, `campus_browser.cjs` |

No se crea otra tabla de matrícula, pagos, organización, energía ni certificados.
El contrato JSON en la matrícula guarda la versión educativa y los requisitos:
no duplica cuerpos HTML ni crea una autoría paralela.

## Activación administrativa y contrato con el catálogo

1. Preparar un `Curso` publicado y su `CourseSettingsLMS`.
2. Vincular materiales publicados mediante `ModuleContent` y sus unidades
   formativas. Debe existir al menos un contenido obligatorio. No se interpreta un
   curso vacío como finalizado.
3. En Django admin, activar `campus_enabled` e indicar `campus_version`.
4. Definir explícitamente las condiciones que corresponden al curso:
   - `campus_free`: acceso gratuito con matrícula activa.
   - `campus_included_plan`: vacío, `plus` o `pro`. PRO incluye lo explícitamente
     incluido en PLUS; una membresía nunca abre todo el catálogo.
   - `campus_entitlement_code`: código real de `PurchasedEntitlement` del 00B.
     El Campus consume su vigencia y revocación, pero no crea compras ni cobra.
   - `campus_certificate_included` y `campus_issuer`: emisor existente y activo.
   - `campus_max_submission_bytes`: límite contractual de archivo. Sin configurar
     se bloquean las entregas de archivos Campus con un error explícito; no se
     inventa un límite comercial. La entrega textual sigue disponible.
5. Las nuevas inscripciones congelan esos términos, requisitos, umbrales,
   referencias de preguntas y grupo. Los derechos comprados y el plan efectivo
   siguen comprobándose en tiempo real; cualquiera de los derechos válidos basta.
6. Las matrículas anteriores no se adoptan automáticamente. Un administrador
   revisa su grupo y utiliza la acción **Revisar y vincular requisitos Campus**
   en `CourseMembership`. La intervención queda en `AuditEvent`; no se crea un
   derecho de compra ficticio. La acción no es una herramienta de migración de
   contratos ya congelados.

El contrato mínimo consume `Curso` + etiqueta de versión, `Tema`, `FormativeUnit`,
`ModuleContent`, docentes de `CourseMembership`, `Section`/`Cohort`, condiciones de
acceso y certificado incluido. Sigue vigente la unicidad curso/usuario de la
matrícula existente: **no se soportan nuevas matrículas independientes por edición
del mismo curso** sin una futura migración explícita del catálogo.

Los cambios posteriores de inclusión comercial no reescriben los términos de
matrículas ya contratadas. Cambiar la versión, el emisor o la inclusión del
certificado, desactivar Campus con contratos congelados, o cambiar el grupo de una
matrícula congelada requiere una migración educativa: estas operaciones se bloquean.
Un material modificado respecto de su definición congelada queda inaccesible con
un aviso de revisión; no se sirve silenciosamente una nueva versión. No existe
todavía una herramienta para esa migración ni para servir revisiones históricas
completas de todos los tipos de material. No editar material en uso sin resolver
antes esa transición.

## Permisos

| Actor | Permitido | No concedido |
|---|---|---|
| Visitante | Presentación del Campus | Matrículas, expedientes o progreso persistente |
| Alumno FREE | Cursos gratuitos inscritos y derechos individuales vigentes | Cursos no incluidos, calificación o docencia |
| Alumno PLUS | Lo anterior y cursos explícitamente incluidos en PLUS; pendientes | Docencia por pertenecer a un plan |
| Alumno PRO | Lo anterior, inclusión explícita en PRO y objetivos existentes | Docencia, administración o comparación de notas incompatibles |
| Profesor/asistente asignado | Material del curso, entregas y seguimiento de los alumnos asignados | Autocalificación o expedientes de otros grupos |
| Profesor con sección | Mis grupos docentes y corrección de su sección | Libro global de notas, listado global de personas o dashboard con expedientes de otros grupos |
| Academia sin vínculo curso-organización | Ningún expediente por el rol solo | Asignaciones ni seguimiento de organizaciones inferidas |
| Headhunter | Sus cursos si además tiene matrícula y derecho legítimos | Expedientes académicos por su rol |
| Administrador | Configuración e incidencias auditadas | Reactivación automática de certificados revocados |

La autorización se comprueba también al abrir páginas, iniciar o actualizar
pruebas, entregar actividades y descargar archivos o adjuntos. Un enlace temporal
de archivo no sustituye el derecho vigente. Los endpoints de temas y cuestionarios
del LMS antiguo no pueden usarse para obtener cuerpos o soluciones de un curso
Campus: los temas se excluyen de esa API y el curso no serializa temas anidados.
Las áreas antiguas de materiales, actividades, pruebas, archivos, programa y
páginas redirigen al alumno al Campus; completar una lección también vuelve allí.

## Aprendizaje y evidencia

- Tipos conectados: páginas, ficheros privados, actividades de texto/archivo y
  pruebas de opción única versionadas sobre 10 del motor existente.
- Abrir una página/fichero registra una vista y el punto de reanudación. No se
  certifica tiempo real de lectura o de vídeo ni atención del alumno.
- `manual` es evidencia **declarada** y requiere una apertura previa;
  `viewed`, `submitted`, `finished` y `passed` usan registros del servidor.
  Marcar un requisito manual no equivale a una evaluación externa verificada.
- Los requisitos de la matrícula, no las incorporaciones posteriores al curso,
  determinan el denominador y los pendientes.
- `content_completed_at` y `passed_at` son hitos separados. La superación exige
  cumplir todos los requisitos obligatorios de la versión. Una actividad aprobada
  exige corrección publicada y el umbral congelado; una prueba aprobada no se
  acredita antes de publicar la nota.
- Las finalizaciones se reflejan en `InscripcionCurso` para reutilizar el dashboard,
  Mi progreso y los objetivos de cursos terminados. Los selectores de progreso y
  las tarjetas del dashboard consumen los requisitos Campus cuando corresponde.
- No hay comparativas numéricas entre competencias o cursos incompatibles.
- Los hitos ya registrados y los certificados emitidos no se revocan
  automáticamente por pérdida de acceso ni por una corrección posterior.

### Entregas y correcciones

El formulario lleva una clave UUID de idempotencia. Repetirla con el mismo
contenido devuelve el mismo recibo; reutilizarla con otra respuesta produce error.
Una entrega distinta crea otra versión, sin borrar las anteriores. Se comprueban
matrícula, derecho, plazo de actividad, apertura/cierre de la tarea, tipos admitidos,
extensión, tamaño configurado y límites de reentrega de `Assignment`.

Las tareas creadas automáticamente por el flujo existente conservan su política
existente de reentrega hasta el plazo; no se añade una cuota comercial nueva.
Las tareas con intentos configurados sí los respetan. No se presenta guardado de
borradores como función nueva: el flujo de actividad guarda entregas confirmadas.

La corrección rechaza notas fuera de rango, borradores, versiones reemplazadas,
autocalificación y otros grupos. `AuditEvent` conserva actor, alumno, entrega y
valores anteriores/nuevos. La publicación respeta la fecha de feedback.

Las pruebas reutilizan orden/versiones congeladas, autosave, límite de intentos
y temporizador del servidor. Recargar no reinicia el tiempo y no se muestran
soluciones antes de la publicación correspondiente.

### Certificados

Se emite el mismo `CourseCertificate` usado por Mis certificaciones, único por
curso y usuario. Hace falta inclusión explícita, emisor activo identificado y
superación. Se conservan versión, título original y huella del contrato.
La descarga del PDF se genera en memoria desde ese registro, solo para su
titular, con `private, no-store` y `nosniff`; no se publica una nueva ruta de
almacenamiento. Las emisiones antiguas conservan su mecanismo anterior.

Se reutiliza la validación pública por código UUID existente. El certificado se
identifica como **propio**, no como homologación oficial. Admin permite una
revocación motivada y auditada; no permite reactivación automática ni eliminar
registros desde ese panel. Una caída del plan no revoca certificados.

## Integraciones disponibles y pendientes

| Integración | Estado |
|---|---|
| Identidad, roles y plan efectivo | Reutilizados del acceso común |
| Compras individuales | Lectura de derechos reales 00B; sin checkout o cobros nuevos |
| Grupos y docencia | `Section`, `Cohort`, `CourseMembership`; seguimiento y corrección acotados |
| Academia | **Pendiente** vínculo autorizado entre curso/edición y organización del catálogo. El rol solo no da acceso |
| Progreso y objetivos | Reutilizados; sin segunda tabla de objetivos |
| Notificaciones | Cola, preferencias y deduplicación existentes; no se afirma envío externo real |
| Logros | Motor de insignias de módulo existente, con requisitos congelados |
| Certificaciones | Registro existente y PDF privado para emisiones Campus |
| Nexus DNA / Tech DNA | Sistemas de evaluación independientes; no se confunden con aprobar un curso ni se comparan notas |
| Tutoría humana | No se inventa su inclusión ni un servicio operativo |
| Energía 00C / ayuda IA | **Pendiente** reserva-confirmación-liberación real. Ayuda IA Campus bloqueada incluso si se habilita un proveedor LMS |
| Lecciones públicas | Solo presentación pública; no se publican automáticamente materiales o recursos privados |
| Versiones/ediciones y asignaciones organizativas comerciales | **Pendiente** contrato y migración del catálogo del prompt 12; no implementados como catálogo paralelo |

Leer, avanzar, entregar, corregir y descargar el certificado no descuenta energía.

## Rutas

| Ruta | Uso |
|---|---|
| `/lms/campus/` | Presentación y Mis cursos |
| `/lms/campus/courses/<course_id>/` | Estado, requisitos, lecciones y reanudación |
| POST a la anterior, `action=enroll` | Inscripción con contrato congelado |
| POST a la anterior, `action=certificate` | Emisión propia idempotente |
| `/lms/campus/teaching/` | Seguimiento docente acotado |
| `/lms/courses/<course_id>/pages/<page_slug>/` | Página del motor existente; utilizar el enlace generado por `content_url` |
| `/lms/courses/<course_id>/contents/<content_id>/complete/` | POST de finalización declarada |
| `/lms/courses/<course_id>/activities/<activity_id>/` | Actividad y POST de entrega |
| `/lms/courses/<course_id>/activities/<activity_id>/submissions/<submission_id>/grade/` | Corrección autorizada |
| `/lms/courses/<course_id>/quizzes/<quiz_id>/` | Evaluación y acceso a intento cronometrado |
| `/certificados/` | Mis certificaciones |
| `/certificados/cursos/<certificate_id>/descargar/` | Descarga privada |
| `/admin/lms/coursesettingslms/` | Activación/configuración |
| `/admin/lms/coursemembership/` | Revisión de matrículas anteriores |
| `/admin/lms/coursecertificate/` | Incidencias y revocación motivada |

Los nombres Django (`lms:course_page_detail`, `certifications:lms_download`, etc.)
son la referencia contractual para construir URLs, sin concatenar identificadores
de otros cursos.

## Verificación reproducible

Las pruebas crean usuarios, emisores, cursos, derechos y entregas en la base
aislada de Django. El navegador también usa esa base: no cambia contraseñas,
membresías ni matrículas reales. No se realizan cargos ni envíos externos.

```powershell
.\.venv\Scripts\python.exe manage.py migrate lms --noinput
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations lms --check --dry-run
.\.venv\Scripts\python.exe manage.py test apps.lms.test_campus_access apps.lms.test_campus_delivery apps.lms.test_campus_certificates apps.lms.test_campus_quizzes --noinput
```

Para navegador real, Node y Playwright son herramientas de prueba, no dependencias
de ejecución del Campus. Se puede utilizar un módulo Playwright existente:

```powershell
npx --yes --package=playwright playwright install chromium
# Directorio absoluto del módulo Playwright que acaba de instalarse en la caché npm:
$env:PLAYWRIGHT_MODULE_PATH = 'C:\ruta\real\node_modules\playwright'
.\.venv\Scripts\python.exe manage.py test apps.lms.test_campus_browser --noinput
```

Sin esa variable la prueba de navegador se declara omitida, no aprobada.
`StaticLiveServerTestCase` inicia y detiene un servidor real con datos aislados.
El flujo comprueba visitante, login obligatorio, matrícula con CSRF, lectura,
reanudación y finalización desde un viewport de 390 px; Python verifica luego
la persistencia. No crea una sesión sobre un usuario real.

### Pasos funcionales

1. Ejecutar las pruebas; no sembrar fixtures en la base de trabajo.
2. Abrir el Campus sin sesión: solo presentación, sin expedientes.
3. En una base de desarrollo aislada, configurar un curso como se indica arriba.
4. Inscribirse, abrir la página y volver al curso: aparece Continuar.
5. Completar la lección: comprobar evidencia declarada y requisitos restantes.
6. Entregar una actividad dos veces con la misma clave: un recibo, una versión.
7. Corregir desde un docente de otro grupo: debe rechazarse.
8. Terminar una prueba: no debe acreditar superación antes de publicar su nota.
9. Superar requisitos y obtener el certificado dos veces: un solo registro.
10. Descargar con otra cuenta: rechazo. Perder el derecho de materiales: se
    bloquean materiales, pero no se borra progreso ni el certificado emitido.

No se certifica aquí el funcionamiento con PostgreSQL, un broker externo ni
servicios de correo reales. La evidencia de ejecución y las incidencias de
regresión se registran en [la validación de Campus](campus-runtime-validation.md).
