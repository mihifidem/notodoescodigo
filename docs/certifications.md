# Mis certificaciones

La lista personal usa Tech Modern Blue, con cabecera, resumen de credenciales,
accesos a declaraciones/emisiones y filtros adaptables. Reutiliza los estilos
acotados de `billing.css` y `personal-tech.css`, con tema claro/oscuro.
El cambio visual no modifica estados de revisión, permisos ni descargas originales.

## Registros y procedencia

La pantalla `/certificados/` reúne datos de sus fuentes originales, sin copiar certificados emitidos:

- `certifications.Certificado`: emisión interna heredada.
- `lms.CourseCertificate`: emisión del LMS; conserva su estado, código, curso y PDF originales.
- `certifications.UserCredential`: credenciales declaradas por una persona. Una declaración no es una comprobación externa.
- `gamification.InsigniaUsuario`: insignias consultadas desde Gamificación; no se convierten en certificados.

Las credenciales declaradas son privadas por defecto. El propietario puede publicar su ficha y elegir por separado si comparte identificador y enlace de verificación. La página pública no entrega evidencias. Los endpoints de perfil/API y de descarga filtran por propietario; los revisores solo acceden a evidencias de las solicitudes que tienen asignadas.

## Autorización administrativa

El permiso Django `certifications.manage_credential_catalog` permite gestionar emisores y definiciones del catálogo. `certifications.review_credential` permite revisar y revocar credenciales. Se asignan mediante grupos o permisos de usuario del administrador de Django; un rol de producto o plan de pago no los concede. Todas las decisiones de revisión guardan autor, método, resultado, fuente, alcance e historial.

| Acción | Condición |
| --- | --- |
| Declarar, editar o archivar una credencial | Sesión autenticada y propiedad del registro |
| Ver la ficha pública declarada | Visibilidad pública del propietario; identificador y URL requieren consentimiento individual |
| Descargar evidencia privada | Propietario o revisor con solicitud asignada para esa versión |
| Solicitar revisión | Propietario; enlace HTTPS o evidencia de la versión actual |
| Revisar, confirmar o revocar | Permiso `review_credential`; revisión asignada; el propietario no puede revisar su propia credencial |
| Publicar entradas del catálogo | Permiso `manage_credential_catalog`; emisor activo y definición validada |
| Ver o descargar una emisión interna en la zona autenticada | Propietario de la emisión |
| Validar un certificado LMS por código | Endpoint público de solo lectura; muestra el estado actual de la emisión |

No hay reglas de elegibilidad por nivel FREE/PLUS/PRO en este elemento; la visibilidad y las acciones se deciden por propiedad, consentimiento y permisos explícitos.

## Configuración

Los valores no fijan límites comerciales ni verificaciones automáticas:

- `CERTIFICATIONS_EVIDENCE_MAX_BYTES`: máximo en bytes para evidencias; `0` desactiva la carga.
- `CERTIFICATIONS_ALLOWED_EVIDENCE_MIME_TYPES`: tipos permitidos; se comprueban firma y extensión del contenido.
- `CERTIFICATIONS_EXPIRY_WARNING_DAYS`: antelación del aviso de caducidad; `0` desactiva los avisos.

Las evidencias se guardan con `CertificationEvidenceStorage`, fuera del directorio público de medios. La capacidad máxima y el backend de almacenamiento de producción deben configurarse de forma explícita. El PDF del LMS conserva el almacenamiento que ya tenía el modelo `CourseCertificate`; la vista nueva exige propiedad, pero no modifica el acceso directo a archivos de `MEDIA_URL` que pudiera habilitar la infraestructura. No se migran archivos heredados automáticamente. La configuración actual no incluye OCR, consulta automática de emisores ni verificación externa automática. Una URL o un PDF no verifican una credencial por sí solos.

Las migraciones `certifications.0004` y `certifications.0005` añaden el flujo de declaraciones, catálogo, evidencias, revisión y auditoría, y registran permisos separados. No eliminan ni migran los registros originales del LMS o de certificaciones heredadas.

## API y operación

- `GET /api/v1/my-credentials/`: declaraciones activas del usuario autenticado.
- `GET /api/v1/my-course-certificates/`: emisiones del LMS del usuario autenticado.
- `GET /certificados/verificar/<código>/`: validación pública del código LMS y estado vigente.
- `GET /certificados/cursos/<id>/descargar/`: descarga del PDF LMS, limitada al propietario.

La validación pública no escribe una nueva verificación al consultar; la auditoría de decisiones pertenece al flujo privado de revisión.
