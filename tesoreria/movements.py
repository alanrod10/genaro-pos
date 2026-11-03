"""Gestión de Movimientos de Entrada y Salida — GENARO TESORERÍA LABORATORIO.

Implementación de LAB-005:
- registerEntry (Ingreso directo de fondos)
- registerExit (Egreso directo de fondos)
- Idempotencia determinista y transaccionalidad estricta.
- Contrapartida económica obligatoria y justificación.
- Inmutabilidad protegida por triggers SQLite y validaciones de negocio.
"""

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Dict, List, Optional
import uuid

from tesoreria.catalog import obtener_fondo_por_codigo
from tesoreria.idempotency import (
    ErrorIdempotencia,
    calcular_hash_solicitud,
    registrar_idempotencia,
    verificar_idempotencia,
)
from tesoreria.jornadas import obtener_jornada_actual
from tesoreria.money import addMoney, assertValidMoney, formatMoney, subtractMoney


class ErrorMovimiento(Exception):
    """Excepción de regla de negocio o validación para movimientos."""
    pass


class ErrorSaldoInsuficiente(ErrorMovimiento):
    """Excepción cuando un egreso excede el saldo disponible."""
    pass


def _obtener_saldo_reservado_fondo(conn: sqlite3.Connection, fondo_id: int) -> int:
    """Calcula la suma de centavos pendientes reservados para un fondo."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COALESCE(SUM(pending_amount_cents), 0)
        FROM tesoreria_reservas
        WHERE fund_id = ? AND status IN ('ACTIVE', 'PARTIALLY_APPLIED')
        """,
        (fondo_id,),
    )
    res = cursor.fetchone()[0]
    cursor.close()
    return int(res)


def _registrar_auditoria(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    action: str,
    user_id: str,
    details: Dict[str, Any],
    timestamp_utc: str,
) -> None:
    """Inserta registro inmutable en tesoreria_auditoria."""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO tesoreria_auditoria (
            entity_type, entity_id, action, user_id, details_json, timestamp_utc
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            entity_type,
            entity_id,
            action,
            user_id,
            json.dumps(details, sort_keys=True, default=str),
            timestamp_utc,
        ),
    )
    cursor.close()


def registrar_movimiento(
    conn: sqlite3.Connection,
    tipo: str,  # 'ENTRY' o 'EXIT'
    codigo_fondo: str,
    monto_centavos: int,
    contrapartida: str,
    motivo: str,
    usuario: str,
    terminal_id: str = "TERM-01",
    idempotency_key: Optional[str] = None,
    referencia_evidencia: Optional[str] = None,
    fecha_ocurrencia_iso: Optional[str] = None,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Registra una entrada o salida atómica en el laboratorio de tesorería."""
    assertValidMoney(monto_centavos)
    if monto_centavos <= 0:
        raise ErrorMovimiento(f"El importe debe ser estrictamente positivo (> 0), recibido: {monto_centavos}")

    if not motivo or not motivo.strip():
        raise ErrorMovimiento("El motivo del movimiento es obligatorio.")

    if not contrapartida or not contrapartida.strip():
        raise ErrorMovimiento("La contrapartida económica es obligatoria (ej: Aporte, Pago proveedor).")

    if not usuario or not usuario.strip():
        raise ErrorMovimiento("La identidad del usuario autenticado es requerida.")

    if tipo not in ("ENTRY", "EXIT"):
        raise ErrorMovimiento(f"Tipo de movimiento no admitido: {tipo}")

    # 1. Comprobación de idempotencia antes de abrir transacción
    payload_hash = calcular_hash_solicitud({
        "tipo": tipo,
        "codigo_fondo": codigo_fondo,
        "monto_centavos": monto_centavos,
        "contrapartida": contrapartida.strip(),
        "motivo": motivo.strip(),
        "usuario": usuario.strip(),
    })

    previo = verificar_idempotencia(conn, idempotency_key, tipo, payload_hash)
    if previo is not None:
        return previo

    # 2. Validar jornada activa
    jornada = obtener_jornada_actual(conn)
    if not jornada:
        raise ErrorMovimiento("Operación rechazada: Se requiere una jornada de tesorería ABIERTA.")
    if jornada["estado"] != "ABIERTA":
        raise ErrorMovimiento(f"Operación rechazada: La jornada actual está en estado '{jornada['estado']}'.")

    # 3. Validar fondo
    fondo = obtener_fondo_por_codigo(conn, codigo_fondo)
    if not fondo:
        raise ErrorMovimiento(f"Fondo no encontrado: {codigo_fondo}")
    if fondo["estado"] != "ACTIVO":
        raise ErrorMovimiento(f"El fondo {codigo_fondo} no está habilitado (estado: {fondo['estado']}).")

    ahora_utc = datetime.now(timezone.utc).isoformat()
    ocurrido = fecha_ocurrencia_iso or ahora_utc
    movement_id = str(uuid.uuid4())

    cursor = conn.cursor()
    try:
        # En caso de EGRESO, validar saldo registrado y disponibilidad (considerando reservas)
        saldo_actual = fondo["saldo_centavos"]
        saldo_reservado = _obtener_saldo_reservado_fondo(conn, fondo["id"])
        disponible_actual = subtractMoney(saldo_actual, saldo_reservado)

        if tipo == "EXIT":
            if disponible_actual < monto_centavos and fondo["permite_sobregiro"] == 0:
                raise ErrorSaldoInsuficiente(
                    f"Saldo disponible insuficiente en fondo {codigo_fondo}: "
                    f"Disponible {formatMoney(disponible_actual)} (Registrado {formatMoney(saldo_actual)} - Reservado {formatMoney(saldo_reservado)}) "
                    f"para egreso de {formatMoney(monto_centavos)}."
                )
            nuevo_saldo = subtractMoney(saldo_actual, monto_centavos)
        else:
            nuevo_saldo = addMoney(saldo_actual, monto_centavos)

        # Actualizar saldo del fondo
        cursor.execute(
            """
            UPDATE tesoreria_fondos
            SET saldo_centavos = ?,
                version = version + 1,
                updated_at = ?,
                actualizado_por = ?
            WHERE id = ? AND version = ?
            """,
            (nuevo_saldo, ahora_utc, usuario.strip(), fondo["id"], fondo["version"]),
        )
        if cursor.rowcount == 0:
            raise ErrorMovimiento("Conflicto de concurrencia al actualizar saldo del fondo.")

        # Insertar movimiento inmutable
        cursor.execute(
            """
            INSERT INTO tesoreria_movimientos (
                movement_id, idempotency_key, journal_id, fund_id, movement_type,
                amount_cents, currency, counterpart, reason, evidence_reference,
                created_by, terminal_id, status, occurred_at, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'ARS', ?, ?, ?, ?, ?, 'POSTED', ?, ?)
            """,
            (
                movement_id,
                idempotency_key,
                jornada["id"],
                fondo["id"],
                tipo,
                monto_centavos,
                contrapartida.strip(),
                motivo.strip(),
                referencia_evidencia,
                usuario.strip(),
                terminal_id,
                ocurrido,
                ahora_utc,
            ),
        )

        # Auditoría en la misma transacción
        _registrar_auditoria(
            conn,
            entity_type="MOVIMIENTO",
            entity_id=movement_id,
            action=f"POST_{tipo}",
            user_id=usuario.strip(),
            details={
                "movement_id": movement_id,
                "codigo_fondo": codigo_fondo,
                "tipo": tipo,
                "monto_centavos": monto_centavos,
                "saldo_previo": saldo_actual,
                "saldo_posterior": nuevo_saldo,
                "counterpart": contrapartida.strip(),
                "reason": motivo.strip(),
            },
            timestamp_utc=ahora_utc,
        )

        resultado = {
            "movement_id": movement_id,
            "idempotency_key": idempotency_key,
            "journal_id": jornada["id"],
            "fund_id": fondo["id"],
            "codigo_fondo": codigo_fondo,
            "nombre_fondo": fondo["nombre"],
            "movement_type": tipo,
            "amount_cents": monto_centavos,
            "amount_formatted": formatMoney(monto_centavos),
            "currency": "ARS",
            "counterpart": contrapartida.strip(),
            "reason": motivo.strip(),
            "evidence_reference": referencia_evidencia,
            "created_by": usuario.strip(),
            "terminal_id": terminal_id,
            "status": "POSTED",
            "saldo_anterior_centavos": saldo_actual,
            "saldo_posterior_centavos": nuevo_saldo,
            "occurred_at": ocurrido,
            "recorded_at": ahora_utc,
        }

        # Registrar clave de idempotencia
        registrar_idempotencia(
            conn,
            idempotency_key=idempotency_key,
            operation_type=tipo,
            resource_id=movement_id,
            payload_hash=payload_hash,
            response_data=resultado,
            status="SUCCESS",
            created_at_iso=ahora_utc,
        )

        if auto_commit:
            conn.commit()
        return resultado

    except Exception:
        if auto_commit:
            conn.rollback()
        raise
    finally:
        cursor.close()


def registerEntry(
    conn: sqlite3.Connection,
    fund_code: str,
    amount_cents: int,
    counterpart: str,
    reason: str,
    created_by: str,
    terminal_id: str = "TERM-01",
    idempotency_key: Optional[str] = None,
    evidence_reference: Optional[str] = None,
    occurred_at: Optional[str] = None,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Comando LAB-005: Registra una entrada de fondos."""
    return registrar_movimiento(
        conn=conn,
        tipo="ENTRY",
        codigo_fondo=fund_code,
        monto_centavos=amount_cents,
        contrapartida=counterpart,
        motivo=reason,
        usuario=created_by,
        terminal_id=terminal_id,
        idempotency_key=idempotency_key,
        referencia_evidencia=evidence_reference,
        fecha_ocurrencia_iso=occurred_at,
        auto_commit=auto_commit,
    )


def registerExit(
    conn: sqlite3.Connection,
    fund_code: str,
    amount_cents: int,
    counterpart: str,
    reason: str,
    created_by: str,
    terminal_id: str = "TERM-01",
    idempotency_key: Optional[str] = None,
    evidence_reference: Optional[str] = None,
    occurred_at: Optional[str] = None,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Comando LAB-005: Registra una salida de fondos."""
    return registrar_movimiento(
        conn=conn,
        tipo="EXIT",
        codigo_fondo=fund_code,
        monto_centavos=amount_cents,
        contrapartida=counterpart,
        motivo=reason,
        usuario=created_by,
        terminal_id=terminal_id,
        idempotency_key=idempotency_key,
        referencia_evidencia=evidence_reference,
        fecha_ocurrencia_iso=occurred_at,
        auto_commit=auto_commit,
    )


def listar_movimientos(
    conn: sqlite3.Connection,
    codigo_fondo: Optional[str] = None,
    jornada_id: Optional[int] = None,
    movement_type: Optional[str] = None,
    search: Optional[str] = None,
    limite: int = 50,
) -> List[Dict[str, Any]]:
    """Consulta el historial inmutable de movimientos de tesorería con trazabilidad de reversiones y búsqueda."""
    cursor = conn.cursor()
    query = """
        SELECT m.id, m.movement_id, m.idempotency_key, m.journal_id, m.fund_id,
               f.codigo as codigo_fondo, f.nombre as nombre_fondo,
               m.movement_type, m.amount_cents, m.currency, m.counterpart,
               m.reason, m.evidence_reference, m.created_by, m.terminal_id,
               m.status, m.occurred_at, m.recorded_at,
               m.is_reversal, m.reversal_of_id, m.reversed_amount_cents
        FROM tesoreria_movimientos m
        JOIN tesoreria_fondos f ON m.fund_id = f.id
        WHERE 1=1
    """
    params: List[Any] = []
    if codigo_fondo:
        query += " AND f.codigo = ?"
        params.append(codigo_fondo)
    if jornada_id:
        query += " AND m.journal_id = ?"
        params.append(jornada_id)
    if movement_type:
        query += " AND m.movement_type = ?"
        params.append(movement_type.upper())
    if search and search.strip():
        s = f"%{search.strip()}%"
        query += " AND (m.reason LIKE ? OR m.counterpart LIKE ? OR m.evidence_reference LIKE ? OR m.movement_id LIKE ?)"
        params.extend([s, s, s, s])

    query += " ORDER BY m.id DESC LIMIT ?"
    params.append(limite)

    cursor.execute(query, tuple(params))
    filas = cursor.fetchall()
    cursor.close()

    resultado = []
    for r in filas:
        d = dict(r)
        d["amount_formatted"] = formatMoney(d["amount_cents"])
        resultado.append(d)
    return resultado
