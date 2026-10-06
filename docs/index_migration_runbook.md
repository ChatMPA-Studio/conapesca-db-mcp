# Runbook: migración de índices (conapesca_landings_historical)

Script: `scripts/migrate_indexes.py` — aplica los índices de
`docs/rds_index_recommendations.md`. Probado contra MySQL 8.0.46 (600k filas):
dry-run, apply, re-apply (idempotente), rollback y re-apply.

## Qué cambia

| Paso | Cambio | Efecto medido (600k filas) |
|---|---|---|
| `estado_anio_tipo` | `idx_estado_anio` → `idx_estado_anio_tipo (nombre_estado, anio_corte, tipo_aviso)` (un solo ALTER atómico) | `get_species` 78 ms → 1.3 ms |
| `anio_estado` | agrega `idx_anio_estado (anio_corte, nombre_estado)` | `get_estados` 564 ms → 300 ms |
| `ft_especie` | agrega FULLTEXT `ft_especie_cientifico` | búsqueda por especie 541 ms → 111 ms **solo cuando el código use `MATCH`** |

Los índices con prefijo (`nombre_estado(19)`, etc.) se copian automáticamente de los índices existentes.
No se borra ni modifica ningún dato.

## Antes de correr

1. Apuntar a la instancia **de prueba**, no a la final. El script pide escribir el nombre del host para confirmar.
2. Usuario con `ALTER`, `INDEX`, `DROP` sobre la tabla (no el usuario read-only `mcp_conapesca_ro`).
3. Espacio libre en la instancia: al menos 2x el tamaño de la tabla (~17 GB para 8.5 GB de datos). Verificar `FreeStorageSpace` en CloudWatch.
4. Sin transacciones largas abiertas (el script avisa si encuentra alguna).
5. Correrlo dentro de `tmux`/`screen`: si la sesión se corta, MySQL cancela el ALTER en curso (no queda a medias, pero hay que repetirlo).
6. `pip install pymysql` (ya está en las dependencias del proyecto).

## Pasos

```bash
export MIGRATION_DB_HOST=<host> MIGRATION_DB_USER=<admin> MIGRATION_DB_NAME=conapesca
export MIGRATION_DB_PASSWORD=...        # o dejar que lo pida
# opcional: --ssl-ca <bundle.pem> para verificar el certificado de RDS

# 1. Dry-run (no cambia nada): revisar SQL y advertencias
python scripts/migrate_indexes.py

# 2. Aplicar. Los pasos 1 y 2 corren sin bloquear lecturas ni escrituras.
python scripts/migrate_indexes.py --apply
```

El paso `ft_especie` **falla a propósito** con `LOCK=NONE is not supported` (error 1846): el primer
FULLTEXT de una tabla InnoDB reconstruye la tabla y bloquea escrituras. El script se detiene sin
cambiar nada en ese paso. Para permitirlo, en una ventana donde nadie escriba en la tabla:

```bash
python scripts/migrate_indexes.py --apply --allow-lock
```

Escrituras bloqueadas (lecturas siguen) durante el build. Con 600k filas tardó 39 s; con 12.4M
estimar ~15-20 min (aprox. lineal, no medido). Re-correr es seguro: los pasos ya aplicados se saltan.

Al terminar el script ejecuta `EXPLAIN` de las queries clave y marca `OK` / `WARN` si el
optimizador no usa el índice nuevo. Todo queda en `migrate_indexes_<fecha>.log`.

## Rollback

```bash
python scripts/migrate_indexes.py --rollback --apply
```

Restaura el layout original (borra los índices nuevos y recrea `idx_estado_anio`). Probado.
Se puede combinar con `--only paso1,paso2`.

## Si falla

| Error | Significado | Acción |
|---|---|---|
| 1846 / 1845 | La operación necesita lock | `--allow-lock` en ventana tranquila |
| 1205 | Esperó demasiado un metadata lock | Terminar la transacción larga (`SHOW PROCESSLIST`) y re-correr |
| 1142 / 1227 | Usuario sin permisos | Usar un usuario con ALTER/INDEX/DROP |
| Disco lleno | Sin espacio para el build | Liberar espacio; el ALTER se revierte solo |

Cada ALTER es atómico: si falla, esa tabla queda como estaba. Los pasos anteriores quedan aplicados.

## Pendiente fuera de este script

El FULLTEXT no mejora nada hasta cambiar `tools/data_access.py` (`_get_landings_sync`,
`_get_taxonomy_sync`) de `LIKE '%x%'` a `MATCH ... AGAINST`. FULLTEXT busca por palabra, no
por subcadena: hay que usar `IN BOOLEAN MODE` con comodín (`'camaron*'`) para que "camaron"
siga encontrando "camarones". Hacer ese cambio de código **después** de validar el índice.
