"""Acceso y lógica contable para gastos, retiros y arqueo diario de caja.

Mantiene compatibilidad exacta con las fórmulas del sistema anterior (mostrar_visor)
y provee trazabilidad y anulación lógica de operaciones.
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
import pandas as pd

from db.readiness import ErrorEsquemaNoPreparado
from db.commercial_outbox_emitter import (
    enqueue_commercial_event,
    to_amount_cents,
    registrar_arqueo_adicionales_turno_db,
)

BACKEND_SHEETS = "SHEETS"
BACKEND_TURSO = "TURSO"

_HOST_DEV_AUTORIZADO = "genaro-pos-dev-alanrod10.aws-sa-east-1.turso.io"
_ENTORNO_DEV_AUTORIZADO = "genaro-pos-dev"
_ZONA_AR = ZoneInfo("America/Argentina/Jujuy")

COLUMNAS_GASTOS_POS = (
    "FECHA",
    "TIPO",
    "DETALLE",
    "MONTO_SALIDA",
    "ORIGEN_FONDOS",
    "METODO_PAGO",
)
COLUMNA_ID_INTERNO = "_TURSO_ID"


class ErrorGastosTurso(ValueError):
    """Error de formato, validación o consistencia de un gasto o arqueo."""


class ErrorConflictoIdempotencia(ErrorGastosTurso):
    """Lanzada cuando se reutiliza una clave de idempotencia con datos discordantes."""


def _abrir_conexion_dev():
    url = os.getenv("TURSO_DEV_DATABASE_URL", "").strip()
    token = os.getenv("TURSO_DEV_AUTH_TOKEN", "").strip()
    entorno = os.getenv("ENV_NAME", "").strip()

    parsed = urlparse(url)
    if (
        entorno != _ENTORNO_DEV_AUTORIZADO
        or parsed.scheme != "turso"
        or parsed.hostname != _HOST_DEV_AUTORIZADO
        or not token
    ):
        raise ErrorGastosTurso(
            "Conexión bloqueada: falta configurar el entorno autorizado "
            "genaro-pos-dev y su endpoint/token de desarrollo."
        )

    import turso_serverless

    conn = turso_serverless.connect(url, auth_token=token)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
    except Exception:
        pass
    return conn


@contextmanager
def _conexion(conn=None):
    propia = conn is None
    actual = _abrir_conexion_dev() if propia else conn
    try:
        yield actual
    finally:
        if propia:
            actual.close()


def _rango_utc_dia_local(fecha_objetivo: str) -> Tuple[str, str]:
    """Calcula el intervalo UTC semiabierto [inicio_utc, fin_utc) para una fecha local en Argentina."""
    texto = str(fecha_objetivo or "").strip()
    if not texto:
        raise ErrorGastosTurso("La fecha objetivo es obligatoria.")
    parte_fecha = texto.split(" ")[0]
    if "/" in parte_fecha:
        d, m, y = [int(p) for p in parte_fecha.split("/")[:3]]
    else:
        y, m, d = [int(p) for p in parte_fecha[:10].split("-")[:3]]

    inicio_local = datetime(y, m, d, 0, 0, 0, tzinfo=_ZONA_AR)
    fin_local = inicio_local + timedelta(days=1)
    inicio_utc = inicio_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    fin_utc = fin_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return inicio_utc, fin_utc


def _fecha_utc(fecha):
    if fecha is None or pd.isna(fecha):
        raise ErrorGastosTurso("La fecha es obligatoria.")

    if isinstance(fecha, pd.Timestamp):
        fecha = fecha.to_pydatetime()

    if isinstance(fecha, datetime):
        dt = fecha
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_ZONA_AR)
        else:
            dt = dt.astimezone(_ZONA_AR)
    elif isinstance(fecha, date):
        dt = datetime.combine(fecha, datetime.min.time()).replace(
            tzinfo=_ZONA_AR
        )
    else:
        texto = str(fecha).strip()
        if not texto:
            raise ErrorGastosTurso("La fecha es obligatoria.")

        dt = None
        formatos = (
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        )
        for formato in formatos:
            try:
                dt = datetime.strptime(texto, formato)
                dt = dt.replace(tzinfo=_ZONA_AR)
                break
            except ValueError:
                continue

        if dt is None:
            try:
                dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ErrorGastosTurso(
                    f"Formato de fecha no reconocido: {texto!r}."
                ) from exc

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_ZONA_AR)
            else:
                dt = dt.astimezone(_ZONA_AR)

    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _fecha_local(fecha_utc):
    try:
        dt = datetime.fromisoformat(str(fecha_utc).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ErrorGastosTurso(
            f"Fecha UTC inválida en la base: {fecha_utc!r}."
        ) from exc

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_ZONA_AR).strftime("%d/%m/%Y %H:%M:%S")


def _entero_importe(valor, campo):
    if valor is None or pd.isna(valor):
        raise ErrorGastosTurso(f"{campo} es obligatorio.")

    try:
        numero = Decimal(str(valor).strip())
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise ErrorGastosTurso(f"{campo} no es un importe válido.") from exc

    if not numero.is_finite() or numero != numero.to_integral_value():
        raise ErrorGastosTurso(f"{campo} debe ser un importe entero en pesos.")

    return int(numero)


def _normalizar_registro_gasto(registro):
    if not hasattr(registro, "get"):
        raise ErrorGastosTurso("El gasto debe ser un registro tipo diccionario.")

    fecha = _fecha_utc(registro.get("FECHA") or registro.get("fecha"))
    tipo = str(registro.get("TIPO") or registro.get("tipo") or "General").strip() or "General"
    detalle = str(registro.get("DETALLE") or registro.get("detalle") or "").strip()
    if not detalle:
        raise ErrorGastosTurso("El detalle o concepto del gasto es obligatorio.")

    monto = _entero_importe(
        registro.get("MONTO_SALIDA") or registro.get("monto_salida"),
        "MONTO_SALIDA",
    )
    if monto <= 0:
        raise ErrorGastosTurso("El importe del gasto debe ser mayor que cero.")

    origen = str(
        registro.get("ORIGEN_FONDOS") or registro.get("origen_fondos") or "Caja diaria"
    ).strip() or "Caja diaria"

    metodo = str(
        registro.get("METODO_PAGO") or registro.get("metodo_pago") or "EFECTIVO"
    ).strip().upper()
    if metodo not in ("EFECTIVO", "TRANSFERENCIA"):
        raise ErrorGastosTurso("El método de pago debe ser EFECTIVO o TRANSFERENCIA.")

    idempotency_key = registro.get("idempotency_key") or registro.get("IDEMPOTENCY_KEY")
    if idempotency_key is not None:
        idempotency_key = str(idempotency_key).strip()
        if not idempotency_key:
            idempotency_key = None

    return {
        "fecha_hora_utc": fecha,
        "tipo": tipo,
        "detalle": detalle,
        "monto_salida": monto,
        "origen_fondos": origen,
        "metodo_pago": metodo,
        "idempotency_key": idempotency_key,
    }


def _consultar_gasto_por_idempotency_key(conn, idempotency_key: str) -> Optional[dict]:
    """Busca un gasto existente por su clave de idempotencia."""
    try:
        fila = conn.execute(
            """
            SELECT id, fecha_hora_utc, tipo, detalle, monto_salida,
                   origen_fondos, metodo_pago, anulado, idempotency_key
            FROM gastos_retiros
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None

    if not fila:
        return None

    return {
        "id": int(fila[0]),
        "fecha_hora_utc": fila[1],
        "tipo": fila[2],
        "detalle": fila[3],
        "monto_salida": int(fila[4]),
        "origen_fondos": fila[5],
        "metodo_pago": fila[6],
        "anulado": int(fila[7]),
        "idempotency_key": fila[8],
    }


def _verificar_identidad_gasto(existente: dict, norm: dict) -> None:
    """Verifica si los datos del gasto coinciden exactamente con la operación previa."""
    if existente.get("anulado", 0) == 1:
        raise ErrorConflictoIdempotencia(
            f"Conflicto de idempotencia: la operación con clave '{norm['idempotency_key']}' "
            "fue anulada previamente y no puede reintentarse."
        )

    tipo_existente = str(existente.get("tipo", "")).strip().lower()
    tipo_nuevo = str(norm.get("tipo", "")).strip().lower()
    origen_existente = str(existente.get("origen_fondos", "")).strip().lower()
    origen_nuevo = str(norm.get("origen_fondos", "")).strip().lower()

    if (
        existente.get("detalle", "").strip() != norm.get("detalle", "").strip()
        or existente.get("monto_salida") != norm.get("monto_salida")
        or tipo_existente != tipo_nuevo
        or origen_existente != origen_nuevo
        or str(existente.get("metodo_pago", "")).strip().upper() != str(norm.get("metodo_pago", "")).strip().upper()
    ):
        raise ErrorConflictoIdempotencia(
            f"Conflicto de idempotencia: la clave '{norm['idempotency_key']}' ya fue utilizada "
            "con datos comerciales diferentes."
        )


def _insertar_gasto(conn, norm: dict) -> int:
    idempotency_key = norm.get("idempotency_key")
    try:
        cursor = conn.execute(
            """
            INSERT INTO gastos_retiros (
                legacy_row_id, fecha_hora_utc, tipo, detalle,
                monto_salida, origen_fondos, metodo_pago, anulado, idempotency_key
            )
            VALUES (NULL, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                norm["fecha_hora_utc"],
                norm["tipo"],
                norm["detalle"],
                norm["monto_salida"],
                norm["origen_fondos"],
                norm["metodo_pago"],
                idempotency_key,
            ),
        )
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "no column named idempotency_key" in msg or "no such column: idempotency_key" in msg:
            if idempotency_key:
                raise ErrorEsquemaNoPreparado(
                    "La tabla gastos_retiros no contiene la columna 'idempotency_key'. "
                    "Requiere aplicar la migración 010."
                ) from e
            cursor = conn.execute(
                """
                INSERT INTO gastos_retiros (
                    legacy_row_id, fecha_hora_utc, tipo, detalle,
                    monto_salida, origen_fondos, metodo_pago, anulado
                )
                VALUES (NULL, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    norm["fecha_hora_utc"],
                    norm["tipo"],
                    norm["detalle"],
                    norm["monto_salida"],
                    norm["origen_fondos"],
                    norm["metodo_pago"],
                ),
            )
        else:
            raise

    rowid = getattr(cursor, "lastrowid", None)
    if rowid is None:
        fila = conn.execute("SELECT last_insert_rowid()").fetchone()
        rowid = fila[0]
    return int(rowid)


def registrar_gasto_idempotente_turso(
    registro: dict,
    idempotency_key: Optional[str] = None,
    actor: str = "sistema",
    conn=None,
) -> Tuple[int, bool]:
    """Inserta un gasto o retiro de forma idempotente con auditoría atómica.

    Retorna una tupla (id_gasto, es_reintento).
    Si idempotency_key ya fue confirmada con datos idénticos, retorna el ID existente
    y es_reintento=True sin duplicar egresos ni auditoría.
    Si difiere en sus datos, lanza ErrorConflictoIdempotencia.
    """
    norm = _normalizar_registro_gasto(registro)
    if idempotency_key:
        norm["idempotency_key"] = str(idempotency_key).strip()

    key = norm.get("idempotency_key")

    with _conexion(conn) as conexion:
        try:
            conexion.execute("BEGIN IMMEDIATE")
            if key:
                existente = _consultar_gasto_por_idempotency_key(conexion, key)
                if existente:
                    _verificar_identidad_gasto(existente, norm)
                    conexion.commit()
                    return existente["id"], True

            nuevo_id = _insertar_gasto(conexion, norm)

            # Auditoría atómica de creación de gasto (migración 006)
            try:
                conexion.execute(
                    """
                    INSERT INTO gastos_auditoria (gasto_id, accion, valores_anteriores, valores_nuevos, actor)
                    VALUES (?, 'CREAR', NULL, ?, ?)
                    """,
                    (nuevo_id, json.dumps(norm, ensure_ascii=False), actor),
                )
            except Exception as e:
                if "no such table" in str(e).lower() or "gastos_auditoria" in str(e).lower():
                    raise ErrorEsquemaNoPreparado(
                        f"Error al registrar auditoría de gasto: {str(e)}. Se requiere la tabla 'gastos_auditoria' (migración 006)."
                    ) from e
                raise

            # Emisión de evento outbox en la misma transacción atómica
            enqueue_commercial_event(
                conn=conexion,
                event_type="EXPENSE_CONFIRMED",
                entity_type="GASTO",
                entity_id=nuevo_id,
                action_type="CONFIRM",
                idempotency_key=f"COMM_EXPENSE_{nuevo_id}_CONFIRM",
                payload={
                    "gasto_id": nuevo_id,
                    "tipo": norm.get("tipo", "GASTO"),
                    "detalle": norm.get("detalle", ""),
                    "monto_salida_cents": to_amount_cents(norm.get("monto_salida")),
                    "origen_fondos": norm.get("origen_fondos", "CAJA_DIARIA"),
                    "metodo_pago": norm.get("metodo_pago", "EFECTIVO"),
                    "fecha_hora_utc": norm.get("fecha_hora_utc"),
                    "actor": actor,
                    "commercial_idempotency_key": key,
                },
                created_at=norm.get("fecha_hora_utc"),
            )

            conexion.commit()
            return nuevo_id, False
        except sqlite3.IntegrityError:
            conexion.rollback()
            if key:
                existente = _consultar_gasto_por_idempotency_key(conexion, key)
                if existente:
                    _verificar_identidad_gasto(existente, norm)
                    return existente["id"], True
            raise
        except Exception:
            conexion.rollback()
            raise


def registrar_gasto_turso(registro: dict, actor: str = "sistema", conn=None) -> int:
    """Inserta un gasto o retiro validado y retorna su ID."""
    nuevo_id, _ = registrar_gasto_idempotente_turso(registro, actor=actor, conn=conn)
    return nuevo_id


def obtener_gasto_por_id_turso(id_gasto: int, conn=None) -> Optional[dict]:
    """Obtiene un gasto puntual por ID."""
    with _conexion(conn) as conexion:
        try:
            fila = conexion.execute(
                """
                SELECT id, fecha_hora_utc, tipo, detalle, monto_salida,
                       origen_fondos, metodo_pago, anulado, idempotency_key
                FROM gastos_retiros
                WHERE id = ?
                """,
                (id_gasto,),
            ).fetchone()
            tiene_key = True
        except sqlite3.OperationalError:
            fila = conexion.execute(
                """
                SELECT id, fecha_hora_utc, tipo, detalle, monto_salida,
                       origen_fondos, metodo_pago, anulado
                FROM gastos_retiros
                WHERE id = ?
                """,
                (id_gasto,),
            ).fetchone()
            tiene_key = False

        if not fila:
            return None
        return {
            COLUMNA_ID_INTERNO: int(fila[0]),
            "FECHA": _fecha_local(fila[1]),
            "FECHA_HORA_UTC": fila[1],
            "TIPO": fila[2],
            "DETALLE": fila[3],
            "MONTO_SALIDA": int(fila[4]),
            "ORIGEN_FONDOS": fila[5],
            "METODO_PAGO": fila[6],
            "ANULADO": int(fila[7]),
            "IDEMPOTENCY_KEY": fila[8] if tiene_key and len(fila) > 8 else None,
        }


def cargar_gastos_turso(
    conn=None,
    fecha_filtro: Optional[str] = None,
    incluir_anulados: bool = False,
) -> pd.DataFrame:
    """Devuelve los gastos registrados como DataFrame."""
    condicion = "" if incluir_anulados else "WHERE anulado = 0"
    query = f"""
        SELECT id, fecha_hora_utc, tipo, detalle, monto_salida,
               origen_fondos, metodo_pago, anulado
        FROM gastos_retiros
        {condicion}
        ORDER BY fecha_hora_utc DESC, id DESC
    """
    with _conexion(conn) as conexion:
        filas = conexion.execute(query).fetchall()

    registros = []
    columnas = [COLUMNA_ID_INTERNO, *COLUMNAS_GASTOS_POS]
    if incluir_anulados:
        columnas.append("ANULADO")

    for fila in filas:
        f_local = _fecha_local(fila[1])
        item = {
            COLUMNA_ID_INTERNO: int(fila[0]),
            "FECHA": f_local,
            "TIPO": fila[2],
            "DETALLE": fila[3],
            "MONTO_SALIDA": int(fila[4]),
            "ORIGEN_FONDOS": fila[5],
            "METODO_PAGO": fila[6],
        }
        if incluir_anulados:
            item["ANULADO"] = int(fila[7])
        registros.append(item)

    df = pd.DataFrame(registros, columns=columnas)
    if df.empty or not fecha_filtro:
        return df

    # Filtrar por fecha
    ff = str(fecha_filtro).strip()
    try:
        if "/" in ff:
            d, m, y = [int(p) for p in ff.split("/")[:3]]
            target = f"{y:04d}-{m:02d}-{d:02d}"
        else:
            target = ff[:10]
    except Exception:
        target = ff

    def _coincide(f_str):
        try:
            d, m, y = [int(p) for p in f_str.split(" ")[0].split("/")[:3]]
            return f"{y:04d}-{m:02d}-{d:02d}" == target
        except Exception:
            return False

    return df[df["FECHA"].apply(_coincide)]


def editar_gasto_turso(id_gasto: int, registro_editado: dict, actor: str = "sistema", conn=None) -> dict:
    """Actualiza un gasto específico sin alterar otros garantizando auditoría."""
    norm = _normalizar_registro_gasto(registro_editado)

    with _conexion(conn) as conexion:
        try:
            conexion.execute("BEGIN IMMEDIATE")
            actual_row = conexion.execute(
                "SELECT id, fecha_hora_utc, tipo, detalle, monto_salida, origen_fondos, metodo_pago, anulado FROM gastos_retiros WHERE id = ?",
                (id_gasto,),
            ).fetchone()
            if not actual_row:
                raise ErrorGastosTurso(f"Gasto #{id_gasto} no encontrado.")
            if actual_row[7] == 1:
                raise ErrorGastosTurso(f"El gasto #{id_gasto} está anulado y no puede editarse.")

            prev_dict = {
                "id": actual_row[0], "fecha_hora_utc": actual_row[1], "tipo": actual_row[2],
                "detalle": actual_row[3], "monto_salida": actual_row[4], "origen_fondos": actual_row[5],
                "metodo_pago": actual_row[6], "anulado": actual_row[7]
            }

            conexion.execute(
                """
                UPDATE gastos_retiros
                SET fecha_hora_utc = ?, tipo = ?, detalle = ?,
                    monto_salida = ?, origen_fondos = ?, metodo_pago = ?
                WHERE id = ? AND anulado = 0
                """,
                (
                    norm["fecha_hora_utc"],
                    norm["tipo"],
                    norm["detalle"],
                    norm["monto_salida"],
                    norm["origen_fondos"],
                    norm["metodo_pago"],
                    id_gasto,
                ),
            )

            try:
                conexion.execute(
                    """
                    INSERT INTO gastos_auditoria (gasto_id, accion, valores_anteriores, valores_nuevos, actor)
                    VALUES (?, 'EDITAR', ?, ?, ?)
                    """,
                    (id_gasto, json.dumps(prev_dict, ensure_ascii=False), json.dumps(norm, ensure_ascii=False), actor),
                )
            except Exception as e:
                if "no such table" in str(e).lower() or "gastos_auditoria" in str(e).lower():
                    raise ErrorEsquemaNoPreparado(
                        f"Error al registrar auditoría de gasto: {str(e)}. Se requiere la tabla 'gastos_auditoria' (migración 006)."
                    ) from e
                raise

            # Emisión de evento outbox de edición / ajuste
            enqueue_commercial_event(
                conn=conexion,
                event_type="EXPENSE_ADJUSTED",
                entity_type="GASTO",
                entity_id=id_gasto,
                action_type="ADJUST",
                idempotency_key=f"COMM_EXPENSE_{id_gasto}_ADJUST_{norm['fecha_hora_utc']}",
                payload={
                    "gasto_id": id_gasto,
                    "tipo": norm.get("tipo", "GASTO"),
                    "detalle": norm.get("detalle", ""),
                    "monto_salida_cents": to_amount_cents(norm.get("monto_salida")),
                    "origen_fondos": norm.get("origen_fondos", "CAJA_DIARIA"),
                    "metodo_pago": norm.get("metodo_pago", "EFECTIVO"),
                    "fecha_hora_utc": norm.get("fecha_hora_utc"),
                    "actor": actor,
                    "reference_action": "CONFIRM",
                    "reference_idempotency_key": f"COMM_EXPENSE_{id_gasto}_CONFIRM",
                },
                created_at=norm.get("fecha_hora_utc"),
            )

            conexion.commit()
            return obtener_gasto_por_id_turso(id_gasto, conn=conexion)
        except Exception:
            conexion.rollback()
            raise


def anular_gasto_turso(id_gasto: int, motivo: Optional[str] = None, actor: str = "sistema", conn=None) -> dict:
    """Anula lógicamente un gasto garantizando auditoría."""
    with _conexion(conn) as conexion:
        try:
            conexion.execute("BEGIN IMMEDIATE")
            actual = conexion.execute(
                "SELECT id, fecha_hora_utc, tipo, detalle, monto_salida, origen_fondos, metodo_pago, anulado FROM gastos_retiros WHERE id = ?",
                (id_gasto,),
            ).fetchone()
            if not actual:
                raise ErrorGastosTurso(f"Gasto #{id_gasto} no encontrado.")
            if actual[7] == 1:
                return obtener_gasto_por_id_turso(id_gasto, conn=conexion)

            prev_dict = {
                "id": actual[0], "fecha_hora_utc": actual[1], "tipo": actual[2],
                "detalle": actual[3], "monto_salida": actual[4], "origen_fondos": actual[5],
                "metodo_pago": actual[6], "anulado": actual[7]
            }

            conexion.execute(
                "UPDATE gastos_retiros SET anulado = 1 WHERE id = ?",
                (id_gasto,),
            )

            try:
                conexion.execute(
                    """
                    INSERT INTO gastos_auditoria (gasto_id, accion, valores_anteriores, valores_nuevos, actor)
                    VALUES (?, 'ANULAR', ?, ?, ?)
                    """,
                    (
                        id_gasto,
                        json.dumps({**prev_dict, "motivo": motivo or "Anulación de usuario"}, ensure_ascii=False),
                        json.dumps({"anulado": 1}, ensure_ascii=False),
                        actor,
                    ),
                )
            except Exception as e:
                if "no such table" in str(e).lower() or "gastos_auditoria" in str(e).lower():
                    raise ErrorEsquemaNoPreparado(
                        f"Error al registrar auditoría de gasto: {str(e)}. Se requiere la tabla 'gastos_auditoria' (migración 006)."
                    ) from e
                raise

            # Emisión de evento outbox de anulación en la misma transacción atómica
            enqueue_commercial_event(
                conn=conexion,
                event_type="EXPENSE_VOIDED",
                entity_type="GASTO",
                entity_id=id_gasto,
                action_type="VOID",
                idempotency_key=f"COMM_EXPENSE_{id_gasto}_VOID",
                payload={
                    "gasto_id": id_gasto,
                    "tipo": actual[2],
                    "detalle": actual[3],
                    "monto_salida_cents": to_amount_cents(actual[4]),
                    "origen_fondos": actual[5],
                    "metodo_pago": actual[6],
                    "motivo": motivo or "Anulación de usuario",
                    "actor": actor,
                    "reference_action": "CONFIRM",
                    "reference_idempotency_key": f"COMM_EXPENSE_{id_gasto}_CONFIRM",
                },
            )

            conexion.commit()
            return obtener_gasto_por_id_turso(id_gasto, conn=conexion)
        except Exception:
            conexion.rollback()
            raise


def calcular_resumen_gastos_turso(fecha_filtro: Optional[str] = None, conn=None) -> dict:
    """Calcula totales de gastos del período separando efectivo y transferencias."""
    df = cargar_gastos_turso(conn=conn, fecha_filtro=fecha_filtro, incluir_anulados=True)
    if df.empty:
        return {
            "total_operaciones": 0,
            "gastos_efvo": 0,
            "gastos_transf": 0,
            "gastos_total": 0,
            "operaciones_anuladas": 0,
        }

    activas = df[df["ANULADO"] == 0] if "ANULADO" in df.columns else df
    anuladas = df[df["ANULADO"] == 1] if "ANULADO" in df.columns else pd.DataFrame()

    gastos_efvo = int(activas.loc[activas["METODO_PAGO"] == "EFECTIVO", "MONTO_SALIDA"].sum())
    gastos_transf = int(activas.loc[activas["METODO_PAGO"] == "TRANSFERENCIA", "MONTO_SALIDA"].sum())

    return {
        "total_operaciones": len(activas),
        "gastos_efvo": gastos_efvo,
        "gastos_transf": gastos_transf,
        "gastos_total": gastos_efvo + gastos_transf,
        "operaciones_anuladas": len(anuladas),
    }


def calcular_arqueo_diario_turso(fecha_objetivo: str, conn=None) -> dict:
    """Calcula el arqueo consolidado del día conforme a las reglas reales de mostrar_visor en app.py.

    Combina:
    - Ventas de productos (Caja A): monto_efvo, monto_transf, total_ventas
    - Servicios y cargas (Cajas B, C, D, E): capital Claro, prepay, sube mp, recargos y d_recargas
    - Gastos del día: salidas efectivo y salidas transferencia
    - Efectivo disponible y esperado en caja física
    - Total neto en banco
    """
    # 1. Gastos del día
    resumen_gastos = calcular_resumen_gastos_turso(fecha_filtro=fecha_objetivo, conn=conn)
    gastos_efvo = resumen_gastos["gastos_efvo"]
    gastos_transf = resumen_gastos["gastos_transf"]

    # 2. Servicios del día (usando db.services)
    from db.services import calcular_resumen_cargas_turso
    res_cargas = calcular_resumen_cargas_turso(fecha_filtro=fecha_objetivo, conn=conn)

    sube_mp_capital = res_cargas["capital_sube_mp"]
    d_recargas = res_cargas["total_transferencia"]
    servicios_efvo = res_cargas["total_efectivo"]
    servicios_transf = res_cargas["total_transferencia"]

    # 3. Ventas del día desde la tabla tickets (excluyendo anuladas y aplicando rango local AR)
    ventas_efvo = 0
    ventas_transf = 0
    total_ventas = 0

    inicio_utc, fin_utc = _rango_utc_dia_local(fecha_objetivo)

    with _conexion(conn) as conexion:
        try:
            filas_ventas = conexion.execute(
                """
                SELECT monto_efectivo, monto_transferencia, total_venta
                FROM tickets
                WHERE anulado = 0
                  AND fecha_hora_utc >= ?
                  AND fecha_hora_utc < ?
                """,
                (inicio_utc, fin_utc),
            ).fetchall()
            for row in filas_ventas:
                ventas_efvo += int(row[0] or 0)
                ventas_transf += int(row[1] or 0)
                total_ventas += int(row[2] or 0)
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "no such table" in msg and "tickets" in msg:
                pass
            else:
                raise

    # 4. Fórmulas de conciliación idénticas a app.py (mostrar_visor)
    # d_ventas_drugstore = a_transf
    # d_total_banco = d_ventas_drugstore + d_recargas - sube_mp_capital - gastos_transf
    total_banco = ventas_transf + d_recargas - sube_mp_capital - gastos_transf

    # Caja A: Efectivo disponible atribuible a ventas drugstore (sin mezclar transferencias)
    efectivo_disponible_drugstore = ventas_efvo

    # Efectivo esperado físico en cajón:
    # Exclusivamente movimientos físicos reales (sin restar d_recargas ni enmascarar con max(0, ...))
    efectivo_esperado = ventas_efvo + servicios_efvo - gastos_efvo

    return {
        "fecha": fecha_objetivo,
        # Ventas (Caja A)
        "ventas_efvo": ventas_efvo,
        "ventas_transf": ventas_transf,
        "total_ventas": total_ventas,
        "ganancia_estimada_ventas": int(round(total_ventas * 0.10)),
        # Servicios (Cajas B, C, D, E)
        "servicios_efvo": servicios_efvo,
        "servicios_transf": servicios_transf,
        "total_servicios": res_cargas["total_cobrado"],
        "capital_prepay": res_cargas["capital_prepay"],
        "capital_claro": res_cargas["capital_claro"],
        "capital_sube_mp": res_cargas["capital_sube_mp"],
        "adicionales_ganancia": res_cargas["total_adicional"],
        "d_recargas": d_recargas,
        # Gastos
        "gastos_efvo": gastos_efvo,
        "gastos_transf": gastos_transf,
        "gastos_total": gastos_efvo + gastos_transf,
        # Totales conciliados
        "efectivo_disponible_drugstore": efectivo_disponible_drugstore,
        "efectivo_esperado": efectivo_esperado,
        "total_banco": total_banco,
    }


def registrar_cierre_diario_turso(cierre: dict, cerrado_por: str = "sistema", conn=None) -> dict:
    """Registra o actualiza el cierre formal del día para auditoría."""
    fecha = str(cierre.get("fecha", "")).strip()
    if not fecha:
        raise ErrorGastosTurso("La fecha del cierre es obligatoria.")

    esperado = int(cierre.get("efectivo_esperado", 0))
    contado = int(cierre.get("efectivo_contado", 0))
    diferencia = contado - esperado
    actor_cierre = cerrado_por if cerrado_por != "sistema" else cierre.get("cerrado_por", "sistema")

    with _conexion(conn) as conexion:
        try:
            conexion.execute("BEGIN IMMEDIATE")
            existente = conexion.execute(
                "SELECT id FROM cierres_diarios WHERE fecha = ?",
                (fecha,),
            ).fetchone()

            if existente:
                conexion.execute(
                    """
                    UPDATE cierres_diarios
                    SET efectivo_esperado = ?, efectivo_contado = ?, diferencia = ?,
                        total_ventas_efvo = ?, total_ventas_transf = ?,
                        total_servicios_efvo = ?, total_servicios_transf = ?,
                        total_gastos_efvo = ?, total_gastos_transf = ?,
                        total_banco = ?, observaciones = ?, cerrado_por = ?,
                        actualizado_en_utc = datetime('now')
                    WHERE id = ?
                    """,
                    (
                        esperado,
                        contado,
                        diferencia,
                        int(cierre.get("total_ventas_efvo", 0)),
                        int(cierre.get("total_ventas_transf", 0)),
                        int(cierre.get("total_servicios_efvo", 0)),
                        int(cierre.get("total_servicios_transf", 0)),
                        int(cierre.get("total_gastos_efvo", 0)),
                        int(cierre.get("total_gastos_transf", 0)),
                        int(cierre.get("total_banco", 0)),
                        cierre.get("observaciones", ""),
                        actor_cierre,
                        existente[0],
                    ),
                )
                cierre_id = existente[0]
            else:
                cursor = conexion.execute(
                    """
                    INSERT INTO cierres_diarios (
                        fecha, efectivo_esperado, efectivo_contado, diferencia,
                        total_ventas_efvo, total_ventas_transf,
                        total_servicios_efvo, total_servicios_transf,
                        total_gastos_efvo, total_gastos_transf,
                        total_banco, observaciones, cerrado_por, cerrado_en_utc
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (
                        fecha,
                        esperado,
                        contado,
                        diferencia,
                        int(cierre.get("total_ventas_efvo", 0)),
                        int(cierre.get("total_ventas_transf", 0)),
                        int(cierre.get("total_servicios_efvo", 0)),
                        int(cierre.get("total_servicios_transf", 0)),
                        int(cierre.get("total_gastos_efvo", 0)),
                        int(cierre.get("total_gastos_transf", 0)),
                        int(cierre.get("total_banco", 0)),
                        cierre.get("observaciones", ""),
                        actor_cierre,
                    ),
                )
                rowid = getattr(cursor, "lastrowid", None)
                if rowid is None:
                    rowid = conexion.execute("SELECT last_insert_rowid()").fetchone()[0]
                cierre_id = int(rowid)

            # Arqueo informativo de adicionales por turno (estrictamente de control, sin asientos ni traslados automáticos)
            try:
                calc_cur = conexion.execute(
                    """
                    SELECT COALESCE(SUM(monto_adicional), 0)
                    FROM cargas_servicios
                    WHERE anulado = 0 AND pago_efectivo > 0;
                    """
                )
                calc_row = calc_cur.fetchone()
                total_adic_calc_pesos = calc_row[0] if calc_row else 0
            except Exception:
                total_adic_calc_pesos = 0

            declarado_pesos = cierre.get("adicionales_efectivo_declarado")
            total_adic_decl_cents = to_amount_cents(declarado_pesos) if declarado_pesos is not None else None
            total_adic_calc_cents = to_amount_cents(total_adic_calc_pesos)
            dif_cents = (total_adic_decl_cents - total_adic_calc_cents) if total_adic_decl_cents is not None else None

            registrar_arqueo_adicionales_turno_db(
                conn=conexion,
                cierre_turno_id=str(cierre_id),
                fecha_operativa=fecha,
                turno=str(cierre.get("turno", "DIARIO")),
                responsable_caja=actor_cierre,
                total_adicionales_efectivo_calculado_cents=total_adic_calc_cents,
                total_adicionales_efectivo_declarado_cents=total_adic_decl_cents,
                diferencia_cents=dif_cents,
                observaciones=cierre.get("observaciones_adicionales", "Arqueo informativo de adicionales registrado al cierre de turno"),
            )

            conexion.commit()
            return obtener_cierre_diario_turso(fecha, conn=conexion)
        except Exception:
            conexion.rollback()
            raise


def obtener_cierre_diario_turso(fecha: str, conn=None) -> Optional[dict]:
    """Obtiene un cierre registrado por fecha."""
    query = """
        SELECT id, fecha, efectivo_esperado, efectivo_contado, diferencia,
               total_ventas_efvo, total_ventas_transf,
               total_servicios_efvo, total_servicios_transf,
               total_gastos_efvo, total_gastos_transf,
               total_banco, observaciones, cerrado_por, cerrado_en_utc
        FROM cierres_diarios
        WHERE fecha = ?
    """
    with _conexion(conn) as conexion:
        fila = conexion.execute(query, (fecha.strip(),)).fetchone()
        if not fila:
            return None
        return {
            "id": int(fila[0]),
            "fecha": fila[1],
            "efectivo_esperado": int(fila[2]),
            "efectivo_contado": int(fila[3]),
            "diferencia": int(fila[4]),
            "total_ventas_efvo": int(fila[5]),
            "total_ventas_transf": int(fila[6]),
            "total_servicios_efvo": int(fila[7]),
            "total_servicios_transf": int(fila[8]),
            "total_gastos_efvo": int(fila[9]),
            "total_gastos_transf": int(fila[10]),
            "total_banco": int(fila[11]),
            "observaciones": fila[12] or "",
            "cerrado_por": fila[13],
            "cerrado_en_utc": fila[14],
        }


def listar_cierres_turso(conn=None) -> List[dict]:
    """Lista todos los cierres registrados en orden cronológico descendente."""
    query = """
        SELECT id, fecha, efectivo_esperado, efectivo_contado, diferencia,
               total_ventas_efvo, total_ventas_transf,
               total_servicios_efvo, total_servicios_transf,
               total_gastos_efvo, total_gastos_transf,
               total_banco, observaciones, cerrado_por, cerrado_en_utc
        FROM cierres_diarios
        ORDER BY fecha DESC, id DESC
    """
    with _conexion(conn) as conexion:
        filas = conexion.execute(query).fetchall()

    return [
        {
            "id": int(f[0]),
            "fecha": f[1],
            "efectivo_esperado": int(f[2]),
            "efectivo_contado": int(f[3]),
            "diferencia": int(f[4]),
            "total_ventas_efvo": int(f[5]),
            "total_ventas_transf": int(f[6]),
            "total_servicios_efvo": int(f[7]),
            "total_servicios_transf": int(f[8]),
            "total_gastos_efvo": int(f[9]),
            "total_gastos_transf": int(f[10]),
            "total_banco": int(f[11]),
            "observaciones": f[12] or "",
            "cerrado_por": f[13],
            "cerrado_en_utc": f[14],
        }
        for f in filas
    ]
