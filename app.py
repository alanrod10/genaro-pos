import datetime
import html
import math

import pandas as pd
import streamlit as st
from st_keyup import st_keyup
from streamlit_gsheets import GSheetsConnection

# ==========================================
# 0. CONFIGURACIÓN REGIONAL Y CONSTANTES
# ==========================================
ZONA_AR = datetime.timezone(datetime.timedelta(hours=-3))
URL_PLANILLA = "https://docs.google.com/spreadsheets/d/1AEsHRAwONhfcATrG7k0gsVmWB1IGlqoHt89_wcT9Uuo/edit?gid=514091242#gid=514091242"

RECARGO_BASE = 2000
RECARGO_POR_TRAMO = 100

# ==========================================
# 1. CONFIGURACIÓN INICIAL Y ESTILOS UI/UX
# ==========================================
st.set_page_config(
    page_title="Genaro POS",
    page_icon="🛒",
    layout="wide",
)


def aplicar_estilos_profesionales():
    """Estilos globales. No modifica la lógica ni el flujo de uso."""
    st.markdown(
        """
        <style>
        /* ---------- Base ---------- */
        .main .block-container {
            padding-top: 1.35rem;
            padding-bottom: 2rem;
            max-width: 1500px;
        }

        html, body, [class*="css"] {
            font-family: "Montserrat", "Segoe UI", Arial, sans-serif;
        }

        h1, h2, h3 {
            letter-spacing: -0.02em;
        }

        /* ---------- Sidebar ---------- */
        section[data-testid="stSidebar"] {
            border-right: 1px solid rgba(128, 128, 128, 0.20);
        }

        section[data-testid="stSidebar"] .stRadio label {
            font-weight: 600;
        }

        /* ---------- Botones ---------- */
        div.stButton > button {
            border-radius: 10px;
            min-height: 42px;
            font-weight: 700;
            transition: transform 0.10s ease, box-shadow 0.10s ease;
        }

        div.stButton > button:hover {
            transform: translateY(-1px);
            box-shadow: 0 5px 14px rgba(0, 0, 0, 0.10);
        }

        /* ---------- Inputs ---------- */
        div[data-baseweb="input"] > div,
        div[data-baseweb="select"] > div {
            border-radius: 9px;
        }

        /* ---------- Cards nativas ---------- */
        div[data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 12px;
        }

        /* ---------- Métricas ---------- */
        div[data-testid="stMetricValue"] {
            font-weight: 800;
            letter-spacing: -0.03em;
        }

        /* ---------- Data editors ---------- */
        div[data-testid="stDataEditor"] {
            border-radius: 10px;
            overflow: hidden;
        }

        /* ---------- Mensajes ---------- */
        div[data-testid="stAlert"] {
            border-radius: 10px;
        }

        /* ---------- Divisores ---------- */
        hr {
            margin-top: 0.65rem;
            margin-bottom: 0.65rem;
        }

        /* ---------- Footer ---------- */
        footer {
            visibility: hidden;
        }

        /* Evita que el contenido HTML del visor provoque scroll horizontal */
        .visor-grid-wrapper {
            width: 100%;
            overflow-x: auto;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


aplicar_estilos_profesionales()

# ==========================================
# 2. HELPERS GENERALES
# ==========================================
def ahora_ar():
    return datetime.datetime.now(ZONA_AR)


def obtener_conexion():
    return st.connection("gsheets", type=GSheetsConnection)


def fecha_texto():
    return ahora_ar().strftime("%d/%m/%Y %H:%M:%S")


def fecha_act_texto():
    return ahora_ar().strftime("%d/%m/%Y")


def normalizar_fecha_columna(df, columna="FECHA"):
    """
    Genera FECHA_REAL sin tocar FECHA.

    Soporta los tres formatos que pueden aparecer al leer Google Sheets:
    - datetime/Timestamp reales;
    - texto dd/mm/yyyy[ hh:mm:ss];
    - seriales numéricos de Excel/Sheets (ej. 46293 o 46293.5).
    """
    if columna not in df.columns:
        df["FECHA_REAL"] = pd.NaT
        return df

    valores = df[columna]
    numerico = pd.to_numeric(valores, errors="coerce")

    # Rango razonable de fechas serializadas de Excel/Sheets.
    es_serial = numerico.between(20000, 60000)

    fechas = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")

    if es_serial.any():
        fechas.loc[es_serial] = pd.to_datetime(
            numerico.loc[es_serial],
            unit="D",
            origin="1899-12-30",
            errors="coerce",
        )

    if (~es_serial).any():
        fechas.loc[~es_serial] = pd.to_datetime(
            valores.loc[~es_serial],
            dayfirst=True,
            errors="coerce",
        )

    df["FECHA_REAL"] = fechas
    return df


def sumar_numerico(df, columna):
    """Suma segura de una columna; devuelve 0 si no existe o está vacía."""
    if columna not in df.columns:
        return 0.0
    serie = pd.to_numeric(df[columna], errors="coerce").fillna(0)
    return float(serie.sum())


def numero_seguro(valor, default=0.0):
    try:
        if pd.isna(valor):
            return default
        return float(valor)
    except (TypeError, ValueError):
        return default


def entero_seguro(valor, default=0):
    try:
        return int(round(numero_seguro(valor, default)))
    except (TypeError, ValueError):
        return default


def dinero(valor):
    return f"${entero_seguro(valor):,}"


# ==========================================
# 3. GESTIÓN DEL ESTADO (MEMORIA)
# ==========================================
def inicializar_memoria():
    defaults = {
        "carrito": [],
        "input_monto_carga": 0,
        "input_monto_adic": 0,
        "search_key": 0,
        "admin_key": 0,
        "prev_key": 0,
        "cargas_key": 0,
        "hist_key": 0,
    }

    for clave, valor in defaults.items():
        if clave not in st.session_state:
            st.session_state[clave] = valor


inicializar_memoria()

# ==========================================
# 4. CONEXIÓN A BASE DE DATOS Y LÓGICA VENTA
# ==========================================
@st.cache_data(ttl=600)
def cargar_productos():
    try:
        conn = obtener_conexion()
        df = conn.read(
            spreadsheet=URL_PLANILLA,
            worksheet="DB_PRODUCTOS",
        )

        if df.empty:
            return df

        if "NOMBRE" in df.columns:
            df = df.dropna(subset=["NOMBRE"]).copy()
            df["NOMBRE"] = df["NOMBRE"].astype(str)

        return df

    except Exception:
        return pd.DataFrame()


def procesar_venta(metodo_pago, monto_efvo=None, monto_transf=None):
    if not st.session_state.carrito:
        st.warning("⚠️ No hay productos en el carrito.")
        return False

    total_venta = sum(
        numero_seguro(item.get("subtotal", 0))
        for item in st.session_state.carrito
    )

    fecha_actual = ahora_ar()
    ticket_id = "T-" + str(int(fecha_actual.timestamp() * 1000))

    if monto_efvo is None and monto_transf is None:
        pago_efvo = total_venta if metodo_pago == "EFECTIVO" else 0
        pago_transf = total_venta if metodo_pago == "TRANSFERENCIA" else 0
    else:
        pago_efvo = numero_seguro(monto_efvo)
        pago_transf = numero_seguro(monto_transf)

    nueva_venta = pd.DataFrame(
        [
            {
                "TICKET_ID": ticket_id,
                "FECHA": fecha_actual.strftime("%d/%m/%Y %H:%M:%S"),
                "TOTAL_VENTA": entero_seguro(total_venta),
                "MONTO_EFECTIVO": entero_seguro(pago_efvo),
                "MONTO_TRANSF": entero_seguro(pago_transf),
                "ES_NOCTURNO": False,
            }
        ]
    )

    items_vendidos = [
        {
            "TICKET_ID": ticket_id,
            "FECHA": fecha_actual.strftime("%d/%m/%Y %H:%M:%S"),
            "PRODUCTO": item["nombre"],
            "CANTIDAD": item["cantidad"],
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": item["precio"],
            "SUBTOTAL": item["subtotal"],
            "METODO_PAGO": metodo_pago,
        }
        for item in st.session_state.carrito
    ]

    df_items_nuevos = pd.DataFrame(items_vendidos)

    try:
        conn = obtener_conexion()

        with st.spinner("💾 Guardando transacción en la nube..."):
            df_mov = conn.read(
                spreadsheet=URL_PLANILLA,
                worksheet="DB_MOVIMIENTOS_CAJA",
                ttl=0,
            )
            conn.update(
                spreadsheet=URL_PLANILLA,
                worksheet="DB_MOVIMIENTOS_CAJA",
                data=pd.concat(
                    [df_mov, nueva_venta],
                    ignore_index=True,
                ),
            )

            df_historial = conn.read(
                spreadsheet=URL_PLANILLA,
                worksheet="DB_HISTORIAL_ITEMS",
                ttl=0,
            )
            conn.update(
                spreadsheet=URL_PLANILLA,
                worksheet="DB_HISTORIAL_ITEMS",
                data=pd.concat(
                    [df_historial, df_items_nuevos],
                    ignore_index=True,
                ),
            )

        st.session_state.carrito = []
        return True

    except Exception:
        st.error("❌ Falló el guardado. Verifica tu conexión a internet.")
        return False


# ==========================================
# 5. CONTROLADORES
# ==========================================
@st.dialog("Dividir Pago (Mixto)")
def modal_pago_mixto(total_cobrar):
    st.write(f"### Total de la compra: **${total_cobrar:,.0f}**")
    st.write("---")

    monto_transf = st.number_input(
        "📱 Monto ingresado en Transferencia:",
        min_value=0,
        max_value=int(total_cobrar),
        step=100,
    )

    monto_efvo = int(total_cobrar - monto_transf)

    st.info(
        f"💵 Restante a cobrar en Efectivo: **${monto_efvo:,.0f}**"
    )

    st.write("---")

    if st.button(
        "✅ Confirmar Pago Mixto",
        use_container_width=True,
        type="primary",
    ):
        if procesar_venta(
            "MIXTO",
            monto_efvo=monto_efvo,
            monto_transf=monto_transf,
        ):
            st.rerun()


def agregar_al_carrito(nombre, precio):
    precio = entero_seguro(precio)

    for item in st.session_state.carrito:
        if item["nombre"] == nombre:
            item["cantidad"] += 1
            item["subtotal"] = item["cantidad"] * precio
            st.session_state.search_key += 1
            return

    st.session_state.carrito.append(
        {
            "nombre": nombre,
            "precio": precio,
            "cantidad": 1,
            "subtotal": precio,
        }
    )
    st.session_state.search_key += 1


def actualizar_desde_cant(i):
    nueva_cant = max(1, entero_seguro(st.session_state[f"cant_{i}"], 1))
    st.session_state.carrito[i]["cantidad"] = nueva_cant
    st.session_state.carrito[i]["subtotal"] = (
        nueva_cant * st.session_state.carrito[i]["precio"]
    )
    st.session_state[f"monto_{i}"] = st.session_state.carrito[i]["subtotal"]


def actualizar_desde_monto(i):
    nuevo_monto = max(0, entero_seguro(st.session_state[f"monto_{i}"], 0))
    st.session_state.carrito[i]["subtotal"] = nuevo_monto

    precio = entero_seguro(st.session_state.carrito[i]["precio"])
    if precio > 0:
        calc = nuevo_monto / precio
        st.session_state.carrito[i]["cantidad"] = int(calc) if calc >= 1 else 1
        st.session_state[f"cant_{i}"] = st.session_state.carrito[i]["cantidad"]


def calcular_recargo_automatico():
    monto = numero_seguro(st.session_state.input_monto_carga)
    st.session_state.input_monto_adic = (
        int(math.ceil(monto / RECARGO_BASE) * RECARGO_POR_TRAMO)
        if monto > 0
        else 0
    )


# ==========================================
# 6. VISTAS - CAJA
# ==========================================
def mostrar_caja():
    st.title("🛒 Caja Registradora")

    df_productos = cargar_productos()

    col_izq, col_der = st.columns([5, 5])

    with col_izq:
        with st.container(border=True):
            st.subheader("🔍 Buscador de Productos")

            busqueda = st_keyup(
                "Busca por nombre o marca (Ej. Lays, Coca):",
                debounce=300,
                key=f"buscador_{st.session_state.search_key}",
            )

            if busqueda:
                if df_productos.empty or "NOMBRE" not in df_productos.columns:
                    st.warning("No hay productos disponibles en el catálogo.")
                else:
                    nombres = df_productos["NOMBRE"].astype(str)
                    resultados = df_productos[
                        nombres.str.contains(
                            busqueda,
                            case=False,
                            na=False,
                        )
                    ].head(15)

                    if resultados.empty:
                        st.warning("No hay coincidencias en el catálogo.")
                    else:
                        for index, row in resultados.iterrows():
                            c1, c2, c3 = st.columns([5, 2, 3])
                            c1.write(f"**{row['NOMBRE']}**")
                            c2.write(f"${entero_seguro(row.get('PRECIO_DIA', 0))}")

                            if c3.button(
                                "➕ Agregar",
                                key=f"btn_add_{index}",
                                use_container_width=True,
                            ):
                                agregar_al_carrito(
                                    row["NOMBRE"],
                                    row.get("PRECIO_DIA", 0),
                                )
                                st.rerun()

    with col_der:
        with st.container(border=True):
            st.subheader("🛒 Tu Carrito")

            if not st.session_state.carrito:
                st.info(
                    "El carrito está vacío. Agrega productos desde el buscador."
                )
            else:
                total = 0

                h1, h2, h3, h4 = st.columns([4, 3, 3, 1])
                h1.write("**Producto**")
                h2.write("**Cant**")
                h3.write("**Monto $**")

                for i, item in enumerate(st.session_state.carrito):
                    c1, c2, c3, c4 = st.columns([4, 3, 3, 1])
                    c1.write(f"{item['nombre']}")

                    c2.number_input(
                        "Cant",
                        value=int(item["cantidad"]),
                        min_value=1,
                        step=1,
                        key=f"cant_{i}",
                        on_change=actualizar_desde_cant,
                        args=(i,),
                        label_visibility="collapsed",
                    )

                    c3.number_input(
                        "Monto",
                        value=int(item["subtotal"]),
                        min_value=0,
                        step=100,
                        key=f"monto_{i}",
                        on_change=actualizar_desde_monto,
                        args=(i,),
                        label_visibility="collapsed",
                    )

                    if c4.button("❌", key=f"del_{i}"):
                        st.session_state.carrito.pop(i)
                        st.rerun()

                    total += item["subtotal"]

                st.divider()
                st.metric("TOTAL A COBRAR", f"${total:,.0f}")

                col_efvo, col_transf, col_mixto = st.columns(3)

                if col_efvo.button(
                    "💵 Efectivo",
                    use_container_width=True,
                    type="primary",
                ):
                    if procesar_venta("EFECTIVO"):
                        st.toast(
                            "✅ Venta en Efectivo registrada.",
                            icon="✅",
                        )
                        st.rerun()

                if col_transf.button(
                    "📱 Transf.",
                    use_container_width=True,
                ):
                    if procesar_venta("TRANSFERENCIA"):
                        st.toast(
                            "✅ Venta por Transferencia registrada.",
                            icon="✅",
                        )
                        st.rerun()

                if col_mixto.button(
                    "💳 Mixto",
                    use_container_width=True,
                ):
                    modal_pago_mixto(total)


# ==========================================
# 7. VISTAS - SERVICIOS
# ==========================================
def mostrar_servicios():
    st.title("📱 Cargas y Servicios")

    with st.container(border=True):
        st.write(
            "Registra recargas virtuales o pagos de servicios de forma ágil."
        )
        st.write("---")

        col1, col2 = st.columns(2)

        with col1:
            servicio = st.selectbox(
                "Empresa / Servicio",
                [
                    "Claro",
                    "Personal",
                    "Movistar",
                    "Tuenti",
                    "DIRECTV",
                    "SUBE",
                    "Otro",
                ],
            )

            monto_carga = st.number_input(
                "Monto a Cargar ($)",
                min_value=0,
                step=500,
                key="input_monto_carga",
                on_change=calcular_recargo_automatico,
            )

        with col2:
            monto_adic = st.number_input(
                "Recargo / Adicional ($)",
                min_value=0,
                step=50,
                key="input_monto_adic",
            )

            metodo_pago = st.radio(
                "Método de Pago",
                ["EFECTIVO", "TRANSFERENCIA", "MIXTO"],
                horizontal=True,
            )

        total_cobrar = int(monto_carga + monto_adic)

        st.info(
            f"### **💰 Total a cobrar al cliente: ${total_cobrar:,.0f}**"
        )

        monto_transf = 0
        monto_efvo = 0

        if metodo_pago == "MIXTO":
            monto_transf = st.number_input(
                "Monto pagado en Transferencia:",
                min_value=0,
                max_value=int(total_cobrar),
                step=100,
            )
            monto_efvo = total_cobrar - monto_transf
            st.write(
                f"💵 Restante en Efectivo: **${monto_efvo:,.0f}**"
            )

        elif metodo_pago == "EFECTIVO":
            monto_efvo = total_cobrar

        elif metodo_pago == "TRANSFERENCIA":
            monto_transf = total_cobrar

        st.divider()

        if st.button(
            "🚀 Registrar Carga",
            type="primary",
            use_container_width=True,
        ):
            if monto_carga <= 0:
                st.error("⚠️ El monto de la carga debe ser mayor a cero.")
            else:
                nueva_carga = pd.DataFrame(
                    [
                        {
                            "FECHA": fecha_texto(),
                            "SERVICIO": servicio,
                            "MONTO_CARGA": monto_carga,
                            "MONTO_ADICIONAL": monto_adic,
                            "TOTAL_COBRADO": total_cobrar,
                            "PAGO_EFVO": monto_efvo,
                            "PAGO_TRANSF": monto_transf,
                        }
                    ]
                )

                try:
                    conn = obtener_conexion()

                    with st.spinner("Guardando en el sistema..."):
                        df_cargas = conn.read(
                            spreadsheet=URL_PLANILLA,
                            worksheet="DB_CARGAS",
                            ttl=0,
                        )

                        conn.update(
                            spreadsheet=URL_PLANILLA,
                            worksheet="DB_CARGAS",
                            data=pd.concat(
                                [df_cargas, nueva_carga],
                                ignore_index=True,
                            ),
                        )

                    st.toast("✅ Carga guardada.", icon="📲")

                    for key in ("input_monto_carga", "input_monto_adic"):
                        st.session_state.pop(key, None)

                    st.rerun()

                except Exception:
                    st.error("❌ Error al guardar. Intente nuevamente.")


# ==========================================
# 8. VISTAS - HISTORIAL DE CARGAS
# ==========================================
def mostrar_historial_cargas():
    st.title("📋 Historial de Cargas")

    if "cargas_msg" in st.session_state:
        st.success(st.session_state.cargas_msg)
        del st.session_state.cargas_msg

    try:
        conn = obtener_conexion()
        df_full = conn.read(
            spreadsheet=URL_PLANILLA,
            worksheet="DB_CARGAS",
            ttl=0,
        )

        if df_full.empty:
            st.info("No hay cargas registradas en la base de datos.")
            return

        df_full = normalizar_fecha_columna(df_full)

        with st.container(border=True):
            fecha_elegida = st.date_input(
                "🗓️ Filtrar por Día:",
                ahora_ar().date(),
            )

            df_filtrado = df_full[
                df_full["FECHA_REAL"].dt.date == fecha_elegida
            ].copy()

            st.info(
                "💡 **Auditoría:** Modifica los valores si cargaste algo mal, "
                "o selecciona la fila y presiona **Suprimir (Del)** para "
                "borrarla por completo. Luego presiona Guardar."
            )

            columnas_editor = [
                "FECHA",
                "SERVICIO",
                "MONTO_CARGA",
                "MONTO_ADICIONAL",
                "TOTAL_COBRADO",
                "PAGO_EFVO",
                "PAGO_TRANSF",
            ]

            edit_source = df_filtrado[columnas_editor].copy()

            edited_cargas = st.data_editor(
                edit_source,
                use_container_width=True,
                num_rows="dynamic",
                key=f"ed_cargas_{st.session_state.cargas_key}",
            )

            if st.button(
                "💾 Guardar Cambios en Cargas",
                type="primary",
                use_container_width=True,
            ):
                with st.spinner("Sincronizando correcciones..."):
                    indices_originales = df_filtrado.index.tolist()
                    indices_editados = edited_cargas.index.tolist()
                    df_final = df_full.copy()

                    indices_eliminados = [
                        idx
                        for idx in indices_originales
                        if idx not in indices_editados
                    ]
                    df_final = df_final.drop(indices_eliminados)

                    for idx, row in edited_cargas.iterrows():
                        if idx in df_final.index:
                            df_final.loc[idx, columnas_editor] = row.values
                        else:
                            df_final = pd.concat(
                                [
                                    df_final,
                                    pd.DataFrame([row]),
                                ],
                                ignore_index=True,
                            )

                    if "FECHA_REAL" in df_final.columns:
                        df_final = df_final.drop(columns=["FECHA_REAL"])

                    conn.update(
                        spreadsheet=URL_PLANILLA,
                        worksheet="DB_CARGAS",
                        data=df_final,
                    )

                    st.cache_data.clear()
                    st.session_state.cargas_msg = (
                        "✅ ¡El historial de cargas fue corregido y "
                        "actualizado exitosamente!"
                    )
                    st.session_state.cargas_key += 1
                    st.rerun()

    except Exception as e:
        st.error(f"Error al cargar el historial de cargas. {e}")


# ==========================================
# 9. VISTAS - ADMINISTRACIÓN DE PRODUCTOS
# ==========================================
def siguiente_id_producto(df):
    if df.empty or "ID_PRODUCTO" not in df.columns:
        return 1

    serie = pd.to_numeric(df["ID_PRODUCTO"], errors="coerce").dropna()
    return int(serie.max()) + 1 if not serie.empty else 1


def mostrar_admin_productos():
    st.title("⚙️ Gestión de Catálogo")

    if "admin_msg" in st.session_state:
        st.success(st.session_state.admin_msg)
        del st.session_state.admin_msg

    try:
        conn = obtener_conexion()
        df_actual = conn.read(
            spreadsheet=URL_PLANILLA,
            worksheet="DB_PRODUCTOS",
            ttl=0,
        )

        if "NOMBRE" in df_actual.columns:
            df_actual = df_actual.dropna(subset=["NOMBRE"]).copy()

        categorias_unicas = sorted(
            df_actual["CATEGORIA"].dropna().astype(str).unique().tolist()
        ) if "CATEGORIA" in df_actual.columns else []

        proveedores_unicos = sorted(
            df_actual["PROVEEDOR"].dropna().astype(str).unique().tolist()
        ) if "PROVEEDOR" in df_actual.columns else []

        col_izq, col_espacio, col_der = st.columns([10, 1, 6])

        with col_izq:
            with st.container(border=True):
                st.markdown("### 🔄 ACTUALIZADOR RÁPIDO")

                lista_productos = sorted(
                    df_actual["NOMBRE"].astype(str).tolist()
                ) if "NOMBRE" in df_actual.columns else []

                producto_seleccionado = st.selectbox(
                    "BUSCAR PRODUCTO A MODIFICAR:",
                    [""] + lista_productos,
                    key=f"mod_sel_{st.session_state.admin_key}",
                )

                if producto_seleccionado:
                    coincidencias = df_actual[
                        df_actual["NOMBRE"].astype(str)
                        == producto_seleccionado
                    ]

                    if coincidencias.empty:
                        st.warning("No se encontró el producto seleccionado.")
                    else:
                        datos_prod = coincidencias.iloc[0]
                        idx_prod = coincidencias.index.tolist()[0]

                        st.write("---")
                        c_actual, c_nuevo = st.columns(2)

                        with c_actual:
                            st.write("**📝 Datos Actuales:**")
                            st.write(
                                f"**Proveedor:** {datos_prod.get('PROVEEDOR', '-')}"
                            )
                            st.write(
                                f"**Costo:** ${entero_seguro(datos_prod.get('COSTO', 0))}"
                            )
                            st.write(
                                f"**Precio:** ${entero_seguro(datos_prod.get('PRECIO_DIA', 0))}"
                            )
                            st.write(
                                f"**Margen:** {numero_seguro(datos_prod.get('MARGEN_%', 0)) * 100:.2f}%"
                            )

                        with c_nuevo:
                            st.write("**✏️ Completar solo si cambia:**")

                            nuevo_prov = st.text_input(
                                "Nuevo Proveedor:",
                                value=str(datos_prod.get("PROVEEDOR", "")),
                                key=f"m_prov_{st.session_state.admin_key}",
                            )

                            nuevo_costo = st.number_input(
                                "Nuevo Costo ($):",
                                value=entero_seguro(datos_prod.get("COSTO", 0)),
                                min_value=0,
                                step=100,
                                key=f"m_cost_{st.session_state.admin_key}",
                            )

                            nuevo_precio = st.number_input(
                                "Nuevo Precio ($):",
                                value=entero_seguro(datos_prod.get("PRECIO_DIA", 0)),
                                min_value=0,
                                step=100,
                                key=f"m_prec_{st.session_state.admin_key}",
                            )

                            nuevo_margen_calc = (
                                (nuevo_precio - nuevo_costo) / nuevo_costo
                                if nuevo_costo > 0
                                else 0
                            )

                            st.info(
                                f"**Margen Proyectado: "
                                f"{nuevo_margen_calc * 100:.2f}%**"
                            )

                        st.write("---")
                        col_btn1, col_btn2 = st.columns(2)

                        with col_btn1:
                            if st.button(
                                "🔄 ACTUALIZAR PRECIOS",
                                type="primary",
                                use_container_width=True,
                                key=f"m_btn_{st.session_state.admin_key}",
                            ):
                                df_actual.at[idx_prod, "PROVEEDOR"] = nuevo_prov
                                df_actual.at[idx_prod, "COSTO"] = nuevo_costo
                                df_actual.at[idx_prod, "PRECIO_DIA"] = nuevo_precio
                                df_actual.at[idx_prod, "PRECIO_NOCHE"] = nuevo_precio
                                df_actual.at[idx_prod, "MARGEN_%"] = nuevo_margen_calc
                                df_actual.at[idx_prod, "FECHA_ACT"] = fecha_act_texto()

                                with st.spinner("Guardando en la nube..."):
                                    conn.update(
                                        spreadsheet=URL_PLANILLA,
                                        worksheet="DB_PRODUCTOS",
                                        data=df_actual,
                                    )
                                    st.cache_data.clear()

                                st.session_state.admin_msg = "✅ ¡Actualizado exitosamente!"
                                st.session_state.admin_key += 1
                                st.rerun()

                        with col_btn2:
                            confirmar = st.checkbox(
                                "⚠️ Confirmar borrado",
                                key=f"m_chk_{st.session_state.admin_key}",
                            )

                            if st.button(
                                "🗑️ ELIMINAR",
                                use_container_width=True,
                                key=f"m_del_{st.session_state.admin_key}",
                            ):
                                if confirmar:
                                    df_actual = df_actual.drop(idx_prod)

                                    with st.spinner("Eliminando..."):
                                        conn.update(
                                            spreadsheet=URL_PLANILLA,
                                            worksheet="DB_PRODUCTOS",
                                            data=df_actual,
                                        )
                                        st.cache_data.clear()

                                    st.session_state.admin_msg = "🗑️ Producto eliminado."
                                    st.session_state.admin_key += 1
                                    st.rerun()
                                else:
                                    st.warning("Debes marcar la casilla.")

        with col_der:
            with st.container(border=True):
                st.markdown("### ➕ ALTA DE PRODUCTO")

                n_nombre = st.text_input(
                    "NOMBRE:",
                    key=f"n_nom_{st.session_state.admin_key}",
                )

                n_cat = st.selectbox(
                    "CATEGORÍA:",
                    categorias_unicas + ["OTRO..."],
                    key=f"n_cat_{st.session_state.admin_key}",
                )

                n_prov = st.selectbox(
                    "PROVEEDOR:",
                    proveedores_unicos + ["OTRO..."],
                    key=f"n_prov_{st.session_state.admin_key}",
                )

                n_unidad = st.selectbox(
                    "UNIDAD:",
                    ["Unidad", "Kg", "Litro"],
                    key=f"n_uni_{st.session_state.admin_key}",
                )

                n_costo = st.number_input(
                    "COSTO ($):",
                    min_value=0,
                    step=100,
                    key=f"n_cost_{st.session_state.admin_key}",
                )

                n_precio = st.number_input(
                    "PRECIO VENTA ($):",
                    min_value=0,
                    step=100,
                    key=f"n_prec_{st.session_state.admin_key}",
                )

                n_margen = (
                    (n_precio - n_costo) / n_costo
                    if n_costo > 0
                    else 0
                )

                st.info(
                    f"**Margen Estimado: {n_margen * 100:.2f}%**"
                )

                if st.button(
                    "➕ CREAR PRODUCTO",
                    type="primary",
                    use_container_width=True,
                    key=f"n_btn_{st.session_state.admin_key}",
                ):
                    if not n_nombre.strip():
                        st.error("⚠️ El nombre es obligatorio.")
                    elif n_precio <= 0:
                        st.error("⚠️ El precio debe ser mayor a 0.")
                    else:
                        nuevo_id = siguiente_id_producto(df_actual)

                        nuevo_registro = pd.DataFrame(
                            [
                                {
                                    "ID_PRODUCTO": nuevo_id,
                                    "NOMBRE": n_nombre,
                                    "CATEGORIA": n_cat,
                                    "PROVEEDOR": n_prov,
                                    "UNIDAD": n_unidad,
                                    "COSTO": n_costo,
                                    "MARGEN_%": n_margen,
                                    "PRECIO_DIA": n_precio,
                                    "PRECIO_NOCHE": n_precio,
                                    "FECHA_ACT": fecha_act_texto(),
                                }
                            ]
                        )

                        with st.spinner("Creando producto..."):
                            conn.update(
                                spreadsheet=URL_PLANILLA,
                                worksheet="DB_PRODUCTOS",
                                data=pd.concat(
                                    [df_actual, nuevo_registro],
                                    ignore_index=True,
                                ),
                            )
                            st.cache_data.clear()

                        st.session_state.admin_msg = (
                            f"✅ ¡{n_nombre} añadido al catálogo!"
                        )
                        st.session_state.admin_key += 1
                        st.rerun()

    except Exception:
        st.error("Error al cargar el panel de administración.")


# ==========================================
# 10. VISTAS - HISTORIAL DE ÍTEMS
# ==========================================
def mostrar_historial():
    st.title("📜 Historial de Ítems")

    if "hist_msg" in st.session_state:
        st.success(st.session_state.hist_msg)
        del st.session_state.hist_msg

    try:
        conn = obtener_conexion()

        df_historial = conn.read(
            spreadsheet=URL_PLANILLA,
            worksheet="DB_HISTORIAL_ITEMS",
            ttl=0,
        )

        df_caja = conn.read(
            spreadsheet=URL_PLANILLA,
            worksheet="DB_MOVIMIENTOS_CAJA",
            ttl=0,
        )

        df_historial = normalizar_fecha_columna(df_historial)

        with st.container(border=True):
            col1, col2 = st.columns([3, 7])

            with col1:
                fecha_elegida = st.date_input(
                    "🗓️ Filtrar por Día:",
                    ahora_ar().date(),
                )
                palabra_clave = st.text_input(
                    "🔍 Buscar producto específico:"
                )

            mask_fecha = (
                df_historial["FECHA_REAL"].dt.date == fecha_elegida
            )

            df_filtrado = df_historial[mask_fecha].copy()

            if palabra_clave:
                df_filtrado = df_filtrado[
                    df_filtrado["PRODUCTO"].astype(str).str.contains(
                        palabra_clave,
                        case=False,
                        na=False,
                    )
                ]

            with col2:
                st.info(
                    "💡 **Auditoría de Ventas:** Si te equivocaste al cobrar, "
                    "selecciona la fila aquí y bórrala (Suprimir) o modifícale "
                    "el Subtotal. **El sistema descontará automáticamente el "
                    "dinero de la Caja.**"
                )

                columnas_mostrar = [
                    "FECHA",
                    "TICKET_ID",
                    "PRODUCTO",
                    "CANTIDAD",
                    "SUBTOTAL",
                    "METODO_PAGO",
                ]

                edited_df = st.data_editor(
                    df_filtrado[columnas_mostrar],
                    use_container_width=True,
                    num_rows="dynamic",
                    key=f"ed_hist_{st.session_state.hist_key}",
                )

                total_items = sumar_numerico(edited_df, "SUBTOTAL")

                metodo = edited_df["METODO_PAGO"].astype(str).str.upper()

                total_efvo = sumar_numerico(
                    edited_df.loc[metodo == "EFECTIVO"],
                    "SUBTOTAL",
                )
                total_transf = sumar_numerico(
                    edited_df.loc[metodo == "TRANSFERENCIA"],
                    "SUBTOTAL",
                )
                total_mixto = sumar_numerico(
                    edited_df.loc[metodo == "MIXTO"],
                    "SUBTOTAL",
                )

                st.write("---")
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("💰 TOTAL FILTRADO", dinero(total_items))
                m2.metric("💵 En Efectivo", dinero(total_efvo))
                m3.metric("📱 En Transf.", dinero(total_transf))
                m4.metric("💳 Pago Mixto", dinero(total_mixto))

            if st.button(
                "💾 Guardar Correcciones y Recalcular Caja",
                type="primary",
                use_container_width=True,
            ):
                with st.spinner(
                    "Sincronizando ítems y recalculando cierres de caja..."
                ):
                    indices_originales = df_filtrado.index.tolist()
                    indices_editados = edited_df.index.tolist()
                    df_final_items = df_historial.copy()

                    indices_eliminados = [
                        idx
                        for idx in indices_originales
                        if idx not in indices_editados
                    ]
                    df_final_items = df_final_items.drop(indices_eliminados)

                    for idx, row in edited_df.iterrows():
                        if idx in df_final_items.index:
                            df_final_items.loc[idx, columnas_mostrar] = row.values
                        else:
                            df_final_items = pd.concat(
                                [
                                    df_final_items,
                                    pd.DataFrame([row]),
                                ],
                                ignore_index=True,
                            )

                    if "FECHA_REAL" in df_final_items.columns:
                        df_final_items = df_final_items.drop(
                            columns=["FECHA_REAL"]
                        )

                    # 3. RECÁLCULO AUTOMÁTICO EN LA CAJA ORIGINAL
                    tickets_involucrados = (
                        df_filtrado["TICKET_ID"].dropna().unique().tolist()
                    )

                    for tid in tickets_involucrados:
                        items_del_ticket = df_final_items[
                            df_final_items["TICKET_ID"] == tid
                        ]

                        new_total = sumar_numerico(
                            items_del_ticket,
                            "SUBTOTAL",
                        )

                        idx_caja_list = df_caja[
                            df_caja["TICKET_ID"] == tid
                        ].index.tolist()

                        if idx_caja_list:
                            idx_caja = idx_caja_list[0]

                            if new_total <= 0:
                                df_caja = df_caja.drop(idx_caja)
                            else:
                                old_total = numero_seguro(
                                    df_caja.at[idx_caja, "TOTAL_VENTA"]
                                )
                                diff = old_total - new_total

                                if diff != 0:
                                    df_caja.at[
                                        idx_caja,
                                        "TOTAL_VENTA",
                                    ] = new_total

                                    efvo = numero_seguro(
                                        df_caja.at[
                                            idx_caja,
                                            "MONTO_EFECTIVO",
                                        ]
                                    )

                                    transf = numero_seguro(
                                        df_caja.at[
                                            idx_caja,
                                            "MONTO_TRANSF",
                                        ]
                                    )

                                    if diff > 0:
                                        if efvo >= diff:
                                            df_caja.at[
                                                idx_caja,
                                                "MONTO_EFECTIVO",
                                            ] = efvo - diff
                                        else:
                                            df_caja.at[
                                                idx_caja,
                                                "MONTO_EFECTIVO",
                                            ] = 0
                                            df_caja.at[
                                                idx_caja,
                                                "MONTO_TRANSF",
                                            ] = max(
                                                0,
                                                transf - (diff - efvo),
                                            )

                                    else:
                                        if transf > 0 and efvo == 0:
                                            df_caja.at[
                                                idx_caja,
                                                "MONTO_TRANSF",
                                            ] = transf - diff
                                        else:
                                            df_caja.at[
                                                idx_caja,
                                                "MONTO_EFECTIVO",
                                            ] = efvo - diff

                    conn.update(
                        spreadsheet=URL_PLANILLA,
                        worksheet="DB_HISTORIAL_ITEMS",
                        data=df_final_items,
                    )

                    conn.update(
                        spreadsheet=URL_PLANILLA,
                        worksheet="DB_MOVIMIENTOS_CAJA",
                        data=df_caja,
                    )

                    st.cache_data.clear()
                    st.session_state.hist_msg = (
                        "✅ ¡Los ítems fueron corregidos y la Caja fue "
                        "recalculada perfectamente!"
                    )
                    st.session_state.hist_key += 1
                    st.rerun()

    except Exception as e:
        st.error(f"No se pudo cargar el historial. Detalle: {e}")


# ==========================================
# 11. VISTAS - VISOR / DASHBOARD
# ==========================================
def mostrar_visor():
    # CSS propio del visor: mantiene la distribución de la planilla
    # pero con mejor adaptación a pantallas y tipografía.
    st.markdown(
        """
        <style>
        .visor-title {
            font-size: 32px;
            font-weight: 800;
            margin: 0 0 18px 0;
            color: #111111;
        }

        .visor-grid {
            display: grid;
            grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
            column-gap: 13%;
            row-gap: 30px;
            width: 100%;
            min-width: 900px;
            margin-top: 10px;
        }

        .visor-card {
            border: 4px solid #b7b7b7;
            background: #ffffff;
            overflow: hidden;
            box-sizing: border-box;
            width: 100%;
            border-radius: 2px;
        }

        .visor-header {
            min-height: 46px;
            display: flex;
            align-items: center;
            padding: 5px 10px;
            box-sizing: border-box;
            font-size: clamp(20px, 1.55vw, 29px);
            font-weight: 800;
            line-height: 1.05;
        }

        .visor-body {
            padding: 7px 10px 0 10px;
            box-sizing: border-box;
        }

        .visor-line {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            align-items: center;
            min-height: 39px;
            font-size: clamp(18px, 1.35vw, 26px);
            line-height: 1.05;
            color: #111111;
            column-gap: 12px;
        }

        .visor-label {
            white-space: nowrap;
        }

        .visor-value {
            text-align: right;
            white-space: nowrap;
            font-variant-numeric: tabular-nums;
        }

        .visor-separator {
            height: 5px;
            background: #b7b7b7;
            margin-top: 4px;
        }

        .visor-total {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            align-items: center;
            min-height: 67px;
            font-size: clamp(19px, 1.40vw, 27px);
            line-height: 1.05;
            color: #111111;
            column-gap: 12px;
        }

        .visor-total-label {
            font-weight: 400;
        }

        .visor-total-value {
            font-size: clamp(29px, 2.1vw, 40px);
            font-weight: 900;
            text-align: right;
            white-space: nowrap;
            font-variant-numeric: tabular-nums;
        }

        .visor-profit {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            align-items: center;
            min-height: 45px;
            border-top: 5px solid #b7b7b7;
            font-size: clamp(18px, 1.18vw, 23px);
            font-weight: 800;
            line-height: 1.05;
            color: #5f666d;
        }

        .visor-profit-value {
            align-self: stretch;
            display: flex;
            align-items: center;
            justify-content: flex-end;
            padding: 0 10px;
            min-width: 215px;
            box-sizing: border-box;
            color: #ffffff;
            font-size: clamp(24px, 1.65vw, 29px);
            font-weight: 900;
            white-space: nowrap;
            font-variant-numeric: tabular-nums;
        }

        .visor-secondary {
            display: flex;
            justify-content: flex-end;
            align-items: center;
            min-height: 39px;
            font-size: clamp(18px, 1.35vw, 27px);
            color: #111111;
            white-space: nowrap;
            font-variant-numeric: tabular-nums;
            margin-top: -4px;
        }

        @media (max-width: 1100px) {
            .visor-grid {
                min-width: 0;
                grid-template-columns: 1fr;
                column-gap: 0;
                row-gap: 22px;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    def visor_header(titulo, fondo, texto="#FFFFFF"):
        return (
            f'<div class="visor-header" '
            f'style="background:{fondo};color:{texto};">'
            f'{html.escape(titulo)}'
            f'</div>'
        )

    def visor_line(label, valor):
        return (
            '<div class="visor-line">'
            f'<div class="visor-label">{html.escape(label)}</div>'
            f'<div class="visor-value">{dinero(valor)}</div>'
            '</div>'
        )

    try:
        conn = obtener_conexion()

        df_caja = conn.read(
            spreadsheet=URL_PLANILLA,
            worksheet="DB_MOVIMIENTOS_CAJA",
            ttl=0,
        )
        df_cargas = conn.read(
            spreadsheet=URL_PLANILLA,
            worksheet="DB_CARGAS",
            ttl=0,
        )
        df_gastos = conn.read(
            spreadsheet=URL_PLANILLA,
            worksheet="GASTOS_RETIROS",
            ttl=0,
        )

        normalizar_fecha_columna(df_caja)
        normalizar_fecha_columna(df_cargas)
        normalizar_fecha_columna(df_gastos)

        st.markdown(
            '<div class="visor-title">📊 Dashboard Ejecutivo</div>',
            unsafe_allow_html=True,
        )

        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 4, 3])
            fecha_elegida = c2.date_input(
                "📅 Seleccionar fecha a consultar:",
                ahora_ar().date(),
            )

        df_hoy_caja = df_caja[
            df_caja["FECHA_REAL"].dt.date == fecha_elegida
        ].copy()

        df_hoy_cargas = df_cargas[
            df_cargas["FECHA_REAL"].dt.date == fecha_elegida
        ].copy()

        df_hoy_gastos = df_gastos[
            df_gastos["FECHA_REAL"].dt.date == fecha_elegida
        ].copy()

        # --------------------------
        # CAJA A - DRUGSTORE
        # --------------------------
        a_efvo = sumar_numerico(df_hoy_caja, "MONTO_EFECTIVO")
        a_transf = sumar_numerico(df_hoy_caja, "MONTO_TRANSF")
        a_total = sumar_numerico(df_hoy_caja, "TOTAL_VENTA")
        a_ganancia = a_total * 0.10

        # --------------------------
        # CAJAS B / C / E
        # --------------------------
        b_efvo = b_transf = b_total = 0.0
        c_efvo = c_transf = c_total = 0.0
        e_efvo = e_transf = e_total = 0.0

        recargas_transferencias_total = 0.0
        sube_mp_capital = 0.0

        for _, row in df_hoy_cargas.iterrows():
            servicio = str(row.get("SERVICIO", "")).strip().upper()
            monto_carga = numero_seguro(row.get("MONTO_CARGA", 0))
            monto_adic = numero_seguro(row.get("MONTO_ADICIONAL", 0))
            pago_transf = numero_seguro(row.get("PAGO_TRANSF", 0))

            # Todas las transferencias de cargas impactan Caja D.
            recargas_transferencias_total += pago_transf

            # Se conserva la misma convención contable de la planilla:
            # primero se considera capital hasta MONTO_CARGA y el excedente
            # de la transferencia pertenece al adicional.
            capital_transferido = min(
                pago_transf,
                monto_carga,
            )

            adicional_transferido = max(
                pago_transf - monto_carga,
                0,
            )

            # CAJA C - ADICIONALES
            c_total += monto_adic
            c_transf += adicional_transferido
            c_efvo += max(
                monto_adic - adicional_transferido,
                0,
            )

            if servicio == "CLARO":
                # CAJA E - CLARO
                e_total += monto_carga
                e_transf += capital_transferido
                e_efvo += max(
                    monto_carga - capital_transferido,
                    0,
                )

            elif servicio != "SUBE (MP)":
                # CAJA B - SUBE / otros servicios de capital
                b_total += monto_carga
                b_transf += capital_transferido
                b_efvo += max(
                    monto_carga - capital_transferido,
                    0,
                )

            else:
                # Capital especial SUBE (MP): no se muestra en B,
                # pero se descuenta del total disponible en banco.
                sube_mp_capital += monto_carga

        # --------------------------
        # GASTOS / RETIROS DEL DÍA
        # --------------------------
        if not df_hoy_gastos.empty and "METODO_PAGO" in df_hoy_gastos.columns:
            metodo_gastos = (
                df_hoy_gastos["METODO_PAGO"]
                .astype(str)
                .str.strip()
                .str.upper()
            )

            gastos_efvo = sumar_numerico(
                df_hoy_gastos.loc[metodo_gastos == "EFECTIVO"],
                "MONTO_SALIDA",
            )

            gastos_transf = sumar_numerico(
                df_hoy_gastos.loc[metodo_gastos == "TRANSFERENCIA"],
                "MONTO_SALIDA",
            )
        else:
            gastos_efvo = 0.0
            gastos_transf = 0.0

        gastos_total = gastos_efvo + gastos_transf

        # --------------------------
        # CAJA D - TRANSFERENCIAS
        # --------------------------
        d_ventas_drugstore = a_transf
        d_recargas = recargas_transferencias_total
        d_total_banco = (
            d_ventas_drugstore
            + d_recargas
            - sube_mp_capital
            - gastos_transf
        )

        # La segunda cifra visible a la derecha del efectivo de Caja A
        # conserva exactamente el criterio utilizado en el visor actual.
        efectivo_disponible = a_efvo - d_recargas

        # --------------------------
        # TARJETAS DEL VISOR
        # --------------------------
        caja_a = (
            '<div class="visor-card">'
            + visor_header("CAJA A - DRUGSTORE", "#0000FF")
            + '<div class="visor-body">'
            + visor_line("(+) EFECTIVO:", a_efvo)
            + (
                '<div class="visor-secondary">'
                f'{dinero(efectivo_disponible)}'
                '</div>'
            )
            + visor_line("(+) TRANSFERENCIAS:", a_transf)
            + '<div class="visor-separator"></div>'
            + (
                '<div class="visor-total">'
                '<div class="visor-total-label">TOTAL VENTAS:</div>'
                f'<div class="visor-total-value">{dinero(a_total)}</div>'
                '</div>'
            )
            + (
                '<div class="visor-profit">'
                '<div>GANANCIA ESTIMADA (10%):</div>'
                '<div class="visor-profit-value" '
                'style="background:#0000FF;">'
                f'{dinero(a_ganancia)}'
                '</div>'
                '</div>'
            )
            + '</div>'
            + '</div>'
        )

        caja_b = (
            '<div class="visor-card">'
            + visor_header(
                "CAJA B - SUBE (Solo Capital)",
                "#FF9900",
                "#111111",
            )
            + '<div class="visor-body">'
            + visor_line("(+) INGRESOS EFECTIVO:", b_efvo)
            + visor_line("(+) INGRESOS TRANSF:", b_transf)
            + '<div class="visor-separator"></div>'
            + (
                '<div class="visor-total">'
                '<div class="visor-total-label">TOTAL (Sin Adic):</div>'
                f'<div class="visor-total-value">{dinero(b_total)}</div>'
                '</div>'
            )
            + '</div>'
            + '</div>'
        )

        caja_c = (
            '<div class="visor-card">'
            + visor_header(
                "CAJA C - ADICIONALES (Ganancia)",
                "#38761D",
            )
            + '<div class="visor-body">'
            + visor_line("(+) EFECTIVO:", c_efvo)
            + visor_line("(+) TRANSFERENCIA:", c_transf)
            + '<div class="visor-separator"></div>'
            + (
                '<div class="visor-total">'
                '<div class="visor-total-label">TOTAL GANANCIA:</div>'
                f'<div class="visor-total-value">{dinero(c_total)}</div>'
                '</div>'
            )
            + '</div>'
            + '</div>'
        )

        caja_d = (
            '<div class="visor-card">'
            + visor_header(
                "CAJA D - TRANSFERENCIAS (Total)",
                "#9900FF",
            )
            + '<div class="visor-body">'
            + visor_line(
                "DE VENTAS DRUGSTORE:",
                d_ventas_drugstore,
            )
            + visor_line(
                "DE RECARGAS (Todas):",
                d_recargas,
            )
            + '<div class="visor-separator"></div>'
            + (
                '<div class="visor-total">'
                '<div class="visor-total-label">TOTAL EN BANCO:</div>'
                '<div class="visor-total-value" '
                'style="background:#DDDDDD;padding:7px 10px;">'
                f'{dinero(d_total_banco)}'
                '</div>'
                '</div>'
            )
            + '</div>'
            + '</div>'
        )

        caja_e = (
            '<div class="visor-card">'
            + visor_header(
                "CAJA E - CLARO (Solo Capital)",
                "#FF0000",
            )
            + '<div class="visor-body">'
            + visor_line("(+) INGRESOS EFECTIVO:", e_efvo)
            + visor_line("(+) INGRESOS TRANSF:", e_transf)
            + '<div class="visor-separator"></div>'
            + (
                '<div class="visor-total">'
                '<div class="visor-total-label">TOTAL (Sin Adic):</div>'
                f'<div class="visor-total-value">{dinero(e_total)}</div>'
                '</div>'
            )
            + '</div>'
            + '</div>'
        )

        caja_gastos = (
            '<div class="visor-card">'
            + visor_header(
                "GASTOS / RETIROS DEL DÍA",
                "#000000",
            )
            + '<div class="visor-body">'
            + visor_line(
                "(-) SALIDAS EFECTIVO:",
                gastos_efvo,
            )
            + visor_line(
                "(-) SALIDAS TRANSF:",
                gastos_transf,
            )
            + '<div class="visor-separator"></div>'
            + (
                '<div class="visor-total">'
                '<div class="visor-total-label"></div>'
                f'<div class="visor-total-value">{dinero(gastos_total)}</div>'
                '</div>'
            )
            + '</div>'
            + '</div>'
        )

        st.markdown(
            '<div class="visor-grid-wrapper">'
            '<div class="visor-grid">'
            + caja_a
            + caja_b
            + caja_c
            + caja_d
            + caja_e
            + caja_gastos
            + '</div>'
            '</div>',
            unsafe_allow_html=True,
        )

    except Exception as e:
        st.error(f"Error cargando el dashboard: {e}")


# ==========================================
# 12. VISTAS - PREVENTISTAS
# ==========================================
def mostrar_preventistas():
    st.title("🚚 Catálogo por Preventista")

    st.write(
        "Selecciona un proveedor, edita los precios directamente en la tabla "
        "o da de alta un producto nuevo."
    )

    if "prev_msg" in st.session_state:
        st.success(st.session_state.prev_msg)
        del st.session_state.prev_msg

    try:
        conn = obtener_conexion()
        df_productos = conn.read(
            spreadsheet=URL_PLANILLA,
            worksheet="DB_PRODUCTOS",
            ttl=0,
        )

        if "NOMBRE" in df_productos.columns:
            df_productos = df_productos.dropna(subset=["NOMBRE"]).copy()

        if not df_productos.empty:
            proveedores_unicos = sorted(
                df_productos["PROVEEDOR"]
                .dropna()
                .astype(str)
                .unique()
                .tolist()
            )

            categorias_unicas = sorted(
                df_productos["CATEGORIA"]
                .dropna()
                .astype(str)
                .unique()
                .tolist()
            )

            with st.container(border=True):
                proveedor_elegido = st.selectbox(
                    "👤 Seleccionar Preventista / Proveedor:",
                    [""] + proveedores_unicos,
                )

                if proveedor_elegido:
                    df_filtrado = df_productos[
                        df_productos["PROVEEDOR"].astype(str)
                        == proveedor_elegido
                    ]

                    st.write(
                        f"### Productos de: **{proveedor_elegido}** "
                        f"({len(df_filtrado)} ítems)"
                    )

                    st.info(
                        "💡 **Tip:** Edita el Costo o el Precio y presiona Enter "
                        "(o toca afuera de la celda). Verás cómo el porcentaje "
                        "de Ganancia se recalcula **en vivo** en la tabla."
                    )

                    columnas_mostrar = [
                        "NOMBRE",
                        "COSTO",
                        "PRECIO_DIA",
                        "MARGEN_%",
                    ]

                    df_edicion = df_filtrado[columnas_mostrar].copy()

                    df_edicion["MARGEN_%"] = (
                        pd.to_numeric(
                            df_edicion["MARGEN_%"],
                            errors="coerce",
                        )
                        .fillna(0)
                        * 100
                    ).round(1)

                    editor_key = (
                        f"ed_prev_{st.session_state.prev_key}_"
                        f"{proveedor_elegido}"
                    )

                    if editor_key in st.session_state:
                        cambios_en_vivo = st.session_state[editor_key].get(
                            "edited_rows",
                            {},
                        )

                        for row_pos_str, mods in cambios_en_vivo.items():
                            row_pos = int(row_pos_str)

                            if row_pos < len(df_edicion):
                                real_idx = df_edicion.index[row_pos]

                                c_val = numero_seguro(
                                    mods.get(
                                        "COSTO",
                                        df_edicion.at[real_idx, "COSTO"],
                                    )
                                )
                                p_val = numero_seguro(
                                    mods.get(
                                        "PRECIO_DIA",
                                        df_edicion.at[real_idx, "PRECIO_DIA"],
                                    )
                                )

                                calc_margen = (
                                    ((p_val - c_val) / c_val) * 100
                                    if c_val > 0
                                    else 0.0
                                )

                                df_edicion.at[
                                    real_idx,
                                    "MARGEN_%",
                                ] = round(calc_margen, 1)

                    edited_df = st.data_editor(
                        df_edicion,
                        key=editor_key,
                        use_container_width=True,
                        hide_index=True,
                        disabled=["NOMBRE", "MARGEN_%"],
                        column_config={
                            "NOMBRE": st.column_config.TextColumn(
                                "PRODUCTO"
                            ),
                            "COSTO": st.column_config.NumberColumn(
                                "COSTO ($)",
                                min_value=0,
                                step=100,
                            ),
                            "PRECIO_DIA": st.column_config.NumberColumn(
                                "PRECIO VENTA ($)",
                                min_value=0,
                                step=100,
                            ),
                            "MARGEN_%": st.column_config.NumberColumn(
                                "GANANCIA (%)",
                                format="%.1f %%",
                            ),
                        },
                    )

                    if st.button(
                        "💾 Guardar Nuevos Precios",
                        type="primary",
                        use_container_width=True,
                    ):
                        with st.spinner(
                            "Actualizando catálogo en la nube..."
                        ):
                            cambios_realizados = False

                            for idx, row in edited_df.iterrows():
                                n_costo = numero_seguro(row["COSTO"])
                                n_precio = numero_seguro(row["PRECIO_DIA"])
                                c_viejo = numero_seguro(
                                    df_filtrado.loc[idx, "COSTO"]
                                )
                                p_viejo = numero_seguro(
                                    df_filtrado.loc[idx, "PRECIO_DIA"]
                                )

                                if n_costo != c_viejo or n_precio != p_viejo:
                                    df_productos.at[idx, "COSTO"] = n_costo
                                    df_productos.at[idx, "PRECIO_DIA"] = n_precio
                                    df_productos.at[idx, "PRECIO_NOCHE"] = n_precio
                                    n_margen = (
                                        (n_precio - n_costo) / n_costo
                                        if n_costo > 0
                                        else 0
                                    )
                                    df_productos.at[idx, "MARGEN_%"] = n_margen
                                    df_productos.at[idx, "FECHA_ACT"] = fecha_act_texto()
                                    cambios_realizados = True

                            if cambios_realizados:
                                conn.update(
                                    spreadsheet=URL_PLANILLA,
                                    worksheet="DB_PRODUCTOS",
                                    data=df_productos,
                                )
                                st.cache_data.clear()
                                st.session_state.prev_msg = (
                                    "✅ ¡Los precios de este proveedor "
                                    "fueron actualizados!"
                                )
                                st.session_state.prev_key += 1
                                st.rerun()
                            else:
                                st.warning(
                                    "No detecté ninguna modificación "
                                    "en los números."
                                )

                    st.write("---")

                    with st.expander(
                        f"➕ Alta rápida de producto para {proveedor_elegido}"
                    ):
                        c1, c2 = st.columns(2)

                        with c1:
                            p_nombre = st.text_input(
                                "NOMBRE DEL PRODUCTO:",
                                key=f"p_nom_{st.session_state.prev_key}",
                            )
                            p_cat = st.selectbox(
                                "CATEGORÍA:",
                                categorias_unicas + ["OTRO..."],
                                key=f"p_cat_{st.session_state.prev_key}",
                            )
                            p_unidad = st.selectbox(
                                "UNIDAD:",
                                ["Unidad", "Kg", "Litro"],
                                key=f"p_uni_{st.session_state.prev_key}",
                            )

                        with c2:
                            p_costo = st.number_input(
                                "COSTO ($):",
                                min_value=0,
                                step=100,
                                key=f"p_cost_{st.session_state.prev_key}",
                            )
                            p_precio = st.number_input(
                                "PRECIO VENTA ($):",
                                min_value=0,
                                step=100,
                                key=f"p_prec_{st.session_state.prev_key}",
                            )

                            p_margen = (
                                (p_precio - p_costo) / p_costo
                                if p_costo > 0
                                else 0
                            )

                            st.info(
                                f"**Margen Estimado: "
                                f"{p_margen * 100:.2f}%**"
                            )

                        if st.button(
                            "➕ GUARDAR NUEVO PRODUCTO",
                            type="primary",
                            use_container_width=True,
                            key=f"btn_p_add_{st.session_state.prev_key}",
                        ):
                            if not p_nombre.strip():
                                st.error("⚠️ El nombre es obligatorio.")
                            elif p_precio <= 0:
                                st.error("⚠️ El precio debe ser mayor a 0.")
                            else:
                                nuevo_id = siguiente_id_producto(df_productos)

                                nuevo_registro = pd.DataFrame(
                                    [
                                        {
                                            "ID_PRODUCTO": nuevo_id,
                                            "NOMBRE": p_nombre,
                                            "CATEGORIA": p_cat,
                                            "PROVEEDOR": proveedor_elegido,
                                            "UNIDAD": p_unidad,
                                            "COSTO": p_costo,
                                            "MARGEN_%": p_margen,
                                            "PRECIO_DIA": p_precio,
                                            "PRECIO_NOCHE": p_precio,
                                            "FECHA_ACT": fecha_act_texto(),
                                        }
                                    ]
                                )

                                with st.spinner("Creando producto..."):
                                    conn.update(
                                        spreadsheet=URL_PLANILLA,
                                        worksheet="DB_PRODUCTOS",
                                        data=pd.concat(
                                            [
                                                df_productos,
                                                nuevo_registro,
                                            ],
                                            ignore_index=True,
                                        ),
                                    )
                                    st.cache_data.clear()

                                st.session_state.prev_msg = (
                                    f"✅ ¡{p_nombre} añadido al catálogo "
                                    f"de {proveedor_elegido}!"
                                )
                                st.session_state.prev_key += 1
                                st.rerun()

        else:
            st.warning("No hay productos cargados en la base de datos.")

    except Exception as e:
        st.error(
            f"Error al cargar el módulo de preventistas. {e}"
        )


# ==========================================
# 13. ENRUTADOR PRINCIPAL (MENÚ LATERAL)
# ==========================================
st.sidebar.image(
    "https://cdn-icons-png.flaticon.com/512/3514/3514491.png",
    width=120,
)

st.sidebar.title("Sistema Genaro")

menu = st.sidebar.radio(
    "Navegación",
    [
        "🛒 Caja",
        "📱 Servicios",
        "📋 Historial de Cargas",
        "⚙️ Admin Productos",
        "📜 Historial de Ítems",
        "📊 Visor (Dashboard)",
        "🚚 Preventistas",
    ],
)

if menu == "🛒 Caja":
    mostrar_caja()

elif menu == "📱 Servicios":
    mostrar_servicios()

elif menu == "📋 Historial de Cargas":
    mostrar_historial_cargas()

elif menu == "⚙️ Admin Productos":
    mostrar_admin_productos()

elif menu == "📜 Historial de Ítems":
    mostrar_historial()

elif menu == "📊 Visor (Dashboard)":
    mostrar_visor()

elif menu == "🚚 Preventistas":
    mostrar_preventistas()
