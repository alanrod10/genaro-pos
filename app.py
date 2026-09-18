import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from st_keyup import st_keyup
import datetime 
import math 

# ==========================================
# 1. CONFIGURACIÓN INICIAL Y ESTILOS
# ==========================================
st.set_page_config(page_title="Genaro POS", page_icon="🛒", layout="wide")

URL_PLANILLA = "https://docs.google.com/spreadsheets/d/1AEsHRAwONhfcATrG7k0gsVmWB1IGlqoHt89_wcT9Uuo/edit?gid=514091242#gid=514091242"

def aplicar_estilos_profesionales():
    """Inyecta CSS para darle aspecto de aplicación de escritorio, eliminando márgenes y menús por defecto."""
    st.markdown("""
        <style>
            /* Oculta el menú superior derecho y el pie de página de Streamlit */
            #MainMenu {visibility: hidden;}
            footer {visibility: hidden;}
            
            /* Reduce los espacios en blanco superiores para aprovechar la pantalla */
            .block-container {
                padding-top: 1.5rem !important;
                padding-bottom: 1rem !important;
            }
            
            /* Mejora el aspecto de las métricas (números grandes) */
            div[data-testid="stMetricValue"] {
                font-size: 2.2rem !important;
                color: #2e7b32 !important;
            }
        </style>
    """, unsafe_allow_html=True)

aplicar_estilos_profesionales()

# ==========================================
# 2. GESTIÓN DEL ESTADO (MEMORIA)
# ==========================================
def inicializar_memoria():
    """Crea todas las variables temporales necesarias desde el inicio para evitar errores."""
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
    """Descarga el catálogo y lo guarda en la RAM por 10 minutos para búsquedas ultrarrápidas."""
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        df = conn.read(spreadsheet=URL_PLANILLA, worksheet="DB_PRODUCTOS")
        return df.dropna(subset=['NOMBRE'])
    except Exception as e:
        st.error(f"⚠️ Error al conectar con Google Sheets (Catálogo). Revisa tu conexión. Detalle: {e}")
        return pd.DataFrame() # Retorna tabla vacía para no romper la app

def procesar_venta(metodo_pago, monto_efvo=None, monto_transf=None):
    """Procesa el carrito, genera el ticket y sube la transacción a las 2 bases de datos."""
    total_venta = sum(item['subtotal'] for item in st.session_state.carrito)
    fecha_actual = datetime.datetime.now()
    ticket_id = "T-" + str(int(fecha_actual.timestamp() * 1000))
    
    # Asignación inteligente de montos
    if monto_efvo is None and monto_transf is None:
        pago_efvo = total_venta if metodo_pago == "EFECTIVO" else 0
        pago_transf = total_venta if metodo_pago == "TRANSFERENCIA" else 0
    else:
        pago_efvo = monto_efvo
        pago_transf = monto_transf
    
    # 1. Estructurar DB_MOVIMIENTOS_CAJA
    nueva_venta = pd.DataFrame([{
        "TICKET_ID": ticket_id,
        "FECHA": fecha_actual.strftime("%d/%m/%Y %H:%M:%S"),
        "TOTAL_VENTA": total_venta,
        "MONTO_EFECTIVO": pago_efvo,
        "MONTO_TRANSF": pago_transf,
        "ES_NOCTURNO": False
    }])
    
    # 2. Estructurar DB_HISTORIAL_ITEMS
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
    
    # 3. Ejecución segura a Google Sheets
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        with st.spinner("💾 Procesando transacción en la nube..."):
            df_mov = conn.read(spreadsheet=URL_PLANILLA, worksheet="DB_MOVIMIENTOS_CAJA", ttl=0)
            conn.update(spreadsheet=URL_PLANILLA, worksheet="DB_MOVIMIENTOS_CAJA", data=pd.concat([df_mov, nueva_venta], ignore_index=True))
            
            df_historial = conn.read(spreadsheet=URL_PLANILLA, worksheet="DB_HISTORIAL_ITEMS", ttl=0)
            conn.update(spreadsheet=URL_PLANILLA, worksheet="DB_HISTORIAL_ITEMS", data=pd.concat([df_historial, df_items_nuevos], ignore_index=True))
        
        st.session_state.carrito = [] # Limpiamos memoria si todo sale bien
        
    except Exception as e:
        st.error(f"❌ Falló el guardado. El cliente no fue cobrado en el sistema. Error: {e}")

# ==========================================
# 4. LÓGICA DE NEGOCIO (Controladores)
# ==========================================
@st.dialog("Dividir Pago (Mixto)")
def modal_pago_mixto(total_cobrar):
    """Ventana emergente para calcular pagos combinados."""
    st.write(f"El total a cobrar es **${total_cobrar:,.0f}**")
    monto_transf = st.number_input("Monto en Transferencia:", min_value=0.0, max_value=float(total_cobrar), step=100.0)
    monto_efvo = total_cobrar - monto_transf
    st.info(f"💵 Restante en Efectivo: **${monto_efvo:,.0f}**")
    
    if st.button("✅ Confirmar Pago", use_container_width=True, type="primary"):
        procesar_venta("MIXTO", monto_efvo=monto_efvo, monto_transf=monto_transf)
        st.rerun()

def agregar_al_carrito(nombre, precio):
    """Agrega un producto o suma 1 si ya existe en la lista."""
    for item in st.session_state.carrito:
        if item['nombre'] == nombre:
            item['cantidad'] += 1.0
            item['subtotal'] = item['cantidad'] * item['precio']
            return
    st.session_state.carrito.append({'nombre': nombre, 'precio': float(precio), 'cantidad': 1.0, 'subtotal': float(precio)})

def actualizar_desde_cant(i):
    """Cálculo bidireccional: Modifica el subtotal basado en la cantidad ingresada."""
    nueva_cant = st.session_state[f"cant_{i}"]
    st.session_state.carrito[i]['cantidad'] = nueva_cant
    st.session_state.carrito[i]['subtotal'] = nueva_cant * st.session_state.carrito[i]['precio']
    st.session_state[f"monto_{i}"] = st.session_state.carrito[i]['subtotal']

def actualizar_desde_monto(i):
    """Cálculo bidireccional: Modifica la cantidad basada en el monto ($) ingresado (Ej: Mignon)."""
    nuevo_monto = st.session_state[f"monto_{i}"]
    st.session_state.carrito[i]['subtotal'] = nuevo_monto
    precio = st.session_state.carrito[i]['precio']
    if precio > 0:
        st.session_state.carrito[i]['cantidad'] = nuevo_monto / precio
        st.session_state[f"cant_{i}"] = st.session_state.carrito[i]['cantidad']

def calcular_recargo_automatico():
    """Redondea el recargo de servicios a múltiplos de 100 por cada $2000."""
    monto = st.session_state.input_monto_carga
    st.session_state.input_monto_adic = float(math.ceil(monto / 2000.0) * 100) if monto > 0 else 0.0

# ==========================================
# 5. VISTAS Y MÓDULOS (Frontend)
# ==========================================
df_productos = cargar_productos() # Se carga globalmente una vez configurado todo

def mostrar_caja():
    st.title("🛒 Caja - Lo de Genaro")
    col_izq, col_der = st.columns([5, 5])
    
    with col_izq:
        st.subheader("🔍 Buscador")
        busqueda = st_keyup("Busca un producto:", placeholder="Ej. coc, mignon...", debounce=300)
        
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
            if col_transf.button("📱 Transf", use_container_width=True, type="primary"):
                procesar_venta("TRANSFERENCIA")
                st.toast("✅ Venta por Transferencia registrada con éxito.", icon="✅")
                st.rerun()
            if col_mixto.button("💳 Mixto", use_container_width=True):
                modal_pago_mixto(total)

def mostrar_servicios():
    st.title("📱 Cargas y Servicios")
    st.write("Registra recargas virtuales o pagos de servicios.")
    
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
        st.info(f"**💰 Total a cobrar al cliente: ${total_cobrar:,.0f}**")
        
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
                st.markdown("### 🔄 BUSCADOR Y ACTUALIZADOR")
                lista_productos = sorted(df_actual['NOMBRE'].tolist())
                producto_seleccionado = st.selectbox("BUSCAR PRODUCTO:", [""] + lista_productos)
                
                if producto_seleccionado:
                    datos_prod = df_actual[df_actual['NOMBRE'] == producto_seleccionado].iloc[0]
                    idx_prod = df_actual.index[df_actual['NOMBRE'] == producto_seleccionado].tolist()[0]
                    
                    st.write("---")
                    c_actual, c_nuevo = st.columns(2)
                    with c_actual:
                        st.write("**Datos Actuales:**")
                        st.write(f"**Proveedor:** {datos_prod.get('PROVEEDOR', '-')}")
                        st.write(f"**Costo:** ${float(datos_prod.get('COSTO', 0)):.2f}")
                        st.write(f"**Precio:** ${float(datos_prod.get('PRECIO_DIA', 0)):.2f}")
                        st.write(f"**Margen:** {float(datos_prod.get('MARGEN_%', 0)) * 100:.2f}%")
                    with c_nuevo:
                        st.write("**Completar solo si cambia:**")
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
                            
                            with st.spinner("Guardando..."):
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
                st.markdown("### ➕ ALTA NUEVO PRODUCTO")
                n_nombre = st.text_input("NOMBRE:")
                n_cat = st.selectbox("CATEGORÍA:", categorias_unicas + ["OTRO..."])
                n_prov = st.selectbox("PROVEEDOR:", proveedores_unicos + ["OTRO..."])
                n_unidad = st.selectbox("UNIDAD:", ["Unidad", "Kg", "Litro"])
                n_costo = st.number_input("COSTO ($):", min_value=0.0, key="new_cost")
                n_precio = st.number_input("PRECIO ($):", min_value=0.0, key="new_price")
                
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
            st.metric(label=f"Total de estos ítems ({fecha_elegida.strftime('%d/%m/%Y')})", value=f"${total_items:,.0f}")
            
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
        
        # Extracción y Cálculo de Métricas Analíticas
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
        
        # Tarjetas HTML / UI Dashboard
        col_izq, col_espacio, col_der = st.columns([10, 1, 10])
        
        with col_izq:
            st.markdown(f"""
            <div style="border: 2px solid #3b1be3; border-radius: 6px; margin-bottom: 25px; font-family: sans-serif; background-color: #ffffff; box-shadow: 2px 2px 5px rgba(0,0,0,0.1);">
                <div style="background-color: #3b1be3; color: white; padding: 8px 15px; font-weight: bold; font-size: 1.1em; letter-spacing: 0.5px;">CAJA A - DRUGSTORE</div>
                <div style="padding: 15px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 8px; color: #333;"><span>(+) EFECTIVO:</span><span>${a_efvo:,.0f}</span></div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 15px; color: #333;"><span>(+) TRANSFERENCIAS:</span><span>${a_transf:,.0f}</span></div>
                    <hr style="border: 1px solid #eee; margin: 10px 0;">
                    <div style="display: flex; justify-content: space-between; font-weight: bold; font-size: 1.5em; color: black;"><span>TOTAL VENTAS:</span><span>${a_total:,.0f}</span></div>
                </div>
                <div style="display: flex; justify-content: space-between; background-color: #553aeb; color: white; padding: 8px 15px; font-weight: bold;"><span>GANANCIA ESTIMADA (10%):</span><span>${a_ganancia:,.0f}</span></div>
            </div>
            """, unsafe_allow_html=True)
            
            st.markdown(f"""
            <div style="border: 2px solid #418042; border-radius: 6px; margin-bottom: 25px; font-family: sans-serif; background-color: #ffffff; box-shadow: 2px 2px 5px rgba(0,0,0,0.1);">
                <div style="background-color: #418042; color: white; padding: 8px 15px; font-weight: bold; font-size: 1.1em; letter-spacing: 0.5px;">CAJA C - ADICIONALES (Ganancia)</div>
                <div style="padding: 15px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 8px; color: #333;"><span>(+) EFECTIVO:</span><span>${c_efvo:,.0f}</span></div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 15px; color: #333;"><span>(+) TRANSFERENCIA:</span><span>${c_transf:,.0f}</span></div>
                    <hr style="border: 1px solid #eee; margin: 10px 0;">
                    <div style="display: flex; justify-content: space-between; font-weight: bold; font-size: 1.5em; color: black;"><span>TOTAL GANANCIA:</span><span>${c_total:,.0f}</span></div>
                </div>
            </div>
            """, unsafe_allow_html=True)

        with col_der:
            st.markdown(f"""
            <div style="border: 2px solid #d68b31; border-radius: 6px; margin-bottom: 25px; font-family: sans-serif; background-color: #ffffff; box-shadow: 2px 2px 5px rgba(0,0,0,0.1);">
                <div style="background-color: #d68b31; color: white; padding: 8px 15px; font-weight: bold; font-size: 1.1em; letter-spacing: 0.5px;">CAJA B - SUBE (Solo Capital)</div>
                <div style="padding: 15px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 8px; color: #333;"><span>(+) INGRESOS EFECTIVO:</span><span>${b_efvo:,.0f}</span></div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 15px; color: #333;"><span>(+) INGRESOS TRANSF:</span><span>${b_transf:,.0f}</span></div>
                    <hr style="border: 1px solid #eee; margin: 10px 0;">
                    <div style="display: flex; justify-content: space-between; font-weight: bold; font-size: 1.5em; color: black;"><span>TOTAL (Sin Adic):</span><span>${b_total:,.0f}</span></div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            st.markdown(f"""
            <div style="border: 2px solid #de3c31; border-radius: 6px; margin-bottom: 25px; font-family: sans-serif; background-color: #ffffff; box-shadow: 2px 2px 5px rgba(0,0,0,0.1);">
                <div style="background-color: #de3c31; color: white; padding: 8px 15px; font-weight: bold; font-size: 1.1em; letter-spacing: 0.5px;">CAJA E - CLARO (Solo Capital)</div>
                <div style="padding: 15px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 8px; color: #333;"><span>(+) INGRESOS EFECTIVO:</span><span>${e_efvo:,.0f}</span></div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 15px; color: #333;"><span>(+) INGRESOS TRANSF:</span><span>${e_transf:,.0f}</span></div>
                    <hr style="border: 1px solid #eee; margin: 10px 0;">
                    <div style="display: flex; justify-content: space-between; font-weight: bold; font-size: 1.5em; color: black;"><span>TOTAL (Sin Adic):</span><span>${e_total:,.0f}</span></div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
    except Exception as e:
        st.error(f"Error cargando el dashboard. Revise la estructura de datos: {e}")

# ==========================================
# 6. ENRUTADOR PRINCIPAL (MENÚ LATERAL)
# ==========================================
st.sidebar.image("https://cdn-icons-png.flaticon.com/512/3514/3514491.png", width=100) 
st.sidebar.title("Sistema Genaro")

menu = st.sidebar.radio("Navegación", [
    "🛒 Caja", 
    "📱 Servicios", 
    "⚙️ Admin Productos",
    "📜 Historial de Ítems",
    "📊 Visor (Dashboard)"
])

# Despliegue de vistas según selección
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
