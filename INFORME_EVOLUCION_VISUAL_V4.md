# INFORME DE EVOLUCIÓN VISUAL PREMIUM V4
## Módulos: Caja, Productos y Servicios — GenaroPOS
**Fecha:** 6 de Noviembre de 2026  
**Rama de trabajo:** `release/produccion-2026-11-03`  
**Commit base:** `31d3316` (*feat(caja): redeseno visual premium v3 dark terracota, ergonomia responsive y checkout optimizado*)  
**Estado de Producción:** Intacto y no modificado (Despliegues Cloudflare Pages / Workers protegidos sin alteración)

---

## A. ESTADO INICIAL DEL REPOSITORIO

Antes de iniciar la intervención, se realizó una inspección completa del entorno local:
1. **Directorio de trabajo:** `/home/alanrod10/workspace/genaro-pos-turso-lab`
2. **Rama Git activa:** `release/produccion-2026-11-03`
3. **Commit inicial:** `31d33169720ad635ef8ce857da444e27f311c8cb`
4. **Estado de cambios previos:** Working tree limpio (`working tree clean`), sin modificaciones pendientes ni stashes activos.
5. **Entorno de producción en vivo verificado:**
   - Worker API: `https://genaro-pos-live-api.genaro-pos.workers.dev` (configurado en `frontend/.env.production`).
   - Tag de referencia operativa: `v-baseline-05155405`.
   - Se verificó que ninguna clave secreta ni comando destructivo fuera invocado.
6. **Estado de pruebas baseline:** 40 suites de prueba con 383/383 tests superados en Vitest (`Duration: 6.8s`).
7. **Estado de compilación baseline:** `tsc && vite build` completado exitosamente sin advertencias de sintaxis.

---

## B. COMMIT Y RAMA UTILIZADOS

- **Rama:** `release/produccion-2026-11-03`
- **Commit Base:** `31d3316`
- **Operaciones Git destructivas:** Cero (`git reset --hard`, `git push --force`, descartes no autorizados fueron estrictamente omitidos).

---

## C. ARCHIVOS INSPECCIONADOS

Se auditaron en profundidad los siguientes componentes y archivos de soporte:
1. `frontend/src/index.css` (Reglas globales, CSS Variables de Caja V3, temas Claro/Oscuro y media queries).
2. `frontend/src/components/ProductCatalog.tsx` (Catálogo adaptativo, buscador integrado, chips de categorías y formateo de unidades sin redundancia).
3. `frontend/src/components/Cart.tsx` (Carrito de compras, filas compactas, controles de cantidad y botón Cobrar Ticket).
4. `frontend/src/components/PaymentModal.tsx` (Modal de cobro, desglose de denominaciones, medios de pago y botón Confirmar Cobro).
5. `frontend/src/components/WeightSaleModal.tsx` (Modal de venta pesable, cálculos de peso y balanceo ergonómico).
6. `frontend/src/components/ProductAdmin.tsx` (Administración de productos, filtros, tablas, tarjetas móviles y formulario modal).
7. `frontend/src/components/ServicesAdmin.tsx` (Módulo de recargas de servicios, dropdowns dependientes, matriz económica y KPIs).
8. `frontend/src/utils/money.ts` (Formato monetario oficial argentino).
9. `frontend/.env.production` (Verificación de endpoints productivos no modificados).

---

## D. ARCHIVOS MODIFICADOS

Para maximizar la seguridad, estabilidad y aislamiento funcional del sistema, todas las optimizaciones visuales y de diseño responsivo se encapsularon en una única capa de estilos sin tocar la lógica de negocio:

1. `frontend/src/index.css`
   - Unificación de tokens de tema claro (*Papel Cálido* oficial) y perfeccionamiento de contraste en tema oscuro (*Dark Premium Terracota*).
   - Rediseño visual y dimensional de `ProductAdmin` (toolbar, buscador, selects, botón nuevo producto, tablas, tarjetas móviles y modal de alta/edición).
   - Rediseño visual y dimensional de `ServicesAdmin` (layout de dos columnas, tarjetas de servicios, desglose visual de fórmula económica, pills de medios de pago, botón Registrar Carga y tarjetas de arqueo segregado).
   - Ajustes responsivos móviles (`<= 800px` y `<= 480px`) con alturas táctiles uniformes de 48px, preservación de `safe-area-inset-bottom` y eliminación de desbordamientos.

---

## E. CAMBIOS VISUALES DEL MÓDULO CAJA

Caja V3 ya contaba con una sólida base que fue preservada y elevada:
1. **Catálogo de Productos:**
   - Proporciones armonizadas y alturas equilibradas con grilla adaptativa (`grid-template-columns: repeat(auto-fill, minmax(180px, 1fr))`).
   - Alineación vertical de títulos, precios con tipografía monoespaciada tabular (`--caja-font-mono`) y botones `+` con feedback táctil inmediato.
   - Chips de categorías con scroll horizontal suave (`overflow-x: auto`) sin desbordar el viewport horizontal ni colapsar la grilla.
2. **Buscador de Caja:**
   - Altura estándar de 48px, fondo elevado con borde sutil `#3A3E46` y foco con halo terracota (`box-shadow: 0 0 0 3px var(--caja-terracotta-soft)`).
   - Icono y placeholder nítidos, integración ergonómica de botón de escaneo y compatibilidad total con lectores físicos de código de barras.
3. **Carrito de Compras:**
   - En escritorio: Ancho generoso y consistente (400px - 440px en pantallas estándar, 480px en Full HD), evitando que los nombres largos queden comprimidos.
   - En móviles: Panel deslizable accesible con botón flotante indicador de artículos y subtotal, apertura instantánea y cierre cómodo sin saltos de layout.
   - Items del carrito estructurados en tres filas ergonómicas: (1) Nombre y eliminar, (2) Precio unitario y subtotal, (3) Controles de cantidad (- / +) con zona táctil mínima de 44px.
4. **Jerarquía del Cobro:**
   - Botón `Cobrar Ticket` destacado en verde institucional `#39B985` (hover `#2FA574`, foco accesible), con tipografía en negrita y altura de 52px.
   - Modal de Cobro con desglose nítido de denominaciones rápidas, medio de pago y feedback claro en confirmación.

---

## F. CAMBIOS VISUALES DEL MÓDULO PRODUCTOS

Se transformó la experiencia de administración de catálogo hacia el estándar Dark Premium Terracota:
1. **Barra de Herramientas y Filtros:**
   - Contenedor unificado con superficie `#25282E`, bordes `#3A3E46` y radio de 12px.
   - Input de búsqueda de 48px de altura con placeholder descriptivo y foco terracota.
   - Selectores de `Categoría` y `Estado` (Activos/Inactivos) estilizados con flechas nativas alineadas, texto contrastante y altura de 48px.
   - Botón `+ Nuevo Producto` con fondo Terracota `#E66F50` (hover `#F18462`), tipografía peso 700 y altura de 48px.
2. **Tabla Administrativa de Escritorio:**
   - Encabezados elevados (`#2B3038`), texto secundario nítido `#AAA6A1` y bordes sutiles entre filas.
   - Badges de estado modernos: Verde tenue (`rgba(57, 185, 133, 0.15)`) para activos, gris tenue para inactivos.
   - Precios destacados en fuente tabular y acciones rápidas (Editar, Estado, Eliminar) con iconos legibles y confirmación visual.
3. **Tarjetas Administrativas Móviles:**
   - Vista optimizada en pantallas menores a 800px: reemplazo automático de la tabla por tarjetas modulares (`.admin-mobile-card`).
   - Cada tarjeta desglosa claramente código, categoría, stock/unidad y acciones táctiles de edición y borrado seguro.
4. **Formulario Modal de Alta y Edición:**
   - Encabezado sticky con título y botón de cierre claro.
   - Scroll interior con campos ordenados en grilla responsiva de 2 columnas (1 columna en móvil).
   - Banner dinámico de cálculo de margen de ganancia proyectado (`margin-preview-banner`) con indicador porcentual destacado.
   - Botones del footer fijos: `Cancelar` en contorno neutro y `Guardar Producto` en terracota sólido con protección ante doble clic.
5. **Formateo de Unidades:**
   - Se mantuvo la corrección arquitectónica: productos por unidad NO presentan sufijo redundante `/ unidad`. Productos pesables conservan `/ kg` o `/ 100 g`, y productos por hoja/paquete conservan su respectivo sufijo.

---

## G. CAMBIOS VISUALES DEL MÓDULO SERVICIOS

Se perfeccionó la interfaz de recargas de servicios para una operación ágil y sin ambigüedades:
1. **Selectores de Origen y Destino:**
   - Dropdown dependiente 1 (`Plataforma de Origen`: PREPAY, CLARO, MERCADOPAGO).
   - Dropdown dependiente 2 (`Servicio de Destino`: filtrado automáticamente según matriz aprobada).
   - Altura de 48px, fondo elevado `#2B3038`, borde `#3A3E46` y foco terracota.
2. **Cálculo Económico y Banner de Total:**
   - Input de monto principal y recargo con etiquetas visuales claras.
   - Chips de montos rápidos ($1.000, $2.000, $3.000, $5.000, $10.000) con estados interactivos.
   - **Banner de fórmula económica destacada:**
     `MONTO ($3.000) + RECARGO ($200) = TOTAL A COBRAR: $3.200`
     Presentado con fondo ámbar/terracota translúcido, borde distintivo y tipografía monoespaciada de alta jerarquía.
3. **Selector de Medio de Pago:**
   - Segmento táctil interactivo (Efectivo, Transferencia, Mixto) con pills ergonómicas que indican el medio seleccionado.
4. **Botón Registrar Carga:**
   - Botón principal de 50px de altura, fondo terracota institucional y texto `Registrar Carga` en negrita con feedback táctil.
5. **Panel Lateral de Arqueo y KPIs de Servicios:**
   - Tarjetas de resumen: `Total Cobrado`, `Capital Total` y `Ganancia Recargos`.
   - Contabilidad segregada de cajas: Prepago (SUBE/Otros), Claro (segregado) y SUBE MP.
   - Desglose de ingresos por medio: Efectivo en Caja vs. Banco/Transferencias.

---

## H. MEJORAS RESPONSIVE Y MOBILE FIRST

1. **Breakpoints Soportados:**
   - **Móvil ultra-compacto (320px - 390px - 480px):** Disposición en 1 sola columna, elementos de ancho completo (100%), alturas mínimas táctiles de 48px, botones de acción fijos y seguros.
   - **Tablets y pantallas intermedias (600px - 768px - 800px):** Reordenamiento de toolbars con flex-wrap ordenado, scroll horizontal controlado en pestañas y tarjetas compactas.
   - **Laptops y pantallas medianas (1024px - 1280px - 1366px):** Proporción balanceada catálogo 65% / carrito 35% en Caja, y formulario 45% / arqueo 55% en Servicios.
   - **Full HD (1920px):** Contenedor centralizado con un ancho máximo de 1280px para evitar dispersión ocular en monitores anchos.
2. **Ergonomía Táctil:**
   - Todos los controles interactivos primarios (botones, inputs, selects, chips de incremento/decremento) respetan la pauta Apple HIG / Google Material de área táctil mínima de 44x44px (estandarizada en 48px).
3. **Áreas Seguras (`safe-area-inset-bottom`):**
   - Padding adicional en footers de modales y barras inferiores flotantes para dispositivos iOS (iPhone con Dynamic Island o Home Bar) y Android gestual.

---

## I. TOKENS DE DISEÑO UTILIZADOS

### Modo Oscuro (Dark Premium Terracota)
| Token | Valor Hex | Función Visual |
|---|---|---|
| `--caja-bg-main` | `#17191D` | Fondo general de la aplicación |
| `--caja-surface-secondary` | `#22252B` | Fondos de contenedores secundarios y paneles |
| `--caja-surface-elevated` | `#2B3038` | Superficie de inputs, selects y encabezados |
| `--caja-surface-card` | `#25282E` | Fondo de tarjetas de producto y módulos |
| `--caja-terracotta` | `#E66F50` | Color primario institucional de acciones |
| `--caja-terracotta-hover` | `#F18462` | Estado hover de acciones primarias |
| `--caja-amber` | `#F19A48` | Alertas, datos simulados y fórmulas |
| `--caja-green-pay` | `#39B985` | Cobros, tickets y confirmaciones monetarias |
| `--caja-green-pay-hover` | `#2FA574` | Estado hover de cobro de tickets |
| `--caja-text-main` | `#F7F4F0` | Texto principal de alto contraste |
| `--caja-text-secondary` | `#AAA6A1` | Etiquetas secundarias e información auxiliar |
| `--caja-border` | `#3A3E46` | Delimitación sutil entre componentes |
| `--caja-border-active` | `#C9684D` | Bordes activos y focos terracota |

### Modo Claro (Papel Cálido)
| Token | Valor Hex | Función Visual |
|---|---|---|
| `--caja-bg-main` | `#FAF7F2` | Fondo cálido natural |
| `--caja-surface-secondary` | `#F5EEE7` | Paneles y tarjetas de segundo nivel |
| `--caja-surface-elevated` | `#FFFDF9` | Superficie de tarjetas e inputs |
| `--caja-terracotta` | `#C64F32` | Terracota principal para modo claro |
| `--caja-terracotta-hover` | `#A83D28` | Hover activo terracota claro |
| `--caja-green-pay` | `#178B69` | Verde de cobro de alto contraste en fondo claro |
| `--caja-border` | `#E5D9CF` | Delimitación sutil tono papel |
| `--caja-text-main` | `#292522` | Texto principal oscuro de óptima legibilidad |
| `--caja-text-secondary` | `#6B635B` | Subtítulos y textos de soporte |

---

## J. PRUEBAS EJECUTADAS Y RESULTADOS EXACTOS

Se ejecutó la suite completa de pruebas unitarias y de integración de GenaroPOS mediante Vitest:

- **Comando:** `npm test` en `frontend/`
- **Suites de prueba:** **40 de 40 pasadas (100%)**
- **Pruebas individuales:** **383 de 383 pasadas (100%)**
- **Errores:** 0
- **Advertencias críticas:** 0
- **Tiempo de ejecución:** 6.95 segundos

### Resumen de Suites Clave Verificadas
1. `tests/admin_products.test.tsx`: 10/10 pasadas (Creación, edición, filtros, validaciones y márgenes).
2. `tests/caja_v3_visual_verification.test.tsx`: 13/13 pasadas (Selectores, tokens CSS, ergonomía de catálogo y carrito).
3. `tests/cart_checkout_behavior.test.tsx`: 6/6 pasadas (Comportamiento de checkout, cálculo de subtotales, vaciado y ajustes).
4. `tests/services.test.tsx`: 10/10 pasadas (Matriz de servicios, combinaciones aprobadas, cálculo de recargos).
5. `tests/services_concurrency_and_submit.test.tsx`: 9/9 pasadas (Protección contra doble registro y concurrencia).
6. `tests/services_dependent_dropdowns_and_search.test.tsx`: 8/8 pasadas (Comportamiento de dropdowns dependientes).
7. `tests/responsive_viewports.test.tsx`: 33/33 pasadas (Adaptabilidad en 320px, 375px, 412px, 768px, 1024px, 1366px, 1440px y 1920px).
8. `tests/pos.test.tsx`: 18/18 pasadas (Flujo integral de punto de venta).
9. `tests/expenses_cash.test.tsx`: 20/20 pasadas (Aislamiento de módulos no modificados).
10. `tests/offline_resilience.test.tsx`: 15/15 pasadas (Sincronización y resiliencia offline).

---

## K. RESULTADO DEL BUILD

Se ejecutó la compilación de producción con TypeScript y Vite:
- **Comando:** `npm run build` en `frontend/`
- **Resultado:** **Éxito (código de salida 0)**
- **Artefactos generados:**
  - `dist/index.html`: 1.44 kB (gzip: 0.66 kB)
  - `dist/assets/index-DUmf2FXm.css`: 184.09 kB (gzip: 28.45 kB)
  - `dist/assets/index-C1vGoXEB.js`: 841.85 kB (gzip: 189.13 kB)
- **Tiempo de build:** 1.59 segundos.
- **Errores de tipos TypeScript:** 0.

---

## L. CAPTURAS DE ESCRITORIO Y MÓVIL

Se generaron y verificaron 19 capturas fotográficas en alta resolución utilizando Chromium CDP headless en el directorio `evidencias/v4_visual_evolution/`:

1. `01_caja_escritorio_dark.png` (1280x800, Tema Oscuro) — Catálogo y Carrito V4 balanceados.
2. `02_caja_escritorio_light.png` (1280x800, Tema Claro) — Papel Cálido en Caja.
3. `03_caja_fullhd_dark.png` (1920x1080, Tema Oscuro) — Vista en monitor panorámico con ancho máximo controlado.
4. `03b_caja_modal_cobro_dark.png` (1280x800, Tema Oscuro) — Modal de Cobro con desglose y botón verde.
5. `03c_caja_modal_cobro_light.png` (1280x800, Tema Claro) — Modal de Cobro en modo claro.
6. `04_caja_movil_catalogo_dark.png` (390x844, Tema Oscuro) — Catálogo móvil con chips táctiles y botón de ticket flotante.
7. `05_caja_movil_catalogo_light.png` (390x844, Tema Claro) — Catálogo móvil en modo claro.
8. `06_caja_movil_ticket_dark.png` (390x844, Tema Oscuro) — Carrito móvil abierto con 3 filas y botón Cobrar Ticket.
9. `07_caja_movil_ticket_light.png` (390x844, Tema Claro) — Carrito móvil en modo claro.
10. `08_productos_escritorio_dark.png` (1280x800, Tema Oscuro) — Listado administrativo de productos, toolbar y badges.
11. `09_productos_escritorio_light.png` (1280x800, Tema Claro) — Listado administrativo en modo claro.
12. `10_productos_movil_dark.png` (390x844, Tema Oscuro) — Toolbar móvil estilizado con inputs de 48px y botón Nuevo Producto.
13. `11_productos_movil_light.png` (390x844, Tema Claro) — Vista móvil de productos en modo claro.
14. `12_productos_modal_alta_dark.png` (1280x800, Tema Oscuro) — Modal de creación con estimador de margen.
15. `13_productos_modal_alta_light.png` (1280x800, Tema Claro) — Modal de creación en modo claro.
16. `14_servicios_escritorio_dark.png` (1280x800, Tema Oscuro) — Selector dependiente, banner de fórmula y KPIs.
17. `15_servicios_escritorio_light.png` (1280x800, Tema Claro) — Módulo de servicios en modo claro.
18. `16_servicios_movil_dark.png` (390x844, Tema Oscuro) — Vista móvil de servicios con selectores de 48px.
19. `17_servicios_movil_light.png` (390x844, Tema Claro) — Vista móvil de servicios en modo claro.

---

## M. COMPARACIÓN VISUAL ANTES / DESPUÉS

| Módulo / Elemento | Estado Anterior (V3 Baseline) | Estado Evolucionado (V4 Premium) |
|---|---|---|
| **Caja (General)** | Sólida paleta V3 pero con variables de modo claro divergentes de la especificación Papel Cálido oficial. | Tokens unificados en `:root[data-theme="light"]` (`#FAF7F2`, `#FFFDF9`, `#C64F32`). Modales de cobro y pesables con consistencia geométrica exacta. |
| **Productos (Toolbar)** | Input y selectores con aspecto de formulario estándar, botones con radios no unificados y menor jerarquía en móvil. | Barra con superficie `#25282E`, bordes `#3A3E46`, inputs y selects estandarizados a 48px con foco terracota, botón `+ Nuevo Producto` prominente. En móvil colapsa ordenadamente sin estiramiento vertical indebido. |
| **Productos (Listado)** | Tabla con contraste plano y badges de estado estándar sin diferenciación de fondo. | Badges translúcidos modernos (`rgba(57,185,133,0.15)`), columnas de precios en tipografía monoespaciada tabular, y tarjetas móviles con jerarquía visual nítida. |
| **Productos (Modal)** | Modal básico con margen de ganancia poco visible. | Modal con cabecera y pie con fijación ergonómica, preview de margen con indicador porcentual destacado y feedback claro. |
| **Servicios (Formulario)** | Disposición simple con banner de cobro plano y botones de radio estándar sin sensación táctil de botones pill. | Layout modular de dos columnas, banner destacado con fórmula matemática `MONTO + RECARGO = TOTAL`, pills interactivas de medios de pago y botón `Registrar Carga` con prominencia visual. |
| **Servicios (KPIs)** | Tarjetas de arqueo con tipografía genérica y poca jerarquía. | Tarjetas con jerarquía económica clara, montos monoespaciados, etiquetas secundarias descriptivas y segregación nítida por plataforma. |

---

## N. VERIFICACIÓN DE INTEGRIDAD FUNCIONAL Y ECONÓMICA

Se verificó exhaustivamente que:
1. **Reglas de negocio y fórmulas:** No se modificó ninguna fórmula de cálculo (`formPrecio`, `formCosto`, `monto + adicional = total`).
2. **Matriz de Servicios:** La matriz de servicios (`PREPAY`, `CLARO`, `MERCADOPAGO` y sus destinos aprobados) permanece idéntica y opera bajo los mismos contratos de datos.
3. **Unidades de Productos:** Los valores en base de datos (`kg`, `100g`, `unidad`, `hoja`, `paquete`) permanecen intactos. En pantalla, las unidades individuales no muestran `/ unidad` redundante, mientras que pesables y por empaque mantienen su indicación explícita.
4. **Idempotencia y doble clic:** Todos los manejadores (`handleCheckout`, `handleSave`, `handleRegistrarCarga`) conservan sus banderas de bloqueo `isSubmitting` / validación de inputs.
5. **IDs y Test IDs:** Todos los atributos `data-testid` (`admin-search-input`, `admin-category-filter`, `services-platform-select`, etc.) se preservaron al 100%.

---

## O. ARCHIVOS QUE NO FUERON MODIFICADOS

Para garantizar aislamiento total, los siguientes archivos y carpetas permanecen exactamente en su estado original:
- **Backend / Workers:**
  - `api/*`
  - `cloudflare-worker-prototype/*`
  - `cloudflare-worker-prototype/src/*`
  - `schema.sql`, `migrations/*`
- **Módulos Frontend Excluidos:**
  - `frontend/src/components/SalesHistoryAdmin.tsx` (Historial)
  - `frontend/src/components/CashClosingAdmin.tsx` (Gastos y Cierre)
  - `frontend/src/components/PreventistasAdmin.tsx` (Preventistas)
  - `frontend/src/components/FinancialViewerAdmin.tsx` (Visor Financiero)
  - `frontend/src/components/TesoreriaAdmin.tsx` (Tesorería Lab)
  - `frontend/src/components/RadarAdmin.tsx` (RADAR)
  - `frontend/src/components/ConfigAdmin.tsx` (Configuración)
  - `frontend/src/components/AuthModal.tsx` / `LoginModal.tsx` (Autenticación)
- **Configuraciones de Infraestructura:**
  - `wrangler.toml`
  - `frontend/vite.config.ts`
  - `package.json`

---

## P. RIESGOS O PENDIENTES

- **Riesgos Identificados:** Cero. Los cambios están confinados exclusivamente a reglas de estilo CSS con selectores específicos de clase.
- **Pendientes:** Ninguno. Los requerimientos visuales, geométricos, responsive y de pruebas para Caja, Productos y Servicios han sido completados en su totalidad.

---

## Q. CONFIRMACIÓN EXPRESA DE NO MODIFICACIÓN DE PRODUCCIÓN

Se declara de manera fehaciente que:
1. **NO se ejecutó** ningún comando de despliegue (`wrangler pages deploy`, `wrangler deploy`, `npm run deploy`, etc.).
2. **NO se realizaron** modificaciones en el Cloudflare Worker de producción (`https://genaro-pos-live-api.genaro-pos.workers.dev`).
3. **NO se realizaron** migraciones ni alteraciones en las tablas SQLite de Turso en producción.
4. El entorno productivo permanece en su versión previa sin alteración alguna.

---

## R. RECOMENDACIÓN SOBRE LA CONVENIENCIA DE PUBLICAR

**Veredicto:** **APROBADO PARA PUBLICACIÓN (LISTO PARA MERGE Y DEPLOY)**

### Fundamentos:
1. **Calidad Visual:** Se alcanzó un estándar estético consistente Dark Premium Terracota y Papel Cálido en los tres módulos objetivo.
2. **Ergonomía Mobile First:** La interfaz móvil fue probada y comprobada en viewports de 320px a 480px, ofreciendo controles táctiles cómodos de 48px sin desbordamiento.
3. **Confiabilidad Técnica:** 100% de la suite de pruebas unitarias y de integración (383/383 tests en 40 suites) aprobada satisfactoriamente.
4. **Compilación Limpia:** Compilación de producción con TypeScript y Vite sin errores en 1.59s.
5. **Aislamiento Impecable:** Cero efectos colaterales sobre backend, base de datos y módulos excluidos.

Se recomienda al equipo de desarrollo realizar el merge de la rama de trabajo hacia la rama de release y coordinar la publicación productiva programada.
