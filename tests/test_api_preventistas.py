"""Pruebas de integración para los endpoints de Preventistas y Pedidos (api/routers/preventistas.py)."""

from pathlib import Path
import sqlite3
import pytest
from fastapi.testclient import TestClient
from api.main import app
from api.dependencies import AuthenticatedUser, get_current_user, get_db_connection

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_004 = ROOT / "db/migrations/004_catalog_audit.sql"
MIGRATION_008 = ROOT / "db/migrations/008_preventistas_y_pedidos.sql"


@pytest.fixture
def mock_db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute(
        """
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL,
            categoria TEXT NOT NULL DEFAULT 'Otro',
            proveedor TEXT NOT NULL DEFAULT 'General',
            unidad TEXT NOT NULL DEFAULT 'Unidad',
            costo REAL NOT NULL DEFAULT 0,
            precio_dia REAL NOT NULL DEFAULT 0,
            precio_noche REAL NOT NULL DEFAULT 0,
            margen_ratio REAL NOT NULL DEFAULT 0,
            activo INTEGER NOT NULL DEFAULT 1,
            fecha_actualizacion TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, precio_noche, margen_ratio, activo)
        VALUES
            (10, 'Alfajor Jorgito', 'Golosinas', 'Distribuidora Dulce', 'Unidad', 500, 800, 800, 0.6, 1),
            (20, 'Caramelos Sugus', 'Golosinas', 'Distribuidora Dulce', 'Unidad', 100, 150, 150, 0.5, 1);
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _schema_migrations (
            version INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL,
            aplicado_en TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.executescript(MIGRATION_004.read_text(encoding="utf-8"))
    conn.executescript(MIGRATION_008.read_text(encoding="utf-8"))
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def client(mock_db):
    app.dependency_overrides[get_db_connection] = lambda: mock_db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_api_get_proveedores(client):
    response = client.get("/api/preventistas/proveedores")
    assert response.status_code == 200
    data = response.json()
    assert "Distribuidora Dulce" in data


def test_api_get_productos_por_proveedor(client):
    response = client.get("/api/preventistas/productos?proveedor=Distribuidora+Dulce")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["nombre"] == "Alfajor Jorgito"


def test_api_update_precios_preventista(client):
    payload = {
        "proveedor": "Distribuidora Dulce",
        "cambios": [
            {"id_producto": 10, "costo": 600, "precio_venta": 1000}
        ]
    }
    response = client.post("/api/preventistas/precios", json=payload)
    assert response.status_code == 200
    res = response.json()
    assert res["actualizados"] == 1

    # Verificar que el nuevo precio se refleja
    check = client.get("/api/preventistas/productos?proveedor=Distribuidora+Dulce")
    p10 = [p for p in check.json() if p["id_producto"] == 10][0]
    assert p10["costo"] == 600.0
    assert p10["precio_venta"] == 1000.0


def test_api_alta_rapida_producto(client):
    payload = {
        "proveedor": "Distribuidora Dulce",
        "nombre": "Chupetín Pico Dulce",
        "categoria": "Golosinas",
        "unidad": "Unidad",
        "costo": 200,
        "precio_venta": 350,
    }
    response = client.post("/api/preventistas/productos/alta-rapida", json=payload)
    assert response.status_code == 201
    prod = response.json()
    assert prod["nombre"] == "Chupetín Pico Dulce"
    assert prod["proveedor"] == "Distribuidora Dulce"


def test_api_pedidos_flow(client):
    # 1. Crear pedido
    payload_pedido = {
        "proveedor": "Distribuidora Dulce",
        "fecha": "2026-10-24",
        "observaciones": "Pedido semanal",
        "items": [
            {"producto_id": 10, "producto_nombre": "Alfajor Jorgito", "cantidad": 24, "costo_unitario": 500}
        ]
    }
    res_crear = client.post("/api/preventistas/pedidos", json=payload_pedido)
    assert res_crear.status_code == 201
    pedido = res_crear.json()
    ped_id = pedido["id"]
    assert pedido["total"] == 12000.0
    assert pedido["estado"] == "PENDIENTE"

    # 2. Consultar listado
    res_lista = client.get("/api/preventistas/pedidos?proveedor=Distribuidora+Dulce")
    assert res_lista.status_code == 200
    assert len(res_lista.json()) == 1

    # 3. Consultar detalle
    res_det = client.get(f"/api/preventistas/pedidos/{ped_id}")
    assert res_det.status_code == 200
    det = res_det.json()
    assert len(det["items"]) == 1
    assert det["items"][0]["cantidad"] == 24.0

    # 4. Cambiar estado a RECIBIDO
    res_est = client.patch(f"/api/preventistas/pedidos/{ped_id}/estado", json={"estado": "RECIBIDO"})
    assert res_est.status_code == 200
    assert res_est.json()["estado"] == "RECIBIDO"

    # 5. Cancelar pedido
    res_canc = client.post(f"/api/preventistas/pedidos/{ped_id}/cancelar", json={"motivo": "Devolución pactada"})
    assert res_canc.status_code == 200
    assert res_canc.json()["estado"] == "CANCELADO"
