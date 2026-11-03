"""Arqueos de Fondos y Gestión de Expedientes de Diferencias — LAB-009.

Reglas e Invariantes:
- Tipos de Arqueo: OPENING, PARTIAL, TOTAL, EXTRAORDINARY, CLOSING.
- Estados de Arqueo y Caso: OPEN, UNDER_REVIEW, PENDING, RESOLVED.
- Captura de saldo registrado como snapshot del momento.
- Captura de saldo verificado (centavos enteros INTEGER).
- Cálculo determinista de diferencia en centavos: subtractMoney(verificado, registrado).
- PROHIBIDO modificar el saldo del fondo al realizar el arqueo.
- PROHIBIDO realizar ajustes automáticos o imputar descuentos a salarios de personas.
- Prohibido establecer vencimientos automáticos o caducidades forzadas.
- Permite expedientes de diferencias sin resolver mientras la investigación continúe.
- Exige autorización expresa y evidencia documentada para resoluciones definitivas.
- Historial inmutable y protegido contra DELETE.
"""

from datetime import datetime, timezone
import sqlite3
from typing import Any, Dict, List, Optional
import uuid

from tesoreria.audit_chain import recordAuditEvent
from tesoreria.catalog import obtener_fondo_por_codigo
from tesoreria.jornadas import obtener_jornada_actual
from tesoreria.money import assertValidMoney, formatMoney, subtractMoney
from tesoreria.outbox import enqueueOutboxEvent

TIPOS_ARQUEO = {"OPENING", "PARTIAL", "TOTAL", "EXTRAORDINARY", "CLOSING"}
ESTADOS_EXPEDIENTE = {"OPEN", "UNDER_REVIEW", "PENDING", "RESOLVED"}
FONDOS_FISICOS = {"1110", "1120", "1130", "1140", "1150", "1160"}
FONDOS_DIGITALES = {"1210", "1220", "1230"}
ADMIN_USERS = {"alan", "yilda"}


class ErrorArqueo(Exception):
    """Excepción para violaciones de reglas en arqueos y conciliaciones."""
    pass


def createReconciliation(
    conn: sqlite3.Connection,
    fund_code: str,
    reconciliation_type: str,
    verified_amount_cents: int,
    user_id: str,
    notes: Optional[str] = None,
    create_difference_case: bool = False,
    investigation_notes: Optional[str] = None,
    authorized_by: Optional[str] = None,
    authorization_notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Registra un arqueo físico o de balance sobre un fondo sin alterar su saldo en libros."""
    if not user_id or not user_id.strip():
        raise ErrorArqueo("El usuario que realiza el arqueo es obligatorio.")

    norm_user = user_id.strip().lower()

    if reconciliation_type not in TIPOS_ARQUEO:
        raise ErrorArqueo(
            f"Tipo de arqueo inválido: '{reconciliation_type}'. Válidos: {', '.join(sorted(TIPOS_ARQUEO))}"
        )

    assertValidMoney(verified_amount_cents)
    if verified_amount_cents < 0:
        raise ErrorArqueo("El importe verificado en arqueo no puede ser negativo.")

    fondo = obtener_fondo_por_codigo(conn, fund_code)
    if not fondo:
        raise ErrorArqueo(f"Fondo '{fund_code}' no existe en el catálogo.")

    jornada = obtener_jornada_actual(conn)
    if not jornada:
        raise ErrorArqueo("No hay una jornada de tesorería activa para registrar el arqueo.")

    saldo_registrado = fondo["saldo_centavos"]
    diferencia = subtractMoney(verified_amount_cents, saldo_registrado)

    clean_notes = (notes.strip() if notes and notes.strip() else None) or (
        investigation_notes.strip() if investigation_notes and investigation_notes.strip() else None
    )

    reconciliation_id = str(uuid.uuid4())
    now_utc = datetime.now(timezone.utc).isoformat()

    status = "RESOLVED" if diferencia == 0 else "OPEN"
    diff_case_id: Optional[str] = None

    auth_by_val: Optional[str] = None
    auth_at_val: Optional[str] = None
    auth_notes_val: Optional[str] = None

    if diferencia != 0:
        auth_by_val = norm_user if norm_user in ADMIN_USERS else user_id.strip()
        auth_at_val = now_utc
        auth_notes_val = clean_notes

    cursor = conn.cursor()

    # 1. Registrar primero el arqueo (NO MODIFICA saldo_centavos en tesoreria_fondos)
    cursor.execute(
        """
        INSERT INTO tesoreria_arqueos (
            reconciliation_id, journal_id, fund_id,
            reconciliation_type, status,
            saldo_registrado_cents, saldo_verificado_cents, diferencia_cents,
            currency, performed_by, performed_at, notes, difference_case_id,
            authorized_by, authorized_at, authorization_notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ARS', ?, ?, ?, NULL, ?, ?, ?)
        """,
        (
            reconciliation_id,
            jornada["id"],
            fondo["id"],
            reconciliation_type,
            status,
            saldo_registrado,
            verified_amount_cents,
            diferencia,
            norm_user,
            now_utc,
            clean_notes,
            auth_by_val,
            auth_at_val,
            auth_notes_val,
        ),
    )

    # 2. Si hay diferencia y se solicita crear expediente de investigación, crearlo ahora que existe el arqueo
    if diferencia != 0 and (create_difference_case or investigation_notes):
        diff_case_id = str(uuid.uuid4())
        inv_notes = (
            investigation_notes.strip()
            if investigation_notes and investigation_notes.strip()
            else (clean_notes or f"Diferencia de {formatMoney(diferencia)} detectada en arqueo {reconciliation_type} de fondo {fund_code}.")
        )

        cursor.execute(
            """
            INSERT INTO tesoreria_expedientes_diferencias (
                case_id, reconciliation_id, journal_id, fund_id,
                difference_cents, status, investigation_notes,
                created_by, created_at, updated_at, version,
                authorized_by, authorized_at, authorization_notes
            ) VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                diff_case_id,
                reconciliation_id,
                jornada["id"],
                fondo["id"],
                diferencia,
                inv_notes,
                norm_user,
                now_utc,
                now_utc,
                auth_by_val,
                auth_at_val,
                auth_notes_val,
            ),
        )

        cursor.execute(
            """
            UPDATE tesoreria_arqueos
            SET difference_case_id = ?
            WHERE reconciliation_id = ?
            """,
            (diff_case_id, reconciliation_id),
        )

    tipo_dif = "FALTANTE" if diferencia < 0 else ("SOBRANTE" if diferencia > 0 else "CONFORME")
    reconciliation_dict = {
        "reconciliation_id": reconciliation_id,
        "journal_id": jornada["id"],
        "fund_id": fondo["id"],
        "fund_code": fund_code,
        "fund_name": fondo["nombre"],
        "reconciliation_type": reconciliation_type,
        "status": status,
        "saldo_registrado_cents": saldo_registrado,
        "saldo_registrado_formatted": formatMoney(saldo_registrado),
        "saldo_verificado_cents": verified_amount_cents,
        "saldo_verificado_formatted": formatMoney(verified_amount_cents),
        "diferencia_cents": diferencia,
        "diferencia_formatted": formatMoney(diferencia),
        "tipo_diferencia": tipo_dif,
        "currency": "ARS",
        "performed_by": user_id.strip(),
        "performed_at": now_utc,
        "notes": notes,
        "difference_case_id": diff_case_id,
        "authorized_by": auth_by_val,
        "authorized_at": auth_at_val,
        "authorization_notes": auth_notes_val,
        "is_authorized": bool(auth_by_val and auth_by_val in ADMIN_USERS),
    }

    # Auditoría canónica encadenada y Outbox
    recordAuditEvent(
        conn=conn,
        event_type="RECONCILIATION_CREATED",
        aggregate_type="RECONCILIATION",
        aggregate_id=reconciliation_id,
        user_id=user_id.strip(),
        payload=reconciliation_dict,
        occurred_at=now_utc,
    )

    enqueueOutboxEvent(
        conn=conn,
        event_type="RECONCILIATION_CREATED",
        aggregate_id=reconciliation_id,
        payload=reconciliation_dict,
    )

    conn.commit()
    cursor.close()

    return reconciliation_dict


def getReconciliation(conn: sqlite3.Connection, reconciliation_id: str) -> Optional[Dict[str, Any]]:
    """Recupera un arqueo específico por identificador único."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT a.id, a.reconciliation_id, a.journal_id, a.fund_id,
               f.codigo as fund_code, f.nombre as fund_name,
               a.reconciliation_type, a.status,
               a.saldo_registrado_cents, a.saldo_verificado_cents, a.diferencia_cents,
               a.currency, a.performed_by, a.performed_at, a.notes, a.difference_case_id,
               a.authorized_by, a.authorized_at, a.authorization_notes
        FROM tesoreria_arqueos a
        JOIN tesoreria_fondos f ON a.fund_id = f.id
        WHERE a.reconciliation_id = ?
        """,
        (reconciliation_id,),
    )
    row = cursor.fetchone()
    cursor.close()
    if not row:
        return None

    d = dict(row)
    d["saldo_registrado_formatted"] = formatMoney(d["saldo_registrado_cents"])
    d["saldo_verificado_formatted"] = formatMoney(d["saldo_verificado_cents"])
    d["diferencia_formatted"] = formatMoney(d["diferencia_cents"])
    d["is_digital"] = d["fund_code"] in FONDOS_DIGITALES or "[CONCILIACIÓN DIGITAL" in (d.get("notes") or "")
    d["estado_revision"] = "CON DIFERENCIA" if d["diferencia_cents"] != 0 else "CONCILIADO"
    d["is_authorized"] = bool(d.get("authorized_by") and str(d["authorized_by"]).strip().lower() in ADMIN_USERS)
    d["tipo_diferencia"] = "FALTANTE" if d["diferencia_cents"] < 0 else ("SOBRANTE" if d["diferencia_cents"] > 0 else "CONFORME")
    return d


def authorizeDifference(
    conn: sqlite3.Connection,
    reconciliation_id: str,
    user_id: str,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Registra la autorización administrativa de Alan o Yilda para una diferencia de arqueo.

    Invariantes de Autorización e Idempotencia:
    1. Solo Alan o Yilda pueden autorizar (ADMIN_USERS).
    2. Primera autorización: persiste authorized_by, authorized_at, authorization_notes,
       actualiza expediente y emite evento canónico de auditoría y outbox.
    3. Segunda autorización / Idempotencia: NO modifica authorized_by, NO actualiza la fecha/hora,
       NO reemplaza observaciones originales, NO emite eventos duplicados y retorna el estado original.
    4. Concurrencia atómica: utiliza actualización condicional WHERE authorized_by IS NULL.
    """
    if not user_id or not user_id.strip():
        raise ErrorArqueo("El usuario que autoriza la diferencia es obligatorio.")

    norm_user = user_id.strip().lower()
    if norm_user not in ADMIN_USERS:
        raise ErrorArqueo(
            f"Usuario '{user_id}' no autorizado. Solo los administradores Alan o Yilda pueden autorizar diferencias de arqueo."
        )

    recon = getReconciliation(conn, reconciliation_id)
    if not recon:
        raise ErrorArqueo(f"Arqueo {reconciliation_id} no encontrado.")

    if recon["diferencia_cents"] == 0:
        raise ErrorArqueo("El arqueo no presenta diferencias para autorizar.")

    # Si ya está autorizado, devolver de forma idempotente sin modificar absolutamente nada
    if recon.get("authorized_by"):
        return recon

    now_utc = datetime.now(timezone.utc).isoformat()
    obs = notes.strip() if notes and notes.strip() else f"Diferencia autorizada por {norm_user}."

    cursor = conn.cursor()
    try:
        # Actualización atómica condicional: SOLO si authorized_by IS NULL
        cursor.execute(
            """
            UPDATE tesoreria_arqueos
            SET authorized_by = ?,
                authorized_at = ?,
                authorization_notes = ?
            WHERE reconciliation_id = ? AND authorized_by IS NULL
            """,
            (norm_user, now_utc, obs, reconciliation_id),
        )

        if cursor.rowcount == 0:
            # Otra solicitud concurrente autorizó primero el arqueo
            cursor.close()
            fresh_recon = getReconciliation(conn, reconciliation_id)
            return fresh_recon or recon

        # Actualizar o crear expediente si no existiera
        diff_case_id = recon.get("difference_case_id")
        if diff_case_id:
            cursor.execute(
                """
                UPDATE tesoreria_expedientes_diferencias
                SET authorized_by = ?,
                    authorized_at = ?,
                    authorization_notes = ?,
                    updated_at = ?,
                    version = version + 1
                WHERE case_id = ? AND (authorized_by IS NULL OR authorized_by = ?)
                """,
                (norm_user, now_utc, obs, now_utc, diff_case_id, norm_user),
            )
        else:
            diff_case_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT INTO tesoreria_expedientes_diferencias (
                    case_id, reconciliation_id, journal_id, fund_id,
                    difference_cents, status, investigation_notes,
                    created_by, created_at, updated_at, version,
                    authorized_by, authorized_at, authorization_notes
                ) VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    diff_case_id,
                    reconciliation_id,
                    recon["journal_id"],
                    recon["fund_id"],
                    recon["diferencia_cents"],
                    f"Diferencia de {formatMoney(recon['diferencia_cents'])} autorizada por {norm_user}.",
                    norm_user,
                    now_utc,
                    now_utc,
                    norm_user,
                    now_utc,
                    obs,
                ),
            )
            cursor.execute(
                """
                UPDATE tesoreria_arqueos
                SET difference_case_id = ?
                WHERE reconciliation_id = ?
                """,
                (diff_case_id, reconciliation_id),
            )

        tipo_dif = "FALTANTE" if recon["diferencia_cents"] < 0 else "SOBRANTE"
        auth_dict = {
            "reconciliation_id": reconciliation_id,
            "difference_case_id": diff_case_id,
            "journal_id": recon["journal_id"],
            "fund_id": recon["fund_id"],
            "fund_code": recon["fund_code"],
            "fund_name": recon["fund_name"],
            "saldo_teorico_cents": recon["saldo_registrado_cents"],
            "saldo_teorico_formatted": recon["saldo_registrado_formatted"],
            "importe_contado_cents": recon["saldo_verificado_cents"],
            "importe_contado_formatted": recon["saldo_verificado_formatted"],
            "diferencia_cents": recon["diferencia_cents"],
            "diferencia_formatted": recon["diferencia_formatted"],
            "tipo_diferencia": tipo_dif,
            "authorized_by": norm_user,
            "authorized_at": now_utc,
            "authorization_notes": obs,
            "is_authorized": True,
            "status": recon["status"],
        }

        # Registrar auditoría canónica encadenada y Outbox SOLO en la primera autorización
        recordAuditEvent(
            conn=conn,
            event_type="DIFFERENCE_AUTHORIZED",
            aggregate_type="RECONCILIATION",
            aggregate_id=reconciliation_id,
            user_id=norm_user,
            payload=auth_dict,
            occurred_at=now_utc,
        )

        enqueueOutboxEvent(
            conn=conn,
            event_type="DIFFERENCE_AUTHORIZED",
            aggregate_id=reconciliation_id,
            payload=auth_dict,
        )

        conn.commit()
        cursor.close()
        return auth_dict
    except Exception:
        conn.rollback()
        cursor.close()
        raise


def updateReconciliationNotes(
    conn: sqlite3.Connection,
    reconciliation_id: str,
    new_notes: str,
    user_id: str,
) -> Dict[str, Any]:
    """Modifica la observación de un arqueo preservando la trazabilidad en auditoría."""
    if not user_id or user_id.strip().lower() not in ADMIN_USERS:
        raise ErrorArqueo("Solo Alan y Yilda pueden modificar observaciones de arqueos.")
    if not new_notes or not new_notes.strip():
        raise ErrorArqueo("La nueva observación no puede estar vacía.")

    recon = getReconciliation(conn, reconciliation_id)
    if not recon:
        raise ErrorArqueo(f"Arqueo '{reconciliation_id}' no encontrado.")

    old_notes = recon.get("notes")
    now_utc = datetime.now(timezone.utc).isoformat()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE tesoreria_arqueos
            SET notes = ?
            WHERE reconciliation_id = ?
            """,
            (new_notes.strip(), reconciliation_id),
        )

        if recon.get("difference_case_id"):
            cursor.execute(
                """
                UPDATE tesoreria_expedientes_diferencias
                SET investigation_notes = ?,
                    updated_at = ?,
                    version = version + 1
                WHERE case_id = ?
                """,
                (new_notes.strip(), now_utc, recon["difference_case_id"]),
            )

        # Registrar evento de auditoría para trazabilidad inmutable
        recordAuditEvent(
            conn=conn,
            event_type="RECONCILIATION_NOTES_UPDATED",
            aggregate_type="RECONCILIATION",
            aggregate_id=reconciliation_id,
            user_id=user_id.strip(),
            payload={
                "reconciliation_id": reconciliation_id,
                "old_notes": old_notes,
                "new_notes": new_notes.strip(),
                "updated_by": user_id.strip(),
                "updated_at": now_utc,
            },
            occurred_at=now_utc,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()

    return getReconciliation(conn, reconciliation_id)


def getPendingTransfersForFund(conn: sqlite3.Connection, fund_code: str) -> List[Dict[str, Any]]:
    """Retorna los traslados en tránsito ('PENDING_RECEIPT') que afectan a un fondo (origen o destino)."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT t.transfer_id, t.origin_fund_id, t.destination_fund_id,
               fo.codigo as origin_fund_code, fo.nombre as origin_fund_name,
               fd.codigo as destination_fund_code, fd.nombre as destination_fund_name,
               t.amount_cents, t.dispatched_by, t.dispatched_at, t.reason, t.out_movement_id
        FROM tesoreria_traslados t
        JOIN tesoreria_fondos fo ON t.origin_fund_id = fo.id
        JOIN tesoreria_fondos fd ON t.destination_fund_id = fd.id
        WHERE t.status = 'PENDING_RECEIPT'
          AND (fo.codigo = ? OR fd.codigo = ?)
        ORDER BY t.id ASC
        """,
        (fund_code, fund_code),
    )
    rows = cursor.fetchall()
    cursor.close()
    result = []
    for r in rows:
        d = dict(r)
        d["amount_formatted"] = formatMoney(d["amount_cents"])
        d["es_origen"] = d["origin_fund_code"] == fund_code
        d["es_destino"] = d["destination_fund_code"] == fund_code
        result.append(d)
    return result


def reconcilePhysicalFund(
    conn: sqlite3.Connection,
    fund_code: str,
    verified_amount_cents: int,
    user_id: str,
    shift_name: Optional[str] = None,
    notes: Optional[str] = None,
    create_difference_case: bool = True,
    investigation_notes: Optional[str] = None,
    reconciliation_type: str = "TOTAL",
    authorized_by: Optional[str] = None,
    authorization_notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Flujo de arqueo físico exclusivo para fondos en efectivo (1110, 1120, 1130, 1140, 1150)."""
    if fund_code in FONDOS_DIGITALES:
        raise ErrorArqueo(
            f"El fondo {fund_code} es una cuenta digital; debe conciliarse mediante el procedimiento "
            f"de conciliación digital, no mediante arqueo físico."
        )
    if fund_code not in FONDOS_FISICOS:
        raise ErrorArqueo(
            f"El fondo '{fund_code}' no es un fondo físico autorizado para arqueo en efectivo. "
            f"Fondos físicos válidos: {', '.join(sorted(FONDOS_FISICOS))}."
        )

    fondo = obtener_fondo_por_codigo(conn, fund_code)
    saldo_registrado = fondo["saldo_centavos"] if fondo else 0
    diferencia = subtractMoney(verified_amount_cents, saldo_registrado)
    clean_obs = (notes.strip() if notes and notes.strip() else None) or (
        investigation_notes.strip() if investigation_notes and investigation_notes.strip() else None
    )
    if diferencia != 0 and not clean_obs:
        raise ErrorArqueo("Es obligatorio ingresar una observación cuando el arqueo presenta diferencias (faltante o sobrante).")

    full_notes = notes or ""
    if shift_name and shift_name.strip():
        full_notes = f"[TURNO: {shift_name.strip()}] {full_notes}".strip()

    recon = createReconciliation(
        conn=conn,
        fund_code=fund_code,
        reconciliation_type=reconciliation_type,
        verified_amount_cents=verified_amount_cents,
        user_id=user_id,
        notes=full_notes if full_notes else None,
        create_difference_case=create_difference_case,
        investigation_notes=investigation_notes,
        authorized_by=authorized_by,
        authorization_notes=authorization_notes,
    )
    recon["naturaleza"] = "FÍSICO"
    recon["is_digital"] = False
    recon["shift_name"] = shift_name
    recon["turno"] = shift_name
    recon["estado_revision"] = "CON DIFERENCIA" if recon["diferencia_cents"] != 0 else "CONCILIADO"
    recon["tipo_diferencia"] = "FALTANTE" if recon["diferencia_cents"] < 0 else ("SOBRANTE" if recon["diferencia_cents"] > 0 else "CONFORME")
    return recon


def reconcileDigitalAccount(
    conn: sqlite3.Connection,
    fund_code: str,
    declared_amount_cents: int,
    user_id: str,
    evidence_reference: Optional[str] = None,
    consultation_timestamp: Optional[str] = None,
    notes: Optional[str] = None,
    investigation_notes: Optional[str] = None,
    reconciliation_type: str = "TOTAL",
) -> Dict[str, Any]:
    """Procedimiento diferenciado de conciliación de cuentas digitales (1210, 1220, 1230)."""
    if fund_code in FONDOS_FISICOS:
        raise ErrorArqueo(
            f"El fondo {fund_code} es un fondo físico; debe arquearse mediante conteo de efectivo, "
            f"no mediante conciliación digital."
        )
    if fund_code not in FONDOS_DIGITALES:
        raise ErrorArqueo(
            f"El fondo '{fund_code}' no es una cuenta digital autorizada para conciliación. "
            f"Cuentas digitales válidas: {', '.join(sorted(FONDOS_DIGITALES))}."
        )

    now_iso = consultation_timestamp or datetime.now(timezone.utc).isoformat()
    evid = evidence_reference.strip() if evidence_reference and evidence_reference.strip() else "NO_SUMINISTRADA"
    obs = notes.strip() if notes and notes.strip() else "Sin observaciones adicionales."

    full_notes = (
        f"[CONCILIACIÓN DIGITAL DECLARADA] "
        f"Evidencia: {evid} | Consultado: {now_iso} | "
        f"Dato declarado manualmente por el operador (sin lectura automática). Obs: {obs}"
    )

    recon = createReconciliation(
        conn=conn,
        fund_code=fund_code,
        reconciliation_type=reconciliation_type,
        verified_amount_cents=declared_amount_cents,
        user_id=user_id,
        notes=full_notes,
        create_difference_case=True if investigation_notes else False,
        investigation_notes=investigation_notes,
    )
    recon["naturaleza"] = "DIGITAL"
    recon["is_digital"] = True
    recon["declared_manually"] = True
    recon["evidence_reference"] = evid
    recon["consultation_timestamp"] = now_iso
    recon["estado_revision"] = "CON DIFERENCIA" if recon["diferencia_cents"] != 0 else "CONCILIADO"
    return recon


def getReconciliationHistory(
    conn: sqlite3.Connection,
    fund_code: Optional[str] = None,
    journal_id: Optional[int] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Consulta histórica de arqueos realizados."""
    query = """
        SELECT a.id, a.reconciliation_id, a.journal_id, a.fund_id,
               f.codigo as fund_code, f.nombre as fund_name, f.naturaleza,
               a.reconciliation_type, a.status,
               a.saldo_registrado_cents, a.saldo_verificado_cents, a.diferencia_cents,
               a.currency, a.performed_by, a.performed_at, a.notes, a.difference_case_id,
               a.authorized_by, a.authorized_at, a.authorization_notes
        FROM tesoreria_arqueos a
        JOIN tesoreria_fondos f ON a.fund_id = f.id
        WHERE 1=1
    """
    params: List[Any] = []
    if fund_code:
        query += " AND f.codigo = ?"
        params.append(fund_code)
    if journal_id:
        query += " AND a.journal_id = ?"
        params.append(journal_id)

    query += " ORDER BY a.id DESC LIMIT ?"
    params.append(limit)

    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()

    result = []
    for r in rows:
        d = dict(r)
        d["saldo_registrado_formatted"] = formatMoney(d["saldo_registrado_cents"])
        d["saldo_verificado_formatted"] = formatMoney(d["saldo_verificado_cents"])
        d["diferencia_formatted"] = formatMoney(d["diferencia_cents"])
        d["is_digital"] = d["fund_code"] in FONDOS_DIGITALES or "[CONCILIACIÓN DIGITAL" in (d.get("notes") or "")
        d["estado_revision"] = "CON DIFERENCIA" if d["diferencia_cents"] != 0 else "CONCILIADO"
        d["is_authorized"] = bool(d.get("authorized_by") and str(d["authorized_by"]).strip().lower() in ADMIN_USERS)
        d["tipo_diferencia"] = "FALTANTE" if d["diferencia_cents"] < 0 else ("SOBRANTE" if d["diferencia_cents"] > 0 else "CONFORME")
        result.append(d)
    return result


def createDifferenceCase(
    conn: sqlite3.Connection,
    reconciliation_id: str,
    investigation_notes: str,
    user_id: str,
) -> Dict[str, Any]:
    """Abre formalmente un expediente de investigación de diferencias."""
    if not investigation_notes or not investigation_notes.strip():
        raise ErrorArqueo("Las notas de investigación son obligatorias para abrir un expediente.")

    recon = getReconciliation(conn, reconciliation_id)
    if not recon:
        raise ErrorArqueo(f"Arqueo {reconciliation_id} no encontrado.")

    if recon["diferencia_cents"] == 0:
        raise ErrorArqueo("No se puede abrir expediente de diferencias si la diferencia es cero.")

    case_id = str(uuid.uuid4())
    now_utc = datetime.now(timezone.utc).isoformat()

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO tesoreria_expedientes_diferencias (
            case_id, reconciliation_id, journal_id, fund_id,
            difference_cents, status, investigation_notes,
            created_by, created_at, updated_at, version
        ) VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, 1)
        """,
        (
            case_id,
            reconciliation_id,
            recon["journal_id"],
            recon["fund_id"],
            recon["diferencia_cents"],
            investigation_notes.strip(),
            user_id.strip(),
            now_utc,
            now_utc,
        ),
    )

    # Vincular al arqueo
    cursor.execute(
        """
        UPDATE tesoreria_arqueos
        SET difference_case_id = ?, status = 'UNDER_REVIEW'
        WHERE reconciliation_id = ?
        """,
        (case_id, reconciliation_id),
    )

    case_dict = {
        "case_id": case_id,
        "reconciliation_id": reconciliation_id,
        "journal_id": recon["journal_id"],
        "fund_id": recon["fund_id"],
        "fund_code": recon["fund_code"],
        "difference_cents": recon["diferencia_cents"],
        "difference_formatted": formatMoney(recon["diferencia_cents"]),
        "status": "OPEN",
        "investigation_notes": investigation_notes.strip(),
        "created_by": user_id.strip(),
        "created_at": now_utc,
    }

    recordAuditEvent(
        conn=conn,
        event_type="DIFFERENCE_CASE_OPENED",
        aggregate_type="DIFFERENCE_CASE",
        aggregate_id=case_id,
        user_id=user_id.strip(),
        payload=case_dict,
        occurred_at=now_utc,
    )

    enqueueOutboxEvent(
        conn=conn,
        event_type="DIFFERENCE_CASE_OPENED",
        aggregate_id=case_id,
        payload=case_dict,
    )

    conn.commit()
    cursor.close()
    return case_dict


def resolveDifferenceCase(
    conn: sqlite3.Connection,
    case_id: str,
    resolution_notes: str,
    evidence_reference: str,
    resolved_by: str,
) -> Dict[str, Any]:
    """Cierra y documenta formalmente la resolución de un expediente de diferencias.

    Invariantes:
    - Exige justificación exhaustiva, evidencia de respaldo y usuario autorizante.
    - No inventa asientos ficticios ni descuenta a personas sin sustento.
    - Actualiza el estado a 'RESOLVED' y sincroniza el arqueo asociado.
    """
    if not resolution_notes or not resolution_notes.strip():
        raise ErrorArqueo("Las notas de resolución y justificación son obligatorias.")

    if not evidence_reference or not evidence_reference.strip():
        raise ErrorArqueo("La referencia de evidencia documental es obligatoria para la resolución.")

    if not resolved_by or not resolved_by.strip():
        raise ErrorArqueo("La identidad del usuario responsable de la resolución es obligatoria.")

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, case_id, reconciliation_id, journal_id, fund_id,
               difference_cents, status, investigation_notes, version
        FROM tesoreria_expedientes_diferencias
        WHERE case_id = ?
        """,
        (case_id,),
    )
    row = cursor.fetchone()
    if not row:
        cursor.close()
        raise ErrorArqueo(f"Expediente de diferencia {case_id} no encontrado.")

    if row["status"] == "RESOLVED":
        cursor.close()
        raise ErrorArqueo(f"El expediente {case_id} ya se encuentra resuelto.")

    now_utc = datetime.now(timezone.utc).isoformat()
    new_version = row["version"] + 1

    cursor.execute(
        """
        UPDATE tesoreria_expedientes_diferencias
        SET status = 'RESOLVED',
            resolution_notes = ?,
            resolution_evidence = ?,
            resolved_by = ?,
            resolved_at = ?,
            updated_at = ?,
            version = ?
        WHERE case_id = ?
        """,
        (
            resolution_notes.strip(),
            evidence_reference.strip(),
            resolved_by.strip(),
            now_utc,
            now_utc,
            new_version,
            case_id,
        ),
    )

    # Actualizar estado del arqueo
    cursor.execute(
        """
        UPDATE tesoreria_arqueos
        SET status = 'RESOLVED'
        WHERE reconciliation_id = ?
        """,
        (row["reconciliation_id"],),
    )

    case_dict = {
        "case_id": case_id,
        "reconciliation_id": row["reconciliation_id"],
        "status": "RESOLVED",
        "difference_cents": row["difference_cents"],
        "difference_formatted": formatMoney(row["difference_cents"]),
        "resolution_notes": resolution_notes.strip(),
        "resolution_evidence": evidence_reference.strip(),
        "resolved_by": resolved_by.strip(),
        "resolved_at": now_utc,
        "version": new_version,
    }

    recordAuditEvent(
        conn=conn,
        event_type="DIFFERENCE_CASE_RESOLVED",
        aggregate_type="DIFFERENCE_CASE",
        aggregate_id=case_id,
        user_id=resolved_by.strip(),
        payload=case_dict,
        occurred_at=now_utc,
    )

    enqueueOutboxEvent(
        conn=conn,
        event_type="DIFFERENCE_CASE_RESOLVED",
        aggregate_id=case_id,
        payload=case_dict,
    )

    conn.commit()
    cursor.close()
    return case_dict


def getDifferenceCases(
    conn: sqlite3.Connection,
    fund_code: Optional[str] = None,
    journal_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Listado de expedientes de diferencias."""
    query = """
        SELECT ed.id, ed.case_id, ed.reconciliation_id, ed.journal_id, ed.fund_id,
               f.codigo as fund_code, f.nombre as fund_name,
               ed.difference_cents, ed.status, ed.investigation_notes,
               ed.resolution_notes, ed.resolution_evidence, ed.resolved_by, ed.resolved_at,
               ed.created_by, ed.created_at, ed.updated_at, ed.version,
               ed.authorized_by, ed.authorized_at, ed.authorization_notes
        FROM tesoreria_expedientes_diferencias ed
        JOIN tesoreria_fondos f ON ed.fund_id = f.id
        WHERE 1=1
    """
    params: List[Any] = []
    if fund_code:
        query += " AND f.codigo = ?"
        params.append(fund_code)
    if journal_id:
        query += " AND ed.journal_id = ?"
        params.append(journal_id)
    if status:
        query += " AND ed.status = ?"
        params.append(status)

    query += " ORDER BY ed.id DESC LIMIT ?"
    params.append(limit)

    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()

    result = []
    for r in rows:
        d = dict(r)
        d["difference_formatted"] = formatMoney(d["difference_cents"])
        d["is_authorized"] = bool(d.get("authorized_by") and str(d["authorized_by"]).strip().lower() in ADMIN_USERS)
        d["tipo_diferencia"] = "FALTANTE" if d["difference_cents"] < 0 else ("SOBRANTE" if d["difference_cents"] > 0 else "CONFORME")
        result.append(d)
    return result
