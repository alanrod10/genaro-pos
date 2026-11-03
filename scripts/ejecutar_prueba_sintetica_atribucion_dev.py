#!/usr/bin/env python3
"""Procedimiento seguro y auditable de prueba sintética de atribución de usuarios y auditoría cruzada en Turso DEV.

Alcance estrictamente autorizado:
1. Comprobaciones previas de solo lectura:
   - Destino Turso DEV (genaro-pos-dev-alanrod10.aws-sa-east-1.turso.io).
   - Servidor Uvicorn actualizado y readiness PREPARADO.
   - Frontend en modo VITE_READ_ONLY_CONNECTED_SMOKE=true.
   - Línea base de conteos e integridad de hashes históricos.
2. Autenticación segura mediante POST /api/auth/login con contraseñas ingresadas vía getpass (sin eco).
   - Tokens resguardados exclusivamente en memoria; sin impresión ni persistencia de credenciales.
3. Creación secuencial y auditada de tres operaciones sintéticas:
   - Venta: POST /api/sales (sesión de Alan) -> verifica ventas_auditoria (CREACION, usuario='alan').
   - Carga de servicio: POST /api/services (sesión de Alan) -> verifica cargas_servicios_auditoria (CREAR, actor='alan').
   - Gasto: POST /api/expenses (sesión de Yilda) -> verifica gastos_auditoria (CREAR, actor='yilda').
4. Anulaciones lógicas cruzadas oficiales:
   - Venta: POST /api/sales/{ticket_id}/anular (sesión de Yilda) -> verifica ventas_auditoria (ANULACION, usuario='yilda').
   - Carga de servicio: DELETE /api/services/{id} (sesión de Yilda) -> verifica cargas_servicios_auditoria (ANULAR, actor='yilda').
   - Gasto: DELETE /api/expenses/{id} (sesión de Alan) -> verifica gastos_auditoria (ANULAR, actor='alan').
5. Verificación final de integridad y conteos en Turso DEV.
"""

from datetime import datetime, timezone
from getpass import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.parse import urlparse
import urllib.request
import urllib.error
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Cargar configuración desde .env
from api.main import cargar_env_local
cargar_env_local(PROJECT_ROOT / ".env")

from db.services import _abrir_conexion_dev
from db.readiness import comprobar_preparacion_esquema

API_BASE = "http://127.0.0.1:8000"
DEV_HOSTNAME_EXPECTED = "genaro-pos-dev-alanrod10.aws-sa-east-1.turso.io"
DEV_ENV_NAME_EXPECTED = "genaro-pos-dev"
_ZONA_AR = ZoneInfo("America/Argentina/Jujuy")

HASH_CARGAS_HISTORICAS_ESPERADO = "d623b0a935c86493035cdf86534dc7dc1794a9019a9e20f881a41eb595c6d0a1"
HASH_TICKETS_PREVIOS_ESPERADO = "fd87842a991ab24084d1c28688fc3f718a2088276660ce83cea344f24998ef29"


def api_request(method: str, path: str, token: str = None, payload: dict = None):
    url = f"{API_BASE}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            status = resp.status
            try:
                data_json = json.loads(body)
            except Exception:
                data_json = body
            return status, data_json
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        status = e.code
        try:
            data_json = json.loads(body)
        except Exception:
            data_json = body
        return status, data_json


def calcular_hash_cargas_historicas(conn, max_id=1718):
    c = conn.cursor()
    cols = [
        'id', 'legacy_row_id', 'fecha_hora_utc', 'servicio', 'monto_carga',
        'monto_adicional', 'total_cobrado', 'pago_efectivo', 'pago_transferencia', 'anulado'
    ]
    c.execute(f"SELECT {', '.join(cols)} FROM cargas_servicios WHERE id <= ? ORDER BY id ASC", (max_id,))
    rows = c.fetchall()
    hasher = hashlib.sha256()
    for fila in rows:
        serialized = json.dumps(list(fila), ensure_ascii=True, separators=(',', ':'))
        hasher.update(serialized.encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest(), len(rows)


def calcular_hash_tickets(conn, ticket_ids: list[str] = None):
    c = conn.cursor()
    cols = ['id', 'fecha_hora_utc', 'total_venta', 'monto_efectivo', 'monto_transferencia', 'metodo_pago', 'anulado']
    if ticket_ids is not None:
        if not ticket_ids:
            return hashlib.sha256(b"").hexdigest(), 0
        placeholders = ", ".join(["?"] * len(ticket_ids))
        c.execute(f"SELECT {', '.join(cols)} FROM tickets WHERE id IN ({placeholders}) ORDER BY id ASC", tuple(ticket_ids))
    else:
        c.execute(f"SELECT {', '.join(cols)} FROM tickets ORDER BY id ASC")
    rows = c.fetchall()
    hasher = hashlib.sha256()
    for fila in rows:
        serialized = json.dumps(list(fila), ensure_ascii=True, separators=(',', ':'))
        hasher.update(serialized.encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest(), len(rows)


def calcular_hash_tickets_preexistentes(conn, ticket_ids: list[str] = None):
    return calcular_hash_tickets(conn, ticket_ids)


def calcular_hash_ticket_items(conn, item_ids: list[int] = None):
    c = conn.cursor()
    cols = ['id', 'ticket_id', 'producto_id', 'cantidad', 'precio_unitario', 'subtotal', 'costo_unitario']
    if item_ids is not None:
        if not item_ids:
            return hashlib.sha256(b"").hexdigest(), 0
        placeholders = ", ".join(["?"] * len(item_ids))
        c.execute(f"SELECT {', '.join(cols)} FROM ticket_items WHERE id IN ({placeholders}) ORDER BY id ASC", tuple(item_ids))
    else:
        c.execute(f"SELECT {', '.join(cols)} FROM ticket_items ORDER BY id ASC")
    rows = c.fetchall()
    hasher = hashlib.sha256()
    for fila in rows:
        serialized = json.dumps(list(fila), ensure_ascii=True, separators=(',', ':'))
        hasher.update(serialized.encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest(), len(rows)


def obtener_conteos(conn):
    c = conn.cursor()
    tablas = [
        'tickets', 'ticket_items', 'ventas_auditoria',
        'cargas_servicios', 'cargas_servicios_auditoria',
        'gastos_retiros', 'gastos_auditoria', 'cierres_diarios'
    ]
    conteos = {}
    for t in tablas:
        if t in ('tickets', 'cargas_servicios', 'gastos_retiros'):
            c.execute(f"SELECT COUNT(*), COUNT(CASE WHEN anulado=0 THEN 1 END), COUNT(CASE WHEN anulado=1 THEN 1 END) FROM {t}")
            tot, act, anul = c.fetchone()
            conteos[t] = {'total': tot, 'activos': act, 'anulados': anul}
        else:
            c.execute(f"SELECT COUNT(*) FROM {t}")
            conteos[t] = {'total': c.fetchone()[0]}
    return conteos


def ejecutar_prueba(pass_alan: str = None, pass_yilda: str = None) -> dict:
    reporte = {
        "fecha_ejecucion_utc": datetime.now(timezone.utc).isoformat(),
        "fase_1_preflight": {},
        "fase_2_autenticacion": {},
        "fase_3_creaciones": {},
        "fase_4_anulaciones": {},
        "fase_5_verificacion_final": {},
        "exito_total": False,
    }

    print("=" * 75)
    print("GENAROPOS: PRUEBA SINTÉTICA DE ATRIBUCIÓN Y AUDITORÍA CRUZADA (TURSO DEV)")
    print("=" * 75)

    # -------------------------------------------------------------
    # FASE 1: PREFLIGHT FINAL DE SOLO LECTURA
    # -------------------------------------------------------------
    print("\n[FASE 1] Verificaciones previas obligatorias...")
    env_name = os.getenv("ENV_NAME")
    turso_url = os.getenv("TURSO_DEV_DATABASE_URL") or os.getenv("TURSO_DATABASE_URL")
    parsed_host = urlparse(turso_url).netloc

    print(f"  • ENV_NAME: {env_name}")
    print(f"  • TURSO Hostname: {parsed_host}")

    if env_name != DEV_ENV_NAME_EXPECTED:
        raise SystemExit(f"ABORT: ENV_NAME '{env_name}' != '{DEV_ENV_NAME_EXPECTED}'")
    if parsed_host != DEV_HOSTNAME_EXPECTED:
        raise SystemExit(f"ABORT: Hostname '{parsed_host}' != '{DEV_HOSTNAME_EXPECTED}'")

    # Frontend read-only check
    ps_vite = subprocess.run(["ps", "-eo", "pid,cmd"], capture_output=True, text=True)
    vite_pids = [line.split()[0] for line in ps_vite.stdout.splitlines() if "vite" in line and "node" in line]
    vite_ro_ok = False
    for v_pid in vite_pids:
        try:
            with open(f"/proc/{v_pid}/environ", "rb") as ef:
                env_bytes = ef.read().decode("utf-8", errors="replace")
                if "VITE_READ_ONLY_CONNECTED_SMOKE=true" in env_bytes:
                    vite_ro_ok = True
                    break
        except Exception:
            pass
    print(f"  • Frontend VITE_READ_ONLY_CONNECTED_SMOKE=true: {'CONFIRMADO' if vite_ro_ok else 'NO DETECTADO'}")
    if not vite_ro_ok:
        raise SystemExit("ABORT: No se pudo confirmar que el frontend esté en modo de solo lectura.")

    # Conexión Turso DEV de lectura y control
    conn = _abrir_conexion_dev()
    conteos_base = obtener_conteos(conn)
    print(f"  • Conteos baseline capturados:")
    print(f"      tickets: {conteos_base['tickets']}")
    print(f"      ticket_items: {conteos_base['ticket_items']}")
    print(f"      ventas_auditoria: {conteos_base['ventas_auditoria']}")
    print(f"      cargas_servicios: {conteos_base['cargas_servicios']}")
    print(f"      cargas_servicios_auditoria: {conteos_base['cargas_servicios_auditoria']}")
    print(f"      gastos_retiros: {conteos_base['gastos_retiros']}")
    print(f"      gastos_auditoria: {conteos_base['gastos_auditoria']}")
    print(f"      cierres_diarios: {conteos_base['cierres_diarios']}")

    # Capturar de forma explícita los IDs de tickets e ítems preexistentes
    c_base = conn.cursor()
    c_base.execute("SELECT id FROM tickets ORDER BY id ASC")
    ticket_ids_base = [r[0] for r in c_base.fetchall()]

    c_base.execute("SELECT id FROM ticket_items ORDER BY id ASC")
    item_ids_base = [r[0] for r in c_base.fetchall()]

    # Validar hashes baseline
    hash_cargas_base, total_cargas_base = calcular_hash_cargas_historicas(conn, 1718)
    print(f"  • SHA-256 cargas históricas (1718): {hash_cargas_base}")
    if hash_cargas_base != HASH_CARGAS_HISTORICAS_ESPERADO:
        raise SystemExit(f"ABORT: Discrepancia en hash de cargas históricas: {hash_cargas_base}")

    hash_tickets_base, total_tickets_base = calcular_hash_tickets(conn, ticket_ids_base)
    print(f"  • SHA-256 tickets preexistentes ({len(ticket_ids_base)}): {hash_tickets_base}")
    if len(ticket_ids_base) == 4 and hash_tickets_base != HASH_TICKETS_PREVIOS_ESPERADO:
        raise SystemExit(f"ABORT: Discrepancia en hash de tickets preexistentes: {hash_tickets_base}")

    hash_items_base, total_items_base = calcular_hash_ticket_items(conn, item_ids_base)
    print(f"  • SHA-256 ticket_items preexistentes ({len(item_ids_base)}): {hash_items_base}")

    reporte["fase_1_preflight"] = {
        "env_name": env_name,
        "turso_hostname": parsed_host,
        "conteos_base": conteos_base,
        "hash_cargas_base": hash_cargas_base,
        "hash_tickets_base": hash_tickets_base,
        "hash_items_base": hash_items_base,
        "ticket_ids_base": ticket_ids_base,
        "item_ids_base": item_ids_base,
        "frontend_read_only_smoke": vite_ro_ok,
    }
    print("✓ Fase 1 completada con éxito.")

    # -------------------------------------------------------------
    # FASE 2: AUTENTICACIÓN SEGURA MEDIANTE ENDPOINT REAL DE LOGIN
    # -------------------------------------------------------------
    print("\n[FASE 2] Autenticación segura mediante POST /api/auth/login...")
    if not pass_alan:
        pass_alan = getpass("  -> Ingrese la contraseña de Alan (sin eco): ")
    if not pass_yilda:
        pass_yilda = getpass("  -> Ingrese la contraseña de Yilda (sin eco): ")

    # Login de Alan
    code_login_alan, data_login_alan = api_request("POST", "/api/auth/login", payload={"username": "alan", "password": pass_alan})
    if code_login_alan != 200 or not isinstance(data_login_alan, dict) or "access_token" not in data_login_alan:
        raise SystemExit(f"ABORT: Login de Alan falló (HTTP {code_login_alan}): {data_login_alan}")
    token_alan = data_login_alan["access_token"]
    print("  ✓ Sesión de Alan autenticada correctamente (HTTP 200). Token resguardado en memoria.")

    # Login de Yilda
    code_login_yilda, data_login_yilda = api_request("POST", "/api/auth/login", payload={"username": "yilda", "password": pass_yilda})
    if code_login_yilda != 200 or not isinstance(data_login_yilda, dict) or "access_token" not in data_login_yilda:
        raise SystemExit(f"ABORT: Login de Yilda falló (HTTP {code_login_yilda}): {data_login_yilda}")
    token_yilda = data_login_yilda["access_token"]
    print("  ✓ Sesión de Yilda autenticada correctamente (HTTP 200). Token resguardado en memoria.")

    # Verificar readiness mediante endpoint HTTP con token válido
    code_readiness, data_readiness = api_request("GET", "/api/system/readiness", token=token_alan)
    if code_readiness != 200 or data_readiness.get("status") != "PREPARADO" or not data_readiness.get("listo"):
        raise SystemExit(f"ABORT: GET /api/system/readiness falló: HTTP {code_readiness}, {data_readiness}")
    print(f"  ✓ Endpoint /api/system/readiness validado: HTTP 200, status={data_readiness.get('status')}, listo={data_readiness.get('listo')}")

    reporte["fase_2_autenticacion"] = {
        "login_alan_http_status": code_login_alan,
        "login_yilda_http_status": code_login_yilda,
        "readiness_http_status": code_readiness,
        "readiness_status": data_readiness.get("status"),
        "readiness_listo": data_readiness.get("listo"),
    }

    # -------------------------------------------------------------
    # FASE 3: CREAR Y VERIFICAR TRES OPERACIONES SINTÉTICAS
    # -------------------------------------------------------------
    print("\n[FASE 3] Creación secuencial de operaciones sintéticas...")
    ts_now = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    rand_suffix = secrets.token_hex(3)
    c = conn.cursor()

    # 1. Venta sintética (Sesión Alan)
    ticket_id = f"T-DEV-AUDIT-ATTRIB-{ts_now}-{rand_suffix}"
    fecha_ar = datetime.now(_ZONA_AR).strftime("%d/%m/%Y %H:%M:%S")
    prod_id = 1
    prod_nombre = "361 1,5lts"
    prod_precio = 2700

    payload_venta = {
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
                "metodo_pago": "EFECTIVO",
            }
        ],
    }

    code_v, resp_v = api_request("POST", "/api/sales", token=token_alan, payload=payload_venta)
    if code_v != 201:
        raise SystemExit(f"ABORT: Error al registrar venta sintética (HTTP {code_v}): {resp_v}")
    print(f"  • Venta {ticket_id} registrada: HTTP 201 Created.")

    # Verificación en BD Turso DEV
    c.execute("SELECT id, total_venta, anulado FROM tickets WHERE id = ?", (ticket_id,))
    row_t = c.fetchone()
    assert row_t and row_t[2] == 0, f"Ticket {ticket_id} no persistido o ya anulado"

    c.execute("SELECT ticket_id, producto_id, subtotal FROM ticket_items WHERE ticket_id = ?", (ticket_id,))
    row_it = c.fetchone()
    assert row_it and row_it[1] == prod_id and row_it[2] == prod_precio, f"Ítem de venta incorrecto: {row_it}"

    c.execute("SELECT ticket_id, accion, usuario FROM ventas_auditoria WHERE ticket_id = ? ORDER BY id ASC", (ticket_id,))
    rows_v_audit = c.fetchall()
    assert len(rows_v_audit) == 1, f"Se esperaba 1 evento de auditoría de venta, hallados: {rows_v_audit}"
    assert rows_v_audit[0][1] == "CREACION" and rows_v_audit[0][2] == "alan", f"Auditoría venta incorrecta: {rows_v_audit[0]}"
    print(f"    ✓ Auditoría en Turso DEV confirmada: accion='{rows_v_audit[0][1]}', usuario='{rows_v_audit[0][2]}'.")

    # 2. Carga de servicio sintética (Sesión Alan)
    idemp_serv = f"IDEMP-DEV-SERV-{ts_now}-{rand_suffix}"
    payload_serv = {
        "servicio": "PRUEBA_SINTETICA_AUDITORIA_ATRIBUCION_DEV",
        "monto_carga": 100,
        "monto_adicional": 0,
        "total_cobrado": 100,
        "pago_efvo": 100,
        "pago_transf": 0,
        "idempotency_key": idemp_serv,
    }

    code_s, resp_s = api_request("POST", "/api/services", token=token_alan, payload=payload_serv)
    if code_s != 201:
        raise SystemExit(f"ABORT: Error al registrar servicio (HTTP {code_s}): {resp_s}")
    carga_id = resp_s.get("id")
    print(f"  • Carga de servicio ID={carga_id} registrada: HTTP 201 Created.")

    # Verificación en BD Turso DEV
    c.execute("SELECT id, servicio, anulado, idempotency_key FROM cargas_servicios WHERE id = ?", (carga_id,))
    row_s = c.fetchone()
    assert row_s and row_s[2] == 0 and row_s[3] == idemp_serv, f"Carga {carga_id} inconsistente: {row_s}"

    c.execute("SELECT carga_id, accion, actor FROM cargas_servicios_auditoria WHERE carga_id = ? ORDER BY id ASC", (carga_id,))
    rows_s_audit = c.fetchall()
    assert len(rows_s_audit) == 1, f"Se esperaba 1 evento de auditoría de servicio, hallados: {rows_s_audit}"
    assert rows_s_audit[0][1] == "CREAR" and rows_s_audit[0][2] == "alan", f"Auditoría servicio incorrecta: {rows_s_audit[0]}"
    print(f"    ✓ Auditoría en Turso DEV confirmada: accion='{rows_s_audit[0][1]}', actor='{rows_s_audit[0][2]}'.")

    # 3. Gasto sintético (Sesión Yilda)
    idemp_gasto = f"IDEMP-DEV-GASTO-{ts_now}-{rand_suffix}"
    payload_gasto = {
        "detalle": "PRUEBA_SINTETICA_AUDITORIA_ATRIBUCION_DEV",
        "monto_salida": 50,
        "tipo": "VARIOS",
        "origen_fondos": "CAJA",
        "metodo_pago": "EFECTIVO",
        "idempotency_key": idemp_gasto,
    }

    code_g, resp_g = api_request("POST", "/api/expenses", token=token_yilda, payload=payload_gasto)
    if code_g != 201:
        raise SystemExit(f"ABORT: Error al registrar gasto (HTTP {code_g}): {resp_g}")
    gasto_id = resp_g.get("id")
    print(f"  • Gasto ID={gasto_id} registrado: HTTP 201 Created.")

    # Verificación en BD Turso DEV
    c.execute("SELECT id, detalle, anulado, idempotency_key FROM gastos_retiros WHERE id = ?", (gasto_id,))
    row_g = c.fetchone()
    assert row_g and row_g[2] == 0 and row_g[3] == idemp_gasto, f"Gasto {gasto_id} inconsistente: {row_g}"

    c.execute("SELECT gasto_id, accion, actor FROM gastos_auditoria WHERE gasto_id = ? ORDER BY id ASC", (gasto_id,))
    rows_g_audit = c.fetchall()
    assert len(rows_g_audit) == 1, f"Se esperaba 1 evento de auditoría de gasto, hallados: {rows_g_audit}"
    assert rows_g_audit[0][1] == "CREAR" and rows_g_audit[0][2] == "yilda", f"Auditoría gasto incorrecta: {rows_g_audit[0]}"
    print(f"    ✓ Auditoría en Turso DEV confirmada: accion='{rows_g_audit[0][1]}', actor='{rows_g_audit[0][2]}'.")

    reporte["fase_3_creaciones"] = {
        "venta": {
            "ticket_id": ticket_id,
            "http_status": code_v,
            "auditoria_accion": rows_v_audit[0][1],
            "auditoria_usuario": rows_v_audit[0][2],
        },
        "servicio": {
            "carga_id": carga_id,
            "idempotency_key": idemp_serv,
            "http_status": code_s,
            "auditoria_accion": rows_s_audit[0][1],
            "auditoria_actor": rows_s_audit[0][2],
        },
        "gasto": {
            "gasto_id": gasto_id,
            "idempotency_key": idemp_gasto,
            "http_status": code_g,
            "auditoria_accion": rows_g_audit[0][1],
            "auditoria_actor": rows_g_audit[0][2],
        },
    }

    # -------------------------------------------------------------
    # FASE 4: ANULACIONES LÓGICAS CRUZADAS
    # -------------------------------------------------------------
    print("\n[FASE 4] Anulaciones lógicas cruzadas...")

    # 1. Anular venta con sesión de Yilda
    code_anul_v, resp_anul_v = api_request(
        "POST",
        f"/api/sales/{ticket_id}/anular",
        token=token_yilda,
        payload={"motivo": "PRUEBA_SINTETICA_AUDITORIA_ATRIBUCION_DEV"},
    )
    if code_anul_v != 200:
        raise SystemExit(f"ABORT: Error al anular venta {ticket_id} con Yilda (HTTP {code_anul_v}): {resp_anul_v}")
    print(f"  • Venta {ticket_id} anulada por Yilda: HTTP 200 OK.")

    # Verificación en BD Turso DEV
    c.execute("SELECT anulado, motivo_anulacion FROM tickets WHERE id = ?", (ticket_id,))
    row_t_anul = c.fetchone()
    assert row_t_anul and row_t_anul[0] == 1, f"Ticket no quedó anulado: {row_t_anul}"

    c.execute("SELECT ticket_id, accion, usuario FROM ventas_auditoria WHERE ticket_id = ? ORDER BY id ASC", (ticket_id,))
    rows_v_audit_all = c.fetchall()
    assert len(rows_v_audit_all) == 2, f"Se esperaban 2 eventos de auditoría para la venta, hallados: {rows_v_audit_all}"
    assert rows_v_audit_all[1][1] == "ANULACION" and rows_v_audit_all[1][2] == "yilda", f"Auditoría anulación venta incorrecta: {rows_v_audit_all[1]}"
    print(f"    ✓ Eventos de venta: 1. ({rows_v_audit_all[0][1]}, '{rows_v_audit_all[0][2]}'), 2. ({rows_v_audit_all[1][1]}, '{rows_v_audit_all[1][2]}').")

    # 2. Anular carga de servicio con sesión de Yilda
    code_anul_s, resp_anul_s = api_request(
        "DELETE",
        f"/api/services/{carga_id}",
        token=token_yilda,
    )
    if code_anul_s != 200:
        raise SystemExit(f"ABORT: Error al anular servicio {carga_id} con Yilda (HTTP {code_anul_s}): {resp_anul_s}")
    print(f"  • Carga de servicio ID={carga_id} anulada por Yilda: HTTP 200 OK.")

    # Verificación en BD Turso DEV
    c.execute("SELECT anulado FROM cargas_servicios WHERE id = ?", (carga_id,))
    row_s_anul = c.fetchone()
    assert row_s_anul and row_s_anul[0] == 1, f"Carga no quedó anulada: {row_s_anul}"

    c.execute("SELECT carga_id, accion, actor FROM cargas_servicios_auditoria WHERE carga_id = ? ORDER BY id ASC", (carga_id,))
    rows_s_audit_all = c.fetchall()
    assert len(rows_s_audit_all) == 2, f"Se esperaban 2 eventos de auditoría para servicio, hallados: {rows_s_audit_all}"
    assert rows_s_audit_all[1][1] == "ANULAR" and rows_s_audit_all[1][2] == "yilda", f"Auditoría anulación servicio incorrecta: {rows_s_audit_all[1]}"
    print(f"    ✓ Eventos de servicio: 1. ({rows_s_audit_all[0][1]}, '{rows_s_audit_all[0][2]}'), 2. ({rows_s_audit_all[1][1]}, '{rows_s_audit_all[1][2]}').")

    # 3. Anular gasto con sesión de Alan
    code_anul_g, resp_anul_g = api_request(
        "DELETE",
        f"/api/expenses/{gasto_id}",
        token=token_alan,
    )
    if code_anul_g != 200:
        raise SystemExit(f"ABORT: Error al anular gasto {gasto_id} con Alan (HTTP {code_anul_g}): {resp_anul_g}")
    print(f"  • Gasto ID={gasto_id} anulado por Alan: HTTP 200 OK.")

    # Verificación en BD Turso DEV
    c.execute("SELECT anulado FROM gastos_retiros WHERE id = ?", (gasto_id,))
    row_g_anul = c.fetchone()
    assert row_g_anul and row_g_anul[0] == 1, f"Gasto no quedó anulado: {row_g_anul}"

    c.execute("SELECT gasto_id, accion, actor FROM gastos_auditoria WHERE gasto_id = ? ORDER BY id ASC", (gasto_id,))
    rows_g_audit_all = c.fetchall()
    assert len(rows_g_audit_all) == 2, f"Se esperaban 2 eventos de auditoría para gasto, hallados: {rows_g_audit_all}"
    assert rows_g_audit_all[1][1] == "ANULAR" and rows_g_audit_all[1][2] == "alan", f"Auditoría anulación gasto incorrecta: {rows_g_audit_all[1]}"
    print(f"    ✓ Eventos de gasto: 1. ({rows_g_audit_all[0][1]}, '{rows_g_audit_all[0][2]}'), 2. ({rows_g_audit_all[1][1]}, '{rows_g_audit_all[1][2]}').")

    reporte["fase_4_anulaciones"] = {
        "venta": {
            "http_status": code_anul_v,
            "auditoria_accion": rows_v_audit_all[1][1],
            "auditoria_usuario": rows_v_audit_all[1][2],
        },
        "servicio": {
            "http_status": code_anul_s,
            "auditoria_accion": rows_s_audit_all[1][1],
            "auditoria_actor": rows_s_audit_all[1][2],
        },
        "gasto": {
            "http_status": code_anul_g,
            "auditoria_accion": rows_g_audit_all[1][1],
            "auditoria_actor": rows_g_audit_all[1][2],
        },
    }

    # -------------------------------------------------------------
    # FASE 5: VERIFICACIÓN FINAL DE INTEGRIDAD Y READINESS
    # -------------------------------------------------------------
    print("\n[FASE 5] Verificación final de integridad y readiness...")
    conteos_post = obtener_conteos(conn)
    print("  • Conteos finales observados en Turso DEV:")
    for k, v in conteos_post.items():
        print(f"      {k}: {v}")

    # Comprobar que los activos volvieron a su línea base
    assert conteos_post["tickets"]["activos"] == conteos_base["tickets"]["activos"] == 1, "Tickets activos difiere de 1"
    assert conteos_post["cargas_servicios"]["activos"] == conteos_base["cargas_servicios"]["activos"] == 1717, "Cargas activas difiere de 1717"
    assert conteos_post["gastos_retiros"]["activos"] == conteos_base["gastos_retiros"]["activos"] == 0, "Gastos activos difiere de 0"
    assert conteos_post["cierres_diarios"]["total"] == conteos_base["cierres_diarios"]["total"] == 0, "Cierres diarios difiere de 0"

    # Comprobar incrementos exactos
    assert conteos_post["tickets"]["total"] == conteos_base["tickets"]["total"] + 1, "Tickets total no incrementó en 1"
    assert conteos_post["ticket_items"]["total"] == conteos_base["ticket_items"]["total"] + 1, "Items total no incrementó en 1"
    assert conteos_post["ventas_auditoria"]["total"] == conteos_base["ventas_auditoria"]["total"] + 2, "Ventas auditoría no incrementó en 2"
    assert conteos_post["cargas_servicios"]["total"] == conteos_base["cargas_servicios"]["total"] + 1, "Cargas total no incrementó en 1"
    assert conteos_post["cargas_servicios_auditoria"]["total"] == conteos_base["cargas_servicios_auditoria"]["total"] + 2, "Cargas auditoría no incrementó en 2"
    assert conteos_post["gastos_retiros"]["total"] == conteos_base["gastos_retiros"]["total"] + 1, "Gastos total no incrementó en 1"
    assert conteos_post["gastos_auditoria"]["total"] == conteos_base["gastos_auditoria"]["total"] + 2, "Gastos auditoría no incrementó en 2"

    # Hashes deterministas de integridad histórica
    hash_cargas_post, _ = calcular_hash_cargas_historicas(conn, 1718)
    hash_tickets_post, total_tickets_post = calcular_hash_tickets(conn, ticket_ids_base)
    hash_items_post, total_items_post = calcular_hash_ticket_items(conn, item_ids_base)

    assert total_tickets_post == len(ticket_ids_base), (
        f"Desaparecieron tickets preexistentes: se esperaban {len(ticket_ids_base)}, hallados {total_tickets_post}"
    )
    assert total_items_post == len(item_ids_base), (
        f"Desaparecieron items preexistentes: se esperaban {len(item_ids_base)}, hallados {total_items_post}"
    )

    assert hash_cargas_post == HASH_CARGAS_HISTORICAS_ESPERADO, f"Hash histórico de cargas alterado: {hash_cargas_post}"
    assert hash_tickets_post == hash_tickets_base, (
        f"Hash histórico de tickets alterado: {hash_tickets_post} != {hash_tickets_base}"
    )
    assert hash_items_post == hash_items_base, (
        f"Hash histórico de ticket_items alterado: {hash_items_post} != {hash_items_base}"
    )
    print("  ✓ Hashes deterministas históricos 100% intactos.")

    # Readiness final
    code_readiness_final, data_readiness_final = api_request("GET", "/api/system/readiness", token=token_alan)
    assert code_readiness_final == 200 and data_readiness_final.get("status") == "PREPARADO" and data_readiness_final.get("listo") is True
    print("  ✓ Endpoint /api/system/readiness final: HTTP 200, status='PREPARADO', listo=True.")

    conn.close()

    reporte["fase_5_verificacion_final"] = {
        "conteos_post": conteos_post,
        "hash_cargas_post": hash_cargas_post,
        "hash_tickets_post": hash_tickets_post,
        "hash_items_post": hash_items_post,
        "ticket_ids_base": ticket_ids_base,
        "item_ids_base": item_ids_base,
        "readiness_final_http_status": code_readiness_final,
        "readiness_final_status": data_readiness_final.get("status"),
        "readiness_final_listo": data_readiness_final.get("listo"),
    }
    reporte["exito_total"] = True

    print("\n" + "=" * 75)
    print("✓ PRUEBA SINTÉTICA DE ATRIBUCIÓN Y AUDITORÍA CRUZADA FINALIZADA CON ÉXITO")
    print("=" * 75)
    return reporte


def main():
    try:
        reporte = ejecutar_prueba()
        reporte_path = PROJECT_ROOT / "scripts" / "resultado_prueba_atribucion_dev.json"
        with open(reporte_path, "w", encoding="utf-8") as rf:
            json.dump(reporte, rf, indent=2, ensure_ascii=False)
        print(f"\nReporte persistido de forma segura en: {reporte_path}")
    except Exception as e:
        print(f"\n❌ Error durante la ejecución de la prueba: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
