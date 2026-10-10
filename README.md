# Genaro POS — documentación técnica completa

**Versión documentada:** V4.6 auditada (paquete final)  
**Idioma e importe:** español; pesos representados como `$`  
**Zona horaria de la aplicación web:** UTC−03:00 (Argentina/Jujuy)  
**Interfaz web:** Python + Streamlit  
**Almacenamiento compartido:** Google Sheets  
**Integración adicional:** Google Apps Script vinculado a la planilla

Este README describe la arquitectura, las hojas de cálculo, los módulos web, las reglas contables, el Apps Script, la instalación, la operación y las limitaciones conocidas. La versión final conserva los flujos de trabajo existentes; la optimización funcional aplicada en esta revisión es evitar una limpieza global de todas las cachés cuando se corrige el historial de cargas, ya que solo es necesario invalidar la caché de cargas usada por el indicador PREPAY.

---

## 1. ¿Qué es Genaro POS?

Genaro POS es una aplicación de punto de venta y administración para un comercio minorista. Permite registrar ventas de productos, cobros en efectivo, transferencias y pagos mixtos; registrar cargas virtuales y sus adicionales; mantener el catálogo; editar historiales; y consultar un resumen diario.

El sistema tiene dos interfaces que comparten datos:

1. **Aplicación web Streamlit (`app.py`).** Es el punto de venta web que se abre en computadora o celular. Lee y escribe las tablas de datos de Google Sheets mediante `GSheetsConnection`.
2. **Google Sheets + Apps Script.** La planilla contiene tablas, vistas auxiliares y fórmulas. El Apps Script permite operar ciertas hojas directamente desde celdas/botones, por ejemplo la hoja `CAJA`, `SERVICIOS` y `ADMIN_PRODUCTOS`.

Ambas interfaces no son una sola transacción coordinada: comparten hojas, pero cada una escribe mediante su propio código. Por eso conviene evitar que dos personas modifiquen exactamente la misma tabla al mismo tiempo.

### Flujo general

```text
Usuario (celular o computadora)
          |
          v
    Streamlit / app.py ----------------------+
          |                                  |
          | GSheetsConnection                | Google Sheets
          v                                  v
   DB_PRODUCTOS                        Tablas compartidas
   DB_MOVIMIENTOS_CAJA                       ^
   DB_HISTORIAL_ITEMS                         |
   DB_CARGAS                                  | Apps Script ligado
   GASTOS_RETIROS                              | a la planilla
                                               v
                               CAJA / SERVICIOS / ADMIN_PRODUCTOS
                               procesarTicket / procesarCarga / CRUD
```

Las hojas `DB_*` son las tablas de datos. Las hojas `CAJA`, `SERVICIOS`, `ADMIN_PRODUCTOS` y `VISOR` también pueden funcionar como formularios/vistas de la planilla y son utilizadas por el Apps Script y/o por las fórmulas existentes.

---

## 2. Archivos del paquete

```text
genaro_pos_final_bundle/
├── app.py                      # Aplicación web Streamlit
├── requirements.txt            # Dependencias Python
├── README.md                   # Este manual
├── AppsScript_Referencia.gs    # Copia de referencia del script compartido
└── .streamlit/
    └── config.toml             # Tema claro y colores globales
```

El Apps Script está guardado en el editor de Apps Script asociado a Google Sheets. El archivo `AppsScript_Referencia.gs` es una copia de referencia del código que compartiste, sin cambios funcionales; no hace falta importarlo de nuevo para usar `app.py`. Si el proyecto vinculado a Sheets contiene cambios posteriores, conserva el script que está en producción como fuente de verdad.

### `app.py`

Contiene el frontend, la lógica de caja y servicios, los cálculos de los resúmenes, la conexión con Google Sheets, los editores de historial, el catálogo y el enrutador del menú lateral.

### `.streamlit/config.toml`

Configura el tema claro de Streamlit:

- color principal azul `#2563EB`;
- fondo gris claro `#F3F6FA`;
- superficies blancas;
- texto azul oscuro `#102033`.

El CSS de `app.py` complementa este archivo y da estilo a botones, contenedores, tablas, tarjetas e indicadores. Los estilos se adaptan a pantallas angostas para permitir operar desde el celular.

### `requirements.txt`

Lista las bibliotecas externas que necesita el proyecto. En el entorno de producción se deben conservar versiones compatibles con el deployment actual de Streamlit.

---

## 3. La planilla de Google Sheets

La aplicación web usa la URL definida por la constante `URL_PLANILLA` en `app.py`. Si se cambia de planilla, debe actualizarse allí y verificar también el proyecto de Apps Script si está vinculado a otra planilla.

### 3.1 `DB_PRODUCTOS` — catálogo maestro

| Columna | Uso |
|---|---|
| `ID_PRODUCTO` | Identificador del producto. |
| `NOMBRE` | Nombre visible y principal campo del buscador. |
| `CATEGORIA` | Agrupación del producto. |
| `PROVEEDOR` | Marca, proveedor o preventista. |
| `UNIDAD` | Unidad de venta: Unidad, Kg, Litro u otra configurada. |
| `COSTO` | Costo de compra. |
| `MARGEN_%` | Margen expresado como proporción en la hoja, por ejemplo `0.30` para 30 %. |
| `PRECIO_DIA` | Precio usado por la Caja web. |
| `PRECIO_NOCHE` | Precio nocturno guardado por los formularios de administración, aunque la caja web descrita utiliza `PRECIO_DIA`. |
| `FECHA_ACT` | Fecha de la última actualización de datos del producto. |

La web carga el catálogo desde `DB_PRODUCTOS`. Para que el buscador sea rápido, normaliza y precalcula texto de búsqueda para nombre, proveedor, categoría, unidad e ID. Esas columnas auxiliares se crean en memoria y **no son columnas que deban agregarse a la planilla**.

### 3.2 `DB_MOVIMIENTOS_CAJA` — cabecera de cada venta/ticket

| Columna | Uso |
|---|---|
| `TICKET_ID` | Identificador único por ticket, normalmente generado con la marca de tiempo. |
| `FECHA` | Fecha y hora del ticket. Puede leerse como fecha de Sheets o como texto. |
| `TOTAL_VENTA` | Importe total del ticket. |
| `MONTO_EFECTIVO` | Parte cobrada en efectivo. |
| `MONTO_TRANSF` | Parte cobrada por transferencia. |
| `ES_NOCTURNO` | Indicador de venta nocturna. La Caja web actual registra `False`. |

Esta tabla es la cabecera contable de las ventas. Los resúmenes diarios obtienen de aquí los totales de ventas, efectivo y transferencias.

### 3.3 `DB_HISTORIAL_ITEMS` — detalle de cada ticket

| Columna | Uso |
|---|---|
| `TICKET_ID` | Vincula el artículo con su ticket de `DB_MOVIMIENTOS_CAJA`. |
| `FECHA` | Fecha y hora de venta del artículo. |
| `PRODUCTO` | Nombre vendido; también determina si corresponde al indicador de impresiones/fotocopias. |
| `CANTIDAD` | Cantidad capturada. |
| `UNIDAD` | Unidad registrada para el ítem. |
| `PRECIO_UNIT` | Precio unitario guardado al cobrar. |
| `SUBTOTAL` | Importe total de esa línea. |
| `METODO_PAGO` | Efectivo, transferencia o mixto. En pago mixto, el desglose exacto está en el ticket padre. |

La relación clave entre ventas y detalle es `TICKET_ID`. La edición del historial puede recalcular el importe del ticket padre usando la suma de los subtotales que sobreviven.

### 3.4 `DB_CARGAS` — recargas y servicios

| Columna | Uso |
|---|---|
| `FECHA` | Fecha y hora de la carga. |
| `SERVICIO` | Claro, Personal, Movistar, Tuenti, DIRECTV, SUBE, SUBE (MP), Otro, etc. |
| `MONTO_CARGA` | Capital entregado a la plataforma/proveedor. No incluye la ganancia adicional. |
| `MONTO_ADICIONAL` | Recargo cobrado; el sistema lo trata como ganancia/adicional. |
| `TOTAL_COBRADO` | Capital más adicional. |
| `PAGO_EFVO` | Parte recibida en efectivo del cliente. |
| `PAGO_TRANSF` | Parte recibida por transferencia del cliente. |

Es importante no confundir `MONTO_CARGA` con `TOTAL_COBRADO`: el primero es capital y el segundo incorpora el adicional.

### 3.5 `GASTOS_RETIROS` — salidas de dinero

La web consulta esta hoja desde el Visor. Las columnas esperadas son:

| Columna | Uso |
|---|---|
| `FECHA` | Fecha del gasto o retiro. |
| `TIPO` | Clase del gasto. |
| `DETALLE` | Descripción libre. |
| `MONTO_SALIDA` | Importe de salida. |
| `ORIGEN_FONDOS` | Origen al que se imputa el gasto. |
| `METODO_PAGO` | `EFECTIVO` o `TRANSFERENCIA`; se utiliza para separar salidas en el visor. |

El código descrito consulta los gastos para el dashboard. El alta/edición de gastos debe realizarse en el flujo que ya tengan configurado en la planilla; este `app.py` no agrega un formulario web nuevo de gastos.

### 3.6 Hojas de interfaz de la planilla

- **`CAJA`:** formulario en hoja de cálculo para capturar productos, total, efectivo y transferencia. Apps Script lee rangos fijos, por ejemplo `F16`, `F17`, `F18`, `C1` y las líneas `A4:F15`.
- **`SERVICIOS`:** formulario de recargas usado por Apps Script con celdas como `C2`, `C4`, `C6`, `C8`, `C9` y `C10`.
- **`ADMIN_PRODUCTOS`:** formulario en hoja para modificar, crear y borrar productos con celdas fijas.
- **`VISOR`:** vista de resumen en la propia planilla con fórmulas, rangos y estilos. Es distinta del visor visual dibujado por la web aunque ambos leen las mismas tablas.

**No cambiar las columnas o posiciones que utiliza el Apps Script** sin actualizar sus referencias: ese código depende de coordenadas de celdas y números de columna.

---

## 4. Aplicación web: tecnología y organización

### Tecnologías

- **Python:** lenguaje de la aplicación.
- **Streamlit:** construye las páginas, formularios, navegación, tablas editables y diálogos de pago.
- **pandas:** trata hojas de Google Sheets como DataFrames; filtra, agrega y combina filas.
- **streamlit-gsheets (`GSheetsConnection`):** conector utilizado para leer y actualizar las hojas.
- **st-keyup (`st_keyup`):** campo de búsqueda reactivo que actualiza los resultados mientras se escribe.
- **CSS y HTML controlado desde `st.markdown`:** diseño de tema claro, tarjetas visuales, resumen de caja, resultados del buscador y las seis cajas del visor.

### Configuración regional

`ZONA_AR` define un desplazamiento fijo de UTC−03:00. `ahora_ar()` se usa para la hora de la aplicación; `fecha_texto()` genera el formato `dd/mm/aaaa hh:mm:ss`, y `fecha_act_texto()` el formato `dd/mm/aaaa`.

`normalizar_fecha_columna()` permite filtrar tanto fechas en texto como fechas devueltas como valores de fecha/hora o seriales típicos de Excel/Sheets. Esto importa porque una misma columna puede llegar en formatos distintos según cómo se haya registrado el valor.

### Estado de la sesión

`st.session_state` mantiene el carrito y las variables de formularios entre los reruns de Streamlit. Algunas claves terminan en `_key` y se incrementan después de guardar para crear widgets nuevos y restablecer formularios/editor.

### Caché

- `cargar_productos()` mantiene el catálogo durante un máximo de 600 segundos para que el buscador no descargue cientos de filas desde Sheets en cada tecla. La propia aplicación limpia esta caché después de altas, bajas o modificaciones de productos.
- Los movimientos, ítems y cargas utilizados en los indicadores usan cachés cortas de 15 segundos para no leer miles de filas en cada rerun.
- Después de guardar ventas, se invalidan las cachés de movimientos e ítems; luego de registrar/corregir cargas se invalida la caché de cargas; después de editar historial de tickets se invalidan movimientos e ítems.
- En la versión final, guardar cambios en `DB_CARGAS` deja intacta la caché de productos, porque no está relacionada con esa edición.

---

## 5. Módulos de navegación

El menú lateral contiene siete módulos. Los nombres y el flujo funcional se conservan entre versiones.

### 5.1 🛒 Caja registradora

1. Carga el catálogo de `DB_PRODUCTOS`.
2. Muestra el **Resumen de hoy** a partir de las tablas transaccionales.
3. Permite buscar productos y añadirlos al carrito.
4. El usuario puede modificar cantidad o subtotal por línea.
5. Calcula el total del carrito.
6. Permite cobrar en efectivo, transferencia o pago mixto.
7. Escribe la cabecera del ticket en `DB_MOVIMIENTOS_CAJA` y el detalle en `DB_HISTORIAL_ITEMS`.
8. Limpia el carrito después de una escritura exitosa.

#### Buscador

La búsqueda se normaliza en minúsculas, sin tildes y con puntuación convertida a espacios. Por eso términos como `coca`, `Coca Cola`, `cocacola` o `diversion` pueden encontrar resultados con espacios, acentos o guiones distintos. Busca primero en `NOMBRE`; cuando no hay coincidencias, amplía a proveedor, categoría, unidad e ID. La búsqueda devuelve hasta 15 resultados y prioriza coincidencias del nombre.

#### Carrito

El carrito vive en la sesión y admite varios renglones. Cambiar la cantidad recalcula el subtotal. Cambiar el subtotal actualiza una cantidad estimada con base en el precio unitario; si el subtotal no es múltiplo del precio, esa cantidad es una aproximación y el subtotal introducido se conserva como importe de línea.

#### Medios de pago

- **Efectivo:** todo el total se guarda en `MONTO_EFECTIVO`.
- **Transferencia:** todo el total se guarda en `MONTO_TRANSF`.
- **Mixto:** el diálogo pide la transferencia y calcula el efectivo restante.

El ticket guarda el total y el desglose de ambos medios en `DB_MOVIMIENTOS_CAJA`. Los artículos del ticket llevan `METODO_PAGO="MIXTO"` cuando el cobro fue mixto; por eso, a nivel de ítem, no hay un desglose exacto de qué parte de cada producto se abonó con cada medio.

#### Resumen de hoy

El resumen contiene:

- **Ventas:** suma de `TOTAL_VENTA` del día en `DB_MOVIMIENTOS_CAJA`.
- **Efectivo:** suma de `MONTO_EFECTIVO`.
- **Transferencias:** suma de `MONTO_TRANSF`.
- **PREPAY — Solo capital:** suma de `MONTO_CARGA` de servicios que no sean Claro ni `SUBE (MP)`. No suma los adicionales. El capital de cada carga se separa entre efectivo y transferencia proporcionalmente a `PAGO_EFVO` y `PAGO_TRANSF`; si el registro antiguo no tiene detalle de pago, el código lo asigna a efectivo por defecto.
- **Impresiones + fotocopias:** suma de `SUBTOTAL` únicamente para estos ocho nombres, normalizados sin tildes y puntuación:
  1. `Impresion a color`
  2. `Impresion a color (doble faz)`
  3. `Impresion a color en papel cartulina`
  4. `Impresion a color en papel fotografico`
  5. `Impresion en ByN`
  6. `Impresion en ByN (doble faz)`
  7. `Fotocopia (DNI, Carnet)`
  8. `Fotocopia comun`

  Los medios de pago de ese indicador se asignan usando `METODO_PAGO` cuando es efectivo o transferencia. Para tickets mixtos, se calcula la proporción del efectivo frente a la transferencia del ticket padre y se reparte cada subtotal en esa proporción. La suma de efectivo y transferencia queda igual al subtotal; es un reparto estimado proporcional, no un dato de pago a nivel de artículo que el sistema no almacena.

### 5.2 📱 Cargas y servicios

1. Selecciona el servicio.
2. Introduce `MONTO_CARGA`.
3. La aplicación sugiere un adicional; puede editarse manualmente.
4. Elige efectivo, transferencia o mixto.
5. La aplicación calcula `TOTAL_COBRADO = MONTO_CARGA + MONTO_ADICIONAL`.
6. Al registrar, agrega una fila a `DB_CARGAS`.

#### Fórmula del adicional automático

El adicional sugerido se calcula en tramos de $2.000:

`ceil(MONTO_CARGA / 2000) × 100`

Ejemplos: $1.000 → $100; $2.000 → $100; $2.001 → $200. El adicional no se calcula como porcentaje: es una regla fija de $100 por cada tramo/fracción de $2.000. El usuario puede modificar el monto sugerido antes de guardar.

#### `SUBE (MP)`

`SUBE (MP)` significa que el capital de la carga se toma del saldo de Mercado Pago del Drugstore. Se diferencia del circuito PREPAY y del circuito de Claro:

- no suma su `MONTO_CARGA` al capital PREPAY;
- no suma su `MONTO_CARGA` al capital Claro;
- su `MONTO_ADICIONAL` sí se contabiliza en los adicionales de Caja C;
- en el cálculo de transferencias del visor, su capital se resta del neto bancario para reflejar el uso del saldo de Mercado Pago.

### 5.3 📋 Historial de cargas

Permite filtrar por fecha, corregir valores, borrar filas y agregar nuevas filas mediante `st.data_editor`.

Al guardar:

1. Lee el estado del editor, incluida la lista `deleted_rows` con posiciones de fila.
2. Mapea esas posiciones a los índices reales de la tabla filtrada.
3. Quita las filas eliminadas.
4. Actualiza filas que sobreviven y añade las nuevas.
5. Escribe nuevamente `DB_CARGAS`.
6. Invalida la caché de cargas para refrescar PREPAY y ejecuta un rerun.

**Uso correcto:** después de seleccionar una fila y eliminarla en el editor, siempre presionar **Guardar Cambios en Cargas**. Borrar visualmente una fila sin guardar no modifica permanentemente la hoja.

### 5.4 ⚙️ Administración de productos

Incluye actualización rápida, alta y eliminación del catálogo.

- **Actualizar:** modifica proveedor, costo, precio de día, precio de noche y margen estimado; registra fecha de actualización.
- **Crear:** solicita nombre, categoría, proveedor, unidad, costo y precio. Valida que el nombre no esté vacío y que el precio sea mayor a cero; genera el siguiente ID a partir del ID numérico máximo leído.
- **Eliminar:** requiere marcar una casilla de confirmación antes de borrar la fila.

Después de una operación exitosa, la caché del catálogo se invalida para que Caja lea los precios actualizados.

### 5.5 📜 Historial de ítems

Permite filtrar el detalle por fecha y texto del producto, modificar subtotales y eliminar renglones.

Al guardar:

1. Reconstruye las filas de ítems, incorporando cambios y borrados.
2. Obtiene los `TICKET_ID` afectados.
3. Vuelve a sumar los subtotales supervivientes de cada ticket.
4. Actualiza `TOTAL_VENTA` en `DB_MOVIMIENTOS_CAJA`.
5. Ajusta el desglose de efectivo y transferencia por la diferencia, usando el criterio actual de restar primero del efectivo y luego de la transferencia cuando el importe baja; cuando sube, lo suma al medio que corresponda con la lógica del ticket actual.
6. Si el ticket queda sin subtotal positivo, elimina el registro padre de caja.
7. Actualiza las hojas y limpia las cachés relacionadas.

Es un mecanismo de corrección en cascada. Los datos de caja y detalle se escriben por separado, por lo que siempre conviene comprobar que ambos guardados hayan terminado y que el ticket conserve el importe deseado.

### 5.6 📊 Visor / Dashboard

Es una vista web separada del resumen pequeño de Caja. Lee `DB_MOVIMIENTOS_CAJA`, `DB_CARGAS` y `GASTOS_RETIROS`, y permite seleccionar la fecha a consultar.

- **Caja A — Drugstore:** total vendido, efectivo, transferencias y estimación de ganancia del 10 %.
- **Caja B — capital PREPAY/otros servicios:** incluye el capital de servicios que no sean Claro ni `SUBE (MP)`; excluye recargos y adicional.
- **Caja C — adicionales:** suma `MONTO_ADICIONAL` de las cargas. El código actual asigna el adicional a transferencia solo por la parte de `PAGO_TRANSF` que excede `MONTO_CARGA`; el resto se muestra como efectivo. Es la convención existente del visor, no un desglose exacto que esté almacenado por separado.
- **Caja D — transferencias:** muestra transferencias de ventas, transferencias de recargas y total bancario calculado con la fórmula programada, incluyendo el descuento del capital de `SUBE (MP)` y los gastos por transferencia.
- **Caja E — Claro:** muestra el capital de Claro por efectivo y transferencia, sin los adicionales.
- **Gastos / Retiros:** suma las salidas por `METODO_PAGO` y muestra efectivo, transferencia y total.

El selector filtra las fechas normalizadas de las tres tablas. La hoja de cálculo `VISOR` puede tener sus propias fórmulas; la vista web calcula los valores en Python según el código actual.

### 5.7 🚚 Preventistas

- Filtra productos por `PROVEEDOR`.
- Permite editar `COSTO` y `PRECIO_DIA` en una tabla.
- Recalcula en pantalla el margen proyectado (`(precio − costo) / costo × 100`) usando el estado del editor.
- Al guardar, actualiza costo, precio de día, precio de noche, margen y fecha.
- Incluye un formulario colapsable para crear un producto ligado al proveedor seleccionado.

La tabla muestra el margen en porcentaje, pero la hoja conserva `MARGEN_%` como proporción decimal. La conversión entre ambas representaciones se realiza en la interfaz.

---

## 6. Fórmulas y convenciones del sistema

### Dinero

Los importes de venta y carga se tratan principalmente como números enteros en pesos. El helper `numero_seguro()` evita fallos por valores vacíos/no numéricos, `entero_seguro()` convierte valores para su uso como importe entero y `sumar_numerico()` suma columnas convertidas a número.

### Ganancia estimada de ventas

El Visor calcula la estimación de Caja A como:

`GANANCIA ESTIMADA = TOTAL_VENTAS_DRUGSTORE × 10 %`

Es una estimación fija sobre ventas, no una utilidad neta contable que tenga en cuenta costos de productos.

### Capital y adicional de una carga

- Capital: `MONTO_CARGA`.
- Ganancia/adicional: `MONTO_ADICIONAL`.
- Cobro al cliente: `TOTAL_COBRADO`.

PREPAY y Claro muestran capital sin adicionales. Caja C suma los adicionales. `SUBE (MP)` no suma su capital a PREPAY ni a Claro.

### Fecha

Las hojas pueden entregar fecha como `datetime`, serial numérico de Sheets/Excel o texto. La función común de normalización crea una columna temporal `FECHA_REAL` solo en memoria para filtrar; esa columna no debe escribirse de regreso en las tablas.

---

## 7. Google Apps Script: cómo funciona el código vinculado

El Apps Script es un segundo punto de operación para trabajar directamente sobre las hojas de cálculo. Los rangos mencionados son posiciones fijas en las hojas actuales: cambiarlas requiere actualizar el script.

### `onEdit(e)`

Es el punto de entrada del trigger simple. Si no hay evento, sale; si existe, delega en `triggerMobile(e)`. No procesa todos los cambios de datos: su objetivo principal es escuchar las celdas que funcionan como botones.

### `triggerMobile(e)`

Revisa el nombre de la hoja, fila, columna y valor de la celda editada:

- En `CAJA`, la celda `B18` con el valor `🚀 COBRAR` dispara `procesarTicket()` si hay un total mayor que cero.
- En `SERVICIOS`, la celda `E2` con el valor `📱 REGISTRAR` dispara `procesarCarga()` si hay un servicio elegido.
- En ambos casos, después de intentar la operación, limpia la celda del botón y llama a `SpreadsheetApp.flush()`.

### `procesarTicket()`

1. Lee `CAJA!F16` como total, `F17` como transferencia, `F18` como efectivo y `C1` como indicador nocturno.
2. Valida que el ticket tenga total y que el efectivo no sea negativo.
3. Determina la etiqueta del pago: `EFECTIVO`, `TRANSFERENCIA` o `MIXTO`.
4. Lee los renglones `A4:F15` e identifica los renglones con nombre y subtotal positivo.
5. Añade las filas de detalle en `DB_HISTORIAL_ITEMS`.
6. Añade la cabecera a `DB_MOVIMIENTOS_CAJA`.
7. Limpia las zonas de captura de la hoja `CAJA` y deja el cursor en `A4`.

### `procesarCarga()`

1. Lee servicio, capital, adicional, total, transferencia y efectivo desde las celdas `SERVICIOS!C2`, `C4`, `C6`, `C8`, `C9` y `C10`.
2. Valida que el servicio exista y que el capital sea mayor que cero.
3. Agrega una fila en `DB_CARGAS` con fecha actual y los siete campos esperados.
4. Limpia las celdas de captura principales y presenta un mensaje de éxito o error.

### `actualizarProducto()`

Lee el producto seleccionado desde `ADMIN_PRODUCTOS!C4`, los campos de actualización desde `E6:E9`, busca por nombre en la columna B de `DB_PRODUCTOS`, actualiza las columnas de proveedor/costo/precios/fecha que corresponden y limpia los campos de edición.

### `crearProducto()`

Lee el formulario `ADMIN_PRODUCTOS!H4:H10`, busca una fila disponible en `DB_PRODUCTOS`, escribe nombre, categoría, proveedor, unidad, costo y precios, guarda la fecha y limpia el formulario.

**Punto a revisar en mantenimiento futuro:** el script proporcionado escribe explícitamente las columnas B, C, D, E, F, H, I y J al crear un producto, pero no asigna explícitamente `ID_PRODUCTO` en A ni `MARGEN_%` en G. Si esas columnas no se completan mediante fórmulas u otro mecanismo existente en la hoja, la alta desde Apps Script podría dejar esos campos vacíos. No se modificó aquí porque esta entrega documenta el sistema sin cambiar el Apps Script ni la funcionalidad.

### `eliminarProducto()`

Lee el producto seleccionado, exige que `ADMIN_PRODUCTOS!E14` sea `TRUE`, busca el nombre en `DB_PRODUCTOS` y elimina la fila. Luego limpia el formulario y desmarca la confirmación.

### Diferencia entre Apps Script y la web

La web escribe usando `GSheetsConnection` y DataFrames; el Apps Script escribe directamente con los métodos de `SpreadsheetApp` (`appendRow`, `setValues`, `setValue`, `deleteRow`). Comparten las tablas, pero **no comparten una transacción atómica ni bloqueo coordinado**. No conviene ejecutar modificaciones concurrentes sobre la misma tabla.

---

## 8. Instalación y puesta en marcha

### Ejecución local

1. Instalar Python compatible con la versión de Streamlit utilizada en producción.
2. Descargar y descomprimir el paquete.
3. Instalar dependencias:

   ```bash
   pip install -r requirements.txt
   ```

4. Configurar el acceso a Google Sheets usando los secretos/credenciales de Streamlit del entorno que ya está conectado. La cuenta o service account utilizada debe tener acceso a la planilla.
5. Iniciar la aplicación desde la carpeta que contiene `app.py`:

   ```bash
   streamlit run app.py
   ```

### Despliegue en Streamlit Cloud u otro servicio

- Subir `app.py`, `requirements.txt`, `.streamlit/config.toml` y este README al repositorio.
- Conservar en el panel de secretos del servicio la configuración sensible de conexión que ya funciona en el despliegue actual.
- No subir claves privadas, tokens, archivos de credenciales ni `.streamlit/secrets.toml` al repositorio público.
- Seleccionar `app.py` como archivo principal.
- Verificar el acceso a todas las hojas que usa la aplicación.

### Cambio de planilla

Si se utiliza una planilla distinta:

1. Actualizar `URL_PLANILLA`.
2. Dar permiso de acceso a la identidad usada por `GSheetsConnection`.
3. Mantener los nombres de las hojas y encabezados esperados, o adaptar el código de forma coordinada.
4. Revisar también la planilla vinculada al Apps Script y el código de ese proyecto.

---

## 9. Operación diaria recomendada

1. **Antes de vender:** verificar que el catálogo cargue y que los precios sean correctos.
2. **Al cobrar:** revisar el total y, en pagos mixtos, introducir correctamente la transferencia; la aplicación calcula el efectivo restante.
3. **Al registrar una carga:** seleccionar el servicio correcto (`SUBE` y `SUBE (MP)` son categorías distintas), introducir el capital y verificar el adicional.
4. **Al corregir un historial:** seleccionar la fecha correcta, editar/borrar y siempre presionar Guardar.
5. **Después de correcciones:** abrir Resumen/Visor y comprobar que los valores reflejen el registro corregido.
6. **Al cambiar productos desde otra interfaz:** tener presente que la caché del catálogo web puede permanecer hasta 10 minutos si el cambio no se realiza desde esta propia app, que sí invalida la caché cuando guarda.
7. **Cierres:** comparar cabeceras de tickets en `DB_MOVIMIENTOS_CAJA` con sus detalles en `DB_HISTORIAL_ITEMS` si una operación falló a mitad de la escritura.

---

## 10. Auditoría final y pruebas

En la versión de entrega se hicieron comprobaciones locales de:

- compilación/sintaxis de Python;
- existencia de los siete módulos y del enrutador del menú;
- búsqueda de productos por nombre, términos compuestos, texto sin acentos, proveedor, categoría e ID;
- cálculo de PREPAY sin incluir Claro ni `SUBE (MP)`;
- filtrado de los ocho nombres exactos de impresión/fotocopia;
- separación de impresiones/fotocopias entre efectivo y transferencia para pagos directos y mixtos;
- estructura del paquete y configuración de tema claro.

Estas pruebas son pruebas locales de código y funciones puras. No equivalen a una prueba end-to-end contra la cuenta real de Google Sheets: el acceso en vivo depende de la configuración de credenciales y permisos del entorno donde se despliegue.

---

## 11. Limitaciones conocidas y mantenimiento seguro

### Escritura por lectura + sobrescritura

Algunas operaciones web leen toda una tabla, modifican el DataFrame y envían la tabla completa con `conn.update`. Es sencillo y coincide con la arquitectura actual, pero dos usuarios editando simultáneamente la misma hoja podrían sobrescribirse cambios. El Apps Script utiliza a menudo `appendRow`, por lo que mezclar escrituras simultáneas de ambas interfaces también puede producir conflictos.

### Guardados en varias hojas

Una venta web escribe primero `DB_MOVIMIENTOS_CAJA` y después `DB_HISTORIAL_ITEMS`. Google Sheets no ofrece en este flujo una transacción conjunta entre las dos hojas. Si la segunda escritura falla, la cabecera y el detalle pueden quedar desincronizados y deben revisarse.

### Medios de pago de artículos mixtos

El sistema guarda el desglose de efectivo/transferencia a nivel de ticket. No conserva cuánto de cada medio se aplicó a cada artículo dentro del ticket mixto. Por eso el indicador de impresiones/fotocopias reparte proporcionalmente esos subtotales, y debe interpretarse como una asignación estimada del medio de pago, aunque el total de impresiones sí sale de los subtotales registrados.

### Ganancia estimada

El 10 % de Caja A es una estimación sobre ventas y no una contabilidad de utilidad neta. El adicional de las cargas se gestiona como ganancia según las reglas existentes del negocio. Costos, impuestos, comisiones de plataformas y diferencias de caja no se calculan como un estado contable integral.

### Nombres de servicio y producto

- `Claro` se clasifica separadamente.
- `SUBE (MP)` se excluye del capital PREPAY y Claro; su adicional sigue sumando a los adicionales.
- Los ocho nombres de impresiones/fotocopias están definidos como una lista exacta normalizada. Si se renombran en `DB_PRODUCTOS` o se venden con una descripción diferente, se debe actualizar la lista `PRODUCTOS_IMPRESIONES_RESUMEN` de `app.py`.

### No cambiar sin revisar

- nombres de hojas;
- encabezados de columnas;
- claves de sesión que permiten resetear widgets;
- rangos de celdas que usa Apps Script;
- nombres y clasificación de los servicios;
- funciones que recalculan cabecera y detalle de un ticket.

Estos elementos forman parte del contrato interno entre las dos interfaces y los datos existentes.

---

## 12. Lista rápida de diagnóstico

| Síntoma | Qué revisar |
|---|---|
| El catálogo aparece vacío | Permisos de la conexión, nombre `DB_PRODUCTOS`, columna `NOMBRE` y estado de Google Sheets. |
| Un producto no aparece en la búsqueda | Nombre actual en `DB_PRODUCTOS`; proveedor/categoría; que el producto no esté vacío; caché del catálogo de 600 s. |
| Una carga no aparece en Resumen/Visor | Que se haya guardado en `DB_CARGAS`, la fecha, `SERVICIO`, y que se haya presionado Guardar en historial después de una corrección. |
| El indicador PREPAY no incluye una carga | Confirmar que el servicio no esté escrito como Claro ni como `SUBE (MP)`; solo se suma capital de servicios incluidos. |
| Las impresiones no coinciden | Revisar el nombre exacto del producto, `SUBTOTAL`, `FECHA`, `TICKET_ID` y el desglose del ticket si fue mixto. |
| El total del ticket no coincide con el detalle | Revisar `DB_MOVIMIENTOS_CAJA` y `DB_HISTORIAL_ITEMS` por `TICKET_ID`; es posible que haya fallado un guardado parcial. |
| El Apps Script no responde al botón | Confirmar la hoja/celda exacta, el texto del botón, permisos y que el script siga vinculado a esa planilla. |
| Se ve un dato antiguo del producto | Esperar el TTL de 600 s o actualizarlo desde la interfaz web, que invalida la caché de catálogo tras guardar. |

---

## 13. Principio de diseño

Genaro POS se mantiene deliberadamente simple: Google Sheets cumple la función de almacenamiento compartido y la aplicación web actúa como interfaz de uso diario. La prioridad de mantenimiento es conservar los datos existentes, evitar consultas repetidas innecesarias y mejorar legibilidad sin complicar el flujo del comercio.

Antes de incorporar funciones nuevas, conviene probar en una copia de la planilla los cambios que afecten las operaciones de escritura, cálculos de caja, borrado de historial o el Apps Script. Para los cambios puramente visuales, mantener el CSS concentrado en `aplicar_estilos_profesionales()` reduce el riesgo de afectar la lógica contable.
