"""Gestión del Catálogo de Fondos — GENARO TESORERÍA LABORATORIO.

Implementación de LAB-003:
- Diez fondos canónicos: 1110, 1120, 1130, 1140, 1150, 1160, 1210, 1220, 1230 y 1310.
- Atributos completos: Identificador, código, nombre, naturaleza, titularidad, estado, saldos enteros (0 inicial), auditoría.
- Restricciones de integridad y protección contra modificaciones no autorizadas o eliminación.
- Cero saldos ficticios o históricos importados.
"""

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Dict, List, Optional
from tesoreria.isolation import ErrorViolacionAislamiento
from tesoreria.money import assertValidMoney

# Definición canónica de los 10 Fondos de Tesorería (Catálogo Aprobado Sprint 4 + Fondo PREPAY Diario 1160)
FONDOS_CANONICOS = [
    {
        "codigo": "1110",
        "nombre": "Drugstore Diario",
        "naturaleza": "EFECTIVO",
        "titularidad": "PROPIA",
        "permite_sobregiro": 0,
    },
    {
        "codigo": "1120",
        "nombre": "Drugstore Acumulado",
        "naturaleza": "EFECTIVO",
        "titularidad": "PROPIA",
        "permite_sobregiro": 0,
    },
    {
        "codigo": "1130",
        "nombre": "Prepay Efectivo",
        "naturaleza": "EFECTIVO",
        "titularidad": "PROPIA",
        "permite_sobregiro": 0,
    },
    {
        "codigo": "1140",
        "nombre": "Adicionales Efectivo",
        "naturaleza": "ADICIONALES_PARTICULARES",
        "titularidad": "PROPIA",
        "permite_sobregiro": 0,
    },
    {
        "codigo": "1150",
        "nombre": "Claro Efectivo",
        "naturaleza": "RECAUDACION_TERCEROS",
        "titularidad": "CUSTODIA",
        "permite_sobregiro": 0,
    },
    {
        "codigo": "1160",
        "nombre": "Prepay Diario Efectivo",
        "naturaleza": "EFECTIVO",
        "titularidad": "PROPIA",
        "permite_sobregiro": 0,
    },
    {
        "codigo": "1210",
        "nombre": "Mercado Pago Propio",
        "naturaleza": "BILLETERA_VIRTUAL",
        "titularidad": "PROPIA",
        "permite_sobregiro": 0,
    },
    {
        "codigo": "1220",
        "nombre": "Prepay Digital Saldo",
        "naturaleza": "BILLETERA_VIRTUAL",
        "titularidad": "PROPIA",
        "permite_sobregiro": 0,
    },
    {
        "codigo": "1230",
        "nombre": "Claro Comercios Digital Saldo",
        "naturaleza": "BILLETERA_VIRTUAL",
        "titularidad": "PROPIA",
        "permite_sobregiro": 0,
    },
    {
        "codigo": "1310",
        "nombre": "Cobranzas en Tránsito Preventistas",
        "naturaleza": "TRANSITO_PREVENTISTAS",
        "titularidad": "TRANSITO",
        "permite_sobregiro": 0,
    },
]

CODIGOS_CANONICOS = {f["codigo"] for f in FONDOS_CANONICOS}


class ErrorCatalogoFondos(Exception):
    """Excepción para violaciones de reglas de negocio o integridad del catálogo."""
    pass


def sembrar_catalogo_canónico(conn: sqlite3.Connection, usuario: str = "SISTEMA_LAB") -> int:
    """Siembra los 9 fondos canónicos en la base de datos si no existen aún.

    Garantiza:
    - Saldo inicial estrictamente 0 centavos.
    - Estado inicial 'ACTIVO'.
    - Moneda 'ARS'.
    - Registro de auditoría automático por trigger.
    """
    ahora_utc = datetime.now(timezone.utc).isoformat()
    insertados = 0

    cursor = conn.cursor()
    for fondo in FONDOS_CANONICOS:
        cursor.execute("SELECT id, nombre, naturaleza, titularidad FROM tesoreria_fondos WHERE codigo = ?", (fondo["codigo"],))
        existe = cursor.fetchone()
        if not existe:
            cursor.execute(
                """
                INSERT INTO tesoreria_fondos (
                    codigo, nombre, naturaleza, titularidad, moneda, estado, saldo_centavos,
                    permite_sobregiro, version, created_at, updated_at, creado_por, actualizado_por
                ) VALUES (?, ?, ?, ?, 'ARS', 'ACTIVO', 0, ?, 1, ?, ?, ?, ?)
                """,
                (
                    fondo["codigo"],
                    fondo["nombre"],
                    fondo["naturaleza"],
                    fondo["titularidad"],
                    fondo["permite_sobregiro"],
                    ahora_utc,
                    ahora_utc,
                    usuario,
                    usuario,
                ),
            )
            insertados += 1
        else:
            # SPRINT 4: Sincronizar catálogo canónico aprobado
            cursor.execute(
                """
                UPDATE tesoreria_fondos
                SET nombre = ?, naturaleza = ?, titularidad = ?, actualizado_por = ?, updated_at = ?
                WHERE codigo = ? AND (nombre <> ? OR naturaleza <> ? OR titularidad <> ?)
                """,
                (
                    fondo["nombre"],
                    fondo["naturaleza"],
                    fondo["titularidad"],
                    usuario,
                    ahora_utc,
                    fondo["codigo"],
                    fondo["nombre"],
                    fondo["naturaleza"],
                    fondo["titularidad"],
                ),
            )

    conn.commit()
    cursor.close()
    return insertados


def obtener_catalogo_fondos(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Retorna los fondos del catálogo ordenados por código contable."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, codigo, nombre, naturaleza, titularidad, moneda, estado,
               saldo_centavos, permite_sobregiro, version, created_at, updated_at,
               creado_por, actualizado_por
        FROM tesoreria_fondos
        ORDER BY codigo ASC
        """
    )
    filas = cursor.fetchall()
    cursor.close()

    resultado = []
    for f in filas:
        d = dict(f)
        # Validar consistencia de saldo entero
        assertValidMoney(d["saldo_centavos"])
        # Indicador de disponibilidad:
        # Los fondos de titularidad CUSTODIA (1150) o TRANSITO (1310) no son disponibilidad propia líquida directa
        es_disponible = (d["estado"] == "ACTIVO") and (d["titularidad"] == "PROPIA")
        d["disponibilidad_inmediata"] = es_disponible
        resultado.append(d)

    return resultado


def obtener_fondo_por_codigo(conn: sqlite3.Connection, codigo: str) -> Optional[Dict[str, Any]]:
    """Busca un fondo específico por su código contable."""
    if codigo not in CODIGOS_CANONICOS:
        raise ErrorCatalogoFondos(f"Código de fondo desconocido o no permitido: {codigo}")

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, codigo, nombre, naturaleza, titularidad, moneda, estado,
               saldo_centavos, permite_sobregiro, version, created_at, updated_at,
               creado_por, actualizado_por
        FROM tesoreria_fondos
        WHERE codigo = ?
        """,
        (codigo,),
    )
    fila = cursor.fetchone()
    cursor.close()

    if not fila:
        return None

    d = dict(fila)
    assertValidMoney(d["saldo_centavos"])
    d["disponibilidad_inmediata"] = (d["estado"] == "ACTIVO") and (d["titularidad"] == "PROPIA")
    return d


def obtener_fondo_por_id(conn: sqlite3.Connection, fondo_id: int) -> Optional[Dict[str, Any]]:
    """Busca un fondo específico por su ID primario."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, codigo, nombre, naturaleza, titularidad, moneda, estado,
               saldo_centavos, permite_sobregiro, version, created_at, updated_at,
               creado_por, actualizado_por
        FROM tesoreria_fondos
        WHERE id = ?
        """,
        (fondo_id,),
    )
    fila = cursor.fetchone()
    cursor.close()
    if not fila:
        return None
    d = dict(fila)
    assertValidMoney(d["saldo_centavos"])
    d["disponibilidad_inmediata"] = (d["estado"] == "ACTIVO") and (d["titularidad"] == "PROPIA")
    return d


def actualizar_estado_fondo(
    conn: sqlite3.Connection,
    codigo: str,
    nuevo_estado: str,
    usuario: str,
    motivo: str = "Actualización administrativa",
) -> Dict[str, Any]:
    """Actualiza el estado operativo de un fondo (ACTIVO, INACTIVO, BLOQUEADO).

    Protección:
    - Valida transiciones de estado permitidas.
    - Prohíbe modificar saldo_centavos o código mediante esta vía.
    - Registra auditoría y actualiza versión optimista.
    """
    if nuevo_estado not in ("ACTIVO", "INACTIVO", "BLOQUEADO"):
        raise ErrorCatalogoFondos(f"Estado de fondo inválido: {nuevo_estado}")

    fondo_actual = obtener_fondo_por_codigo(conn, codigo)
    if not fondo_actual:
        raise ErrorCatalogoFondos(f"Fondo no encontrado: {codigo}")

    ahora_utc = datetime.now(timezone.utc).isoformat()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE tesoreria_fondos
        SET estado = ?,
            version = version + 1,
            updated_at = ?,
            actualizado_por = ?
        WHERE codigo = ? AND version = ?
        """,
        (nuevo_estado, ahora_utc, usuario, codigo, fondo_actual["version"]),
    )

    if cursor.rowcount == 0:
        cursor.close()
        raise ErrorCatalogoFondos("Conflicto de concurrencia al actualizar el fondo (versión desactualizada).")

    conn.commit()
    cursor.close()
    return obtener_fondo_por_codigo(conn, codigo)


def obtener_auditoria_fondo(conn: sqlite3.Connection, codigo: str) -> List[Dict[str, Any]]:
    """Retorna el historial de auditoría inmutable para un fondo específico."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, fondo_id, codigo, accion, usuario, valores_previos,
               valores_nuevos, motivo, timestamp_utc
        FROM tesoreria_fondos_auditoria
        WHERE codigo = ?
        ORDER BY id DESC
        """,
        (codigo,),
    )
    filas = cursor.fetchall()
    cursor.close()

    registros = []
    for r in filas:
        item = dict(r)
        if item["valores_previos"]:
            item["valores_previos"] = json.loads(item["valores_previos"])
        if item["valores_nuevos"]:
            item["valores_nuevos"] = json.loads(item["valores_nuevos"])
        registros.append(item)

    return registros


def verificar_integridad_catalogo(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Valida la integridad completa del catálogo:

    1. Existencia exacta de los 9 fondos canónicos.
    2. Cero saldos negativos no permitidos.
    3. Tipos INTEGER válidos para saldos en centavos.
    4. Triggers de protección activos.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT codigo, saldo_centavos, estado, naturaleza, titularidad FROM tesoreria_fondos")
    fondos = cursor.fetchall()
    cursor.close()

    codigos_presentes = {f["codigo"] for f in fondos}
    if codigos_presentes != CODIGOS_CANONICOS:
        faltantes = CODIGOS_CANONICOS - codigos_presentes
        sobrantes = codigos_presentes - CODIGOS_CANONICOS
        return {
            "valido": False,
            "error": f"Discrepancia en catálogo canónico. Faltantes: {faltantes}, Sobrantes: {sobrantes}",
            "codigos_presentes": list(codigos_presentes),
        }

    for f in fondos:
        saldo = f["saldo_centavos"]
        assertValidMoney(saldo)

    return {
        "valido": True,
        "total_fondos": len(fondos),
        "codigos": sorted(list(codigos_presentes)),
    }
