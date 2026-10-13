import streamlit as st
import requests
from datetime import datetime, timedelta
import mysql.connector
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import unicodedata
import re
from collections import defaultdict
import numpy as np

# Configuración inicial de la página
st.set_page_config(
    page_title="MIAA - Lecturas Inteligentes y Telemetría",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Estilo visual moderno / Dark/Clean para MIAA
st.markdown("""
    <style>
    .main { background-color: #0e1117; color: #ffffff; }
    .stMetric { background-color: #161b22; padding: 15px; border-radius: 8px; border: 1px solid #30363d; }
    </style>
""", unsafe_allow_html=True)

DEBUG = False

# ------------------------ Utilidades de normalización ------------------------
def _to_ascii(text):
    if text is None:
        return ""
    txt = str(text)
    txt = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode("ascii")
    return txt

def _canon_numeric(value):
    if value is None:
        return ""
    s = _to_ascii(value)
    digits = re.sub(r"[^0-9]", "", s)
    return digits

def get_primer_dia_mes(dt):
    return dt.replace(day=1)

# ---------------- Autenticación y obtención desde API ----------------
@st.cache_data(ttl=3600, show_spinner=False)
def login_api():
    login_url = "https://prelec.miaa.mx/auth/login"
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": "com.wms.mscv.lecturas",
    }
    login_payload = {
        "username": "pedro.templos@miaa.mx",
        "password": "Pedro02081984"
    }
    try:
        resp = requests.post(login_url, json=login_payload, headers=headers, timeout=20)
        if resp.status_code == 200:
            token = resp.json().get("token")
            return f"Bearer {token}" if token else None
    except Exception as e:
        st.error(f"Error de autenticación API: {e}")
    return None

def obtener_lecturas_completas(fecha_inicio, fecha_fin):
    token = login_api()
    if not token:
        st.error("No se pudo autenticar en la API de MIAA.")
        return []
        
    lectura_url = "https://prelec.miaa.mx/msvc-lecturas/lecturas/getLecturasInteligentes"
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": "com.wms.mscv.lecturas",
        "Authorization": token
    }
    
    todas_las_lecturas = []
    page = 0
    size = 500
    
    progress_text = st.empty()
    progress_bar = st.progress(0)
    
    try:
        while True:
            params = {
                'page': page,
                'size': size,
                'sort': 'fecha',
                'direction': 'desc',
                'fechaDesde': fecha_inicio,
                'fechaHasta': fecha_fin
            }
            
            response = requests.get(lectura_url, headers=headers, params=params, timeout=30)
            if response.status_code != 200:
                break
                
            data = response.json()
            items_pagina = data.get("content", data.get("data", [])) if isinstance(data, dict) else data
                
            if not items_pagina:
                break
                
            todas_las_lecturas.extend(items_pagina)
            progress_text.text(f"Cargando registros desde la API... Página {page} (Total acumulado: {len(todas_las_lecturas):,})")
            
            if len(items_pagina) < size or (isinstance(data, dict) and data.get("last") is True):
                break
                
            page += 1
            
        progress_text.empty()
        progress_bar.empty()
        return todas_las_lecturas
        
    except Exception as e:
        progress_text.empty()
        progress_bar.empty()
        st.error(f"Excepción al conectar con la API: {e}")
        return []

# -------------------- Bases de Datos MySQL (Padrón y GPS) --------------------
@st.cache_data(ttl=1800)
def obtener_lat_long():
    try:
        conn = mysql.connector.connect(
            host="qgiscon.miaa.mx", port=3306, database="gps",
            user="pozos", password='8pU?"X_64(5H7Og4',
            charset='utf8mb4', collation='utf8mb4_general_ci', use_pure=True
        )
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT serie, latitud, longitud FROM medidores")
        resultados = cursor.fetchall()
        cursor.close()
        conn.close()
        
        return {
            _canon_numeric(row["serie"]): {
                "Latitud": row["latitud"], "Longitud": row["longitud"]
            } for row in resultados
        }
    except Exception as e:
        st.warning(f"No se pudo conectar a la BD GPS: {e}")
        return {}

@st.cache_data(ttl=1800)
def obtener_info_padron():
    try:
        conn = mysql.connector.connect(
            host="miaa.mx", database="miaamx_telemetria2",
            user="miaamx_telemetria2", password="bWkrw1Uum1O&",
            charset="utf8mb4", collation='utf8mb4_general_ci', use_pure=True
        )
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""SELECT Cliente, Medidor, Nombre, Domicilio, Colonia, Nivel, `Predio`,
                Situacion_comercial, Giro, Marca, Modelo, Primer_instalacion, Distrito, Sector FROM WASION""")
        resultados = cursor.fetchall()
        cursor.close()
        conn.close()

        by_cliente, by_medidor = {}, {}
        for row in resultados:
            cli = _canon_numeric(row.get("Cliente"))
            med = _canon_numeric(row.get("Medidor"))
            datos = {
                "Cliente": cli, "Medidor": med,
                "Nombre": _to_ascii(row.get("Nombre", "")),
                "Domicilio": _to_ascii(row.get("Domicilio", "")),
                "Colonia": _to_ascii(row.get("Colonia", "")),
                "Nivel": _to_ascii(row.get("Nivel", "")),
                "Predio": _to_ascii(row.get("Predio", "")),
                "Situacion_comercial": _to_ascii(row.get("Situacion_comercial", "")),
                "Giro": _to_ascii(row.get("Giro", "")),
                "Marca": _to_ascii(row.get("Marca", "")),
                "Modelo": _to_ascii(row.get("Modelo", "")),
                "Primer_instalacion": _to_ascii(row.get("Primer_instalacion", "")),
                "Distrito": _to_ascii(row.get("Distrito", "")),
                "Sector": _to_ascii(row.get("Sector", "")),
            }
            if cli: by_cliente[cli] = datos
            if med: by_medidor[med] = datos
        return by_cliente, by_medidor
    except Exception as e:
        st.warning(f"No se pudo conectar a la BD Padrón: {e}")
        return {}, {}

# -------------------- Interfaz Principal Streamlit --------------------
st.title("💧 MIAA - Panel de Lecturas Inteligentes y Telemetría")

# Barra Lateral de Filtros
st.sidebar.header("⚙️ Filtros y Parámetros")

hoy = datetime.now().date()
ayer = hoy - timedelta(days=1)

periodo_op = st.sidebar.selectbox("Periodo a procesar", ["Este mes (hasta ayer)", "Mes anterior", "Últimos 30 días", "Personalizado"])

if periodo_op == "Personalizado":
    dias_pers = st.sidebar.number_input("Días de retroceso", min_value=1, max_value=60, value=30)
    f_inicio = ayer - timedelta(days=dias_pers)
    f_fin = ayer
elif periodo_op == "Este mes (hasta ayer)":
    f_inicio = get_primer_dia_mes(ayer)
    f_fin = ayer
elif periodo_op == "Mes anterior":
    p_actual = get_primer_dia_mes(hoy)
    f_fin = p_actual - timedelta(days=1)
    f_inicio = get_primer_dia_mes(f_fin)
else:
    f_inicio = ayer - timedelta(days=29)
    f_fin = ayer

col_i, col_f = st.sidebar.columns(2)
fecha_inicio = col_i.date_input("Fecha Inicio", value=f_inicio)
fecha_fin = col_f.date_input("Fecha Fin", value=f_fin)

metodo_filtro = st.sidebar.selectbox("Método de Lectura", ["Todos", "LoraWan", "App"])
medidor_input = st.sidebar.text_input("Filtrar por Medidor (Serie)")

# Cargar opciones de padrón para selectores
pad_by_cli, pad_by_med = obtener_info_padron()
gps_data = obtener_lat_long()

niveles = sorted(list(set(d.get("Nivel") for d in pad_by_med.values() if d.get("Nivel"))))
colonias = sorted(list(set(d.get("Colonia") for d in pad_by_med.values() if d.get("Colonia"))))
sectores = sorted(list(set(d.get("Sector") for d in pad_by_med.values() if d.get("Sector"))))

tarifa_sel = st.sidebar.selectbox("Tarifa (Nivel)", ["Todas"] + niveles)
colonia_sel = st.sidebar.selectbox("Colonia", ["Todas"] + colonias)
sector_sel = st.sidebar.selectbox("Sector", ["Todas"] + sectores)

btn_buscar = st.sidebar.button("🔍 Cargar y Procesar Lecturas", type="primary")

# Estado de sesión para almacenar los datos procesados
if "df_lecturas" not in st.session_state:
    st.session_state.df_lecturas = pd.DataFrame()

if btn_buscar:
    with st.spinner("Conectando a la API de MIAA y procesando registros..."):
        raw_data = obtener_lecturas_completas(fecha_inicio.strftime("%Y-%m-%d"), fecha_fin.strftime("%Y-%m-%d"))
        
        if not raw_data:
            st.warning("No se obtuvieron registros de la API para este rango.")
        else:
            registros_tmp = []
            for item in raw_data:
                fecha_raw = str(item.get("fecha", item.get("fechaLectura", item.get("createdDate", ""))))
                fecha_txt = fecha_raw.replace("T", " ")
                try:
                    fecha_dt = datetime.strptime(fecha_txt[:19], "%Y-%m-%d %H:%M:%S")
                except:
                    continue

                medidor_id = _canon_numeric(item.get("medidorId", item.get("medidor", item.get("serie", ""))))
                
                if metodo_filtro != "Todos":
                    m_api = "LORAWAN" if metodo_filtro == "LoraWan" else "APP"
                    if str(item.get("metodoId", item.get("metodo", ""))).upper() != m_api:
                        continue
                
                if medidor_input and medidor_id != _canon_numeric(medidor_input):
                    continue

                try:
                    lectura = float(item.get("lectura", item.get("valor", 0)) or 0)
                except:
                    lectura = 0.0

                cli_raw = str(item.get("clienteId") or item.get("cliente") or "")
                cli_canon = _canon_numeric(cli_raw)
                
                info = pad_by_cli.get(cli_canon, {}) or pad_by_med.get(medidor_id, {})
                
                registros_tmp.append({
                    "ID": str(item.get("id", f"REAL-{medidor_id}-{fecha_dt.strftime('%Y%m%d')}")),
                    "Fecha": fecha_dt,
                    "Fecha_txt": fecha_txt[:19],
                    "Predio": info.get("Predio", "SIN DATO"),
                    "Medidor": medidor_id,
                    "Lectura": lectura,
                    "MetodoID_API": item.get("metodoId", item.get("metodo", "")),
                    "ClienteID_API": cli_raw,
                    "Nombre": info.get("Nombre", "SIN DATO"),
                    "Domicilio": info.get("Domicilio", "SIN DATO"),
                    "Colonia": info.get("Colonia", "SIN DATO"),
                    "Nivel": info.get("Nivel", "SIN DATO"),
                    "Situacion_comercial": info.get("Situacion_comercial", "SIN DATO"),
                    "Giro": info.get("Giro", "SIN DATO"),
                    "Marca": info.get("Marca", ""),
                    "Modelo": info.get("Modelo", ""),
                    "Primer_instalacion": info.get("Primer_instalacion", ""),
                    "Distrito": info.get("Distrito", ""),
                    "Sector": info.get("Sector", ""),
                    "Latitud": gps_data.get(medidor_id, {}).get("Latitud", ""),
                    "Longitud": gps_data.get(medidor_id, {}).get("Longitud", ""),
                    "Consumo_diario": 0.0
                })

            df = pd.DataFrame(registros_tmp)
            if not df.empty:
                df = df.sort_values(by=["Medidor", "Fecha"])
                
                # Calcular consumo diario interpolado
                df["Consumo_diario"] = df.groupby("Medidor")["Lectura"].diff().fillna(0)
                df["Consumo_diario"] = df["Consumo_diario"].apply(lambda x: max(0.0, x))

                # Filtros de Padrón en DataFrame
                if tarifa_sel != "Todas":
                    df = df[df["Nivel"] == tarifa_sel]
                if colonia_sel != "Todas":
                    df = df[df["Colonia"] == colonia_sel]
                if sector_sel != "Todas":
                    df = df[df["Sector"] == sector_sel]

                st.session_state.df_lecturas = df
                st.success(f"¡Proceso completado! Se cargaron y filtraron {len(df):,} registros exitosamente.")
            else:
                st.warning("No hay registros después de aplicar los filtros.")

# Si ya hay datos cargados, mostramos pestañas con la tabla y gráficas
if not st.session_state.df_lecturas.empty:
    df_view = st.session_state.df_lecturas

    # Métricas Resumen arriba
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Registros", f"{len(df_view):,}")
    col2.metric("Consumo Total Acumulado (m³)", f"{df_view['Consumo_diario'].sum():,.2f}")
    col3.metric("Consumo Promedio (m³)", f"{df_view['Consumo_diario'].mean():,.2f}")

    tab1, tab2, tab3 = st.tabs(["📋 Tabla de Registros", "📊 Gráficas de Consumo", "🏆 Top 10 Medidores"])

    with tab1:
        st.subheader("Registros Detallados")
        st.dataframe(df_view, use_container_width=True, height=500)
        
        # Botón para descargar a CSV
        csv = df_view.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Descargar datos filtrados en CSV", data=csv, file_name="lecturas_miaa.csv", mime="text/csv")

    with tab2:
        st.subheader("Evolución de Consumo Diario")
        df_group_fecha = df_view.groupby(df_view["Fecha"].dt.date)["Consumo_diario"].sum().reset_index()
        fig_line = px.line(df_group_fecha, x="Fecha", y="Consumo_diario", title="Consumo Diario Total Agregado (m³)", markers=True, color_discrete_sequence=["#198754"])
        st.plotly_chart(fig_line, use_container_width=True)

    with tab3:
        st.subheader("Top 10 Medidores con Mayor Consumo")
        df_top10 = df_view.groupby(["Medidor", "Nombre", "Colonia"])["Consumo_diario"].sum().reset_index()
        df_top10 = df_top10.sort_values(by="Consumo_diario", ascending=False).head(10)
        
        fig_bar = px.bar(df_top10, x="Consumo_diario", y="Medidor", orientation="h", text="Consumo_diario",
                         hover_data=["Nombre", "Colonia"], title="Top 10 Medidores", color_discrete_sequence=["coral"])
        fig_bar.update_layout(yaxis={'categoryorder':'total ascending'})
        st.plotly_chart(fig_bar, use_container_width=True)
else:
    st.info("👈 Configura los filtros en la barra lateral y haz clic en **'Cargar y Procesar Lecturas'** para comenzar.")
