"""Pruebas Exhaustivas: Módulo de Cuenta Corriente PREPAY / David — GenaroPOS.

Valida los 8 Escenarios Obligatorios de Integridad y Reglas Económicas:
1. Escenario 1: Cuenta nueva ($0 obligación, $0 acreditado, $0 pagado, $0 histórico).
2. Escenario 2: Acreditación efectiva (1220 sube, obligación sube, 1130 intacto, idempotencia).
3. Escenario 3: Pago parcial efectivo (1130 baja, obligación baja, idempotencia).
4. Escenario 4: Saldo insuficiente en 1130 (rechazo atómico, cero mutación).
5. Escenario 5: Reconocimiento de deuda histórica (aumenta obligación, 1130/1220 intactos, confirmación explícita).
6. Escenario 6: Atomicidad y prevención estricta de sobregiro y duplicación.
7. Escenario 7: Historial cronológico, running balance derivado y filtro por fecha de corte.
8. Escenario 8: Integridad referencial (PRAGMA foreign_key_check) y endpoints HTTP de la API.
"""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.dependencies import get_current_user, get_tesoreria_connection
from api.auth import AuthenticatedUser
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.catalog import sembrar_catalogo_canónico, obtener_fondo_por_codigo
from tesoreria.jornadas import abrir_jornada
from tesoreria.movements import registerEntry, registerExit
from tesoreria.cuenta_corriente_david import (
    ErrorCuentaCorrienteDavid,
    ErrorSaldoInsuficienteDavid,
    ErrorValidacionDavid,
    obtener_estado_cuenta_david,
    registrar_acreditacion_david,
    registrar_pago_david,
    registrar_reconocimiento_deuda_historica,
)


@pytest.fixture
def isolated_db(tmp_path: Path):
    """Crea una base de datos aislada temporal de tesorería con jornada abierta y catálogo canónico."""
    db_file = tmp_path / "test_tesoreria_david.db"
    conn = sqlite3.connect(str(db_file), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)

    # Abrir jornada operativa para permitir movimientos reales de Tesorería
    abrir_jornada(conn, usuario="alan_socio", observaciones="Jornada de prueba David")

    yield conn
    conn.close()


def test_escenario_1_cuenta_nueva(isolated_db: sqlite3.Connection):
    """Escenario 1: Cuenta nueva inicia en $0 en todas las dimensiones."""
    estado = obtener_estado_cuenta_david(isolated_db)
    assert estado["obligacion_pendiente_cents"] == 0
    assert estado["total_acreditaciones_cents"] == 0
    assert estado["total_pagos_cents"] == 0
    assert estado["total_deuda_historica_cents"] == 0
    assert estado["cantidad_movimientos"] == 0
    assert estado["movimientos"] == []


def test_escenario_2_acreditacion_efectiva(isolated_db: sqlite3.Connection):
    """Escenario 2: Acreditación efectiva de $10.000 provista por David.
    
    - 1220 aumenta $10.000.
    - Obligación pendiente aumenta $10.000.
    - Fondo 1130 permanece intacto.
    - Movimiento real de Tesorería vinculado.
    - Reintento con misma clave de idempotencia no duplica.
    """
    f1220_inicial = obtener_fondo_por_codigo(isolated_db, "1220")["saldo_centavos"]
    f1130_inicial = obtener_fondo_por_codigo(isolated_db, "1130")["saldo_centavos"]

    monto = 10_000_00  # $10.000,00 ARS
    res = registrar_acreditacion_david(
        conn=isolated_db,
        amount_cents=monto,
        referencia="COMP-DAVID-001",
        actor="alan_socio",
        idempotency_key="david_acr_001",
        motivo="Fondeo mayorista saldo PREPAY",
        fecha_referencia="2026-11-02",
    )
    assert res["status"] == "SUCCESS"
    assert res["movement_id"] is not None
    assert res["fondo_afectado"] == "1220"

    # Verificar saldos de fondos
    f1220_despues = obtener_fondo_por_codigo(isolated_db, "1220")["saldo_centavos"]
    f1130_despues = obtener_fondo_por_codigo(isolated_db, "1130")["saldo_centavos"]
    assert f1220_despues == f1220_inicial + monto
    assert f1130_despues == f1130_inicial

    # Verificar estado de cuenta
    estado = obtener_estado_cuenta_david(isolated_db)
    assert estado["obligacion_pendiente_cents"] == monto
    assert estado["total_acreditaciones_cents"] == monto
    assert estado["total_pagos_cents"] == 0
    assert estado["total_deuda_historica_cents"] == 0
    assert estado["cantidad_movimientos"] == 1

    # Reintento idempotente con la misma clave
    res_reintento = registrar_acreditacion_david(
        conn=isolated_db,
        amount_cents=monto,
        referencia="COMP-DAVID-001",
        actor="alan_socio",
        idempotency_key="david_acr_001",
    )
    assert res_reintento["status"] == "ALREADY_PROCESSED"
    assert res_reintento["operation_id"] == res["operation_id"]

    # Verificar que no se duplicó el saldo ni el movimiento
    assert obtener_fondo_por_codigo(isolated_db, "1220")["saldo_centavos"] == f1220_despues
    assert obtener_estado_cuenta_david(isolated_db)["cantidad_movimientos"] == 1


def test_escenario_3_pago_parcial_efectivo(isolated_db: sqlite3.Connection):
    """Escenario 3: Con saldo en 1130, pago parcial de $3.000 reduce obligación a $7.000."""
    # 1. Acreditar $10.000 de saldo David
    registrar_acreditacion_david(
        conn=isolated_db,
        amount_cents=10_000_00,
        referencia="COMP-DAVID-001",
        actor="alan_socio",
        idempotency_key="david_acr_002",
    )

    # 2. Simular recaudación previa en 1130 por $5.000 (Prepay Efectivo)
    registerEntry(
        conn=isolated_db,
        fund_code="1130",
        amount_cents=5_000_00,
        counterpart="RECAUDACION_MOSTRADOR",
        reason="Recaudaciones en efectivo de recargas",
        created_by="cajero1",
        idempotency_key="seed_1130_001",
    )
    f1130_antes_pago = obtener_fondo_por_codigo(isolated_db, "1130")["saldo_centavos"]

    # 3. Pagar $3.000 a David
    monto_pago = 3_000_00
    res_pago = registrar_pago_david(
        conn=isolated_db,
        amount_cents=monto_pago,
        referencia="RECIBO-DAVID-PAG-01",
        actor="alan_socio",
        idempotency_key="david_pag_001",
        motivo="Pago parcial de saldo PREPAY entregado en mano",
        fecha_referencia="2026-11-02",
    )
    assert res_pago["status"] == "SUCCESS"
    assert res_pago["fondo_afectado"] == "1130"
    assert res_pago["movement_id"] is not None

    # 4. Comprobar que 1130 bajó $3.000
    f1130_despues_pago = obtener_fondo_por_codigo(isolated_db, "1130")["saldo_centavos"]
    assert f1130_despues_pago == f1130_antes_pago - monto_pago

    # 5. Comprobar obligación pendiente = $7.000 ($10.000 - $3.000)
    estado = obtener_estado_cuenta_david(isolated_db)
    assert estado["total_acreditaciones_cents"] == 10_000_00
    assert estado["total_pagos_cents"] == 3_000_00
    assert estado["obligacion_pendiente_cents"] == 7_000_00
    assert estado["cantidad_movimientos"] == 2

    # 6. Reintento idempotente del pago no duplica
    reintento_pago = registrar_pago_david(
        conn=isolated_db,
        amount_cents=monto_pago,
        referencia="RECIBO-DAVID-PAG-01",
        actor="alan_socio",
        idempotency_key="david_pag_001",
    )
    assert reintento_pago["status"] == "ALREADY_PROCESSED"
    assert obtener_fondo_por_codigo(isolated_db, "1130")["saldo_centavos"] == f1130_despues_pago
    assert obtener_estado_cuenta_david(isolated_db)["obligacion_pendiente_cents"] == 7_000_00


def test_escenario_4_saldo_insuficiente_1130_rechazo(isolated_db: sqlite3.Connection):
    """Escenario 4: Intento de pago con saldo insuficiente en 1130 se rechaza atómicamente."""
    # Acreditar $10.000 de David
    registrar_acreditacion_david(
        conn=isolated_db,
        amount_cents=10_000_00,
        referencia="COMP-DAVID-001",
        actor="alan_socio",
        idempotency_key="david_acr_003",
    )

    # 1130 tiene $0 disponible. Intentar pagar $1.000
    with pytest.raises(ErrorSaldoInsuficienteDavid, match="insuficiente en fondo 1130"):
        registrar_pago_david(
            conn=isolated_db,
            amount_cents=1_000_00,
            referencia="RECIBO-FAIL-01",
            actor="alan_socio",
            idempotency_key="david_pag_fail_001",
        )

    # Verificar que nada mutó
    assert obtener_fondo_por_codigo(isolated_db, "1130")["saldo_centavos"] == 0
    estado = obtener_estado_cuenta_david(isolated_db)
    assert estado["total_pagos_cents"] == 0
    assert estado["obligacion_pendiente_cents"] == 10_000_00
    assert estado["cantidad_movimientos"] == 1


def test_escenario_5_reconocimiento_deuda_historica(isolated_db: sqlite3.Connection):
    """Escenario 5: Reconocimiento de deuda histórica aumenta la obligación sin alterar 1130 ni 1220."""
    f1220_antes = obtener_fondo_por_codigo(isolated_db, "1220")["saldo_centavos"]
    f1130_antes = obtener_fondo_por_codigo(isolated_db, "1130")["saldo_centavos"]

    monto_historico = 20_000_00  # $20.000,00 ARS

    # Rechazo si falta confirmación explícita
    with pytest.raises(ErrorValidacionDavid, match="confirmación explícita"):
        registrar_reconocimiento_deuda_historica(
            conn=isolated_db,
            amount_cents=monto_historico,
            fecha_referencia="2026-10-15",
            motivo="Saldo pendiente acordado de corte anterior",
            actor="alan_socio",
            idempotency_key="david_his_001",
            confirmacion_explicita=False,
        )

    # Registro válido con confirmación explícita
    res = registrar_reconocimiento_deuda_historica(
        conn=isolated_db,
        amount_cents=monto_historico,
        fecha_referencia="2026-10-15",
        motivo="Saldo pendiente acordado de corte anterior",
        actor="alan_socio",
        idempotency_key="david_his_001",
        confirmacion_explicita=True,
        referencia_externa="ACTA-ACUERDO-DAVID-OCT2026",
    )
    assert res["status"] == "SUCCESS"
    assert res["movement_id"] is None
    assert res["fondo_afectado"] is None
    assert res["tipo_movimiento"] == "RECONOCIMIENTO_DEUDA_HISTORICA"

    # Verificar que ningún fondo fue alterado
    assert obtener_fondo_por_codigo(isolated_db, "1220")["saldo_centavos"] == f1220_antes
    assert obtener_fondo_por_codigo(isolated_db, "1130")["saldo_centavos"] == f1130_antes

    # Comprobar que la obligación aumentó a $20.000
    estado = obtener_estado_cuenta_david(isolated_db)
    assert estado["total_deuda_historica_cents"] == monto_historico
    assert estado["total_acreditaciones_cents"] == 0
    assert estado["total_pagos_cents"] == 0
    assert estado["obligacion_pendiente_cents"] == monto_historico


def test_escenario_6_repeticion_concurrencia_y_sobregiro(isolated_db: sqlite3.Connection):
    """Escenario 6: Validaciones de pago superior a obligación y prevención estricta de sobregiro."""
    # Acreditar $5.000
    registrar_acreditacion_david(
        conn=isolated_db,
        amount_cents=5_000_00,
        referencia="COMP-005",
        actor="alan_socio",
        idempotency_key="david_acr_006",
    )

    # Dotar fondo 1130 con $10.000 (más que la obligación)
    registerEntry(
        conn=isolated_db,
        fund_code="1130",
        amount_cents=10_000_00,
        counterpart="RECAUDACION_VENTAS",
        reason="Fondo 1130 con abundancia",
        created_by="alan_socio",
        idempotency_key="seed_1130_006",
    )

    # Intentar pagar $7.000 (supera los $5.000 de obligación pendiente)
    with pytest.raises(ErrorValidacionDavid, match="no puede superar la obligación pendiente"):
        registrar_pago_david(
            conn=isolated_db,
            amount_cents=7_000_00,
            referencia="RECIBO-EXCESO",
            actor="alan_socio",
            idempotency_key="david_pag_exceso_001",
        )

    # Saldo de 1130 debe permanecer intacto en $10.000
    assert obtener_fondo_por_codigo(isolated_db, "1130")["saldo_centavos"] == 10_000_00


def test_escenario_7_historial_cronologico_y_saldo_resultante(isolated_db: sqlite3.Connection):
    """Escenario 7: Secuencia heterogénea calcula running balance derivado y respeta fecha_corte."""
    # 1. Reconocimiento histórico: $20.000 al 2026-10-01
    registrar_reconocimiento_deuda_historica(
        conn=isolated_db,
        amount_cents=20_000_00,
        fecha_referencia="2026-10-01",
        motivo="Deuda histórica inicial",
        actor="alan_socio",
        idempotency_key="seq_his_01",
        confirmacion_explicita=True,
    )

    # 2. Acreditación: $10.000 al 2026-10-15
    registrar_acreditacion_david(
        conn=isolated_db,
        amount_cents=10_000_00,
        referencia="REF-OCT-15",
        actor="alan_socio",
        idempotency_key="seq_acr_01",
        fecha_referencia="2026-10-15",
    )

    # Fondeo para permitir pago en 1130
    registerEntry(
        conn=isolated_db,
        fund_code="1130",
        amount_cents=15_000_00,
        counterpart="RECAUDACION",
        reason="Efectivo en mostrador",
        created_by="alan_socio",
        idempotency_key="seed_seq_01",
    )

    # 3. Pago: $12.000 al 2026-10-20
    registrar_pago_david(
        conn=isolated_db,
        amount_cents=12_000_00,
        referencia="REC-OCT-20",
        actor="alan_socio",
        idempotency_key="seq_pag_01",
        fecha_referencia="2026-10-20",
    )

    # 4. Acreditación: $5.000 al 2026-11-01
    registrar_acreditacion_david(
        conn=isolated_db,
        amount_cents=5_000_00,
        referencia="REF-NOV-01",
        actor="alan_socio",
        idempotency_key="seq_acr_02",
        fecha_referencia="2026-11-01",
    )

    # Consultar estado general sin corte
    estado_total = obtener_estado_cuenta_david(isolated_db)
    # Obligación final: 20.000 + 10.000 - 12.000 + 5.000 = 23.000
    assert estado_total["obligacion_pendiente_cents"] == 23_000_00
    assert estado_total["total_deuda_historica_cents"] == 20_000_00
    assert estado_total["total_acreditaciones_cents"] == 15_000_00
    assert estado_total["total_pagos_cents"] == 12_000_00

    movs = estado_total["movimientos"]
    assert len(movs) == 4
    assert movs[0]["saldo_obligacion_resultante_cents"] == 20_000_00
    assert movs[1]["saldo_obligacion_resultante_cents"] == 30_000_00
    assert movs[2]["saldo_obligacion_resultante_cents"] == 18_000_00
    assert movs[3]["saldo_obligacion_resultante_cents"] == 23_000_00

    # Consultar con fecha de corte a 2026-10-18 (debe incluir mov 1 y 2 = $30.000)
    estado_corte = obtener_estado_cuenta_david(isolated_db, fecha_corte="2026-10-18")
    assert estado_corte["obligacion_pendiente_cents"] == 30_000_00
    assert len(estado_corte["movimientos"]) == 2


def test_escenario_8_integridad_referencial_y_api(isolated_db: sqlite3.Connection):
    """Escenario 8: Integridad referencial PRAGMA y endpoints de API con TestClient."""
    # 1. Chequeo de claves foráneas estricto
    fk_violations = isolated_db.execute("PRAGMA foreign_key_check;").fetchall()
    assert fk_violations == [], f"Violaciones de clave foránea detectadas: {fk_violations}"

    # 2. Configurar TestClient con dependencia inyectada a la base aislada
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        username="alan_socio", display_name="Alan Socio", role="socio"
    )

    # Obtener la ruta del archivo SQLite antes del hilo
    cursor = isolated_db.cursor()
    cursor.execute("PRAGMA database_list;")
    db_path = cursor.fetchone()[2]
    cursor.close()

    from api.routers import tesoreria
    original_get_db = tesoreria._get_lab_db
    def _override_db():
        c = sqlite3.connect(db_path, timeout=30.0)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON;")
        return c

    tesoreria._get_lab_db = _override_db

    try:
        client = TestClient(app)

        # A) Consultar estado inicial vía API
        resp_get = client.get("/api/tesoreria/cuenta-corriente/david")
        assert resp_get.status_code == 200
        data_get = resp_get.json()
        assert data_get["obligacion_pendiente_cents"] == 0

        # B) Acreditación sin Idempotency-Key debe retornar 400
        resp_fail_idem = client.post(
            "/api/tesoreria/cuenta-corriente/david/acreditacion",
            json={"monto_centavos": 500000, "referencia": "TEST-NO-IDEM"},
        )
        assert resp_fail_idem.status_code == 400

        # C) Acreditación válida vía API
        resp_acr = client.post(
            "/api/tesoreria/cuenta-corriente/david/acreditacion",
            headers={"Idempotency-Key": "api_acr_001"},
            json={"monto_centavos": 800000, "referencia": "LOTE-API-01", "motivo": "Acreditación API"},
        )
        assert resp_acr.status_code == 200
        data_acr = resp_acr.json()
        assert data_acr["status"] == "SUCCESS"
        assert data_acr["monto_cents"] == 800000

        # D) Pago con saldo insuficiente en 1130 debe retornar 422
        resp_pago_insuf = client.post(
            "/api/tesoreria/cuenta-corriente/david/pago",
            headers={"Idempotency-Key": "api_pag_001"},
            json={"monto_centavos": 200000, "referencia": "REC-API-01"},
        )
        assert resp_pago_insuf.status_code == 422

        # E) Reconocimiento histórico sin confirmación explícita retorna 400
        resp_rec_sin_conf = client.post(
            "/api/tesoreria/cuenta-corriente/david/reconocimiento-historico",
            headers={"Idempotency-Key": "api_his_001"},
            json={
                "monto_centavos": 1000000,
                "fecha_referencia": "2026-09-01",
                "motivo": "Falta confirmacion",
                "confirmacion_explicita": False,
            },
        )
        assert resp_rec_sin_conf.status_code == 400

        # F) Reconocimiento histórico válido vía API
        resp_rec_ok = client.post(
            "/api/tesoreria/cuenta-corriente/david/reconocimiento-historico",
            headers={"Idempotency-Key": "api_his_002"},
            json={
                "monto_centavos": 1000000,
                "fecha_referencia": "2026-09-01",
                "motivo": "Deuda histórica auditada",
                "confirmacion_explicita": True,
            },
        )
        assert resp_rec_ok.status_code == 200

        # G) Estado final consolidado: 800.000 + 1.000.000 = 1.800.000 centavos ($18.000,00)
        resp_final = client.get("/api/tesoreria/cuenta-corriente/david")
        assert resp_final.status_code == 200
        data_final = resp_final.json()
        assert data_final["obligacion_pendiente_cents"] == 1800000
        assert data_final["cantidad_movimientos"] == 2
    finally:
        tesoreria._get_lab_db = original_get_db
        app.dependency_overrides.clear()
