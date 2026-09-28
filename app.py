import datetime
import html
import math
import unicodedata

import pandas as pd
import streamlit as st
from st_keyup import st_keyup
from streamlit_gsheets import GSheetsConnection

# ==========================================
# 0. CONFIGURACIÓN REGIONAL
# ==========================================
ZONA_AR = datetime.timezone(datetime.timedelta(hours=-3))
URL_PLANILLA = "https://docs.google.com/spreadsheets/d/1AEsHRAwONhfcATrG7k0gsVmWB1IGlqoHt89_wcT9Uuo/edit?gid=514091242#gid=514091242"

RECARGO_BASE = 2000
RECARGO_POR_TRAMO = 100

# ==========================================
# 1. CONFIGURACIÓN INICIAL Y UI/UX
# ==========================================
st.set_page_config(
    page_title="Genaro POS",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="auto",
)


def aplicar_estilos_profesionales():
    """Tema claro exclusivo del POS. Solo modifica presentación, no lógica."""
    st.markdown(
        """
        <style>
        /* ==========================================================
           GENARO POS — LIGHT UI SYSTEM
           Alta legibilidad · contraste · escritorio + celular
           ========================================================== */

        :root {
            color-scheme: light !important;
            --g-bg: #F3F6FA;
            --g-surface: #FFFFFF;
            --g-surface-soft: #F8FAFD;
            --g-text: #102033;
            --g-text-strong: #07111F;
            --g-muted: #5B6B7F;
            --g-border: #D9E1EA;
            --g-border-strong: #C3CEDA;
            --g-primary: #2563EB;
            --g-primary-dark: #1D4ED8;
            --g-success: #16A34A;
            --g-warning: #F59E0B;
            --g-danger: #DC2626;
            --g-purple: #7C3AED;
            --g-radius: 16px;
            --g-shadow: 0 8px 24px rgba(15, 23, 42, 0.07);
        }

        /* ---------- SOLO TEMA CLARO ---------- */
        html,
        body,
        [data-testid="stAppViewContainer"],
        [data-testid="stHeader"] {
            color-scheme: light !important;
        }

        /* Oculta el menú nativo que permite cambiar tema/configuración.
           El sistema queda visualmente bloqueado en tema claro. */
        [data-testid="stToolbar"] button[aria-label="Main menu"],
        button[aria-label="Main menu"] {
            display: none !important;
        }

        /* ---------- Lienzo principal ---------- */
        [data-testid="stAppViewContainer"] {
            background: var(--g-bg) !important;
        }

        [data-testid="stMainBlockContainer"],
        .main .block-container {
            width: 100% !important;
            max-width: 100% !important;
            padding-top: 0.80rem !important;
            padding-bottom: 1.75rem !important;
            padding-left: clamp(0.55rem, 1.40vw, 1.45rem) !important;
            padding-right: clamp(0.55rem, 1.40vw, 1.45rem) !important;
        }

        html, body, [class*="css"] {
            font-family: "Inter", "Segoe UI", Arial, sans-serif;
            color: var(--g-text) !important;
        }

        /* ---------- Encabezados ---------- */
        h1 {
            font-size: clamp(1.75rem, 2.6vw, 2.45rem) !important;
            line-height: 1.0 !important;
            font-weight: 850 !important;
            letter-spacing: -0.045em !important;
            color: var(--g-text-strong) !important;
            margin: 0 0 0.18rem 0 !important;
        }

        h2, h3 {
            color: var(--g-text-strong) !important;
            letter-spacing: -0.025em !important;
        }

        [data-testid="stCaptionContainer"] {
            color: var(--g-muted) !important;
            font-size: 0.88rem !important;
            font-weight: 500 !important;
        }

        /* ---------- Sidebar ---------- */
        section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #0A1730 0%, #102A56 100%) !important;
            border-right: 1px solid rgba(255,255,255,0.09) !important;
        }

        section[data-testid="stSidebar"] > div {
            padding-top: 0.75rem !important;
        }

        section[data-testid="stSidebar"] img {
            display: block;
            margin: 0 auto 0.55rem auto;
            max-width: 78px;
            border-radius: 18px;
            box-shadow: 0 8px 20px rgba(0,0,0,0.18);
        }

        section[data-testid="stSidebar"] h1,
        section[data-testid="stSidebar"] h2,
        section[data-testid="stSidebar"] h3,
        section[data-testid="stSidebar"] p,
        section[data-testid="stSidebar"] label,
        section[data-testid="stSidebar"] small {
            color: #F8FAFC !important;
        }

        section[data-testid="stSidebar"] .stRadio > label {
            font-size: 0.78rem !important;
            font-weight: 850 !important;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            opacity: 0.72;
        }

        section[data-testid="stSidebar"] [role="radiogroup"] {
            gap: 0.20rem;
        }

        section[data-testid="stSidebar"] [role="radiogroup"] label {
            border-radius: 12px;
            padding: 0.42rem 0.52rem;
            transition: background 0.15s ease, transform 0.15s ease;
        }

        section[data-testid="stSidebar"] [role="radiogroup"] label:hover {
            background: rgba(255,255,255,0.095);
            transform: translateX(1px);
        }

        /* ---------- Contenedores ---------- */
        div[data-testid="stVerticalBlockBorderWrapper"] {
            background: var(--g-surface) !important;
            border: 1px solid var(--g-border) !important;
            border-radius: var(--g-radius) !important;
            box-shadow: var(--g-shadow);
        }

        /* ---------- Botones ---------- */
        div.stButton > button {
            min-height: 43px;
            border-radius: 11px !important;
            font-weight: 750 !important;
            font-size: 0.91rem !important;
            border: 1px solid var(--g-border-strong) !important;
            color: #17304C !important;
            background: #FFFFFF !important;
            transition: transform 0.12s ease, box-shadow 0.12s ease, filter 0.12s ease;
        }

        div.stButton > button:hover {
            transform: translateY(-1px);
            box-shadow: 0 8px 18px rgba(15,23,42,0.10);
        }

        div.stButton > button[kind="primary"] {
            background: linear-gradient(135deg, var(--g-primary), var(--g-primary-dark)) !important;
            border-color: transparent !important;
            color: #FFFFFF !important;
            box-shadow: 0 5px 14px rgba(37,99,235,0.24);
        }

        div.stButton > button[kind="primary"]:hover {
            filter: brightness(1.035);
        }

        /* ---------- Inputs ---------- */
        div[data-baseweb="input"] > div,
        div[data-baseweb="select"] > div,
        div[data-baseweb="textarea"] > div {
            min-height: 43px;
            border-radius: 10px !important;
            border: 1px solid var(--g-border-strong) !important;
            background: #FFFFFF !important;
            box-shadow: none !important;
        }

        div[data-baseweb="input"]:focus-within > div,
        div[data-baseweb="select"]:focus-within > div,
        div[data-baseweb="textarea"]:focus-within > div {
            border-color: var(--g-primary) !important;
            box-shadow: 0 0 0 3px rgba(37,99,235,0.12) !important;
        }

        div[data-baseweb="input"] input,
        div[data-baseweb="textarea"] textarea,
        div[data-baseweb="select"] * {
            color: var(--g-text) !important;
        }

        input::placeholder,
        textarea::placeholder {
            color: #8290A1 !important;
            opacity: 1 !important;
        }

        /* ---------- Radio / Checkbox ---------- */
        div[data-testid="stRadio"] label,
        div[data-testid="stCheckbox"] label {
            color: var(--g-text) !important;
            font-weight: 600 !important;
        }

        /* ---------- Expander ---------- */
        div[data-testid="stExpander"] {
            border: 1px solid var(--g-border) !important;
            border-radius: 13px !important;
            background: #FFFFFF !important;
        }

        /* ---------- Alertas ---------- */
        div[data-testid="stAlert"] {
            border-radius: 11px !important;
            border-width: 1px !important;
        }

        /* ---------- Métricas ---------- */
        div[data-testid="stMetric"] {
            background: #FFFFFF !important;
            border: 1px solid var(--g-border) !important;
            border-radius: 14px !important;
            padding: 0.65rem 0.78rem !important;
            box-shadow: 0 5px 18px rgba(15,23,42,0.045);
        }

        div[data-testid="stMetricLabel"] {
            color: var(--g-muted) !important;
            font-weight: 750 !important;
        }

        div[data-testid="stMetricValue"] {
            color: var(--g-text-strong) !important;
            font-weight: 880 !important;
            letter-spacing: -0.04em;
        }

        /* ---------- Data editor ---------- */
        div[data-testid="stDataEditor"] {
            border: 1px solid var(--g-border) !important;
            border-radius: 12px !important;
            overflow: hidden !important;
            box-shadow: 0 4px 14px rgba(15,23,42,0.045);
            background: #FFFFFF !important;
        }

        /* ---------- Divisores ---------- */
        hr {
            margin-top: 0.55rem !important;
            margin-bottom: 0.65rem !important;
            border-color: var(--g-border) !important;
        }

        /* ======================================================
           RESUMEN DE HOY — VISTA CARD
           ====================================================== */
        .resumen-hoy {
            background: linear-gradient(135deg, #123A7A 0%, #2563EB 100%);
            border-radius: 18px;
            padding: 13px;
            margin: 0 0 17px 0;
            box-shadow: 0 12px 30px rgba(37,99,235,0.17);
            color: #FFFFFF;
        }

        .resumen-hoy-top {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 10px;
            margin-bottom: 11px;
        }

        .resumen-hoy-title {
            font-size: 19px;
            font-weight: 900;
            letter-spacing: -0.02em;
        }

        .resumen-hoy-date {
            font-size: 11px;
            opacity: 0.80;
            margin-top: 3px;
        }

        .resumen-hoy-badge {
            font-size: 10px;
            font-weight: 850;
            border: 1px solid rgba(255,255,255,0.20);
            background: rgba(255,255,255,0.10);
            border-radius: 999px;
            padding: 5px 8px;
            white-space: nowrap;
        }

        .resumen-grid {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 8px;
        }

        .resumen-card {
            min-width: 0;
            border-radius: 13px;
            background: #FFFFFF;
            color: #102033;
            border: 1px solid rgba(255,255,255,0.60);
            border-top: 4px solid rgba(255,255,255,0.95);
            padding: 8px 10px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.09);
        }

        .resumen-card:nth-child(2) { border-top-color: #22C55E; }
        .resumen-card:nth-child(3) { border-top-color: #7C3AED; }
        .resumen-card:nth-child(4) { border-top-color: #F59E0B; }
        .resumen-card:nth-child(5) { border-top-color: #0EA5E9; }

        .resumen-label {
            color: #52657B;
            font-size: 10px;
            font-weight: 850;
            letter-spacing: 0.045em;
            margin-bottom: 3px;
        }

        .resumen-value {
            font-size: clamp(19px, 1.55vw, 26px);
            line-height: 1;
            font-weight: 900;
            letter-spacing: -0.04em;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            font-variant-numeric: tabular-nums;
        }

        .resumen-sub {
            color: #75859A;
            font-size: 9px;
            margin-top: 3px;
        }

        /* ======================================================
           BUSCADOR
           ====================================================== */
        .busqueda-hint {
            display: inline-block;
            color: #5B6B7F;
            background: #EEF4FF;
            border: 1px solid #CFE0FF;
            border-radius: 9px;
            padding: 5px 8px;
            font-size: 11px;
            font-weight: 650;
            margin: -4px 0 9px 0;
        }

        .producto-resultado {
            background: #F8FAFD;
            border: 1px solid #D9E2EC;
            border-left: 4px solid #2563EB;
            border-radius: 12px;
            padding: 8px 10px;
            min-height: 51px;
            box-sizing: border-box;
            box-shadow: 0 2px 7px rgba(15,23,42,0.025);
        }

        .producto-resultado:hover {
            background: #F1F6FF;
        }

        .producto-nombre {
            font-size: 14px;
            line-height: 1.10;
            font-weight: 850;
            color: #0B1A2B;
        }

        .producto-meta {
            font-size: 10.5px;
            line-height: 1.20;
            color: #64748B;
            margin-top: 3px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .producto-precio {
            display: inline-block;
            background: #ECFDF3;
            border: 1px solid #A7F3C2;
            border-radius: 10px;
            padding: 7px 9px;
            font-size: 16px;
            font-weight: 900;
            line-height: 1;
            color: #116329;
            text-align: right;
            white-space: nowrap;
            font-variant-numeric: tabular-nums;
        }

        /* ======================================================
           VISOR / DASHBOARD
           ====================================================== */
        .visor-title {
            font-size: clamp(1.8rem, 2.7vw, 2.45rem);
            font-weight: 900;
            letter-spacing: -0.045em;
            margin: 0 0 0.65rem 0;
            color: #0B1726;
        }

        .visor-grid-wrapper {
            width: 100%;
            overflow-x: auto;
            padding: 2px;
        }

        .visor-grid {
            display: grid;
            grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
            column-gap: 18px;
            row-gap: 18px;
            width: 100%;
            margin-top: 7px;
        }

        .visor-card {
            border: 1px solid #D6DFE9;
            background: #FFFFFF;
            overflow: hidden;
            box-sizing: border-box;
            width: 100%;
            border-radius: 16px;
            box-shadow: 0 8px 20px rgba(15,23,42,0.06);
        }

        .visor-header {
            min-height: 49px;
            display: flex;
            align-items: center;
            padding: 7px 13px;
            box-sizing: border-box;
            font-size: clamp(18px, 1.34vw, 26px);
            font-weight: 900;
            line-height: 1.05;
            letter-spacing: -0.025em;
        }

        .visor-body {
            padding: 8px 13px 0 13px;
        }

        .visor-line {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            align-items: center;
            min-height: 39px;
            font-size: clamp(16px, 1.10vw, 22px);
            line-height: 1.05;
            color: #142337;
            column-gap: 10px;
        }

        .visor-label {
            white-space: nowrap;
            font-weight: 650;
        }

        .visor-value {
            text-align: right;
            white-space: nowrap;
            font-variant-numeric: tabular-nums;
            font-weight: 750;
        }

        .visor-separator {
            height: 2px;
            background: #DDE5EE;
            margin-top: 4px;
        }

        .visor-total {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            align-items: center;
            min-height: 68px;
            font-size: clamp(17px, 1.18vw, 24px);
            color: #102033;
            column-gap: 10px;
        }

        .visor-total-label {
            font-weight: 800;
        }

        .visor-total-value {
            font-size: clamp(28px, 1.95vw, 38px);
            font-weight: 950;
            text-align: right;
            white-space: nowrap;
            font-variant-numeric: tabular-nums;
            letter-spacing: -0.05em;
        }

        .visor-profit {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            align-items: stretch;
            min-height: 47px;
            border-top: 2px solid #DDE5EE;
            font-size: clamp(15px, 0.96vw, 19px);
            font-weight: 850;
            color: #5B6B7F;
        }

        .visor-profit > div:first-child {
            display: flex;
            align-items: center;
            padding-left: 2px;
        }

        .visor-profit-value {
            align-self: stretch;
            display: flex;
            align-items: center;
            justify-content: flex-end;
            padding: 0 12px;
            min-width: 165px;
            box-sizing: border-box;
            color: #FFFFFF;
            font-size: clamp(22px, 1.38vw, 28px);
            font-weight: 950;
            white-space: nowrap;
            font-variant-numeric: tabular-nums;
        }

        .visor-secondary {
            display: flex;
            justify-content: flex-end;
            align-items: center;
            min-height: 30px;
            font-size: clamp(15px, 1.02vw, 20px);
            color: #708096;
            white-space: nowrap;
            font-variant-numeric: tabular-nums;
            margin-top: -3px;
        }

        /* ---------- Responsive ---------- */
        @media (max-width: 1100px) {
            .resumen-grid {
                grid-template-columns: repeat(3, minmax(0, 1fr));
            }
            .visor-grid {
                grid-template-columns: 1fr;
                row-gap: 16px;
            }
        }

        @media (max-width: 700px) {
            [data-testid="stMainBlockContainer"],
            .main .block-container {
                padding: 0.55rem 0.45rem 1.5rem 0.45rem !important;
            }

            h1 {
                font-size: 1.65rem !important;
            }

            [data-testid="stCaptionContainer"] {
                font-size: 0.78rem !important;
            }

            .resumen-hoy {
                border-radius: 15px;
                padding: 10px;
                margin-bottom: 12px;
            }

            .resumen-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 6px;
            }

            .resumen-card:first-child {
                grid-column: span 2;
            }

            .resumen-card {
                padding: 8px;
                border-radius: 11px;
            }

            .resumen-value {
                font-size: 19px;
            }

            .resumen-hoy-badge {
                display: none;
            }

            .busqueda-hint {
                display: block;
                line-height: 1.25;
            }

            .producto-nombre {
                font-size: 13px;
            }

            .producto-meta {
                font-size: 9.5px;
            }

            .producto-precio {
                font-size: 15px;
                padding: 6px 8px;
            }

            .visor-header {
                min-height: 47px;
                padding-left: 11px;
                padding-right: 11px;
            }

            .visor-body {
                padding-left: 11px;
                padding-right: 11px;
            }

            .visor-line {
                font-size: 15px;
                min-height: 37px;
            }

            .visor-total {
                font-size: 17px;
                min-height: 62px;
            }

            .visor-total-value {
                font-size: 27px;
            }

            .visor-profit {
                font-size: 14px;
                min-height: 45px;
            }

            .visor-profit-value {
                min-width: 130px;
                font-size: 21px;
            }

            div.stButton > button {
                min-height: 45px;
                font-size: 0.86rem !important;
            }
        }

        @media (max-width: 430px) {
            .resumen-grid {
                gap: 5px;
            }

            .resumen-value {
                font-size: 17px;
            }

            .visor-line {
                font-size: 14px;
            }

            .visor-total {
                font-size: 16px;
            }

            .visor-total-value {
                font-size: 25px;
            }

            .visor-profit {
                font-size: 13px;
            }

            .visor-profit-value {
                min-width: 118px;
                font-size: 20px;
            }
        }

        footer {
            visibility: hidden;
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
    """Convierte fechas de texto, datetime o seriales de Sheets/Excel."""
    if columna not in df.columns:
        df["FECHA_REAL"] = pd.NaT
        return df

    valores = df[columna]
    numerico = pd.to_numeric(valores, errors="coerce")
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


def sumar_numerico(df, columna):
    if columna not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[columna], errors="coerce").fillna(0).sum())


def dinero(valor):
    return f"${entero_seguro(valor):,}"


def normalizar_busqueda(valor):
    """Normaliza texto para búsqueda literal, rápida y tolerante."""
    if valor is None:
        return ""

    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass

    texto = unicodedata.normalize("NFKD", str(valor))
    texto = "".join(
        caracter
        for caracter in texto
        if not unicodedata.combining(caracter)
    ).lower()

    # "Coca-Cola", "coca cola" y "coca/cola" quedan equivalentes.
    texto = "".join(
        caracter if caracter.isalnum() else " "
        for caracter in texto
    )
    return " ".join(texto.split())


def construir_indice_busqueda(df):
    """Prepara el texto auxiliar una sola vez cuando se carga el catálogo."""
    campos = [
        campo
        for campo in [
            "NOMBRE",
            "PROVEEDOR",
            "CATEGORIA",
            "UNIDAD",
            "ID_PRODUCTO",
        ]
        if campo in df.columns
    ]

    if not campos:
        vacio = pd.Series("", index=df.index, dtype="string")
        return vacio, vacio

    partes = []
    nombre = pd.Series("", index=df.index, dtype="string")

    for campo in campos:
        serie = (
            df[campo]
            .fillna("")
            .astype(str)
            .map(normalizar_busqueda)
            .astype("string")
        )
        partes.append(serie)
        if campo == "NOMBRE":
            nombre = serie

    texto = pd.concat(partes, axis=1).fillna("").agg(" ".join, axis=1)
    return texto.astype("string"), nombre


def buscar_productos_inteligente(df, consulta, limite=15):
    """
    Buscador para caja optimizado para el caso real del negocio.

    1. Busca directamente en NOMBRE, que es el 99% de las consultas.
    2. Si no encuentra, busca en proveedor/categoría/unidad/ID.
    3. Ignora mayúsculas, acentos y signos.
    4. "cocacola" también encuentra "Coca Cola".

    No usa regex ni fuzzy matching: para un catálogo de ~800 productos es
    más rápido, más predecible y más fácil de mantener.
    """
    if df.empty or "NOMBRE" not in df.columns:
        return df.iloc[0:0].copy()

    consulta_limpia = normalizar_busqueda(consulta)
    if not consulta_limpia:
        return df.iloc[0:0].copy()

    tokens = consulta_limpia.split()

    # ----------------------------------------------------------
    # 1) NOMBRE: lectura directa del campo real del catálogo.
    # No dependemos del índice auxiliar para que "coca" siempre
    # pueda encontrarse mientras exista en NOMBRE.
    # ----------------------------------------------------------
    if "__BUSQ_NOMBRE" in df.columns:
        nombre = df["__BUSQ_NOMBRE"].astype("string").fillna("")
    else:
        nombre = (
            df["NOMBRE"]
            .fillna("")
            .astype(str)
            .map(normalizar_busqueda)
            .astype("string")
        )

    mask_nombre = pd.Series(True, index=df.index, dtype=bool)
    for token in tokens:
        mask_nombre &= nombre.str.contains(
            token,
            regex=False,
            na=False,
        )

    # Variante sin espacios: "cocacola" -> "coca cola".
    consulta_compacta = consulta_limpia.replace(" ", "")
    nombre_compacto = nombre.str.replace(" ", "", regex=False)
    mask_nombre |= nombre_compacto.str.contains(
        consulta_compacta,
        regex=False,
        na=False,
    )

    if mask_nombre.any():
        resultados = df.loc[mask_nombre].copy()
        nombre_resultados = nombre.loc[mask_nombre]

        resultados["__SCORE"] = (
            nombre_resultados.eq(consulta_limpia).astype("int16") * 1000
            + nombre_resultados.str.startswith(
                consulta_limpia,
                na=False,
            ).astype("int16") * 100
            + nombre_resultados.str.contains(
                consulta_limpia,
                regex=False,
                na=False,
            ).astype("int16") * 10
        )
        resultados["__NOMBRE_ORDEN"] = nombre_resultados

        return (
            resultados.sort_values(
                by=["__SCORE", "__NOMBRE_ORDEN"],
                ascending=[False, True],
                kind="stable",
            )
            .head(limite)
            .drop(
                columns=["__SCORE", "__NOMBRE_ORDEN"],
                errors="ignore",
            )
        )

    # ----------------------------------------------------------
    # 2) METADATOS: proveedor, categoría, unidad e ID.
    # ----------------------------------------------------------
    if "__BUSQ_TEXTO" in df.columns:
        texto = df["__BUSQ_TEXTO"].astype("string").fillna("")
    else:
        texto, _ = construir_indice_busqueda(df)

    mask = pd.Series(True, index=df.index, dtype=bool)
    for token in tokens:
        mask &= texto.str.contains(
            token,
            regex=False,
            na=False,
        )

    if not mask.any():
        texto_compacto = texto.str.replace(" ", "", regex=False)
        mask = texto_compacto.str.contains(
            consulta_compacta,
            regex=False,
            na=False,
        )

    if not mask.any():
        return df.iloc[0:0].copy()

    resultados = df.loc[mask].copy()
    nombre_resultados = nombre.loc[mask]
    texto_resultados = texto.loc[mask]

    resultados["__SCORE"] = (
        nombre_resultados.str.contains(
            consulta_limpia,
            regex=False,
            na=False,
        ).astype("int16") * 100
        + texto_resultados.str.startswith(
            consulta_limpia,
            na=False,
        ).astype("int16") * 10
    )
    resultados["__NOMBRE_ORDEN"] = nombre_resultados

    return (
        resultados.sort_values(
            by=["__SCORE", "__NOMBRE_ORDEN"],
            ascending=[False, True],
            kind="stable",
        )
        .head(limite)
        .drop(
            columns=["__SCORE", "__NOMBRE_ORDEN"],
            errors="ignore",
        )
    )


# ==========================================
# 3. GESTIÓN DEL ESTADO
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
# 4. DATOS Y LÓGICA DE VENTA
# ==========================================
@st.cache_data(ttl=600)
def cargar_productos():
    """Carga y deja listo el catálogo para Caja y Preventistas."""
    try:
        conn = obtener_conexion()
        df = conn.read(
            spreadsheet=URL_PLANILLA,
            worksheet="DB_PRODUCTOS",
        )

        if df is None or df.empty:
            return pd.DataFrame()

        df = df.copy()

        # Encabezados tolerantes a espacios/casing accidental.
        vistos = {}
        nuevos = []
        for col in df.columns:
            base = str(col).strip().upper()
            n = vistos.get(base, 0)
            nuevos.append(base if n == 0 else f"{base}_{n}")
            vistos[base] = n + 1
        df.columns = nuevos

        if "NOMBRE" not in df.columns:
            return pd.DataFrame()

        df = df.dropna(subset=["NOMBRE"]).copy()
        df["NOMBRE"] = df["NOMBRE"].astype(str).str.strip()
        df = df[df["NOMBRE"] != ""].copy()

        texto, nombre = construir_indice_busqueda(df)
        df["__BUSQ_TEXTO"] = texto
        df["__BUSQ_NOMBRE"] = nombre

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

    nueva_venta = pd.DataFrame([
        {
            "TICKET_ID": ticket_id,
            "FECHA": fecha_actual.strftime("%d/%m/%Y %H:%M:%S"),
            "TOTAL_VENTA": entero_seguro(total_venta),
            "MONTO_EFECTIVO": entero_seguro(pago_efvo),
            "MONTO_TRANSF": entero_seguro(pago_transf),
            "ES_NOCTURNO": False,
        }
    ])

    df_items_nuevos = pd.DataFrame([
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
    ])

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
                data=pd.concat([df_mov, nueva_venta], ignore_index=True),
            )

            df_historial = conn.read(
                spreadsheet=URL_PLANILLA,
                worksheet="DB_HISTORIAL_ITEMS",
                ttl=0,
            )
            conn.update(
                spreadsheet=URL_PLANILLA,
                worksheet="DB_HISTORIAL_ITEMS",
                data=pd.concat([df_historial, df_items_nuevos], ignore_index=True),
            )

        st.session_state.carrito = []
        cargar_movimientos_resumen.clear()
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

    st.info(f"💵 Restante a cobrar en Efectivo: **${monto_efvo:,.0f}**")
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
# 6. CAJA
# ==========================================
@st.cache_data(ttl=15)
def cargar_movimientos_resumen():
    """Lee movimientos para el resumen de Caja durante 15 segundos.

    El resumen se muestra arriba del buscador, por lo que sin este caché
    cada tecla en st_keyup provocaría una nueva lectura de Google Sheets.
    Después de registrar una venta el caché se limpia explícitamente para
    que el resumen se actualice inmediatamente.
    """
    conn = obtener_conexion()
    df = conn.read(
        spreadsheet=URL_PLANILLA,
        worksheet="DB_MOVIMIENTOS_CAJA",
        ttl=0,
    )
    return normalizar_fecha_columna(df)


def mostrar_caja():
    st.title("🛒 Caja Registradora")
    st.caption("Punto de venta · búsqueda rápida · cobro en efectivo, transferencia o mixto")

    # ------------------------------------------
    # RESUMEN DE HOY
    # ------------------------------------------
    try:
        ahora = ahora_ar()
        df_hoy = cargar_movimientos_resumen()
        fecha_hoy = ahora.date()
        df_hoy = df_hoy[
            df_hoy["FECHA_REAL"].dt.date == fecha_hoy
        ].copy()

        hoy_ventas = sumar_numerico(df_hoy, "TOTAL_VENTA")
        hoy_efvo = sumar_numerico(df_hoy, "MONTO_EFECTIVO")
        hoy_transf = sumar_numerico(df_hoy, "MONTO_TRANSF")
        hoy_tickets = len(df_hoy)
        hoy_ganancia = hoy_ventas * 0.10

        resumen_html = f"""
        <div class="resumen-hoy">
            <div class="resumen-hoy-top">
                <div>
                    <div class="resumen-hoy-title">📈 Resumen de hoy</div>
                    <div class="resumen-hoy-date">{ahora.strftime('%d/%m/%Y')} · actividad registrada</div>
                </div>
                <div class="resumen-hoy-badge">EN TIEMPO REAL</div>
            </div>
            <div class="resumen-grid">
                <div class="resumen-card">
                    <div class="resumen-label">VENTAS</div>
                    <div class="resumen-value">{dinero(hoy_ventas)}</div>
                    <div class="resumen-sub">importe total</div>
                </div>
                <div class="resumen-card">
                    <div class="resumen-label">EFECTIVO</div>
                    <div class="resumen-value">{dinero(hoy_efvo)}</div>
                    <div class="resumen-sub">cobros</div>
                </div>
                <div class="resumen-card">
                    <div class="resumen-label">TRANSFERENCIAS</div>
                    <div class="resumen-value">{dinero(hoy_transf)}</div>
                    <div class="resumen-sub">cobros</div>
                </div>
                <div class="resumen-card">
                    <div class="resumen-label">TICKETS</div>
                    <div class="resumen-value">{hoy_tickets}</div>
                    <div class="resumen-sub">ventas registradas</div>
                </div>
                <div class="resumen-card">
                    <div class="resumen-label">GANANCIA EST.</div>
                    <div class="resumen-value">{dinero(hoy_ganancia)}</div>
                    <div class="resumen-sub">10% estimado</div>
                </div>
            </div>
        </div>
        """
        st.markdown(resumen_html, unsafe_allow_html=True)
    except Exception:
        st.markdown(
            """
            <div class="resumen-hoy">
                <div class="resumen-hoy-title">📈 Resumen de hoy</div>
                <div class="resumen-hoy-date">No se pudo cargar el resumen en este momento.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    df_productos = cargar_productos()

    # Si el primer intento dejó el catálogo vacío por un fallo temporal,
    # se reintenta una sola vez antes de mostrar el estado de la Caja.
    if df_productos.empty:
        try:
            cargar_productos.clear()
            df_productos = cargar_productos()
        except Exception:
            df_productos = pd.DataFrame()

    col_izq, col_der = st.columns([5, 5], gap="large")

    with col_izq:
        with st.container(border=True):
            st.subheader("🔍 Buscador de Productos")
            st.markdown(
                '<div class="busqueda-hint">Busca por nombre, marca, proveedor, categoría, unidad o código.</div>',
                unsafe_allow_html=True,
            )

            busqueda = st_keyup(
                "Buscar producto… (ej.: coca, lays, secco)",
                debounce=220,
                key=f"buscador_{st.session_state.search_key}",
            )

            if busqueda and not df_productos.empty:
                resultados = buscar_productos_inteligente(
                    df_productos,
                    busqueda,
                    limite=15,
                )

                if resultados.empty:
                    st.warning(
                        f'No encontré "{busqueda.strip()}" en el nombre, proveedor, categoría o código.'
                    )
                else:
                    for index, row in resultados.iterrows():
                        c1, c2 = st.columns([8, 2], vertical_alignment="center")

                        proveedor = str(row.get("PROVEEDOR", "")).strip()
                        categoria = str(row.get("CATEGORIA", "")).strip()
                        unidad = str(row.get("UNIDAD", "")).strip()
                        meta = " · ".join(
                            [x for x in [proveedor, categoria, unidad] if x and x.lower() != "nan"]
                        )

                        with c1:
                            st.markdown(
                                f"""
                                <div class="producto-resultado">
                                    <div class="producto-nombre">{html.escape(str(row['NOMBRE']))}</div>
                                    <div class="producto-meta">{html.escape(meta if meta else 'Producto de catálogo')}</div>
                                </div>
                                """,
                                unsafe_allow_html=True,
                            )

                        with c2:
                            st.markdown(
                                f'<div class="producto-precio">{dinero(row.get("PRECIO_DIA", 0))}</div>',
                                unsafe_allow_html=True,
                            )
                            if st.button(
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
                st.info("El carrito está vacío. Agrega productos desde el buscador.")
            else:
                total = 0

                h1, h2, h3, h4 = st.columns([4, 3, 3, 1])
                h1.write("**Producto**")
                h2.write("**Cant**")
                h3.write("**Monto $**")

                for i, item in enumerate(st.session_state.carrito):
                    c1, c2, c3, c4 = st.columns([4, 3, 3, 1], vertical_alignment="center")
                    c1.write(item["nombre"])

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
                        st.toast("✅ Venta en Efectivo registrada.", icon="✅")
                        st.rerun()

                if col_transf.button(
                    "📱 Transf.",
                    use_container_width=True,
                ):
                    if procesar_venta("TRANSFERENCIA"):
                        st.toast("✅ Venta por Transferencia registrada.", icon="✅")
                        st.rerun()

                if col_mixto.button(
                    "💳 Mixto",
                    use_container_width=True,
                ):
                    modal_pago_mixto(total)


# ==========================================
# 7. SERVICIOS
# ==========================================
def mostrar_servicios():
    st.title("📱 Cargas y Servicios")
    st.caption("Recargas virtuales y servicios con cálculo automático del adicional")

    with st.container(border=True):
        st.write("Registra recargas virtuales o pagos de servicios de forma ágil.")
        st.write("---")

        col1, col2 = st.columns(2, gap="large")

        with col1:
            servicio = st.selectbox(
                "Empresa / Servicio",
                ["Claro", "Personal", "Movistar", "Tuenti", "DIRECTV", "SUBE", "Otro"],
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
        st.info(f"### **💰 Total a cobrar al cliente: ${total_cobrar:,.0f}**")

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
            st.write(f"💵 Restante en Efectivo: **${monto_efvo:,.0f}**")
        elif metodo_pago == "EFECTIVO":
            monto_efvo = total_cobrar
        else:
            monto_transf = total_cobrar

        st.divider()

        if st.button("🚀 Registrar Carga", type="primary", use_container_width=True):
            if monto_carga <= 0:
                st.error("⚠️ El monto de la carga debe ser mayor a cero.")
            else:
                nueva_carga = pd.DataFrame([
                    {
                        "FECHA": fecha_texto(),
                        "SERVICIO": servicio,
                        "MONTO_CARGA": monto_carga,
                        "MONTO_ADICIONAL": monto_adic,
                        "TOTAL_COBRADO": total_cobrar,
                        "PAGO_EFVO": monto_efvo,
                        "PAGO_TRANSF": monto_transf,
                    }
                ])

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
                            data=pd.concat([df_cargas, nueva_carga], ignore_index=True),
                        )

                    st.toast("✅ Carga guardada.", icon="📲")
                    st.session_state.pop("input_monto_carga", None)
                    st.session_state.pop("input_monto_adic", None)
                    st.rerun()
                except Exception:
                    st.error("❌ Error al guardar. Intente nuevamente.")


# ==========================================
# 8. HISTORIAL DE CARGAS
# ==========================================
def mostrar_historial_cargas():
    st.title("📋 Historial de Cargas")
    st.caption("Auditoría y corrección de cargas registradas")

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

        normalizar_fecha_columna(df_full)

        with st.container(border=True):
            fecha_elegida = st.date_input("🗓️ Filtrar por Día:", ahora_ar().date())
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

            edited_cargas = st.data_editor(
                df_filtrado[columnas_editor],
                use_container_width=True,
                num_rows="dynamic",
                key=f"ed_cargas_{st.session_state.cargas_key}",
            )

            if st.button("💾 Guardar Cambios en Cargas", type="primary", use_container_width=True):
                with st.spinner("Sincronizando correcciones..."):
                    indices_originales = df_filtrado.index.tolist()
                    indices_editados = edited_cargas.index.tolist()
                    df_final = df_full.copy()

                    indices_eliminados = [
                        idx for idx in indices_originales if idx not in indices_editados
                    ]
                    df_final = df_final.drop(indices_eliminados)

                    for idx, row in edited_cargas.iterrows():
                        if idx in df_final.index:
                            df_final.loc[idx, columnas_editor] = row.values
                        else:
                            df_final = pd.concat(
                                [df_final, pd.DataFrame([row])],
                                ignore_index=True,
                            )

                    df_final = df_final.drop(columns=["FECHA_REAL"], errors="ignore")

                    conn.update(
                        spreadsheet=URL_PLANILLA,
                        worksheet="DB_CARGAS",
                        data=df_final,
                    )

                    st.session_state.cargas_msg = (
                        "✅ ¡El historial de cargas fue corregido y actualizado exitosamente!"
                    )
                    st.session_state.cargas_key += 1
                    st.rerun()

    except Exception as e:
        st.error(f"Error al cargar el historial de cargas. {e}")


# ==========================================
# 9. ADMIN PRODUCTOS
# ==========================================
def siguiente_id_producto(df):
    if df.empty or "ID_PRODUCTO" not in df.columns:
        return 1
    serie = pd.to_numeric(df["ID_PRODUCTO"], errors="coerce").dropna()
    return int(serie.max()) + 1 if not serie.empty else 1


def mostrar_admin_productos():
    st.title("⚙️ Gestión de Catálogo")
    st.caption("Actualización rápida, altas y bajas del catálogo")

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
                        df_actual["NOMBRE"].astype(str) == producto_seleccionado
                    ]

                    if not coincidencias.empty:
                        datos_prod = coincidencias.iloc[0]
                        idx_prod = coincidencias.index.tolist()[0]

                        st.write("---")
                        c_actual, c_nuevo = st.columns(2)

                        with c_actual:
                            st.write("**📝 Datos Actuales:**")
                            st.write(f"**Proveedor:** {datos_prod.get('PROVEEDOR', '-')}")
                            st.write(f"**Costo:** ${entero_seguro(datos_prod.get('COSTO', 0))}")
                            st.write(f"**Precio:** ${entero_seguro(datos_prod.get('PRECIO_DIA', 0))}")
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
                                f"**Margen Proyectado: {nuevo_margen_calc * 100:.2f}%**"
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
                                    cargar_productos.clear()

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
                                        cargar_productos.clear()
                                    st.session_state.admin_msg = "🗑️ Producto eliminado."
                                    st.session_state.admin_key += 1
                                    st.rerun()
                                else:
                                    st.warning("Debes marcar la casilla.")

        with col_der:
            with st.container(border=True):
                st.markdown("### ➕ ALTA DE PRODUCTO")
                n_nombre = st.text_input("NOMBRE:", key=f"n_nom_{st.session_state.admin_key}")
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
                    "COSTO ($)",
                    min_value=0,
                    step=100,
                    key=f"n_cost_{st.session_state.admin_key}",
                )
                n_precio = st.number_input(
                    "PRECIO VENTA ($)",
                    min_value=0,
                    step=100,
                    key=f"n_prec_{st.session_state.admin_key}",
                )
                n_margen = (
                    (n_precio - n_costo) / n_costo
                    if n_costo > 0
                    else 0
                )
                st.info(f"**Margen Estimado: {n_margen * 100:.2f}%**")

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
                        nuevo_registro = pd.DataFrame([
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
                        ])
                        with st.spinner("Creando producto..."):
                            conn.update(
                                spreadsheet=URL_PLANILLA,
                                worksheet="DB_PRODUCTOS",
                                data=pd.concat([df_actual, nuevo_registro], ignore_index=True),
                            )
                            cargar_productos.clear()
                        st.session_state.admin_msg = f"✅ ¡{n_nombre} añadido al catálogo!"
                        st.session_state.admin_key += 1
                        st.rerun()

    except Exception as e:
        st.error(f"Error al cargar el panel de administración. Detalle: {e}")


# ==========================================
# 10. HISTORIAL DE ÍTEMS
# ==========================================
def mostrar_historial():
    st.title("📜 Historial de Ítems")
    st.caption("Auditoría de ventas y recálculo automático de caja")

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

        normalizar_fecha_columna(df_historial)

        with st.container(border=True):
            col1, col2 = st.columns([3, 7], gap="large")

            with col1:
                fecha_elegida = st.date_input("🗓️ Filtrar por Día:", ahora_ar().date())
                palabra_clave = st.text_input("🔍 Buscar producto específico:")

            mask_fecha = df_historial["FECHA_REAL"].dt.date == fecha_elegida
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
                metodos = edited_df["METODO_PAGO"].astype(str).str.upper()
                total_efvo = sumar_numerico(edited_df.loc[metodos == "EFECTIVO"], "SUBTOTAL")
                total_transf = sumar_numerico(edited_df.loc[metodos == "TRANSFERENCIA"], "SUBTOTAL")
                total_mixto = sumar_numerico(edited_df.loc[metodos == "MIXTO"], "SUBTOTAL")

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
                with st.spinner("Sincronizando ítems y recalculando cierres de caja..."):
                    indices_originales = df_filtrado.index.tolist()
                    indices_editados = edited_df.index.tolist()
                    df_final_items = df_historial.copy()

                    indices_eliminados = [
                        idx for idx in indices_originales if idx not in indices_editados
                    ]
                    df_final_items = df_final_items.drop(indices_eliminados)

                    for idx, row in edited_df.iterrows():
                        if idx in df_final_items.index:
                            df_final_items.loc[idx, columnas_mostrar] = row.values
                        else:
                            df_final_items = pd.concat(
                                [df_final_items, pd.DataFrame([row])],
                                ignore_index=True,
                            )

                    df_final_items = df_final_items.drop(
                        columns=["FECHA_REAL"],
                        errors="ignore",
                    )

                    tickets_involucrados = (
                        df_filtrado["TICKET_ID"].dropna().unique().tolist()
                    )

                    for tid in tickets_involucrados:
                        items_del_ticket = df_final_items[
                            df_final_items["TICKET_ID"] == tid
                        ]
                        new_total = sumar_numerico(items_del_ticket, "SUBTOTAL")

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
                                    df_caja.at[idx_caja, "TOTAL_VENTA"] = new_total
                                    efvo = numero_seguro(
                                        df_caja.at[idx_caja, "MONTO_EFECTIVO"]
                                    )
                                    transf = numero_seguro(
                                        df_caja.at[idx_caja, "MONTO_TRANSF"]
                                    )

                                    if diff > 0:
                                        if efvo >= diff:
                                            df_caja.at[idx_caja, "MONTO_EFECTIVO"] = efvo - diff
                                        else:
                                            df_caja.at[idx_caja, "MONTO_EFECTIVO"] = 0
                                            df_caja.at[idx_caja, "MONTO_TRANSF"] = max(
                                                0,
                                                transf - (diff - efvo),
                                            )
                                    else:
                                        if transf > 0 and efvo == 0:
                                            df_caja.at[idx_caja, "MONTO_TRANSF"] = transf - diff
                                        else:
                                            df_caja.at[idx_caja, "MONTO_EFECTIVO"] = efvo - diff

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
                    cargar_movimientos_resumen.clear()
                    st.session_state.hist_msg = (
                        "✅ ¡Los ítems fueron corregidos y la Caja fue recalculada perfectamente!"
                    )
                    st.session_state.hist_key += 1
                    st.rerun()

    except Exception as e:
        st.error(f"No se pudo cargar el historial. Detalle: {e}")


# ==========================================
# 11. VISOR / DASHBOARD
# ==========================================
def mostrar_visor():
    st.markdown(
        """
        <style>
        .visor-title { font-size: clamp(1.8rem, 2.8vw, 2.5rem); font-weight: 850; letter-spacing: -0.045em; margin: 0 0 .8rem 0; color:#0f172a; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    def visor_header(titulo, fondo, texto="#FFFFFF"):
        return (
            f'<div class="visor-header" style="background:{fondo};color:{texto};">'
            f'{html.escape(titulo)}</div>'
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

        df_hoy_caja = df_caja[df_caja["FECHA_REAL"].dt.date == fecha_elegida].copy()
        df_hoy_cargas = df_cargas[df_cargas["FECHA_REAL"].dt.date == fecha_elegida].copy()
        df_hoy_gastos = df_gastos[df_gastos["FECHA_REAL"].dt.date == fecha_elegida].copy()

        a_efvo = sumar_numerico(df_hoy_caja, "MONTO_EFECTIVO")
        a_transf = sumar_numerico(df_hoy_caja, "MONTO_TRANSF")
        a_total = sumar_numerico(df_hoy_caja, "TOTAL_VENTA")
        a_ganancia = a_total * 0.10

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

            recargas_transferencias_total += pago_transf
            capital_transferido = min(pago_transf, monto_carga)
            adicional_transferido = max(pago_transf - monto_carga, 0)

            c_total += monto_adic
            c_transf += adicional_transferido
            c_efvo += max(monto_adic - adicional_transferido, 0)

            if servicio == "CLARO":
                e_total += monto_carga
                e_transf += capital_transferido
                e_efvo += max(monto_carga - capital_transferido, 0)
            elif servicio != "SUBE (MP)":
                b_total += monto_carga
                b_transf += capital_transferido
                b_efvo += max(monto_carga - capital_transferido, 0)
            else:
                sube_mp_capital += monto_carga

        if not df_hoy_gastos.empty and "METODO_PAGO" in df_hoy_gastos.columns:
            metodo_gastos = df_hoy_gastos["METODO_PAGO"].astype(str).str.strip().str.upper()
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
        d_ventas_drugstore = a_transf
        d_recargas = recargas_transferencias_total
        d_total_banco = d_ventas_drugstore + d_recargas - sube_mp_capital - gastos_transf
        efectivo_disponible = a_efvo - d_recargas

        caja_a = (
            '<div class="visor-card">'
            + visor_header("CAJA A - DRUGSTORE", "#0000FF")
            + '<div class="visor-body">'
            + visor_line("(+) EFECTIVO:", a_efvo)
            + f'<div class="visor-secondary">{dinero(efectivo_disponible)}</div>'
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
                '<div class="visor-profit-value" style="background:#0000FF;">'
                f'{dinero(a_ganancia)}</div>'
                '</div>'
            )
            + '</div></div>'
        )

        caja_b = (
            '<div class="visor-card">'
            + visor_header("CAJA B - SUBE (Solo Capital)", "#FF9900", "#111111")
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
            + '</div></div>'
        )

        caja_c = (
            '<div class="visor-card">'
            + visor_header("CAJA C - ADICIONALES (Ganancia)", "#38761D")
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
            + '</div></div>'
        )

        caja_d = (
            '<div class="visor-card">'
            + visor_header("CAJA D - TRANSFERENCIAS (Total)", "#9900FF")
            + '<div class="visor-body">'
            + visor_line("DE VENTAS DRUGSTORE:", d_ventas_drugstore)
            + visor_line("DE RECARGAS (Todas):", d_recargas)
            + '<div class="visor-separator"></div>'
            + (
                '<div class="visor-total">'
                '<div class="visor-total-label">TOTAL EN BANCO:</div>'
                '<div class="visor-total-value" style="background:#D9D9D9;padding:7px 10px;">'
                f'{dinero(d_total_banco)}</div>'
                '</div>'
            )
            + '</div></div>'
        )

        caja_e = (
            '<div class="visor-card">'
            + visor_header("CAJA E - CLARO (Solo Capital)", "#FF0000")
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
            + '</div></div>'
        )

        caja_gastos = (
            '<div class="visor-card">'
            + visor_header("GASTOS / RETIROS DEL DÍA", "#000000")
            + '<div class="visor-body">'
            + visor_line("(-) SALIDAS EFECTIVO:", gastos_efvo)
            + visor_line("(-) SALIDAS TRANSF:", gastos_transf)
            + '<div class="visor-separator"></div>'
            + (
                '<div class="visor-total">'
                '<div class="visor-total-label"></div>'
                f'<div class="visor-total-value">{dinero(gastos_total)}</div>'
                '</div>'
            )
            + '</div></div>'
        )

        st.markdown(
            '<div class="visor-grid-wrapper"><div class="visor-grid">'
            + caja_a + caja_b + caja_c + caja_d + caja_e + caja_gastos
            + '</div></div>',
            unsafe_allow_html=True,
        )

    except Exception as e:
        st.error(f"Error cargando el dashboard: {e}")


# ==========================================
# 12. PREVENTISTAS
# ==========================================
def mostrar_preventistas():
    st.title("🚚 Catálogo por Preventista")
    st.caption("Actualiza costos y precios por proveedor directamente desde la tabla")

    st.write(
        "Selecciona un proveedor, edita los precios directamente en la tabla o da de alta un producto nuevo."
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
        df_productos = df_productos.dropna(subset=["NOMBRE"]).copy()

        if not df_productos.empty:
            proveedores_unicos = sorted(
                df_productos["PROVEEDOR"].dropna().astype(str).unique().tolist()
            )
            categorias_unicas = sorted(
                df_productos["CATEGORIA"].dropna().astype(str).unique().tolist()
            )

            with st.container(border=True):
                proveedor_elegido = st.selectbox(
                    "👤 Seleccionar Preventista / Proveedor:",
                    [""] + proveedores_unicos,
                )

                if proveedor_elegido:
                    df_filtrado = df_productos[
                        df_productos["PROVEEDOR"].astype(str) == proveedor_elegido
                    ]
                    st.write(
                        f"### Productos de: **{proveedor_elegido}** ({len(df_filtrado)} ítems)"
                    )
                    st.info(
                        "💡 **Tip:** Edita el Costo o el Precio y presiona Enter "
                        "(o toca afuera de la celda). Verás cómo el porcentaje "
                        "de Ganancia se recalcula **en vivo** en la tabla."
                    )

                    columnas_mostrar = ["NOMBRE", "COSTO", "PRECIO_DIA", "MARGEN_%"]
                    df_edicion = df_filtrado[columnas_mostrar].copy()
                    df_edicion["MARGEN_%"] = (
                        pd.to_numeric(df_edicion["MARGEN_%"], errors="coerce").fillna(0) * 100
                    ).round(1)

                    editor_key = f"ed_prev_{st.session_state.prev_key}_{proveedor_elegido}"

                    if editor_key in st.session_state:
                        cambios_en_vivo = st.session_state[editor_key].get("edited_rows", {})
                        for row_pos_str, mods in cambios_en_vivo.items():
                            row_pos = int(row_pos_str)
                            if row_pos < len(df_edicion):
                                real_idx = df_edicion.index[row_pos]
                                c_val = numero_seguro(
                                    mods.get("COSTO", df_edicion.at[real_idx, "COSTO"])
                                )
                                p_val = numero_seguro(
                                    mods.get("PRECIO_DIA", df_edicion.at[real_idx, "PRECIO_DIA"])
                                )
                                calc_margen = (
                                    ((p_val - c_val) / c_val) * 100
                                    if c_val > 0
                                    else 0.0
                                )
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
                            "MARGEN_%": st.column_config.NumberColumn("GANANCIA (%)", format="%.1f %%"),
                        },
                    )

                    if st.button("💾 Guardar Nuevos Precios", type="primary", use_container_width=True):
                        with st.spinner("Actualizando catálogo en la nube..."):
                            cambios_realizados = False
                            for idx, row in edited_df.iterrows():
                                n_costo = numero_seguro(row["COSTO"])
                                n_precio = numero_seguro(row["PRECIO_DIA"])
                                c_viejo = numero_seguro(df_filtrado.loc[idx, "COSTO"])
                                p_viejo = numero_seguro(df_filtrado.loc[idx, "PRECIO_DIA"])

                                if n_costo != c_viejo or n_precio != p_viejo:
                                    df_productos.at[idx, "COSTO"] = n_costo
                                    df_productos.at[idx, "PRECIO_DIA"] = n_precio
                                    df_productos.at[idx, "PRECIO_NOCHE"] = n_precio
                                    df_productos.at[idx, "MARGEN_%"] = (
                                        (n_precio - n_costo) / n_costo
                                        if n_costo > 0
                                        else 0
                                    )
                                    df_productos.at[idx, "FECHA_ACT"] = fecha_act_texto()
                                    cambios_realizados = True

                            if cambios_realizados:
                                conn.update(
                                    spreadsheet=URL_PLANILLA,
                                    worksheet="DB_PRODUCTOS",
                                    data=df_productos,
                                )
                                cargar_productos.clear()
                                st.session_state.prev_msg = "✅ ¡Los precios de este proveedor fueron actualizados!"
                                st.session_state.prev_key += 1
                                st.rerun()
                            else:
                                st.warning("No detecté ninguna modificación en los números.")

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
                                nuevo_registro = pd.DataFrame([
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
                                ])
                                with st.spinner("Creando producto..."):
                                    conn.update(
                                        spreadsheet=URL_PLANILLA,
                                        worksheet="DB_PRODUCTOS",
                                        data=pd.concat([df_productos, nuevo_registro], ignore_index=True),
                                    )
                                    cargar_productos.clear()
                                st.session_state.prev_msg = f"✅ ¡{p_nombre} añadido al catálogo de {proveedor_elegido}!"
                                st.session_state.prev_key += 1
                                st.rerun()
        else:
            st.warning("No hay productos cargados en la base de datos.")

    except Exception as e:
        st.error(f"Error al cargar el módulo de preventistas. {e}")


# ==========================================
# 13. ENRUTADOR PRINCIPAL
# ==========================================
st.sidebar.image(
    "https://cdn-icons-png.flaticon.com/512/3514/3514491.png",
    width=120,
)

st.sidebar.title("Sistema Genaro")
st.sidebar.caption("POS · Gestión · Control")

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
