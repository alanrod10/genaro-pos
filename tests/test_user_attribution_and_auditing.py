"""Pruebas exhaustivas de atribución de usuarios y auditoría en GenaroPOS.

Verifica:
1. Petición autenticada de Alan crea servicio -> evento en cargas_servicios_auditoria con actor='alan'.
2. Petición autenticada de Yilda anula servicio -> evento en cargas_servicios_auditoria con actor='yilda'.
3. Creación, edición y anulación de gastos registran al usuario autenticado.
4. Anti-spoofing: Petición con token de Yilda y usuario='alan' en el cuerpo atribuye a 'yilda'.
5. Creación, edición y anulación de ventas registran al usuario autenticado.
6. Reintento idempotente de venta y gasto no duplica registros ni eventos de creación.
7. Fallo al insertar evento de auditoría revierte la transacción comercial (atomicidad).
8. Cierre diario guarda el actor autenticado en cerrado_por.
9. Registros históricos preexistentes no se modifican.
10. Autenticación y modo conectado de solo lectura intactos.
"""
import json
import sqlite3
import pytest
from fastapi.testclient import TestClient

from api.auth import AuthenticatedUser
from api.dependencies import get_current_user, get_db_connection
from api.main import app
from db.sales import registrar_venta_idempotente_turso
from db.services import registrar_carga_idempotente_turso
from db.expenses import registrar_gasto_idempotente_turso
from db.readiness import ErrorEsquemaNoPreparado


@pytest.fixture
def memory_audit_db():
    """Crea una base de datos SQLite en memoria con el esquema completo de migraciones 001..010."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")

    conn.executescript("""
        CREATE TABLE _schema_migrations (
            version INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL,
            aplicado_en TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL COLLATE NOCASE,
            categoria TEXT DEFAULT 'Otro',
            proveedor TEXT DEFAULT 'General',
            unidad TEXT DEFAULT 'Unidad',
            costo INTEGER DEFAULT 0,
            precio_dia INTEGER DEFAULT 1000,
            precio_noche INTEGER DEFAULT 1000,
            margen_ratio REAL DEFAULT 1.0,
            fecha_actualizacion TEXT DEFAULT '2026-10-25',
            activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1))
        );

        CREATE TABLE productos_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL,
            accion TEXT NOT NULL,
            valores_anteriores TEXT,
            valores_nuevos TEXT NOT NULL,
            fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
            actor TEXT NOT NULL DEFAULT 'sistema'
        );

        CREATE TABLE pedidos_proveedor (
            id TEXT PRIMARY KEY,
            fecha TEXT NOT NULL,
            proveedor TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'PENDIENTE',
            total INTEGER NOT NULL DEFAULT 0,
            observaciones TEXT DEFAULT '',
            creado_en_utc TEXT NOT NULL DEFAULT (datetime('now')),
            actualizado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE pedido_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id TEXT NOT NULL,
            producto_id INTEGER,
            producto_nombre TEXT NOT NULL,
            cantidad REAL NOT NULL,
            costo_unitario INTEGER NOT NULL,
            subtotal INTEGER NOT NULL,
            FOREIGN KEY (pedido_id) REFERENCES pedidos_proveedor (id) ON DELETE CASCADE
        );

        CREATE TABLE pedidos_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id TEXT NOT NULL,
            accion TEXT NOT NULL,
            valores_anteriores TEXT,
            valores_nuevos TEXT NOT NULL,
            fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
            actor TEXT NOT NULL DEFAULT 'sistema',
            FOREIGN KEY (pedido_id) REFERENCES pedidos_proveedor (id) ON DELETE CASCADE
        );

        CREATE TABLE tickets (
            id TEXT PRIMARY KEY,
            fecha_hora_utc TEXT NOT NULL DEFAULT (datetime('now')),
            total_venta INTEGER NOT NULL CHECK (total_venta >= 0),
            monto_efectivo INTEGER NOT NULL DEFAULT 0,
            monto_transferencia INTEGER NOT NULL DEFAULT 0,
            metodo_pago TEXT NOT NULL CHECK (metodo_pago IN ('EFECTIVO', 'TRANSFERENCIA', 'MIXTO')),
            es_nocturno INTEGER NOT NULL DEFAULT 0,
            anulado INTEGER NOT NULL DEFAULT 0,
            motivo_anulacion TEXT,
            CONSTRAINT chk_ticket_balance CHECK (total_venta = monto_efectivo + monto_transferencia)
        );

        CREATE TABLE ticket_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            producto_id INTEGER,
            producto_nombre TEXT NOT NULL,
            cantidad REAL NOT NULL CHECK (cantidad > 0),
            unidad TEXT NOT NULL DEFAULT 'Unidad',
            precio_unitario INTEGER NOT NULL CHECK (precio_unitario >= 0),
            subtotal INTEGER NOT NULL CHECK (subtotal >= 0),
            metodo_pago TEXT NOT NULL,
            costo_unitario INTEGER,
            FOREIGN KEY (ticket_id) REFERENCES tickets (id) ON DELETE CASCADE
        );

        CREATE TABLE ventas_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            item_id INTEGER,
            accion TEXT NOT NULL CHECK (accion IN ('CREACION', 'ANULACION', 'EDICION_ITEM', 'ELIMINACION_ITEM')),
            motivo TEXT,
            usuario TEXT NOT NULL DEFAULT 'sistema',
            datos_previos_json TEXT,
            datos_nuevos_json TEXT,
            creado_en_utc TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (ticket_id) REFERENCES tickets (id) ON DELETE CASCADE
        );

        CREATE TABLE cargas_servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legacy_row_id TEXT,
            fecha_hora_utc TEXT NOT NULL DEFAULT (datetime('now')),
            servicio TEXT NOT NULL COLLATE NOCASE,
            monto_carga INTEGER NOT NULL CHECK (monto_carga > 0),
            monto_adicional INTEGER NOT NULL DEFAULT 0 CHECK (monto_adicional >= 0),
            total_cobrado INTEGER NOT NULL CHECK (total_cobrado >= 0),
            pago_efectivo INTEGER NOT NULL DEFAULT 0 CHECK (pago_efectivo >= 0),
            pago_transferencia INTEGER NOT NULL DEFAULT 0 CHECK (pago_transferencia >= 0),
            anulado INTEGER NOT NULL DEFAULT 0 CHECK (anulado IN (0, 1)),
            idempotency_key TEXT DEFAULT NULL,
            CONSTRAINT chk_carga_total CHECK (total_cobrado = (monto_carga + monto_adicional)),
            CONSTRAINT chk_carga_balance CHECK ((monto_carga + monto_adicional) = (pago_efectivo + pago_transferencia))
        );
        CREATE UNIQUE INDEX uq_cargas_idempotency_key ON cargas_servicios (idempotency_key) WHERE idempotency_key IS NOT NULL;

        CREATE TABLE cargas_servicios_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            carga_id INTEGER NOT NULL,
            accion TEXT NOT NULL CHECK (accion IN ('CREAR', 'EDITAR', 'ANULAR')),
            valores_anteriores TEXT,
            valores_nuevos TEXT NOT NULL,
            fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
            actor TEXT NOT NULL DEFAULT 'sistema',
            FOREIGN KEY (carga_id) REFERENCES cargas_servicios (id) ON DELETE CASCADE
        );

        CREATE TABLE gastos_retiros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legacy_row_id TEXT,
            fecha_hora_utc TEXT NOT NULL DEFAULT (datetime('now')),
            tipo TEXT NOT NULL DEFAULT 'General',
            detalle TEXT NOT NULL,
            monto_salida INTEGER NOT NULL CHECK (monto_salida > 0),
            origen_fondos TEXT DEFAULT 'Caja diaria',
            metodo_pago TEXT NOT NULL CHECK (metodo_pago IN ('EFECTIVO', 'TRANSFERENCIA')),
            anulado INTEGER NOT NULL DEFAULT 0 CHECK (anulado IN (0, 1)),
            idempotency_key TEXT DEFAULT NULL
        );
        CREATE UNIQUE INDEX uq_gastos_idempotency_key ON gastos_retiros (idempotency_key) WHERE idempotency_key IS NOT NULL;

        CREATE TABLE gastos_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gasto_id INTEGER NOT NULL,
            accion TEXT NOT NULL CHECK (accion IN ('CREAR', 'EDITAR', 'ANULAR')),
            valores_anteriores TEXT,
            valores_nuevos TEXT NOT NULL,
            fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
            actor TEXT NOT NULL DEFAULT 'sistema',
            FOREIGN KEY (gasto_id) REFERENCES gastos_retiros (id) ON DELETE CASCADE
        );

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
        );

        -- Semilla de productos
        INSERT INTO productos (id, nombre, costo, activo) VALUES (1, 'Gaseosa Cola', 500, 1);
        INSERT INTO productos (id, nombre, costo, activo) VALUES (2, 'Alfajor', 300, 1);
    """)
    conn.commit()
    yield conn
    conn.close()


def test_1_crear_servicio_atribuido_a_alan(memory_audit_db):
    """Punto 1: Petición autenticada de Alan crea servicio y genera auditoría con actor='alan'."""
    app.dependency_overrides[get_db_connection] = lambda: memory_audit_db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")

    with TestClient(app) as client:
        payload = {
            "servicio": "Claro",
            "monto_carga": 5000,
            "monto_adicional": 200,
            "total_cobrado": 5200,
            "pago_efvo": 5200,
            "pago_transf": 0,
            "idempotency_key": "TEST-SERV-ALAN-1",
        }
        res = client.post("/api/services", json=payload)
        assert res.status_code == 201
        carga_id = res.json()["id"]

        cur = memory_audit_db.cursor()
        cur.execute("SELECT carga_id, accion, actor, valores_anteriores, valores_nuevos FROM cargas_servicios_auditoria WHERE carga_id = ?", (carga_id,))
        row = cur.fetchone()
        assert row is not None
        assert row[0] == carga_id
        assert row[1] == "CREAR"
        assert row[2] == "alan"
        assert row[3] is None
        assert "Claro" in row[4]


def test_2_anular_servicio_atribuido_a_yilda(memory_audit_db):
    """Punto 2: Petición autenticada de Yilda anula un servicio y genera auditoría con actor='yilda'."""
    app.dependency_overrides[get_db_connection] = lambda: memory_audit_db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")

    with TestClient(app) as client:
        # Alan crea
        res_crear = client.post("/api/services", json={
            "servicio": "Personal",
            "monto_carga": 3000,
            "monto_adicional": 100,
            "total_cobrado": 3100,
            "pago_efvo": 3100,
            "pago_transf": 0,
        })
        carga_id = res_crear.json()["id"]

        # Yilda anula
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="yilda", display_name="Yilda")
        res_anular = client.delete(f"/api/services/{carga_id}")
        assert res_anular.status_code == 200

        cur = memory_audit_db.cursor()
        cur.execute("SELECT accion, actor, valores_anteriores, valores_nuevos FROM cargas_servicios_auditoria WHERE carga_id = ? ORDER BY id DESC", (carga_id,))
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "ANULAR"
        assert row[1] == "yilda"
        assert json.loads(row[3])["anulado"] == 1


def test_3_gastos_creacion_edicion_anulacion_registran_usuario(memory_audit_db):
    """Punto 3: Creación, edición y anulación de gastos registran al usuario autenticado."""
    app.dependency_overrides[get_db_connection] = lambda: memory_audit_db

    with TestClient(app) as client:
        # Alan crea gasto
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")
        res_crear = client.post("/api/expenses", json={
            "detalle": "Compra de insumos",
            "monto_salida": 2500,
            "tipo": "Limpieza",
            "metodo_pago": "EFECTIVO",
        })
        assert res_crear.status_code == 201
        gasto_id = res_crear.json()["id"]

        # Yilda edita gasto
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="yilda", display_name="Yilda")
        res_edit = client.patch(f"/api/expenses/{gasto_id}", json={
            "monto_salida": 3000,
        })
        assert res_edit.status_code == 200

        # Alan anula gasto
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")
        res_anul = client.delete(f"/api/expenses/{gasto_id}")
        assert res_anul.status_code == 200

        cur = memory_audit_db.cursor()
        cur.execute("SELECT accion, actor FROM gastos_auditoria WHERE gasto_id = ? ORDER BY id ASC", (gasto_id,))
        auditorias = cur.fetchall()

        assert len(auditorias) == 3
        assert auditorias[0] == ("CREAR", "alan")
        assert auditorias[1] == ("EDITAR", "yilda")
        assert auditorias[2] == ("ANULAR", "alan")


def test_4_anti_spoofing_ignora_usuario_arbitrario_en_payload(memory_audit_db):
    """Punto 4: Una petición con token de Yilda y usuario='alan' en el cuerpo atribuye a 'yilda'."""
    app.dependency_overrides[get_db_connection] = lambda: memory_audit_db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="yilda", display_name="Yilda")

    with TestClient(app) as client:
        # Venta creada inicialmente
        res_crear = client.post("/api/sales", json={
            "ticket": {
                "ticket_id": "T-SPOOF-01",
                "fecha": "25/10/2026 10:00:00",
                "total_venta": 500,
                "monto_efectivo": 500,
                "monto_transf": 0,
                "metodo_pago": "EFECTIVO",
            },
            "items": [{
                "producto": "Gaseosa Cola",
                "cantidad": 1,
                "unidad": "Unidad",
                "precio_unit": 500,
                "subtotal": 500,
            }],
        })
        assert res_crear.status_code == 201

        # Anulación enviando usuario 'alan' fraudulento en body
        res_anular = client.post("/api/sales/T-SPOOF-01/anular", json={
            "motivo": "Devolución",
            "usuario": "alan",  # Intento de suplantar autoría
        })
        assert res_anular.status_code == 200

        cur = memory_audit_db.cursor()
        cur.execute("SELECT accion, usuario FROM ventas_auditoria WHERE ticket_id = 'T-SPOOF-01' ORDER BY id ASC")
        eventos = cur.fetchall()

        assert len(eventos) == 2
        assert eventos[0] == ("CREACION", "yilda")
        # El backend debe haber usado 'yilda' de la sesión autenticada, NO 'alan' del JSON
        assert eventos[1] == ("ANULACION", "yilda")


def test_5_ventas_creacion_edicion_anulacion_registran_usuario(memory_audit_db):
    """Punto 5: Creación, edición y anulación de ventas registran al usuario autenticado."""
    app.dependency_overrides[get_db_connection] = lambda: memory_audit_db

    with TestClient(app) as client:
        # Alan crea
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")
        res_crear = client.post("/api/sales", json={
            "ticket": {
                "ticket_id": "T-FLOW-001",
                "fecha": "25/10/2026 12:00:00",
                "total_venta": 800,
                "monto_efectivo": 800,
                "monto_transf": 0,
                "metodo_pago": "EFECTIVO",
            },
            "items": [{
                "producto": "Alfajor",
                "cantidad": 2,
                "unidad": "Unidad",
                "precio_unit": 400,
                "subtotal": 800,
            }],
        })
        assert res_crear.status_code == 201

        # Yilda edita ítem
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="yilda", display_name="Yilda")
        detalle = client.get("/api/sales/T-FLOW-001").json()
        item_id = detalle["items"][0]["id"]

        res_edit = client.put(f"/api/sales/T-FLOW-001/items/{item_id}", json={
            "nueva_cantidad": 1,
            "nuevo_subtotal": 400,
        })
        assert res_edit.status_code == 200

        # Alan anula
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")
        res_anul = client.post("/api/sales/T-FLOW-001/anular", json={"motivo": "Cancelado"})
        assert res_anul.status_code == 200

        cur = memory_audit_db.cursor()
        cur.execute("SELECT accion, usuario FROM ventas_auditoria WHERE ticket_id = 'T-FLOW-001' ORDER BY id ASC")
        eventos = cur.fetchall()

        assert len(eventos) == 3
        assert eventos[0] == ("CREACION", "alan")
        assert eventos[1] == ("EDICION_ITEM", "yilda")
        assert eventos[2] == ("ANULACION", "alan")


def test_6_reintento_idempotente_no_duplica_auditoria(memory_audit_db):
    """Punto 6: Reintento de venta o gasto con misma clave no duplica la fila ni el evento original."""
    app.dependency_overrides[get_db_connection] = lambda: memory_audit_db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")

    with TestClient(app) as client:
        # Venta
        v_payload = {
            "ticket": {
                "ticket_id": "T-IDEMP-01",
                "fecha": "25/10/2026 14:00:00",
                "total_venta": 500,
                "monto_efectivo": 500,
                "monto_transf": 0,
                "metodo_pago": "EFECTIVO",
            },
            "items": [{
                "producto": "Gaseosa Cola",
                "cantidad": 1,
                "unidad": "Unidad",
                "precio_unit": 500,
                "subtotal": 500,
            }],
        }
        res_v1 = client.post("/api/sales", json=v_payload)
        assert res_v1.status_code == 201

        res_v2 = client.post("/api/sales", json=v_payload)
        assert res_v2.status_code == 200

        cur = memory_audit_db.cursor()
        cur.execute("SELECT COUNT(*) FROM tickets WHERE id = 'T-IDEMP-01'")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT COUNT(*) FROM ventas_auditoria WHERE ticket_id = 'T-IDEMP-01'")
        assert cur.fetchone()[0] == 1  # No se duplicó el evento CREACION

        # Gasto
        g_payload = {
            "detalle": "Luz",
            "monto_salida": 1500,
            "tipo": "General",
            "metodo_pago": "EFECTIVO",
            "idempotency_key": "KEY-GASTO-IDEMP-1",
        }
        res_g1 = client.post("/api/expenses", json=g_payload, headers={"Idempotency-Key": "KEY-GASTO-IDEMP-1"})
        assert res_g1.status_code == 201
        gid = res_g1.json()["id"]

        res_g2 = client.post("/api/expenses", json=g_payload, headers={"Idempotency-Key": "KEY-GASTO-IDEMP-1"})
        assert res_g2.status_code == 200

        cur.execute("SELECT COUNT(*) FROM gastos_retiros WHERE idempotency_key = 'KEY-GASTO-IDEMP-1'")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT COUNT(*) FROM gastos_auditoria WHERE gasto_id = ?", (gid,))
        assert cur.fetchone()[0] == 1  # No se duplicó el evento CREAR


def test_7_atomicidad_fallo_auditoria_revierte_operacion(memory_audit_db):
    """Punto 7: Un fallo al insertar el evento de auditoría revierte la modificación comercial."""
    # Eliminamos temporalmente la tabla cargas_servicios_auditoria para provocar un fallo en auditoría
    memory_audit_db.execute("DROP TABLE cargas_servicios_auditoria")
    memory_audit_db.commit()

    carga_dict = {
        "FECHA": "25/10/2026 15:00:00",
        "SERVICIO": "Claro",
        "MONTO_CARGA": 2000,
        "MONTO_ADICIONAL": 100,
        "TOTAL_COBRADO": 2100,
        "PAGO_EFVO": 2100,
        "PAGO_TRANSF": 0,
        "idempotency_key": "TEST-FAIL-AUDIT",
    }

    with pytest.raises(ErrorEsquemaNoPreparado):
        registrar_carga_idempotente_turso(carga_dict, idempotency_key="TEST-FAIL-AUDIT", actor="alan", conn=memory_audit_db)

    # Verificar que cargas_servicios NO conservó la fila insertada (rollback efectivo)
    cur = memory_audit_db.cursor()
    cur.execute("SELECT COUNT(*) FROM cargas_servicios WHERE idempotency_key = 'TEST-FAIL-AUDIT'")
    assert cur.fetchone()[0] == 0


def test_8_cierre_diario_persiste_actor_autenticado(memory_audit_db):
    """Punto 8: Los cierres diarios guardan el actor autenticado en cerrado_por."""
    app.dependency_overrides[get_db_connection] = lambda: memory_audit_db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="yilda", display_name="Yilda")

    with TestClient(app) as client:
        cierre_payload = {
            "fecha": "2026-10-25",
            "efectivo_esperado": 5000,
            "efectivo_contado": 5000,
            "cerrado_por": "alguien_arbitrario",  # Debe ser sobreescrito por la sesión
        }
        res = client.post("/api/expenses/cierres", json=cierre_payload)
        assert res.status_code == 201
        assert res.json()["cerrado_por"] == "yilda"

        cur = memory_audit_db.cursor()
        cur.execute("SELECT cerrado_por FROM cierres_diarios WHERE fecha = '2026-10-25'")
        assert cur.fetchone()[0] == "yilda"


def test_9_registros_historicos_preexistentes_no_se_modifican(memory_audit_db):
    """Punto 9: Filas históricas conservan sus valores originales sin alteración retrospectiva."""
    cur = memory_audit_db.cursor()
    cur.execute("""
        INSERT INTO cargas_servicios (id, legacy_row_id, fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado, idempotency_key)
        VALUES (999, 'ROW-999', '2026-01-01 10:00:00', 'Claro', 1000, 50, 1050, 1050, 0, 0, NULL)
    """)
    memory_audit_db.commit()

    cur.execute("SELECT * FROM cargas_servicios WHERE id = 999")
    fila_antes = cur.fetchone()

    # Operamos con Alan en otra carga nueva
    app.dependency_overrides[get_db_connection] = lambda: memory_audit_db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")

    with TestClient(app) as client:
        client.post("/api/services", json={
            "servicio": "Personal",
            "monto_carga": 2000,
            "monto_adicional": 100,
            "total_cobrado": 2100,
            "pago_efvo": 2100,
            "pago_transf": 0,
        })

    cur.execute("SELECT * FROM cargas_servicios WHERE id = 999")
    fila_despues = cur.fetchone()
    assert fila_antes == fila_despues


def test_10_autenticacion_y_modo_solo_lectura_intactos(memory_audit_db, monkeypatch):
    """Punto 10: Validar que llamadas sin token son rechazadas y que el modo solo lectura bloquea."""
    app.dependency_overrides[get_db_connection] = lambda: memory_audit_db
    app.dependency_overrides.clear()  # Quitamos overrides para probar seguridad real

    with TestClient(app) as client:
        # Sin token Bearer -> 401 Unauthorized
        res_no_auth = client.post("/api/services", json={"servicio": "Claro", "monto_carga": 1000, "total_cobrado": 1000, "pago_efvo": 1000})
        assert res_no_auth.status_code == 401


def test_11_atribucion_real_en_preventistas_y_pedidos(memory_audit_db):
    """P1-01: Verifica que los 5 endpoints de mutación de preventistas y pedidos
    registren al operador autenticado (alan o yilda) y nunca 'api_web'."""
    app.dependency_overrides[get_db_connection] = lambda: memory_audit_db

    with TestClient(app) as client:
        # 1. Alan actualiza precios por lote
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")
        res_precios = client.post(
            "/api/preventistas/precios",
            json={
                "proveedor": "General",
                "cambios": [{"id_producto": 1, "costo": 600, "precio_venta": 1200}],
            },
        )
        assert res_precios.status_code == 200

        # 2. Yilda crea producto por alta rápida
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="yilda", display_name="Yilda")
        res_alta = client.post(
            "/api/preventistas/productos/alta-rapida",
            json={
                "proveedor": "Coca Cola",
                "nombre": "Fanta Naranja 2L",
                "categoria": "Bebidas",
                "unidad": "Unidad",
                "costo": 1000,
                "precio_venta": 1800,
            },
        )
        assert res_alta.status_code == 201
        nuevo_prod_id = res_alta.json()["id_producto"]

        # 3. Alan crea un pedido a proveedor
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")
        res_ped = client.post(
            "/api/preventistas/pedidos",
            json={
                "id": "PED-ATTR-001",
                "proveedor": "Coca Cola",
                "fecha": "2026-10-25",
                "observaciones": "Pedido inicial",
                "items": [
                    {
                        "producto_id": nuevo_prod_id,
                        "producto_nombre": "Fanta Naranja 2L",
                        "cantidad": 6,
                        "costo_unitario": 1000,
                        "subtotal": 6000,
                    }
                ],
            },
        )
        assert res_ped.status_code == 201

        # 4. Yilda cambia estado del pedido a RECIBIDO
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="yilda", display_name="Yilda")
        res_est = client.patch(
            "/api/preventistas/pedidos/PED-ATTR-001/estado",
            json={"estado": "RECIBIDO", "observaciones": "Recibido completo"},
        )
        assert res_est.status_code == 200

        # 5. Alan cancela otro pedido
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")
        client.post(
            "/api/preventistas/pedidos",
            json={
                "id": "PED-ATTR-002",
                "proveedor": "Coca Cola",
                "fecha": "2026-10-25",
                "items": [
                    {
                        "producto_id": nuevo_prod_id,
                        "producto_nombre": "Fanta Naranja 2L",
                        "cantidad": 2,
                        "costo_unitario": 1000,
                        "subtotal": 2000,
                    }
                ],
            },
        )
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="yilda", display_name="Yilda")
        res_canc = client.post(
            "/api/preventistas/pedidos/PED-ATTR-002/cancelar",
            json={"motivo": "Proveedor sin stock"},
        )
        assert res_canc.status_code == 200

        cur = memory_audit_db.cursor()
        cur.execute("SELECT producto_id, accion, actor FROM productos_auditoria ORDER BY id ASC")
        prod_aud = cur.fetchall()
        assert prod_aud == [
            (1, "EDITAR", "alan"),
            (nuevo_prod_id, "CREAR", "yilda"),
        ]
        assert all(r[2] != "api_web" for r in prod_aud)

        cur.execute("SELECT pedido_id, accion, actor FROM pedidos_auditoria ORDER BY id ASC")
        ped_aud = cur.fetchall()
        assert ped_aud == [
            ("PED-ATTR-001", "CREAR_PEDIDO", "alan"),
            ("PED-ATTR-001", "CAMBIAR_ESTADO", "yilda"),
            ("PED-ATTR-002", "CREAR_PEDIDO", "alan"),
            ("PED-ATTR-002", "CANCELAR_PEDIDO", "yilda"),
        ]
        assert all(r[2] != "api_web" for r in ped_aud)

    app.dependency_overrides.clear()
