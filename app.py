from flask import Flask, jsonify, request
from flask_cors import CORS
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup
import re
import os
import logging
import json

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "es-PY,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        logger.warning(f"fetch error {url}: {e}")
    return None

def limpiar_precio(txt):
    if not txt: return None
    txt = re.sub(r"[^\d]", "", str(txt))
    try:
        n = float(txt)
        return n if n > 500 else None
    except:
        return None

def base_result(nombre, url, barcode):
    return {
        "supermercado": nombre,
        "url_supermercado": url,
        "barcode": barcode,
        "nombre": None,
        "precio": None,
        "url_producto": f"{url}/search?q={barcode}",
        "imagen": None,
        "disponible": False,
        "error": "No encontrado"
    }

def extraer_jsonld_precio(html):
    """Extrae precio de JSON-LD schema.org"""
    try:
        soup = BeautifulSoup(html, "html.parser")
        for sc in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(sc.string or "")
                if isinstance(data, list): data = data[0]
                if data.get("@type") == "Product":
                    nombre = data.get("name")
                    offers = data.get("offers", {})
                    if isinstance(offers, list): offers = offers[0]
                    precio = limpiar_precio(str(offers.get("price", "")))
                    if precio:
                        return nombre, precio
            except:
                continue
    except:
        pass
    return None, None

def extraer_precio_texto(html):
    """Busca precio en texto visible de la página"""
    try:
        soup = BeautifulSoup(html, "html.parser")
        # Eliminar nav/header/footer para evitar falsos positivos
        for tag in soup.find_all(["nav", "header", "footer", "script", "style"]):
            tag.decompose()
        txt = soup.get_text(" ", strip=True)
        for pat in [r'[₲Gg][Ss]?\.?\s*([\d\.]+)', r'([\d]{4,7})\s*[₲Gg]']:
            m = re.search(pat, txt, re.I)
            if m:
                p = limpiar_precio(m.group(1))
                if p: return p
    except:
        pass
    return None

# ══════════════════════════════════════════════
# SUPERSEIS ✅ Funciona - JSON-LD en página producto
# ══════════════════════════════════════════════
def scrape_superseis(barcode):
    r = base_result("Superseis", "https://www.superseis.com.py", barcode)
    search_url = f"https://www.superseis.com.py/search?search={barcode}"
    r["url_producto"] = search_url

    html = fetch(search_url)
    if not html:
        r["error"] = "Sin conexión"
        return r

    soup = BeautifulSoup(html, "html.parser")

    # Buscar link /product/
    prod_link = None
    for a in soup.find_all("a", href=True):
        if "/product/" in a["href"]:
            prod_link = "https://www.superseis.com.py" + a["href"] if a["href"].startswith("/") else a["href"]
            break

    if prod_link:
        r["url_producto"] = prod_link
        html2 = fetch(prod_link)
        if html2:
            nombre, precio = extraer_jsonld_precio(html2)
            if nombre: r["nombre"] = nombre
            if precio:
                r["precio"] = precio
                r["disponible"] = True
                r["error"] = None
                return r
            # Fallback texto
            precio2 = extraer_precio_texto(html2)
            if precio2:
                r["precio"] = precio2
                r["disponible"] = True
                r["error"] = None
                soup2 = BeautifulSoup(html2, "html.parser")
                h1 = soup2.select_one("h1")
                if h1: r["nombre"] = h1.get_text(strip=True)
    else:
        # Intentar precio directo en página de búsqueda
        precio = extraer_precio_texto(html)
        if precio:
            r["precio"] = precio
            r["disponible"] = True
            r["error"] = None

    return r

# ══════════════════════════════════════════════
# SALEMMA ✅ Funciona - verifica barcode en página
# ══════════════════════════════════════════════
def scrape_salemma(barcode):
    r = base_result("Salemma", "https://www.salemmaonline.com.py", barcode)
    search_url = f"https://www.salemmaonline.com.py/search?q={barcode}"
    r["url_producto"] = search_url

    html = fetch(search_url)
    if not html:
        r["error"] = "Sin conexión"
        return r

    soup = BeautifulSoup(html, "html.parser")

    # Recolectar links /producto/
    prod_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/producto/" in href:
            full = "https://www.salemmaonline.com.py" + href if href.startswith("/") else href
            prod_links.append(full)

    if not prod_links:
        r["error"] = "Producto no encontrado"
        return r

    # Verificar que el barcode aparece en la página del producto
    for prod_link in prod_links[:5]:
        html2 = fetch(prod_link)
        if not html2: continue
        if barcode not in html2: continue  # No es el producto correcto

        r["url_producto"] = prod_link
        soup2 = BeautifulSoup(html2, "html.parser")

        h1 = soup2.select_one("h1")
        if h1: r["nombre"] = h1.get_text(strip=True)

        precio = extraer_precio_texto(html2)
        if precio:
            r["precio"] = precio
            r["disponible"] = True
            r["error"] = None

            img = soup2.select_one("img[src*='.webp'], img[src*='.jpg'], img[src*='.png']")
            if img:
                src = img.get("src", "")
                if src and "logo" not in src.lower():
                    r["imagen"] = src if src.startswith("http") else "https://www.salemmaonline.com.py" + src
        return r

    r["error"] = "Producto no encontrado"
    return r

# ══════════════════════════════════════════════
# BIGGIE / STOCK / REAL — link manual
# (bloqueados desde servidores en USA)
# ══════════════════════════════════════════════
def manual(nombre, sitio, barcode):
    r = base_result(nombre, sitio, barcode)
    r["url_producto"] = f"{sitio}/search?q={barcode}"
    r["error"] = "Consultar manualmente"
    return r

# ══════════════════════════════════════════════
# GENÉRICO para supers agregados manualmente
# ══════════════════════════════════════════════
def scrape_generico(nombre, url_base, barcode):
    r = base_result(nombre, url_base, barcode)
    patterns = [
        f"{url_base.rstrip('/')}/search?q={barcode}",
        f"{url_base.rstrip('/')}/search?search={barcode}",
        f"{url_base.rstrip('/')}/buscar?q={barcode}",
    ]
    for url in patterns:
        html = fetch(url)
        if not html or len(html) < 1000: continue
        r["url_producto"] = url

        nombre_ld, precio_ld = extraer_jsonld_precio(html)
        if precio_ld:
            r["nombre"] = nombre_ld
            r["precio"] = precio_ld
            r["disponible"] = True
            r["error"] = None
            return r

        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if any(x in href for x in ["/product/", "/item/", "/p/", "/producto/"]):
                from urllib.parse import urljoin
                prod_url = urljoin(url_base, href)
                html2 = fetch(prod_url)
                if html2:
                    n, p = extraer_jsonld_precio(html2)
                    if not p: p = extraer_precio_texto(html2)
                    if p:
                        r["precio"] = p
                        r["nombre"] = n
                        r["disponible"] = True
                        r["error"] = None
                        r["url_producto"] = prod_url
                        return r
                break

        p = extraer_precio_texto(html)
        if p:
            r["precio"] = p
            r["disponible"] = True
            r["error"] = None
            return r

    r["error"] = "Consultar manualmente"
    return r

# ══════════════════════════════════════════════
# MAPA DE SCRAPERS
# ══════════════════════════════════════════════
from urllib.parse import urlparse

SCRAPERS = {
    "superseis.com.py": lambda b: scrape_superseis(b),
    "salemmaonline.com.py": lambda b: scrape_salemma(b),
    "biggie.com.py": lambda b: manual("Biggie", "https://biggie.com.py", b),
    "biggie.com": lambda b: manual("Biggie", "https://biggie.com.py", b),
    "stock.com.py": lambda b: manual("Stock", "https://www.stock.com.py", b),
    "realonline.com.py": lambda b: manual("Real", "https://www.realonline.com.py", b),
}

def get_scraper(url):
    domain = urlparse(url).netloc.lower().replace("www.", "")
    return SCRAPERS.get(domain, None)

def calcular_resumen(barcode, resultados):
    precios = [x["precio"] for x in resultados if x["precio"]]
    return {
        "barcode": barcode,
        "total_supermercados": len(resultados),
        "encontrado_en": len(precios),
        "precio_minimo": min(precios) if precios else None,
        "precio_maximo": max(precios) if precios else None,
        "ahorro_maximo": round(max(precios) - min(precios), 0) if len(precios) >= 2 else 0,
        "resultados": resultados
    }

# ══════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════

@app.route("/")
def home():
    return jsonify({"api": "SuperComparador Paraguay", "version": "9.0", "estado": "online"})

@app.route("/ping")
def ping():
    return jsonify({"status": "ok", "version": "9.0"})

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

    return jsonify(calcular_resumen(barcode, resultados))

@app.route("/buscar-custom", methods=["POST"])
def buscar_custom():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Body JSON requerido"}), 400
    barcode = data.get("barcode", "").strip()
    supermercados = data.get("supermercados", [])
    if not barcode:
        return jsonify({"error": "barcode requerido"}), 400
    if not supermercados:
        return jsonify({"error": "supermercados requerido"}), 400

    def hacer(item):
        nombre = item.get("nombre", "Supermercado")
        url = item.get("url", "")
        if not url:
            return None
        fn = get_scraper(url)
        res = fn(barcode) if fn else scrape_generico(nombre, url, barcode)
        res["supermercado"] = nombre
        return res

    resultados = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = [ex.submit(hacer, item) for item in supermercados]
        for f in as_completed(futures):
            try:
                res = f.result()
                if res: resultados.append(res)
            except Exception as e:
                logger.error(str(e))

    return jsonify(calcular_resumen(barcode, resultados))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
