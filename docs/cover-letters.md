# Prompt 07 — Cartas de presentación

La colección de cartas usa Tech Modern Blue, con cabecera de privacidad,
navegación por acciones, búsqueda y tarjetas. La base de cartas comparte
`billing.css` y `personal-tech.css` con selectores acotados y tema claro/oscuro.
El editor, las revisiones y las exportaciones conservan sus flujos y condiciones.

## Auditoría y alcance entregado

Se reutilizan la autenticación Django, `can(..., 'view_owned_resource', recurso)`,
el nivel efectivo, los perfiles `CVProfile` del prompt 06, sus instantáneas y
ReportLab, que ya exporta el CV. Se sustituye el enlace sin función «Carta
presentación» del menú Ducky Profile.

No existía un modelo de carta, historial o editor reutilizable. Celery está
instalado, pero no se encontró el contrato de Energía Ducky del 00C. El cliente
OpenAI del tutor está orientado a tutoría y no reserva ni confirma energía.
No se lo invoca desde las cartas.

`PurchasedEntitlement` representa derechos vigentes, y facturación emite una fila
por unidad comprada, pero no ofrece reserva, consumo ni conciliación de esas
unidades. Este módulo no interpreta un código comercial inventado como permiso
para ejecutar IA ni consume derechos directamente.

**Disponible:** creación manual con revisión previa, cartas privadas, selección
de datos, instantáneas, edición, títulos, historial, restauración del contenido
de versiones anteriores mediante una nueva edición, duplicación, archivo,
restauración, eliminación, copia, TXT y PDF.

**No disponible:** generación/mejoras IA, extracción remota de ofertas,
presupuesto energético, reserva de servicios individuales y trabajos IA
asíncronos. La interfaz informa del bloqueo; no genera cartas ficticias como si
fueran resultados reales. No se implementa ATS, envío ni candidaturas.

## Matriz de acceso

| Acción | Visitante | FREE / PLUS / PRO | Roles profesionales / ADMIN | Propiedad / compra |
| --- | --- | --- | --- | --- |
| Demo fija e identificada | Sí | Sí | Sí | Sin datos personales |
| Crear borrador y elegir perfil | Registro necesario | Misma función | Sin privilegio adicional | Perfil propio no archivado |
| Revisar, editar, copiar y descargar | No | Sí, sin energía | Sin acceso especial a otra persona | Exclusivamente propietario activo |
| Duplicar, archivar, restaurar, eliminar | No | Sí, sin energía | No eluden propiedad | Revisión esperada obligatoria |
| Generación IA | No | Integración pendiente | No eluden el bloqueo | Ni plan ni compra activan una integración inexistente |
| Configuración y fallos técnicos | No | No por plan | ADMIN de plataforma autorizado para Django Admin | Metadatos, no contenido privado |

El descenso de plan no oculta cartas guardadas. No se añaden capacidades de
perfiles ni roles al crear una carta. Las acciones normales no llaman a pagos,
IA ni energía. No se inventan descuentos o tarifas.

## Datos y migración

La migración incremental `curriculum.0012_cover_letters` añade:

- `LetterConfiguration`: versión inmutable, disponibilidad de creación manual,
  solicitud de activación IA, instrucciones y límites técnicos.
- `LetterOffer`: texto original, URL de referencia, puesto/empresa aportados,
  método y estado de extracción. Las ofertas pegadas no tienen extracción
  remota: `method=pasted`, `extraction_status=not_requested`.
- `CoverLetter`: UUID, propietario, perfil, oferta, opciones, estado, revisión
  de concurrencia, clave de creación y copia privada revisada.
- `LetterVersion`: contenido de texto plano, número, autor, origen, anterior
  y referencia opcional de operación.
- `LetterGeneration`: operación idempotente bloqueada con código de error,
  versión de configuración y copia controlada de la entrada. No representa
  una ejecución ni un consumo realizado.

Índices: propietario/estado/actualización; claves únicas de creación,
usuario/operación y carta/número de versión. No se alteran cuentas, perfiles,
roles, membresías, compras ni contraseñas.

La semilla de configuración v1 es idempotente. Activa solo el flujo manual.
Límites técnicos iniciales, visibles y administrables mediante una nueva
versión: oferta 24 000 caracteres; carta 40 000 caracteres; 12 guardados u
operaciones por minuto por cuenta. El límite de frecuencia usa registros de
versiones y operaciones, no es una cuota IA ni un saldo. Se aplica en servidor
con bloqueo de usuario y límites compartidos por las peticiones de escritura.
Las notas y hechos adicionales admiten 2 000 caracteres cada uno; la revisión
completa está limitada a 150 000 caracteres serializados. El límite global de
petición de Django también sigue vigente.

La migración es reversible mediante el mecanismo habitual de Django. Como
cualquier reversión que elimina tablas, retroceder a 0011 después de crear
cartas destruye esos documentos: requiere copia de seguridad y autorización.
No es un procedimiento de solución de errores.

## Rutas y contratos

| Método | Ruta | Uso |
| --- | --- | --- |
| GET | `/cv/cartas/demo/` | Demo editorial ficticia pública |
| GET | `/cv/cartas/` | Mis cartas; búsqueda por título y paginación |
| GET / POST | `/cv/cartas/nueva/` | Elegir datos y obtener revisión |
| POST | `/cv/cartas/confirmar/` | Confirmar revisión y guardar borrador |
| GET / POST | `/cv/cartas/<uuid>/` | Editor, guardado e historial |
| POST | `/cv/cartas/<uuid>/accion/duplicate/` | Duplicar como borrador |
| POST | `/cv/cartas/<uuid>/accion/archive/` | Archivar |
| POST | `/cv/cartas/<uuid>/accion/restore/` | Restaurar |
| POST | `/cv/cartas/<uuid>/accion/delete/` | Eliminar contenido privado |
| POST | `/cv/cartas/<uuid>/generar/` | Operación bloqueada explícitamente |
| GET | `/cv/cartas/<uuid>/version/<numero>/pdf/` | PDF de esa versión |
| GET | `/cv/cartas/<uuid>/version/<numero>/txt/` | Texto UTF-8 de esa versión |

La revisión usa un payload firmado y fechado `curriculum.letter-review.v1`,
vinculado al propietario y configuración. Caduca a los 30 minutos. Confirmar
dos veces la misma revisión devuelve el mismo borrador. Cambiar perfil u oferta
después de revisar no cambia la copia autorizada. Un perfil archivado/eliminado
antes de confirmar se rechaza; no se elige otro silenciosamente.

Los datos profesionales autorizados proceden de la instantánea del perfil, no
de documentos Nexus, tests, facturación o conversaciones. Fotografías y enlaces
sociales no se copian. Email y teléfono requieren selección expresa y ser
visibles en ese perfil. Las ausencias se explican y permiten alcance reducido.

El editor recibe `title`, `content`, `revision`. Una escritura o acción con
revisión antigua devuelve 409 sin sobrescribir. La edición fallida conserva el
texto en el formulario y permite copiarlo. Las versiones son solo de adición
desde las rutas del módulo; guardar una versión antigua crea otra, no altera
la anterior. Los números pueden tener saltos porque archivar/restaurar también
incrementa la revisión de concurrencia.

La operación bloqueada recibe `revision` y `idempotency_key` UUID. El fingerprint
`letter.generate.v1:<carta>:<revision>` vincula la clave a la solicitud. El
servidor responde 503:

```json
{
  "ok": false,
  "operation_id": "<uuid>",
  "status": "blocked",
  "error": "energy_integration_pending",
  "energy_cost": null,
  "message": "Generación no disponible..."
}
```

Repetir la clave devuelve la misma operación; reutilizarla para otra revisión
devuelve 409. Una petición inválida devuelve 400 y el control de frecuencia 429.
`null` significa coste desconocido, nunca coste cero. Ninguna cantidad enviada
por el cliente autoriza un gasto. No se registra una reserva ficticia.

El punto de integración local es `generation_availability` en
`services/letters.py`. Incluso activar `generation_enabled` en configuración
no sortea el bloqueo. Para habilitar generación deben conectarse los contratos
reales de presupuesto, reserva, confirmación, liberación y conciliación del 00C
y de consumo unitario del 00B. El futuro despacho Celery debe conservar la
misma identidad de operación y registrar resultado utilizable antes de
confirmar consumo. No se ejecuta un proveedor con coste desconocido.

## URL de oferta

Se admite una URL HTTP/HTTPS pública sin credenciales como referencia, **sin
descargarla**. Se rechazan protocolos no admitidos, hosts locales habituales
y direcciones IP no globales. El texto pegado es obligatorio. Ni siquiera se
resuelve DNS; no se siguen redirecciones ni se accede a sesiones externas.
Una URL aparentemente pública que resuelva a una red privada tampoco genera
una petición porque el conector está deshabilitado para todas las URLs.

Para conectar extracción real faltan validación DNS/IP por conexión y por
redirección, protección contra rebinding, límites de tiempo/tamaño/MIME y
extracción sin scripts. La validación sintáctica actual **no es un extractor
SSRF-safe reutilizable**. No se debe añadir `requests.get` tras ella sin estas
protecciones. La UI explica que no se ha analizado el enlace.

## Privacidad, administración y conservación

Todos los accesos personales filtran por propietario y cuenta activa, incluido
el superusuario. No hay rutas públicas de cartas personales ni adjuntos externos.
Las descargas se generan en memoria y llevan caché `private, no-store` y
`nosniff`. El HTML se escapa; el PDF escapa el texto antes de pasarlo a ReportLab.
La copia usa el portapapeles con alternativa legible por teclado.

Administración:

- `/admin/curriculum/letterconfiguration/`: crear nueva versión, inspeccionar
  configuración. Django registra la creación administrativa en su historial.
- `/admin/curriculum/lettergeneration/`: solo metadatos de errores/estado e
  identificadores, sin oferta, instrucciones personales ni snapshot.

Cartas, ofertas y versiones no se registran en Admin. Un rol profesional no
concede lectura. No se añade una vía extraordinaria de soporte sobre contenido
privado: requeriría permiso/finalidad/auditoría específicos.

No se encontró política central de purga de documentos que reutilizar. No se
inventa un plazo. Archivar conserva el contenido. Eliminar por petición del
propietario elimina versiones/oferta y vacía el snapshot de operaciones
bloqueadas; conserva sus metadatos y no repone ni altera compras/energía.
La limpieza también se aplica a la eliminación en cascada de la cuenta, para
no dejar ofertas o copias privadas huérfanas. Operaciones distintas de `blocked`
impiden eliminar desde la carta hasta conciliación.
No existe compartición ni candidaturas en este apartado. Antes de conectarlas
debe añadirse la protección de versiones compartidas.

## Guía de prueba sin alterar cuentas existentes

Las pruebas automatizadas crean `alumno1`, `alumno2`, `alumno3` y `admin1`
exclusivamente en la base aislada de tests. No cambian usuarios reales.

En desarrollo, inicia sesión normalmente como `alumno1`, sin restablecer su
contraseña ni cambiar su plan:

1. Abre `/cv/cartas/` desde Ducky Profile → Carta presentación.
2. Crea/elige un perfil propio. Usa una oferta ficticia identificada, por ejemplo:
   «Oferta ficticia de apoyo administrativo para organizar documentación y
   atender consultas del equipo de Empresa Ejemplo».
3. En Nueva carta elige idioma, tono, extensión y campos autorizados. Omite
   email/teléfono y comprueba que no aparecen en la revisión.
4. Revisa y confirma para crear un borrador **manual**. No se rellena con una
   carta IA. La generación muestra integración pendiente, sin coste inventado.
5. Escribe texto propio, guarda dos versiones y selecciona la anterior.
   Descarga TXT y PDF de la versión seleccionada. Guardar texto largo con
   «organización, atención, español» conserva caracteres y saltos de página.
6. Abre el editor en dos pestañas. Guarda en una y trata de guardar desde la
   otra: debe mostrar conflicto y conservar el texto no guardado.
7. Prueba copiar, duplicar, archivar/restaurar y, sobre un borrador de prueba,
   eliminar. Ninguna acción consume energía.
8. Con `alumno2`, intenta abrir el UUID o descarga de `alumno1`: 404.
   Con `alumno3`, repite según su nivel efectivo sin modificarlo.
9. Con `admin1`, crea una nueva configuración y consulta los metadatos de
   operaciones; no se ofrece lectura ordinaria de cartas privadas.

Generación/fallo real de proveedor, saldo insuficiente, recuperación,
confirmación/liberación y conciliación **no son verificables end-to-end** hasta
conectar 00C/00B. Sí se prueba que POST a generación devuelve 503, no llama
OpenAI/facturación y repetir la operación no crea otra.

Validación automatizada:

```powershell
.\.venv\Scripts\python.exe manage.py test apps.curriculum.test_letters apps.curriculum.test_profiles apps.curriculum.test_profile_migrations
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
git diff --check
```

La prueba de migración recorre el esquema legado hasta 0012, comprueba
preservación del CV y semilla sin cartas inventadas, y restaura las hojas del
grafo de migraciones en la base de tests.

### Resultado de esta implementación

- 51 pruebas superadas: 30 de cartas, 20 de My CV y una de migraciones.
- `check`: solo el aviso preexistente `urls.W002` del dashboard.
- `makemigrations --check --dry-run`: sin cambios pendientes de modelo.
- `git diff --check`: sin errores de espacios.
- Migración 0012 aplicada en la base local después de una copia SQLite.
  Conservadas 38 cuentas, sus contraseñas y datos, 38 membresías, 51 asignaciones
  de roles y ocho perfiles; solo se observó actividad concurrente en `last_login`.
- Navegador real: demo pública, contenido sin desbordamiento horizontal a
  390 px, foco por teclado y acceso mediante Enter que redirige al login con
  `next=/cv/cartas/nueva/`. El navegador tuvo fallos de carga de algunos recursos
  externos del portal; no se certifica el diseño visual completo de todas las
  pantallas privadas. Sus recorridos sí se verificaron mediante POST de tests.

### Archivos del apartado

- [Modelos](../apps/curriculum/letter_models.py), importados por
  [models.py](../apps/curriculum/models.py).
- [Migración y semilla](../apps/curriculum/migrations/0012_cover_letters.py).
- [Formularios y validación](../apps/curriculum/letter_forms.py).
- [Servicios](../apps/curriculum/services/letters.py).
- [Vistas](../apps/curriculum/letter_views.py) y [rutas](../apps/curriculum/urls.py).
- [PDF compartido](../apps/curriculum/services/pdf.py).
- [Administración](../apps/curriculum/admin.py) y
  [limpieza de contenido privado](../apps/curriculum/signals.py).
- [Pantallas](../templates/curriculum/letters/) y
  [navegación existente](../templates/base.html).
- [Pruebas de cartas](../apps/curriculum/test_letters.py) y
  [compatibilidad de migraciones](../apps/curriculum/test_profile_migrations.py).
