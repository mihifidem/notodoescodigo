# Validacion de Campus

Fecha: 2026-10-20. Alcance: Campus del prompt 11 sobre el LMS existente.
Configuracion y limites funcionales: [guia Campus](campus.md).

## Entorno y aislamiento

- Python 3.10.4, Django 4.2.16, entorno virtual del proyecto.
- `docker info` termina con codigo 1: `failed to connect to the docker API at
  npipe:////./pipe/dockerDesktopLinuxEngine`; el sistema no encuentra la tuberia.
  No se inicia ni reconfigura Docker Desktop compartido.
- Infraestructura alternativa: base SQLite aislada creada y destruida por Django.
  No se certifican concurrencia ni bloqueos de PostgreSQL, broker o correo real.
- Node v24.14.1 disponible. Chromium headless real mediante Playwright.
- Tras detectar que Playwright no estaba instalado, se ejecuto
  `npx --yes --package=playwright playwright install chromium` correctamente.
  La herramienta queda en cache npm, no como dependencia de ejecucion del Campus.
- Las pruebas crean sus propios usuarios, sesiones, cursos, grupos, derechos,
  entregas y emisores. No se modifican usuarios reales ni se cobran compras.

## Arranque y navegador

`CampusBrowserTests`, basado en `StaticLiveServerTestCase`, arranca un servidor
HTTP real con la base de prueba y lo detiene al terminar. Playwright verifica
presentacion anonima, redireccion al login, matricula por formulario con CSRF,
lectura, reanudacion y finalizacion desde un viewport de 390 px. La autenticacion
del alumno utiliza exclusivamente una sesion de un usuario creado por la prueba;
no se prueba aqui el formulario de contrasena ni un proveedor externo de identidad.

Python comprueba despues la persistencia de `last_content`, `passed_at` y un unico
registro de progreso. La prueba falla si Node o Chromium devuelven un codigo
distinto de cero. Sin `PLAYWRIGHT_MODULE_PATH` se omite explicitamente: no cuenta
como aprobada. En las ejecuciones registradas la variable esta configurada.
No se midio separadamente el tiempo de arranque del servidor.

## Comandos reproducibles

```powershell
$env:PLAYWRIGHT_MODULE_PATH='C:\Users\mihif\AppData\Local\npm-cache\_npx\e41f203b7505f1fb\node_modules\playwright'
.\.venv\Scripts\python.exe manage.py test apps.lms.test_campus_access apps.lms.test_campus_delivery apps.lms.test_campus_certificates apps.lms.test_campus_quizzes apps.lms.test_campus_browser apps.lms.test_activity_workflow.ActivityStudentWorkflowTests apps.lms.test_module_progression.ModuleProgressionTests apps.lms.test_module_badges apps.lms.test_knowledge_api apps.lms.test_lifecycle.LifecycleTests apps.lms.test_quiz_management.QuizManagementTests apps.certifications.tests.LmsCourseCertificateIntegrationTests --noinput --verbosity 1
```

La ruta de cache es la utilizada en esta maquina; en otra instalacion debe
sustituirse por la ubicacion real de Playwright.

Comprobacion intermedia de la navegacion:

```powershell
.\.venv\Scripts\python.exe manage.py test apps.lms.test_campus_access apps.lms.test_campus_delivery apps.lms.test_campus_certificates apps.lms.test_campus_quizzes apps.lms.test_campus_browser apps.lms.test_module_progression.ModuleProgressionTests --noinput --verbosity 1
```

Comprobaciones de configuracion y esquema:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations lms --check --dry-run
.\.venv\Scripts\python.exe manage.py showmigrations lms
git --no-pager diff --check -- apps\lms apps\courses apps\users\access.py apps\users\dashboard_services.py apps\certifications\views.py templates\certifications templates\base.html docs\lms.md
```

## Resultados y correcciones

| Ejecucion | Resultado | Codigo | Evidencia |
|---|---|---|---|
| Seleccion de regresion, ejecucion 40 | PASS | 0 | 126 pruebas, 79.553 s |
| Proteccion de politica y emision docente, ejecucion 45 | PASS | 0 | 126 pruebas, 65.115 s; incluye Chromium |
| Ultima navegacion, ejecucion 47 | FAIL | 1 | 41 pruebas: 40 correctas, 1 error; Chromium correcto |
| Repeticion final, ejecucion 51 | PASS | 0 | 126 pruebas, 71.893 s, sin errores ni omisiones; incluye Chromium |
| `manage.py check` | PASS con aviso | 0 | Aviso previo `urls.W002` |
| `makemigrations lms --check --dry-run` | PASS | 0 | Sin cambios pendientes |
| Migracion 0038 | APLICADA | 0 | `showmigrations` muestra `[X]` |
| Comprobacion de sintaxis Pylance | PASS | - | Vistas y pruebas modificadas sin errores de sintaxis |
| `git diff --check` del alcance | PASS | 0 | Sin errores de espacios |

La ejecucion 47 descubrio que un segundo metodo `CourseAreaView.get` anulaba
la redireccion nueva: el programa antiguo llegaba a renderizarse y fallaba al
resolver un autor ausente. Se integro la redireccion en el metodo efectivo,
conservando su exportacion CSV y navegacion anteriores para cursos no activados.
La prueba ahora verifica las seis areas antiguas de materiales, actividades,
pruebas, archivos, programa y paginas. No se ha ocultado el fallo cambiando
el fixture ni suprimiendo la comprobacion del destino de redireccion.

### Cobertura nueva

Se anaden 22 pruebas de aceptacion, integracion y navegador, repartidas en cinco
modulos, reutilizando el ejecutor `manage.py test`:

| Modulo | Recorridos y persistencia comprobados |
|---|---|
| `test_campus_access.py` | Visitante, alta, derechos independientes, perdida de acceso, reanudacion, version congelada, preservacion de progreso y bloqueo de rutas antiguas |
| `test_campus_delivery.py` | Entrega repetida/conflictiva, archivos y plazos, versiones, correccion publicada, grupo y auditoria |
| `test_campus_certificates.py` | Superacion, inclusion y emisor, emision unica, descarga propia, revocacion, permanencia tras perdida de acceso |
| `test_campus_quizzes.py` | Intento real y publicacion de nota antes de acreditar superacion |
| `test_campus_browser.py` | Flujo HTTP y Chromium real descrito arriba |

Las pruebas anteriores relacionadas se reutilizan, no se sustituyen. Las pruebas
HTML originales de Nexus pertenecen a la migracion pausada del prompt 9; no son
una bateria E2E del Campus.

## Diagnosticos ampliados: no aprobados

Las selecciones ampliadas ajenas al conjunto anterior terminaron con codigo 1.
Estos resultados no se convierten en PASS por haber pasado otras pruebas:

- Falta `static/quiz/0201QZ.csv`, requerido por
  `test_supplied_csv_has_94_valid_questions`,
  `test_create_bank_with_csv_preview_confirm_and_cancel` y
  `StudentQuizTests.test_full_94_question_csv_can_be_taken_and_graded`.
  No se inventa un CSV para satisfacer expectativas de datos originales.
- `StudentQuizTests.test_teacher_reopens_with_grace_and_preserves_grades_and_attempt_limit`
  espera un aviso de cierre, pero recibe dos. Se reprodujo el fallo con las
  funciones originales `process_quiz` y `complete_student` de HEAD sustituidas
  solo en memoria, sin revertir archivos ni tocar cambios ajenos.
- Persiste el aviso previo `urls.W002` por una ruta dashboard con barra inicial.

## Veredicto

```text
environment:
  docker: UNAVAILABLE - docker info, exit_code: 1, tuberia del daemon inexistente
  node: AVAILABLE - v24.14.1
  playwright: AVAILABLE - Chromium instalado y ejecutado
  infra-tier: FALLBACK(SQLite aislada) - sin daemon Docker funcional
  browser-tier: PRIMARY(Playwright) - independiente de Docker
startup: PASS - exit_code: 0, servidor StaticLiveServerTestCase, HTTP y navegador operativos
integration: PASS - exit_code: 0, passed: 125, failed: 0, skipped: 0; ejecucion 51
e2e: PASS - exit_code: 0, passed: 1, failed: 0, skipped: 0; ejecucion 51
broader-diagnostics: FAIL - exit_code: 1, CSV ausente y aviso duplicado previo
overall: FAIL - diagnosticos ampliados no resueltos; no se certifica todo el repositorio
campus-scope: PASS - exit_code: 0, seleccion final completa, 126 pruebas
```

Integracion y navegador se ejecutan en el mismo comando; su codigo 0 corresponde
al conjunto de 126 pruebas. No se suman las repeticiones como pruebas distintas.
Los mensajes `Broken pipe` al cancelar peticiones del navegador no produjeron
fallos; la prueba finalizo y cerro tanto Chromium como el servidor de prueba.

No se certifican la asignacion de academias por organizacion, migraciones entre
ediciones comerciales, tutoria humana ni el ciclo de energia 00C. La ayuda IA
Campus permanece bloqueada, no simulada, hasta disponer del contrato real.
