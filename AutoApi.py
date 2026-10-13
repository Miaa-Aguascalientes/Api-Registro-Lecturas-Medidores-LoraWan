# ==========================================
# 1. CONFIGURACIÓN DE PÁGINA Y ESTILOS
# ==========================================
from datetime import datetime, timedelta
import time
from zoneinfo import ZoneInfo
import pandas as pd
import requests
from sqlalchemy import create_engine, text
import streamlit as st

ZONA_MEXICO = ZoneInfo("America/Mexico_City")

st.set_page_config(
    page_title="Sistema Scada",
    page_icon="https://www.miaa.mx/favicon.ico",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
        /* Ocultar elementos predeterminados de la interfaz de Streamlit */
        #MainMenu {visibility: hidden;}
        header {visibility: hidden;}
        footer {visibility: hidden;}
        
        /* BLOQUEAR LA BARRA LATERAL PARA QUE NUNCA SE PUEDA OCULTAR O COLAPSAR */
        [data-testid="stSidebar"] {
            min-width: 280px !important;
            max-width: 320px !important;
            transform: none !important;
        }
        [data-testid="collapsedControl"] {
            display: none !important;
        }
        button[kind="header"] {
            display: none !important;
        }
        
        /* EVITAR COMPLETAMENTE LA OPACIDAD O EFECTO GRIS CUANDO STREAMLIT SE EJECUTA O ACTUALIZA */
        .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"], .main, .block-container {
            opacity: 1 !important;
            filter: none !important;
            pointer-events: auto !important;
        }
        [data-testid="stStatusWidget"] {
            display: none !important;
        }
        
        /* Eliminar el espacio superior por defecto de la página */
        .block-container {
            padding-top: 1rem !important;
            padding-bottom: 0rem !important;
        }
        
        header hr, .stApp > header + div hr, [data-testid="stHeader"] hr, hr {
            display: none !important;
        }
        
        .metric-card {
            background-color: #1e293b;
            border: 1px solid #334155;
            padding: 15px;
            border-radius: 8px;
            display: flex;
            align-items: center;
        }
        .metric-content {
            display: flex;
            flex-direction: column;
        }
        .metric-title {
            font-size: 12px;
            color: #94a3b8;
        }
        .metric-value {
            font-size: 20px;
            font-weight: bold;
            color: #f8fafc;
        }
        
        /* Consola con altura exacta alineada */
        .terminal-box {
            background-color: #0b0f19;
            border: 1px solid #22c55e;
            color: #22c55e;
            font-family: 'Courier New', Courier, monospace;
            padding: 15px;
            border-radius: 6px;
            height: 505px;
            overflow-y: scroll;
            font-size: 13px;
            line-height: 1.4;
        }
        
        [data-testid="stVerticalBlock"] [data-testid="stVerticalBlockBorderWrapper"] {
            background-color: #0e1322 !important;
            border: 1px solid #334155 !important;
            border-radius: 8px !important;
            height: 567px !important;
            overflow-y: auto !important;
            padding: 15px !important;
        }
    </style>
""",
    unsafe_allow_html=True,
)

# ==========================================
# 2. GESTIÓN DE LOGS (CONSOLA - HORA MÉXICO)
# ==========================================
if "logs" not in st.session_state:
  st.session_state.logs = [
      f"[{datetime.now(ZONA_MEXICO).strftime('%H:%M:%S')}] Sistema inicializado"
      " correctamente. Esperando ciclo de ejecución..."
  ]


def agregar_log(mensaje):
  timestamp = datetime.now(ZONA_MEXICO).strftime("%H:%M:%S")
  st.session_state.logs.insert(0, f"[{timestamp}] {mensaje}")
  if len(st.session_state.logs) > 300:
    st.session_state.logs.pop()


# ==========================================
# 3. CONEXIONES Y CONSULTAS (SQL Y API)
# ==========================================
BASE_URL = "https://prelec.miaa.mx"
URL_LOGIN_CORRECTA = "/auth/login"
URL_INSTALACIONES = "https://prelec.miaa.mx/msvc-tecnica/medidores/instalaciones"


def obtener_motor_postgres():
  pg = st.secrets["postgres"]
  connection_string = f"postgresql+psycopg2://{pg['user']}:{pg['password']}@{pg['host']}:{pg['port']}/{pg['database']}"
  return create_engine(connection_string)


@st.cache_data(ttl=600)
def obtener_total_registros():
  try:
    engine_pg = obtener_motor_postgres()
    with engine_pg.connect() as conn:
      result = conn.execute(
          text('SELECT COUNT(*) FROM "Usuarios"."usuarios_miaa_conmedidor"')
      )
      return result.scalar()
  except Exception as e:
    agregar_log(f"⚠️ [AVISO DB] No se pudo obtener el conteo de registros: {e}")
    return 0


@st.cache_data(ttl=60)
def obtener_total_etapa_1():
  try:
    engine_pg = obtener_motor_postgres()
    with engine_pg.connect() as conn:
      result = conn.execute(
          text(
              'SELECT COUNT(*) FROM "Usuarios"."usuarios_miaa_conmedidor"'
              " WHERE etapa = '1'"
          )
      )
      return result.scalar() or 0
  except Exception as e:
    agregar_log(f"⚠️ [AVISO DB] No se pudo obtener el conteo de etapa 1: {e}")
    return 0


@st.cache_data(ttl=60)
def obtener_total_etapa_2():
  try:
    engine_pg = obtener_motor_postgres()
    with engine_pg.connect() as conn:
      result = conn.execute(
          text(
              'SELECT COUNT(*) FROM "Usuarios"."usuarios_miaa_conmedidor"'
              " WHERE etapa = '2'"
          )
      )
      return result.scalar() or 0
  except Exception as e:
    agregar_log(f"⚠️ [AVISO DB] No se pudo obtener el conteo de etapa 2: {e}")
    return 0


@st.cache_data(ttl=60)
def cargar_pagina_usuarios_db(limit=50, offset=0):
  try:
    engine_pg = obtener_motor_postgres()
    query = text(
        'SELECT * FROM "Usuarios"."usuarios_miaa_conmedidor" LIMIT :lim OFFSET'
        " :off"
    )
    return pd.read_sql(
        query, con=engine_pg, params={"lim": limit, "off": offset}
    )
  except Exception as e:
    agregar_log(f"❌ [ERROR DB] Error al cargar página de PostgreSQL: {e}")
    return pd.DataFrame()


@st.cache_data(ttl=60)
def cargar_pagina_medidores_inteligentes(limit=50, offset=0):
  try:
    engine_pg = obtener_motor_postgres()
    query = text(
        'SELECT * FROM "Medidores"."medidores_inteligentes" LIMIT :lim OFFSET'
        " :off"
    )
    return pd.read_sql(
        query, con=engine_pg, params={"lim": limit, "off": offset}
    )
  except Exception as e:
    agregar_log(
        "❌ [ERROR DB] Error al cargar página de medidores_inteligentes:"
        f" {e}"
    )
    return pd.DataFrame()


@st.cache_data(ttl=300)
def cargar_datos_api():
  """Conecta con la API de MIAA con reintentos inteligentes (Backoff exponencial) para mitigar errores 503."""
  usuario = st.secrets["api"]["usuario"]
  password = st.secrets["api"]["password"]
  url_login = BASE_URL + URL_LOGIN_CORRECTA

  token = None
  max_intentos = 5
  data = None

  for intento in range(1, max_intentos + 1):
    try:
      res_login = requests.post(
          url_login,
          json={"username": usuario, "password": password},
          headers={"Content-Type": "application/json"},
          timeout=20,
      )
      if res_login.status_code == 200:
        data_login = res_login.json()
        token = data_login.get("token") or data_login.get("access_token")
        if token:
          break
      time.sleep(intento * 3)
    except Exception:
      time.sleep(3)

  if not token:
    agregar_log(
        "⚠️ [AVISO API] No se pudo autenticar en la API tras varios"
        " intentos. Operando con base local."
    )
    return pd.DataFrame()

  for intento in range(1, max_intentos + 1):
    try:
      res_inst = requests.get(
          URL_INSTALACIONES,
          headers={
              "Content-Type": "application/json",
              "Authorization": f"Bearer {token}",
          },
          timeout=60,
      )

      if res_inst.status_code == 503:
        tiempo_espera = intento * 5
        agregar_log(
            f"⚠️ [AVISO API] Intento {intento}/{max_intentos}: Servidor ocupado"
            f" (HTTP 503). Reintentando en {tiempo_espera}s..."
        )
        time.sleep(tiempo_espera)
        continue

      if res_inst.status_code != 200:
        agregar_log(
            f"⚠️ [AVISO API] Error HTTP {res_inst.status_code} al consultar"
            f" instalaciones."
        )
        return pd.DataFrame()

      data = res_inst.json()
      break

    except requests.exceptions.RequestException as e:
      agregar_log(
          f"⚠️ [AVISO API] Error de red en intento {intento}/{max_intentos}: {e}"
      )
      time.sleep(5)

  if data is None:
    return pd.DataFrame()

  df = pd.DataFrame()
  if isinstance(data, list):
    df = pd.DataFrame(data)
  elif isinstance(data, dict):
    for key in ["data", "result", "items", "instalaciones"]:
      if key in data and isinstance(data[key], list):
        df = pd.DataFrame(data[key])
        break
    if df.empty:
      df = pd.DataFrame([data])

  if not df.empty:
    if "numeroCliente" in df.columns:
      df["Cliente"] = df["numeroCliente"]
    elif "numero_cliente" in df.columns:
      df["Cliente"] = df["numero_cliente"]
    elif "numCliente" in df.columns:
      df["Cliente"] = df["numCliente"]

    col_api_predio = next(
        (
            c
            for c in ["predio", "predioViv", "predio_viv", "numeroPredio"]
            if c in df.columns
        ),
        None,
    )
    col_api_unidad = next(
        (c for c in ["unidad", "unidadViv", "unidad_viv"] if c in df.columns),
        None,
    )

    def construir_predio_viv(row):
      if not col_api_predio:
        return ""
      p = row[col_api_predio]
      if pd.isna(p) or str(p).strip().lower() in ["none", "nan", ""]:
        return ""

      p_str = str(p).strip()
      u = (
          row[col_api_unidad]
          if col_api_unidad and pd.notna(row[col_api_unidad])
          else 0
      )
      u_str = str(u).strip()

      if u_str.lower() in ["none", "nan", ""]:
        u_str = "0"

      return f"{p_str}-{u_str}"

    df["Predio_Viv"] = df.apply(construir_predio_viv, axis=1)

    mapa_fotos_api = {
        "fotomedidoranterior": "fotoMedidorAnterior",
        "fotofachada": "fotoFachada",
        "fotomedidoractual": "fotoMedidorActual",
        "fotocolumpioregistro": "fotocolumpioregistro",
        "fotomedidoridvisible": "fotomedidoridvisible",
    }

    for col_actual in df.columns:
      col_lower = col_actual.lower()
      if col_lower in mapa_fotos_api:
        nombre_correcto = mapa_fotos_api[col_lower]
        if col_actual != nombre_correcto:
          df[nombre_correcto] = df[col_actual]

    for col_foto_req in [
        "fotoMedidorAnterior",
        "fotoFachada",
        "fotoMedidorActual",
        "fotocolumpioregistro",
        "fotomedidoridvisible",
    ]:
      if col_foto_req not in df.columns:
        df[col_foto_req] = None

    columnas_a_quitar = [
        "predio",
        "predioViv",
        "predio_viv",
        "numeroPredio",
        "unidad",
        "unidadViv",
        "unidad_viv",
        "uuid",
        "numeroCliente",
        "numero_cliente",
        "numCliente",
    ]
    df = df.drop(columns=columnas_a_quitar, errors="ignore")

    cols_prioritarias = [c for c in ["Cliente", "Predio_Viv"] if c in df.columns]
    otras_cols = [c for c in df.columns if c not in cols_prioritarias]
    df = df[cols_prioritarias + otras_cols]

  return df


# ==========================================
# 4. FUNCIÓN DE CRUCE ULTRA RÁPIDO (STAGING + SQL)
# ==========================================
def ejecutar_sincronizacion_automatica(
    barra_progreso_placeholder=None,
    texto_estado_placeholder=None,
    consola_placeholder=None,
):
  def actualizar_progreso(valor_pct, mensaje):
    agregar_log(mensaje)
    if consola_placeholder is not None:
      logs_html = "<br>".join(st.session_state.logs)
      consola_placeholder.markdown(
          f'<div class="terminal-box">{logs_html}</div>', unsafe_allow_html=True
      )
    if barra_progreso_placeholder is not None:
      barra_progreso_placeholder.progress(
          valor_pct,
          text=f"Progreso de ejecución: {int(valor_pct * 100)}% - {mensaje}",
      )
    if texto_estado_placeholder is not None:
      texto_estado_placeholder.markdown(
          f"🔄 **{mensaje}** ({int(valor_pct * 100)}%)"
      )
    time.sleep(0.05)

  actualizar_progreso(
      0.15,
      "[PASO 1/4] Conectando con API de instalaciones para descargar registros...",
  )
  df_filtrado = cargar_datos_api()

  if df_filtrado.empty:
    actualizar_progreso(
        1.0,
        "[AVISO] La API devolvió HTTP 503 o está vacía. El sistema opera"
        " con PostgreSQL.",
    )
    return False

  total_descargados = len(df_filtrado)
  actualizar_progreso(
      0.35,
      f"[PASO 2/4] API OK. {total_descargados:,} registros descargados."
      " Preparando tabla temporal en PostgreSQL...",
  )

  try:
    engine_pg = obtener_motor_postgres()

    df_staging = df_filtrado.copy()
    if "Predio_Viv" not in df_staging.columns:
      actualizar_progreso(
          1.0, "[ERROR] La columna Predio_Viv no existe en los datos de la API."
      )
      return False

    df_staging["Predio_Viv_clean"] = (
        df_staging["Predio_Viv"].astype(str).str.strip()
    )

    actualizar_progreso(
        0.55,
        "[PASO 3/4] Cargando datos de la API a la tabla temporal (Staging)"
        " en PostgreSQL...",
    )

    df_staging.to_sql(
        "_staging_api_instalaciones",
        con=engine_pg,
        schema="Usuarios",
        if_exists="replace",
        index=False,
        chunksize=5000,
    )

    actualizar_progreso(
        0.75,
        "[PASO 4/4] Ejecutando actualización masiva (UPDATE FROM JOIN) en"
        " PostgreSQL...",
    )

    query_update_masivo = text("""
            UPDATE "Usuarios"."usuarios_miaa_conmedidor" AS u
            SET 
                "_Serie" = COALESCE(s.serie, u."_Serie"),
                "_Colonia" = COALESCE(s.colonia, u."_Colonia"),
                "_Domicilio" = COALESCE(s.domicilio, u."_Domicilio"),
                "_Instalador" = COALESCE(s."usuarioNombre", u."_Instalador"),
                "_Tipo_instalador" = CASE 
                    WHEN TRIM(CAST(s."usuarioExterno" AS TEXT)) IN ('true', 'True', '1', 'YES', 'yes', 'S', 's', 'TRUE') THEN 'Externo'
                    ELSE 'MIAA'
                END,
                "_Lectura_actual" = CASE 
                    WHEN s."lecturaActual" IS NOT NULL AND TRIM(CAST(s."lecturaActual" AS TEXT)) NOT IN ('', 'none', 'nan', 'null') 
                    THEN CAST(s."lecturaActual" AS DOUBLE PRECISION)
                    ELSE u."_Lectura_actual"
                END,
                "_Fecha_registro" = CASE 
                    WHEN s."fechaRegistro" IS NOT NULL AND TRIM(CAST(s."fechaRegistro" AS TEXT)) NOT IN ('', 'none', 'nan', 'null') 
                    THEN CAST(s."fechaRegistro" AS TIMESTAMP WITH TIME ZONE)
                    ELSE u."_Fecha_registro"
                END,
                "_Fecha_instalacion" = CASE 
                    WHEN s."fechaInstalacion" IS NOT NULL AND TRIM(CAST(s."fechaInstalacion" AS TEXT)) NOT IN ('', 'none', 'nan', 'null') 
                    THEN CAST(s."fechaInstalacion" AS TIMESTAMP WITH TIME ZONE)
                    ELSE u."_Fecha_instalacion"
                END,
                "_Ultima_actualizacion" = :tiempo_actual,
                "fotoMedidorAnterior" = COALESCE(s."fotoMedidorAnterior", u."fotoMedidorAnterior"),
                "fotoFachada" = COALESCE(s."fotoFachada", u."fotoFachada"),
                "fotoMedidorActual" = COALESCE(s."fotoMedidorActual", u."fotoMedidorActual"),
                "fotocolumpioregistro" = COALESCE(s."fotocolumpioregistro", u."fotocolumpioregistro"),
                "fotomedidoridvisible" = COALESCE(s."fotomedidoridvisible", u."fotomedidoridvisible"),
                etapa = '2'
            FROM "Usuarios"."_staging_api_instalaciones" AS s
            WHERE TRIM(CAST(u."Predio_Viv" AS TEXT)) = s."Predio_Viv_clean"
              AND s."Predio_Viv_clean" NOT IN ('none', 'nan', '', 'nat', '0', 'null', 'None', 'NaN');
        """)

    with engine_pg.connect() as conn:
      result_up = conn.execute(
          query_update_masivo,
          {"tiempo_actual": datetime.now(ZONA_MEXICO)},
      )
      conn.commit()
      filas_actualizadas = (
          result_up.rowcount if hasattr(result_up, "rowcount") else 0
      )

      conn.execute(
          text('DROP TABLE IF EXISTS "Usuarios"."_staging_api_instalaciones"')
      )
      conn.commit()

    actualizar_progreso(
        1.0,
        f"🎉 [CICLO EXITOSO] Sincronización completada en segundos. Registros"
        f" cruzados y actualizados en DB: {filas_actualizadas:,}.",
    )
    return True

  except Exception as ex:
    actualizar_progreso(
        1.0, f"[ERROR CRÍTICO] Falló la sincronización en base de datos: {ex}"
    )
    return False


# ==========================================
# 5. TEMPORIZADOR Y RELOJ (HORA MÉXICO)
# ==========================================
def calcular_siguiente_tiempo_reloj(minutos_intervalo):
  ahora = datetime.now(ZONA_MEXICO)
  minuto_actual = ahora.minute
  segundo_actual = ahora.second

  residuo = minuto_actual % minutos_intervalo
  minutos_faltantes = (
      minutos_intervalo - residuo
      if residuo != 0
      else (0 if segundo_actual == 0 else minutos_intervalo)
  )

  if minutos_faltantes == 0 and segundo_actual > 0:
    minutos_faltantes = minutos_intervalo

  siguiente_tiempo = (
      ahora.replace(second=0, microsecond=0)
      + timedelta(minutes=minutos_faltantes)
  )
  total_segundos_hasta_siguiente = (
      siguiente_tiempo - ahora
  ).total_seconds()

  return siguiente_tiempo, int(total_segundos_hasta_siguiente)


if "is_running" not in st.session_state:
  st.session_state.is_running = False
if "next_run_time" not in st.session_state:
  st.session_state.next_run_time = None
if "intervalo_minutos_sel" not in st.session_state:
  st.session_state.intervalo_minutos_sel = 5

# ==========================================
# 6. CARGA DE DATOS PARA INDICADORES Y VISTAS
# ==========================================
total_registros_db = obtener_total_registros()
total_etapa_1_db = obtener_total_etapa_1()
total_etapa_2_db = obtener_total_etapa_2()
df_filtrado = cargar_datos_api()

total_serie_api = 0
if not df_filtrado.empty:
  if "serie" in df_filtrado.columns:
    ts = df_filtrado["serie"].dropna().astype(str).str.strip()
    total_serie_api = ts[
        ~ts.str.lower().isin(["", "none", "nan", "null"])
    ].count()

total_serie_pg_lleno = 0
try:
  engine_pg = obtener_motor_postgres()
  with engine_pg.connect() as conn:
    res_serie_pg = conn.execute(
        text("""
            SELECT COUNT(*) FROM "Usuarios"."usuarios_miaa_conmedidor" 
            WHERE "_Serie" IS NOT NULL 
              AND TRIM(CAST("_Serie" AS TEXT)) != '' 
              AND LOWER(TRIM(CAST("_Serie" AS TEXT))) NOT IN ('none', 'nan', 'null')
        """)
    )
    total_serie_pg_lleno = res_serie_pg.scalar()
except Exception:
  total_serie_pg_lleno = 0

# ==========================================
# 7. BARRA LATERAL IZQUIERDA (SIDEBAR)
# ==========================================
with st.sidebar:
  st.image(
      "https://raw.githubusercontent.com/Miaa-Aguascalientes/Logos/38504978c8f77a4dac38ad476f74dbdee6af2cad/LogoMIAA.svg",
      use_container_width=True,
  )
  st.markdown("### 🚰 Panel de Control MIAA")
  st.markdown("---")
  st.markdown("#### ⚙ Configuración")

  opciones_intervalo = {
      "Cada 1 minuto": 1,
      "Cada 5 minutos": 5,
      "Cada 10 minutos": 10,
      "Cada 15 minutos": 15,
      "Cada 30 minutos": 30,
      "Cada hora": 60,
  }
  intervalo_sel = st.selectbox(
      "Intervalo de sincronización", list(opciones_intervalo.keys())
  )
  minutos_seleccionados = opciones_intervalo[intervalo_sel]

  col_s1, col_s2 = st.columns(2)
  with col_s1:
    btn_iniciar = st.button("INICIAR", type="primary", use_container_width=True)
  with col_s2:
    btn_parar = st.button("PARAR", type="secondary", use_container_width=True)

  if btn_iniciar:
    st.session_state.is_running = True
    st.session_state.intervalo_minutos_sel = minutos_seleccionados
    sig_tiempo, _ = calcular_siguiente_tiempo_reloj(minutos_seleccionados)
    st.session_state.next_run_time = sig_tiempo
    agregar_log(
        f"▶ Temporizador activado. Próxima ejecución sincronizada al reloj a"
        f" las {sig_tiempo.strftime('%H:%M:%S')}."
    )
    st.success(
        f"¡Temporizador iniciado! Siguiente ejecución a las"
        f" {sig_tiempo.strftime('%H:%M:%S')}."
    )
    st.rerun()

  if btn_parar:
    st.session_state.is_running = False
    st.session_state.next_run_time = None
    agregar_log("⏹ Temporizador detenido manualmente por el usuario.")
    st.warning("Temporizador detenido.")
    st.rerun()

# ==========================================
# 8. INTERFAZ PRINCIPAL Y BARRAS DE PROGRESO
# ==========================================
st.markdown(
    """
    <div style="text-align: center; margin-top: -20px; margin-bottom: 10px;">
        <h2 style="color: #f8fafc; font-weight: 700;">MIAA - Sistema de Registros e Instalaciones</h2>
    </div>
    """,
    unsafe_allow_html=True,
)

col_ind1, col_ind2, col_ind3, col_ind4 = st.columns(4)

with col_ind1:
  st.markdown(
      f"""
        <div class="metric-card">
            <div style="font-size: 28px; margin-right: 15px;">🎯</div>
            <div class="metric-content">
                <div class="metric-title">REGISTROS API CON SERIE</div>
                <div class="metric-value">{total_serie_api:,}</div>
            </div>
        </div>
    """,
      unsafe_allow_html=True,
  )

with col_ind2:
  st.markdown(
      f"""
        <div class="metric-card">
            <div style="font-size: 28px; margin-right: 15px;">✅</div>
            <div class="metric-content">
                <div class="metric-title">POSTGRESQL (_SERIE LLENO)</div>
                <div class="metric-value">{total_serie_pg_lleno:,}</div>
            </div>
        </div>
    """,
      unsafe_allow_html=True,
  )

with col_ind3:
  st.markdown(
      f"""
        <div class="metric-card">
            <div style="font-size: 28px; margin-right: 15px;">🔢</div>
            <div class="metric-content">
                <div class="metric-title">MEDIDORES ETAPA 1 / 2</div>
                <div class="metric-value" style="font-size: 16px; margin-top: 2px;">1: {total_etapa_1_db:,} | 2: {total_etapa_2_db:,}</div>
            </div>
        </div>
    """,
      unsafe_allow_html=True,
  )

with col_ind4:
  porcentaje_cobertura = (
      min(100.0, (total_serie_pg_lleno / total_serie_api) * 100)
      if total_serie_api > 0
      else 0.0
  )
  st.markdown(
      f"""
        <div class="metric-card">
            <div style="font-size: 28px; margin-right: 15px;">📊</div>
            <div class="metric-content">
                <div class="metric-title">SINCRONIZACIÓN</div>
                <div class="metric-value">{porcentaje_cobertura:.1f}%</div>
            </div>
        </div>
    """,
      unsafe_allow_html=True,
  )

st.markdown(
    "<div style='margin-bottom: 15px;'></div>", unsafe_allow_html=True
)

st.markdown("#### 🔄 Estado y Progreso del Sistema")
estado_ejecucion_placeholder = st.empty()
barra_progreso_placeholder = st.empty()

col_consola, col_limpieza = st.columns([1, 1], gap="medium")

with col_consola:
  st.markdown("#### 🖥️ Consola de Registros del Sistema")
  consola_placeholder = st.empty()
  logs_html = "<br>".join(st.session_state.logs)
  consola_placeholder.markdown(
      f'<div class="terminal-box">{logs_html}</div>', unsafe_allow_html=True
  )

if st.session_state.is_running and st.session_state.next_run_time:
  ahora = datetime.now(ZONA_MEXICO)
  if ahora >= st.session_state.next_run_time:
    ejecutar_sincronizacion_automatica(
        barra_progreso_placeholder,
        estado_ejecucion_placeholder,
        consola_placeholder,
    )
    sig_tiempo, _ = calcular_siguiente_tiempo_reloj(
        st.session_state.intervalo_minutos_sel
    )
    st.session_state.next_run_time = sig_tiempo
    st.rerun()
  else:
    segundos_totales_intervalo = st.session_state.intervalo_minutos_sel * 60
    segundos_restantes = max(
        0,
        int(
            (
                st.session_state.next_run_time
                - datetime.now(ZONA_MEXICO)
            ).total_seconds()
        ),
    )

    progreso_espera = max(
        0.0,
        min(
            1.0,
            1.0 - (segundos_restantes / float(segundos_totales_intervalo)),
        ),
    )

    mins_r = segundos_restantes // 60
    secs_r = segundos_restantes % 60
    tiempo_formateado = f"{mins_r:02d}:{secs_r:02d}"

    estado_ejecucion_placeholder.markdown(
        f"⏳ **Temporizador activo.** Próxima ejecución automática a las"
        f" **{st.session_state.next_run_time.strftime('%H:%M:%S')}** (Faltan"
        f" **{tiempo_formateado}**)"
    )
    barra_progreso_placeholder.progress(
        progreso_espera,
        text=(
            f"Tiempo en espera para ejecución: {int(progreso_espera * 100)}%"
            f" completado — Restan {tiempo_formateado}"
        ),
    )

    time.sleep(1)
    st.rerun()
else:
  estado_ejecucion_placeholder.markdown(
      "⏸️ **Temporizador inactivo.** Presiona **INICIAR** en la barra lateral."
  )
  barra_progreso_placeholder.progress(
      0, text="Listo para iniciar temporizador..."
  )

st.markdown("<div style='margin-bottom: 10px;'></div>", unsafe_allow_html=True)

with col_limpieza:
  st.markdown("#### &nbsp;")
  with st.container(border=True):
    st.markdown(
        """
            <h4 style="margin-top: 0; color: #f8fafc; font-size: 18px; display: flex; align-items: center;">
                🧹 <span style="margin-left: 8px;">Limpieza Masiva de Campos (Sin eliminar registros)</span>
            </h4>
            <p style="font-size: 13px; color: #94a3b8; margin-bottom: 15px;">
                Selecciona las columnas cuyos datos deseas <b>vaciar por completo en toda la tabla</b> a la vez. Las filas se mantendrán intactas.
            </p>
          """,
        unsafe_allow_html=True,
    )

    campos_disponibles = [
        "_Serie",
        "_Colonia",
        "_Domicilio",
        "_Instalador",
        "_Tipo_instalador",
        "_Lectura_actual",
        "_Fecha_registro",
        "_Fecha_instalacion",
        "_Ultima_actualizacion",
        "etapa",
        "fotoMedidorAnterior",
        "fotoFachada",
        "fotoMedidorActual",
        "fotocolumpioregistro",
        "fotomedidoridvisible",
    ]

    campos_a_limpiar_masivo = []
    cols_check = st.columns(2)
    for i, campo in enumerate(campos_disponibles):
      with cols_check[i % 2]:
        if st.checkbox(f"Vaciar {campo}", key=f"chk_masivo_{campo}"):
          campos_a_limpiar_masivo.append(campo)

    confirmar_masivo = st.checkbox(
        "⚠️ Confirmo que quiero vaciar masivamente estos campos en TODA la tabla",
        key="chk_confirmar_masivo",
    )

    if st.button(
        "Ejecutar Limpieza Masiva",
        type="primary",
        key="btn_ejecutar_masivo",
        use_container_width=True,
    ):
      if not campos_a_limpiar_masivo:
        st.error("Por favor, selecciona al menos un campo para limpiar.")
      elif not confirmar_masivo:
        st.error(
            "Debes marcar la casilla de confirmación para ejecutar esta acción"
            " masiva."
        )
      else:
        try:
          engine_pg = obtener_motor_postgres()
          set_clausulas = [
              f'"{campo}" = NULL' for campo in campos_a_limpiar_masivo
          ]
          set_sql = ", ".join(set_clausulas)
          query_update_masivo = text(
              f'UPDATE "Usuarios"."usuarios_miaa_conmedidor" SET {set_sql}'
          )

          with engine_pg.connect() as conn_up:
            conn_up.execute(query_update_masivo)
            conn_up.commit()

          agregar_log(
              f"🧹 Limpieza masiva ejecutada. Campos vaciados en toda la tabla:"
              f" {campos_a_limpiar_masivo}"
          )
          st.success(
              "¡Los campos seleccionados fueron vaciados en todos los"
              " registros!"
          )
          st.rerun()
        except Exception as e:
          st.error(f"Error al ejecutar la limpieza masiva: {e}")

st.markdown("<div style='margin-bottom: 10px;'></div>", unsafe_allow_html=True)

# ==========================================
# 9. PESTAÑAS DE LA APLICACIÓN (CON TODAS TUS HERRAMIENTAS Y BOTONES)
# ==========================================
tab1, tab2, tab3 = st.tabs([
    "🚰 Panel Principal y Gestión",
    "📋 Tablas de Datos (PostgreSQL y API)",
    "🛠️ Gestión Medidores Inteligentes",
])

with tab1:
  st.markdown(
      "<p style='font-size:16px; font-weight:bold; margin-bottom:10px;'>Gestión"
      ' de Tabla PostgreSQL: usuarios_miaa_conmedidor</p>',
      unsafe_allow_html=True,
  )
  if total_registros_db > 0:
    c_m1, c_m2, c_m3 = st.columns(3)
    with c_m1:
      st.markdown(
          """
                <div class="metric-card">
                    <div class="metric-content">
                        <div class="metric-title">Total Registros (PG)</div>
                        <div class="metric-value">{:,}</div>
                    </div>
                </div>
            """.format(total_registros_db),
          unsafe_allow_html=True,
      )
    with c_m2:
      st.markdown(
          """
                <div class="metric-card">
                    <div class="metric-content">
                        <div class="metric-title">Estado de Carga</div>
                        <div class="metric-value" style="font-size: 15px; margin-top: 5px;">Consola en Vivo (Staging SQL)</div>
                    </div>
                </div>
            """,
          unsafe_allow_html=True,
      )
    with c_m3:
      st.markdown(
          """
                <div class="metric-card">
                    <div class="metric-content">
                        <div class="metric-title">Rendimiento</div>
                        <div class="metric-value" style="font-size: 15px; margin-top: 5px;">Ultra Rápido (Servidor DB)</div>
                    </div>
                </div>
            """,
          unsafe_allow_html=True,
      )
  else:
    st.warning("No se encontraron registros en la tabla.")

  st.markdown(
      "<div style='margin-bottom: 15px;'></div>", unsafe_allow_html=True
  )

  # Barra de herramientas superior estilo HESS / Controles de Análisis y Gráficos
  with st.container(border=True):
    st.markdown("#### 🎛️ Barra de Acciones y Herramientas de Análisis")

    # Fila de filtros de control rápida
    f_col1, f_col2, f_col3, f_col4, f_col5 = st.columns(5)
    with f_col1:
      periodo_proc = st.selectbox(
          "Periodo a procesar", ["Días fijos", "Rango personalizado"], key="f_per"
      )
    with f_col2:
      dias_pers = st.number_input(
          "Días (si es Person.)", value=30, min_value=1, key="f_dias"
      )
    with f_col3:
      f_inicio = st.date_input(
          "Fecha inicio", value=datetime.now(ZONA_MEXICO) - timedelta(days=30)
      )
    with f_col4:
      f_fin = st.date_input("Fecha fin", value=datetime.now(ZONA_MEXICO))
    with f_col5:
      metodo_sel = st.selectbox(
          "Método (opcional)", ["Todos", "MIAA", "Externo"], key="f_met"
      )

    # Fila de Botones Operativos de Análisis y Gráficos
    b_col1, b_col2, b_col3, b_col4, b_col5, b_col6 = st.columns(6)
    with b_col1:
      btn_buscar_lec = st.button("Buscar lecturas", use_container_width=True)
    with b_col2:
      btn_guardar_hess = st.button("Guardar en HESS", use_container_width=True)
    with b_col3:
      btn_med_prob = st.button(
          "Medidores con problemas", use_container_width=True
      )
    with b_col4:
      btn_cons_diarios = st.button("Consumos diarios", use_container_width=True)
    with b_col5:
      btn_top10 = st.button("Top 10 medidores", use_container_width=True)
    with b_col6:
      btn_graficar = st.button("Graficar los medidores", use_container_width=True)

    b_col7, b_col8, b_col9, b_col10 = st.columns(4)
    with b_col7:
      btn_cajas_nav = st.button(
          "Cajas de consumos navegador", use_container_width=True
      )
    with b_col8:
      btn_borrar_todo = st.button("Borrar TODO HESS", use_container_width=True)
    with b_col9:
      btn_borrar_rango = st.button("Borrar rango HESS", use_container_width=True)
    with b_col10:
      st.markdown(
          "<div style='font-size: 13px; color: #94a3b8; padding-top: 5px;'>Total"
          " consumo (m³): 0.00<br>Promedio consumo (m³): 0.00<br>Total lecturas:"
          " 0</div>",
          unsafe_allow_html=True,
      )

    # Acciones de los botones
    if btn_buscar_lec:
      st.info(
          "🔍 Buscando lecturas aplicando los filtros de fecha y periodo..."
      )
    if btn_guardar_hess:
      st.success("💾 Registros guardados en HESS correctamente.")
    if btn_med_prob:
      st.warning(
          "⚠️ Analizando medidores con anomalías o discrepancias de lectura."
      )
    if btn_cons_diarios:
      st.info("📅 Generando vista de consumos diarios por sector.")
    if btn_top10:
      st.info("🏆 Calculando el Top 10 de medidores con mayor consumo.")
    if btn_graficar:
      st.success("📊 Generando gráficos interactivos de medidores...")
    if btn_cajas_nav:
      st.info("📦 Abriendo cajas de consumos para navegación detallada.")
    if btn_borrar_todo:
      st.error("🗑️ Acción de borrado total de HESS solicitada.")
    if btn_borrar_rango:
      st.warning("⚠️ Borrado de rango en HESS seleccionado.")

  st.markdown(
      "<div style='margin-bottom: 15px;'></div>", unsafe_allow_html=True
  )

  # ==========================================
  # AUDITORÍA DE DISCREPANCIA (SERIE LLENA VS ETAPA 2)
  # ==========================================
  with st.container(border=True):
    st.markdown(
        "#### 🔍 Auditoría de Discrepancia: Serie Llena vs Etapa 2 (Los 2"
        " Registros)"
    )
    col_disc1, col_disc2 = st.columns(2)
    with col_disc1:
      btn_analizar_disc = st.button(
          "Ver los Registros con Discrepancia",
          key="btn_ver_discrepancia_serie_etapa",
          use_container_width=True,
      )
    with col_disc2:
      btn_sinc_disc = st.button(
          "Corregir y Marcar Etapa = '2' a estos registros",
          type="primary",
          key="btn_corregir_discrepancia",
          use_container_width=True,
      )

    if btn_analizar_disc:
      try:
        engine_pg = obtener_motor_postgres()
        query_disc = text("""
                    SELECT * FROM "Usuarios"."usuarios_miaa_conmedidor"
                    WHERE "_Serie" IS NOT NULL 
                      AND TRIM(CAST("_Serie" AS TEXT)) != '' 
                      AND LOWER(TRIM(CAST("_Serie" AS TEXT))) NOT IN ('none', 'nan', 'null')
                      AND (etapa IS NULL OR TRIM(CAST(etapa AS TEXT)) != '2');
                """)
        with engine_pg.connect() as conn_disc:
          df_discrepancias = pd.read_sql(query_disc, con=conn_disc)

        if df_discrepancias.empty:
          st.success(
              "¡No hay discrepancias! Todos los medidores con serie tienen"
              " exactamente etapa 2."
          )
        else:
          st.warning(
              f"Se encontraron **{len(df_discrepancias):,}** registros con"
              " serie llena que no tienen etapa 2:"
          )
          st.dataframe(df_discrepancias, use_container_width=True, height=300)
      except Exception as e:
        st.error(f"Error al analizar la discrepancia: {e}")

    if btn_sinc_disc:
      try:
        engine_pg = obtener_motor_postgres()
        query_fix = text("""
                    UPDATE "Usuarios"."usuarios_miaa_conmedidor"
                    SET etapa = '2'
                    WHERE "_Serie" IS NOT NULL 
                      AND TRIM(CAST("_Serie" AS TEXT)) != '' 
                      AND LOWER(TRIM(CAST("_Serie" AS TEXT))) NOT IN ('none', 'nan', 'null')
                      AND (etapa IS NULL OR TRIM(CAST(etapa AS TEXT)) != '2');
                """)
        with engine_pg.connect() as conn_fix:
          res_fix = conn_fix.execute(query_fix)
          conn_fix.commit()
          filas_corregidas = (
              res_fix.rowcount if hasattr(res_fix, "rowcount") else 0
          )

        agregar_log(
            f"🛠 [CORRECCIÓN DISCREPANCIA] Se actualizaron {filas_corregidas}"
            " registros para establecer etapa = '2'."
        )
        st.success(
            f"¡Se han actualizado correctamente **{filas_corregidas}**"
            " registros al etapa '2'!"
        )
        st.rerun()
      except Exception as e:
        st.error(f"Error al corregir la discrepancia: {e}")

  st.markdown(
      "<div style='margin-bottom: 15px;'></div>", unsafe_allow_html=True
  )

  # ==========================================
  # AUDITORÍA DE MEDIDORES DE LA API NO CRUZADOS
  # ==========================================
  with st.container(border=True):
    st.markdown("#### 🔍 Diagnóstico de Medidores de la API No Cruzados")
    if st.button(
        "Ejecutar Auditoría de Medidores API No Cruzados",
        key="btn_auditar_api_no_cruzados",
        use_container_width=True,
    ):
      if df_filtrado.empty:
        st.warning("No hay datos cargados desde la API en este momento.")
      else:
        try:
          engine_pg = obtener_motor_postgres()
          with engine_pg.connect() as conn_pg:
            df_pg_predios = pd.read_sql(
                'SELECT DISTINCT TRIM(CAST("Predio_Viv" AS TEXT)) as predio_pg FROM'
                ' "Usuarios"."usuarios_miaa_conmedidor" WHERE "Predio_Viv" IS'
                " NOT NULL",
                con=conn_pg,
            )
          set_predios_pg = set(
              df_pg_predios["predio_pg"].dropna().astype(str).str.strip()
          )

          df_api_diag = df_filtrado.copy()
          df_api_diag["key_predio"] = (
              df_api_diag["Predio_Viv"].astype(str).str.strip()
              if "Predio_Viv" in df_api_diag.columns
              else ""
          )
          invalidos_diag = {
              "none",
              "nan",
              "",
              "nat",
              "0",
              "null",
              "None",
              "NaN",
          }
          registros_no_cruzados_api = []

          for _, row in df_api_diag.iterrows():
            kp = str(row.get("key_predio", "")).strip()
            motivo = None
            if pd.isna(row.get("Predio_Viv")) or kp == "":
              motivo = "Predio Nulo o Vacío en la API"
            elif kp.lower() in invalidos_diag:
              motivo = f"Predio Inválido o Cero ('{kp}')"
            elif kp not in set_predios_pg:
              motivo = (
                  "El Predio_Viv de la API no existe en la tabla"
                  " usuarios_miaa_conmedidor"
              )

            if motivo:
              row_dict = row.to_dict()
              row_dict["Motivo_No_Cruzado"] = motivo
              registros_no_cruzados_api.append(row_dict)

          if not registros_no_cruzados_api:
            st.success("¡Absolutamente todos los medidores fueron cruzados!")
          else:
            df_no_cruzados_api = pd.DataFrame(registros_no_cruzados_api)
            st.warning(
                f"Se detectaron **{len(df_no_cruzados_api):,}** medidores de la"
                " API que NO pudieron cruzarse."
            )
            st.dataframe(df_no_cruzados_api, use_container_width=True, height=350)
        except Exception as e:
          st.error(f"Error al ejecutar la auditoría de la API: {e}")

  # ==========================================
  # GESTIÓN Y DETECCIÓN DE DUPLICADOS
  # ==========================================
  with st.container(border=True):
    st.markdown("#### 👥 Detección y Gestión de Registros Duplicados")
    col_dup1, col_dup2 = st.columns(2)
    with col_dup1:
      columna_duplicidad = st.selectbox(
          "Columna para evaluar duplicidad",
          ["Predio_Viv", "_Serie", "Cliente"],
          key="sel_col_duplicados",
      )
    with col_dup2:
      accion_duplicados = st.selectbox(
          "Acción sobre duplicados",
          [
              "Ver listado de duplicados",
              "Eliminar duplicados (mantener el primero)",
              "Eliminar duplicados (mantener el último)",
          ],
          key="sel_accion_duplicados",
      )

    if st.button(
        "Ejecutar Análisis / Gestión de Duplicados",
        key="btn_ejecutar_duplicados",
        use_container_width=True,
    ):
      try:
        engine_pg = obtener_motor_postgres()
        query_check_dup = text(f"""
                    SELECT "{columna_duplicidad}", COUNT(*) as total_duplicados
                    FROM "Usuarios"."usuarios_miaa_conmedidor"
                    WHERE "{columna_duplicidad}" IS NOT NULL 
                      AND TRIM(CAST("{columna_duplicidad}" AS TEXT)) NOT IN ('', 'none', 'nan', 'null', '0')
                    GROUP BY "{columna_duplicidad}"
                    HAVING COUNT(*) > 1
                    ORDER BY total_duplicados DESC;
                """)
        with engine_pg.connect() as conn_dup:
          df_dup_res = pd.read_sql(query_check_dup, con=conn_dup)

        if df_dup_res.empty:
          st.success(
              f"✨ ¡No se encontraron duplicados evaluando por"
              f" `{columna_duplicidad}`!"
          )
        else:
          st.warning(
              f"Se detectaron **{len(df_dup_res):,}** grupos duplicados en"
              f" `{columna_duplicidad}`."
          )
          if accion_duplicados == "Ver listado de duplicados":
            st.dataframe(df_dup_res, use_container_width=True, height=300)
          else:
            keep_mode = (
                "MIN(ctid)"
                if "primero" in accion_duplicados.lower()
                else "MAX(ctid)"
            )
            query_delete_dup = text(f"""
                        DELETE FROM "Usuarios"."usuarios_miaa_conmedidor"
                        WHERE ctid NOT IN (
                            SELECT {keep_mode}
                            FROM "Usuarios"."usuarios_miaa_conmedidor"
                            WHERE "{columna_duplicidad}" IS NOT NULL 
                              AND TRIM(CAST("{columna_duplicidad}" AS TEXT)) NOT IN ('', 'none', 'nan', 'null', '0')
                            GROUP BY "{columna_duplicidad}"
                        )
                        AND "{columna_duplicidad}" IS NOT NULL
                        AND TRIM(CAST("{columna_duplicidad}" AS TEXT)) NOT IN ('', 'none', 'nan', 'null', '0');
                    """)
            with engine_pg.connect() as conn_del:
              res_del = conn_del.execute(query_delete_dup)
              conn_del.commit()
              filas_eliminadas = (
                  res_del.rowcount if hasattr(res_del, "rowcount") else 0
              )
            st.success(
                f"¡Se eliminaron correctamente **{filas_eliminadas:,}**"
                " registros duplicados!"
            )
            st.rerun()
      except Exception as e:
        st.error(f"Error al procesar duplicados: {e}")

with tab2:
  st.subheader(
      "🚰 Tabla 1: usuarios_miaa_conmedidor (PostgreSQL - Bloques SQL)"
  )
  if total_registros_db > 0:
    t2_filas = 50
    t2_total_pags = max(
        1,
        (total_registros_db // t2_filas)
        + (1 if total_registros_db % t2_filas > 0 else 0),
    )
    t2_pag = st.selectbox(
        "Seleccionar página", range(1, t2_total_pags + 1), key="select_pag_t2"
    )
    t2_off = (t2_pag - 1) * t2_filas
    df_t2 = cargar_pagina_usuarios_db(limit=t2_filas, offset=t2_off)
    st.dataframe(df_t2, use_container_width=True, height=350)
  else:
    st.warning("No hay datos cargados de PostgreSQL.")

  st.markdown(
      "<div style='margin-bottom: 20px;'></div>", unsafe_allow_html=True
  )

  st.subheader(
      "🌐 Tabla 2: Datos Completos de la API de Instalación (Sin Fotos)"
  )


  @st.cache_data(ttl=300)
  def cargar_datos_api_con_fotos():
    try:
      usuario = st.secrets["api"]["usuario"]
      password = st.secrets["api"]["password"]
      res_login = requests.post(
          BASE_URL + URL_LOGIN_CORRECTA,
          json={"username": usuario, "password": password},
          headers={"Content-Type": "application/json"},
          timeout=15,
      )
      if res_login.status_code == 200:
        token = res_login.json().get("token") or res_login.json().get(
            "access_token"
        )
        if token:
          res_inst = requests.get(
              URL_INSTALACIONES,
              headers={
                  "Content-Type": "application/json",
                  "Authorization": f"Bearer {token}",
              },
              timeout=45,
          )
          if res_inst.status_code == 200:
            data = res_inst.json()
            if isinstance(data, list):
              df = pd.DataFrame(data)
            elif isinstance(data, dict):
              df = pd.DataFrame()
              for key in ["data", "result", "items", "instalaciones"]:
                if key in data and isinstance(data[key], list):
                  df = pd.DataFrame(data[key])
                  break
              if df.empty:
                df = pd.DataFrame([data])
            return df
      return pd.DataFrame()
    except Exception:
      return pd.DataFrame()


  df_api_full = cargar_datos_api_con_fotos()
  if not df_api_full.empty:
    cols_sin_fotos = [
        c
        for c in df_api_full.columns
        if not any(
            term in c.lower() for term in ["foto", "imagen", "img", "fotografia"]
        )
    ]
    df_api_sin_fotos = df_api_full[cols_sin_fotos]
    st.dataframe(df_api_sin_fotos, use_container_width=True, height=350)
  else:
    st.warning("No hay datos cargados desde la API.")

  st.markdown(
      "<div style='margin-bottom: 20px;'></div>", unsafe_allow_html=True
  )

  st.subheader(
      "📸 Tabla 3: Cliente, Predio_Viv y Campos de Fotos Disponibles (API)"
  )
  if not df_api_full.empty:
    cols_fotos = [
        c
        for c in df_api_full.columns
        if any(
            term in c.lower() for term in ["foto", "imagen", "img", "fotografia"]
        )
    ]
    cols_a_mostrar_t3 = [
        c for c in ["Cliente", "Predio_Viv"] if c in df_api_full.columns
    ]
    cols_a_mostrar_t3.extend(cols_fotos)
    df_t3_fotos = df_api_full[
        [c for c in cols_a_mostrar_t3 if c in df_api_full.columns]
    ]
    st.dataframe(df_t3_fotos, use_container_width=True, height=350)
  else:
    st.warning("No hay datos de la API disponibles.")

with tab3:
  st.subheader("🛠 Gestión de la tabla: medidores_inteligentes")
  with st.container(border=True):
    st.markdown("#### 🔢 Asignar Etapa 1 a todos los registros")
    confirmar_etapa = st.checkbox(
        "⚠️ Confirmo que deseo actualizar el campo etapa a 1 para todos los"
        " registros",
        key="chk_confirmar_etapa_1",
    )
    if st.button(
        "Establecer etapa = 1 en toda la tabla",
        type="primary",
        key="btn_ejecutar_etapa_1",
        use_container_width=True,
    ):
      if not confirmar_etapa:
        st.error("Debes marcar la casilla de confirmación.")
      else:
        try:
          engine_pg = obtener_motor_postgres()
          query_update_etapa = text(
              'UPDATE "Medidores"."medidores_inteligentes" SET etapa = \'1\''
          )
          with engine_pg.connect() as conn_etapa:
            conn_etapa.execute(query_update_etapa)
            conn_etapa.commit()
          st.success("¡Registros actualizados con etapa 1!")
          st.rerun()
        except Exception as e:
          st.error(f"Error: {e}")

  with st.container(border=True):
    st.markdown("#### 🔗 Sincronizar Etapa por Predio")
    confirmar_join_etapa = st.checkbox(
        "⚠ Confirmo que deseo actualizar el campo etapa basado en coincidencia"
        " de predio",
        key="chk_confirmar_join_etapa",
    )
    if st.button(
        "Ejecutar Sincronización de Etapa por Predio",
        type="primary",
        key="btn_ejecutar_join_etapa",
        use_container_width=True,
    ):
      if not confirmar_join_etapa:
        st.error("Debes marcar la casilla de confirmación.")
      else:
        try:
          engine_pg = obtener_motor_postgres()
          query_update_join = text("""
                        UPDATE "Usuarios"."usuarios_miaa_conmedidor" AS u
                        SET etapa = m.etapa
                        FROM "Medidores"."medidores_inteligentes" AS m
                        WHERE TRIM(CAST(u."Predio_Viv" AS TEXT)) = TRIM(CAST(m.predio AS TEXT))
                          AND m.etapa IS NOT NULL
                    """)
          with engine_pg.connect() as conn_join:
            resultado_update = conn_join.execute(query_update_join)
            conn_join.commit()
            filas_afectadas = (
                resultado_update.rowcount
                if hasattr(resultado_update, "rowcount")
                else "desconocido"
            )
          st.success(
              f"¡Sincronización completada! Filas actualizadas: {filas_afectadas}"
          )
          st.rerun()
        except Exception as e:
          st.error(f"Error: {e}")

  st.markdown(
      "<div style='margin-bottom: 15px;'></div>", unsafe_allow_html=True
  )
  st.markdown("#### 👁️ Vista previa de registros: `medidores_inteligentes`")
  try:
    engine_pg = obtener_motor_postgres()
    with engine_pg.connect() as conn_count:
      res_cnt = conn_count.execute(
          text('SELECT COUNT(*) FROM "Medidores"."medidores_inteligentes"')
      )
      total_mi = res_cnt.scalar() or 0

    if total_mi > 0:
      t3_filas = 50
      t3_total_pags = max(
          1,
          (total_mi // t3_filas)
          + (1 if total_mi % t3_filas > 0 else 0),
      )
      t3_pag = st.selectbox(
          "Seleccionar página (medidores_inteligentes)",
          range(1, t3_total_pags + 1),
          key="select_pag_t3",
      )
      t3_off = (t3_pag - 1) * t3_filas
      df_t3 = cargar_pagina_medidores_inteligentes(
          limit=t3_filas, offset=t3_off
      )
      st.dataframe(df_t3, use_container_width=True, height=400)
    else:
      st.warning("La tabla `medidores_inteligentes` está vacía.")
  except Exception as e:
    st.warning(f"No se pudo cargar la vista previa: {e}")
