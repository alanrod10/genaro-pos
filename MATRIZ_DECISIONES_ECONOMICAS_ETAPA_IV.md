# GENAROPOS V4 — MATRIZ DE DECISIONES ECONÓMICAS (FASE IV-B)
## Definiciones Funcionales y Financieras que Requieren Aprobación del Propietario

**Proyecto:** GenaroPOS V4  
**Fecha:** 06/11/2026  
**Ámbito:** STAGING / Consolidación Económica Previa a Producción  
**Estado:** PENDIENTE DE CONFIRMACIÓN DEL PROPIETARIO  

---

## 1. MATRIZ DE DECISIONES ECONÓMICAS

A continuación se detallan las decisiones comerciales y contables necesarias para cerrar la brecha entre el código y la realidad financiera del negocio:

| Código | Decisión Económica | Opciones Técnicas | Impacto Financiero | Estado |
| :--- | :--- | :--- | :--- | :---: |
| **DEC-01** | **Destino del Adicional de Recargas Claro y Mercado Pago en Efectivo** | **Opción A (Recomendada):** El adicional cobrado al cliente se imputa inmediatamente a `1110 Drugstore Efectivo` (ganancia del negocio). El capital de recarga va a `1150 Claro Custodia`.<br>**Opción B:** Todo el dinero va a `1150` y se liquida la comisión a fin de mes. | La Opción A refleja la ganancia diaria en el arqueo del cajón y no sobreestima la deuda con Claro. | ⚠️ **PENDIENTE CONFIRMACIÓN** |
| **DEC-02** | **Circuito Compensatorio de Claro por Transferencias Digitales** | **Opción A (Bancaria):** El cobro entra a `1210 MP Propio`. El pago al recaudador o CBU de Claro se realiza mediante egreso bancario (`EXIT 1210` contra `PAGO_PROVEEDOR_CLARO`).<br>**Opción B (Efectivo en Caja):** Se retira efectivo del cajón (`1110`) y se coloca en el sobre de Claro (`1150`), compensando con el dinero digital recibido en `1210`. | La Opción A evita manipular billetes innecesariamente si Claro acepta transferencia. La Opción B es necesaria si el recaudador de Claro solo acepta billetes en mano. | ⚠️ **PENDIENTE CONFIRMACIÓN** |
| **DEC-03** | **Valuación de Mercadería en Cuenta Corriente de Ezequiel** | **Opción A:** Ezequiel retira mercadería valorizada a **Precio de Venta al Público (PVP)**.<br>**Opción B:** Ezequiel retira a **Precio de Costo** (reposición).<br>**Opción C:** Precio diferencial pactado (con descuento). | Define si el saldo adeudado por Ezequiel representa ingresos comerciales o meramente reposición de stock. | ⚠️ **PENDIENTE CONFIRMACIÓN** |
| **DEC-04** | **Restricción de Pagos a Ezequiel y Cancelación de Deuda** | **Opción A (Homologada a David):** Ezequiel solo puede cobrar o retirar fondos con validación estricta de saldo disponible en la caja asignada y contrapartida auditada.<br>**Opción B:** Retiros libres con balance negativo permitido. | La Opción A impide que se retiren fondos de la caja sin saldo real disponible. | ⚠️ **PENDIENTE CONFIRMACIÓN** |
| **DEC-05** | **Gobierno del Cargo Diario PREPAY ($350)** | **Opción A:** Mantener $350 como parámetro configurable en base de datos (`tesoreria_configuracion`), no cableado en código.<br>**Opción B:** Mantener $350 fijo en migraciones. | La Opción A permite ajustar el cargo operativo ante cambios del proveedor sin tocar el código. | ⚠️ **PENDIENTE CONFIRMACIÓN** |

---

## 2. MATRIZ DETALLADA DE RECARGAS (PLATAFORMA × DESTINO × MEDIO)

| # | Plataforma | Destino | Medio de Pago | ¿Cómo se cobra? | ¿Qué entra a caja/banco propio? | ¿Qué se debe al proveedor? | Asientos Contables Propuestos |
| :-: | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1** | PREPAY | SUBE / Telco | **Efectivo** | Mostrador | `1140` Adicional (Ganancia turno) | `1160` Capital (Bolsa Prepay) | `ENTRY 1160` (Capital) + `ENTRY 1140` (Adicional). Cargo diario en `1220`. |
| **2** | PREPAY | SUBE / Telco | **Transferencia** | MP / QR | `1210` Total cobrado | Deuda virtual Prepay | `ENTRY 1210` (Total). Traslado compensatorio interno `1120 -> 1130`. |
| **3** | CLARO | Claro | **Efectivo** | Mostrador | `1110` Adicional (Cajón propio) | `1150` Capital (Sobre Claro) | `ENTRY 1150` (Capital) + `ENTRY 1110` (Adicional). |
| **4** | CLARO | Claro | **Transferencia** | MP / QR | `1210` Total cobrado | Claro virtual | `ENTRY 1210` (Total). Pasivo transitorio Claro pendiente de pago. |
| **5** | MERCADO PAGO | SUBE | **Efectivo** | Mostrador | `1110` Total cobrado | Consume saldo `1210` | `EXIT 1210` (Capital consumido) + `ENTRY 1110` (Total cobrado en cajón). |
| **6** | MERCADO PAGO | SUBE | **Transferencia** | MP / QR | `1210` Total cobrado | Consume saldo `1210` | `EXIT 1210` (Capital) + `ENTRY 1210` (Total cobrado). Movimiento neto = Ganancia. |

---

## 3. SOLICITUD DE CONFIRMACIÓN AL PROPIETARIO

Para poder iniciar la **Fase IV-D (Implementación Controlada en Staging)**, se requiere la confirmación expresa de:
1. **DEC-01:** ¿Aprueba que el adicional en efectivo de Claro ingrese al cajón `1110` (Opción A)?
2. **DEC-02:** ¿Cómo se salda habitualmente la deuda de Claro por recargas cobradas por transferencia: con transferencia al proveedor (Opción A) o con billetes del cajón (Opción B)?
3. **DEC-03:** ¿La mercadería que retira Ezequiel se computa a PVP (Opción A) o al Costo (Opción B)?
4. **DEC-05:** ¿Aprueba parametrizar el cargo diario de PREPAY en lugar de tenerlo cableado en código?
