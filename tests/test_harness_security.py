"""
Pruebas unitarias locales para la validación estricta de seguridad del harness (Fase 13).
Valida:
1. Análisis estructurado de la URL del endpoint (esquema, host exacto, ausencia de credenciales embebidas, rutas o puertos).
2. Sanitización y validación de identificadores de tablas en db/accounting.py.
3. Comportamiento de bloqueo del preflight ante omisión de variables de seguridad.
4. Código de salida de un preflight bloqueado (código 2).
5. Orden de confirmación entre UPDATE ... RETURNING y la prueba de rollback (aislamiento estricto).
6. Creación y limpieza de nombres sintéticos sin afectar objetos ajenos.
7. Coordinación concurrente de trabajadores en hilos con barrera de sincronización.
"""

import os
import sqlite3
import tempfile
import threading
import pytest
from db.accounting import (
    _validar_identificador_tabla,
    reclamar_lote_outbox,
    transaccion,
)
from tests.test_turso_dev_remote import (
    DESTINO_AUTORIZADO_HOST,
    DESTINO_AUTORIZADO_SCHEME,
    ESTADO_LIMPIEZA_COMPLETA,
    ESTADO_LIMPIEZA_INCOMPLETA,
    ESTADO_LIMPIEZA_NO_REQUERIDA,
    ESTADO_PREFLIGHT_BLOQUEADO,
    ESTADO_PREFLIGHT_OK,
    EXIT_CODE_PREFLIGHT_BLOCKED,
    OBJETO_INTERNO_PERMITIDO_NOMBRE,
    OBJETO_INTERNO_PERMITIDO_TIPO,
    ejecutar_limpieza_segura,
    ejecutar_preflight,
    inspeccionar_estado_inicial_base,
    main,
    validar_endpoint_estructurado,
)


def test_url_autorizada_exacta():
    url_valida = f"{DESTINO_AUTORIZADO_SCHEME}://{DESTINO_AUTORIZADO_HOST}"
    valido, detalle = validar_endpoint_estructurado(url_valida)
    assert valido is True
    assert detalle == ""


def test_rechazo_esquemas_invalidos():
    for esquema in ["https", "http", "libsql", "ftp", "ws"]:
        url = f"{esquema}://{DESTINO_AUTORIZADO_HOST}"
        valido, detalle = validar_endpoint_estructurado(url)
        assert valido is False
        assert "Esquema de URL no autorizado" in detalle


def test_rechazo_host_diferente_o_subdominio_falso():
    urls_invalidas = [
        f"{DESTINO_AUTORIZADO_SCHEME}://otro-host.turso.io",
        f"{DESTINO_AUTORIZADO_SCHEME}://{DESTINO_AUTORIZADO_HOST}.atacante.com",
        f"{DESTINO_AUTORIZADO_SCHEME}://fake-{DESTINO_AUTORIZADO_HOST}",
        f"{DESTINO_AUTORIZADO_SCHEME}://genaro-pos-prod.turso.io",
    ]
    for url in urls_invalidas:
        valido, detalle = validar_endpoint_estructurado(url)
        assert valido is False
        assert "Host remoto no autorizado" in detalle


def test_rechazo_credenciales_embebidas_en_url():
    url = f"{DESTINO_AUTORIZADO_SCHEME}://usuario:secreto@{DESTINO_AUTORIZADO_HOST}"
    valido, detalle = validar_endpoint_estructurado(url)
    assert valido is False
    assert "credenciales embebidas" in detalle


def test_rechazo_puerto_personalizado():
    url = f"{DESTINO_AUTORIZADO_SCHEME}://{DESTINO_AUTORIZADO_HOST}:8080"
    valido, detalle = validar_endpoint_estructurado(url)
    assert valido is False
    assert "puerto personalizado" in detalle


def test_rechazo_rutas_query_y_fragmentos():
    url_con_ruta = f"{DESTINO_AUTORIZADO_SCHEME}://{DESTINO_AUTORIZADO_HOST}/v1/query"
    url_con_query = f"{DESTINO_AUTORIZADO_SCHEME}://{DESTINO_AUTORIZADO_HOST}?admin=1"
    url_con_frag = f"{DESTINO_AUTORIZADO_SCHEME}://{DESTINO_AUTORIZADO_HOST}#seccion"

    assert validar_endpoint_estructurado(url_con_ruta)[0] is False
    assert validar_endpoint_estructurado(url_con_query)[0] is False
    assert validar_endpoint_estructurado(url_con_frag)[0] is False


def test_sanitizacion_identificadores_tabla():
    # Válidos
    assert _validar_identificador_tabla("tickets") == "tickets"
    assert _validar_identificador_tabla("_f13_probe_a1b2c3d4_tickets") == "_f13_probe_a1b2c3d4_tickets"

    # Inválidos / Inyecciones
    with pytest.raises(ValueError):
        _validar_identificador_tabla("tickets; DROP TABLE tickets;")
    with pytest.raises(ValueError):
        _validar_identificador_tabla("tickets--")
    with pytest.raises(ValueError):
        _validar_identificador_tabla("tickets union select 1")
    with pytest.raises(ValueError):
        _validar_identificador_tabla("123tickets")


def test_preflight_bloquea_sin_variables(monkeypatch):
    monkeypatch.delenv("ENV_NAME", raising=False)
    monkeypatch.delenv("TURSO_TEST_APPROVED", raising=False)
    monkeypatch.delenv("TURSO_DEV_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_DEV_AUTH_TOKEN", raising=False)

    aprobado, bloqueos = ejecutar_preflight()
    assert aprobado is False
    assert len(bloqueos) >= 3


def test_codigo_salida_preflight_bloqueado(monkeypatch):
    """El preflight bloqueado debe finalizar con código de salida distinto de cero (código 2)."""
    monkeypatch.delenv("ENV_NAME", raising=False)
    monkeypatch.delenv("TURSO_TEST_APPROVED", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == EXIT_CODE_PREFLIGHT_BLOCKED
    assert excinfo.value.code != 0


def test_orden_confirmacion_returning_y_aislamiento_rollback():
    """Valida que UPDATE ... RETURNING confirmado no sea revertido por el rollback de la prueba siguiente."""
    conn = sqlite3.connect(":memory:")
    tabla = "_f13_probe_test_returning"
    conn.execute(f"""
        CREATE TABLE {tabla} (
            id TEXT PRIMARY KEY,
            total INTEGER NOT NULL,
            efvo INTEGER NOT NULL
        );
    """)
    conn.commit()

    # Paso 1: Inserción inicial
    with transaccion(conn):
        conn.execute(f"INSERT INTO {tabla} VALUES ('T-RET-PROBE', 1000, 1000);")

    # Paso 2: UPDATE ... RETURNING explícitamente confirmado
    with transaccion(conn):
        cursor = conn.execute(f"UPDATE {tabla} SET total = 1200 WHERE id = 'T-RET-PROBE' RETURNING total;")
        ret_val = cursor.fetchone()[0]
        assert ret_val == 1200

    # Paso 3: Transacción subsiguiente con fallo inducido y rollback
    try:
        with transaccion(conn):
            conn.execute(f"INSERT INTO {tabla} VALUES ('T-FAIL-PROBE', 3000, 3000);")
            raise RuntimeError("Fallo forzado en bloque subsiguiente")
    except RuntimeError:
        pass

    # Aserción de aislamiento:
    # 1. El registro fallido no existe
    assert conn.execute(f"SELECT COUNT(*) FROM {tabla} WHERE id = 'T-FAIL-PROBE';").fetchone()[0] == 0
    # 2. El registro de RETURNING conservó su valor confirmado (1200) y no fue revertido accidentalmente
    assert conn.execute(f"SELECT total FROM {tabla} WHERE id = 'T-RET-PROBE';").fetchone()[0] == 1200
    conn.close()


def test_creacion_y_limpieza_nombres_sinteticos_sin_afectar_ajenos():
    """Valida que la limpieza solo elimine tablas que coincidan con el run_id y no toque objetos ajenos."""
    conn = sqlite3.connect(":memory:")
    run_id = "testrun1"
    tabla_propia = f"_f13_probe_{run_id}_tickets"
    tabla_ajena_1 = "clientes_produccion"
    tabla_ajena_2 = "_f13_probe_otrorun_tickets"

    conn.execute(f"CREATE TABLE {tabla_ajena_1} (id INT);")
    conn.execute(f"CREATE TABLE {tabla_ajena_2} (id INT);")
    conn.execute(f"CREATE TABLE {tabla_propia} (id INT);")
    conn.commit()

    # Simular la lógica de limpieza estricta por run_id
    objetos_creados_exitosamente = [tabla_propia, tabla_ajena_1] # tabla_ajena_1 no debe borrarse aunque esté en la lista
    for t in reversed(objetos_creados_exitosamente):
        if t.startswith(f"_f13_probe_{run_id}_"):
            conn.execute(f"DROP TABLE {t};")
            conn.commit()

    tablas_restantes = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]

    # La tabla propia del run fue eliminada
    assert tabla_propia not in tablas_restantes
    # Las tablas ajenas permanecen intactas
    assert tabla_ajena_1 in tablas_restantes
    assert tabla_ajena_2 in tablas_restantes
    conn.close()


def test_coordinacion_concurrente_trabajadores_con_barrera():
    """Valida la coordinación concurrente con hilos independientes y Barrier en base WAL local."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    init_conn = sqlite3.connect(db_path)
    init_conn.execute("PRAGMA journal_mode = WAL;")
    tabla_outbox = "_f13_probe_barrier_outbox"
    init_conn.execute(f"""
        CREATE TABLE {tabla_outbox} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aggregate_type TEXT NOT NULL,
            aggregate_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'PENDIENTE',
            intentos INTEGER NOT NULL DEFAULT 0,
            ultimo_error TEXT,
            creado_en_utc TEXT NOT NULL DEFAULT (datetime('now')),
            procesado_en_utc TEXT
        );
    """)
    for i in range(1, 9):
        init_conn.execute(
            f"INSERT INTO {tabla_outbox} (aggregate_type, aggregate_id, operation, payload_json, estado) "
            f"VALUES ('TICKET', 'T-{i}', 'INSERT', '{{}}', 'PENDIENTE');"
        )
    init_conn.commit()
    init_conn.close()

    barrier = threading.Barrier(2)
    lote1 = []
    lote2 = []

    def worker(dest):
        w_conn = sqlite3.connect(db_path, timeout=10)
        try:
            barrier.wait(timeout=5)
            reclamados = reclamar_lote_outbox(w_conn, limite=3, tabla_outbox=tabla_outbox)
            dest.extend([e["id"] for e in reclamados])
        finally:
            w_conn.close()

    t1 = threading.Thread(target=worker, args=(lote1,))
    t2 = threading.Thread(target=worker, args=(lote2,))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    # Ambos obtuvieron lotes
    assert len(lote1) == 3
    assert len(lote2) == 3
    # Los lotes no se solapan
    assert set(lote1).isdisjoint(set(lote2))

    if os.path.exists(db_path):
        os.remove(db_path)


def test_tabla_confirmada_se_elimina_en_limpieza():
    """Demuestra que una tabla creada y confirmada se incluye y elimina en la limpieza segura."""
    conn = sqlite3.connect(":memory:")
    run_id = "run123"
    tabla = f"_f13_probe_{run_id}_tickets"
    conn.execute(f"CREATE TABLE {tabla} (id INT);")
    conn.commit()

    resumen = ejecutar_limpieza_segura(
        conn=conn,
        run_id=run_id,
        objetos_creados_exitosamente=[tabla],
        objetos_inciertos=[],
    )

    assert tabla in resumen["tablas_eliminadas"]
    assert resumen["limpieza_completa"] is True
    assert resumen["estado_final"] == ESTADO_LIMPIEZA_COMPLETA

    tablas = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
    assert tabla not in tablas
    conn.close()


def test_objeto_incierto_nunca_recibe_drop_table():
    """Demuestra que un nombre registrado como incierto NUNCA recibe una orden DROP TABLE."""
    from unittest.mock import MagicMock
    mock_conn = MagicMock()
    run_id = "run_inc"
    tabla_incierta = f"_f13_probe_{run_id}_items"

    resumen = ejecutar_limpieza_segura(
        conn=mock_conn,
        run_id=run_id,
        objetos_creados_exitosamente=[],
        objetos_inciertos=[tabla_incierta],
    )

    # REGLA: Nunca se ejecuta execute("DROP TABLE ...") sobre el objeto incierto
    assert mock_conn.execute.call_count == 0, "No debe ejecutarse DROP TABLE sobre un objeto incierto!"
    assert resumen["limpieza_completa"] is False
    assert resumen["estado_final"] == ESTADO_LIMPIEZA_INCOMPLETA
    assert tabla_incierta in resumen["objetos_inciertos"]


def test_errores_de_limpieza_quedan_informados():
    """Demuestra que los fallos al eliminar tablas confirmadas quedan registrados e informados."""
    from unittest.mock import MagicMock
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = sqlite3.OperationalError("database is locked")
    run_id = "run_err"
    tabla_confirmada = f"_f13_probe_{run_id}_tickets"

    resumen = ejecutar_limpieza_segura(
        conn=mock_conn,
        run_id=run_id,
        objetos_creados_exitosamente=[tabla_confirmada],
        objetos_inciertos=[],
    )

    assert resumen["limpieza_completa"] is False
    assert tabla_confirmada in resumen["tablas_con_error_eliminacion"]
    assert "database is locked" in resumen["tablas_con_error_eliminacion"][tabla_confirmada]
    assert resumen["estado_final"] == ESTADO_LIMPIEZA_INCOMPLETA


def test_informe_no_declara_limpieza_completa_con_objetos_inciertos():
    """Demuestra que el informe no declara limpieza completa si permanecen objetos inciertos."""
    conn = sqlite3.connect(":memory:")
    run_id = "run_part"
    tabla_ok = f"_f13_probe_{run_id}_tickets"
    tabla_inc = f"_f13_probe_{run_id}_outbox"
    conn.execute(f"CREATE TABLE {tabla_ok} (id INT);")
    conn.commit()

    resumen = ejecutar_limpieza_segura(
        conn=conn,
        run_id=run_id,
        objetos_creados_exitosamente=[tabla_ok],
        objetos_inciertos=[tabla_inc],
    )

    # La tabla confirmada sí se eliminó
    assert tabla_ok in resumen["tablas_eliminadas"]
    # Pero el estado general es INCOMPLETO debido al objeto incierto
    assert resumen["limpieza_completa"] is False
    assert resumen["estado_final"] == ESTADO_LIMPIEZA_INCOMPLETA
    assert tabla_inc in resumen["objetos_inciertos"]
    conn.close()


def test_preflight_permite_exclusivamente_objeto_interno_tursodb():
    """Valida que la tabla interna exacta __turso_internal_mvcc_meta es la única excepción permitida."""
    conn = sqlite3.connect(":memory:")
    conn.execute(f"CREATE TABLE {OBJETO_INTERNO_PERMITIDO_NOMBRE} (key TEXT PRIMARY KEY, val TEXT);")
    conn.commit()

    es_seguro, estado, objetos = inspeccionar_estado_inicial_base(conn)
    assert es_seguro is True
    assert estado == ESTADO_PREFLIGHT_OK
    assert objetos == [f"{OBJETO_INTERNO_PERMITIDO_TIPO}:{OBJETO_INTERNO_PERMITIDO_NOMBRE}"]
    conn.close()


def test_preflight_bloquea_otros_objetos_incluso_con_prefijo_interno():
    """Valida que objetos con prefijo __turso_internal_ distintos a la tabla exacta son bloqueados."""
    conn = sqlite3.connect(":memory:")
    # Se crea la tabla permitida pero también otra con prefijo interno similar
    conn.execute(f"CREATE TABLE {OBJETO_INTERNO_PERMITIDO_NOMBRE} (key TEXT);")
    conn.execute("CREATE TABLE __turso_internal_extra_meta (key TEXT);")
    conn.commit()

    es_seguro, estado, objetos = inspeccionar_estado_inicial_base(conn)
    assert es_seguro is False
    assert estado == ESTADO_PREFLIGHT_BLOQUEADO
    assert "table:__turso_internal_extra_meta" in objetos
    conn.close()


def test_preflight_bloquea_tablas_vistas_indices_y_triggers_ajenos():
    """Valida que cualquier tabla, vista, índice o trigger no permitido bloquee el preflight."""
    # 1. Tabla de usuario común
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE tickets_anteriores (id INT);")
    es_seguro, estado, _ = inspeccionar_estado_inicial_base(conn)
    assert es_seguro is False
    assert estado == ESTADO_PREFLIGHT_BLOQUEADO
    conn.close()

    # 2. Vista de usuario
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIEW resumen_tickets AS SELECT 1;")
    es_seguro, estado, _ = inspeccionar_estado_inicial_base(conn)
    assert es_seguro is False
    assert estado == ESTADO_PREFLIGHT_BLOQUEADO
    conn.close()

    # 3. Índice inesperado sobre tabla interna
    conn = sqlite3.connect(":memory:")
    conn.execute(f"CREATE TABLE {OBJETO_INTERNO_PERMITIDO_NOMBRE} (key TEXT, val TEXT);")
    conn.execute(f"CREATE INDEX idx_custom_meta ON {OBJETO_INTERNO_PERMITIDO_NOMBRE} (val);")
    es_seguro, estado, objetos = inspeccionar_estado_inicial_base(conn)
    assert es_seguro is False
    assert estado == ESTADO_PREFLIGHT_BLOQUEADO
    assert "index:idx_custom_meta" in objetos
    conn.close()

    # 4. Trigger inesperado
    conn = sqlite3.connect(":memory:")
    conn.execute(f"CREATE TABLE {OBJETO_INTERNO_PERMITIDO_NOMBRE} (key TEXT, val TEXT);")
    conn.execute(f"""
        CREATE TRIGGER trg_test AFTER INSERT ON {OBJETO_INTERNO_PERMITIDO_NOMBRE}
        BEGIN
            SELECT 1;
        END;
    """)
    es_seguro, estado, objetos = inspeccionar_estado_inicial_base(conn)
    assert es_seguro is False
    assert estado == ESTADO_PREFLIGHT_BLOQUEADO
    assert "trigger:trg_test" in objetos
    conn.close()


def test_limpieza_no_requerida_si_no_se_crearon_objetos():
    """Demuestra que si la ejecución se detuvo antes de crear objetos, el estado es LIMPIEZA_NO_REQUERIDA."""
    from unittest.mock import MagicMock
    mock_conn = MagicMock()
    run_id = "run_noop"

    resumen = ejecutar_limpieza_segura(
        conn=mock_conn,
        run_id=run_id,
        objetos_creados_exitosamente=[],
        objetos_inciertos=[],
    )

    assert mock_conn.execute.call_count == 0
    assert resumen["tablas_eliminadas"] == []
    assert resumen["limpieza_completa"] is True
    assert resumen["estado_final"] == ESTADO_LIMPIEZA_NO_REQUERIDA


def test_tabla_interna_tursodb_nunca_recibe_drop_table():
    """Demuestra que la tabla interna de TursoDB nunca recibe DROP TABLE aunque se pase a la función."""
    from unittest.mock import MagicMock
    mock_conn = MagicMock()
    run_id = "run_safe"

    resumen = ejecutar_limpieza_segura(
        conn=mock_conn,
        run_id=run_id,
        objetos_creados_exitosamente=[OBJETO_INTERNO_PERMITIDO_NOMBRE],
        objetos_inciertos=[],
    )

    # Nunca se debe ejecutar DROP TABLE sobre la tabla interna
    assert mock_conn.execute.call_count == 0
    assert OBJETO_INTERNO_PERMITIDO_NOMBRE not in resumen["tablas_eliminadas"]
    assert resumen["limpieza_completa"] is False
    assert resumen["estado_final"] == ESTADO_LIMPIEZA_INCOMPLETA
