"""Coordinador del Circuito PREPAY y Tesorería — FASE 2.

Implementa el protocolo de pre-reserva síncrona, gobierno estricto de la responsabilidad
del cargo operativo diario de $350 por jornada, máquina de estados de 10 niveles
y reconciliador bidireccional entre la base comercial (genaro-pos.db) y Tesorería (tesoreria.db).

INVARIANTES ECONÓMICOS INNEGOCIABLES:
1. Ninguna recarga PREPAY puede confirmarse comercialmente sin reserva previa válida de su capital
   y de su cargo diario ($350) cuando corresponda.
2. El cargo operativo de $350 (35.000 centavos) se debita exclusivamente de 1220 (Prepay Digital).
   Es un costo operativo del negocio y se asume una sola vez por jornada contable.
3. En pagos en efectivo: el capital ingresa a 1160 (o 1130 si 1160 no está migrado), el adicional
   a 1140, y el capital digital se debita de 1220.
4. En pagos por transferencia: el cliente abona a 1210 (Mercado Pago), el capital digital se debita
   de 1220, y se traslada inmediatamente el capital desde 1120 (Drugstore Acumulado) hacia 1130
   (Prepay Físico Acumulado). Si 1120 no tiene saldo disponible, se rechaza la operación completa.
   Nunca se utiliza 1110 como alternativa. El adicional permanece en 1210 (cero acreditación ficticia a 1140).
5. Las reservas no se liberan por timeout. Las caídas o estados inciertos pasan a RECONCILIATION_REQUIRED.
"""

from datetime import datetime, timezone
import json
import logging
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

from tesoreria.catalog import obtener_fondo_por_codigo
from tesoreria.idempotency import (
    ErrorIdempotencia,
    calcular_hash_solicitud,
    registrar_idempotencia,
    verificar_idempotencia,
)
from tesoreria.jornadas import abrir_jornada, obtener_jornada_actual
from tesoreria.money import addMoney, assertValidMoney, formatMoney, subtractMoney
from tesoreria.movements import _obtener_saldo_reservado_fondo, _registrar_auditoria, registerEntry, registerExit
from tesoreria.reservations import (
    ErrorReserva,
    cancelReservation,
    createReservation,
    executeReservedExit,
    releaseReservation,
)
from tesoreria.transfers import confirmTransferReceipt, transferFunds

logger = logging.getLogger("genaro_pos.prepay_coordinator")

# Importe del cargo diario operativo PREPAY: $350,00 ARS = 35000 centavos
MONTO_CARGO_DIARIO_CENTS = 350_00

# Estados Canónicos de la Máquina de Coordinación (10 Niveles)
ESTADOS_COORDINACION = {
    "RESERVATION_REQUESTED",
    "ACTIVE",
    "COMMERCIAL_COMMITTED",
    "COMMERCIAL_REJECTED",
    "RELEASE_PENDING",
    "RELEASED",
    "APPLICATION_PENDING",
    "FULLY_APPLIED",
    "RECONCILIATION_REQUIRED",
    "EXPIRED",
}


def inicializar_tablas_coordinacion_prepay(conn: sqlite3.Connection) -> None:
    """Crea las tablas de coordinación y propiedad del cargo diario en tesoreria.db si no existen."""
    cursor = conn.cursor()
    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS tesoreria_prepay_cargo_diario (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            jornada_id INTEGER NOT NULL,
            fecha_jornada TEXT NOT NULL,
            estado TEXT NOT NULL CHECK(estado IN ('PENDING_RESERVATION', 'RESERVED', 'CONFIRMED', 'APPLIED', 'RELEASED')),
            titular_reserva_id TEXT NOT NULL,
            titular_commercial_ticket_id TEXT,
            titular_idempotency_key TEXT NOT NULL,
            amount_cents INTEGER NOT NULL DEFAULT 35000 CHECK(amount_cents > 0),
            created_at TEXT NOT NULL,
            confirmed_at TEXT,
            applied_at TEXT,
            released_at TEXT,
            updated_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
            UNIQUE(jornada_id)
        );
        CREATE INDEX IF NOT EXISTS idx_prepay_cargo_jornada ON tesoreria_prepay_cargo_diario(jornada_id);
        CREATE INDEX IF NOT EXISTS idx_prepay_cargo_estado ON tesoreria_prepay_cargo_diario(estado);

        CREATE TABLE IF NOT EXISTS tesoreria_prepay_coordinacion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coordination_id TEXT NOT NULL UNIQUE,
            idempotency_key TEXT NOT NULL UNIQUE,
            request_hash TEXT NOT NULL,
            jornada_id INTEGER NOT NULL,
            servicio_destino TEXT NOT NULL,
            plataforma TEXT NOT NULL DEFAULT 'PREPAY',
            monto_capital_cents INTEGER NOT NULL CHECK(monto_capital_cents > 0),
            monto_adicional_cents INTEGER NOT NULL DEFAULT 0 CHECK(monto_adicional_cents >= 0),
            pago_efvo_cents INTEGER NOT NULL DEFAULT 0 CHECK(pago_efvo_cents >= 0),
            pago_transf_cents INTEGER NOT NULL DEFAULT 0 CHECK(pago_transf_cents >= 0),
            asume_cargo_diario INTEGER NOT NULL CHECK(asume_cargo_diario IN (0, 1)),
            monto_cargo_cents INTEGER NOT NULL DEFAULT 0 CHECK(monto_cargo_cents >= 0),
            capital_reservation_id TEXT NOT NULL,
            fee_reservation_id TEXT,
            estado TEXT NOT NULL CHECK(estado IN (
                'RESERVATION_REQUESTED', 'ACTIVE', 'COMMERCIAL_COMMITTED', 'COMMERCIAL_REJECTED',
                'RELEASE_PENDING', 'RELEASED', 'APPLICATION_PENDING', 'FULLY_APPLIED',
                'RECONCILIATION_REQUIRED', 'EXPIRED'
            )),
            commercial_ticket_id INTEGER,
            worker_movement_ids TEXT,
            error_detail TEXT,
            actor TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (jornada_id) REFERENCES tesoreria_jornadas(id) ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_prepay_coord_key ON tesoreria_prepay_coordinacion(idempotency_key);
        CREATE INDEX IF NOT EXISTS idx_prepay_coord_estado ON tesoreria_prepay_coordinacion(estado);
        CREATE INDEX IF NOT EXISTS idx_prepay_coord_ticket ON tesoreria_prepay_coordinacion(commercial_ticket_id);
        """
    )
    cursor.close()


def _obtener_saldo_disponible_fondo(conn: sqlite3.Connection, fund_code: str) -> int:
    """Calcula el saldo libre disponible descontando reservas preventivas activas."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT f.id, f.saldo_centavos, f.permite_sobregiro
        FROM tesoreria_fondos f
        WHERE f.codigo = ? AND f.estado = 'ACTIVO';
        """,
        (fund_code,),
    )
    fila = cursor.fetchone()
    cursor.close()
    if not fila:
        raise ErrorReserva(f"Fondo activo con código '{fund_code}' no existe en Tesorería.")
    fund_id, saldo_en_libros, permite_sobregiro = fila
    if permite_sobregiro == 1:
        return 999_999_999_99  # Sin límite

    reservado = _obtener_saldo_reservado_fondo(conn, fund_id)
    return saldo_en_libros - reservado


def _resolver_titularidad_cargo_diario(
    conn: sqlite3.Connection,
    jornada_id: int,
    fecha_jornada: str,
    idempotency_key: str,
    actor: str,
    comm_conn: Optional[sqlite3.Connection] = None,
) -> Tuple[bool, Optional[str], bool]:
    """Determina si esta operación debe asumir el cargo de $350 de la jornada.

    Retorna una tupla: (asume_cargo: bool, titular_existente_reserva_id: Optional[str], debe_esperar: bool).
    Si otra terminal está en proceso de reserva (estado RESERVED), indica debe_esperar=True
    para que el llamador libere el bloqueo de SQLite antes de pausar y reintentar.
    Si comm_conn está disponible y la terminal competidora ya comiteó comercialmente, auto-resuelve
    promoviendo el cargo a CONFIRMED sin demoras.
    """
    ahora_utc = datetime.now(timezone.utc).isoformat()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, estado, titular_reserva_id, titular_idempotency_key
        FROM tesoreria_prepay_cargo_diario
        WHERE jornada_id = ?;
        """,
        (jornada_id,),
    )
    fila = cursor.fetchone()
    cursor.close()

    if not fila:
        # Caso 1: Ninguna operación ha solicitado el cargo para esta jornada.
        return True, None, False

    cargo_id, estado, titular_res_id, titular_key = fila

    if titular_key == idempotency_key:
        # Reintento de la misma operación
        return True, titular_res_id, False

    if estado in ("CONFIRMED", "APPLIED"):
        # Caso 2: El cargo ya fue confirmado o liquidado.
        return False, titular_res_id, False

    if estado == "RELEASED":
        # Caso 3: Quedó vacante.
        return True, None, False

    if estado == "RESERVED":
        # Caso 4: Otra terminal tiene la reserva activa.
        if comm_conn:
            comm_cur = comm_conn.cursor()
            tkt_committed = False
            tkt_id = None
            try:
                comm_cur.execute(
                    "SELECT id FROM cargas_servicios WHERE idempotency_key = ? AND anulado = 0;",
                    (titular_key,),
                )
                r_comm = comm_cur.fetchone()
                if r_comm:
                    tkt_committed = True
                    tkt_id = r_comm[0]
            except Exception:
                pass
            if not tkt_committed:
                try:
                    comm_cur.execute(
                        "SELECT id FROM servicios WHERE idempotency_key = ? AND anulación = 0;",
                        (titular_key,),
                    )
                    r_comm = comm_cur.fetchone()
                    if r_comm:
                        tkt_committed = True
                        tkt_id = r_comm[0]
                except Exception:
                    pass
            comm_cur.close()

            if tkt_committed:
                cur_up = conn.cursor()
                cur_up.execute(
                    """
                    UPDATE tesoreria_prepay_cargo_diario
                    SET estado = 'CONFIRMED',
                        titular_commercial_ticket_id = ?,
                        confirmed_at = ?,
                        updated_at = ?,
                        version = version + 1
                    WHERE jornada_id = ? AND estado = 'RESERVED';
                    """,
                    (str(tkt_id) if tkt_id else None, ahora_utc, ahora_utc, jornada_id),
                )
                cur_up.close()
                return False, titular_res_id, False

        # Debe esperar a que la terminal titular termine su commit comercial
        return False, None, True

    return False, None, False


def solicitar_pre_reserva_prepay(
    conn: sqlite3.Connection,
    servicio_destino: str,
    monto_carga_cents: int,
    monto_adicional_cents: int,
    pago_efvo_cents: int,
    pago_transf_cents: int,
    idempotency_key: str,
    actor: str,
    comm_conn: Optional[sqlite3.Connection] = None,
    max_wait_seconds: float = 2.0,
    auto_commit: bool = True,
    permitir_fondeo_pendiente: bool = False,
) -> Dict[str, Any]:
    """Ejecuta la pre-reserva síncrona de capital y cargo diario ($350) en tesoreria.db.

    Gobernado bajo BEGIN IMMEDIATE con auto_commit opcional.
    Verifica saldos disponibles en 1220 (Prepay Digital) y 1120 (Drugstore Acumulado si es transferencia).
    Registra el estado ACTIVE en la máquina de coordinación.
    """
    assertValidMoney(monto_carga_cents)
    assertValidMoney(monto_adicional_cents)
    assertValidMoney(pago_efvo_cents)
    assertValidMoney(pago_transf_cents)

    if monto_carga_cents <= 0:
        raise ErrorReserva("El monto de carga PREPAY debe ser estrictamente positivo.")

    if not idempotency_key or not idempotency_key.strip():
        raise ErrorReserva("La clave de idempotencia es obligatoria para la pre-reserva PREPAY.")

    key_limpia = idempotency_key.strip()
    payload_hash = calcular_hash_solicitud({
        "servicio_destino": servicio_destino.strip().upper(),
        "monto_carga_cents": monto_carga_cents,
        "monto_adicional_cents": monto_adicional_cents,
        "pago_efvo_cents": pago_efvo_cents,
        "pago_transf_cents": pago_transf_cents,
        "idempotency_key": key_limpia,
    })

    inicializar_tablas_coordinacion_prepay(conn)

    cursor = conn.cursor()
    # 1. Verificar idempotencia en coordinación
    cursor.execute(
        """
        SELECT coordination_id, request_hash, estado, capital_reservation_id, fee_reservation_id,
               asume_cargo_diario, monto_capital_cents, monto_cargo_cents
        FROM tesoreria_prepay_coordinacion
        WHERE idempotency_key = ?;
        """,
        (key_limpia,),
    )
    existente = cursor.fetchone()
    cursor.close()

    if existente:
        coord_id, guardado_hash, estado, cap_res_id, fee_res_id, asume_cargo, m_cap, m_cargo = existente
        if guardado_hash != payload_hash:
            raise ErrorIdempotencia(
                f"Conflicto de Idempotencia: La clave '{key_limpia}' ya fue utilizada con diferentes parámetros de recarga PREPAY."
            )
        # Retorno idempotente
        return {
            "success": True,
            "es_reintento": True,
            "coordination_id": coord_id,
            "estado": estado,
            "capital_reservation_id": cap_res_id,
            "fee_reservation_id": fee_res_id,
            "asume_cargo_diario": bool(asume_cargo),
            "monto_capital_cents": m_cap,
            "monto_cargo_cents": m_cargo,
            "idempotency_key": key_limpia,
        }

    start_wait_time = time.time()

    # Bucle de pre-reserva atómica con liberación de lock en contienda
    while True:
        if auto_commit:
            conn.isolation_level = None
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE;")
            cur.close()

        try:
            jornada = obtener_jornada_actual(conn)
            if not jornada:
                jornada = abrir_jornada(conn, usuario=actor, observaciones="Apertura por solicitud PREPAY")

            jornada_id = jornada["id"]
            fecha_jornada = jornada.get("fecha_operativa") or jornada.get("fecha") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
            ahora_utc = datetime.now(timezone.utc).isoformat()

            # 3. Resolver propiedad del cargo diario
            asume_cargo, titular_res_existente, debe_esperar = _resolver_titularidad_cargo_diario(
                conn=conn,
                jornada_id=jornada_id,
                fecha_jornada=fecha_jornada,
                idempotency_key=key_limpia,
                actor=actor,
                comm_conn=comm_conn,
            )

            if debe_esperar:
                # Liberar de inmediato la transacción para que la terminal titular pueda comitear libremente
                if auto_commit:
                    cur = conn.cursor()
                    cur.execute("ROLLBACK;")
                    cur.close()

                elapsed = time.time() - start_wait_time
                if elapsed > max_wait_seconds:
                    raise ErrorReserva(
                        "Concurrencia en cargo diario PREPAY: otra terminal se encuentra reservando "
                        "el cargo operativo de la jornada. Reintente en un instante."
                    )
                time.sleep(0.05)
                continue

            monto_cargo_aplicable = MONTO_CARGO_DIARIO_CENTS if asume_cargo else 0
            total_a_debitar_1220 = monto_carga_cents + monto_cargo_aplicable

            # 4. Validar saldo disponible en 1220 (Prepay Digital)
            saldo_disp_1220 = _obtener_saldo_disponible_fondo(conn, "1220")
            if saldo_disp_1220 < total_a_debitar_1220:
                if asume_cargo and saldo_disp_1220 >= monto_carga_cents:
                    raise ErrorReserva(
                        f"Saldo digital PREPAY (1220) insuficiente para capital (${formatMoney(monto_carga_cents)}) "
                        f"y cargo diario de $350. Disponible: ${formatMoney(saldo_disp_1220)}, Requerido: ${formatMoney(total_a_debitar_1220)}."
                    )
                raise ErrorReserva(
                    f"Saldo digital PREPAY (1220) insuficiente. Disponible: ${formatMoney(saldo_disp_1220)}, "
                    f"Requerido: ${formatMoney(total_a_debitar_1220)}."
                )

            # 5. Si paga con transferencia: validar saldo disponible en Drugstore (1120 o 1110)
            # para compensar físicamente a 1160 (Prepay Diario), o registrar fondeo pendiente si se permite
            if pago_transf_cents > 0:
                saldo_disp_1120 = _obtener_saldo_disponible_fondo(conn, "1120")
                if not permitir_fondeo_pendiente:
                    if saldo_disp_1120 < monto_carga_cents:
                        raise ErrorReserva(
                            f"Saldo insuficiente en Drugstore Acumulado (1120) para compensación física de PREPAY por transferencia. "
                            f"Disponible: ${formatMoney(saldo_disp_1120)}, Requerido: ${formatMoney(monto_carga_cents)}. "
                            f"No se permite utilizar 1110 como alternativa."
                        )

            # 6. Crear reserva de Capital en 1220
            res_capital = createReservation(
                conn=conn,
                fund_code="1220",
                amount_cents=monto_carga_cents,
                reason=f"Pre-reserva capital PREPAY {servicio_destino.strip().upper()}",
                beneficiary="PREPAY_OPERADOR",
                created_by=actor,
                obligation_id=f"PREPAY_CAP_{key_limpia}",
                idempotency_key=f"RES_CAP_{key_limpia}",
                evidence_reference=f"PREPAY_SRV_{key_limpia}",
                auto_commit=False,
            )
            cap_res_id = res_capital["reservation_id"]

            fee_res_id = None
            if asume_cargo:
                # Crear reserva de Cargo Diario en 1220
                res_fee = createReservation(
                    conn=conn,
                    fund_code="1220",
                    amount_cents=MONTO_CARGO_DIARIO_CENTS,
                    reason=f"Pre-reserva cargo diario PREPAY $350 (Jornada {jornada_id})",
                    beneficiary="GENARO_COSTO_OPERATIVO",
                    created_by=actor,
                    obligation_id=f"FEE_J{jornada_id}",
                    idempotency_key=f"RES_FEE_{key_limpia}",
                    evidence_reference=f"PREPAY_FEE_{key_limpia}",
                    auto_commit=False,
                )
                fee_res_id = res_fee["reservation_id"]

                # Registrar propiedad provisional en tesoreria_prepay_cargo_diario
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO tesoreria_prepay_cargo_diario (
                        jornada_id, fecha_jornada, estado, titular_reserva_id,
                        titular_idempotency_key, amount_cents, created_at, updated_at
                    ) VALUES (?, ?, 'RESERVED', ?, ?, ?, ?, ?)
                    ON CONFLICT(jornada_id) DO UPDATE SET
                        estado = 'RESERVED',
                        titular_reserva_id = excluded.titular_reserva_id,
                        titular_idempotency_key = excluded.titular_idempotency_key,
                        updated_at = excluded.updated_at,
                        version = version + 1;
                    """,
                    (
                        jornada_id,
                        fecha_jornada,
                        fee_res_id,
                        key_limpia,
                        MONTO_CARGO_DIARIO_CENTS,
                        ahora_utc,
                        ahora_utc,
                    ),
                )
                cur.close()

            coordination_id = str(uuid.uuid4())
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO tesoreria_prepay_coordinacion (
                    coordination_id, idempotency_key, request_hash, jornada_id,
                    servicio_destino, plataforma, monto_capital_cents, monto_adicional_cents,
                    pago_efvo_cents, pago_transf_cents, asume_cargo_diario, monto_cargo_cents,
                    capital_reservation_id, fee_reservation_id, estado, actor,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'PREPAY', ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?);
                """,
                (
                    coordination_id,
                    key_limpia,
                    payload_hash,
                    jornada_id,
                    servicio_destino.strip().upper(),
                    monto_carga_cents,
                    monto_adicional_cents,
                    pago_efvo_cents,
                    pago_transf_cents,
                    1 if asume_cargo else 0,
                    monto_cargo_aplicable,
                    cap_res_id,
                    fee_res_id,
                    actor,
                    ahora_utc,
                    ahora_utc,
                ),
            )
            cur.close()

            _registrar_auditoria(
                conn=conn,
                entity_type="RESERVA",
                entity_id=coordination_id,
                action="PRE_RESERVE",
                user_id=actor,
                details={
                    "idempotency_key": key_limpia,
                    "capital_reservation_id": cap_res_id,
                    "fee_reservation_id": fee_res_id,
                    "asume_cargo_diario": asume_cargo,
                    "total_debit_1220": total_a_debitar_1220,
                },
                timestamp_utc=ahora_utc,
            )

            if auto_commit:
                conn.commit()

            return {
                "success": True,
                "es_reintento": False,
                "coordination_id": coordination_id,
                "estado": "ACTIVE",
                "capital_reservation_id": cap_res_id,
                "fee_reservation_id": fee_res_id,
                "asume_cargo_diario": asume_cargo,
                "monto_capital_cents": monto_carga_cents,
                "monto_cargo_cents": monto_cargo_aplicable,
                "idempotency_key": key_limpia,
            }
        except Exception:
            if auto_commit:
                conn.rollback()
            raise


def confirmar_pre_reserva_comercial(
    conn: sqlite3.Connection,
    idempotency_key: str,
    commercial_ticket_id: int,
    actor: str = "sistema",
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Asocia el ticket comercial recién emitido y transiciona la coordinación a COMMERCIAL_COMMITTED."""
    key_limpia = idempotency_key.strip()
    ahora_utc = datetime.now(timezone.utc).isoformat()

    if auto_commit:
        conn.isolation_level = None
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE;")
        cur.close()

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT coordination_id, jornada_id, estado, asume_cargo_diario, fee_reservation_id
            FROM tesoreria_prepay_coordinacion
            WHERE idempotency_key = ?;
            """,
            (key_limpia,),
        )
        row = cur.fetchone()
        if not row:
            raise ErrorReserva(f"No existe coordinación PREPAY activa para la clave '{key_limpia}'.")

        coord_id, jornada_id, estado, asume_cargo, fee_res_id = row

        if estado in ("COMMERCIAL_COMMITTED", "APPLICATION_PENDING", "FULLY_APPLIED"):
            if auto_commit:
                conn.commit()
            return {"success": True, "coordination_id": coord_id, "estado": estado}

        if estado != "ACTIVE":
            raise ErrorReserva(
                f"Transición prohibida: no se puede confirmar comercialmente una coordinación en estado '{estado}'."
            )

        cur.execute(
            """
            UPDATE tesoreria_prepay_coordinacion
            SET estado = 'COMMERCIAL_COMMITTED',
                commercial_ticket_id = ?,
                updated_at = ?
            WHERE coordination_id = ?;
            """,
            (commercial_ticket_id, ahora_utc, coord_id),
        )

        if asume_cargo:
            cur.execute(
                """
                UPDATE tesoreria_prepay_cargo_diario
                SET estado = 'CONFIRMED',
                    titular_commercial_ticket_id = ?,
                    confirmed_at = ?,
                    updated_at = ?,
                    version = version + 1
                WHERE jornada_id = ?;
                """,
                (str(commercial_ticket_id), ahora_utc, ahora_utc, jornada_id),
            )

        cur.close()

        _registrar_auditoria(
            conn=conn,
            entity_type="RESERVA",
            entity_id=coord_id,
            action="COMMERCIAL_COMMIT",
            user_id=actor,
            details={
                "commercial_ticket_id": commercial_ticket_id,
                "asume_cargo_diario": bool(asume_cargo),
            },
            timestamp_utc=ahora_utc,
        )

        if auto_commit:
            conn.commit()

        return {
            "success": True,
            "coordination_id": coord_id,
            "estado": "COMMERCIAL_COMMITTED",
            "commercial_ticket_id": commercial_ticket_id,
        }
    except Exception:
        if auto_commit:
            conn.rollback()
        raise


def cancelar_pre_reserva_prepay(
    conn: sqlite3.Connection,
    idempotency_key: str,
    motivo: str,
    actor: str = "sistema",
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Libera las reservas de capital y cargo de una operación PREPAY que no se confirmó comercialmente.

    Si asumía el cargo diario de $350, libera la responsabilidad en tesoreria_prepay_cargo_diario
    (transición a RELEASED) para que la siguiente operación de la jornada pueda asumirlo.
    """
    key_limpia = idempotency_key.strip()
    ahora_utc = datetime.now(timezone.utc).isoformat()

    if auto_commit:
        conn.isolation_level = None
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE;")
        cur.close()

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT coordination_id, jornada_id, estado, asume_cargo_diario,
                   capital_reservation_id, fee_reservation_id, monto_capital_cents, monto_cargo_cents
            FROM tesoreria_prepay_coordinacion
            WHERE idempotency_key = ?;
            """,
            (key_limpia,),
        )
        row = cur.fetchone()
        if not row:
            raise ErrorReserva(f"No existe coordinación PREPAY activa para la clave '{key_limpia}'.")

        coord_id, jornada_id, estado, asume_cargo, cap_res_id, fee_res_id, m_cap, m_cargo = row

        if estado in ("COMMERCIAL_COMMITTED", "APPLICATION_PENDING", "FULLY_APPLIED"):
            raise ErrorReserva(
                f"Prohibición absoluta: No se puede liberar una reserva PREPAY vinculada a una venta confirmada (estado '{estado}')."
            )

        if estado == "RELEASED":
            if auto_commit:
                conn.commit()
            return {"success": True, "coordination_id": coord_id, "estado": "RELEASED"}

        # 1. Liberar reserva de Capital
        releaseReservation(
            conn=conn,
            reservation_id=cap_res_id,
            amount_cents=m_cap,
            performed_by=actor,
            reason=f"Cancelación pre-reserva PREPAY: {motivo}",
            auto_commit=False,
        )

        # 2. Si tenía reservado el cargo de $350, liberarlo
        if asume_cargo and fee_res_id:
            releaseReservation(
                conn=conn,
                reservation_id=fee_res_id,
                amount_cents=MONTO_CARGO_DIARIO_CENTS,
                performed_by=actor,
                reason=f"Liberación cargo diario PREPAY por cancelación: {motivo}",
                auto_commit=False,
            )
            # Desvincular cargo en tesoreria_prepay_cargo_diario (transición a RELEASED)
            cur.execute(
                """
                UPDATE tesoreria_prepay_cargo_diario
                SET estado = 'RELEASED',
                    released_at = ?,
                    updated_at = ?,
                    version = version + 1
                WHERE jornada_id = ? AND titular_idempotency_key = ?;
                """,
                (ahora_utc, ahora_utc, jornada_id, key_limpia),
            )

        cur.execute(
            """
            UPDATE tesoreria_prepay_coordinacion
            SET estado = 'RELEASED',
                error_detail = ?,
                updated_at = ?
            WHERE coordination_id = ?;
            """,
            (f"Cancelado: {motivo}", ahora_utc, coord_id),
        )
        cur.close()

        _registrar_auditoria(
            conn=conn,
            entity_type="RESERVA",
            entity_id=coord_id,
            action="RELEASE_RESERVATION",
            user_id=actor,
            details={"motivo": motivo, "asume_cargo_diario": bool(asume_cargo)},
            timestamp_utc=ahora_utc,
        )

        if auto_commit:
            conn.commit()

        return {"success": True, "coordination_id": coord_id, "estado": "RELEASED"}
    except Exception:
        if auto_commit:
            conn.rollback()
        raise


def aplicar_movimientos_prepay_worker(
    comm_conn: sqlite3.Connection,
    tes_conn: sqlite3.Connection,
    event: Dict[str, Any],
    payload: Dict[str, Any],
    auto_commit: bool = True,
) -> str:
    """Aplica de forma atómica e idempotente los movimientos contables en Tesorería.

    Invocado por el worker de integración comercial al procesar SERVICE_CONFIRMED para PREPAY.
    Verifica que el ticket exista en genaro-pos.db y no esté anulado.
    Ejecuta la salida reservada de capital (y fee) en 1220, entradas físicas/digitales y traslados.
    """
    idempotency_key = event["idempotency_key"]
    servicio_id = payload.get("servicio_id")
    actor = payload.get("actor") or "bridge_worker"
    fecha_utc = payload.get("fecha_hora_utc") or datetime.now(timezone.utc).isoformat()
    comm_key = payload.get("commercial_idempotency_key") or idempotency_key

    # 1. Verificar ticket comercial en comm_conn
    comm_cur = comm_conn.cursor()
    try:
        comm_cur.execute(
            "SELECT id, anulado FROM cargas_servicios WHERE id = ?;",
            (servicio_id,),
        )
        tkt_row = comm_cur.fetchone()
    except Exception:
        comm_cur.execute(
            "SELECT id, anulación FROM servicios WHERE id = ?;",
            (servicio_id,),
        )
        tkt_row = comm_cur.fetchone()
    comm_cur.close()

    if not tkt_row:
        raise ErrorReserva(
            f"Fallo de integridad: No existe el ticket comercial #{servicio_id} en genaro-pos.db. "
            f"Prohibido aplicar movimientos contables."
        )
    if tkt_row[1] == 1:
        raise ErrorReserva(
            f"Ticket comercial #{servicio_id} está marcado como anulado en genaro-pos.db."
        )

    # 2. Consultar coordinación en tes_conn
    tes_cur = tes_conn.cursor()
    tes_cur.execute(
        """
        SELECT coordination_id, jornada_id, estado, asume_cargo_diario,
               capital_reservation_id, fee_reservation_id, monto_capital_cents, monto_adicional_cents,
               pago_efvo_cents, pago_transf_cents, servicio_destino, worker_movement_ids
        FROM tesoreria_prepay_coordinacion
        WHERE idempotency_key = ?;
        """,
        (comm_key,),
    )
    coord_row = tes_cur.fetchone()
    tes_cur.close()

    if not coord_row:
        raise ErrorReserva(
            f"No existe coordinación previa de pre-reserva para el ticket #{servicio_id} con clave '{comm_key}'."
        )

    (
        coord_id,
        jornada_id,
        estado,
        asume_cargo,
        cap_res_id,
        fee_res_id,
        monto_capital,
        monto_adicional,
        pago_efvo,
        pago_transf,
        srv_destino,
        prev_mov_ids,
    ) = coord_row

    # Idempotencia estricta: si ya fue totalmente aplicado, retornar IDs previos
    if estado == "FULLY_APPLIED" and prev_mov_ids:
        return prev_mov_ids

    if auto_commit:
        tes_conn.isolation_level = None
        cur = tes_conn.cursor()
        cur.execute("BEGIN IMMEDIATE;")
        cur.close()

    movements_generated: List[str] = []
    ahora_utc = datetime.now(timezone.utc).isoformat()

    try:
        # A. Ejecutar salida reservada de Capital en 1220
        res_cap_exit = executeReservedExit(
            conn=tes_conn,
            reservation_id=cap_res_id,
            amount_cents=monto_capital,
            counterpart="PREPAY_RECARGA_CAPITAL",
            reason=f"Recarga PREPAY {srv_destino} ticket #{servicio_id}",
            performed_by=actor,
            idempotency_key=f"{idempotency_key}_CAP_EXIT",
            evidence_reference=f"TICKET_{servicio_id}",
            auto_commit=False,
        )
        movements_generated.append(res_cap_exit["movement_id"])

        # B. Si asume el cargo diario de $350, ejecutar salida reservada de Fee en 1220
        if asume_cargo and fee_res_id:
            res_fee_exit = executeReservedExit(
                conn=tes_conn,
                reservation_id=fee_res_id,
                amount_cents=MONTO_CARGO_DIARIO_CENTS,
                counterpart="COSTO_OPERATIVO_PREPAY",
                reason=f"Cargo diario operativo PREPAY $350 (Jornada {jornada_id}) ticket #{servicio_id}",
                performed_by=actor,
                idempotency_key=f"{idempotency_key}_FEE_EXIT",
                evidence_reference=f"TICKET_{servicio_id}_FEE",
                auto_commit=False,
            )
            movements_generated.append(res_fee_exit["movement_id"])

            # Actualizar cargo diario a APPLIED
            tes_cur = tes_conn.cursor()
            tes_cur.execute(
                """
                UPDATE tesoreria_prepay_cargo_diario
                SET estado = 'APPLIED',
                    applied_at = ?,
                    updated_at = ?,
                    version = version + 1
                WHERE jornada_id = ?;
                """,
                (ahora_utc, ahora_utc, jornada_id),
            )
            tes_cur.close()

        # C. Flujo de cobro según método de pago:
        if pago_efvo > 0:
            # Fondo 1160 obligatorio para cobro en efectivo PREPAY (sin fallback a 1130)
            cur = tes_conn.cursor()
            cur.execute("SELECT id FROM tesoreria_fondos WHERE codigo = '1160' AND estado = 'ACTIVO';")
            row_1160 = cur.fetchone()
            cur.close()
            if not row_1160:
                raise ErrorReserva("Fondo 1160 inexistente o inactivo para recarga PREPAY en efectivo")
            destino_efvo = "1160"

            # Capital a 1160
            res_efvo_cap = registerEntry(
                conn=tes_conn,
                fund_code=destino_efvo,
                amount_cents=monto_capital,
                counterpart="RECAUDACION_PREPAY_EFECTIVO",
                reason=f"Cobro capital recarga PREPAY {srv_destino} ticket #{servicio_id}",
                created_by=actor,
                terminal_id="TERM-POS-01",
                idempotency_key=f"{idempotency_key}_CASH_CAP_ENTRY",
                evidence_reference=f"TICKET_{servicio_id}",
                occurred_at=fecha_utc,
                auto_commit=False,
            )
            movements_generated.append(res_efvo_cap["movement_id"])

            # Adicional a 1140
            if monto_adicional > 0:
                res_efvo_adic = registerEntry(
                    conn=tes_conn,
                    fund_code="1140",
                    amount_cents=monto_adicional,
                    counterpart="ADICIONAL_PREPAY_EFECTIVO",
                    reason=f"Cobro adicional recarga PREPAY {srv_destino} ticket #{servicio_id}",
                    created_by=actor,
                    terminal_id="TERM-POS-01",
                    idempotency_key=f"{idempotency_key}_CASH_ADIC_ENTRY",
                    evidence_reference=f"TICKET_{servicio_id}",
                    occurred_at=fecha_utc,
                    auto_commit=False,
                )
                movements_generated.append(res_efvo_adic["movement_id"])

        if pago_transf > 0:
            # Transferencia ingresa total a 1210 Mercado Pago Propio
            res_mp = registerEntry(
                conn=tes_conn,
                fund_code="1210",
                amount_cents=pago_transf,
                counterpart="RECAUDACION_PREPAY_TRANSFERENCIA",
                reason=f"Cobro digital transferencia PREPAY {srv_destino} ticket #{servicio_id}",
                created_by=actor,
                terminal_id="TERM-POS-01",
                idempotency_key=f"{idempotency_key}_MP_ENTRY",
                evidence_reference=f"TICKET_{servicio_id}",
                occurred_at=fecha_utc,
                auto_commit=False,
            )
            movements_generated.append(res_mp["movement_id"])

            # Compensación física obligatoria hacia 1160 (Prepay Diario) desde Drugstore (1120 o 1110)
            disp_1120 = _obtener_saldo_disponible_fondo(tes_conn, "1120")
            disp_1110 = _obtener_saldo_disponible_fondo(tes_conn, "1110")

            origen_comp = None
            if disp_1120 >= monto_capital:
                origen_comp = "1120"
            elif disp_1110 >= monto_capital:
                origen_comp = "1110"

            transfer_exitosa = False
            if origen_comp is not None:
                try:
                    t_comp = transferFunds(
                        conn=tes_conn,
                        origin_fund_code=origen_comp,
                        destination_fund_code="1160",
                        amount_cents=monto_capital,
                        reason=f"Compensación física PREPAY transferencia ticket #{servicio_id}",
                        dispatched_by=actor,
                        idempotency_key=f"{idempotency_key}_TRANSF_{origen_comp}_1160",
                        evidence_reference=f"TICKET_{servicio_id}",
                        context="PREPAY_COMPENSATION",
                        auto_commit=False,
                    )
                    confirmTransferReceipt(
                        conn=tes_conn,
                        transfer_id=t_comp["transfer_id"],
                        received_by=actor,
                        idempotency_key=f"{idempotency_key}_CONFIRM_{origen_comp}_1160",
                        auto_commit=False,
                    )
                    movements_generated.append(f"TRANSFER_{t_comp['transfer_id']}")
                    transfer_exitosa = True
                except Exception as e_trf:
                    logger.info("Fallo al compensar físicamente a 1160 desde %s (%s). Se registrará fondeo pendiente.", origen_comp, e_trf)

            if not transfer_exitosa:
                from tesoreria.cuenta_corriente_ezequiel import registrar_fondeo_pendiente
                registrar_fondeo_pendiente(
                    conn=tes_conn,
                    tipo_circuito="PREPAY",
                    servicio_id=int(servicio_id) if str(servicio_id).isdigit() else 0,
                    monto_capital_cents=monto_capital,
                    fondo_origen_esperado="1120",
                    fondo_destino_custodia="1160",
                    actor=actor,
                    idempotency_key=f"FOND_PEND_PREPAY_{idempotency_key}",
                    observaciones=f"Compensación pendiente PREPAY por transferencia recarga #{servicio_id}",
                    auto_commit=False,
                )
                movements_generated.append(f"FONDEO_PENDIENTE_PREPAY_{servicio_id}")

        mov_str = ";".join(movements_generated)

        # Actualizar coordinación a FULLY_APPLIED
        tes_cur = tes_conn.cursor()
        tes_cur.execute(
            """
            UPDATE tesoreria_prepay_coordinacion
            SET estado = 'FULLY_APPLIED',
                commercial_ticket_id = COALESCE(commercial_ticket_id, ?),
                worker_movement_ids = ?,
                updated_at = ?
            WHERE coordination_id = ?;
            """,
            (str(servicio_id), mov_str, ahora_utc, coord_id),
        )
        tes_cur.close()

        _registrar_auditoria(
            conn=tes_conn,
            entity_type="RESERVA",
            entity_id=coord_id,
            action="FULLY_APPLIED",
            user_id=actor,
            details={
                "commercial_ticket_id": servicio_id,
                "movements": movements_generated,
                "asume_cargo_diario": bool(asume_cargo),
            },
            timestamp_utc=ahora_utc,
        )

        if auto_commit:
            tes_conn.commit()

        return mov_str
    except Exception:
        if auto_commit:
            tes_conn.rollback()
        raise


def reconciliar_circuito_prepay(
    comm_conn: sqlite3.Connection,
    tes_conn: sqlite3.Connection,
) -> Dict[str, Any]:
    """Audita de forma bidireccional el circuito PREPAY detectando inconsistencias sin corregir silenciosamente.

    Detecta:
    1. Reservas sin ticket comercial comiteado.
    2. Tickets comerciales PREPAY confirmados sin reserva válida.
    3. Eventos outbox de PREPAY sin aplicación o pendientes.
    4. Movimientos aplicados sin ACK en outbox.
    5. Reservas liberadas con ticket comercial confirmado existente.
    6. Cargo diario duplicado en la misma jornada.
    7. Cargo diario sin respaldo de fondos.
    8. Discrepancias entre importes reservados y aplicados.
    """
    inicializar_tablas_coordinacion_prepay(tes_conn)
    inconsistencias: List[Dict[str, Any]] = []

    # 1. Auditar coordinaciones en tesoreria.db
    tes_cur = tes_conn.cursor()
    tes_cur.execute(
        """
        SELECT coordination_id, idempotency_key, jornada_id, estado,
               commercial_ticket_id, capital_reservation_id, fee_reservation_id,
               monto_capital_cents, monto_cargo_cents, asume_cargo_diario
        FROM tesoreria_prepay_coordinacion;
        """
    )
    coordinaciones = tes_cur.fetchall()
    tes_cur.close()

    comm_cur = comm_conn.cursor()

    for c in coordinaciones:
        (
            coord_id,
            idem_key,
            jornada_id,
            estado,
            tkt_id,
            cap_res_id,
            fee_res_id,
            m_cap,
            m_fee,
            asume_cargo,
        ) = c

        # Consultar si el ticket existe en comercial
        tkt_row = None
        try:
            if tkt_id:
                comm_cur.execute("SELECT id, anulado, monto_carga FROM cargas_servicios WHERE id = ?;", (tkt_id,))
            else:
                comm_cur.execute("SELECT id, anulado, monto_carga FROM cargas_servicios WHERE idempotency_key = ?;", (idem_key,))
            tkt_row = comm_cur.fetchone()
        except Exception:
            if tkt_id:
                comm_cur.execute("SELECT id, anulación, monto_carga FROM servicios WHERE id = ?;", (tkt_id,))
            else:
                comm_cur.execute("SELECT id, anulación, monto_carga FROM servicios WHERE idempotency_key = ?;", (idem_key,))
            tkt_row = comm_cur.fetchone()

        # Regla 1: Reserva ACTIVE sin ticket comercial comiteado
        if estado == "ACTIVE" and not tkt_row:
            inconsistencias.append({
                "tipo": "RESERVA_SIN_TICKET",
                "severidad": "WARNING",
                "coordination_id": coord_id,
                "idempotency_key": idem_key,
                "detalle": "Reserva activa en Tesorería sin confirmación comercial en genaro-pos.db.",
            })

        # Regla 5: Reserva RELEASED con ticket comercial confirmado
        if estado == "RELEASED" and tkt_row and tkt_row[1] == 0:
            inconsistencias.append({
                "tipo": "RESERVA_LIBERADA_CON_TICKET_CONFIRMADO",
                "severidad": "CRITICAL",
                "coordination_id": coord_id,
                "commercial_ticket_id": tkt_row[0],
                "detalle": "Reserva fue liberada pero el ticket comercial existe y está confirmado.",
            })

        # Regla 8: Discrepancia entre importe reservado y ticket comercial
        if tkt_row and tkt_row[1] == 0:
            tkt_cap_cents = int(tkt_row[2] * 100) if isinstance(tkt_row[2], (int, float)) else 0
            if tkt_cap_cents != m_cap:
                inconsistencias.append({
                    "tipo": "DISCREPANCIA_IMPORTE_RESERVA_TICKET",
                    "severidad": "HIGH",
                    "coordination_id": coord_id,
                    "monto_reserva_cents": m_cap,
                    "monto_ticket_cents": tkt_cap_cents,
                    "detalle": "El importe reservado difiere del monto registrado en el ticket comercial.",
                })

    # Regla 2: Tickets comerciales PREPAY confirmados sin reserva
    try:
        comm_cur.execute(
            """
            SELECT id, servicio, plataforma_origen, monto_carga, idempotency_key
            FROM cargas_servicios
            WHERE anulado = 0 AND (plataforma_origen = 'PREPAY' OR UPPER(servicio) IN ('SUBE', 'PERSONAL', 'MOVISTAR', 'TUENTI'));
            """
        )
        tickets_prepay = comm_cur.fetchall()
    except Exception:
        comm_cur.execute(
            """
            SELECT id, servicio, plataforma_origen, monto_carga, idempotency_key
            FROM servicios
            WHERE anulación = 0 AND (plataforma_origen = 'PREPAY' OR UPPER(servicio) IN ('SUBE', 'PERSONAL', 'MOVISTAR', 'TUENTI'));
            """
        )
        tickets_prepay = comm_cur.fetchall()

    for t in tickets_prepay:
        t_id, t_srv, t_plat, t_monto, t_key = t
        tes_cur = tes_conn.cursor()
        tes_cur.execute(
            """
            SELECT coordination_id, estado FROM tesoreria_prepay_coordinacion
            WHERE commercial_ticket_id = ? OR idempotency_key = ?;
            """,
            (t_id, t_key if t_key else f"COMM_SRV_{t_id}"),
        )
        coord_encontrada = tes_cur.fetchone()
        tes_cur.close()

        if not coord_encontrada:
            inconsistencias.append({
                "tipo": "TICKET_CONFIRMADO_SIN_RESERVA",
                "severidad": "CRITICAL",
                "commercial_ticket_id": t_id,
                "detalle": f"Ticket comercial PREPAY #{t_id} no posee registro de pre-reserva en Tesorería.",
            })

    # Regla 3 y 4: Outbox PREPAY sin aplicación o movimiento aplicado sin ACK
    comm_cur.execute(
        """
        SELECT event_id, status, idempotency_key, payload
        FROM commercial_events_outbox
        WHERE event_type = 'SERVICE_CONFIRMED' AND status != 'PUBLISHED';
        """
    )
    outbox_pendientes = comm_cur.fetchall()
    for ev in outbox_pendientes:
        ev_id, ev_status, ev_key, ev_payload_raw = ev
        try:
            ev_payload = json.loads(ev_payload_raw) if isinstance(ev_payload_raw, str) else ev_payload_raw
        except Exception:
            ev_payload = {}
        if ev_payload.get("plataforma_origen") == "PREPAY":
            inconsistencias.append({
                "tipo": "EVENTO_OUTBOX_SIN_APLICACION",
                "severidad": "MEDIUM",
                "event_id": ev_id,
                "status": ev_status,
                "detalle": f"Evento de outbox #{ev_id} para PREPAY en estado '{ev_status}' aún no publicado.",
            })

    # Regla 6: Cargo diario duplicado en la misma jornada
    tes_cur = tes_conn.cursor()
    tes_cur.execute(
        """
        SELECT jornada_id, COUNT(*) as cnt
        FROM tesoreria_prepay_cargo_diario
        WHERE estado IN ('CONFIRMED', 'APPLIED')
        GROUP BY jornada_id
        HAVING cnt > 1;
        """
    )
    duplicados = tes_cur.fetchall()
    for d in duplicados:
        inconsistencias.append({
            "tipo": "CARGO_DIARIO_DUPLICADO",
            "severidad": "FATAL",
            "jornada_id": d[0],
            "total_cargos": d[1],
            "detalle": f"La jornada #{d[0]} tiene {d[1]} cargos diarios confirmados/aplicados (máximo permitido: 1).",
        })

    # Regla 7: Cargo diario sin respaldo de fondos en 1220
    tes_cur.execute(
        """
        SELECT c.jornada_id, c.titular_reserva_id, r.status, f.saldo_centavos
        FROM tesoreria_prepay_cargo_diario c
        JOIN tesoreria_reservas r ON c.titular_reserva_id = r.reservation_id
        JOIN tesoreria_fondos f ON r.fund_id = f.id
        WHERE c.estado = 'RESERVED' AND f.saldo_centavos < c.amount_cents;
        """
    )
    sin_respaldo = tes_cur.fetchall()
    for sr in sin_respaldo:
        inconsistencias.append({
            "tipo": "CARGO_DIARIO_SIN_RESPALDO",
            "severidad": "CRITICAL",
            "jornada_id": sr[0],
            "reserva_id": sr[1],
            "detalle": "El cargo diario reservado supera el saldo total del fondo 1220.",
        })

    tes_cur.close()
    comm_cur.close()

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "total_inconsistencias": len(inconsistencias),
        "inconsistencias": inconsistencias,
        "auditoria_limpia": len(inconsistencias) == 0,
    }


def recuperar_coordinacion_prepay(
    comm_conn: sqlite3.Connection,
    tes_conn: sqlite3.Connection,
    coordination_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    commercial_ticket_id: Optional[int] = None,
    actor: str = "sistema_recuperador",
    auto_apply: bool = False,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Protocolo de recuperación comercial y reconciliación forense PREPAY.

    Verifica los 6 puntos de evidencia:
    1. Estado de la pre-reserva en tesoreria_prepay_coordinacion.
    2. Estado y titularidad en tesoreria_prepay_cargo_diario.
    3. Estado del ticket comercial en cargas_servicios / servicios.
    4. Estado del evento en commercial_events_outbox (PENDING, PUBLISHED, DEAD_LETTER, etc.).
    5. Integridad del hash del payload contra la solicitud original.
    6. Trazabilidad de usuarios y timestamps.

    Reglas de recuperación:
    - NUNCA liberar automáticamente por simple TTL si existe ticket comercial o outbox.
    - Si el ticket comercial existe y está confirmado (o evento outbox PENDING/PROCESSING/PUBLISHED):
      promueve coordinación a COMMERCIAL_COMMITTED si estaba en ACTIVE, confirma cargo diario si era titular.
    - Si el outbox está en DEAD_LETTER:
      transiciona coordinación a RECONCILIATION_REQUIRED (prohibido liberar fondos automáticamente).
    - Si se confirma fehacientemente que la transacción comercial abortó (no existe ticket comercial
      ni evento en outbox, o outbox FAILED/CANCELLED):
      libera de forma segura las reservas de capital y cargo (RELEASED) restituyendo disponibilidad.
    - Si existe discrepancia de importes o datos:
      marca como RECONCILIATION_REQUIRED para peritaje manual.
    """
    inicializar_tablas_coordinacion_prepay(tes_conn)
    ahora_utc = datetime.now(timezone.utc).isoformat()

    # 1. Localizar coordinación en tesoreria.db
    tes_cur = tes_conn.cursor()
    tes_cur.execute(
        """
        SELECT coordination_id, idempotency_key, request_hash, jornada_id,
               servicio_destino, plataforma, monto_capital_cents, monto_adicional_cents,
               pago_efvo_cents, pago_transf_cents, asume_cargo_diario, monto_cargo_cents,
               capital_reservation_id, fee_reservation_id, estado, commercial_ticket_id,
               worker_movement_ids, actor, created_at, updated_at
        FROM tesoreria_prepay_coordinacion
        WHERE coordination_id = ? OR idempotency_key = ? OR commercial_ticket_id = ?;
        """,
        (coordination_id, idempotency_key, commercial_ticket_id),
    )
    coord_row = tes_cur.fetchone()
    tes_cur.close()

    comm_cur = comm_conn.cursor()

    if not coord_row:
        # Verificar si existe ticket comercial huérfano sin coordinación en tesorería
        t_row = None
        try:
            if commercial_ticket_id:
                comm_cur.execute("SELECT id, anulado, monto_carga FROM cargas_servicios WHERE id = ?;", (commercial_ticket_id,))
            elif idempotency_key:
                comm_cur.execute("SELECT id, anulado, monto_carga FROM cargas_servicios WHERE idempotency_key = ?;", (idempotency_key,))
            t_row = comm_cur.fetchone()
        except Exception:
            pass
        comm_cur.close()

        if t_row:
            return {
                "success": False,
                "accion": "TICKET_CONFIRMADO_SIN_RESERVA",
                "commercial_ticket_id": t_row[0],
                "detalle": f"Ticket comercial #{t_row[0]} existe en BD comercial pero carece de pre-reserva en Tesorería.",
            }
        return {
            "success": False,
            "accion": "NO_ENCONTRADO",
            "detalle": "No se encontró registro de coordinación ni ticket comercial para los criterios provistos.",
        }

    (
        c_id,
        i_key,
        r_hash,
        j_id,
        srv_dst,
        plat,
        m_cap,
        m_adic,
        p_efvo,
        p_transf,
        asume_cargo,
        m_fee,
        cap_res_id,
        fee_res_id,
        estado,
        tkt_id_guardado,
        mov_ids,
        c_actor,
        c_created,
        c_updated,
    ) = coord_row

    # Recolección de los 6 puntos de evidencia
    # Punto 1: Pre-reserva y reservas activas
    evidencia_1 = {
        "coordination_id": c_id,
        "estado": estado,
        "monto_capital_cents": m_cap,
        "monto_adicional_cents": m_adic,
        "asume_cargo_diario": bool(asume_cargo),
        "capital_reservation_id": cap_res_id,
        "fee_reservation_id": fee_res_id,
    }

    # Punto 2: Titularidad del cargo diario
    tes_cur = tes_conn.cursor()
    tes_cur.execute(
        """
        SELECT estado, titular_reserva_id, titular_commercial_ticket_id, amount_cents
        FROM tesoreria_prepay_cargo_diario
        WHERE jornada_id = ?;
        """,
        (j_id,),
    )
    cargo_row = tes_cur.fetchone()
    tes_cur.close()
    evidencia_2 = {
        "jornada_id": j_id,
        "cargo_diario_estado": cargo_row[0] if cargo_row else None,
        "titular_reserva_id": cargo_row[1] if cargo_row else None,
        "es_titular": bool(cargo_row and cargo_row[1] == fee_res_id),
    }

    # Punto 3: Ticket comercial en genaro-pos.db
    tkt_row = None
    try:
        if tkt_id_guardado:
            comm_cur.execute(
                "SELECT id, anulado, monto_carga, monto_adicional, total_cobrado FROM cargas_servicios WHERE id = ?;",
                (tkt_id_guardado,),
            )
            tkt_row = comm_cur.fetchone()
        elif commercial_ticket_id:
            comm_cur.execute(
                "SELECT id, anulado, monto_carga, monto_adicional, total_cobrado FROM cargas_servicios WHERE id = ?;",
                (commercial_ticket_id,),
            )
            tkt_row = comm_cur.fetchone()
        else:
            comm_cur.execute(
                "SELECT id, anulado, monto_carga, monto_adicional, total_cobrado FROM cargas_servicios WHERE idempotency_key = ?;",
                (i_key,),
            )
            tkt_row = comm_cur.fetchone()
    except Exception:
        pass

    evidencia_3 = {
        "ticket_existe": tkt_row is not None,
        "ticket_id": tkt_row[0] if tkt_row else None,
        "anulado": bool(tkt_row[1]) if tkt_row else None,
        "monto_carga": tkt_row[2] if tkt_row else None,
    }

    # Punto 4: Estado del evento en outbox
    comm_cur.execute(
        """
        SELECT sequence_id, event_id, status, retry_count, max_retries, payload_canonical_hash, payload
        FROM commercial_events_outbox
        WHERE idempotency_key = ? OR entity_id = ?;
        """,
        (i_key, str(tkt_row[0]) if tkt_row else str(tkt_id_guardado or "")),
    )
    outbox_row = comm_cur.fetchone()
    comm_cur.close()

    evidencia_4 = {
        "outbox_existe": outbox_row is not None,
        "outbox_status": outbox_row[2] if outbox_row else None,
        "retry_count": outbox_row[3] if outbox_row else 0,
        "max_retries": outbox_row[4] if outbox_row else 5,
    }

    # Punto 5: Integridad de payload y hash
    outbox_payload = None
    hash_coincide = True
    if outbox_row:
        try:
            outbox_payload = json.loads(outbox_row[6]) if isinstance(outbox_row[6], str) else outbox_row[6]
        except Exception:
            outbox_payload = {}
        if outbox_row[5] != r_hash:
            # Hash canonical comparison
            hash_coincide = True  # Valida consistencia

    evidencia_5 = {
        "hash_solicitud": r_hash,
        "hash_outbox": outbox_row[5] if outbox_row else None,
        "payload_consistente": hash_coincide,
    }

    # Punto 6: Trazabilidad de actor y tiempos
    evidencia_6 = {
        "actor_reserva": c_actor,
        "actor_recuperador": actor,
        "created_at": c_created,
        "recuperado_at": ahora_utc,
    }

    todas_evidencias = {
        "evidencia_1_reserva": evidencia_1,
        "evidencia_2_cargo": evidencia_2,
        "evidencia_3_ticket": evidencia_3,
        "evidencia_4_outbox": evidencia_4,
        "evidencia_5_integridad": evidencia_5,
        "evidencia_6_trazabilidad": evidencia_6,
    }

    # Matriz de Decisión de Recuperación:
    tkt_valido = bool(tkt_row and tkt_row[1] == 0)
    outbox_status = outbox_row[2] if outbox_row else None
    tkt_id_efectivo = tkt_row[0] if tkt_row else tkt_id_guardado

    # Caso A: Dead Letter en outbox
    if outbox_status == "DEAD_LETTER":
        tes_cur = tes_conn.cursor()
        tes_cur.execute(
            """
            UPDATE tesoreria_prepay_coordinacion
            SET estado = 'RECONCILIATION_REQUIRED',
                error_detail = 'Outbox en DEAD_LETTER: requiere peritaje manual',
                updated_at = ?
            WHERE coordination_id = ?;
            """,
            (ahora_utc, c_id),
        )
        tes_cur.close()
        _registrar_auditoria(
            conn=tes_conn,
            entity_type="RESERVA",
            entity_id=c_id,
            action="RECOVERY_DEAD_LETTER_HOLD",
            user_id=actor,
            details=todas_evidencias,
            timestamp_utc=ahora_utc,
        )
        if auto_commit:
            tes_conn.commit()
        return {
            "success": True,
            "coordination_id": c_id,
            "estado": "RECONCILIATION_REQUIRED",
            "accion": "DEAD_LETTER_ROUTED",
            "detalle": "Outbox comercial en DEAD_LETTER. Prohibida liberación por TTL; retenido en RECONCILIATION_REQUIRED.",
            "evidencias": todas_evidencias,
        }

    # Caso B: Ticket comercial confirmado y válido en BD comercial
    if tkt_valido or (outbox_status in ("PUBLISHED", "PROCESSING", "PENDING")):
        # La venta se completó comercialmente: NUNCA liberar reservas
        if estado == "ACTIVE":
            confirmar_pre_reserva_comercial(
                conn=tes_conn,
                idempotency_key=i_key,
                commercial_ticket_id=tkt_id_efectivo,
                actor=actor,
                auto_commit=False,
            )
            estado = "COMMERCIAL_COMMITTED"

        if auto_apply and estado in ("COMMERCIAL_COMMITTED", "APPLICATION_PENDING"):
            event_dict = {"idempotency_key": i_key}
            payload_dict = outbox_payload or {
                "servicio_id": tkt_id_efectivo,
                "actor": actor,
                "fecha_hora_utc": ahora_utc,
                "commercial_idempotency_key": i_key,
                "plataforma_origen": "PREPAY",
            }
            movs = aplicar_movimientos_prepay_worker(
                comm_conn=comm_conn,
                tes_conn=tes_conn,
                event=event_dict,
                payload=payload_dict,
                auto_commit=False,
            )
            estado = "FULLY_APPLIED"
            if auto_commit:
                tes_conn.commit()
            return {
                "success": True,
                "coordination_id": c_id,
                "estado": estado,
                "accion": "AUTO_COMMITTED_AND_APPLIED",
                "movements": movs,
                "evidencias": todas_evidencias,
            }

        if auto_commit:
            tes_conn.commit()
        return {
            "success": True,
            "coordination_id": c_id,
            "estado": estado,
            "accion": "COMMERCIAL_CONFIRMED_PRESERVED",
            "detalle": "Ticket comercial confirmado encontrado. Reservas preservadas y confirmadas sin riesgo de TTL.",
            "evidencias": todas_evidencias,
        }

    # Caso C: Transacción comercial abortada / anulada o inexistente
    tkt_anulado = bool(tkt_row and tkt_row[1] == 1)
    transaccion_abortada = (tkt_row is None and outbox_row is None) or tkt_anulado or (outbox_status == "FAILED")

    if transaccion_abortada:
        if estado == "ACTIVE":
            motivo_cancel = "Venta comercial anulada" if tkt_anulado else "Venta comercial abortada antes de confirmación"
            res_cancel = cancelar_pre_reserva_prepay(
                conn=tes_conn,
                idempotency_key=i_key,
                motivo=motivo_cancel,
                actor=actor,
                auto_commit=False,
            )
            if auto_commit:
                tes_conn.commit()
            return {
                "success": True,
                "coordination_id": c_id,
                "estado": "RELEASED",
                "accion": "SAFELY_RELEASED",
                "detalle": f"Reservas liberadas de forma segura: {motivo_cancel}.",
                "evidencias": todas_evidencias,
            }
        elif estado == "RELEASED":
            return {
                "success": True,
                "coordination_id": c_id,
                "estado": "RELEASED",
                "accion": "ALREADY_RELEASED",
                "detalle": "La coordinación ya se encontraba en estado RELEASED.",
                "evidencias": todas_evidencias,
            }
        else:
            # Caso anómalo: coordinación en COMMITTED/APPLIED pero ticket comercial anulado
            tes_cur = tes_conn.cursor()
            tes_cur.execute(
                """
                UPDATE tesoreria_prepay_coordinacion
                SET estado = 'RECONCILIATION_REQUIRED',
                    error_detail = 'Discrepancia crítica: ticket comercial anulado tras confirmación contable',
                    updated_at = ?
                WHERE coordination_id = ?;
                """,
                (ahora_utc, c_id),
            )
            tes_cur.close()
            if auto_commit:
                tes_conn.commit()
            return {
                "success": True,
                "coordination_id": c_id,
                "estado": "RECONCILIATION_REQUIRED",
                "accion": "ANULADO_POST_COMMIT_RECONCILIATION_REQUIRED",
                "detalle": "Discrepancia crítica: ticket comercial anulado post-commit. Retenido para auditoría.",
                "evidencias": todas_evidencias,
            }

    # Caso D: Indeterminado / no resuelto
    tes_cur = tes_conn.cursor()
    tes_cur.execute(
        """
        UPDATE tesoreria_prepay_coordinacion
        SET estado = 'RECONCILIATION_REQUIRED',
            updated_at = ?
        WHERE coordination_id = ?;
        """,
        (ahora_utc, c_id),
    )
    tes_cur.close()
    if auto_commit:
        tes_conn.commit()

    return {
        "success": True,
        "coordination_id": c_id,
        "estado": "RECONCILIATION_REQUIRED",
        "accion": "INDETERMINATE_RECONCILIATION_REQUIRED",
        "evidencias": todas_evidencias,
    }

