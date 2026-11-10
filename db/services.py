"""Acceso a cargas de servicios en Turso DEV.

La conexión se inyecta en las operaciones de datos o se abre de forma
perezosa y restringida al endpoint de desarrollo autorizado.
"""
import os
import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd

from db.readiness import ErrorEsquemaNoPreparado
from db.commercial_outbox_emitter import enqueue_commercial_event, to_amount_cents


BACKEND_SHEETS = "SHEETS"
BACKEND_TURSO = "TURSO"

_HOST_DEV_AUTORIZADO = "genaro-pos-dev-alanrod10.aws-sa-east-1.turso.io"
_ENTORNO_DEV_AUTORIZADO = "genaro-pos-dev"
_ZONA_AR = ZoneInfo("America/Argentina/Jujuy")

COLUMNAS_CARGAS_POS = (
    "FECHA",
    "SERVICIO",
    "MONTO_CARGA",
    "MONTO_ADICIONAL",
    "TOTAL_COBRADO",
    "PAGO_EFVO",
    "PAGO_TRANSF",
)
COLUMNA_ID_INTERNO = "_TURSO_ID"


class ErrorCargasTurso(ValueError):
    """Error de acceso, formato o consistencia de una carga."""


class ErrorConflictoIdempotencia(ErrorCargasTurso):
    """Lanzada cuando se reutiliza una clave de idempotencia con datos discordantes."""


def obtener_backend_servicios():
    """Selecciona SHEETS (predeterminado) o TURSO explícitamente."""
    backend = os.getenv("SERVICIOS_BACKEND", BACKEND_SHEETS).strip().upper()
    if backend not in {BACKEND_SHEETS, BACKEND_TURSO}:
        raise ErrorCargasTurso(
            "SERVICIOS_BACKEND debe ser SHEETS o TURSO."
        )
    return backend


def _abrir_conexion_dev():
    """Abre únicamente la base Turso DEV autorizada; nunca imprime el token."""
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
        raise ErrorCargasTurso(
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


gestor_conexion_servicios = _conexion


def clasificar_servicio(servicio: Optional[str], plataforma_origen: Optional[str] = None) -> str:
    """Clasifica de forma canónica el nombre de un servicio o carga.

    Retorna:
    - 'CLARO': recargas y pagos de Claro Comercios.
    - 'SUBE_MP': cargas emitidas directamente desde Mercado Pago Propio (saldo digital 1210).
    - 'PREPAY': resto de servicios prepagos mayoristas (David / 1220).
    """
    plat_norm = str(plataforma_origen or "").strip().upper()
    normalizado = " ".join(str(servicio or "").strip().lower().split())
    if plat_norm == "MERCADO PAGO":
        return "SUBE_MP"
    if "claro" in normalizado or plat_norm == "CLARO COMERCIOS":
        return "CLARO"
    if "sube" in normalizado and ("mp" in normalizado or "mercado" in normalizado):
        return "SUBE_MP"
    return "PREPAY"


def _fecha_utc(fecha):
    """Convierte una fecha de interfaz argentina a texto UTC para SQLite."""
    if fecha is None or pd.isna(fecha):
        raise ErrorCargasTurso("La fecha es obligatoria.")

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
            raise ErrorCargasTurso("La fecha es obligatoria.")

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
                raise ErrorCargasTurso(
                    f"Formato de fecha no reconocido: {texto!r}."
                ) from exc

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_ZONA_AR)
            else:
                dt = dt.astimezone(_ZONA_AR)

    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _fecha_local(fecha_utc):
    """Representa la fecha UTC almacenada como fecha/hora local argentina."""
    try:
        dt = datetime.fromisoformat(str(fecha_utc).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ErrorCargasTurso(
            f"Fecha UTC inválida en la base: {fecha_utc!r}."
        ) from exc

    # Las filas importadas deben estar en UTC. Las fechas sin zona se
    # interpretan como UTC para mantener la compatibilidad con el esquema.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_ZONA_AR).strftime("%d/%m/%Y %H:%M:%S")


def _entero_importe(valor, campo):
    if valor is None or pd.isna(valor):
        raise ErrorCargasTurso(f"{campo} es obligatorio.")

    try:
        numero = Decimal(str(valor).strip())
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise ErrorCargasTurso(f"{campo} no es un importe válido.") from exc

    if not numero.is_finite() or numero != numero.to_integral_value():
        raise ErrorCargasTurso(f"{campo} debe ser un importe entero en pesos.")

    return int(numero)


def _normalizar_registro(registro):
    """Valida los campos mostrados por Servicios e Historial."""
    if not hasattr(registro, "get"):
        raise ErrorCargasTurso("La carga debe ser un registro tipo diccionario.")

    fecha = _fecha_utc(registro.get("FECHA"))

    plataforma_origen = registro.get("PLATAFORMA_ORIGEN") or registro.get("plataforma_origen")
    if plataforma_origen is not None:
        plataforma_origen = str(plataforma_origen).strip()
        if not plataforma_origen:
            plataforma_origen = None
        else:
            plataforma_origen = plataforma_origen.upper()

    servicio_destino = registro.get("SERVICIO_DESTINO") or registro.get("servicio_destino")
    if servicio_destino is not None:
        servicio_destino = str(servicio_destino).strip()
        if not servicio_destino:
            servicio_destino = None

    servicio = str(registro.get("SERVICIO", "") or "").strip()
    if not servicio and servicio_destino:
        servicio = servicio_destino
    if not servicio:
        raise ErrorCargasTurso("El servicio es obligatorio.")
    if plataforma_origen is not None and servicio_destino is None and servicio:
        servicio_destino = servicio

    carga = _entero_importe(registro.get("MONTO_CARGA"), "MONTO_CARGA")
    adicional = _entero_importe(
        registro.get("MONTO_ADICIONAL"), "MONTO_ADICIONAL"
    )
    total = _entero_importe(registro.get("TOTAL_COBRADO"), "TOTAL_COBRADO")
    efectivo = _entero_importe(registro.get("PAGO_EFVO"), "PAGO_EFVO")
    transferencia = _entero_importe(
        registro.get("PAGO_TRANSF"), "PAGO_TRANSF"
    )

    if carga <= 0:
        raise ErrorCargasTurso("El monto de la carga debe ser mayor que cero.")
    if adicional < 0 or efectivo < 0 or transferencia < 0 or total < 0:
        raise ErrorCargasTurso("Los importes no pueden ser negativos.")
    if total != carga + adicional:
        raise ErrorCargasTurso(
            "TOTAL_COBRADO debe coincidir con carga más adicional."
        )
    if efectivo + transferencia != total:
        raise ErrorCargasTurso(
            "La suma de efectivo y transferencia debe coincidir con el total."
        )

    idempotency_key = registro.get("IDEMPOTENCY_KEY") or registro.get("idempotency_key")
    if idempotency_key is not None:
        idempotency_key = str(idempotency_key).strip()
        if not idempotency_key:
            idempotency_key = None

    return {
        "fecha_hora_utc": fecha,
        "servicio": servicio,
        "plataforma_origen": plataforma_origen,
        "servicio_destino": servicio_destino,
        "monto_carga": carga,
        "monto_adicional": adicional,
        "total_cobrado": total,
        "pago_efectivo": efectivo,
        "pago_transferencia": transferencia,
        "idempotency_key": idempotency_key,
    }


def _consultar_carga_por_idempotency_key(conn, idempotency_key: str) -> Optional[dict]:
    """Busca una carga existente por su clave de idempotencia."""
    plat_orig = None
    srv_dest = None
    try:
        fila = conn.execute(
            """
            SELECT id, fecha_hora_utc, servicio, monto_carga,
                   monto_adicional, total_cobrado, pago_efectivo,
                   pago_transferencia, anulado, idempotency_key,
                   plataforma_origen, servicio_destino
            FROM cargas_servicios
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()
        if fila:
            plat_orig = fila[10] if len(fila) > 10 else None
            srv_dest = fila[11] if len(fila) > 11 else None
    except sqlite3.OperationalError:
        try:
            fila = conn.execute(
                """
                SELECT id, fecha_hora_utc, servicio, monto_carga,
                       monto_adicional, total_cobrado, pago_efectivo,
                       pago_transferencia, anulado, idempotency_key
                FROM cargas_servicios
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
            plat_orig = None
            srv_dest = None
        except sqlite3.OperationalError:
            return None

    if not fila:
        return None

    return {
        "id": int(fila[0]),
        "fecha_hora_utc": fila[1],
        "servicio": fila[2],
        "monto_carga": int(fila[3]),
        "monto_adicional": int(fila[4]),
        "total_cobrado": int(fila[5]),
        "pago_efectivo": int(fila[6]),
        "pago_transferencia": int(fila[7]),
        "anulado": int(fila[8]),
        "idempotency_key": fila[9],
        "plataforma_origen": plat_orig,
        "servicio_destino": srv_dest or fila[2],
    }


def _formatear_carga_guardada(fila_dict: dict) -> dict:
    """Convierte el diccionario interno de base de datos al formato canónico de respuesta."""
    srv = fila_dict["servicio"]
    return {
        COLUMNA_ID_INTERNO: int(fila_dict["id"]),
        "id": int(fila_dict["id"]),
        "FECHA": _fecha_local(fila_dict["fecha_hora_utc"]),
        "FECHA_HORA_UTC": fila_dict["fecha_hora_utc"],
        "SERVICIO": srv,
        "servicio": srv,
        "PLATAFORMA_ORIGEN": fila_dict.get("plataforma_origen"),
        "plataforma_origen": fila_dict.get("plataforma_origen"),
        "SERVICIO_DESTINO": fila_dict.get("servicio_destino"),
        "servicio_destino": fila_dict.get("servicio_destino"),
        "MONTO_CARGA": int(fila_dict["monto_carga"]),
        "MONTO_ADICIONAL": int(fila_dict["monto_adicional"]),
        "TOTAL_COBRADO": int(fila_dict["total_cobrado"]),
        "PAGO_EFVO": int(fila_dict["pago_efectivo"]),
        "PAGO_TRANSF": int(fila_dict["pago_transferencia"]),
        "ANULADO": int(fila_dict.get("anulado", 0)),
        "anulado": int(fila_dict.get("anulado", 0)),
        "IDEMPOTENCY_KEY": fila_dict.get("idempotency_key"),
    }


def _verificar_identidad_carga(existente: dict, normalizado: dict) -> None:
    """Verifica si los datos de la carga coinciden exactamente con la operación previa."""
    if existente.get("anulado", 0) == 1:
        raise ErrorConflictoIdempotencia(
            f"Conflicto de idempotencia: la operación con clave '{normalizado['idempotency_key']}' "
            "fue anulada previamente y no puede reintentarse."
        )

    srv_existente = str(existente.get("servicio", "")).strip().lower()
    srv_nuevo = str(normalizado.get("servicio", "")).strip().lower()

    if (
        srv_existente != srv_nuevo
        or existente.get("monto_carga") != normalizado.get("monto_carga")
        or existente.get("monto_adicional") != normalizado.get("monto_adicional")
        or existente.get("total_cobrado") != normalizado.get("total_cobrado")
        or existente.get("pago_efectivo") != normalizado.get("pago_efectivo")
        or existente.get("pago_transferencia") != normalizado.get("pago_transferencia")
    ):
        raise ErrorConflictoIdempotencia(
            f"Conflicto de idempotencia: la clave '{normalizado['idempotency_key']}' ya fue utilizada "
            "con datos comerciales diferentes."
        )


def _insertar_carga(conn, registro):
    idempotency_key = registro.get("idempotency_key")
    plat_orig = registro.get("plataforma_origen")
    srv_dest = registro.get("servicio_destino")
    try:
        cursor = conn.execute(
            """
            INSERT INTO cargas_servicios (
                legacy_row_id, fecha_hora_utc, servicio, monto_carga,
                monto_adicional, total_cobrado, pago_efectivo,
                pago_transferencia, anulado, idempotency_key,
                plataforma_origen, servicio_destino
            )
            VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                registro["fecha_hora_utc"],
                registro["servicio"],
                registro["monto_carga"],
                registro["monto_adicional"],
                registro["total_cobrado"],
                registro["pago_efectivo"],
                registro["pago_transferencia"],
                idempotency_key,
                plat_orig,
                srv_dest,
            ),
        )
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "plataforma_origen" in msg or "servicio_destino" in msg:
            cursor = conn.execute(
                """
                INSERT INTO cargas_servicios (
                    legacy_row_id, fecha_hora_utc, servicio, monto_carga,
                    monto_adicional, total_cobrado, pago_efectivo,
                    pago_transferencia, anulado, idempotency_key
                )
                VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    registro["fecha_hora_utc"],
                    registro["servicio"],
                    registro["monto_carga"],
                    registro["monto_adicional"],
                    registro["total_cobrado"],
                    registro["pago_efectivo"],
                    registro["pago_transferencia"],
                    idempotency_key,
                ),
            )
        elif "no column named idempotency_key" in msg or "no such column: idempotency_key" in msg:
            if idempotency_key:
                raise ErrorEsquemaNoPreparado(
                    "La tabla cargas_servicios no contiene la columna 'idempotency_key'. "
                    "Requiere aplicar la migración 010."
                ) from e
            # Fallback para bases legadas sin columna idempotency_key cuando no se solicitó clave
            cursor = conn.execute(
                """
                INSERT INTO cargas_servicios (
                    legacy_row_id, fecha_hora_utc, servicio, monto_carga,
                    monto_adicional, total_cobrado, pago_efectivo,
                    pago_transferencia, anulado
                )
                VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    registro["fecha_hora_utc"],
                    registro["servicio"],
                    registro["monto_carga"],
                    registro["monto_adicional"],
                    registro["total_cobrado"],
                    registro["pago_efectivo"],
                    registro["pago_transferencia"],
                ),
            )
        else:
            raise

    rowid = getattr(cursor, "lastrowid", None)
    if rowid is None:
        fila = conn.execute("SELECT last_insert_rowid()").fetchone()
        rowid = fila[0]
    return int(rowid)


def _normalizar_fecha_filtro(fecha_filtro) -> Optional[str]:
    """Normaliza YYYY-MM-DD o DD/MM/YYYY a 'YYYY-MM-DD'; None si es inválida."""
    texto = str(fecha_filtro or "").strip()
    if not texto:
        return None
    parte = texto.split(" ")[0]
    try:
        if "/" in parte:
            d, m, y = [int(p) for p in parte.split("/")[:3]]
        else:
            y, m, d = [int(p) for p in parte[:10].split("-")[:3]]
        return datetime(y, m, d).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _rango_utc_dia_local(fecha_filtro) -> Optional[Tuple[str, str]]:
    """Intervalo UTC semiabierto [inicio, fin) del día local de Jujuy.

    Devuelve None si la fecha es inválida (el filtro no coincide con nada,
    igual que el comportamiento previo).
    """
    iso = _normalizar_fecha_filtro(fecha_filtro)
    if iso is None:
        return None
    y, m, d = [int(p) for p in iso.split("-")]
    inicio_local = datetime(y, m, d, tzinfo=_ZONA_AR)
    # Medianoche local del día siguiente (fin exclusivo).
    fin_local = datetime.combine(
        date(y, m, d) + timedelta(days=1), datetime.min.time()
    ).replace(tzinfo=_ZONA_AR)
    fmt = "%Y-%m-%d %H:%M:%S"
    return (
        inicio_local.astimezone(timezone.utc).strftime(fmt),
        fin_local.astimezone(timezone.utc).strftime(fmt),
    )


def cargar_cargas_turso(
    conn=None,
    incluir_anuladas=False,
    fecha=None,
    plataforma_origen: Optional[str] = None,
    servicio_destino: Optional[str] = None,
):
    """Devuelve las cargas con los nombres de columna que usa la interfaz.

    Si se recibe `fecha`, filtra en SQL por el día local de Jujuy usando un
    rango directo sobre fecha_hora_utc (aprovecha idx de fecha).
    Permite filtrar opcionalmente por plataforma_origen y servicio_destino.
    """
    condiciones = []
    params = []
    if not incluir_anuladas:
        condiciones.append("anulado = 0")

    fecha_invalida = False
    if fecha:
        rango = _rango_utc_dia_local(fecha)
        if rango is None:
            fecha_invalida = True
        else:
            condiciones.append("fecha_hora_utc >= ? AND fecha_hora_utc < ?")
            params.extend(rango)

    if plataforma_origen:
        p_clean = plataforma_origen.strip().upper()
        if p_clean in ("HISTORICO", "SIN_ORIGEN", "NULL"):
            condiciones.append("(plataforma_origen IS NULL OR plataforma_origen = '')")
        else:
            condiciones.append("plataforma_origen = ?")
            params.append(p_clean)

    if servicio_destino:
        s_clean = servicio_destino.strip()
        condiciones.append("(servicio_destino = ? OR (servicio_destino IS NULL AND servicio = ?))")
        params.extend([s_clean, s_clean])

    condicion = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""

    columnas_sql = """id, fecha_hora_utc, servicio, monto_carga,
                      monto_adicional, total_cobrado, pago_efectivo,
                      pago_transferencia, anulado, plataforma_origen, servicio_destino"""
    query = f"""
        SELECT {columnas_sql}
        FROM cargas_servicios
        {condicion}
        ORDER BY fecha_hora_utc DESC, id DESC
    """

    if fecha_invalida:
        filas = []
    else:
        with _conexion(conn) as conexion:
            try:
                filas = conexion.execute(query, params).fetchall()
            except sqlite3.OperationalError:
                # Fallback para bases sin columnas plataforma_origen/servicio_destino
                query_fallback = f"""
                    SELECT id, fecha_hora_utc, servicio, monto_carga,
                           monto_adicional, total_cobrado, pago_efectivo,
                           pago_transferencia, anulado
                    FROM cargas_servicios
                    {condicion}
                    ORDER BY fecha_hora_utc DESC, id DESC
                """
                filas = conexion.execute(query_fallback, params).fetchall()

    registros = []
    columnas = [
        COLUMNA_ID_INTERNO,
        *COLUMNAS_CARGAS_POS,
        "PLATAFORMA_ORIGEN",
        "SERVICIO_DESTINO",
    ]
    if incluir_anuladas:
        columnas.append("ANULADO")

    for fila in filas:
        srv = fila[2]
        plat_orig = fila[9] if len(fila) > 9 else None
        srv_dest = fila[10] if len(fila) > 10 else srv
        if not srv_dest:
            srv_dest = srv

        item = {
            COLUMNA_ID_INTERNO: int(fila[0]),
            "FECHA": _fecha_local(fila[1]),
            "SERVICIO": srv,
            "MONTO_CARGA": int(fila[3]),
            "MONTO_ADICIONAL": int(fila[4]),
            "TOTAL_COBRADO": int(fila[5]),
            "PAGO_EFVO": int(fila[6]),
            "PAGO_TRANSF": int(fila[7]),
            "PLATAFORMA_ORIGEN": plat_orig,
            "SERVICIO_DESTINO": srv_dest,
        }
        if incluir_anuladas:
            item["ANULADO"] = int(fila[8])
        registros.append(item)

    return pd.DataFrame(
        registros,
        columns=columnas,
    )


def registrar_carga_idempotente_turso(
    registro: dict,
    idempotency_key: Optional[str] = None,
    actor: str = "sistema",
    conn=None,
    retornar_registro: bool = False,
    tesoreria_conn: Optional[sqlite3.Connection] = None,
):
    """Inserta una carga nueva de forma idempotente con auditoría atómica.

    Retorna una tupla (id_carga, es_reintento) o (id_carga, es_reintento, carga_dict)
    si retornar_registro=True.
    Si idempotency_key ya fue confirmada con datos idénticos, devuelve el ID existente
    y es_reintento=True sin duplicar registros ni auditoría.
    Si difiere en sus datos, lanza ErrorConflictoIdempotencia.
    """
    normalizado = _normalizar_registro(registro)
    if idempotency_key:
        normalizado["idempotency_key"] = str(idempotency_key).strip()

    key = normalizado.get("idempotency_key")
    plat_orig = normalizado.get("plataforma_origen")
    pre_reserva = None

    # FASE 2: Pre-reserva síncrona en Tesorería si la plataforma es PREPAY
    if plat_orig == "PREPAY" and tesoreria_conn is not None:
        from tesoreria.prepay_coordinator import (
            solicitar_pre_reserva_prepay,
            confirmar_pre_reserva_comercial,
            cancelar_pre_reserva_prepay,
        )
        m_cap_cents = to_amount_cents(normalizado["monto_carga"])
        m_adic_cents = to_amount_cents(normalizado["monto_adicional"])
        p_efvo_cents = to_amount_cents(normalizado["pago_efectivo"])
        p_transf_cents = to_amount_cents(normalizado["pago_transferencia"])
        srv_dest = normalizado.get("servicio_destino") or normalizado["servicio"]

        coord_key = key or f"PREPAY_OP_{normalizado['fecha_hora_utc']}_{m_cap_cents}"
        pre_reserva = solicitar_pre_reserva_prepay(
            conn=tesoreria_conn,
            servicio_destino=srv_dest,
            monto_carga_cents=m_cap_cents,
            monto_adicional_cents=m_adic_cents,
            pago_efvo_cents=p_efvo_cents,
            pago_transf_cents=p_transf_cents,
            idempotency_key=coord_key,
            actor=actor,
            auto_commit=True,
        )

    with _conexion(conn) as conexion:
        try:
            conexion.execute("BEGIN IMMEDIATE")
            if key:
                existente = _consultar_carga_por_idempotency_key(conexion, key)
                if existente:
                    _verificar_identidad_carga(existente, normalizado)
                    conexion.commit()
                    if retornar_registro:
                        return existente["id"], True, _formatear_carga_guardada(existente)
                    return existente["id"], True

            nuevo_id = _insertar_carga(conexion, normalizado)

            # Auditoría atómica de creación de carga (migración 005)
            try:
                conexion.execute(
                    """
                    INSERT INTO cargas_servicios_auditoria (
                        carga_id, accion, valores_anteriores, valores_nuevos, actor
                    )
                    VALUES (?, 'CREAR', NULL, ?, ?)
                    """,
                    (nuevo_id, json.dumps(normalizado, ensure_ascii=False), actor),
                )
            except Exception as e:
                if "no such table" in str(e).lower() or "cargas_servicios_auditoria" in str(e).lower():
                    raise ErrorEsquemaNoPreparado(
                        f"Error al registrar auditoría de servicio: {str(e)}. Se requiere la tabla 'cargas_servicios_auditoria' (migración 005)."
                    ) from e
                raise

            guardado = None
            if retornar_registro:
                guardado = obtener_carga_por_id_turso(nuevo_id, conn=conexion)

            # Emisión de evento outbox en la misma transacción atómica
            idempotency_outbox_key = f"COMM_SERVICE_{nuevo_id}_CONFIRM"
            outbox_payload = {
                "servicio_id": nuevo_id,
                "servicio": normalizado["servicio"],
                "plataforma_origen": normalizado.get("plataforma_origen"),
                "servicio_destino": normalizado.get("servicio_destino"),
                "monto_carga_cents": to_amount_cents(normalizado["monto_carga"]),
                "monto_adicional_cents": to_amount_cents(normalizado["monto_adicional"]),
                "total_cobrado_cents": to_amount_cents(normalizado["total_cobrado"]),
                "pago_efectivo_cents": to_amount_cents(normalizado["pago_efectivo"]),
                "pago_transferencia_cents": to_amount_cents(normalizado["pago_transferencia"]),
                "fecha_hora_utc": normalizado["fecha_hora_utc"],
                "actor": actor,
                "commercial_idempotency_key": key,
            }
            if pre_reserva:
                outbox_payload["requiere_pre_reserva"] = True
                outbox_payload["pre_reserva"] = pre_reserva

            enqueue_commercial_event(
                conn=conexion,
                event_type="SERVICE_CONFIRMED",
                entity_type="SERVICIO",
                entity_id=nuevo_id,
                action_type="CONFIRM",
                idempotency_key=idempotency_outbox_key,
                payload=outbox_payload,
                created_at=normalizado["fecha_hora_utc"],
            )

            conexion.commit()

            # Confirmar vinculación en Tesorería tras commit comercial exitoso
            if pre_reserva and tesoreria_conn is not None:
                from tesoreria.prepay_coordinator import confirmar_pre_reserva_comercial
                coord_key = key or pre_reserva.get("idempotency_key")
                confirmar_pre_reserva_comercial(
                    conn=tesoreria_conn,
                    idempotency_key=coord_key,
                    commercial_ticket_id=nuevo_id,
                    actor=actor,
                    auto_commit=True,
                )

            if retornar_registro:
                return nuevo_id, False, guardado
            return nuevo_id, False
        except sqlite3.IntegrityError:
            conexion.rollback()
            if key:
                existente = _consultar_carga_por_idempotency_key(conexion, key)
                if existente:
                    _verificar_identidad_carga(existente, normalizado)
                    if retornar_registro:
                        return existente["id"], True, _formatear_carga_guardada(existente)
                    return existente["id"], True
            if pre_reserva and tesoreria_conn is not None and not pre_reserva.get("es_reintento"):
                try:
                    from tesoreria.prepay_coordinator import cancelar_pre_reserva_prepay
                    coord_key = key or pre_reserva.get("idempotency_key")
                    cancelar_pre_reserva_prepay(
                        conn=tesoreria_conn,
                        idempotency_key=coord_key,
                        motivo="Fallo de integridad comercial",
                        actor=actor,
                        auto_commit=True,
                    )
                except Exception:
                    pass
            raise
        except Exception:
            conexion.rollback()
            if pre_reserva and tesoreria_conn is not None and not pre_reserva.get("es_reintento"):
                try:
                    from tesoreria.prepay_coordinator import cancelar_pre_reserva_prepay
                    coord_key = key or pre_reserva.get("idempotency_key")
                    cancelar_pre_reserva_prepay(
                        conn=tesoreria_conn,
                        idempotency_key=coord_key,
                        motivo="Fallo en persistencia de ticket comercial",
                        actor=actor,
                        auto_commit=True,
                    )
                except Exception:
                    pass
            raise


def registrar_carga_turso(registro, actor: str = "sistema", conn=None):
    """Inserta una carga nueva y retorna su ID interno."""
    nuevo_id, _ = registrar_carga_idempotente_turso(registro, actor=actor, conn=conn)
    return nuevo_id


def guardar_ediciones_cargas_turso(
    actualizaciones,
    ids_anulados,
    nuevas,
    actor: str = "sistema",
    conn=None,
):
    """Guarda en una transacción ediciones, anulaciones e inserciones nuevas con auditoría.

    Las anulaciones son lógicas: no se destruyen físicamente filas históricas.
    actualizaciones es una secuencia de pares (id_turso, registro).
    """
    preparados = [
        (int(identificador), _normalizar_registro(registro))
        for identificador, registro in actualizaciones
    ]
    ids_baja = [int(identificador) for identificador in ids_anulados]
    preparados_nuevos = [_normalizar_registro(registro) for registro in nuevas]

    ids_actualizar = [identificador for identificador, _ in preparados]
    todos_ids = ids_actualizar + ids_baja

    if any(identificador <= 0 for identificador in todos_ids):
        raise ErrorCargasTurso("ID interno de carga inválido.")
    if len(set(ids_actualizar)) != len(ids_actualizar):
        raise ErrorCargasTurso("Hay IDs repetidos entre las filas editadas.")
    if len(set(ids_baja)) != len(ids_baja):
        raise ErrorCargasTurso("Hay IDs repetidos entre las filas anuladas.")
    if set(ids_actualizar) & set(ids_baja):
        raise ErrorCargasTurso(
            "Una misma fila no puede editarse y anularse en una operación."
        )

    with _conexion(conn) as conexion:
        try:
            conexion.execute("BEGIN IMMEDIATE")

            # Comprobar todas las identidades primero evita aplicar solo una
            # parte del cambio cuando el historial está desactualizado.
            for identificador in todos_ids:
                existe = conexion.execute(
                    """
                    SELECT 1 FROM cargas_servicios
                    WHERE id = ? AND anulado = 0
                    """,
                    (identificador,),
                ).fetchone()
                if existe is None:
                    raise ErrorCargasTurso(
                        "Una carga cambió o ya no está activa. "
                        "Recargá el historial antes de guardar."
                    )

            filas_anuladas = {}
            for identificador in ids_baja:
                prev = conexion.execute(
                    """
                    SELECT id, fecha_hora_utc, servicio, monto_carga, monto_adicional,
                           total_cobrado, pago_efectivo, pago_transferencia, anulado
                    FROM cargas_servicios WHERE id = ?
                    """,
                    (identificador,),
                ).fetchone()
                prev_dict = {
                    "id": prev[0], "fecha_hora_utc": prev[1], "servicio": prev[2],
                    "monto_carga": prev[3], "monto_adicional": prev[4], "total_cobrado": prev[5],
                    "pago_efectivo": prev[6], "pago_transferencia": prev[7], "anulado": prev[8]
                } if prev else {}

                if prev:
                    filas_anuladas[identificador] = {
                        COLUMNA_ID_INTERNO: int(prev[0]),
                        "id": int(prev[0]),
                        "FECHA": _fecha_local(prev[1]),
                        "FECHA_HORA_UTC": prev[1],
                        "SERVICIO": prev[2],
                        "MONTO_CARGA": int(prev[3]),
                        "MONTO_ADICIONAL": int(prev[4]),
                        "TOTAL_COBRADO": int(prev[5]),
                        "PAGO_EFVO": int(prev[6]),
                        "PAGO_TRANSF": int(prev[7]),
                        "ANULADO": 1,
                        "anulado": 1,
                    }

                conexion.execute(
                    """
                    UPDATE cargas_servicios
                    SET anulado = 1
                    WHERE id = ? AND anulado = 0
                    """,
                    (identificador,),
                )

                try:
                    conexion.execute(
                        """
                        INSERT INTO cargas_servicios_auditoria (
                            carga_id, accion, valores_anteriores, valores_nuevos, actor
                        )
                        VALUES (?, 'ANULAR', ?, ?, ?)
                        """,
                        (
                            identificador,
                            json.dumps(prev_dict, ensure_ascii=False),
                            json.dumps({"anulado": 1}, ensure_ascii=False),
                            actor,
                        ),
                    )
                except Exception as e:
                    if "no such table" in str(e).lower() or "cargas_servicios_auditoria" in str(e).lower():
                        raise ErrorEsquemaNoPreparado(
                            f"Error al registrar auditoría de servicio: {str(e)}. Se requiere la tabla 'cargas_servicios_auditoria' (migración 005)."
                        ) from e
                    raise

            for identificador, registro in preparados:
                try:
                    prev = conexion.execute(
                        """
                        SELECT id, fecha_hora_utc, servicio, monto_carga, monto_adicional,
                               total_cobrado, pago_efectivo, pago_transferencia, anulado,
                               plataforma_origen, servicio_destino
                        FROM cargas_servicios WHERE id = ?
                        """,
                        (identificador,),
                    ).fetchone()
                    if prev:
                        plat_prev = prev[9] if len(prev) > 9 else None
                        dest_prev = prev[10] if len(prev) > 10 else None
                        prev_dict = {
                            "id": prev[0], "fecha_hora_utc": prev[1], "servicio": prev[2],
                            "monto_carga": prev[3], "monto_adicional": prev[4], "total_cobrado": prev[5],
                            "pago_efectivo": prev[6], "pago_transferencia": prev[7], "anulado": prev[8],
                            "plataforma_origen": plat_prev,
                            "servicio_destino": dest_prev,
                        }
                    else:
                        prev_dict = {}
                except sqlite3.OperationalError:
                    prev = conexion.execute(
                        """
                        SELECT id, fecha_hora_utc, servicio, monto_carga, monto_adicional,
                               total_cobrado, pago_efectivo, pago_transferencia, anulado
                        FROM cargas_servicios WHERE id = ?
                        """,
                        (identificador,),
                    ).fetchone()
                    if prev:
                        prev_dict = {
                            "id": prev[0], "fecha_hora_utc": prev[1], "servicio": prev[2],
                            "monto_carga": prev[3], "monto_adicional": prev[4], "total_cobrado": prev[5],
                            "pago_efectivo": prev[6], "pago_transferencia": prev[7], "anulado": prev[8],
                        }
                    else:
                        prev_dict = {}

                try:
                    conexion.execute(
                        """
                        UPDATE cargas_servicios
                        SET fecha_hora_utc = ?, servicio = ?, monto_carga = ?,
                            monto_adicional = ?, total_cobrado = ?,
                            pago_efectivo = ?, pago_transferencia = ?,
                            plataforma_origen = ?, servicio_destino = ?
                        WHERE id = ? AND anulado = 0
                        """,
                        (
                            registro["fecha_hora_utc"],
                            registro["servicio"],
                            registro["monto_carga"],
                            registro["monto_adicional"],
                            registro["total_cobrado"],
                            registro["pago_efectivo"],
                            registro["pago_transferencia"],
                            registro.get("plataforma_origen"),
                            registro.get("servicio_destino"),
                            identificador,
                        ),
                    )
                except sqlite3.OperationalError:
                    conexion.execute(
                        """
                        UPDATE cargas_servicios
                        SET fecha_hora_utc = ?, servicio = ?, monto_carga = ?,
                            monto_adicional = ?, total_cobrado = ?,
                            pago_efectivo = ?, pago_transferencia = ?
                        WHERE id = ? AND anulado = 0
                        """,
                        (
                            registro["fecha_hora_utc"],
                            registro["servicio"],
                            registro["monto_carga"],
                            registro["monto_adicional"],
                            registro["total_cobrado"],
                            registro["pago_efectivo"],
                            registro["pago_transferencia"],
                            identificador,
                        ),
                    )

                try:
                    conexion.execute(
                        """
                        INSERT INTO cargas_servicios_auditoria (
                            carga_id, accion, valores_anteriores, valores_nuevos, actor
                        )
                        VALUES (?, 'EDITAR', ?, ?, ?)
                        """,
                        (
                            identificador,
                            json.dumps(prev_dict, ensure_ascii=False),
                            json.dumps(registro, ensure_ascii=False),
                            actor,
                        ),
                    )
                except Exception as e:
                    if "no such table" in str(e).lower() or "cargas_servicios_auditoria" in str(e).lower():
                        raise ErrorEsquemaNoPreparado(
                            f"Error al registrar auditoría de servicio: {str(e)}. Se requiere la tabla 'cargas_servicios_auditoria' (migración 005)."
                        ) from e
                    raise

                import time
                adj_srv_ts = int(time.time() * 1000)
                enqueue_commercial_event(
                    conn=conexion,
                    event_type="SERVICE_ADJUSTED",
                    entity_type="SERVICIO",
                    entity_id=identificador,
                    action_type="ADJUST",
                    idempotency_key=f"COMM_SERVICE_{identificador}_ADJUST_{adj_srv_ts}",
                    payload={
                        "servicio_id": identificador,
                        "servicio": registro["servicio"],
                        "old_monto_carga_cents": to_amount_cents(prev_dict.get("monto_carga")),
                        "old_monto_adicional_cents": to_amount_cents(prev_dict.get("monto_adicional")),
                        "old_total_cobrado_cents": to_amount_cents(prev_dict.get("total_cobrado")),
                        "old_pago_efectivo_cents": to_amount_cents(prev_dict.get("pago_efectivo")),
                        "old_pago_transferencia_cents": to_amount_cents(prev_dict.get("pago_transferencia")),
                        "monto_carga_cents": to_amount_cents(registro["monto_carga"]),
                        "monto_adicional_cents": to_amount_cents(registro["monto_adicional"]),
                        "total_cobrado_cents": to_amount_cents(registro["total_cobrado"]),
                        "pago_efectivo_cents": to_amount_cents(registro["pago_efectivo"]),
                        "pago_transferencia_cents": to_amount_cents(registro["pago_transferencia"]),
                        "plataforma_origen": registro.get("plataforma_origen"),
                        "servicio_destino": registro.get("servicio_destino"),
                        "fecha_hora_utc": registro["fecha_hora_utc"],
                        "actor": actor,
                        "reference_action": "CONFIRM",
                        "reference_idempotency_key": f"COMM_SERVICE_{identificador}_CONFIRM",
                    },
                )

            for registro in preparados_nuevos:
                nid = _insertar_carga(conexion, registro)
                try:
                    conexion.execute(
                        """
                        INSERT INTO cargas_servicios_auditoria (
                            carga_id, accion, valores_anteriores, valores_nuevos, actor
                        )
                        VALUES (?, 'CREAR', NULL, ?, ?)
                        """,
                        (nid, json.dumps(registro, ensure_ascii=False), actor),
                    )
                except Exception as e:
                    if "no such table" in str(e).lower() or "cargas_servicios_auditoria" in str(e).lower():
                        raise ErrorEsquemaNoPreparado(
                            f"Error al registrar auditoría de servicio: {str(e)}. Se requiere la tabla 'cargas_servicios_auditoria' (migración 005)."
                        ) from e
                    raise

                enqueue_commercial_event(
                    conn=conexion,
                    event_type="SERVICE_CONFIRMED",
                    entity_type="SERVICIO",
                    entity_id=nid,
                    action_type="CONFIRM",
                    idempotency_key=f"COMM_SERVICE_{nid}_CONFIRM",
                    payload={
                        "servicio_id": nid,
                        "servicio": registro["servicio"],
                        "monto_carga_cents": to_amount_cents(registro["monto_carga"]),
                        "monto_adicional_cents": to_amount_cents(registro["monto_adicional"]),
                        "total_cobrado_cents": to_amount_cents(registro["total_cobrado"]),
                        "pago_efectivo_cents": to_amount_cents(registro["pago_efectivo"]),
                        "pago_transferencia_cents": to_amount_cents(registro["pago_transferencia"]),
                        "fecha_hora_utc": registro["fecha_hora_utc"],
                        "actor": actor,
                    },
                    created_at=registro["fecha_hora_utc"],
                )

            # Emisión de eventos de anulación para filas dadas de baja
            for identificador in ids_baja:
                prev_info = filas_anuladas.get(identificador, {})
                enqueue_commercial_event(
                    conn=conexion,
                    event_type="SERVICE_VOIDED",
                    entity_type="SERVICIO",
                    entity_id=identificador,
                    action_type="VOID",
                    idempotency_key=f"COMM_SERVICE_{identificador}_VOID",
                    payload={
                        "servicio_id": identificador,
                        "servicio": prev_info.get("SERVICIO"),
                        "monto_carga_cents": to_amount_cents(prev_info.get("MONTO_CARGA")),
                        "monto_adicional_cents": to_amount_cents(prev_info.get("MONTO_ADICIONAL")),
                        "total_cobrado_cents": to_amount_cents(prev_info.get("TOTAL_COBRADO")),
                        "pago_efectivo_cents": to_amount_cents(prev_info.get("PAGO_EFVO")),
                        "pago_transferencia_cents": to_amount_cents(prev_info.get("PAGO_TRANSF")),
                        "actor": actor,
                        "reference_action": "CONFIRM",
                        "reference_idempotency_key": f"COMM_SERVICE_{identificador}_CONFIRM",
                    },
                )

            conexion.commit()
            return filas_anuladas
        except Exception:
            conexion.rollback()
            raise


def obtener_carga_por_id_turso(id_carga: int, conn=None):
    """Recupera una carga puntual por su ID interno."""
    with _conexion(conn) as conexion:
        try:
            fila = conexion.execute(
                """
                SELECT id, fecha_hora_utc, servicio, monto_carga,
                       monto_adicional, total_cobrado, pago_efectivo,
                       pago_transferencia, anulado, idempotency_key,
                       plataforma_origen, servicio_destino
                FROM cargas_servicios
                WHERE id = ?
                """,
                (id_carga,),
            ).fetchone()
            tiene_key = True
            plat_orig = fila[10] if fila and len(fila) > 10 else None
            srv_dest = fila[11] if fila and len(fila) > 11 else None
        except sqlite3.OperationalError:
            try:
                fila = conexion.execute(
                    """
                    SELECT id, fecha_hora_utc, servicio, monto_carga,
                           monto_adicional, total_cobrado, pago_efectivo,
                           pago_transferencia, anulado, idempotency_key
                    FROM cargas_servicios
                    WHERE id = ?
                    """,
                    (id_carga,),
                ).fetchone()
                tiene_key = True
                plat_orig = None
                srv_dest = None
            except sqlite3.OperationalError:
                fila = conexion.execute(
                    """
                    SELECT id, fecha_hora_utc, servicio, monto_carga,
                           monto_adicional, total_cobrado, pago_efectivo,
                           pago_transferencia, anulado
                    FROM cargas_servicios
                    WHERE id = ?
                    """,
                    (id_carga,),
                ).fetchone()
                tiene_key = False
                plat_orig = None
                srv_dest = None

        if not fila:
            return None
        srv = fila[2]
        return {
            COLUMNA_ID_INTERNO: int(fila[0]),
            "FECHA": _fecha_local(fila[1]),
            "FECHA_HORA_UTC": fila[1],
            "SERVICIO": srv,
            "servicio": srv,
            "PLATAFORMA_ORIGEN": plat_orig,
            "plataforma_origen": plat_orig,
            "SERVICIO_DESTINO": srv_dest,
            "servicio_destino": srv_dest,
            "MONTO_CARGA": int(fila[3]),
            "MONTO_ADICIONAL": int(fila[4]),
            "TOTAL_COBRADO": int(fila[5]),
            "PAGO_EFVO": int(fila[6]),
            "PAGO_TRANSF": int(fila[7]),
            "ANULADO": int(fila[8]),
            "IDEMPOTENCY_KEY": fila[9] if tiene_key and len(fila) > 9 else None,
        }


def anular_carga_individual_turso(id_carga: int, actor: str = "sistema", conn=None):
    """Anula lógicamente una carga individual garantizando atomicidad y auditoría."""
    filas = guardar_ediciones_cargas_turso(
        actualizaciones=[],
        ids_anulados=[id_carga],
        nuevas=[],
        actor=actor,
        conn=conn,
    )
    if filas and id_carga in filas:
        return filas[id_carga]
    return {
        COLUMNA_ID_INTERNO: id_carga,
        "id": id_carga,
        "ANULADO": 1,
        "anulado": 1,
    }


def editar_carga_individual_turso(id_carga: int, registro_editado: dict, actor: str = "sistema", conn=None):
    """Actualiza una carga individual garantizando consistencia contable y auditoría."""
    guardar_ediciones_cargas_turso(
        actualizaciones=[(id_carga, registro_editado)],
        ids_anulados=[],
        nuevas=[],
        actor=actor,
        conn=conn,
    )
    return obtener_carga_por_id_turso(id_carga, conn=conn)


def calcular_resumen_cargas_turso(fecha_filtro: str = None, conn=None):
    """Calcula los totales y saldos de servicios conforme a las reglas del sistema anterior.

    El capital de Claro y SUBE (MP) se segrega del PREPAY (SUBE, Personal, Movistar, etc.).
    """
    # El filtro de fecha se resuelve en SQL (rango UTC del día local); los
    # totales se calculan sobre todas las filas del período, sin LIMIT.
    df = cargar_cargas_turso(
        conn=conn, incluir_anuladas=True, fecha=fecha_filtro or None
    )
    if df.empty:
        return {
            "total_operaciones": 0,
            "total_cobrado": 0,
            "total_capital": 0,
            "total_adicional": 0,
            "total_efectivo": 0,
            "total_transferencia": 0,
            "capital_prepay": 0,
            "capital_claro": 0,
            "capital_sube_mp": 0,
            "operaciones_anuladas": 0,
        }

    anuladas = df[df["ANULADO"] == 1] if "ANULADO" in df.columns else pd.DataFrame()
    activas = df[df["ANULADO"] == 0] if "ANULADO" in df.columns else df

    total_op = len(activas)
    total_cobrado = int(activas["TOTAL_COBRADO"].sum()) if not activas.empty else 0
    total_capital = int(activas["MONTO_CARGA"].sum()) if not activas.empty else 0
    total_adicional = int(activas["MONTO_ADICIONAL"].sum()) if not activas.empty else 0
    total_efvo = int(activas["PAGO_EFVO"].sum()) if not activas.empty else 0
    total_transf = int(activas["PAGO_TRANSF"].sum()) if not activas.empty else 0

    capital_prepay = 0
    capital_claro = 0
    capital_sube_mp = 0

    for _, row in activas.iterrows():
        categoria_srv = clasificar_servicio(
            row.get("SERVICIO", ""),
            row.get("PLATAFORMA_ORIGEN") or row.get("plataforma_origen"),
        )
        cap = int(row.get("MONTO_CARGA", 0))
        if categoria_srv == "CLARO":
            capital_claro += cap
        elif categoria_srv == "SUBE_MP":
            capital_sube_mp += cap
        else:
            capital_prepay += cap

    return {
        "total_operaciones": total_op,
        "total_cobrado": total_cobrado,
        "total_capital": total_capital,
        "total_adicional": total_adicional,
        "total_efectivo": total_efvo,
        "total_transferencia": total_transf,
        "capital_prepay": capital_prepay,
        "capital_claro": capital_claro,
        "capital_sube_mp": capital_sube_mp,
        "operaciones_anuladas": len(anuladas),
    }
