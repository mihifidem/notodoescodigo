# Ducky Me: insignias y logros

La colección de `/gamificacion/insignias/` amplía los modelos `Insignia` e
`InsigniaUsuario` existentes. Las insignias son distintivos internos; no representan
certificaciones oficiales.

La colección usa Tech Modern Blue: cabecera con el total vigente, accesos de
gestión según permisos, filtros y tarjetas de progreso. Los estilos de
`static/css/badges.css` están limitados a `.badges-app` y reutilizan la base
visual de facturación. Admiten tema claro/oscuro, móvil y movimiento reducido.
Se conservan formularios, filtros, estados y controles de publicación privada.
La colección pública `/gamificacion/coleccion/<username>/` usa la misma cabecera,
tarjetas y estado vacío Tech Modern Blue, con un contador exclusivamente de
concesiones públicas vigentes. El rediseño no cambia los filtros de privacidad.

## Requisitos versionados y concesiones

- `InsigniaVersion` almacena la condición pública, la fuente y el método de verificación.
  Las reglas implementadas usan datos existentes: curso/tema completado, evaluación
  aprobada, cantidad de temas, racha y puntos acumulados.
- Las concesiones automáticas se verifican en el servidor. Una concesión por usuario e
  insignia es única; la clave idempotente y la restricción de base de datos evitan
  duplicados. Concesiones y cambios administrativos generan `InsigniaAuditEvent`.
- La migración `gamification/0006` conserva registros históricos, los mantiene privados
  y solo traduce a reglas nuevas los tipos heredados que pueden verificarse sin
  inventar datos. No crea concesiones a partir de progreso retrospectivo.
- Los cambios de requisitos crean una nueva versión. Las propuestas de academia quedan
  en revisión; la publicación global requiere ADMIN.

## Privacidad y permisos

- El catálogo publicado es visible para visitantes, sin datos personales ni progreso.
- La API `GET /api/v1/my-badges/` requiere autenticación y devuelve únicamente
  concesiones vigentes del usuario autenticado. El catálogo API `GET /api/v1/badges/`
  contiene solo definiciones publicadas y visibles.
- Las colecciones públicas muestran solo concesiones vigentes que su propietario ha
  elegido compartir. Evidencia, motivos y referencias internas no aparecen allí.
- ADMIN gestiona publicación y auditoría. ACADEMIA se limita a sus insignias y
  membresías. El profesorado solo puede conceder insignias manuales publicadas a
  estudiantes asignados a su curso. Cambiar de plan no concede permisos de gestión.
- La administración Django es de solo lectura para insignias y concesiones; las
  escrituras pasan por servicios que validan ámbito y registran auditoría.

## Integraciones pendientes

El subsistema LMS `Badge`/`UserBadge` sigue siendo independiente; no se fusiona ni se
presenta como una concesión equivalente. Las condiciones de progreso no soportadas o
sin una fuente verificable se muestran como no calculables y no conceden insignias.
