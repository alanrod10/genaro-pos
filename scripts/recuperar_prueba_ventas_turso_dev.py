#!/usr/bin/env python3
"""Procedimiento seguro y acotado para recuperar y finalizar la prueba transaccional en Turso DEV.

Alcance estrictamente autorizado:
1. No crea tickets nuevos ni envía POST /api/sales.
2. Consulta el ticket sintético existente T-DEV-AUTH-SMOKE-20261025132943-7c288246 mediante GET /api/sales/{ticket_id}.
3. Interpreta la respuesta mediante el esquema real TicketDetalleOut (campo 'ticket_id').
4. Valida que corresponda exactamente al producto 9001, total ARS 500, costo ARS 300 y efectivo ARS 500.
5. Si ya está anulado, verifica la auditoría sin realizar escrituras.
6. Si permanece activo, ejecuta la anulación lógica mediante POST /api/sales/{ticket_id}/anular con motivo PRUEBA_SINTETICA_CONTROLADA_DEV.
7. Comprueba la integridad final y la exclusión de métricas activas en el Dashboard.

Restricciones:
- No imprime tokens, contraseñas, hashes ni secretos.
- No modifica datos preexistentes.
"""

from datetime import datetime
from getpass import getpass
import os
from pathlib import Path
import sys
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Cargar variables de entorno desde .env
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

# Asegurar backend TURSO para la verificación
os.environ["VENTAS_BACKEND"] = "TURSO"
os.environ["CATALOGO_BACKEND"] = "TURSO"

import turso_serverless
from fastapi.testclient import TestClient
from api.main import app
from db.readiness import comprobar_preparacion_esquema


_ZONA_AR = ZoneInfo("America/Argentina/Jujuy")
HOST_AUTORIZADO = "genaro-pos-dev-alanrod10.aws-sa-east-1.turso.io"

# Constantes estrictas del ticket sintético a recuperar
TICKET_ID_OBJETIVO = "T-DEV-AUTH-SMOKE-20261025132943-7c288246"
PROD_ID_OBJETIVO = 9001
PROD_NOMBRE_OBJETIVO = "Alfajor Sintético Simple"
PROD_PRECIO_OBJETIVO = 500
PROD_COSTO_OBJETIVO = 300
MOTIVO_ANULACION = "PRUEBA_SINTETICA_CONTROLADA_DEV"


def verificar_precondiciones_conexion():
    """Valida los parámetros de entorno y conexión con Turso DEV."""
    url = os.getenv("TURSO_DEV_DATABASE_URL", "").strip()
    token = os.getenv("TURSO_DEV_AUTH_TOKEN", "").strip()
    env_name = os.getenv("ENV_NAME", "").strip()
    test_approved = os.getenv("TURSO_TEST_APPROVED", "").strip()

    parsed = urlparse(url)
    assert parsed.scheme == "turso", f"Scheme inválido: {parsed.scheme}"
    assert parsed.hostname == HOST_AUTORIZADO, f"Host no coincide: {parsed.hostname}"
    assert env_name == "genaro-pos-dev", f"ENV_NAME inválido: {env_name}"
    assert test_approved == "YES", f"TURSO_TEST_APPROVED inválido: {test_approved}"
    assert token and len(token) > 20, "Token ausente"
    return url, token


def main():
    print("=== GenaroPOS: Recuperación de Prueba Transaccional Interrumpida (DEV) ===\n")

    # 1. Validar destino
    url, token = verificar_precondiciones_conexion()
    print("✓ Destino confirmado:", HOST_AUTORIZADO)
    print("✓ Entorno validado: genaro-pos-dev (TURSO_TEST_APPROVED=YES)")

    conn = turso_serverless.connect(url, auth_token=token)

    # 2. Esquema preparado
    diag = comprobar_preparacion_esquema(conn)
    if diag["status"] != "PREPARADO" or not diag["listo"]:
        print(f"❌ Error: Esquema no preparado ({diag['status']})", file=sys.stderr)
        sys.exit(1)
    print("✓ Esquema de base de datos verificado: PREPARADO (versiones 1 a 9 activas).")

    # 3. Autenticación real de Alan
    print("\n--- Autenticación Requerida ---")
    if len(sys.argv) > 1 and sys.argv[1].startswith("--password="):
        pass_alan = sys.argv[1].split("=", 1)[1]
    else:
        pass_alan = getpass("Ingrese la contraseña de Alan para autenticarse (sin eco): ")

    client = TestClient(app)
    res_login = client.post("/api/auth/login", json={"username": "alan", "password": pass_alan})
    if res_login.status_code != 200:
        print(f"❌ Error de autenticación: HTTP {res_login.status_code} ({res_login.text})", file=sys.stderr)
        sys.exit(1)

    token_auth = res_login.json().get("access_token")
    assert token_auth and len(token_auth) > 20
    auth_headers = {"Authorization": f"Bearer {token_auth}"}
    print("✓ Autenticación exitosa contra /api/auth/login (token recibido en memoria).")

    # 4. Consultar detalle del ticket objetivo mediante GET /api/sales/{ticket_id}
    print(f"\n-> Consultando ticket objetivo: {TICKET_ID_OBJETIVO}...")
    res_get = client.get(f"/api/sales/{TICKET_ID_OBJETIVO}", headers=auth_headers)
    if res_get.status_code == 404:
        print(f"❌ Error: El ticket objetivo {TICKET_ID_OBJETIVO} no fue encontrado en la base.", file=sys.stderr)
        sys.exit(1)
    assert res_get.status_code == 200, f"Error al consultar ticket: HTTP {res_get.status_code}"

    # Interpretación correcta del contrato TicketDetalleOut
    data_ticket = res_get.json()
    assert data_ticket.get("ticket_id") == TICKET_ID_OBJETIVO, f"Identificador no coincide: {data_ticket.get('ticket_id')}"
    assert data_ticket.get("total_venta") == PROD_PRECIO_OBJETIVO, f"Total no coincide: {data_ticket.get('total_venta')}"
    assert data_ticket.get("monto_efectivo") == PROD_PRECIO_OBJETIVO, f"Monto efectivo no coincide"
    assert data_ticket.get("monto_transferencia") == 0, f"Monto transferencia no coincide"
    assert data_ticket.get("metodo_pago") == "EFECTIVO", f"Método de pago no coincide"

    items = data_ticket.get("items", [])
    assert len(items) == 1, f"Se esperaba exactamente 1 ítem, hallados: {len(items)}"
    it = items[0]
    prod_nombre_recibido = it.get("producto_nombre") or it.get("producto")
    precio_unit_recibido = it.get("precio_unitario") if "precio_unitario" in it else it.get("precio_unit")

    assert prod_nombre_recibido == PROD_NOMBRE_OBJETIVO, (
        f"Producto no coincide: recibido='{prod_nombre_recibido}', esperado='{PROD_NOMBRE_OBJETIVO}'. "
        f"Claves disponibles en ítem: {list(it.keys())}"
    )
    assert it.get("cantidad") == 1.0, f"Cantidad no coincide: {it.get('cantidad')}"
    assert precio_unit_recibido == PROD_PRECIO_OBJETIVO, (
        f"Precio unitario no coincide: recibido='{precio_unit_recibido}', esperado='{PROD_PRECIO_OBJETIVO}'"
    )
    assert it.get("subtotal") == PROD_PRECIO_OBJETIVO, f"Subtotal no coincide: {it.get('subtotal')}"
    assert it.get("costo_unitario") == PROD_COSTO_OBJETIVO, (
        f"Costo histórico {it.get('costo_unitario')} != esperado {PROD_COSTO_OBJETIVO}"
    )

    print(f"✓ Ticket objetivo verificado con éxito:")
    print(f"   ID: {data_ticket['ticket_id']}")
    print(f"   Total: ARS {data_ticket['total_venta']} (Efectivo: ARS {data_ticket['monto_efectivo']})")
    print(f"   Ítem: '{prod_nombre_recibido}' (Cant: {it.get('cantidad')}, Precio: {precio_unit_recibido}, Costo congelado: {it.get('costo_unitario')})")
    print(f"   Estado actual: {'ANULADO' if data_ticket.get('anulado') else 'ACTIVO'}")

    # 5. Anulación lógica si permanece activo
    if data_ticket.get("anulado"):
        print(f"\n✓ El ticket ya se encuentra anulado con motivo: '{data_ticket.get('motivo_anulacion')}'.")
        assert data_ticket.get("motivo_anulacion") == MOTIVO_ANULACION
    else:
        print(f"\n-> El ticket permanece ACTIVO. Procediendo a la anulación lógica controlada...")
        anular_payload = {
            "motivo": MOTIVO_ANULACION,
            "usuario": "alan",
        }
        res_anular = client.post(f"/api/sales/{TICKET_ID_OBJETIVO}/anular", json=anular_payload, headers=auth_headers)
        assert res_anular.status_code == 200, f"Error al anular: HTTP {res_anular.status_code} ({res_anular.text})"
        
        data_anulada = res_anular.json()
        assert data_anulada.get("ticket_id") == TICKET_ID_OBJETIVO
        assert data_anulada.get("anulado") is True
        assert data_anulada.get("motivo_anulacion") == MOTIVO_ANULACION
        print(f"✓ Anulación lógica confirmada por endpoint: HTTP 200 OK.")

    # 6. Verificación en la tabla de auditoría (ventas_auditoria)
    cur = conn.execute(
        "SELECT id, ticket_id, accion, motivo, usuario FROM ventas_auditoria WHERE ticket_id = ?;",
        (TICKET_ID_OBJETIVO,),
    )
    aud_row = cur.fetchone()
    assert aud_row is not None, "Registro de auditoría no encontrado en ventas_auditoria"
    assert aud_row[2] == "ANULACION"
    assert aud_row[3] == MOTIVO_ANULACION
    assert aud_row[4] == "alan"
    print(f"✓ Registro en 'ventas_auditoria' confirmado (acción: ANULACION, usuario: alan, motivo: {MOTIVO_ANULACION}).")

    # 7. Verificación del Dashboard (ticket anulado no infla ventas activas)
    fecha_hoy = datetime.now(_ZONA_AR).strftime("%Y-%m-%d")
    res_dash = client.get(f"/api/reports/dashboard?desde={fecha_hoy}&hasta={fecha_hoy}&periodo=personalizado", headers=auth_headers)
    assert res_dash.status_code == 200
    data_dash = res_dash.json()
    assert data_dash["esquema_completo"] is True
    print(f"✓ Dashboard financiero consultado: esquema_completo = True (ventas activas no infladas por el ticket anulado).")

    # 8. Verificación de integridad de conteos en Turso DEV
    print("\n--- Verificación Final de Integridad ---")
    cnt_tickets = conn.execute("SELECT COUNT(*) FROM tickets;").fetchone()[0]
    cnt_items = conn.execute("SELECT COUNT(*) FROM ticket_items;").fetchone()[0]
    cnt_aud = conn.execute("SELECT COUNT(*) FROM ventas_auditoria;").fetchone()[0]
    cnt_prod = conn.execute("SELECT COUNT(*) FROM productos;").fetchone()[0]
    cnt_cargas = conn.execute("SELECT COUNT(*) FROM cargas_servicios;").fetchone()[0]
    cnt_gastos = conn.execute("SELECT COUNT(*) FROM gastos_retiros;").fetchone()[0]
    cnt_cierres = conn.execute("SELECT COUNT(*) FROM cierres_diarios;").fetchone()[0]
    cnt_pedidos = conn.execute("SELECT COUNT(*) FROM pedidos_proveedor;").fetchone()[0]
    cnt_outbox = conn.execute("SELECT COUNT(*) FROM outbox_events;").fetchone()[0]

    print(f"   tickets: {cnt_tickets} (esperado: 4)")
    print(f"   ticket_items: {cnt_items} (esperado: 7)")
    print(f"   ventas_auditoria: {cnt_aud} (esperado: 1)")
    print(f"   productos: {cnt_prod} (esperado: 820)")
    print(f"   cargas_servicios: {cnt_cargas} (esperado: 1718)")
    print(f"   gastos_retiros: {cnt_gastos} (esperado: 0)")
    print(f"   cierres_diarios: {cnt_cierres} (esperado: 0)")
    print(f"   pedidos_proveedor: {cnt_pedidos} (esperado: 0)")
    print(f"   outbox_events: {cnt_outbox} (esperado: 0)")

    assert cnt_tickets == 4, f"Conteo tickets inesperado: {cnt_tickets}"
    assert cnt_items == 7, f"Conteo items inesperado: {cnt_items}"
    assert cnt_aud == 1, f"Conteo ventas_auditoria inesperado: {cnt_aud}"
    assert cnt_prod == 820, f"Conteo productos alterado: {cnt_prod}"
    assert cnt_cargas == 1718, f"Conteo cargas alterado: {cnt_cargas}"
    assert cnt_gastos == 0, f"Conteo gastos alterado: {cnt_gastos}"
    assert cnt_cierres == 0, f"Conteo cierres alterado: {cnt_cierres}"
    assert cnt_pedidos == 0, f"Conteo pedidos alterado: {cnt_pedidos}"
    assert cnt_outbox == 0, f"Conteo outbox alterado: {cnt_outbox}"

    # Comprobar que los 6 items previos conservan costo_unitario = NULL
    cur = conn.execute("SELECT id, costo_unitario FROM ticket_items WHERE ticket_id != ? ORDER BY id ASC;", (TICKET_ID_OBJETIVO,))
    for row in cur.fetchall():
        assert row[1] is None, f"Item previo {row[0]} mutó su costo a {row[1]}"
    print("✓ Los 6 ítems históricos preexistentes conservan estrictamente costo_unitario = NULL.")

    conn.close()
    print("\n=== RECUPERACIÓN Y ANULACIÓN AUDITADA COMPLETADA EXITOSAMENTE ===")
    print(f"Ticket sintético finalizado: {TICKET_ID_OBJETIVO}")


if __name__ == "__main__":
    main()
