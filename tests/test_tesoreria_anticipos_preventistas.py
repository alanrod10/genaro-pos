"""Pruebas Exhaustivas: Módulo de Anticipos y Reposición de Preventistas — GenaroPOS.

Valida los 16 Escenarios Obligatorios de Integridad y Reglas Económicas:
1. Anticipo válido desde 1130.
2. Rechazo por saldo insuficiente en 1130.
3. Reposición completa desde 1120.
4. Reposición parcial desde 1120.
5. Reposición combinada: primero 1120 y luego 1110.
6. Reposición parcial y posterior finalización.
7. Rechazo si ambos fondos no tienen disponibilidad suficiente para el importe solicitado.
8. Rechazo de reposición superior al pendiente.
9. Reintento con la misma clave de idempotencia.
10. Dos solicitudes concurrentes de reposición sobre el mismo anticipo.
11. Dos anticipos concurrentes que compiten por el saldo disponible de 1130.
12. Rollback ante fallo entre débito y crédito.
13. Integridad de las referencias entre anticipo y movimientos (PRAGMA foreign_key_check).
14. Verificación de que la reposición no crea un segundo gasto en gastos_retiros.
15. Verificación de que ningún fondo queda negativo.
16. Verificación de que el pendiente se deriva correctamente de los movimientos vinculados.
"""

import concurrent.futures
from pathlib import Path
import sqlite3
import threading
import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.dependencies import get_current_user
from api.auth import AuthenticatedUser
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.catalog import sembrar_catalogo_canónico, obtener_fondo_por_codigo
from tesoreria.jornadas import abrir_jornada
from tesoreria.movements import registerEntry
from tesoreria.anticipos_preventistas import (
    AnticipoNoEncontrado,
    ErrorAnticipoPreventista,
    ErrorSaldoInsuficienteAnticipo,
    ErrorSaldoInsuficienteReposicion,
    ErrorValidacionAnticipo,
    crear_anticipo_preventista,
    listar_anticipos_preventistas,
    obtener_anticipo_preventista,
    obtener_historial_reposiciones,
    obtener_resumen_obligaciones_preventistas,
    reponer_anticipo_preventista,
)


@pytest.fixture
def isolated_db(tmp_path: Path):
    """Crea una base de datos aislada temporal de tesorería con jornada abierta y catálogo."""
    db_file = tmp_path / "test_tesoreria_anticipos.db"
    conn = sqlite3.connect(str(db_file), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="alan_socio", observaciones="Jornada de prueba Anticipos")
    yield conn
    conn.close()


def test_escenario_01_anticipo_valido_desde_1130(isolated_db: sqlite3.Connection):
    """1. Anticipo válido desde 1130 con importe real, proveedor y motivo."""
    # Dotar 1130 con $50.000,00
    registerEntry(isolated_db, "1130", 50_000_00, "RECAUDACION", "Recaudacion prepay", "cajero1", idempotency_key="seed_01")
    f1130_antes = obtener_fondo_por_codigo(isolated_db, "1130")["saldo_centavos"]

    monto = 15_000_00  # $15.000,00
    res = crear_anticipo_preventista(
        conn=isolated_db,
        proveedor="Distribuidora Norte SRL",
        monto_cents=monto,
        motivo="Pago de mercadería lácteos y quesos",
        actor="alan_socio",
        idempotency_key="ant_test_01",
        referencia_documento="REMITO-8841",
        pedido_id="PED-20261102-001",
    )

    assert res["status"] == "SUCCESS"
    assert res["proveedor"] == "Distribuidora Norte SRL"
    assert res["monto_original_cents"] == monto
    assert res["monto_repuesto_cents"] == 0
    assert res["monto_pendiente_cents"] == monto
    assert res["estado"] == "PENDIENTE_REPOSICION"
    assert res["salida_movement_id"] is not None

    # Verificar que 1130 bajó exactamente $15.000
    f1130_despues = obtener_fondo_por_codigo(isolated_db, "1130")["saldo_centavos"]
    assert f1130_despues == f1130_antes - monto


def test_escenario_02_rechazo_saldo_insuficiente_1130(isolated_db: sqlite3.Connection):
    """2. Rechazo atómico si 1130 no tiene saldo suficiente (cero sobregiro)."""
    # 1130 tiene $0 disponible. Intentar sacar $10.000
    with pytest.raises(ErrorSaldoInsuficienteAnticipo, match="insuficiente en fondo 1130"):
        crear_anticipo_preventista(
            conn=isolated_db,
            proveedor="Proveedor Gaseosas",
            monto_cents=10_000_00,
            motivo="Pago urgente repartidor",
            actor="alan_socio",
            idempotency_key="ant_fail_02",
        )

    # Verificar que el fondo sigue en 0 y no se crearon expedientes
    assert obtener_fondo_por_codigo(isolated_db, "1130")["saldo_centavos"] == 0
    assert len(listar_anticipos_preventistas(isolated_db)) == 0


def test_escenario_03_reposicion_completa_desde_1120(isolated_db: sqlite3.Connection):
    """3. Reposición completa desde 1120 (Drugstore Acumulado / Tesoro)."""
    # Dotar 1130 con $20.000 y 1120 con $50.000
    registerEntry(isolated_db, "1130", 20_000_00, "RECAUDACION", "Recaudacion prepay", "cajero1", idempotency_key="seed_03_1130")
    registerEntry(isolated_db, "1120", 50_000_00, "CAPITAL", "Fondo tesoro inicial", "alan_socio", idempotency_key="seed_03_1120")

    # Crear anticipo de $12.000 desde 1130
    ant = crear_anticipo_preventista(
        conn=isolated_db,
        proveedor="Arcor Distribuidor",
        monto_cents=12_000_00,
        motivo="Entrega golosinas",
        actor="alan_socio",
        idempotency_key="ant_test_03",
    )
    f1130_post_ant = obtener_fondo_por_codigo(isolated_db, "1130")["saldo_centavos"]
    f1120_antes_rep = obtener_fondo_por_codigo(isolated_db, "1120")["saldo_centavos"]

    # Reponer la totalidad ($12.000)
    rep = reponer_anticipo_preventista(
        conn=isolated_db,
        anticipo_id=ant["id"],
        actor="alan_socio",
        idempotency_key="rep_test_03",
    )

    assert rep["status"] == "SUCCESS"
    assert rep["monto_repuesto_cents"] == 12_000_00
    assert rep["monto_pendiente_cents"] == 0
    assert rep["estado"] == "TOTALMENTE_REPUESTO"
    assert len(rep["tramos_reposicion"]) == 1
    assert rep["tramos_reposicion"][0]["fondo_origen"] == "1120"
    assert rep["tramos_reposicion"][0]["monto_cents"] == 12_000_00

    # 1120 se redujo en $12.000 y 1130 recuperó los $12.000
    assert obtener_fondo_por_codigo(isolated_db, "1120")["saldo_centavos"] == f1120_antes_rep - 12_000_00
    assert obtener_fondo_por_codigo(isolated_db, "1130")["saldo_centavos"] == f1130_post_ant + 12_000_00


def test_escenario_04_reposicion_parcial_desde_1120(isolated_db: sqlite3.Connection):
    """4. Reposición parcial desde 1120 mantiene identificado el saldo pendiente."""
    registerEntry(isolated_db, "1130", 30_000_00, "RECAUDACION", "Fondeo 1130", "cajero1", idempotency_key="seed_04_1130")
    registerEntry(isolated_db, "1120", 40_000_00, "CAPITAL", "Fondeo 1120", "alan_socio", idempotency_key="seed_04_1120")

    ant = crear_anticipo_preventista(
        conn=isolated_db,
        proveedor="Panificadora Central",
        monto_cents=20_000_00,
        motivo="Facturas y medialunas",
        actor="alan_socio",
        idempotency_key="ant_test_04",
    )

    # Reponer parcialmente $8.000
    rep = reponer_anticipo_preventista(
        conn=isolated_db,
        anticipo_id=ant["id"],
        actor="alan_socio",
        idempotency_key="rep_test_04",
        monto_cents=8_000_00,
    )

    assert rep["status"] == "SUCCESS"
    assert rep["monto_repuesto_cents"] == 8_000_00
    assert rep["monto_pendiente_cents"] == 12_000_00
    assert rep["estado"] == "PARCIALMENTE_REPUESTO"


def test_escenario_05_reposicion_combinada_1120_y_1110(isolated_db: sqlite3.Connection):
    """5. Reposición combinada: agota primero 1120 y toma el remanente de 1110."""
    registerEntry(isolated_db, "1130", 50_000_00, "RECAUDACION", "Fondeo 1130", "cajero1", idempotency_key="seed_05_1130")
    # 1120 tiene solo $6.000 disponible, 1110 tiene $20.000 disponible
    registerEntry(isolated_db, "1120", 6_000_00, "CAPITAL", "Fondeo 1120", "alan_socio", idempotency_key="seed_05_1120")
    registerEntry(isolated_db, "1110", 20_000_00, "VENTAS", "Fondeo 1110", "cajero1", idempotency_key="seed_05_1110")

    ant = crear_anticipo_preventista(
        conn=isolated_db,
        proveedor="Bebidas del Valle",
        monto_cents=10_000_00,
        motivo="Cajones de gaseosa",
        actor="alan_socio",
        idempotency_key="ant_test_05",
    )

    # Reponer $10.000: debe tomar $6.000 de 1120 y $4.000 de 1110
    rep = reponer_anticipo_preventista(
        conn=isolated_db,
        anticipo_id=ant["id"],
        actor="alan_socio",
        idempotency_key="rep_test_05",
        monto_cents=10_000_00,
    )

    assert rep["status"] == "SUCCESS"
    assert rep["monto_repuesto_cents"] == 10_000_00
    assert rep["monto_pendiente_cents"] == 0
    assert rep["estado"] == "TOTALMENTE_REPUESTO"

    tramos = rep["tramos_reposicion"]
    assert len(tramos) == 2
    assert tramos[0]["fondo_origen"] == "1120"
    assert tramos[0]["monto_cents"] == 6_000_00
    assert tramos[1]["fondo_origen"] == "1110"
    assert tramos[1]["monto_cents"] == 4_000_00

    # 1120 quedó en 0, 1110 bajó a 16.000, 1130 recuperó los 10.000
    assert obtener_fondo_por_codigo(isolated_db, "1120")["saldo_centavos"] == 0
    assert obtener_fondo_por_codigo(isolated_db, "1110")["saldo_centavos"] == 16_000_00


def test_escenario_06_reposicion_parcial_y_posterior_finalizacion(isolated_db: sqlite3.Connection):
    """6. Reposición parcial inicial y posterior finalización completando el anticipo."""
    registerEntry(isolated_db, "1130", 30_000_00, "RECAUDACION", "Fondeo 1130", "cajero1", idempotency_key="seed_06_1130")
    registerEntry(isolated_db, "1120", 30_000_00, "CAPITAL", "Fondeo 1120", "alan_socio", idempotency_key="seed_06_1120")

    ant = crear_anticipo_preventista(
        conn=isolated_db,
        proveedor="Snacks Jujuy",
        monto_cents=15_000_00,
        motivo="Papas fritas y maníes",
        actor="alan_socio",
        idempotency_key="ant_test_06",
    )

    # 1er pago: $5.000
    rep1 = reponer_anticipo_preventista(
        conn=isolated_db,
        anticipo_id=ant["id"],
        actor="alan_socio",
        idempotency_key="rep_test_06_a",
        monto_cents=5_000_00,
    )
    assert rep1["estado"] == "PARCIALMENTE_REPUESTO"
    assert rep1["monto_pendiente_cents"] == 10_000_00

    # 2do pago: los $10.000 restantes
    rep2 = reponer_anticipo_preventista(
        conn=isolated_db,
        anticipo_id=ant["id"],
        actor="alan_socio",
        idempotency_key="rep_test_06_b",
    )
    assert rep2["estado"] == "TOTALMENTE_REPUESTO"
    assert rep2["monto_pendiente_cents"] == 0
    assert rep2["cantidad_reposiciones"] == 2


def test_escenario_07_rechazo_fondos_insuficientes_para_importe_solicitado(isolated_db: sqlite3.Connection):
    """7. Rechazo si ambos fondos (1120 + 1110) no tienen disponibilidad suficiente."""
    registerEntry(isolated_db, "1130", 20_000_00, "RECAUDACION", "Fondeo 1130", "cajero1", idempotency_key="seed_07_1130")
    # 1120 tiene $1.000, 1110 tiene $2.000 (total disponible = $3.000)
    registerEntry(isolated_db, "1120", 1_000_00, "CAPITAL", "Fondeo 1120", "alan_socio", idempotency_key="seed_07_1120")
    registerEntry(isolated_db, "1110", 2_000_00, "VENTAS", "Fondeo 1110", "cajero1", idempotency_key="seed_07_1110")

    ant = crear_anticipo_preventista(
        conn=isolated_db,
        proveedor="Distribuidora Cervezas",
        monto_cents=10_000_00,
        motivo="Entrega cajones",
        actor="alan_socio",
        idempotency_key="ant_test_07",
    )

    # Intentar reponer $5.000 (supera los $3.000 disponibles entre ambos fondos)
    with pytest.raises(ErrorSaldoInsuficienteReposicion, match="Disponibilidad combinada insuficiente"):
        reponer_anticipo_preventista(
            conn=isolated_db,
            anticipo_id=ant["id"],
            actor="alan_socio",
            idempotency_key="rep_test_07_fail",
            monto_cents=5_000_00,
        )

    # Verificar que los saldos quedaron intactos
    assert obtener_fondo_por_codigo(isolated_db, "1120")["saldo_centavos"] == 1_000_00
    assert obtener_fondo_por_codigo(isolated_db, "1110")["saldo_centavos"] == 2_000_00
    assert obtener_anticipo_preventista(isolated_db, ant["id"])["monto_repuesto_cents"] == 0


def test_escenario_08_rechazo_reposicion_superior_al_pendiente(isolated_db: sqlite3.Connection):
    """8. Rechazo si el importe solicitado supera el saldo pendiente del anticipo."""
    registerEntry(isolated_db, "1130", 20_000_00, "RECAUDACION", "Fondeo 1130", "cajero1", idempotency_key="seed_08_1130")
    registerEntry(isolated_db, "1120", 50_000_00, "CAPITAL", "Fondeo 1120", "alan_socio", idempotency_key="seed_08_1120")

    ant = crear_anticipo_preventista(
        conn=isolated_db,
        proveedor="Frigorífico Local",
        monto_cents=8_000_00,
        motivo="Carnes y embutidos",
        actor="alan_socio",
        idempotency_key="ant_test_08",
    )

    # Intentar reponer $10.000 cuando el anticipo fue por $8.000
    with pytest.raises(ErrorValidacionAnticipo, match="no puede superar el saldo pendiente"):
        reponer_anticipo_preventista(
            conn=isolated_db,
            anticipo_id=ant["id"],
            actor="alan_socio",
            idempotency_key="rep_test_08_exceso",
            monto_cents=10_000_00,
        )


def test_escenario_09_reintento_misma_clave_idempotencia(isolated_db: sqlite3.Connection):
    """9. Reintento con la misma clave de idempotencia retorna ALREADY_PROCESSED sin duplicar."""
    registerEntry(isolated_db, "1130", 30_000_00, "RECAUDACION", "Fondeo 1130", "cajero1", idempotency_key="seed_09_1130")
    registerEntry(isolated_db, "1120", 30_000_00, "CAPITAL", "Fondeo 1120", "alan_socio", idempotency_key="seed_09_1120")

    # Anticipo con clave fija
    ant1 = crear_anticipo_preventista(
        conn=isolated_db,
        proveedor="Lácteos Jujuy",
        monto_cents=10_000_00,
        motivo="Quesos cremosos",
        actor="alan_socio",
        idempotency_key="idem_ant_09",
    )
    ant2 = crear_anticipo_preventista(
        conn=isolated_db,
        proveedor="Lácteos Jujuy",
        monto_cents=10_000_00,
        motivo="Quesos cremosos",
        actor="alan_socio",
        idempotency_key="idem_ant_09",
    )

    assert ant1["status"] == "SUCCESS"
    assert ant2["status"] == "ALREADY_PROCESSED"
    assert ant1["id"] == ant2["id"]
    assert len(listar_anticipos_preventistas(isolated_db)) == 1

    # Reposición con clave fija
    rep1 = reponer_anticipo_preventista(
        conn=isolated_db,
        anticipo_id=ant1["id"],
        actor="alan_socio",
        idempotency_key="idem_rep_09",
        monto_cents=5_000_00,
    )
    rep2 = reponer_anticipo_preventista(
        conn=isolated_db,
        anticipo_id=ant1["id"],
        actor="alan_socio",
        idempotency_key="idem_rep_09",
        monto_cents=5_000_00,
    )

    assert rep1["status"] == "SUCCESS"
    assert rep2["status"] == "ALREADY_PROCESSED"
    assert rep1["monto_repuesto_cents"] == 5_000_00
    assert rep2["monto_repuesto_cents"] == 5_000_00
    assert len(obtener_historial_reposiciones(isolated_db, ant1["id"])) == 1


def test_escenario_10_concurrencia_reposiciones_mismo_anticipo(tmp_path: Path):
    """10. Dos solicitudes concurrentes de reposición sobre el mismo anticipo."""
    db_file = tmp_path / "conc_rep.db"
    conn = sqlite3.connect(str(db_file), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="alan_socio")

    registerEntry(conn, "1130", 50_000_00, "RECAUDACION", "Fondeo", "cajero", idempotency_key="s10_1130")
    registerEntry(conn, "1120", 50_000_00, "CAPITAL", "Fondeo", "cajero", idempotency_key="s10_1120")

    ant = crear_anticipo_preventista(
        conn=conn,
        proveedor="Bebidas del Norte",
        monto_cents=10_000_00,
        motivo="Cajones agua mineral",
        actor="alan_socio",
        idempotency_key="ant_s10",
    )
    conn.close()

    results = []
    errors = []

    def run_rep(key):
        c = sqlite3.connect(str(db_file), timeout=30.0)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON;")
        try:
            r = reponer_anticipo_preventista(
                conn=c,
                anticipo_id=ant["id"],
                actor="alan_socio",
                idempotency_key=key,
                monto_cents=8_000_00,
            )
            results.append(r)
        except Exception as e:
            errors.append(e)
        finally:
            c.close()

    t1 = threading.Thread(target=run_rep, args=("rep_conc_1",))
    t2 = threading.Thread(target=run_rep, args=("rep_conc_2",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Como el anticipo era de $10.000, 2 reposiciones de $8.000 suman $16.000 (> $10.000).
    # Como máximo 1 puede aprobarse, la otra debe fallar o por concurrencia o por superar pendiente.
    assert len(results) == 1
    assert len(errors) == 1

    conn = sqlite3.connect(str(db_file), timeout=30.0)
    conn.row_factory = sqlite3.Row
    ant_final = obtener_anticipo_preventista(conn, ant["id"])
    conn.close()
    assert ant_final["monto_repuesto_cents"] == 8_000_00
    assert ant_final["monto_pendiente_cents"] == 2_000_00


def test_escenario_11_concurrencia_anticipos_compitiendo_1130(tmp_path: Path):
    """11. Dos anticipos concurrentes que compiten por el saldo disponible de 1130."""
    db_file = tmp_path / "conc_ant.db"
    conn = sqlite3.connect(str(db_file), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="alan_socio")

    # 1130 tiene exactamente $500,00
    registerEntry(conn, "1130", 500_00, "RECAUDACION", "Fondeo", "cajero", idempotency_key="s11_1130")
    conn.close()

    results = []
    errors = []

    def run_ant(prov, key):
        c = sqlite3.connect(str(db_file), timeout=30.0)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON;")
        try:
            r = crear_anticipo_preventista(
                conn=c,
                proveedor=prov,
                monto_cents=350_00,  # $350 c/u, suma $700 > $500
                motivo="Pago urgente preventista",
                actor="alan_socio",
                idempotency_key=key,
            )
            results.append(r)
        except Exception as e:
            errors.append(e)
        finally:
            c.close()

    t1 = threading.Thread(target=run_ant, args=("Proveedor A", "ant_comp_1"))
    t2 = threading.Thread(target=run_ant, args=("Proveedor B", "ant_comp_2"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactamente 1 aprobado, 1 rechazado
    assert len(results) == 1
    assert len(errors) == 1

    conn = sqlite3.connect(str(db_file), timeout=30.0)
    conn.row_factory = sqlite3.Row
    f1130_saldo = obtener_fondo_por_codigo(conn, "1130")["saldo_centavos"]
    conn.close()
    assert f1130_saldo == 150_00  # $500 - $350 = $150 (cero sobregiro)


def test_escenario_12_rollback_ante_fallo(isolated_db: sqlite3.Connection, monkeypatch):
    """12. Rollback atómico ante fallo durante la reposición."""
    registerEntry(isolated_db, "1130", 20_000_00, "RECAUDACION", "Fondeo 1130", "cajero1", idempotency_key="s12_1130")
    registerEntry(isolated_db, "1120", 20_000_00, "CAPITAL", "Fondeo 1120", "alan_socio", idempotency_key="s12_1120")

    ant = crear_anticipo_preventista(
        conn=isolated_db,
        proveedor="Proveedor Rollback",
        monto_cents=10_000_00,
        motivo="Prueba de atomicidad",
        actor="alan_socio",
        idempotency_key="ant_s12",
    )
    f1120_antes = obtener_fondo_por_codigo(isolated_db, "1120")["saldo_centavos"]

    # Inyectar error en confirmTransferReceipt para forzar fallo entre débito y crédito
    import tesoreria.anticipos_preventistas as ap_mod
    def _failing_confirm(*args, **kwargs):
        raise RuntimeError("Fallo simulado en acreditación de destino")

    monkeypatch.setattr(ap_mod, "confirmTransferReceipt", _failing_confirm)

    with pytest.raises(RuntimeError, match="Fallo simulado"):
        reponer_anticipo_preventista(
            conn=isolated_db,
            anticipo_id=ant["id"],
            actor="alan_socio",
            idempotency_key="rep_s12_fail",
            monto_cents=5_000_00,
        )

    # Verificar que el rollback restauró el saldo de 1120 y el anticipo sigue sin reposiciones
    assert obtener_fondo_por_codigo(isolated_db, "1120")["saldo_centavos"] == f1120_antes
    assert len(obtener_historial_reposiciones(isolated_db, ant["id"])) == 0


def test_escenario_13_integridad_referencial_fk(isolated_db: sqlite3.Connection):
    """13. Integridad referencial PRAGMA foreign_key_check."""
    registerEntry(isolated_db, "1130", 20_000_00, "RECAUDACION", "Fondeo 1130", "cajero1", idempotency_key="s13_1130")
    registerEntry(isolated_db, "1120", 20_000_00, "CAPITAL", "Fondeo 1120", "alan_socio", idempotency_key="s13_1120")

    ant = crear_anticipo_preventista(
        conn=isolated_db,
        proveedor="Proveedor FK Check",
        monto_cents=5_000_00,
        motivo="Prueba FK",
        actor="alan_socio",
        idempotency_key="ant_s13",
    )
    reponer_anticipo_preventista(
        conn=isolated_db,
        anticipo_id=ant["id"],
        actor="alan_socio",
        idempotency_key="rep_s13",
    )

    fk_violations = isolated_db.execute("PRAGMA foreign_key_check;").fetchall()
    assert fk_violations == [], f"Violaciones de clave foránea detectadas: {fk_violations}"


def test_escenario_14_no_crea_segundo_gasto_en_gastos_retiros(isolated_db: sqlite3.Connection):
    """14. Verificación de que la reposición NO crea registros en gastos_retiros ni duplica gastos."""
    # Verificar si la tabla gastos_retiros existe; si no, crearla en la base aislada para comprobar
    isolated_db.execute(
        """
        CREATE TABLE IF NOT EXISTS gastos_retiros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detalle TEXT,
            monto_salida INTEGER
        );
        """
    )
    isolated_db.commit()

    registerEntry(isolated_db, "1130", 30_000_00, "RECAUDACION", "Fondeo 1130", "cajero1", idempotency_key="s14_1130")
    registerEntry(isolated_db, "1120", 30_000_00, "CAPITAL", "Fondeo 1120", "alan_socio", idempotency_key="s14_1120")

    ant = crear_anticipo_preventista(
        conn=isolated_db,
        proveedor="Proveedor Sin Gasto Duplicado",
        monto_cents=10_000_00,
        motivo="Compra mercadería",
        actor="alan_socio",
        idempotency_key="ant_s14",
    )
    reponer_anticipo_preventista(
        conn=isolated_db,
        anticipo_id=ant["id"],
        actor="alan_socio",
        idempotency_key="rep_s14",
    )

    cur = isolated_db.cursor()
    cur.execute("SELECT COUNT(*) FROM gastos_retiros;")
    count_gastos = cur.fetchone()[0]
    assert count_gastos == 0, f"No deben existir registros en gastos_retiros, pero se encontraron {count_gastos}"


def test_escenario_15_ningun_fondo_queda_negativo(isolated_db: sqlite3.Connection):
    """15. Verificación de que ningún fondo físico queda con saldo negativo."""
    registerEntry(isolated_db, "1130", 10_000_00, "RECAUDACION", "Fondeo", "cajero1", idempotency_key="s15_1130")
    registerEntry(isolated_db, "1120", 5_000_00, "CAPITAL", "Fondeo", "alan_socio", idempotency_key="s15_1120")
    registerEntry(isolated_db, "1110", 5_000_00, "VENTAS", "Fondeo", "cajero1", idempotency_key="s15_1110")

    ant = crear_anticipo_preventista(
        conn=isolated_db,
        proveedor="Proveedor Limite",
        monto_cents=10_000_00,
        motivo="Pago exacto de saldo",
        actor="alan_socio",
        idempotency_key="ant_s15",
    )
    # 1130 quedó en 0, no negativo
    assert obtener_fondo_por_codigo(isolated_db, "1130")["saldo_centavos"] == 0

    reponer_anticipo_preventista(
        conn=isolated_db,
        anticipo_id=ant["id"],
        actor="alan_socio",
        idempotency_key="rep_s15",
    )
    # 1120 quedó en 0, 1110 quedó en 0, 1130 recuperó 10.000. Ninguno negativo.
    assert obtener_fondo_por_codigo(isolated_db, "1120")["saldo_centavos"] == 0
    assert obtener_fondo_por_codigo(isolated_db, "1110")["saldo_centavos"] == 0
    assert obtener_fondo_por_codigo(isolated_db, "1130")["saldo_centavos"] == 10_000_00


def test_escenario_16_pendiente_derivado_correctamente(isolated_db: sqlite3.Connection):
    """16. Verificación de que el pendiente se deriva estrictamente de los movimientos reales vinculados."""
    registerEntry(isolated_db, "1130", 50_000_00, "RECAUDACION", "Fondeo", "cajero1", idempotency_key="s16_1130")
    registerEntry(isolated_db, "1120", 50_000_00, "CAPITAL", "Fondeo", "alan_socio", idempotency_key="s16_1120")

    ant = crear_anticipo_preventista(
        conn=isolated_db,
        proveedor="Proveedor Múltiples Tramos",
        monto_cents=25_000_00,
        motivo="Entrega semanal",
        actor="alan_socio",
        idempotency_key="ant_s16",
    )

    reponer_anticipo_preventista(conn=isolated_db, anticipo_id=ant["id"], actor="alan", idempotency_key="r16_1", monto_cents=5_000_00)
    reponer_anticipo_preventista(conn=isolated_db, anticipo_id=ant["id"], actor="alan", idempotency_key="r16_2", monto_cents=7_500_00)
    reponer_anticipo_preventista(conn=isolated_db, anticipo_id=ant["id"], actor="alan", idempotency_key="r16_3", monto_cents=2_500_00)

    # Pendiente esperado: 25.000 - (5.000 + 7.500 + 2.500) = 10.000
    detalle = obtener_anticipo_preventista(isolated_db, ant["id"])
    assert detalle["monto_repuesto_cents"] == 15_000_00
    assert detalle["monto_pendiente_cents"] == 10_000_00
    assert detalle["estado"] == "PARCIALMENTE_REPUESTO"
    assert detalle["cantidad_reposiciones"] == 3

    # Resumen general
    resumen = obtener_resumen_obligaciones_preventistas(isolated_db)
    assert resumen["total_anticipado_cents"] == 25_000_00
    assert resumen["total_repuesto_cents"] == 15_000_00
    assert resumen["total_pendiente_reposicion_cents"] == 10_000_00


def test_escenario_17_api_endpoints_preventistas(isolated_db: sqlite3.Connection):
    """17. Verificación completa de contratos HTTP vía FastAPI TestClient."""
    cursor = isolated_db.cursor()
    cursor.execute("PRAGMA database_list;")
    db_path = cursor.fetchone()[2]
    cursor.close()

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        username="alan_socio", display_name="Alan Socio", role="socio"
    )

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

        # Fondeo para permitir operaciones
        registerEntry(isolated_db, "1130", 40_000_00, "RECAUDACION", "Fondeo 1130", "cajero1", idempotency_key="api_seed_1130")
        registerEntry(isolated_db, "1120", 40_000_00, "CAPITAL", "Fondeo 1120", "alan_socio", idempotency_key="api_seed_1120")

        # 1. POST anticipo sin Idempotency-Key -> 400
        r_fail = client.post("/api/tesoreria/preventistas/anticipos", json={"proveedor": "Coca Cola", "monto_centavos": 1000000, "motivo": "Test"})
        assert r_fail.status_code == 400

        # 2. POST anticipo válido -> 200
        r_ant = client.post(
            "/api/tesoreria/preventistas/anticipos",
            headers={"Idempotency-Key": "api_ant_01"},
            json={"proveedor": "Coca Cola", "monto_centavos": 10_000_00, "motivo": "Bebidas fiesta", "referencia_documento": "REM-991"},
        )
        assert r_ant.status_code == 200
        ant_data = r_ant.json()
        assert ant_data["status"] == "SUCCESS"
        ant_id = ant_data["id"]

        # 3. GET detalle anticipo -> 200
        r_det = client.get(f"/api/tesoreria/preventistas/anticipos/{ant_id}")
        assert r_det.status_code == 200
        assert r_det.json()["monto_pendiente_cents"] == 10_000_00

        # 4. POST reposición -> 200
        r_rep = client.post(
            f"/api/tesoreria/preventistas/anticipos/{ant_id}/reposicion",
            headers={"Idempotency-Key": "api_rep_01"},
            json={"monto_centavos": 6_000_00, "motivo": "Reposicion parcial API"},
        )
        assert r_rep.status_code == 200
        rep_data = r_rep.json()
        assert rep_data["monto_repuesto_cents"] == 6_000_00
        assert rep_data["monto_pendiente_cents"] == 4_000_00

        # 5. GET reposiciones de anticipo -> 200
        r_reps = client.get(f"/api/tesoreria/preventistas/anticipos/{ant_id}/reposiciones")
        assert r_reps.status_code == 200
        assert len(r_reps.json()) == 1

        # 6. GET resumen -> 200
        r_resumen = client.get("/api/tesoreria/preventistas/resumen")
        assert r_resumen.status_code == 200
        resumen_data = r_resumen.json()
        assert resumen_data["total_anticipado_cents"] == 10_000_00
        assert resumen_data["total_repuesto_cents"] == 6_000_00
        assert resumen_data["total_pendiente_reposicion_cents"] == 4_000_00
    finally:
        tesoreria._get_lab_db = original_get_db
        app.dependency_overrides.clear()
