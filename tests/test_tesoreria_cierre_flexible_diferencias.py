"""Pruebas Automatizadas de Consolidación de Cierre Diario y Diferencias de Caja — GenaroPOS.

Entorno obligatorio: STRICT_LOCAL_SQLITE / STAGING.
Cubre las 12 pruebas obligatorias exigidas por la especificación:
1. Cierre sin diferencia.
2. Cierre con faltante y observación.
3. Cierre con sobrante y observación.
4. Cierre con diferencia sin observación: debe solicitarla (rechazo inmediato).
5. Cierre con diferencia documentada: permite finalizar sin aprobación adicional.
6. Siguiente jornada con 1110 en cero.
7. Traslado del efectivo contado completo a 1120 (tanto faltante como sobrante).
8. Conservación del antecedente histórico de la diferencia.
9. Registro posterior de dinero encontrado en una jornada nueva sin alterar el pasado.
10. Usuario no autorizado intentando realizar arqueos o cierres (HTTP 403 / Error).
11. Traslados pendientes y demás controles existentes no relajados.
12. Repetición de solicitudes: no duplicar movimientos ni traslados (idempotencia).
Extra: Trazabilidad inmutable ante modificación de observaciones.
Integridad: Verificación de la base de datos de producción intacta.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
import sqlite3
import tempfile
import pytest

from api.auth import AuthenticatedUser
from api.dependencies import get_current_user
from api.main import app
import api.routers.tesoreria as tes_router
from fastapi.testclient import TestClient

from tesoreria.catalog import obtener_fondo_por_codigo, sembrar_catalogo_canónico
from tesoreria.closures import (
    ADMIN_USERS,
    ErrorCierre,
    beginClosure,
    confirmClosure,
    executeCashDrawerSweep,
    getClosure,
    openNextJournal,
    validateClosure,
)
from tesoreria.jornadas import abrir_jornada, obtener_jornada_actual
from tesoreria.movements import registerEntry
from tesoreria.reconciliations import (
    ErrorArqueo,
    authorizeDifference,
    reconcilePhysicalFund,
    updateReconciliationNotes,
)
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.transfers import transferFunds


@pytest.fixture
def conn_lab():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="alan")
    yield conn
    conn.close()


# =====================================================================
# 1. CIERRE SIN DIFERENCIA
# =====================================================================
def test_01_cierre_sin_diferencia(conn_lab):
    """Prueba 1: Cierre ordinario donde saldo teórico y físico coinciden."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=600_000, counterpart="Venta", reason="Fondeo", created_by="alan")
    j = obtener_jornada_actual(conn_lab)

    arq = reconcilePhysicalFund(
        conn_lab,
        fund_code="1110",
        verified_amount_cents=600_000,
        user_id="alan",
        notes="Recuento exacto",
    )
    assert arq["diferencia_cents"] == 0

    beginClosure(conn_lab, journal_id=j["id"], user_id="alan")
    sweep = executeCashDrawerSweep(conn_lab, journal_id=j["id"], user_id="alan")
    assert sweep["swept"] is True
    assert sweep["amount_cents"] == 600_000

    f1110 = obtener_fondo_por_codigo(conn_lab, "1110")
    f1120 = obtener_fondo_por_codigo(conn_lab, "1120")
    assert f1110["saldo_centavos"] == 0
    assert f1120["saldo_centavos"] == 600_000

    val = validateClosure(conn_lab, journal_id=j["id"])
    assert val["can_close"] is True

    cierre = confirmClosure(conn_lab, journal_id=j["id"], user_id="alan")
    assert cierre["status"] == "CONFIRMED"


# =====================================================================
# 2. CIERRE CON FALTANTE Y OBSERVACIÓN
# =====================================================================
def test_02_cierre_con_faltante_y_observacion(conn_lab):
    """Prueba 2: Cierre con faltante documentado con observación.
    - Teórico: $6.000, Contado: $5.800, Diferencia: -$200.
    - Pasan $5.800 a 1120.
    - 1110 queda en $0 sin movimientos comerciales ficticios.
    """
    registerEntry(conn_lab, fund_code="1110", amount_cents=600_000, counterpart="Venta", reason="Fondeo", created_by="alan")
    j = obtener_jornada_actual(conn_lab)

    arq = reconcilePhysicalFund(
        conn_lab,
        fund_code="1110",
        verified_amount_cents=580_000,
        user_id="alan",
        notes="Faltante de $200 por error de cambio en billetes",
    )
    assert arq["diferencia_cents"] == -20_000
    assert arq["notes"] == "Faltante de $200 por error de cambio en billetes"

    beginClosure(conn_lab, journal_id=j["id"], user_id="alan")
    val_pre = validateClosure(conn_lab, journal_id=j["id"])
    # No bloquea el cierre porque tiene observación
    assert any("Faltante físico documentado" in w for w in val_pre["warnings"])

    sweep = executeCashDrawerSweep(conn_lab, journal_id=j["id"], user_id="alan")
    assert sweep["swept"] is True
    assert sweep["amount_cents"] == 580_000  # Pasan exactamente $5.800 físicos

    f1110 = obtener_fondo_por_codigo(conn_lab, "1110")
    f1120 = obtener_fondo_por_codigo(conn_lab, "1120")
    assert f1110["saldo_centavos"] == 0  # 1110 en cero para la siguiente jornada
    assert f1120["saldo_centavos"] == 580_000  # 1120 recibió el dinero real

    # Sin movimientos comerciales falsos
    c = conn_lab.cursor()
    c.execute("SELECT COUNT(*) FROM tesoreria_movimientos WHERE counterpart LIKE '%AJUSTE%' OR reason LIKE '%AJUSTE%'")
    assert c.fetchone()[0] == 0
    c.close()

    cierre = confirmClosure(conn_lab, journal_id=j["id"], user_id="alan")
    assert cierre["status"] == "CONFIRMED"


# =====================================================================
# 3. CIERRE CON SOBRANTE Y OBSERVACIÓN
# =====================================================================
def test_03_cierre_con_sobrante_y_observacion(conn_lab):
    """Prueba 3: Cierre con sobrante documentado con observación.
    - Teórico: $6.000, Contado: $6.200, Diferencia: +$200.
    - Pasan $6.200 completos a 1120 (todo el dinero físico real).
    - 1110 queda en $0 sin registrar venta o ingreso comercial ficticio.
    """
    registerEntry(conn_lab, fund_code="1110", amount_cents=600_000, counterpart="Venta", reason="Fondeo", created_by="alan")
    j = obtener_jornada_actual(conn_lab)

    arq = reconcilePhysicalFund(
        conn_lab,
        fund_code="1110",
        verified_amount_cents=620_000,
        user_id="yilda",
        notes="Sobrante $200 cambio no retirado por cliente",
    )
    assert arq["diferencia_cents"] == 20_000

    beginClosure(conn_lab, journal_id=j["id"], user_id="yilda")
    val_pre = validateClosure(conn_lab, journal_id=j["id"])
    assert any("Sobrante físico documentado" in w for w in val_pre["warnings"])

    sweep = executeCashDrawerSweep(conn_lab, journal_id=j["id"], user_id="yilda")
    assert sweep["swept"] is True
    assert sweep["amount_cents"] == 620_000  # Todo el dinero contado ($6.200) pasa a 1120

    f1110 = obtener_fondo_por_codigo(conn_lab, "1110")
    f1120 = obtener_fondo_por_codigo(conn_lab, "1120")
    assert f1110["saldo_centavos"] == 0
    assert f1120["saldo_centavos"] == 620_000

    # Sin ventas ficticias creadas
    c = conn_lab.cursor()
    c.execute("SELECT COUNT(*) FROM tesoreria_movimientos WHERE counterpart LIKE '%VENTA FICTICIA%' OR reason LIKE '%SOBRANTE%'")
    assert c.fetchone()[0] == 0
    c.close()

    cierre = confirmClosure(conn_lab, journal_id=j["id"], user_id="yilda")
    assert cierre["status"] == "CONFIRMED"


# =====================================================================
# 4. CIERRE CON DIFERENCIA SIN OBSERVACIÓN: DEBE SOLICITARLA
# =====================================================================
def test_04_cierre_con_diferencia_sin_observacion_la_solicita(conn_lab):
    """Prueba 4: Arqueo con diferencia sin observación es rechazado inmediatamente."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=600_000, counterpart="Venta", reason="Fondeo", created_by="alan")

    # Intentar arqueo con diferencia sin notas debe fallar
    with pytest.raises(ErrorArqueo, match="obligatorio ingresar una observación"):
        reconcilePhysicalFund(conn_lab, fund_code="1110", verified_amount_cents=580_000, user_id="alan", notes="")

    with pytest.raises(ErrorArqueo, match="obligatorio ingresar una observación"):
        reconcilePhysicalFund(conn_lab, fund_code="1110", verified_amount_cents=620_000, user_id="alan", notes=None)


# =====================================================================
# 5. CIERRE CON DIFERENCIA DOCUMENTADA: PERMITE FINALIZAR
# =====================================================================
def test_05_cierre_con_diferencia_documentada_permite_finalizar(conn_lab):
    """Prueba 5: Diferencia documentada con observación no impide cerrar (sin segundo paso de aprobación)."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=600_000, counterpart="Venta", reason="Fondeo", created_by="alan")
    j = obtener_jornada_actual(conn_lab)
    reconcilePhysicalFund(
        conn_lab,
        fund_code="1110",
        verified_amount_cents=590_000,
        user_id="alan",
        notes="Diferencia de $100 documentada",
    )

    beginClosure(conn_lab, journal_id=j["id"], user_id="alan")
    executeCashDrawerSweep(conn_lab, journal_id=j["id"], user_id="alan")

    val = validateClosure(conn_lab, journal_id=j["id"])
    assert val["can_close"] is True

    cierre = confirmClosure(conn_lab, journal_id=j["id"], user_id="alan")
    assert cierre["status"] == "CONFIRMED"


# =====================================================================
# 6. SIGUIENTE JORNADA CON 1110 EN CERO
# =====================================================================
def test_06_siguiente_jornada_con_1110_en_cero(conn_lab):
    """Prueba 6: Tras cerrar con faltante o sobrante, la siguiente jornada abre con 1110 en exactamente $0."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=600_000, counterpart="Venta", reason="Fondeo", created_by="alan")
    j1 = obtener_jornada_actual(conn_lab)
    reconcilePhysicalFund(conn_lab, fund_code="1110", verified_amount_cents=580_000, user_id="alan", notes="Faltante $200")

    beginClosure(conn_lab, journal_id=j1["id"], user_id="alan")
    executeCashDrawerSweep(conn_lab, journal_id=j1["id"], user_id="alan")
    confirmClosure(conn_lab, journal_id=j1["id"], user_id="alan")

    # Abrir jornada 2
    j2 = openNextJournal(conn_lab, user_id="alan")
    assert j2["numero_jornada"] == 2
    f1110 = obtener_fondo_por_codigo(conn_lab, "1110")
    assert f1110["saldo_centavos"] == 0


# =====================================================================
# 7. TRASLADO DEL EFECTIVO CONTADO COMPLETO A 1120
# =====================================================================
def test_07_traslado_del_efectivo_contado_completo_a_1120(conn_lab):
    """Prueba 7: El importe del barrido coincide siempre con el efectivo contado verificado."""
    # Caso sobrante: $6.000 teo, $6.500 contado -> traslado $6.500 a 1120
    registerEntry(conn_lab, fund_code="1110", amount_cents=600_000, counterpart="Venta", reason="Fondeo", created_by="alan")
    j = obtener_jornada_actual(conn_lab)
    reconcilePhysicalFund(conn_lab, fund_code="1110", verified_amount_cents=650_000, user_id="alan", notes="Sobrante $500")

    beginClosure(conn_lab, journal_id=j["id"], user_id="alan")
    sweep = executeCashDrawerSweep(conn_lab, journal_id=j["id"], user_id="alan")
    assert sweep["amount_cents"] == 650_000
    assert sweep["saldo_contado_cents"] == 650_000

    f1120 = obtener_fondo_por_codigo(conn_lab, "1120")
    assert f1120["saldo_centavos"] == 650_000


# =====================================================================
# 8. CONSERVACIÓN DEL ANTECEDENTE HISTÓRICO DE LA DIFERENCIA
# =====================================================================
def test_08_conservacion_del_antecedente_historico_de_la_diferencia(conn_lab):
    """Prueba 8: El arqueo y el expediente conservan la diferencia y la observación."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=600_000, counterpart="Venta", reason="Fondeo", created_by="alan")
    j = obtener_jornada_actual(conn_lab)
    arq = reconcilePhysicalFund(
        conn_lab,
        fund_code="1110",
        verified_amount_cents=580_000,
        user_id="alan",
        notes="Faltante de $200 documentado",
    )

    c = conn_lab.cursor()
    c.execute("SELECT diferencia_cents, notes, difference_case_id FROM tesoreria_arqueos WHERE reconciliation_id = ?", (arq["reconciliation_id"],))
    row_arq = c.fetchone()
    assert row_arq["diferencia_cents"] == -20_000
    assert row_arq["notes"] == "Faltante de $200 documentado"
    assert row_arq["difference_case_id"] is not None

    c.execute("SELECT difference_cents, investigation_notes, status FROM tesoreria_expedientes_diferencias WHERE case_id = ?", (row_arq["difference_case_id"],))
    row_exp = c.fetchone()
    assert row_exp["difference_cents"] == -20_000
    assert row_exp["investigation_notes"] == "Faltante de $200 documentado"
    assert row_exp["status"] == "OPEN"
    c.close()


# =====================================================================
# 9. REGISTRO POSTERIOR DE DINERO ENCONTRADO EN UNA JORNADA NUEVA
# =====================================================================
def test_09_registro_posterior_dinero_encontrado_en_jornada_nueva(conn_lab):
    """Prueba 9: Si aparece dinero correspondiente a un faltante anterior en la nueva jornada,
    se ingresa como ENTRY normal en la jornada nueva sin modificar el arqueo histórico anterior.
    """
    # Jornada 1: Cierre con faltante -$200
    registerEntry(conn_lab, fund_code="1110", amount_cents=600_000, counterpart="Venta J1", reason="Fondeo", created_by="alan")
    j1 = obtener_jornada_actual(conn_lab)
    arq1 = reconcilePhysicalFund(conn_lab, fund_code="1110", verified_amount_cents=580_000, user_id="alan", notes="Faltante $200")
    beginClosure(conn_lab, journal_id=j1["id"], user_id="alan")
    executeCashDrawerSweep(conn_lab, journal_id=j1["id"], user_id="alan")
    cierre1 = confirmClosure(conn_lab, journal_id=j1["id"], user_id="alan")
    hash_j1_previo = cierre1["canonical_hash"]

    # Jornada 2: Apertura en $0
    j2 = openNextJournal(conn_lab, user_id="alan")

    # Aparecen los $200 del día anterior: se ingresan como recupero en 1110 de Jornada 2
    recupero = registerEntry(
        conn_lab,
        fund_code="1110",
        amount_cents=20_000,
        counterpart="Recupero de efectivo",
        reason="Aparición de billete traspapelado de la jornada anterior",
        created_by="alan",
    )
    assert recupero["amount_cents"] == 20_000
    assert recupero["movement_type"] == "ENTRY"

    f1110_j2 = obtener_fondo_por_codigo(conn_lab, "1110")
    assert f1110_j2["saldo_centavos"] == 20_000

    # Comprobar que el arqueo de J1 y el snapshot de J1 permanecen rigurosamente inalterados
    c = conn_lab.cursor()
    c.execute("SELECT diferencia_cents, notes FROM tesoreria_arqueos WHERE reconciliation_id = ?", (arq1["reconciliation_id"],))
    row_arq1 = c.fetchone()
    assert row_arq1["diferencia_cents"] == -20_000
    assert row_arq1["notes"] == "Faltante $200"

    c.execute("SELECT canonical_hash FROM tesoreria_cierres WHERE journal_id = ?", (j1["id"],))
    assert c.fetchone()[0] == hash_j1_previo
    c.close()


# =====================================================================
# 10. USUARIO NO AUTORIZADO INTENTANDO REALIZAR ARQUEOS O CIERRES
# =====================================================================
def test_10_usuario_no_autorizado_rechazado_403_y_error(monkeypatch):
    """Prueba 10: Usuarios que no sean Alan o Yilda son rechazados tanto en API (403) como en backend (Error)."""
    db_uri = "file:mem_test_user_auth?mode=memory&cache=shared"
    init_conn = sqlite3.connect(db_uri, uri=True)
    init_conn.row_factory = sqlite3.Row
    init_conn.execute("PRAGMA foreign_keys = ON;")
    inicializar_esquema_laboratorio(init_conn)
    sembrar_catalogo_canónico(init_conn)
    abrir_jornada(init_conn, usuario="alan")
    init_conn.commit()

    def get_test_db():
        c = sqlite3.connect(db_uri, uri=True)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(tes_router, "_get_lab_db", get_test_db)
    client = TestClient(app)

    # Simular operador 'carlos'
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        username="carlos",
        display_name="Carlos Operador",
        role="operador",
    )

    # Intento de arqueo
    resp_arq = client.post("/api/tesoreria/arqueos", json={"codigo_fondo": "1110", "monto_verificado": 500.0, "observaciones": "test"})
    assert resp_arq.status_code == 403
    assert "Solo Alan y Yilda" in resp_arq.json()["detail"]

    # Intento de iniciar cierre
    resp_cierre = client.post("/api/tesoreria/cierres/iniciar", json={"journal_id": 1})
    assert resp_cierre.status_code == 403
    assert "Solo Alan y Yilda" in resp_cierre.json()["detail"]

    # Nivel API: verificación de que el usuario no administrador es rechazado con 403
    resp_sweep = client.post("/api/tesoreria/cierres/vaciado-1110", json={"journal_id": 1})
    assert resp_sweep.status_code == 403
    assert "Solo Alan y Yilda" in resp_sweep.json()["detail"]

    resp_confirm = client.post("/api/tesoreria/cierres/confirmar", json={"journal_id": 1})
    assert resp_confirm.status_code == 403
    assert "Solo Alan y Yilda" in resp_confirm.json()["detail"]

    resp_next = client.post("/api/tesoreria/jornada/siguiente", json={})
    assert resp_next.status_code == 403
    assert "Solo Alan y Yilda" in resp_next.json()["detail"]

    # Nivel funciones de backend para autorizaciones y modificaciones
    with pytest.raises(ErrorArqueo, match="no autorizado"):
        authorizeDifference(init_conn, reconciliation_id="dummy-id", user_id="carlos")

    with pytest.raises(ErrorArqueo, match="Solo Alan y Yilda"):
        updateReconciliationNotes(init_conn, reconciliation_id="dummy-id", new_notes="nota", user_id="carlos")

    app.dependency_overrides.clear()
    init_conn.close()


# =====================================================================
# 11. TRASLADOS PENDIENTES Y DEMÁS CONTROLES NO RELAJADOS
# =====================================================================
def test_11_traslados_pendientes_bloquean_cierre_controles_existentes(conn_lab):
    """Prueba 11: Controles existentes intactos: traslados en tránsito sin confirmar bloquean el cierre."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=1000_000, counterpart="Venta", reason="Fondeo", created_by="alan")
    j = obtener_jornada_actual(conn_lab)

    # Iniciar traslado ordinario 1110 -> 1120 pero NO confirmarlo
    transferFunds(
        conn=conn_lab,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=200_000,
        dispatched_by="alan",
        reason="Traslado ordinario en tránsito",
    )

    beginClosure(conn_lab, journal_id=j["id"], user_id="alan")
    val = validateClosure(conn_lab, journal_id=j["id"])
    assert val["can_close"] is False
    assert val["transfers_in_transit_count"] == 1
    assert any("traslados en tránsito" in b for b in val["blockers"])

    with pytest.raises(ErrorCierre, match="traslados en tránsito"):
        confirmClosure(conn_lab, journal_id=j["id"], user_id="alan")


# =====================================================================
# 12. REPETICIÓN DE SOLICITUDES: NO DUPLICAR MOVIMIENTOS
# =====================================================================
def test_12_repeticion_de_solicitudes_no_duplica_movimientos(conn_lab):
    """Prueba 12: Idempotencia: ejecutar executeCashDrawerSweep dos veces no duplica traslados ni movimientos."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=600_000, counterpart="Venta", reason="Fondeo", created_by="alan")
    j = obtener_jornada_actual(conn_lab)
    reconcilePhysicalFund(conn_lab, fund_code="1110", verified_amount_cents=580_000, user_id="alan", notes="Faltante $200")

    beginClosure(conn_lab, journal_id=j["id"], user_id="alan")

    sweep1 = executeCashDrawerSweep(conn_lab, journal_id=j["id"], user_id="alan")
    assert sweep1["swept"] is True
    assert sweep1["amount_cents"] == 580_000

    sweep2 = executeCashDrawerSweep(conn_lab, journal_id=j["id"], user_id="alan")
    assert sweep2["swept"] is False
    assert sweep2["amount_cents"] == 0

    c = conn_lab.cursor()
    c.execute("SELECT COUNT(*) FROM tesoreria_movimientos WHERE reason LIKE '%Vaciado%'")
    assert c.fetchone()[0] == 2  # Exactamente 2: 1 TRANSFER_OUT y 1 TRANSFER_IN
    c.close()


# =====================================================================
# EXTRA: TRAZABILIDAD ANTE MODIFICACIÓN DE OBSERVACIONES
# =====================================================================
def test_13_modificacion_observacion_preserva_trazabilidad(conn_lab):
    """Extra: Modificar una observación registra evento en auditoría y actualiza el expediente."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=600_000, counterpart="Venta", reason="Fondeo", created_by="alan")
    arq = reconcilePhysicalFund(
        conn_lab,
        fund_code="1110",
        verified_amount_cents=580_000,
        user_id="alan",
        notes="Observación original",
    )

    updated = updateReconciliationNotes(
        conn=conn_lab,
        reconciliation_id=arq["reconciliation_id"],
        new_notes="Observación corregida con mayor detalle",
        user_id="yilda",
    )
    assert updated["notes"] == "Observación corregida con mayor detalle"

    # Verificar registro en tesoreria_auditoria_eventos
    c = conn_lab.cursor()
    c.execute("SELECT payload_json FROM tesoreria_auditoria_eventos WHERE event_type = 'RECONCILIATION_NOTES_UPDATED'")
    row = c.fetchone()
    assert row is not None
    payload = json.loads(row[0])
    assert payload["old_notes"] == "Observación original"
    assert payload["new_notes"] == "Observación corregida con mayor detalle"
    assert payload["updated_by"] == "yilda"
    c.close()


# =====================================================================
# INTEGRIDAD DE PRODUCCIÓN (READ-ONLY)
# =====================================================================
def test_verificacion_integridad_produccion(hashes_bases_protegidas_iniciales):
    """Verifica que la base de datos de producción permanece bit a bit inalterada durante la sesión.

    El hash de referencia se captura al inicio de la sesión (tests/conftest.py).
    """
    prod_path = "/home/alanrod10/production/data/genaro-pos.db"
    expected_hash = hashes_bases_protegidas_iniciales["produccion_comercial"]

    hasher = hashlib.sha256()
    with open(prod_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    actual_hash = hasher.hexdigest()

    assert actual_hash == expected_hash, (
        f"VIOLACIÓN DE INTEGRIDAD EN PRODUCCIÓN: esperado {expected_hash}, obtenido {actual_hash}"
    )
