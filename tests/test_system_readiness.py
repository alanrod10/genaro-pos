"""Pruebas exhaustivas de preparación, integridad del esquema y prevención de DDL en operaciones normales.

Verifica:
1. Ninguna operación normal de la API ejecuta DDL (CREATE, ALTER, DROP).
2. Detección precisa de bases de datos incompletas (ej. solo 001–003 o solo 001–007).
3. Detección de inconsistencias: versión registrada sin estructura física o viceversa.
4. Operaciones de escritura fallan con HTTP 503 SCHEMA_NOT_READY ante estructuras faltantes.
5. Captura obligatoria de costo histórico en ventas conectadas (sin degradación silenciosa).
6. Lecturas degradan de forma controlada sin excepciones 500 no capturadas.
7. Autenticación funcional incluso con esquema no preparado.
8. Endpoint protegido GET /api/system/readiness.
9. Dashboard distingue datos vacíos válidos de esquema incompleto.
10. Operación completa y exitosa con esquema 001–009 aplicado.
"""

from pathlib import Path
import sqlite3
import pytest
from fastapi.testclient import TestClient

from api.dependencies import AuthenticatedUser, get_current_user, get_db_connection
from api.auth import generate_password_hash
from api.main import app
from db.migrations_runner import ejecutar_migraciones
from db.readiness import (
    ErrorEsquemaNoPreparado,
    comprobar_preparacion_esquema,
    verificar_esquema_listo,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "db/migrations"
TEST_SECRET_KEY = "clave_secreta_de_prueba_con_mas_de_32_caracteres_12345"


def _crear_base_con_catalogo_baseline() -> sqlite3.Connection:
    """Crea una base SQLite en memoria con el catálogo preexistente de Turso DEV."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute(
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
            activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
            fecha_actualizacion TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    return conn


@pytest.fixture(autouse=True)
def setup_auth_env(monkeypatch):
    """Configura variables de entorno ficticias para pruebas de autenticación."""
    alan_hash = generate_password_hash("pos", iterations=1_000)
    yilda_hash = generate_password_hash("pos", iterations=1_000)

    monkeypatch.setenv("GENARO_AUTH_SECRET_KEY", TEST_SECRET_KEY)
    monkeypatch.setenv("GENARO_USER_ALAN_HASH", alan_hash)
    monkeypatch.setenv("GENARO_USER_YILDA_HASH", yilda_hash)
    monkeypatch.setenv("GENARO_AUTH_TOKEN_EXPIRE_MINUTES", "60")


class DDLInterceptionSpy:
    """Spy para interceptar conexiones y verificar que ninguna instrucción DDL se ejecute."""

    def __init__(self, conn):
        self._conn = conn
        self.statements = []
        self.ddl_executed = []

    def execute(self, sql, *args, **kwargs):
        s_upper = sql.strip().upper()
        self.statements.append(sql.strip())
        if any(ddl in s_upper for ddl in ["CREATE TABLE", "ALTER TABLE", "DROP TABLE", "CREATE INDEX"]):
            self.ddl_executed.append(sql.strip())
        return self._conn.execute(sql, *args, **kwargs)

    def executemany(self, sql, *args, **kwargs):
        return self._conn.executemany(sql, *args, **kwargs)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture
def auth_client():
    """TestClient con autenticación simulada para Alan."""
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        username="alan", display_name="Alan"
    )
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ==============================================================================
# 1. VERIFICACIÓN DE DETECCIÓN DE ESQUEMA Y MIGRACIONES
# ==============================================================================

def test_readiness_detecta_base_incompleta_solo_001_a_003():
    """Verifica que una base con solo las migraciones 001–003 se detecte como MIGRACIONES_PENDIENTES."""
    conn = _crear_base_con_catalogo_baseline()
    ejecutar_migraciones(conn, hasta_version=3, directorio=MIGRATIONS_DIR)

    diag = comprobar_preparacion_esquema(conn)
    assert diag["status"] == "MIGRACIONES_PENDIENTES"
    assert diag["listo"] is False
    assert diag["versiones_aplicadas"] == [1, 2, 3]
    assert diag["migraciones_pendientes"] == [4, 5, 6, 7, 8, 9, 10, 11]
    assert "productos_auditoria" in diag["tablas_faltantes"]
    assert "cargas_servicios_auditoria" in diag["tablas_faltantes"]
    assert "ventas_auditoria" in diag["tablas_faltantes"]
    assert "pedidos_proveedor" in diag["tablas_faltantes"]
    assert "auth_scram_challenges" in diag["tablas_faltantes"]
    assert "ticket_items.costo_unitario" in diag["columnas_faltantes"]

    with pytest.raises(ErrorEsquemaNoPreparado):
        verificar_esquema_listo(conn)
    conn.close()


def test_readiness_detecta_base_incompleta_solo_001_a_007():
    """Verifica que una base con migraciones 001–007 reporte pendientes 008 a 011."""
    conn = _crear_base_con_catalogo_baseline()
    ejecutar_migraciones(conn, hasta_version=7, directorio=MIGRATIONS_DIR)

    diag = comprobar_preparacion_esquema(conn)
    assert diag["status"] == "MIGRACIONES_PENDIENTES"
    assert diag["listo"] is False
    assert diag["versiones_aplicadas"] == [1, 2, 3, 4, 5, 6, 7]
    assert diag["migraciones_pendientes"] == [8, 9, 10, 11]
    assert "pedidos_proveedor" in diag["tablas_faltantes"]
    assert "auth_scram_challenges" in diag["tablas_faltantes"]
    assert "ticket_items.costo_unitario" in diag["columnas_faltantes"]
    conn.close()


def test_readiness_detecta_inconsistencia_version_sin_estructura_fisica():
    """Verifica detección de versión registrada en _schema_migrations sin las estructuras físicas."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    # Creamos _schema_migrations y registramos versiones 1..9 sin haber creado las tablas
    conn.execute(
        """
        CREATE TABLE _schema_migrations (
            version INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL,
            aplicado_en TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    for v in range(1, 10):
        conn.execute("INSERT INTO _schema_migrations (version, nombre) VALUES (?, ?)", (v, f"mig_{v}"))
    conn.commit()

    diag = comprobar_preparacion_esquema(conn)
    assert diag["status"] == "ESQUEMA_INCONSISTENTE"
    assert diag["listo"] is False
    assert len(diag["inconsistencias"]) > 0
    # Debe detectar que las tablas y columnas no existen a pesar de figurar registradas
    assert any("tabla 'productos' no existe" in inc for inc in diag["inconsistencias"])
    conn.close()


def test_readiness_detecta_inconsistencia_estructura_fisica_sin_migracion_registrada():
    """Verifica detección de estructura física presente sin su versión en _schema_migrations."""
    conn = _crear_base_con_catalogo_baseline()

    # Creamos esquema hasta la versión 3
    ejecutar_migraciones(conn, hasta_version=3, directorio=MIGRATIONS_DIR)

    # Agregamos manualmente la columna costo_unitario sin registrar la versión 9
    conn.execute("ALTER TABLE ticket_items ADD COLUMN costo_unitario INTEGER;")
    conn.commit()

    diag = comprobar_preparacion_esquema(conn)
    assert diag["status"] == "ESQUEMA_INCONSISTENTE"
    assert diag["listo"] is False
    assert any("costo_unitario" in inc and "versión 9 no está registrada" in inc for inc in diag["inconsistencias"])
    conn.close()


def test_readiness_100_preparado_con_migraciones_001_a_009():
    """Verifica que una base con migraciones 001–009 completas sea reportada como PREPARADO."""
    conn = _crear_base_con_catalogo_baseline()
    ejecutar_migraciones(conn, directorio=MIGRATIONS_DIR)

    diag = comprobar_preparacion_esquema(conn)
    assert diag["status"] == "PREPARADO"
    assert diag["listo"] is True
    assert diag["migraciones_pendientes"] == []
    assert diag["tablas_faltantes"] == []
    assert diag["columnas_faltantes"] == []
    assert diag["inconsistencias"] == []

    # verificar_esquema_listo no debe lanzar excepción
    verificar_esquema_listo(conn)
    conn.close()


# ==============================================================================
# 2. ENDPOINT PROTEGIDO Y AUTENTICACIÓN
# ==============================================================================

def test_endpoint_readiness_protegido_requiere_autenticacion():
    """Verifica que GET /api/system/readiness rechace solicitudes no autenticadas."""
    with TestClient(app) as c:
        resp = c.get("/api/system/readiness")
        assert resp.status_code == 401


def test_endpoint_readiness_con_usuario_autenticado_retorna_diagnostico(auth_client):
    """Verifica que GET /api/system/readiness devuelva el estado completo sin secretos."""
    conn = _crear_base_con_catalogo_baseline()
    ejecutar_migraciones(conn, directorio=MIGRATIONS_DIR)

    app.dependency_overrides[get_db_connection] = lambda: conn
    resp = auth_client.get("/api/system/readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "PREPARADO"
    assert data["listo"] is True
    assert "TURSO_DEV_AUTH_TOKEN" not in resp.text
    assert "token" not in resp.text.lower() or "token_type" not in resp.text
    conn.close()


def test_autenticacion_funciona_aun_con_base_de_datos_vacia_o_incompleta():
    """Verifica que Alan y Yilda puedan autenticarse aunque la base de negocio no esté preparada."""
    with TestClient(app) as c:
        # Alan
        resp = c.post("/api/auth/login", json={"username": "alan", "password": "pos"})
        assert resp.status_code == 200
        token = resp.json()["access_token"]
        assert token

        # Con el token emitido, puede consultar /api/system/readiness para diagnosticar la BD
        resp_me = c.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp_me.status_code == 200
        assert resp_me.json()["username"] == "alan"


# ==============================================================================
# 3. ELIMINACIÓN DE DDL EN OPERACIONES NORMALES
# ==============================================================================

def test_operaciones_normales_no_ejecutan_ddl(auth_client):
    """Verifica que ninguna operación de la API ejecute CREATE TABLE, ALTER TABLE ni CREATE INDEX."""
    real_conn = _crear_base_con_catalogo_baseline()
    ejecutar_migraciones(real_conn, directorio=MIGRATIONS_DIR)

    spy = DDLInterceptionSpy(real_conn)
    app.dependency_overrides[get_db_connection] = lambda: spy

    # 1. Crear producto en catálogo
    res_prod = auth_client.post(
        "/api/catalog",
        json={
            "nombre": "Producto Sin DDL",
            "categoria": "Kiosco",
            "proveedor": "Distribuidora DDL",
            "costo": 100,
            "precio_venta": 200,
        },
    )
    assert res_prod.status_code == 201

    # 2. Registrar venta
    res_venta = auth_client.post(
        "/api/sales",
        json={
            "ticket": {
                "ticket_id": "T-NO-DDL-1",
                "fecha": "24/10/2026 12:00:00",
                "total_venta": 200,
                "monto_efectivo": 200,
                "monto_transf": 0,
                "metodo_pago": "EFECTIVO",
            },
            "items": [
                {
                    "ticket_id": "T-NO-DDL-1",
                    "producto": "Producto Sin DDL",
                    "cantidad": 1,
                    "precio_unit": 200,
                    "subtotal": 200,
                }
            ],
        },
    )
    assert res_venta.status_code == 201

    # 3. Registrar carga de servicio
    res_carga = auth_client.post(
        "/api/services",
        json={
            "servicio": "Claro",
            "monto_carga": 1000,
            "monto_adicional": 100,
            "pago_efvo": 1100,
            "pago_transf": 0,
            "fecha": "24/10/2026 12:05:00",
        },
    )
    assert res_carga.status_code == 201

    # 4. Registrar gasto
    res_gasto = auth_client.post(
        "/api/expenses",
        json={
            "tipo": "Limpieza",
            "detalle": "Lavandina",
            "monto_salida": 500,
            "origen_fondos": "CAJA",
            "metodo_pago": "EFECTIVO",
            "fecha": "24/10/2026 12:10:00",
        },
    )
    assert res_gasto.status_code == 201

    # 5. Crear pedido a proveedor
    res_pedido = auth_client.post(
        "/api/preventistas/pedidos",
        json={
            "proveedor": "Distribuidora DDL",
            "fecha": "2026-10-24",
            "items": [
                {
                    "producto_nombre": "Producto Sin DDL",
                    "cantidad": 10,
                    "costo_unitario": 100,
                }
            ],
        },
    )
    assert res_pedido.status_code == 201

    # Comprobar que en ninguna de las 5 operaciones se ejecutó DDL
    assert spy.ddl_executed == [], f"Se detectaron instrucciones DDL en operaciones normales: {spy.ddl_executed}"
    real_conn.close()


# ==============================================================================
# 4. FALLO SEGURO (HTTP 503 SCHEMA_NOT_READY) ANTE ESTRUCTURAS FALTANTES
# ==============================================================================

def test_venta_conectada_exige_costo_unitario_y_falla_503_si_falta(auth_client):
    """Verifica que una venta no se registre sin capturar el costo histórico (migración 009 faltante)."""
    conn = _crear_base_con_catalogo_baseline()
    # Solo aplicamos hasta migración 007 (sin migración 009)
    ejecutar_migraciones(conn, hasta_version=7, directorio=MIGRATIONS_DIR)

    app.dependency_overrides[get_db_connection] = lambda: conn
    payload = {
        "ticket": {
            "ticket_id": "T-FAIL-COSTO-1",
            "fecha": "24/10/2026 12:00:00",
            "total_venta": 500,
            "monto_efectivo": 500,
            "monto_transf": 0,
            "metodo_pago": "EFECTIVO",
        },
        "items": [
            {
                "ticket_id": "T-FAIL-COSTO-1",
                "producto": "Alfajor Triple",
                "cantidad": 1,
                "precio_unit": 500,
                "subtotal": 500,
            }
        ],
    }

    resp = auth_client.post("/api/sales", json=payload)
    assert resp.status_code == 503
    assert "SCHEMA_NOT_READY" in resp.text
    assert "costo_unitario" in resp.text or "migración 009" in resp.text

    # Verificar que el ticket NO fue insertado
    cur = conn.execute("SELECT COUNT(*) FROM tickets WHERE id = 'T-FAIL-COSTO-1';")
    assert cur.fetchone()[0] == 0
    conn.close()


def test_alta_producto_falla_503_si_falta_tabla_auditoria(auth_client):
    """Verifica que crear un producto falle de forma controlada si falta productos_auditoria."""
    conn = _crear_base_con_catalogo_baseline()
    # Solo migración 001 (productos baseline, sin migración 004 de auditoría)
    ejecutar_migraciones(conn, hasta_version=1, directorio=MIGRATIONS_DIR)

    app.dependency_overrides[get_db_connection] = lambda: conn
    payload = {
        "nombre": "Producto Sin Auditoria",
        "categoria": "Kiosco",
        "costo": 100,
        "precio_venta": 200,
    }

    resp = auth_client.post("/api/catalog", json=payload)
    assert resp.status_code == 503
    assert "SCHEMA_NOT_READY" in resp.text
    assert "productos_auditoria" in resp.text

    # Verificar que el producto no se creó físicamente
    cur = conn.execute("SELECT COUNT(*) FROM productos WHERE nombre = 'Producto Sin Auditoria';")
    assert cur.fetchone()[0] == 0
    conn.close()


def test_crear_pedido_falla_503_si_faltan_tablas_pedidos(auth_client):
    """Verifica que crear pedido falle con 503 si faltan tablas de pedidos (migración 008)."""
    conn = _crear_base_con_catalogo_baseline()
    ejecutar_migraciones(conn, hasta_version=7, directorio=MIGRATIONS_DIR)

    app.dependency_overrides[get_db_connection] = lambda: conn
    payload = {
        "proveedor": "Distribuidora Quilmes",
        "items": [{"producto_nombre": "Cerveza", "cantidad": 5, "costo_unitario": 1000}],
    }

    resp = auth_client.post("/api/preventistas/pedidos", json=payload)
    assert resp.status_code == 503
    assert "SCHEMA_NOT_READY" in resp.text
    assert "migración 008" in resp.text or "pedidos_proveedor" in resp.text
    conn.close()


def test_lecturas_degradan_seguras_ante_tablas_faltantes_sin_500(auth_client):
    """Verifica que consultar tablas inexistentes devuelva listas vacías y no errores 500 no controlados."""
    conn = _crear_base_con_catalogo_baseline()
    # Solo migración 001 (sin tickets, cargas, gastos ni pedidos)
    ejecutar_migraciones(conn, hasta_version=1, directorio=MIGRATIONS_DIR)

    app.dependency_overrides[get_db_connection] = lambda: conn

    # 1. Ventas
    resp_ventas = auth_client.get("/api/sales")
    assert resp_ventas.status_code == 200
    assert resp_ventas.json()["total"] == 0

    # 2. Servicios
    resp_servicios = auth_client.get("/api/services")
    assert resp_servicios.status_code == 200
    assert resp_servicios.json()["total"] == 0

    # 3. Gastos
    resp_gastos = auth_client.get("/api/expenses")
    assert resp_gastos.status_code == 200
    assert resp_gastos.json()["total"] == 0

    # 4. Pedidos
    resp_pedidos = auth_client.get("/api/preventistas/pedidos")
    assert resp_pedidos.status_code == 200
    assert resp_pedidos.json() == []
    conn.close()


# ==============================================================================
# 5. INTEGRIDAD CONTABLE Y DISTINCIÓN DE CERO VÁLIDO EN DASHBOARD
# ==============================================================================

def test_dashboard_distingue_cero_valido_de_esquema_incompleto(auth_client):
    """Verifica que el dashboard reporte esquema_completo=True cuando todo está migrado pero con datos en 0."""
    conn = _crear_base_con_catalogo_baseline()
    ejecutar_migraciones(conn, directorio=MIGRATIONS_DIR)

    app.dependency_overrides[get_db_connection] = lambda: conn
    resp = auth_client.get("/api/reports/dashboard?periodo=hoy")
    assert resp.status_code == 200
    data = resp.json()

    # Base limpia migrada: valores en 0 válidos y esquema completo
    assert data["caja_a"]["total_ventas"] == 0
    assert data["gastos"]["gastos_total"] == 0
    assert data["esquema_completo"] is True
    assert data["tablas_faltantes"] == []
    assert data["advertencia_esquema"] is None

    # Ahora probamos con base parcial (solo migración 1..3, faltan gastos y cierres)
    conn_parcial = _crear_base_con_catalogo_baseline()
    ejecutar_migraciones(conn_parcial, hasta_version=3, directorio=MIGRATIONS_DIR)

    app.dependency_overrides[get_db_connection] = lambda: conn_parcial
    resp_parcial = auth_client.get("/api/reports/dashboard?periodo=hoy")
    assert resp_parcial.status_code == 200
    data_parcial = resp_parcial.json()

    assert data_parcial["esquema_completo"] is False
    assert "gastos_retiros" in data_parcial["tablas_faltantes"]
    assert data_parcial["advertencia_esquema"] is not None

    conn.close()
    conn_parcial.close()


# ==============================================================================
# 6. PRUEBAS ESPECÍFICAS DE DIAGNÓSTICO E INTEGRIDAD PARA MIGRACIÓN 010
# ==============================================================================


def test_readiness_detecta_migracion_010_pendiente_con_columnas_e_indices_faltantes():
    """Verifica que una base con migraciones 001–009 reporte 010 pendiente, columnas e índices faltantes."""
    conn = _crear_base_con_catalogo_baseline()
    ejecutar_migraciones(conn, hasta_version=9, directorio=MIGRATIONS_DIR)

    diag = comprobar_preparacion_esquema(conn)
    assert diag["status"] == "MIGRACIONES_PENDIENTES"
    assert diag["listo"] is False
    assert 10 in diag["migraciones_pendientes"]
    assert "cargas_servicios.idempotency_key" in diag["columnas_faltantes"]
    assert "gastos_retiros.idempotency_key" in diag["columnas_faltantes"]
    assert "cargas_servicios.uq_cargas_idempotency_key" in diag["indices_faltantes"]
    assert "gastos_retiros.uq_gastos_idempotency_key" in diag["indices_faltantes"]

    with pytest.raises(ErrorEsquemaNoPreparado):
        verificar_esquema_listo(conn)

    conn.close()


def test_readiness_detecta_indice_con_definicion_incorrecta():
    """Verifica que si un índice no es UNIQUE o no es parcial, readiness lo identifique como incorrecto."""
    conn = _crear_base_con_catalogo_baseline()
    ejecutar_migraciones(conn, hasta_version=9, directorio=MIGRATIONS_DIR)

    # Añadimos las columnas requeridas
    conn.execute("ALTER TABLE cargas_servicios ADD COLUMN idempotency_key TEXT;")
    conn.execute("ALTER TABLE gastos_retiros ADD COLUMN idempotency_key TEXT;")

    # Creamos un índice NO ÚNICO y NO PARCIAL para cargas_servicios
    conn.execute("CREATE INDEX uq_cargas_idempotency_key ON cargas_servicios(idempotency_key);")
    # Creamos un índice único pero NO PARCIAL (sin WHERE) para gastos_retiros
    conn.execute("CREATE UNIQUE INDEX uq_gastos_idempotency_key ON gastos_retiros(idempotency_key);")

    # Simulamos versión 10 asentada
    conn.execute("INSERT INTO _schema_migrations (version, nombre) VALUES (10, '010_idempotencia_servicios_gastos');")
    conn.commit()

    diag = comprobar_preparacion_esquema(conn)
    assert diag["listo"] is False

    # Debe figurar en indices_incorrectos o inconsistencias
    assert any("uq_cargas_idempotency_key" in item for item in diag["indices_incorrectos"] + diag["inconsistencias"])
    assert any("no es UNIQUE" in item for item in diag["indices_incorrectos"] + diag["inconsistencias"])
    assert any("uq_gastos_idempotency_key" in item for item in diag["indices_incorrectos"] + diag["inconsistencias"])
    assert any("no es parcial" in item for item in diag["indices_incorrectos"] + diag["inconsistencias"])

    conn.close()


def test_readiness_esquema_completamente_preparado_001_a_010():
    """Verifica que una base con todas las migraciones 001–010 devuelva PREPARADO, listo=True y 0 pendientes."""
    conn = _crear_base_con_catalogo_baseline()
    ejecutar_migraciones(conn, directorio=MIGRATIONS_DIR)

    diag = comprobar_preparacion_esquema(conn)
    assert diag["status"] == "PREPARADO"
    assert diag["listo"] is True
    assert diag["migraciones_pendientes"] == []
    assert diag["tablas_faltantes"] == []
    assert diag["columnas_faltantes"] == []
    assert diag["indices_faltantes"] == []
    assert diag["indices_incorrectos"] == []
    assert diag["inconsistencias"] == []
    assert "001–010" in diag["mensaje"]

    # verificar_esquema_listo debe completarse sin excepciones
    verificar_esquema_listo(conn)
    conn.close()


def test_indice_parcial_010_admite_multiples_null_y_restringe_unicidad_clave():
    """Verifica el comportamiento del índice único parcial: múltiples NULL permitidos, claves duplicadas rechazadas."""
    conn = _crear_base_con_catalogo_baseline()
    ejecutar_migraciones(conn, directorio=MIGRATIONS_DIR)

    # 1. Insertar múltiples filas históricas con idempotency_key IS NULL
    conn.execute(
        """
        INSERT INTO cargas_servicios (
            legacy_row_id, fecha_hora_utc, servicio, monto_carga,
            monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado, idempotency_key
        ) VALUES (NULL, '2026-10-25 10:00:00', 'Claro', 1000, 100, 1100, 1100, 0, 0, NULL);
        """
    )
    conn.execute(
        """
        INSERT INTO cargas_servicios (
            legacy_row_id, fecha_hora_utc, servicio, monto_carga,
            monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado, idempotency_key
        ) VALUES (NULL, '2026-10-25 10:05:00', 'Personal', 2000, 200, 2200, 2200, 0, 0, NULL);
        """
    )
    conn.commit()

    filas_null = conn.execute("SELECT COUNT(*) FROM cargas_servicios WHERE idempotency_key IS NULL;").fetchone()[0]
    assert filas_null == 2

    # 2. Insertar primera fila con clave concreta
    conn.execute(
        """
        INSERT INTO cargas_servicios (
            legacy_row_id, fecha_hora_utc, servicio, monto_carga,
            monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado, idempotency_key
        ) VALUES (NULL, '2026-10-25 10:10:00', 'Tuenti', 3000, 300, 3300, 3300, 0, 0, 'clave-unica-123');
        """
    )
    conn.commit()

    # 3. Intentar insertar segunda fila con la misma clave no nula debe fallar por IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO cargas_servicios (
                legacy_row_id, fecha_hora_utc, servicio, monto_carga,
                monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado, idempotency_key
            ) VALUES (NULL, '2026-10-25 10:15:00', 'Tuenti', 3000, 300, 3300, 3300, 0, 0, 'clave-unica-123');
            """
        )

    conn.close()

    app.dependency_overrides[get_db_connection] = lambda: conn_parcial
    resp_parcial = auth_client.get("/api/reports/dashboard?periodo=hoy")
    assert resp_parcial.status_code == 200
    data_parcial = resp_parcial.json()

    assert data_parcial["esquema_completo"] is False
    assert "gastos_retiros" in data_parcial["tablas_faltantes"]
    assert data_parcial["advertencia_esquema"] is not None

    conn.close()
    conn_parcial.close()


# ==============================================================================
# 6. PRUEBAS ESPECÍFICAS DE DIAGNÓSTICO E INTEGRIDAD PARA MIGRACIÓN 010
# ==============================================================================


def test_readiness_detecta_migracion_010_pendiente_con_columnas_e_indices_faltantes():
    """Verifica que una base con migraciones 001–009 reporte 010 pendiente, columnas e índices faltantes."""
    conn = _crear_base_con_catalogo_baseline()
    ejecutar_migraciones(conn, hasta_version=9, directorio=MIGRATIONS_DIR)

    diag = comprobar_preparacion_esquema(conn)
    assert diag["status"] == "MIGRACIONES_PENDIENTES"
    assert diag["listo"] is False
    assert 10 in diag["migraciones_pendientes"]
    assert "cargas_servicios.idempotency_key" in diag["columnas_faltantes"]
    assert "gastos_retiros.idempotency_key" in diag["columnas_faltantes"]
    assert "cargas_servicios.uq_cargas_idempotency_key" in diag["indices_faltantes"]
    assert "gastos_retiros.uq_gastos_idempotency_key" in diag["indices_faltantes"]

    with pytest.raises(ErrorEsquemaNoPreparado):
        verificar_esquema_listo(conn)

    conn.close()


def test_readiness_detecta_indice_con_definicion_incorrecta():
    """Verifica que si un índice no es UNIQUE o no es parcial, readiness lo identifique como incorrecto."""
    conn = _crear_base_con_catalogo_baseline()
    ejecutar_migraciones(conn, hasta_version=9, directorio=MIGRATIONS_DIR)

    # Añadimos las columnas requeridas
    conn.execute("ALTER TABLE cargas_servicios ADD COLUMN idempotency_key TEXT;")
    conn.execute("ALTER TABLE gastos_retiros ADD COLUMN idempotency_key TEXT;")

    # Creamos un índice NO ÚNICO y NO PARCIAL para cargas_servicios
    conn.execute("CREATE INDEX uq_cargas_idempotency_key ON cargas_servicios(idempotency_key);")
    # Creamos un índice único pero NO PARCIAL (sin WHERE) para gastos_retiros
    conn.execute("CREATE UNIQUE INDEX uq_gastos_idempotency_key ON gastos_retiros(idempotency_key);")

    # Simulamos versión 10 asentada
    conn.execute("INSERT INTO _schema_migrations (version, nombre) VALUES (10, '010_idempotencia_servicios_gastos');")
    conn.commit()

    diag = comprobar_preparacion_esquema(conn)
    assert diag["listo"] is False

    # Debe figurar en indices_incorrectos o inconsistencias
    assert any("uq_cargas_idempotency_key" in item for item in diag["indices_incorrectos"] + diag["inconsistencias"])
    assert any("no es UNIQUE" in item for item in diag["indices_incorrectos"] + diag["inconsistencias"])
    assert any("uq_gastos_idempotency_key" in item for item in diag["indices_incorrectos"] + diag["inconsistencias"])
    assert any("no es parcial" in item for item in diag["indices_incorrectos"] + diag["inconsistencias"])

    conn.close()


def test_readiness_esquema_completamente_preparado_001_a_010():
    """Verifica que una base con todas las migraciones 001–010 devuelva PREPARADO, listo=True y 0 pendientes."""
    conn = _crear_base_con_catalogo_baseline()
    ejecutar_migraciones(conn, directorio=MIGRATIONS_DIR)

    diag = comprobar_preparacion_esquema(conn)
    assert diag["status"] == "PREPARADO"
    assert diag["listo"] is True
    assert diag["migraciones_pendientes"] == []
    assert diag["tablas_faltantes"] == []
    assert diag["columnas_faltantes"] == []
    assert diag["indices_faltantes"] == []
    assert diag["indices_incorrectos"] == []
    assert diag["inconsistencias"] == []
    assert "001–010" in diag["mensaje"]

    # verificar_esquema_listo debe completarse sin excepciones
    verificar_esquema_listo(conn)
    conn.close()


def test_indice_parcial_010_admite_multiples_null_y_restringe_unicidad_clave():
    """Verifica el comportamiento del índice único parcial: múltiples NULL permitidos, claves duplicadas rechazadas."""
    conn = _crear_base_con_catalogo_baseline()
    ejecutar_migraciones(conn, directorio=MIGRATIONS_DIR)

    # 1. Insertar múltiples filas históricas con idempotency_key IS NULL
    conn.execute(
        """
        INSERT INTO cargas_servicios (
            legacy_row_id, fecha_hora_utc, servicio, monto_carga,
            monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado, idempotency_key
        ) VALUES (NULL, '2026-10-25 10:00:00', 'Claro', 1000, 100, 1100, 1100, 0, 0, NULL);
        """
    )
    conn.execute(
        """
        INSERT INTO cargas_servicios (
            legacy_row_id, fecha_hora_utc, servicio, monto_carga,
            monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado, idempotency_key
        ) VALUES (NULL, '2026-10-25 10:05:00', 'Personal', 2000, 200, 2200, 2200, 0, 0, NULL);
        """
    )
    conn.commit()

    filas_null = conn.execute("SELECT COUNT(*) FROM cargas_servicios WHERE idempotency_key IS NULL;").fetchone()[0]
    assert filas_null == 2

    # 2. Insertar primera fila con clave concreta
    conn.execute(
        """
        INSERT INTO cargas_servicios (
            legacy_row_id, fecha_hora_utc, servicio, monto_carga,
            monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado, idempotency_key
        ) VALUES (NULL, '2026-10-25 10:10:00', 'Tuenti', 3000, 300, 3300, 3300, 0, 0, 'clave-unica-123');
        """
    )
    conn.commit()

    # 3. Intentar insertar segunda fila con la misma clave no nula debe fallar por IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO cargas_servicios (
                legacy_row_id, fecha_hora_utc, servicio, monto_carga,
                monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado, idempotency_key
            ) VALUES (NULL, '2026-10-25 10:15:00', 'Tuenti', 3000, 300, 3300, 3300, 0, 0, 'clave-unica-123');
            """
        )

    conn.close()


def test_readiness_verifica_esquema_de_auth_scram_challenges_migracion_011():
    """Verifica que readiness exija la versión 11 y la estructura completa de auth_scram_challenges."""
    conn = _crear_base_con_catalogo_baseline()
    ejecutar_migraciones(conn, hasta_version=10, directorio=MIGRATIONS_DIR)

    # 1. Falta versión 11 y falta tabla auth_scram_challenges
    diag_pend = comprobar_preparacion_esquema(conn)
    assert diag_pend["listo"] is False
    assert diag_pend["status"] == "MIGRACIONES_PENDIENTES"
    assert 11 in diag_pend["migraciones_pendientes"]
    assert "auth_scram_challenges" in diag_pend["tablas_faltantes"]

    # 2. Tabla creada con esquema incompleto (falta columna 'used' e índices) pero versión 11 registrada
    conn.execute("CREATE TABLE auth_scram_challenges (jti TEXT PRIMARY KEY, exp INTEGER NOT NULL);")
    conn.execute("INSERT INTO _schema_migrations (version, nombre) VALUES (11, '011_auth_scram_challenges');")
    conn.commit()

    diag_inc = comprobar_preparacion_esquema(conn)
    assert diag_inc["listo"] is False
    assert diag_inc["status"] == "ESQUEMA_INCONSISTENTE"
    assert "auth_scram_challenges.used" in diag_inc["columnas_faltantes"]
    assert "auth_scram_challenges.creado_en_utc" in diag_inc["columnas_faltantes"]
    assert "auth_scram_challenges.idx_scram_challenges_exp" in diag_inc["indices_faltantes"]
    assert "auth_scram_challenges.idx_scram_challenges_used" in diag_inc["indices_faltantes"]

    conn.close()
