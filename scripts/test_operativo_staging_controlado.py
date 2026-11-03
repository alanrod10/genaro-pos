"""Validación Operativa Controlada en STAGING — FASES C, D, E.

Ejecuta las pruebas operativas autorizadas sobre las bases persistentes de Staging:
1. PREPAY Efectivo (Op 1 con cargo de $350, Op 2 sin cargo, rechazo por saldo insuficiente).
2. PREPAY Transferencia (Acreditación en 1210, consumo en 1220, compensación 1120->1130, adicional en 1210).
3. Gobernanza, arqueo y cierre 1160 (vaciado a 1130, estado EN_CIERRE, arqueo físico obligatorio).
4. Pruebas de integración, trazabilidad y ausencia de duplicaciones.
"""

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from db.services import registrar_carga_idempotente_turso
from tesoreria.catalog import obtener_fondo_por_codigo
from tesoreria.closures import (
    ErrorCierre,
    beginClosure,
    confirmClosure,
    executePrepayDrawerSweep,
    validateClosure,
)
from tesoreria.commercial_bridge_worker import process_pending_commercial_events
from tesoreria.jornadas import abrir_jornada, cerrar_jornada, obtener_jornada_actual
from tesoreria.movements import registerEntry
from tesoreria.prepay_coordinator import solicitar_pre_reserva_prepay
from tesoreria.reconciliations import reconcilePhysicalFund
from tesoreria.reservations import ErrorReserva, createReservation
from tesoreria.transfers import ErrorTraslado, transferFunds


def ejecutar_pruebas_operativas_staging():
    print("=" * 80)
    print("EJECUCIÓN DE PRUEBAS OPERATIVAS CONTROLADAS EN STAGING PERSISTENTE")
    print("=" * 80)

    comm_path = ROOT_DIR / "staging" / "genaro_pos_staging.db"
    tes_path = ROOT_DIR / "staging" / "tesoreria_staging.db"

    comm_conn = sqlite3.connect(str(comm_path))
    comm_conn.row_factory = sqlite3.Row
    tes_conn = sqlite3.connect(str(tes_path))
    tes_conn.row_factory = sqlite3.Row

    # 1. Asegurar jornada de prueba abierta
    jornada = obtener_jornada_actual(tes_conn)
    if not jornada:
        abrir_jornada(tes_conn, usuario="alan", observaciones="Jornada de prueba operativa controlada Staging")
        jornada = obtener_jornada_actual(tes_conn)
    print(f"[OK] Jornada #{jornada['numero_jornada']} activa (estado: {jornada['estado']}).")

    # Registro de saldos iniciales
    def get_balances():
        rows = tes_conn.execute("SELECT codigo, saldo_centavos FROM tesoreria_fondos").fetchall()
        return {r["codigo"]: r["saldo_centavos"] for r in rows}

    s_ini = get_balances()
    print("Saldos iniciales:")
    for k in sorted(s_ini.keys()):
        print(f"  Fondo {k}: ${s_ini[k]/100:.2f}")

    # =========================================================================
    # 5.1 PREPAY EN EFECTIVO
    # =========================================================================
    print("\n--- 5.1 PREPAY EN EFECTIVO ---")
    # Operación 1: $500 capital + $50 adicional en efectivo
    idemp_efvo1 = "staging_test_prepay_efvo_01"
    res1, ya_existia1, t1 = registrar_carga_idempotente_turso(
        registro={
            "FECHA": datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M:%S"),
            "SERVICIO": "Personal",
            "PLATAFORMA_ORIGEN": "PREPAY",
            "SERVICIO_DESTINO": "PERSONAL",
            "MONTO_CARGA": 500,
            "MONTO_ADICIONAL": 50,
            "TOTAL_COBRADO": 550,
            "PAGO_EFVO": 550,
            "PAGO_TRANSF": 0,
        },
        idempotency_key=idemp_efvo1,
        actor="cajero1",
        conn=comm_conn,
        retornar_registro=True,
        tesoreria_conn=tes_conn,
    )
    print(f"[OK] Venta comercial PREPAY #1 registrada. Ticket #{res1}")
    stats1 = process_pending_commercial_events(comm_conn, tes_conn)
    print(f"[OK] Eventos procesados por worker: {stats1['published']}")

    s_post_op1 = get_balances()
    # 1160 (Caja Prepay Diario): +$500
    assert s_post_op1["1160"] == s_ini["1160"] + 500_00, f"Error en 1160: {s_post_op1['1160']}"
    # 1140 (Adicionales Efectivo): +$50
    assert s_post_op1["1140"] == s_ini["1140"] + 50_00, f"Error en 1140: {s_post_op1['1140']}"
    # 1220 (Prepay Digital): -$500 capital - $350 cargo diario = -$850
    assert s_post_op1["1220"] == s_ini["1220"] - 850_00, f"Error en 1220: {s_post_op1['1220']}"
    print(f"[OK] Op 1 verificada: 1160=+${500}, 1140=+${50}, 1220=-${850} ($500 capital + $350 cargo diario).")

    # Operación 2: $300 capital + $30 adicional en efectivo (misma jornada)
    idemp_efvo2 = "staging_test_prepay_efvo_02"
    res2, ya_existia2, t2 = registrar_carga_idempotente_turso(
        registro={
            "FECHA": datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M:%S"),
            "SERVICIO": "Personal",
            "PLATAFORMA_ORIGEN": "PREPAY",
            "SERVICIO_DESTINO": "PERSONAL",
            "MONTO_CARGA": 300,
            "MONTO_ADICIONAL": 30,
            "TOTAL_COBRADO": 330,
            "PAGO_EFVO": 330,
            "PAGO_TRANSF": 0,
        },
        idempotency_key=idemp_efvo2,
        actor="cajero1",
        conn=comm_conn,
        retornar_registro=True,
        tesoreria_conn=tes_conn,
    )
    process_pending_commercial_events(comm_conn, tes_conn)

    s_post_op2 = get_balances()
    # 1160: +$300 (total acumulado +$800)
    assert s_post_op2["1160"] == s_post_op1["1160"] + 300_00
    # 1140: +$30
    assert s_post_op2["1140"] == s_post_op1["1140"] + 30_00
    # 1220: -$300 (SIN cargo de $350)
    assert s_post_op2["1220"] == s_post_op1["1220"] - 300_00
    print(f"[OK] Op 2 verificada: 1160=+${300}, 1140=+${30}, 1220=-${300}. Cargo diario NO fue repetido.")

    # Rechazo por saldo insuficiente en 1220
    try:
        solicitar_pre_reserva_prepay(
            conn=tes_conn,
            servicio_destino="MOVISTAR",
            monto_carga_cents=9999999_00,
            monto_adicional_cents=0,
            pago_efvo_cents=9999999_00,
            pago_transf_cents=0,
            idempotency_key="staging_test_insufficient",
            actor="cajero1",
        )
        assert False, "Debió rechazar por saldo insuficiente"
    except ErrorReserva as e:
        assert "insuficiente" in str(e).lower()
        print(f"[OK] Rechazo por saldo insuficiente verificado: {e}")

    # =========================================================================
    # 5.2 PREPAY POR TRANSFERENCIA
    # =========================================================================
    print("\n--- 5.2 PREPAY POR TRANSFERENCIA ---")
    idemp_transf1 = "staging_test_prepay_transf_01"
    res_trf, ya_trf, t_trf = registrar_carga_idempotente_turso(
        registro={
            "FECHA": datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M:%S"),
            "SERVICIO": "Movistar",
            "PLATAFORMA_ORIGEN": "PREPAY",
            "SERVICIO_DESTINO": "MOVISTAR",
            "MONTO_CARGA": 400,
            "MONTO_ADICIONAL": 40,
            "TOTAL_COBRADO": 440,
            "PAGO_EFVO": 0,
            "PAGO_TRANSF": 440,
        },
        idempotency_key=idemp_transf1,
        actor="cajero1",
        conn=comm_conn,
        retornar_registro=True,
        tesoreria_conn=tes_conn,
    )
    process_pending_commercial_events(comm_conn, tes_conn)

    s_post_trf = get_balances()
    # 1. 1210 (Mercado Pago Propio) recibe el total ($440)
    assert s_post_trf["1210"] == s_post_op2["1210"] + 440_00
    # 2. 1220 debita exclusivamente el capital ($400)
    assert s_post_trf["1220"] == s_post_op2["1220"] - 400_00
    # 3. 1120 transfiere exclusivamente el capital ($400) hacia 1130
    assert s_post_trf["1120"] == s_post_op2["1120"] - 400_00
    assert s_post_trf["1130"] == s_post_op2["1130"] + 400_00
    # 4. 1140 NO recibe efectivo ficticio
    assert s_post_trf["1140"] == s_post_op2["1140"]
    # 5. 1110 y 1310 permanecen intactos
    assert s_post_trf["1110"] == s_post_op2["1110"]
    assert s_post_trf["1310"] == s_post_op2["1310"]
    print(f"[OK] Transferencia verificada: 1210=+${440}, 1220=-${400}, 1120=-${400}, 1130=+${400}.")

    # Idempotencia de reintento
    res_dup, ya_dup, _ = registrar_carga_idempotente_turso(
        registro={
            "FECHA": datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M:%S"),
            "SERVICIO": "Movistar",
            "PLATAFORMA_ORIGEN": "PREPAY",
            "SERVICIO_DESTINO": "MOVISTAR",
            "MONTO_CARGA": 400,
            "MONTO_ADICIONAL": 40,
            "TOTAL_COBRADO": 440,
            "PAGO_EFVO": 0,
            "PAGO_TRANSF": 440,
        },
        idempotency_key=idemp_transf1,
        actor="cajero1",
        conn=comm_conn,
        retornar_registro=True,
        tesoreria_conn=tes_conn,
    )
    assert res_dup == res_trf
    assert ya_dup is True
    assert get_balances() == s_post_trf
    print("[OK] Reintento de transferencia idempotente confirmado: sin duplicación.")

    # =========================================================================
    # 5.4 CIERRE DEL FONDO 1160
    # =========================================================================
    print("\n--- 5.4 CIERRE Y VACIADO DEL FONDO 1160 ---")
    # En 1160 hay acumulados $800 de las 2 recargas en efectivo
    saldo_1160_acum = s_post_trf["1160"]
    assert saldo_1160_acum == 800_00
    print(f"Saldo acumulado en 1160 previo al cierre: ${saldo_1160_acum/100:.2f}")

    # 1. Intento de vaciado en estado ABIERTA -> DEBE RECHAZARSE
    try:
        executePrepayDrawerSweep(tes_conn, jornada["id"], "alan")
        assert False, "Debió rechazar vaciado con jornada en ABIERTA"
    except ErrorCierre as e:
        assert "solo puede ejecutarse cuando la jornada está en estado 'EN_CIERRE'" in str(e)
        print(f"[OK] Rechazo en estado ABIERTA verificado: {e}")

    # 2. Iniciar cierre de jornada
    beginClosure(tes_conn, jornada["id"], "alan")
    jornada_en_cierre = obtener_jornada_actual(tes_conn)
    assert jornada_en_cierre["estado"] == "EN_CIERRE"
    print(f"[OK] Jornada #{jornada['numero_jornada']} en estado EN_CIERRE.")

    # 3. Intento de vaciado sin arqueo físico -> DEBE RECHAZARSE
    try:
        executePrepayDrawerSweep(tes_conn, jornada["id"], "alan")
        assert False, "Debió rechazar vaciado sin arqueo previo"
    except ErrorCierre as e:
        assert "se requiere un arqueo físico registrado en la jornada activa" in str(e)
        print(f"[OK] Rechazo sin arqueo previo verificado: {e}")

    # 4. Registrar Arqueo Físico Conforme en 1160 ($800 contado vs $800 libro)
    reconcilePhysicalFund(
        conn=tes_conn,
        fund_code="1160",
        verified_amount_cents=800_00,
        user_id="alan",
        notes="Arqueo físico conforme gaveta PREPAY jornada de prueba Staging",
    )
    print("[OK] Arqueo físico conforme de $800 registrado.")

    # 5. Ejecutar vaciado obligatorio 1160 -> 1130
    s_1130_pre_sweep = get_balances()["1130"]
    res_sweep = executePrepayDrawerSweep(tes_conn, jornada["id"], "alan")
    assert res_sweep["swept"] is True
    assert res_sweep["amount_cents"] == 800_00
    assert res_sweep["caja_prepay_saldo_final_cents"] == 0

    s_final = get_balances()
    # 1160 queda en estrictamente $0,00
    assert s_final["1160"] == 0, f"1160 no quedó en 0: {s_final['1160']}"
    # 1130 recibió exactamente los $800
    assert s_final["1130"] == s_1130_pre_sweep + 800_00, f"1130 esperado {s_1130_pre_sweep + 800_00}, obtenido {s_final['1130']}"
    print(f"[OK] Vaciado ejecutado con éxito: 1160=$0, 1130=+${800}.")

    # 6. Reintento de vaciado -> Retorna ALREADY_EXECUTED
    reintento_sweep = executePrepayDrawerSweep(tes_conn, jornada["id"], "alan")
    assert reintento_sweep["status"] == "ALREADY_EXECUTED"
    assert reintento_sweep["amount_cents"] == 0
    assert get_balances() == s_final
    print("[OK] Reintento de vaciado retornó ALREADY_EXECUTED sin duplicar.")

    # 7. Validar y confirmar cierre de jornada definitivo
    v_cierre = validateClosure(tes_conn, jornada["id"])
    print(f"[OK] Validación de cierre: can_close={v_cierre['can_close']}, saldo 1160={v_cierre['saldo_1160_formatted']}")
    assert v_cierre["saldo_1160_cents"] == 0
    assert v_cierre["needs_prepay_sweep"] is False

    confirmClosure(
        conn=tes_conn,
        journal_id=jornada["id"],
        user_id="alan",
        observations="Cierre de jornada de prueba operativa completado con éxito",
        allow_nonzero_1110=True,
    )
    j_cerrada = obtener_jornada_actual(tes_conn)
    assert j_cerrada is None  # No hay jornada abierta
    print(f"[OK] Jornada #{jornada['numero_jornada']} CERRADA definitivamente con snapshot canónico.")

    # Verificación final de claves foráneas
    assert len(comm_conn.execute("PRAGMA foreign_key_check;").fetchall()) == 0
    assert len(tes_conn.execute("PRAGMA foreign_key_check;").fetchall()) == 0

    comm_conn.close()
    tes_conn.close()

    print("\n" + "=" * 80)
    print("PRUEBAS OPERATIVAS CONTROLADAS EN STAGING PERSISTENTE SUPERADAS AL 100%")
    print("=" * 80)


if __name__ == "__main__":
    ejecutar_pruebas_operativas_staging()
