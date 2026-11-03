# INFORME TÉCNICO: PRIMER DRY-RUN LOCAL DEL HISTÓRICO (EN MEMORIA)

> **Documento:** Informe de Ejecución y Validación de Simulación Dry-Run.  
> **Fecha de Ejecución:** 29 de Octubre de 2026.  
> **Modo de Ejecución:** Exclusivamente SQLite `:memory:` (Cero escrituras en disco o base de datos).  
> **Archivo Origen Auditado:** `/home/alanrod10/workspace/genaro-pos-turso-lab/SISTEMA_GENARO_v4_PRODUCCION.xlsx`.  
> **Tamaño del Archivo:** 1.381.969 bytes.  
> **Hash MD5:** `851599ddf096b662dedf7ab5f820f444`  
> **Hash SHA-256:** `54f793f68f337a625d72bf1d195da59ee7c62dd2bbc0119917e791f7bb3b48ee`

---

## 1. RESUMEN EJECUTIVO Y DECLARACIÓN DE AISLAMIENTO

Se completó con éxito el **primer ensayo técnico (dry-run)** del histórico de GenaroPOS bajo un entorno 100% aislado y no destructivo:
1. Toda la simulación operó en memoria volátil de proceso (`sqlite3.connect(":memory:")`), cerrándose la conexión al concluir.
2. Cero conexiones a bases de datos remotas (`genaro-pos-live-ops`, `genaro-pos-prod` o Turso).
3. Cero modificaciones en tablas operativas (`tickets`, `ticket_items`, `cargas_servicios`, `cierres_diarios`, `productos`).
4. Cero modificaciones en el archivo Excel original (verificado por hashes SHA-256 y MD5 idénticos antes y después del ensayo).
5. Se validaron satisfactoriamente las **13 pruebas obligatorias** establecidas por el propietario.

---

## 2. RESULTADO DE LAS 13 PRUEBAS OBLIGATORIAS

| N° | Prueba Obligatoria | Criterio / Esperado | Resultado Obtenido en Simulación | Estado |
| :---: | :--- | :---: | :---: | :---: |
| **1** | **Cantidades de registros** | Tickets: 7.246<br>Items: 11.566<br>Cargas: 1.741 | `historico_tickets`: **7.246**<br>`historico_ticket_items`: **11.566**<br>`historico_cargas_servicios`: **1.741** | **APROBADO** |
| **2** | **Totales monetarios originales** | Ventas: $23.623.890<br>Items: $23.522.590<br>Cargas: $9.556.400 | Ventas: **$23.623.890** (Efvo: $16.789.820, Transf: $6.840.970)<br>Items: **$23.522.590**<br>Cargas: **$9.556.400** (Carga: $9.069.600, Adic: $486.800) | **APROBADO** |
| **3** | **Conservación de 4 discrepancias de pagos** | 4 tickets intactos con diferencia neta de -$6.900 | 4 tickets identificados y conservados con sus importes exactos y columna `balance_pago_diferencia != 0` | **APROBADO** |
| **4** | **Conservación de 12 parejas de cargas idénticas** | 24 filas conservadas y señaladas como posibles duplicados | 24 filas persistidas íntegramente con marca explícita `es_posible_duplicado = 1` | **APROBADO** |
| **5** | **Identificación de 4 grupos de reintentos de ítems** | 6 filas identificadas; rankings depurados las excluyen | 6 filas marcadas con `es_duplicado_deriva = 1` ($16.200); excluidas exitosamente en consultas depuradas | **APROBADO** |
| **6** | **Disponibilidad de 31 tickets sin detalle** | 29 sin ítems + 2 reasociados por deriva temporal | 29 tickets puros con `tiene_items = 0` ($117.400) y 2 reasociados con `tiene_items = 1` ($6.500) | **APROBADO** |
| **7** | **Integridad ante deriva temporal de milisegundos** | 0 registros perdidos | 2 ítems vinculados a su cabecera por proximidad temporal; 0 registros descartados | **APROBADO** |
| **8** | **Preservación de cantidades nulas en pesables** | 1.220 filas con `cantidad IS NULL` | **1.220 filas** preservadas con cantidad nula y subtotal financiero intacto ($3.093.360) | **APROBADO** |
| **9** | **Idempotencia ante reimportación accidental** | Bloqueo estricto ante segundo lote idéntico | Excepción `sqlite3.IntegrityError` disparada por `UNIQUE(source_sha256)`. Cero duplicación | **APROBADO** |
| **10**| **Distinción entre estadísticas originales y depuradas**| Coexistencia de vistas sin mezclar datos | Rankings generados en paralelo: Original (11.566 filas) vs Depurado (11.560 filas) | **APROBADO** |
| **11**| **Comprobación de integridad de SQLite** | `PRAGMA integrity_check` = `ok` | **`ok`** (100% de índices, tipos y constraints válidos) | **APROBADO** |
| **12**| **Cero afectación a tablas operativas** | Cero tablas operativas modificadas o creadas | Solo residieron en memoria las 4 tablas `historico_*` y tabla de secuencia | **APROBADO** |
| **13**| **Aislamiento en caja, cierres y stock** | Cero interferencia contable | Cero consultas operativas alteradas; el arqueo diario en vivo no lee las tablas históricas | **APROBADO** |

---

## 3. ANOMALÍAS CONSERVADAS Y AUDITADAS

### 3.1. Tickets con discrepancia de pago (Consistencia: 99,94%)

Los 4 tickets se conservaron con sus importes reales en `historico_tickets`:

| `id` | Fecha Registrada | `total_venta` | `monto_efectivo` | `monto_transferencia` | `balance_pago_diferencia` | Causa Raíz Auditada |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `T-1776286833621` | 2026-04-15 18:00:34 | $3.500 | $3.500 | $3.500 | **-3500** | Pago mixto duplicado en ambas columnas |
| `T-1776295225247` | 2026-04-15 20:20:25 | $1.200 | $1.200 | $1.200 | **-1200** | Pago mixto duplicado en ambas columnas |
| `T-1776300603362` | 2026-04-15 21:50:03 | $2.000 | $2.000 | $2.000 | **-2000** | Pago mixto duplicado en ambas columnas |
| `T-1782588411406` | 2026-06-27 16:26:51 | $5.100 | $0 | $5.300 | **-200** | Cobro con recargo de $200 no reflejado en cabecera |

### 3.2. Cargas virtuales idénticas (12 parejas / 24 filas)

Se identificaron y marcaron con `es_posible_duplicado = 1` en `historico_cargas_servicios` las 24 filas que comparten idéntica fecha (al segundo), servicio, monto de carga, adicional y medio de pago:

- Filas Excel 41 y 42 (04/12/2025): SUBE $3.000 (Adic $200) Efvo
- Filas Excel 44 y 46 (05/12/2025): SUBE $2.000 (Adic $100) Efvo
- Filas Excel 681 y 682 (25/03/2026 12:17:44): SUBE $2.000 (Adic $100) Efvo
- Filas Excel 1132 y 1134 (26/06/2026 16:04:22): SUBE $3.000 (Adic $200) Efvo
- Filas Excel 1140 y 1141 (27/06/2026 15:21:12): SUBE $2.000 (Adic $100) Efvo
- Filas Excel 1211 y 1212 (10/07/2026 21:42:47): SUBE $2.000 (Adic $100) Efvo
- Filas Excel 1485 y 1486 (03/09/2026 21:14:10): SUBE $5.000 (Adic $300) Transf
- Filas Excel 1522 y 1523 (10/09/2026 21:04:37): SUBE $10.000 (Adic $500) Efvo
- Filas Excel 1528 y 1529 (11/09/2026 20:07:00): SUBE $9.500 (Adic $500) Efvo
- Filas Excel 1557 y 1558 (17/09/2026 17:41:19): SUBE $4.000 (Adic $200) Efvo
- Filas Excel 1559 y 1560 (17/09/2026 18:58:51): SUBE $4.000 (Adic $200) Efvo
- Filas Excel 1563 y 1564 (17/09/2026 20:30:30): SUBE $9.000 (Adic $500) Efvo

*Impacto total involucrado:* $124.600 en cobros de recargas. Ninguna fila fue descartada ni fusionada; ambas coexisten con fingerprints deterministas diferenciados.

### 3.3. Reintentos de ítems duplicados (4 grupos / 6 filas)

Se marcaron con `es_duplicado_deriva = 1` en `historico_ticket_items`:
1. Fila 9668 (`T-1789152684913`): *Impresión a color* (3 u × $400 = $1.200)
2. Fila 9669 (`T-1789152684913`): *Chicle Beldent* (1 u × $900 = $900)
3. Fila 9872 (`T-1789593208443`): *Leche entera Cosalta 1lt* (1 u × $2.000 = $2.000)
4. Fila 9879 (`T-1789599964121`): *Quilmes 1lt* (2 u × $3.800 = $7.600)
5. Fila 9883 (`T-1789601117700`): *Mignon blandito* (Pesable = $1.000)
6. Fila 9884 (`T-1789601117700`): *Lays Clásicas 85gr* (1 u × $3.500 = $3.500)

*Impacto total involucrado:* $16.200 en subtotales de artículos.

---

## 4. ESTADÍSTICAS OBTENIDAS: ORIGINALES VS DEPURADAS

### 4.1. Ranking Top 10 Productos Más Vendidos

| Puesto | Producto | Ventas (Original) | Recaudación (Original) | Ventas (Depurado) | Recaudación (Depurado) | Efecto de la Depuración |
| :---: | :--- | :---: | :---: | :---: | :---: | :--- |
| **1** | **Quilmes 1lt** | 238 | $1.253.200 | **237** | **$1.245.600** | Excluye 1 reintento de 2 u ($7.600) |
| **2** | **Norte 1lt** | 189 | $1.040.100 | **189** | **$1.040.100** | Sin duplicados |
| **3** | **Coca 2,5lts** | 220 | $832.700 | **220** | **$832.700** | Sin duplicados |
| **4** | **361 1,5lts** | 150 | $745.100 | **150** | **$745.100** | Sin duplicados |
| **5** | **Coca retornable 2,5lts** | 173 | $739.100 | **173** | **$739.100** | Sin duplicados |
| **6** | **Mignon blandito** | 533 | $668.700 | **532** | **$667.700** | Excluye 1 reintento pesable ($1.000) |
| **7** | **Schneider 710cc** | 154 | $501.900 | **154** | **$501.900** | Sin duplicados |
| **8** | **Bizcochos Chatos/Rombo** | 291 | $466.100 | **291** | **$466.100** | Sin duplicados |
| **9** | **Heineken 1lt** | 89 | $440.000 | **89** | **$440.000** | Sin duplicados |
| **10**| **Quilmes 710cc** | 114 | $430.700 | **114** | **$430.700** | Sin duplicados |

*Conclusión del Ranking:* La depuración de reintentos previene sobrecontabilizar ventas ficticias generadas por fallos de red en Google Sheets, reflejando con máxima fidelidad la demanda real de productos.

### 4.2. Distribución Mensual de Facturación Histórica

| Período | Tickets | Facturación Total | Efectivo | Transferencia | Recargas (Cobrado) | Adicional Recargas |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **2025-12** | 913 | $2.378.930 | $1.769.330 | $609.600 | $1.018.000 | $51.700 |
| **2026-01** | 797 | $2.341.350 | $1.721.550 | $619.800 | $750.500 | $38.800 |
| **2026-02** | 685 | $1.743.730 | $1.295.430 | $448.300 | $877.600 | $45.500 |
| **2026-03** | 756 | $2.389.830 | $1.761.330 | $628.500 | $950.700 | $49.200 |
| **2026-04** | 562 | $2.040.350 | $1.393.850 | $653.200 | $868.000 | $45.000 |
| **2026-05** | 470 | $1.956.110 | $1.328.710 | $627.400 | $663.500 | $33.900 |
| **2026-06** | 532 | $1.771.500 | $1.229.400 | $542.300 | $895.600 | $45.600 |
| **2026-07** | 502 | $1.823.700 | $1.309.800 | $513.900 | $805.300 | $41.200 |
| **2026-08** | 574 | $2.183.300 | $1.523.500 | $659.800 | $1.051.700 | $53.400 |
| **2026-09** | 676 | $2.728.740 | $1.884.040 | $844.700 | $870.100 | $42.500 |
| **2026-10** | 779 | $2.266.350 | $1.572.880 | $693.470 | $805.400 | $40.000 |
| **TOTAL** | **7.246** | **$23.623.890** | **$16.789.820** | **$6.840.970** | **$9.556.400** | **$486.800** |

---

## 5. EVIDENCIA DE IDEMPOTENCIA Y CONTROL DE INTEGRIDAD

1. **Intento de reinserción simulado:**  
   Se ejecutó un segundo intento de carga con los mismos parámetros del archivo fuente (`source_sha256 = '54f793f68f33...'`).  
   - **Resultado:** Interrupción inmediata por el motor SQLite: `sqlite3.IntegrityError: UNIQUE constraint failed: historico_lotes.source_sha256`.
   - **Garantía:** Es imposible que una ejecución accidental duplique registros históricos.
2. **Chequeo de Integridad Relacional:**  
   - Comando: `PRAGMA integrity_check`.
   - Resultado: **`ok`**. Cero páginas corruptas, cero índices inconsistentes.

---

## 6. LIMITACIONES IDENTIFICADAS Y DECISIONES TÉCNICAS

1. **Ambigüedad en parejas de cargas simultáneas:**  
   Al no poseer número de tarjeta ni usuario, se mantiene la decisión de conservar las 24 filas ($124.600) identificándolas con `es_posible_duplicado = 1`, permitiendo al usuario emitir reportes con o sin estas operaciones.
2. **Subtotales de pesables sin cantidad física:**  
   1.220 líneas ($3.093.360) conservan `cantidad IS NULL`. El sistema muestra el total en dinero recaudado por esos productos, pero advierte que el cálculo de kilos/unidades físicas vendidas es parcial.
3. **Ausencia de costo unitario histórico:**  
   Se ratifica que no es factible reconstruir márgenes pasados de ganancia sin inventar costos de compra de 2025.

---

## 7. CONFIRMACIÓN FINAL DE SEGURIDAD

- **El archivo Excel original no sufrió modificaciones** (Hash SHA-256 verificado: `54f793f68f337a625d72bf1d195da59ee7c62dd2bbc0119917e791f7bb3b48ee`).
- **No se realizaron escrituras en ninguna base de datos física** (ni Turso, ni local en disco, ni servidores remotos).
- **No se modificó ningún módulo del código operativo existente**.
- **No se ejecutaron commits ni push en Git**.

---
*Fin del Informe Técnico de Simulación. Se aguarda la revisión y autorización expresa del propietario para las siguientes etapas.*
