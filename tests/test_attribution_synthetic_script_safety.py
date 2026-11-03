"""Pruebas locales de regresión y seguridad para el procedimiento sintético de atribución y auditoría.

Verifica:
1. Que el script scripts/ejecutar_prueba_sintetica_atribucion_dev.py exista, sea ejecutable y no exponga contraseñas.
2. Que los payloads de las operaciones comerciales cumplan estrictamente con los esquemas Pydantic del backend.
3. Que el cálculo de hashes históricos de tickets e ítems no sufra falsos positivos al crearse y anularse operaciones sintéticas nuevas.
4. Que el cálculo de hashes detecte cualquier modificación o eliminación de los registros originales preexistentes.
5. Que la consulta maneje correctamente listas vacías y use parámetros SQL sin interpolación.
"""

import hashlib
import json
from pathlib import Path
import sqlite3
import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.dependencies import AuthenticatedUser, get_current_user, get_db_connection
from api.schemas import (
    AnularVentaIn,
    CargaServicioCreateIn,
    GastoCreateIn,
    VentaRequest,
)
from scripts.ejecutar_prueba_sintetica_atribucion_dev import (
    calcular_hash_tickets,
    calcular_hash_tickets_preexistentes,
    calcular_hash_ticket_items,
)

ROOT = Path(__file__).resolve().parents[1]


def test_script_seguridad_estatica():
    """1. Comprueba restricciones de seguridad y configuración en el script sintético."""
    script_path = ROOT / "scripts/ejecutar_prueba_sintetica_atribucion_dev.py"
    assert script_path.exists(), "El script sintético debe existir"
    content = script_path.read_text(encoding="utf-8")

    # Debe validar el hostname y entorno autorizado
    assert "genaro-pos-dev-alanrod10.aws-sa-east-1.turso.io" in content
    assert "genaro-pos-dev" in content

    # Debe usar getpass para no exponer contraseñas
    assert "getpass(" in content

    # No debe imprimir contraseñas ni tokens
    assert "print(pass_alan)" not in content
    assert "print(pass_yilda)" not in content
    assert "print(token_alan)" not in content
    assert "print(token_yilda)" not in content


def test_payloads_cumplen_contratos_pydantic():
    """2. Comprueba que las cargas útiles utilizadas en el script validen sin error contra los esquemas."""
    # Venta
    venta_payload = {
        "ticket": {
            "ticket_id": "T-DEV-AUDIT-ATTRIB-20261025-001",
            "fecha": "25/10/2026 19:30:00",
            "total_venta": 2700,
            "monto_efectivo": 2700,
            "monto_transf": 0,
            "metodo_pago": "EFECTIVO",
            "es_nocturno": False,
        },
        "items": [
            {
                "producto": "361 1,5lts",
                "cantidad": 1.0,
                "unidad": "Unidad",
                "precio_unit": 2700,
                "subtotal": 2700,
                "metodo_pago": "EFECTIVO",
            }
        ],
    }
    v_req = VentaRequest(**venta_payload)
    assert v_req.ticket.ticket_id == "T-DEV-AUDIT-ATTRIB-20261025-001"
    assert len(v_req.items) == 1

    # Carga de servicio
    serv_payload = {
        "servicio": "PRUEBA_SINTETICA_AUDITORIA_ATRIBUCION_DEV",
        "monto_carga": 100,
        "monto_adicional": 0,
        "total_cobrado": 100,
        "pago_efvo": 100,
        "pago_transf": 0,
        "idempotency_key": "IDEMP-DEV-SERV-TEST",
    }
    s_req = CargaServicioCreateIn(**serv_payload)
    assert s_req.servicio == "PRUEBA_SINTETICA_AUDITORIA_ATRIBUCION_DEV"
    assert s_req.idempotency_key == "IDEMP-DEV-SERV-TEST"

    # Gasto
    gasto_payload = {
        "detalle": "PRUEBA_SINTETICA_AUDITORIA_ATRIBUCION_DEV",
        "monto_salida": 50,
        "tipo": "VARIOS",
        "origen_fondos": "CAJA",
        "metodo_pago": "EFECTIVO",
        "idempotency_key": "IDEMP-DEV-EXP-TEST",
    }
    g_req = GastoCreateIn(**gasto_payload)
    assert g_req.detalle == "PRUEBA_SINTETICA_AUDITORIA_ATRIBUCION_DEV"
    assert g_req.idempotency_key == "IDEMP-DEV-EXP-TEST"

    # Anulación venta
    anul_payload = {"motivo": "PRUEBA_SINTETICA_AUDITORIA_ATRIBUCION_DEV"}
    a_req = AnularVentaIn(**anul_payload)
    assert a_req.motivo == "PRUEBA_SINTETICA_AUDITORIA_ATRIBUCION_DEV"


@pytest.fixture
def temp_db():
    """Base de datos SQLite en memoria con esquema de tickets e ítems."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript("""
        CREATE TABLE tickets (
            id TEXT PRIMARY KEY,
            fecha_hora_utc TEXT NOT NULL,
            total_venta INTEGER NOT NULL,
            monto_efectivo INTEGER NOT NULL,
            monto_transferencia INTEGER NOT NULL,
            metodo_pago TEXT NOT NULL,
            anulado INTEGER NOT NULL DEFAULT 0,
            motivo_anulacion TEXT
        );

        CREATE TABLE ticket_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            producto_id INTEGER NOT NULL,
            cantidad REAL NOT NULL,
            precio_unitario INTEGER NOT NULL,
            subtotal INTEGER NOT NULL,
            costo_unitario INTEGER,
            FOREIGN KEY (ticket_id) REFERENCES tickets(id)
        );
    """)
    # Insertar 4 tickets iniciales preexistentes
    conn.executescript("""
        INSERT INTO tickets (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago, anulado) VALUES
            ('T-DEV-001', '2026-10-25 10:00:00', 3000, 3000, 0, 'EFECTIVO', 0),
            ('T-DEV-002', '2026-10-25 11:00:00', 500, 500, 0, 'EFECTIVO', 1),
            ('T-DEV-003', '2026-10-25 12:00:00', 2000, 2000, 0, 'EFECTIVO', 1),
            ('T-DEV-004', '2026-10-25 13:00:00', 1500, 1500, 0, 'EFECTIVO', 1);

        INSERT INTO ticket_items (id, ticket_id, producto_id, cantidad, precio_unitario, subtotal, costo_unitario) VALUES
            (1, 'T-DEV-001', 10, 1.0, 3000, 3000, NULL),
            (2, 'T-DEV-002', 20, 1.0, 500, 500, 300),
            (3, 'T-DEV-003', 30, 2.0, 1000, 2000, NULL),
            (4, 'T-DEV-004', 40, 1.0, 1500, 1500, NULL);
    """)
    yield conn
    conn.close()


def test_hash_tickets_preexistentes_ignora_quinto_ticket_sintetico_y_su_anulacion(temp_db):
    """3. Verifica que un quinto ticket sintético (activo o anulado) no altere el hash del conjunto original."""
    ticket_ids_base = ['T-DEV-001', 'T-DEV-002', 'T-DEV-003', 'T-DEV-004']

    # Hash inicial de los 4 tickets
    hash_base, count_base = calcular_hash_tickets(temp_db, ticket_ids_base)
    assert count_base == 4
    assert isinstance(hash_base, str) and len(hash_base) == 64

    # Alias compatible
    hash_alias, count_alias = calcular_hash_tickets_preexistentes(temp_db, ticket_ids_base)
    assert hash_alias == hash_base
    assert count_alias == 4

    # Insertar un 5º ticket sintético (activo)
    temp_db.execute("""
        INSERT INTO tickets (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago, anulado)
        VALUES ('T-DEV-AUDIT-ATTRIB-999', '2026-10-25 19:30:00', 2700, 2700, 0, 'EFECTIVO', 0)
    """)
    temp_db.commit()

    # Total de tickets ahora es 5 en la tabla
    cur = temp_db.cursor()
    cur.execute("SELECT COUNT(*) FROM tickets")
    assert cur.fetchone()[0] == 5

    # Comprobación acotada al conjunto original: el hash DEBE permanecer idéntico
    hash_con_5to_activo, count_post = calcular_hash_tickets(temp_db, ticket_ids_base)
    assert count_post == 4
    assert hash_con_5to_activo == hash_base, "El quinto ticket activo no debe cambiar el hash del conjunto original"

    # Anular el 5º ticket sintético
    temp_db.execute("UPDATE tickets SET anulado = 1, motivo_anulacion = 'TEST' WHERE id = 'T-DEV-AUDIT-ATTRIB-999'")
    temp_db.commit()

    # Comprobación tras anulación del 5to ticket: el hash DEBE seguir idéntico
    hash_con_5to_anulado, count_anul = calcular_hash_tickets(temp_db, ticket_ids_base)
    assert count_anul == 4
    assert hash_con_5to_anulado == hash_base, "La anulación del ticket sintético no debe cambiar el hash del conjunto original"

    # Comparación de contraste: la consulta global SIN filtro sí habría cambiado de hash (el error previo)
    hash_sin_filtro, count_sin_filtro = calcular_hash_tickets(temp_db, None)
    assert count_sin_filtro == 5
    assert hash_sin_filtro != hash_base, "La consulta global sin filtro produce el falso positivo reportado"


def test_hash_tickets_detecta_modificacion_o_eliminacion_en_preexistentes(temp_db):
    """4. Verifica que cualquier alteración o eliminación en los 4 tickets originales sí cambie el hash o conteo."""
    ticket_ids_base = ['T-DEV-001', 'T-DEV-002', 'T-DEV-003', 'T-DEV-004']
    hash_base, _ = calcular_hash_tickets(temp_db, ticket_ids_base)

    # 1. Modificar total_venta de un ticket preexistente
    temp_db.execute("UPDATE tickets SET total_venta = 9999 WHERE id = 'T-DEV-001'")
    temp_db.commit()
    hash_mod, count_mod = calcular_hash_tickets(temp_db, ticket_ids_base)
    assert count_mod == 4
    assert hash_mod != hash_base, "Modificar un campo en un ticket original debe cambiar el hash"

    # Restaurar
    temp_db.execute("UPDATE tickets SET total_venta = 3000 WHERE id = 'T-DEV-001'")
    temp_db.commit()
    hash_restaurado, _ = calcular_hash_tickets(temp_db, ticket_ids_base)
    assert hash_restaurado == hash_base

    # 2. Modificar estado anulado de un ticket preexistente
    temp_db.execute("UPDATE tickets SET anulado = 1 WHERE id = 'T-DEV-001'")
    temp_db.commit()
    hash_anulado_mod, _ = calcular_hash_tickets(temp_db, ticket_ids_base)
    assert hash_anulado_mod != hash_base, "Modificar el estado anulado en un ticket original debe cambiar el hash"

    # Restaurar
    temp_db.execute("UPDATE tickets SET anulado = 0 WHERE id = 'T-DEV-001'")
    temp_db.commit()

    # 3. Eliminar un ticket preexistente (borrando su ítem asociado para respetar FK)
    temp_db.execute("DELETE FROM ticket_items WHERE ticket_id = 'T-DEV-004'")
    temp_db.execute("DELETE FROM tickets WHERE id = 'T-DEV-004'")
    temp_db.commit()
    hash_eliminado, count_eliminado = calcular_hash_tickets(temp_db, ticket_ids_base)
    assert count_eliminado == 3  # Detecta que falta 1
    assert count_eliminado < len(ticket_ids_base)
    assert hash_eliminado != hash_base, "Eliminar un ticket original debe cambiar el hash"


def test_hash_tickets_manejo_lista_vacia_y_parametros_sql(temp_db):
    """5. Verifica que calcular_hash_tickets maneje listas vacías y use parámetros SQL de forma segura."""
    # Lista vacía no ejecuta SQL con sintaxis inválida IN ()
    hash_vacio, count_vacio = calcular_hash_tickets(temp_db, [])
    assert count_vacio == 0
    assert hash_vacio == hashlib.sha256(b"").hexdigest()

    # IDs con caracteres especiales o intentos de inyección se manejan con seguridad
    ids_inyeccion = ["' OR '1'='1", "T-DEV-001'; DROP TABLE tickets; --"]
    hash_iny, count_iny = calcular_hash_tickets(temp_db, ids_inyeccion)
    assert count_iny == 0  # No existen esos IDs

    # La tabla debe seguir existiendo intacta
    cur = temp_db.cursor()
    cur.execute("SELECT COUNT(*) FROM tickets")
    assert cur.fetchone()[0] == 4


def test_hash_ticket_items_ignora_item_nuevo_y_detecta_alteraciones(temp_db):
    """6. Verifica que calcular_hash_ticket_items aísle los ítems originales y detecte cambios."""
    item_ids_base = [1, 2, 3, 4]
    hash_items_base, count_items_base = calcular_hash_ticket_items(temp_db, item_ids_base)
    assert count_items_base == 4
    assert isinstance(hash_items_base, str) and len(hash_items_base) == 64

    # Insertar un 5to ítem sintético nuevo
    temp_db.execute("""
        INSERT INTO ticket_items (id, ticket_id, producto_id, cantidad, precio_unitario, subtotal, costo_unitario)
        VALUES (99, 'T-DEV-001', 999, 1.0, 100, 100, 80)
    """)
    temp_db.commit()

    # Con la lista base explícita, el hash de los ítems originales no cambia
    hash_items_con_nuevo, count_con_nuevo = calcular_hash_ticket_items(temp_db, item_ids_base)
    assert count_con_nuevo == 4
    assert hash_items_con_nuevo == hash_items_base

    # Modificar un ítem original
    temp_db.execute("UPDATE ticket_items SET costo_unitario = 999 WHERE id = 1")
    temp_db.commit()
    hash_items_mod, _ = calcular_hash_ticket_items(temp_db, item_ids_base)
    assert hash_items_mod != hash_items_base

    # Restaurar
    temp_db.execute("UPDATE ticket_items SET costo_unitario = NULL WHERE id = 1")
    temp_db.commit()

    # Eliminar un ítem original
    temp_db.execute("DELETE FROM ticket_items WHERE id = 4")
    temp_db.commit()
    hash_items_del, count_del = calcular_hash_ticket_items(temp_db, item_ids_base)
    assert count_del == 3
    assert hash_items_del != hash_items_base
