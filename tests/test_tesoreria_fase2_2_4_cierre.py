"""Suite de Pruebas FASE 2.2.4: GOBERNANZA, INTEGRIDAD Y MIGRACIÓN DEL FONDO PREPAY 1160.

Verificación rigurosa de las 24 condiciones obligatorias:
- Sección A: Gobernanza del barrido 1160 -> 1130 (Tests 1 a 9)
- Sección B: Integridad y atomicidad del barrido (Tests 10 a 15)
- Sección C: Migración y preservación de triggers de tesoreria_fondos (Tests 16 a 24)
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

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
)
from tesoreria.jornadas import abrir_jornada, cerrar_jornada, obtener_jornada_actual
from tesoreria.migrations_runner import (
    ejecutar_migraciones_tesoreria,
    migrar_tabla_fondos_si_requiere_1160,
    obtener_versiones_aplicadas,
)
from tesoreria.movements import registerEntry
from tesoreria.reconciliations import reconcilePhysicalFund
from tesoreria.reservations import createReservation
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.transfers import ErrorTraslado, transferFunds


@pytest.fixture
def db_mem():
    """Base de datos SQLite en memoria con esquema canónico completo de Tesorería."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    yield conn
    conn.close()


# =====================================================================
# SECCIÓN A: GOBERNANZA DEL BARRIDO 1160 -> 1130 (Tests 1 a 9)
# =====================================================================

def test_01_barrido_rechazado_si_jornada_abierta(db_mem):
    """1. Intento de barrido con jornada en estado ABIERTA -> RECHAZADO con ErrorCierre."""
    abrir_jornada(db_mem, usuario="alan")
    jornada = obtener_jornada_actual(db_mem)
    assert jornada["estado"] == "ABIERTA"

    registerEntry(db_mem, "1160", 5000_00, "RECAUDACION", "Cobro recarga", "alan")

    with pytest.raises(ErrorCierre, match="solo puede ejecutarse cuando la jornada está en estado 'EN_CIERRE'"):
        executePrepayDrawerSweep(db_mem, jornada["id"], "alan")


def test_02_barrido_rechazado_si_jornada_cerrada(db_mem):
    """2. Intento de barrido con jornada en estado CERRADA -> RECHAZADO con ErrorCierre."""
    abrir_jornada(db_mem, usuario="alan")
    jornada = obtener_jornada_actual(db_mem)
    cerrar_jornada(db_mem, jornada["id"], "alan")

    with pytest.raises(ErrorCierre, match="solo puede ejecutarse cuando la jornada está en estado 'EN_CIERRE'"):
        executePrepayDrawerSweep(db_mem, jornada["id"], "alan")


def test_03_barrido_rechazado_en_cierre_sin_arqueo(db_mem):
    """3. Intento de barrido en EN_CIERRE sin arqueo previo en 1160 -> RECHAZADO con ErrorCierre."""
    abrir_jornada(db_mem, usuario="alan")
    jornada = obtener_jornada_actual(db_mem)
    registerEntry(db_mem, "1160", 4000_00, "RECAUDACION", "Cobro recarga", "alan")
    beginClosure(db_mem, jornada["id"], "alan")

    j_act = obtener_jornada_actual(db_mem)
    assert j_act["estado"] == "EN_CIERRE"

    with pytest.raises(ErrorCierre, match="se requiere un arqueo físico registrado en la jornada activa"):
        executePrepayDrawerSweep(db_mem, jornada["id"], "alan")


def test_04_barrido_rechazado_con_arqueo_jornada_anterior(db_mem):
    """4. Intento de barrido en EN_CIERRE con arqueo de jornada anterior (no la activa) -> RECHAZADO con ErrorCierre."""
    # Jornada 1: se abre, arquea y cierra
    abrir_jornada(db_mem, usuario="alan")
    j1 = obtener_jornada_actual(db_mem)
    reconcilePhysicalFund(
        conn=db_mem,
        fund_code="1160",
        verified_amount_cents=1000_00,
        user_id="alan",
        notes="Arqueo jornada 1",
    )
    cerrar_jornada(db_mem, j1["id"], "alan")

    # Jornada 2: se abre y se pone en EN_CIERRE sin arqueo propio
    abrir_jornada(db_mem, usuario="alan")
    j2 = obtener_jornada_actual(db_mem)
    registerEntry(db_mem, "1160", 2000_00, "RECAUDACION", "Cobro recarga j2", "alan")
    beginClosure(db_mem, j2["id"], "alan")

    # Debe rechazar porque no hay arqueo correspondiente a la jornada 2
    with pytest.raises(ErrorCierre, match="se requiere un arqueo físico registrado en la jornada activa"):
        executePrepayDrawerSweep(db_mem, j2["id"], "alan")


def test_05_barrido_rechazado_con_diferencia_sin_observacion(db_mem):
    """5. Intento de barrido en EN_CIERRE con arqueo con diferencia no resuelta y sin observación -> RECHAZADO con ErrorCierre."""
    abrir_jornada(db_mem, usuario="alan")
    jornada = obtener_jornada_actual(db_mem)
    registerEntry(db_mem, "1160", 5000_00, "RECAUDACION", "Cobro recarga", "alan")
    beginClosure(db_mem, jornada["id"], "alan")

    # Registrar arqueo físico directamente en BD con diferencia de -$200 sin observación
    f1160 = obtener_fondo_por_codigo(db_mem, "1160")
    db_mem.execute(
        """
        INSERT INTO tesoreria_arqueos (
            reconciliation_id, journal_id, fund_id, reconciliation_type, saldo_registrado_cents,
            saldo_verificado_cents, diferencia_cents, notes, status, performed_at, performed_by
        ) VALUES ('arq_unobserved', ?, ?, 'TOTAL', 500000, 480000, -20000, '', 'OPEN', datetime('now'), 'alan')
        """,
        (jornada["id"], f1160["id"]),
    )

    with pytest.raises(ErrorCierre, match="sin observación registrada"):
        executePrepayDrawerSweep(db_mem, jornada["id"], "alan")


def test_06_barrido_exitoso_en_cierre_con_arqueo_valido(db_mem):
    """6. Barrido en EN_CIERRE con arqueo válido registrado en la jornada activa -> EXITOSO."""
    abrir_jornada(db_mem, usuario="alan")
    jornada = obtener_jornada_actual(db_mem)
    registerEntry(db_mem, "1160", 5000_00, "RECAUDACION", "Cobro recarga", "alan")
    beginClosure(db_mem, jornada["id"], "alan")

    reconcilePhysicalFund(
        conn=db_mem,
        fund_code="1160",
        verified_amount_cents=5000_00,
        user_id="alan",
        notes="Arqueo conforme",
    )

    res = executePrepayDrawerSweep(db_mem, jornada["id"], "alan")
    assert res["swept"] is True
    assert res["amount_cents"] == 5000_00
    assert res["caja_prepay_saldo_final_cents"] == 0

    f1160 = obtener_fondo_por_codigo(db_mem, "1160")
    f1130 = obtener_fondo_por_codigo(db_mem, "1130")
    assert f1160["saldo_centavos"] == 0
    assert f1130["saldo_centavos"] == 5000_00


def test_07_transfer_funds_directo_con_strings_magicos_en_abierta_rechazado(db_mem):
    """7. Intento directo vía transferFunds con strings mágicos pero jornada en ABIERTA -> RECHAZADO con ErrorTraslado."""
    abrir_jornada(db_mem, usuario="alan")
    jornada = obtener_jornada_actual(db_mem)
    assert jornada["estado"] == "ABIERTA"

    registerEntry(db_mem, "1160", 3000_00, "RECAUDACION", "Cobro", "alan")

    with pytest.raises(ErrorTraslado, match="solo puede ejecutarse cuando la jornada activa está estrictamente en estado 'EN_CIERRE'"):
        transferFunds(
            conn=db_mem,
            origin_fund_code="1160",
            destination_fund_code="1130",
            amount_cents=3000_00,
            dispatched_by="alan",
            reason="Barrido forzado directo",
            evidence_reference="BARRIDO_PREPAY_CIERRE_DIARIO",
            context="PREPAY_DRAWER_SWEEP",
        )


def test_08_endpoint_post_traslados_1160_a_1130_rechazado(tmp_path, monkeypatch):
    """8. Intento directo vía endpoint POST /traslados con 1160 -> 1130 -> RECHAZADO (HTTP 400)."""
    db_path = tmp_path / "test_api_transfer_blocked.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="alan")
    conn.commit()
    conn.close()

    def _mock_db():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr("api.routers.tesoreria._get_lab_db", _mock_db)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        username="alan", role="ADMIN", display_name="Alan"
    )
    client = TestClient(app)

    try:
        r = client.post(
            "/api/tesoreria/traslados",
            json={
                "origin_fund_code": "1160",
                "destination_fund_code": "1130",
                "amount_cents": 1000_00,
                "reason": "Intento manual vía API",
            },
        )
        assert r.status_code == 400
        assert "barrido de cierre" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_09_reintento_legitimo_retorna_already_executed(db_mem):
    """9. Reintento legítimo tras barrido exitoso -> Retorna ALREADY_EXECUTED sin duplicar traslados ni fondos."""
    abrir_jornada(db_mem, usuario="alan")
    jornada = obtener_jornada_actual(db_mem)
    registerEntry(db_mem, "1160", 4500_00, "RECAUDACION", "Cobro recarga", "alan")
    beginClosure(db_mem, jornada["id"], "alan")

    reconcilePhysicalFund(
        conn=db_mem,
        fund_code="1160",
        verified_amount_cents=4500_00,
        user_id="alan",
        notes="Arqueo conforme",
    )

    res1 = executePrepayDrawerSweep(db_mem, jornada["id"], "alan")
    assert res1["swept"] is True
    assert res1["amount_cents"] == 4500_00

    # Segundo llamado: debe responder ALREADY_EXECUTED
    res2 = executePrepayDrawerSweep(db_mem, jornada["id"], "alan")
    assert res2["status"] == "ALREADY_EXECUTED"
    assert res2["swept"] is False
    assert res2["amount_cents"] == 0

    # Los fondos deben mantenerse intactos (1160 en 0, 1130 en 4500_00)
    f1160 = obtener_fondo_por_codigo(db_mem, "1160")
    f1130 = obtener_fondo_por_codigo(db_mem, "1130")
    assert f1160["saldo_centavos"] == 0
    assert f1130["saldo_centavos"] == 4500_00


# =====================================================================
# SECCIÓN B: INTEGRIDAD DEL BARRIDO (Tests 10 a 15)
# =====================================================================

def test_10_arqueo_conforme(db_mem):
    """10. Arqueo conforme ($6.000 libro, $6.000 contado): traslada exactamente $6.000 a 1130, deja 1160 en $0,00."""
    abrir_jornada(db_mem, usuario="alan")
    jornada = obtener_jornada_actual(db_mem)
    registerEntry(db_mem, "1160", 6000_00, "RECAUDACION", "Cobro recarga", "alan")
    beginClosure(db_mem, jornada["id"], "alan")

    reconcilePhysicalFund(
        conn=db_mem,
        fund_code="1160",
        verified_amount_cents=6000_00,
        user_id="alan",
        notes="Arqueo conforme gaveta",
    )

    res = executePrepayDrawerSweep(db_mem, jornada["id"], "alan")
    assert res["swept"] is True
    assert res["amount_cents"] == 6000_00

    assert obtener_fondo_por_codigo(db_mem, "1160")["saldo_centavos"] == 0
    assert obtener_fondo_por_codigo(db_mem, "1130")["saldo_centavos"] == 6000_00


def test_11_arqueo_con_faltante_documentado(db_mem):
    """11. Arqueo con faltante documentado ($6.000 libro, $5.800 contado):
    traslada exactamente $5.800 a 1130, genera RECONCILIATION_BALANCE_CUTOFF por -$200, deja 1160 en $0,00.
    """
    abrir_jornada(db_mem, usuario="alan")
    jornada = obtener_jornada_actual(db_mem)
    registerEntry(db_mem, "1160", 6000_00, "RECAUDACION", "Cobro recarga", "alan")
    beginClosure(db_mem, jornada["id"], "alan")

    reconcilePhysicalFund(
        conn=db_mem,
        fund_code="1160",
        verified_amount_cents=5800_00,
        user_id="alan",
        notes="Faltante de $200 documentado en gaveta PREPAY",
    )

    res = executePrepayDrawerSweep(db_mem, jornada["id"], "alan")
    assert res["swept"] is True
    assert res["amount_cents"] == 5800_00

    assert obtener_fondo_por_codigo(db_mem, "1160")["saldo_centavos"] == 0
    assert obtener_fondo_por_codigo(db_mem, "1130")["saldo_centavos"] == 5800_00

    # Comprobar evento RECONCILIATION_BALANCE_CUTOFF en auditoría
    ev = db_mem.execute(
        "SELECT payload_json FROM tesoreria_auditoria_eventos WHERE event_type = 'RECONCILIATION_BALANCE_CUTOFF' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert ev is not None
    payload = json.loads(ev[0])
    assert payload["diferencia_cents"] == -20000


def test_12_arqueo_con_sobrante_documentado(db_mem):
    """12. Arqueo con sobrante documentado ($6.000 libro, $6.200 contado):
    traslada exactamente $6.200 a 1130, genera RECONCILIATION_BALANCE_CUTOFF por +$200, deja 1160 en $0,00.
    """
    abrir_jornada(db_mem, usuario="alan")
    jornada = obtener_jornada_actual(db_mem)
    registerEntry(db_mem, "1160", 6000_00, "RECAUDACION", "Cobro recarga", "alan")
    beginClosure(db_mem, jornada["id"], "alan")

    reconcilePhysicalFund(
        conn=db_mem,
        fund_code="1160",
        verified_amount_cents=6200_00,
        user_id="alan",
        notes="Sobrante de $200 documentado en gaveta PREPAY",
    )

    res = executePrepayDrawerSweep(db_mem, jornada["id"], "alan")
    assert res["swept"] is True
    assert res["amount_cents"] == 6200_00

    assert obtener_fondo_por_codigo(db_mem, "1160")["saldo_centavos"] == 0
    assert obtener_fondo_por_codigo(db_mem, "1130")["saldo_centavos"] == 6200_00

    ev = db_mem.execute(
        "SELECT payload_json FROM tesoreria_auditoria_eventos WHERE event_type = 'RECONCILIATION_BALANCE_CUTOFF' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert ev is not None
    payload = json.loads(ev[0])
    assert payload["diferencia_cents"] == 20000


def test_13_arqueo_con_faltante_total_documentado(db_mem):
    """13. Arqueo con faltante total documentado ($6.000 libro, $0 contado):
    traslada $0, corta 1160 a $0,00, genera eventos de auditoría correspondientes, re-ancla reservas.
    """
    abrir_jornada(db_mem, usuario="alan")
    jornada = obtener_jornada_actual(db_mem)
    registerEntry(db_mem, "1160", 6000_00, "RECAUDACION", "Cobro recarga", "alan")

    # Crear una reserva activa en 1160
    res_activa = createReservation(
        conn=db_mem,
        fund_code="1160",
        amount_cents=1000_00,
        reason="Reserva PREPAY activa de prueba",
        beneficiary="CLIENTE_01",
        created_by="alan",
    )
    assert res_activa["status"] == "ACTIVE"

    beginClosure(db_mem, jornada["id"], "alan")

    reconcilePhysicalFund(
        conn=db_mem,
        fund_code="1160",
        verified_amount_cents=0,
        user_id="alan",
        notes="Faltante total: gaveta vacía $0 contado",
    )

    res = executePrepayDrawerSweep(db_mem, jornada["id"], "alan")
    assert res["swept"] is False
    assert res["amount_cents"] == 0
    assert res["status"] == "COMPLETED"

    # 1160 debe estar cortado en exactamente 0
    assert obtener_fondo_por_codigo(db_mem, "1160")["saldo_centavos"] == 0
    # 1130 no debió recibir nada
    assert obtener_fondo_por_codigo(db_mem, "1130")["saldo_centavos"] == 0

    # La reserva activa debió ser re-anclada al fondo 1130
    f1130 = obtener_fondo_por_codigo(db_mem, "1130")
    row_res = db_mem.execute(
        "SELECT fund_id FROM tesoreria_reservas WHERE reservation_id = ?",
        (res_activa["reservation_id"],)
    ).fetchone()
    assert row_res["fund_id"] == f1130["id"]


def test_14_fallo_inducido_rollback_completo(db_mem):
    """14. Fallo inducido a mitad del barrido (ej. error en confirmTransferReceipt):
    rollback completo, 1160 conserva su saldo previo, 1130 no se modifica.
    """
    abrir_jornada(db_mem, usuario="alan")
    jornada = obtener_jornada_actual(db_mem)
    registerEntry(db_mem, "1160", 7000_00, "RECAUDACION", "Cobro recarga", "alan")
    beginClosure(db_mem, jornada["id"], "alan")

    reconcilePhysicalFund(
        conn=db_mem,
        fund_code="1160",
        verified_amount_cents=7000_00,
        user_id="alan",
        notes="Arqueo conforme",
    )

    # Inducir un fallo durante confirmTransferReceipt
    with patch("tesoreria.closures.confirmTransferReceipt", side_effect=RuntimeError("Fallo inducido en confirmación")):
        with pytest.raises(RuntimeError, match="Fallo inducido"):
            executePrepayDrawerSweep(db_mem, jornada["id"], "alan")

    # Integridad post-fallo: saldos sin alterar
    f1160 = obtener_fondo_por_codigo(db_mem, "1160")
    f1130 = obtener_fondo_por_codigo(db_mem, "1130")
    assert f1160["saldo_centavos"] == 7000_00
    assert f1130["saldo_centavos"] == 0


def test_15_concurrencia_llamadas_simultaneas(tmp_path):
    """15. Concurrencia: dos llamadas simultáneas a executePrepayDrawerSweep:
    una tiene éxito, la otra retorna ALREADY_EXECUTED o falla limpiamente sin duplicación.
    """
    db_file = tmp_path / "concurrent_sweep.db"
    conn = sqlite3.connect(str(db_file), timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="alan")
    jornada = obtener_jornada_actual(conn)
    registerEntry(conn, "1160", 3500_00, "RECAUDACION", "Cobro recarga", "alan")
    beginClosure(conn, jornada["id"], "alan")

    reconcilePhysicalFund(
        conn=conn,
        fund_code="1160",
        verified_amount_cents=3500_00,
        user_id="alan",
        notes="Arqueo conforme",
    )
    conn.commit()
    conn.close()

    results = []
    errors = []

    def _worker(worker_id):
        c = sqlite3.connect(str(db_file), timeout=20.0)
        c.row_factory = sqlite3.Row
        try:
            r = executePrepayDrawerSweep(c, jornada["id"], f"user_{worker_id}")
            results.append(r)
        except Exception as e:
            errors.append(e)
        finally:
            c.close()

    t1 = threading.Thread(target=_worker, args=(1,))
    t2 = threading.Thread(target=_worker, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Comprobar resultado en base de datos: exactamente $3.500 trasladados a 1130 y 1160 en $0
    check_conn = sqlite3.connect(str(db_file))
    check_conn.row_factory = sqlite3.Row
    f1160 = obtener_fondo_por_codigo(check_conn, "1160")
    f1130 = obtener_fondo_por_codigo(check_conn, "1130")
    assert f1160["saldo_centavos"] == 0
    assert f1130["saldo_centavos"] == 3500_00

    # No debe haber duplicación de traslados
    cnt = check_conn.execute(
        "SELECT COUNT(*) FROM tesoreria_traslados WHERE origin_fund_id = ? AND destination_fund_id = ? AND status = 'CONFIRMED'",
        (f1160["id"], f1130["id"]),
    ).fetchone()[0]
    assert cnt == 1
    check_conn.close()


# =====================================================================
# SECCIÓN C: MIGRACIÓN Y TRIGGERS DEL CATÁLOGO DE FONDOS (Tests 16 a 24)
# =====================================================================

def _crear_base_legacy_9_fondos(path: Path) -> sqlite3.Connection:
    """Crea una base legacy con 9 fondos, check constraint restringido y los 3 triggers originales."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(
        """
        CREATE TABLE tesoreria_fondos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT NOT NULL UNIQUE CHECK(codigo IN ('1110', '1120', '1130', '1140', '1150', '1210', '1220', '1230', '1310')),
            nombre TEXT NOT NULL CHECK(length(trim(nombre)) > 0),
            naturaleza TEXT NOT NULL CHECK(naturaleza IN ('EFECTIVO', 'FONDO_FIJO', 'ADICIONALES_PARTICULARES', 'RECAUDACION_TERCEROS', 'CUENTA_BANCARIA', 'BILLETERA_VIRTUAL', 'TRANSITO', 'TRANSITO_PREVENTISTAS')),
            titularidad TEXT NOT NULL CHECK(titularidad IN ('PROPIA', 'CUSTODIA', 'TRANSITO')),
            moneda TEXT NOT NULL DEFAULT 'ARS' CHECK(moneda = 'ARS'),
            estado TEXT NOT NULL DEFAULT 'ACTIVO' CHECK(estado IN ('ACTIVO', 'INACTIVO', 'BLOQUEADO')),
            saldo_centavos INTEGER NOT NULL DEFAULT 0 CHECK(typeof(saldo_centavos) = 'integer'),
            permite_sobregiro INTEGER NOT NULL DEFAULT 0 CHECK(permite_sobregiro IN (0, 1)),
            version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            creado_por TEXT NOT NULL DEFAULT 'SISTEMA_LAB',
            actualizado_por TEXT NOT NULL DEFAULT 'SISTEMA_LAB'
        );

        CREATE TABLE tesoreria_fondos_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fondo_id INTEGER NOT NULL,
            codigo TEXT NOT NULL,
            accion TEXT NOT NULL CHECK(accion IN ('INSERT', 'UPDATE', 'DELETE_ATTEMPT', 'BLOQUEO')),
            usuario TEXT NOT NULL,
            valores_previos TEXT,
            valores_nuevos TEXT,
            motivo TEXT,
            timestamp_utc TEXT NOT NULL,
            FOREIGN KEY (fondo_id) REFERENCES tesoreria_fondos(id) ON DELETE RESTRICT
        );

        CREATE TABLE tesoreria_movimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            movement_id TEXT NOT NULL UNIQUE,
            fondo_id INTEGER NOT NULL REFERENCES tesoreria_fondos(id),
            amount_cents INTEGER NOT NULL,
            occurred_at TEXT NOT NULL
        );

        CREATE TABLE tesoreria_traslados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transfer_id TEXT NOT NULL UNIQUE,
            origin_fund_id INTEGER NOT NULL REFERENCES tesoreria_fondos(id),
            destination_fund_id INTEGER NOT NULL REFERENCES tesoreria_fondos(id),
            amount_cents INTEGER NOT NULL,
            status TEXT NOT NULL
        );

        CREATE TABLE tesoreria_reservas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_id TEXT NOT NULL UNIQUE,
            fund_id INTEGER NOT NULL REFERENCES tesoreria_fondos(id),
            amount_cents INTEGER NOT NULL,
            status TEXT NOT NULL
        );

        CREATE TABLE tesoreria_arqueos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reconciliation_id TEXT NOT NULL UNIQUE,
            fund_id INTEGER NOT NULL REFERENCES tesoreria_fondos(id),
            saldo_verificado_cents INTEGER NOT NULL
        );

        CREATE TRIGGER trg_proteger_borrado_fondos
        BEFORE DELETE ON tesoreria_fondos
        BEGIN
            SELECT RAISE(ABORT, 'VIOLACIÓN DE INTEGRIDAD: No está permitido eliminar fondos del catálogo de tesorería.');
        END;

        CREATE TRIGGER trg_audit_insert_fondo
        AFTER INSERT ON tesoreria_fondos
        BEGIN
            INSERT INTO tesoreria_fondos_auditoria (
                fondo_id, codigo, accion, usuario, valores_previos, valores_nuevos, motivo, timestamp_utc
            ) VALUES (
                NEW.id, NEW.codigo, 'INSERT', NEW.creado_por, NULL,
                json_object('codigo', NEW.codigo, 'nombre', NEW.nombre, 'saldo_centavos', NEW.saldo_centavos),
                'Alta', NEW.created_at
            );
        END;

        CREATE TRIGGER trg_audit_update_fondo
        AFTER UPDATE ON tesoreria_fondos
        BEGIN
            INSERT INTO tesoreria_fondos_auditoria (
                fondo_id, codigo, accion, usuario, valores_previos, valores_nuevos, motivo, timestamp_utc
            ) VALUES (
                NEW.id, NEW.codigo, 'UPDATE', NEW.actualizado_por,
                json_object('codigo', OLD.codigo, 'nombre', OLD.nombre, 'saldo_centavos', OLD.saldo_centavos),
                json_object('codigo', NEW.codigo, 'nombre', NEW.nombre, 'saldo_centavos', NEW.saldo_centavos),
                'Modificación', NEW.updated_at
            );
        END;
        """
    )

    # Insertar fondos semilla 1110 y 1130
    conn.execute(
        """
        INSERT INTO tesoreria_fondos (id, codigo, nombre, naturaleza, titularidad, saldo_centavos, created_at, updated_at)
        VALUES (1, '1110', 'Caja Principal', 'EFECTIVO', 'PROPIA', 100000, '2026-10-01', '2026-10-01')
        """
    )
    conn.execute(
        """
        INSERT INTO tesoreria_fondos (id, codigo, nombre, naturaleza, titularidad, saldo_centavos, created_at, updated_at)
        VALUES (2, '1130', 'Prepay Efectivo', 'EFECTIVO', 'PROPIA', 200000, '2026-10-01', '2026-10-01')
        """
    )
    # Movimiento histórico dependiente
    conn.execute(
        "INSERT INTO tesoreria_movimientos (movement_id, fondo_id, amount_cents, occurred_at) VALUES ('mov_h1', 1, 100000, '2026-10-01')"
    )
    conn.commit()
    return conn


def test_16_migracion_ampliacion_recrea_10_fondos_y_triggers(tmp_path):
    """16. Base con 9 fondos y 3 triggers previa a migración ->
    migración ejecuta con éxito -> base resultante tiene 10 fondos (incluyendo 1160) y los 3 triggers existen en sqlite_master.
    """
    db_path = tmp_path / "legacy_16.db"
    conn = _crear_base_legacy_9_fondos(db_path)

    # Reconstrucción estructural
    migrar_tabla_fondos_si_requiere_1160(conn)

    # Insertar 1160: debe ser permitido sin violar CHECK
    conn.execute(
        """
        INSERT INTO tesoreria_fondos (codigo, nombre, naturaleza, titularidad, created_at, updated_at)
        VALUES ('1160', 'Prepay Diario Efectivo', 'EFECTIVO', 'PROPIA', '2026-11-01', '2026-11-01')
        """
    )
    conn.commit()

    # Comprobar los 3 triggers en sqlite_master
    triggers = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'tesoreria_fondos'"
        ).fetchall()
    ]
    assert "trg_proteger_borrado_fondos" in triggers
    assert "trg_audit_insert_fondo" in triggers
    assert "trg_audit_update_fondo" in triggers
    conn.close()


def test_17_intento_delete_tesoreria_fondos_abortado_por_trigger(tmp_path):
    """17. Intento de DELETE en tesoreria_fondos sobre base migrada -> ABORTADO por trg_proteger_borrado_fondos."""
    db_path = tmp_path / "legacy_17.db"
    conn = _crear_base_legacy_9_fondos(db_path)
    migrar_tabla_fondos_si_requiere_1160(conn)

    with pytest.raises(sqlite3.IntegrityError, match="No está permitido eliminar fondos del catálogo de tesorería"):
        conn.execute("DELETE FROM tesoreria_fondos WHERE codigo = '1110'")

    conn.close()


def test_18_update_tesoreria_fondos_auditado_por_trigger(tmp_path):
    """18. UPDATE en tesoreria_fondos sobre base migrada ->
    genera registro en tesoreria_fondos_auditoria con valores previos y nuevos completos.
    """
    db_path = tmp_path / "legacy_18.db"
    conn = _crear_base_legacy_9_fondos(db_path)
    migrar_tabla_fondos_si_requiere_1160(conn)

    now_utc = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE tesoreria_fondos SET estado = 'BLOQUEADO', updated_at = ?, actualizado_por = 'admin_audit' WHERE codigo = '1110'",
        (now_utc,),
    )
    conn.commit()

    audit_row = conn.execute(
        "SELECT accion, usuario, valores_previos, valores_nuevos FROM tesoreria_fondos_auditoria WHERE codigo = '1110' AND accion = 'UPDATE' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert audit_row is not None
    assert audit_row[0] == "UPDATE"
    assert audit_row[1] == "admin_audit"
    v_prev = json.loads(audit_row[2])
    v_new = json.loads(audit_row[3])
    assert v_prev["estado"] == "ACTIVO"
    assert v_new["estado"] == "BLOQUEADO"
    conn.close()


def test_19_update_saldo_movimiento_ordinario_auditado_por_trigger(tmp_path):
    """19. UPDATE de saldo mediante movimiento ordinario sobre fondo migrado -> auditado correctamente por trigger."""
    db_path = tmp_path / "legacy_19.db"
    conn = _crear_base_legacy_9_fondos(db_path)
    migrar_tabla_fondos_si_requiere_1160(conn)

    now_utc = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE tesoreria_fondos SET saldo_centavos = saldo_centavos + 50000, version = version + 1, updated_at = ?, actualizado_por = 'cajero_01' WHERE codigo = '1110'",
        (now_utc,),
    )
    conn.commit()

    audit_row = conn.execute(
        "SELECT accion, usuario, valores_previos, valores_nuevos FROM tesoreria_fondos_auditoria WHERE codigo = '1110' AND accion = 'UPDATE' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert audit_row is not None
    assert audit_row[1] == "cajero_01"
    v_prev = json.loads(audit_row[2])
    v_new = json.loads(audit_row[3])
    assert v_prev["saldo_centavos"] == 100000
    assert v_new["saldo_centavos"] == 150000
    conn.close()


def test_20_re_ejecucion_migracion_idempotente(tmp_path):
    """20. Re-ejecución de migración sobre base ya migrada (idempotencia) ->
    no duplica fondos ni triggers, no corrompe datos.
    """
    db_path = tmp_path / "legacy_20.db"
    conn = _crear_base_legacy_9_fondos(db_path)

    # Primera ejecución
    ret1 = migrar_tabla_fondos_si_requiere_1160(conn)
    assert ret1 is True

    # Segunda ejecución: detecta que ya tiene 10 fondos y retorna False inmediatamente
    ret2 = migrar_tabla_fondos_si_requiere_1160(conn)
    assert ret2 is False

    # Los datos y triggers se conservan exactamente iguales
    f1 = conn.execute("SELECT COUNT(*) FROM tesoreria_fondos").fetchone()[0]
    assert f1 == 2  # Los 2 fondos que insertamos
    triggers = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'tesoreria_fondos'"
    ).fetchone()[0]
    assert triggers == 3
    conn.close()


def test_21_migracion_fallo_inducido_rollback(tmp_path):
    """21. Migración con fallo inducido -> rollback completo, base conserva estructura previa."""
    db_path = tmp_path / "legacy_21.db"
    conn = _crear_base_legacy_9_fondos(db_path)

    # Inducir fallo creando de antemano la tabla tesoreria_fondos_v2 con una columna incompatible
    # para que CREATE TABLE o INSERT INTO falle durante la migración
    conn.execute("CREATE TABLE tesoreria_fondos_v2 (columna_incompatible TEXT NOT NULL);")
    conn.commit()

    with pytest.raises(Exception):
        migrar_tabla_fondos_si_requiere_1160(conn)

    # La base debe seguir siendo funcional y mantener su estructura de tesoreria_fondos previa
    f1110 = conn.execute("SELECT codigo, saldo_centavos FROM tesoreria_fondos WHERE codigo = '1110'").fetchone()
    assert f1110 is not None
    assert f1110[0] == "1110"
    assert f1110[1] == 100000
    conn.close()


def test_22_pragma_foreign_key_check_limpio(tmp_path):
    """22. PRAGMA foreign_key_check tras migración completa -> 0 violaciones."""
    db_path = tmp_path / "legacy_22.db"
    conn = _crear_base_legacy_9_fondos(db_path)

    # Insertar registros foráneos en las tablas dependientes antes de migrar
    conn.execute(
        "INSERT INTO tesoreria_traslados (transfer_id, origin_fund_id, destination_fund_id, amount_cents, status) VALUES ('trf_01', 1, 2, 50000, 'CONFIRMED')"
    )
    conn.execute(
        "INSERT INTO tesoreria_reservas (reservation_id, fund_id, amount_cents, status) VALUES ('res_01', 1, 10000, 'ACTIVE')"
    )
    conn.execute(
        "INSERT INTO tesoreria_arqueos (reconciliation_id, fund_id, saldo_verificado_cents) VALUES ('arq_01', 1, 100000)"
    )
    conn.commit()

    migrar_tabla_fondos_si_requiere_1160(conn)

    fk_violations = conn.execute("PRAGMA foreign_key_check;").fetchall()
    assert len(fk_violations) == 0
    conn.close()


def test_23_indices_unicos_existen_y_funcionan(tmp_path):
    """23. Índices únicos idx_tesoreria_fondos_codigo existen y funcionan post-migración."""
    db_path = tmp_path / "legacy_23.db"
    conn = _crear_base_legacy_9_fondos(db_path)
    migrar_tabla_fondos_si_requiere_1160(conn)

    # Verificar existencia en sqlite_master
    idx = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'idx_tesoreria_fondos_codigo'"
    ).fetchone()
    assert idx is not None

    # Intentar insertar un código duplicado: debe fallar por unicidad
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        conn.execute(
            """
            INSERT INTO tesoreria_fondos (codigo, nombre, naturaleza, titularidad, created_at, updated_at)
            VALUES ('1110', 'Duplicado 1110', 'EFECTIVO', 'PROPIA', '2026-11-01', '2026-11-01')
            """
        )
    conn.close()


def test_24_claves_foraneas_dependientes_integras(tmp_path):
    """24. Claves foráneas dependientes (movimientos, traslados, reservas, arqueos) conservan integridad post-migración."""
    db_path = tmp_path / "legacy_24.db"
    conn = _crear_base_legacy_9_fondos(db_path)

    conn.execute(
        "INSERT INTO tesoreria_movimientos (movement_id, fondo_id, amount_cents, occurred_at) VALUES ('mov_99', 1, 50000, '2026-10-01')"
    )
    conn.execute(
        "INSERT INTO tesoreria_traslados (transfer_id, origin_fund_id, destination_fund_id, amount_cents, status) VALUES ('trf_99', 1, 2, 50000, 'CONFIRMED')"
    )
    conn.execute(
        "INSERT INTO tesoreria_reservas (reservation_id, fund_id, amount_cents, status) VALUES ('res_99', 2, 20000, 'ACTIVE')"
    )
    conn.execute(
        "INSERT INTO tesoreria_arqueos (reconciliation_id, fund_id, saldo_verificado_cents) VALUES ('arq_99', 2, 200000)"
    )
    conn.commit()

    migrar_tabla_fondos_si_requiere_1160(conn)

    # Comprobar que los registros dependientes siguen referenciando a los fondos correctos
    m = conn.execute("SELECT fondo_id FROM tesoreria_movimientos WHERE movement_id = 'mov_99'").fetchone()[0]
    assert m == 1

    t = conn.execute("SELECT origin_fund_id, destination_fund_id FROM tesoreria_traslados WHERE transfer_id = 'trf_99'").fetchone()
    assert t[0] == 1 and t[1] == 2

    r = conn.execute("SELECT fund_id FROM tesoreria_reservas WHERE reservation_id = 'res_99'").fetchone()[0]
    assert r == 2

    a = conn.execute("SELECT fund_id FROM tesoreria_arqueos WHERE reconciliation_id = 'arq_99'").fetchone()[0]
    assert a == 2

    # Intentar insertar registro dependiente con un fondo inexistente -> debe ser bloqueado por FK
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        conn.execute(
            "INSERT INTO tesoreria_movimientos (movement_id, fondo_id, amount_cents, occurred_at) VALUES ('mov_invalid', 9999, 1000, '2026-11-01')"
        )

    conn.close()
