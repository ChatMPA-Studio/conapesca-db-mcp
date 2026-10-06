-- Propuesta de migración de índices para conapesca_landings_historical
-- ============================================================================
-- NOTA: para ejecutar esto usar scripts/migrate_indexes.py (ver
-- docs/index_migration_runbook.md), no este archivo a mano. Probado contra
-- MySQL 8.0: el FULLTEXT (punto 1) FALLA con LOCK=NONE (error 1846) y los
-- índices existentes usan prefijos (ej. nombre_estado(19)) que este archivo
-- no replica; el script maneja ambas cosas. Este archivo queda como referencia.
-- ============================================================================
-- NO EJECUTAR AUTOMÁTICAMENTE. Este archivo NO corrió contra ninguna base —
-- es una propuesta lista para revisar y correr statement por statement,
-- manualmente, cuando el equipo decida. Generado a partir de evidencia REAL
-- (no hipótesis): ver docs/index_audit_output.md, corrido el 2026-09-23
-- contra la RDS de producción (conapesca, 12,439,784 filas, 8.5GB).
--
-- Requiere un usuario con privilegio ALTER/INDEX (ej. el "admin" del .env),
-- no alcanza con el usuario de solo lectura mcp_conapesca_ro. Para correr un
-- statement:
--   mysql --ssl-mode=REQUIRED -h <host> -u admin -p conapesca
--   (o vía chatmpa-data-hub/scripts/connect_rds.py, que hoy solo permite
--   SELECT -- habría que abrir esa restricción a propósito para esto, no
--   por accidente)
--
-- Por qué ALGORITHM=INPLACE, LOCK=NONE en cada uno: si MySQL no puede hacer
-- el cambio sin bloquear lecturas/escrituras, la sentencia falla de
-- inmediato con un error -- no se queda a medias, no bloquea la tabla ni el
-- MCP en producción. Si alguno de estos falla por esa razón, NO reintentar
-- sin la cláusula (eso oculta el problema) -- significa que ese cambio
-- puntual necesita una ventana de mantenimiento, decisión del equipo, no
-- algo que este archivo decida solo.
--
-- Cada ALTER es independiente: se pueden correr en cualquier orden, o solo
-- algunos, sin que los otros dependan de eso.
-- ============================================================================


-- ============================================================================
-- 1) PRIORIDAD ALTA -- especie LIKE '%...%' hace full table scan
-- ============================================================================
-- Evidencia real (docs/index_audit_output.md, sección "get_taxonomy('camaron')"):
--   type=ALL, possible_keys=None, rows=12439784, Extra="Using where; Using temporary"
-- O sea: escanea las 12.4M filas completas en cada búsqueda por nombre de
-- especie. Ya existe idx_especie sobre nombre_especie y NO ayuda en nada
-- acá -- es la prueba en datos reales de por qué un índice B-tree normal
-- nunca sirve contra un LIKE con wildcard al inicio, exista o no (ver
-- docs/rds_index_recommendations.md sección 3). Necesita un mecanismo
-- distinto, no un índice distinto.

ALTER TABLE conapesca_landings_historical
  ADD FULLTEXT INDEX ft_especie_cientifico (nombre_especie, nombre_cientifico),
  ALGORITHM=INPLACE, LOCK=NONE;

-- Este ALTER por sí solo NO cambia nada para la app -- crea el índice pero
-- nadie lo usa hasta que el código de la tool cambie. Falta, en
-- tools/data_access.py (_get_landings_sync y _get_taxonomy_sync):
--   ANTES: "(nombre_especie LIKE ? OR nombre_cientifico LIKE ?)"
--          con params [f"%{especie.upper()}%", f"%{especie.upper()}%"]
--   DESPUÉS: "MATCH(nombre_especie, nombre_cientifico) AGAINST (? IN NATURAL LANGUAGE MODE)"
--          con params [especie]  (FULLTEXT ya es case-insensitive y
--          tokeniza solo, se cae el .upper()/%-wrapping)
-- OJO -- cambio de semántica, no solo de sintaxis: NATURAL LANGUAGE MODE
-- rankea por relevancia y usa un umbral de ~50% (ignora palabras que
-- aparecen en más de la mitad de las filas) y su propia lista de stopwords
-- -- un nombre de especie muy común podría comportarse distinto al LIKE de
-- hoy. Probar con una muestra real de valores `especie=` antes de
-- confirmar. (Ver docs/rds_index_recommendations.md sección 3 para la
-- alternativa -- tabla catálogo de especies -- si esto no calza.)


-- ============================================================================
-- 2) PRIORIDAD MEDIA -- get_species() solo filtra al 10% dentro de su índice
-- ============================================================================
-- Evidencia real (docs/index_audit_output.md, sección "get_species(...)"):
--   key=idx_estado_anio, key_len=88, filtered=10.0, rows=171082
-- idx_estado_anio (nombre_estado, anio_corte) no incluye tipo_aviso, así
-- que MySQL trae 171,082 filas del índice y recién ahí filtra
-- tipo_aviso='MAYORES' fila por fila (10% de eficiencia real). Ampliarlo a
-- 3 columnas dobla como reemplazo directo -- cualquier consulta que hoy
-- usa solo (nombre_estado) o (nombre_estado, anio_corte) como prefijo
-- sigue funcionando igual, es un superset, no hay que dejar el índice
-- viejo en paralelo.

ALTER TABLE conapesca_landings_historical
  DROP INDEX idx_estado_anio,
  ADD INDEX idx_estado_anio_tipo (nombre_estado, anio_corte, tipo_aviso),
  ALGORITHM=INPLACE, LOCK=NONE;


-- ============================================================================
-- 3) PRIORIDAD BAJA -- get_estados() ordena con tabla temporal
-- ============================================================================
-- Evidencia real (docs/index_audit_output.md, sección "get_estados(...)"):
--   key=idx_anio, filtered=100.0, Extra="Using temporary; Using filesort"
-- Ya usa índice (no es full scan) -- esto solo evita el paso extra de
-- ordenar/deduplicar en memoria después de traer las filas del año. Ganancia
-- menor comparada con 1) y 2); queda como candidato, no como urgente.

ALTER TABLE conapesca_landings_historical
  ADD INDEX idx_anio_estado (anio_corte, nombre_estado),
  ALGORITHM=INPLACE, LOCK=NONE;


-- ============================================================================
-- Deliberadamente NO se propone nada para:
-- ============================================================================
-- - get_landings(group_by='folio'): el GROUP BY junta 5 columnas
--   (folio_aviso, anio_corte, tipo_aviso, nombre_estado, nombre_oficina) que
--   ningún índice cubre completo empezando por folio_aviso -- MySQL va a
--   necesitar tabla temporal igual (docs/index_audit_output.md, sección
--   correspondiente, key=idx_anio, Extra="Using temporary; Using filesort").
--   Es una limitación estructural de la consulta, no de índices.
-- - get_row_count() / get_coverage(): agregados incondicionales (sin
--   WHERE/GROUP) -- ningún índice ayuda a un COUNT(*)/MIN/MAX sobre toda la
--   tabla. El fix real sería un contador/resumen mantenido aparte, no un
--   índice (ver docs/rds_index_recommendations.md, nota al final de la
--   sección 2).
--
-- Notas que aplican a 1), 2) y 3):
-- - Cada índice nuevo tiene costo de escritura en cada INSERT/UPDATE y
--   espacio en disco proporcional a las columnas indexadas × 12.4M filas.
--   Si esta tabla se recarga en bloque periódicamente, vale la pena evaluar
--   si conviene dropear estos índices antes de una carga masiva y
--   recrearlos después.
-- - Verificar la versión real del engine antes de correr 1): FULLTEXT en
--   InnoDB requiere MySQL 5.6+ -- la RDS ya confirmó 8.0.45/8.0.46 al
--   correr el audit, así que no es bloqueante, queda solo como
--   recordatorio si esto se reusa en otro entorno.
