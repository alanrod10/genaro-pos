"""Pruebas obligatorias de optimización, integridad y ciclo de vida de conexiones para System Readiness.

Cubre exhaustivamente las 10 verificaciones obligatorias:
1. Preparación correcta cuando todas las condiciones se cumplen (PREPARADO).
2. Detección precisa de migraciones pendientes (MIGRACIONES_PENDIENTES).
3. Detección de migraciones o estructuras inconsistentes (ESQUEMA_INCONSISTENTE).
4. Tablas o columnas obligatorias ausentes.
5. Fallos de conexión o de introspección (ERROR_CONEXION con fallback seguro).
6. Cierre de conexiones garantizado incluso ante errores o excepciones.
7. Ausencia de conexiones compartidas o globales entre solicitudes independientes.
8. Que una condición de preparación fallida NUNCA se reporte como PREPARADO.
9. Que el contrato de respuesta público (SystemReadinessOut) se mantenga íntegro.
10. Que el número de consultas e introspecciones se haya consolidado drásticamente (máximo 4 consultas en base preparada).
"""

from pathlib import Path
import sqlite3
import pytest
from unittest.mock import MagicMock, patch

from api.dependencies import AuthenticatedUser, get_current_user, get_db_connection
from api.auth import generate_password_hash
from api.main import app
from api.schemas import SystemReadinessOut
from db.migrations_runner import ejecutar_migraciones
from db.readiness import (
    ErrorEsquemaNoPreparado,
    comprobar_preparacion_esquema,
    verificar_esquema_listo,
    TABLAS_REQUERIDAS,
    MIGRACIONES_REQUERIDAS,
)
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "db/migrations"
TEST_SECRET_KEY = "clave_secreta_de_prueba_con_mas_de_32_caracteres_12345"


class QueryCounterSpy:
    """Spy para contar y registrar consultas ejecutadas sobre una conexión SQLite."""

    def __init__(self, conn):
        self._conn = conn
        self.statements = []
        self.closed = False

    def execute(self, sql, *args, **kwargs):
        self.statements.append(sql.strip())
        return self._conn.execute(sql, *args, **kwargs)

    def executemany(self, sql, *args, **kwargs):
        self.statements.append(sql.strip())
        return self._conn.executemany(sql, *args, **kwargs)

    def fetchall(self):
        return self._conn.fetchall()

    def fetchone(self):
        return self._conn.fetchone()

    def close(self):
        self.closed = True
        return self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _crear_base_baseline() -> sqlite3.Connection:
    """Crea una base SQLite en memoria con el catálogo preexistente."""
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
    """Configura variables de entorno para autenticación en tests."""
    alan_hash = generate_password_hash("pos", iterations=1_000)
    monkeypatch.setenv("GENARO_AUTH_SECRET_KEY", TEST_SECRET_KEY)
    monkeypatch.setenv("GENARO_USER_ALAN_HASH", alan_hash)
    monkeypatch.setenv("GENARO_AUTH_TOKEN_EXPIRE_MINUTES", "60")


# ==============================================================================
# 1. Preparación correcta cuando todas las condiciones se cumplen
# ==============================================================================
def test_1_preparacion_correcta_todas_las_condiciones():
    conn = _crear_base_baseline()
    ejecutar_migraciones(conn, directorio=MIGRATIONS_DIR)

    res = comprobar_preparacion_esquema(conn)
    assert res["status"] == "PREPARADO"
    assert res["listo"] is True
    assert res["migraciones_pendientes"] == []
    assert res["tablas_faltantes"] == []
    assert res["columnas_faltantes"] == []
    assert res["indices_faltantes"] == []
    assert res["indices_incorrectos"] == []
    assert res["inconsistencias"] == []
    assert "001–010" in res["mensaje"]

    # verificar_esquema_listo no debe lanzar excepción
    verificar_esquema_listo(conn)
    conn.close()


# ==============================================================================
# 2. Migraciones pendientes
# ==============================================================================
def test_2_migraciones_pendientes_detectadas():
    conn = _crear_base_baseline()
    ejecutar_migraciones(conn, hasta_version=8, directorio=MIGRATIONS_DIR)

    res = comprobar_preparacion_esquema(conn)
    assert res["status"] == "MIGRACIONES_PENDIENTES"
    assert res["listo"] is False
    assert 9 in res["migraciones_pendientes"]
    assert 10 in res["migraciones_pendientes"]
    assert "ticket_items.costo_unitario" in res["columnas_faltantes"]
    assert "cargas_servicios.idempotency_key" in res["columnas_faltantes"]
    assert "gastos_retiros.idempotency_key" in res["columnas_faltantes"]

    with pytest.raises(ErrorEsquemaNoPreparado) as exc:
        verificar_esquema_listo(conn)
    assert exc.value.estado == "MIGRACIONES_PENDIENTES"
    conn.close()


# ==============================================================================
# 3. Migraciones o estructuras inconsistentes
# ==============================================================================
def test_3_inconsistencias_detectadas():
    conn = _crear_base_baseline()
    ejecutar_migraciones(conn, hasta_version=5, directorio=MIGRATIONS_DIR)

    # Simular versión 7 asentada en _schema_migrations sin haber corrido migración 6 ni 7
    conn.execute("INSERT INTO _schema_migrations (version, nombre) VALUES (7, '007_sales_audit');")
    conn.commit()

    res = comprobar_preparacion_esquema(conn)
    assert res["status"] == "ESQUEMA_INCONSISTENTE"
    assert res["listo"] is False
    assert len(res["inconsistencias"]) > 0
    assert any("ventas_auditoria" in inc for inc in res["inconsistencias"])
    conn.close()


# ==============================================================================
# 4. Tablas o columnas obligatorias ausentes
# ==============================================================================
def test_4_tablas_o_columnas_obligatorias_ausentes():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    # Base vacía sin tablas

    res = comprobar_preparacion_esquema(conn)
    assert res["status"] == "MIGRACIONES_PENDIENTES"
    assert res["listo"] is False
    for t in TABLAS_REQUERIDAS:
        assert t in res["tablas_faltantes"]
    conn.close()


# ==============================================================================
# 5. Fallos de conexión o de introspección
# ==============================================================================
def test_5_fallo_de_conexion_retorna_error_conexion():
    with patch("db.catalog._obtener_conexion_turso", side_effect=Exception("Error de red simulado")):
        res = comprobar_preparacion_esquema(conn=None)
        assert res["status"] == "ERROR_CONEXION"
        assert res["listo"] is False
        assert "No se pudo conectar a la base de datos" in res["mensaje"]


def test_5b_fallback_si_falla_introspeccion_agrupada():
    """Verifica que si la consulta agrupada falla (ej. motor antiguo), el fallback individual funcione."""
    conn = _crear_base_baseline()
    ejecutar_migraciones(conn, directorio=MIGRATIONS_DIR)

    spy = QueryCounterSpy(conn)
    original_execute = conn.execute

    def flaky_execute(sql, *args, **kwargs):
        # Provocar error sólo en la consulta agrupada de columnas
        if "pragma_table_info" in sql:
            raise sqlite3.OperationalError("Simulated grouped query failure")
        return original_execute(sql, *args, **kwargs)

    with patch.object(spy, "execute", side_effect=flaky_execute):
        res = comprobar_preparacion_esquema(conn=spy)
        assert res["status"] == "PREPARADO"
        assert res["listo"] is True
        assert res["columnas_faltantes"] == []

    conn.close()


# ==============================================================================
# 6. Cierre de conexiones incluso ante errores
# ==============================================================================
def test_6_cierre_de_conexion_propia_garantizado():
    fake_conn = MagicMock()
    fake_conn.execute.side_effect = sqlite3.OperationalError("Fallo inesperado durante introspección")

    with patch("db.catalog._obtener_conexion_turso", return_value=fake_conn):
        try:
            comprobar_preparacion_esquema(conn=None)
        except Exception:
            pass
        fake_conn.close.assert_called_once()


def test_6b_conexion_externa_no_es_cerrada_por_readiness():
    """La conexión provista por el caller (ej. pool o test) no debe cerrarse dentro de readiness."""
    conn = _crear_base_baseline()
    ejecutar_migraciones(conn, directorio=MIGRATIONS_DIR)

    spy = QueryCounterSpy(conn)
    comprobar_preparacion_esquema(conn=spy)
    assert spy.closed is False
    conn.close()


# ==============================================================================
# 7. Ausencia de conexiones compartidas entre solicitudes independientes
# ==============================================================================
def test_7_sin_conexiones_compartidas_entre_solicitudes():
    conns_created = []

    def mock_obtener_conn():
        c = _crear_base_baseline()
        ejecutar_migraciones(c, directorio=MIGRATIONS_DIR)
        conns_created.append(c)
        return c

    with patch("db.catalog._obtener_conexion_turso", side_effect=mock_obtener_conn):
        res1 = comprobar_preparacion_esquema(conn=None)
        res2 = comprobar_preparacion_esquema(conn=None)

        assert res1["listo"] is True
        assert res2["listo"] is True
        assert len(conns_created) == 2
        # Conexiones deben ser objetos distintos e independientes
        assert conns_created[0] is not conns_created[1]


# ==============================================================================
# 8. Que una condición de preparación fallida nunca se reporte como PREPARADO
# ==============================================================================
@pytest.mark.parametrize("scenario", ["tablas_faltantes", "migraciones_pendientes", "inconsistencia", "indices_mal"])
def test_8_condicion_fallida_nunca_reporta_preparado(scenario):
    conn = _crear_base_baseline()
    if scenario == "tablas_faltantes":
        pass  # Solo tiene productos
    elif scenario == "migraciones_pendientes":
        ejecutar_migraciones(conn, hasta_version=4, directorio=MIGRATIONS_DIR)
    elif scenario == "inconsistencia":
        ejecutar_migraciones(conn, hasta_version=3, directorio=MIGRATIONS_DIR)
        conn.execute("ALTER TABLE ticket_items ADD COLUMN costo_unitario INTEGER;")
        conn.commit()
    elif scenario == "indices_mal":
        ejecutar_migraciones(conn, hasta_version=9, directorio=MIGRATIONS_DIR)
        conn.execute("ALTER TABLE cargas_servicios ADD COLUMN idempotency_key TEXT;")
        conn.execute("CREATE INDEX uq_cargas_idempotency_key ON cargas_servicios(idempotency_key);")
        conn.execute("INSERT INTO _schema_migrations (version, nombre) VALUES (10, 'mig_10');")
        conn.commit()

    res = comprobar_preparacion_esquema(conn)
    assert res["status"] != "PREPARADO"
    assert res["listo"] is False
    conn.close()


# ==============================================================================
# 9. Que el contrato de respuesta pública se mantenga
# ==============================================================================
def test_9_contrato_de_respuesta_publica_se_mantiene():
    conn = _crear_base_baseline()
    ejecutar_migraciones(conn, directorio=MIGRATIONS_DIR)

    res = comprobar_preparacion_esquema(conn)

    # Validar contra esquema Pydantic de la API
    out = SystemReadinessOut(**res)
    assert out.status == "PREPARADO"
    assert out.listo is True
    assert isinstance(out.versiones_aplicadas, list)
    assert isinstance(out.migraciones_pendientes, list)
    assert isinstance(out.tablas_existentes, list)
    assert isinstance(out.tablas_faltantes, list)
    assert isinstance(out.columnas_faltantes, list)
    assert isinstance(out.indices_faltantes, list)
    assert isinstance(out.indices_incorrectos, list)
    assert isinstance(out.inconsistencias, list)
    assert isinstance(out.mensaje, str)

    # Test HTTP vía FastAPI TestClient
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        username="alan", display_name="Alan"
    )
    app.dependency_overrides[get_db_connection] = lambda: conn

    with TestClient(app) as client:
        resp = client.get("/api/system/readiness")
        assert resp.status_code == 200
        json_data = resp.json()
        assert json_data["status"] == "PREPARADO"
        assert json_data["listo"] is True
        assert set(range(1, 13)).issubset(set(json_data["versiones_aplicadas"]))

    app.dependency_overrides.clear()
    conn.close()


# ==============================================================================
# 10. Que el número de conexiones y consultas haya disminuido drásticamente
# ==============================================================================
def test_10_consolidacion_de_consultas_maximo_4_en_base_preparada():
    conn = _crear_base_baseline()
    ejecutar_migraciones(conn, directorio=MIGRATIONS_DIR)

    spy = QueryCounterSpy(conn)
    res = comprobar_preparacion_esquema(spy)

    assert res["status"] == "PREPARADO"
    assert res["listo"] is True

    # Antes se ejecutaban 47 consultas secuenciales.
    # Con la introspección consolidada, debe ejecutarse a lo sumo 4 consultas:
    # 1. sqlite_master
    # 2. _schema_migrations
    # 3. pragma_table_info agrupada
    # 4. pragma_index_list / pragma_index_info agrupada
    assert len(spy.statements) <= 4, f"Se esperaban <= 4 consultas, pero se ejecutaron {len(spy.statements)}:\n" + "\n".join(spy.statements)
    conn.close()
