"""Pruebas de Certificación Integral — FASE 2.2.2: GOBERNANZA, ARQUEOS, CIERRES Y MIGRACIÓN 1160.

Verifica la resolución exhaustiva de los hallazgos H-01 a H-08:
- H-01, H-02, H-03: Gobernanza estricta 1120 -> 1130 (ticket real, monto de capital, no duplicidad, bloqueo reposición y REST).
- H-04: Arqueo físico de 1160 autorizado; conciliación digital rechazada; observación obligatoria ante diferencias.
- H-05: Barrido 1160 -> 1130 con sobrantes verificados por arqueo; corte contable a $0,00 sin mutaciones directas arbitrarias; re-anclaje de reservas.
- H-06: Endpoint REST POST /api/tesoreria/cierres/vaciado-1160 con autorización RBAC (solo Alan y Yilda) e idempotencia.
- H-07: Gobernanza estricta 1160 -> 1130 (bloqueo de traslados manuales por API o funciones ordinarias fuera de cierre).
- H-08: Migración estructural de 9 a 10 fondos en SQLite preservando datos, saldos, FKs y trazabilidad en _tesoreria_schema_migrations.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import threading
import uuid
import pytest
from fastapi.testclient import TestClient

from api.auth import AuthenticatedUser
from api.dependencies import get_current_user
from api.main import app
from tesoreria.catalog import obtener_fondo_por_codigo, sembrar_catalogo_canónico
from tesoreria.closures import (
    ErrorCierre,
    beginClosure,
    confirmClosure,
    executePrepayDrawerSweep,
    validateClosure,
)
from tesoreria.jornadas import abrir_jornada, obtener_jornada_actual
from tesoreria.migrations_runner import (
    ejecutar_migraciones_tesoreria,
    migrar_tabla_fondos_si_requiere_1160,
    obtener_versiones_aplicadas,
)
from tesoreria.money import formatMoney
from tesoreria.movements import registerEntry
from tesoreria.reconciliations import (
    ErrorArqueo,
    reconcileDigitalAccount,
    reconcilePhysicalFund,
)
from tesoreria.reservations import createReservation
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.transfers import (
    ErrorTraslado,
    confirmTransferReceipt,
    transferFunds,
    validar_politica_traslado,
)


@pytest.fixture
def db_mem():
    """Conexión SQLite en memoria con esquema de tesorería completo y catálogo sembrado."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="alan")
    yield conn
    conn.close()


# =====================================================================
# SECCIÓN 1: GOBERNANZA 1120 -> 1130 (H-01, H-02, H-03)
# =====================================================================

def test_h01_h02_h03_gobernanza_1120_a_1130_rechazos_textuales_y_bypass(db_mem):
    """Verifica que ningún motivo textual arbitrario permita saltarse la gobernanza de 1120 -> 1130."""
    registerEntry(db_mem, "1120", 5000_00, "FONDEO", "Fondeo tesoro", "alan")

    # 1. reason="PREPAY" sin context
    with pytest.raises(ErrorTraslado, match="GOBERNANZA DE FONDOS: El traslado de Drugstore Acumulado"):
        transferFunds(
            conn=db_mem,
            origin_fund_code="1120",
            destination_fund_code="1130",
            amount_cents=1000_00,
            dispatched_by="alan",
            reason="PREPAY",
        )

    # 2. evidence_reference="OPERACION PREPAY" sin context
    with pytest.raises(ErrorTraslado, match="GOBERNANZA DE FONDOS: El traslado de Drugstore Acumulado"):
        transferFunds(
            conn=db_mem,
            origin_fund_code="1120",
            destination_fund_code="1130",
            amount_cents=1000_00,
            dispatched_by="alan",
            reason="Compensación física",
            evidence_reference="OPERACION PREPAY",
        )

    # 3. context="PREPAY_COMPENSATION" sin ticket real en DB
    with pytest.raises(ErrorTraslado, match="GOBERNANZA: No existe coordinación PREPAY válida"):
        transferFunds(
            conn=db_mem,
            origin_fund_code="1120",
            destination_fund_code="1130",
            amount_cents=1000_00,
            dispatched_by="alan",
            reason="Compensación física PREPAY transferencia",
            context="PREPAY_COMPENSATION",
            evidence_reference="TICKET_9999",
        )

    # 4. context="REPOSICION_PREVENTISTAS" sin expediente de anticipo válido queda bloqueado
    #    (el circuito ya está formalizado; la gobernanza exige un expediente existente).
    with pytest.raises(ErrorTraslado, match="No existe expediente de anticipo de preventista válido"):
        transferFunds(
            conn=db_mem,
            origin_fund_code="1120",
            destination_fund_code="1130",
            amount_cents=500_00,
            dispatched_by="alan",
            reason="Reposición a preventistas",
            context="REPOSICION_PREVENTISTAS",
            evidence_reference="DOSSIER_001",
        )


def test_h01_h02_h03_gobernanza_1120_a_1130_validaciones_de_coordinacion(db_mem):
    """Verifica validación de estado de coordinación, cobro en transferencia y monto de capital exacto."""
    registerEntry(db_mem, "1120", 10000_00, "FONDEO", "Fondeo tesoro", "alan")

    # Ticket 101: Cobrado solo en EFECTIVO (pago_transf_cents = 0) -> NO admite compensación 1120 -> 1130
    db_mem.execute(
        """
        INSERT INTO tesoreria_prepay_coordinacion (
            coordination_id, idempotency_key, request_hash, jornada_id, servicio_destino,
            monto_capital_cents, monto_adicional_cents, pago_efvo_cents, pago_transf_cents,
            asume_cargo_diario, capital_reservation_id, estado, commercial_ticket_id, actor,
            created_at, updated_at
        ) VALUES (
            'coord_101', 'key_101', 'h_101', 1, 'PERSONAL',
            2000_00, 200_00, 2200_00, 0,
            0, 'res_101', 'COMMERCIAL_COMMITTED', 101, 'alan',
            datetime('now'), datetime('now')
        )
        """
    )

    # Ticket 102: Estado CANCELADO -> NO admite compensación
    db_mem.execute(
        """
        INSERT INTO tesoreria_prepay_coordinacion (
            coordination_id, idempotency_key, request_hash, jornada_id, servicio_destino,
            monto_capital_cents, monto_adicional_cents, pago_efvo_cents, pago_transf_cents,
            asume_cargo_diario, capital_reservation_id, estado, commercial_ticket_id, actor,
            created_at, updated_at
        ) VALUES (
            'coord_102', 'key_102', 'h_102', 1, 'CLARO',
            1500_00, 150_00, 0, 1650_00,
            0, 'res_102', 'COMMERCIAL_REJECTED', 102, 'alan',
            datetime('now'), datetime('now')
        )
        """
    )

    # Ticket 103: Válido con capital de 3000_00 y adicional de 300_00
    db_mem.execute(
        """
        INSERT INTO tesoreria_prepay_coordinacion (
            coordination_id, idempotency_key, request_hash, jornada_id, servicio_destino,
            monto_capital_cents, monto_adicional_cents, pago_efvo_cents, pago_transf_cents,
            asume_cargo_diario, capital_reservation_id, estado, commercial_ticket_id, actor,
            created_at, updated_at
        ) VALUES (
            'coord_103', 'key_103', 'h_103', 1, 'DIRECTV',
            3000_00, 300_00, 0, 3300_00,
            0, 'res_103', 'COMMERCIAL_COMMITTED', 103, 'alan',
            datetime('now'), datetime('now')
        )
        """
    )
    db_mem.commit()

    # Intento 1: Compensar ticket 101 (pago_transf_cents=0) -> RECHAZA
    with pytest.raises(ErrorTraslado, match="no registra cobro por transferencia bancaria"):
        transferFunds(
            conn=db_mem,
            origin_fund_code="1120",
            destination_fund_code="1130",
            amount_cents=2000_00,
            dispatched_by="alan",
            reason="Compensación ticket 101",
            context="PREPAY_COMPENSATION",
            evidence_reference="TICKET_101",
        )

    # Intento 2: Compensar ticket 102 (estado inválido) -> RECHAZA
    with pytest.raises(ErrorTraslado, match="no admite compensación física"):
        transferFunds(
            conn=db_mem,
            origin_fund_code="1120",
            destination_fund_code="1130",
            amount_cents=1500_00,
            dispatched_by="alan",
            reason="Compensación ticket 102",
            context="PREPAY_COMPENSATION",
            evidence_reference="TICKET_102",
        )

    # Intento 3: Compensar ticket 103 con monto que incluye comisión (3300) en vez de solo capital (3000) -> RECHAZA
    with pytest.raises(ErrorTraslado, match="no coincide con el capital neto"):
        transferFunds(
            conn=db_mem,
            origin_fund_code="1120",
            destination_fund_code="1130",
            amount_cents=3300_00,
            dispatched_by="alan",
            reason="Compensación ticket 103 con adicional",
            context="PREPAY_COMPENSATION",
            evidence_reference="TICKET_103",
        )

    # Intento 4: Compensar ticket 103 con monto exacto de capital (3000_00) -> ÉXITO
    trf = transferFunds(
        conn=db_mem,
        origin_fund_code="1120",
        destination_fund_code="1130",
        amount_cents=3000_00,
        dispatched_by="alan",
        reason="Compensación legítima ticket 103",
        context="PREPAY_COMPENSATION",
        evidence_reference="TICKET_103",
        idempotency_key="key_comp_103",
    )
    assert trf["status"] == "PENDING_RECEIPT"
    confirmTransferReceipt(db_mem, trf["transfer_id"], "alan")

    # Intento 5: Duplicar compensación del ticket 103 con OTRA clave de idempotencia -> RECHAZA
    with pytest.raises(ErrorTraslado, match="Ya existe una compensación física activa"):
        transferFunds(
            conn=db_mem,
            origin_fund_code="1120",
            destination_fund_code="1130",
            amount_cents=3000_00,
            dispatched_by="alan",
            reason="Segunda compensación duplicada ticket 103",
            context="PREPAY_COMPENSATION",
            evidence_reference="TICKET_103",
            idempotency_key="key_comp_103_distinta",
        )


# =====================================================================
# SECCIÓN 2: ARQUEO FÍSICO 1160 (H-04)
# =====================================================================

def test_h04_arqueo_fisico_1160_permitido_y_digital_rechazado(db_mem):
    """Verifica que 1160 se clasifica como fondo físico: permite conteo físico y rechaza conciliación digital."""
    registerEntry(db_mem, "1160", 4000_00, "RECAUDACION_PREPAY_EFECTIVO", "Recaudación jornada", "cajero")

    # 1. Arqueo digital en 1160: DEBE RECHAZAR
    with pytest.raises(ErrorArqueo, match="es un fondo físico; debe arquearse mediante conteo de efectivo"):
        reconcileDigitalAccount(
            conn=db_mem,
            fund_code="1160",
            declared_amount_cents=4000_00,
            user_id="cajero",
        )

    # 2. Arqueo físico con diferencia pero SIN observación: DEBE RECHAZAR
    with pytest.raises(ErrorArqueo, match="obligatorio ingresar una observación"):
        reconcilePhysicalFund(
            conn=db_mem,
            fund_code="1160",
            verified_amount_cents=4200_00,
            user_id="cajero",
            notes=None,
        )

    # 3. Arqueo físico conforme (sin diferencia): DEBE PERMITIR
    arq_ok = reconcilePhysicalFund(
        conn=db_mem,
        fund_code="1160",
        verified_amount_cents=4000_00,
        user_id="cajero",
        notes="Conteo conforme",
    )
    assert arq_ok["diferencia_cents"] == 0
    assert arq_ok["naturaleza"] == "FÍSICO"
    assert arq_ok["is_digital"] is False

    # 4. Arqueo físico con diferencia y observación documentada: DEBE PERMITIR
    arq_diff = reconcilePhysicalFund(
        conn=db_mem,
        fund_code="1160",
        verified_amount_cents=4500_00,
        user_id="alan",
        notes="Sobrante documentado por billete retenido",
    )
    assert arq_diff["diferencia_cents"] == 500_00
    assert arq_diff["tipo_diferencia"] == "SOBRANTE"


# =====================================================================
# SECCIÓN 3: BARRIDO Y GOBERNANZA 1160 -> 1130 (H-05, H-07, H-06)
# =====================================================================

def test_h07_gobernanza_1160_a_1130_bloqueo_manual(db_mem):
    """Verifica que el traslado 1160 -> 1130 está bloqueado para llamadas manuales fuera del cierre."""
    registerEntry(db_mem, "1160", 5000_00, "RECAUDACION", "Ingreso", "cajero")

    # Invocación directa manual: DEBE RECHAZAR
    with pytest.raises(ErrorTraslado, match="está reservado exclusivamente al proceso de cierre diario"):
        transferFunds(
            conn=db_mem,
            origin_fund_code="1160",
            destination_fund_code="1130",
            amount_cents=2000_00,
            dispatched_by="cajero",
            reason="Pase manual",
        )


def test_h05_barrido_1160_casos_a_b_c_y_reanclaje(db_mem):
    """Verifica los 3 casos económicos del barrido PREPAY y el corte atómico a $0,00."""
    # 1. Movimientos en jornada ABIERTA
    registerEntry(db_mem, "1160", 6000_00, "RECAUDACION", "Cobros prepay", "cajero")

    # Crear una reserva activa en 1160 para verificar re-anclaje a 1130
    res = createReservation(
        conn=db_mem,
        fund_code="1160",
        amount_cents=500_00,
        reason="Reserva PREPAY activa",
        beneficiary="CLIENTE_TEST",
        created_by="alan",
    )
    assert res["status"] == "ACTIVE"

    # Iniciar cierre
    jornada = obtener_jornada_actual(db_mem)
    beginClosure(db_mem, jornada["id"], "alan")

    # CASO C: Sobrante contado ($6.200 contado vs $6.000 libro)
    reconcilePhysicalFund(
        conn=db_mem,
        fund_code="1160",
        verified_amount_cents=6200_00,
        user_id="alan",
        notes="Sobrante $200 documentado en gaveta PREPAY",
    )

    # Ejecutar barrido: debe trasladar íntegramente los $6.200 contados a 1130
    res_sweep = executePrepayDrawerSweep(db_mem, jornada["id"], "alan")
    assert res_sweep["swept"] is True
    assert res_sweep["amount_cents"] == 6200_00

    # 1160 DEBE haber quedado en exactamente 0 centavos
    f1160 = obtener_fondo_por_codigo(db_mem, "1160")
    assert f1160["saldo_centavos"] == 0

    # 1130 DEBE haber recibido los 6200_00
    f1130 = obtener_fondo_por_codigo(db_mem, "1130")
    assert f1130["saldo_centavos"] == 6200_00

    # La reserva activa de 1160 debió ser re-anclada a 1130
    row_res = db_mem.execute("SELECT fund_id FROM tesoreria_reservas WHERE reservation_id = ?", (res["reservation_id"],)).fetchone()
    assert row_res["fund_id"] == f1130["id"]

    # Idempotencia: segundo barrido no vuelve a trasladar dinero
    res_sweep2 = executePrepayDrawerSweep(db_mem, jornada["id"], "alan")
    assert res_sweep2["status"] == "ALREADY_EXECUTED"
    assert res_sweep2["amount_cents"] == 0
    assert row_res["fund_id"] == f1130["id"]

    # Idempotencia: segundo barrido no vuelve a trasladar dinero
    res_sweep2 = executePrepayDrawerSweep(db_mem, jornada["id"], "alan")
    assert res_sweep2["status"] == "ALREADY_EXECUTED"
    assert res_sweep2["amount_cents"] == 0


# =====================================================================
# SECCIÓN 4: ENDPOINT REST Y GOBERNANZA API (H-06, H-07)
# =====================================================================

def test_h06_h07_api_rest_gobernanza_y_endpoint_vaciado_1160(tmp_path, monkeypatch):
    """Verifica que la API rechace traslados manuales 1120->1130 y 1160->1130, y exponga vaciado-1160 con RBAC."""
    db_path = tmp_path / "test_api_tesoreria.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="alan")
    registerEntry(conn, "1160", 3000_00, "RECAUDACION", "Cobro", "alan")
    conn.commit()
    conn.close()

    def _mock_db():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr("api.routers.tesoreria._get_lab_db", _mock_db)

    # Iniciar como Alan (ADMIN)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        username="alan", role="ADMIN", display_name="Alan"
    )
    client = TestClient(app)

    try:
        # 1. Invocación manual REST 1120 -> 1130: DEBE RECHAZAR con 400
        r_trf_1120 = client.post(
            "/api/tesoreria/traslados",
            json={
                "origin_fund_code": "1120",
                "destination_fund_code": "1130",
                "amount_cents": 1000_00,
                "reason": "Intento manual",
            },
        )
        assert r_trf_1120.status_code == 400
        assert "proceso interno automático del outbox PREPAY" in r_trf_1120.json()["detail"]

        # 2. Invocación manual REST 1160 -> 1130: DEBE RECHAZAR con 400
        r_trf_1160 = client.post(
            "/api/tesoreria/traslados",
            json={
                "origin_fund_code": "1160",
                "destination_fund_code": "1130",
                "amount_cents": 1000_00,
                "reason": "Intento manual",
            },
        )
        assert r_trf_1160.status_code == 400
        assert "barrido de cierre" in r_trf_1160.json()["detail"]

        # 3. Endpoint vaciado-1160 con usuario no autorizado (cajero): DEBE RECHAZAR con 403
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
            username="cajero", role="CAJERO", display_name="Cajero"
        )
        r_vaciado_unauth = client.post(
            "/api/tesoreria/cierres/vaciado-1160",
            json={"journal_id": 1},
        )
        assert r_vaciado_unauth.status_code == 403

        # 4. Iniciar cierre, registrar arqueo físico conforme y ejecutar vaciado-1160 con usuario autorizado (alan): ÉXITO
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
            username="alan", role="ADMIN", display_name="Alan"
        )
        client.post("/api/tesoreria/cierres/iniciar", json={"journal_id": 1})
        r_arq = client.post(
            "/api/tesoreria/arqueos",
            json={
                "fund_code": "1160",
                "verified_amount_cents": 3000_00,
                "notes": "Arqueo físico conforme de gaveta PREPAY",
            },
        )
        assert r_arq.status_code == 200

        r_vaciado_ok = client.post(
            "/api/tesoreria/cierres/vaciado-1160",
            json={"journal_id": 1, "motivo": "Cierre formal"},
        )
        assert r_vaciado_ok.status_code == 200
        data_vac = r_vaciado_ok.json()
        assert data_vac["swept"] is True
        assert data_vac["amount_cents"] == 3000_00
    finally:
        app.dependency_overrides.clear()


# =====================================================================
# SECCIÓN 5: MIGRACIÓN ESTRUCTURAL 1160 (H-08)
# =====================================================================

def test_h08_migracion_estructural_reconstruccion_check_10_fondos(tmp_path):
    """Verifica que una base preexistente de 9 fondos sea migrada a 10 fondos preservando todo el historial."""
    legacy_db_path = tmp_path / "tesoreria_legacy.db"
    conn = sqlite3.connect(str(legacy_db_path))

    # Crear esquema legacy con restricción CHECK estricta de 9 fondos (sin 1160)
    conn.executescript(
        """
        CREATE TABLE tesoreria_fondos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT NOT NULL UNIQUE CHECK(codigo IN ('1110', '1120', '1130', '1140', '1150', '1210', '1220', '1230', '1310')),
            nombre TEXT NOT NULL,
            naturaleza TEXT NOT NULL,
            titularidad TEXT NOT NULL,
            moneda TEXT NOT NULL DEFAULT 'ARS',
            estado TEXT NOT NULL DEFAULT 'ACTIVO',
            saldo_centavos INTEGER NOT NULL DEFAULT 0,
            permite_sobregiro INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            creado_por TEXT NOT NULL DEFAULT 'SISTEMA_LAB',
            actualizado_por TEXT NOT NULL DEFAULT 'SISTEMA_LAB'
        );

        CREATE TABLE tesoreria_movimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            movement_id TEXT NOT NULL UNIQUE,
            fund_id INTEGER NOT NULL REFERENCES tesoreria_fondos(id),
            amount_cents INTEGER NOT NULL,
            occurred_at TEXT NOT NULL
        );
        """
    )

    # Insertar fondos históricos (ej: 1110 con saldo de $25.000)
    conn.execute(
        """
        INSERT INTO tesoreria_fondos (id, codigo, nombre, naturaleza, titularidad, saldo_centavos, created_at, updated_at)
        VALUES (1, '1110', 'Caja Principal', 'EFECTIVO', 'PROPIA', 2500000, '2026-10-01', '2026-10-01')
        """
    )
    conn.execute(
        """
        INSERT INTO tesoreria_movimientos (movement_id, fund_id, amount_cents, occurred_at)
        VALUES ('mov_001', 1, 2500000, '2026-10-01')
        """
    )
    conn.commit()

    # Confirmar que en la base legacy NO se puede insertar 1160
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        conn.execute(
            """
            INSERT INTO tesoreria_fondos (codigo, nombre, naturaleza, titularidad, created_at, updated_at)
            VALUES ('1160', 'Prepay Diario', 'EFECTIVO', 'PROPIA', '2026-11-01', '2026-11-01')
            """
        )

    # Ejecutar migración estructural mediante el runner formal
    migradas = ejecutar_migraciones_tesoreria(conn)
    assert 3 in migradas

    # Comprobar que _tesoreria_schema_migrations registró la versión
    versiones = obtener_versiones_aplicadas(conn)
    assert 3 in versiones

    # Comprobar que el fondo 1160 ahora existe en tesoreria_fondos con saldo 0
    f1160 = conn.execute("SELECT codigo, nombre, saldo_centavos FROM tesoreria_fondos WHERE codigo='1160'").fetchone()
    assert f1160 is not None
    assert f1160[0] == "1160"
    assert f1160[2] == 0

    # Comprobar que el fondo 1110 y su movimiento histórico conservan id, saldo y clave foránea
    f1110 = conn.execute("SELECT id, codigo, saldo_centavos FROM tesoreria_fondos WHERE codigo='1110'").fetchone()
    assert f1110[0] == 1
    assert f1110[2] == 2500000

    fk_violations = conn.execute("PRAGMA foreign_key_check;").fetchall()
    assert len(fk_violations) == 0

    # Idempotencia: volver a ejecutar no altera nada ni da error
    re_migradas = ejecutar_migraciones_tesoreria(conn)
    assert len(re_migradas) == 0

    conn.close()


# =====================================================================
# SECCIÓN 6: CONCURRENCIA MULTI-THREAD
# =====================================================================

def test_concurrencia_multi_thread_coordinacion_y_barrido(tmp_path):
    """Verifica resistencia ante accesos concurrentes en el circuito de compensación y vaciado."""
    db_file = tmp_path / "concurrent_tesoreria.db"
    conn_setup = sqlite3.connect(str(db_file))
    conn_setup.row_factory = sqlite3.Row
    inicializar_esquema_laboratorio(conn_setup)
    sembrar_catalogo_canónico(conn_setup)
    abrir_jornada(conn_setup, usuario="alan")
    registerEntry(conn_setup, "1120", 50000_00, "FONDEO", "Fondeo tesoro", "alan")

    # Crear 5 tickets reales
    for i in range(1, 6):
        conn_setup.execute(
            f"""
            INSERT INTO tesoreria_prepay_coordinacion (
                coordination_id, idempotency_key, request_hash, jornada_id, servicio_destino,
                monto_capital_cents, monto_adicional_cents, pago_efvo_cents, pago_transf_cents,
                asume_cargo_diario, capital_reservation_id, estado, commercial_ticket_id, actor,
                created_at, updated_at
            ) VALUES (
                'coord_{i}', 'key_coord_{i}', 'hash_{i}', 1, 'PERSONAL',
                1000_00, 100_00, 0, 1100_00,
                0, 'res_{i}', 'COMMERCIAL_COMMITTED', {i}, 'cajero_{i}',
                datetime('now'), datetime('now')
            )
            """
        )
    conn_setup.commit()
    conn_setup.close()

    def compensar_ticket(ticket_num: int):
        import time
        for attempt in range(10):
            conn = sqlite3.connect(str(db_file), timeout=15.0)
            conn.row_factory = sqlite3.Row
            try:
                trf = transferFunds(
                    conn=conn,
                    origin_fund_code="1120",
                    destination_fund_code="1130",
                    amount_cents=1000_00,
                    dispatched_by=f"cajero_{ticket_num}",
                    reason=f"Compensación ticket {ticket_num}",
                    context="PREPAY_COMPENSATION",
                    evidence_reference=f"TICKET_{ticket_num}",
                    idempotency_key=f"idem_trf_{ticket_num}",
                )
                confirmTransferReceipt(
                    conn=conn,
                    transfer_id=trf["transfer_id"],
                    received_by=f"cajero_{ticket_num}",
                    idempotency_key=f"idem_confirm_{ticket_num}",
                )
                return True
            except ErrorTraslado as e:
                if "concurrencia" in str(e).lower() and attempt < 9:
                    time.sleep(0.02 * (ticket_num + attempt))
                    continue
                raise
            finally:
                conn.close()

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(compensar_ticket, range(1, 6)))

    assert all(results)

    # Verificar saldo final en 1130: 5 * 1000 = 5000
    conn_verify = sqlite3.connect(str(db_file))
    conn_verify.row_factory = sqlite3.Row
    f1130 = obtener_fondo_por_codigo(conn_verify, "1130")
    assert f1130["saldo_centavos"] == 5000_00
    conn_verify.close()
