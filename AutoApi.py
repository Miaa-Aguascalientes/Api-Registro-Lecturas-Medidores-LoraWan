import requests
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import mysql.connector
import threading
from queue import Queue
import re
import unicodedata
from collections import defaultdict
import time
import openpyxl
from tkcalendar import DateEntry

# Importaciones para gráficas (requiere 'pip install matplotlib')
try:
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure
    MATPLOTLIB_INSTALLED = True
except ImportError:
    MATPLOTLIB_INSTALLED = False

# Importación para gráficas interactivas en navegador
try:
    import plotly.graph_objects as go
    import plotly.express as px
    import pandas as pd
    import webbrowser
    import tempfile
    import os
    PLOTLY_INSTALLED = True
except ImportError:
    PLOTLY_INSTALLED = False

# Cambiar a True para ver prints de depuración en consola
DEBUG = True

# Variables Globales (Para los hilos y cancelación)
PADRON_CACHE = ({}, {})
GPS_CACHE = {}
GRAPH_CANVAS = None
GRAPH_TOOLBAR = None

# --- Variables de control para el servicio silencioso/diario ---
SILENCIOSO_THREAD = None
SILENCIOSO_STOP_EVENT = threading.Event()
SILENCIOSO_STATUS_LABEL = None
SILENCIOSO_PROGRESS_BAR = None

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

# -------------------- Funciones de Soporte de Fechas --------------------
def get_primer_dia_mes(dt):
    return dt.replace(day=1)

def get_ultimo_dia_mes(dt):
    next_month = dt.replace(day=28) + timedelta(days=4)
    return next_month - timedelta(days=next_month.day)

# ---------------- Autenticación y obtención desde API ----------------
def login_api(max_retries=3, initial_delay=1):
    login_url = "https://prelec.miaa.mx/auth/login"
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": "com.wms.mscv.lecturas",
    }
    login_payload = {
        "username": "pedro.templos@miaa.mx",
        "password": "Pedro02081984"
    }
    
    for attempt in range(max_retries):
        try:
            resp = requests.post(login_url, json=login_payload, headers=headers, timeout=20)
            if resp.status_code == 200:
                token = resp.json().get("token")
                if not token:
                    if not SILENCIOSO_STOP_EVENT.is_set():
                        messagebox.showerror("Error", "No se recibió token de autenticación.")
                    return None
                return f"Bearer {token}"
            else:
                if attempt < max_retries - 1:
                    if DEBUG: print(f"Intento {attempt + 1} de login fallido con código {resp.status_code}. Reintentando en {initial_delay}s...")
                    time.sleep(initial_delay)
                else:
                    if not SILENCIOSO_STOP_EVENT.is_set():
                        messagebox.showerror("Error de login", f"{resp.status_code} - {resp.text}")
                    return None
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                if DEBUG: print(f"Intento {attempt + 1} de conexión fallido. Reintentando en {initial_delay}s...")
                time.sleep(initial_delay)
            else:
                if not SILENCIOSO_STOP_EVENT.is_set():
                    messagebox.showerror("Error de login", f"No se pudo llamar al servicio de login después de {max_retries} intentos:\n{e}")
                return None
    return None

def obtener_lecturas_completas(fecha_inicio, fecha_fin, root_ref=None):
    token = login_api()
    if not token:
        if root_ref and hasattr(root_ref, 'lbl_api_status'):
            root_ref.after(0, lambda: root_ref.lbl_api_status.config(text="API: Error de Login ❌", foreground="red"))
        return None
        
    lectura_url = "https://prelec.miaa.mx/msvc-lecturas/lecturas/getLecturasInteligentes"
    
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": "com.wms.mscv.lecturas",
        "Authorization": token
    }
    
    todas_las_lecturas = []
    page = 0
    size = 500  # Tamaño de página optimizado para el servidor
    
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
            if DEBUG: print(f"API Request URL: {response.url} | Status: {response.status_code}")
            
            if response.status_code != 200:
                if root_ref and hasattr(root_ref, 'lbl_api_status'):
                    root_ref.after(0, lambda: root_ref.lbl_api_status.config(text=f"API: Error HTTP {response.status_code} ❌", foreground="red"))
                break
                
            data = response.json()
            
            items_pagina = []
            if isinstance(data, dict):
                items_pagina = data.get("content", data.get("data", []))
            elif isinstance(data, list):
                items_pagina = data
                
            if not items_pagina:
                break
                
            todas_las_lecturas.extend(items_pagina)
            if DEBUG: print(f"Página {page}: {len(items_pagina)} registros cargados. Total acumulado: {len(todas_las_lecturas)}")
            
            if len(items_pagina) < size or (isinstance(data, dict) and data.get("last") is True):
                break
                
            page += 1

        if root_ref and hasattr(root_ref, 'lbl_api_status'):
            root_ref.after(0, lambda: root_ref.lbl_api_status.config(text="API: Conectado (Status 200) ✅", foreground="green"))

        if not todas_las_lecturas:
            if not SILENCIOSO_STOP_EVENT.is_set():
                messagebox.showinfo("Aviso", "No se encontraron datos en la API para el rango seleccionado.")
            return None
            
        return todas_las_lecturas
        
    except Exception as e:
        if DEBUG: print(f"Excepción general en obtener_lecturas_completas: {e}")
        if root_ref and hasattr(root_ref, 'lbl_api_status'):
            root_ref.after(0, lambda: root_ref.lbl_api_status.config(text="API: Excepción de Conexión ❌", foreground="red"))
        return None

# -------------------- GPS y Padrón TABLA WASION (MySQL) --------------------
def obtener_lat_long():
    global GPS_CACHE
    if GPS_CACHE:
        return GPS_CACHE
    try:
        conn = mysql.connector.connect(
            host="qgiscon.miaa.mx",
            port=3306,
            database="gps",
            user="pozos",
            password='8pU?"X_64(5H7Og4',
            charset='utf8mb4',
            collation='utf8mb4_general_ci',
            use_pure=True
        )
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT serie, latitud, longitud FROM medidores")
        resultados = cursor.fetchall()
        cursor.close()
        conn.close()
        
        GPS_CACHE = {
            _canon_numeric(row["serie"]): {
                "Latitud": row["latitud"],
                "Longitud": row["longitud"]
            } for row in resultados
        }
        return GPS_CACHE
        
    except Exception as e:
        if DEBUG:
            print("Error obtener_lat_long (MySQL GPS):", e)
        return {}

def obtener_info_padron():
    global PADRON_CACHE
    if PADRON_CACHE[0] or PADRON_CACHE[1]:
        return PADRON_CACHE
        
    conn = None
    try:
        conn = mysql.connector.connect(
            host="miaa.mx",
            database="miaamx_telemetria2",
            user="miaamx_telemetria2",
            password="bWkrw1Uum1O&",
            charset="utf8mb4",
            collation="utf8mb4_general_ci",
            use_pure=True
        )
        cursor = conn.cursor(dictionary=True)
        query = ("""SELECT Cliente, Medidor, Nombre, Domicilio, Colonia, Nivel, `Predio`,
                Situacion_comercial, Giro, Marca, Modelo, Primer_instalacion, Distrito, Sector
        FROM WASION""")
        cursor.execute(query)
        resultados = cursor.fetchall()
        cursor.close()

        by_cliente = {}
        by_medidor = {}

        for row in resultados:
            cli = _canon_numeric(row.get("Cliente"))
            med = _canon_numeric(row.get("Medidor"))
            datos = {
                "Cliente": cli,
                "Medidor": med,
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
            if cli:
                by_cliente[cli] = datos
            if med:
                by_medidor[med] = datos
        
        PADRON_CACHE = (by_cliente, by_medidor)
        return PADRON_CACHE

    except Exception as e:
        if DEBUG:
            print("Error obtener_info_padron (MySQL Padrón):", e)
        PADRON_CACHE = ({}, {})
        return PADRON_CACHE
    finally:
        if conn and conn.is_connected():
            conn.close()

# -------------------- MEDIDORES CON PROBLEMAS --------------------
def analizar_medidores_problemas(root, fecha_inicio_ref, fecha_fin_ref):
    fecha_inicio = fecha_inicio_ref.get().strip()
    fecha_fin = fecha_fin_ref.get().strip()
    
    if not fecha_inicio or not fecha_fin:
        messagebox.showerror("Error", "Las fechas de inicio y fin no pueden estar vacías.")
        return
    
    try:
        fecha_inicio_dt = datetime.strptime(fecha_inicio, "%Y-%m-%d").date()
        fecha_fin_dt = datetime.strptime(fecha_fin, "%Y-%m-%d").date()
    except ValueError:
        messagebox.showerror("Error", "Formato de fecha inválido. Use AAAA-MM-DD.")
        return
    
    ventana_problemas = tk.Toplevel(root)
    ventana_problemas.title("Medidores con problemas")
    ventana_problemas.geometry("1400x700")
    
    notebook = ttk.Notebook(ventana_problemas)
    notebook.pack(fill="both", expand=True, padx=10, pady=10)
    
    frame_consumo_cero = ttk.Frame(notebook)
    frame_lecturas_trabadas = ttk.Frame(notebook)
    frame_sin_lectura_7dias = ttk.Frame(notebook)
    
    notebook.add(frame_consumo_cero, text="Consumo siempre cero")
    notebook.add(frame_lecturas_trabadas, text="Lecturas trabadas")
    notebook.add(frame_sin_lectura_7dias, text="Sin lectura últimos 7 días")
    
    columnas = ('Medidor', 'Nombre', 'Domicilio', 'Colonia', 'Sector', 'Tipo de problema', 'Consumo siempre cero', 'Última Lectura', 'Fecha Última Lectura')
    
    trees = {}
    for nombre_frame, frame in [
        ('consumo_cero', frame_consumo_cero),
        ('trabadas', frame_lecturas_trabadas),
        ('sin_lectura', frame_sin_lectura_7dias)
    ]:
        tree = ttk.Treeview(frame, columns=columnas, show='headings', height=20)
        for col in columnas:
            tree.heading(col, text=col)
            if col == 'Medidor':
                tree.column(col, width=100)
            elif col in ['Nombre', 'Domicilio']:
                tree.column(col, width=200)
            elif col == 'Colonia':
                tree.column(col, width=150)
            else:
                tree.column(col, width=120)
        
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        
        trees[nombre_frame] = tree
    
    frame_botones = ttk.Frame(ventana_problemas)
    frame_botones.pack(fill="x", padx=10, pady=5)
    
    btn_exportar = ttk.Button(frame_botones, text="Exportar a Excel", 
                              command=lambda: exportar_problemas_excel(trees, notebook))
    btn_exportar.pack(side="left", padx=5)
    
    lbl_status = ttk.Label(frame_botones, text="Analizando medidores...")
    lbl_status.pack(side="left", padx=20)
    
    def analizar_worker():
        try:
            fecha_inicio_api = (fecha_inicio_dt - timedelta(days=30)).strftime("%Y-%m-%d")
            fecha_fin_api = (fecha_fin_dt + timedelta(days=1)).strftime("%Y-%m-%d")
            
            data = obtener_lecturas_completas(fecha_inicio_api, fecha_fin_api, root_ref=root)
            if not data:
                if ventana_problemas.winfo_exists():
                    root.after(0, lambda: lbl_status.configure(text="No se encontraron datos"))
                return
            
            pad_by_cli, pad_by_med = obtener_info_padron()
            
            lecturas_por_medidor = defaultdict(list)
            for item in data:
                medidor_id = _canon_numeric(item.get("medidorId", item.get("medidor", item.get("serie", ""))))
                if not medidor_id:
                    continue
                
                fecha_raw = str(item.get("fecha", item.get("fechaLectura", item.get("createdDate", ""))))
                fecha_txt = fecha_raw.replace("T", " ")
                try:
                    fecha_dt = datetime.strptime(fecha_txt[:19], "%Y-%m-%d %H:%M:%S")
                except:
                    continue
                
                try:
                    lectura = float(item.get("lectura", item.get("valor", 0)) or 0)
                except:
                    lectura = 0.0
                
                lecturas_por_medidor[medidor_id].append({
                    'fecha': fecha_dt,
                    'lectura': lectura
                })
            
            for med in lecturas_por_medidor:
                lecturas_por_medidor[med].sort(key=lambda x: x['fecha'])
            
            medidores_consumo_cero = []
            medidores_trabados = []
            medidores_sin_lectura_7dias = []
            
            hoy = datetime.now().date()
            hace_7_dias = hoy - timedelta(days=7)
            
            todos_medidores = set(pad_by_med.keys())
            
            for medidor, lecturas in lecturas_por_medidor.items():
                if len(lecturas) < 2:
                    continue
                
                consumos = []
                for i in range(len(lecturas) - 1):
                    consumo = max(0, lecturas[i+1]['lectura'] - lecturas[i]['lectura'])
                    consumos.append(consumo)
                
                if consumos and all(c == 0 for c in consumos):
                    info = pad_by_med.get(medidor, {})
                    ultima_lectura = lecturas[-1]['lectura']
                    fecha_ultima = lecturas[-1]['fecha'].strftime('%Y-%m-%d %H:%M:%S')
                    
                    medidores_consumo_cero.append({
                        'medidor': medidor,
                        'nombre': info.get('Nombre', 'SIN DATO'),
                        'domicilio': info.get('Domicilio', 'SIN DATO'),
                        'colonia': info.get('Colonia', 'SIN DATO'),
                        'sector': info.get('Sector', 'SIN DATO'),
                        'tipo_problema': 'Consumo siempre cero',
                        'consumo_cero': '0.00',
                        'ultima_lectura': f'{ultima_lectura:.2f}',
                        'fecha_ultima': fecha_ultima
                    })
            
            for medidor, lecturas in lecturas_por_medidor.items():
                if len(lecturas) < 3:
                    continue
                
                ultimas_lecturas = [l['lectura'] for l in lecturas[-5:]]
                if len(set(ultimas_lecturas)) == 1 and len(ultimas_lecturas) >= 3:
                    info = pad_by_med.get(medidor, {})
                    ultima_lectura = lecturas[-1]['lectura']
                    fecha_ultima = lecturas[-1]['fecha'].strftime('%Y-%m-%d %H:%M:%S')
                    
                    medidores_trabados.append({
                        'medidor': medidor,
                        'nombre': info.get('Nombre', 'SIN DATO'),
                        'domicilio': info.get('Domicilio', 'SIN DATO'),
                        'colonia': info.get('Colonia', 'SIN DATO'),
                        'sector': info.get('Sector', 'SIN DATO'),
                        'tipo_problema': 'Lectura Trabada',
                        'consumo_cero': '0.00',
                        'ultima_lectura': f'{ultima_lectura:.2f}',
                        'fecha_ultima': fecha_ultima
                    })
            
            for medidor in todos_medidores:
                if medidor in lecturas_por_medidor:
                    ultima_fecha = lecturas_por_medidor[medidor][-1]['fecha'].date()
                    if ultima_fecha < hace_7_dias:
                        info = pad_by_med.get(medidor, {})
                        ultima_lectura = lecturas_por_medidor[medidor][-1]['lectura']
                        fecha_ultima = lecturas_por_medidor[medidor][-1]['fecha'].strftime('%Y-%m-%d %H:%M:%S')
                        
                        medidores_sin_lectura_7dias.append({
                            'medidor': medidor,
                            'nombre': info.get('Nombre', 'SIN DATO'),
                            'domicilio': info.get('Domicilio', 'SIN DATO'),
                            'colonia': info.get('Colonia', 'SIN DATO'),
                            'sector': info.get('Sector', 'SIN DATO'),
                            'tipo_problema': 'Sin lectura > 7 días',
                            'consumo_cero': 'N/A',
                            'ultima_lectura': f'{ultima_lectura:.2f}',
                            'fecha_ultima': fecha_ultima
                        })
                else:
                    info = pad_by_med.get(medidor, {})
                    medidores_sin_lectura_7dias.append({
                        'medidor': medidor,
                        'nombre': info.get('Nombre', 'SIN DATO'),
                        'domicilio': info.get('Domicilio', 'SIN DATO'),
                        'colonia': info.get('Colonia', 'SIN DATO'),
                        'sector': info.get('Sector', 'SIN DATO'),
                        'tipo_problema': 'Sin lecturas reportadas',
                        'consumo_cero': 'N/A',
                        'ultima_lectura': 'N/A',
                        'fecha_ultima': 'N/A'
                    })
            
            if ventana_problemas.winfo_exists():
                root.after(0, lambda: actualizar_trees_problemas(
                    trees, medidores_consumo_cero, medidores_trabados, 
                    medidores_sin_lectura_7dias, lbl_status
                ))
            
        except Exception as e:
            if ventana_problemas.winfo_exists():
                root.after(0, lambda: messagebox.showerror("Error", f"Error al analizar medidores:\n{e}", parent=ventana_problemas))
    
    threading.Thread(target=analizar_worker, daemon=True).start()

def actualizar_trees_problemas(trees, consumo_cero, trabadas, sin_lectura, lbl_status):
    for tree in trees.values():
        for item in tree.get_children():
            tree.delete(item)
    
    for item in consumo_cero:
        trees['consumo_cero'].insert('', tk.END, values=(
            item['medidor'], item['nombre'], item['domicilio'], 
            item['colonia'], item['sector'], item['tipo_problema'],
            item['consumo_cero'], item['ultima_lectura'], item['fecha_ultima']
        ))
    
    for item in trabadas:
        trees['trabadas'].insert('', tk.END, values=(
            item['medidor'], item['nombre'], item['domicilio'], 
            item['colonia'], item['sector'], item['tipo_problema'],
            item['consumo_cero'], item['ultima_lectura'], item['fecha_ultima']
        ))
    
    for item in sin_lectura:
        trees['sin_lectura'].insert('', tk.END, values=(
            item['medidor'], item['nombre'], item['domicilio'], 
            item['colonia'], item['sector'], item['tipo_problema'],
            item['consumo_cero'], item['ultima_lectura'], item['fecha_ultima']
        ))
    
    total = len(consumo_cero) + len(trabadas) + len(sin_lectura)
    lbl_status.configure(text=f"Análisis completo. Total: {total} medidores con problemas")

def exportar_problemas_excel(trees, notebook):
    try:
        tab_actual = notebook.index(notebook.select())
        nombres_tabs = ['consumo_cero', 'trabadas', 'sin_lectura']
        tree_actual = trees[nombres_tabs[tab_actual]]
        
        archivo = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")],
            title="Guardar reporte de medidores con problemas"
        )
        
        if not archivo:
            return
        
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Medidores con problemas"
        
        columnas = tree_actual["columns"]
        for col_idx, col in enumerate(columnas, start=1):
            cell = ws.cell(row=1, column=col_idx)
            cell.value = col
            cell.font = openpyxl.styles.Font(bold=True)
        
        for row_idx, item_id in enumerate(tree_actual.get_children(), start=2):
            valores = tree_actual.item(item_id)["values"]
            for col_idx, valor in enumerate(valores, start=1):
                ws.cell(row=row_idx, column=col_idx, value=valor)
        
        wb.save(archivo)
        messagebox.showinfo("Éxito", f"Reporte exportado exitosamente a:\n{archivo}")
    except Exception as e:
        messagebox.showerror("Error", f"No se pudo exportar el archivo:\n{e}")

# -------------------- Funciones de Graficado (Matplotlib) --------------------
def _borrar_grafica(root):
    global GRAPH_CANVAS, GRAPH_TOOLBAR
    if GRAPH_CANVAS:
        GRAPH_CANVAS.get_tk_widget().destroy()
        GRAPH_CANVAS = None
    if GRAPH_TOOLBAR:
        GRAPH_TOOLBAR.destroy()
        GRAPH_TOOLBAR = None

def _dibujar_grafica(root, figure, graph_frame):
    global GRAPH_CANVAS, GRAPH_TOOLBAR
    _borrar_grafica(root)
    
    GRAPH_CANVAS = FigureCanvasTkAgg(figure, master=graph_frame)
    GRAPH_CANVAS.draw()
    GRAPH_CANVAS.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=1)

    GRAPH_TOOLBAR = NavigationToolbar2Tk(GRAPH_CANVAS, graph_frame)
    GRAPH_TOOLBAR.update()
    GRAPH_CANVAS.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=1)

def _leer_tabla_consumos(tree):
    por_medidor = defaultdict(list)
    MEDIDOR_INDEX = 3
    CONSUMO_DIARIO_INDEX = 20
    FECHA_INDEX = 1
    LECTURA_INDEX = 4
    
    for iid in tree.get_children():
        vals = tree.item(iid)["values"]
        if not vals or len(vals) < 21:
            continue
            
        fecha = str(vals[FECHA_INDEX])[:10]
        medidor = str(vals[MEDIDOR_INDEX])
        
        try:
            lectura = float(vals[LECTURA_INDEX])
        except Exception:
            lectura = 0.0
            
        consumo_reportado = 0.0
        try:
            consumo_reportado = float(vals[CONSUMO_DIARIO_INDEX]) 
        except Exception:
            consumo_reportado = 0.0
            
        por_medidor[medidor].append((fecha, lectura, consumo_reportado))
        
    for m in por_medidor:
        por_medidor[m].sort(key=lambda x: x[0]) 
    return por_medidor

def graficar_consumo(root, tree, graph_frame, entry_medidor):
    if not MATPLOTLIB_INSTALLED:
        messagebox.showerror("Error", "La librería Matplotlib no está instalada.")
        return
        
    por_medidor = _leer_tabla_consumos(tree)
    medidor_filtro = _canon_numeric(entry_medidor.get().strip())
    
    if medidor_filtro and medidor_filtro in por_medidor:
        lecturas = por_medidor[medidor_filtro]
        fechas = [datetime.strptime(l[0], '%Y-%m-%d') for l in lecturas]
        consumos = [float(str(l[2]).replace(',', '.')) for l in lecturas] 
        lecturas_val = [float(str(l[1]).replace(',', '.')) for l in lecturas] 

        fig = Figure(figsize=(8, 4), dpi=100)
        ax1 = fig.add_subplot(111)
        
        ax1.set_xlabel('Fecha')
        ax1.set_ylabel('Consumo Diario (m³)', color='tab:blue')
        ax1.plot(fechas, consumos, color='tab:blue', marker='o')
        ax1.grid(True, linestyle='--', alpha=0.7)

        ax2 = ax1.twinx()
        ax2.set_ylabel('Lectura Acumulada (m³)', color='tab:red')
        ax2.plot(fechas, lecturas_val, color='tab:red', linestyle='--', marker='x')

        fig.suptitle(f'Consumo y Lectura del Medidor: {medidor_filtro}')
        fig.autofmt_xdate()
        fig.tight_layout()
        
        _dibujar_grafica(root, fig, graph_frame)
        plt.close(fig)
    else:
        messagebox.showwarning("Aviso", "Escribe un medidor válido que esté visible en la tabla para graficar.")

def top10_medidores(root, tree, graph_frame):
    if not MATPLOTLIB_INSTALLED:
        messagebox.showerror("Error", "Matplotlib no está instalado.")
        return

    por_medidor = _leer_tabla_consumos(tree)
    if not por_medidor:
        messagebox.showwarning("Aviso", "No hay datos en la tabla para calcular el Top 10.")
        return

    consumo_total_por_medidor = {med: sum(l[2] for l in lects) for med, lects in por_medidor.items()}
    top_10 = sorted(consumo_total_por_medidor.items(), key=lambda item: item[1], reverse=True)[:10]

    _, pad_by_med = obtener_info_padron()
    medidores, consumos = [], []

    for medidor, consumo in top_10:
        medidores.append(medidor)
        consumos.append(consumo)

    fig = Figure(figsize=(8, 4), dpi=100)
    ax = fig.add_subplot(111)
    ax.barh(medidores, consumos, color='coral')
    ax.invert_yaxis()
    ax.set_xlabel('Consumo Total Acumulado (m³)')
    ax.set_title('Top 10 Medidores por Consumo Total')
    fig.tight_layout()

    _dibujar_grafica(root, fig, graph_frame)
    plt.close(fig)

def Graficar_medidores(root, tree, graph_frame):
    if not PLOTLY_INSTALLED:
        messagebox.showerror("Error", "Plotly no está instalado.")
        return

    por_medidor = _leer_tabla_consumos(tree)
    if not por_medidor:
        messagebox.showwarning("Aviso", "No hay datos en la tabla.")
        return

    consumo_data = [{'Medidor': str(med), 'Consumo_Total': sum(float(l[2]) for l in lects)} for med, lects in por_medidor.items()]
    df = pd.DataFrame(consumo_data)

    fig = px.bar(df, x='Medidor', y='Consumo_Total', title="Consumo Total de Todos los Medidores", color_discrete_sequence=['#198754'])
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=".html", encoding='utf-8') as tmp:
        fig.write_html(tmp, full_html=True)
        webbrowser.open(f"file://{tmp.name}")

def graficar_clasificacion_consumos_acumulados_navegador(root, tree, graph_frame):
    if not PLOTLY_INSTALLED:
        messagebox.showerror("Error", "Plotly no está instalado.")
        return

    por_tarifa = defaultdict(list)
    for iid in tree.get_children():
        vals = tree.item(iid)["values"]
        tarifa = str(vals[10]).strip().upper()
        try:
            consumo = float(vals[20])
        except:
            consumo = 0.0
        if consumo > 0:
            por_tarifa[tarifa].append(consumo)

    fig = go.Figure()
    for tarifa, valores in por_tarifa.items():
        fig.add_trace(go.Box(y=valores, name=tarifa))

    fig.update_layout(title='Distribución del Consumo Acumulado por Tarifa', yaxis_title='Consumo (m³)')
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=".html", encoding='utf-8') as tmp:
        fig.write_html(tmp.name)
        webbrowser.open(f"file://{tmp.name}")

# -------------------- Inserción y Gestión HES --------------------
def _borrar_rango_HES_silencioso(fecha_inicio_str, fecha_fin_str):
    conn = None
    try:
        conn = mysql.connector.connect(host='miaa.mx', database='miaamx_telemetria2', user='miaamx_telemetria2', password='bWkrw1Uum1O&', charset='utf8mb4', use_pure=True)
        cursor = conn.cursor()
        fecha_fin_dt = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
        fecha_fin_ajustada_str = (fecha_fin_dt + timedelta(days=1)).strftime('%Y-%m-%d')
        cursor.execute("DELETE FROM HES WHERE Fecha >= %s AND Fecha < %s", (fecha_inicio_str, fecha_fin_ajustada_str))
        conn.commit()
        cursor.close()
    except Exception as e:
        if DEBUG: print("Error borrando HES:", e)
    finally:
        if conn and conn.is_connected(): conn.close()

def insertar_en_HES(registros, queue=None, root=None):
    conn = None
    try:
        conn = mysql.connector.connect(host='miaa.mx', database='miaamx_telemetria2', user='miaamx_telemetria2', password='bWkrw1Uum1O&', charset='utf8mb4', use_pure=True)
        cursor = conn.cursor()
        query = ("""INSERT INTO HES (
            ID, Fecha, `Predio`, Medidor, Lectura, MetodoID_API, ClienteID_API,
            Nombre, Domicilio, Colonia, Nivel,
            Situacion_comercial, Giro, Marca, Modelo, Primer_instalacion, Distrito,
            Sector, Latitud, Longitud, Consumo_diario
        ) VALUES (""" + ", ".join(["%s"] * 21) + ") ON DUPLICATE KEY UPDATE " 
        + """Fecha=VALUES(Fecha), Lectura=VALUES(Lectura), Consumo_diario=VALUES(Consumo_diario)""")
        
        batch_size = 500
        total = len(registros)
        for i in range(0, total, batch_size):
            if SILENCIOSO_STOP_EVENT.is_set(): break
            cursor.executemany(query, registros[i:i+batch_size])
            conn.commit()
            if queue and root:
                avance = min(100, int(((i + batch_size) / total) * 100))
                root.after(0, lambda q=queue, a=avance: q.put(("progress", a)))

        if queue and root:
            root.after(0, lambda: queue.put(("done", f"Se guardaron {total} registros en HES.")))
        cursor.close()
    except Exception as e:
        if queue and root:
            root.after(0, lambda: queue.put(("error", str(e))))
    finally:
        if conn and conn.is_connected(): conn.close()

def borrar_HES_confirmado(root):
    try:
        conn = mysql.connector.connect(host='miaa.mx', database='miaamx_telemetria2', user='miaamx_telemetria2', password='bWkrw1Uum1O&', use_pure=True)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM HES")
        conn.commit()
        cursor.close()
        conn.close()
        messagebox.showinfo("Éxito", "Registros de HES eliminados.")
    except Exception as e:
        messagebox.showerror("Error", f"No se pudo borrar HES: {e}")

def ejecutar_borrado_rango_HES(root, fecha_inicio_ref, fecha_fin_ref):
    try:
        conn = mysql.connector.connect(host='miaa.mx', database='miaamx_telemetria2', user='miaamx_telemetria2', password='bWkrw1Uum1O&', use_pure=True)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM HES WHERE Fecha >= %s AND Fecha < %s", (fecha_inicio_ref.get_date().strftime('%Y-%m-%d'), (fecha_fin_ref.get_date() + timedelta(days=1)).strftime('%Y-%m-%d')))
        conn.commit()
        cursor.close()
        conn.close()
        messagebox.showinfo("Éxito", "Rango de HES eliminado.")
    except Exception as e:
        messagebox.showerror("Error", f"No se pudo borrar rango: {e}")

def verificar_contrasena(root, callback_func, *args):
    win = tk.Toplevel(root)
    win.title("Confirmar")
    win.geometry("250x100")
    ttk.Label(win, text="Escribe 'DELETE':").pack(pady=5)
    e = ttk.Entry(win, show="*")
    e.pack(pady=5)
    def ok():
        if e.get() == "DELETE":
            win.destroy()
            callback_func(*args)
        else:
            messagebox.showerror("Error", "Incorrecto", parent=win)
    ttk.Button(win, text="OK", command=ok).pack()

# -------------------- BÚSQUEDA Y VOLCADO OPTIMIZADO --------------------
def buscar_lecturas(root, fecha_inicio_ref, fecha_fin_ref, entry_medidor, cmb_metodo, cmb_tarifa, cmb_colonia, cmb_sector, tree, progress_bar):
    fecha_inicio = fecha_inicio_ref.get().strip()
    fecha_fin = fecha_fin_ref.get().strip()
    medidor_filtro = _canon_numeric(entry_medidor.get().strip())
    tarifa_filtro = cmb_tarifa.get()
    colonia_filtro = cmb_colonia.get()
    sector_filtro = cmb_sector.get()

    if not fecha_inicio or not fecha_fin:
        messagebox.showerror("Error", "Fechas vacías.")
        return

    def buscar_worker():
        progress_bar.configure(mode="indeterminate")
        progress_bar.start()

        data = obtener_lecturas_completas(fecha_inicio, fecha_fin, root_ref=root)
        if not data:
            root.after(0, lambda: progress_bar.stop())
            return

        pad_by_cli, pad_by_med = obtener_info_padron()
        gps_data = obtener_lat_long()

        registros_tmp = []
        for item in data:
            fecha_raw = str(item.get("fecha", item.get("fechaLectura", item.get("createdDate", ""))))
            fecha_txt = fecha_raw.replace("T", " ")
            try:
                fecha_dt = datetime.strptime(fecha_txt[:19], "%Y-%m-%d %H:%M:%S")
            except:
                continue

            medidor_id = _canon_numeric(item.get("medidorId", item.get("medidor", item.get("serie", ""))))
            
            if medidor_filtro and medidor_id != medidor_filtro:
                continue

            try:
                lectura = float(item.get("lectura", item.get("valor", 0)) or 0)
            except:
                lectura = 0.0

            cli_raw = str(item.get("clienteId") or item.get("cliente") or "")
            info = pad_by_cli.get(_canon_numeric(cli_raw), {}) or pad_by_med.get(medidor_id, {})

            registros_tmp.append({
                "id": str(item.get("id", f"REAL-{medidor_id}-{fecha_dt.strftime('%Y%m%d')}")),
                "fecha_dt": fecha_dt,
                "fecha_txt": fecha_txt[:19],
                "medidor": medidor_id,
                "lectura": lectura,
                "metodo": item.get("metodoId", item.get("metodo", "")),
                "cliente": cli_raw,
                "info": info,
                "lat": gps_data.get(medidor_id, {}).get("Latitud", ""),
                "lon": gps_data.get(medidor_id, {}).get("Longitud", ""),
                "consumo_diario": 0.0
            })

        registros_tmp.sort(key=lambda x: (x['medidor'], x['fecha_dt']))
        lecturas_por_medidor = defaultdict(list)
        for r in registros_tmp:
            lecturas_por_medidor[r['medidor']].append(r)
        
        registros_completos = []
        for med, lects in lecturas_por_medidor.items():
            for i in range(len(lects) - 1):
                dias = (lects[i+1]['fecha_dt'] - lects[i]['fecha_dt']).days
                if dias > 0:
                    lects[i]['consumo_diario'] = max(0.0, lects[i+1]['lectura'] - lects[i]['lectura']) / dias
                registros_completos.append(lects[i])
            if lects:
                registros_completos.append(lects[-1])

        # Filtrar por padrón
        registros_filtrados = []
        for r in registros_completos:
            info = r['info']
            if (tarifa_filtro == "Todas" or info.get('Nivel') == tarifa_filtro) and \
               (colonia_filtro == "Todas" or info.get('Colonia') == colonia_filtro) and \
               (sector_filtro == "Todas" or info.get('Sector') == sector_filtro):
                registros_filtrados.append(r)

        if not registros_filtrados:
            registros_filtrados = registros_tmp

        root.after(0, lambda: actualizar_interfaz_optimizada(root, tree, progress_bar, registros_filtrados))

    threading.Thread(target=buscar_worker, daemon=True).start()

def actualizar_interfaz_optimizada(root, tree, progress_bar, registros):
    progress_bar.stop()
    for item in tree.get_children():
        tree.delete(item)

    datos_para_tabla = []
    total_cons = 0.0

    for r in registros:
        cons = r['consumo_diario']
        total_cons += cons
        tup = (
            r['id'], r['fecha_txt'], r['info'].get('Predio', 'SIN DATO'), r['medidor'], r['lectura'],
            r['metodo'], r['cliente'], r['info'].get('Nombre', 'SIN DATO'), r['info'].get('Domicilio', 'SIN DATO'),
            r['info'].get('Colonia', 'SIN DATO'), r['info'].get('Nivel', 'SIN DATO'), r['info'].get('Situacion_comercial', 'SIN DATO'),
            r['info'].get('Giro', 'SIN DATO'), r['info'].get('Marca', ''), r['info'].get('Modelo', ''),
            r['info'].get('Primer_instalacion', ''), r['info'].get('Distrito', ''), r['info'].get('Sector', ''),
            r['lat'], r['lon'], f"{cons:.3f}"
        )
        datos_para_tabla.append(tup)

    root.datos_lecturas_actuales = datos_para_tabla

    # Volcado por lotes rápido para evitar congelamiento de interfaz
    chunk_size = 2000
    for i in range(0, len(datos_para_tabla), chunk_size):
        lote = datos_para_tabla[i:i + chunk_size]
        for tup in lote:
            tree.insert('', tk.END, values=tup)
        root.update_idletasks()

    root.lbl_total_consumo.config(text=f"Total consumo (m³): {total_cons:,.2f}")
    root.lbl_promedio_consumo.config(text=f"Promedio consumo (m³): {total_cons / len(datos_para_tabla) if datos_para_tabla else 0:,.2f}")
    root.lbl_total_lecturas.config(text=f"Total lecturas: {len(datos_para_tabla):,}")
    
    messagebox.showinfo("Éxito", f"Se cargaron {len(datos_para_tabla):,} registros correctamente en la tabla.")

def actualizar_fechas_por_periodo(event, cmb_periodo, fecha_inicio_ref, fecha_fin_ref, spin_days, days_var):
    periodo = cmb_periodo.get()
    hoy = datetime.now().date()
    ayer = hoy - timedelta(days=1)
    if periodo == "Personalizado":
        spin_days.configure(state="normal")
    else:
        spin_days.configure(state="disabled")
        if periodo == "Este mes (hasta ayer)":
            fecha_inicio_ref.set_date(get_primer_dia_mes(ayer))
            fecha_fin_ref.set_date(ayer)
        elif periodo == "Mes anterior":
            f_fin = get_primer_dia_mes(hoy) - timedelta(days=1)
            fecha_inicio_ref.set_date(get_primer_dia_mes(f_fin))
            fecha_fin_ref.set_date(f_fin)
        elif periodo == "Últimos 30 días":
            fecha_inicio_ref.set_date(ayer - timedelta(days=29))
            fecha_fin_ref.set_date(ayer)

# -------------------- CONSTRUCCIÓN DE LA VENTANA PRINCIPAL --------------------
def crear_interfaz():
    root = tk.Tk()
    root.title("Lecturas API + Padrón (WASION) + GPS - Entregable")
    
    try:
        root.state('zoomed')
    except:
        root.geometry("1400x800+0+0")

    style = ttk.Style()
    style.theme_use("default")
    style.configure("green.Horizontal.TProgressbar", troughcolor="white", background="green")

    frame = ttk.Frame(root, padding=8)
    frame.pack(fill="both", expand=True)

    hoy = datetime.now().date()
    ayer = hoy - timedelta(days=1)

    top_frame = ttk.Frame(frame)
    top_frame.pack(fill="x", pady=4, padx=4)

    ttk.Label(top_frame, text="Periodo a procesar").grid(row=0, column=0, sticky="w")
    ttk.Label(top_frame, text="Días (si es Person.)").grid(row=0, column=1, sticky="w")
    ttk.Label(top_frame, text="Fecha inicio").grid(row=0, column=2, sticky="w")
    ttk.Label(top_frame, text="Fecha fin").grid(row=0, column=3, sticky="w")
    ttk.Label(top_frame, text="Método (opcional)").grid(row=0, column=4, sticky="w")
    ttk.Label(top_frame, text="Medidor").grid(row=0, column=5, sticky="w")
    ttk.Label(top_frame, text="Tarifa (Nivel)").grid(row=0, column=6, sticky="w")
    ttk.Label(top_frame, text="Colonia").grid(row=0, column=7, sticky="w")
    ttk.Label(top_frame, text="Sector").grid(row=0, column=8, sticky="w")

    cmb_periodo = ttk.Combobox(top_frame, values=["Personalizado", "Este mes (hasta ayer)", "Mes anterior", "Últimos 30 días"], width=20, state="readonly")
    cmb_periodo.set("Este mes (hasta ayer)")
    cmb_periodo.grid(row=1, column=0, padx=4, sticky="w")

    days_var = tk.StringVar(value="30")
    spin_days = ttk.Spinbox(top_frame, from_=1, to=31, wrap=True, state="disabled", width=4, textvariable=days_var)
    spin_days.grid(row=1, column=1, padx=4, sticky="w")

    fecha_inicio = DateEntry(top_frame, date_pattern="yyyy-mm-dd")
    fecha_inicio.set_date(get_primer_dia_mes(ayer))
    fecha_inicio.grid(row=1, column=2, padx=4, sticky="w")

    fecha_fin = DateEntry(top_frame, date_pattern="yyyy-mm-dd")
    fecha_fin.set_date(ayer)
    fecha_fin.grid(row=1, column=3, padx=4, sticky="w")

    cmb_periodo.bind("<<ComboboxSelected>>", lambda e: actualizar_fechas_por_periodo(e, cmb_periodo, fecha_inicio, fecha_fin, spin_days, days_var))

    cmb_metodo = ttk.Combobox(top_frame, values=["Todos", "LoraWan", "App"], width=12, state="readonly")
    cmb_metodo.set("Todos")
    cmb_metodo.grid(row=1, column=4, padx=4, sticky="w")

    entry_medidor = ttk.Entry(top_frame, width=12)
    entry_medidor.grid(row=1, column=5, padx=4, sticky="w")

    cmb_tarifa = ttk.Combobox(top_frame, values=["Todas"], width=12, state="readonly")
    cmb_tarifa.set("Todas")
    cmb_tarifa.grid(row=1, column=6, padx=4, sticky="w")

    cmb_colonia = ttk.Combobox(top_frame, values=["Todas"], width=18, state="readonly")
    cmb_colonia.set("Todas")
    cmb_colonia.grid(row=1, column=7, padx=4, sticky="w")

    cmb_sector = ttk.Combobox(top_frame, values=["Todas"], width=12, state="readonly")
    cmb_sector.set("Todas")
    cmb_sector.grid(row=1, column=8, padx=4, sticky="w")

    botones_frame = ttk.Frame(top_frame)
    botones_frame.grid(row=2, column=0, columnspan=9, sticky="w", pady=5)

    resumen_frame = ttk.Frame(top_frame)
    resumen_frame.grid(row=2, column=9, sticky="e", padx=10)
    
    root.lbl_api_status = ttk.Label(resumen_frame, text="API: En espera ⚪", font=("Arial", 9, "bold"))
    root.lbl_total_consumo = ttk.Label(resumen_frame, text="Total consumo (m³): 0.00")
    root.lbl_promedio_consumo = ttk.Label(resumen_frame, text="Promedio consumo (m³): 0.00")
    root.lbl_total_lecturas = ttk.Label(resumen_frame, text="Total lecturas: 0")
    
    root.lbl_api_status.pack(anchor='e')
    root.lbl_total_consumo.pack(anchor='e')
    root.lbl_promedio_consumo.pack(anchor='e')
    root.lbl_total_lecturas.pack(anchor='e')

    # TODOS TUS BOTONES ORIGINALES
    btn_buscar = ttk.Button(botones_frame, text="Buscar lecturas", command=lambda: buscar_lecturas(root, fecha_inicio, fecha_fin, entry_medidor, cmb_metodo, cmb_tarifa, cmb_colonia, cmb_sector, tree, progress_guardar))
    btn_buscar.pack(side="left", padx=3)

    progress_guardar = ttk.Progressbar(botones_frame, mode="determinate", length=80, style="green.Horizontal.TProgressbar")

    btn_guardar = ttk.Button(botones_frame, text="Guardar en HESS", command=lambda: threading.Thread(target=insertar_en_HES, args=(root.datos_lecturas_actuales, Queue(), root)).start())
    btn_guardar.pack(side="left", padx=3)

    btn_sin_lectura = ttk.Button(botones_frame, text="Medidores con problemas", command=lambda: analizar_medidores_problemas(root, fecha_inicio, fecha_fin))
    btn_sin_lectura.pack(side="left", padx=3)

    btn_graf = ttk.Button(botones_frame, text="Consumos diarios", command=lambda: graficar_consumo(root, tree, graph_frame, entry_medidor))
    btn_graf.pack(side="left", padx=3)

    btn_top10 = ttk.Button(botones_frame, text="Top 10 medidores", command=lambda: top10_medidores(root, tree, graph_frame))
    btn_top10.pack(side="left", padx=3)

    btn_top_all = ttk.Button(botones_frame, text="Graficar los medidores", command=lambda: Graficar_medidores(root, tree, graph_frame))
    btn_top_all.pack(side="left", padx=3)
    
    btn_boxplot = ttk.Button(botones_frame, text="Cajas de consumos navegador", command=lambda: graficar_clasificacion_consumos_acumulados_navegador(root, tree, graph_frame))
    btn_boxplot.pack(side="left", padx=3)
                
    btn_borrar = ttk.Button(botones_frame, text="Borrar TODO HESS", command=lambda: verificar_contrasena(root, borrar_HES_confirmado, root))
    btn_borrar.pack(side="left", padx=3)
    
    btn_borrar_rango = ttk.Button(botones_frame, text="Borrar rango HESS", command=lambda: verificar_contrasena(root, ejecutar_borrado_rango_HES, root, fecha_inicio, fecha_fin))
    btn_borrar_rango.pack(side="left", padx=3)

    # Servicio Silencioso
    servicio_frame = ttk.LabelFrame(top_frame, text="Servicio de Registro Diario (Silencioso y Recurrente)", padding=5)
    servicio_frame.grid(row=3, column=0, columnspan=10, sticky="ew", py=5)

    ttk.Label(servicio_frame, text="Hora:").pack(side="left")
    hour_var = tk.StringVar(value=f"{datetime.now().hour:02d}")
    ttk.Spinbox(servicio_frame, from_=0, to=23, textvariable=hour_var, width=3, state="readonly").pack(side="left", padx=2)

    ttk.Label(servicio_frame, text="Minuto:").pack(side="left")
    minute_var = tk.StringVar(value=f"{datetime.now().minute:02d}")
    ttk.Spinbox(servicio_frame, from_=0, to=59, textvariable=minute_var, width=3, state="readonly").pack(side="left", padx=2)

    ttk.Button(servicio_frame, text="Registrar y Comenzar").pack(side="left", padx=5)
    ttk.Button(servicio_frame, text="Cancelar Servicio").pack(side="left", padx=5)
    status_label = ttk.Label(servicio_frame, text="Servicio Silencioso: Inactivo.")
    status_label.pack(side="left", padx=10)

    # LA TABLA DE REGISTROS (TREEVIEW)
    tree_frame = ttk.Frame(frame)
    tree_frame.pack(fill="both", expand=True, pady=8)

    columnas = (
        'ID', 'Fecha', 'Predio', 'Medidor', 'Lectura', 'MetodoID_API', 'ClienteID_API',
        'Nombre', 'Domicilio', 'Colonia', 'Nivel', 'Situacion_comercial', 'Giro', 
        'Marca', 'Modelo', 'Primer_instalacion', 'Distrito', 'Sector', 
        'Latitud', 'Longitud', 'Consumo_diario'
    )
    
    tree = ttk.Treeview(tree_frame, columns=columnas, show='headings')
    for col in columnas:
        tree.heading(col, text=col)
        tree.column(col, width=110)

    vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

    tree.grid(row=0, column=0, sticky='nsew')
    vsb.grid(row=0, column=1, sticky='ns')
    hsb.grid(row=1, column=0, sticky='ew')
    tree_frame.grid_rowconfigure(0, weight=1)
    tree_frame.grid_columnconfigure(0, weight=1)
    
    graph_frame = ttk.Frame(frame, height=200)
    graph_frame.pack(fill="x", pady=4)
    root.datos_lecturas_actuales = []

    # Poblar combos iniciales
    def poblar_combos():
        pad_by_cli, pad_by_med = obtener_info_padron()
        niveles = sorted(list(set(d.get("Nivel") for d in pad_by_med.values() if d.get("Nivel"))))
        colonias = sorted(list(set(d.get("Colonia") for d in pad_by_med.values() if d.get("Colonia"))))
        sectores = sorted(list(set(d.get("Sector") for d in pad_by_med.values() if d.get("Sector"))))
        root.after(0, lambda: [
            cmb_tarifa.configure(values=["Todas"] + niveles),
            cmb_colonia.configure(values=["Todas"] + colonias),
            cmb_sector.configure(values=["Todas"] + sectores)
        ])
    threading.Thread(target=poblar_combos, daemon=True).start()

    root.mainloop()

if __name__ == "__main__":
    crear_interfaz()
