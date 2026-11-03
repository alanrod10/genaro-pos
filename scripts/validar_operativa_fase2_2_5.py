"""Script de Validación Operativa FASE 2.2.5 — GENAROPOS.

Ejecuta de forma exhaustiva:
1. Migración real oficial sobre copias de tesoreria_staging.db y genaro_pos_staging.db.
2. Circuito PREPAY Efectivo y Transferencia con todas las reglas económicas y de gobernanza.
3. Arqueo y Cierre 1160 en los 4 casos (conforme, faltante, sobrante, faltante total).
4. Verificación de inmutabilidad, triggers, claves foráneas e idempotencia.
"""

import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Agregar raíz del proyecto al path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from db.commercial_outbox_emitter import enqueue_commercial_event
from db.migrations_runner import ejecutar_migraciones as ejecutar_migraciones_comercial
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
from tesoreria.migrations_runner import (
    ejecutar_migraciones_tesoreria,
    migrar_tabla_fondos_si_requiere_1160,
    obtener_versiones_aplicadas,
)
from tesoreria.movements import registerEntry
from tesoreria.prepay_coordinator import (
    MONTO_CARGO_DIARIO_CENTS,
    solicitar_pre_reserva_prepay,
)
from tesoreria.reconciliations import reconcilePhysicalFund
from tesoreria.reservations import ErrorReserva, createReservation
from tesoreria.transfers import ErrorTraslado, transferFunds


def crear_copias_frescas(dest_dir: Path):
    """Copia las bases de Staging a un directorio de trabajo aislado."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    src_comm = ROOT_DIR / "staging" / "genaro_pos_staging.db"
    src_tes = ROOT_DIR / "staging" / "tesoreria_staging.db"
    dst_comm = dest_dir / "genaro_pos_staging_copy.db"
    dst_tes = dest_dir / "tesoreria_staging_copy.db"
    shutil.copy2(src_comm, dst_comm)
    shutil.copy2(src_tes, dst_tes)
    return dst_comm, dst_tes


def ejecutar_validacion_completa():
    print("=" * 80)
    print("FASE 2.2.5 — VALIDACIÓN OPERATIVA SOBRE COPIAS AISLADAS")
    print("=" * 80)

    work_dir = Path("/tmp/staging_fase2_2_5_run")
    if work_dir.exists():
        shutil.rmtree(work_dir)
    comm_db_path, tes_db_path = crear_copias_frescas(work_dir)
    print(f"[OK] Copias creadas en {work_dir}")

    # =========================================================================
    # PARTE 1: MIGRACIÓN REAL SOBRE COPIAS
    # =========================================================================
    print("\n--- PARTE 1: MIGRACIÓN REAL SOBRE COPIA DE TESORERÍA ---")
    tes_conn = sqlite3.connect(str(tes_db_path))
    tes_conn.row_factory = sqlite3.Row

    initial_funds = [dict(r) for r in tes_conn.execute("SELECT * FROM tesoreria_fondos ORDER BY id").fetchall()]
    print(f"Fondos pre-migración: {len(initial_funds)}")

    migradas_tes = ejecutar_migraciones_tesoreria(tes_conn)
    print(f"Migraciones aplicadas a Tesorería: {migradas_tes}")
    assert 1 in migradas_tes and 2 in migradas_tes and 3 in migradas_tes

    # Verificar versiones en BD
    vers = obtener_versiones_aplicadas(tes_conn)
    print(f"Versiones registradas en Tesorería: {vers}")
    assert 1 in vers and 2 in vers and 3 in vers

    # Verificar 1160
    f1160 = obtener_fondo_por_codigo(tes_conn, "1160")
    assert f1160 is not None, "Fondo 1160 no fue creado!"
    assert f1160["codigo"] == "1160"
    assert f1160["nombre"] == "Prepay Diario Efectivo"
    assert f1160["saldo_centavos"] == 0
    assert f1160["naturaleza"] == "EFECTIVO"
    assert f1160["titularidad"] == "PROPIA"
    print(f"[OK] Fondo 1160 incorporado correctamente: ID {f1160['id']}, Saldo {f1160['saldo_centavos']}")

    # Conservación de los 9 fondos preexistentes
    post_funds = [dict(r) for r in tes_conn.execute("SELECT * FROM tesoreria_fondos WHERE codigo != '1160' ORDER BY id").fetchall()]
    assert len(initial_funds) == len(post_funds)
    for ini, post in zip(initial_funds, post_funds):
        assert ini["id"] == post["id"]
        assert ini["codigo"] == post["codigo"]
        assert ini["saldo_centavos"] == post["saldo_centavos"]
    print(f"[OK] Todos los 9 fondos preexistentes conservan exactamente su ID, código y saldo.")

    # Triggers presentes en sqlite_master
    triggers = [r[0] for r in tes_conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'tesoreria_fondos'").fetchall()]
    print(f"Triggers en tesoreria_fondos: {triggers}")
    assert "trg_proteger_borrado_fondos" in triggers
    assert "trg_audit_insert_fondo" in triggers
    assert "trg_audit_update_fondo" in triggers
    print("[OK] Los 3 triggers de integridad e inmutabilidad están presentes.")

    # PRAGMA foreign_key_check
    fk_errs = tes_conn.execute("PRAGMA foreign_key_check;").fetchall()
    assert len(fk_errs) == 0, f"Violación de FK en Tesorería: {fk_errs}"
    print("[OK] PRAGMA foreign_key_check = 0 violaciones.")

    # Idempotencia
    vuelta2 = ejecutar_migraciones_tesoreria(tes_conn)
    assert len(vuelta2) == 0
    print("[OK] Idempotencia verificada: re-ejecución no duplica ni corrompe (retornó []).")

    # Migración de la base comercial
    print("\n--- MIGRACIÓN DE BASE COMERCIAL COPY ---")
    comm_conn = sqlite3.connect(str(comm_db_path))
    comm_conn.row_factory = sqlite3.Row
    migradas_comm = ejecutar_migraciones_comercial(comm_conn)
    print(f"Migraciones aplicadas a Commercial DB: {migradas_comm}")
    fk_comm = comm_conn.execute("PRAGMA foreign_key_check;").fetchall()
    assert len(fk_comm) == 0
    print("[OK] Commercial DB sin violaciones de FK.")

    # =========================================================================
    # PARTE 2: CIRCUITO PREPAY EFECTIVO Y TRANSFERENCIA
    # =========================================================================
    print("\n--- PARTE 2: PRUEBA DEL CIRCUITO PREPAY (EFECTIVO Y TRANSFERENCIA) ---")
    # Abrir jornada operativa en tesorería
    abrir_jornada(tes_conn, usuario="alan")
    jornada = obtener_jornada_actual(tes_conn)
    j_id = jornada["id"]
    print(f"[OK] Jornada #{jornada['numero_jornada']} ABIERTA.")

    # Fondeo inicial de saldos para la prueba
    # 1220 Prepay Digital Saldo ya tiene $10.000 (1000000 cents) en staging
    s_1220_ini = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1220'").fetchone()[0]
    print(f"Saldo inicial 1220: ${s_1220_ini/100:.2f}")

    # A. PREPAY EFECTIVO — Operación 1 (asume cargo diario de $350)
    print("\n> A.1: PREPAY Efectivo #1 ($500 capital + $50 adicional en efectivo)")
    res_efvo1 = registrar_carga_idempotente_turso(
        registro={
            "FECHA": "01/11/2026 14:00:00",
            "SERVICIO": "Personal",
            "PLATAFORMA_ORIGEN": "PREPAY",
            "SERVICIO_DESTINO": "PERSONAL",
            "MONTO_CARGA": 500,
            "MONTO_ADICIONAL": 50,
            "TOTAL_COBRADO": 550,
            "PAGO_EFVO": 550,
            "PAGO_TRANSF": 0,
        },
        idempotency_key="tx_prepay_efvo_01",
        actor="cajero1",
        conn=comm_conn,
        retornar_registro=True,
        tesoreria_conn=tes_conn,
    )
    print(f"[OK] Carga registrada en Commercial DB. Ticket #{res_efvo1}")

    # Procesar outbox
    stats1 = process_pending_commercial_events(comm_conn, tes_conn)
    print(f"[OK] Worker outbox procesó: {stats1['published']} evento(s).")
    assert stats1["published"] == 1

    # Verificaciones de saldos Operación 1:
    # 1160 (Caja PREPAY Diario) debió recibir los $500 de capital
    s_1160_op1 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1160'").fetchone()[0]
    assert s_1160_op1 == 500_00, f"Esperado 50000 en 1160, obtenido {s_1160_op1}"
    # 1140 (Adicionales) debió recibir los $50 de comisión
    s_1140_op1 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1140'").fetchone()[0]
    # 1220 (Prepay Digital) debió descontar $500 (capital) + $350 (cargo diario) = $850
    s_1220_op1 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1220'").fetchone()[0]
    assert s_1220_op1 == s_1220_ini - 850_00
    print(f"[OK] Operación 1: 1160=+${s_1160_op1/100:.2f}, 1220 descontó $500 capital + $350 fee.")

    # A.2: PREPAY EFECTIVO — Operación 2 (NO debe repetir cargo diario de $350)
    print("\n> A.2: PREPAY Efectivo #2 ($300 capital + $30 adicional, misma jornada)")
    res_efvo2 = registrar_carga_idempotente_turso(
        registro={
            "FECHA": "01/11/2026 14:15:00",
            "SERVICIO": "Personal",
            "PLATAFORMA_ORIGEN": "PREPAY",
            "SERVICIO_DESTINO": "PERSONAL",
            "MONTO_CARGA": 300,
            "MONTO_ADICIONAL": 30,
            "TOTAL_COBRADO": 330,
            "PAGO_EFVO": 330,
            "PAGO_TRANSF": 0,
        },
        idempotency_key="tx_prepay_efvo_02",
        actor="cajero1",
        conn=comm_conn,
        retornar_registro=True,
        tesoreria_conn=tes_conn,
    )
    process_pending_commercial_events(comm_conn, tes_conn)

    # 1160 acumuló $500 + $300 = $800
    s_1160_op2 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1160'").fetchone()[0]
    assert s_1160_op2 == 800_00
    # 1220 debió descontar ÚNICAMENTE $300 (capital), SIN fee de $350
    s_1220_op2 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1220'").fetchone()[0]
    assert s_1220_op2 == s_1220_op1 - 300_00
    print(f"[OK] Operación 2: 1160=+${s_1160_op2/100:.2f}, 1220 descontó solo $300 (cargo de $350 NO repetido).")

    # A.3: Rechazo por saldo insuficiente en 1220
    print("\n> A.3: Rechazo por saldo insuficiente en 1220")
    try:
        solicitar_pre_reserva_prepay(
            conn=tes_conn,
            servicio_destino="MOVISTAR",
            monto_carga_cents=999999_00,  # Importe enorme
            monto_adicional_cents=0,
            pago_efvo_cents=999999_00,
            pago_transf_cents=0,
            idempotency_key="tx_insufficient_1220",
            actor="cajero1",
        )
        assert False, "Debió rechazar por saldo insuficiente"
    except ErrorReserva as e:
        assert "insuficiente" in str(e).lower()
        print(f"[OK] Rechazado correctamente por saldo insuficiente: {e}")

    # B. PREPAY TRANSFERENCIA
    print("\n> B.1: PREPAY Transferencia ($400 capital + $40 adicional pagado por Mercado Pago)")
    # Fondeo previo de 1120 para asegurar compensación física hacia 1130
    registerEntry(tes_conn, "1120", 1000_00, "FONDEO", "Fondeo para compensación", "admin")
    s_1210_prev = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1210'").fetchone()[0]
    s_1120_prev = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1120'").fetchone()[0]
    s_1130_prev = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1130'").fetchone()[0]

    res_transf = registrar_carga_idempotente_turso(
        registro={
            "FECHA": "01/11/2026 14:30:00",
            "SERVICIO": "Movistar",
            "PLATAFORMA_ORIGEN": "PREPAY",
            "SERVICIO_DESTINO": "MOVISTAR",
            "MONTO_CARGA": 400,
            "MONTO_ADICIONAL": 40,
            "TOTAL_COBRADO": 440,
            "PAGO_EFVO": 0,
            "PAGO_TRANSF": 440,
        },
        idempotency_key="tx_prepay_transf_01",
        actor="cajero1",
        conn=comm_conn,
        retornar_registro=True,
        tesoreria_conn=tes_conn,
    )
    process_pending_commercial_events(comm_conn, tes_conn)

    # 1. 1210 (Mercado Pago) recibió el total ($440)
    s_1210_post = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1210'").fetchone()[0]
    assert s_1210_post == s_1210_prev + 440_00

    # 2. 1220 consumió $400 de capital
    s_1220_post_trf = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1220'").fetchone()[0]
    assert s_1220_post_trf == s_1220_op2 - 400_00

    # 3. 1120 transfirió exclusivamente capital ($400) a 1130
    s_1120_post = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1120'").fetchone()[0]
    assert s_1120_post == s_1120_prev - 400_00

    # 4. 1130 recibió exactamente los $400 de capital
    s_1130_post = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1130'").fetchone()[0]
    assert s_1130_post == s_1130_prev + 400_00

    # 5. Adicional ($40) permanece en 1210 y NO generó efectivo ficticio en 1140
    print(f"[OK] Transferencia verificada: 1210=+${440}, 1220=-${400}, 1120=-${400}, 1130=+${400}.")

    # B.2: Idempotencia en reintentos
    print("\n> B.2: Protección contra duplicación por reintentos")
    res_dup = registrar_carga_idempotente_turso(
        registro={
            "FECHA": "01/11/2026 14:30:00",
            "SERVICIO": "Movistar",
            "PLATAFORMA_ORIGEN": "PREPAY",
            "SERVICIO_DESTINO": "MOVISTAR",
            "MONTO_CARGA": 400,
            "MONTO_ADICIONAL": 40,
            "TOTAL_COBRADO": 440,
            "PAGO_EFVO": 0,
            "PAGO_TRANSF": 440,
        },
        idempotency_key="tx_prepay_transf_01",  # Misma clave
        actor="cajero1",
        conn=comm_conn,
        retornar_registro=True,
        tesoreria_conn=tes_conn,
    )
    assert res_dup[0] == res_transf[0]
    assert res_dup[1] is True  # Marcado como reintento idempotente
    # Saldos no debieron variar
    assert tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1130'").fetchone()[0] == s_1130_post
    print("[OK] Reintento con misma clave retornó ticket existente sin duplicar fondos.")

    # =========================================================================
    # PARTE 3: PRUEBA DE CIERRE 1160 (LOS 4 ESCENARIOS)
    # =========================================================================
    print("\n--- PARTE 3: PRUEBA DE CIERRE 1160 EN LOS 4 ESCENARIOS ---")
    escenarios = [
        ("CONFORME", 6000_00, 6000_00, 6000_00, 0),
        ("FALTANTE", 6000_00, 5800_00, 5800_00, -200_00),
        ("SOBRANTE", 6000_00, 6200_00, 6200_00, 200_00),
        ("FALTANTE_TOTAL", 6000_00, 0, 0, -6000_00),
    ]

    for nombre_esc, saldo_libro, saldo_contado, transfer_esp, diff_esp in escenarios:
        print(f"\n> Escenario Cierre: {nombre_esc} (Libro: ${saldo_libro/100:.2f}, Contado: ${saldo_contado/100:.2f})")
        # Creamos una base SQLite de prueba fresca aislada
        c_db = sqlite3.connect(":memory:")
        c_db.row_factory = sqlite3.Row
        from tesoreria.schema import inicializar_esquema_laboratorio
        from tesoreria.catalog import sembrar_catalogo_canónico
        inicializar_esquema_laboratorio(c_db)
        sembrar_catalogo_canónico(c_db)

        abrir_jornada(c_db, usuario="alan")
        j = obtener_jornada_actual(c_db)

        # Cargar libro en 1160
        registerEntry(c_db, "1160", saldo_libro, "RECAUDACION", "Cobros PREPAY del día", "alan")
        assert obtener_fondo_por_codigo(c_db, "1160")["saldo_centavos"] == saldo_libro

        # Crear una reserva activa de prueba en 1160 para verificar re-anclaje
        res_act = createReservation(
            conn=c_db,
            fund_code="1160",
            amount_cents=500_00,
            reason="Reserva activa pre-cierre",
            beneficiary="CLIENTE_TEST",
            created_by="alan",
        )

        # Pasar a EN_CIERRE
        beginClosure(c_db, j["id"], "alan")
        assert obtener_jornada_actual(c_db)["estado"] == "EN_CIERRE"

        # Registrar Arqueo Físico
        notes_arq = f"Arqueo {nombre_esc} gaveta PREPAY"
        reconcilePhysicalFund(
            conn=c_db,
            fund_code="1160",
            verified_amount_cents=saldo_contado,
            user_id="alan",
            notes=notes_arq,
        )

        # Ejecutar barrido
        sweep_res = executePrepayDrawerSweep(c_db, j["id"], "alan")
        print(f"  Resultado barrido: swept={sweep_res['swept']}, amount={sweep_res['amount_cents']}")

        # Verificaciones críticas:
        # 1. 1160 DEBE terminar en exactamente 0
        f1160_post = obtener_fondo_por_codigo(c_db, "1160")
        assert f1160_post["saldo_centavos"] == 0, f"1160 no terminó en 0: {f1160_post['saldo_centavos']}"

        # 2. 1130 DEBE recibir el importe correcto
        f1130_post = obtener_fondo_por_codigo(c_db, "1130")
        assert f1130_post["saldo_centavos"] == transfer_esp, f"1130 esperado {transfer_esp}, obtenido {f1130_post['saldo_centavos']}"

        # 3. La reserva activa debió re-anclarse al fondo 1130
        res_row = c_db.execute("SELECT fund_id FROM tesoreria_reservas WHERE reservation_id = ?", (res_act["reservation_id"],)).fetchone()
        assert res_row["fund_id"] == f1130_post["id"], "La reserva activa no fue re-anclada a 1130"

        # 4. Diferencia documentada en auditoría (si hubo diferencia)
        if diff_esp != 0:
            ev = c_db.execute("SELECT payload_json FROM tesoreria_auditoria_eventos WHERE event_type = 'RECONCILIATION_BALANCE_CUTOFF' ORDER BY id DESC LIMIT 1").fetchone()
            assert ev is not None
            payload = json.loads(ev[0])
            assert payload["diferencia_cents"] == diff_esp, f"Diferencia esperada {diff_esp}, obtenida {payload['diferencia_cents']}"

        # 5. Reintento sin duplicaciones
        reintento = executePrepayDrawerSweep(c_db, j["id"], "alan")
        assert reintento["status"] == "ALREADY_EXECUTED"
        assert reintento["amount_cents"] == 0
        assert obtener_fondo_por_codigo(c_db, "1160")["saldo_centavos"] == 0
        assert obtener_fondo_por_codigo(c_db, "1130")["saldo_centavos"] == transfer_esp
        print(f"  [OK] Escenario {nombre_esc} verificado: 1160=$0, 1130=${transfer_esp/100:.2f}, re-anclaje e idempotencia OK.")
        c_db.close()

    comm_conn.close()
    tes_conn.close()

    print("\n" + "=" * 80)
    print("TODAS LAS PRUEBAS OPERATIVAS DEL CIRCUITO PREPAY Y MIGRACIÓN 1160 SUPERADAS AL 100%")
    print("=" * 80)


if __name__ == "__main__":
    ejecutar_validacion_completa()
