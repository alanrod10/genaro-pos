"""Pruebas de Arqueos y Expedientes de Diferencias — LAB-009 (ARC-001 a ARC-007)."""

import sqlite3
import pytest
from tesoreria.catalog import obtener_fondo_por_codigo, sembrar_catalogo_canónico
from tesoreria.jornadas import abrir_jornada
from tesoreria.movements import registerEntry
from tesoreria.reconciliations import (
    ErrorArqueo,
    createDifferenceCase,
    createReconciliation,
    getDifferenceCases,
    getReconciliation,
    getReconciliationHistory,
    resolveDifferenceCase,
)
from tesoreria.schema import inicializar_esquema_laboratorio


@pytest.fixture
def conn_lab():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="alan")
    yield conn
    conn.close()


def test_arc_001_reconciliation_exact_balance_no_diff(conn_lab):
    """ARC-001: Arqueo con saldo verificado igual al registrado: diferencia cero y estado RESOLVED."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=1000_00, counterpart="Aporte", reason="Fondeo", created_by="alan")

    f_antes = obtener_fondo_por_codigo(conn_lab, "1110")
    assert f_antes["saldo_centavos"] == 1000_00

    recon = createReconciliation(
        conn=conn_lab,
        fund_code="1110",
        reconciliation_type="CLOSING",
        verified_amount_cents=1000_00,
        user_id="alan",
        notes="Conteo conforme sin novedades",
    )

    assert recon["status"] == "RESOLVED"
    assert recon["saldo_registrado_cents"] == 1000_00
    assert recon["saldo_verificado_cents"] == 1000_00
    assert recon["diferencia_cents"] == 0
    assert recon["difference_case_id"] is None

    # Invariante crítica: el saldo del fondo NO se modifica
    f_despues = obtener_fondo_por_codigo(conn_lab, "1110")
    assert f_despues["saldo_centavos"] == 1000_00


def test_arc_002_reconciliation_positive_difference_sobrante(conn_lab):
    """ARC-002: Arqueo con diferencia positiva (sobrante): no modifica fondo ni ajusta automáticamente."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=500_00, counterpart="Venta", reason="Ingreso", created_by="alan")

    recon = createReconciliation(
        conn=conn_lab,
        fund_code="1110",
        reconciliation_type="PARTIAL",
        verified_amount_cents=550_00,  # 50 pesos de más
        user_id="alan",
        notes="Sobrante detectado en caja",
        create_difference_case=True,
    )

    assert recon["status"] == "OPEN"
    assert recon["diferencia_cents"] == 50_00
    assert recon["difference_case_id"] is not None

    # Saldo del fondo permanece idéntico en libros
    f = obtener_fondo_por_codigo(conn_lab, "1110")
    assert f["saldo_centavos"] == 500_00


def test_arc_003_reconciliation_negative_difference_faltante_no_payroll_deduction(conn_lab):
    """ARC-003: Arqueo con diferencia negativa (faltante): PROHIBIDO descontar a personas o ajustar libros."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=1000_00, counterpart="Aporte", reason="Ingreso", created_by="alan")

    recon = createReconciliation(
        conn=conn_lab,
        fund_code="1110",
        reconciliation_type="TOTAL",
        verified_amount_cents=950_00,  # 50 pesos de menos
        user_id="cajero_01",
        create_difference_case=True,
        investigation_notes="Faltante en recuento nocturno",
    )

    assert recon["status"] == "OPEN"
    assert recon["diferencia_cents"] == -50_00
    assert recon["difference_case_id"] is not None

    # El saldo del fondo no se reduce arbitrariamente
    f = obtener_fondo_por_codigo(conn_lab, "1110")
    assert f["saldo_centavos"] == 1000_00


def test_arc_004_create_difference_case_and_history(conn_lab):
    """ARC-004: Apertura y consulta de expedientes de diferencias."""
    recon = createReconciliation(
        conn=conn_lab,
        fund_code="1120",
        reconciliation_type="EXTRAORDINARY",
        verified_amount_cents=200_00,
        user_id="alan",
        notes="Arqueo extraordinario con diferencia",
    )
    # Diferencia es 200_00 - 0 = 200_00
    case = createDifferenceCase(
        conn=conn_lab,
        reconciliation_id=recon["reconciliation_id"],
        investigation_notes="Billetes encontrados en sobre de seguridad",
        user_id="supervisor",
    )

    assert case["status"] == "OPEN"
    assert case["difference_cents"] == 200_00

    cases = getDifferenceCases(conn_lab, fund_code="1120")
    assert len(cases) == 1
    assert cases[0]["case_id"] == case["case_id"]


def test_arc_005_resolve_difference_case_with_evidence(conn_lab):
    """ARC-005: Resolución documentada de expediente con evidencia documental y responsable."""
    recon = createReconciliation(
        conn=conn_lab,
        fund_code="1110",
        reconciliation_type="CLOSING",
        verified_amount_cents=100_00,
        user_id="alan",
        create_difference_case=True,
        investigation_notes="Diferencia en arqueo",
    )
    case_id = recon["difference_case_id"]

    resolved = resolveDifferenceCase(
        conn=conn_lab,
        case_id=case_id,
        resolution_notes="Error de tipeo en comprobante manual verificado contra ticket duplicado",
        evidence_reference="TICKET-DUP-88219",
        resolved_by="auditor_jefe",
    )

    assert resolved["status"] == "RESOLVED"
    assert resolved["resolution_evidence"] == "TICKET-DUP-88219"

    # Verificar sincronización con el arqueo
    recon_actual = getReconciliation(conn_lab, recon["reconciliation_id"])
    assert recon_actual["status"] == "RESOLVED"


def test_arc_006_valid_and_invalid_reconciliation_types(conn_lab):
    """ARC-006: Validación de tipos canónicos de arqueo."""
    for tipo in ("OPENING", "PARTIAL", "TOTAL", "EXTRAORDINARY", "CLOSING"):
        r = createReconciliation(conn_lab, fund_code="1130", reconciliation_type=tipo, verified_amount_cents=0, user_id="alan")
        assert r["reconciliation_type"] == tipo

    with pytest.raises(ErrorArqueo, match="Tipo de arqueo inválido"):
        createReconciliation(conn_lab, fund_code="1130", reconciliation_type="ARBITRARIO", verified_amount_cents=0, user_id="alan")


def test_arc_007_inmutabilidad_triggers_arqueos(conn_lab):
    """ARC-007: Trigger protege contra eliminación de arqueos."""
    recon = createReconciliation(conn_lab, fund_code="1110", reconciliation_type="CLOSING", verified_amount_cents=0, user_id="alan")

    cursor = conn_lab.cursor()
    with pytest.raises(sqlite3.IntegrityError, match="No está permitido eliminar registros de arqueos"):
        cursor.execute("DELETE FROM tesoreria_arqueos WHERE reconciliation_id = ?", (recon["reconciliation_id"],))
