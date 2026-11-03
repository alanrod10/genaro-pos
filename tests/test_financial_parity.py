"""Suite de paridad financiera cruzada entre Ventas, Servicios, Gastos, Arqueo y Dashboard (Sección 20).

Verifica sobre una misma base SQLite con las migraciones 001..010 aplicadas que:
- Historial de ventas (listar_ventas_turso)
- Resumen de servicios (calcular_resumen_cargas_turso)
- Resumen de gastos (calcular_resumen_gastos_turso)
- Arqueo diario de caja (calcular_arqueo_diario_turso)
- Dashboard financiero (calcular_metricas_dashboard_turso)
coincidan exactamente peso por peso ante ventas en efectivo, transferencia, mixtas,
nocturnas (22:30 hora argentina), anulaciones, servicios Claro/SUBE_MP/PREPAY y gastos.
"""

from pathlib import Path
import sqlite3

from db.expenses import (
    anular_gasto_turso,
    calcular_arqueo_diario_turso,
    calcular_resumen_gastos_turso,
    registrar_gasto_idempotente_turso,
)
from db.migrations_runner import ejecutar_migraciones
from db.reports import calcular_metricas_dashboard_turso
from db.sales import (
    anular_venta_turso,
    listar_ventas_turso,
    registrar_venta_turso,
)
from db.services import (
    anular_carga_individual_turso,
    calcular_resumen_cargas_turso,
    registrar_carga_idempotente_turso,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "db/migrations"


def _crear_bd_migrada() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute(
        """
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL COLLATE NOCASE,
            categoria TEXT NOT NULL DEFAULT 'Kiosco',
            proveedor TEXT NOT NULL DEFAULT 'General',
            unidad TEXT NOT NULL DEFAULT 'Unidad',
            costo INTEGER NOT NULL DEFAULT 500 CHECK (costo >= 0),
            precio_dia INTEGER NOT NULL DEFAULT 1000 CHECK (precio_dia >= 0),
            precio_noche INTEGER NOT NULL DEFAULT 1000,
            margen_ratio REAL NOT NULL DEFAULT 1.0,
            activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
            fecha_actualizacion TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute(
        """
        INSERT INTO productos (id, nombre, costo, precio_dia, precio_noche, margen_ratio, activo)
        VALUES
            (1, 'Gaseosa Cola 2L', 1200, 2000, 2000, 0.6667, 1),
            (2, 'Alfajor Triple', 500, 1000, 1000, 1.0, 1);
        """
    )
    ejecutar_migraciones(conn, directorio=MIGRATIONS_DIR)
    return conn


def test_paridad_financiera_cruzada_completa():
    """Comprueba que Ventas, Servicios, Gastos, Arqueo y Dashboard produzcan exactamente
    los mismos totales sobre el mismo conjunto de operaciones del día local argentino."""
    conn = _crear_bd_migrada()
    try:
        fecha_ar = "2026-10-25"

        # 1. Ventas del 25/10/2026 (incluyendo una venta nocturna a las 22:30 AR = 01:30 UTC del 26)
        # V1: Efectivo $4.000
        registrar_venta_turso(
            {
                "TICKET_ID": "T-PAR-EFVO",
                "FECHA": "25/10/2026 10:15:00",
                "TOTAL_VENTA": 4000,
                "MONTO_EFECTIVO": 4000,
                "MONTO_TRANSF": 0,
                "METODO_PAGO": "EFECTIVO",
            },
            [
                {
                    "TICKET_ID": "T-PAR-EFVO",
                    "FECHA": "25/10/2026 10:15:00",
                    "PRODUCTO": "Gaseosa Cola 2L",
                    "CANTIDAD": 2,
                    "UNIDAD": "Unidad",
                    "PRECIO_UNIT": 2000,
                    "SUBTOTAL": 4000,
                    "METODO_PAGO": "EFECTIVO",
                }
            ],
            usuario="alan",
            conn=conn,
        )

        # V2: Transferencia $3.000
        registrar_venta_turso(
            {
                "TICKET_ID": "T-PAR-TRANSF",
                "FECHA": "25/10/2026 14:20:00",
                "TOTAL_VENTA": 3000,
                "MONTO_EFECTIVO": 0,
                "MONTO_TRANSF": 3000,
                "METODO_PAGO": "TRANSFERENCIA",
            },
            [
                {
                    "TICKET_ID": "T-PAR-TRANSF",
                    "FECHA": "25/10/2026 14:20:00",
                    "PRODUCTO": "Alfajor Triple",
                    "CANTIDAD": 3,
                    "UNIDAD": "Unidad",
                    "PRECIO_UNIT": 1000,
                    "SUBTOTAL": 3000,
                    "METODO_PAGO": "TRANSFERENCIA",
                }
            ],
            usuario="yilda",
            conn=conn,
        )

        # V3: Mixta $5.000 ($2.000 efvo + $3.000 transf)
        registrar_venta_turso(
            {
                "TICKET_ID": "T-PAR-MIXTO",
                "FECHA": "25/10/2026 18:45:00",
                "TOTAL_VENTA": 5000,
                "MONTO_EFECTIVO": 2000,
                "MONTO_TRANSF": 3000,
                "METODO_PAGO": "MIXTO",
            },
            [
                {
                    "TICKET_ID": "T-PAR-MIXTO",
                    "FECHA": "25/10/2026 18:45:00",
                    "PRODUCTO": "Gaseosa Cola 2L",
                    "CANTIDAD": 2,
                    "UNIDAD": "Unidad",
                    "PRECIO_UNIT": 2000,
                    "SUBTOTAL": 4000,
                    "METODO_PAGO": "MIXTO",
                },
                {
                    "TICKET_ID": "T-PAR-MIXTO",
                    "FECHA": "25/10/2026 18:45:00",
                    "PRODUCTO": "Alfajor Triple",
                    "CANTIDAD": 1,
                    "UNIDAD": "Unidad",
                    "PRECIO_UNIT": 1000,
                    "SUBTOTAL": 1000,
                    "METODO_PAGO": "MIXTO",
                },
            ],
            usuario="alan",
            conn=conn,
        )

        # V4: Nocturna 22:30 AR ($2.000 efvo -> en UTC es 2026-10-26 01:30:00)
        registrar_venta_turso(
            {
                "TICKET_ID": "T-PAR-NOCHE",
                "FECHA": "25/10/2026 22:30:00",
                "TOTAL_VENTA": 2000,
                "MONTO_EFECTIVO": 2000,
                "MONTO_TRANSF": 0,
                "METODO_PAGO": "EFECTIVO",
            },
            [
                {
                    "TICKET_ID": "T-PAR-NOCHE",
                    "FECHA": "25/10/2026 22:30:00",
                    "PRODUCTO": "Gaseosa Cola 2L",
                    "CANTIDAD": 1,
                    "UNIDAD": "Unidad",
                    "PRECIO_UNIT": 2000,
                    "SUBTOTAL": 2000,
                    "METODO_PAGO": "EFECTIVO",
                }
            ],
            usuario="yilda",
            conn=conn,
        )

        # V5: Venta luego anulada ($15.000 efvo -> debe quedar excluida de todos los módulos)
        registrar_venta_turso(
            {
                "TICKET_ID": "T-PAR-ANUL",
                "FECHA": "25/10/2026 12:00:00",
                "TOTAL_VENTA": 6000,
                "MONTO_EFECTIVO": 6000,
                "MONTO_TRANSF": 0,
                "METODO_PAGO": "EFECTIVO",
            },
            [
                {
                    "TICKET_ID": "T-PAR-ANUL",
                    "FECHA": "25/10/2026 12:00:00",
                    "PRODUCTO": "Gaseosa Cola 2L",
                    "CANTIDAD": 3,
                    "UNIDAD": "Unidad",
                    "PRECIO_UNIT": 2000,
                    "SUBTOTAL": 6000,
                    "METODO_PAGO": "EFECTIVO",
                }
            ],
            usuario="alan",
            conn=conn,
        )
        anular_venta_turso("T-PAR-ANUL", "Error de cobro", usuario="alan", conn=conn)

        # 2. Servicios del 25/10/2026 (con distintas variantes de nombres)
        # S1: Claro ($2.000 + $200 = $2.200 efvo)
        registrar_carga_idempotente_turso(
            {
                "FECHA": "25/10/2026 11:00:00",
                "SERVICIO": "Claro Recarga",
                "MONTO_CARGA": 2000,
                "MONTO_ADICIONAL": 200,
                "TOTAL_COBRADO": 2200,
                "PAGO_EFVO": 2200,
                "PAGO_TRANSF": 0,
            },
            idempotency_key="SRV-PAR-1",
            actor="alan",
            conn=conn,
        )
        # S2: SUBE (MP) ($3.000 + $100 = $3.100 transf)
        registrar_carga_idempotente_turso(
            {
                "FECHA": "25/10/2026 13:00:00",
                "SERVICIO": "SUBE (MP)",
                "MONTO_CARGA": 3000,
                "MONTO_ADICIONAL": 100,
                "TOTAL_COBRADO": 3100,
                "PAGO_EFVO": 0,
                "PAGO_TRANSF": 3100,
            },
            idempotency_key="SRV-PAR-2",
            actor="yilda",
            conn=conn,
        )
        # S3: SUBE MercadoPago ($1.000 + $100 = $1.100 efvo)
        registrar_carga_idempotente_turso(
            {
                "FECHA": "25/10/2026 16:00:00",
                "SERVICIO": "SUBE MercadoPago",
                "MONTO_CARGA": 1000,
                "MONTO_ADICIONAL": 100,
                "TOTAL_COBRADO": 1100,
                "PAGO_EFVO": 1100,
                "PAGO_TRANSF": 0,
            },
            idempotency_key="SRV-PAR-3",
            actor="alan",
            conn=conn,
        )
        # S4: Personal Prepay ($1.500 + $100 = $1.600 transf)
        registrar_carga_idempotente_turso(
            {
                "FECHA": "25/10/2026 22:40:00",
                "SERVICIO": "Personal",
                "MONTO_CARGA": 1500,
                "MONTO_ADICIONAL": 100,
                "TOTAL_COBRADO": 1600,
                "PAGO_EFVO": 0,
                "PAGO_TRANSF": 1600,
            },
            idempotency_key="SRV-PAR-4",
            actor="yilda",
            conn=conn,
        )
        # S5: Servicio luego anulado
        id_srv_anul, _ = registrar_carga_idempotente_turso(
            {
                "FECHA": "25/10/2026 17:00:00",
                "SERVICIO": "Claro",
                "MONTO_CARGA": 9000,
                "MONTO_ADICIONAL": 500,
                "TOTAL_COBRADO": 9500,
                "PAGO_EFVO": 9500,
                "PAGO_TRANSF": 0,
            },
            idempotency_key="SRV-PAR-ANUL",
            actor="alan",
            conn=conn,
        )
        anular_carga_individual_turso(id_srv_anul, actor="alan", conn=conn)

        # 3. Gastos del 25/10/2026
        # G1: Efectivo $1.200
        registrar_gasto_idempotente_turso(
            {
                "FECHA": "25/10/2026 12:30:00",
                "TIPO": "Limpieza",
                "DETALLE": "Bolsas y desinfectante",
                "MONTO_SALIDA": 1200,
                "ORIGEN_FONDOS": "Caja diaria",
                "METODO_PAGO": "EFECTIVO",
            },
            idempotency_key="GAS-PAR-1",
            actor="alan",
            conn=conn,
        )
        # G2: Transferencia $800
        registrar_gasto_idempotente_turso(
            {
                "FECHA": "25/10/2026 19:10:00",
                "TIPO": "Proveedor",
                "DETALLE": "Flete",
                "MONTO_SALIDA": 800,
                "ORIGEN_FONDOS": "Banco",
                "METODO_PAGO": "TRANSFERENCIA",
            },
            idempotency_key="GAS-PAR-2",
            actor="yilda",
            conn=conn,
        )
        # G3: Gasto luego anulado
        id_gas_anul, _ = registrar_gasto_idempotente_turso(
            {
                "FECHA": "25/10/2026 20:00:00",
                "TIPO": "Retiro",
                "DETALLE": "Retiro anulado",
                "MONTO_SALIDA": 5000,
                "ORIGEN_FONDOS": "Caja diaria",
                "METODO_PAGO": "EFECTIVO",
            },
            idempotency_key="GAS-PAR-ANUL",
            actor="alan",
            conn=conn,
        )
        anular_gasto_turso(id_gas_anul, actor="alan", conn=conn)

        # ======================================================================
        # CONSULTA DE LOS 5 MÓDULOS SOBRE LA MISMA FECHA LOCAL (2026-10-25)
        # ======================================================================
        ventas_lista = listar_ventas_turso(
            fecha=fecha_ar, incluir_anuladas=False, conn=conn
        )
        res_servicios = calcular_resumen_cargas_turso(
            fecha_filtro=fecha_ar, conn=conn
        )
        res_gastos = calcular_resumen_gastos_turso(
            fecha_filtro=fecha_ar, conn=conn
        )
        arqueo = calcular_arqueo_diario_turso(
            fecha_objetivo=fecha_ar, conn=conn
        )
        dashboard = calcular_metricas_dashboard_turso(
            desde_param=fecha_ar,
            hasta_param=fecha_ar,
            periodo_tipo="personalizado",
            conn=conn,
        )

        # 1. Paridad de Ventas (Efectivo: 4000 + 2000 + 2000 = 8000 | Transf: 3000 + 3000 = 6000 | Total: 14000)
        assert len(ventas_lista) == 4
        sum_v_efvo = sum(v["monto_efectivo"] for v in ventas_lista)
        sum_v_transf = sum(v["monto_transferencia"] for v in ventas_lista)
        sum_v_total = sum(v["total_venta"] for v in ventas_lista)

        assert sum_v_efvo == 8000
        assert sum_v_transf == 6000
        assert sum_v_total == 14000

        assert arqueo["ventas_efvo"] == sum_v_efvo == dashboard["caja_a"]["ventas_efvo"]
        assert arqueo["ventas_transf"] == sum_v_transf == dashboard["caja_a"]["ventas_transf"]
        assert arqueo["total_ventas"] == sum_v_total == dashboard["caja_a"]["total_ventas"]
        assert (
            arqueo["efectivo_disponible_drugstore"]
            == sum_v_efvo
            == dashboard["caja_a"]["efectivo_disponible_drugstore"]
        )

        # 2. Paridad de Servicios
        # Claro capital: 2000 | SUBE_MP capital: 3000 + 1000 = 4000 | Prepay capital: 1500
        # Adicionales: 200 + 100 + 100 + 100 = 500
        # Servicios efvo: 2200 + 1100 = 3300 | Servicios transf (d_recargas): 3100 + 1600 = 4700
        assert res_servicios["capital_claro"] == 2000
        assert res_servicios["capital_sube_mp"] == 4000
        assert res_servicios["capital_prepay"] == 1500
        assert res_servicios["total_adicional"] == 500
        assert res_servicios["total_efectivo"] == 3300
        assert res_servicios["total_transferencia"] == 4700
        assert res_servicios["total_cobrado"] == 8000

        assert arqueo["capital_claro"] == res_servicios["capital_claro"] == dashboard["caja_e"]["capital_total"]
        assert arqueo["capital_sube_mp"] == res_servicios["capital_sube_mp"] == dashboard["caja_d"]["sube_mp_capital"]
        assert arqueo["capital_prepay"] == res_servicios["capital_prepay"] == dashboard["caja_b"]["capital_total"]
        assert (
            arqueo["adicionales_ganancia"]
            == res_servicios["total_adicional"]
            == dashboard["caja_c"]["ganancia_adicional_total"]
        )
        assert (
            arqueo["servicios_efvo"]
            == res_servicios["total_efectivo"]
            == dashboard["conciliacion_cajon"]["servicios_efvo"]
        )
        assert (
            arqueo["d_recargas"]
            == res_servicios["total_transferencia"]
            == dashboard["caja_d"]["recargas_transf"]
        )

        # 3. Paridad de Gastos (Efectivo: 1200 | Transferencia: 800 | Total: 2000)
        assert res_gastos["gastos_efvo"] == 1200
        assert res_gastos["gastos_transf"] == 800
        assert res_gastos["gastos_total"] == 2000

        assert arqueo["gastos_efvo"] == res_gastos["gastos_efvo"] == dashboard["gastos"]["gastos_efvo"]
        assert arqueo["gastos_transf"] == res_gastos["gastos_transf"] == dashboard["gastos"]["gastos_transf"]
        assert arqueo["gastos_total"] == res_gastos["gastos_total"] == dashboard["gastos"]["gastos_total"]

        # 4. Paridad de Cajón Físico y Banco Neto
        # Efectivo esperado = 8000 (ventas_efvo) + 3300 (servicios_efvo) - 1200 (gastos_efvo) = 10100
        assert arqueo["efectivo_esperado"] == 10100
        assert dashboard["conciliacion_cajon"]["efectivo_esperado_cajon"] == 10100

        # Total banco = 6000 (ventas_transf) + 4700 (d_recargas) - 4000 (sube_mp_capital) - 800 (gastos_transf) = 5900
        assert arqueo["total_banco"] == 5900
        assert dashboard["caja_d"]["total_banco_neto"] == 5900
    finally:
        conn.close()


def test_independencia_caja_a_vs_efectivo_esperado_cajon_ante_gastos_y_anulaciones():
    """Verifica el invariante contable entre Caja A y el efectivo físico esperado del cajón:

    Escenario de referencia:
    - Ventas activas en efectivo: $4.600 ($2.000 efvo + $1.600 parte efvo de mixto + $1.000 efvo nocturno 22:30 AR).
    - Ventas activas en transferencia: $3.400 ($1.000 transf + $2.400 parte transf de mixto).
    - Venta anulada en efectivo: $6.000 (excluida).
    - Servicios activos cobrados en efectivo: $4.300 (Claro $2.100 efvo + Personal $2.200 parte efvo de mixto).
    - Servicios activos cobrados en transferencia: $5.150 (SUBE MP $3.150 transf + Personal $2.000 parte transf de mixto).
    - Servicio anulado en efectivo: $1.100 (excluido).
    - Gastos activos en efectivo: $1.500 (reduce el efectivo esperado del cajón, NUNCA se resta de Caja A).
    - Gastos activos en transferencia: $800 (reduce saldo bancario neto Caja D).
    - Gasto anulado en efectivo: $9.000 (excluido).

    Resultados obligatorios en Historial, Arqueo y Dashboard:
    - Caja A -> ventas_efvo == $4.600
    - Caja A -> efectivo_disponible_drugstore == $4.600 (NUNCA $3.100; los gastos en efectivo no se restan de Caja A)
    - Conciliación Cajón -> servicios_efvo == $4.300
    - Conciliación Cajón -> gastos_efvo == $1.500
    - Conciliación Cajón -> efectivo_esperado / efectivo_esperado_cajon == $7.400 ($4.600 + $4.300 - $1.500)
    - Caja B (Capital PREPAY) == $4.000 (Personal $4.000; SUBE MP $3.000 excluida)
    - Caja C (Beneficio Servicios) == $450 ($100 Claro + $150 SUBE MP + $200 Personal)
    - Caja D (Saldo Bancario Neto) == $4.750 ($3.400 ventas_transf + $5.150 d_recargas - $3.000 sube_mp_capital - $800 gastos_transf)
    - Caja E (Capital Claro) == $2.000
    """
    conn = _crear_bd_migrada()
    try:
        fecha_ar = "2026-10-25"

        # 1. Ventas del 25/10/2026 -> ventas_efvo = 2000 + 1600 + 1000 = 4600; ventas_transf = 1000 + 2400 = 3400
        registrar_venta_turso(
            {
                "TICKET_ID": "T-4600-EFVO",
                "FECHA": "25/10/2026 10:15:00",
                "TOTAL_VENTA": 2000,
                "MONTO_EFECTIVO": 2000,
                "MONTO_TRANSF": 0,
                "METODO_PAGO": "EFECTIVO",
            },
            [
                {
                    "TICKET_ID": "T-4600-EFVO",
                    "FECHA": "25/10/2026 10:15:00",
                    "PRODUCTO": "Gaseosa Cola 2L",
                    "CANTIDAD": 1,
                    "UNIDAD": "Unidad",
                    "PRECIO_UNIT": 2000,
                    "SUBTOTAL": 2000,
                    "METODO_PAGO": "EFECTIVO",
                }
            ],
            usuario="alan",
            conn=conn,
        )

        registrar_venta_turso(
            {
                "TICKET_ID": "T-4600-TRANSF",
                "FECHA": "25/10/2026 12:15:00",
                "TOTAL_VENTA": 1000,
                "MONTO_EFECTIVO": 0,
                "MONTO_TRANSF": 1000,
                "METODO_PAGO": "TRANSFERENCIA",
            },
            [
                {
                    "TICKET_ID": "T-4600-TRANSF",
                    "FECHA": "25/10/2026 12:15:00",
                    "PRODUCTO": "Alfajor Triple",
                    "CANTIDAD": 1,
                    "UNIDAD": "Unidad",
                    "PRECIO_UNIT": 1000,
                    "SUBTOTAL": 1000,
                    "METODO_PAGO": "TRANSFERENCIA",
                }
            ],
            usuario="yilda",
            conn=conn,
        )

        registrar_venta_turso(
            {
                "TICKET_ID": "T-4600-MIXTO",
                "FECHA": "25/10/2026 16:30:00",
                "TOTAL_VENTA": 4000,
                "MONTO_EFECTIVO": 1600,
                "MONTO_TRANSF": 2400,
                "METODO_PAGO": "MIXTO",
            },
            [
                {
                    "TICKET_ID": "T-4600-MIXTO",
                    "FECHA": "25/10/2026 16:30:00",
                    "PRODUCTO": "Gaseosa Cola 2L",
                    "CANTIDAD": 2,
                    "UNIDAD": "Unidad",
                    "PRECIO_UNIT": 2000,
                    "SUBTOTAL": 4000,
                    "METODO_PAGO": "MIXTO",
                }
            ],
            usuario="alan",
            conn=conn,
        )

        registrar_venta_turso(
            {
                "TICKET_ID": "T-4600-NOCHE",
                "FECHA": "25/10/2026 22:30:00",
                "TOTAL_VENTA": 1000,
                "MONTO_EFECTIVO": 1000,
                "MONTO_TRANSF": 0,
                "METODO_PAGO": "EFECTIVO",
            },
            [
                {
                    "TICKET_ID": "T-4600-NOCHE",
                    "FECHA": "25/10/2026 22:30:00",
                    "PRODUCTO": "Alfajor Triple",
                    "CANTIDAD": 1,
                    "UNIDAD": "Unidad",
                    "PRECIO_UNIT": 1000,
                    "SUBTOTAL": 1000,
                    "METODO_PAGO": "EFECTIVO",
                }
            ],
            usuario="yilda",
            conn=conn,
        )

        registrar_venta_turso(
            {
                "TICKET_ID": "T-4600-ANUL",
                "FECHA": "25/10/2026 18:00:00",
                "TOTAL_VENTA": 6000,
                "MONTO_EFECTIVO": 6000,
                "MONTO_TRANSF": 0,
                "METODO_PAGO": "EFECTIVO",
            },
            [
                {
                    "TICKET_ID": "T-4600-ANUL",
                    "FECHA": "25/10/2026 18:00:00",
                    "PRODUCTO": "Gaseosa Cola 2L",
                    "CANTIDAD": 3,
                    "UNIDAD": "Unidad",
                    "PRECIO_UNIT": 2000,
                    "SUBTOTAL": 6000,
                    "METODO_PAGO": "EFECTIVO",
                }
            ],
            usuario="alan",
            conn=conn,
        )
        anular_venta_turso("T-4600-ANUL", "Error de cobro", usuario="alan", conn=conn)

        # 2. Servicios del 25/10/2026 -> servicios_efvo = 2100 + 2200 = 4300; d_recargas = 3150 + 2000 = 5150
        registrar_carga_idempotente_turso(
            {
                "FECHA": "25/10/2026 11:00:00",
                "SERVICIO": "Claro",
                "MONTO_CARGA": 2000,
                "MONTO_ADICIONAL": 100,
                "TOTAL_COBRADO": 2100,
                "PAGO_EFVO": 2100,
                "PAGO_TRANSF": 0,
            },
            idempotency_key="SRV-4600-1",
            actor="alan",
            conn=conn,
        )
        registrar_carga_idempotente_turso(
            {
                "FECHA": "25/10/2026 13:00:00",
                "SERVICIO": "SUBE (MP)",
                "MONTO_CARGA": 3000,
                "MONTO_ADICIONAL": 150,
                "TOTAL_COBRADO": 3150,
                "PAGO_EFVO": 0,
                "PAGO_TRANSF": 3150,
            },
            idempotency_key="SRV-4600-2",
            actor="yilda",
            conn=conn,
        )
        registrar_carga_idempotente_turso(
            {
                "FECHA": "25/10/2026 15:00:00",
                "SERVICIO": "Personal",
                "MONTO_CARGA": 4000,
                "MONTO_ADICIONAL": 200,
                "TOTAL_COBRADO": 4200,
                "PAGO_EFVO": 2200,
                "PAGO_TRANSF": 2000,
            },
            idempotency_key="SRV-4600-3",
            actor="alan",
            conn=conn,
        )
        id_srv_anul, _ = registrar_carga_idempotente_turso(
            {
                "FECHA": "25/10/2026 17:00:00",
                "SERVICIO": "Movistar",
                "MONTO_CARGA": 1000,
                "MONTO_ADICIONAL": 100,
                "TOTAL_COBRADO": 1100,
                "PAGO_EFVO": 1100,
                "PAGO_TRANSF": 0,
            },
            idempotency_key="SRV-4600-ANUL",
            actor="alan",
            conn=conn,
        )
        anular_carga_individual_turso(id_srv_anul, actor="alan", conn=conn)

        # 3. Gastos del 25/10/2026 -> gastos_efvo = 1500; gastos_transf = 800
        registrar_gasto_idempotente_turso(
            {
                "FECHA": "25/10/2026 18:30:00",
                "TIPO": "Limpieza",
                "DETALLE": "Insumos bolsas",
                "MONTO_SALIDA": 1500,
                "ORIGEN_FONDOS": "Caja diaria",
                "METODO_PAGO": "EFECTIVO",
            },
            idempotency_key="GAS-4600-EFVO",
            actor="alan",
            conn=conn,
        )
        registrar_gasto_idempotente_turso(
            {
                "FECHA": "25/10/2026 19:30:00",
                "TIPO": "Proveedor",
                "DETALLE": "Comisión proveedor",
                "MONTO_SALIDA": 800,
                "ORIGEN_FONDOS": "Banco",
                "METODO_PAGO": "TRANSFERENCIA",
            },
            idempotency_key="GAS-4600-TRANSF",
            actor="yilda",
            conn=conn,
        )
        id_gas_anul, _ = registrar_gasto_idempotente_turso(
            {
                "FECHA": "25/10/2026 20:00:00",
                "TIPO": "Retiro",
                "DETALLE": "Gasto anulado",
                "MONTO_SALIDA": 9000,
                "ORIGEN_FONDOS": "Caja diaria",
                "METODO_PAGO": "EFECTIVO",
            },
            idempotency_key="GAS-4600-ANUL",
            actor="alan",
            conn=conn,
        )
        anular_gasto_turso(id_gas_anul, actor="alan", conn=conn)

        # Consultas sobre 2026-10-25
        ventas_lista = listar_ventas_turso(
            fecha=fecha_ar, incluir_anuladas=False, conn=conn
        )
        res_servicios = calcular_resumen_cargas_turso(
            fecha_filtro=fecha_ar, conn=conn
        )
        res_gastos = calcular_resumen_gastos_turso(
            fecha_filtro=fecha_ar, conn=conn
        )
        arqueo = calcular_arqueo_diario_turso(
            fecha_objetivo=fecha_ar, conn=conn
        )
        dashboard = calcular_metricas_dashboard_turso(
            desde_param=fecha_ar,
            hasta_param=fecha_ar,
            periodo_tipo="personalizado",
            conn=conn,
        )

        # INVARIANTE 1: Caja A (ventas en efectivo de la despensa y efectivo disponible de la despensa)
        # Debe ser exactamente $4.600 ($2.000 + $1.600 + $1.000).
        # Los gastos en efectivo ($1.500) NUNCA se restan de Caja A (NUNCA da $3.100).
        sum_v_efvo = sum(v["monto_efectivo"] for v in ventas_lista)
        sum_v_transf = sum(v["monto_transferencia"] for v in ventas_lista)
        assert sum_v_efvo == 4600
        assert sum_v_transf == 3400
        assert arqueo["ventas_efvo"] == 4600
        assert arqueo["efectivo_disponible_drugstore"] == 4600
        assert dashboard["caja_a"]["ventas_efvo"] == 4600
        assert dashboard["caja_a"]["efectivo_disponible_drugstore"] == 4600
        assert dashboard["conciliacion_cajon"]["ventas_efvo"] == 4600

        # INVARIANTE 2: Efectivo esperado del cajón físico
        # Fórmula: ventas_efvo ($4.600) + servicios_efvo ($4.300) - gastos_efvo ($1.500) = $7.400
        assert res_servicios["total_efectivo"] == 4300
        assert res_gastos["gastos_efvo"] == 1500
        assert arqueo["servicios_efvo"] == 4300
        assert arqueo["gastos_efvo"] == 1500
        assert arqueo["efectivo_esperado"] == 7400
        assert dashboard["conciliacion_cajon"]["servicios_efvo"] == 4300
        assert dashboard["conciliacion_cajon"]["gastos_efvo"] == 1500
        assert dashboard["conciliacion_cajon"]["efectivo_esperado_cajon"] == 7400

        # INVARIANTE 3: Coherencia del resto de cajas (B, C, D, E) y exclusión de anulados
        assert arqueo["ventas_transf"] == 3400 == dashboard["caja_a"]["ventas_transf"]
        assert arqueo["total_ventas"] == 8000 == dashboard["caja_a"]["total_ventas"]
        assert arqueo["capital_prepay"] == 4000 == dashboard["caja_b"]["capital_total"]
        assert arqueo["adicionales_ganancia"] == 450 == dashboard["caja_c"]["ganancia_adicional_total"]
        assert arqueo["d_recargas"] == 5150 == dashboard["caja_d"]["recargas_transf"]
        assert arqueo["capital_sube_mp"] == 3000 == dashboard["caja_d"]["sube_mp_capital"]
        assert arqueo["gastos_transf"] == 800 == dashboard["gastos"]["gastos_transf"]

        # Verificación explícita de la fórmula contable de Caja D (Banco Neto):
        # total_banco = ventas_transf (3.400) + d_recargas (5.150) - capital_sube_mp (3.000) - gastos_transf (800) = 4.750
        calculo_esperado_caja_d = (
            arqueo["ventas_transf"]
            + arqueo["d_recargas"]
            - arqueo["capital_sube_mp"]
            - arqueo["gastos_transf"]
        )
        assert calculo_esperado_caja_d == 4750
        assert arqueo["total_banco"] == 4750 == dashboard["caja_d"]["total_banco_neto"]
        assert arqueo["capital_claro"] == 2000 == dashboard["caja_e"]["capital_total"]
    finally:
        conn.close()

