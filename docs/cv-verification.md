# Prompt 08 — Verificar CV

La colección de análisis usa Tech Modern Blue con cabecera de revisión
orientativa, navegación, búsqueda y tarjetas de informes privados. La base
comparte `billing.css` y `personal-tech.css` con selectores acotados, adaptación
móvil y tema claro/oscuro. Se mantienen los avisos de limitaciones, revisiones,
formularios y reglas de procesamiento existentes.

## Auditoría y reutilización

Se conservan Django 4.2, autenticación, `can`, `effective_plan`, derechos vigentes
`PurchasedEntitlement`, perfiles `CVProfile` y revisiones `CVProfileRevision`.
Las secciones reconocibles reutilizan `SECTION_PATTERNS` del importador de CV.
ReportLab es el generador existente; se extrae `build_text_pdf` de la exportación
de cartas para compartirlo sin cambiar el resultado del prompt 07.

El importador existente aplica campos al CV y recorta texto/páginas; no se
invoca para analizar documentos. La verificación usa el mismo extractor pypdf
con procesamiento separado, límites explícitos y revisión del texto sin
modificar perfiles. No se reconstruyen My CV, cartas, compras o energía.

No se encontró un contrato de Energía Ducky 00C ni consumo/reserva de unidades
de servicios 00B. El cliente OpenAI de tutoría no tiene ese contrato y no se
invoca desde aquí. Celery existe, pero no se despachan trabajos IA de coste
desconocido. La lectura remota del prompt 07 sigue no disponible; se reutiliza
solo su validación de URLs **de referencia**, sin realizar peticiones.

## Alcance y matriz de acceso

| Acción | VISITANTE | FREE | PLUS | PRO | Derecho individual |
| --- | --- | --- | --- | --- | --- |
| Demo fija con datos ficticios | Sí | Sí | Sí | Sí | No necesario |
| Perfil propio / PDF / texto revisado | Registro | Sí | Sí | Sí | No necesario |
| Reglas, avisos importantes, resumen, historial, PDF resumen | No | Sí | Sí | Sí | No necesario |
| Desglose por requisito, evidencia y PDF detallado | No | No | Sí | Sí | Código de acceso vigente configurado |
| Priorización determinista, comparación y PDF comparativa | No | No | No | Sí | Código de acceso vigente configurado |
| Análisis semántico IA | No | Integración pendiente | Integración pendiente | Integración pendiente | No sortea el bloqueo |
| Reformulación IA | No | No | No | Integración pendiente | Acceso compatible y contrato energético pendiente |
| Lectura/descarga de informes ajenos | No | No | No | No | Tampoco por rol profesional/ADMIN |

La calidad de cada regla es idéntica. FREE no oculta avisos de fechas, formato,
extracción o secciones no identificadas; incluye requisitos sin evidencia y
un resumen de coincidencias. PLUS añade el desglose íntegro y evidencias.
PRO ordena primero avisos comprobables y después observaciones informativas;
no añade una fórmula global de empleabilidad.

El alcance de un informe completado se guarda, por lo que perder el plan no
borra ni bloquea el resultado ya obtenido. Nuevos análisis y comparaciones
revalidan derechos en servidor. Los roles no conceden acceso a otra cuenta.

## Datos y migración

`curriculum.0013_ats_verification` es incremental y reversible:

- `ATSConfiguration`: configuración inmutable, versión de criterios, reglas,
  tamaños, páginas, tiempo y frecuencia; códigos de derechos opcionales.
- `ATSSource`: propietario, fuente, perfil/revisión, huella original, método,
  texto extraído, limitaciones y PDF original privado opcional.
- `ATSReport`: propietario, texto autorizado exacto, huella, oferta/procedencia,
  alcance y fundamento de acceso, criterios, hallazgos, cobertura, limitaciones,
  estado, archivo lógico y clave idempotente.
- `ATSComparison`: referencias concretas a dos informes y resultado conservado.
- `ATSOperation`: metadatos de intentos IA bloqueados y extracción PDF
  (`task=extract`, estado en ejecución/completado/fallido), sin copiar documentos.

Hallazgos y requisitos son resultados JSON versionados, no tablas duplicadas
innecesarias. Se guardan procedencia, evidencia, descripción, prioridad,
recomendación y limitaciones. No se almacenan puntuaciones de contratación.

Índice de historial por propietario/fecha. Restricciones únicas por
usuario/clave de informe, usuario/clave de operación y usuario/par de comparación.
La semilla v1 es idempotente, con `sections`, `contacts`, `links`, `dates`.
No crea usuarios, membresías, compras, CV ficticios ni informes personales.

Revertir esta migración elimina las tablas y documentos del apartado: solo debe
hacerse con autorización y copia de seguridad, no como reparación rutinaria.

## Formatos, extracción y privacidad

Formatos reales: PDF con texto, perfil NTEC y texto pegado. No DOCX ni OCR.
Un PDF sin texto se conserva como fuente pendiente con avisos y permite pegar
una transcripción; no se interpreta como ausencia de experiencia.

Límites técnicos de configuración, heredando el techo existente del importador:

- Un PDF por petición; extensión `.pdf`, MIME PDF/octet-stream y cabecera `%PDF-`.
- 5 MiB por archivo como máximo (se puede configurar un límite menor).
- 30 páginas como máximo; exceso rechazado, no ocultado por truncamiento.
- 100 000 caracteres como máximo; truncamiento identificado como parcial.
- 15 segundos de extracción por defecto, configurable hasta 30.
- 2 MiB de contenido descomprimido por flujo de página antes de extraer texto.
- Oferta pegada de hasta 24 000 caracteres y 100 líneas evaluables. Un exceso
  de líneas marca cobertura parcial explícita.
- Control de frecuencia técnico: seis creaciones de fuente/informe/intentos IA
  por cuenta y minuto por defecto. No es una cuota energética ni de documentos;
  no hay límite comercial total de análisis inventado.

Los intentos de extracción PDF se registran antes de procesar, incluidos los
fallidos: repetir un PDF corrupto o que agote el timeout no elude el control de
frecuencia. Una extracción convencional nunca se confunde con una operación IA
ni reserva energía. Solo se guardan códigos de fallo, no el documento en logs.

pypdf se ejecuta en un proceso separado con timeout, sin shell, sin ejecutar
scripts, sin seguir enlaces ni acceder a recursos externos. Se rechazan PDF
protegidos, corruptos, sin páginas, acciones automáticas, scripts incrustados,
adjuntos, formularios activos y anotaciones con acciones ejecutables. Algunas
estructuras PDF no compatibles se rechazan con un error legible.

El límite de tiempo protege al proceso web de extracciones largas. Este
subproceso **no es un sandbox de seguridad ni impone un límite duro de memoria
del sistema operativo**; para exposición a cargas masivas/no confiables sigue
siendo recomendable un trabajador aislado con memoria limitada y concurrencia
acotada en infraestructura. No se promete análisis antivirus.

Los originales se guardan como binario acotado en la base privada, no en
`MEDIA_URL`, sin nombres proporcionados por el usuario ni URL pública. Se
descargan como adjunto con UUID fijo, propietario, `private, no-store`,
`nosniff` y CSP sandbox. El texto revisado se guarda en el informe, separado
del original; nunca se llama al importador que modifica el CV.

No se copian automáticamente Nexus DNA, tests, certificados, conversaciones,
facturación ni fotografías. Para perfiles, el usuario elige secciones; email
y teléfono necesitan selección y visibilidad en el perfil. Una revisión
publicada utiliza su snapshot, no el borrador actual.

## Reglas implementadas y límites

`text-rules-v1`:

1. Encabezados reconocibles de presentación, experiencia, formación y
   competencias mediante patrones existentes. «No identificada» no significa
   inexistente. No se penaliza automáticamente falta de experiencia.
2. Presencia de email en datos autorizados y formato de posibles emails.
   No se obliga a incluir edad, foto, estado civil u otros datos sensibles.
3. Formato HTTP/HTTPS de enlaces escritos; no se abre el destino.
4. Validez y orden de intervalos ISO `AAAA-MM-DD - AAAA-MM-DD` (también
   separadores `a`, `to`, `hasta`, guion largo). No se infiere cronología completa.
5. Texto breve y caracteres ilegibles: cobertura limitada, no juicio profesional.
6. Oferta por líneas/semicolon: términos normalizados, coincidencia literal,
   clasificación solo si aparecen marcadores obligatorios/deseables explícitos.
   Líneas con atributos sensibles reconocibles se excluyen de la comparación.

Estados de requisitos: términos encontrados, coincidencia parcial a revisar,
sin evidencia textual, no evaluable. Palabras encontradas no acreditan dominio
y ausencias no demuestran incumplimiento. No hay análisis semántico, inferencia
de competencias, validación visual, probabilidad ni garantía ATS.

Las recomendaciones son reglas editoriales vinculadas a observaciones, no
experiencia ni resultados inventados. No se aplican cambios automáticamente.

## Energía, compras e IA

Subir, extraer, revisar, ejecutar reglas, comparar determinísticamente, consultar
y descargar no consumen energía ni llaman a facturación.

`request_ai` es un límite explícito no disponible. Recibe `idempotency_key` UUID
y `task=semantic|rephrase`; comprueba propietario y alcance antes de crear una
operación `blocked`. Devuelve 503 con `energy_cost: null`, código
`energy_integration_pending` y motivo legible. Repetir la operación conserva su
identidad; cambiar informe/tarea con la misma clave devuelve conflicto.
No se reserva ni libera energía ficticia, no se simula éxito y no se reescribe
el informe convencional como si incluyera IA.

La configuración admite códigos opcionales `detail_entitlement` y
`comparison_entitlement`. Inicialmente están vacíos: no se inventan productos,
precios ni códigos comerciales activos. Se resuelven por `can/access_entitlement`
del 00B: propietario, revocación, comienzo y caducidad.

**Solo deben asociarse derechos de acceso por vigencia**, no unidades de un
análisis de un solo uso. Reservar/consumir cantidades compradas para prestaciones
unitarias sigue pendiente del contrato común 00B/00C; este módulo no transforma
una unidad en generaciones ilimitadas ni cambia su saldo. No hay doble cobro.
La configuración de códigos es una decisión comercial explícita de ADMIN.

Para habilitar IA faltan presupuesto aceptado, proveedor aprobado, reserva,
despacho asíncrono idempotente, validación de resultado utilizable, confirmación,
liberación y conciliación del 00C/00B. Fallos y recuperación de consumo real no
se certifican aquí. Tener energía nunca sustituirá el permiso PRO para reformular.

## Comparativas y conservación

Se comparan informes concretos del mismo perfil o archivos/textos que el
usuario identifica expresamente como versiones del mismo CV. La oferta y URL
de procedencia deben coincidir exactamente. La versión de criterios y reglas
activas deben ser compatibles; la interfaz muestra ambas configuraciones y
sus coberturas/limitaciones.

Se muestran observaciones no detectadas ahora, nuevas, pendientes y evidencias
textuales añadidas/retiradas. Ninguna diferencia implica automáticamente mejora
o peor CV. La comparación queda guardada y se puede volver a descargar sin IA.

Archivar no borra contenido. Eliminar un informe elimina su comparativa y deja
intactos otros informes de la fuente; al borrar el último, elimina también
fuente/PDF. Nunca elimina el perfil original. Los intentos IA bloqueados conservan
solo metadatos con referencia nula; operaciones no conciliadas impedirían borrar.
Eliminar una cuenta elimina sus fuentes privadas mediante relaciones en cascada.

No se encontró una política central de purga que aplicar; no se inventan plazos.
No existe compartición ni envío/candidaturas desde este módulo. Antes de conectar
compartición hay que proteger y gestionar las referencias a versiones enviadas.

Administración de configuración y metadatos:

- `/admin/curriculum/atsconfiguration/`
- `/admin/curriculum/atsoperation/`

Se reutiliza la política de ADMIN de plataforma. Configuraciones nuevas quedan
auditadas por Django Admin. Fuentes, informes y comparativas privadas no se
registran en Admin. El acceso excepcional a contenido sigue sin habilitar:
requiere un contrato específico de permiso/finalidad/auditoría.

## Rutas

| Ruta | Método y finalidad |
| --- | --- |
| `/cv/verificar/demo/` | GET, demo pública ficticia |
| `/cv/verificar/` | GET, Mis análisis, búsqueda y paginación |
| `/cv/verificar/nuevo/` | GET/POST, crear fuente propia |
| `/cv/verificar/fuente/<uuid>/` | GET/POST, revisar/corregir y confirmar |
| `/cv/verificar/fuente/<uuid>/original/` | GET, PDF privado autorizado |
| `/cv/verificar/fuente/<uuid>/descartar/` | POST, descartar fuente pendiente |
| `/cv/verificar/informe/<uuid>/` | GET, informe |
| `/cv/verificar/informe/<uuid>/pdf/summary/` | GET, PDF resumen |
| `/cv/verificar/informe/<uuid>/pdf/full/` | GET, PDF completo autorizado |
| `/cv/verificar/informe/<uuid>/ia/` | POST, dependencia IA explícitamente bloqueada |
| `/cv/verificar/informe/<uuid>/accion/archive/` | POST, archivar |
| `/cv/verificar/informe/<uuid>/accion/restore/` | POST, restaurar |
| `/cv/verificar/informe/<uuid>/accion/delete/` | POST, eliminar |
| `/cv/verificar/comparar/` | GET/POST, elegir informes compatibles |
| `/cv/verificar/comparativa/<uuid>/` | GET, comparación guardada |
| `/cv/verificar/comparativa/<uuid>/?download=pdf` | GET, descargar comparación |

La confirmación recibe únicamente texto revisado, oferta, URL de referencia,
alcance disponible, consentimiento y UUID idempotente. Repetir la misma clave
y contenido recupera el mismo informe; otro contenido con esa clave se rechaza.
Todas las rutas personales comprueban cuenta activa y propietario en servidor.
Las acciones de escritura requieren POST y CSRF.

## Guía de verificación con cuentas existentes

No se modifican usuarios reales ni contraseñas/membresías para probar.

1. Como `alumno1`, abre Ducky Profile → Verificar CV → Nuevo análisis.
   Elige un perfil propio y campos autorizados, o un PDF ficticio con texto.
2. Revisa el texto antes de confirmar. Modifica un fragmento y verifica que
   el original descargable y My CV permanecen intactos.
3. Sin oferta, el informe debe indicar que no evalúa afinidad.
   Con oferta ficticia, usa líneas como «Obligatorio: Python»,
   «Deseable: SQL», «Imprescindible: Kubernetes».
4. Prueba PDF vacío/escaneado: muestra aviso y permite pegar texto.
   PDF protegido, corrupto, excesivo o activo se rechaza explícitamente.
   No hay OCR ni lectura remota por URL.
5. Descarga resumen. Como `alumno3`, verifica las prestaciones de su **nivel
   efectivo actual**, sin cambiarlo. PLUS/PRO ven PDF completo; solo PRO o
   derecho compatible puede crear comparaciones.
6. Para comparar, crea dos informes con la misma oferta y reglas. Usa el mismo
   perfil o confirma la identidad de archivos/textos. Cambiar la oferta debe
   impedir comparar afinidad como si fuera equivalente.
7. Como `alumno2`, intenta abrir UUIDs/descargas de `alumno1`: 404.
8. Como `admin1`, consulta configuración y metadatos, no CV privados.
9. Archiva, restaura o elimina solo documentos de prueba. Comprueba que no
   se modifica el perfil ni se repone energía.

Los tests crean `alumno1/2/3` y `admin1` en base aislada, PDF con texto, vacío,
activo, protegido y multipágina. Se comprueban permisos, compras de acceso,
idempotencia, reglas, PDF, privacidad, límites, timeout y compatibilidad previa.

```powershell
.\.venv\Scripts\python.exe manage.py test apps.curriculum.test_ats apps.curriculum.test_profiles apps.curriculum.test_letters apps.curriculum.test_profile_migrations
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
git diff --check
```

## Archivos

- [Modelos](../apps/curriculum/ats_models.py), importados en
  [models.py](../apps/curriculum/models.py).
- [Migración 0013](../apps/curriculum/migrations/0013_ats_verification.py).
- [Formularios](../apps/curriculum/ats_forms.py) y [vistas](../apps/curriculum/ats_views.py).
- [Servicios y permisos](../apps/curriculum/services/ats.py),
  [reglas](../apps/curriculum/services/ats_rules.py) y
  [extractor PDF separado](../apps/curriculum/services/ats_pdf_worker.py).
- [PDF compartido](../apps/curriculum/services/pdf.py),
  [rutas](../apps/curriculum/urls.py) y [administración](../apps/curriculum/admin.py).
- [Pantallas](../templates/curriculum/ats/) y [menú](../templates/base.html).
- [Pruebas ATS](../apps/curriculum/test_ats.py) y
  [pruebas de migración](../apps/curriculum/test_profile_migrations.py).

## Resultado de la verificación

- Suite conjunta: 91 pruebas superadas (40 ATS en ese punto y 51 de perfiles,
  cartas y migraciones). Tras añadir el caso de frecuencia de PDF fallido,
  la suite final ATS de 41 pruebas también pasó.
- Prueba enfocada adicional de la etiqueta/revisión histórica: superada.
- `check`: solo el aviso preexistente `urls.W002` del dashboard.
- `makemigrations --check --dry-run`: sin diferencias de modelos.
- `git diff --check`: sin errores de espacios.
- Pylance: sin errores de sintaxis en servicios, vistas y formularios ATS.
- Migración 0013 aplicada en SQLite local después de copia de seguridad.
  Comparación con esa copia: conservadas 38 cuentas, 38 membresías, 51
  asignaciones de roles, ocho perfiles, una carta y sus dos versiones; no se
  modificaron sus filas ni los derechos comprados.
- Navegador real: demo pública, foco en «Nuevo análisis», sin desbordamiento del
  contenido a 390 px y acceso directo anónimo redirigido al login conservando
  `next=/cv/verificar/nuevo/`. La comprobación de activación con Enter no pudo
  confirmarse en la herramienta; no se declara validación completa de teclado
  ni del diseño de pantallas privadas. Los formularios, informes y comparativas
  privadas se verificaron mediante peticiones autenticadas de tests.
- No se han ejecutado proveedor IA, OCR, cobros, envíos, candidaturas ni acceso
  a ofertas remotas. Sus integraciones siguen explícitamente pendientes.
