import sqlite3
from pathlib import Path

import pytest

from db.import_cargas import (
    ConflictoIdentidadOrigen,
    ConflictoSnapshotCargas,
    ErrorImportacionCargas,
    migrar_cargas,
    prevalidar_cargas,
)
from db.normalization import utc_to_argentina


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "db/schema_v1.sql"
MIGRACION_1 = ROOT / "db/migrations/001_baseline_catalogo_existente.sql"
MIGRACION_2 = ROOT / "db/migrations/002_cargas_servicios.sql"


@pytest.fixture
def conn_local():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE productos (id INTEGER PRIMARY KEY, nombre TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO productos (id, nombre) VALUES (1, 'Producto sintético')"
    )
    conn.commit()
    conn.executescript(MIGRACION_1.read_text(encoding="utf-8"))
    conn.executescript(MIGRACION_2.read_text(encoding="utf-8"))
    yield conn
    conn.close()


@pytest.fixture
def csv_cargas(tmp_path):
    path = tmp_path / "DB_CARGAS.csv"
    contenido = (
        "FECHA,SERVICIO,MONTO_CARGA,MONTO_ADICIONAL,TOTAL_COBRADO,"
        "PAGO_EFVO,PAGO_TRANSF\n"
        "23/10/2026 12:00:00,Servicio Demo,1000,100,1100,1000,100\n"
        "23/10/2026 12:00:00,Servicio Demo,1000,100,1100,1000,100\n"
        "23/10/2026 12:01:00,Servicio Otro,7000,400,7400,7000,0\n"
    )
    path.write_text(contenido, encoding="utf-8")
    return path


def test_prevalidacion_preserva_duplicados_y_cuarentena(csv_cargas):
    result = prevalidar_cargas(csv_cargas)

    assert result.total_filas == 3
    assert len(result.validas) == 2
    assert len(result.cuarentena) == 1
    assert len(result.grupos_duplicados) == 1
    assert result.grupos_duplicados[0]["cantidad"] == 2

    q = result.cuarentena[0]
    assert q["_QUARANTINE_REASON"] == "BALANCE_PAGOS_INCONSISTENTE"
    assert q["_DIFERENCIA_TOTAL_MENOS_PAGOS"] == 400


def test_fecha_local_se_convierte_a_utc(csv_cargas):
    result = prevalidar_cargas(csv_cargas)
    dt_local = utc_to_argentina(result.validas[0]["fecha_hora_utc"])
    assert dt_local.hour == 12
    assert dt_local.day == 23
    assert dt_local.month == 10


def test_dry_run_no_inserta(conn_local, csv_cargas):
    result = migrar_cargas(csv_cargas, conn=conn_local, dry_run=True)

    assert result["validas"] == 2
    assert result["cuarentena"] == 1
    assert result["pendientes"] == 2
    assert result["insertadas"] == 0
    assert conn_local.execute(
        "SELECT COUNT(*) FROM cargas_servicios"
    ).fetchone()[0] == 0


def test_importacion_local_es_idempotente_y_conserva_duplicados(
    conn_local, csv_cargas
):
    with pytest.raises(ErrorImportacionCargas):
        migrar_cargas(
            csv_cargas, conn=conn_local, dry_run=False
        )

    first = migrar_cargas(
        csv_cargas,
        conn=conn_local,
        dry_run=False,
        confirmar_cuarentena=True,
    )
    second = migrar_cargas(
        csv_cargas,
        conn=conn_local,
        dry_run=False,
        confirmar_cuarentena=True,
    )

    assert first["insertadas"] == 2
    assert second["insertadas"] == 0
    assert second["existentes_con_mismo_contenido"] == 2
    assert conn_local.execute(
        "SELECT COUNT(*) FROM cargas_servicios"
    ).fetchone()[0] == 2
    assert conn_local.execute(
        "SELECT COUNT(DISTINCT legacy_row_id) FROM cargas_servicios"
    ).fetchone()[0] == 2


def test_conflicto_de_identidad_no_se_sobrescribe(conn_local, csv_cargas):
    first = migrar_cargas(
        csv_cargas,
        conn=conn_local,
        dry_run=False,
        confirmar_cuarentena=True,
    )
    legacy_id = first["auditoria"].validas[0]["legacy_row_id"]

    conn_local.execute(
        "UPDATE cargas_servicios SET servicio = 'Servicio editado' "
        "WHERE legacy_row_id = ?",
        (legacy_id,),
    )
    conn_local.commit()

    with pytest.raises(ConflictoIdentidadOrigen):
        migrar_cargas(
            csv_cargas,
            conn=conn_local,
            dry_run=False,
            confirmar_cuarentena=True,
        )

    assert conn_local.execute(
        "SELECT COUNT(*) FROM cargas_servicios"
    ).fetchone()[0] == 2


def test_exportacion_distinta_se_bloquea(conn_local, csv_cargas):
    migrar_cargas(
        csv_cargas,
        conn=conn_local,
        dry_run=False,
        confirmar_cuarentena=True,
    )

    modificada = csv_cargas.read_text(encoding="utf-8").replace(
        "Servicio Otro", "Servicio Editado"
    )
    csv_cargas.write_text(modificada, encoding="utf-8")

    with pytest.raises(ConflictoSnapshotCargas):
        migrar_cargas(csv_cargas, conn=conn_local, dry_run=True)

    assert conn_local.execute(
        "SELECT COUNT(*) FROM cargas_servicios"
    ).fetchone()[0] == 2
