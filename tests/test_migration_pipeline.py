"""Pruebas exhaustivas para el pipeline seguro de migraciones locales (001 a 009) en GenaroPOS.

Verifica:
1. Aplicación secuencial desde 001 hasta 009.
2. Registro correlativo de las 9 versiones en _schema_migrations.
3. Creación correcta de tablas de preventistas, pedidos y auditoría.
4. Registro atómico y correcto de la versión 8 ('008_preventistas_y_pedidos').
5. Existencia de 'ticket_items.costo_unitario' tras la versión 9.
6. Conservación de datos históricos con costo_unitario IS NULL (sin inventar ceros).
7. Persistencia correcta de costos históricos congelados en nuevas ventas.
8. Reejecución segura del proceso sin fallos por ALTER TABLE repetido.
9. Detección y rechazo explícito (ErrorInconsistenciaEsquema) si costo_unitario ya existe sin versión 9.
10. Rollback transaccional atómico si una migración falla a mitad de camino.
11. Detección y advertencia en el Dashboard cuando el esquema está incompleto.
"""

from pathlib import Path
import sqlite3
import pytest

from db.migrations_runner import (
    ErrorInconsistenciaEsquema,
    ErrorEjecucionMigracion,
    ejecutar_migraciones,
    obtener_versiones_aplicadas,
    listar_archivos_migracion,
)
from db.reports import calcular_metricas_dashboard_turso


@pytest.fixture
def clean_db():
    """Crea una base de datos SQLite en memoria con esquema de catálogo inicial."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    # Esquema inicial preexistente de productos (equivalente al estado inicial de Turso DEV)
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
    # Semilla de producto
    conn.execute(
        "INSERT INTO productos (id, nombre, costo, precio_dia) VALUES (1, 'Golosina Test', 300, 600);"
    )
    conn.commit()
    yield conn
    conn.close()


def test_1_aplicacion_secuencial_completa_001_a_010(clean_db):
    """1. Aplica secuencialmente todas las migraciones desde 001 hasta 012."""
    aplicadas = ejecutar_migraciones(clean_db)
    assert set(range(1, 13)).issubset(set(aplicadas))

    # Verificar que existan todas las tablas esperadas
    cursor = clean_db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    tablas = {r[0] for r in cursor.fetchall()}
    esperadas = {
        "_schema_migrations",
        "productos",
        "cargas_servicios",
        "cargas_import_batch",
        "tickets",
        "ticket_items",
        "outbox_events",
        "productos_auditoria",
        "cargas_servicios_auditoria",
        "gastos_retiros",
        "cierres_diarios",
        "gastos_auditoria",
        "ventas_auditoria",
        "pedidos_proveedor",
        "pedido_items",
        "pedidos_auditoria",
        "auth_scram_challenges",
        "commercial_events_outbox",
        "commercial_adicionales_arqueo_turno",
    }
    assert esperadas.issubset(tablas)

    # Verificar que las columnas de idempotencia existan tras la migración 010
    cols_cargas = {r[1] for r in clean_db.execute("PRAGMA table_info(cargas_servicios);").fetchall()}
    assert "idempotency_key" in cols_cargas
    cols_gastos = {r[1] for r in clean_db.execute("PRAGMA table_info(gastos_retiros);").fetchall()}
    assert "idempotency_key" in cols_gastos

    # Verificar esquema de auth_scram_challenges tras la migración 011
    cols_scram = {r[1] for r in clean_db.execute("PRAGMA table_info(auth_scram_challenges);").fetchall()}
    assert {"jti", "exp", "used", "creado_en_utc"}.issubset(cols_scram)


def test_2_registro_correlativo_y_version_8(clean_db):
    """2 y 4. Verifica el registro correlativo de las 12 versiones e inclusión de versiones 8, 10, 11 y 12."""
    ejecutar_migraciones(clean_db)
    versiones = obtener_versiones_aplicadas(clean_db)

    assert set(range(1, 13)).issubset(set(versiones.keys()))
    assert "008_preventistas_y_pedidos" in versiones[8]
    assert "010_idempotencia_servicios_gastos" in versiones[10]
    assert "011_auth_scram_challenges" in versiones[11]
    assert "012_commercial_integration_outbox_lab" in versiones[12]


def test_3_tablas_preventistas_y_pedidos_funcionales(clean_db):
    """3. Verifica que pedidos_proveedor, pedido_items y pedidos_auditoria operan con integridad."""
    ejecutar_migraciones(clean_db)

    # Inserción de pedido
    clean_db.execute(
        """
        INSERT INTO pedidos_proveedor (id, fecha, proveedor, estado, total, observaciones)
        VALUES ('PED-001', '2026-10-25', 'Arcor', 'PENDIENTE', 15000, 'Pedido semanal');
        """
    )
    clean_db.execute(
        """
        INSERT INTO pedido_items (pedido_id, producto_id, producto_nombre, cantidad, costo_unitario, subtotal)
        VALUES ('PED-001', 1, 'Golosina Test', 50, 300, 15000);
        """
    )
    clean_db.execute(
        """
        INSERT INTO pedidos_auditoria (pedido_id, accion, valores_anteriores, valores_nuevos, actor)
        VALUES ('PED-001', 'CREAR_PEDIDO', NULL, '{\"total\": 15000}', 'alan');
        """
    )
    clean_db.commit()

    pedido = clean_db.execute("SELECT id, proveedor, total FROM pedidos_proveedor WHERE id = 'PED-001'").fetchone()
    assert pedido == ("PED-001", "Arcor", 15000.0)

    # Verificación de borrado en cascada
    clean_db.execute("DELETE FROM pedidos_proveedor WHERE id = 'PED-001'")
    clean_db.commit()
    items_restantes = clean_db.execute("SELECT COUNT(*) FROM pedido_items WHERE pedido_id = 'PED-001'").fetchone()[0]
    assert items_restantes == 0


def test_5_y_6_costo_unitario_preserva_historicos_como_null(clean_db):
    """5 y 6. Datos preexistentes antes de 009 deben conservar costo_unitario = NULL tras migrar."""
    # Aplicar hasta 003
    ejecutar_migraciones(clean_db, hasta_version=3)

    # Insertar ticket histórico sin costo_unitario
    clean_db.execute(
        """
        INSERT INTO tickets (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago)
        VALUES ('T-HIST-001', '2026-10-24 10:00:00', 600, 600, 0, 'EFECTIVO');
        """
    )
    clean_db.execute(
        """
        INSERT INTO ticket_items (id, ticket_id, producto_id, producto_nombre, cantidad, precio_unitario, subtotal, metodo_pago)
        VALUES (100, 'T-HIST-001', 1, 'Golosina Test', 1.0, 600, 600, 'EFECTIVO');
        """
    )
    clean_db.commit()

    # Aplicar el resto de migraciones (004 a 009)
    ejecutar_migraciones(clean_db)

    # Verificar que el ítem histórico tiene costo_unitario = NULL
    item_hist = clean_db.execute("SELECT id, costo_unitario FROM ticket_items WHERE id = 100").fetchone()
    assert item_hist[0] == 100
    assert item_hist[1] is None  # Estrictamente NULL, jamás 0


def test_7_persistencia_costo_en_venta_nueva(clean_db):
    """7. Verifica que tras la migración 009, una venta nueva persiste el snapshot de costo."""
    ejecutar_migraciones(clean_db)

    clean_db.execute(
        """
        INSERT INTO tickets (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago)
        VALUES ('T-NUEVO-002', '2026-10-25 11:00:00', 1200, 1200, 0, 'EFECTIVO');
        """
    )
    clean_db.execute(
        """
        INSERT INTO ticket_items (ticket_id, producto_id, producto_nombre, cantidad, precio_unitario, subtotal, metodo_pago, costo_unitario)
        VALUES ('T-NUEVO-002', 1, 'Golosina Test', 2.0, 600, 1200, 'EFECTIVO', 300);
        """
    )
    clean_db.commit()

    item_nuevo = clean_db.execute("SELECT ticket_id, costo_unitario FROM ticket_items WHERE ticket_id = 'T-NUEVO-002'").fetchone()
    assert item_nuevo == ("T-NUEVO-002", 300)


def test_8_reejecucion_idempotente_sin_errores(clean_db):
    """8. Reejecutar el proceso completo sobre una base migrada omite todas las migraciones sin error."""
    primera_vez = ejecutar_migraciones(clean_db)
    assert len(primera_vez) >= 12

    # Reejecución
    segunda_vez = ejecutar_migraciones(clean_db)
    assert segunda_vez == []  # Cero migraciones nuevas, sin fallos en ALTER TABLE


def test_9_deteccion_inconsistencia_costo_unitario_sin_version_9(clean_db):
    """9. Si costo_unitario existe pero la versión 9 no está registrada, el ejecutor debe abortar con ErrorInconsistenciaEsquema."""
    # Aplicar hasta la versión 8
    ejecutar_migraciones(clean_db, hasta_version=8)

    # Forzar inconsistencia manual en el esquema (agregar la columna sin asentar la versión 9)
    clean_db.execute("ALTER TABLE ticket_items ADD COLUMN costo_unitario INTEGER;")
    clean_db.commit()

    # Intentar ejecutar migraciones debe levantar ErrorInconsistenciaEsquema
    with pytest.raises(ErrorInconsistenciaEsquema) as excinfo:
        ejecutar_migraciones(clean_db)

    assert "costo_unitario" in str(excinfo.value)
    assert "versión 9" in str(excinfo.value)


def test_10_rollback_atomico_ante_fallo(clean_db, tmp_path):
    """10. Si una migración transaccional falla, no deben quedar cambios parciales."""
    ejecutar_migraciones(clean_db, hasta_version=3)

    # Crear una migración defectuosa temporal 004
    bad_migration = tmp_path / "004_bad_migration.sql"
    bad_migration.write_text(
        """
        BEGIN;
        CREATE TABLE tabla_prueba_rollback (id INT);
        SELECT * FROM tabla_que_no_existe_para_forzar_error;
        COMMIT;
        """,
        encoding="utf-8",
    )

    with pytest.raises(ErrorEjecucionMigracion):
        ejecutar_migraciones(clean_db, directorio=tmp_path)

    # Comprobar que la tabla no quedó creada
    cursor = clean_db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tabla_prueba_rollback'")
    assert cursor.fetchone() is None


def test_11_dashboard_distingue_esquema_incompleto(clean_db):
    """11. Verifica que el Dashboard identifique explícitamente la ausencia de tablas requeridas y no entregue un falso saldo contable."""
    # Aplicar solo hasta 003 (faltan gastos_retiros y cierres_diarios)
    ejecutar_migraciones(clean_db, hasta_version=3)

    metricas = calcular_metricas_dashboard_turso(conn=clean_db)

    # Debe advertir que el esquema está incompleto
    assert metricas["esquema_completo"] is False
    assert "gastos_retiros" in metricas["tablas_faltantes"]
    assert "cierres_diarios" in metricas["tablas_faltantes"]
    assert metricas["advertencia_esquema"] is not None
    assert "Esquema incompleto" in metricas["advertencia_esquema"]

    # Al completar las migraciones (004 a 009)
    ejecutar_migraciones(clean_db)
    metricas_completas = calcular_metricas_dashboard_turso(conn=clean_db)

    assert metricas_completas["esquema_completo"] is True
    assert metricas_completas["tablas_faltantes"] == []
    assert metricas_completas["advertencia_esquema"] is None


def test_12_migracion_010_aplica_correctamente_en_esquema_limpio(clean_db):
    """12. Verifica que sobre un esquema limpio con migraciones 1..9, la migración 010 aplique sin error."""
    ejecutar_migraciones(clean_db, hasta_version=9)

    # Verificar que antes de la versión 10 no existan las columnas ni los índices
    cols_cargas_antes = {r[1] for r in clean_db.execute("PRAGMA table_info(cargas_servicios);").fetchall()}
    assert "idempotency_key" not in cols_cargas_antes
    cols_gastos_antes = {r[1] for r in clean_db.execute("PRAGMA table_info(gastos_retiros);").fetchall()}
    assert "idempotency_key" not in cols_gastos_antes

    # Aplicar migración 10
    nuevas = ejecutar_migraciones(clean_db, hasta_version=10)
    assert nuevas == [10]

    # Y aplicar migración 11 subsiguiente
    nuevas_11 = ejecutar_migraciones(clean_db, hasta_version=11)
    assert nuevas_11 == [11]

    # Verificar existencia de columnas e índices parciales
    cols_cargas_despues = {r[1] for r in clean_db.execute("PRAGMA table_info(cargas_servicios);").fetchall()}
    assert "idempotency_key" in cols_cargas_despues
    cols_gastos_despues = {r[1] for r in clean_db.execute("PRAGMA table_info(gastos_retiros);").fetchall()}
    assert "idempotency_key" in cols_gastos_despues

    # Verificar definición del índice parcial en sqlite_master
    sql_idx = clean_db.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_cargas_idempotency_key';"
    ).fetchone()[0]
    assert "UNIQUE" in sql_idx
    assert "WHERE idempotency_key IS NOT NULL" in sql_idx


def test_13_deteccion_inconsistencia_columnas_010_duplicadas(clean_db):
    """13. Si ambas columnas idempotency_key existen pero la versión 10 no está en _schema_migrations, aborta."""
    ejecutar_migraciones(clean_db, hasta_version=9)

    # Forzar alteración manual en ambas tablas fuera del runner
    clean_db.execute("ALTER TABLE cargas_servicios ADD COLUMN idempotency_key TEXT;")
    clean_db.execute("ALTER TABLE gastos_retiros ADD COLUMN idempotency_key TEXT;")
    clean_db.commit()

    with pytest.raises(ErrorInconsistenciaEsquema) as excinfo:
        ejecutar_migraciones(clean_db)

    msg = str(excinfo.value)
    assert "cargas_servicios" in msg
    assert "gastos_retiros" in msg
    assert "versión 10" in msg


def test_14_deteccion_inconsistencia_tabla_parcialmente_migrada_010(clean_db):
    """14. Si solo una tabla tiene la columna pero la versión 10 no está en _schema_migrations, aborta identificando la tabla."""
    ejecutar_migraciones(clean_db, hasta_version=9)

    # Solo alterar cargas_servicios
    clean_db.execute("ALTER TABLE cargas_servicios ADD COLUMN idempotency_key TEXT;")
    clean_db.commit()

    with pytest.raises(ErrorInconsistenciaEsquema) as excinfo:
        ejecutar_migraciones(clean_db)

    msg = str(excinfo.value)
    assert "cargas_servicios" in msg
    assert "versión 10" in msg


def test_15_deteccion_inconsistencia_indice_inesperado_010(clean_db):
    """15. Si existe un residuo de índice uq_cargas_idempotency_key sin versión 10 en _schema_migrations, aborta."""
    ejecutar_migraciones(clean_db, hasta_version=9)

    # Añadir columna e índice manual
    clean_db.execute("ALTER TABLE cargas_servicios ADD COLUMN idempotency_key TEXT;")
    clean_db.execute("CREATE UNIQUE INDEX uq_cargas_idempotency_key ON cargas_servicios(idempotency_key);")
    clean_db.commit()

    with pytest.raises(ErrorInconsistenciaEsquema) as excinfo:
        ejecutar_migraciones(clean_db)

    msg = str(excinfo.value)
    assert "uq_cargas_idempotency_key" in msg
    assert "versión 10" in msg


def test_16_migracion_011_esquema_auth_scram_challenges(clean_db):
    """16. Verifica que la migración 011 crea auth_scram_challenges con sus columnas e índices."""
    ejecutar_migraciones(clean_db, hasta_version=10)
    tablas_antes = {
        r[0] for r in clean_db.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
    }
    assert "auth_scram_challenges" not in tablas_antes

    nuevas = ejecutar_migraciones(clean_db, hasta_version=11)
    assert nuevas == [11]

    cols = {r[1] for r in clean_db.execute("PRAGMA table_info(auth_scram_challenges);").fetchall()}
    assert cols == {"jti", "exp", "used", "creado_en_utc"}

    idxs = {r[1] for r in clean_db.execute("PRAGMA index_list(auth_scram_challenges);").fetchall()}
    assert "idx_scram_challenges_exp" in idxs
    assert "idx_scram_challenges_used" in idxs
