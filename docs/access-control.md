# Identidad y control de acceso

La identidad de usuario sigue siendo la de Django/allauth. Una visita anónima es
un contexto `visitor`, no una fila `User`. El registro crea una cuenta FREE con
el rol general `USUARIO`; no permite autoasignarse roles profesionales.
`User.role` identifica Usuario, Profesor, Alumno, Empresa, Academia o Headhunter.
El registro público siempre crea Usuario; ni el perfil ni la API permiten
autoasignarse otro rol. Su edición en Django admin requiere superusuario o
`users.assign_access_roles`, además del permiso para gestionar usuarios.
ADMIN sigue siendo una asignación independiente, no una opción de este campo.

## Activación de los accesos de autenticación

Login y Register permanecen ocultos hasta pulsar **Ctrl+O**. El atajo envía
un POST protegido por CSRF y guarda la activación en la sesión de Django.
Sin esa activación, GET y POST a `/accounts/login/`, `/dashboard/register/`
y `/dashboard/registro/` redirigen a la home.
La activación se conserva al navegar dentro de la misma sesión y los enlaces
se muestran de nuevo sin repetir el atajo. No hay un temporizador de activación:
se mantiene hasta hacer logout (o hasta que termine la sesión de Django).
El logout elimina explícitamente la activación, incluso si la visita todavía
no había iniciado sesión. Para volver a acceder hay que pulsar Ctrl+O de nuevo.

Esto es una barrera de presentación y navegación, no un secreto ni un permiso:
un cliente puede reproducir la petición de activación. Tras activarla, los
visitantes pueden crear una cuenta FREE de tipo Usuario, sin autoasignarse
roles profesionales. Las cuentas ya autenticadas se redirigen al dashboard.

Al guardar un cambio de rol se activa su asignación de acceso y se desactivan
las del rol anterior, conservando el rol general USUARIO, ADMIN, los permisos
Django y las membresías de cursos. Profesor y Alumno tienen asignación global;
Empresa, Academia y Headhunter solo se activan en organizaciones compatibles
con membresía activa. Sin esa membresía se conserva el tipo elegido, pero no
se concede acceso organizativo; los cambios posteriores de organización o
membresía sincronizan esas asignaciones. Profesor no habilita edición global
de cursos ajenos: se conservan los controles por curso.

Las migraciones `users.0018` y `users.0019` cambian Basic y Premium a Usuario,
mantienen Alumno y sincronizan su asignación. No modifican planes, contratos,
pagos ni suscripciones. Las modificaciones programáticas del rol deben usar
`User.save()`, no `QuerySet.update()` ni operaciones masivas que omiten la
sincronización. El proceso de migración es idempotente; revertirlo no recupera
la distinción antigua Basic/Premium.

Nexus utiliza PLUS o PRO vigente para los tests de acceso Premium y para el
acceso a informes antes ligado al rol Premium; los pagos individuales de informes
siguen siendo válidos. El tipo de usuario por sí solo nunca da acceso de pago.

La pantalla **Mi acceso y permisos** (`/dashboard/accesos/`) usa el diseño
Tech Modern Blue compartido con facturación: cabecera con nivel vigente,
accesos por sección, tarjetas de nivel y roles, y estados textuales de prestaciones.
Incluye un enlace a la gestión del plan, soporte móvil y temas claro/oscuro.
Los estilos se limitan a `.billing-app.access-app`; no modifican las políticas
ni permiten editar permisos desde el resumen.
El sidebar muestra junto al nivel de experiencia una etiqueta FREE/PLUS/PRO
calculada con `effective_plan`, incluyendo contratos y concesiones vigentes;
no utiliza el rol heredado ni confunde experiencia con membresía.

## Matriz aplicada

| Acción/recurso | Nivel | Rol | Propiedad/ámbito | Derecho individual |
| --- | --- | --- | --- | --- |
| Contenido registrado (`view_registered_content`) | Cualquier cuenta activa, incluido FREE | No requerido | No aplica | No requerido |
| Contenido privado premium del blog | PLUS o PRO vigente | No requerido | Post visible al usuario | No requerido |
| Acceso a miniapp privada | PRO vigente o ALUMNO | No requerido | Configuración activa; si es privada, además | No requerido |
| Editar curso | Independiente del nivel | Propietario/docente/asistente del curso; o permiso de gestión existente | Membresía activa en ese curso | No requerido |
| Publicar curso | Independiente del nivel | Propietario; o docente/asistente con permiso de publicación | Membresía activa en ese curso | No requerido |
| Ver curso | Independiente del nivel | No requerido para curso publicado; inscripción/membresía para borrador | Curso concreto | No requerido |
| Reportar/consultar bugs | Independiente del nivel | ALUMNO o estudiante en curso activo | Cuenta que reporta; las vistas de revisión usan permiso administrativo | No requerido |
| Ver recurso de organización | Independiente del nivel | Membresía activa de esa organización | Organización concreta | No requerido |
| Ver candidatos | Independiente del nivel | HEADHUNTER global o de la empresa correspondiente | Recurso propio y ámbito de empresa | No requerido; integración pendiente |
| Acceder a un servicio comprado | Independiente del nivel | No requerido | El derecho pertenece a la cuenta indicada | Derecho vigente, no revocado y coincidente con el código |
| Eliminar curso | Independiente del nivel | Staff de Django, superusuario, ADMIN o `lms.manage_platform`; el permiso de borrar cursos por sí solo no basta | Acción global | Evento LMS existente |
| Administrar políticas/membresías/roles | No concede privilegios por plan | Permiso Django específico o superusuario | Acción administrativa | Cada cambio administrado se audita |

Las denegaciones de `can(actor, action, resource)` distinguen, entre otras,
`registration_required`, `account_inactive`, `plan_required`,
`subscription_inactive`, `role_required`, `ownership_required`,
`organization_membership_required`, `purchase_required` y
`resource_unavailable`. Los identificadores de denegación son estables para
consumidores de API; no incluyen datos privados.

## Contratos y propiedad

- `AccessMembership` es una fila por cuenta con plan, estado, periodo y
  vigencia. La ausencia de fila significa FREE. No se deriva el plan de
  `User.role`.
- `AccessRoleAssignment` permite roles globales y roles acotados a organización;
  una asignación organizativa requiere membresía activa. ALUMNO y PROFESOR
  conservan además los ámbitos formativos propios de las membresías LMS.
- `OrganizationMembership` identifica la pertenencia y su estado. La
  autorización compara la organización exacta del recurso; no concede acceso a
  otras organizaciones del mismo usuario.
- `PurchasedEntitlement` pertenece a `user`; `code` identifica la prestación,
  `source` y `source_reference` identifican el origen, y `idempotency_key`
  impide registrar dos veces la misma operación. `starts_at`, `expires_at` y
  `revoked_at` determinan la vigencia con independencia de la membresía.
- `AccessPolicyConfiguration` conserva versiones. Se edita en borrador, se
  aprueba en una transición separada y solo una versión por clave puede estar
  activa. No se precargan tarifas, descuentos, precios ni límites económicos.
- `GET /api/v1/access/` devuelve al cliente un resumen del propio actor o el
  contexto visitante. Es informativo; toda operación protegida vuelve a llamar
  la autorización en el servidor. No se exponen saldos ni datos de terceros.

La migración `users.0012_access_roles_plans_entitlements` conserva los modelos
heredados y crea las entidades incrementales. Las cuentas existentes reciben
FREE y `USUARIO`, sin inferir suscripciones ni roles profesionales del campo
heredado. Es necesario aplicar migraciones por el procedimiento habitual del
entorno (`python manage.py migrate users`), con respaldo previo de producción.

## Configuración e integraciones pendientes

- PLUS/PRO, sus periodos y cualquier cambio de acceso deben proceder de una
  fuente de suscripción autorizada. No hay checkout ni catálogo aprobado
  conectado aquí; los precios y límites económicos permanecen desactivados.
- Los derechos individuales disponen de almacenamiento y comprobación, pero
  requieren que el sistema de compra verificado los emita mediante un adaptador
  idempotente. No se crea un derecho desde el navegador.
- El módulo de ofertas/candidatos no tiene un modelo de recurso verificable en
  este repositorio; `view_candidates` falla cerrado si no se entrega un recurso
  real. No se simulan candidatos.
- La verificación de email y recuperación siguen delegadas al flujo existente
  de django-allauth; esta entrega no altera sus políticas.
- Las acciones y límites cuyo valor comercial no está definido requieren
  configuración aprobada. No se interpretan como ilimitados.

La configuración de precios, contratos, cargos, facturas y bonos se describe
en [Facturación, membresías y prestaciones](./billing.md). El nivel efectivo
también contempla contratos de membresía y concesiones administrativas vigentes;
ninguno de ellos otorga roles profesionales.
