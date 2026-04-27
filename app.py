from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re
import time
import logging
import os
import json
from urllib.parse import urlparse, urljoin

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Rotar User-Agents para evitar bloqueos
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]
_ua_index = 0

def get_session():
    global _ua_index
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENTS[_ua_index % len(USER_AGENTS)],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "es-PY,es;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "max-age=0",
    })
    _ua_index += 1
    return s

def limpiar_precio(texto):
    if not texto: return None
    limpio = re.sub(r"[^\d.,]", "", str(texto).strip())
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

def fetch_html(url, timeout=20, referer=None):
    try:
        s = get_session()
        if referer:
            s.headers["Referer"] = referer
        r = s.get(url, timeout=timeout, allow_redirects=True)
        if r.status_code == 200:
            return r.text
        logger.warning(f"HTTP {r.status_code} para {url}")
        return None
    except Exception as e:
        logger.warning(f"fetch error {url}: {e}")
        return None

def fetch_json(url, timeout=15):
    try:
        s = get_session()
        s.headers["Accept"] = "application/json, text/plain, */*"
        s.headers["X-Requested-With"] = "XMLHttpRequest"
        r = s.get(url, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        logger.warning(f"fetch_json error {url}: {e}")
        return None

def extraer_precio_soup(soup):
    selectores = [
        "span.price-item--regular", "span.price-item--sale",
        "span.money", ".product__price span",
        ".priceText", "[class*='sellingPrice']", ".skuBestPrice",
        ".special-price .price", ".regular-price .price",
        "[data-price-type='finalPrice'] .price",
        ".price-wrapper .price", "span.price",
        "[class*='ProductPrice']", "[class*='product-price']",
        ".offer-price", ".sale-price",
    ]
    for sel in selectores:
        try:
            el = soup.select_one(sel)
            if el:
                p = limpiar_precio(el.get_text())
                if p: return p
        except Exception:
            continue
    # Búsqueda por regex en todo el HTML — buscar patrones de precio paraguayo
    texto = soup.get_text()
    patrones = [
        r'Gs\.?\s*([\d\.]+)',
        r'GS\s*([\d\.]+)',
        r'₲\s*([\d\.]+)',
        r'"price":\s*"?([\d\.]+)"?',
        r'"Price":\s*([\d\.]+)',
    ]
    for pat in patrones:
        matches = re.findall(pat, texto)
        for m in matches:
            p = limpiar_precio(m)
            if p and p > 1000:
                return p
    return None

# ══════════════════════════════════════════════
# BIGGIE — Shopify
# ══════════════════════════════════════════════
def scrape_biggie(barcode):
    base = "https://biggie.com.py"
    r = build_result("Biggie", base, barcode)

    # 1. API Shopify JSON
    data = fetch_json(f"{base}/search?q={barcode}&type=product&view=json")
    if data and isinstance(data, dict):
        products = data.get("products", [])
        if products:
            prod = products[0]
            r["nombre"] = prod.get("title")
            variants = prod.get("variants", [])
            if variants:
                precio_raw = variants[0].get("price", "")
                r["precio"] = limpiar_precio(str(precio_raw))
            handle = prod.get("handle", "")
            if handle:
                r["url_producto"] = f"{base}/products/{handle}"
            r["disponible"] = r["precio"] is not None
            if r["disponible"]: return r

    # 2. Buscar producto via /search.json
    data2 = fetch_json(f"{base}/search.json?q={barcode}&type=product")
    if data2:
        results = data2.get("results", [])
        if results:
            prod = results[0]
            r["nombre"] = prod.get("title")
            r["precio"] = limpiar_precio(str(prod.get("price", "")))
            r["url_producto"] = base + prod.get("url", "")
            r["disponible"] = r["precio"] is not None
            if r["disponible"]: return r

    # 3. HTML scraping
    search_url = f"{base}/search?q={barcode}&type=product"
    html = fetch_html(search_url)
    if html:
        soup = BeautifulSoup(html, "html.parser")
        # Buscar en JSON embebido (Shopify lo incluye en el HTML)
        scripts = soup.find_all("script", type="application/json")
        for sc in scripts:
            try:
                jdata = json.loads(sc.string or "")
                if isinstance(jdata, dict) and "products" in jdata:
                    prods = jdata["products"]
                    if prods:
                        p = prods[0]
                        r["nombre"] = p.get("title")
                        vars_ = p.get("variants", [])
                        if vars_:
                            r["precio"] = limpiar_precio(str(vars_[0].get("price", "")))
                        r["disponible"] = r["precio"] is not None
                        if r["disponible"]: return r
            except Exception:
                continue

        link = soup.select_one("a[href*='/products/']")
        if link:
            prod_url = urljoin(base, link["href"].split("?")[0])
            r["url_producto"] = prod_url
            time.sleep(1)
            html2 = fetch_html(prod_url, referer=search_url)
            if html2:
                soup2 = BeautifulSoup(html2, "html.parser")
                h1 = soup2.select_one("h1")
                if h1: r["nombre"] = h1.get_text(strip=True)
                r["precio"] = extraer_precio_soup(soup2)
                r["disponible"] = r["precio"] is not None

    if not r["disponible"]:
        r["error"] = "Producto no encontrado"
    return r

# ══════════════════════════════════════════════
# STOCK — VTEX
# ══════════════════════════════════════════════
def scrape_stock(barcode):
    base = "https://www.stock.com.py"
    r = build_result("Stock", base, barcode)

    # 1. API VTEX por EAN
    data = fetch_json(f"{base}/api/catalog_system/pub/products/search?fq=alternateIds_Ean:{barcode}&_from=0&_to=2")
    if data and len(data) > 0:
        prod = data[0]
        r["nombre"] = prod.get("productName") or prod.get("name")
        items = prod.get("items", [])
        if items:
            sellers = items[0].get("sellers", [])
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
        if r["disponible"]: return r

    # 2. Búsqueda inteligente VTEX
    data2 = fetch_json(f"{base}/api/catalog_system/pub/products/search/{barcode}?_from=0&_to=2")
    if data2 and len(data2) > 0:
        prod = data2[0]
        r["nombre"] = prod.get("productName")
        items = prod.get("items", [])
        if items:
            sellers = items[0].get("sellers", [])
            if sellers:
                comm = sellers[0].get("commertialOffer", {})
                precio = comm.get("Price")
                if precio: r["precio"] = float(precio)
        r["disponible"] = r["precio"] is not None
        if r["disponible"]: return r

    # 3. HTML
    search_url = f"{base}/search?q={barcode}"
    r["url_producto"] = search_url
    html = fetch_html(search_url)
    if html:
        soup = BeautifulSoup(html, "html.parser")
        link = (soup.select_one("a.product-name") or
                soup.select_one("[class*='ProductName'] a") or
                soup.select_one("h2 a"))
        if link:
            prod_url = urljoin(base, link.get("href", ""))
            r["url_producto"] = prod_url
            time.sleep(1)
            html2 = fetch_html(prod_url, referer=search_url)
            if html2:
                soup2 = BeautifulSoup(html2, "html.parser")
                h1 = soup2.select_one("h1")
                if h1: r["nombre"] = h1.get_text(strip=True)
                r["precio"] = extraer_precio_soup(soup2)
                r["disponible"] = r["precio"] is not None

    if not r["disponible"]:
        r["error"] = "Producto no encontrado"
    return r

# ══════════════════════════════════════════════
# SUPERSEIS — Magento
# ══════════════════════════════════════════════
def scrape_superseis(barcode):
    base = "https://www.superseis.com.py"
    r = build_result("Superseis", base, barcode)

    search_url = f"{base}/catalogsearch/result/?q={barcode}"
    r["url_producto"] = search_url
    html = fetch_html(search_url)
    if not html:
        r["error"] = "No se pudo conectar"
        return r

    soup = BeautifulSoup(html, "html.parser")

    # Buscar en JSON-LD
    for sc in soup.find_all("script", type="application/ld+json"):
        try:
            jdata = json.loads(sc.string or "")
            if isinstance(jdata, list):
                jdata = jdata[0]
            if jdata.get("@type") in ["Product", "ItemList"]:
                if jdata.get("@type") == "ItemList":
                    items = jdata.get("itemListElement", [])
                    if items: jdata = items[0].get("item", {})
                r["nombre"] = jdata.get("name")
                offers = jdata.get("offers", {})
                if isinstance(offers, list): offers = offers[0]
                precio = offers.get("price")
                if precio:
                    r["precio"] = limpiar_precio(str(precio))
                    r["disponible"] = r["precio"] is not None
                    if r["disponible"]: return r
        except Exception:
            continue

    link = (soup.select_one("a.product-item-link") or
            soup.select_one(".product-item-name a") or
            soup.select_one("h2.product-name a"))
    if not link:
        r["error"] = "Producto no encontrado"
        return r

    prod_url = urljoin(base, link.get("href", ""))
    r["url_producto"] = prod_url
    time.sleep(1)
    html2 = fetch_html(prod_url, referer=search_url)
    if html2:
        soup2 = BeautifulSoup(html2, "html.parser")
        h1 = soup2.select_one("h1.page-title span, h1")
        if h1: r["nombre"] = h1.get_text(strip=True)

        # JSON-LD en página de producto
        for sc in soup2.find_all("script", type="application/ld+json"):
            try:
                jdata = json.loads(sc.string or "")
                if isinstance(jdata, list): jdata = jdata[0]
                if jdata.get("@type") == "Product":
                    offers = jdata.get("offers", {})
                    if isinstance(offers, list): offers = offers[0]
                    precio = offers.get("price")
                    if precio:
                        r["precio"] = limpiar_precio(str(precio))
                        r["disponible"] = r["precio"] is not None
                        if r["disponible"]: return r
            except Exception:
                continue

        r["precio"] = extraer_precio_soup(soup2)
        r["disponible"] = r["precio"] is not None

    if not r["disponible"]:
        r["error"] = "Precio no encontrado"
    return r

# ══════════════════════════════════════════════
# SALEMMA — Shopify
# ══════════════════════════════════════════════
def scrape_salemma(barcode):
    base = "https://www.salemmaonline.com.py"
    r = build_result("Salemma", base, barcode)

    # API Shopify
    data = fetch_json(f"{base}/search?q={barcode}&type=product&view=json")
    if data and isinstance(data, dict):
        products = data.get("products", [])
        if products:
            prod = products[0]
            r["nombre"] = prod.get("title")
            variants = prod.get("variants", [])
            if variants:
                r["precio"] = limpiar_precio(str(variants[0].get("price", "")))
            handle = prod.get("handle", "")
            if handle: r["url_producto"] = f"{base}/products/{handle}"
            r["disponible"] = r["precio"] is not None
            if r["disponible"]: return r

    # HTML fallback
    search_url = f"{base}/search?q={barcode}&type=product"
    r["url_producto"] = search_url
    html = fetch_html(search_url)
    if html:
        soup = BeautifulSoup(html, "html.parser")
        link = soup.select_one("a[href*='/products/']")
        if link:
            prod_url = urljoin(base, link["href"].split("?")[0])
            r["url_producto"] = prod_url
            time.sleep(1)
            html2 = fetch_html(prod_url, referer=search_url)
            if html2:
                soup2 = BeautifulSoup(html2, "html.parser")
                h1 = soup2.select_one("h1")
                if h1: r["nombre"] = h1.get_text(strip=True)
                r["precio"] = extraer_precio_soup(soup2)
                r["disponible"] = r["precio"] is not None

    if not r["disponible"]:
        r["error"] = "Producto no encontrado"
    return r

# ══════════════════════════════════════════════
# REAL — VTEX
# ══════════════════════════════════════════════
def scrape_real(barcode):
    base = "https://www.realonline.com.py"
    r = build_result("Real", base, barcode)

    data = fetch_json(f"{base}/api/catalog_system/pub/products/search?fq=alternateIds_Ean:{barcode}&_from=0&_to=2")
    if data and len(data) > 0:
        prod = data[0]
        r["nombre"] = prod.get("productName") or prod.get("name")
        items = prod.get("items", [])
        if items:
            sellers = items[0].get("sellers", [])
            if sellers:
                comm = sellers[0].get("commertialOffer", {})
                precio = comm.get("Price") or comm.get("ListPrice")
                if precio: r["precio"] = float(precio)
        link = prod.get("link", "")
        if link:
            r["url_producto"] = link if link.startswith("http") else urljoin(base, "/" + link.strip("/"))
        r["disponible"] = r["precio"] is not None
        if r["disponible"]: return r

    search_url = f"{base}/search?q={barcode}"
    r["url_producto"] = search_url
    html = fetch_html(search_url)
    if html:
        soup = BeautifulSoup(html, "html.parser")
        link = soup.select_one("a.product-name") or soup.select_one("[class*='ProductName'] a")
        if link:
            prod_url = urljoin(base, link.get("href", ""))
            r["url_producto"] = prod_url
            time.sleep(1)
            html2 = fetch_html(prod_url, referer=search_url)
            if html2:
                soup2 = BeautifulSoup(html2, "html.parser")
                h1 = soup2.select_one("h1")
                if h1: r["nombre"] = h1.get_text(strip=True)
                r["precio"] = extraer_precio_soup(soup2)
                r["disponible"] = r["precio"] is not None

    if not r["disponible"]:
        r["error"] = "Producto no encontrado"
    return r

# ══════════════════════════════════════════════
# GENÉRICO
# ══════════════════════════════════════════════
def scrape_generico(barcode, nombre, url_base):
    r = build_result(nombre, url_base, barcode)
    # Intentar VTEX API
    data = fetch_json(f"{url_base.rstrip('/')}/api/catalog_system/pub/products/search?fq=alternateIds_Ean:{barcode}&_from=0&_to=2")
    if data and len(data) > 0:
        return scrape_real.__wrapped__(barcode) if hasattr(scrape_real, '__wrapped__') else scrape_stock(barcode)

    patterns = [
        f"{url_base.rstrip('/')}/search?q={barcode}",
        f"{url_base.rstrip('/')}/catalogsearch/result/?q={barcode}",
        f"{url_base.rstrip('/')}/search?q={barcode}&type=product",
    ]
    for url in patterns:
        html = fetch_html(url)
        if not html: continue
        r["url_producto"] = url
        soup = BeautifulSoup(html, "html.parser")
        link = (soup.select_one("a[href*='/products/']") or
                soup.select_one("a[href*='/p']") or
                soup.select_one("a.product-item-link") or
                soup.select_one("a.product-name") or
                soup.select_one("h2 a"))
        if not link: continue
        prod_url = urljoin(url_base, link.get("href", ""))
        r["url_producto"] = prod_url
        time.sleep(1)
        html2 = fetch_html(prod_url, referer=url)
        if not html2: continue
        soup2 = BeautifulSoup(html2, "html.parser")
        h1 = soup2.select_one("h1")
        if h1: r["nombre"] = h1.get_text(strip=True)
        r["precio"] = extraer_precio_soup(soup2)
        r["disponible"] = r["precio"] is not None
        if r["disponible"]: return r

    if not r["disponible"]:
        r["error"] = "Producto no encontrado"
    return r

SCRAPERS_MAP = {
    "biggie.com.py": lambda b: scrape_biggie(b),
    "biggie.com":    lambda b: scrape_biggie(b),
    "salemmaonline.com.py": lambda b: scrape_salemma(b),
    "stock.com.py":  lambda b: scrape_stock(b),
    "realonline.com.py": lambda b: scrape_real(b),
    "superseis.com.py": lambda b: scrape_superseis(b),
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
    return jsonify({"api": "SuperComparador Paraguay", "version": "4.0", "estado": "online"})

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "version": "4.0"})

@app.route("/buscar", methods=["GET"])
def buscar():
    barcode = request.args.get("barcode", "").strip()
    if not barcode:
        return jsonify({"error": "barcode requerido"}), 400
    logger.info(f"Buscando: {barcode}")
    resultados = []
    for fn in [scrape_biggie, scrape_stock, scrape_superseis, scrape_salemma, scrape_real]:
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
