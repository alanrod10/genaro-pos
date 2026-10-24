import sqlite3

import pytest

from db.services import (
    ErrorCargasTurso,
    cargar_cargas_turso,
    guardar_ediciones_cargas_turso,
    registrar_carga_turso,
)


@pytest.fixture
def conn():
    conexion = sqlite3.connect(":memory:")
    conexion.execute(
        """
        CREATE TABLE cargas_servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legacy_row_id TEXT,
            fecha_hora_utc TEXT NOT NULL,
            servicio TEXT NOT NULL COLLATE NOCASE,
            monto_carga INTEGER NOT NULL CHECK (monto_carga > 0),
            monto_adicional INTEGER NOT NULL DEFAULT 0
                CHECK (monto_adicional >= 0),
            total_cobrado INTEGER NOT NULL CHECK (total_cobrado >= 0),
            pago_efectivo INTEGER NOT NULL DEFAULT 0
                CHECK (pago_efectivo >= 0),
            pago_transferencia INTEGER NOT NULL DEFAULT 0
                CHECK (pago_transferencia >= 0),
            anulado INTEGER NOT NULL DEFAULT 0 CHECK (anulado IN (0, 1)),
            CHECK (total_cobrado = monto_carga + monto_adicional),
            CHECK (
                total_cobrado = pago_efectivo + pago_transferencia
            )
        )
        """
    )
    conexion.execute(
        """
        CREATE UNIQUE INDEX uq_cargas_legacy_row_id
        ON cargas_servicios (legacy_row_id)
        """
    )
    conexion.commit()
    yield conexion
    conexion.close()


def registro(**cambios):
    base = {
        "FECHA": "23/10/2026 12:30:00",
        "SERVICIO": "Claro",
        "MONTO_CARGA": 7000,
        "MONTO_ADICIONAL": 400,
        "TOTAL_COBRADO": 7400,
        "PAGO_EFVO": 7000,
        "PAGO_TRANSF": 400,
    }
    base.update(cambios)
    return base


def test_alta_y_conversion_de_hora_local(conn):
    nuevo_id = registrar_carga_turso(registro(), conn=conn)

    df = cargar_cargas_turso(conn=conn)

    assert len(df) == 1
    assert int(df.iloc[0]["_TURSO_ID"]) == nuevo_id
    assert df.iloc[0]["FECHA"] == "23/10/2026 12:30:00"
    assert df.iloc[0]["SERVICIO"] == "Claro"
    assert int(df.iloc[0]["TOTAL_COBRADO"]) == 7400


def test_edicion_y_anulacion_por_id_no_mezclan_duplicados(conn):
    id_1 = registrar_carga_turso(registro(), conn=conn)
    id_2 = registrar_carga_turso(registro(), conn=conn)
    assert id_1 != id_2

    editada = registro(
        SERVICIO="Movistar",
        MONTO_CARGA=8000,
        MONTO_ADICIONAL=200,
        TOTAL_COBRADO=8200,
        PAGO_EFVO=8200,
        PAGO_TRANSF=0,
    )

    guardar_ediciones_cargas_turso(
        actualizaciones=[(id_1, editada)],
        ids_anulados=[id_2],
        nuevas=[],
        conn=conn,
    )

    df = cargar_cargas_turso(conn=conn)
    assert len(df) == 1
    assert int(df.iloc[0]["_TURSO_ID"]) == id_1
    assert df.iloc[0]["SERVICIO"] == "Movistar"

    filas = conn.execute(
        "SELECT id, anulado FROM cargas_servicios ORDER BY id"
    ).fetchall()
    assert filas == [(id_1, 0), (id_2, 1)]


def test_rechaza_balance_incorrecto_sin_insertar(conn):
    with pytest.raises(ErrorCargasTurso, match="suma de efectivo"):
        registrar_carga_turso(
            registro(PAGO_EFVO=6900, PAGO_TRANSF=400),
            conn=conn,
        )

    assert conn.execute(
        "SELECT COUNT(*) FROM cargas_servicios"
    ).fetchone()[0] == 0


def test_actualizacion_atomica_si_un_id_ya_no_existe(conn):
    id_1 = registrar_carga_turso(registro(), conn=conn)
    cambio = registro(
        SERVICIO="Movistar",
        MONTO_CARGA=8000,
        MONTO_ADICIONAL=200,
        TOTAL_COBRADO=8200,
        PAGO_EFVO=8200,
        PAGO_TRANSF=0,
    )

    with pytest.raises(ErrorCargasTurso, match="Recargá el historial"):
        guardar_ediciones_cargas_turso(
            actualizaciones=[(id_1, cambio)],
            ids_anulados=[999999],
            nuevas=[],
            conn=conn,
        )

    df = cargar_cargas_turso(conn=conn)
    assert len(df) == 1
    assert df.iloc[0]["SERVICIO"] == "Claro"
    assert int(df.iloc[0]["MONTO_CARGA"]) == 7000
