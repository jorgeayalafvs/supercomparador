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

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]
_ua_idx = 0

def get_headers():
    global _ua_idx
    ua = USER_AGENTS[_ua_idx % len(USER_AGENTS)]
    _ua_idx += 1
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "es-PY,es;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    }

def fetch(url, timeout=20, referer=None):
    try:
        headers = get_headers()
        if referer:
            headers["Referer"] = referer
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        logger.info(f"GET {url} -> {r.status_code}")
        if r.status_code == 200:
            return BeautifulSoup(r.text, "html.parser")
        return None
    except Exception as e:
        logger.warning(f"fetch error {url}: {e}")
        return None

def fetch_json(url, timeout=15):
    try:
        headers = get_headers()
        headers["Accept"] = "application/json, text/plain, */*"
        r = requests.get(url, headers=headers, timeout=timeout)
        logger.info(f"JSON {url} -> {r.status_code}")
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        logger.warning(f"fetch_json error {url}: {e}")
        return None

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

def extraer_precio_texto(texto):
    """Busca patrones de precio en guaraníes en texto plano"""
    patrones = [
        r'[Gg]s\.?\s*([\d\.]+)',
        r'[Gg][Ss]\s*([\d\.]+)',
        r'₲\s*([\d\.]+)',
        r'"[Pp]rice"["\s:]+(\d[\d\.]+)',
        r'"[Pp]recio"["\s:]+(\d[\d\.]+)',
    ]
    for pat in patrones:
        matches = re.findall(pat, texto)
        for m in matches:
            p = limpiar_precio(m)
            if p and p > 1000:
                return p
    return None

# ══════════════════════════════════════════════
# BIGGIE — sitio propio con búsqueda /search?q=
# Precio visible en card: ₲ 12.000
# Selector: el precio está en span o div con texto "₲"
# ══════════════════════════════════════════════
def scrape_biggie(barcode):
    base = "https://biggie.com.py"
    r = build_result("Biggie", base, barcode)
    search_url = f"{base}/search?q={barcode}"
    r["url_producto"] = search_url

    soup = fetch(search_url)
    if not soup:
        r["error"] = "No se pudo conectar"
        return r

    # Buscar precio directo en resultados (card del producto)
    # El precio aparece como "₲ 12.000" en la tarjeta
    texto_pagina = soup.get_text()
    precio = extraer_precio_texto(texto_pagina)
    if precio:
        r["precio"] = precio
        # Buscar nombre del producto
        nombre_el = soup.select_one(".product-name, .product-title, h3, h2")
        if nombre_el:
            r["nombre"] = nombre_el.get_text(strip=True)
        r["disponible"] = True
        return r

    # Intentar entrar al producto
    link = (soup.select_one("a[href*='/item/']") or
            soup.select_one("a[href*='/product/']") or
            soup.select_one("a[href*='/products/']") or
            soup.select_one(".product-card a") or
            soup.select_one("article a"))

    if link:
        prod_url = urljoin(base, link.get("href", ""))
        r["url_producto"] = prod_url
        time.sleep(1)
        soup2 = fetch(prod_url, referer=search_url)
        if soup2:
            h1 = soup2.select_one("h1")
            if h1: r["nombre"] = h1.get_text(strip=True)
            precio = extraer_precio_texto(soup2.get_text())
            if precio:
                r["precio"] = precio
                r["disponible"] = True
                return r

    if not r["disponible"]:
        r["error"] = "Producto no encontrado"
    return r


# ══════════════════════════════════════════════
# STOCK — ASP.NET propio (NO es VTEX)
# Hay que encontrar la URL correcta de búsqueda
# ══════════════════════════════════════════════
def scrape_stock(barcode):
    base = "https://www.stock.com.py"
    r = build_result("Stock", base, barcode)

    # Probar diferentes URLs de búsqueda de Stock
    urls_busqueda = [
        f"{base}/search?search={barcode}",
        f"{base}/search?q={barcode}",
        f"{base}/buscar?q={barcode}",
        f"{base}/productos?search={barcode}",
    ]

    for search_url in urls_busqueda:
        r["url_producto"] = search_url
        soup = fetch(search_url)
        if not soup:
            continue

        texto = soup.get_text()
        # Verificar que hay resultados (no página de error)
        if "no se encontr" in texto.lower() or "sin resultados" in texto.lower():
            continue

        precio = extraer_precio_texto(texto)
        if precio:
            r["precio"] = precio
            nombre_el = soup.select_one("h1, h2, h3, .product-name, .product-title")
            if nombre_el:
                r["nombre"] = nombre_el.get_text(strip=True)
            r["disponible"] = True
            return r

        # Buscar link de producto
        link = (soup.select_one("a[href*='/producto/']") or
                soup.select_one("a[href*='/product/']") or
                soup.select_one("a[href*='/item/']") or
                soup.select_one(".product-card a") or
                soup.select_one("article a") or
                soup.select_one("h2 a, h3 a"))
        if link:
            prod_url = urljoin(base, link.get("href", ""))
            r["url_producto"] = prod_url
            time.sleep(1)
            soup2 = fetch(prod_url, referer=search_url)
            if soup2:
                h1 = soup2.select_one("h1")
                if h1: r["nombre"] = h1.get_text(strip=True)
                precio = extraer_precio_texto(soup2.get_text())
                if precio:
                    r["precio"] = precio
                    r["disponible"] = True
                    return r

    if not r["disponible"]:
        r["error"] = "Producto no encontrado"
    return r


# ══════════════════════════════════════════════
# SUPERSEIS — URL correcta: /search?search=
# Precio visible: ₲ 12.000
# ══════════════════════════════════════════════
def scrape_superseis(barcode):
    base = "https://www.superseis.com.py"
    r = build_result("Superseis", base, barcode)

    # URL CORRECTA según captura: /search?search=
    search_url = f"{base}/search?search={barcode}"
    r["url_producto"] = search_url

    soup = fetch(search_url)
    if not soup:
        r["error"] = "No se pudo conectar"
        return r

    # Precio directo en la tarjeta de resultados
    texto = soup.get_text()
    precio = extraer_precio_texto(texto)
    if precio:
        r["precio"] = precio
        nombre_el = (soup.select_one(".product-name") or
                     soup.select_one("h3") or
                     soup.select_one(".item-name"))
        if nombre_el:
            r["nombre"] = nombre_el.get_text(strip=True)
        r["disponible"] = True
        return r

    # Entrar al producto
    link = (soup.select_one("a[href*='/product/']") or
            soup.select_one("a[href*='/item/']") or
            soup.select_one(".product-card a") or
            soup.select_one("article a") or
            soup.select_one("h3 a, h2 a"))

    if link:
        prod_url = urljoin(base, link.get("href", ""))
        r["url_producto"] = prod_url
        time.sleep(1)
        soup2 = fetch(prod_url, referer=search_url)
        if soup2:
            h1 = soup2.select_one("h1")
            if h1: r["nombre"] = h1.get_text(strip=True)
            precio = extraer_precio_texto(soup2.get_text())
            if precio:
                r["precio"] = precio
                r["disponible"] = True
                return r

    if not r["disponible"]:
        r["error"] = "Producto no encontrado"
    return r


# ══════════════════════════════════════════════
# SALEMMA — Shopify
# ══════════════════════════════════════════════
def scrape_salemma(barcode):
    base = "https://www.salemmaonline.com.py"
    r = build_result("Salemma", base, barcode)
    search_url = f"{base}/search?q={barcode}&type=product"
    r["url_producto"] = search_url

    soup = fetch(search_url)
    if not soup:
        r["error"] = "No se pudo conectar"
        return r

    texto = soup.get_text()
    precio = extraer_precio_texto(texto)
    if precio:
        r["precio"] = precio
        nombre_el = soup.select_one("h1, h2, h3, .product-name")
        if nombre_el: r["nombre"] = nombre_el.get_text(strip=True)
        r["disponible"] = True
        return r

    link = (soup.select_one("a[href*='/products/']") or
            soup.select_one("a[href*='/product/']"))
    if link:
        prod_url = urljoin(base, link.get("href", "").split("?")[0])
        r["url_producto"] = prod_url
        time.sleep(1)
        soup2 = fetch(prod_url, referer=search_url)
        if soup2:
            h1 = soup2.select_one("h1")
            if h1: r["nombre"] = h1.get_text(strip=True)
            precio = extraer_precio_texto(soup2.get_text())
            if precio:
                r["precio"] = precio
                r["disponible"] = True
                return r

    if not r["disponible"]:
        r["error"] = "Producto no encontrado"
    return r


# ══════════════════════════════════════════════
# REAL — probar URLs correctas
# ══════════════════════════════════════════════
def scrape_real(barcode):
    base = "https://www.realonline.com.py"
    r = build_result("Real", base, barcode)

    urls_busqueda = [
        f"{base}/search?search={barcode}",
        f"{base}/search?q={barcode}",
        f"{base}/buscar?q={barcode}",
    ]

    for search_url in urls_busqueda:
        r["url_producto"] = search_url
        soup = fetch(search_url)
        if not soup: continue

        texto = soup.get_text()
        if "no se encontr" in texto.lower(): continue

        precio = extraer_precio_texto(texto)
        if precio:
            r["precio"] = precio
            nombre_el = soup.select_one("h1, h2, h3, .product-name")
            if nombre_el: r["nombre"] = nombre_el.get_text(strip=True)
            r["disponible"] = True
            return r

        link = (soup.select_one("a[href*='/producto/']") or
                soup.select_one("a[href*='/product/']") or
                soup.select_one("a[href*='/item/']") or
                soup.select_one("article a") or
                soup.select_one("h2 a, h3 a"))
        if link:
            prod_url = urljoin(base, link.get("href", ""))
            r["url_producto"] = prod_url
            time.sleep(1)
            soup2 = fetch(prod_url, referer=search_url)
            if soup2:
                h1 = soup2.select_one("h1")
                if h1: r["nombre"] = h1.get_text(strip=True)
                precio = extraer_precio_texto(soup2.get_text())
                if precio:
                    r["precio"] = precio
                    r["disponible"] = True
                    return r

    if not r["disponible"]:
        r["error"] = "Producto no encontrado"
    return r


# ══════════════════════════════════════════════
# GENÉRICO
# ══════════════════════════════════════════════
def scrape_generico(barcode, nombre, url_base):
    r = build_result(nombre, url_base, barcode)
    patterns = [
        f"{url_base.rstrip('/')}/search?search={barcode}",
        f"{url_base.rstrip('/')}/search?q={barcode}",
        f"{url_base.rstrip('/')}/buscar?q={barcode}",
        f"{url_base.rstrip('/')}/search?q={barcode}&type=product",
    ]
    for url in patterns:
        soup = fetch(url)
        if not soup: continue
        r["url_producto"] = url
        texto = soup.get_text()
        precio = extraer_precio_texto(texto)
        if precio:
            r["precio"] = precio
            nombre_el = soup.select_one("h1, h2, h3, .product-name")
            if nombre_el: r["nombre"] = nombre_el.get_text(strip=True)
            r["disponible"] = True
            return r
        link = (soup.select_one("a[href*='/product']") or
                soup.select_one("a[href*='/item']") or
                soup.select_one("article a") or soup.select_one("h2 a"))
        if link:
            prod_url = urljoin(url_base, link.get("href", ""))
            r["url_producto"] = prod_url
            time.sleep(1)
            soup2 = fetch(prod_url, referer=url)
            if soup2:
                h1 = soup2.select_one("h1")
                if h1: r["nombre"] = h1.get_text(strip=True)
                precio = extraer_precio_texto(soup2.get_text())
                if precio:
                    r["precio"] = precio
                    r["disponible"] = True
                    return r
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
    return jsonify({"api": "SuperComparador Paraguay", "version": "5.0", "estado": "online"})

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "version": "5.0"})

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
