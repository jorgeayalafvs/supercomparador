from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re
import time
import logging
import os
from urllib.parse import urlparse, urljoin

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-PY,es;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

def limpiar_precio(texto):
    if not texto: return None
    limpio = re.sub(r"[^\d.,]", "", texto.strip())
    if not limpio: return None
    if "," in limpio:
        partes = limpio.split(",")
        limpio = partes[0].replace(".", "") + "." + (partes[1] if len(partes) > 1 else "0")
    else:
        partes = limpio.split(".")
        limpio = "".join(partes) if (len(partes) > 1 and len(partes[-1]) == 3) else limpio.replace(".", "")
    try:
        val = float(limpio)
        return val if val > 100 else None
    except ValueError:
        return None

def build_result(supermercado, url_base, barcode):
    return {
        "supermercado": supermercado, "url_supermercado": url_base,
        "barcode": barcode, "nombre": None, "precio": None,
        "url_producto": f"{url_base.rstrip('/')}/search?q={barcode}",
        "imagen": None, "disponible": False, "error": None,
    }

def fetch(url, timeout=15):
    try:
        r = SESSION.get(url, timeout=timeout, allow_redirects=True)
        return BeautifulSoup(r.text, "html.parser") if r.status_code == 200 else None
    except Exception as e:
        logger.warning(f"fetch error {url}: {e}")
        return None

def extraer_precio(soup):
    selectores = [
        "span.price-item--regular", "span.price-item--sale", "span.money",
        ".product__price span", ".priceText", "[class*='sellingPrice']",
        ".skuBestPrice", ".special-price .price", ".regular-price .price",
        "[data-price-type='finalPrice'] .price", ".price-wrapper .price",
    ]
    for sel in selectores:
        try:
            el = soup.select_one(sel)
            if el:
                p = limpiar_precio(el.get_text())
                if p: return p
        except Exception:
            continue
    for el in soup.find_all(class_=re.compile(r'price|precio', re.I)):
        p = limpiar_precio(el.get_text(strip=True))
        if p and p > 100: return p
    return None

def extraer_nombre(soup):
    h1 = soup.select_one("h1")
    return h1.get_text(strip=True) if h1 else None

def scrape_shopify(barcode, base, nombre):
    r = build_result(nombre, base, barcode)
    # Intentar API JSON de Shopify primero
    try:
        api = f"{base.rstrip('/')}/search?q={barcode}&type=product&view=json"
        resp = SESSION.get(api, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            products = data.get("products", [])
            if products:
                prod = products[0]
                r["nombre"] = prod.get("title")
                variants = prod.get("variants", [{}])
                precio_raw = str(variants[0].get("price", "")) if variants else ""
                if precio_raw:
                    r["precio"] = limpiar_precio(precio_raw)
                r["url_producto"] = f"{base.rstrip('/')}/products/{prod.get('handle', '')}"
                r["disponible"] = r["precio"] is not None
                return r
    except Exception:
        pass

    # Fallback HTML
    search_url = f"{base.rstrip('/')}/search?q={barcode}&type=product"
    r["url_producto"] = search_url
    soup = fetch(search_url)
    if not soup:
        r["error"] = "No se pudo conectar"
        return r
    link = soup.select_one("a[href*='/products/']") or soup.select_one("a.product-item__title")
    if not link:
        r["error"] = "Producto no encontrado"
        return r
    prod_url = urljoin(base, link.get("href", ""))
    r["url_producto"] = prod_url
    time.sleep(0.5)
    ps = fetch(prod_url)
    if ps:
        r["nombre"] = extraer_nombre(ps)
        r["precio"] = extraer_precio(ps)
        r["disponible"] = r["precio"] is not None
    if not r["disponible"]:
        r["error"] = "Precio no encontrado"
    return r

def scrape_vtex(barcode, base, nombre):
    r = build_result(nombre, base, barcode)
    # API VTEX por EAN (código de barras)
    try:
        api = f"{base.rstrip('/')}/api/catalog_system/pub/products/search?fq=alternateIds_Ean:{barcode}&_from=0&_to=2"
        resp = SESSION.get(api, timeout=12)
        if resp.status_code == 200:
            products = resp.json()
            if products:
                prod = products[0]
                r["nombre"] = prod.get("productName") or prod.get("name")
                items = prod.get("items", [])
                if items:
                    sellers = items[0].get("sellers", [{}])
                    if sellers:
                        comm = sellers[0].get("commertialOffer", {})
                        precio = comm.get("Price") or comm.get("ListPrice")
                        if precio: r["precio"] = float(precio)
                    imgs = items[0].get("images", [])
                    if imgs: r["imagen"] = imgs[0].get("imageUrl")
                link = prod.get("link", "")
                if link:
                    r["url_producto"] = link if link.startswith("http") else urljoin(base, "/" + link.strip("/"))
                r["disponible"] = r["precio"] is not None
                return r
    except Exception as e:
        logger.warning(f"VTEX API falló {nombre}: {e}")

    # Fallback HTML
    search_url = f"{base.rstrip('/')}/search?q={barcode}"
    r["url_producto"] = search_url
    soup = fetch(search_url)
    if not soup:
        r["error"] = "No se pudo conectar"
        return r
    link = (soup.select_one("a.product-name") or
            soup.select_one("[class*='ProductName'] a") or
            soup.select_one("h2 a[href*='/p']"))
    if link:
        prod_url = urljoin(base, link.get("href", ""))
        r["url_producto"] = prod_url
        time.sleep(0.5)
        ps = fetch(prod_url)
        if ps:
            r["nombre"] = extraer_nombre(ps)
            r["precio"] = extraer_precio(ps)
            r["disponible"] = r["precio"] is not None
    if not r["disponible"]:
        r["error"] = "Producto no encontrado"
    return r

def scrape_magento(barcode, base, nombre):
    r = build_result(nombre, base, barcode)
    search_url = f"{base.rstrip('/')}/catalogsearch/result/?q={barcode}"
    r["url_producto"] = search_url
    soup = fetch(search_url)
    if not soup:
        r["error"] = "No se pudo conectar"
        return r
    link = (soup.select_one("a.product-item-link") or
            soup.select_one(".product-item-name a") or
            soup.select_one("h2.product-name a"))
    if not link:
        r["error"] = "Producto no encontrado"
        return r
    prod_url = urljoin(base, link.get("href", ""))
    r["url_producto"] = prod_url
    time.sleep(0.5)
    ps = fetch(prod_url)
    if ps:
        r["nombre"] = extraer_nombre(ps)
        r["precio"] = extraer_precio(ps)
        r["disponible"] = r["precio"] is not None
    if not r["disponible"]:
        r["error"] = "Precio no encontrado"
    return r

def scrape_generico(barcode, nombre, url_base):
    r = build_result(nombre, url_base, barcode)
    # Probar API VTEX primero
    try:
        api = f"{url_base.rstrip('/')}/api/catalog_system/pub/products/search?fq=alternateIds_Ean:{barcode}&_from=0&_to=2"
        resp = SESSION.get(api, timeout=8)
        if resp.status_code == 200 and resp.json():
            return scrape_vtex(barcode, url_base, nombre)
    except Exception:
        pass
    # Probar patrones comunes
    patterns = [
        f"{url_base.rstrip('/')}/search?q={barcode}",
        f"{url_base.rstrip('/')}/catalogsearch/result/?q={barcode}",
    ]
    for url in patterns:
        soup = fetch(url)
        if not soup: continue
        r["url_producto"] = url
        link = (soup.select_one("a[href*='/products/']") or
                soup.select_one("a[href*='/p']") or
                soup.select_one("a.product-item-link") or
                soup.select_one("a.product-name") or
                soup.select_one("h2 a"))
        if not link: continue
        prod_url = urljoin(url_base, link.get("href", ""))
        r["url_producto"] = prod_url
        time.sleep(0.5)
        ps = fetch(prod_url)
        if not ps: continue
        r["nombre"] = extraer_nombre(ps)
        r["precio"] = extraer_precio(ps)
        r["disponible"] = r["precio"] is not None
        if r["disponible"]: return r
    if not r["disponible"]:
        r["error"] = "Producto no encontrado"
    return r

SCRAPERS_MAP = {
    "biggie.com.py": lambda b: scrape_shopify(b, "https://biggie.com.py", "Biggie"),
    "biggie.com":    lambda b: scrape_shopify(b, "https://biggie.com.py", "Biggie"),
    "salemmaonline.com.py": lambda b: scrape_shopify(b, "https://www.salemmaonline.com.py", "Salemma"),
    "stock.com.py":  lambda b: scrape_vtex(b, "https://www.stock.com.py", "Stock"),
    "realonline.com.py": lambda b: scrape_vtex(b, "https://www.realonline.com.py", "Real"),
    "superseis.com.py": lambda b: scrape_magento(b, "https://www.superseis.com.py", "Superseis"),
}

def get_scraper(url):
    domain = urlparse(url).netloc.lower().replace("www.", "")
    return SCRAPERS_MAP.get(domain, None)

def calcular_resumen(barcode, resultados):
    precios = [r["precio"] for r in resultados if r["precio"]]
    return {
        "barcode": barcode,
        "total_supermercados": len(resultados),
        "encontrado_en": len(precios),
        "precio_minimo": min(precios) if precios else None,
        "precio_maximo": max(precios) if precios else None,
        "ahorro_maximo": round(max(precios) - min(precios), 0) if len(precios) >= 2 else 0,
        "resultados": resultados,
    }

@app.route("/", methods=["GET"])
def home():
    return jsonify({"api": "SuperComparador Paraguay", "version": "3.0", "estado": "online"})

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "version": "3.0"})

@app.route("/buscar", methods=["GET"])
def buscar():
    barcode = request.args.get("barcode", "").strip()
    if not barcode:
        return jsonify({"error": "barcode requerido"}), 400
    resultados = []
    for fn in [
        lambda b: scrape_shopify(b, "https://biggie.com.py", "Biggie"),
        lambda b: scrape_vtex(b, "https://www.stock.com.py", "Stock"),
        lambda b: scrape_magento(b, "https://www.superseis.com.py", "Superseis"),
        lambda b: scrape_shopify(b, "https://www.salemmaonline.com.py", "Salemma"),
        lambda b: scrape_vtex(b, "https://www.realonline.com.py", "Real"),
    ]:
        try: resultados.append(fn(barcode))
        except Exception as e: logger.error(str(e))
    return jsonify(calcular_resumen(barcode, resultados))

@app.route("/buscar-custom", methods=["POST"])
def buscar_custom():
    data = request.get_json()
    if not data: return jsonify({"error": "Body JSON requerido"}), 400
    barcode = data.get("barcode", "").strip()
    supermercados = data.get("supermercados", [])
    if not barcode: return jsonify({"error": "barcode requerido"}), 400
    if not supermercados: return jsonify({"error": "supermercados requerido"}), 400
    resultados = []
    for item in supermercados:
        nombre = item.get("nombre", "Supermercado")
        url = item.get("url", "")
        if not url: continue
        fn = get_scraper(url)
        res = fn(barcode) if fn else scrape_generico(barcode, nombre, url)
        res["supermercado"] = nombre
        resultados.append(res)
    return jsonify(calcular_resumen(barcode, resultados))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
