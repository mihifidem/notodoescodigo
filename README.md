# MihiTraining — Plataforma Educativa SaaS

Plataforma web educativa completa con gamificación, cursos, certificados, IA tutor y marketplace de recompensas.

## Stack

- **Backend:** Django 4.2 + Django REST Framework
- **Auth:** django-allauth (usuario/contraseña + Google OAuth)
- **BD:** SQLite (dev) / PostgreSQL (prod)
- **Cache/Queue:** Redis + Celery
- **PDF:** ReportLab
- **IA:** OpenAI GPT-4o
- **Frontend:** Bootstrap 5

## Estructura de Apps

| App | Responsabilidad |
|-----|----------------|
| `users` | Modelo de usuario personalizado, perfiles, notificaciones |
| `courses` | Cursos, temas, inscripciones, progreso, quizzes |
| `gamification` | Insignias, logros, misiones, streaks, niveles |
| `rewards` | Marketplace, recompensas, historial de canjes |
| `analytics` | Dashboard, actividad, rutas de aprendizaje |
| `certifications` | Certificados con generación de PDF y validación |
| `ai_tutor` | Chatbot tutor con historial de conversación |
| `api` | Router DRF unificado + docs Swagger |
| `lms` | Campus LMS: aulas, secciones, membresías, permisos por curso y auditoría |
| `updates` | Próximas actualizaciones, gestionadas desde Django admin |
| `prompts` | Biblioteca pública y registro privado de prompts internos de IA |

## Precios en la home

Las secciones de precios de la home, **Elige tu plan según tu etapa** y
**Precio transparente y sin sorpresas**, muestran PLUS/PRO a partir de la
`BillingConfiguration` activa: importe y moneda por mes, semestre y año.
Los cambios de configuración activa se reflejan en la siguiente visita.
Los borradores y las versiones retiradas no se publican; si no existe tarifa
se muestra «No disponible», sin sustituirla por precios fijos. FREE sigue siendo
gratuito. Los botones llevan al flujo existente de membresías, sin alterar cobros.

## Prompts internos de IA

El modelo `prompts.InternalPrompt` centraliza las plantillas utilizadas por
Tutor IA, redacción e imágenes del Blog, avatares Ducky y corrección de
evaluaciones (sistema, reglas y formato de respuesta).
Se gestiona en **Django admin → Prompts → Prompts internos de IA**:
`/admin/prompts/internalprompt/`, con los permisos estándar del modelo.
No se publica en la biblioteca de usuarios ni en una API pública.

Aplicar `python manage.py migrate prompts` antes de desplegar los consumidores.
La migración carga las diez plantillas originales e importa todas las versiones
de `evaluations.PromptCorreccion`, conservando contenido, activación y versión.
Si hay versiones repetidas del mismo ámbito, añade `-legacy-ID` para conservarlas.
El modelo anterior se mantiene como archivo histórico de solo lectura en admin.
La reversión de la migración de datos no borra ni restablece textos editados.

Cada registro contiene clave interna, título, descripción, versión, texto,
variables, estado y fechas. Solo puede existir una versión activa por clave;
para cambiarla, desactiva la anterior y activa la nueva. Las claves y las
variables existentes son contratos del código, por eso son de solo lectura en
admin. Los cambios de texto se aplican a las nuevas solicitudes, sin reiniciar
el servidor; para conservar historial crea una versión nueva en vez de editar
la anterior. Las variables se escriben como `{nombre}`; los registros sin
variables conservan las llaves literales (por ejemplo, los esquemas JSON).
No se guardan aquí conversaciones, respuestas, datos personales ni claves API.

Las claves de corrección `evaluations.system.TIPO_PRUEBA` tienen prioridad
sobre `evaluations.system.DEFAULT`; si no hay ninguna activa, se usa
`evaluations.fallback.OBJ_TEST` o `evaluations.fallback.default`. La corrección
continúa auditando el texto y la versión utilizados. Las claves
`evaluations.rules` y `evaluations.precomputed_rules` contienen listas JSON de
textos y `evaluations.response_schema` un objeto JSON.
Si falta una plantilla requerida o es inválida, se comunica un error explícito;
no se recupera silenciosamente una copia hardcoded.
Los mensajes aportados por usuarios al endpoint genérico de IA y los enunciados
de preguntas no son plantillas internas y no se incorporan a este catálogo.

## Próximas actualizaciones

En Django admin, **Próximas actualizaciones** permite crear, consultar, editar y
eliminar actualizaciones con referencia manual única, título, descripción,
equipo responsable (texto libre), fecha de inicio, fecha de fin y estado:
**Análisis**, **Desarrollo**, **Test** o **Producción**. Todos los campos son
obligatorios; el estado inicial es Análisis y la fecha de fin debe ser igual o
posterior a la de inicio. El listado permite buscar y filtrar los registros.
El acceso utiliza los permisos estándar de Django admin; no se añade una página
pública ni un enlace al menú lateral. El dashboard del administrador incluye un
acceso rápido a **Próximas actualizaciones**, visible para quienes tienen permiso
de consulta o modificación del modelo. Aplicar la migración con
`python manage.py migrate updates` antes de utilizarlo.
La portada de Django admin (`/admin/`) también incluye el bloque destacado
**Próximas actualizaciones · Cosas pendientes**, con acceso a la lista y,
si se dispone de permiso de creación, a añadir una actualización.

El enlace **Tech DNA (Hard skills)** del portal abre
`/actualizaciones/tech-dna/` para usuarios autenticados. Muestra la actualización
con referencia `DPR_001` (no depende de su ID): título, descripción, estado y
fechas, sin el equipo responsable. Los cambios desde admin se reflejan en la
página; si no existe la referencia devuelve 404. Las demás actualizaciones no
se exponen en esta vista.
El enlace **Formación HUB** abre `/actualizaciones/formacion-hub/` y muestra
la referencia `DAC_002` con los mismos campos y restricciones, sin equipo.
Los botones contiguos **DuckyShop** y **Tech2Hand** de la barra superior abren
`/actualizaciones/duckyshop/` (referencia `DSH_003`) y
`/actualizaciones/tech2hand/` (referencia `DSH_004`), respectivamente,
con la misma presentación, autenticación y exclusión del equipo responsable.
Los cinco enlaces de **Ducky Jobs** muestran también actualizaciones por
referencia: Empleos para ti (`DJO_011`), Impulsa tu carrera (`DJO_012`),
Entrena tu entrevista (`DJO_013`), Explora oportunidades (`DJO_014`) y
Descubre empresas (`DJO_015`), sin equipo responsable. El acceso al currículum
continúa disponible desde Ducky Profile.

## Campus LMS

El LMS se integra con el usuario ya configurado en `AUTH_USER_MODEL` y reutiliza el acceso existente de django-allauth. Consulta [la guía del LMS](docs/lms.md) para permisos, URLs y límites funcionales de esta primera entrega.

## Entrada de Ducky Arenas

El lobby muestra el Ducky actual, su nivel, rango, rating, monedas y progreso de
experiencia. Selecciona Quiz, Battle, Challenge o Escape y pulsa **Jugar** para
abrir la arena elegida. La selección no cambia el personaje ni el equipamiento;
el perfil y el inventario conservan sus accesos propios. Sin JavaScript, cada
arena mantiene un enlace directo para entrar.

Las características se muestran como **ATK** (ataque), **DEF** (defensa),
**LOG** (lógica), **SPE** (velocidad) e **INT** (inteligencia), con los bonos del
equipamiento. INT sustituye la etiqueta Energía; se conserva la clave interna
`energy` para mantener los valores y la compatibilidad de perfiles y objetos.

Battle permite negociar [retos directos y públicos](docs/battle-challenges.md)
con caducidad, aceptación y contrapropuestas. **DuckyClash** tiene acceso desde
la navegación lateral, bandejas privadas, Mis duelos, sala de espera y resultados.
«Historial y estadísticas» muestra resultados, preguntas, daño, objetos y
DuckyCoins netas **confirmadas**, con filtro y desglose por categoría. Los
avisos reutilizan la bandeja existente y no se duplican al reintentar. Los
resultados actualizan una sola vez el perfil de Arenas existente, sin modificar
su saldo. El Admin permite gestionar reglas con permiso explícito y auditoría;
cada duelo conserva las reglas vigentes al aceptar la oferta.
El Ring solo se habilita en la fecha pactada con dos reservas verificadas.
El motor es autoritativo; los pagos requieren el adaptador bancario real configurado.
El Ring web combina una escena Canvas con controles HTML accesibles: dos patos
frontales de cuerpo entero, paisaje, HP, pregunta, reloj e inventario. Las
animaciones reflejan eventos y resultados confirmados por Django, sin decidir
daños, consumos ni victorias. Se incluyen sprites originales provisionales y un
fondo alternativo cuando no hay imágenes; no se requiere Pygame en el navegador.

La app `duckyclash` añade una base compatible en `/clash/`: ofertas versionadas,
acuerdos, duelos, participantes, preguntas y referencias económicas. Reutiliza
el reto de Arenas mediante un proxy, sin otro usuario ni perfil. Permite buscar
un rival y negociar retos privados, con historial de fecha/hora/apuesta,
notificaciones y consulta de saldo mediante adaptador. En `/clash/open/` se
publican retos abiertos con vencimiento explícito, filtros y aceptación directa
por un único rival. Celery Beat persiste la caducidad de retos públicos y privados; el panel excluye los
vencidos aunque el job no se ejecute. El admin operativo es
de consulta. Un flujo económico persistente permite reservas, compensación,
devoluciones y pago único mediante un adaptador DuckyBank configurable. No se ha
localizado un contrato bancario externo real: sin él no se simulan movimientos.
El Banco del Conocimiento reutiliza el servicio real del LMS, con aprobación
explícita de versiones para Clash, categorías/subcategorías, preguntas compartidas
por ronda y corrección exclusivamente servidor. Las pantallas y el JSON no
incluyen soluciones ni claves de validación. La API externa y un proveedor de
distractores certificados no se han localizado; Multiplicador permanece no
disponible, sin generar alternativas ficticias. `DuelEngine` coordina el combate
en Django: desbloqueo programado, dos confirmaciones de preparación, cuenta atrás
de tres segundos, preguntas, resolución y siguientes rondas. Los plazos,
reconexiones y eventos ordenados dependen del servidor, no del reloj del navegador.
Los seis consumibles reutilizan `ArenaItem` y `UserArenaInventory`, con consumo
atómico, recibos idempotentes y efectos verificados en Django. EcoMotor dispone
de un contrato configurable y una política **fake exclusiva de desarrollo y
pruebas**, nunca habilitada por defecto. La resolución de ronda captura ambas
respuestas, aplica ambos daños conjuntamente, audita HP y entrega el resultado
a la saga bancaria existente; no hace transferencias directamente.
Los ataques se preparan durante la cuenta atrás y las defensas durante la
pregunta. Celery Worker y Beat deben estar activos; el Ring recupera el estado
mediante WebSocket privado por duelo, con snapshots personalizados y
recuperación HTTP. Se reutilizan Channels, ASGI y autenticación de sesión;
la capa de canales existente es en memoria y la recuperación de cambios del
worker usa también snapshots periódicos. El flujo con apuestas reales continúa bloqueado
hasta configurar los contratos reales DuckyBank y EcoMotor.
Consulta los límites y la migración en [la guía de retos](docs/battle-challenges.md).

## Instalación

```bash
# 1. Clonar y crear entorno virtual
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar variables de entorno
copy .env.example .env
# Editar .env con tus valores

# 4. Migraciones
python manage.py migrate

# 5. Crear superusuario
python manage.py createsuperuser

# 6. Cargar datos iniciales (insignias, misiones, etc.)
python manage.py loaddata fixtures/initial_data.json

# 7. Lanzar servidor de desarrollo
python manage.py runserver
```

### Migración del catálogo Ducky Arenas en PostgreSQL

En `ducky_arenas.0009`, el campo temporal `HistoricalEra.slug` se añade sin
índice; después de poblar el catálogo se aplica su definición final única.
Esto evita crear dos veces el índice PostgreSQL `varchar_pattern_ops`.
Si la migración se detuvo con
`ducky_arenas_historicalera_slug_ac1319db_like ya existe`, actualiza el código
y repite `python manage.py migrate`. No borres el índice ni uses `--fake`:
la migración es atómica y el intento fallido se revierte en PostgreSQL.

## Ejecutar Celery (tareas asíncronas)

```bash
# Worker
celery -A config worker -l info

# Beat scheduler (tareas periódicas)
celery -A config beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

## API Docs

Con el servidor corriendo, visita:
- Swagger UI: http://localhost:8000/api/v1/docs/
- ReDoc: http://localhost:8000/api/v1/redoc/

## Variables de Entorno

Ver `.env.example` para la lista completa de variables requeridas.
# Categorias al crear prompts

En Prompts > Crear, los administradores con acceso al formulario pueden abrir
«Crear categoria» y «Crear subcategoria». Se guardan junto con el prompt:
selecciona una categoria existente o indica una nueva, y utiliza una
subcategoria existente de esa categoria o crea otra. Los nombres duplicados
y las subcategorias de otra categoria se rechazan sin guardar datos parciales.
