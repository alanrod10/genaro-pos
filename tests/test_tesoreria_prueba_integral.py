"""Prueba Integral Obligatoria — Sprint 2 (Sección 8).

Ejecuta el escenario sintético completo de 12 pasos:
1. Abrir jornada.
2. Ingresar $100.000 en 1110.
3. Ingresar $50.000 en 1130.
4. Trasladar $30.000 de 1110 a 1120.
5. Confirmar recepción.
6. Reservar $20.000 de 1130.
7. Consultar disponibilidad de los tres fondos.
8. Aplicar $5.000 de la reserva.
9. Liberar $5.000 de la reserva.
10. Reintentar una operación con la misma clave de idempotencia.
11. Comprobar que no se duplicó.
12. Verificar movimientos, saldos, reservas y auditoría.

Resultado esperado estricto:
- 1110: $70.000.
- 1120: $30.000.
- 1130: $50.000 registrados.
- 1130: $40.000 disponibles.
- Reserva 1130: $10.000 pendientes.
"""

from tesoreria.isolation import conectar_db_laboratorio
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.catalog import sembrar_catalogo_canónico, obtener_fondo_por_codigo
from tesoreria.jornadas import abrir_jornada, obtener_jornada_actual
from tesoreria.movements import registerEntry, listar_movimientos
from tesoreria.transfers import transferFunds, confirmTransferReceipt, listar_traslados
from tesoreria.reservations import (
    createReservation,
    applyReservation,
    releaseReservation,
    getReservationHistory,
)
from tesoreria.availability import getFundBalance, getTreasurySummary


def test_escenario_sintetico_integral_12_pasos(tmp_path):
    # Inicialización del entorno aislado
    db_file = tmp_path / "tesoreria_prueba_integral.db"
    conn = conectar_db_laboratorio(str(db_file))
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)

    # Paso 1: Abrir jornada
    jornada = abrir_jornada(conn, usuario="alan", observaciones="Prueba Integral Sprint 2")
    assert jornada["estado"] == "ABIERTA"
    assert jornada["numero_jornada"] == 1

    # Paso 2: Ingresar $100.000 en 1110
    entry_1110 = registerEntry(
        conn=conn,
        fund_code="1110",
        amount_cents=100000_00,  # 100.000,00 ARS en centavos
        counterpart="Aporte de capital laboratorio",
        reason="Fondeo inicial caja central",
        created_by="alan",
        idempotency_key="idemp_integral_entry_1110",
    )
    assert entry_1110["status"] == "POSTED"
    assert entry_1110["saldo_posterior_centavos"] == 100000_00

    # Paso 3: Ingresar $50.000 en 1130
    entry_1130 = registerEntry(
        conn=conn,
        fund_code="1130",
        amount_cents=50000_00,  # 50.000,00 ARS en centavos
        counterpart="Dotación caja fuerte",
        reason="Fondeo tesoro de seguridad",
        created_by="alan",
        idempotency_key="idemp_integral_entry_1130",
    )
    assert entry_1130["status"] == "POSTED"
    assert entry_1130["saldo_posterior_centavos"] == 50000_00

    # Paso 4: Trasladar $30.000 de 1110 a 1120
    trf = transferFunds(
        conn=conn,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=30000_00,
        dispatched_by="alan",
        reason="Dotación de cambio para caja auxiliar",
        idempotency_key="idemp_integral_trf_30000",
    )
    assert trf["status"] == "PENDING_RECEIPT"
    assert obtener_fondo_por_codigo(conn, "1110")["saldo_centavos"] == 70000_00
    assert obtener_fondo_por_codigo(conn, "1120")["saldo_centavos"] == 0  # En tránsito

    # Paso 5: Confirmar recepción
    conf = confirmTransferReceipt(
        conn=conn,
        transfer_id=trf["transfer_id"],
        received_by="yilda",
        idempotency_key="idemp_integral_conf_30000",
    )
    assert conf["status"] == "CONFIRMED"
    assert obtener_fondo_por_codigo(conn, "1120")["saldo_centavos"] == 30000_00

    # Paso 6: Reservar $20.000 de 1130
    res = createReservation(
        conn=conn,
        fund_code="1130",
        amount_cents=20000_00,
        reason="Reserva pago impuestos extraordinarios",
        beneficiary="AFIP / Rentas",
        obligation_id="OBL-INTEGRAL-01",
        created_by="alan",
        idempotency_key="idemp_integral_res_20000",
    )
    assert res["status"] == "ACTIVE"
    assert res["original_amount_cents"] == 20000_00
    assert res["pending_amount_cents"] == 20000_00
    rid = res["reservation_id"]

    # Paso 7: Consultar disponibilidad de los tres fondos
    bal_1110_p7 = getFundBalance(conn, "1110")
    bal_1120_p7 = getFundBalance(conn, "1120")
    bal_1130_p7 = getFundBalance(conn, "1130")

    assert bal_1110_p7["saldo_registrado_cents"] == 70000_00
    assert bal_1110_p7["saldo_disponible_cents"] == 70000_00

    assert bal_1120_p7["saldo_registrado_cents"] == 30000_00
    assert bal_1120_p7["saldo_disponible_cents"] == 30000_00

    assert bal_1130_p7["saldo_registrado_cents"] == 50000_00
    assert bal_1130_p7["saldo_reservado_cents"] == 20000_00
    assert bal_1130_p7["saldo_disponible_cents"] == 30000_00  # 50.000 - 20.000

    # Paso 8: Aplicar $5.000 de la reserva
    app_res = applyReservation(
        conn=conn,
        reservation_id=rid,
        amount_cents=5000_00,
        performed_by="alan",
        reason="Pago de anticipo de impuestos",
        idempotency_key="idemp_integral_app_5000",
    )
    assert app_res["applied_amount_cents"] == 5000_00
    assert app_res["pending_amount_cents"] == 15000_00
    assert app_res["status"] == "PARTIALLY_APPLIED"

    # Paso 9: Liberar $5.000 de la reserva
    rel_res = releaseReservation(
        conn=conn,
        reservation_id=rid,
        amount_cents=5000_00,
        performed_by="alan",
        reason="Exención fiscal concedida",
        idempotency_key="idemp_integral_rel_5000",
    )
    assert rel_res["released_amount_cents"] == 5000_00
    assert rel_res["pending_amount_cents"] == 10000_00
    assert rel_res["status"] == "PARTIALLY_APPLIED"

    # Paso 10: Reintentar una operación con la misma clave de idempotencia
    # Reintentamos la liberación con 'idemp_integral_rel_5000'
    rel_res_retry = releaseReservation(
        conn=conn,
        reservation_id=rid,
        amount_cents=5000_00,
        performed_by="alan",
        reason="Exención fiscal concedida",
        idempotency_key="idemp_integral_rel_5000",
    )

    # Paso 11: Comprobar que no se duplicó
    assert rel_res_retry["pending_amount_cents"] == 10000_00
    h_final = getReservationHistory(conn, rid)
    assert h_final["released_amount_cents"] == 5000_00  # NO subió a 10.000!
    assert h_final["pending_amount_cents"] == 10000_00

    # Paso 12: Verificar movimientos, saldos, reservas y auditoría finales
    bal_1110 = getFundBalance(conn, "1110")
    bal_1120 = getFundBalance(conn, "1120")
    bal_1130 = getFundBalance(conn, "1130")

    # RESULTADOS ESPERADOS EXACTOS:
    # 1110: $70.000
    assert bal_1110["saldo_registrado_cents"] == 70000_00
    assert bal_1110["saldo_disponible_cents"] == 70000_00

    # 1120: $30.000
    assert bal_1120["saldo_registrado_cents"] == 30000_00
    assert bal_1120["saldo_disponible_cents"] == 30000_00

    # 1130: $50.000 registrados
    assert bal_1130["saldo_registrado_cents"] == 50000_00

    # 1130: $40.000 disponibles (50.000 registrado - 10.000 reservado pendiente)
    assert bal_1130["saldo_disponible_cents"] == 40000_00

    # Reserva 1130: $10.000 pendientes
    assert bal_1130["saldo_reservado_cents"] == 10000_00
    assert h_final["pending_amount_cents"] == 10000_00

    # Invariante contable en la reserva: 20.000 = 5.000 (aplicado) + 5.000 (liberado) + 10.000 (pendiente)
    assert h_final["original_amount_cents"] == 20000_00
    assert h_final["applied_amount_cents"] == 5000_00
    assert h_final["released_amount_cents"] == 5000_00
    assert h_final["pending_amount_cents"] == 10000_00
    assert h_final["original_amount_cents"] == (
        h_final["applied_amount_cents"] + h_final["released_amount_cents"] + h_final["pending_amount_cents"]
    )

    # Verificar que las tablas de auditoría registraron todos los eventos
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM tesoreria_auditoria")
    total_auditoria = cursor.fetchone()[0]
    assert total_auditoria >= 6, f"Se esperaban al menos 6 eventos de auditoría, encontrados {total_auditoria}"
    cursor.close()

    conn.close()
