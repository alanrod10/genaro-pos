"""Prevalidación e importación controlada de DB_CARGAS.

No abre conexiones por sí mismo. La conexión debe ser inyectada.
La identidad legacy_row_id identifica una fila dentro de una exportación
concreta; no es una clave permanente entre exportaciones modificadas.
"""
import csv
import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from db.normalization import parse_moneda_pesos, argentina_to_utc_str


COLUMNAS_ORIGEN = (
    "FECHA",
    "SERVICIO",
    "MONTO_CARGA",
    "MONTO_ADICIONAL",
    "TOTAL_COBRADO",
    "PAGO_EFVO",
    "PAGO_TRANSF",
)

COLUMNAS_MONETARIAS = (
    "MONTO_CARGA",
    "MONTO_ADICIONAL",
    "TOTAL_COBRADO",
    "PAGO_EFVO",
    "PAGO_TRANSF",
)

ZONA_JUJUY = ZoneInfo("America/Argentina/Jujuy")


class ErrorImportacionCargas(ValueError):
    """Error de validación o consistencia del proceso de cargas."""


class ConflictoIdentidadOrigen(ErrorImportacionCargas):
    """Una referencia de origen existente tiene un contenido diferente."""


class ConflictoSnapshotCargas(ErrorImportacionCargas):
    """El CSV no coincide con el lote histórico registrado."""


@dataclass
class ResultadoAuditoria:
    sha256: str
    total_filas: int
    validas: list
    cuarentena: list
    grupos_duplicados: list
    formatos_fecha: dict


def _parse_fecha_local(valor):
    texto = (valor or "").strip()
    if not texto:
        raise ValueError("FECHA_VACIA")

    # Aceptar seriales de fecha/hora de Excel o Google Sheets.
    try:
        serial = float(texto)
        if 20000 <= serial <= 60000:
            dt = datetime(1899, 12, 30) + timedelta(days=serial)
            return dt.replace(tzinfo=ZONA_JUJUY), "serial"
    except ValueError:
        pass

    formatos = (
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    )
    for formato in formatos:
        try:
            dt = datetime.strptime(texto, formato)
            return dt.replace(tzinfo=ZONA_JUJUY), formato
        except ValueError:
            continue

    raise ValueError("FECHA_FORMATO_NO_RECONOCIDO")


def prevalidar_cargas(csv_path):
    """Audita un CSV sin escribir datos ni conectar con servicios externos."""
    ruta = Path(csv_path)
    if not ruta.is_file():
        raise FileNotFoundError(f"No existe el CSV: {ruta}")

    contenido = ruta.read_bytes()
    sha = hashlib.sha256(contenido).hexdigest()

    validas = []
    cuarentena = []
    contador_duplicados = defaultdict(list)
    formatos_fecha = {}
    total_filas = 0

    with ruta.open("r", encoding="utf-8-sig", newline="") as archivo:
        muestra = archivo.read(8192)
        archivo.seek(0)
        try:
            dialecto = csv.Sniffer().sniff(muestra, delimiters=",;\t")
        except csv.Error:
            dialecto = csv.excel

        lector = csv.DictReader(archivo, dialect=dialecto)
        encabezados_originales = lector.fieldnames or []
        encabezados = [h.strip().upper() for h in encabezados_originales]

        if len(encabezados) != len(set(encabezados)):
            raise ErrorImportacionCargas(
                "Encabezados duplicados después de normalizarlos."
            )

        faltantes = sorted(set(COLUMNAS_ORIGEN) - set(encabezados))
        if faltantes:
            raise ErrorImportacionCargas(
                f"Faltan columnas obligatorias: {faltantes}"
            )

        renombres = dict(zip(encabezados_originales, encabezados))

        for numero_registro, fila_csv in enumerate(lector, start=1):
            fila = {
                renombres[k]: (v or "").strip()
                for k, v in fila_csv.items()
                if k is not None and k in renombres
            }

            if not any(fila.values()):
                continue

            total_filas += 1
            numero_origen = numero_registro
            problemas = []
            fecha_utc = None

            extras = fila_csv.get(None)
            if extras and any(str(x).strip() for x in extras):
                problemas.append("COLUMNAS_EXTRA")

            try:
                fecha_local, formato = _parse_fecha_local(fila.get("FECHA", ""))
                fecha_utc = argentina_to_utc_str(fecha_local)
                formatos_fecha[formato] = formatos_fecha.get(formato, 0) + 1
            except ValueError as exc:
                problemas.append(str(exc))

            montos = {}
            for campo in COLUMNAS_MONETARIAS:
                texto = fila.get(campo, "")
                if not texto:
                    problemas.append(f"IMPORTE_VACIO:{campo}")
                    continue

                monto, valido = parse_moneda_pesos(texto)
                if not valido:
                    problemas.append(f"IMPORTE_INVALIDO:{campo}")
                else:
                    montos[campo] = monto

            servicio = fila.get("SERVICIO", "").strip()
            if not servicio:
                problemas.append("SERVICIO_VACIO")

            if len(montos) == len(COLUMNAS_MONETARIAS):
                carga = montos["MONTO_CARGA"]
                adicional = montos["MONTO_ADICIONAL"]
                total = montos["TOTAL_COBRADO"]
                efectivo = montos["PAGO_EFVO"]
                transferencia = montos["PAGO_TRANSF"]

                if carga <= 0:
                    problemas.append("CARGA_NO_POSITIVA")
                if adicional < 0:
                    problemas.append("ADICIONAL_NEGATIVO")
                if efectivo < 0 or transferencia < 0:
                    problemas.append("PAGO_NEGATIVO")
                if carga + adicional != total:
                    problemas.append("TOTAL_NO_COINCIDE")
                if efectivo + transferencia != total:
                    problemas.append("BALANCE_PAGOS_INCONSISTENTE")

            # Se identifican duplicados por contenido original, pero
            # cada ocurrencia mantiene su propio número de registro.
            clave = tuple(fila.get(c, "") for c in COLUMNAS_ORIGEN)
            clave_json = json.dumps(clave, ensure_ascii=False, separators=(",", ":"))
            huella_fila = hashlib.sha256(clave_json.encode("utf-8")).hexdigest()
            contador_duplicados[huella_fila].append(numero_origen)

            referencia = f"DB_CARGAS:{sha}:{numero_origen:06d}"

            if problemas:
                cuarentena.append({
                    **fila,
                    "_SOURCE_RECORD_NUMBER": numero_origen,
                    "_SOURCE_SHA256": sha,
                    "_QUARANTINE_REASON": ";".join(problemas),
                    "_DIFERENCIA_TOTAL_MENOS_PAGOS": (
                        montos.get("TOTAL_COBRADO", 0)
                        - montos.get("PAGO_EFVO", 0)
                        - montos.get("PAGO_TRANSF", 0)
                        if "BALANCE_PAGOS_INCONSISTENTE" in problemas
                        and len(montos) == len(COLUMNAS_MONETARIAS)
                        else ""
                    ),
                })
                continue

            validas.append({
                "legacy_row_id": referencia,
                "source_sha256": sha,
                "source_record_number": numero_origen,
                "fecha_hora_utc": fecha_utc,
                "servicio": servicio,
                "monto_carga": montos["MONTO_CARGA"],
                "monto_adicional": montos["MONTO_ADICIONAL"],
                "total_cobrado": montos["TOTAL_COBRADO"],
                "pago_efectivo": montos["PAGO_EFVO"],
                "pago_transferencia": montos["PAGO_TRANSF"],
            })

    grupos_duplicados = [
        {
            "grupo_id": grupo_id,
            "registros_origen": numeros,
            "cantidad": len(numeros),
        }
        for grupo_id, numeros in contador_duplicados.items()
        if len(numeros) > 1
    ]

    return ResultadoAuditoria(
        sha256=sha,
        total_filas=total_filas,
        validas=validas,
        cuarentena=cuarentena,
        grupos_duplicados=grupos_duplicados,
        formatos_fecha=formatos_fecha,
    )


def exportar_informes(resultado, cuarentena_csv, duplicados_csv):
    """Exporta los reportes locales de revisión; no modifica el CSV fuente."""
    campos_cuarentena = list(COLUMNAS_ORIGEN) + [
        "_SOURCE_RECORD_NUMBER",
        "_SOURCE_SHA256",
        "_QUARANTINE_REASON",
        "_DIFERENCIA_TOTAL_MENOS_PAGOS",
    ]
    with Path(cuarentena_csv).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=campos_cuarentena, extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(resultado.cuarentena)

    campos_duplicados = [
        "GRUPO_ID", "TAMANO_GRUPO", "REGISTROS_ORIGEN",
    ]
    with Path(duplicados_csv).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=campos_duplicados)
        writer.writeheader()
        for grupo in resultado.grupos_duplicados:
            writer.writerow({
                "GRUPO_ID": grupo["grupo_id"],
                "TAMANO_GRUPO": grupo["cantidad"],
                "REGISTROS_ORIGEN": ",".join(
                    map(str, grupo["registros_origen"])
                ),
            })


def _verificar_esquema(conn):
    tablas = {
        fila[0]
        for fila in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "cargas_servicios" not in tablas:
        raise ErrorImportacionCargas(
            "No existe cargas_servicios en la base conectada."
        )
    if "cargas_import_batch" not in tablas:
        raise ErrorImportacionCargas(
            "No existe cargas_import_batch; falta aplicar la migración local "
            "del control de lote."
        )

    indices = conn.execute(
        "PRAGMA index_list('cargas_servicios')"
    ).fetchall()
    indice_valido = any(
        fila[1] == "uq_cargas_legacy_row_id" and fila[2] == 1
        for fila in indices
    )
    if not indice_valido:
        raise ErrorImportacionCargas(
            "Falta el índice único uq_cargas_legacy_row_id."
        )


def _registro_existente(conn, registro):
    return conn.execute(
        """
        SELECT fecha_hora_utc, servicio, monto_carga,
               monto_adicional, total_cobrado,
               pago_efectivo, pago_transferencia
        FROM cargas_servicios
        WHERE legacy_row_id = ?
        """,
        (registro["legacy_row_id"],),
    ).fetchone()


def _payload(registro):
    return (
        registro["fecha_hora_utc"],
        registro["servicio"],
        registro["monto_carga"],
        registro["monto_adicional"],
        registro["total_cobrado"],
        registro["pago_efectivo"],
        registro["pago_transferencia"],
    )




def _validar_snapshot_destino(conn, auditoria):
    """Impide mezclar snapshots históricos sin reconciliación explícita."""
    lote = conn.execute(
        """
        SELECT source_sha256, filas_origen, filas_importadas,
               filas_cuarentena, grupos_duplicados
        FROM cargas_import_batch
        WHERE singleton = 1
        """
    ).fetchone()

    if lote is None:
        total_existente = conn.execute(
            "SELECT COUNT(*) FROM cargas_servicios"
        ).fetchone()[0]
        if total_existente:
            raise ConflictoSnapshotCargas(
                "Hay cargas existentes sin lote de importación registrado. "
                "Se requiere reconciliación antes de continuar."
            )
        return None

    if lote[0] != auditoria.sha256:
        raise ConflictoSnapshotCargas(
            "El CSV tiene un SHA-256 distinto del lote ya registrado. "
            "No se importará otro snapshot hasta implementar reconciliación."
        )

    esperado = (
        auditoria.total_filas,
        len(auditoria.validas),
        len(auditoria.cuarentena),
        len(auditoria.grupos_duplicados),
    )
    registrado = tuple(lote[1:])
    if registrado != esperado:
        raise ConflictoSnapshotCargas(
            "Los conteos del CSV no coinciden con el lote registrado."
        )

    prefijo = f"DB_CARGAS:{auditoria.sha256}:"
    cantidad = conn.execute(
        """
        SELECT COUNT(*)
        FROM cargas_servicios
        WHERE substr(legacy_row_id, 1, ?) = ?
        """,
        (len(prefijo), prefijo),
    ).fetchone()[0]

    if cantidad != lote[2]:
        raise ConflictoSnapshotCargas(
            "El número de filas del lote no coincide con las filas guardadas. "
            "Se requiere reconciliación antes de reintentar."
        )

    return lote


def migrar_cargas(
    csv_path,
    *,
    conn,
    dry_run=True,
    confirmar_cuarentena=False,
):
    """Importa snapshot auditado con consultas de identidad e inserciones por lotes."""
    auditoria = prevalidar_cargas(csv_path)
    _verificar_esquema(conn)

    if not dry_run and auditoria.cuarentena and not confirmar_cuarentena:
        raise ErrorImportacionCargas(
            "Importación cancelada: hay filas en cuarentena. "
            "Se requiere una decisión explícita para el tratamiento histórico."
        )

    pendientes = []
    existentes = 0

    def planificar():
        nonlocal existentes
        pendientes.clear()
        existentes = 0

        filas = conn.execute("""
            SELECT legacy_row_id, fecha_hora_utc, servicio,
                   monto_carga, monto_adicional, total_cobrado,
                   pago_efectivo, pago_transferencia
            FROM cargas_servicios
            WHERE legacy_row_id IS NOT NULL
        """).fetchall()

        por_id = {}
        for fila in filas:
            legacy_id = fila[0]
            if legacy_id in por_id:
                raise ConflictoIdentidadOrigen(
                    "Hay identificadores de origen duplicados en destino: "
                    + str(legacy_id)
                )
            por_id[legacy_id] = tuple(fila[1:])

        for registro in auditoria.validas:
            legacy_id = registro["legacy_row_id"]
            actual = por_id.get(legacy_id)

            if actual is None:
                pendientes.append(registro)
            elif actual == _payload(registro):
                existentes += 1
            else:
                raise ConflictoIdentidadOrigen(
                    "La referencia de origen ya existe con contenido diferente: "
                    + legacy_id
                )

    insertadas = 0

    if dry_run:
        _validar_snapshot_destino(conn, auditoria)
        planificar()
    else:
        conn.execute("BEGIN IMMEDIATE")
        try:
            lote = _validar_snapshot_destino(conn, auditoria)
            planificar()

            tamano_lote = 50
            columnas = """
                legacy_row_id, fecha_hora_utc, servicio,
                monto_carga, monto_adicional, total_cobrado,
                pago_efectivo, pago_transferencia
            """
            placeholder = "(" + ", ".join(["?"] * 8) + ")"
            total_lotes = (
                len(pendientes) + tamano_lote - 1
            ) // tamano_lote

            for inicio in range(0, len(pendientes), tamano_lote):
                grupo = pendientes[inicio:inicio + tamano_lote]
                parametros = []

                for registro in grupo:
                    parametros.extend((
                        registro["legacy_row_id"],
                        registro["fecha_hora_utc"],
                        registro["servicio"],
                        registro["monto_carga"],
                        registro["monto_adicional"],
                        registro["total_cobrado"],
                        registro["pago_efectivo"],
                        registro["pago_transferencia"],
                    ))

                sql = (
                    f"INSERT INTO cargas_servicios ({columnas}) VALUES "
                    + ", ".join([placeholder] * len(grupo))
                )

                numero_lote = inicio // tamano_lote + 1
                print(
                    f"Lote SQL {numero_lote}/{total_lotes}: "
                    f"{len(grupo)} filas; transacción sin confirmar.",
                    flush=True,
                )
                conn.execute(sql, tuple(parametros))
                insertadas += len(grupo)

            if lote is None:
                conn.execute("""
                    INSERT INTO cargas_import_batch (
                        singleton, source_sha256, filas_origen,
                        filas_importadas, filas_cuarentena, grupos_duplicados
                    ) VALUES (1, ?, ?, ?, ?, ?)
                """, (
                    auditoria.sha256,
                    auditoria.total_filas,
                    insertadas,
                    len(auditoria.cuarentena),
                    len(auditoria.grupos_duplicados),
                ))

            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {
        "sha256": auditoria.sha256,
        "filas_origen": auditoria.total_filas,
        "validas": len(auditoria.validas),
        "cuarentena": len(auditoria.cuarentena),
        "grupos_duplicados": len(auditoria.grupos_duplicados),
        "filas_en_grupos_duplicados": sum(
            g["cantidad"] for g in auditoria.grupos_duplicados
        ),
        "existentes_con_mismo_contenido": existentes,
        "pendientes": len(pendientes),
        "insertadas": insertadas,
        "dry_run": dry_run,
        "auditoria": auditoria,
    }

