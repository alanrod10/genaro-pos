"""Módulo de verificación de preparación e integridad del esquema de base de datos.

Audita la presencia de migraciones (001–009) y estructuras físicas (tablas, columnas, índices),
detectando inconsistencias y previniendo escrituras sin auditoría o congelamiento de costos.
"""

from collections import defaultdict
import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

MIGRACIONES_REQUERIDAS: List[int] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]

TABLAS_REQUERIDAS: List[str] = [
    "productos",
    "tickets",
    "ticket_items",
    "cargas_servicios",
    "gastos_retiros",
    "cierres_diarios",
    "gastos_auditoria",
    "productos_auditoria",
    "cargas_servicios_auditoria",
    "ventas_auditoria",
    "pedidos_proveedor",
    "pedido_items",
    "pedidos_auditoria",
    "auth_scram_challenges",
    "commercial_events_outbox",
    "commercial_adicionales_arqueo_turno",
    "tesoreria_prepay_cargo_diario",
    "tesoreria_prepay_coordinacion",
]

# Estructuras físicas requeridas por cada versión de migración
ESTRUCTURAS_POR_MIGRACION: Dict[int, Dict[str, Any]] = {
    1: {"tablas": ["productos"], "columnas": {}, "indices": {}},
    2: {"tablas": ["cargas_servicios"], "columnas": {}, "indices": {}},
    3: {"tablas": ["tickets", "ticket_items"], "columnas": {}, "indices": {}},
    4: {"tablas": ["productos_auditoria"], "columnas": {}, "indices": {}},
    5: {"tablas": ["cargas_servicios_auditoria"], "columnas": {}, "indices": {}},
    6: {"tablas": ["gastos_retiros", "cierres_diarios", "gastos_auditoria"], "columnas": {}, "indices": {}},
    7: {"tablas": ["ventas_auditoria"], "columnas": {}, "indices": {}},
    8: {"tablas": ["pedidos_proveedor", "pedido_items", "pedidos_auditoria"], "columnas": {}, "indices": {}},
    9: {"tablas": [], "columnas": {"ticket_items": ["costo_unitario"]}, "indices": {}},
    10: {
        "tablas": [],
        "columnas": {
            "cargas_servicios": ["idempotency_key"],
            "gastos_retiros": ["idempotency_key"],
        },
        "indices": {
            "cargas_servicios": [
                {
                    "nombre": "uq_cargas_idempotency_key",
                    "columnas": ["idempotency_key"],
                    "unique": True,
                    "partial": True,
                }
            ],
            "gastos_retiros": [
                {
                    "nombre": "uq_gastos_idempotency_key",
                    "columnas": ["idempotency_key"],
                    "unique": True,
                    "partial": True,
                }
            ],
        },
    },
    11: {
        "tablas": ["auth_scram_challenges"],
        "columnas": {
            "auth_scram_challenges": ["jti", "exp", "used", "creado_en_utc"],
        },
        "indices": {
            "auth_scram_challenges": [
                {
                    "nombre": "idx_scram_challenges_exp",
                    "columnas": ["exp"],
                    "unique": False,
                    "partial": False,
                },
                {
                    "nombre": "idx_scram_challenges_used",
                    "columnas": ["used"],
                    "unique": False,
                    "partial": False,
                },
            ],
        },
    },
    12: {
        "tablas": ["commercial_events_outbox", "commercial_adicionales_arqueo_turno"],
        "columnas": {},
        "indices": {},
    },
    13: {
        "tablas": [],
        "columnas": {
            "cargas_servicios": ["plataforma_origen", "servicio_destino"],
        },
        "indices": {},
    },
    14: {
        "tablas": ["tesoreria_prepay_cargo_diario", "tesoreria_prepay_coordinacion"],
        "columnas": {},
        "indices": {},
    },
}

FOREIGN_KEYS_REQUERIDAS = [
    {"tabla": "ticket_items", "from": "ticket_id", "to_tabla": "tickets", "to_col": "id"},
    {"tabla": "cargas_servicios_auditoria", "from": "carga_id", "to_tabla": "cargas_servicios", "to_col": "id"},
    {"tabla": "gastos_auditoria", "from": "gasto_id", "to_tabla": "gastos_retiros", "to_col": "id"},
    {"tabla": "ventas_auditoria", "from": "ticket_id", "to_tabla": "tickets", "to_col": "id"},
    {"tabla": "productos_auditoria", "from": "producto_id", "to_tabla": "productos", "to_col": "id"},
    {"tabla": "pedido_items", "from": "pedido_id", "to_tabla": "pedidos_proveedor", "to_col": "id"},
    {"tabla": "tesoreria_prepay_coordinacion", "from": "jornada_id", "to_tabla": "tesoreria_jornadas", "to_col": "id"},
]


class ErrorEsquemaNoPreparado(Exception):
    """El esquema de la base de datos no está preparado, tiene migraciones pendientes o es inconsistente."""

    def __init__(
        self,
        mensaje: str,
        estado: str = "SCHEMA_NOT_READY",
        detalles: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.estado = estado
        self.detalles = detalles or {}


def _listar_tablas_existentes(conn: Any) -> Set[str]:
    """Obtiene el conjunto de tablas existentes en la base de datos."""
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
        )
        return {str(r[0]) for r in cur.fetchall()}
    except Exception as e:
        logger.error("Error al consultar sqlite_master: %s", e)
        return set()


def _listar_columnas_tabla(conn: Any, tabla: str) -> Set[str]:
    """Obtiene el conjunto de columnas de una tabla dada."""
    try:
        cur = conn.execute(f"PRAGMA table_info({tabla});")
        return {str(r[1]) for r in cur.fetchall()}
    except Exception:
        return set()


def _listar_foreign_keys_tabla(conn: Any, tabla: str) -> List[Dict[str, str]]:
    """Obtiene metadatos de foreign keys de una tabla dada."""
    try:
        cur = conn.execute(f"PRAGMA foreign_key_list({tabla});")
        return [{"from": str(r[3]), "to_tabla": str(r[2])} for r in cur.fetchall()]
    except Exception:
        return []


def _listar_indices_tabla(conn: Any, tabla: str) -> Dict[str, Dict[str, Any]]:
    """Obtiene metadatos de índices creados en una tabla dada."""
    try:
        cur = conn.execute(f"PRAGMA index_list({tabla});")
        indices = {}
        for row in cur.fetchall():
            name = str(row[1])
            is_unique = bool(row[2])
            is_partial = bool(row[4])
            info_cur = conn.execute(f"PRAGMA index_info({name});")
            cols = [str(r[2]) for r in info_cur.fetchall()]
            sql_row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name = ?;", (name,)
            ).fetchone()
            sql_text = str(sql_row[0] or "") if sql_row else ""
            indices[name] = {
                "unique": is_unique,
                "partial": is_partial,
                "columnas": cols,
                "sql": sql_text,
            }
        return indices
    except Exception:
        return {}


def _listar_versiones_aplicadas(conn: Any, tablas_existentes: Set[str]) -> List[int]:
    """Obtiene la lista ordenada de versiones aplicadas registradas en _schema_migrations."""
    if "_schema_migrations" not in tablas_existentes:
        return []
    try:
        cur = conn.execute("SELECT version FROM _schema_migrations ORDER BY version ASC;")
        return [int(r[0]) for r in cur.fetchall()]
    except Exception:
        return []


def comprobar_preparacion_esquema(conn: Optional[Any] = None) -> Dict[str, Any]:
    """Audita exhaustivamente el esquema físico frente a las migraciones esperadas.

    Retorna un diccionario de diagnóstico seguro, sin exponer credenciales, tokens ni URLs.
    """
    propia = False
    if conn is None:
        try:
            from db.catalog import _obtener_conexion_turso
            conn = _obtener_conexion_turso()
            propia = True
        except Exception:
            return {
                "status": "ERROR_CONEXION",
                "listo": False,
                "versiones_aplicadas": [],
                "migraciones_pendientes": list(MIGRACIONES_REQUERIDAS),
                "tablas_existentes": [],
                "tablas_faltantes": list(TABLAS_REQUERIDAS),
                "columnas_faltantes": [
                    "ticket_items.costo_unitario",
                    "cargas_servicios.idempotency_key",
                    "gastos_retiros.idempotency_key",
                ],
                "indices_faltantes": [
                    "cargas_servicios.uq_cargas_idempotency_key",
                    "gastos_retiros.uq_gastos_idempotency_key",
                ],
                "indices_incorrectos": [],
                "foreign_keys_faltantes": [],
                "inconsistencias": [],
                "mensaje": "No se pudo conectar a la base de datos para verificar el esquema.",
            }

    try:
        # 1. Introspección consolidada de objetos en sqlite_master
        master_cur = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '__turso_%';"
        )
        master_rows = master_cur.fetchall()
        tablas_existentes = {str(r[1]) for r in master_rows if r[0] == "table"}
        index_sql_map = {str(r[1]): str(r[3] or "") for r in master_rows if r[0] == "index"}

        # 2. Versiones aplicadas registradas
        versiones_aplicadas = []
        if "_schema_migrations" in tablas_existentes:
            try:
                cur_v = conn.execute("SELECT version FROM _schema_migrations ORDER BY version ASC;")
                versiones_aplicadas = [int(r[0]) for r in cur_v.fetchall()]
            except Exception:
                versiones_aplicadas = []
        versiones_set = set(versiones_aplicadas)

        # 3. Introspección agrupada de columnas y foreign keys con fallback individual memoizado (E.1)
        columnas_por_tabla: Dict[str, Set[str]] = {t: set() for t in tablas_existentes}
        fks_por_tabla: Dict[str, List[Dict[str, str]]] = {t: [] for t in tablas_existentes}
        agrupado_cols_ok = False
        try:
            cur_cols_fks = conn.execute(
                "SELECT 'COL' as kind, m.name as tbl, p.name as detail, '' as extra "
                "FROM sqlite_master m, pragma_table_info(m.name) p "
                "WHERE m.type='table' AND m.name NOT LIKE 'sqlite_%' AND m.name NOT LIKE '__turso_%' "
                "UNION ALL "
                "SELECT 'FK' as kind, m.name as tbl, fk.[from] as detail, fk.[table] as extra "
                "FROM sqlite_master m, pragma_foreign_key_list(m.name) fk "
                "WHERE m.type='table' AND m.name NOT LIKE 'sqlite_%' AND m.name NOT LIKE '__turso_%';"
            )
            for kind, tbl, detail, extra in cur_cols_fks.fetchall():
                tbl_s = str(tbl)
                if tbl_s in tablas_existentes:
                    if kind == "COL":
                        columnas_por_tabla[tbl_s].add(str(detail))
                    elif kind == "FK":
                        fks_por_tabla[tbl_s].append({"from": str(detail), "to_tabla": str(extra)})
            agrupado_cols_ok = True
        except Exception as e:
            logger.debug("Introspección agrupada de columnas y fks no disponible (%s), usando fallback.", e)

        # 4. Introspección agrupada de índices con fallback individual memoizado
        indices_por_tabla: Dict[str, Dict[str, Dict[str, Any]]] = {t: {} for t in tablas_existentes}
        agrupado_idx_ok = False
        try:
            cur_idx = conn.execute(
                "SELECT m.name, il.name, il.[unique], il.partial, ii.name "
                "FROM sqlite_master m, pragma_index_list(m.name) il, pragma_index_info(il.name) ii "
                "WHERE m.type='table' AND m.name NOT LIKE 'sqlite_%' AND m.name NOT LIKE '__turso_%' "
                "ORDER BY m.name, il.name, ii.seqno;"
            )
            for tbl, idx_name, is_unq, is_part, col_name in cur_idx.fetchall():
                tbl_str = str(tbl)
                idx_str = str(idx_name)
                if tbl_str in indices_por_tabla:
                    if idx_str not in indices_por_tabla[tbl_str]:
                        indices_por_tabla[tbl_str][idx_str] = {
                            "unique": bool(is_unq),
                            "partial": bool(is_part),
                            "columnas": [],
                            "sql": index_sql_map.get(idx_str, ""),
                        }
                    indices_por_tabla[tbl_str][idx_str]["columnas"].append(str(col_name))
            agrupado_idx_ok = True
        except Exception as e:
            logger.debug("Introspección agrupada de índices no disponible (%s), usando fallback.", e)

        def _obtener_cols(tabla: str) -> Set[str]:
            if agrupado_cols_ok and tabla in columnas_por_tabla:
                return columnas_por_tabla[tabla]
            if tabla not in columnas_por_tabla or not agrupado_cols_ok:
                columnas_por_tabla[tabla] = _listar_columnas_tabla(conn, tabla)
            return columnas_por_tabla[tabla]

        def _obtener_fks(tabla: str) -> List[Dict[str, str]]:
            if agrupado_cols_ok and tabla in fks_por_tabla:
                return fks_por_tabla[tabla]
            if tabla not in fks_por_tabla or not agrupado_cols_ok:
                fks_por_tabla[tabla] = _listar_foreign_keys_tabla(conn, tabla)
            return fks_por_tabla[tabla]

        def _obtener_indices(tabla: str) -> Dict[str, Dict[str, Any]]:
            if agrupado_idx_ok and tabla in indices_por_tabla:
                return indices_por_tabla[tabla]
            if tabla not in indices_por_tabla or not agrupado_idx_ok:
                indices_por_tabla[tabla] = _listar_indices_tabla(conn, tabla)
            return indices_por_tabla[tabla]

        # Tablas faltantes
        tablas_faltantes = [t for t in TABLAS_REQUERIDAS if t not in tablas_existentes]

        # Columnas faltantes (evaluadas dinámicamente de todas las migraciones requeridas)
        columnas_faltantes = []
        for v in MIGRACIONES_REQUERIDAS:
            req_cols = ESTRUCTURAS_POR_MIGRACION.get(v, {}).get("columnas", {})
            for t, cols in req_cols.items():
                if t in tablas_existentes:
                    t_cols = _obtener_cols(t)
                    for col in cols:
                        col_id = f"{t}.{col}"
                        if col not in t_cols and col_id not in columnas_faltantes:
                            columnas_faltantes.append(col_id)

        # Índices faltantes e incorrectos
        indices_faltantes = []
        indices_incorrectos = []
        for v in MIGRACIONES_REQUERIDAS:
            req_indices = ESTRUCTURAS_POR_MIGRACION.get(v, {}).get("indices", {})
            for t, specs in req_indices.items():
                if t in tablas_existentes:
                    indices_existentes = _obtener_indices(t)
                    for spec in specs:
                        idx_name = spec["nombre"]
                        idx_id = f"{t}.{idx_name}"
                        if idx_name not in indices_existentes:
                            if idx_id not in indices_faltantes:
                                indices_faltantes.append(idx_id)
                        else:
                            idx_meta = indices_existentes[idx_name]
                            errores = []
                            if spec.get("unique") and not idx_meta["unique"]:
                                errores.append("no es UNIQUE")
                            if spec.get("partial") and not idx_meta["partial"]:
                                errores.append("no es parcial (falta condición WHERE)")
                            if spec.get("columnas") and idx_meta["columnas"] != spec["columnas"]:
                                errores.append(f"columnas indexadas {idx_meta['columnas']} != esperadas {spec['columnas']}")
                            if spec.get("partial") and "WHERE" not in idx_meta["sql"].upper():
                                errores.append("falta cláusula WHERE en definición SQL")
                            if errores:
                                indices_incorrectos.append(f"{idx_id} ({', '.join(errores)})")

        migraciones_pendientes = [v for v in MIGRACIONES_REQUERIDAS if v not in versiones_set]

        # Foreign keys faltantes e inspección referencial (E.1)
        foreign_keys_faltantes = []
        for fk_spec in FOREIGN_KEYS_REQUERIDAS:
            child_tbl = fk_spec["tabla"]
            parent_tbl = fk_spec["to_tabla"]
            from_col = fk_spec["from"]
            if child_tbl in tablas_existentes and parent_tbl in tablas_existentes:
                actual_fks = _obtener_fks(child_tbl)
                has_fk = any(
                    str(f.get("from", "")).lower() == from_col.lower()
                    and str(f.get("to_tabla", "")).lower() == parent_tbl.lower()
                    for f in actual_fks
                )
                if not has_fk:
                    fk_label = f"{child_tbl}.{from_col} -> {parent_tbl}"
                    foreign_keys_faltantes.append(fk_label)

        # Inconsistencias
        inconsistencias: List[str] = []

        if foreign_keys_faltantes:
            for fk_faltante in foreign_keys_faltantes:
                inconsistencias.append(
                    f"Falta clave foránea requerida: {fk_faltante} (PRAGMA foreign_key_list)."
                )

        # 1. Si hay tablas pero falta la tabla _schema_migrations
        if "_schema_migrations" not in tablas_existentes and len(tablas_existentes) > 0:
            inconsistencias.append(
                "Existen tablas en la base de datos pero la tabla '_schema_migrations' no existe."
            )

        # 2. Versión registrada en _schema_migrations pero la estructura física no existe o es incorrecta
        for v in versiones_aplicadas:
            req = ESTRUCTURAS_POR_MIGRACION.get(v)
            if not req:
                continue
            for t in req["tablas"]:
                if t not in tablas_existentes:
                    inconsistencias.append(
                        f"La migración versión {v} figura como aplicada pero la tabla '{t}' no existe físicamente."
                    )
            for t, cols in req["columnas"].items():
                if t in tablas_existentes:
                    t_cols = _obtener_cols(t)
                    for col in cols:
                        if col not in t_cols:
                            inconsistencias.append(
                                f"La migración versión {v} figura como aplicada pero la columna '{col}' no existe en la tabla '{t}'."
                            )
                else:
                    inconsistencias.append(
                        f"La migración versión {v} figura como aplicada pero la tabla '{t}' no existe para validar sus columnas."
                    )
            for t, specs in req.get("indices", {}).items():
                if t in tablas_existentes:
                    indices_existentes = _obtener_indices(t)
                    for spec in specs:
                        idx_name = spec["nombre"]
                        if idx_name not in indices_existentes:
                            inconsistencias.append(
                                f"La migración versión {v} figura como aplicada pero el índice '{idx_name}' no existe en la tabla '{t}'."
                            )
                        else:
                            idx_meta = indices_existentes[idx_name]
                            if spec.get("unique") and not idx_meta["unique"]:
                                inconsistencias.append(
                                    f"La migración versión {v} figura como aplicada pero el índice '{idx_name}' en '{t}' no es UNIQUE."
                                )
                            if spec.get("partial") and not idx_meta["partial"]:
                                inconsistencias.append(
                                    f"La migración versión {v} figura como aplicada pero el índice '{idx_name}' en '{t}' no es parcial (requiere WHERE)."
                                )
                else:
                    inconsistencias.append(
                        f"La migración versión {v} figura como aplicada pero la tabla '{t}' no existe para validar sus índices."
                    )

        # 3. Estructuras físicas presentes pero sin la migración registrada en _schema_migrations
        if "productos_auditoria" in tablas_existentes and 4 not in versiones_set:
            inconsistencias.append(
                "La tabla 'productos_auditoria' existe físicamente pero la versión 4 no está registrada en _schema_migrations."
            )

        if "cargas_servicios_auditoria" in tablas_existentes and 5 not in versiones_set:
            inconsistencias.append(
                "La tabla 'cargas_servicios_auditoria' existe físicamente pero la versión 5 no está registrada en _schema_migrations."
            )

        if (
            "gastos_retiros" in tablas_existentes
            or "cierres_diarios" in tablas_existentes
            or "gastos_auditoria" in tablas_existentes
        ) and 6 not in versiones_set:
            inconsistencias.append(
                "Las tablas de gastos/cierres existen físicamente pero la versión 6 no está registrada en _schema_migrations."
            )

        if "ventas_auditoria" in tablas_existentes and 7 not in versiones_set:
            inconsistencias.append(
                "La tabla 'ventas_auditoria' existe físicamente pero la versión 7 no está registrada en _schema_migrations."
            )

        if ("pedidos_proveedor" in tablas_existentes or "pedido_items" in tablas_existentes) and 8 not in versiones_set:
            inconsistencias.append(
                "Las tablas de pedidos existen físicamente pero la versión 8 no está registrada en _schema_migrations."
            )

        if "ticket_items" in tablas_existentes:
            cols_ti = _obtener_cols("ticket_items")
            if "costo_unitario" in cols_ti and 9 not in versiones_set:
                inconsistencias.append(
                    "La columna 'costo_unitario' existe en 'ticket_items' pero la versión 9 no está registrada en _schema_migrations."
                )

        if "cargas_servicios" in tablas_existentes:
            cols_cs = _obtener_cols("cargas_servicios")
            if "idempotency_key" in cols_cs and 10 not in versiones_set:
                inconsistencias.append(
                    "La columna 'idempotency_key' existe en 'cargas_servicios' pero la versión 10 no está registrada en _schema_migrations."
                )

        if "gastos_retiros" in tablas_existentes:
            cols_gr = _obtener_cols("gastos_retiros")
            if "idempotency_key" in cols_gr and 10 not in versiones_set:
                inconsistencias.append(
                    "La columna 'idempotency_key' existe en 'gastos_retiros' pero la versión 10 no está registrada en _schema_migrations."
                )

        if "auth_scram_challenges" in tablas_existentes and 11 not in versiones_set:
            inconsistencias.append(
                "La tabla 'auth_scram_challenges' existe físicamente pero la versión 11 no está registrada en _schema_migrations."
            )

        if "commercial_events_outbox" in tablas_existentes and 12 not in versiones_set:
            inconsistencias.append(
                "La tabla 'commercial_events_outbox' existe físicamente pero la versión 12 no está registrada en _schema_migrations."
            )

        if "cargas_servicios" in tablas_existentes:
            cols_cs = _obtener_cols("cargas_servicios")
            if "plataforma_origen" in cols_cs and 13 not in versiones_set:
                inconsistencias.append(
                    "La columna 'plataforma_origen' existe en 'cargas_servicios' pero la versión 13 no está registrada en _schema_migrations."
                )

        if "tesoreria_prepay_coordinacion" in tablas_existentes and 14 not in versiones_set:
            inconsistencias.append(
                "La tabla 'tesoreria_prepay_coordinacion' existe físicamente pero la versión 14 no está registrada en _schema_migrations."
            )

        # Clasificación del estado global
        if inconsistencias:
            status = "ESQUEMA_INCONSISTENTE"
            listo = False
            mensaje = f"Se detectaron {len(inconsistencias)} inconsistencia(s) entre _schema_migrations y el esquema físico."
        elif migraciones_pendientes or tablas_faltantes or columnas_faltantes or indices_faltantes or indices_incorrectos:
            status = "MIGRACIONES_PENDIENTES"
            listo = False
            detalles_pend = []
            if migraciones_pendientes:
                detalles_pend.append(f"migraciones pendientes: {migraciones_pendientes}")
            if tablas_faltantes:
                detalles_pend.append(f"tablas faltantes: {tablas_faltantes}")
            if columnas_faltantes:
                detalles_pend.append(f"columnas faltantes: {columnas_faltantes}")
            if indices_faltantes:
                detalles_pend.append(f"índices faltantes: {indices_faltantes}")
            if indices_incorrectos:
                detalles_pend.append(f"índices con definición incorrecta: {indices_incorrectos}")
            mensaje = f"La base de datos requiere mantenimiento de esquema ({', '.join(detalles_pend)})."
        else:
            status = "PREPARADO"
            listo = True
            mensaje = (
                "El esquema de base de datos está 100% preparado y verificado "
                "con las migraciones 001–014 (incluyendo 001–010 y 011–014)."
            )

        return {
            "status": status,
            "listo": listo,
            "versiones_aplicadas": versiones_aplicadas,
            "migraciones_pendientes": migraciones_pendientes,
            "tablas_existentes": sorted(list(tablas_existentes)),
            "tablas_faltantes": sorted(tablas_faltantes),
            "columnas_faltantes": sorted(columnas_faltantes),
            "indices_faltantes": sorted(indices_faltantes),
            "indices_incorrectos": sorted(indices_incorrectos),
            "foreign_keys_faltantes": sorted(foreign_keys_faltantes),
            "inconsistencias": inconsistencias,
            "mensaje": mensaje,
        }
    finally:
        if propia and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def verificar_esquema_listo(conn: Optional[Any] = None) -> None:
    """Verifica que el esquema esté listo para operaciones. Lanza ErrorEsquemaNoPreparado si no lo está."""
    res = comprobar_preparacion_esquema(conn)
    if not res["listo"]:
        raise ErrorEsquemaNoPreparado(
            mensaje=res["mensaje"],
            estado=res["status"],
            detalles=res,
        )
