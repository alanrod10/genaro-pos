# GENAROPOS V4 — ESPECIFICACIÓN COMPLEMENTARIA B.1 Y B.2
## Matriz Económica de Recargas por Plataforma, Tratamiento de Adicionales y Traslados Compensatorios de Claro

**Proyecto:** GenaroPOS V4  
**Fecha:** 06/11/2026  
**Ámbito:** STAGING / Arquitectura Centralizada de Contabilización  
**Estado:** Documento de Especificación Técnica y Económica (Sin ejecución de código)  
**Autor:** Antigravity Engineering & Financial Systems Audit Team  

---

## 1. OBJETIVO Y CONTEXTO

Durante la Etapa I de auditoría integral se identificaron dos puntos de riesgo e inconsistencia conceptual en la contabilización de servicios:
1. **Hallazgo B.1:** El cálculo y destino del **adicional comercial** de las recargas (cargo de servicio) difiere según el canal comercial, pero actualmente en algunos flujos se absorbe dentro del capital de recarga o se mezcla en el fondo de custodia sin segregación clara de la ganancia operativa.
2. **Hallazgo B.2:** En el caso de **Claro Comercios**, cuando el cliente abona su recarga mediante **transferencia bancaria digital**, el dinero ingresa efectivamente a la cuenta bancaria del negocio (`1210 Mercado Pago Propio`), mientras que la deuda con la plataforma Claro consume saldo de la plataforma externa. Si la recaudación física en efectivo va a la bolsa/custodia Claro (`1150`), se genera una discordancia física-digital si no existe un traslado compensatorio formalizado.

Este documento establece la especificación exhaustiva de ambas reglas para su posterior aprobación e implementación controlada.

---

## 2. MATRIZ INTEGRAL DE COMBINACIONES (PLATAFORMA × SERVICIO × MEDIO DE PAGO)

En GenaroPOS operan actualmente tres plataformas oficiales:
- **PREPAY:** Recargas de telefonía (Personal, Movistar, Claro) y SUBE.
- **CLARO COMERCIOS:** Recargas y pines exclusivos de Claro.
- **MERCADO PAGO:** Recargas de saldo digital y SUBE ejecutadas vía app o terminal MP.

### 2.1. Matriz de Imputación de Fondos

| Plataforma Origen | Servicio Destino | Medio de Pago | Tratamiento Capital Carga | Tratamiento Adicional Comercial | Imputación Financiera Primaria |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **PREPAY** | SUBE / Telco | **EFECTIVO** | Se debita de cupo digital Prepay; el efectivo ingresa a `1160` (Bolsa Recaudación Prepay). | Ingresa a `1140` (Adicionales Turno Cajón), pendiente de arqueo físico. | `ENTRY 1160` (Capital) + `ENTRY 1140` (Adicional). Cargo diario $350 debitado de `1220`. |
| **PREPAY** | SUBE / Telco | **TRANSFERENCIA** | Se debita de cupo digital Prepay; el dinero entra a `1210` (MP Propio). | Queda en `1210` como parte del cobro digital. | `ENTRY 1210` (Total cobrado). Se genera traslado compensatorio automático `1120 -> 1130` para reponer capital físico a Prepay. |
| **CLARO COMERCIOS** | Claro | **EFECTIVO** | El efectivo cobrado va a la bolsa de custodia física Claro (`1150`). | El adicional queda retenido en caja diaria `1110` (ganancia neta de GenaroPOS). | `ENTRY 1150` (Capital) + `ENTRY 1110` (Adicional Drugstore). |
| **CLARO COMERCIOS** | Claro | **TRANSFERENCIA** | El dinero entra a `1210` (MP Propio). | Queda en `1210` como ingreso comercial. | `ENTRY 1210` (Total cobrado). Requiere traslado compensatorio formal: egreso bancario para reposición o depósito a Claro. |
| **MERCADO PAGO** | SUBE | **EFECTIVO** | Consume saldo de `1210` (MP Propio). El efectivo entra a `1110` (Cajón Drugstore). | El adicional entra a `1110` (Cajón Drugstore). | `EXIT 1210` (Capital consumido) + `ENTRY 1110` (Cobro total en cajón). |
| **MERCADO PAGO** | SUBE | **TRANSFERENCIA** | Consume saldo de `1210`. El cobro entra a `1210`. | Queda en `1210`. | `EXIT 1210` (Capital) + `ENTRY 1210` (Cobro total). Movimiento neto = Adicional ganado. |

---

## 3. TRATAMIENTO CONTABLE Y FINANCIERO DEL ADICIONAL (B.1)

### 3.1. Principio Financiero Rector
El importe cobrado al cliente se compone de:
$$\text{Total Cobrado} = \text{Monto Carga (Capital del Tercero)} + \text{Monto Adicional (Honorario GenaroPOS)}$$

- **Monto Carga:** Es dinero del proveedor/plataforma (`1160` Prepay, `1150` Claro, o reposición a `1210` MP). GenaroPOS actúa únicamente como agente de recaudación o custodio.
- **Monto Adicional:** Es ingreso genuino propio del negocio.
  - En cobros en **Efectivo de PREPAY**: debe acumularse en el fondo transitorio `1140` (Adicionales Turno Cajón) y conciliarse en el arqueo físico al final de jornada para transferirse a `1110` (Drugstore).
  - En cobros en **Efectivo de CLARO / MERCADO PAGO**: debe asentarse directamente en `1110` (Caja Diaria Drugstore) o computarse como ingreso por comisiones de servicios en el balance diario.
  - En cobros por **Transferencia**: el total ingresa a `1210` (Cuenta Digital Propia); la porción correspondiente a la comisión propia incrementa el patrimonio líquido sin obligación de restitución.

### 3.2. Prohibición de Mezcla de Capitales
Queda expresamente prohibido que el Adicional sea acreditado en cuentas de custodia de terceros (`1150` Claro o `1160`/`1130` Prepay). Los fondos de terceros deben reflejar con exactitud milimétrica el capital adeudado al proveedor.

---

## 4. CIRCUITO COMPENSATORIO DE CLARO COMERCIOS (B.2)

### 4.1. Anatomía del Descalce Operativo
Cuando un cliente realiza una recarga Claro pagando por **Transferencia Bancaria**:
1. Claro debita el saldo virtual del cupo comercial de GenaroPOS.
2. El cliente transfiere los fondos a la cuenta Mercado Pago de GenaroPOS (`1210`).
3. El proveedor Claro espera cobrar su reposición en efectivo mediante recaudador físico o mediante pago bancario específico.
4. Si el negocio no traslada los fondos desde `1210` hacia el fondo o cuenta de Claro, la bolsa física `1150` queda deficitaria respecto a la deuda real con Claro, y la cuenta `1210` queda con un excedente que no le pertenece íntegramente.

### 4.2. Especificación del Traslado Compensatorio
Para regularizar esta operación, se define el siguiente circuito contable:
1. **Al confirmar la recarga Claro por Transferencia:**
   - Asiento 1: `ENTRY 1210` por el total cobrado (contrapartida: `SERVICIO_CLARO_DIGITAL`).
   - Registro de Pasivo Transitorio: Se emite un asiento de provisión o pasivo: `CLARO_PAYABLE_PROVISION` vinculado al `carga_id`.
2. **Al ejecutar la liquidación o reposición a Claro:**
   - Se habilita en Tesorería la operación formal `TRASLADO_COMPENSATORIO_CLARO`:
     - Origen: `1210 Mercado Pago Propio` (o `1110 Drugstore` si se cubre en efectivo).
     - Destino: Pago directo a proveedor Claro (Efectivo `1150` o transferencia bancaria al CBU de Claro).
     - Asiento: `EXIT 1210` (o `1110`) con contrapartida `PAGO_PROVEEDOR_CLARO`.
3. **Control de Auditoría:**
   - El saldo adeudado a Claro en cualquier momento $t$ debe satisfacer:
     $$\text{Saldo Adeudado Claro} = \sum \text{Cargas Claro Efectivo} + \sum \text{Cargas Claro Transf.} - \sum \text{Pagos/Rendiciones Claro}$$

---

## 5. PROPUESTA DE MIGRACIÓN SIN RUPTURA HISTÓRICA

Para implementar B.1 y B.2 sin alterar registros pasados:
1. **Campos en base de datos:**
   - Conservar intactas las columnas `monto_carga`, `monto_adicional`, `total_cobrado`, `pago_efectivo`, `pago_transferencia` en `cargas_servicios`.
   - No ejecutar migraciones destructivas ni reescribir tickets preexistentes.
2. **Lógica de Contabilización en `commercial_bridge_worker.py`:**
   - Aplicar las reglas perfeccionadas de B.1 y B.2 únicamente a partir de eventos nuevos o eventos con `created_at >= fecha_activacion`.
   - Para tickets antiguos ya imputados, mantener sus referencias contables originales inalteradas.

---

## 6. CONCLUSIÓN Y RECOMENDACIÓN

Esta especificación resuelve la ambigüedad conceptual detectada en la Etapa I y provee un marco normativo sólido para que la contabilidad de recargas sea 100% auditable y fidedigna.  
Se eleva a consideración de los titulares para su validación previa a cualquier desarrollo de código.
