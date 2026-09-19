import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from st_keyup import st_keyup
import datetime 
import math 

# ==========================================
# 0. CONFIGURACIÓN REGIONAL (ARGENTINA GMT-3)
# ==========================================
ZONA_AR = datetime.timezone(datetime.timedelta(hours=-3))

# ==========================================
# 1. CONFIGURACIÓN INICIAL Y ESTILOS UI/UX
# ==========================================
st.set_page_config(page_title="Genaro POS", page_icon="🛒", layout="wide")

URL_PLANILLA = "https://docs.google.com/spreadsheets/d/1AEsHRAwONhfcATrG7k0gsVmWB1IGlqoHt89_wcT9Uuo/edit?gid=514091242#gid=514091242"

def aplicar_estilos_profesionales():
    st.markdown("""
        <style>
            /* Limpieza de la interfaz nativa */
            footer {visibility: hidden;}
            
            .block-container {
                padding-top: 1.5rem !important;
                padding-bottom: 2rem !important;
                max-width: 98% !important;
            }
            
            /* Tipografía moderna y armónica */
            p, label, span, .stMarkdown {
                font-size: 1.1rem !important;
                font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            }
            
            h1 { font-size: 2.5rem !important; font-weight: 800 !important; margin-bottom: 1rem; color: var(--text-color); }
            h2 { font-size: 2rem !important; font-weight: 700 !important; }
            h3 { font-size: 1.5rem !important; font-weight: 600 !important; }
            
            /* Botones Premium con animaciones suaves */
            .stButton > button {
                border-radius: 10px !important;
                font-size: 1.15rem !important;
                font-weight: 700 !important;
                letter-spacing: 0.5px;
                transition: all 0.2s ease-in-out;
                border: none !important;
                min-height: 3.2rem;
            }
            .stButton > button:hover {
                transform: translateY(-3px);
                box-shadow: 0 6px 15px rgba(0,0,0,0.15);
                filter: brightness(1.1);
            }
            
            /* Botón de EFECTIVO resaltado en Verde Éxito */
            button[kind="primary"] {
                background-color: #27AE60 !important;
                color: white !important;
            }
            
            /* Métricas Gigantes (El Total a Cobrar) */
            div[data-testid="stMetricValue"] {
                font-size: 3.5rem !important;
                font-weight: 900 !important;
                color: #27AE60 !important; 
                line-height: 1.1;
            }
            div[data-testid="stMetricLabel"] {
                font-size: 1.2rem !important;
                font-weight: 800 !important;
                text-transform: uppercase;
                letter-spacing: 1.5px;
                color: var(--text-color) !important;
                opacity: 0.8;
            }
            
            /* Cajas y Alertas */
            .stAlert {
                border-radius: 10px !important;
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
        st.session_state.input_monto_carga = 0
    if 'input_monto_adic' not in st.session_state:
        st.session_state.input_monto_adic = 0
    if 'search_key' not in st.session_state:
        st.session_state.search_key = 0 
    if 'admin_key' not in st.session_state:
        st.session_state.admin_key = 0
    if 'prev_key' not in st.session_state:
        st.session_state.prev_key = 0

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
        st.error("⚠️ Error de conexión. Revisa tu internet o la base de datos.")
        return pd.DataFrame() 

def procesar_venta(metodo_pago, monto_efvo=None, monto_transf=None):
    total_venta = sum(item['subtotal'] for item in st.session_state.carrito)
    fecha_actual = datetime.datetime.now(ZONA_AR)
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
        "TOTAL_VENTA": int(total_venta),
        "MONTO_EFECTIVO": int(pago_efvo),
        "MONTO_TRANSF": int(pago_transf),
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
        with st.spinner("💾 Guardando transacción en la nube..."):
            df_mov = conn.read(spreadsheet=URL_PLANILLA, worksheet="DB_MOVIMIENTOS_CAJA", ttl=0)
            conn.update(spreadsheet=URL_PLANILLA, worksheet="DB_MOVIMIENTOS_CAJA", data=pd.concat([df_mov, nueva_venta], ignore_index=True))
            
            df_historial = conn.read(spreadsheet=URL_PLANILLA, worksheet="DB_HISTORIAL_ITEMS", ttl=0)
            conn.update(spreadsheet=URL_PLANILLA, worksheet="DB_HISTORIAL_ITEMS", data=pd.concat([df_historial, df_items_nuevos], ignore_index=True))
        
        st.session_state.carrito = [] 
        
    except Exception as e:
        st.error("❌ Falló el guardado. Verifica tu conexión a internet.")

# ==========================================
# 4. LÓGICA DE NEGOCIO
# ==========================================
@st.dialog("Dividir Pago (Mixto)")
def modal_pago_mixto(total_cobrar):
    st.write(f"### Total de la compra: **${total_cobrar:,.0f}**")
    st.write("---")
    monto_transf = st.number_input("📱 Monto ingresado en Transferencia:", min_value=0, max_value=int(total_cobrar), step=100)
    monto_efvo = int(total_cobrar - monto_transf)
    st.info(f"💵 Restante a cobrar en Efectivo: **${monto_efvo:,.0f}**")
    
    st.write("---")
    if st.button("✅ Confirmar Pago Mixto", use_container_width=True, type="primary"):
        procesar_venta("MIXTO", monto_efvo=monto_efvo, monto_transf=monto_transf)
        st.rerun()

def agregar_al_carrito(nombre, precio):
    for item in st.session_state.carrito:
        if item['nombre'] == nombre:
            item['cantidad'] += 1
            item['subtotal'] = item['cantidad'] * int(precio)
            st.session_state.search_key += 1
            return
    st.session_state.carrito.append({'nombre': nombre, 'precio': int(precio), 'cantidad': 1, 'subtotal': int(precio)})
    st.session_state.search_key += 1

def actualizar_desde_cant(i):
    nueva_cant = int(st.session_state[f"cant_{i}"])
    st.session_state.carrito[i]['cantidad'] = nueva_cant
    st.session_state.carrito[i]['subtotal'] = nueva_cant * st.session_state.carrito[i]['precio']
    st.session_state[f"monto_{i}"] = st.session_state.carrito[i]['subtotal']

def actualizar_desde_monto(i):
    nuevo_monto = int(st.session_state[f"monto_{i}"])
    st.session_state.carrito[i]['subtotal'] = nuevo_monto
    precio = st.session_state.carrito[i]['precio']
    if precio > 0:
        calc = nuevo_monto / precio
        st.session_state.carrito[i]['cantidad'] = int(calc) if calc >= 1 else 1
        st.session_state[f"cant_{i}"] = st.session_state.carrito[i]['cantidad']

def calcular_recargo_automatico():
    monto = st.session_state.input_monto_carga
    st.session_state.input_monto_adic = int(math.ceil(monto / 2000.0) * 100) if monto > 0 else 0

# ==========================================
# 5. VISTAS Y MÓDULOS (Frontend)
# ==========================================

def mostrar_caja():
    st.markdown("<h1>🛒 Caja Registradora</h1>", unsafe_allow_html=True)
    df_productos = cargar_productos() 
    
    col_izq, col_der = st.columns([5, 5])
    
    with col_izq:
        with st.container(border=True):
            st.subheader("🔍 Buscador de Productos")
            busqueda = st_keyup("Busca por nombre o marca (Ej. Lays, Coca):", debounce=300, key=f"buscador_{st.session_state.search_key}")
            
            if busqueda:
                resultados = df_productos[df_productos['NOMBRE'].str.contains(busqueda, case=False, na=False)].head(15)
                if resultados.empty:
                    st.warning("No hay coincidencias en el catálogo.")
                else:
                    for index, row in resultados.iterrows():
                        c1, c2, c3 = st.columns([5, 2, 3])
                        c1.write(f"**{row['NOMBRE']}**")
                        c2.write(f"${int(row['PRECIO_DIA'])}")
                        if c3.button("➕ Agregar", key=f"btn_add_{index}", use_container_width=True):
                            agregar_al_carrito(row['NOMBRE'], row['PRECIO_DIA'])
                            st.rerun()
                            
    with col_der:
        with st.container(border=True):
            st.subheader("🛒 Tu Carrito")
            if not st.session_state.carrito:
                st.info("El carrito está vacío. Agrega productos desde el buscador.")
            else:
                total = 0
                h1, h2, h3, h4 = st.columns([4, 3, 3, 1])
                h1.write("**Producto**")
                h2.write("**Cant**")
                h3.write("**Monto $**")
                
                for i, item in enumerate(st.session_state.carrito):
                    c1, c2, c3, c4 = st.columns([4, 3, 3, 1])
                    c1.write(f"{item['nombre']}")
                    
                    c2.number_input("Cant", value=int(item['cantidad']), min_value=1, step=1, 
                                    key=f"cant_{i}", on_change=actualizar_desde_cant, args=(i,), label_visibility="collapsed")
                                    
                    c3.number_input("Monto", value=int(item['subtotal']), min_value=0, step=100, 
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
                    st.toast("✅ Venta en Efectivo registrada.", icon="✅")
                    st.rerun()
                if col_transf.button("📱 Transf.", use_container_width=True):
                    procesar_venta("TRANSFERENCIA")
                    st.toast("✅ Venta por Transferencia registrada.", icon="✅")
                    st.rerun()
                if col_mixto.button("💳 Mixto", use_container_width=True):
                    modal_pago_mixto(total)

def mostrar_servicios():
    st.markdown("<h1>📱 Cargas y Servicios</h1>", unsafe_allow_html=True)
    
    with st.container(border=True):
        st.write("Registra recargas virtuales o pagos de servicios de forma ágil.")
        st.write("---")
        col1, col2 = st.columns(2)
        with col1:
            servicio = st.selectbox("Empresa / Servicio", ["Claro", "Personal", "Movistar", "Tuenti", "DIRECTV", "SUBE", "Otro"])
            monto_carga = st.number_input("Monto a Cargar ($)", min_value=0, step=500, 
                                          key="input_monto_carga", on_change=calcular_recargo_automatico)
        with col2:
            monto_adic = st.number_input("Recargo / Adicional ($)", min_value=0, step=50, key="input_monto_adic")
            metodo_pago = st.radio("Método de Pago", ["EFECTIVO", "TRANSFERENCIA", "MIXTO"], horizontal=True)
            
        total_cobrar = int(monto_carga + monto_adic)
        st.info(f"### **💰 Total a cobrar al cliente: ${total_cobrar:,.0f}**")
        
        monto_transf = 0
        monto_efvo = 0
        if metodo_pago == "MIXTO":
            monto_transf = st.number_input("Monto pagado en Transferencia:", min_value=0, max_value=int(total_cobrar), step=100)
            monto_efvo = total_cobrar - monto_transf
            st.write(f"💵 Restante en Efectivo: **${monto_efvo:,.0f}**")
        elif metodo_pago == "EFECTIVO":
            monto_efvo = total_cobrar
        elif metodo_pago == "TRANSFERENCIA":
            monto_transf = total_cobrar
            
        st.divider()
        
        if st.button("🚀 Registrar Carga", type="primary", use_container_width=True):
            if monto_carga <= 0:
                st.error("⚠️ El monto de la carga debe ser mayor a cero.")
            else:
                fecha = datetime.datetime.now(ZONA_AR).strftime("%d/%m/%Y %H:%M:%S")
                nueva_carga = pd.DataFrame([{
                    "FECHA": fecha, "SERVICIO": servicio, "MONTO_CARGA": monto_carga,
                    "MONTO_ADICIONAL": monto_adic, "TOTAL_COBRADO": total_cobrar,
                    "PAGO_EFVO": monto_efvo, "PAGO_TRANSF": monto_transf
                }])
                try:
                    conn = st.connection("gsheets", type=GSheetsConnection)
                    with st.spinner("Guardando en el sistema..."):
                        df_cargas = conn.read(spreadsheet=URL_PLANILLA, worksheet="DB_CARGAS", ttl=0)
                        conn.update(spreadsheet=URL_PLANILLA, worksheet="DB_CARGAS", data=pd.concat([df_cargas, nueva_carga], ignore_index=True))
                    st.toast(f"✅ Carga guardada.", icon="📲")
                    del st.session_state["input_monto_carga"]
                    del st.session_state["input_monto_adic"]
                    st.rerun()
                except Exception as e:
                    st.error("❌ Error al guardar. Intente nuevamente.")

def mostrar_admin_productos():
    st.markdown("<h1>⚙️ Gestión de Catálogo</h1>", unsafe_allow_html=True)
    
    if 'admin_msg' in st.session_state:
        st.success(st.session_state.admin_msg)
        del st.session_state.admin_msg
        
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
                producto_seleccionado = st.selectbox("BUSCAR PRODUCTO A MODIFICAR:", [""] + lista_productos, key=f"mod_sel_{st.session_state.admin_key}")
                
                if producto_seleccionado:
                    datos_prod = df_actual[df_actual['NOMBRE'] == producto_seleccionado].iloc[0]
                    idx_prod = df_actual.index[df_actual['NOMBRE'] == producto_seleccionado].tolist()[0]
                    
                    st.write("---")
                    c_actual, c_nuevo = st.columns(2)
                    with c_actual:
                        st.write("**📝 Datos Actuales:**")
                        st.write(f"**Proveedor:** {datos_prod.get('PROVEEDOR', '-')}")
                        st.write(f"**Costo:** ${int(datos_prod.get('COSTO', 0))}")
                        st.write(f"**Precio:** ${int(datos_prod.get('PRECIO_DIA', 0))}")
                        st.write(f"**Margen:** {float(datos_prod.get('MARGEN_%', 0)) * 100:.2f}%")
                    with c_nuevo:
                        st.write("**✏️ Completar solo si cambia:**")
                        nuevo_prov = st.text_input("Nuevo Proveedor:", value=datos_prod.get('PROVEEDOR', ''), key=f"m_prov_{st.session_state.admin_key}")
                        nuevo_costo = st.number_input("Nuevo Costo ($):", value=int(datos_prod.get('COSTO', 0)), min_value=0, step=100, key=f"m_cost_{st.session_state.admin_key}")
                        nuevo_precio = st.number_input("Nuevo Precio ($):", value=int(datos_prod.get('PRECIO_DIA', 0)), min_value=0, step=100, key=f"m_prec_{st.session_state.admin_key}")
                        nuevo_margen_calc = (nuevo_precio - nuevo_costo) / nuevo_costo if nuevo_costo > 0 else 0
                        st.info(f"**Margen Proyectado: {nuevo_margen_calc * 100:.2f}%**")
                    
                    st.write("---")
                    col_btn1, col_btn2 = st.columns(2)
                    with col_btn1:
                        if st.button("🔄 ACTUALIZAR PRECIOS", type="primary", use_container_width=True, key=f"m_btn_{st.session_state.admin_key}"):
                            df_actual.at[idx_prod, 'PROVEEDOR'] = nuevo_prov
                            df_actual.at[idx_prod, 'COSTO'] = nuevo_costo
                            df_actual.at[idx_prod, 'PRECIO_DIA'] = nuevo_precio
                            df_actual.at[idx_prod, 'PRECIO_NOCHE'] = nuevo_precio 
                            df_actual.at[idx_prod, 'MARGEN_%'] = nuevo_margen_calc
                            df_actual.at[idx_prod, 'FECHA_ACT'] = datetime.datetime.now(ZONA_AR).strftime("%d/%m/%Y")
                            
                            with st.spinner("Guardando en la nube..."):
                                conn.update(spreadsheet=URL_PLANILLA, worksheet="DB_PRODUCTOS", data=df_actual)
                                st.cache_data.clear() 
                            st.session_state.admin_msg = "✅ ¡Actualizado exitosamente!"
                            st.session_state.admin_key += 1
                            st.rerun()
                    with col_btn2:
                        confirmar = st.checkbox("⚠️ Confirmar borrado", key=f"m_chk_{st.session_state.admin_key}")
                        if st.button("🗑️ ELIMINAR", use_container_width=True, key=f"m_del_{st.session_state.admin_key}"):
                            if confirmar:
                                df_actual = df_actual.drop(idx_prod)
                                with st.spinner("Eliminando..."):
                                    conn.update(spreadsheet=URL_PLANILLA, worksheet="DB_PRODUCTOS", data=df_actual)
                                    st.cache_data.clear()
                                st.session_state.admin_msg = "🗑️ Producto eliminado."
                                st.session_state.admin_key += 1
                                st.rerun()
                            else:
                                st.warning("Debes marcar la casilla.")
                                
        with col_der:
            with st.container(border=True):
                st.markdown("### ➕ ALTA DE PRODUCTO")
                n_nombre = st.text_input("NOMBRE:", key=f"n_nom_{st.session_state.admin_key}")
                n_cat = st.selectbox("CATEGORÍA:", categorias_unicas + ["OTRO..."], key=f"n_cat_{st.session_state.admin_key}")
                n_prov = st.selectbox("PROVEEDOR:", proveedores_unicos + ["OTRO..."], key=f"n_prov_{st.session_state.admin_key}")
                n_unidad = st.selectbox("UNIDAD:", ["Unidad", "Kg", "Litro"], key=f"n_uni_{st.session_state.admin_key}")
                n_costo = st.number_input("COSTO ($):", min_value=0, step=100, key=f"n_cost_{st.session_state.admin_key}")
                n_precio = st.number_input("PRECIO VENTA ($):", min_value=0, step=100, key=f"n_prec_{st.session_state.admin_key}")
                
                n_margen = (n_precio - n_costo) / n_costo if n_costo > 0 else 0
                st.info(f"**Margen Estimado: {n_margen * 100:.2f}%**")
                
                if st.button("➕ CREAR PRODUCTO", type="primary", use_container_width=True, key=f"n_btn_{st.session_state.admin_key}"):
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
                            "FECHA_ACT": datetime.datetime.now(ZONA_AR).strftime("%d/%m/%Y")
                        }])
                        with st.spinner("Creando producto..."):
                            conn.update(spreadsheet=URL_PLANILLA, worksheet="DB_PRODUCTOS", data=pd.concat([df_actual, nuevo_registro], ignore_index=True))
                            st.cache_data.clear()
                        st.session_state.admin_msg = f"✅ ¡{n_nombre} añadido al catálogo!"
                        st.session_state.admin_key += 1
                        st.rerun()
    except Exception as e:
        st.error(f"Error al cargar el panel de administración.")

def mostrar_historial():
    st.markdown("<h1>📜 Historial de Ítems</h1>", unsafe_allow_html=True)
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        df_historial = conn.read(spreadsheet=URL_PLANILLA, worksheet="DB_HISTORIAL_ITEMS", ttl=0)
        df_historial['FECHA_REAL'] = pd.to_datetime(df_historial['FECHA'], dayfirst=True, errors='coerce')
        
        with st.container(border=True):
            col1, col2 = st.columns([3, 7])
            with col1:
                fecha_elegida = st.date_input("🗓️ Filtrar por Día:", datetime.datetime.now(ZONA_AR).date())
                palabra_clave = st.text_input("🔍 Buscar producto específico:")
            
            mask_fecha = df_historial['FECHA_REAL'].dt.date == fecha_elegida
            df_filtrado = df_historial[mask_fecha]
            
            if palabra_clave:
                df_filtrado = df_filtrado[df_filtrado['PRODUCTO'].str.contains(palabra_clave, case=False, na=False)]
            
            with col2:
                columnas_mostrar = ['FECHA', 'TICKET_ID', 'PRODUCTO', 'CANTIDAD', 'SUBTOTAL', 'METODO_PAGO']
                st.dataframe(df_filtrado[columnas_mostrar], use_container_width=True, hide_index=True)
                
                total_items = df_filtrado['SUBTOTAL'].sum()
                total_efvo = df_filtrado[df_filtrado['METODO_PAGO'] == 'EFECTIVO']['SUBTOTAL'].sum()
                total_transf = df_filtrado[df_filtrado['METODO_PAGO'] == 'TRANSFERENCIA']['SUBTOTAL'].sum()
                total_mixto = df_filtrado[df_filtrado['METODO_PAGO'] == 'MIXTO']['SUBTOTAL'].sum()
                
                st.write("---")
                m1, m2, m3, m4 = st.columns(4)
                m1.metric(label=f"💰 TOTAL FILTRADO", value=f"${int(total_items):,.0f}")
                m2.metric(label="💵 En Efectivo", value=f"${int(total_efvo):,.0f}")
                m3.metric(label="📱 En Transf.", value=f"${int(total_transf):,.0f}")
                m4.metric(label="💳 Pago Mixto", value=f"${int(total_mixto):,.0f}")
            
    except Exception as e:
        st.error("No se pudo cargar el historial.")

def mostrar_visor():
    st.markdown("<h1>📊 Dashboard Ejecutivo</h1>", unsafe_allow_html=True)
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        df_caja = conn.read(spreadsheet=URL_PLANILLA, worksheet="DB_MOVIMIENTOS_CAJA", ttl=0)
        df_cargas = conn.read(spreadsheet=URL_PLANILLA, worksheet="DB_CARGAS", ttl=0)
        
        df_caja['FECHA_REAL'] = pd.to_datetime(df_caja['FECHA'], dayfirst=True, errors='coerce')
        df_cargas['FECHA_REAL'] = pd.to_datetime(df_cargas['FECHA'], dayfirst=True, errors='coerce')
        
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 4, 3])
            fecha_elegida = c2.date_input("📅 Seleccionar fecha a consultar:", datetime.datetime.now(ZONA_AR).date())
        
        df_hoy_caja = df_caja[df_caja['FECHA_REAL'].dt.date == fecha_elegida]
        df_hoy_cargas = df_cargas[df_cargas['FECHA_REAL'].dt.date == fecha_elegida]
        
        a_efvo = int(df_hoy_caja['MONTO_EFECTIVO'].sum())
        a_transf = int(df_hoy_caja['MONTO_TRANSF'].sum())
        a_total = int(df_hoy_caja['TOTAL_VENTA'].sum())
        a_ganancia = int(a_total * 0.10)
        
        b_efvo = b_transf = b_total = 0
        c_efvo = c_transf = c_total = 0
        e_efvo = e_transf = e_total = 0
        
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
            <div style="border: 1px solid rgba(128,128,128,0.2); border-radius: 12px; margin-bottom: 25px; background-color: var(--secondary-background-color); box-shadow: 0 4px 10px rgba(0,0,0,0.1); overflow: hidden;">
                <div style="background-color: #3b1be3; color: white; padding: 12px 20px; font-weight: bold; font-size: 1.2em;">CAJA A - DRUGSTORE</div>
                <div style="padding: 20px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 10px; color: var(--text-color); font-size: 1.1em;"><span>(+) EFECTIVO:</span><span>${int(a_efvo):,.0f}</span></div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 15px; color: var(--text-color); font-size: 1.1em;"><span>(+) TRANSFERENCIAS:</span><span>${int(a_transf):,.0f}</span></div>
                    <div style="border-bottom: 1px solid var(--text-color); opacity: 0.1; margin: 15px 0;"></div>
                    <div style="display: flex; justify-content: space-between; font-weight: 900; font-size: 1.8em; color: var(--text-color);"><span>TOTAL VENTAS:</span><span>${int(a_total):,.0f}</span></div>
                </div>
                <div style="display: flex; justify-content: space-between; background-color: rgba(59, 27, 227, 0.8); color: white; padding: 12px 20px; font-weight: bold;"><span>GANANCIA ESTIMADA (10%):</span><span>${int(a_ganancia):,.0f}</span></div>
            </div>
            """, unsafe_allow_html=True)
            
            st.markdown(f"""
            <div style="border: 1px solid rgba(128,128,128,0.2); border-radius: 12px; margin-bottom: 25px; background-color: var(--secondary-background-color); box-shadow: 0 4px 10px rgba(0,0,0,0.1); overflow: hidden;">
                <div style="background-color: #418042; color: white; padding: 12px 20px; font-weight: bold; font-size: 1.2em;">CAJA C - ADICIONALES (Ganancia)</div>
                <div style="padding: 20px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 10px; color: var(--text-color); font-size: 1.1em;"><span>(+) EFECTIVO:</span><span>${int(c_efvo):,.0f}</span></div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 15px; color: var(--text-color); font-size: 1.1em;"><span>(+) TRANSFERENCIA:</span><span>${int(c_transf):,.0f}</span></div>
                    <div style="border-bottom: 1px solid var(--text-color); opacity: 0.1; margin: 15px 0;"></div>
                    <div style="display: flex; justify-content: space-between; font-weight: 900; font-size: 1.8em; color: var(--text-color);"><span>TOTAL GANANCIA:</span><span>${int(c_total):,.0f}</span></div>
                </div>
            </div>
            """, unsafe_allow_html=True)

        with col_der:
            st.markdown(f"""
            <div style="border: 1px solid rgba(128,128,128,0.2); border-radius: 12px; margin-bottom: 25px; background-color: var(--secondary-background-color); box-shadow: 0 4px 10px rgba(0,0,0,0.1); overflow: hidden;">
                <div style="background-color: #d68b31; color: white; padding: 12px 20px; font-weight: bold; font-size: 1.2em;">CAJA B - SUBE (Solo Capital)</div>
                <div style="padding: 20px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 10px; color: var(--text-color); font-size: 1.1em;"><span>(+) INGRESOS EFECTIVO:</span><span>${int(b_efvo):,.0f}</span></div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 15px; color: var(--text-color); font-size: 1.1em;"><span>(+) INGRESOS TRANSF:</span><span>${int(b_transf):,.0f}</span></div>
                    <div style="border-bottom: 1px solid var(--text-color); opacity: 0.1; margin: 15px 0;"></div>
                    <div style="display: flex; justify-content: space-between; font-weight: 900; font-size: 1.8em; color: var(--text-color);"><span>TOTAL (Sin Adic):</span><span>${int(b_total):,.0f}</span></div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            st.markdown(f"""
            <div style="border: 1px solid rgba(128,128,128,0.2); border-radius: 12px; margin-bottom: 25px; background-color: var(--secondary-background-color); box-shadow: 0 4px 10px rgba(0,0,0,0.1); overflow: hidden;">
                <div style="background-color: #de3c31; color: white; padding: 12px 20px; font-weight: bold; font-size: 1.2em;">CAJA E - CLARO (Solo Capital)</div>
                <div style="padding: 20px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 10px; color: var(--text-color); font-size: 1.1em;"><span>(+) INGRESOS EFECTIVO:</span><span>${int(e_efvo):,.0f}</span></div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 15px; color: var(--text-color); font-size: 1.1em;"><span>(+) INGRESOS TRANSF:</span><span>${int(e_transf):,.0f}</span></div>
                    <div style="border-bottom: 1px solid var(--text-color); opacity: 0.1; margin: 15px 0;"></div>
                    <div style="display: flex; justify-content: space-between; font-weight: 900; font-size: 1.8em; color: var(--text-color);"><span>TOTAL (Sin Adic):</span><span>${int(e_total):,.0f}</span></div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
    except Exception as e:
        st.error("Error cargando el dashboard.")

# ==========================================
# NUEVO MÓDULO: PREVENTISTAS (Interactivo + Alta)
# ==========================================
def mostrar_preventistas():
    st.markdown("<h1>🚚 Catálogo por Preventista</h1>", unsafe_allow_html=True)
    st.write("Selecciona un proveedor, edita los precios directamente en la tabla o da de alta un producto nuevo.")
    
    if 'prev_msg' in st.session_state:
        st.success(st.session_state.prev_msg)
        del st.session_state.prev_msg
        
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        df_productos = conn.read(spreadsheet=URL_PLANILLA, worksheet="DB_PRODUCTOS", ttl=0).dropna(subset=['NOMBRE'])
        
        if not df_productos.empty:
            proveedores_unicos = sorted(df_productos['PROVEEDOR'].dropna().unique().tolist())
            categorias_unicas = sorted(df_productos['CATEGORIA'].dropna().unique().tolist())
            
            with st.container(border=True):
                proveedor_elegido = st.selectbox("👤 Seleccionar Preventista / Proveedor:", [""] + proveedores_unicos)
                
                if proveedor_elegido:
                    df_filtrado = df_productos[df_productos['PROVEEDOR'] == proveedor_elegido]
                    st.write(f"### Productos de: **{proveedor_elegido}** ({len(df_filtrado)} ítems)")
                    st.info("💡 **Tip:** Edita el Costo o el Precio y presiona Enter (o toca afuera de la celda). Verás cómo el porcentaje de Ganancia se recalcula **en vivo** en la tabla.")
                    
                    columnas_mostrar = ['NOMBRE', 'COSTO', 'PRECIO_DIA', 'MARGEN_%']
                    df_edicion = df_filtrado[columnas_mostrar].copy()
                    df_edicion['MARGEN_%'] = (pd.to_numeric(df_edicion['MARGEN_%'], errors='coerce').fillna(0) * 100).round(1)
                    
                    editor_key = f"ed_prev_{st.session_state.prev_key}_{proveedor_elegido}"
                    
                    # ⚡ TRUCO DE CÁLCULO EN VIVO ⚡
                    # Interceptamos lo que el usuario escribió antes de que se dibuje la tabla
                    if editor_key in st.session_state:
                        cambios_en_vivo = st.session_state[editor_key].get("edited_rows", {})
                        for row_pos_str, mods in cambios_en_vivo.items():
                            row_pos = int(row_pos_str)
                            if row_pos < len(df_edicion):
                                real_idx = df_edicion.index[row_pos]
                                c_val = mods.get("COSTO", df_edicion.at[real_idx, "COSTO"])
                                p_val = mods.get("PRECIO_DIA", df_edicion.at[real_idx, "PRECIO_DIA"])
                                
                                if c_val > 0:
                                    calc_margen = ((p_val - c_val) / c_val) * 100
                                else:
                                    calc_margen = 0.0
                                    
                                df_edicion.at[real_idx, "MARGEN_%"] = round(calc_margen, 1)

                    edited_df = st.data_editor(
                        df_edicion,
                        key=editor_key,
                        use_container_width=True,
                        hide_index=True,
                        disabled=["NOMBRE", "MARGEN_%"], 
                        column_config={
                            "NOMBRE": st.column_config.TextColumn("PRODUCTO"),
                            "COSTO": st.column_config.NumberColumn("COSTO ($)", min_value=0, step=100),
                            "PRECIO_DIA": st.column_config.NumberColumn("PRECIO VENTA ($)", min_value=0, step=100),
                            "MARGEN_%": st.column_config.NumberColumn("GANANCIA (%)", format="%.1f %%")
                        }
                    )
                    
                    if st.button("💾 Guardar Nuevos Precios", type="primary", use_container_width=True):
                        with st.spinner("Actualizando catálogo en la nube..."):
                            cambios_realizados = False
                            for idx, row in edited_df.iterrows():
                                n_costo = float(row['COSTO'])
                                n_precio = float(row['PRECIO_DIA'])
                                c_viejo = float(df_filtrado.loc[idx, 'COSTO'])
                                p_viejo = float(df_filtrado.loc[idx, 'PRECIO_DIA'])
                                
                                if n_costo != c_viejo or n_precio != p_viejo:
                                    df_productos.at[idx, 'COSTO'] = n_costo
                                    df_productos.at[idx, 'PRECIO_DIA'] = n_precio
                                    df_productos.at[idx, 'PRECIO_NOCHE'] = n_precio
                                    n_margen = (n_precio - n_costo) / n_costo if n_costo > 0 else 0
                                    df_productos.at[idx, 'MARGEN_%'] = n_margen
                                    df_productos.at[idx, 'FECHA_ACT'] = datetime.datetime.now(ZONA_AR).strftime("%d/%m/%Y")
                                    cambios_realizados = True
                            
                            if cambios_realizados:
                                conn.update(spreadsheet=URL_PLANILLA, worksheet="DB_PRODUCTOS", data=df_productos)
                                st.cache_data.clear()
                                st.session_state.prev_msg = "✅ ¡Los precios de este proveedor fueron actualizados!"
                                st.session_state.prev_key += 1
                                st.rerun()
                            else:
                                st.warning("No detecté ninguna modificación en los números.")
                    
                    # ---------------- ALTA RÁPIDA DE PRODUCTOS (PREVENTISTAS) ----------------
                    st.write("---")
                    with st.expander(f"➕ Alta rápida de producto para {proveedor_elegido}"):
                        c1, c2 = st.columns(2)
                        with c1:
                            p_nombre = st.text_input("NOMBRE DEL PRODUCTO:", key=f"p_nom_{st.session_state.prev_key}")
                            p_cat = st.selectbox("CATEGORÍA:", categorias_unicas + ["OTRO..."], key=f"p_cat_{st.session_state.prev_key}")
                            p_unidad = st.selectbox("UNIDAD:", ["Unidad", "Kg", "Litro"], key=f"p_uni_{st.session_state.prev_key}")
                        with c2:
                            p_costo = st.number_input("COSTO ($):", min_value=0, step=100, key=f"p_cost_{st.session_state.prev_key}")
                            p_precio = st.number_input("PRECIO VENTA ($):", min_value=0, step=100, key=f"p_prec_{st.session_state.prev_key}")
                            p_margen = (p_precio - p_costo) / p_costo if p_costo > 0 else 0
                            st.info(f"**Margen Estimado: {p_margen * 100:.2f}%**")
                            
                        if st.button("➕ GUARDAR NUEVO PRODUCTO", type="primary", use_container_width=True, key=f"btn_p_add_{st.session_state.prev_key}"):
                            if not p_nombre.strip():
                                st.error("⚠️ El nombre es obligatorio.")
                            elif p_precio <= 0:
                                st.error("⚠️ El precio debe ser mayor a 0.")
                            else:
                                nuevo_id = df_productos['ID_PRODUCTO'].max() + 1 if not df_productos.empty else 1
                                nuevo_registro = pd.DataFrame([{
                                    "ID_PRODUCTO": nuevo_id, "NOMBRE": p_nombre, "CATEGORIA": p_cat, "PROVEEDOR": proveedor_elegido,
                                    "UNIDAD": p_unidad, "COSTO": p_costo, "MARGEN_%": p_margen, 
                                    "PRECIO_DIA": p_precio, "PRECIO_NOCHE": p_precio, 
                                    "FECHA_ACT": datetime.datetime.now(ZONA_AR).strftime("%d/%m/%Y")
                                }])
                                with st.spinner("Creando producto..."):
                                    conn.update(spreadsheet=URL_PLANILLA, worksheet="DB_PRODUCTOS", data=pd.concat([df_productos, nuevo_registro], ignore_index=True))
                                    st.cache_data.clear()
                                st.session_state.prev_msg = f"✅ ¡{p_nombre} añadido al catálogo de {proveedor_elegido}!"
                                st.session_state.prev_key += 1
                                st.rerun()
                                
        else:
            st.warning("No hay productos cargados en la base de datos.")
            
    except Exception as e:
        st.error(f"Error al cargar el módulo de preventistas.")

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
    "📊 Visor (Dashboard)",
    "🚚 Preventistas"
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
elif menu == "🚚 Preventistas":
    mostrar_preventistas()
