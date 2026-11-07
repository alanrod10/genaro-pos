"""Suite de Pruebas de Certificación del Circuito Económico V4 — Correcciones Canónicas.

Verifica rigurosamente los puntos críticos auditados:
- PUNTO A: Recargas PREPAY por transferencia van a 1160 (Prepay Diario), no a 1130, y barren al cierre.
          Si no hay liquidez en Drugstore, encolan en tesoreria_fondeos_pendientes sin sobregirar.
- PUNTO B: Adicionales en cobros por transferencia no inflan billetes ficticios en 1140.
- PUNTO C: Aislamiento total de recargas directas desde Mercado Pago (1210) sin tocar 1220/1230/1160/1150
          ni alterar cuentas corrientes ni disparar cargo de $350.
- PUNTO E: Fondeo Claro DEC-02 ejecutable tanto desde 1110 (Diario) como desde 1120 (Acumulado).
- PUNTO G: Desglose analítico de Cuenta Corriente David (saldo disponible 1220 y consumido en recargas).
          Anticipos a preventistas con salida de 1130 y reposición en cascada (1120 -> 1110).
"""

from datetime import datetime, timezone
import json
import sqlite3
import pytest

from db.commercial_outbox_emitter import enqueue_commercial_event
from db.services import clasificar_servicio, registrar_carga_idempotente_turso
from tesoreria.catalog import sembrar_catalogo_canónico
from tesoreria.commercial_bridge_worker import (
    process_pending_commercial_events,
)
from tesoreria.anticipos_preventistas import (
    crear_anticipo_preventista,
    obtener_anticipo_preventista,
    reponer_anticipo_preventista,
)
from tesoreria.closures import beginClosure, executePrepayDrawerSweep
from tesoreria.reconciliations import reconcilePhysicalFund
from tesoreria.cuenta_corriente_david import (
    obtener_estado_cuenta_david,
    registrar_acreditacion_david,
    registrar_pago_david,
)
from tesoreria.cuenta_corriente_ezequiel import (
    ErrorValidacionEzequiel,
    ejecutar_fondeo_pendiente,
    listar_fondeos_pendientes,
    registrar_fondeo_pendiente,
)
from tesoreria.isolation import conectar_db_laboratorio
from tesoreria.jornadas import abrir_jornada, obtener_jornada_actual
from tesoreria.movements import registerEntry, registerExit
from tesoreria.prepay_coordinator import (
    inicializar_tablas_coordinacion_prepay,
    solicitar_pre_reserva_prepay,
)
from tesoreria.schema import inicializar_esquema_laboratorio


def _get_saldo(conn: sqlite3.Connection, codigo_fondo: str) -> int:
    row = conn.execute(
        "SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = ?",
        (codigo_fondo,),
    ).fetchone()
    if not row:
        raise ValueError(f"Fondo no encontrado: {codigo_fondo}")
    return row[0]


def _crear_esquema_comercial(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS cargas_servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legacy_row_id TEXT,
            fecha_hora_utc TEXT NOT NULL,
            servicio TEXT NOT NULL,
            monto_carga INTEGER NOT NULL CHECK (monto_carga > 0),
            monto_adicional INTEGER NOT NULL DEFAULT 0 CHECK (monto_adicional >= 0),
            total_cobrado INTEGER NOT NULL CHECK (total_cobrado >= 0),
            pago_efectivo INTEGER NOT NULL DEFAULT 0 CHECK (pago_efectivo >= 0),
            pago_transferencia INTEGER NOT NULL DEFAULT 0 CHECK (pago_transferencia >= 0),
            anulado INTEGER NOT NULL DEFAULT 0 CHECK (anulado IN (0, 1)),
            idempotency_key TEXT UNIQUE,
            plataforma_origen TEXT,
            servicio_destino TEXT
        );

        CREATE TABLE IF NOT EXISTS servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            servicio TEXT NOT NULL,
            plataforma_origen TEXT,
            servicio_destino TEXT,
            monto_carga REAL NOT NULL,
            monto_adicional REAL NOT NULL,
            total_cobrado REAL NOT NULL,
            pago_efvo REAL NOT NULL,
            pago_transf REAL NOT NULL,
            anulación INTEGER NOT NULL DEFAULT 0,
            idempotency_key TEXT UNIQUE
        );

        CREATE TABLE IF NOT EXISTS cargas_servicios_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            carga_id INTEGER NOT NULL,
            accion TEXT NOT NULL,
            valores_anteriores TEXT,
            valores_nuevos TEXT,
            actor TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS commercial_events_outbox (
            sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            payload_canonical_hash TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED', 'DEAD_LETTER')),
            retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
            max_retries INTEGER NOT NULL DEFAULT 5 CHECK (max_retries >= 1),
            error_message TEXT,
            tesoreria_movement_id TEXT,
            reprocessed_by TEXT,
            reprocessed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            published_at TEXT
        );
        """
    )
    cur.close()


@pytest.fixture
def env_v4(tmp_path):
    comm_path = tmp_path / "comm_v4.db"
    tes_path = tmp_path / "tes_v4.db"

    comm_conn = sqlite3.connect(str(comm_path), timeout=30.0)
    comm_conn.row_factory = sqlite3.Row
    comm_conn.execute("PRAGMA foreign_keys = ON;")
    _crear_esquema_comercial(comm_conn)

    tes_conn = conectar_db_laboratorio(str(tes_path))
    inicializar_esquema_laboratorio(tes_conn)
    sembrar_catalogo_canónico(tes_conn)
    inicializar_tablas_coordinacion_prepay(tes_conn)

    abrir_jornada(tes_conn, usuario="admin", observaciones="Jornada Certificación V4")

    # Establecer fondos en estado OPENED
    tes_conn.execute("UPDATE tesoreria_fondos SET estado_apertura = 'OPENED';")
    tes_conn.commit()

    yield comm_conn, tes_conn, str(comm_path), str(tes_path)


# =====================================================================
# PUNTO A: Recargas PREPAY cobradas por transferencia (1160 vs 1130)
# =====================================================================

def test_punto_a_recarga_prepay_transferencia_va_a_1160_y_barre_a_1130(env_v4):
    comm_conn, tes_conn, _, _ = env_v4

    # 1. Dotar Prepay Digital (1220) con $50.000 y Drugstore Acumulado (1120) con $30.000
    registerEntry(tes_conn, "1220", 50000_00, "FONDEO", "Acreditación digital Prepay", "admin")
    registerEntry(tes_conn, "1120", 30000_00, "FONDEO", "Fondo general tesoro", "admin")

    # 2. Registrar carga comercial idempotente por transferencia: $10.000 carga + $500 adicional
    registrar_carga_idempotente_turso(
        registro={
            "FECHA": "02/11/2026 10:00:00",
            "SERVICIO": "SUBE",
            "PLATAFORMA_ORIGEN": "PREPAY",
            "SERVICIO_DESTINO": "SUBE",
            "MONTO_CARGA": 10000,
            "MONTO_ADICIONAL": 500,
            "TOTAL_COBRADO": 10500,
            "PAGO_EFVO": 0,
            "PAGO_TRANSF": 10500,
        },
        idempotency_key="tx_prepay_transf_canonico_v4",
        conn=comm_conn,
        tesoreria_conn=tes_conn,
    )

    # 3. Procesar en bridge worker
    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 1

    # 4. VERIFICACIONES CRÍTICAS:
    # - 1210 (MP virtual) recibió $10.500
    assert _get_saldo(tes_conn, "1210") == 10500_00
    # - 1220 (Prepay digital) debitó $10.000 de capital y $350 de cargo diario = $10.350
    assert _get_saldo(tes_conn, "1220") == 50000_00 - 10350_00
    # - 1120 (Drugstore Acumulado) aportó los $10.000 de compensación física
    assert _get_saldo(tes_conn, "1120") == 30000_00 - 10000_00
    # - 1160 (Prepay Diario) RECIBIÓ LOS $10.000 en billetes
    assert _get_saldo(tes_conn, "1160") == 10000_00
    # - 1130 (Prepay Acumulado) DEBE SEGUIR EN $0 (la compensación física va a 1160, no a 1130)
    assert _get_saldo(tes_conn, "1130") == 0

    # 5. Al cierre de jornada, el barrido de Prepay mueve 1160 -> 1130
    jornada = obtener_jornada_actual(tes_conn)
    beginClosure(tes_conn, jornada["id"], "admin")
    reconcilePhysicalFund(
        conn=tes_conn,
        fund_code="1160",
        verified_amount_cents=10000_00,
        user_id="admin",
        notes="Arqueo físico conforme de gaveta PREPAY",
    )
    sweep_res = executePrepayDrawerSweep(tes_conn, journal_id=jornada["id"], user_id="admin")
    assert sweep_res["swept"] is True
    assert sweep_res["amount_cents"] == 10000_00
    assert _get_saldo(tes_conn, "1160") == 0
    assert _get_saldo(tes_conn, "1130") == 10000_00


def test_punto_a_recarga_prepay_transferencia_sin_liquidez_encola_fondeo_pendiente(env_v4):
    comm_conn, tes_conn, _, _ = env_v4

    # Dotar 1220 de saldo digital pero dejar 1120 y 1110 en $0
    registerEntry(tes_conn, "1220", 20000_00, "FONDEO", "Acreditación digital Prepay", "admin")
    assert _get_saldo(tes_conn, "1120") == 0
    assert _get_saldo(tes_conn, "1110") == 0

    res = solicitar_pre_reserva_prepay(
        conn=tes_conn,
        servicio_destino="PERSONAL",
        monto_carga_cents=5000_00,
        monto_adicional_cents=300_00,
        pago_efvo_cents=0,
        pago_transf_cents=5300_00,
        idempotency_key="tx_prepay_sin_saldo_002",
        actor="cajero1",
        comm_conn=comm_conn,
        permitir_fondeo_pendiente=True,
    )
    assert res["success"] is True

    # Insertar registro comercial para validar integridad
    comm_conn.execute(
        """
        INSERT INTO cargas_servicios (id, fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado, idempotency_key, plataforma_origen, servicio_destino)
        VALUES (2, '2026-11-02T10:00:00Z', 'PERSONAL', 5000, 300, 5300, 0, 5300, 0, 'tx_prepay_sin_saldo_002', 'PREPAY', 'PERSONAL');
        """
    )
    comm_conn.commit()

    # Insertar evento comercial para el worker
    payload = {
        "servicio_id": 2,
        "carga_id": 2,
        "monto_carga_cents": 5000_00,
        "monto_adicional_cents": 300_00,
        "pago_efectivo_cents": 0,
        "pago_transferencia_cents": 5300_00,
        "servicio": "PERSONAL",
        "plataforma_origen": "PREPAY",
        "servicio_destino": "PERSONAL",
        "idempotency_key": "tx_prepay_sin_saldo_002",
        "commercial_idempotency_key": "tx_prepay_sin_saldo_002",
        "pre_reserva_id": res["coordination_id"],
        "requiere_pre_reserva": True,
    }
    enqueue_commercial_event(
        comm_conn,
        event_type="SERVICE_CONFIRMED",
        entity_type="SERVICE_CHARGE",
        entity_id="2",
        action_type="INSERT",
        idempotency_key="outbox-prepay-002",
        payload=payload,
    )

    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 1

    # Verificar que no hubo sobregiro en Drugstore
    assert _get_saldo(tes_conn, "1120") == 0
    assert _get_saldo(tes_conn, "1110") == 0

    # Verificar que se creó un fondeo pendiente en tesoreria_fondeos_pendientes hacia 1160
    fondeos = listar_fondeos_pendientes(tes_conn, "PENDIENTE")
    prepay_fondeos = [f for f in fondeos if f["tipo_circuito"] == "PREPAY"]
    assert len(prepay_fondeos) == 1
    assert prepay_fondeos[0]["monto_capital_cents"] == 5000_00
    assert prepay_fondeos[0]["fondo_destino_custodia"] == "1160"

    # Regularizar fondeo pendiente PREPAY dinámicamente hacia 1160
    registerEntry(tes_conn, "1120", 10000_00, "FONDEO", "Fondo tesoro para regularizar", "admin")
    res_reg = ejecutar_fondeo_pendiente(
        conn=tes_conn,
        fondeo_id=prepay_fondeos[0]["id"],
        actor="admin",
        fondo_origen="1120",
    )
    assert res_reg["estado"] == "FONDEADO"
    assert res_reg["fondo_destino"] == "1160"
    assert _get_saldo(tes_conn, "1120") == 5000_00
    assert _get_saldo(tes_conn, "1160") == 5000_00
    assert _get_saldo(tes_conn, "1150") == 0  # Garantizar que jamás se desvió a 1150


# =====================================================================
# PUNTO B: Reconocimiento de Adicionales en recargas por transferencia
# =====================================================================

def test_punto_b_adicionales_por_transferencia_no_inflan_fondo_1140(env_v4):
    comm_conn, tes_conn, _, _ = env_v4

    registerEntry(tes_conn, "1220", 30000_00, "FONDEO", "Digital Prepay", "admin")
    registerEntry(tes_conn, "1120", 20000_00, "FONDEO", "Drugstore Acumulado", "admin")

    registrar_carga_idempotente_turso(
        registro={
            "FECHA": "02/11/2026 11:00:00",
            "SERVICIO": "Personal",
            "PLATAFORMA_ORIGEN": "PREPAY",
            "SERVICIO_DESTINO": "PERSONAL",
            "MONTO_CARGA": 5000,
            "MONTO_ADICIONAL": 400,
            "TOTAL_COBRADO": 5400,
            "PAGO_EFVO": 0,
            "PAGO_TRANSF": 5400,
        },
        idempotency_key="tx_adicional_transf_003",
        conn=comm_conn,
        tesoreria_conn=tes_conn,
    )

    process_pending_commercial_events(comm_conn, tes_conn)

    # El adicional ($400) ingresó de forma virtual a Mercado Pago (1210 total = 5400)
    assert _get_saldo(tes_conn, "1210") == 5400_00
    # La caja física de adicionales (1140) NO debe inflarse con billetes ficticios
    assert _get_saldo(tes_conn, "1140") == 0


# =====================================================================
# PUNTO C: Aislamiento estricto de recargas directas desde Mercado Pago (1210)
# =====================================================================

def test_punto_c_aislamiento_recarga_directa_mercado_pago(env_v4):
    comm_conn, tes_conn, _, _ = env_v4

    # Dotar fondos
    registerEntry(tes_conn, "1210", 40000_00, "FONDEO", "Saldo propio Mercado Pago", "admin")
    registerEntry(tes_conn, "1220", 25000_00, "FONDEO", "Saldo Prepay David", "admin")
    registerEntry(tes_conn, "1230", 25000_00, "FONDEO", "Saldo Claro Ezequiel", "admin")

    # Clasificación canónica debe ser SUBE_MP
    assert clasificar_servicio("SUBE", "MERCADO PAGO") == "SUBE_MP"
    assert clasificar_servicio("Personal", "MERCADO PAGO") == "SUBE_MP"

    # Venta comercial de recarga con capital de 1210 cobrada en efectivo ($8.000 + $500)
    registrar_carga_idempotente_turso(
        registro={
            "FECHA": "02/11/2026 12:00:00",
            "SERVICIO": "Personal",
            "PLATAFORMA_ORIGEN": "MERCADO PAGO",
            "SERVICIO_DESTINO": "PERSONAL",
            "MONTO_CARGA": 8000,
            "MONTO_ADICIONAL": 500,
            "TOTAL_COBRADO": 8500,
            "PAGO_EFVO": 8500,
            "PAGO_TRANSF": 0,
        },
        idempotency_key="tx_recarga_directa_mp_004",
        conn=comm_conn,
        tesoreria_conn=tes_conn,
    )

    stats = process_pending_commercial_events(comm_conn, tes_conn)
    print("DEBUG OUTBOX:", [dict(r) for r in comm_conn.execute("SELECT * FROM commercial_events_outbox").fetchall()])
    print("DEBUG ALL FONDEOS:", [dict(r) for r in tes_conn.execute("SELECT * FROM tesoreria_fondeos_pendientes").fetchall()])
    assert stats["published"] == 1

    # 1. 1210 debita el capital propio ($8.000)
    assert _get_saldo(tes_conn, "1210") == 40000_00 - 8000_00
    # 2. El cajón 1110 recibe el capital en efectivo ($8.000)
    assert _get_saldo(tes_conn, "1110") == 8000_00
    # 3. La caja 1140 recibe el adicional en billetes ($500)
    assert _get_saldo(tes_conn, "1140") == 500_00

    # 4. AISLAMIENTO TOTAL: Proveedores y cajas ajenas inalteradas
    assert _get_saldo(tes_conn, "1220") == 25000_00  # Prepay no se toca
    assert _get_saldo(tes_conn, "1230") == 25000_00  # Claro no se toca
    assert _get_saldo(tes_conn, "1160") == 0         # Prepay Diario intocado
    assert _get_saldo(tes_conn, "1130") == 0         # Prepay Acumulado intocado
    assert _get_saldo(tes_conn, "1150") == 0         # Claro Custodia intocado

    # 5. Cero cargos diarios de $350 Prepay debitados
    cursor = tes_conn.execute("SELECT COUNT(*) FROM tesoreria_movimientos WHERE counterpart = 'COSTO_OPERATIVO_PREPAY'")
    assert cursor.fetchone()[0] == 0


# =====================================================================
# PUNTO E: Fondeo Claro DEC-02 ejecutable desde 1110 y 1120
# =====================================================================

def test_punto_e_fondeo_claro_dec02_regularizacion_desde_1110_y_1120(env_v4):
    _, tes_conn, _, _ = env_v4

    # 1. Crear un fondeo pendiente hacia 1150 de $4.000
    res_f1 = registrar_fondeo_pendiente(
        conn=tes_conn,
        tipo_circuito="CLARO",
        servicio_id=101,
        monto_capital_cents=4000_00,
        fondo_origen_esperado="1110",
        fondo_destino_custodia="1150",
        actor="admin",
        idempotency_key="fondeo-test-1110-001",
        observaciones="Fondeo DEC-02 diferido para prueba 1110",
    )
    fondeo_id_1 = res_f1["id"]

    # Dotar 1110 de efectivo ($10.000) y regularizar desde 1110
    registerEntry(tes_conn, "1110", 10000_00, "FONDEO", "Efectivo mostrador", "admin")
    mov_id_1 = ejecutar_fondeo_pendiente(tes_conn, fondeo_id_1, actor="admin", fondo_origen="1110")
    assert mov_id_1 is not None
    assert _get_saldo(tes_conn, "1110") == 6000_00
    assert _get_saldo(tes_conn, "1150") == 4000_00

    # 2. Crear un segundo fondeo pendiente hacia 1150 de $6.000
    res_f2 = registrar_fondeo_pendiente(
        conn=tes_conn,
        tipo_circuito="CLARO",
        servicio_id=102,
        monto_capital_cents=6000_00,
        fondo_origen_esperado="1110",
        fondo_destino_custodia="1150",
        actor="admin",
        idempotency_key="fondeo-test-1120-002",
        observaciones="Fondeo DEC-02 diferido para prueba 1120 (Acumulado)",
    )
    fondeo_id_2 = res_f2["id"]

    # Dotar 1120 con $15.000 y regularizar explícitamente desde 1120
    registerEntry(tes_conn, "1120", 15000_00, "FONDEO", "Efectivo tesoro", "admin")
    mov_id_2 = ejecutar_fondeo_pendiente(tes_conn, fondeo_id_2, actor="admin", fondo_origen="1120")
    assert mov_id_2 is not None
    assert _get_saldo(tes_conn, "1120") == 9000_00
    assert _get_saldo(tes_conn, "1150") == 10000_00  # 4.000 previo + 6.000

    # 3. Intentar regularizar desde un fondo no autorizado (ej. 1130 Prepay) debe fallar
    res_f3 = registrar_fondeo_pendiente(
        conn=tes_conn,
        tipo_circuito="CLARO",
        servicio_id=103,
        monto_capital_cents=1000_00,
        fondo_origen_esperado="1110",
        fondo_destino_custodia="1150",
        actor="admin",
        idempotency_key="fondeo-test-invalido-003",
        observaciones="Fondeo invalido desde 1130",
    )
    fondeo_id_3 = res_f3["id"]
    registerEntry(tes_conn, "1130", 5000_00, "FONDEO", "Prepay acumulado", "admin")
    with pytest.raises((ValueError, ErrorValidacionEzequiel), match="Fondo de origen.*no autorizado"):
        ejecutar_fondeo_pendiente(tes_conn, fondeo_id_3, actor="admin", fondo_origen="1130")


# =====================================================================
# PUNTO G: Cuenta Corriente David y Desglose Digital 1220
# =====================================================================

def test_punto_g_cuenta_corriente_david_desglose_y_restriccion_1130(env_v4):
    _, tes_conn, _, _ = env_v4

    # 1. Registrar acreditación de David: $100.000 a 1220
    registrar_acreditacion_david(
        conn=tes_conn,
        amount_cents=100000_00,
        referencia="TRANSF-DAVID-001",
        actor="admin",
        idempotency_key="cc-david-acred-001",
        motivo="Acreditación inicial David",
    )

    # 2. Simular consumo de recarga de $30.000 desde 1220
    registerExit(
        conn=tes_conn,
        fund_code="1220",
        amount_cents=30000_00,
        counterpart="RECARGA",
        reason="Consumo recargas PREPAY SUBE",
        created_by="worker",
    )

    # 3. Obtener estado de cuenta David
    estado = obtener_estado_cuenta_david(tes_conn)
    assert estado["total_acreditaciones_cents"] == 100000_00
    assert estado["obligacion_pendiente_cents"] == 100000_00
    assert estado["saldo_disponible_1220_cents"] == 70000_00
    assert estado["capital_consumido_recargas_cents"] == 30000_00

    # 4. Dotar 1130 con $50.000 y registrar pago a David
    registerEntry(tes_conn, "1130", 50000_00, "FONDEO", "Fondo prepay acumulado", "admin")
    registrar_pago_david(
        conn=tes_conn,
        amount_cents=40000_00,
        referencia="RECIBO-EFECTIVO-001",
        actor="admin",
        idempotency_key="cc-david-pago-001",
        motivo="Pago parcial en efectivo a David",
    )

    estado_post = obtener_estado_cuenta_david(tes_conn)
    assert estado_post["total_pagos_cents"] == 40000_00
    assert estado_post["obligacion_pendiente_cents"] == 60000_00
    assert estado_post["saldo_disponible_1130_cents"] == 10000_00


def test_punto_g_anticipos_preventistas_salida_1130_y_cascada_reposicion(env_v4):
    _, tes_conn, _, _ = env_v4

    # 1. Registrar acreditación de David para registrar pasivo de $50.000
    registrar_acreditacion_david(
        conn=tes_conn,
        amount_cents=50000_00,
        referencia="ACRED-TEST",
        actor="admin",
        idempotency_key="david-acred-anticipo-test",
    )
    # Dotar 1130 con $20.000 billetes
    registerEntry(tes_conn, "1130", 20000_00, "FONDEO", "Fondo prepay acumulado", "admin")

    # 2. Retiro temporal de 1130 para pagar a preventista de Arcor: $15.000
    anticipo = crear_anticipo_preventista(
        conn=tes_conn,
        proveedor="Preventista Arcor",
        monto_cents=15000_00,
        motivo="Entrega golosinas Arcor",
        actor="admin",
        idempotency_key="anticipo-arcor-001",
    )
    assert anticipo["monto_original_cents"] == 15000_00
    assert _get_saldo(tes_conn, "1130") == 5000_00

    # Verificar que el retiro transitorio NO reduce la deuda con David
    estado_david = obtener_estado_cuenta_david(tes_conn)
    assert estado_david["obligacion_pendiente_cents"] == 50000_00
    assert estado_david["total_pagos_cents"] == 0

    # 3. Dotar 1120 con $10.000 y 1110 con $8.000
    registerEntry(tes_conn, "1120", 10000_00, "FONDEO", "Tesoro general", "admin")
    registerEntry(tes_conn, "1110", 8000_00, "FONDEO", "Caja mostrador", "admin")

    # 4. Reposición en cascada: 1° 1120 ($10.000 completo) y 2° 1110 ($5.000 restante)
    res_rep = reponer_anticipo_preventista(
        conn=tes_conn,
        anticipo_id=anticipo["id"],
        actor="admin",
        idempotency_key="rep-arcor-001",
        monto_cents=15000_00,
        motivo="Reposición cascada Arcor",
    )
    assert res_rep["estado"] == "TOTALMENTE_REPUESTO"

    # 1120 se consumió por completo ($10.000 -> $0)
    assert _get_saldo(tes_conn, "1120") == 0
    # 1110 aportó el remanente de $5.000 ($8.000 - $5.000 = $3.000)
    assert _get_saldo(tes_conn, "1110") == 3000_00
    # 1130 recuperó la totalidad del dinero prestado ($5.000 + $15.000 = $20.000)
    assert _get_saldo(tes_conn, "1130") == 20000_00


def test_destino_dinamico_fondeos_pendientes_derivacion_segun_circuito(env_v4):
    """Verifica que ejecutar_fondeo_pendiente derive dinámicamente el destino canónico:
    1. Lee destino/fondo_destino/fondo_destino_custodia.
    2. Si es PREPAY -> destino 1160 (Prepay Diario).
    3. Si es CLARO -> destino 1150 (Claro Físico).
    4. Si la tabla no tiene columna de destino, deriva según el circuito (1160 PREPAY / 1150 CLARO).
    """
    comm_conn, tes_conn, _, _ = env_v4

    # Dotar 1110 de fondos para regularizaciones
    registerEntry(tes_conn, "1110", 50000_00, "FONDEO", "Fondo para regularizaciones", "admin")

    # Caso A: Fondeo CLARO leyendo columna -> transfiere a 1150
    cur = tes_conn.cursor()
    cur.execute(
        """
        INSERT INTO tesoreria_fondeos_pendientes
            (tipo_circuito, servicio_id, idempotency_key, monto_capital_cents, fondo_origen_esperado, fondo_destino_custodia, estado, actor)
        VALUES ('CLARO', 201, 'fondeo-dinamico-claro-01', 3000_00, '1110', '1150', 'PENDIENTE', 'admin');
        """
    )
    f_claro_id = cur.lastrowid
    tes_conn.commit()
    cur.close()

    res_claro = ejecutar_fondeo_pendiente(tes_conn, f_claro_id, actor="admin", fondo_origen="1110")
    assert res_claro["estado"] == "FONDEADO"
    assert res_claro["fondo_destino"] == "1150"
    assert _get_saldo(tes_conn, "1150") == 3000_00

    # Caso B: Fondeo PREPAY leyendo columna -> transfiere a 1160
    cur = tes_conn.cursor()
    cur.execute(
        """
        INSERT INTO tesoreria_prepay_coordinacion
            (coordination_id, idempotency_key, request_hash, jornada_id, servicio_destino,
             plataforma, monto_capital_cents, monto_adicional_cents, pago_efvo_cents, pago_transf_cents,
             asume_cargo_diario, monto_cargo_cents, capital_reservation_id, fee_reservation_id,
             estado, actor, commercial_ticket_id, created_at, updated_at)
        VALUES ('coord-dinamico-01', 'fondeo-dinamico-prepay-01', 'hash01', 1, 'PERSONAL',
                'PREPAY', 4000_00, 200_00, 0, 4200_00, 0, 0, 'res01', NULL, 'FULLY_APPLIED', 'admin', '202',
                datetime('now'), datetime('now'));
        """
    )
    cur.execute(
        """
        INSERT INTO tesoreria_fondeos_pendientes
            (tipo_circuito, servicio_id, idempotency_key, monto_capital_cents, fondo_origen_esperado, fondo_destino_custodia, estado, actor)
        VALUES ('PREPAY', 202, 'fondeo-dinamico-prepay-01', 4000_00, '1110', '1160', 'PENDIENTE', 'admin');
        """
    )
    f_prepay_id = cur.lastrowid
    tes_conn.commit()
    cur.close()

    res_prepay = ejecutar_fondeo_pendiente(tes_conn, f_prepay_id, actor="admin", fondo_origen="1110")
    assert res_prepay["estado"] == "FONDEADO"
    assert res_prepay["fondo_destino"] == "1160"
    assert _get_saldo(tes_conn, "1160") == 4000_00


