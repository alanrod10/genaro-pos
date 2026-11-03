"""Gestión de Jornadas de Tesorería — GENARO TESORERÍA LABORATORIO.

Implementación de LAB-004:
- Control de ciclo de vida de jornada: ABIERTA, EN_ARQUEO, CERRADA.
- Garantía de única jornada activa simultánea.
- Registro inmutable de saldos de apertura y cierre por fondo en centavos enteros.
- Cálculo determinista de diferencias de arqueo con subtractMoney.
- Sin saldos inventados ni importación histórica.
"""

from datetime import datetime, timezone
import sqlite3
from typing import Any, Dict, List, Optional
from tesoreria.catalog import obtener_catalogo_fondos
from tesoreria.money import addMoney, assertValidMoney, subtractMoney


class ErrorJornada(Exception):
    """Excepción para violaciones de reglas de negocio en jornadas de tesorería."""
    pass


def obtener_jornada_actual(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    """Retorna la jornada de tesorería actualmente activa (ABIERTA o EN_ARQUEO), o None si no hay ninguna."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, numero_jornada, fecha_operativa, estado,
               apertura_timestamp_utc, cierre_timestamp_utc,
               usuario_apertura, usuario_cierre,
               total_apertura_centavos, total_cierre_centavos,
               observaciones, version
        FROM tesoreria_jornadas
        WHERE estado IN ('ABIERTA', 'EN_ARQUEO', 'EN_CIERRE')
        ORDER BY id DESC
        LIMIT 1
        """
    )
    fila = cursor.fetchone()
    cursor.close()

    if not fila:
        return None

    jornada = dict(fila)
    # Cargar detalles por fondo
    jornada["detalles"] = obtener_detalles_jornada(conn, jornada["id"])
    return jornada


def obtener_detalles_jornada(conn: sqlite3.Connection, jornada_id: int) -> List[Dict[str, Any]]:
    """Retorna el desglose de fondos para una jornada específica."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT jd.id, jd.jornada_id, jd.fondo_id, jd.codigo_fondo,
               f.nombre as nombre_fondo, f.naturaleza, f.titularidad,
               jd.saldo_apertura_centavos, jd.saldo_cierre_centavos, jd.diferencia_centavos
        FROM tesoreria_jornada_detalles jd
        JOIN tesoreria_fondos f ON jd.fondo_id = f.id
        WHERE jd.jornada_id = ?
        ORDER BY jd.codigo_fondo ASC
        """,
        (jornada_id,),
    )
    filas = cursor.fetchall()
    cursor.close()
    return [dict(f) for f in filas]


def abrir_jornada(
    conn: sqlite3.Connection,
    usuario: str,
    observaciones: Optional[str] = None,
) -> Dict[str, Any]:
    """Abre una nueva jornada de tesorería.

    Invariantes:
    - No debe existir otra jornada ABIERTA o EN_ARQUEO.
    - Se registra el snapshot de apertura de cada uno de los 9 fondos canónicos.
    - Todos los saldos son centavos enteros (INTEGER).
    """
    if not usuario or not usuario.strip():
        raise ErrorJornada("El usuario de apertura es obligatorio.")

    jornada_activa = obtener_jornada_actual(conn)
    if jornada_activa:
        raise ErrorJornada(
            f"No se puede abrir una nueva jornada: ya existe la jornada #{jornada_activa['numero_jornada']} "
            f"en estado '{jornada_activa['estado']}'."
        )

    fondos = obtener_catalogo_fondos(conn)
    if len(fondos) not in (9, 10):
        raise ErrorJornada(
            f"El catálogo de tesorería no está íntegro: se esperaban 9 o 10 fondos canónicos, se encontraron {len(fondos)}."
        )

    ahora_utc = datetime.now(timezone.utc)
    fecha_hoy = ahora_utc.strftime("%Y-%m-%d")
    timestamp_iso = ahora_utc.isoformat()

    cursor = conn.cursor()
    cursor.execute("SELECT COALESCE(MAX(numero_jornada), 0) + 1 FROM tesoreria_jornadas")
    siguiente_numero = cursor.fetchone()[0]

    # Calcular total de apertura sumando los saldos reales de los fondos
    total_apertura = 0
    for f in fondos:
        total_apertura = addMoney(total_apertura, f["saldo_centavos"])

    cursor.execute(
        """
        INSERT INTO tesoreria_jornadas (
            numero_jornada, fecha_operativa, estado,
            apertura_timestamp_utc, usuario_apertura,
            total_apertura_centavos, observaciones, version
        ) VALUES (?, ?, 'ABIERTA', ?, ?, ?, ?, 1)
        """,
        (
            siguiente_numero,
            fecha_hoy,
            timestamp_iso,
            usuario.strip(),
            total_apertura,
            observaciones,
        ),
    )
    jornada_id = cursor.lastrowid

    # Insertar snapshots de cada fondo
    for f in fondos:
        cursor.execute(
            """
            INSERT INTO tesoreria_jornada_detalles (
                jornada_id, fondo_id, codigo_fondo, saldo_apertura_centavos
            ) VALUES (?, ?, ?, ?)
            """,
            (jornada_id, f["id"], f["codigo"], f["saldo_centavos"]),
        )

    conn.commit()
    cursor.close()

    return obtener_jornada_por_id(conn, jornada_id)


def cerrar_jornada(
    conn: sqlite3.Connection,
    jornada_id: int,
    usuario: str,
    arqueo_centavos_por_fondo: Optional[Dict[str, int]] = None,
    observaciones: Optional[str] = None,
) -> Dict[str, Any]:
    """Cierra la jornada de tesorería y registra el arqueo/corte final.

    - Calcula diferencias entre saldo en libros y saldo arqueado mediante subtractMoney.
    - Cambia estado a 'CERRADA'.
    """
    if not usuario or not usuario.strip():
        raise ErrorJornada("El usuario de cierre es obligatorio.")

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, numero_jornada, estado, total_apertura_centavos
        FROM tesoreria_jornadas
        WHERE id = ?
        """,
        (jornada_id,),
    )
    fila = cursor.fetchone()
    if not fila:
        cursor.close()
        raise ErrorJornada(f"Jornada #{jornada_id} no encontrada.")

    jornada = dict(fila)
    if jornada["estado"] == "CERRADA":
        cursor.close()
        raise ErrorJornada(f"La jornada #{jornada['numero_jornada']} ya se encuentra cerrada.")

    fondos = obtener_catalogo_fondos(conn)
    fondos_map = {f["codigo"]: f for f in fondos}

    arqueo = arqueo_centavos_por_fondo or {}
    total_cierre = 0
    ahora_utc = datetime.now(timezone.utc).isoformat()

    for codigo, f in fondos_map.items():
        saldo_sistema = f["saldo_centavos"]
        # Si se proveyó arqueo físico, tomarlo; sino, tomar saldo de sistema como conforme
        if codigo in arqueo:
            saldo_arqueado = arqueo[codigo]
            assertValidMoney(saldo_arqueado)
        else:
            saldo_arqueado = saldo_sistema

        diferencia = subtractMoney(saldo_arqueado, saldo_sistema)
        total_cierre = addMoney(total_cierre, saldo_arqueado)

        cursor.execute(
            """
            UPDATE tesoreria_jornada_detalles
            SET saldo_cierre_centavos = ?,
                diferencia_centavos = ?
            WHERE jornada_id = ? AND codigo_fondo = ?
            """,
            (saldo_arqueado, diferencia, jornada_id, codigo),
        )

    obs_final = observaciones
    cursor.execute(
        """
        UPDATE tesoreria_jornadas
        SET estado = 'CERRADA',
            cierre_timestamp_utc = ?,
            usuario_cierre = ?,
            total_cierre_centavos = ?,
            observaciones = CASE
                WHEN ? IS NOT NULL THEN COALESCE(observaciones || ' | ' || ?, ?)
                ELSE observaciones
            END,
            version = version + 1
        WHERE id = ?
        """,
        (ahora_utc, usuario.strip(), total_cierre, obs_final, obs_final, obs_final, jornada_id),
    )

    conn.commit()
    cursor.close()
    return obtener_jornada_por_id(conn, jornada_id)


def obtener_jornada_por_id(conn: sqlite3.Connection, jornada_id: int) -> Dict[str, Any]:
    """Retorna una jornada completa con sus detalles."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, numero_jornada, fecha_operativa, estado,
               apertura_timestamp_utc, cierre_timestamp_utc,
               usuario_apertura, usuario_cierre,
               total_apertura_centavos, total_cierre_centavos,
               observaciones, version
        FROM tesoreria_jornadas
        WHERE id = ?
        """,
        (jornada_id,),
    )
    fila = cursor.fetchone()
    cursor.close()

    if not fila:
        raise ErrorJornada(f"Jornada #{jornada_id} no existe.")

    j = dict(fila)
    j["detalles"] = obtener_detalles_jornada(conn, j["id"])
    return j
