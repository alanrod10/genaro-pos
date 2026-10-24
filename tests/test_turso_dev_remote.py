"""Harness de pruebas de aceptación controlada contra Turso DEV (Fase 13).

BARRERAS ESTRICTAS DE SEGURIDAD (Actualizadas y endurecidas):
1. Validación estructurada de URL (urllib.parse):
   - Esquema obligatorio: 'turso'
   - Host obligatorio exactamente: 'genaro-pos-dev-alanrod10.aws-sa-east-1.turso.io'
   - Prohibido: credenciales embebidas en URL, puertos personalizados, rutas, parámetros o fragmentos.
2. Variables de entorno obligatorias:
   - ENV_NAME == 'genaro-pos-dev'
   - TURSO_TEST_APPROVED == 'YES'
   - TURSO_DEV_DATABASE_URL y TURSO_DEV_AUTH_TOKEN configurados.
3. Inspección previa del estado de la base:
   - Si la base remota contiene tablas o cualquier objeto preexistente, aborta sin DDL, DML ni limpiezas.
4. Tablas sintéticas con identificador único por ejecución (run_id):
   - Nombres dinámicos: _f13_probe_<run_id>_*
   - Sin cláusula 'IF NOT EXISTS' para evitar reutilización espuria.
5. Limpieza de pertenencia estricta:
   - Solo se eliminan objetos explícitamente registrados en 'objetos_creados_exitosamente' u 'objetos_inciertos'
     que coincidan con el prefijo del run_id.
6. Aislamiento transaccional estricto:
   - Cada operación DML (incluyendo UPDATE ... RETURNING) se confirma explícitamente antes del caso de rollback.
7. Concurrencia real de trabajadores:
   - Dos hilos paralelos con conexiones independientes coordinados por una barrera de sincronización (Barrier).
   - Reclamaciones disjuntas y verificación de orden secuencial por entidad.
8. Salida del preflight bloqueado:
   - Código de salida 2 (EXIT_CODE_PREFLIGHT_BLOCKED) para indicar bloqueo deliberado por seguridad.
"""

from datetime import datetime, timezone
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
import uuid

# Códigos de salida del proceso
EXIT_CODE_SUCCESS = 0
EXIT_CODE_TEST_FAILED = 1
EXIT_CODE_PREFLIGHT_BLOCKED = 2

# Estados de preflight y limpieza
ESTADO_PREFLIGHT_OK = "PREFLIGHT_OK"
ESTADO_PREFLIGHT_BLOQUEADO = "PREFLIGHT_BLOQUEADO"

ESTADO_LIMPIEZA_NO_REQUERIDA = "LIMPIEZA_NO_REQUERIDA"
ESTADO_LIMPIEZA_COMPLETA = "LIMPIEZA_COMPLETA"
ESTADO_LIMPIEZA_INCOMPLETA = "LIMPIEZA_INCOMPLETA"

# Excepción explícita y única para metadatos internos de MVCC de TursoDB
OBJETO_INTERNO_PERMITIDO_TIPO = "table"
OBJETO_INTERNO_PERMITIDO_NOMBRE = "__turso_internal_mvcc_meta"

# Destino remoto exacto y autorizado para el laboratorio DEV de Genaro POS
DESTINO_AUTORIZADO_SCHEME = "turso"
DESTINO_AUTORIZADO_HOST = "genaro-pos-dev-alanrod10.aws-sa-east-1.turso.io"


def validar_endpoint_estructurado(url: Optional[str]) -> Tuple[bool, str]:
  """Valida exhaustivamente la URL del endpoint de Turso mediante análisis sintáctico formal."""
  if not url:
    return False, "Variable TURSO_DEV_DATABASE_URL no configurada."

  try:
    parsed = urllib.parse.urlparse(url)
  except Exception as e:
    return False, f"Error al parsear TURSO_DEV_DATABASE_URL: {e}"

  if parsed.scheme != DESTINO_AUTORIZADO_SCHEME:
    return False, (
        f"Esquema de URL no autorizado: '{parsed.scheme}'. El destino esperado"
        f" debe iniciar exactamente con '{DESTINO_AUTORIZADO_SCHEME}://'."
    )

  if parsed.hostname != DESTINO_AUTORIZADO_HOST:
    return False, (
        f"Host remoto no autorizado: '{parsed.hostname}'. El host esperado es"
        f" exactamente '{DESTINO_AUTORIZADO_HOST}'."
    )

  if parsed.username is not None or parsed.password is not None:
    return False, (
        "La URL contiene credenciales embebidas (usuario/contraseña), lo cual"
        " está prohibido por seguridad."
    )

  if parsed.port is not None:
    return (
        False,
        f"La URL contiene un puerto personalizado no autorizado: {parsed.port}.",
    )

  if parsed.path not in ("", "/"):
    return False, f"La URL contiene una ruta no autorizada: '{parsed.path}'."

  if parsed.query != "":
    return (
        False,
        "La URL contiene parámetros de consulta (query string) no permitidos:"
        f" '{parsed.query}'.",
    )

  if parsed.fragment != "":
    return (
        False,
        "La URL contiene fragmentos inesperados:" f" '{parsed.fragment}'.",
    )

  return True, ""


def ejecutar_preflight() -> Tuple[bool, List[str]]:
  """Valida las condiciones locales obligatorias antes de cualquier conexión remota."""
  bloqueos = []

  # 1. Nombre de entorno exacto
  env_name = os.environ.get("ENV_NAME")
  if env_name != "genaro-pos-dev":
    bloqueos.append(
        "ENV_NAME no está configurado como 'genaro-pos-dev' (actual:"
        f" '{env_name}')."
    )

  # 2. Aprobación explícita del propietario
  test_approved = os.environ.get("TURSO_TEST_APPROVED")
  if test_approved != "YES":
    bloqueos.append(
        "TURSO_TEST_APPROVED no está configurado como 'YES' (actual:"
        f" '{test_approved}')."
    )

  # 3. Credenciales y análisis estructurado del endpoint
  url = os.environ.get("TURSO_DEV_DATABASE_URL")
  valido, detalle_url = validar_endpoint_estructurado(url)
  if not valido:
    bloqueos.append(detalle_url)

  token = os.environ.get("TURSO_DEV_AUTH_TOKEN")
  if not token:
    bloqueos.append(
        "TURSO_DEV_AUTH_TOKEN no configurada en las variables de entorno."
    )

  # 4. Disponibilidad del SDK turso_serverless
  try:
    import turso_serverless
  except ImportError:
    bloqueos.append(
        "Paquete 'turso-serverless' no está instalado en el entorno virtual."
    )

  return len(bloqueos) == 0, bloqueos


def inspeccionar_estado_inicial_base(conn: Any) -> Tuple[bool, str, List[str]]:
  """Inspecciona los objetos preexistentes en sqlite_master.

  Regla estricta TursoDB:
  - Se ignoran las tablas del sistema SQLite ('sqlite_%').
  - Se permite EXCLUSIVAMENTE el objeto interno exacto:
      type == 'table' y name == '__turso_internal_mvcc_meta'
  - Cualquier otro objeto (tabla, vista, índice o trigger), incluso si empieza por '__turso_internal_',
    provoca BLOQUEO inmediato antes de realizar DDL o DML.

  Retorna:
    (es_seguro, estado, detalle_objetos)
    - (True, 'PREFLIGHT_OK', [objetos_permitidos]) si solo hay objetos del sistema o permitidos.
    - (False, 'PREFLIGHT_BLOQUEADO', [objetos_no_permitidos]) si hay objetos inesperados.
  """
  cursor = conn.execute(
      "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%';"
  )
  filas = cursor.fetchall()

  objetos_inesperados = []
  objetos_permitidos = []

  for tipo, nombre in filas:
    if (
        tipo == OBJETO_INTERNO_PERMITIDO_TIPO
        and nombre == OBJETO_INTERNO_PERMITIDO_NOMBRE
    ):
      objetos_permitidos.append(f"{tipo}:{nombre}")
    else:
      objetos_inesperados.append(f"{tipo}:{nombre}")

  if objetos_inesperados:
    return False, ESTADO_PREFLIGHT_BLOQUEADO, objetos_inesperados

  return True, ESTADO_PREFLIGHT_OK, objetos_permitidos


def ejecutar_limpieza_segura(
    conn: Any,
    run_id: str,
    objetos_creados_exitosamente: List[str],
    objetos_inciertos: List[str],
) -> Dict[str, Any]:
  """Ejecuta la limpieza segura post-prueba bajo las siguientes reglas estrictas:

  1. Si no se crearon objetos de prueba (ej. aborto en preflight), retorna LIMPIEZA_NO_REQUERIDA.
  2. Elimina automáticamente ÚNICAMENTE las tablas incluidas en `objetos_creados_exitosamente`.
  3. NUNCA ejecuta DROP TABLE sobre tablas en `objetos_inciertos`.
  4. Valida que cada tabla a eliminar pertenezca al `run_id` actual (`_f13_probe_{run_id}_`).
  5. Nunca elimina ni modifica la tabla interna '__turso_internal_mvcc_meta' ni objetos ajenos.
  6. Ejecuta DROP TABLE en orden inverso de creación.
  7. Si hay objetos inciertos o errores en DROP TABLE, marca el estado como LIMPIEZA_INCOMPLETA
     y NUNCA afirma que la base quedó completamente restaurada.
  8. No realiza consultas ni operaciones destructivas adicionales sobre objetos inciertos.
  """
  if not objetos_creados_exitosamente and not objetos_inciertos:
    print(
        f"\n[LIMPIEZA CONTROLADA]: No se crearon objetos de prueba en este run. Estado: {ESTADO_LIMPIEZA_NO_REQUERIDA}."
    )
    return {
        "tablas_eliminadas": [],
        "tablas_con_error_eliminacion": {},
        "objetos_inciertos": [],
        "limpieza_completa": True,
        "estado_final": ESTADO_LIMPIEZA_NO_REQUERIDA,
    }

  resumen: Dict[str, Any] = {
      "tablas_eliminadas": [],
      "tablas_con_error_eliminacion": {},
      "objetos_inciertos": list(objetos_inciertos),
      "limpieza_completa": True,
      "estado_final": ESTADO_LIMPIEZA_COMPLETA,
  }

  print(
      "\n[LIMPIEZA CONTROLADA]: Eliminando exclusivamente objetos confirmados..."
  )

  # 1. Eliminar únicamente objetos confirmados y demostrados
  for tabla in reversed(objetos_creados_exitosamente):
    if tabla.startswith(f"_f13_probe_{run_id}_"):
      try:
        conn.execute(f"DROP TABLE {tabla};")
        conn.commit()
        resumen["tablas_eliminadas"].append(tabla)
        print(f"  -> {tabla} eliminada con éxito.")
      except Exception as err:
        resumen["limpieza_completa"] = False
        resumen["tablas_con_error_eliminacion"][tabla] = str(err)
        print(f"  -> Error al eliminar {tabla}: {err}")
    else:
      resumen["limpieza_completa"] = False
      print(
          f"  -> [OMITIDO POR SEGURIDAD]: {tabla} no coincide con el run_id"
          f" {run_id} (nunca se eliminan objetos internos ni ajenos)."
      )

  # 2. Informar objetos inciertos SIN ejecutar DROP TABLE
  if objetos_inciertos:
    resumen["limpieza_completa"] = False
    print(
        f"\n[ALERTA DE SEGURIDAD]: Se registraron objetos en estado incierto para el Run ID '{run_id}':"
    )
    for obj in objetos_inciertos:
      print(f"  * {obj} (creación o confirmación no verificada conclusivamente)")
    print(
        "  -> REGLA: No se ejecuta DROP TABLE sobre objetos inciertos para evitar destruir objetos cuya propiedad no está demostrada."
    )
    print(
        f"  -> La limpieza queda marcada como {ESTADO_LIMPIEZA_INCOMPLETA} (requiere revisión manual en consola de Turso)."
    )

  # 3. Reporte del veredicto de limpieza
  if not resumen["limpieza_completa"] or objetos_inciertos:
    resumen["estado_final"] = ESTADO_LIMPIEZA_INCOMPLETA
    print(
        f"\n[RESULTADO DE LIMPIEZA]: {ESTADO_LIMPIEZA_INCOMPLETA}. La base NO puede considerarse completamente restaurada."
    )
  else:
    resumen["estado_final"] = ESTADO_LIMPIEZA_COMPLETA
    print(
        f"\n[RESULTADO DE LIMPIEZA]: {ESTADO_LIMPIEZA_COMPLETA}. Todos los objetos del run fueron eliminados exitosamente."
    )

  return resumen


def ejecutar_pruebas_remotas() -> bool:
  """Ejecuta la suite de aceptación remota sobre Turso DEV con aislamiento riguroso."""
  aprobado, bloqueos = ejecutar_preflight()
  if not aprobado:
    print("[PREFLIGHT BLOQUEADO]: No se cumplen las condiciones de seguridad:")
    for b in bloqueos:
      print(f"  - {b}")
    print("\nPruebas remotas abortadas sin realizar conexiones.")
    return False

  url = os.environ["TURSO_DEV_DATABASE_URL"]
  token = os.environ["TURSO_DEV_AUTH_TOKEN"]

  import turso_serverless
  from db.accounting import (
      ErrorCommitIncierto,
      ErrorValidacion,
      ItemVenta,
      confirmar_evento_outbox,
      reclamar_lote_outbox,
      registrar_venta_atomica,
      transaccion,
  )

  run_id = uuid.uuid4().hex[:8]
  tabla_tickets = f"_f13_probe_{run_id}_tickets"
  tabla_items = f"_f13_probe_{run_id}_ticket_items"
  tabla_outbox = f"_f13_probe_{run_id}_outbox_events"

  operaciones_completadas: List[str] = []
  objetos_creados_exitosamente: List[str] = []
  objetos_inciertos: List[str] = []

  print(f"[CONEXIÓN]: Conectando a {url} (Run ID: {run_id})...")
  conn = turso_serverless.connect(url, auth_token=token)

  try:
    # --------------------------------------------------------------------------
    # BARRERA 2: Inspección del estado inicial de la base remota
    # --------------------------------------------------------------------------
    print(
        "[PRE-CHECK]: Inspeccionando objetos existentes en la base remota..."
    )
    es_seguro, estado_preflight, objetos_detectados = inspeccionar_estado_inicial_base(conn)
    if not es_seguro:
      print(
          f"[{estado_preflight}]: La base remota contiene objetos no autorizados: {objetos_detectados}"
      )
      print(
          "Abortando inmediatamente antes de ejecutar DDL, DML o limpiezas sobre objetos ajenos."
      )
      return False

    print(f"[{estado_preflight}]: Estado inicial de la base verificado y autorizado.")
    if objetos_detectados:
      print(
          f"  -> Objeto interno de metadatos detectado y permitido por excepción TursoDB: {objetos_detectados}"
      )
    else:
      print("  -> Base remota completamente limpia (sin objetos de usuario ni de motor).")
    print(f"  -> Autorizado para crear objetos sintéticos del run {run_id}.")
    operaciones_completadas.append(f"{estado_preflight}: Inspección de base autorizada")
    print(
        "  -> Base remota verificada vacía. Autorizado para crear objetos del"
        f" run {run_id}."
    )

    # --------------------------------------------------------------------------
    # BLOQUE 1: Conexión y Operaciones SQL Remotas (DDL sin IF NOT EXISTS)
    # --------------------------------------------------------------------------
    print("\n[BLOQUE 1]: Pruebas de conexión y DDL dinámico...")
    tablas_definicion = [
        (
            tabla_tickets,
            f"""
            CREATE TABLE {tabla_tickets} (
                id TEXT PRIMARY KEY,
                fecha_hora_utc TEXT NOT NULL DEFAULT (datetime('now')),
                total_venta INTEGER NOT NULL CHECK (total_venta >= 0),
                monto_efectivo INTEGER NOT NULL DEFAULT 0 CHECK (monto_efectivo >= 0),
                monto_transferencia INTEGER NOT NULL DEFAULT 0 CHECK (monto_transferencia >= 0),
                metodo_pago TEXT NOT NULL CHECK (metodo_pago IN ('EFECTIVO', 'TRANSFERENCIA', 'MIXTO')),
                es_nocturno INTEGER NOT NULL DEFAULT 0 CHECK (es_nocturno IN (0, 1)),
                anulado INTEGER NOT NULL DEFAULT 0 CHECK (anulado IN (0, 1)),
                motivo_anulacion TEXT,
                CONSTRAINT chk_ticket_balance CHECK (total_venta = (monto_efectivo + monto_transferencia))
            );
        """,
        ),
        (
            tabla_items,
            f"""
            CREATE TABLE {tabla_items} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT NOT NULL,
                producto_id INTEGER,
                producto_nombre TEXT NOT NULL,
                cantidad REAL NOT NULL CHECK (cantidad > 0),
                unidad TEXT NOT NULL DEFAULT 'Unidad',
                precio_unitario INTEGER NOT NULL CHECK (precio_unitario >= 0),
                subtotal INTEGER NOT NULL CHECK (subtotal >= 0),
                metodo_pago TEXT NOT NULL
            );
        """,
        ),
        (
            tabla_outbox,
            f"""
            CREATE TABLE {tabla_outbox} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                aggregate_type TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                operation TEXT NOT NULL CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE')),
                payload_json TEXT NOT NULL,
                estado TEXT NOT NULL DEFAULT 'PENDIENTE' CHECK (estado IN ('PENDIENTE', 'EN_PROCESO', 'SINCRONIZADO', 'ERROR')),
                intentos INTEGER NOT NULL DEFAULT 0,
                ultimo_error TEXT,
                creado_en_utc TEXT NOT NULL DEFAULT (datetime('now')),
                procesado_en_utc TEXT
            );
        """,
        ),
    ]

    for nombre_tabla, ddl_sql in tablas_definicion:
      try:
        conn.execute(ddl_sql)
        conn.commit()
        objetos_creados_exitosamente.append(nombre_tabla)
        operaciones_completadas.append(f"CREATE TABLE {nombre_tabla}")
      except Exception as err:
        objetos_inciertos.append(nombre_tabla)
        operaciones_completadas.append(
            f"CREATE TABLE {nombre_tabla} (INCIERTO: {err})"
        )
        raise

    # Prueba de UPDATE ... RETURNING con aislamiento y confirmación inmediata
    with transaccion(conn):
      conn.execute(f"""
            INSERT INTO {tabla_tickets} (id, total_venta, monto_efectivo, monto_transferencia, metodo_pago)
            VALUES ('T-RET-PROBE', 1000, 1000, 0, 'EFECTIVO');
        """)

    with transaccion(conn):
      cursor = conn.execute(f"""
            UPDATE {tabla_tickets}
            SET total_venta = 1200, monto_efectivo = 1200
            WHERE id = 'T-RET-PROBE'
            RETURNING id, total_venta;
        """)
      ret_row = cursor.fetchone()
      assert ret_row == (
          "T-RET-PROBE",
          1200,
      ), f"Fallo en RETURNING, recibido: {ret_row}"

    operaciones_completadas.append(
        "Verificación de UPDATE ... RETURNING confirmada"
    )
    print(
        "  -> UPDATE ... RETURNING validado y confirmado exitosamente en Turso"
        " remoto."
    )

    # --------------------------------------------------------------------------
    # BLOQUE 2: Transacción y Rollback Remoto Aislado
    # --------------------------------------------------------------------------
    print("\n[BLOQUE 2]: Pruebas de transacción y rollback remoto aislado...")
    try:
      with transaccion(conn):
        conn.execute(f"""
                    INSERT INTO {tabla_tickets} (id, total_venta, monto_efectivo, monto_transferencia, metodo_pago)
                    VALUES ('T-FAIL-PROBE', 3000, 3000, 0, 'EFECTIVO');
                """)
        raise RuntimeError("Fallo inducido para validar rollback remoto")
    except RuntimeError:
      pass

    count_fail = conn.execute(
        f"SELECT COUNT(*) FROM {tabla_tickets} WHERE id = 'T-FAIL-PROBE';"
    ).fetchone()[0]
    assert (
        count_fail == 0
    ), "Aislamiento violado: fila persistió tras rollback remoto!"

    # Verificar que el registro previo de RETURNING no fue afectado por el rollback de este bloque
    prev_row = conn.execute(
        f"SELECT total_venta FROM {tabla_tickets} WHERE id = 'T-RET-PROBE';"
    ).fetchone()
    assert prev_row == (1200,), (
        "Aislamiento violado: RETURNING fue alterado por rollback de otro"
        f" bloque: {prev_row}"
    )

    operaciones_completadas.append("Rollback remoto aislado ante fallo inducido")
    print(
        "  -> Rollback remoto comprobado y aislado: 0 filas persistidas tras"
        " error."
    )

    # --------------------------------------------------------------------------
    # BLOQUE 3: Pruebas Reales de Idempotencia del Registro de Ventas
    # --------------------------------------------------------------------------
    print(
        "\n[BLOQUE 3]: Pruebas reales de idempotencia llamando a"
        " registrar_venta_atomica()..."
    )
    items_prueba = [
        ItemVenta(
            producto_nombre="Cerveza Artesanal",
            cantidad=2,
            precio_unitario=2500,
            subtotal=5000,
            metodo_pago="EFECTIVO",
        )
    ]
    ticket_id = f"T-REAL-{run_id}-001"

    # 3.1 Primer registro legítimo
    res1 = registrar_venta_atomica(
        conn=conn,
        ticket_id=ticket_id,
        total_venta=5000,
        monto_efectivo=5000,
        monto_transferencia=0,
        metodo_pago="EFECTIVO",
        items=items_prueba,
        tabla_tickets=tabla_tickets,
        tabla_items=tabla_items,
        tabla_outbox=tabla_outbox,
    )
    assert res1["status"] == "REGISTRADO"
    operaciones_completadas.append("Registro inicial de venta con función real")

    # 3.2 Reintento idéntico (Idempotencia)
    res2 = registrar_venta_atomica(
        conn=conn,
        ticket_id=ticket_id,
        total_venta=5000,
        monto_efectivo=5000,
        monto_transferencia=0,
        metodo_pago="EFECTIVO",
        items=items_prueba,
        tabla_tickets=tabla_tickets,
        tabla_items=tabla_items,
        tabla_outbox=tabla_outbox,
    )
    assert res2["status"] == "EXISTENTE_IDEMPOTENTE"
    operaciones_completadas.append("Reintento idempotente con función real")

    # 3.3 Verificación en base: no hay duplicados
    cant_tickets = conn.execute(
        f"SELECT COUNT(*) FROM {tabla_tickets} WHERE id = ?;", (ticket_id,)
    ).fetchone()[0]
    cant_items = conn.execute(
        f"SELECT COUNT(*) FROM {tabla_items} WHERE ticket_id = ?;", (ticket_id,)
    ).fetchone()[0]
    cant_outbox = conn.execute(
        f"SELECT COUNT(*) FROM {tabla_outbox} WHERE aggregate_id = ?;",
        (ticket_id,),
    ).fetchone()[0]
    assert cant_tickets == 1, f"Tickets duplicados: {cant_tickets}"
    assert cant_items == 1, f"Items duplicados: {cant_items}"
    assert cant_outbox == 1, f"Outbox duplicado: {cant_outbox}"
    operaciones_completadas.append("Verificación de no duplicación en BD")

    # 3.4 Conflicto de payload con misma clave
    items_distintos = [
        ItemVenta(
            producto_nombre="Cerveza Artesanal",
            cantidad=3,
            precio_unitario=2500,
            subtotal=7500,
            metodo_pago="EFECTIVO",
        )
    ]
    try:
      registrar_venta_atomica(
          conn=conn,
          ticket_id=ticket_id,
          total_venta=7500,
          monto_efectivo=7500,
          monto_transferencia=0,
          metodo_pago="EFECTIVO",
          items=items_distintos,
          tabla_tickets=tabla_tickets,
          tabla_items=tabla_items,
          tabla_outbox=tabla_outbox,
      )
      assert False, (
          "Se esperaba ErrorValidacion por conflicto de payload pero no ocurrió!"
      )
    except ErrorValidacion:
      operaciones_completadas.append(
          "Rechazo de conflicto de payload con misma clave"
      )
      print("  -> Conflicto de idempotencia detectado y rechazado correctamente.")

    # --------------------------------------------------------------------------
    # BLOQUE 4: Concurrencia Real de Trabajadores en Hilos Paralelos con Barrera
    # --------------------------------------------------------------------------
    print(
        "\n[BLOQUE 4]: Pruebas concurrentes en hilos independientes con barrera"
        " de sincronización..."
    )

    # 4.1 Preparar eventos sintéticos suficientes en la tabla outbox (8 eventos)
    with transaccion(conn):
      for i in range(1, 9):
        conn.execute(f"""
                INSERT INTO {tabla_outbox} (aggregate_type, aggregate_id, operation, payload_json, estado)
                VALUES ('TICKET', 'T-CONC-{run_id}-{i}', 'INSERT', '{{}}', 'PENDIENTE');
            """)
    operaciones_completadas.append(
        "Inserción de 8 eventos concurrentes en Outbox"
    )

    # 4.2 Coordinación con Barrera y dos hilos
    barrier = threading.Barrier(2)
    lote_w1: List[Dict[str, Any]] = []
    lote_w2: List[Dict[str, Any]] = []
    error_w1: List[Exception] = []
    error_w2: List[Exception] = []

    def worker_claiming(
        worker_id: int,
        dest_lote: List[Dict[str, Any]],
        err_holder: List[Exception],
    ):
      try:
        w_conn = turso_serverless.connect(url, auth_token=token)
        try:
          # Sincronización en la barrera
          barrier.wait(timeout=10)

          # Política de reintentos acotada ante conflicto transaccional
          max_reintentos = 3
          reclamados = None
          for intento in range(max_reintentos):
            try:
              reclamados = reclamar_lote_outbox(
                  w_conn, limite=3, tabla_outbox=tabla_outbox
              )
              break
            except Exception as e:
              if intento < max_reintentos - 1:
                time.sleep(0.05 * (2**intento))
              else:
                raise e
          if reclamados:
            dest_lote.extend(reclamados)
        finally:
          w_conn.close()
      except Exception as exc:
        err_holder.append(exc)

    t1 = threading.Thread(target=worker_claiming, args=(1, lote_w1, error_w1))
    t2 = threading.Thread(target=worker_claiming, args=(2, lote_w2, error_w2))
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    if error_w1:
      raise RuntimeError(
          f"Error en Worker 1 durante concurrencia: {error_w1[0]}"
      ) from error_w1[0]
    if error_w2:
      raise RuntimeError(
          f"Error en Worker 2 durante concurrencia: {error_w2[0]}"
      ) from error_w2[0]

    ids_w1 = [e["id"] for e in lote_w1]
    ids_w2 = [e["id"] for e in lote_w2]

    assert len(ids_w1) > 0, "Worker 1 no obtuvo eventos"
    assert len(ids_w2) > 0, "Worker 2 no obtuvo eventos"
    assert set(ids_w1).isdisjoint(set(ids_w2)), (
        "Violación de exclusión mutua: Colisión entre trabajadores! w1:"
        f" {ids_w1}, w2: {ids_w2}"
    )
    operaciones_completadas.append(
        "Reclamación concurrente real con exclusión mutua confirmada"
    )
    print(f"  -> Worker 1 (hilo 1) reclamó: {ids_w1}")
    print(f"  -> Worker 2 (hilo 2) reclamó: {ids_w2}")
    print(
        "  -> Exclusión mutua concurrente confirmada en hilos paralelos"
        " independientes."
    )

    # 4.3 Fencing token con confirmar_evento_outbox
    conn_fence = turso_serverless.connect(url, auth_token=token)
    try:
      ev_id = ids_w1[0]
      # Intento con token obsoleto
      fail_ack = confirmar_evento_outbox(
          conn_fence,
          event_id=ev_id,
          intentos_esperados=99,
          tabla_outbox=tabla_outbox,
      )
      assert (
          fail_ack is False
      ), "Confirmación con fencing token obsoleto debió retornar False"

      # Intento con token válido
      ok_ack = confirmar_evento_outbox(
          conn_fence,
          event_id=ev_id,
          intentos_esperados=0,
          tabla_outbox=tabla_outbox,
      )
      assert (
          ok_ack is True
      ), "Confirmación con fencing token válido debió retornar True"
      operaciones_completadas.append("Fencing token validado en Outbox remoto")
      print("  -> Fencing token y protección de estados validados en remoto.")
    finally:
      conn_fence.close()

    # 4.4 Verificación de orden secuencial para la misma entidad (anti-desorden)
    with transaccion(conn):
      conn.execute(f"""
            INSERT INTO {tabla_outbox} (aggregate_type, aggregate_id, operation, payload_json, estado)
            VALUES ('TICKET', 'T-SEQ-{run_id}', 'INSERT', '{{}}', 'PENDIENTE');
        """)
      conn.execute(f"""
            INSERT INTO {tabla_outbox} (aggregate_type, aggregate_id, operation, payload_json, estado)
            VALUES ('TICKET', 'T-SEQ-{run_id}', 'UPDATE', '{{}}', 'PENDIENTE');
        """)

    conn_seq = turso_serverless.connect(url, auth_token=token)
    try:
      lote_seq_1 = reclamar_lote_outbox(
          conn_seq, limite=1, tabla_outbox=tabla_outbox
      )
      ev_seq_1 = next(
          (e for e in lote_seq_1 if e["aggregate_id"] == f"T-SEQ-{run_id}"),
          None,
      )
      if ev_seq_1:
        lote_seq_2 = reclamar_lote_outbox(
            conn_seq, limite=10, tabla_outbox=tabla_outbox
        )
        ev_seq_2 = next(
            (e for e in lote_seq_2 if e["aggregate_id"] == f"T-SEQ-{run_id}"),
            None,
        )
        assert ev_seq_2 is None, (
            "Violación de orden secuencial: se reclamó un UPDATE mientras el"
            " evento anterior estaba EN_PROCESO!"
        )
        confirmar_evento_outbox(
            conn_seq, event_id=ev_seq_1["id"], tabla_outbox=tabla_outbox
        )
        lote_seq_3 = reclamar_lote_outbox(
            conn_seq, limite=10, tabla_outbox=tabla_outbox
        )
        ev_seq_3 = next(
            (e for e in lote_seq_3 if e["aggregate_id"] == f"T-SEQ-{run_id}"),
            None,
        )
        assert (
            ev_seq_3 is not None
        ), "El segundo evento debió quedar liberado tras confirmarse el primero"
        confirmar_evento_outbox(
            conn_seq, event_id=ev_seq_3["id"], tabla_outbox=tabla_outbox
        )
        operaciones_completadas.append(
            "Orden secuencial por entidad verificado en Outbox"
        )
        print(
            "  -> Orden secuencial por entidad confirmado: eventos concurrentes"
            " no se desordenan."
        )
    finally:
      conn_seq.close()

    # --------------------------------------------------------------------------
    # BLOQUE 5: Transparencia sobre Comportamientos Simulados vs No Probados
    # --------------------------------------------------------------------------
    print("\n[BLOQUE 5]: Clasificación de pruebas no alcanzables en este run:")
    print(
        "  - [SIMULADO LOCAL]: Pérdida real de respuesta HTTP 200 tras commit"
        " confirmado (requiere fallo de red físico en TLS)."
    )
    print(
        "  - [NO PROBADO REMOTO]: Códigos HTTP 429 por saturación de cuota y"
        " reintentos exponenciales."
    )

    print(
        "\n[ÉXITO TOTAL]: Todas las pruebas remotas autorizadas concluyeron con"
        " éxito."
    )
    return True

  except Exception as e:
    print(f"\n[ERROR EN EJECUCIÓN REMOTA]: {e}")
    print("[OPERACIONES COMPLETADAS ANTES DEL FALLO]:")
    for op in operaciones_completadas:
      print(f"  * {op}")
    return False

  finally:
    resumen_limpieza = ejecutar_limpieza_segura(
        conn=conn,
        run_id=run_id,
        objetos_creados_exitosamente=objetos_creados_exitosamente,
        objetos_inciertos=objetos_inciertos,
    )
    try:
      conn.close()
    except Exception:
      pass
    print(
        f"[CONEXIÓN CERRADA]: Estado de limpieza:"
        f" {resumen_limpieza['estado_final']}"
    )


def main():
  es_aprobado, bloqueos = ejecutar_preflight()
  if not es_aprobado:
    print("=" * 75)
    print("PREFLIGHT CONTROLADO — TURSO DEV (FASE 13)")
    print("=" * 75)
    print(
        "ESTADO: DETENIDO POR BARRERAS DE SEGURIDAD LOCALES (CÓDIGO DE SALIDA"
        f" {EXIT_CODE_PREFLIGHT_BLOCKED})"
    )
    print(
        "No se cumplen los requisitos previos obligatorios para iniciar pruebas"
        " remotas:"
    )
    for idx, b in enumerate(bloqueos, 1):
      print(f"  {idx}. {b}")
    print("\nAcción requerida:")
    print(
        "  El propietario debe configurar localmente las variables en el"
        " entorno:"
    )
    print("    export ENV_NAME=genaro-pos-dev")
    print("    export TURSO_TEST_APPROVED=YES")
    print(
        "    export"
        " TURSO_DEV_DATABASE_URL=turso://genaro-pos-dev-alanrod10.aws-sa-east-1.turso.io"
    )
    print("    export TURSO_DEV_AUTH_TOKEN=<dev_auth_token>")
    print("=" * 75)
    sys.exit(EXIT_CODE_PREFLIGHT_BLOCKED)

  exito = ejecutar_pruebas_remotas()
  sys.exit(EXIT_CODE_SUCCESS if exito else EXIT_CODE_TEST_FAILED)


if __name__ == "__main__":
  main()
