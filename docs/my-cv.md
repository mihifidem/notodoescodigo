# My CV: perfiles, catálogo y publicación

El panel del CV, la lista de perfiles y el catálogo de plantillas comparten
Tech Modern Blue: cabecera, acciones, tarjetas y estados, con tema claro/oscuro
y adaptación móvil. Se reutilizan `billing.css` y `personal-tech.css` con
selectores acotados. No se modifica el diseño de los CV publicados, sus PDFs
ni sus plantillas de demostración; tampoco los límites o la publicación privada.

## Rutas

- Catálogo y demostraciones ficticias: `/cv/plantillas/` y `/cv/plantillas/<codigo>/vista-previa/`.
- Perfiles del usuario autenticado: `/cv/perfiles/`.
- Crear un perfil: `/cv/perfiles/crear/`.
- Editar los datos específicos del perfil: `/cv/perfiles/<id>/editar/`.
- Seleccionar contenido existente del CV principal: `/cv/perfiles/<id>/contenido/`.
- Previsualizar, publicar/actualizar, hacer privado, duplicar, archivar y restaurar: acciones en `/cv/perfiles/`.
- Exportar PDF de un borrador o de la versión privada actual: `/cv/perfiles/<id>/pdf/` (solo propietario).
- Administración de plantillas: `/admin/curriculum/profiletemplate/` (solo ADMIN de plataforma).

Los identificadores `<id>` son internos y requieren autenticación y propiedad. El enlace público y su PDF solo funcionan para perfiles publicados.

## Política aplicada

Los perfiles activos (borrador o público) tienen capacidad FREE 1, PLUS 3 y PRO 10. Cada derecho vigente `PurchasedEntitlement` con código `cv-profile-slot` añade una plaza. Este código es un contrato técnico, no un producto o precio creado por esta implementación. Un derecho de plantilla se reconoce con el código `profile-template-<codigo-estable>` y no cambia el nivel de la cuenta. El checkout existente no tiene una ruta de contratación identificable en las rutas inspeccionadas; por ello esta sección no cobra, no crea derechos ni simula compras.

Las cuatro plantillas generales admiten los tres niveles. Las cinco temáticas originales admiten PLUS y PRO, o un derecho individual vigente. Sus demos son públicas y contienen exclusivamente datos ficticios; poder abrir una demo no permite aplicar la plantilla. Si el nivel pierde acceso a una plantilla temática, los datos siguen guardados y tanto web como PDF muestran una alternativa general. El perfil conserva su selección histórica para que pueda recuperarla al volver a disponer del derecho.

Un perfil nuevo se crea privado. La publicación captura una revisión y una instantánea de contenido; editar el borrador no altera la revisión ya publicada. El propietario debe volver a publicar para actualizarla. El historial de revisiones conserva los datos y la versión de plantilla. Archivar conserva el registro y retira el slug público. Los campos visibles específicos del perfil (nombre, titular, presentación, ubicación y contactos) pueden diferir de los datos del CV principal; los campos vacíos usan los valores del CV principal. La selección de entradas reutiliza las entradas del CV principal; la instantánea pública no cambia si se editan esas entradas y solo se actualiza al volver a publicar. Email, teléfono y fotografía permanecen ocultos salvo selección expresa.

Tras un descenso que exceda el número de perfiles públicos permitido, los slugs públicos devuelven 404 hasta que el propietario elija cuáles conservar en la tarjeta «El cambio de nivel supera el límite de publicaciones». Los demás se convierten en borradores privados; no se borran. Los perfiles y ramas de la cuenta siguen separados, y cambiar `User.rama_principal` no reescribe ramas de perfiles.

Los colores deben ser `#RRGGBB` y mantener al menos 4,5:1 de contraste sobre fondo claro. Las plantillas son renderizadores de servidor, no HTML/CSS/JavaScript subidos por usuarios. Las URL publicadas se limitan a HTTP/HTTPS. El PDF backend usa ReportLab: el enlace público exporta la última instantánea publicada; la ruta privada del propietario exporta el estado actual del borrador. Ambos generan texto seleccionable, saltos de página y enlaces.

Las estadísticas PRO muestran cargas de la vista pública registradas en los últimos 30 días, no personas únicas. No se guardan IP ni identidad del visitante; se excluyen las vistas del propietario y las vistas previas. No hay una señal fiable para filtrar bots o identificar visitas de prueba anónimas. Los registros se conservan al cambiar de nivel, aunque solo PRO los ve en la interfaz.

### Energía Ducky e IA

Crear, editar, duplicar, cambiar de rama o plantilla, personalizar, publicar, previsualizar y exportar un CV no consume Energía Ducky. La sugerencia IA de resumen que existía en el editor permanece deshabilitada hasta que el prompt 00C exponga el contrato compartido de reserva/confirmación/liberación; su endpoint responde como no disponible y no envía datos del CV a un proveedor. No se crean aquí contadores ni cuotas IA propios. Cualquier futura generación o mejora IA debe usar únicamente Energía Ducky.

## Añadir una plantilla

Matrix Premium se registra mediante `curriculum.0014_matrix_profile_template`.
Está incluida en PRO y también admite el derecho individual vigente
`profile-template-matrix`. La demo pública usa datos ficticios; la vista previa
personal y la publicación usan exclusivamente la instantánea seleccionada, no
los datos privados del CV principal. Mantiene el diseño, lluvia de caracteres y
secciones interactivas del original, con adaptación móvil y movimiento reducido.
Si se pierde acceso se aplica la alternativa básica existente. Su PDF conserva
el exportador de texto del portal; no reproduce las animaciones del navegador.

1. En Django Admin, crea una fila en **Plantillas de perfiles** con un código estable, una versión superior a las existentes del mismo código, el renderizador registrado, el esquema compatible, los niveles permitidos, las ramas recomendadas/compatibles y el color inicial.
2. Las plantillas disponibles se registran en `PROFILE_TEMPLATE_RENDERERS` en `apps/curriculum/models.py`; las variantes visuales usan la clase `renderer-<código>` de `templates/curriculum/cv_templates/catalog_profile.html`. No se modifica el editor para agregar una variante visual existente.
3. Crea primero la nueva versión en borrador, comprueba la demostración en `/cv/plantillas/<codigo>/vista-previa/` y publícala desde Admin. Las versiones publicadas son inmutables; no cambies permisos, esquema, renderizador ni diseño de una versión que ya usan perfiles. Archiva una versión para impedir selecciones nuevas.
4. Los códigos y versiones fijados en perfiles/revisiones no se deben borrar. Una compra de plantilla o capacidad solo se reconocerá cuando el contrato de compra aprobado emita el `PurchasedEntitlement` con el código documentado.

## Migraciones y verificación

Las migraciones de este apartado son `curriculum.0008` a `curriculum.0011`. Si se aplican, siembran de forma idempotente nueve plantillas, conservan los perfiles existentes, trasladan las elecciones de plantilla heredadas conocidas sin cambiar usuarios y marcan como publicadas las filas que ya eran públicas. No asignan ramas a cuentas existentes. Las migraciones no se han aplicado automáticamente a la base de datos local.

Para verificar con el entorno del proyecto:

```powershell
.\.venv\Scripts\python.exe manage.py test apps.curriculum.test_profiles
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

Usa usuarios y base de datos de pruebas aislados para verificar FREE/PLUS/PRO; no edites cuentas reales ni restablezcas contraseñas para probar niveles.

Las pruebas incluyen POST autenticados de creación, duplicación, publicación y ajuste de publicaciones, además del límite FREE. Los bloqueos transaccionales consultan el modelo de usuario configurado con `get_user_model()`, compatible con el usuario diferido (`SimpleLazyObject`) que Django proporciona en `request.user`.
