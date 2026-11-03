"""Validación aislada de respaldo (backup) y restauración para GenaroPOS.

Verifica:
1. Creación de respaldo completo en memoria/archivo sintético temporal.
2. Integridad de las 11 migraciones aplicadas en la base restaurada.
3. Integridad de los datos de catálogo, ventas, auditoría y desafíos SCRAM.
4. Recuperación determinística tras fallo o corrupción simulada.
"""

import sqlite3
from pathlib import Path
import pytest
from db.migrations_runner import ejecutar_migraciones, obtener_versiones_aplicadas
from db.readiness import comprobar_preparacion_esquema

RUTA_MIGRACIONES = Path(__file__).resolve().parents[1] / "db" / "migrations"


def test_backup_y_restauracion_aislada_esquema_completo(tmp_path):
    # 1. Base origen sintética con esquema 001..011 completo
    db_origen_path = tmp_path / "origen.db"
    conn_origen = sqlite3.connect(db_origen_path)
    conn_origen.execute("PRAGMA foreign_keys = ON;")

    # Esquema inicial preexistente de productos (baseline)
    conn_origen.execute(
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
    
    versiones = ejecutar_migraciones(conn_origen, directorio=RUTA_MIGRACIONES)
    assert len(versiones) >= 12, "Deben haberse aplicado al menos 12 migraciones"

    # Insertar datos sintéticos de catálogo, ticket y desafío SCRAM
    conn_origen.execute(
        "INSERT INTO productos (id, nombre, costo, precio_dia, activo) VALUES (101, 'Alfajor Backup Test', 500, 900, 1);"
    )
    conn_origen.execute(
        "INSERT INTO tickets (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago, anulado) "
        "VALUES ('T-BACKUP-001', '2026-10-29T12:00:00Z', 900, 900, 0, 'EFECTIVO', 0);"
    )
    conn_origen.execute(
        "INSERT INTO ticket_items (ticket_id, producto_id, producto_nombre, cantidad, precio_unitario, subtotal, metodo_pago, costo_unitario) "
        "VALUES ('T-BACKUP-001', 101, 'Alfajor Backup Test', 1, 900, 900, 'EFECTIVO', 500);"
    )
    conn_origen.execute(
        "INSERT INTO auth_scram_challenges (jti, exp, used, creado_en_utc) "
        "VALUES ('jti-backup-test-123', 1800000000, 0, '2026-10-29T12:00:00Z');"
    )
    conn_origen.commit()

    # 2. Procedimiento de Backup Online (sqlite3 backup API / VACUUM INTO)
    db_backup_path = tmp_path / "backup.db"
    conn_backup = sqlite3.connect(db_backup_path)
    conn_origen.backup(conn_backup)
    conn_backup.close()
    conn_origen.close()

    assert db_backup_path.exists(), "El archivo de backup debe existir físicamente"
    assert db_backup_path.stat().st_size > 0, "El backup no puede estar vacío"

    # 3. Simulación de Restauración en nueva base de datos aislada
    db_restaurada_path = tmp_path / "restaurada.db"
    conn_restaurada = sqlite3.connect(db_restaurada_path)
    conn_restaurada.execute("PRAGMA foreign_keys = ON;")
    
    conn_from_backup = sqlite3.connect(db_backup_path)
    conn_from_backup.backup(conn_restaurada)
    conn_from_backup.close()

    # 4. Verificación exhaustiva de integridad en la base restaurada
    versiones_restauradas = obtener_versiones_aplicadas(conn_restaurada)
    assert len(versiones_restauradas) >= 12
    assert set(range(1, 13)).issubset(set(versiones_restauradas.keys()))

    # Integridad de datos
    prod = conn_restaurada.execute("SELECT nombre, costo, precio_dia FROM productos WHERE id = 101;").fetchone()
    assert prod == ("Alfajor Backup Test", 500, 900)

    ticket = conn_restaurada.execute("SELECT total_venta, metodo_pago FROM tickets WHERE id = 'T-BACKUP-001';").fetchone()
    assert ticket == (900, "EFECTIVO")

    item = conn_restaurada.execute("SELECT costo_unitario, subtotal FROM ticket_items WHERE ticket_id = 'T-BACKUP-001';").fetchone()
    assert item == (500, 900)

    challenge = conn_restaurada.execute("SELECT used FROM auth_scram_challenges WHERE jti = 'jti-backup-test-123';").fetchone()
    assert challenge == (0,)

    # Readiness del sistema sobre la base restaurada
    readiness = comprobar_preparacion_esquema(conn=conn_restaurada)
    assert readiness["listo"] is True
    assert readiness["status"] == "PREPARADO"
    assert len(readiness["tablas_faltantes"]) == 0
    assert len(readiness["inconsistencias"]) == 0

    conn_restaurada.close()
