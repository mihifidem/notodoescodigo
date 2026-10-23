# Ducky Me: dashboard personal

`/dashboard/` is the personal landing page for a visitor or an authenticated account.
The visitor response uses fixed, clearly labeled demonstration copy and does not query
user-specific records. Authentication is never inferred from a profile field.

## Sources and scope

The authenticated aggregator is `apps/users/dashboard_services.py`. Each implemented
source is queried with the authenticated user as owner:

- course enrollment and lesson progress (`InscripcionCurso`, `Progreso`);
- activity history (`RegistroActividad`);
- badges, achievements, and missions;
- the user's own Nexus DNA profile and completed `SesionTest` records;
- assigned LMS course memberships for the teacher-facing section.

The page does not calculate a global ranking, does not turn a missing Ducky Arenas
profile into a zero balance, and does not expose assessment answer payloads. A source
failure is logged and rendered as temporarily unavailable; an empty result is distinct
from a failed query. Course completion percentage uses the course's actual topic count.
Comparisons use consecutive 30-day windows and calendar-month metrics use the
configured local timezone (`Europe/Madrid` in the development configuration).

Employment applications, AI quota/usage, and Ducky Bank are not connected to verified
owner-scoped services in this project. The dashboard labels those integrations as
pending and does not display mock balances, applications, or consumption.

## Access and personal goals

The dashboard reads the effective plan and role assignments through the shared access
service. PRO-only monthly and period-comparison cards use the shared `can()` policy.
Teacher courses are loaded only for an account with a teacher role and only from its
active LMS memberships. Access level does not imply a professional or administrative
role.

`PersonalGoal` is stored in the users app and owned by one account. Migration
`0015_personalgoal` adds the model without altering existing records. PRO accounts may
create, edit, complete, archive, and export their own goals. Server endpoints re-check
the shared PRO policy and owner; hiding buttons in the page is not the authorization
boundary. Goal exports include only the authenticated owner's records. Downgrading
does not delete goals: active goals remain visible read-only and management/export
endpoints reject non-PRO access.

Automatic metric sources are limited to existing owner-scoped data: completed course
topics, completed courses, completed Nexus tests, and recorded activities. A missing
or failed source is not reported as zero. Goal targets and manual progress are entered
by the owner; configurable business limits have not been invented.

## Routes and UI

- `/dashboard/`: visitor demo or account dashboard.
- `/objetivos/nuevo/`, `/objetivos/<id>/editar/`: PRO goal create/edit.
- `/objetivos/<id>/complete/`, `/objetivos/<id>/archive/`: POST-only state changes.
- `/objetivos/exportar/`: PRO-only CSV export of the owner's goals.

The templates use the existing Bootstrap layout, labels, keyboard-operable links and
forms, visible validation messages, and responsive grid classes.
The dashboard and visitor demo use the shared Tech Modern Blue styles, with a navy
hero, blue-accented metric cards, responsive quick actions, and light/dark themes.
Dashboard-specific rules are scoped to `.ducky-dashboard`; the shared sidebar is
unchanged. Authenticated accounts have anchor navigation to progress, courses,
achievements, results, and personal goals. This presentation does not change data
sources, plan restrictions, role checks, or goal actions.

Authenticated users can explicitly join or leave each published public ranking
from the dashboard. Joining requires a public alias and an open/prepared season;
existing participants can withdraw even after a season closes. The forms reuse
the ranking participation service and audit trail, use CSRF-protected POST, act
only on the current account, and return to the dashboard. Visiting the dashboard
never enables participation. No ranking is created automatically.

The private account profile (`/dashboard/perfil/<username>/`) also uses Tech Modern
Blue, with scoped `.profile-tech` styles, section navigation, and responsive cards.
Profile ownership/staff access, credential visibility, certificate downloads, and
the explicit account deletion confirmation are unchanged.
The identity card displays the profile owner's effective FREE/PLUS/PRO plan
instead of the legacy role label, alongside the separate experience level.
It uses the shared access resolver, including paid contracts and their expiry,
even when a staff member views another account's profile.

## Navegación lateral

El menú principal agrupa Ducky Profile, Ducky Academy, Ducky Arenas y Ducky Jobs
en cuatro bloques desplegables con iconos y foco de teclado visible. Ducky Me
conserva las opciones de cuenta; Campus y certificaciones están en Academy,
y DuckyClash dentro de Arenas. Administración y utilidades permanecen aparte
con sus permisos previos. Los destinos pendientes no se convierten en nuevas
funcionalidades. Se selecciona el enlace más específico de la ruta actual y se
abre su bloque, sin reutilizar selecciones antiguas de otra página.
El acceso DuckyShop se encuentra a la derecha de la barra superior, conservando
su destino. En la tarjeta del sidebar queda un único botón Ducky Me.
Este botón ocupa el ancho disponible y tiene un área de pulsación mínima de
48 px. Blog Tips, Biblioteca de prompts, Links web y MiniApps comparten el
estilo azul de DuckyShop y conservan sus rutas.

The profile edit page uses the same Tech Modern Blue styles, including responsive
avatar tools, current-avatar preview, an icon when no avatar exists, themed inputs,
and save/cancel actions. Avatar generation/confirmation endpoints and form fields
remain unchanged, as do protected classroom and role restrictions.

## ADMIN content backups

Platform administrators have a **Copias JSON: prompts, blog y enlaces** quick action.
The **Admin** sidebar section also includes **Copias JSON**, with an active-page indicator.
The page is `/data-admin/copias-json/`. Authorization uses `is_platform_admin`;
staff status or a paid plan alone is insufficient. The existing CSV tool is unchanged.

- Each section downloads separately using a CSRF-protected POST to
  `/data-admin/copias-json/<section>/descargar/`.
- Upload requires selecting the matching section and explicitly confirming the restore.
  IDs are preserved: matching rows are updated, missing rows inserted, and additional
  rows are never deleted. Restored records' category and tag relationships match the backup.
- Included tables: prompt categories/subcategories/hashtags/prompts; blog
  categories/subcategories/hashtags/posts; important links. Accounts, points, readings,
  ratings and access history are excluded. Link authors remain existing user IDs.
- Images are paths only, not binary files. Back up the media directory separately.
  Restoring does not write, download or delete physical media files.
- JSON has `format: portal-content-backup`, `version: 1`, `section`, `created_at`,
  `files_included: false`, the ordered model list and serialized `objects`.
  Dates retain their full precision. Limits are 10 MB and 10,000 records per section.
- Only allowlisted models and the exact current field schema are accepted.
  Invalid values, foreign keys, duplicate IDs, unique-key conflicts and incompatible
  category relationships roll back the entire operation. Explicit IDs also reset
  database sequences where supported. Restores bypass model save overrides to retain
  timestamps; they do not run user activity or reward flows.
- Downloads and successful restores are recorded in Django's administration log.
  Responses are non-cacheable. Validation failures are displayed and logged.

Keep JSON files private: they may contain restricted or unpublished editorial content.
Use backups from the same installation and schema; matching IDs from another database
could overwrite unrelated content. This is a selective content merge, not a full database
or media disaster-recovery backup. Always download a fresh copy before restoration.
