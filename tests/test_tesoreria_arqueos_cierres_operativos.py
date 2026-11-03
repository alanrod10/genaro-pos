"""Pruebas exhaustivas del circuito operativo de Arqueos, Conciliación Digital y Cierres.

Cubre las 17 condiciones operativas exigidas por la especificación:
1. Arqueo sin diferencia (diferencia == 0, status RESOLVED, estado_revision CONCILIADO).
2. Arqueo con diferencia positiva / sobrante (diferencia > 0, estado_revision CON DIFERENCIA, expediente abierto).
3. Arqueo con diferencia negativa / faltante (diferencia < 0, estado_revision CON DIFERENCIA, expediente abierto).
4. El arqueo NO modifica el saldo de fondos en tesoreria_fondos ni genera movimientos.
5. Conciliación digital declarativa manual (1210/1220/1230, saldo informado, evidencia, fecha consulta).
6. Identificación de evidencia no disponible / notas explícitas sin invención de saldos.
7. Cierre con todos los fondos conciliados.
8. Cierre con diferencias documentadas en snapshot.
9. Cierre con traslados pendientes autorizado (allow_pending_transfers=True).
10. El dinero en tránsito NO se computa como disponibilidad ni recibido en destino.
11. Fotografía original inmutable ante movimientos posteriores.
12. Rechazar duplicación de cierre para la misma jornada.
13. Verificación de permisos y atribución de responsable.
14. Persistencia y trazabilidad con hash SHA-256 canónico.
15. NO se generan asientos de ajuste automáticos ni compensaciones contables artificiales.
16. Movimientos históricos #51 y #52 permanecen inalterados.
17. Consistencia estricta de saldos antes y después del cierre.
"""

import json
import sqlite3
import pytest

from tesoreria.catalog import obtener_fondo_por_codigo, sembrar_catalogo_canónico
from tesoreria.closures import (
    ErrorCierre,
    beginClosure,
    confirmClosure,
    getClosure,
    getClosureHistory,
    openNextJournal,
    validateClosure,
)
from tesoreria.jornadas import abrir_jornada, obtener_jornada_actual
from tesoreria.movements import registerEntry, registerExit
from tesoreria.reconciliations import (
    ErrorArqueo,
    authorizeDifference,
    createDifferenceCase,
    getDifferenceCases,
    getPendingTransfersForFund,
    getReconciliation,
    getReconciliationHistory,
    reconcileDigitalAccount,
    reconcilePhysicalFund,
    resolveDifferenceCase,
)
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.transfers import transferFunds


@pytest.fixture
def conn_lab():
    """Conexión a SQLite en memoria aislada con catálogo canónico y jornada 1 abierta."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="alan")
    yield conn
    conn.close()


def test_01_arqueo_fisico_sin_diferencia_resolved(conn_lab):
    """Condición 1: Arqueo sin diferencia -> diferencia_cents == 0, status RESOLVED, CONCILIADO."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=100_000_00, counterpart="Aporte", reason="Fondeo diario", created_by="alan")

    rec = reconcilePhysicalFund(
        conn=conn_lab,
        fund_code="1110",
        verified_amount_cents=100_000_00,
        user_id="alan",
        shift_name="MAÑANA",
        notes="Recuento turno mañana exacto",
    )

    assert rec["fund_code"] == "1110"
    assert rec["naturaleza"] == "FÍSICO"
    assert rec["turno"] == "MAÑANA"
    assert rec["saldo_registrado_cents"] == 100_000_00
    assert rec["saldo_verificado_cents"] == 100_000_00
    assert rec["diferencia_cents"] == 0
    assert rec["status"] == "RESOLVED"
    assert rec["estado_revision"] == "CONCILIADO"
    assert rec["difference_case_id"] is None


def test_02_arqueo_fisico_diferencia_positiva_sobrante(conn_lab):
    """Condición 2: Arqueo con diferencia positiva (sobrante) -> CON DIFERENCIA y expediente abierto."""
    registerEntry(conn_lab, fund_code="1120", amount_cents=50_000_00, counterpart="Aporte", reason="Ahorro", created_by="alan")

    rec = reconcilePhysicalFund(
        conn=conn_lab,
        fund_code="1120",
        verified_amount_cents=52_000_00,  # +$2.000 sobrante
        user_id="alan",
        shift_name="TARDE",
        notes="Sobrante en caja",
        create_difference_case=True,
        investigation_notes="Posible propina o cambio no retirado",
    )

    assert rec["diferencia_cents"] == 2_000_00
    assert rec["status"] == "OPEN"
    assert rec["estado_revision"] == "CON DIFERENCIA"
    assert rec["difference_case_id"] is not None

    cases = getDifferenceCases(conn_lab, fund_code="1120")
    assert len(cases) == 1
    assert cases[0]["case_id"] == rec["difference_case_id"]
    assert cases[0]["difference_cents"] == 2_000_00
    assert cases[0]["status"] == "OPEN"
    assert "Posible propina" in cases[0]["investigation_notes"]


def test_03_arqueo_fisico_diferencia_negativa_faltante(conn_lab):
    """Condición 3: Arqueo con diferencia negativa (faltante) -> CON DIFERENCIA y expediente abierto."""
    registerEntry(conn_lab, fund_code="1130", amount_cents=80_000_00, counterpart="Aporte", reason="Fondeo Prepay", created_by="alan")

    rec = reconcilePhysicalFund(
        conn=conn_lab,
        fund_code="1130",
        verified_amount_cents=75_000_00,  # -$5.000 faltante
        user_id="alan",
        shift_name="NOCHE",
        notes="Faltante al cierre de turno",
        create_difference_case=True,
        investigation_notes="Revisar tickets de recarga pendientes",
    )

    assert rec["diferencia_cents"] == -5_000_00
    assert rec["status"] == "OPEN"
    assert rec["estado_revision"] == "CON DIFERENCIA"
    assert rec["difference_case_id"] is not None

    cases = getDifferenceCases(conn_lab, fund_code="1130")
    assert len(cases) == 1
    assert cases[0]["case_id"] == rec["difference_case_id"]
    assert cases[0]["difference_cents"] == -5_000_00
    assert cases[0]["status"] == "OPEN"


def test_04_arqueo_no_modifica_saldo_de_fondos(conn_lab):
    """Condición 4: Confirmar que el arqueo NO modifica el saldo del fondo en tesoreria_fondos ni genera movimientos."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=30_000_00, counterpart="Venta", reason="Fondo", created_by="alan")

    f_antes = obtener_fondo_por_codigo(conn_lab, "1110")
    movs_antes = conn_lab.execute("SELECT COUNT(*) FROM tesoreria_movimientos").fetchone()[0]

    # Ejecutar arqueo con gran diferencia (-$10.000)
    reconcilePhysicalFund(
        conn=conn_lab,
        fund_code="1110",
        verified_amount_cents=20_000_00,
        user_id="alan",
        shift_name="MAÑANA",
        notes="Arqueo con descuadre",
    )

    f_despues = obtener_fondo_por_codigo(conn_lab, "1110")
    movs_despues = conn_lab.execute("SELECT COUNT(*) FROM tesoreria_movimientos").fetchone()[0]

    # Invariante contable estricta: saldo y versión de tesoreria_fondos no mutan
    assert f_despues["saldo_centavos"] == f_antes["saldo_centavos"] == 30_000_00
    assert f_despues["version"] == f_antes["version"]
    assert movs_despues == movs_antes  # Cero movimientos automáticos de ajuste


def test_05_conciliacion_digital_declarativa_manual(conn_lab):
    """Condición 5: Conciliación digital declarativa manual para 1210/1220/1230 sin API externa."""
    # Fondeo digital en MP
    registerEntry(conn_lab, fund_code="1210", amount_cents=250_000_00, counterpart="Cobros MP", reason="Cobranza QR", created_by="alan")

    rec = reconcileDigitalAccount(
        conn=conn_lab,
        fund_code="1210",
        declared_amount_cents=250_000_00,
        user_id="alan",
        evidence_reference="EXTRACTO-MP-2026-11-01",
        consultation_timestamp="2026-11-01T15:30:00Z",
        notes="Saldo verificado en panel web de Mercado Pago",
    )

    assert rec["fund_code"] == "1210"
    assert rec["naturaleza"] == "DIGITAL"
    assert rec["is_digital"] is True
    assert rec["saldo_registrado_cents"] == 250_000_00
    assert rec["saldo_verificado_cents"] == 250_000_00
    assert rec["diferencia_cents"] == 0
    assert rec["estado_revision"] == "CONCILIADO"
    assert "Saldo verificado en panel web de Mercado Pago" in rec["notes"]
    assert "EXTRACTO-MP-2026-11-01" in rec["evidence_reference"]

    # Rechazo si se intenta conciliar cuenta física como digital
    with pytest.raises(ErrorArqueo, match="fondo físico"):
        reconcileDigitalAccount(
            conn=conn_lab,
            fund_code="1110",
            declared_amount_cents=1000_00,
            user_id="alan",
            evidence_reference="NA",
        )

    # Rechazo si se intenta arquear cuenta digital como fondo físico
    with pytest.raises(ErrorArqueo, match="cuenta digital"):
        reconcilePhysicalFund(
            conn=conn_lab,
            fund_code="1210",
            verified_amount_cents=1000_00,
            user_id="alan",
        )


def test_06_identificacion_evidencia_no_disponible(conn_lab):
    """Condición 6: Identificación explícita de evidencia no disponible sin inventar saldos."""
    registerEntry(conn_lab, fund_code="1220", amount_cents=15_000_00, counterpart="PREPAY", reason="Carga", created_by="alan")

    rec = reconcileDigitalAccount(
        conn=conn_lab,
        fund_code="1220",
        declared_amount_cents=15_000_00,
        user_id="alan",
        evidence_reference="PENDIENTE_EXTRACTO_PORTAL_CAIDO",
        consultation_timestamp="2026-11-01T16:00:00Z",
        notes="Portal de Prepay reporta error 500 temporal; saldo declarado por llamada telefónica",
    )

    assert rec["estado_revision"] == "CONCILIADO"
    assert "Portal de Prepay reporta error 500" in rec["notes"]
    assert "PENDIENTE_EXTRACTO_PORTAL_CAIDO" in rec["evidence_reference"]
    # Evidencia registrada con trazabilidad clara
    recon_row = getReconciliation(conn_lab, rec["reconciliation_id"])
    assert "PORTAL_CAIDO" in recon_row["notes"]


def test_07_cierre_con_todos_los_fondos_conciliados(conn_lab):
    """Condición 7: Cierre con todos los fondos conciliados -> can_close True, confirmClosure exitoso."""
    # Fondear y vaciar 1110 a 1120 para cierre limpio
    registerEntry(conn_lab, fund_code="1110", amount_cents=10_000_00, counterpart="Venta", reason="Fondo", created_by="alan")
    transferFunds(conn_lab, origin_fund_code="1110", destination_fund_code="1120", amount_cents=10_000_00, dispatched_by="alan", reason="Barrido")

    # Confirmar recepción del traslado para no dejar tránsito
    trf = conn_lab.execute("SELECT transfer_id FROM tesoreria_traslados WHERE status = 'PENDING_RECEIPT'").fetchone()
    from tesoreria.transfers import confirmTransferReceipt
    confirmTransferReceipt(conn_lab, transfer_id=trf["transfer_id"], received_by="alan")

    # Realizar arqueos para los fondos con saldo
    reconcilePhysicalFund(conn_lab, fund_code="1110", verified_amount_cents=0, user_id="alan")
    reconcilePhysicalFund(conn_lab, fund_code="1120", verified_amount_cents=10_000_00, user_id="alan")

    jornada = obtener_jornada_actual(conn_lab)
    beginClosure(conn_lab, journal_id=jornada["id"], user_id="alan")

    val = validateClosure(conn_lab, journal_id=jornada["id"])
    assert val["can_close"] is True
    assert val["cash_drawer_balance_cents"] == 0
    assert val["transfers_in_transit_count"] == 0

    cierre = confirmClosure(conn_lab, journal_id=jornada["id"], user_id="alan", observations="Cierre limpio sin novedades")
    assert cierre["snapshot_hash"] is not None
    assert len(cierre["snapshot_hash"]) == 64  # SHA-256 hex string


def test_08_cierre_con_diferencias_documentadas(conn_lab):
    """Condición 8: Cierre con diferencias capturadas en el snapshot canónico."""
    # Fondeo 1120
    registerEntry(conn_lab, fund_code="1120", amount_cents=50_000_00, counterpart="Aporte", reason="Fondeo", created_by="alan")

    # Arqueo con diferencia
    arq = reconcilePhysicalFund(
        conn_lab,
        fund_code="1120",
        verified_amount_cents=48_000_00,  # -$2.000 de diferencia
        user_id="alan",
        notes="Faltante documentado",
        create_difference_case=True,
    )
    authorizeDifference(conn_lab, reconciliation_id=arq["reconciliation_id"], user_id="alan", notes="Autorizado")

    jornada = obtener_jornada_actual(conn_lab)
    beginClosure(conn_lab, journal_id=jornada["id"], user_id="alan")

    cierre = confirmClosure(
        conn_lab,
        journal_id=jornada["id"],
        user_id="alan",
        observations="Cierre con diferencia en 1120 documentada",
        allow_nonzero_1110=True,
    )

    snap = json.loads(cierre["snapshot_json"])
    assert "fondos" in snap
    fondo_1120_snap = next(f for f in snap["fondos"] if f["fund_code"] == "1120")
    assert fondo_1120_snap["saldo_registrado_cents"] == 50_000_00
    assert fondo_1120_snap["saldo_verificado_cents"] == 48_000_00
    assert fondo_1120_snap["diferencia_cents"] == -2_000_00
    assert fondo_1120_snap["estado_revision"] == "CON DIFERENCIA"


def test_09_cierre_con_traslados_pendientes_autorizado(conn_lab):
    """Condición 9: Cierre con traslados pendientes autorizado (allow_pending_transfers=True)."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=100_000_00, counterpart="Aporte", reason="Fondeo", created_by="alan")
    transferFunds(conn_lab, origin_fund_code="1110", destination_fund_code="1120", amount_cents=40_000_00, dispatched_by="alan", reason="Traslado nocturno")

    jornada = obtener_jornada_actual(conn_lab)
    beginClosure(conn_lab, journal_id=jornada["id"], user_id="alan")

    # Sin autorización explícita debe bloquear
    val_sin_auth = validateClosure(conn_lab, journal_id=jornada["id"], allow_pending_transfers=False)
    assert val_sin_auth["can_close"] is False
    assert val_sin_auth["transfers_in_transit_count"] == 1

    with pytest.raises(ErrorCierre, match="No se puede confirmar el cierre"):
        confirmClosure(conn_lab, journal_id=jornada["id"], user_id="alan", allow_pending_transfers=False)

    # Con autorización explícita debe permitir
    val_con_auth = validateClosure(conn_lab, journal_id=jornada["id"], allow_pending_transfers=True, allow_nonzero_1110=True)
    assert val_con_auth["can_close"] is True
    assert any("PENDING_RECEIPT" in w for w in val_con_auth["warnings"])

    cierre = confirmClosure(
        conn_lab,
        journal_id=jornada["id"],
        user_id="alan",
        observations="Cierre con traslado nocturno en camino",
        allow_pending_transfers=True,
        allow_nonzero_1110=True,
    )

    snap = json.loads(cierre["snapshot_json"])
    assert len(snap["traslados_pendientes"]) == 1
    assert snap["traslados_pendientes"][0]["amount_cents"] == 40_000_00
    assert snap["traslados_pendientes"][0]["origin_fund_code"] == "1110"
    assert snap["traslados_pendientes"][0]["destination_fund_code"] == "1120"


def test_10_dinero_en_transito_no_se_computa_en_destino(conn_lab):
    """Condición 10: Verificar que el dinero en tránsito NO se compute como recibido en destino."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=100_000_00, counterpart="Aporte", reason="Fondeo", created_by="alan")

    f_dest_antes = obtener_fondo_por_codigo(conn_lab, "1120")
    assert f_dest_antes["saldo_centavos"] == 0

    # Despachar traslado de 1110 a 1120
    transferFunds(conn_lab, origin_fund_code="1110", destination_fund_code="1120", amount_cents=25_000_00, dispatched_by="alan", reason="Envío")

    # Comprobar fondo de destino 1120
    f_dest_despues = obtener_fondo_por_codigo(conn_lab, "1120")
    assert f_dest_despues["saldo_centavos"] == 0  # CERO en destino mientras esté en tránsito

    # Comprobar la consulta de traslados en tránsito para el fondo
    pending_trfs = getPendingTransfersForFund(conn_lab, "1120")
    assert len(pending_trfs) == 1
    assert pending_trfs[0]["amount_cents"] == 25_000_00
    assert pending_trfs[0]["destination_fund_code"] == "1120"


def test_11_fotografia_original_inmutable_ante_eventos_posteriores(conn_lab):
    """Condición 11: Verificar que el cierre conserva la fotografía original inmutable aunque se inserten movimientos posteriores."""
    registerEntry(conn_lab, fund_code="1120", amount_cents=10_000_00, counterpart="Aporte", reason="Fondo", created_by="alan")
    jornada1 = obtener_jornada_actual(conn_lab)
    beginClosure(conn_lab, journal_id=jornada1["id"], user_id="alan")

    cierre1 = confirmClosure(conn_lab, journal_id=jornada1["id"], user_id="alan")
    hash_original = cierre1["snapshot_hash"]
    snap_original_str = cierre1["snapshot_json"]

    # Abrir siguiente jornada e insertar nuevos movimientos (1110 está en 0)
    jornada2 = openNextJournal(conn_lab, user_id="alan")
    assert jornada2["numero_jornada"] == 2
    registerEntry(conn_lab, fund_code="1120", amount_cents=999_000_00, counterpart="Venta Masiva", reason="Nueva venta", created_by="alan")

    # Recuperar cierre de jornada 1
    cierre1_recuperado = getClosure(conn_lab, journal_id=jornada1["id"])
    assert cierre1_recuperado["snapshot_hash"] == hash_original
    assert cierre1_recuperado["snapshot_json"] == snap_original_str


def test_12_rechazar_duplicacion_de_cierre(conn_lab):
    """Condición 12: Rechazar duplicación de cierre para la misma jornada."""
    jornada = obtener_jornada_actual(conn_lab)
    beginClosure(conn_lab, journal_id=jornada["id"], user_id="alan")
    confirmClosure(conn_lab, journal_id=jornada["id"], user_id="alan", allow_nonzero_1110=True)

    # Intentar confirmar cierre nuevamente sobre la jornada ya archivada
    with pytest.raises(ErrorCierre, match="ya está cerrada"):
        confirmClosure(conn_lab, journal_id=jornada["id"], user_id="alan")


def test_13_verificar_permisos_y_responsable(conn_lab):
    """Condición 13: Verificar permisos y responsable (operador atribuido correctamente)."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=50_000_00, counterpart="Venta", reason="Fondo", created_by="alan")

    rec = reconcilePhysicalFund(conn_lab, fund_code="1110", verified_amount_cents=50_000_00, user_id="cajero_turno_manana", shift_name="MAÑANA")
    assert rec["performed_by"] == "cajero_turno_manana"

    jornada = obtener_jornada_actual(conn_lab)
    beginClosure(conn_lab, journal_id=jornada["id"], user_id="supervisor_cierre")
    cierre = confirmClosure(conn_lab, journal_id=jornada["id"], user_id="gerente_general", allow_nonzero_1110=True)

    assert cierre["closed_by"] == "gerente_general"
    snap = json.loads(cierre["snapshot_json"])
    assert snap["closed_by"] == "gerente_general"


def test_14_verificar_persistencia_y_hash_sha256(conn_lab):
    """Condición 14: Verificar persistencia y trazabilidad con hash SHA-256 canónico determinista."""
    jornada = obtener_jornada_actual(conn_lab)
    beginClosure(conn_lab, journal_id=jornada["id"], user_id="alan")
    cierre = confirmClosure(conn_lab, journal_id=jornada["id"], user_id="alan", allow_nonzero_1110=True)

    import hashlib
    raw_json = cierre["snapshot_json"]
    computed_hash = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
    assert cierre["snapshot_hash"] == computed_hash

    # Verificar que el historial lista este cierre
    historial = getClosureHistory(conn_lab)
    assert len(historial) == 1
    assert historial[0]["snapshot_hash"] == computed_hash


def test_15_no_genera_asientos_automaticos_ni_compensaciones(conn_lab):
    """Condición 15: Verificar que NO se generen asientos de ajuste automáticos ni compensaciones contables."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=10_000_00, counterpart="Venta", reason="Fondo", created_by="alan")

    # Arqueo con faltante grande
    arq = reconcilePhysicalFund(conn_lab, fund_code="1110", verified_amount_cents=5_000_00, user_id="alan", notes="Faltante documentado")
    authorizeDifference(conn_lab, reconciliation_id=arq["reconciliation_id"], user_id="alan", notes="Autorizado")

    jornada = obtener_jornada_actual(conn_lab)
    beginClosure(conn_lab, journal_id=jornada["id"], user_id="alan")
    confirmClosure(conn_lab, journal_id=jornada["id"], user_id="alan", allow_nonzero_1110=True)

    # Verificar que en tesoreria_movimientos NO existen registros automáticos de ajuste
    filas_ajuste = conn_lab.execute(
        "SELECT COUNT(*) FROM tesoreria_movimientos WHERE reason LIKE '%ajuste%' OR reason LIKE '%compensac%' OR counterpart LIKE '%ajuste%'"
    ).fetchone()[0]
    assert filas_ajuste == 0


def test_16_movimientos_historicos_51_52_inalterados(conn_lab):
    """Condición 16: Verificar que los movimientos #51 y #52 permanezcan inalterados."""
    # Insertar movimientos #51 y #52 representativos en la base de staging
    conn_lab.execute(
        """
        INSERT OR IGNORE INTO tesoreria_movimientos
        (id, movement_id, journal_id, fund_id, movement_type, amount_cents, counterpart, reason, created_by, status, occurred_at, recorded_at)
        VALUES
        (51, 'mov-hist-051', 1, 1, 'ENTRY', 510000, 'HISTORICO', 'Movimiento historico 51', 'sistema', 'POSTED', '2026-10-01T10:00:00Z', '2026-10-01T10:00:00Z'),
        (52, 'mov-hist-052', 1, 1, 'ENTRY', 520000, 'HISTORICO', 'Movimiento historico 52', 'sistema', 'POSTED', '2026-10-01T11:00:00Z', '2026-10-01T11:00:00Z')
        """
    )
    conn_lab.commit()

    m51_antes = dict(conn_lab.execute("SELECT * FROM tesoreria_movimientos WHERE id = 51").fetchone())
    m52_antes = dict(conn_lab.execute("SELECT * FROM tesoreria_movimientos WHERE id = 52").fetchone())

    # Realizar arqueos, traslados y cierre
    reconcilePhysicalFund(conn_lab, fund_code="1110", verified_amount_cents=0, user_id="alan")
    jornada = obtener_jornada_actual(conn_lab)
    beginClosure(conn_lab, journal_id=jornada["id"], user_id="alan")
    confirmClosure(conn_lab, journal_id=jornada["id"], user_id="alan", allow_nonzero_1110=True)

    m51_despues = dict(conn_lab.execute("SELECT * FROM tesoreria_movimientos WHERE id = 51").fetchone())
    m52_despues = dict(conn_lab.execute("SELECT * FROM tesoreria_movimientos WHERE id = 52").fetchone())

    assert m51_antes == m51_despues
    assert m52_antes == m52_despues


def test_17_consistencia_de_saldos_antes_y_despues_del_cierre(conn_lab):
    """Condición 17: Verificar consistencia estricta de saldos antes y después del cierre."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=100_000_00, counterpart="Venta", reason="Fondo", created_by="alan")
    registerEntry(conn_lab, fund_code="1120", amount_cents=200_000_00, counterpart="Fondo Fijo", reason="Fondo", created_by="alan")
    registerEntry(conn_lab, fund_code="1210", amount_cents=300_000_00, counterpart="MP", reason="Cobros", created_by="alan")

    total_saldos_antes = conn_lab.execute("SELECT SUM(saldo_centavos) FROM tesoreria_fondos").fetchone()[0]

    jornada = obtener_jornada_actual(conn_lab)
    beginClosure(conn_lab, journal_id=jornada["id"], user_id="alan")
    confirmClosure(conn_lab, journal_id=jornada["id"], user_id="alan", allow_nonzero_1110=True)

    total_saldos_despues = conn_lab.execute("SELECT SUM(saldo_centavos) FROM tesoreria_fondos").fetchone()[0]

    assert total_saldos_antes == total_saldos_despues == 600_000_00
