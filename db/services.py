"""Acceso a cargas de servicios en Turso DEV.

La conexión se inyecta en las operaciones de datos o se abre de forma
perezosa y restringida al endpoint de desarrollo autorizado.
"""
import os
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd


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

    return turso_serverless.connect(url, auth_token=token)


@contextmanager
def _conexion(conn=None):
    propia = conn is None
    actual = _abrir_conexion_dev() if propia else conn
    try:
        yield actual
    finally:
        if propia:
            actual.close()


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
    servicio = str(registro.get("SERVICIO", "") or "").strip()
    if not servicio:
        raise ErrorCargasTurso("El servicio es obligatorio.")

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

    return {
        "fecha_hora_utc": fecha,
        "servicio": servicio,
        "monto_carga": carga,
        "monto_adicional": adicional,
        "total_cobrado": total,
        "pago_efectivo": efectivo,
        "pago_transferencia": transferencia,
    }


def _insertar_carga(conn, registro):
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
    rowid = getattr(cursor, "lastrowid", None)
    if rowid is None:
        fila = conn.execute("SELECT last_insert_rowid()").fetchone()
        rowid = fila[0]
    return int(rowid)


def cargar_cargas_turso(conn=None, incluir_anuladas=False):
    """Devuelve las cargas con los nombres de columna que usa la interfaz."""
    condicion = "" if incluir_anuladas else "WHERE anulado = 0"
    query = f"""
        SELECT id, fecha_hora_utc, servicio, monto_carga,
               monto_adicional, total_cobrado, pago_efectivo,
               pago_transferencia
        FROM cargas_servicios
        {condicion}
        ORDER BY fecha_hora_utc DESC, id DESC
    """

    with _conexion(conn) as conexion:
        filas = conexion.execute(query).fetchall()

    registros = []
    for fila in filas:
        registros.append(
            {
                COLUMNA_ID_INTERNO: int(fila[0]),
                "FECHA": _fecha_local(fila[1]),
                "SERVICIO": fila[2],
                "MONTO_CARGA": int(fila[3]),
                "MONTO_ADICIONAL": int(fila[4]),
                "TOTAL_COBRADO": int(fila[5]),
                "PAGO_EFVO": int(fila[6]),
                "PAGO_TRANSF": int(fila[7]),
            }
        )

    return pd.DataFrame(
        registros,
        columns=[COLUMNA_ID_INTERNO, *COLUMNAS_CARGAS_POS],
    )


def registrar_carga_turso(registro, conn=None):
    """Inserta una carga nueva y retorna su ID interno."""
    normalizado = _normalizar_registro(registro)

    with _conexion(conn) as conexion:
        try:
            conexion.execute("BEGIN IMMEDIATE")
            nuevo_id = _insertar_carga(conexion, normalizado)
            conexion.commit()
            return nuevo_id
        except Exception:
            conexion.rollback()
            raise


def guardar_ediciones_cargas_turso(
    actualizaciones,
    ids_anulados,
    nuevas,
    conn=None,
):
    """Guarda en una transacción ediciones, anulaciones e inserciones nuevas.

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

            for identificador in ids_baja:
                conexion.execute(
                    """
                    UPDATE cargas_servicios
                    SET anulado = 1
                    WHERE id = ? AND anulado = 0
                    """,
                    (identificador,),
                )

            for identificador, registro in preparados:
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

            for registro in preparados_nuevos:
                _insertar_carga(conexion, registro)

            conexion.commit()
        except Exception:
            conexion.rollback()
            raise
