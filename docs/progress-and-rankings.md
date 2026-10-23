# Ducky Me: mi progreso y rankings

`/progreso/` es la vista personal autenticada; visitantes reciben únicamente el
estado de demostración sin datos de actividad personal. `/gamificacion/ranking/`
solo muestra clasificaciones que ADMIN haya publicado y no expone la puntuación
histórica global de la cuenta.

Ambas páginas usan Tech Modern Blue: cabeceras diferenciadas para progreso
privado y participación optativa, tarjetas, filtros y tablas adaptables.
Los estilos compartidos se limitan a `.billing-app.personal-tech`, admiten
tema claro/oscuro y no cambian métricas, consentimiento ni permisos de exportación.

## Progreso personal

`apps/users/progress_services.py` reúne, siempre filtrando por el usuario autenticado:

- temas y cursos completados, y resultados de quizzes;
- sesiones Nexus completadas (resultado personal privado);
- actividad propia registrada e insignias vigentes;
- objetivos personales ya existentes.

Los filtros de fechas usan días de calendario en la zona horaria configurada en
Django. Los datos de juegos y empleo no tienen fuentes integradas: la interfaz los
marca como pendientes en vez de mostrar ceros o valores simulados. La comparación
entre periodos y la descarga CSV requieren PRO; tanto la vista como la exportación
vuelven a comprobar el acceso en servidor. Los datos ajenos nunca forman parte del
resumen ni de la descarga.

## Rankings públicos optativos

La migración `gamification/0007` añade definiciones, reglas versionadas, temporadas,
participación, snapshots, auditoría e incidencias revisables sin reemplazar los
registros históricos de gamificación. Las fuentes habilitadas son matrículas de
cursos completadas y progreso de temas completados, fechados dentro de la temporada.
Las incidencias pendientes o excluidas no puntúan; las marcadas válidas se incluyen.

Para publicar una clasificación, ADMIN debe crear una definición, crear al menos
una `RankingRuleVersion` con la misma métrica, crear una temporada preparada con
fechas y versión de regla, publicar la definición y activar la temporada dentro de
su periodo. Las opciones administrativas de cierre generan el snapshot; un
recálculo posterior requiere ADMIN y motivo obligatorio y queda auditado.

La participación se activa explícitamente por persona y ranking; solo el alias
elegido se presenta públicamente. Retirar el consentimiento oculta a la persona
también en snapshots de temporadas cerradas, aunque el dato histórico de auditoría
se conserva. La retirada está disponible aunque no haya temporadas abiertas. Las
posiciones con la misma puntuación son compartidas. El nivel FREE/PLUS/PRO no
concede participación administrativa ni roles profesionales.

## Límites e integraciones

- El ámbito soportado es público; no se publican rankings por organización, curso
  privado ni grupo.
- Las dos métricas y sus fórmulas son cerradas y verificables; no se admiten
  fórmulas inventadas o fuentes remotas.
- No se garantiza la integridad del dato fuente retrospectivamente; incidencias
  detectadas se revisan y solo un recálculo administrativo explícito actualiza
  temporadas cerradas.
  Las operaciones de activación, cierre y recálculo bloquean la temporada sin unir
  la regla nullable en el `SELECT FOR UPDATE`, para ser compatibles con PostgreSQL.
  El filtro de incidencias compara los identificadores como texto de forma explícita:
  las claves numéricas de progreso/matrículas se convierten al tipo de las referencias
  de incidencia, evitando comparaciones incompatibles entre bigint y varchar.
- El ranking heredado de puntos no se reutiliza como clasificación pública.
- La migración se incorpora al esquema de pruebas; aplícala en cada entorno con
  el procedimiento normal de migraciones Django tras revisar el despliegue.
