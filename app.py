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

@app.route("/")
def home():
    return send_file("app.html")

@app.route("/ping")
def ping():
    return jsonify({"status": "ok", "version": "9.0"})

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code == 200:
            return r.text
    except:
        return None
    return None

def limpiar_precio(txt):
    if not txt:
        return None
    txt = re.sub(r"[^\d]", "", txt)
    try:
        n = float(txt)
        return n if n > 100 else None
    except:
        return None

def base(nombre, url, barcode):
    return {
        "supermercado": nombre,
        "url_supermercado": url,
        "barcode": barcode,
        "nombre": barcode,
        "precio": None,
        "url_producto": url,
        "imagen": None,
        "disponible": False,
        "error": "No encontrado"
    }

def buscar_precio_texto(html):
    txt = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)

    patrones = [
        r'Gs\.?\s*([\d\.]+)',
        r'₲\s*([\d\.]+)',
    ]

    for p in patrones:
        m = re.search(p, txt, re.I)
        if m:
            return limpiar_precio(m.group(1))
    return None

def scrape_superseis(barcode):
    url = f"https://www.superseis.com.py/search?search={barcode}"
    r = base("Superseis", "https://www.superseis.com.py", barcode)
    r["url_producto"] = url

    html = fetch(url)
    if not html:
        r["error"] = "Sin conexión"
        return r

    precio = buscar_precio_texto(html)
    if precio:
        r["precio"] = precio
        r["disponible"] = True
        r["error"] = None

    return r

def scrape_salemma(barcode):
    url = f"https://www.salemmaonline.com.py/search?q={barcode}"
    r = base("Salemma", "https://www.salemmaonline.com.py", barcode)
    r["url_producto"] = url

    html = fetch(url)
    if not html:
        r["error"] = "Sin conexión"
        return r

    precio = buscar_precio_texto(html)
    if precio:
        r["precio"] = precio
        r["disponible"] = True
        r["error"] = None

    return r

def manual(nombre, sitio, barcode):
    r = base(nombre, sitio, barcode)
    r["url_producto"] = f"{sitio}/search?q={barcode}"
    r["error"] = "Consultar manualmente"
    return r

def resumen(barcode, resultados):
    precios = [x["precio"] for x in resultados if x["precio"]]

    return {
        "barcode": barcode,
        "total_supermercados": len(resultados),
        "encontrado_en": len(precios),
        "precio_minimo": min(precios) if precios else None,
        "precio_maximo": max(precios) if precios else None,
        "ahorro_maximo": (max(precios)-min(precios)) if len(precios) >= 2 else 0,
        "resultados": resultados
    }

@app.route("/buscar")
def buscar():
    barcode = request.args.get("barcode", "").strip()

    if not barcode:
        return jsonify({"error": "barcode requerido"}), 400

    tareas = [
        lambda: scrape_superseis(barcode),
        lambda: scrape_salemma(barcode),
        lambda: manual("Biggie", "https://biggie.com.py", barcode),
        lambda: manual("Stock", "https://www.stock.com.py", barcode),
        lambda: manual("Real", "https://www.realonline.com.py", barcode),
    ]

    resultados = []

    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = [ex.submit(t) for t in tareas]

        for f in as_completed(futures):
            try:
                resultados.append(f.result())
            except Exception as e:
                logger.error(str(e))

    return jsonify(resumen(barcode, resultados))

@app.route("/buscar-custom", methods=["POST"])
def buscar_custom():
    data = request.get_json()
    barcode = data.get("barcode", "").strip()
    request.args = {"barcode": barcode}
    return buscar()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
