"""Pruebas de Aislamiento y Seguridad — ISO-001 a ISO-008.

Verifica de forma estricta el cumplimiento de las REGLAS INNEGOCIABLES:
- ISO-001: SQLite local exclusivo.
- ISO-002: Rechazo inmediato (fail-closed) de conexiones remotas (libsql, https, turso).
- ISO-003: Bloqueo de acceso a bases comerciales preexistentes.
- ISO-004: Prohibición de tablas comerciales en el laboratorio.
- ISO-005: Cumplimiento de PRAGMAs obligatorios (foreign_keys=ON, journal_mode=WAL).
- ISO-006: Desacoplamiento de migraciones comerciales.
- ISO-007: Estanqueidad de endpoints API de tesorería.
- ISO-008: Inmutabilidad de bases comerciales preexistentes (verificación criptográfica SHA256).
"""

import hashlib
from pathlib import Path
import sqlite3
import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.dependencies import get_current_user
from api.auth import AuthenticatedUser
from tesoreria.isolation import (
    ErrorViolacionAislamiento,
    conectar_db_laboratorio,
    obtener_ruta_db_laboratorio,
    verificar_cadena_conexion_aislada,
    verificar_tablas_prohibidas,
    TABLAS_COMERCIALES_PROHIBIDAS,
)
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.catalog import sembrar_catalogo_canónico


def test_iso_001_sqlite_local_exclusivo(tmp_path):
    """ISO-001: La base del laboratorio es exclusivamente un archivo SQLite local independiente."""
    db_file = tmp_path / "tesoreria_custom.db"
    conn = conectar_db_laboratorio(str(db_file))
    try:
        assert isinstance(conn, sqlite3.Connection)
        # Verificar que es una base SQLite local
        cursor = conn.cursor()
        cursor.execute("SELECT 1;")
        assert cursor.fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.parametrize(
    "url_remota",
    [
        "libsql://genaro-pos-prod.turso.io",
        "https://genaro-pos-live.turso.io",
        "turso://database-cloud.turso.io",
        "wss://realtime-sync.turso.io",
        "postgres://usuario:pass@host/db",
    ],
)
def test_iso_002_fail_closed_conexiones_remotas(url_remota):
    """ISO-002: Rechazo inmediato de cualquier intento de configurar conexiones remotas."""
    with pytest.raises(ErrorViolacionAislamiento, match="VIOLACIÓN DE AISLAMIENTO: Esquema remoto"):
        verificar_cadena_conexion_aislada(url_remota)

    with pytest.raises(ErrorViolacionAislamiento):
        conectar_db_laboratorio(url_remota)


@pytest.mark.parametrize(
    "base_comercial",
    [
        "genaro-pos-dev-pre-migraciones-20261025.db",
        "genaro-pos-dev-pre-migraciones-20261025-verificable.db",
        "respaldo_verificable.db",
        "produccion.db",
    ],
)
def test_iso_003_bloqueo_acceso_bases_comerciales(base_comercial, tmp_path):
    """ISO-003: Bloqueo explícito ante intentos de abrir bases comerciales protegidas."""
    ruta_falsa = tmp_path / base_comercial
    with pytest.raises(ErrorViolacionAislamiento, match="VIOLACIÓN DE AISLAMIENTO: No se permite operar"):
        verificar_cadena_conexion_aislada(str(ruta_falsa))


def test_iso_004_prohibicion_tablas_comerciales(tmp_path):
    """ISO-004: Prohibición de existencia de tablas comerciales en el entorno de tesorería."""
    db_file = tmp_path / "test_contaminada.db"
    conn_raw = sqlite3.connect(str(db_file))
    conn_raw.execute("CREATE TABLE ventas (id INTEGER PRIMARY KEY, total REAL);")
    conn_raw.commit()
    conn_raw.close()

    # Intentar conectar con el laboratorio debe fallar inmediatamente
    with pytest.raises(ErrorViolacionAislamiento, match="VIOLACIÓN DE AISLAMIENTO: La base contiene tablas comerciales prohibidas"):
        conectar_db_laboratorio(str(db_file))


def test_iso_005_verificacion_pragmas_obligatorios(tmp_path):
    """ISO-005: La conexión aislada activa obligatoriamente PRAGMA foreign_keys = ON y journal_mode = WAL."""
    db_file = tmp_path / "tesoreria_pragmas.db"
    conn = conectar_db_laboratorio(str(db_file))
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys;")
        fk_status = cursor.fetchone()[0]
        assert fk_status == 1, "foreign_keys debe estar activado (=1)"

        cursor.execute("PRAGMA journal_mode;")
        journal_mode = cursor.fetchone()[0]
        assert journal_mode.upper() == "WAL", "journal_mode debe ser WAL"
    finally:
        conn.close()


def test_iso_006_aislamiento_migraciones_esquema(tmp_path):
    """ISO-006: La inicialización de tesorería solo crea tablas de tesorería y no toca tablas comerciales."""
    db_file = tmp_path / "tesoreria_clean.db"
    conn = conectar_db_laboratorio(str(db_file))
    try:
        inicializar_esquema_laboratorio(conn)
        sembrar_catalogo_canónico(conn)

        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tablas_creadas = {r[0] for r in cursor.fetchall()}

        # Debe contener tablas de tesorería
        assert "tesoreria_fondos" in tablas_creadas
        assert "tesoreria_fondos_auditoria" in tablas_creadas
        assert "tesoreria_jornadas" in tablas_creadas
        assert "tesoreria_jornada_detalles" in tablas_creadas

        # Ninguna tabla comercial permitida
        for t in TABLAS_COMERCIALES_PROHIBIDAS:
            assert t not in tablas_creadas
    finally:
        conn.close()


def test_iso_007_estanqueidad_api_tesoreria(monkeypatch, tmp_path):
    """ISO-007: Los endpoints bajo /api/tesoreria son estancos y no invocan lógica comercial."""
    test_db = tmp_path / "api_test_tesoreria.db"
    monkeypatch.setenv("GENARO_TESORERIA_DB_PATH", str(test_db))

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        username="alan", display_name="Alan"
    )

    client = TestClient(app)
    try:
        resp = client.get("/api/tesoreria/estado")
        assert resp.status_code == 200
        data = resp.json()
        assert data["modulo"] == "GENARO TESORERÍA LABORATORIO"
        assert data["turso_conectado"] is False
        assert data["conexion_remota"] is False
        assert data["aislamiento"] == "ESTRICTO_LOCAL_SQLITE"
        assert data["db_path"] == str(test_db.resolve())
    finally:
        app.dependency_overrides.clear()


def test_iso_008_inmutabilidad_bases_comerciales_existentes():
    """ISO-008: Verificación de que las bases comerciales existentes en backups no sufrieron alteración."""
    backups_path = Path("/home/alanrod10/backups")
    if not backups_path.exists():
        pytest.skip("Directorio de backups no disponible en el entorno")

    db_verificable = backups_path / "genaro-pos-dev-pre-migraciones-20261025-verificable.db"
    if db_verificable.exists():
        hash_sha256 = hashlib.sha256(db_verificable.read_bytes()).hexdigest()
        # Hash canónico de referencia registrado al inicio del sprint
        assert hash_sha256 == "b0597fbeb0d27f5fc4b3f588efdc58fda23fc3427be102080134f2790300b557", (
            "ALERTA CRÍTICA: Se detectó alteración en la base comercial verificable!"
        )
