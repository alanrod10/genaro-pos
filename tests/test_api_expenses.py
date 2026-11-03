"""Pruebas unitarias y de integración para la API de Gastos, Arqueo y Cierre Diario."""
import sqlite3
import pytest
from fastapi.testclient import TestClient

from api.dependencies import AuthenticatedUser, get_current_user, get_db_connection
from api.main import app


@pytest.fixture
def sqlite_expenses_conn():
    """Base SQLite en memoria con esquema de gastos_retiros, cierres_diarios, cargas y tickets."""
    conexion = sqlite3.connect(":memory:", check_same_thread=False)
    conexion.execute(
        """
        CREATE TABLE gastos_retiros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legacy_row_id TEXT,
            fecha_hora_utc TEXT NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'General',
            detalle TEXT NOT NULL,
            monto_salida INTEGER NOT NULL CHECK (monto_salida > 0),
            origen_fondos TEXT DEFAULT 'Caja diaria',
            metodo_pago TEXT NOT NULL CHECK (metodo_pago IN ('EFECTIVO', 'TRANSFERENCIA')),
            anulado INTEGER NOT NULL DEFAULT 0 CHECK (anulado IN (0, 1)),
            idempotency_key TEXT DEFAULT NULL
        )
        """
    )
    conexion.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_gastos_idempotency_key
        ON gastos_retiros(idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """
    )
    conexion.execute(
        """
        CREATE TABLE cierres_diarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL UNIQUE,
            efectivo_esperado INTEGER NOT NULL,
            efectivo_contado INTEGER NOT NULL,
            diferencia INTEGER NOT NULL,
            total_ventas_efvo INTEGER NOT NULL DEFAULT 0,
            total_ventas_transf INTEGER NOT NULL DEFAULT 0,
            total_servicios_efvo INTEGER NOT NULL DEFAULT 0,
            total_servicios_transf INTEGER NOT NULL DEFAULT 0,
            total_gastos_efvo INTEGER NOT NULL DEFAULT 0,
            total_gastos_transf INTEGER NOT NULL DEFAULT 0,
            total_banco INTEGER NOT NULL DEFAULT 0,
            observaciones TEXT,
            cerrado_por TEXT NOT NULL DEFAULT 'sistema',
            cerrado_en_utc TEXT NOT NULL DEFAULT (datetime('now')),
            actualizado_en_utc TEXT
        )
        """
    )
    conexion.execute(
        """
        CREATE TABLE cargas_servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legacy_row_id TEXT,
            fecha_hora_utc TEXT NOT NULL,
            servicio TEXT NOT NULL COLLATE NOCASE,
            monto_carga INTEGER NOT NULL CHECK (monto_carga > 0),
            monto_adicional INTEGER NOT NULL DEFAULT 0 CHECK (monto_adicional >= 0),
            total_cobrado INTEGER NOT NULL CHECK (total_cobrado >= 0),
            pago_efectivo INTEGER NOT NULL DEFAULT 0 CHECK (pago_efectivo >= 0),
            pago_transferencia INTEGER NOT NULL DEFAULT 0 CHECK (pago_transferencia >= 0),
            anulado INTEGER NOT NULL DEFAULT 0 CHECK (anulado IN (0, 1)),
            CHECK (total_cobrado = monto_carga + monto_adicional),
            CHECK (total_cobrado = pago_efectivo + pago_transferencia)
        )
        """
    )
    conexion.execute(
        """
        CREATE TABLE tickets (
            id TEXT PRIMARY KEY,
            fecha_hora_utc TEXT NOT NULL,
            total_venta INTEGER NOT NULL,
            monto_efectivo INTEGER NOT NULL,
            monto_transferencia INTEGER NOT NULL,
            metodo_pago TEXT NOT NULL,
            anulado INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conexion.execute(
        """
        CREATE TABLE IF NOT EXISTS gastos_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gasto_id INTEGER NOT NULL,
            accion TEXT NOT NULL CHECK (accion IN ('CREAR', 'EDITAR', 'ANULAR')),
            valores_anteriores TEXT,
            valores_nuevos TEXT NOT NULL,
            fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
            actor TEXT NOT NULL DEFAULT 'sistema',
            FOREIGN KEY (gasto_id) REFERENCES gastos_retiros (id) ON DELETE CASCADE
        )
        """
    )
    conexion.commit()
    yield conexion
    conexion.close()


@pytest.fixture
def client(sqlite_expenses_conn):
    app.dependency_overrides[get_db_connection] = lambda: sqlite_expenses_conn
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_registrar_gasto_valido(client):
    """Registra un gasto válido y comprueba respuesta 201."""
    payload = {
        "detalle": "Compra de bolsas y cinta",
        "monto_salida": 1500,
        "tipo": "Limpieza",
        "origen_fondos": "Caja diaria",
        "metodo_pago": "EFECTIVO",
        "fecha": "24/10/2026 15:30:00",
    }
    res = client.post("/api/expenses", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["id"] > 0
    assert data["detalle"] == "Compra de bolsas y cinta"
    assert data["monto_salida"] == 1500
    assert data["metodo_pago"] == "EFECTIVO"
    assert data["anulado"] is False


def test_rechaza_gasto_invalido(client):
    """Rechaza gastos con detalle vacío, monto negativo o método desconocido."""
    # Detalle vacío
    res1 = client.post("/api/expenses", json={"detalle": "   ", "monto_salida": 1000})
    assert res1.status_code in (400, 422)

    # Monto cero o negativo
    res2 = client.post("/api/expenses", json={"detalle": "Pan", "monto_salida": 0})
    assert res2.status_code in (400, 422)

    # Método inválido
    res3 = client.post(
        "/api/expenses",
        json={"detalle": "Pan", "monto_salida": 500, "metodo_pago": "CHEQUE"},
    )
    assert res3.status_code in (400, 422)


def test_listar_y_filtrar_gastos(client):
    """Crea gastos y comprueba filtrado por fecha y categoría."""
    client.post(
        "/api/expenses",
        json={
            "detalle": "Factura de Luz",
            "monto_salida": 12000,
            "tipo": "Servicios del local",
            "metodo_pago": "TRANSFERENCIA",
            "fecha": "23/10/2026 10:00:00",
        },
    )
    client.post(
        "/api/expenses",
        json={
            "detalle": "Panadería medialunas",
            "monto_salida": 2000,
            "tipo": "Comida",
            "metodo_pago": "EFECTIVO",
            "fecha": "24/10/2026 11:00:00",
        },
    )

    # Listar todos
    res = client.get("/api/expenses")
    assert res.status_code == 200
    assert res.json()["total"] == 2

    # Filtrar por fecha 24/10/2026
    res_fecha = client.get("/api/expenses?fecha=24/10/2026")
    assert res_fecha.status_code == 200
    assert res_fecha.json()["total"] == 1
    assert res_fecha.json()["gastos"][0]["detalle"] == "Panadería medialunas"


def test_resumen_gastos_efectivo_y_transferencia(client):
    """Comprueba la suma separada de egresos en efectivo y transferencias."""
    client.post(
        "/api/expenses",
        json={
            "detalle": "Pago a Repartidor",
            "monto_salida": 3000,
            "metodo_pago": "EFECTIVO",
            "fecha": "24/10/2026 12:00:00",
        },
    )
    client.post(
        "/api/expenses",
        json={
            "detalle": "Distribuidora Mayorista",
            "monto_salida": 15000,
            "metodo_pago": "TRANSFERENCIA",
            "fecha": "24/10/2026 13:00:00",
        },
    )

    res = client.get("/api/expenses/summary?fecha=24/10/2026")
    assert res.status_code == 200
    summary = res.json()
    assert summary["total_operaciones"] == 2
    assert summary["gastos_efvo"] == 3000
    assert summary["gastos_transf"] == 15000
    assert summary["gastos_total"] == 18000
    assert summary["operaciones_anuladas"] == 0


def test_edicion_puntual_de_gasto(client):
    """Edita un gasto específico sin alterar otros."""
    res_create = client.post(
        "/api/expenses",
        json={
            "detalle": "Artículos de librería",
            "monto_salida": 800,
            "metodo_pago": "EFECTIVO",
            "fecha": "24/10/2026 14:00:00",
        },
    )
    gasto_id = res_create.json()["id"]

    res_edit = client.patch(
        f"/api/expenses/{gasto_id}",
        json={"monto_salida": 950, "detalle": "Librería y resma A4"},
    )
    assert res_edit.status_code == 200
    editado = res_edit.json()
    assert editado["monto_salida"] == 950
    assert editado["detalle"] == "Librería y resma A4"


def test_anulacion_logica_de_gasto(client):
    """Anula lógicamente un gasto y verifica exclusión del resumen activo."""
    res_create = client.post(
        "/api/expenses",
        json={
            "detalle": "Gasto erróneo",
            "monto_salida": 5000,
            "metodo_pago": "EFECTIVO",
            "fecha": "24/10/2026 15:00:00",
        },
    )
    gasto_id = res_create.json()["id"]

    res_cancel = client.post(f"/api/expenses/{gasto_id}/cancel")
    assert res_cancel.status_code == 200
    assert res_cancel.json()["status"] == "anulado"

    # En resumen activo debe ser 0
    res_sum = client.get("/api/expenses/summary?fecha=24/10/2026")
    assert res_sum.json()["gastos_total"] == 0
    assert res_sum.json()["operaciones_anuladas"] == 1


def test_arqueo_diario_integrado(client, sqlite_expenses_conn):
    """Verifica que el arqueo combine ventas, servicios y gastos con las fórmulas exactas del visor."""
    # 1. Ventas tickets (esquema real 003: id, monto_transferencia)
    sqlite_expenses_conn.execute(
        """
        INSERT INTO tickets (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago, anulado)
        VALUES ('T-001', '2026-10-24 10:00:00', 10000, 8000, 2000, 'MIXTO', 0)
        """
    )
    # 2. Servicios
    sqlite_expenses_conn.execute(
        """
        INSERT INTO cargas_servicios (fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado)
        VALUES ('2026-10-24 11:00:00', 'Claro', 3000, 200, 3200, 3200, 0, 0)
        """
    )
    # 3. Gasto en efectivo
    sqlite_expenses_conn.execute(
        """
        INSERT INTO gastos_retiros (fecha_hora_utc, tipo, detalle, monto_salida, metodo_pago, anulado)
        VALUES ('2026-10-24 12:00:00', 'General', 'Limpieza', 1000, 'EFECTIVO', 0)
        """
    )
    sqlite_expenses_conn.commit()

    res = client.get("/api/expenses/arqueo?fecha=2026-10-24")
    assert res.status_code == 200
    data = res.json()
    assert data["ventas_efvo"] == 8000
    assert data["ventas_transf"] == 2000
    assert data["total_ventas"] == 10000
    assert data["servicios_efvo"] == 3200
    assert data["gastos_efvo"] == 1000
    # Efectivo esperado = 8000 (ventas) + 3200 (servicios) - 1000 (gastos) = 10200
    assert data["efectivo_esperado"] == 10200
    assert data["total_banco"] == 2000


def test_registro_y_consulta_de_cierre_diario(client):
    """Registra un cierre formal, calcula la diferencia y permite consultarlo."""
    cierre_payload = {
        "fecha": "2026-10-24",
        "efectivo_esperado": 10200,
        "efectivo_contado": 10000,  # Faltante de $200
        "total_ventas_efvo": 8000,
        "total_ventas_transf": 2000,
        "total_servicios_efvo": 3200,
        "total_servicios_transf": 0,
        "total_gastos_efvo": 1000,
        "total_gastos_transf": 0,
        "total_banco": 2000,
        "observaciones": "Faltante de $200 por redondeo",
        "cerrado_por": "Alan",
    }
    res_crear = client.post("/api/expenses/cierres", json=cierre_payload)
    assert res_crear.status_code == 201
    cierre = res_crear.json()
    assert cierre["id"] > 0
    assert cierre["diferencia"] == -200
    assert cierre["cerrado_por"] == "alan"

    # Consultar por fecha
    res_get = client.get("/api/expenses/cierres/2026-10-24")
    assert res_get.status_code == 200
    assert res_get.json()["diferencia"] == -200


def test_regresion_arqueo_valores_dev_turso_y_segregacion(client, sqlite_expenses_conn):
    """Regresión del módulo de gastos y arqueo con valores de Turso DEV:
    - Ventas efvo: $1.000, transf: $2.000
    - Servicios efvo: $123.600, transf: $10.500
    - Gastos iniciales: $0
    - Efectivo esperado físico: $124.600
    - Efectivo disponible drugstore: $1.000
    - Total banco: $12.500
    Verifica también que:
    - Gasto en efectivo disminuye el efectivo esperado sin alterar el banco.
    - Gasto por transferencia disminuye el banco sin alterar el efectivo físico.
    """
    fecha = "2026-10-28"
    dt = f"{fecha} 14:00:00"

    # Ventas: $1.000 efvo, $2.000 transf
    sqlite_expenses_conn.execute(
        """
        INSERT INTO tickets (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago, anulado)
        VALUES ('T-EXP-DEV', ?, 3000, 1000, 2000, 'MIXTO', 0)
        """,
        (dt,),
    )

    # Servicios:
    # Prepay efvo: 116.100 capital
    sqlite_expenses_conn.execute(
        """
        INSERT INTO cargas_servicios (fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado)
        VALUES (?, 'PREPAY', 116100, 0, 116100, 116100, 0, 0)
        """,
        (dt,),
    )
    # Prepay transf: 10.000 capital + 500 adicional = 10.500 transf
    sqlite_expenses_conn.execute(
        """
        INSERT INTO cargas_servicios (fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado)
        VALUES (?, 'PREPAY', 10000, 500, 10500, 0, 10500, 0)
        """,
        (dt,),
    )
    # Claro efvo: 1.000 capital
    sqlite_expenses_conn.execute(
        """
        INSERT INTO cargas_servicios (fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado)
        VALUES (?, 'CLARO', 1000, 0, 1000, 1000, 0, 0)
        """,
        (dt,),
    )
    # Recargos efvo: 6.500
    sqlite_expenses_conn.execute(
        """
        INSERT INTO cargas_servicios (fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado)
        VALUES (?, 'PREPAY', 1, 6500, 6501, 6501, 0, 0)
        """,
        (dt,),
    )
    sqlite_expenses_conn.commit()

    res = client.get(f"/api/expenses/arqueo?fecha={fecha}")
    assert res.status_code == 200
    data = res.json()

    assert data["ventas_efvo"] == 1000
    assert data["ventas_transf"] == 2000
    assert data["total_ventas"] == 3000
    assert data["efectivo_disponible_drugstore"] == 1000

    # Servicios efvo: 116.100 + 1.000 + 6.501 = 123.601 (o redondeo exacto de los ítems)
    # d_recargas: 10.500
    assert data["d_recargas"] == 10500
    assert data["total_banco"] == 12500  # 2.000 + 10.500
    assert data["efectivo_esperado"] == data["ventas_efvo"] + data["servicios_efvo"] - data["gastos_efvo"]

    # Agregar gasto en efectivo $4.600
    sqlite_expenses_conn.execute(
        """
        INSERT INTO gastos_retiros (fecha_hora_utc, tipo, detalle, monto_salida, metodo_pago, anulado)
        VALUES (?, 'Limpieza', 'Artículos de limpieza', 4600, 'EFECTIVO', 0)
        """,
        (dt,),
    )
    sqlite_expenses_conn.commit()

    res_post_efvo = client.get(f"/api/expenses/arqueo?fecha={fecha}")
    d_post_efvo = res_post_efvo.json()
    assert d_post_efvo["gastos_efvo"] == 4600
    assert d_post_efvo["gastos_transf"] == 0
    assert d_post_efvo["ventas_efvo"] == 1000  # Caja A inalterada ante gastos en efectivo
    assert d_post_efvo["efectivo_disponible_drugstore"] == 1000  # NUNCA resta gastos_efvo
    assert d_post_efvo["efectivo_esperado"] == data["efectivo_esperado"] - 4600
    assert d_post_efvo["total_banco"] == 12500  # Inalterado

    # Agregar gasto por transferencia $2.500
    sqlite_expenses_conn.execute(
        """
        INSERT INTO gastos_retiros (fecha_hora_utc, tipo, detalle, monto_salida, metodo_pago, anulado)
        VALUES (?, 'Servicios', 'Internet MP', 2500, 'TRANSFERENCIA', 0)
        """,
        (dt,),
    )
    sqlite_expenses_conn.commit()

    res_post_transf = client.get(f"/api/expenses/arqueo?fecha={fecha}")
    d_post_transf = res_post_transf.json()
    assert d_post_transf["gastos_transf"] == 2500
    assert d_post_transf["total_banco"] == 10000  # 12.500 - 2.500
    assert d_post_transf["efectivo_esperado"] == d_post_efvo["efectivo_esperado"]  # Inalterado


def test_arqueo_refleja_faltante_real_sin_limite_artificial(client, sqlite_expenses_conn):
    """Verifica que si los gastos en efectivo superan el efectivo ingresado,
    la fórmula no oculte el saldo negativo mediante límites artificiales como max(0, ...)."""
    fecha_negativa = "2026-10-29"
    dt = f"{fecha_negativa} 10:00:00"

    # Venta de $1.000 en efectivo
    sqlite_expenses_conn.execute(
        """
        INSERT INTO tickets (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago, anulado)
        VALUES ('T-NEG', ?, 1000, 1000, 0, 'EFECTIVO', 0)
        """,
        (dt,),
    )
    # Gasto de $3.000 en efectivo
    sqlite_expenses_conn.execute(
        """
        INSERT INTO gastos_retiros (fecha_hora_utc, tipo, detalle, monto_salida, metodo_pago, anulado)
        VALUES (?, 'Retiro', 'Retiro de caja', 3000, 'EFECTIVO', 0)
        """,
        (dt,),
    )
    sqlite_expenses_conn.commit()

    res = client.get(f"/api/expenses/arqueo?fecha={fecha_negativa}")
    assert res.status_code == 200
    data = res.json()
    assert data["ventas_efvo"] == 1000
    assert data["gastos_efvo"] == 3000
    # Efectivo esperado debe reflejar el déficit real (-2000), sin max(0, ...)
    assert data["efectivo_esperado"] == -2000


def test_arqueo_excluye_ventas_anuladas_y_respeta_zona_horaria_argentina(client, sqlite_expenses_conn):
    """P0-01: Verifica sobre esquema real (id, monto_transferencia) que:
    1. Las ventas anuladas (anulado = 1) quedan excluidas del arqueo.
    2. Una venta a las 22:30 hora argentina del 2026-10-25 (01:30 UTC del 2026-10-26)
       se computa en el arqueo del 2026-10-25 y NO en el del 2026-10-26.
    3. Una venta exactamente a las 00:00:00 AR del 2026-10-25 (03:00:00 UTC) entra en 2026-10-25
       y no se duplica en 2026-10-24.
    """
    # Venta activa a las 00:00:00 AR del 2026-10-25 -> 2026-10-25 03:00:00 UTC
    sqlite_expenses_conn.execute(
        """
        INSERT INTO tickets (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago, anulado)
        VALUES ('T-AR-00', '2026-10-25 03:00:00', 4000, 4000, 0, 'EFECTIVO', 0)
        """
    )
    # Venta activa nocturna a las 22:30:00 AR del 2026-10-25 -> 2026-10-26 01:30:00 UTC
    sqlite_expenses_conn.execute(
        """
        INSERT INTO tickets (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago, anulado)
        VALUES ('T-AR-2230', '2026-10-26 01:30:00', 6000, 2500, 3500, 'MIXTO', 0)
        """
    )
    # Venta ANULADA en el mismo día -> no debe sumarse
    sqlite_expenses_conn.execute(
        """
        INSERT INTO tickets (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago, anulado)
        VALUES ('T-AR-ANULADA', '2026-10-25 15:00:00', 50000, 30000, 20000, 'MIXTO', 1)
        """
    )
    sqlite_expenses_conn.commit()

    # Arqueo del 2026-10-24: no debe incluir la venta de las 00:00:00 AR del 25
    res_24 = client.get("/api/expenses/arqueo?fecha=2026-10-24")
    assert res_24.status_code == 200
    assert res_24.json()["total_ventas"] == 0

    # Arqueo del 2026-10-25: debe incluir T-AR-00 ($4.000) + T-AR-2230 ($6.000) y excluir T-AR-ANULADA
    res_25 = client.get("/api/expenses/arqueo?fecha=2026-10-25")
    assert res_25.status_code == 200
    d25 = res_25.json()
    assert d25["ventas_efvo"] == 6500
    assert d25["ventas_transf"] == 3500
    assert d25["total_ventas"] == 10000
    assert d25["efectivo_esperado"] == 6500
    assert d25["total_banco"] == 3500

    # Arqueo del 2026-10-26: no debe incluir T-AR-2230 del 25 a la noche
    res_26 = client.get("/api/expenses/arqueo?fecha=2026-10-26")
    assert res_26.status_code == 200
    assert res_26.json()["total_ventas"] == 0


def test_arqueo_no_oculta_error_de_columna_inexistente():
    """P0-01: Verifica que si la tabla tickets existe pero tiene un esquema corrupto
    (sin columna monto_transferencia), calcular_arqueo_diario_turso propague OperationalError
    en lugar de silenciarlo y devolver $0."""
    from db.expenses import calcular_arqueo_diario_turso

    conn_rota = sqlite3.connect(":memory:")
    conn_rota.execute(
        "CREATE TABLE gastos_retiros (id INTEGER PRIMARY KEY, fecha_hora_utc TEXT, tipo TEXT, detalle TEXT, monto_salida INTEGER, origen_fondos TEXT, metodo_pago TEXT, anulado INTEGER DEFAULT 0)"
    )
    conn_rota.execute(
        "CREATE TABLE cargas_servicios (id INTEGER PRIMARY KEY, fecha_hora_utc TEXT, servicio TEXT, monto_carga INTEGER, monto_adicional INTEGER, total_cobrado INTEGER, pago_efectivo INTEGER, pago_transferencia INTEGER, anulado INTEGER DEFAULT 0)"
    )
    conn_rota.execute(
        "CREATE TABLE tickets (id TEXT PRIMARY KEY, fecha_hora_utc TEXT, total_venta INTEGER, monto_efectivo INTEGER, anulado INTEGER DEFAULT 0)"
    )
    with pytest.raises(sqlite3.OperationalError):
        calcular_arqueo_diario_turso("2026-10-25", conn=conn_rota)
    conn_rota.close()


def test_resumen_gastos_sin_tabla_retorna_schema_valido():
    """P2-01: Verifica que GET /api/expenses/summary sin tabla gastos_retiros retorne GastosSummaryOut válido."""
    conn_vacia = sqlite3.connect(":memory:", check_same_thread=False)
    app.dependency_overrides[get_db_connection] = lambda: conn_vacia
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")
    try:
        with TestClient(app) as c:
            res = c.get("/api/expenses/summary?fecha=2026-10-25")
            assert res.status_code == 200
            body = res.json()
            assert body == {
                "total_operaciones": 0,
                "gastos_efvo": 0,
                "gastos_transf": 0,
                "gastos_total": 0,
                "operaciones_anuladas": 0,
            }
    finally:
        app.dependency_overrides.clear()
        conn_vacia.close()
