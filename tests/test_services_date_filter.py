"""Filtro SQL por día local (America/Argentina/Jujuy, UTC-3) en Servicios."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from api.dependencies import AuthenticatedUser, get_current_user, get_db_connection
from api.main import app
from db.services import (
    _rango_utc_dia_local,
    calcular_resumen_cargas_turso,
    cargar_cargas_turso,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.execute(
        """
        CREATE TABLE cargas_servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legacy_row_id TEXT,
            fecha_hora_utc TEXT NOT NULL,
            servicio TEXT NOT NULL,
            monto_carga INTEGER NOT NULL,
            monto_adicional INTEGER NOT NULL DEFAULT 0,
            total_cobrado INTEGER NOT NULL,
            pago_efectivo INTEGER NOT NULL DEFAULT 0,
            pago_transferencia INTEGER NOT NULL DEFAULT 0,
            anulado INTEGER NOT NULL DEFAULT 0,
            idempotency_key TEXT
        )
        """
    )
    c.execute("CREATE INDEX idx_cargas_servicios_fecha_hora_utc ON cargas_servicios (fecha_hora_utc)")
    # (fecha UTC, servicio, capital, adicional, efvo, transf, anulado)
    filas = [
        ("2026-10-24 02:59:59", "Claro", 1000, 100, 1100, 0, 0),      # 23/10 23:59:59 local
        ("2026-10-24 03:00:00", "Claro", 2000, 200, 2200, 0, 0),      # 24/10 00:00:00 local (inicio inclusivo)
        ("2026-10-24 15:00:00", "SUBE (MP)", 3000, 100, 0, 3100, 0),  # 24/10 12:00
        ("2026-10-24 18:00:00", "Personal", 500, 100, 600, 0, 1),     # 24/10 15:00 anulada
        ("2026-10-25 02:59:59", "Personal", 4000, 200, 4200, 0, 0),   # 24/10 23:59:59 (último)
        ("2026-10-25 03:00:00", "Movistar", 5000, 300, 5300, 0, 0),   # 25/10 00:00 (fin exclusivo)
    ]
    for f, srv, cap, adic, efvo, tr, anu in filas:
        c.execute(
            "INSERT INTO cargas_servicios (fecha_hora_utc, servicio, monto_carga, monto_adicional,"
            " total_cobrado, pago_efectivo, pago_transferencia, anulado) VALUES (?,?,?,?,?,?,?,?)",
            (f, srv, cap, adic, cap + adic, efvo, tr, anu),
        )
    c.commit()
    yield c
    c.close()


def test_rango_utc_dia_local_jujuy():
    assert _rango_utc_dia_local("2026-10-24") == ("2026-10-24 03:00:00", "2026-10-25 03:00:00")
    assert _rango_utc_dia_local("24/10/2026") == ("2026-10-24 03:00:00", "2026-10-25 03:00:00")
    assert _rango_utc_dia_local("2026-12-31") == ("2026-12-31 03:00:00", "2027-01-01 03:00:00")
    assert _rango_utc_dia_local("basura") is None


def test_inicio_inclusivo_fin_exclusivo_sin_vecinos(conn):
    df = cargar_cargas_turso(conn=conn, fecha="2026-10-24")
    # Activas del día 24: Claro 2000, SUBE (MP) 3000, Personal 4000 (la anulada queda fuera)
    assert sorted(df["MONTO_CARGA"].tolist()) == [2000, 3000, 4000]
    assert not any(f.startswith("23/10") or f.startswith("25/10") for f in df["FECHA"])


def test_anuladas_segun_contrato(conn):
    sin = cargar_cargas_turso(conn=conn, fecha="24/10/2026")
    con = cargar_cargas_turso(conn=conn, fecha="24/10/2026", incluir_anuladas=True)
    assert len(sin) == 3 and len(con) == 4
    assert int(con["ANULADO"].sum()) == 1


def test_dia_vecino_y_historial_sin_fecha(conn):
    assert len(cargar_cargas_turso(conn=conn, fecha="2026-10-23")) == 1
    assert len(cargar_cargas_turso(conn=conn, fecha="2026-10-25")) == 1
    assert len(cargar_cargas_turso(conn=conn, fecha="2020-01-01")) == 0
    assert len(cargar_cargas_turso(conn=conn, fecha="basura")) == 0
    assert len(cargar_cargas_turso(conn=conn)) == 5  # historial completo (sin anuladas)
    assert len(cargar_cargas_turso(conn=conn, incluir_anuladas=True)) == 6


def test_resumen_correcto_por_dia_y_total(conn):
    r = calcular_resumen_cargas_turso(fecha_filtro="2026-10-24", conn=conn)
    assert r["total_operaciones"] == 3
    assert r["total_capital"] == 9000
    assert r["total_adicional"] == 500
    assert r["total_cobrado"] == 9500
    assert r["total_efectivo"] == 6400
    assert r["total_transferencia"] == 3100
    assert r["capital_claro"] == 2000
    assert r["capital_sube_mp"] == 3000
    assert r["capital_prepay"] == 4000
    assert r["operaciones_anuladas"] == 1

    todo = calcular_resumen_cargas_turso(conn=conn)  # compatibilidad: sin fecha = histórico
    assert todo["total_operaciones"] == 5
    assert todo["total_capital"] == 1000 + 2000 + 3000 + 4000 + 5000
    assert todo["operaciones_anuladas"] == 1


def test_usa_indice_de_fecha(conn):
    ini, fin = _rango_utc_dia_local("2026-10-24")
    plan = conn.execute(
        "EXPLAIN QUERY PLAN SELECT id FROM cargas_servicios WHERE fecha_hora_utc >= ? AND fecha_hora_utc < ?",
        (ini, fin),
    ).fetchall()
    assert any("idx_cargas_servicios_fecha_hora_utc" in str(p) for p in plan)


def test_endpoints_http_con_fecha(conn):
    app.dependency_overrides[get_db_connection] = lambda: conn
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")
    try:
        with TestClient(app) as client:
            r = client.get("/api/services?fecha=2026-10-24")
            assert r.status_code == 200 and r.json()["total"] == 3
            r = client.get("/api/services?fecha=24/10/2026&incluir_anuladas=true")
            assert r.json()["total"] == 4
            assert client.get("/api/services").json()["total"] == 5
            s = client.get("/api/services/summary?fecha=2026-10-24").json()
            assert s["total_operaciones"] == 3 and s["operaciones_anuladas"] == 1
    finally:
        app.dependency_overrides.clear()
