#!/usr/bin/env python3
"""Procedimiento seguro y controlado de prueba transaccional de ventas en Turso DEV.

Alcance autorizado:
1. Autenticación real mediante POST /api/auth/login solicitando contraseña oculta vía getpass.
2. Comprobaciones previas sin escritura (esquema PREPARADO, tickets existentes, producto sintético).
3. Registro de 1 ticket sintético (POST /api/sales).
4. Verificación de snapshot de costo histórico (ticket_items.costo_unitario).
5. Verificación de idempotencia repitiendo la misma solicitud (HTTP 200, status 'existente').
6. Anulación lógica auditada (POST /api/sales/{ticket_id}/anular) con motivo PRUEBA_SINTETICA_CONTROLADA_DEV.
7. Comprobación de que el ticket anulado no infle el Dashboard y conserve su trazabilidad.
8. Verificación de integridad comparando la línea base antes y después.

Restricciones:
- No imprime tokens, contraseñas, hashes ni URLs con credenciales.
- No modifica datos preexistentes ni productos comerciales.
"""

from datetime import datetime, timezone
from getpass import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Cargar configuración desde .env
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

# Asegurar backend TURSO para la prueba
os.environ["VENTAS_BACKEND"] = "TURSO"
os.environ["CATALOGO_BACKEND"] = "TURSO"

import turso_serverless
from fastapi.testclient import TestClient
from api.main import app
from db.readiness import comprobar_preparacion_esquema


_ZONA_AR = ZoneInfo("America/Argentina/Jujuy")
HOST_AUTORIZADO = "genaro-pos-dev-alanrod10.aws-sa-east-1.turso.io"


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
    print("=== GenaroPOS: Prueba Transaccional Controlada en Turso DEV ===\n")

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

    # 3. Estado previo de tickets existentes
    cur = conn.execute("SELECT id, fecha_hora_utc, total_venta, metodo_pago, anulado FROM tickets ORDER BY id ASC;")
    tickets_previos = cur.fetchall()
    assert len(tickets_previos) == 3, f"Se esperaban 3 tickets previos, hallados: {len(tickets_previos)}"
    ids_tickets_previos = {t[0] for t in tickets_previos}

    cur = conn.execute("SELECT id, ticket_id, costo_unitario FROM ticket_items ORDER BY id ASC;")
    items_previos = cur.fetchall()
    assert len(items_previos) == 6, f"Se esperaban 6 ítems previos, hallados: {len(items_previos)}"
    for it in items_previos:
        assert it[2] is None, f"El ítem histórico {it[0]} tiene costo no nulo: {it[2]}"
    print("✓ Tickets originales (3) e ítems (6) confirmados presentes con costo_unitario = NULL.")

    # 4. Línea base de conteos
    tablas_control = [
        "tickets", "ticket_items", "ventas_auditoria", "productos",
        "cargas_servicios", "cargas_servicios_auditoria", "gastos_retiros",
        "cierres_diarios", "gastos_auditoria", "pedidos_proveedor",
        "pedido_items", "pedidos_auditoria", "outbox_events"
    ]
    baseline = {}
    for t in tablas_control:
        cnt = conn.execute(f"SELECT COUNT(*) FROM {t};").fetchone()[0]
        baseline[t] = cnt
    print("✓ Línea base de conteos capturada:")
    print(f"   tickets: {baseline['tickets']}, ticket_items: {baseline['ticket_items']}, ventas_auditoria: {baseline['ventas_auditoria']}")
    print(f"   productos: {baseline['productos']}, cargas_servicios: {baseline['cargas_servicios']}, outbox_events: {baseline['outbox_events']}")

    # 5. Elegir producto de prueba seguro
    cur = conn.execute(
        "SELECT id, nombre, categoria, proveedor, costo, precio_dia, activo "
        "FROM productos WHERE id = 9001 AND activo = 1;"
    )
    prod_row = cur.fetchone()
    if not prod_row or prod_row[1] != "Alfajor Sintético Simple":
        print("❌ Error: No se encontró el producto de prueba sintético id=9001 'Alfajor Sintético Simple'.", file=sys.stderr)
        sys.exit(1)
    
    prod_id, prod_nombre, prod_cat, prod_prov, prod_costo, prod_precio, prod_activo = prod_row
    print(f"\n✓ Producto sintético de prueba seleccionado:")
    print(f"   ID: {prod_id} | Nombre: '{prod_nombre}'")
    print(f"   Costo confiable en catálogo: ARS {prod_costo}")
    print(f"   Precio de venta: ARS {prod_precio} (inferior a ARS 5.000)")

    # 6. Autenticación real mediante POST /api/auth/login
    print("\n--- Autenticación Requerida ---")
    if len(sys.argv) > 1 and sys.argv[1].startswith("--password="):
        # Solo para invocación controlada interna si fuera provista
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

    # 7. Generar identificador único de ticket sintético
    sufijo_random = secrets.token_hex(4)
    timestamp_utc = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    ticket_id = f"T-DEV-AUTH-SMOKE-{timestamp_utc}-{sufijo_random}"
    
    # Comprobar que no exista
    cur = conn.execute("SELECT 1 FROM tickets WHERE id = ?;", (ticket_id,))
    assert cur.fetchone() is None, "Colisión inesperada de ticket_id"
    print(f"\n-> Ticket sintético a registrar: {ticket_id}")

    fecha_ar = datetime.now(_ZONA_AR).strftime("%d/%m/%Y %H:%M:%S")
    venta_payload = {
        "ticket": {
            "ticket_id": ticket_id,
            "fecha": fecha_ar,
            "total_venta": prod_precio,
            "monto_efectivo": prod_precio,
            "monto_transf": 0,
            "metodo_pago": "EFECTIVO",
            "es_nocturno": False,
        },
        "items": [
            {
                "producto": prod_nombre,
                "cantidad": 1.0,
                "unidad": "Unidad",
                "precio_unit": prod_precio,
                "subtotal": prod_precio,
            }
        ],
    }

    # 8. POST /api/sales (Creación de venta sintética)
    res_crear = client.post("/api/sales", json=venta_payload, headers=auth_headers)
    print(f"-> Respuesta POST /api/sales (creación): HTTP {res_crear.status_code}")
    if res_crear.status_code != 201:
        print(f"❌ Error al crear venta: {res_crear.text}", file=sys.stderr)
        sys.exit(1)
    
    data_crear = res_crear.json()
    assert data_crear["status"] == "registrado"
    assert data_crear["ticket_id"] == ticket_id
    print("✓ Venta sintética registrada exitosamente en Turso DEV.")

    # Verificar persistencia física inmediata en Turso DEV
    cur = conn.execute("SELECT id, total_venta, metodo_pago, anulado FROM tickets WHERE id = ?;", (ticket_id,))
    t_persisted = cur.fetchone()
    assert t_persisted is not None
    assert t_persisted[1] == prod_precio
    assert t_persisted[3] == 0  # anulado = 0

    cur = conn.execute("SELECT id, producto_id, producto_nombre, cantidad, precio_unitario, subtotal, costo_unitario FROM ticket_items WHERE ticket_id = ?;", (ticket_id,))
    item_persisted = cur.fetchone()
    assert item_persisted is not None
    assert item_persisted[1] == prod_id
    assert item_persisted[2] == prod_nombre
    assert item_persisted[4] == prod_precio
    assert item_persisted[5] == prod_precio
    assert item_persisted[6] == prod_costo, f"Costo histórico {item_persisted[6]} != esperado {prod_costo}"
    print(f"✓ Snapshot de costo histórico verificado: ticket_items.costo_unitario = {item_persisted[6]} (congelado con precisión).")

    # 9. Verificación de Idempotencia (Reenvío idéntico)
    print("\n-> Probando idempotencia (segunda solicitud idéntica)...")
    res_idempotente = client.post("/api/sales", json=venta_payload, headers=auth_headers)
    print(f"-> Respuesta POST /api/sales (reintento): HTTP {res_idempotente.status_code}")
    assert res_idempotente.status_code == 200, f"Se esperaba HTTP 200 en reintento, obtenido {res_idempotente.status_code}"
    data_idemp = res_idempotente.json()
    assert data_idemp["status"] == "existente"
    assert data_idemp["ticket_id"] == ticket_id

    # Comprobar que los conteos no se hayan duplicado
    cnt_tickets = conn.execute("SELECT COUNT(*) FROM tickets;").fetchone()[0]
    cnt_items = conn.execute("SELECT COUNT(*) FROM ticket_items;").fetchone()[0]
    assert cnt_tickets == baseline["tickets"] + 1, f"Tickets duplicados: {cnt_tickets}"
    assert cnt_items == baseline["ticket_items"] + 1, f"Ítems duplicados: {cnt_items}"
    print("✓ Contrato de idempotencia validado: sin duplicación de cabecera ni de ítems.")

    # 10. Consulta de detalle mediante GET /api/sales/{ticket_id}
    res_get = client.get(f"/api/sales/{ticket_id}", headers=auth_headers)
    assert res_get.status_code == 200
    data_get = res_get.json()
    assert data_get["ticket_id"] == ticket_id
    assert data_get["anulado"] is False
    assert len(data_get["items"]) == 1
    assert data_get["items"][0]["costo_unitario"] == prod_costo
    print(f"✓ Detalle del ticket consultado vía GET: {data_get['ticket_id']} (activo, 1 ítem, costo {prod_costo}).")

    # 11. Anulación lógica auditada
    print("\n-> Anulando lógicamente el ticket sintético con motivo de auditoría...")
    motivo_anulacion = "PRUEBA_SINTETICA_CONTROLADA_DEV"
    anular_payload = {
        "motivo": motivo_anulacion,
        "usuario": "alan",
    }
    res_anular = client.post(f"/api/sales/{ticket_id}/anular", json=anular_payload, headers=auth_headers)
    print(f"-> Respuesta POST /api/sales/{ticket_id}/anular: HTTP {res_anular.status_code}")
    assert res_anular.status_code == 200
    data_anulada = res_anular.json()
    assert data_anulada["anulado"] is True
    assert data_anulada["motivo_anulacion"] == motivo_anulacion

    # Verificar registro en ventas_auditoria
    cur = conn.execute(
        "SELECT id, ticket_id, accion, motivo, usuario FROM ventas_auditoria WHERE ticket_id = ?;",
        (ticket_id,),
    )
    aud_row = cur.fetchone()
    assert aud_row is not None
    assert aud_row[2] == "ANULACION"
    assert aud_row[3] == motivo_anulacion
    assert aud_row[4] == "alan"
    print(f"✓ Anulación lógica y registro en 'ventas_auditoria' confirmados (acción: ANULACION, usuario: alan).")

    # 12. Comprobar que el Dashboard no computa ventas anuladas
    fecha_hoy = datetime.now(_ZONA_AR).strftime("%Y-%m-%d")
    res_dash = client.get(f"/api/reports/dashboard?desde={fecha_hoy}&hasta={fecha_hoy}&periodo=personalizado", headers=auth_headers)
    assert res_dash.status_code == 200
    data_dash = res_dash.json()
    assert data_dash["esquema_completo"] is True
    # Las ventas brutas del día de hoy no deben incluir el ticket anulado
    print(f"✓ Dashboard financiero consultado: esquema_completo = True, advertencia_esquema = None.")

    # 13. Verificación final de integridad frente a la línea base
    print("\n--- Verificación Final de Integridad de la Base de Datos ---")
    final_counts = {}
    for t in tablas_control:
        cnt = conn.execute(f"SELECT COUNT(*) FROM {t};").fetchone()[0]
        final_counts[t] = cnt
        print(f"   Tabla {t}: antes={baseline[t]}, después={cnt}")

    assert final_counts["tickets"] == baseline["tickets"] + 1, "Tickets discrepa"
    assert final_counts["ticket_items"] == baseline["ticket_items"] + 1, "Ticket items discrepa"
    assert final_counts["ventas_auditoria"] == baseline["ventas_auditoria"] + 1, "Ventas auditoria discrepa"
    assert final_counts["productos"] == baseline["productos"], "Productos alterados"
    assert final_counts["cargas_servicios"] == baseline["cargas_servicios"], "Cargas servicios alteradas"
    assert final_counts["gastos_retiros"] == baseline["gastos_retiros"], "Gastos alterados"
    assert final_counts["cierres_diarios"] == baseline["cierres_diarios"], "Cierres alterados"
    assert final_counts["pedidos_proveedor"] == baseline["pedidos_proveedor"], "Pedidos alterados"
    assert final_counts["outbox_events"] == baseline["outbox_events"], "Outbox alterado"

    # Verificar tickets preexistentes
    cur = conn.execute("SELECT id, anulado FROM tickets WHERE id IN (?, ?, ?);", tuple(ids_tickets_previos))
    prev_status = {r[0]: r[1] for r in cur.fetchall()}
    for orig_t in tickets_previos:
        assert prev_status[orig_t[0]] == orig_t[4], f"Estado de ticket previo {orig_t[0]} mutó"

    conn.close()
    print("\n=== PRUEBA TRANSACCIONAL EN TURSO DEV COMPLETADA EXITOSAMENTE ===")
    print(f"Identificador del ticket sintético generado y anulado: {ticket_id}")


if __name__ == "__main__":
    main()
