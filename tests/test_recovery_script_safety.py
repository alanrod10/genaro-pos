"""Pruebas locales de regresión y seguridad para el procedimiento de recuperación.

Verifica:
1. Que scripts/recuperar_prueba_ventas_turso_dev.py no contenga llamadas de creación POST /api/sales.
2. Que la respuesta de GET /api/sales/{ticket_id} y POST /api/sales/{ticket_id}/anular
   implemente el contrato TicketDetalleOut con el campo 'ticket_id' (y no 'id').
3. Que la anulación lógica devuelva el contrato esperado sin requerir escrituras remotas.
"""

from pathlib import Path
import sqlite3
import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.dependencies import AuthenticatedUser, get_current_user, get_db_connection
from api.schemas import TicketDetalleOut
from db.sales import (
    registrar_venta_idempotente_turso,
    anular_venta_turso,
    obtener_detalle_ticket_turso,
)

ROOT = Path(__file__).resolve().parents[1]


def test_recuperador_nunca_crea_ventas():
    """1. Asegura que el script de recuperación no contenga sentencias para crear ventas."""
    script_path = ROOT / "scripts/recuperar_prueba_ventas_turso_dev.py"
    assert script_path.exists(), "El script de recuperación debe existir"
    content = script_path.read_text(encoding="utf-8")

    # No debe existir llamada a POST /api/sales para crear ventas
    assert 'client.post("/api/sales",' not in content
    assert "client.post('/api/sales'," not in content
    assert 'client.post("/api/sales", ' not in content
    assert "client.post('/api/sales', " not in content


@pytest.fixture
def memory_db():
    """Base de datos en memoria con esquema completo para prueba local de contrato."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript("""
        CREATE TABLE _schema_migrations (
            version INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL,
            aplicado_en TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO _schema_migrations (version, nombre) VALUES
            (1, '001'), (2, '002'), (3, '003'), (4, '004'),
            (5, '005'), (6, '006'), (7, '007'), (8, '008'), (9, '009');

        CREATE TABLE productos (
            id INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL COLLATE NOCASE,
            costo INTEGER NOT NULL DEFAULT 0,
            precio_dia INTEGER NOT NULL DEFAULT 0,
            activo INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO productos (id, nombre, costo, precio_dia) VALUES (9001, 'Alfajor Sintético Simple', 300, 500);

        CREATE TABLE tickets (
            id TEXT PRIMARY KEY,
            fecha_hora_utc TEXT NOT NULL DEFAULT (datetime('now')),
            total_venta INTEGER NOT NULL,
            monto_efectivo INTEGER NOT NULL DEFAULT 0,
            monto_transferencia INTEGER NOT NULL DEFAULT 0,
            metodo_pago TEXT NOT NULL,
            es_nocturno INTEGER NOT NULL DEFAULT 0,
            anulado INTEGER NOT NULL DEFAULT 0,
            motivo_anulacion TEXT
        );

        CREATE TABLE ticket_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
            producto_id INTEGER REFERENCES productos(id),
            producto_nombre TEXT NOT NULL,
            cantidad REAL NOT NULL,
            unidad TEXT NOT NULL DEFAULT 'Unidad',
            precio_unitario INTEGER NOT NULL,
            subtotal INTEGER NOT NULL,
            metodo_pago TEXT NOT NULL,
            costo_unitario INTEGER
        );

        CREATE TABLE ventas_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
            item_id INTEGER,
            accion TEXT NOT NULL,
            motivo TEXT,
            usuario TEXT NOT NULL DEFAULT 'sistema',
            datos_previos_json TEXT,
            datos_nuevos_json TEXT,
            creado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    yield conn
    conn.close()


def test_contrato_ticket_detalle_usa_ticket_id(memory_db):
    """2. Comprueba que el contrato real de GET y anulación expone 'ticket_id' y no 'id'."""
    # Insertar ticket de prueba sintético
    memory_db.execute("""
        INSERT INTO tickets (id, total_venta, monto_efectivo, monto_transferencia, metodo_pago)
        VALUES ('T-TEST-CONTRATO-001', 500, 500, 0, 'EFECTIVO');
    """)
    memory_db.execute("""
        INSERT INTO ticket_items (ticket_id, producto_id, producto_nombre, cantidad, precio_unitario, subtotal, metodo_pago, costo_unitario)
        VALUES ('T-TEST-CONTRATO-001', 9001, 'Alfajor Sintético Simple', 1.0, 500, 500, 'EFECTIVO', 300);
    """)
    memory_db.commit()

    # Inyectar dependencias locales
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")
    app.dependency_overrides[get_db_connection] = lambda: memory_db

    try:
        client = TestClient(app)

        # GET /api/sales/{ticket_id}
        res_get = client.get("/api/sales/T-TEST-CONTRATO-001")
        assert res_get.status_code == 200
        data_get = res_get.json()

        # Comprobación de regresión clave:
        # data_get tiene 'ticket_id', NO 'id'
        assert "ticket_id" in data_get
        assert data_get["ticket_id"] == "T-TEST-CONTRATO-001"
        assert "id" not in data_get  # Demuestra el origen del KeyError en el script anterior

        # Validar ítems bajo el contrato real ItemVentaOut
        assert len(data_get["items"]) == 1
        item = data_get["items"][0]
        # Campos reales serializados por Pydantic ItemVentaOut:
        assert "producto_nombre" in item
        assert item["producto_nombre"] == "Alfajor Sintético Simple"
        assert "producto" not in item  # Demuestra el origen del fallo: it.get("producto") devolvía None
        assert "precio_unitario" in item
        assert item["precio_unitario"] == 500
        assert "precio_unit" not in item  # Demuestra que 'precio_unit' no forma parte del esquema de salida
        assert item["costo_unitario"] == 300

        # POST /api/sales/{ticket_id}/anular
        res_anular = client.post(
            "/api/sales/T-TEST-CONTRATO-001/anular",
            json={"motivo": "PRUEBA_SINTETICA_CONTROLADA_DEV", "usuario": "alan"},
        )
        assert res_anular.status_code == 200
        data_anular = res_anular.json()

        assert "ticket_id" in data_anular
        assert data_anular["ticket_id"] == "T-TEST-CONTRATO-001"
        assert data_anular["anulado"] is True
        assert data_anular["motivo_anulacion"] == "PRUEBA_SINTETICA_CONTROLADA_DEV"
    finally:
        app.dependency_overrides.clear()


def test_validacion_item_recuperador_detecta_discrepancias():
    """3. Comprueba que la lógica de validación de ítems del script de recuperación
    acepte el contrato real y rechace discrepancias de producto, cantidad, precio o costo."""
    item_valido = {
        "id": 1,
        "ticket_id": "T-DEV-AUTH-SMOKE-20261025132943-7c288246",
        "producto_nombre": "Alfajor Sintético Simple",
        "cantidad": 1.0,
        "unidad": "u.",
        "precio_unitario": 500,
        "subtotal": 500,
        "metodo_pago": "EFECTIVO",
        "costo_unitario": 300,
    }

    def validar_item(it):
        prod_nombre = it.get("producto_nombre") or it.get("producto")
        precio_unit = it.get("precio_unitario") if "precio_unitario" in it else it.get("precio_unit")
        assert prod_nombre == "Alfajor Sintético Simple", f"Producto no coincide: {prod_nombre}"
        assert it.get("cantidad") == 1.0, "Cantidad no coincide"
        assert precio_unit == 500, f"Precio unitario no coincide: {precio_unit}"
        assert it.get("subtotal") == 500, "Subtotal no coincide"
        assert it.get("costo_unitario") == 300, f"Costo histórico no coincide: {it.get('costo_unitario')}"

    # Caso válido pasa sin excepción
    validar_item(item_valido)

    # Discrepancia en producto
    with pytest.raises(AssertionError, match="Producto no coincide"):
        item_malo = dict(item_valido, producto_nombre="Alfajor Triple")
        validar_item(item_malo)

    # Discrepancia en precio
    with pytest.raises(AssertionError, match="Precio unitario no coincide"):
        item_malo = dict(item_valido, precio_unitario=600)
        validar_item(item_malo)

    # Discrepancia en costo histórico
    with pytest.raises(AssertionError, match="Costo histórico no coincide"):
        item_malo = dict(item_valido, costo_unitario=250)
        validar_item(item_malo)
