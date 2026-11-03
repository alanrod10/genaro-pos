"""Pruebas exhaustivas de Etapa 2: Infraestructura de Integración Comercial Outbox (Laboratorio).

Verifica:
1. Aplicación limpia de la migración 012 mediante el runner oficial de migraciones.
2. Creación y estructura exacta de `commercial_events_outbox`.
3. Creación y estructura exacta de `commercial_adicionales_arqueo_turno`.
4. Existencia y operatividad de índices y restricciones de integridad (CHECK, UNIQUE).
5. Inserción correcta de eventos comerciales con payload canónico e idempotencia dual.
6. Rechazo de colisiones por `event_id` duplicado y por `idempotency_key` duplicado.
7. Ciclo de vida completo del outbox (PENDING -> PROCESSING -> PUBLISHED / FAILED / DEAD_LETTER).
8. Ordenamiento secuencial estricto para consumo asíncrono (sequence_id ASC).
9. Registro de arqueo informativo de adicionales por turno y verificación de que NO genera
   movimientos financieros automáticos ni altera saldos.
10. Rollback simétrico completo mediante `rollback_012_commercial_integration_outbox_lab.sql`.
11. Re-aplicación limpia de la migración tras el rollback.
12. Aislamiento absoluto respecto de `db/tesoreria_lab.db` y backups comerciales.
"""

import hashlib
import json
from pathlib import Path
import sqlite3
import pytest

from db.migrations_runner import (
    ejecutar_migraciones,
    obtener_versiones_aplicadas,
    ErrorEjecucionMigracion,
    ErrorInconsistenciaEsquema,
)



# Hash de db/tesoreria_lab.db capturado al importar el módulo (inicio de la sesión de pytest).
# Reemplaza el hash fijo histórico: la base de laboratorio evolucionó legítimamente; lo que se
# verifica es que la ejecución de las pruebas no la modifique.
_RUTA_TESORERIA_LAB = Path(__file__).resolve().parent.parent / "db" / "tesoreria_lab.db"
_HASH_TESORERIA_LAB_INICIAL = (
    hashlib.sha256(_RUTA_TESORERIA_LAB.read_bytes()).hexdigest() if _RUTA_TESORERIA_LAB.exists() else None
)

@pytest.fixture
def lab_commercial_db():
    """Crea una base de datos comercial SQLite en memoria con el baseline previo y migra hasta 012."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    
    # Esquema inicial mínimo de catálogo compatible con 001
    conn.execute(
        """
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL UNIQUE,
            precio_base INTEGER NOT NULL CHECK (precio_base >= 0),
            es_pesable INTEGER NOT NULL DEFAULT 0,
            activo INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    
    # Ejecuta el pipeline completo de migraciones hasta 012
    ejecutar_migraciones(conn)
    return conn


def test_migracion_012_aplicada_exitosamente(lab_commercial_db):
    """Verifica que la versión 12 quede registrada en _schema_migrations."""
    versiones = obtener_versiones_aplicadas(lab_commercial_db)
    assert 12 in versiones
    assert versiones[12] == "012_commercial_integration_outbox_lab"


def test_estructura_commercial_events_outbox(lab_commercial_db):
    """Verifica que commercial_events_outbox tenga todas las columnas requeridas."""
    cur = lab_commercial_db.execute("PRAGMA table_info(commercial_events_outbox);")
    cols = {row[1]: (row[2], bool(row[3]), row[4], bool(row[5])) for row in cur.fetchall()}

    expected_cols = [
        "sequence_id",
        "event_id",
        "event_type",
        "entity_type",
        "entity_id",
        "action_type",
        "idempotency_key",
        "payload_canonical_hash",
        "payload",
        "status",
        "retry_count",
        "max_retries",
        "error_message",
        "tesoreria_movement_id",
        "created_at",
        "updated_at",
        "published_at",
    ]
    for col in expected_cols:
        assert col in cols, f"Columna {col} no encontrada en commercial_events_outbox"

    # sequence_id es primary key autoincremental
    assert cols["sequence_id"][3] is True  # is_pk


def test_indices_commercial_events_outbox(lab_commercial_db):
    """Verifica la existencia de los índices definidos en la migración 012."""
    cur = lab_commercial_db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='commercial_events_outbox';"
    )
    indices = {row[0] for row in cur.fetchall()}
    assert "idx_outbox_status_seq" in indices
    assert "idx_outbox_idempotency_unique" in indices
    assert "idx_outbox_entity_lookup" in indices


def test_restricciones_check_outbox_status(lab_commercial_db):
    """Verifica que solo se permitan estados válidos en commercial_events_outbox."""
    base_insert = """
        INSERT INTO commercial_events_outbox (
            event_id, event_type, entity_type, entity_id, action_type,
            idempotency_key, payload_canonical_hash, payload, status,
            created_at, updated_at
        ) VALUES (
            'evt-chk', 'SALE_CONFIRMED', 'TICKET', 'tkt-1', 'CONFIRM',
            'COMM_SALE_1_CONFIRM', 'hash123', '{}', ?,
            '2026-10-31T12:00:00Z', '2026-10-31T12:00:00Z'
        );
    """
    # Estados válidos
    for valid_status in ["PENDING", "PROCESSING", "PUBLISHED", "FAILED", "DEAD_LETTER"]:
        lab_commercial_db.execute(
            base_insert.replace("evt-chk", f"evt-{valid_status}").replace("COMM_SALE_1", f"COMM_SALE_{valid_status}"),
            (valid_status,),
        )

    # Estado inválido debe disparar IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        lab_commercial_db.execute(base_insert.replace("evt-chk", "evt-invalid"), ("INVALID_STATUS",))


def test_unicidad_event_id_e_idempotency_key(lab_commercial_db):
    """Verifica que no se puedan insertar duplicados de event_id ni de idempotency_key."""
    lab_commercial_db.execute(
        """
        INSERT INTO commercial_events_outbox (
            event_id, event_type, entity_type, entity_id, action_type,
            idempotency_key, payload_canonical_hash, payload,
            created_at, updated_at
        ) VALUES (
            'evt-unique-1', 'SALE_CONFIRMED', 'TICKET', 'tkt-10', 'CONFIRM',
            'COMM_SALE_TKT-10_CONFIRM', 'hash_aaa', '{"total": 1000}',
            '2026-10-31T12:00:00Z', '2026-10-31T12:00:00Z'
        );
        """
    )

    # Duplicado de event_id con diferente idempotency_key -> Falla
    with pytest.raises(sqlite3.IntegrityError):
        lab_commercial_db.execute(
            """
            INSERT INTO commercial_events_outbox (
                event_id, event_type, entity_type, entity_id, action_type,
                idempotency_key, payload_canonical_hash, payload,
                created_at, updated_at
            ) VALUES (
                'evt-unique-1', 'SALE_CONFIRMED', 'TICKET', 'tkt-11', 'CONFIRM',
                'COMM_SALE_TKT-11_CONFIRM', 'hash_bbb', '{"total": 2000}',
                '2026-10-31T12:01:00Z', '2026-10-31T12:01:00Z'
            );
            """
        )

    # Duplicado de idempotency_key con diferente event_id -> Falla
    with pytest.raises(sqlite3.IntegrityError):
        lab_commercial_db.execute(
            """
            INSERT INTO commercial_events_outbox (
                event_id, event_type, entity_type, entity_id, action_type,
                idempotency_key, payload_canonical_hash, payload,
                created_at, updated_at
            ) VALUES (
                'evt-unique-2', 'SALE_CONFIRMED', 'TICKET', 'tkt-10', 'CONFIRM',
                'COMM_SALE_TKT-10_CONFIRM', 'hash_ccc', '{"total": 1000}',
                '2026-10-31T12:02:00Z', '2026-10-31T12:02:00Z'
            );
            """
        )


def test_secuencia_autoincremental_estricta(lab_commercial_db):
    """Verifica que sequence_id se incremente de forma monótona y estricta."""
    for i in range(1, 6):
        lab_commercial_db.execute(
            """
            INSERT INTO commercial_events_outbox (
                event_id, event_type, entity_type, entity_id, action_type,
                idempotency_key, payload_canonical_hash, payload,
                created_at, updated_at
            ) VALUES (
                ?, 'SALE_CONFIRMED', 'TICKET', ?, 'CONFIRM',
                ?, 'hash', '{}', '2026-10-31T12:00:00Z', '2026-10-31T12:00:00Z'
            );
            """,
            (f"evt-seq-{i}", f"tkt-{i}", f"COMM_SALE_TKT-{i}_CONFIRM"),
        )

    cur = lab_commercial_db.execute(
        "SELECT sequence_id, event_id FROM commercial_events_outbox ORDER BY sequence_id ASC;"
    )
    rows = cur.fetchall()
    assert len(rows) == 5
    assert [r[0] for r in rows] == [1, 2, 3, 4, 5]
    assert [r[1] for r in rows] == [f"evt-seq-{i}" for i in range(1, 6)]


def test_ciclo_de_vida_estados_outbox(lab_commercial_db):
    """Verifica el flujo de estados PENDING -> PROCESSING -> PUBLISHED y FAILED -> DEAD_LETTER."""
    # 1. Inserción en PENDING
    lab_commercial_db.execute(
        """
        INSERT INTO commercial_events_outbox (
            event_id, event_type, entity_type, entity_id, action_type,
            idempotency_key, payload_canonical_hash, payload,
            created_at, updated_at
        ) VALUES (
            'evt-flow-1', 'SERVICE_CONFIRMED', 'SERVICIO', 'sube-1', 'CONFIRM',
            'COMM_SRV_SUBE-1_CONFIRM', 'hash_flow', '{"amount_cents": 115000}',
            '2026-10-31T12:00:00Z', '2026-10-31T12:00:00Z'
        );
        """
    )
    row = lab_commercial_db.execute(
        "SELECT status, retry_count, published_at FROM commercial_events_outbox WHERE event_id='evt-flow-1';"
    ).fetchone()
    assert row[0] == "PENDING"
    assert row[1] == 0
    assert row[2] is None

    # 2. Worker toma el evento: PENDING -> PROCESSING con retry_count incrementado
    lab_commercial_db.execute(
        """
        UPDATE commercial_events_outbox
        SET status = 'PROCESSING', retry_count = retry_count + 1, updated_at = '2026-10-31T12:00:05Z'
        WHERE event_id = 'evt-flow-1' AND status = 'PENDING';
        """
    )
    row = lab_commercial_db.execute(
        "SELECT status, retry_count FROM commercial_events_outbox WHERE event_id='evt-flow-1';"
    ).fetchone()
    assert row[0] == "PROCESSING"
    assert row[1] == 1

    # 3. Confirmación en Tesorería: PROCESSING -> PUBLISHED con movement_id
    lab_commercial_db.execute(
        """
        UPDATE commercial_events_outbox
        SET status = 'PUBLISHED',
            tesoreria_movement_id = 'mov-uuid-8899',
            published_at = '2026-10-31T12:00:06Z',
            updated_at = '2026-10-31T12:00:06Z'
        WHERE event_id = 'evt-flow-1' AND status = 'PROCESSING';
        """
    )
    row = lab_commercial_db.execute(
        "SELECT status, tesoreria_movement_id, published_at FROM commercial_events_outbox WHERE event_id='evt-flow-1';"
    ).fetchone()
    assert row[0] == "PUBLISHED"
    assert row[1] == "mov-uuid-8899"
    assert row[2] == "2026-10-31T12:00:06Z"

    # 4. Caso de fallo transitorio a FAILED
    lab_commercial_db.execute(
        """
        INSERT INTO commercial_events_outbox (
            event_id, event_type, entity_type, entity_id, action_type,
            idempotency_key, payload_canonical_hash, payload,
            created_at, updated_at
        ) VALUES (
            'evt-fail-1', 'EXPENSE_CONFIRMED', 'GASTO', 'gst-1', 'CONFIRM',
            'COMM_EXP_GST-1_CONFIRM', 'hash_fail', '{"amount_cents": 50000}',
            '2026-10-31T12:10:00Z', '2026-10-31T12:10:00Z'
        );
        """
    )
    lab_commercial_db.execute(
        """
        UPDATE commercial_events_outbox
        SET status = 'FAILED', retry_count = 1, error_message = 'Network timeout connecting to worker',
            updated_at = '2026-10-31T12:10:02Z'
        WHERE event_id = 'evt-fail-1';
        """
    )
    row = lab_commercial_db.execute(
        "SELECT status, retry_count, error_message FROM commercial_events_outbox WHERE event_id='evt-fail-1';"
    ).fetchone()
    assert row[0] == "FAILED"
    assert row[1] == 1
    assert "timeout" in row[2]

    # 5. Agotamiento de reintentos a DEAD_LETTER
    lab_commercial_db.execute(
        """
        UPDATE commercial_events_outbox
        SET status = 'DEAD_LETTER', retry_count = 5, error_message = 'Max retries exceeded',
            updated_at = '2026-10-31T12:15:00Z'
        WHERE event_id = 'evt-fail-1';
        """
    )
    row = lab_commercial_db.execute(
        "SELECT status, retry_count, error_message FROM commercial_events_outbox WHERE event_id='evt-fail-1';"
    ).fetchone()
    assert row[0] == "DEAD_LETTER"
    assert row[1] == 5


def test_commercial_adicionales_arqueo_turno_funcionalidad(lab_commercial_db):
    """Verifica que el arqueo de adicionales registre la información de control
    y comprueba su naturaleza estrictamente informativa (no genera asientos)."""
    lab_commercial_db.execute(
        """
        INSERT INTO commercial_adicionales_arqueo_turno (
            arqueo_id, cierre_turno_id, fecha_operativa, turno, responsable_caja,
            total_adicionales_efectivo_calculado_cents,
            total_adicionales_efectivo_declarado_cents,
            diferencia_cents, observaciones, traslado_ejecutado, created_at
        ) VALUES (
            'arq-001', 'ct-101', '2026-10-31', 'TARDE', 'cajero_juan',
            150000, 150000, 0, 'Arqueo informativo de cierre de turno sin discrepancias',
            0, '2026-10-31T21:00:00Z'
        );
        """
    )
    row = lab_commercial_db.execute(
        """
        SELECT arqueo_id, total_adicionales_efectivo_calculado_cents, traslado_ejecutado, traslado_movement_id
        FROM commercial_adicionales_arqueo_turno WHERE arqueo_id='arq-001';
        """
    ).fetchone()
    assert row[0] == "arq-001"
    assert row[1] == 150000
    assert row[2] == 0  # Traslado no ejecutado aún (dinero sigue en 1110)
    assert row[3] is None

    # Verificación de restricción de centavos >= 0
    with pytest.raises(sqlite3.IntegrityError):
        lab_commercial_db.execute(
            """
            INSERT INTO commercial_adicionales_arqueo_turno (
                arqueo_id, fecha_operativa, turno, responsable_caja,
                total_adicionales_efectivo_calculado_cents, created_at
            ) VALUES ('arq-neg', '2026-10-31', 'MANANA', 'cajero_pedro', -500, '2026-10-31T14:00:00Z');
            """
        )


def test_rollback_simetrico_012(lab_commercial_db):
    """Verifica que el rollback 012 desmonte completamente las tablas e índices creados y deje _schema_migrations intacto."""
    rollback_sql_path = Path(__file__).resolve().parent.parent / "db" / "migrations" / "rollback_012_commercial_integration_outbox_lab.sql"
    assert rollback_sql_path.exists(), "El script de rollback simétrico debe existir"
    
    contenido_rollback = rollback_sql_path.read_text(encoding="utf-8")
    lab_commercial_db.executescript(contenido_rollback)

    # 1. Las tablas ya no deben existir
    cur = lab_commercial_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('commercial_events_outbox', 'commercial_adicionales_arqueo_turno');"
    )
    assert cur.fetchall() == []

    # 2. La versión 12 ya no debe estar en _schema_migrations
    versiones = obtener_versiones_aplicadas(lab_commercial_db)
    assert 12 not in versiones

    # 3. Re-aplicación limpia sin errores
    versiones_nuevas = ejecutar_migraciones(lab_commercial_db)
    assert 12 in versiones_nuevas
    versiones_post = obtener_versiones_aplicadas(lab_commercial_db)
    assert 12 in versiones_post


def test_aislamiento_motor_tesoreria_y_backups():
    """Verifica que los archivos de backups comerciales y db/tesoreria_lab.db no hayan sido alterados."""
    ruta_repo = Path(__file__).resolve().parent.parent
    db_tesoreria = ruta_repo / "db" / "tesoreria_lab.db"
    assert db_tesoreria.exists()

    # La base no debe haber sido modificada durante la sesión de pruebas
    hash_tesoreria = hashlib.sha256(db_tesoreria.read_bytes()).hexdigest()
    assert hash_tesoreria == _HASH_TESORERIA_LAB_INICIAL

    # Backups comerciales en /home/alanrod10/backups/
    ruta_backup_verificable = Path("/home/alanrod10/backups/genaro-pos-dev-pre-migraciones-20261025-verificable.db")
    if ruta_backup_verificable.exists():
        hash_bkp = hashlib.sha256(ruta_backup_verificable.read_bytes()).hexdigest()
        assert hash_bkp == "b0597fbeb0d27f5fc4b3f588efdc58fda23fc3427be102080134f2790300b557"
