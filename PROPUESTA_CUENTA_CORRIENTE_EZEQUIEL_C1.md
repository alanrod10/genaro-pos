# GENAROPOS V4 — PROPUESTA FUNCIONAL Y TÉCNICA C.1
## Cuenta Corriente de Ezequiel / Especificación de Migración 015

**Proyecto:** GenaroPOS V4  
**Fecha:** 06/11/2026  
**Ámbito:** STAGING / Arquitectura Centralizada de Contabilización  
**Estado:** Propuesta Técnica de Diseño (Sin ejecución de DDL ni código)  
**Autor:** Antigravity Engineering & Financial Systems Audit Team  

---

## 1. JUSTIFICACIÓN DEL ACTOR "EZEQUIEL"

En el análisis de trazabilidad económica de GenaroPOS se constató que la cuenta corriente de **David** (socio/proveedor de mercadería y fondos operativos) cuenta con un esquema de base de datos robusto y testeado (`tesoreria_cuenta_corriente_david` implementado en la migración 008).

Sin embargo, en la dinámica comercial del Drugstore participa un segundo actor clave denominado **Ezequiel**, el cual realiza operaciones tales como:
- Retiros de mercadería a cuenta.
- Anticipos operativos o entregas de efectivo en mano.
- Cobros de servicios a liquidar contra caja.
- Rendiciones periódicas por compensación de saldos.

Actualmente, no existe una tabla formal para registrar la cuenta corriente de Ezequiel, lo que genera que sus movimientos deban registrarse de forma heterogénea como gastos generales, retiros de caja o notas informales, perdiendo trazabilidad contable y auditoría de saldo histórico.

---

## 2. DECISIÓN DE DISEÑO ARQUITECTURAL

Se evalúan dos opciones técnicas:
- **Opción A (Específica):** Crear `tesoreria_cuenta_corriente_ezequiel` replicando el diseño probado de David (append-only con balance progresivo).
- **Opción B (Generalizada):** Unificar cuentas corrientes en una tabla genérica `tesoreria_cuentas_corrientes_titulares` con clave foránea al titular.

**Recomendación:**  
Siguiendo la arquitectura actual del sistema (donde `tesoreria_cuenta_corriente_david` ya cuenta con más de 10 tests de concurrencia e integridad referencial y endpoints dedicados), la **Opción A** garantiza consistencia inmediata, minimiza riesgos de regresión y permite aislar completamente las auditorías entre ambos titulares.

---

## 3. ESPECIFICACIÓN DDL: MIGRACIÓN 015

A continuación se detalla la propuesta formal para el archivo de migración `db/migrations/015_cuenta_corriente_ezequiel.sql`:

```sql
-- Migración 015: Cuenta Corriente Ezequiel
-- Diseñada para registrar entregas, retiros, pagos y compensaciones con balance append-only.

CREATE TABLE IF NOT EXISTS tesoreria_cuenta_corriente_ezequiel (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_hora_utc TEXT NOT NULL DEFAULT (datetime('now')),
    tipo_movimiento TEXT NOT NULL CHECK(tipo_movimiento IN ('ENTREGA_MERCADERIA', 'PAGO_RECIBIDO', 'RETIRO_FONDOS', 'AJUSTE_COMPENSATORIO')),
    monto_centavos INTEGER NOT NULL CHECK(monto_centavos > 0),
    signo INTEGER NOT NULL CHECK(signo IN (1, -1)),
    saldo_resultante_centavos INTEGER NOT NULL,
    descripcion TEXT NOT NULL,
    referencia_operativa TEXT,
    actor TEXT NOT NULL DEFAULT 'sistema',
    idempotency_key TEXT UNIQUE,
    anulado INTEGER NOT NULL DEFAULT 0 CHECK(anulado IN (0, 1)),
    anulado_por TEXT,
    anulado_fecha_utc TEXT,
    motivo_anulacion TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cc_ezequiel_fecha ON tesoreria_cuenta_corriente_ezequiel(fecha_hora_utc);
CREATE INDEX IF NOT EXISTS idx_cc_ezequiel_tipo ON tesoreria_cuenta_corriente_ezequiel(tipo_movimiento);
CREATE INDEX IF NOT EXISTS idx_cc_ezequiel_anulado ON tesoreria_cuenta_corriente_ezequiel(anulado);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cc_ezequiel_idempotency ON tesoreria_cuenta_corriente_ezequiel(idempotency_key) WHERE idempotency_key IS NOT NULL;

-- Tabla de auditoría inmutable
CREATE TABLE IF NOT EXISTS tesoreria_cc_ezequiel_auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    movimiento_id INTEGER NOT NULL,
    accion TEXT NOT NULL CHECK(accion IN ('CREAR', 'ANULAR')),
    valores_anteriores TEXT,
    valores_nuevos TEXT,
    fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
    actor TEXT NOT NULL DEFAULT 'sistema',
    FOREIGN KEY (movimiento_id) REFERENCES tesoreria_cuenta_corriente_ezequiel(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_audit_cc_ezequiel_mov ON tesoreria_cc_ezequiel_auditoria(movimiento_id);
```

---

## 4. MAPEO DE CIRCUITOS ECONÓMICOS

| Circuito Operativo | Tipo de Movimiento | Signo | Impacto en Saldo Ezequiel | Contrapartida en Tesorería |
| :--- | :--- | :--- | :--- | :--- |
| **Ezequiel retira mercadería sin pagar en el acto** | `ENTREGA_MERCADERIA` | `+1` (Deuda aumenta) | Ezequiel debe más dinero al negocio. | Costo de mercadería / Stock comercial. |
| **Ezequiel abona dinero en efectivo a la caja** | `PAGO_RECIBIDO` | `-1` (Deuda disminuye) | Saldo deudor se reduce. | `ENTRY 1110` (Caja Diaria Drugstore). |
| **Ezequiel abona mediante transferencia bancaria** | `PAGO_RECIBIDO` | `-1` (Deuda disminuye) | Saldo deudor se reduce. | `ENTRY 1210` (Mercado Pago Propio). |
| **Caja entrega fondos a Ezequiel como adelanto** | `RETIRO_FONDOS` | `+1` (Deuda aumenta) | Ezequiel adeuda los fondos recibidos. | `EXIT 1110` (Egreso de Caja Diaria). |
| **Ajuste o bonificación aprobada por administración** | `AJUSTE_COMPENSATORIO` | `-1` o `+1` | Corrige discrepancias documentadas. | Asiento de ajuste contable debidamente auditado. |

---

## 5. REGLAS DE INTEGRIDAD Y OBSERVABILIDAD

1. **Invariante de Balance Progresivo:**
   Cada registro nuevo debe verificar que:
   $$\text{saldo\_resultante\_centavos} = \text{saldo\_anterior} + (\text{signo} \times \text{monto\_centavos})$$
2. **Anulación Lógica Estricta:**
   No se eliminan registros (`DELETE`). La anulación lógica marca `anulado = 1` y genera un asiento compensatorio inverso con auditoría completa.
3. **Integración con Commercial Bridge:**
   Las operaciones que involucren movimientos de caja emitirán eventos tipificados al Outbox contable para su procesamiento centralizado.

---

## 6. PRÓXIMOS PASOS RECOMENDADOS

1. Someter a revisión de los socios la denominación de los conceptos y circuitos.
2. Una vez obtenida la aprobación expresa, incorporar la migración `015` en `db/migrations/` en una futura etapa de desarrollo.
3. Integrar los endpoints FastAPI y Worker respetando la arquitectura centralizada.
