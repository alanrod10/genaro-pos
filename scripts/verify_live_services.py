#!/usr/bin/env python3
"""Script de verificación integral de servicios dependientes y buscador en entorno live."""

import hashlib
import json
import requests
import sys
from pathlib import Path

BASE_URL = "http://127.0.0.1:8000"

def log(msg, ok=True):
    symbol = "✓" if ok else "✗"
    print(f"[{symbol}] {msg}")

def main():
    print("=== INICIANDO VERIFICACIÓN LIVE DE SERVICIOS DEPENDIENTES Y BUSCADOR ===")
    
    # 1. Login
    login_resp = requests.post(f"{BASE_URL}/api/auth/login", json={"username": "alan", "password": "alan2026"})
    if login_resp.status_code != 200:
        log(f"Fallo de autenticación: {login_resp.status_code} {login_resp.text}", ok=False)
        sys.exit(1)
    
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    log("Autenticación exitosa (Token JWT obtenido)")

    # 2. Mapa esperado de combinaciones válidas
    valid_matrix = {
        "PREPAY": ["SUBE", "Personal", "Movistar", "Claro"],
        "CLARO COMERCIOS": ["Claro"],
        "MERCADO PAGO": ["PREPAY", "SUBE", "Personal", "Movistar", "Claro"],
    }

    created_ids = []

    print("\n--- 2. Probando TODAS las combinaciones permitidas ---")
    counter = 2000
    for plat, dests in valid_matrix.items():
        for dest in dests:
            counter += 1
            payload = {
                "plataforma_origen": plat,
                "servicio_destino": dest,
                "monto_carga": 500,
                "monto_adicional": 50,
                "total_cobrado": 550,
                "pago_efvo": 550,
                "pago_transf": 0,
                "idempotency_key": f"test-serv-live-{plat.lower().replace(' ', '_')}-{dest.lower()}-{counter}",
            }
            res = requests.post(f"{BASE_URL}/api/services", json=payload, headers=headers)
            if res.status_code in (200, 201):
                data = res.json()
                assert data["plataforma_origen"] == plat, f"Expected {plat}, got {data['plataforma_origen']}"
                assert data["servicio_destino"] == dest, f"Expected {dest}, got {data['servicio_destino']}"
                assert data["servicio"] == dest, f"Expected servicio == {dest}, got {data['servicio']}"
                created_ids.append(data["id"])
                log(f"Permitido OK: '{plat}' -> '{dest}' (ID: {data['id']}, Recargo: ${data['monto_adicional']})")
            else:
                log(f"Error registrando '{plat}' -> '{dest}': {res.status_code} {res.text}", ok=False)
                sys.exit(1)

    print("\n--- 3. Probando combinaciones INVÁLIDAS / INCOMPATIBLES ---")
    invalid_cases = [
        ("CLARO COMERCIOS", "SUBE"),
        ("CLARO COMERCIOS", "Personal"),
        ("CLARO COMERCIOS", "Movistar"),
        ("CLARO COMERCIOS", "PREPAY"),
        ("PREPAY", "PREPAY"),
        ("PLATAFORMA_INVENTADA", "SUBE"),
        ("MERCADO PAGO", "SERVICIO_FANTASMA"),
        ("", "SUBE"),
        ("PREPAY", ""),
    ]

    for plat, dest in invalid_cases:
        counter += 1
        payload = {
            "plataforma_origen": plat,
            "servicio_destino": dest,
            "monto_carga": 500,
            "monto_adicional": 50,
            "total_cobrado": 550,
            "pago_efvo": 550,
            "pago_transf": 0,
            "idempotency_key": f"test-serv-invalid-{counter}",
        }
        res = requests.post(f"{BASE_URL}/api/services", json=payload, headers=headers)
        if res.status_code in (400, 422):
            log(f"Rechazo correcto (HTTP {res.status_code}): '{plat}' -> '{dest}'")
        else:
            log(f"FALLO: Combinación inválida '{plat}' -> '{dest}' fue aceptada (HTTP {res.status_code})", ok=False)
            sys.exit(1)

    print("\n--- 4. Comprobando registros históricos (origen NULL preservado) ---")
    history_res = requests.get(f"{BASE_URL}/api/services", headers=headers)
    assert history_res.status_code == 200
    all_cargas = history_res.json().get("cargas", [])
    historical_count = sum(1 for c in all_cargas if c.get("plataforma_origen") is None)
    log(f"Total cargas listadas: {len(all_cargas)}. Cargas históricas con origen NULL (sin inventar): {historical_count}")

    print("\n--- 5. Probando filtros por plataforma_origen y servicio_destino ---")
    filter_plat = requests.get(f"{BASE_URL}/api/services?plataforma_origen=CLARO+COMERCIOS", headers=headers).json().get("cargas", [])
    assert len(filter_plat) > 0, "No se encontraron registros de CLARO COMERCIOS"
    assert all(c["plataforma_origen"] == "CLARO COMERCIOS" for c in filter_plat), "Filtro plataforma_origen falló"
    log(f"Filtro plataforma_origen='CLARO COMERCIOS': {len(filter_plat)} resultados conformes")

    filter_dest = requests.get(f"{BASE_URL}/api/services?servicio_destino=Personal", headers=headers).json().get("cargas", [])
    assert len(filter_dest) > 0, "No se encontraron registros con destino Personal"
    assert all(c["servicio_destino"] == "Personal" for c in filter_dest), "Filtro servicio_destino falló"
    log(f"Filtro servicio_destino='Personal': {len(filter_dest)} resultados conformes")

    print("\n--- 6. Probando EDICIÓN con consistencia ---")
    target_id = created_ids[0]
    # Edición válida: cambiar a PREPAY -> Personal
    edit_valid = {
        "plataforma_origen": "PREPAY",
        "servicio_destino": "Personal",
        "monto_carga": 600,
        "monto_adicional": 60,
        "total_cobrado": 660,
        "pago_efvo": 0,
        "pago_transf": 660,
        "motivo": "Ajuste de prueba autorizado",
    }
    edit_res = requests.patch(f"{BASE_URL}/api/services/{target_id}", json=edit_valid, headers=headers)
    assert edit_res.status_code == 200, f"Fallo edición válida: {edit_res.text}"
    updated = edit_res.json()
    assert updated["plataforma_origen"] == "PREPAY" and updated["servicio_destino"] == "Personal"
    log(f"Edición válida OK en ID {target_id}: ahora es PREPAY -> Personal")

    # Edición incompatible: intentar CLARO COMERCIOS con Personal
    edit_invalid = {
        "plataforma_origen": "CLARO COMERCIOS",
        "servicio_destino": "Personal",
        "monto_carga": 600,
        "monto_adicional": 60,
        "total_cobrado": 660,
        "pago_efvo": 0,
        "pago_transf": 660,
        "motivo": "Intento de incoherencia",
    }
    edit_res_inv = requests.patch(f"{BASE_URL}/api/services/{target_id}", json=edit_invalid, headers=headers)
    assert edit_res_inv.status_code in (400, 422), "Edición inconsistente no fue rechazada"
    log(f"Rechazo correcto de edición inconsistente (HTTP {edit_res_inv.status_code})")

    print("\n--- 7. Verificación del Commercial Bridge & RADAR ---")
    trigger_res = requests.post(f"{BASE_URL}/api/tesoreria/commercial-bridge/trigger-drain", headers=headers)
    assert trigger_res.status_code == 200
    log(f"Trigger drain ejecutado: {trigger_res.json()}")

    bridge_status = requests.get(f"{BASE_URL}/api/tesoreria/commercial-bridge/status", headers=headers).json()
    log(f"Estado Commercial Bridge: Total={bridge_status['total']}, Published={bridge_status['published']}, Pending={bridge_status['pending']}, Failed={bridge_status['failed']}")

    print("\n--- 8. Verificación de Integridad de la Base de PRODUCCIÓN ---")
    prod_path = Path("/home/alanrod10/production/data/genaro-pos.db")
    assert prod_path.exists(), "La base de producción no existe"
    current_sha256 = hashlib.sha256(prod_path.read_bytes()).hexdigest()
    expected_sha256 = "8a1df991b8ab9bee27770047d6e49a000dfd53b64f957618ec0c272f071c6f76"
    assert current_sha256 == expected_sha256, f"¡ALERTA! SHA-256 no coincide: {current_sha256}"
    log(f"PRODUCCIÓN INTACTA: SHA-256 = {current_sha256}")

    print("\n=======================================================")
    print("¡TODAS LAS VERIFICACIONES EN STAGING RESULTARON EXITOSAS!")
    print("=======================================================")

if __name__ == "__main__":
    main()
