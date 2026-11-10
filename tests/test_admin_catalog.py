"""Pruebas unitarias e integración local para administración de catálogo y auditoría."""
import sqlite3
import pytest
from fastapi.testclient import TestClient
from api.main import app
from api.dependencies import AuthenticatedUser, get_current_user, get_db_connection
from db.admin_catalog import (
    ErrorDuplicadoProducto,
    ErrorValidacionProducto,
    ProductoNoEncontrado,
    cambiar_estado_producto_turso,
    crear_producto_turso,
    editar_producto_turso,
    listar_productos_admin_turso,
    obtener_historial_auditoria_turso,
    obtener_producto_por_id_turso,
)


@pytest.fixture
def sqlite_test_db(tmp_path):
    """Crea una base de datos SQLite aislada con esquema de catálogo y tabla de auditoría."""
    db_file = tmp_path / "test_admin_catalog.db"
    conn = sqlite3.connect(str(db_file), check_same_thread=False, autocommit=True)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL COLLATE NOCASE,
            categoria TEXT NOT NULL DEFAULT 'Otro',
            proveedor TEXT NOT NULL DEFAULT 'General',
            unidad TEXT NOT NULL DEFAULT 'Unidad',
            costo INTEGER NOT NULL DEFAULT 0 CHECK (costo >= 0),
            precio_dia INTEGER NOT NULL CHECK (precio_dia >= 0),
            precio_noche INTEGER NOT NULL DEFAULT 0,
            margen_ratio REAL GENERATED ALWAYS AS (
                CASE WHEN costo > 0 THEN ROUND((CAST(precio_dia AS REAL) - costo) / costo, 4) ELSE 0.0 END
            ) VIRTUAL,
            activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
            fecha_actualizacion TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS productos_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL,
            accion TEXT NOT NULL CHECK (accion IN ('CREAR', 'EDITAR', 'ACTIVAR', 'DESACTIVAR')),
            valores_anteriores TEXT,
            valores_nuevos TEXT NOT NULL,
            fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
            actor TEXT NOT NULL DEFAULT 'sistema',
            FOREIGN KEY (producto_id) REFERENCES productos (id) ON DELETE CASCADE
        );
        """
    )

    # Insertar 2 productos iniciales
    conn.execute(
        """
        INSERT INTO productos (nombre, categoria, proveedor, unidad, costo, precio_dia, precio_noche, activo, fecha_actualizacion)
        VALUES ('Gaseosa Cola 500ml', 'Bebidas', 'Distribuidora Central', 'Unidad', 800, 1200, 1200, 1, '2026-10-24 10:00:00'),
               ('Alfajor Triple', 'Golosinas', 'Dulces del Norte', 'Unidad', 500, 800, 800, 1, '2026-10-24 10:00:00');
        """
    )

    yield conn
    conn.close()


@pytest.fixture
def api_client(sqlite_test_db):
    """Cliente FastAPI con inyección de la base de datos de test local."""
    def _override_get_db():
        return sqlite_test_db

    app.dependency_overrides[get_db_connection] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


# ==============================================================================
# PRUEBAS DEL ADAPTADOR db/admin_catalog.py
# ==============================================================================

def test_crear_producto_valido_con_auditoria(sqlite_test_db):
    """Verifica que un producto se cree correctamente con precio único y registro en auditoría."""
    datos = {
        "nombre": "Agua con Gas 500ml",
        "categoria": "Bebidas",
        "proveedor": "Distribuidora Central",
        "unidad": "Unidad",
        "costo": 600,
        "precio_venta": 1000,
        "activo": True,
    }

    prod = crear_producto_turso(datos, conn=sqlite_test_db, actor="test_runner")

    assert prod["id_producto"] > 2
    assert prod["nombre"] == "Agua con Gas 500ml"
    assert prod["precio_venta"] == 1000
    assert prod["precio_dia"] == 1000
    assert prod["precio_noche"] == 1000
    assert prod["costo"] == 600
    assert prod["margen_porcentaje"] == round((1000 - 600) / 600, 4)
    assert prod["activo"] is True

    # Verificar que el registro de auditoría fue creado
    audits = obtener_historial_auditoria_turso(prod["id_producto"], conn=sqlite_test_db)
    assert len(audits) == 1
    assert audits[0]["accion"] == "CREAR"
    assert audits[0]["valores_anteriores"] is None
    assert audits[0]["valores_nuevos"]["nombre"] == "Agua con Gas 500ml"
    assert audits[0]["actor"] == "test_runner"


def test_crear_producto_validaciones_negocio(sqlite_test_db):
    """Valida que nombres vacíos, costos negativos o precios inválidos sean rechazados."""
    # Nombre vacío
    with pytest.raises(ErrorValidacionProducto):
        crear_producto_turso({"nombre": "   ", "costo": 100, "precio_venta": 200}, conn=sqlite_test_db)

    # Costo negativo
    with pytest.raises(ErrorValidacionProducto):
        crear_producto_turso({"nombre": "Vino", "costo": -10, "precio_venta": 200}, conn=sqlite_test_db)

    # Precio venta menor o igual a cero
    with pytest.raises(ErrorValidacionProducto):
        crear_producto_turso({"nombre": "Vino", "costo": 100, "precio_venta": 0}, conn=sqlite_test_db)

    # Duplicado insensible a mayúsculas
    with pytest.raises(ErrorDuplicadoProducto):
        crear_producto_turso({"nombre": "GASEOSA COLA 500ML", "costo": 500, "precio_venta": 900}, conn=sqlite_test_db)


def test_editar_producto_sin_sobrescribir_otros(sqlite_test_db):
    """Verifica que la modificación por ID actualice únicamente el producto indicado y audite cambios."""
    # Modificar únicamente el costo y precio de Alfajor Triple (id=2)
    cambios = {"costo": 550, "precio_venta": 950}
    editado = editar_producto_turso(2, cambios, conn=sqlite_test_db, actor="admin_user")

    assert editado["id_producto"] == 2
    assert editado["costo"] == 550
    assert editado["precio_venta"] == 950
    assert editado["precio_dia"] == 950
    # Los demás campos permanecen intactos
    assert editado["nombre"] == "Alfajor Triple"
    assert editado["categoria"] == "Golosinas"

    # Gaseosa Cola (id=1) no debió ser alterada
    gaseosa = obtener_producto_por_id_turso(1, conn=sqlite_test_db)
    assert gaseosa["costo"] == 800
    assert gaseosa["precio_venta"] == 1200

    # Auditoría
    audits = obtener_historial_auditoria_turso(2, conn=sqlite_test_db)
    assert len(audits) == 1
    assert audits[0]["accion"] == "EDITAR"
    assert audits[0]["valores_anteriores"]["costo"] == 500
    assert audits[0]["valores_nuevos"]["costo"] == 550


def test_desactivar_y_reactivar_producto(sqlite_test_db):
    """Verifica desactivación lógica y posterior reactivación sin borrado físico."""
    # Desactivar
    res_desc = cambiar_estado_producto_turso(1, activo=False, conn=sqlite_test_db)
    assert res_desc["activo"] is False

    # El producto sigue existiendo físicamente en la base
    prod = obtener_producto_por_id_turso(1, conn=sqlite_test_db)
    assert prod is not None
    assert prod["activo"] is False

    # Reactivar
    res_act = cambiar_estado_producto_turso(1, activo=True, conn=sqlite_test_db)
    assert res_act["activo"] is True

    # Comprobar eventos de auditoría
    audits = obtener_historial_auditoria_turso(1, conn=sqlite_test_db)
    assert len(audits) == 2
    assert audits[0]["accion"] == "ACTIVAR"
    assert audits[1]["accion"] == "DESACTIVAR"


def test_operaciones_producto_inexistente_genera_404(sqlite_test_db):
    """Intentar editar o cambiar estado de un ID inexistente genera ProductoNoEncontrado."""
    with pytest.raises(ProductoNoEncontrado):
        editar_producto_turso(99999, {"precio_venta": 500}, conn=sqlite_test_db)

    with pytest.raises(ProductoNoEncontrado):
        cambiar_estado_producto_turso(99999, activo=False, conn=sqlite_test_db)


# ==============================================================================
# PRUEBAS DE LOS ENDPOINTS API FASTAPI
# ==============================================================================

def test_api_crear_producto(api_client):
    """POST /api/catalog crea producto y retorna 201."""
    payload = {
        "nombre": "Chocolate Blanco 100g",
        "categoria": "Golosinas",
        "proveedor": "Dulces del Norte",
        "unidad": "Unidad",
        "costo": 400,
        "precio_venta": 750,
        "activo": True,
    }
    resp = api_client.post("/api/catalog", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["nombre"] == "Chocolate Blanco 100g"
    assert data["precio_venta"] == 750
    assert data["activo"] is True


def test_api_crear_producto_duplicado_retorna_409(api_client):
    """POST /api/catalog con nombre ya existente retorna 409 Conflict."""
    payload = {
        "nombre": "Gaseosa Cola 500ml",
        "costo": 800,
        "precio_venta": 1200,
    }
    resp = api_client.post("/api/catalog", json=payload)
    assert resp.status_code == 409


def test_api_editar_producto(api_client):
    """PATCH /api/catalog/{id} actualiza campos específicos."""
    resp = api_client.patch("/api/catalog/1", json={"precio_venta": 1350, "costo": 850})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id_producto"] == 1
    assert data["precio_venta"] == 1350
    assert data["costo"] == 850
    assert data["nombre"] == "Gaseosa Cola 500ml"  # Nombre conservado


def test_api_cambiar_estado_producto(api_client):
    """PATCH /api/catalog/{id}/status modifica el estado lógico."""
    resp = api_client.patch("/api/catalog/1/status", json={"activo": False})
    assert resp.status_code == 200
    assert resp.json()["activo"] is False

    # Consulta estándar no incluye inactivos
    resp_cat = api_client.get("/api/catalog")
    ids = [p["id_producto"] for p in resp_cat.json()["productos"]]
    assert 1 not in ids

    # Consulta con include_inactive=true sí incluye inactivos
    resp_all = api_client.get("/api/catalog?include_inactive=true")
    ids_all = [p["id_producto"] for p in resp_all.json()["productos"]]
    assert 1 in ids_all


def test_api_producto_inexistente_retorna_404(api_client):
    """Operar sobre un producto que no existe retorna 404 Not Found."""
    resp_patch = api_client.patch("/api/catalog/9999", json={"precio_venta": 100})
    assert resp_patch.status_code == 404

    resp_status = api_client.patch("/api/catalog/9999/status", json={"activo": False})
    assert resp_status.status_code == 404


def test_crear_y_editar_producto_actualiza_margen_ratio_en_bd():
    """P2-02: Verifica que crear_producto_turso y editar_producto_turso persistan margen_ratio
    en la tabla productos cuando margen_ratio es una columna física normal (no generada),
    manteniendo consistencia con preventistas."""
    from db.admin_catalog import crear_producto_turso, editar_producto_turso
    from db.preventistas import listar_productos_por_proveedor_turso

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL COLLATE NOCASE,
            categoria TEXT NOT NULL DEFAULT 'Otro',
            proveedor TEXT NOT NULL DEFAULT 'General',
            unidad TEXT NOT NULL DEFAULT 'Unidad',
            costo INTEGER NOT NULL DEFAULT 0 CHECK (costo >= 0),
            precio_dia INTEGER NOT NULL CHECK (precio_dia >= 0),
            precio_noche INTEGER NOT NULL DEFAULT 0,
            margen_ratio REAL DEFAULT 0.0,
            activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
            fecha_actualizacion TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE productos_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL,
            accion TEXT NOT NULL,
            valores_anteriores TEXT,
            valores_nuevos TEXT NOT NULL,
            fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
            actor TEXT NOT NULL DEFAULT 'sistema'
        );
        """
    )

    creado = crear_producto_turso(
        {
            "nombre": "Agua Mineral 1.5L",
            "categoria": "Bebidas",
            "proveedor": "Villavicencio",
            "unidad": "Unidad",
            "costo": 1000,
            "precio_venta": 1500,
        },
        conn=conn,
        actor="alan",
    )
    pid = creado["id_producto"]
    row_create = conn.execute("SELECT margen_ratio FROM productos WHERE id = ?", (pid,)).fetchone()
    assert row_create is not None
    assert row_create[0] == pytest.approx(0.5)

    # Editar precio_venta a 2000 (con costo 1000 -> margen_ratio = 1.0)
    editar_producto_turso(pid, {"precio_venta": 2000}, conn=conn, actor="yilda")
    row_edit = conn.execute("SELECT margen_ratio FROM productos WHERE id = ?", (pid,)).fetchone()
    assert row_edit[0] == pytest.approx(1.0)

    # Verificar que listar_productos_por_proveedor_turso lea el nuevo margen_ratio
    lista_prov = listar_productos_por_proveedor_turso("Villavicencio", conn=conn)
    assert len(lista_prov) == 1
    assert lista_prov[0]["margen_ratio"] == pytest.approx(1.0)
    assert lista_prov[0]["margen_porcentaje"] == pytest.approx(100.0)
    conn.close()


def test_smart_delete_sin_ventas_borrado_fisico(api_client, sqlite_test_db):
    """Smart Delete: Si el producto NO tiene ventas en ticket_items, ejecuta DELETE físico."""
    # Crear tabla ticket_items vacía
    sqlite_test_db.execute(
        """
        CREATE TABLE IF NOT EXISTS ticket_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            producto_id INTEGER,
            producto_nombre TEXT NOT NULL,
            cantidad REAL NOT NULL,
            unidad TEXT NOT NULL DEFAULT 'Unidad',
            precio_unitario INTEGER NOT NULL,
            subtotal INTEGER NOT NULL,
            metodo_pago TEXT NOT NULL
        );
        """
    )

    resp = api_client.delete("/api/catalog/2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id_producto"] == 2
    assert data["eliminado_fisico"] is True
    assert data["tiene_ventas"] is False
    assert data["accion"] == "borrado_fisico"

    # Ya no existe en la base de datos física
    assert obtener_producto_por_id_turso(2, conn=sqlite_test_db) is None

    # No aparece en el catálogo de caja
    resp_cat = api_client.get("/api/catalog")
    ids = [p["id_producto"] for p in resp_cat.json()["productos"]]
    assert 2 not in ids


def test_smart_delete_con_ventas_baja_logica(api_client, sqlite_test_db):
    """Smart Delete: Si el producto SÍ tiene ventas en ticket_items, ejecuta UPDATE activo = 0."""
    sqlite_test_db.execute(
        """
        CREATE TABLE IF NOT EXISTS ticket_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            producto_id INTEGER,
            producto_nombre TEXT NOT NULL,
            cantidad REAL NOT NULL,
            unidad TEXT NOT NULL DEFAULT 'Unidad',
            precio_unitario INTEGER NOT NULL,
            subtotal INTEGER NOT NULL,
            metodo_pago TEXT NOT NULL
        );
        """
    )
    sqlite_test_db.execute(
        """
        INSERT INTO ticket_items (ticket_id, producto_id, producto_nombre, cantidad, unidad, precio_unitario, subtotal, metodo_pago)
        VALUES ('T-001', 1, 'Gaseosa Cola 500ml', 1, 'Unidad', 1200, 1200, 'EFECTIVO');
        """
    )

    resp = api_client.delete("/api/catalog/1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id_producto"] == 1
    assert data["eliminado_fisico"] is False
    assert data["tiene_ventas"] is True
    assert data["accion"] == "baja_logica"
    assert data["activo"] is False

    # Sigue existiendo en la base de datos pero con activo = False
    prod = obtener_producto_por_id_turso(1, conn=sqlite_test_db)
    assert prod is not None
    assert prod["activo"] is False

    # Desaparece del listado de caja registradora (activos)
    resp_cat = api_client.get("/api/catalog")
    ids = [p["id_producto"] for p in resp_cat.json()["productos"]]
    assert 1 not in ids

    # Eliminar producto inexistente retorna 404
    resp_404 = api_client.delete("/api/catalog/99999")
    assert resp_404.status_code == 404


def test_script_normalizar_catalogo_v4_heuristica():
    """Verifica el mapeo heurístico de categorías antiguas a las 10 categorías canónicas."""
    from scripts.normalizar_catalogo_v4 import CATEGORIAS_CANONICAS, sugerir_categoria_canonica

    assert sugerir_categoria_canonica("Golosina", "Caramelos Menta") == "Golosinas y Chocolates"
    assert sugerir_categoria_canonica("golosinas", "Alfajor Triple Chocolate") == "Galletitas y Alfajores"
    assert sugerir_categoria_canonica("Bebidas", "Gaseosa Coca Cola Original 500ml") == "Bebidas e Infusiones"
    assert sugerir_categoria_canonica("Bebidas", "Cerveza Quilmes 1L") == "Bebidas con Alcohol"
    assert sugerir_categoria_canonica("varios", "Papas Fritas Lays 85g") == "Snacks y Salados"
    assert sugerir_categoria_canonica("Servicios", "Fotocopia B/N") == "Librería y Regalería"
    assert sugerir_categoria_canonica("Limpieza", "Jabón Tocador") == "Higiene y Cuidado Personal"
    assert sugerir_categoria_canonica("Panadería", "Mignon") == "Panadería y Fiambrería"
    assert sugerir_categoria_canonica("Almacén", "Leche Entera 1L") == "Almacén y Lácteos"
    assert sugerir_categoria_canonica("Tabaquería", "Cigarrillos Box 20") == "Cigarrillos y Tabaquería"
    for cat in CATEGORIAS_CANONICAS:
        assert sugerir_categoria_canonica(cat, "Producto Genérico") == cat


