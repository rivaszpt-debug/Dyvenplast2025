from flask import Flask, render_template, request, redirect, url_for, Response, jsonify
import sqlite3
from datetime import datetime
import csv
from io import StringIO
import os
import time
import requests
import urllib3
import re
import json

from google import genai
from google.genai import types

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# --- INICIALIZACIÓN DE GOOGLE GENAI ---
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
# --- VARIABLES GLOBALES PARA MEMORIA DE LA IA ---
chat_session = None
RESET_CHAT = False

# --- LIMPIADOR Y CALCULADOR DE PRECISIÓN MATEMÁTICA ---
def limpiar_numero(val):
    if not val:
        return 0.0
    s = str(val).strip()
    if '.' in s and ',' in s:
        s = s.replace('.', '').replace(',', '.')
    elif ',' in s:
        s = s.replace(',', '.')
    
    try:
        return float(s)
    except ValueError:
        return 0.0

# --- ESTRUCTURA DE CACHÉ INDEPENDIENTE ---
CACHE = {
    "BCV": {"valor": 0.0, "time": 0},
    "Euro": {"valor": 0.0, "time": 0},
    "USDT": {"valor": 0.0, "time": 0}
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# --- EXTRACCIÓN GENERAL DE TASAS ---
def obtener_tasas():
    ahora = time.time()
    TTL = 1800 

    # La máscara que salvó a Binance, ahora protegerá a las tres monedas
    headers_seguros = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }

    # --- BCV ---
    if CACHE["BCV"]["valor"] == 0 or (ahora - CACHE["BCV"]["time"] > TTL):
        try: 
            r = requests.get("https://ve.dolarapi.com/v1/dolares/oficial", headers=headers_seguros, timeout=5)
            val = float(r.json().get("promedio", 0))
            if val > 0: CACHE["BCV"] = {"valor": round(val, 2), "time": ahora}
        except:
            try: 
                r = requests.get("https://pydolarvenezuela-api.vercel.app/api/v1/dollar/page?page=bcv", headers=headers_seguros, timeout=5)
                val = float(r.json()["monitors"]["bcv"]["price"])
                if val > 0: CACHE["BCV"] = {"valor": round(val, 2), "time": ahora}
            except:
                pass

    # --- EURO ---
    if CACHE["Euro"]["valor"] == 0 or (ahora - CACHE["Euro"]["time"] > TTL):
        try:
            r = requests.get("https://ve.dolarapi.com/v1/euros/oficial", headers=headers_seguros, timeout=5)
            val = float(r.json().get("promedio", 0))
            if val > 0: CACHE["Euro"] = {"valor": round(val, 2), "time": ahora}
        except:
            try:
                r = requests.get("https://pydolarvenezuela-api.vercel.app/api/v1/euro/page?page=bcv", headers=headers_seguros, timeout=5)
                val = float(r.json()["monitors"]["euro"]["price"])
                if val > 0: CACHE["Euro"] = {"valor": round(val, 2), "time": ahora}
            except:
                pass

    # --- USDT (El que ya probaste y funciona perfecto) ---
    if CACHE["USDT"]["valor"] == 0 or (ahora - CACHE["USDT"]["time"] > TTL):
        val_binance = 0.0
        try:
            r = requests.get("https://ve.dolarapi.com/v1/dolares/binance", headers=headers_seguros, timeout=5)
            val_binance = float(r.json().get("promedio", 0))
        except:
            pass
        
        if val_binance == 0.0:
            try:
                r = requests.get("https://pydolarvenezuela-api.vercel.app/api/v1/dollar/page?page=binance", headers=headers_seguros, timeout=5)
                val_binance = float(r.json()["monitors"]["binance"]["price"])
            except:
                pass

        if val_binance == 0.0:
            try:
                r = requests.get("https://criptoya.com/api/binancep2p/usdt/ves/0.1", headers=headers_seguros, timeout=5)
                val_binance = float(r.json().get("ask", 0))
            except:
                pass
        
        if val_binance > 0:
            CACHE["USDT"] = {"valor": round(val_binance, 2), "time": ahora}

    return {
        "BCV": CACHE["BCV"]["valor"],
        "Euro": CACHE["Euro"]["valor"],
        "USDT": CACHE["USDT"]["valor"]
    }


# --- BASE DE DATOS ---
def obtener_conexion():
    # Si detecta que está en Railway, usa la ruta del volumen seguro. Si no, usa tu PC.
    ruta_db = '/data/pagos.db' if os.getenv('RAILWAY_ENVIRONMENT') else 'pagos.db'
    
    conn = sqlite3.connect(ruta_db)
    conn.row_factory = sqlite3.Row
    return conn

def crear_tabla():
    conn = obtener_conexion()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS pagos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            monto REAL NOT NULL,
            monto_bs REAL DEFAULT 0,
            tasa_usada TEXT,
            concepto TEXT NOT NULL,
            origen TEXT NOT NULL,
            empresa TEXT NOT NULL,
            categoria TEXT NOT NULL,
            estatus TEXT NOT NULL,
            fecha TIMESTAMP DEFAULT (datetime('now', 'localtime'))
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS categorias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT UNIQUE NOT NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS cuentas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT UNIQUE NOT NULL
        )
    ''')

    # Poblado inicial de categorías
    cat_count = conn.execute("SELECT COUNT(*) FROM categorias").fetchone()[0]
    if cat_count == 0:
        cats_defecto = [
            "Adicionales nómina", "Alcaldía", "Alquiler de Galpón", "Binance",
            "Bono de alimentación (Cestaticket)", "Comisiones", "Compra Aditivos",
            "Compra de maquinaria y equipo", "Compra Materia Prima", "Corpoelec",
            "Deberes Formales", "Donaciones", "Dotación uniforme", "Fletes",
            "Gastos accionista", "Gasto de representación", "Gastos de Mantenimiento",
            "Gastos de Oficina", "Gastos médicos", "Ingresos por Ventas", "Internet Wi-Fi",
            "Liquidación de prestaciones sociales", "Mantenimiento Planta Eléctrica",
            "Mantenimiento Vehículo", "Nómina", "Pago guía Desoca", "Prepago Materia Prima",
            "Préstamos Empleados", "Préstamos Terceros"
        ]
        for c in cats_defecto:
            conn.execute("INSERT OR IGNORE INTO categorias (nombre) VALUES (?)", (c,))

    # Poblado inicial de cuentas/orígenes
    cta_count = conn.execute("SELECT COUNT(*) FROM cuentas").fetchone()[0]
    if cta_count == 0:
        ctas_defecto = ["Cuenta Ingeniero", "Cuenta Mayra", "Cuenta Binance", "Cuenta Yorgelis"]
        for c in ctas_defecto:
            conn.execute("INSERT OR IGNORE INTO cuentas (nombre) VALUES (?)", (c,))

    conn.commit()
    conn.close()

def obtener_lista_categorias():
    conn = obtener_conexion()
    rows = conn.execute("SELECT nombre FROM categorias ORDER BY nombre ASC").fetchall()
    conn.close()
    return [r['nombre'] for r in rows]

def obtener_lista_cuentas():
    conn = obtener_conexion()
    rows = conn.execute("SELECT nombre FROM cuentas ORDER BY nombre ASC").fetchall()
    conn.close()
    return [r['nombre'] for r in rows]

# --- HERRAMIENTAS PARA EL FUNCTION CALLING DE GEMINI ---
def registrar_movimiento(monto: float, empresa: str, concepto: str, categoria: str, origen: str, estatus: str = "Pagado", tipo_tasa: str = "BCV", tasa_custom: float = 0.0) -> str:
    """Registra una nueva transacción financiera (gasto o ingreso) en la base de datos de Dyvenplast."""
    try:
        tasas = obtener_tasas()
        if tasa_custom > 0:
            tasa_val = tasa_custom
            tasa_nombre = f"Personalizada ({tasa_val:.2f})"
        else:
            tasa_val = tasas.get(tipo_tasa, tasas['BCV'])
            tasa_nombre = f"{tipo_tasa} ({tasa_val:.2f})"

        monto_bs = round(monto * tasa_val, 2) if tasa_val > 0 else 0.0

        conn = obtener_conexion()
        conn.execute('''
            INSERT INTO pagos (monto, monto_bs, tasa_usada, concepto, origen, empresa, categoria, estatus)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (monto, monto_bs, tasa_nombre, concepto, origen, empresa, categoria, estatus))

        conn.execute("INSERT OR IGNORE INTO categorias (nombre) VALUES (?)", (categoria.strip(),))
        conn.execute("INSERT OR IGNORE INTO cuentas (nombre) VALUES (?)", (origen.strip(),))

        conn.commit()
        conn.close()
        return f"✅ Movimiento registrado: {monto}$ ({monto_bs} Bs al cambio {tasa_nombre}) | Empresa: {empresa} | Concepto: {concepto} | Categoría: {categoria} | Origen: {origen} | Estatus: {estatus}."
    except Exception as e:
        return f"Error al registrar movimiento: {str(e)}"

def editar_movimiento(id: int, monto: float = None, empresa: str = None, concepto: str = None, origen: str = None, categoria: str = None, estatus: str = None) -> str:
    """Edita un registro existente por su ID."""
    try:
        conn = obtener_conexion()
        row = conn.execute("SELECT * FROM pagos WHERE id = ?", (id,)).fetchone()
        if not row:
            conn.close()
            return f"No se encontró el registro con ID #{id}."

        nuevo_monto = monto if monto is not None else row['monto']
        nueva_empresa = empresa if empresa is not None else row['empresa']
        nuevo_concepto = concepto if concepto is not None else row['concepto']
        nuevo_origen = origen if origen is not None else row['origen']
        nueva_categoria = categoria if categoria is not None else row['categoria']
        nuevo_estatus = estatus if estatus is not None else row['estatus']

        tasa_str = row['tasa_usada']
        match = re.search(r'\(([\d\.]+)\)', tasa_str)
        tasa_num = float(match.group(1)) if match else 1.0
        monto_bs = round(nuevo_monto * tasa_num, 2)

        conn.execute('''
            UPDATE pagos 
            SET monto=?, monto_bs=?, empresa=?, concepto=?, origen=?, categoria=?, estatus=?
            WHERE id=?
        ''', (nuevo_monto, monto_bs, nueva_empresa, nuevo_concepto, nuevo_origen, nueva_categoria, nuevo_estatus, id))
        conn.commit()
        conn.close()
        return f"✏️ Registro ID #{id} actualizado correctamente."
    except Exception as e:
        return f"Error al editar registro: {str(e)}"

def eliminar_movimiento(id: int) -> str:
    """Elimina un único registro específico por su ID."""
    try:
        conn = obtener_conexion()
        conn.execute("DELETE FROM pagos WHERE id = ?", (id,))
        conn.commit()
        conn.close()
        return f"🗑️ Registro ID #{id} eliminado correctamente."
    except Exception as e:
        return f"Error al eliminar registro: {str(e)}"

def registrar_abono(empresa: str, monto_abonado: float, origen: str, concepto_abono: str = None) -> str:
    """Busca una deuda pendiente, resta el monto abonado y registra un nuevo ingreso."""
    try:
        conn = obtener_conexion()
        
        # 1. Buscar la deuda original pendiente de esta empresa
        deuda = conn.execute('''
            SELECT * FROM pagos 
            WHERE empresa LIKE ? AND estatus IN ('Pendiente por cobrar', 'Abono', 'Pendiente') 
            ORDER BY id ASC LIMIT 1
        ''', (f"%{empresa}%",)).fetchone()
        
        if not deuda:
            conn.close()
            return f"No encontré ninguna deuda pendiente (Por cobrar) registrada para '{empresa}'. Revisa el nombre."
        
        id_deuda = deuda['id']
        monto_deuda_actual = float(deuda['monto'])
        categoria_deuda = deuda['categoria']
        
        # 2. Restar el abono
        nuevo_saldo = monto_deuda_actual - monto_abonado
        estatus_deuda = 'Abono' if nuevo_saldo > 0 else 'Pagado'
        if nuevo_saldo < 0: 
            nuevo_saldo = 0.0 # No dejar saldos negativos
            
        tasa_str = deuda['tasa_usada']
        match = re.search(r'\(([\d\.]+)\)', tasa_str)
        tasa_num = float(match.group(1)) if match else 1.0
        nuevo_saldo_bs = round(nuevo_saldo * tasa_num, 2)
        
        # 3. Actualizar la deuda original (Esto arregla tu cuadro naranja de 'Por Cobrar')
        conn.execute('''
            UPDATE pagos SET monto=?, monto_bs=?, estatus=? WHERE id=?
        ''', (nuevo_saldo, nuevo_saldo_bs, estatus_deuda, id_deuda))
        
        # 4. Registrar la entrada del dinero a la cuenta hoy (Esto arregla tu cuadro verde de 'Ingresos')
        if not concepto_abono:
            concepto_abono = f"Abono a deuda (Saldo restante: ${nuevo_saldo})"
            
        tasas = obtener_tasas()
        tasa_actual = tasas.get('BCV', 1.0)
        monto_ingreso_bs = round(monto_abonado * tasa_actual, 2)
        tasa_nombre = f"BCV ({tasa_actual:.2f})"
        
        conn.execute('''
            INSERT INTO pagos (monto, monto_bs, tasa_usada, concepto, origen, empresa, categoria, estatus)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'Pagado')
        ''', (monto_abonado, monto_ingreso_bs, tasa_nombre, concepto_abono, origen, empresa, categoria_deuda))
        
        conn.commit()
        conn.close()
        
        return f"✅ Abono exitoso. Desconté ${monto_abonado} a '{empresa}'. Su saldo restante es de ${nuevo_saldo} y registré el dinero entrante en {origen}."
        
    except Exception as e:
        return f"Error al registrar abono: {str(e)}"

def agregar_categoria(nombre: str) -> str:
    """Añade una nueva categoría a la lista disponible en la base de datos."""
    try:
        conn = obtener_conexion()
        conn.execute("INSERT OR IGNORE INTO categorias (nombre) VALUES (?)", (nombre.strip(),))
        conn.commit()
        conn.close()
        return f"➕ Categoría '{nombre.strip()}' guardada con éxito."
    except Exception as e:
        return f"Error al añadir categoría: {str(e)}"

def eliminar_categoria(nombre: str) -> str:
    """Elimina una categoría de la lista de categorías."""
    try:
        conn = obtener_conexion()
        conn.execute("DELETE FROM categorias WHERE LOWER(nombre) = LOWER(?)", (nombre.strip(),))
        conn.commit()
        conn.close()
        return f"🗑️ Categoría '{nombre.strip()}' eliminada con éxito."
    except Exception as e:
        return f"Error al borrar categoría: {str(e)}"

def consultar_datos(filtro: str) -> str:
    """Consulta la base de datos para extraer registros, sumatorias o buscar por empresa/categoría/estatus/ID."""
    try:
        conn = obtener_conexion()
        filtro_low = filtro.lower()
        if "hoy" in filtro_low:
            hoy = datetime.now().strftime('%Y-%m-%d')
            filas = conn.execute("SELECT id, fecha, monto, monto_bs, empresa, concepto, categoria, estatus, origen FROM pagos WHERE fecha LIKE ? ORDER BY id DESC", (f"{hoy}%",)).fetchall()
        elif "cobrar" in filtro_low or "pendiente" in filtro_low:
            filas = conn.execute("SELECT id, fecha, monto, monto_bs, empresa, concepto, categoria, estatus, origen FROM pagos WHERE estatus IN ('Pendiente por cobrar', 'Abono') ORDER BY id DESC").fetchall()
        else:
            filas = conn.execute("SELECT id, fecha, monto, monto_bs, empresa, concepto, categoria, estatus, origen FROM pagos ORDER BY id DESC LIMIT 30").fetchall()
        conn.close()
        registros = [dict(r) for r in filas]
        if not registros:
            return "La consulta arrojó 0 resultados."
        return f"Datos obtenidos ({len(registros)} registros): {json.dumps(registros, ensure_ascii=False)}"
    except Exception as e:
        return f"Error al consultar: {str(e)}"

def vaciar_historial() -> str:
    """PELIGRO: Elimina el historial financiero completo de la base de datos. SOLO EJECUTAR si el usuario ya dio su confirmación expresa de borrar todo el historial."""
    try:
        conn = obtener_conexion()
        conn.execute('DELETE FROM pagos')
        conn.execute("DELETE FROM sqlite_sequence WHERE name='pagos'")
        conn.commit()
        conn.close()
        return "BASE DE DATOS ELIMINADA CON ÉXITO. El historial financiero quedó en 0."
    except Exception as e:
        return f"Error al vaciar: {str(e)}"

def limpiar_memoria_chat() -> str:
    """Borra únicamente la memoria de la conversación actual de la IA cuando el usuario le pide reiniciar el chat."""
    global RESET_CHAT
    RESET_CHAT = True
    return "Memoria del chat reiniciada con éxito."

# --- RUTAS DE LA APLICACIÓN ---
@app.route('/')
def index():
    conn = obtener_conexion()
    pagos_db = conn.execute('SELECT * FROM pagos ORDER BY id DESC').fetchall()
    conn.close()

    pagos = [dict(p) for p in pagos_db]
    totales_categoria = {}

    hoy = datetime.now().strftime('%Y-%m-%d')
    resumen_hoy = {
        "movimientos_hechos": 0,
        "ingresos": 0.0,
        "egresos": 0.0,
        "saldo": 0.0,
        "por_cobrar": 0.0
    }

    for p in pagos:
        estatus_p = p['estatus']
        monto_p = float(p['monto'])
        if estatus_p in ['Pendiente por cobrar', 'Abono'] and monto_p > 0:
            resumen_hoy['por_cobrar'] += monto_p

        cat = p['categoria']
        p['es_ingreso'] = True if 'ingreso' in cat.lower() or 'venta' in cat.lower() else False

        if not p['es_ingreso']:
            totales_categoria[cat] = totales_categoria.get(cat, 0) + monto_p

        if p['fecha'] and str(p['fecha']).startswith(hoy):
            resumen_hoy['movimientos_hechos'] += 1
            if estatus_p in ['Pagado']:
                if p['es_ingreso']:
                    resumen_hoy['ingresos'] += monto_p
                else:
                    resumen_hoy['egresos'] += monto_p

    resumen_hoy['saldo'] = resumen_hoy['ingresos'] - resumen_hoy['egresos']
    tasas_vivas = obtener_tasas()

    return render_template('index.html', pagos=pagos, totales=totales_categoria, resumen_hoy=resumen_hoy, tasas=tasas_vivas)

@app.route('/agregar', methods=['POST'])
def agregar_pago():
    monto_str = request.form.get('monto')
    concepto = request.form.get('concepto')
    empresa = request.form.get('empresa')
    categoria = request.form.get('categoria')
    estatus = request.form.get('estatus')
    tipo_tasa = request.form.get('tipo_tasa')
    tasa_valor = request.form.get('tasa_valor')

    origen_str = request.form.get('origen')
    if origen_str == 'Otra':
        origen_str = request.form.get('origen_otro')

    if monto_str and concepto:
        monto = limpiar_numero(monto_str)
        tasa_num = limpiar_numero(tasa_valor) if tasa_valor else 1.0
        monto_bs = round(monto * tasa_num, 2)

        conn = obtener_conexion()
        conn.execute('''
            INSERT INTO pagos (monto, monto_bs, tasa_usada, concepto, origen, empresa, categoria, estatus)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (monto, monto_bs, f"{tipo_tasa} ({tasa_num:.2f})", concepto, origen_str, empresa, categoria, estatus))
        conn.execute("INSERT OR IGNORE INTO categorias (nombre) VALUES (?)", (categoria.strip(),))
        conn.execute("INSERT OR IGNORE INTO cuentas (nombre) VALUES (?)", (origen_str.strip(),))
        conn.commit()
        conn.close()

    return redirect(url_for('index'))

@app.route('/editar/<int:id>', methods=['POST'])
def editar_pago(id):
    fecha = request.form.get('fecha')
    empresa = request.form.get('empresa')
    concepto = request.form.get('concepto')
    monto_str = request.form.get('monto')
    origen = request.form.get('origen')
    categoria = request.form.get('categoria')
    estatus = request.form.get('estatus')

    monto = limpiar_numero(monto_str)

    conn = obtener_conexion()
    pago = conn.execute('SELECT * FROM pagos WHERE id = ?', (id,)).fetchone()

    if pago:
        tasa_str = pago['tasa_usada']
        try:
            match = re.search(r'\(([\d\.]+)\)', tasa_str)
            tasa_num = float(match.group(1)) if match else 1.0
        except:
            tasa_num = 1.0

        monto_bs = round(monto * tasa_num, 2)

        fecha_actual = str(pago['fecha'])
        if fecha and len(fecha_actual) > 10:
            hora = fecha_actual[10:]
            fecha_nueva = f"{fecha}{hora}"
        else:
            fecha_nueva = f"{fecha} 12:00:00"

        conn.execute('''
            UPDATE pagos 
            SET fecha=?, empresa=?, concepto=?, monto=?, monto_bs=?, origen=?, categoria=?, estatus=?
            WHERE id=?
        ''', (fecha_nueva, empresa, concepto, monto, monto_bs, origen, categoria, estatus, id))
        conn.commit()

    conn.close()
    return redirect(url_for('index'))

@app.route('/eliminar/<int:id>', methods=['POST'])
def eliminar_pago(id):
    conn = obtener_conexion()
    conn.execute('DELETE FROM pagos WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/vaciar_historial', methods=['POST'])
def vaciar_historial_route():
    conn = obtener_conexion()
    conn.execute('DELETE FROM pagos')
    conn.execute("DELETE FROM sqlite_sequence WHERE name='pagos'")
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/exportar')
def exportar_excel():
    conn = obtener_conexion()
    pagos_db = conn.execute('SELECT * FROM pagos ORDER BY id DESC').fetchall()
    conn.close()

    si = StringIO()
    cw = csv.writer(si, delimiter=',', quoting=csv.QUOTE_MINIMAL) 
    cw.writerow(['Fecha y Hora', 'Empresa/Entidad', 'Concepto', 'Origen del Dinero', 'Categoria', 'Monto ($)', 'Monto (Bs)', 'Tasa', 'Estatus'])

    for p in pagos_db:
        fecha_corta = str(p['fecha'])[:16]
        monto_usd_str = "{:,.2f}".format(p['monto']).replace(',', 'X').replace('.', ',').replace('X', '.')
        monto_ves_str = "{:,.2f}".format(p['monto_bs']).replace(',', 'X').replace('.', ',').replace('X', '.')
        cw.writerow([fecha_corta, p['empresa'], p['concepto'], p['origen'], p['categoria'], f"${monto_usd_str}", f"Bs. {monto_ves_str}", p['tasa_usada'], p['estatus']])

    output = si.getvalue().encode('utf-8-sig')

    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=Reporte_Cuentas_y_Pagos.csv"}
    )

# --- RUTAS DE LA APLICACIÓN: ASISTENTE DE IA ---
@app.route('/api/ia-chat', methods=['POST'])
def ia_chat():
    global chat_session, RESET_CHAT

    try:
        mensaje_usuario = request.form.get('mensaje', '')
        imagenes_adjuntas = request.files.getlist('imagenes')
        audio_adjunto = request.files.get('audio')

        if not mensaje_usuario and not imagenes_adjuntas and not audio_adjunto:
            return jsonify({"success": True, "respuesta": "¿Se te comió la lengua el ratón o vas a preguntar algo?"})

        tasas = obtener_tasas()
        categorias_list = obtener_lista_categorias()
        cuentas_list = obtener_lista_cuentas()

        system_instruction = f"""Eres la Asistente Financiera Inteligente, Administradora y Colega Ejecutiva de Dyvenplast.
No eres un bot predecible ni robótico; eres una trabajadora proactiva, resolutiva, simpática y con humor 100% ajustable al tono del usuario.

CONOCIMIENTO GENERAL Y VERSATILIDAD:
- Puedes responder a cualquier tema general, preguntas de cultura general, curiosidades o conversación casual sin colapsar ni salirte de tu rol.
- Si la conversación es informal, fluye con gracia y reconduce amablemente al trabajo cuando sea oportuno.

TASAS DE CAMBIO OFICIALES DEL DÍA:
- BCV: {tasas['BCV']} Bs/$
- USDT (Binance): {tasas['USDT']} Bs/$
- Euro: {tasas['Euro']} Bs/$

LISTAS OFICIALES REGISTRADAS EN LA BASE DE DATOS:
- CATEGORÍAS DISPONIBLES ({len(categorias_list)}): {", ".join(categorias_list)}
- CUENTAS / ORIGENES REGISTRADOS ({len(cuentas_list)}): {", ".join(cuentas_list)}

PROTOCOLO DE REGISTRO E INFORMACIÓN FALTANTE:
1. Al recibir un texto, nota de voz o foto (comprobante de pago, transferencia, factura):
   * Extrae los datos visibles: Monto ($ o Bs), Empresa/Entidad, Concepto, Origen y Estatus.
   * Si detectas el monto en Bs, conviértelo a $ usando la tasa correspondiente (BCV por defecto).
2. MANEJO DE AMBIGÜEDADES (¡REGLA DE ORO!):
   * Si falta la CATEGORÍA o la CUENTA ORIGEN en la imagen/audio/mensaje: NO inventes ni adivines.
   * Detente y pregúntale directamente al usuario, mostrándole las opciones disponibles e indicando que también puede indicarte una nueva categoría o cuenta para agregarla.
   * Si el usuario te pide crear una categoría nueva, ejecuta la herramienta 'agregar_categoria' y luego registra el movimiento.

HERRAMIENTAS Y ACCIONES DIRECTAS:
- 'registrar_abono': ÚSALA OBLIGATORIAMENTE cuando el usuario te indique que un cliente está pagando, abonando o liquidando una deuda. Modificará el registro pendiente original y registrará el ingreso nuevo.
- 'registrar_movimiento': Úsala SOLO para registrar nuevos ingresos limpios o gastos desde cero (no la uses para abonos de cuentas pendientes).
- 'editar_movimiento': Para modificar cualquier campo de un registro por su ID.
- 'eliminar_movimiento': Para borrar un registro individual por su ID.
- 'agregar_categoria' / 'eliminar_categoria': Para administrar la lista de categorías.
- 'consultar_datos': Para buscar registros o ver el historial.
- 'limpiar_memoria_chat': Si el usuario pide reiniciar o borrar el chat.
- 'vaciar_historial': Para borrar TODA la base de datos (SOLO previa confirmación explícita del usuario)."""

        if chat_session is None:
            chat_session = client.chats.create(
                model="gemini-3.6-flash",
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.7,
                    tools=[
                        registrar_abono,
                        registrar_movimiento,
                        editar_movimiento,
                        eliminar_movimiento,
                        agregar_categoria,
                        eliminar_categoria,
                        consultar_datos,
                        vaciar_historial,
                        limpiar_memoria_chat
                    ]
                ),
            )

        partes_mensaje = []
        texto_final = f"Usuario: {mensaje_usuario if mensaje_usuario else '[Archivo adjunto]'}"
        partes_mensaje.append(texto_final)

        if audio_adjunto and audio_adjunto.filename != '':
            audio_bytes = audio_adjunto.read()
            mime_type = audio_adjunto.mimetype
            if not mime_type or not mime_type.startswith('audio/'):
                mime_type = 'audio/webm'
            partes_mensaje.append(types.Part.from_bytes(data=audio_bytes, mime_type=mime_type))

        for img in imagenes_adjuntas:
            if img and img.filename != '':
                img_bytes = img.read()
                mime = img.mimetype
                if not mime or not mime.startswith('image/'):
                    mime = 'image/jpeg'
                partes_mensaje.append(types.Part.from_bytes(data=img_bytes, mime_type=mime))

        response = chat_session.send_message(partes_mensaje)
        texto_respuesta = response.text if response and response.text else "Procesado correctamente."

        clear_ui = False
        if RESET_CHAT:
            chat_session = None
            RESET_CHAT = False
            clear_ui = True

        return jsonify({
            "success": True,
            "respuesta": texto_respuesta,
            "reply": texto_respuesta,
            "message": texto_respuesta,
            "clear_chat_ui": clear_ui
        })

    except Exception as e:
        error_str = str(e)
        if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "quota" in error_str.lower():
            mensaje_error = "El sistema está procesando demasiadas solicitudes en este momento. Por favor, espera unos 30 segundos e intenta de nuevo."
        elif "503" in error_str or "UNAVAILABLE" in error_str:
            mensaje_error = "Los servidores de procesamiento están ocupados en este instante. Reintenta en unos segundos."
        else:
            mensaje_error = f"[Aviso del sistema] Error: {error_str}"

        return jsonify({
            "success": True,
            "respuesta": mensaje_error,
            "reply": mensaje_error,
            "message": mensaje_error
        })

if __name__ == '__main__':
    crear_tabla()
    port = int(os.environ.get("PORT", 5005))
    app.run(host='0.0.0.0', port=port, debug=False)