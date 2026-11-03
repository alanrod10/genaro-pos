# GENARO POS + GENARO TESORERÍA
# MANUAL OPERATIVO DEL DÍA D Y GUÍA DE PRODUCCIÓN

**Versión:** 1.0 (Certificada para Etapa 8)  
**Destinatarios:** Alan (Administrador / Operaciones), Yilda (Socia), Encargados de Turno y Cajeros  
**Entorno Objetivo:** Producción Local Desacoplada (WAL SQLite + Outbox Asíncrono)

---

## 1. CHECKLIST PREVIO AL DÍA D (PRE-FLIGHT)

Antes de abrir el local o encender el sistema comercial:

- [ ] **Verificación de Red:** Confirmar que no haya conexiones abiertas a Turso ni URLs remotas configuradas.
- [ ] **Respaldo Obligatorio de Seguridad:**
  ```bash
  cp /home/alanrod10/production/data/genaro-pos.db /home/alanrod10/backups/genaro-pos_pre_dia_d_$(date +%Y%m%d_%H%M%S).db
  sha256sum /home/alanrod10/backups/genaro-pos_pre_dia_d_*.db > /home/alanrod10/backups/HASHES_DIA_D.txt
  ```
- [ ] **Ejecución del Despliegue de Integración:**
  ```bash
  python production/deploy_production_bridge.py --comm-db /home/alanrod10/production/data/genaro-pos.db --tes-db /home/alanrod10/production/data/tesoreria.db
  ```
  *Verificar que el reporte indique `"status": "READY_FOR_OPERATION"` y que la migración 012 esté presente.*

---

## 2. PROTOCOLO DEL DÍA D — ESTABLECIMIENTO DE SALDOS INICIALES

> [!IMPORTANT]
> **REGLAS ECONÓMICAS INNEGOCIABLES:**
> 1. Queda estrictamente prohibido importar saldos antiguos de planillas Excel.
> 2. No se fabricarán saldos contables artificiales.
> 3. Cada peso registrado debe corresponder a billetes físicos contados o saldos digitales verificados en pantalla.
> 4. Los fondos en custodia (1150 Claro) no pertenecen al patrimonio del negocio ni deben usarse para dar cambio.

### Procedimiento Presencial de Conteo:
1. **Fondo 1110 (Drugstore Diario):** Contar el dinero en el cajón de mostrador destinado a dar cambio inicial (ej. $25.000,00).
2. **Fondo 1120 (Drugstore Acumulado):** Contar el dinero en la caja de seguridad / tesoro.
3. **Fondo 1130 (Prepay Efectivo):** Contar el efectivo destinado a cobranzas de recargas Prepay.
4. **Fondo 1140 (Adicionales Efectivo):** Contar el dinero existente en la caja de adicionales de los socios.
5. **Fondo 1150 (Claro Efectivo Custodia):** Contar el efectivo físico de cobranzas de Claro.
6. **Fondo 1210 (Mercado Pago Propio):** Abrir la app de Mercado Pago y registrar el saldo disponible en cuenta.
7. **Fondo 1220 (Prepay Digital Saldo):** Abrir el portal de Prepay y registrar el saldo mayorista disponible.
8. **Fondo 1230 (Claro Digital Saldo):** Abrir la plataforma de Claro y registrar el saldo mayorista disponible.
9. **Fondo 1310 (Tránsito Preventistas):** Inicia habitualmente en $0,00.

### Ejecución del Acta Inicial:
```bash
python production/dia_d_apertura_fondos.py \
  --tes-db /home/alanrod10/production/data/tesoreria.db \
  --user-id "alan_socio" \
  --fecha "2026-11-01" \
  --saldos-json '{"1110": 2500000, "1120": 5000000, "1130": 0, "1140": 0, "1150": 0, "1210": 1500000, "1220": 1000000, "1230": 1000000, "1310": 0}' \
  --acta "ACTA_CONSTITUTIVA_DIA_D_APERTURA"
```
*El sistema generará el Certificado Digital firmado con hash SHA-256 e imprimirá el cuadro de cuadre exacto.*

---

## 3. ACTIVACIÓN DEL WORKER EN PRODUCCIÓN

Iniciar el servicio autónomo para procesar los eventos de venta en segundo plano:

```bash
sudo systemctl start genaro-commercial-bridge.service
sudo systemctl status genaro-commercial-bridge.service
```

### Comprobación de Salud Operativa:
```bash
python scripts/run_commercial_bridge_worker.py --health-check \
  --db-comm /home/alanrod10/production/data/genaro-pos.db \
  --db-tes /home/alanrod10/production/data/tesoreria.db
```
*Debe reportar `"status": "HEALTHY"`.*

---

## 4. OPERATORIA ORDINARIA POR TURNOS

### A) Rol Cajero (Mostrador)
- **Ventas Ordinarias:** El cajero cobra normalmente en efectivo, Mercado Pago (QR/transferencia) o mixto. El sistema emite el ticket y registra atómicamente el evento en el Outbox.
- **Indicador Superior de Sincronización (Header):**
  - `✓ Tesorería al día`: Todas las operaciones están confirmadas en Tesorería.
  - `⏳ Sync (N)`: Hay operaciones en tránsito procesándose (se sincronizan cada 5 segundos).
  - `⚠️ Atención Sync`: Alguna operación falló o requiere revisión. **Acción:** Avisar a Alan o Yilda sin detener la caja.
- **Servicios Virtuales (Prepay / Claro):** Cobrar y registrar según medio de pago. Claro se aparta automáticamente en custodia contable (1150).
- **Gastos Autorizados:** Si el cajero paga un gasto menor autorizado en efectivo, se emite el comprobante y el worker registra el egreso en 1110.

### B) Cierre de Turno y Arqueo Informativo de Adicionales
1. Al terminar el turno, el cajero realiza el arqueo ciego habitual.
2. Se registra el **Arqueo Informativo de Adicionales** en el sistema.
3. **Decisión de los Socios:**
   - Si los socios deciden **dejar el dinero en el cajón**: No se hace ningún movimiento contable.
   - Si los socios deciden **retirar los adicionales en efectivo**: Se realiza un traslado manual formal desde 1110 hacia 1140 con comprobante.
   - Los adicionales cobrados por Mercado Pago **permanecen en la cuenta digital (1210)**.

---

## 5. CIERRE DIARIO DEFINITIVO DE TESORERÍA (LAB-010)

Al finalizar la jornada comercial:
1. **Inspección de Monitor:** Ingresar al Panel de Tesorería (Administrador / Socios) y constatar que la bandeja Outbox tenga 0 eventos pendientes (`PENDING: 0`).
2. **Vaciado Obligatorio de Caja Principal (`executeCashDrawerSweep`):**
   - El sistema transfiere todo el efectivo remanente de `1110` (Drugstore Diario) hacia `1120` (Drugstore Acumulado / Tesoro).
   - El fondo `1110` queda estrictamente en **$0,00**.
3. **Confirmación de Cierre Diario:**
   - Se genera el snapshot contable inmutable con hash canónico SHA-256.
   - La jornada queda cerrada formalmente (`status: CONFIRMED`).

---

## 6. CONTINGENCIAS Y RECUPERACIÓN

### Contingencia 1: Corte de Luz o Reinicio Abrupto de la Computadora
- Al volver la energía, el servicio systemd se inicia automáticamente.
- El worker ejecuta `recover_orphaned_processing_events`, detecta los eventos que hayan quedado interrumpidos a mitad de procesamiento y los reintenta de inmediato.
- **Idempotencia estricta:** Ninguna operación se duplica ni se pierde dinero.

### Contingencia 2: Error Crítico o Necesidad de Reversión Inmediata
Si por fuerza mayor se requiere suspender la integración y volver a operar sin sincronización de tesorería:
```bash
# 1. Detener el servicio del worker
sudo systemctl stop genaro-commercial-bridge.service

# 2. Ejecutar script de rollback simétrico
python production/rollback_production_bridge.py --comm-db /home/alanrod10/production/data/genaro-pos.db

# 3. La caja comercial continúa funcionando con normalidad sin Outbox
```
