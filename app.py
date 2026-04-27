from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup
import re
import os
import logging

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# SERVIR FRONTEND
@app.route("/")
def home():
    return send_file("app.html")

@app.route("/ping")
def ping():
    return jsonify({"status":"ok","version":"9.0"})

# ---------- HELPERS ----------

HEADERS = {
    "User-Agent":"Mozilla/5.0"
}

def limpiar_precio(txt):
    if not txt:
        return None

    txt = re.sub(r"[^\d]", "", txt)

    try:
        val = float(txt)
        return val if val > 100 else None
    except:
        return None

def resultado_base(nombre, url, barcode):
    return {
        "supermercado": nombre,
        "url_supermercado": url,
        "barcode": barcode,
        "nombre": None,
        "precio": None,
        "url_producto": url,
        "imagen": None,
        "disponible": False,
        "error": "No encontrado"
    }

def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            return r.text
    except:
        pass
    return None

# ---------- SCRAPERS ----------

def scrape_superseis(barcode):
    base = "https://www.superseis.com.py"
    r = resultado_base("Superseis", base, barcode)

    url = f"{base}/search?search={barcode}"
    html = fetch(url)

    if not html:
        r["error"] = "Sin conexión"
        return r

    soup = BeautifulSoup(html, "html.parser")

    txt = soup.get_text(" ", strip=True)

    precio = re.search(r'Gs\.?\s*([\d\.]+)', txt)

    if precio:
        r["precio"] = limpiar_precio(precio.group(1))
        r["nombre"] = barcode
        r["disponible"] = True
        r["error"] = None

    r["url_producto"] = url
    return r

def scrape_salemma(barcode):
    base = "https://www.salemmaonline.com.py"
    r = resultado_base("Salemma", base, barcode)

    url = f"{base}/search?q={barcode}"
    html = fetch(url)

    if not html:
        r["error"] = "Sin conexión"
        return r

    txt = BeautifulSoup(html,"html.parser").get_text(" ", strip=True)

    precio = re.search(r'Gs\.?\s*([\d\.]+)', txt)

    if precio:
        r["precio"] = limpiar_precio(precio.group(1))
        r["nombre"] = barcode
        r["disponible"] = True
        r["error"] = None

    r["url_producto"] = url
    return r

def scrape_fake(nombre,url,barcode):
    r = resultado_base(nombre,url,barcode)
    r["error"] = "Consultar manualmente"
    r["url_producto"] = f"{url}/search?q={barcode}"
    return r

# ---------- RESUMEN ----------

def resumen(barcode, resultados):
    precios = [x["precio"] for x in resultados if x["precio"]]

    return {
        "barcode": barcode,
        "total_supermercados": len(resultados),
        "encontrado_en": len(precios),
        "precio_minimo": min(precios) if precios else None,
        "precio_maximo": max(precios) if precios else None,
        "ahorro_maximo": max(precios)-min(precios) if len(precios)>=2 else 0,
        "resultados": resultados
    }

# ---------- API ----------

@app.route("/buscar")
def buscar():

    barcode = request.args.get("barcode","").strip()

    if not barcode:
        return jsonify({"error":"barcode requerido"}),400

    funciones = [
        lambda: scrape_superseis(barcode),
        lambda: scrape_salemma(barcode),
        lambda: scrape_fake("Biggie","https://biggie.com.py",barcode),
        lambda: scrape_fake("Stock","https://www.stock.com.py",barcode),
        lambda: scrape_fake("Real","https://www.realonline.com.py",barcode),
    ]

    resultados = []

    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = [ex.submit(fn) for fn in funciones]

        for f in as_completed(futures):
            try:
                resultados.append(f.result())
            except Exception as e:
                logger.error(str(e))

    return jsonify(resumen(barcode,resultados))

@app.route("/buscar-custom", methods=["POST"])
def buscar_custom():
    data = request.get_json()
    barcode = data.get("barcode","").strip()

    return buscar()

if __name__ == "__main__":
    port = int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0",port=port)
