import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from st_keyup import st_keyup
import datetime 
import math 

# ==========================================
# 1. CONFIGURACIÓN INICIAL Y ESTILOS UI/UX
# ==========================================
st.set_page_config(page_title="Genaro POS", page_icon="🛒", layout="wide")

URL_PLANILLA = "https://docs.google.com/spreadsheets/d/1AEsHRAwONhfcATrG7k0gsVmWB1IGlqoHt89_wcT9Uuo/edit?gid=514091242#gid=514091242"

def aplicar_estilos_profesionales():
    """Inyecta CSS avanzado para una UI moderna, adaptativa al Modo Oscuro/Claro."""
    st.markdown("""
        <style>
            /* 1. Ocultar footer pero mantener el menú de configuraciones (Theme/Cache) */
            footer {visibility: hidden;}
            
            .block-container {
                padding-top: 2rem !important;
                padding-bottom: 2rem !important;
                max-width: 98% !important;
            }
            
            /* 2. Tipografía general */
            p, label, span, .stMarkdown {
                font-size: 1.1rem !important;
                font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            }
            
            /* 3. Títulos imponentes */
            h1 { font-size: 2.8rem !important; font-weight: 800 !important; padding-bottom: 0.5rem; }
            h2 { font-size: 2.2rem !important; font-weight: 700 !important; }
            h3 { font-size: 1.6rem !important; font-weight: 600 !important; }
            
            /* 4. Botones: Diseño táctil (Touch-friendly) */
            .stButton > button {
                min-height: 3.5rem;
                border-radius: 12px !important;
                font-size: 1.15rem !important;
                font-weight: 700 !important;
                letter-spacing: 0.5px;
                transition: all 0.2s ease-in-out;
                border: none !important;
            }
            .stButton > button:hover {
                transform: translateY(-3px);
                box-shadow: 0 6px 15px rgba(0,0,0,0.15);
                filter: brightness(1.05);
            }
            
            /* 5. Inputs (Cajas de texto y números) */
            input[type="text"], input[type="number"] {
                font-size: 1.25rem !important;
                padding: 0.7rem !important;
                border-radius: 8px !important;
                font-weight: 500 !important;
            }
            
            /* 6. Métricas (TOTAL A COBRAR) Gigantes - Usando variable de color dinámica */
            div[data-testid="stMetricValue"] {
                font-size: 3.5rem !important;
                font-weight: 900 !important;
                color: #27AE60 !important; 
                line-height: 1.2;
            }
            div[data-testid="stMetricLabel"] {
                font-size: 1.2rem !important;
                font-weight: 800 !important;
                text-transform: uppercase;
                letter-spacing: 1.5px;
                color: var(--text-color) !important;
                opacity: 0.7;
            }
            
            /* 7. Alertas con bordes suaves */
            .stAlert {
                border-radius: 12px !important;
                font-size: 1.1rem !important;
                font-weight: 500 !important;
            }
        </style>
    """, unsafe_allow_html=True)

aplicar_estilos_profesionales()

# ==========================================
# 2. GESTIÓN DEL ESTADO (MEMORIA)
# ==========================================
def inicializar_memoria():
    if 'carrito' not in st.session_state:
        st.session_state.carrito = []
    if 'input_monto_carga' not in st.session_state:
        st.session_state.input_monto_carga = 0.0
    if 'input_monto_adic' not in st.session_state:
        st.session_state.input_monto_adic = 0.0

inicializar_memoria()

# ==========================================
# 3. CONEXIÓN A BASE DE DATOS (ETL)
# ==========================================
@st.cache_data(ttl=600)
def cargar_productos():
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        df = conn.read(spreadsheet=URL_PLANILLA, worksheet="DB_PRODUCTOS")
        return df.dropna(subset=['NOMBRE'])
    except Exception as e:
        st.error(f"⚠️ Error al conectar con Google Sheets (Catálogo). Revisa tu conexión. Detalle: {e}")
        return pd.DataFrame() 

def procesar_venta(metodo_pago, monto_efvo=None, monto_transf=None):
    total_venta = sum(item['subtotal'] for item in st.session_state.carrito)
    fecha_actual = datetime.datetime.now()
    ticket_id = "T-" + str(int(fecha_actual.timestamp() * 1000))
    
    if monto_efvo is None and monto_transf is None:
        pago_efvo = total_venta if metodo_pago == "EFECTIVO" else 0
        pago_transf = total_venta if metodo_pago == "TRANSFERENCIA" else 0
    else:
        pago_efvo = monto_efvo
        pago_transf = monto_transf
    
    nueva_venta = pd.DataFrame([{
        "TICKET_ID": ticket_id,
        "FECHA": fecha_actual.strftime("%d/%m/%Y %H:%M:%S"),
        "TOTAL_VENTA": total_venta,
        "MONTO_EFECTIVO": pago_efvo,
        "MONTO_TRANSF": pago_transf,
        "ES_NOCTURNO": False
    }])
    
    items_vendidos = [{
        "TICKET_ID": ticket_id,
        "FECHA": fecha_actual.strftime("%d/%m/%Y %H:%M:%S"),
        "PRODUCTO": item['nombre'],
        "CANTIDAD": item['cantidad'],
        "UNIDAD": "Unidad", 
        "PRECIO_UNIT": item['precio'],
        "SUBTOTAL": item['subtotal'],
        "METODO_PAGO": metodo_pago
    } for item in st.session_state.carrito]
    df_items_nuevos = pd.DataFrame(items_vendidos)
    
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        with st.spinner("💾 Procesando transacción en la nube..."):
            df_mov = conn.read(spreadsheet=URL_PLANILLA, worksheet="DB_MOVIMIENTOS_CAJA", ttl=0)
            conn.update(spreadsheet=URL_PLANILLA, worksheet="DB_MOVIMIENTOS_CAJA", data=pd.concat([df_mov, nueva_venta], ignore_index=True))
            
            df_historial = conn.read(spreadsheet=URL_PLANILLA, worksheet="DB_HISTORIAL_ITEMS", ttl=0)
            conn.update(spreadsheet=URL_PLANILLA, worksheet="DB_HISTORIAL_ITEMS", data=pd.concat([df_historial, df_items_nuevos], ignore_index=True))
        
        st.session_state.carrito = [] 
        
    except Exception as e:
        st.error(f"❌ Falló el guardado. El cliente no fue cobrado en el sistema. Error: {e}")

# ==========================================
# 4. LÓGICA DE NEGOCIO (Controladores)
# ==========================================
@st.dialog("Dividir Pago (Mixto)")
def modal_pago_mixto(total_cobrar):
    st.write(f"### El total a cobrar es **${total_cobrar:,.0f}**")
    monto_transf = st.number_input("Monto ingresado en Transferencia:", min_value=0.0, max_value=float(total_cobrar), step=100.0)
    monto_efvo = total_cobrar - monto_transf
    st.info(f"💵 Restante a cobrar en Efectivo: **${monto_efvo:,.0f}**")
    
    if st.button("✅ Confirmar Pago Mixto", use_container_width=True, type="primary"):
        procesar_venta("MIXTO", monto_efvo=monto_efvo, monto_transf=monto_transf)
        st.rerun()

def agregar_al_carrito(nombre, precio):
    for item in st.session_state.carrito:
        if item['nombre'] == nombre:
            item['cantidad'] += 1.0
            item['subtotal'] = item['cantidad'] * item['precio']
            return
    st.session_state.carrito.append({'nombre': nombre, 'precio': float(precio), 'cantidad': 1.0, 'subtotal': float(precio)})

def actualizar_desde_cant(i):
    nueva_cant = st.session_state[f"cant_{i}"]
    st.session_state.carrito[i]['cantidad'] = nueva_cant
    st.session_state.carrito[i]['subtotal'] = nueva_cant * st.session_state.carrito[i]['precio']
    st.session_state[f"monto_{i}"] = st.session_state.carrito[i]['subtotal']

def actualizar_desde_monto(i):
    nuevo_monto = st.session_state[f"monto_{i}"]
    st.session_state.carrito[i]['subtotal'] = nuevo_monto
    precio = st.session_state.carrito[i]['precio']
    if precio > 0:
        st.session_state.carrito[i]['cantidad'] = nuevo_monto / precio
        st.session_state[f"cant_{i}"] = st.session_state.carrito[i]['cantidad']

def calcular_recargo_automatico():
    monto = st.session_state.input_monto_carga
    st.session_state.input_monto_adic = float(math.ceil(monto / 2000.0) * 100) if monto > 0 else 0.0

# ==========================================
# 5. VISTAS Y MÓDULOS (Frontend)
# ==========================================
df_productos = cargar_productos() 

def mostrar_caja():
    st.title("🛒 Caja - Lo de Genaro")
    col_izq, col_der = st.columns([5, 5])
    
    with col_izq:
        st.subheader("🔍 Buscador de Productos")
        busqueda = st_keyup("Busca por nombre o marca:", placeholder="Ej. coc, mignon, lays...", debounce=300)
        
        if busqueda:
            resultados = df_productos[df_productos['NOMBRE'].str.contains(busqueda, case=False, na=False)].head(15)
            if resultados.empty:
                st.warning("No hay coincidencias.")
            else:
                for index, row in resultados.iterrows():
                    c1, c2, c3 = st.columns([6, 2, 3])
                    c1.write(f"**{row['NOMBRE']}**")
                    c2.write(f"${row['PRECIO_DIA']}")
                    if c3.button("➕ Agregar", key=f"btn_add_{index}"):
                        agregar_al_carrito(row['NOMBRE'], row['PRECIO_DIA'])
                        st.rerun()
                        
    with col_der:
        st.subheader("🛒 Tu Carrito")
        if not st.session_state.carrito:
            st.info("El carrito está vacío. Busca un producto a la izquierda para comenzar.")
        else:
            total = 0
            h1, h2, h3, h4 = st.columns([4, 2, 3, 1])
            h1.write("**Producto**")
            h2.write("**Cant**")
            h3.write("**Monto $**")
            
            for i, item in enumerate(st.session_state.carrito):
                c1, c2, c3, c4 = st.columns([4, 2, 3, 1])
                c1.write(f"{item['nombre']}")
                c2.number_input("Cant", value=float(item['cantidad']), min_value=0.01, step=1.0, 
                                key=f"cant_{i}", on_change=actualizar_desde_cant, args=(i,), label_visibility="collapsed")
                c3.number_input("Monto", value=float(item['subtotal']), min_value=0.0, step=100.0, 
                                key=f"monto_{i}", on_change=actualizar_desde_monto, args=(i,), label_visibility="collapsed")
                if c4.button("❌", key=f"del_{i}"):
                    st.session_state.carrito.pop(i)
                    st.rerun()
                total += item['subtotal']
                
            st.divider()
            st.metric(label="TOTAL A COBRAR", value=f"${total:,.0f}")
            
            col_efvo, col_transf, col_mixto = st.columns(3)
            if col_efvo.button("💵 Efectivo", use_container_width=True, type="primary"):
                procesar_venta("EFECTIVO")
                st.toast("✅ Venta en Efectivo registrada con éxito.", icon="✅")
                st.rerun()
            if col_transf.button("📱 Transf.", use_container_width=True, type="primary"):
                procesar_venta("TRANSFERENCIA")
                st.toast("✅ Venta por Transferencia registrada con éxito.", icon="✅")
                st.rerun()
            if col_mixto.button("💳 Mixto", use_container_width=True):
                modal_pago_mixto(total)

def mostrar_servicios():
    st.title("📱 Cargas y Servicios")
    st.write("Registra recargas virtuales o pagos de servicios de forma rápida.")
    
    with st.container(border=True):
        col1, col2 = st.columns(2)
        with col1:
            servicio = st.selectbox("Empresa / Servicio", ["Claro", "Personal", "Movistar", "Tuenti", "DIRECTV", "SUBE", "Otro"])
            monto_carga = st.number_input("Monto a Cargar ($)", min_value=0.0, step=500.0, 
                                          key="input_monto_carga", on_change=calcular_recargo_automatico)
        with col2:
            monto_adic = st.number_input("Recargo / Adicional ($)", min_value=0.0, step=50.0, key="input_monto_adic")
            metodo_pago = st.radio("Método de Pago", ["EFECTIVO", "TRANSFERENCIA", "MIXTO"], horizontal=True)
            
        total_cobrar = monto_carga + monto_adic
        st.info(f"### **💰 Total a cobrar al cliente: ${total_cobrar:,.0f}**")
        
        monto_transf = 0.0
        monto_efvo = 0.0
        if metodo_pago == "MIXTO":
            monto_transf = st.number_input("Monto pagado en Transferencia:", min_value=0.0, max_value=float(total_cobrar), step=100.0)
            monto_efvo = total_cobrar - monto_transf
            st.write(f"💵 Restante en Efectivo: **${monto_efvo:,.0f}**")
        elif metodo_pago == "EFECTIVO":
            monto_efvo = total_cobrar
        elif metodo_pago == "TRANSFERENCIA":
            monto_transf = total_cobrar
            
        st.divider()
        
        if st.button("🚀 Registrar Carga", type="primary", use_container_width=True):
            if monto_carga <= 0:
                st.error("⚠️ El monto de la carga debe ser mayor a cero para registrarla.")
            else:
                fecha = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                nueva_carga = pd.DataFrame([{
                    "FECHA": fecha, "SERVICIO": servicio, "MONTO_CARGA": monto_carga,
                    "MONTO_ADICIONAL": monto_adic, "TOTAL_COBRADO": total_cobrar,
                    "PAGO_EFVO": monto_efvo, "PAGO_TRANSF": monto_transf
                }])
                try:
                    conn = st.connection("gsheets", type=GSheetsConnection)
                    with st.spinner("Enviando a la nube..."):
                        df_cargas = conn.read(spreadsheet=URL_PLANILLA, worksheet="DB_CARGAS", ttl=0)
                        conn.update(spreadsheet=URL_PLANILLA, worksheet="DB_CARGAS", data=pd.concat([df_cargas, nueva_carga], ignore_index=True))
                    
                    st.toast(f"✅ Carga de {servicio} guardada exitosamente.", icon="📲")
                    del st.session_state["input_monto_carga"]
                    del st.session_state["input_monto_adic"]
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Error al guardar la carga. Intente nuevamente. {e}")

def mostrar_admin_productos():
    st.title("⚙️ Gestión de Catálogo")
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        df_actual = conn.read(spreadsheet=URL_PLANILLA, worksheet="DB_PRODUCTOS", ttl=0).dropna(subset=['NOMBRE'])
        
        categorias_unicas = sorted(df_actual['CATEGORIA'].dropna().unique().tolist())
        proveedores_unicos = sorted(df_actual['PROVEEDOR'].dropna().unique().tolist())
        
        col_izq, col_espacio, col_der = st.columns([10, 1, 6])
        
        with col_izq:
            with st.container(border=True):
                st.markdown("### 🔄 ACTUALIZADOR RÁPIDO")
                lista_productos = sorted(df_actual['NOMBRE'].tolist())
                producto_seleccionado = st.selectbox("BUSCAR PRODUCTO A MODIFICAR:", [""] + lista_productos)
                
                if producto_seleccionado:
                    datos_prod = df_actual[df_actual['NOMBRE'] == producto_seleccionado].iloc[0]
                    idx_prod = df_actual.index[df_actual['NOMBRE'] == producto_seleccionado].tolist()[0]
                    
                    st.write("---")
                    c_actual, c_nuevo = st.columns(2)
                    with c_actual:
                        st.write("**📝 Datos Actuales:**")
                        st.write(f"**Proveedor:** {datos_prod.get('PROVEEDOR', '-')}")
                        st.write(f"**Costo:** ${float(datos_prod.get('COSTO', 0)):.2f}")
                        st.write(f"**Precio:** ${float(datos_prod.get('PRECIO_DIA', 0)):.2f}")
                        st.write(f"**Margen:** {float(datos_prod.get('MARGEN_%', 0)) * 100:.2f}%")
                    with c_nuevo:
                        st.write("**✏️ Completar solo si cambia:**")
                        nuevo_prov = st.text_input("Nuevo Proveedor:", value=datos_prod.get('PROVEEDOR', ''))
                        nuevo_costo = st.number_input("Nuevo Costo ($):", value=float(datos_prod.get('COSTO', 0)), min_value=0.0)
                        nuevo_precio = st.number_input("Nuevo Precio ($):", value=float(datos_prod.get('PRECIO_DIA', 0)), min_value=0.0)
                        nuevo_margen_calc = (nuevo_precio - nuevo_costo) / nuevo_costo if nuevo_costo > 0 else 0
                        st.info(f"**Margen Proyectado: {nuevo_margen_calc * 100:.2f}%**")
                    
                    st.write("---")
                    col_btn1, col_btn2 = st.columns(2)
                    with col_btn1:
                        if st.button("🔄 ACTUALIZAR PRECIOS", type="primary", use_container_width=True):
                            df_actual.at[idx_prod, 'PROVEEDOR'] = nuevo_prov
                            df_actual.at[idx_prod, 'COSTO'] = nuevo_costo
                            df_actual.at[idx_prod, 'PRECIO_DIA'] = nuevo_precio
                            df_actual.at[idx_prod, 'PRECIO_NOCHE'] = nuevo_precio 
                            df_actual.at[idx_prod, 'MARGEN_%'] = nuevo_margen_calc
                            df_actual.at[idx_prod, 'FECHA_ACT'] = datetime.datetime.now().strftime("%d/%m/%Y")
                            
                            with st.spinner("Guardando en la nube..."):
                                conn.update(spreadsheet=URL_PLANILLA, worksheet="DB_PRODUCTOS", data=df_actual)
                                st.cache_data.clear() 
                            st.success("¡Producto actualizado exitosamente!")
                            st.rerun()
                    with col_btn2:
                        confirmar = st.checkbox("⚠️ Confirmar borrado")
                        if st.button("🗑️ ELIMINAR", use_container_width=True):
                            if confirmar:
                                df_actual = df_actual.drop(idx_prod)
                                with st.spinner("Eliminando..."):
                                    conn.update(spreadsheet=URL_PLANILLA, worksheet="DB_PRODUCTOS", data=df_actual)
                                    st.cache_data.clear()
                                st.error("Producto eliminado definitivamente.")
                                st.rerun()
                            else:
                                st.warning("Debes marcar la casilla de confirmación para eliminar.")
                                
        with col_der:
            with st.container(border=True):
                st.markdown("### ➕ ALTA DE PRODUCTO")
                n_nombre = st.text_input("NOMBRE:")
                n_cat = st.selectbox("CATEGORÍA:", categorias_unicas + ["OTRO..."])
                n_prov = st.selectbox("PROVEEDOR:", proveedores_unicos + ["OTRO..."])
                n_unidad = st.selectbox("UNIDAD:", ["Unidad", "Kg", "Litro"])
                n_costo = st.number_input("COSTO ($):", min_value=0.0, key="new_cost")
                n_precio = st.number_input("PRECIO VENTA ($):", min_value=0.0, key="new_price")
                
                n_margen = (n_precio - n_costo) / n_costo if n_costo > 0 else 0
                st.info(f"**Margen Estimado: {n_margen * 100:.2f}%**")
                
                if st.button("➕ CREAR PRODUCTO", type="primary", use_container_width=True):
                    if not n_nombre.strip():
                        st.error("⚠️ El nombre es obligatorio.")
                    elif n_precio <= 0:
                        st.error("⚠️ El precio debe ser mayor a 0.")
                    else:
                        nuevo_id = df_actual['ID_PRODUCTO'].max() + 1 if not df_actual.empty else 1
                        nuevo_registro = pd.DataFrame([{
                            "ID_PRODUCTO": nuevo_id, "NOMBRE": n_nombre, "CATEGORIA": n_cat, "PROVEEDOR": n_prov,
                            "UNIDAD": n_unidad, "COSTO": n_costo, "MARGEN_%": n_margen, 
                            "PRECIO_DIA": n_precio, "PRECIO_NOCHE": n_precio, 
                            "FECHA_ACT": datetime.datetime.now().strftime("%d/%m/%Y")
                        }])
                        with st.spinner("Creando producto..."):
                            conn.update(spreadsheet=URL_PLANILLA, worksheet="DB_PRODUCTOS", data=pd.concat([df_actual, nuevo_registro], ignore_index=True))
                            st.cache_data.clear()
                        st.success(f"¡{n_nombre} añadido al catálogo!")
                        st.rerun()
    except Exception as e:
        st.error(f"Error al cargar el panel de administración: {e}")

def mostrar_historial():
    st.title("📜 Historial de Ítems Vendidos")
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        df_historial = conn.read(spreadsheet=URL_PLANILLA, worksheet="DB_HISTORIAL_ITEMS", ttl=0)
        df_historial['FECHA_REAL'] = pd.to_datetime(df_historial['FECHA'], dayfirst=True, errors='coerce')
        
        col1, col2 = st.columns([3, 7])
        with col1:
            fecha_elegida = st.date_input("🗓️ Filtrar por Día:", datetime.date.today())
            palabra_clave = st.text_input("🔍 Buscar producto específico (opcional):")
        
        mask_fecha = df_historial['FECHA_REAL'].dt.date == fecha_elegida
        df_filtrado = df_historial[mask_fecha]
        
        if palabra_clave:
            df_filtrado = df_filtrado[df_filtrado['PRODUCTO'].str.contains(palabra_clave, case=False, na=False)]
        
        with col2:
            columnas_mostrar = ['FECHA', 'TICKET_ID', 'PRODUCTO', 'CANTIDAD', 'SUBTOTAL', 'METODO_PAGO']
            st.dataframe(df_filtrado[columnas_mostrar], use_container_width=True, hide_index=True)
            total_items = df_filtrado['SUBTOTAL'].sum()
            st.metric(label=f"Total Filtrado ({fecha_elegida.strftime('%d/%m/%Y')})", value=f"${total_items:,.0f}")
            
    except Exception as e:
        st.error(f"No se pudo cargar el historial. Revisa tu conexión: {e}")

def mostrar_visor():
    st.title("📊 Visor de Caja (Resumen Ejecutivo)")
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        df_caja = conn.read(spreadsheet=URL_PLANILLA, worksheet="DB_MOVIMIENTOS_CAJA", ttl=0)
        df_cargas = conn.read(spreadsheet=URL_PLANILLA, worksheet="DB_CARGAS", ttl=0)
        
        df_caja['FECHA_REAL'] = pd.to_datetime(df_caja['FECHA'], dayfirst=True, errors='coerce')
        df_cargas['FECHA_REAL'] = pd.to_datetime(df_cargas['FECHA'], dayfirst=True, errors='coerce')
        
        c1, c2, c3 = st.columns([3, 4, 3])
        fecha_elegida = c2.date_input("📅 Seleccionar fecha de Caja:", datetime.date.today())
        
        df_hoy_caja = df_caja[df_caja['FECHA_REAL'].dt.date == fecha_elegida]
        df_hoy_cargas = df_cargas[df_cargas['FECHA_REAL'].dt.date == fecha_elegida]
        
        a_efvo = df_hoy_caja['MONTO_EFECTIVO'].sum()
        a_transf = df_hoy_caja['MONTO_TRANSF'].sum()
        a_total = df_hoy_caja['TOTAL_VENTA'].sum()
        a_ganancia = a_total * 0.10
        
        b_efvo = b_transf = b_total = 0.0
        c_efvo = c_transf = c_total = 0.0
        e_efvo = e_transf = e_total = 0.0
        
        for _, row in df_hoy_cargas.iterrows():
            total_cobrado = float(row.get('TOTAL_COBRADO', 0))
            monto_carga = float(row.get('MONTO_CARGA', 0))
            monto_adic = float(row.get('MONTO_ADICIONAL', 0))
            servicio = str(row.get('SERVICIO', '')).strip().upper()
            
            ratio_efvo = float(row.get('PAGO_EFVO', 0)) / total_cobrado if total_cobrado > 0 else 0
            ratio_transf = float(row.get('PAGO_TRANSF', 0)) / total_cobrado if total_cobrado > 0 else 0
            
            c_efvo += monto_adic * ratio_efvo
            c_transf += monto_adic * ratio_transf
            c_total += monto_adic
            
            if servicio == "CLARO":
                e_efvo += monto_carga * ratio_efvo
                e_transf += monto_carga * ratio_transf
                e_total += monto_carga
            else:
                b_efvo += monto_carga * ratio_efvo
                b_transf += monto_carga * ratio_transf
                b_total += monto_carga

        st.write("---")
        
        col_izq, col_espacio, col_der = st.columns([10, 1, 10])
        
        with col_izq:
            st.markdown(f"""
            <div style="border: 2px solid #3b1be3; border-radius: 10px; margin-bottom: 25px; font-family: sans-serif; background-color: var(--secondary-background-color); box-shadow: 0 4px 8px rgba(0,0,0,0.2); overflow: hidden;">
                <div style="background-color: #3b1be3; color: white; padding: 12px 20px; font-weight: bold; font-size: 1.2em; letter-spacing: 0.5px;">CAJA A - DRUGSTORE</div>
                <div style="padding: 20px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 10px; color: var(--text-color); font-size: 1.1em;"><span>(+) EFECTIVO:</span><span>${a_efvo:,.0f}</span></div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 15px; color: var(--text-color); font-size: 1.1em;"><span>(+) TRANSFERENCIAS:</span><span>${a_transf:,.0f}</span></div>
                    <div style="border-bottom: 1px solid var(--text-color); opacity: 0.2; margin: 15px 0;"></div>
                    <div style="display: flex; justify-content: space-between; font-weight: 900; font-size: 1.8em; color: var(--text-color);"><span>TOTAL VENTAS:</span><span>${a_total:,.0f}</span></div>
                </div>
                <div style="display: flex; justify-content: space-between; background-color: #553aeb; color: white; padding: 12px 20px; font-weight: bold; font-size: 1.1em;"><span>GANANCIA ESTIMADA (10%):</span><span>${a_ganancia:,.0f}</span></div>
            </div>
            """, unsafe_allow_html=True)
            
            st.markdown(f"""
            <div style="border: 2px solid #418042; border-radius: 10px; margin-bottom: 25px; font-family: sans-serif; background-color: var(--secondary-background-color); box-shadow: 0 4px 8px rgba(0,0,0,0.2); overflow: hidden;">
                <div style="background-color: #418042; color: white; padding: 12px 20px; font-weight: bold; font-size: 1.2em; letter-spacing: 0.5px;">CAJA C - ADICIONALES (Ganancia)</div>
                <div style="padding: 20px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 10px; color: var(--text-color); font-size: 1.1em;"><span>(+) EFECTIVO:</span><span>${c_efvo:,.0f}</span></div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 15px; color: var(--text-color); font-size: 1.1em;"><span>(+) TRANSFERENCIA:</span><span>${c_transf:,.0f}</span></div>
                    <div style="border-bottom: 1px solid var(--text-color); opacity: 0.2; margin: 15px 0;"></div>
                    <div style="display: flex; justify-content: space-between; font-weight: 900; font-size: 1.8em; color: var(--text-color);"><span>TOTAL GANANCIA:</span><span>${c_total:,.0f}</span></div>
                </div>
            </div>
            """, unsafe_allow_html=True)

        with col_der:
            st.markdown(f"""
            <div style="border: 2px solid #d68b31; border-radius: 10px; margin-bottom: 25px; font-family: sans-serif; background-color: var(--secondary-background-color); box-shadow: 0 4px 8px rgba(0,0,0,0.2); overflow: hidden;">
                <div style="background-color: #d68b31; color: white; padding: 12px 20px; font-weight: bold; font-size: 1.2em; letter-spacing: 0.5px;">CAJA B - SUBE (Solo Capital)</div>
                <div style="padding: 20px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 10px; color: var(--text-color); font-size: 1.1em;"><span>(+) INGRESOS EFECTIVO:</span><span>${b_efvo:,.0f}</span></div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 15px; color: var(--text-color); font-size: 1.1em;"><span>(+) INGRESOS TRANSF:</span><span>${b_transf:,.0f}</span></div>
                    <div style="border-bottom: 1px solid var(--text-color); opacity: 0.2; margin: 15px 0;"></div>
                    <div style="display: flex; justify-content: space-between; font-weight: 900; font-size: 1.8em; color: var(--text-color);"><span>TOTAL (Sin Adic):</span><span>${b_total:,.0f}</span></div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            st.markdown(f"""
            <div style="border: 2px solid #de3c31; border-radius: 10px; margin-bottom: 25px; font-family: sans-serif; background-color: var(--secondary-background-color); box-shadow: 0 4px 8px rgba(0,0,0,0.2); overflow: hidden;">
                <div style="background-color: #de3c31; color: white; padding: 12px 20px; font-weight: bold; font-size: 1.2em; letter-spacing: 0.5px;">CAJA E - CLARO (Solo Capital)</div>
                <div style="padding: 20px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 10px; color: var(--text-color); font-size: 1.1em;"><span>(+) INGRESOS EFECTIVO:</span><span>${e_efvo:,.0f}</span></div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 15px; color: var(--text-color); font-size: 1.1em;"><span>(+) INGRESOS TRANSF:</span><span>${e_transf:,.0f}</span></div>
                    <div style="border-bottom: 1px solid var(--text-color); opacity: 0.2; margin: 15px 0;"></div>
                    <div style="display: flex; justify-content: space-between; font-weight: 900; font-size: 1.8em; color: var(--text-color);"><span>TOTAL (Sin Adic):</span><span>${e_total:,.0f}</span></div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
    except Exception as e:
        st.error(f"Error cargando el dashboard. Revise la estructura de datos: {e}")

# ==========================================
# 6. ENRUTADOR PRINCIPAL (MENÚ LATERAL)
# ==========================================
st.sidebar.image("https://cdn-icons-png.flaticon.com/512/3514/3514491.png", width=120) 
st.sidebar.title("Sistema Genaro")

menu = st.sidebar.radio("Navegación", [
    "🛒 Caja", 
    "📱 Servicios", 
    "⚙️ Admin Productos",
    "📜 Historial de Ítems",
    "📊 Visor (Dashboard)"
])

if menu == "🛒 Caja":
    mostrar_caja()
elif menu == "📱 Servicios":
    mostrar_servicios()
elif menu == "⚙️ Admin Productos":
    mostrar_admin_productos()
elif menu == "📜 Historial de Ítems":
    mostrar_historial()
elif menu == "📊 Visor (Dashboard)":
    mostrar_visor()
