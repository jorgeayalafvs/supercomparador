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

def get_headers(referer=None):
    global _ua_idx
    h = {
        "User-Agent": USER_AGENTS[_ua_idx % len(USER_AGENTS)],
        "Accept-Language": "es-PY,es;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Upgrade-Insecure-Requests": "1",
    }
    _ua_idx += 1
    if referer: h["Referer"] = referer
    return h

def fetch(url, referer=None, timeout=20):
    try:
        r = requests.get(url, headers=get_headers(referer=referer), timeout=timeout, allow_redirects=True)
        logger.info(f"GET {url} → {r.status_code} ({len(r.text)} bytes)")
        return r.text if r.status_code == 200 else None
    except Exception as e:
        logger.warning(f"fetch error {url}: {e}")
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
        return val if val > 500 else None
    except ValueError:
        return None

def build_result(supermercado, url_base, barcode):
    return {
        "supermercado": supermercado,
        "url_supermercado": url_base,
        "barcode": barcode,
        "nombre": None, "precio": None,
        "url_producto": f"{url_base.rstrip('/')}/search?q={barcode}",
        "imagen": None, "disponible": False, "error": None,
    }

def extraer_jsonld(soup):
    """Extrae precio y nombre de JSON-LD (schema.org Product)"""
    for sc in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(sc.string or "")
            if isinstance(data, list): data = data[0]
            tipo = data.get("@type", "")
            if tipo == "Product":
                nombre = data.get("name")
                offers = data.get("offers", {})
                if isinstance(offers, list): offers = offers[0]
                precio = limpiar_precio(str(offers.get("price", "")))
                if precio:
                    return nombre, precio
        except Exception:
            continue
    return None, None

def extraer_og(soup):
    """Extrae título de Open Graph"""
    og = soup.find("meta", property="og:title")
    if og: return og.get("content", "").strip()
    return None

def precio_de_texto(texto):
    """Busca patrón de precio en guaraníes dentro de un texto"""
    patrones = [
        r'[₲Gg][Ss]?\.?\s*([\d]{1,3}(?:[.\s][\d]{3})*)',
        r'([\d]{4,7})\s*[₲Gg]',
        r'"price"\s*:\s*"?([\d]+(?:\.\d+)?)"?',
    ]
    for pat in patrones:
        for m in re.findall(pat, texto, re.IGNORECASE):
            p = limpiar_precio(m)
            if p and p > 500: return p
    return None


# ══════════════════════════════════════════════
# SALEMMA
# Sistema propio. URL producto: /producto/SLUG
# Precio en página: "Gs. 12.000 por unidad"
# Código visible en página: "Codigo: 7840058002549"
# ══════════════════════════════════════════════
def scrape_salemma(barcode):
    base = "https://www.salemmaonline.com.py"
    r = build_result("Salemma", base, barcode)
    search_url = f"{base}/search?q={barcode}"
    r["url_producto"] = search_url

    html = fetch(search_url)
    if not html:
        r["error"] = "No se pudo conectar"
        return r

    soup = BeautifulSoup(html, "html.parser")

    # Salemma muestra resultados de búsqueda — buscar link al producto
    # El producto tiene el barcode en la URL o en el texto
    prod_link = None

    # Buscar links de producto (contienen /producto/ en la URL)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/producto/" in href:
            prod_link = urljoin(base, href)
            break

    if not prod_link:
        r["error"] = "Producto no encontrado"
        return r

    r["url_producto"] = prod_link
    time.sleep(1)
    html2 = fetch(prod_link, referer=search_url)
    if not html2:
        r["error"] = "No se pudo acceder al producto"
        return r

    soup2 = BeautifulSoup(html2, "html.parser")

    # Nombre: h1 o título OG
    h1 = soup2.select_one("h1")
    if h1:
        r["nombre"] = h1.get_text(strip=True)
    else:
        r["nombre"] = extraer_og(soup2)

    # Precio: buscar "Gs. 12.000" en el texto de la página
    # Salemma usa: "Gs. 12.000 por unidad"
    texto_precio = soup2.get_text(" ")
    r["precio"] = precio_de_texto(texto_precio)

    # Imagen
    img = soup2.select_one("img[src*='producto'], img[src*='product'], .product-image img")
    if img:
        src = img.get("src", "")
        r["imagen"] = urljoin(base, src) if src else None

    r["disponible"] = r["precio"] is not None
    if not r["disponible"]: r["error"] = "Precio no encontrado"
    return r


# ══════════════════════════════════════════════
# SUPERSEIS
# Sistema propio. URL producto: /product/SLUG
# Precio en página: "₲ 12.000"
# SKU visible: "7840058002549"
# JSON-LD disponible en <head>
# ══════════════════════════════════════════════
def scrape_superseis(barcode):
    base = "https://www.superseis.com.py"
    r = build_result("Superseis", base, barcode)
    search_url = f"{base}/search?search={barcode}"
    r["url_producto"] = search_url

    html = fetch(search_url)
    if not html:
        r["error"] = "No se pudo conectar"
        return r

    soup = BeautifulSoup(html, "html.parser")

    # Buscar link al producto (/product/ en la URL)
    prod_link = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/product/" in href:
            prod_link = urljoin(base, href)
            break

    if not prod_link:
        r["error"] = "Producto no encontrado en búsqueda"
        return r

    r["url_producto"] = prod_link
    time.sleep(1)
    html2 = fetch(prod_link, referer=search_url)
    if not html2:
        r["error"] = "No se pudo acceder al producto"
        return r

    soup2 = BeautifulSoup(html2, "html.parser")

    # 1. JSON-LD (más confiable, Superseis lo incluye)
    nombre_ld, precio_ld = extraer_jsonld(soup2)
    if nombre_ld: r["nombre"] = nombre_ld
    if precio_ld:
        r["precio"] = precio_ld
        r["disponible"] = True
        r["url_producto"] = prod_link
        return r

    # 2. Fallback: buscar precio ₲ en texto
    h1 = soup2.select_one("h1")
    if h1: r["nombre"] = h1.get_text(strip=True)

    texto = soup2.get_text(" ")
    r["precio"] = precio_de_texto(texto)

    r["disponible"] = r["precio"] is not None
    if not r["disponible"]: r["error"] = "Precio no encontrado"
    return r


# ══════════════════════════════════════════════
# BIGGIE
# ══════════════════════════════════════════════
def scrape_biggie(barcode):
    base = "https://biggie.com.py"
    r = build_result("Biggie", base, barcode)
    search_url = f"{base}/search?q={barcode}"
    r["url_producto"] = search_url

    html = fetch(search_url)
    if not html:
        r["error"] = "No se pudo conectar"
        return r

    soup = BeautifulSoup(html, "html.parser")

    # Buscar link de producto
    prod_link = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(x in href for x in ["/product/", "/item/", "/p/", "/productos/"]):
            prod_link = urljoin(base, href)
            break

    # Si no encontró link específico, buscar precio directamente en resultados
    if not prod_link:
        texto = soup.get_text(" ")
        p = precio_de_texto(texto)
        if p:
            r["precio"] = p
            # Buscar nombre
            for tag in ["h2", "h3", "h4"]:
                for el in soup.find_all(tag):
                    txt = el.get_text(strip=True)
                    if len(txt) > 5 and "₲" not in txt:
                        r["nombre"] = txt
                        break
                if r["nombre"]: break
            r["disponible"] = True
            return r
        r["error"] = "Producto no encontrado"
        return r

    r["url_producto"] = prod_link
    time.sleep(1)
    html2 = fetch(prod_link, referer=search_url)
    if not html2:
        r["error"] = "No se pudo acceder al producto"
        return r

    soup2 = BeautifulSoup(html2, "html.parser")

    # JSON-LD
    nombre_ld, precio_ld = extraer_jsonld(soup2)
    if nombre_ld: r["nombre"] = nombre_ld
    if precio_ld: r["precio"] = precio_ld

    if not r["precio"]:
        h1 = soup2.select_one("h1")
        if h1: r["nombre"] = h1.get_text(strip=True)
        r["precio"] = precio_de_texto(soup2.get_text(" "))

    r["disponible"] = r["precio"] is not None
    if not r["disponible"]: r["error"] = "Precio no encontrado"
    return r


# ══════════════════════════════════════════════
# STOCK
# ══════════════════════════════════════════════
def scrape_stock(barcode):
    base = "https://www.stock.com.py"
    r = build_result("Stock", base, barcode)

    urls = [
        f"{base}/search?q={barcode}",
        f"{base}/buscar?q={barcode}",
        f"{base}/productos?search={barcode}",
        f"{base}/Search?buscar={barcode}",
    ]

    for url in urls:
        html = fetch(url)
        if not html or len(html) < 2000:
            time.sleep(0.3)
            continue
        r["url_producto"] = url
        soup = BeautifulSoup(html, "html.parser")

        # Buscar link de producto
        prod_link = None
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if any(x in href for x in ["/product/", "/item/", "/p/", "/detalle"]):
                prod_link = urljoin(base, href)
                break

        if prod_link:
            r["url_producto"] = prod_link
            time.sleep(1)
            html2 = fetch(prod_link, referer=url)
            if html2:
                soup2 = BeautifulSoup(html2, "html.parser")
                nombre_ld, precio_ld = extraer_jsonld(soup2)
                if nombre_ld: r["nombre"] = nombre_ld
                if precio_ld: r["precio"] = precio_ld
                if not r["precio"]:
                    h1 = soup2.select_one("h1")
                    if h1: r["nombre"] = h1.get_text(strip=True)
                    r["precio"] = precio_de_texto(soup2.get_text(" "))
                if r["precio"]:
                    r["disponible"] = True
                    return r

        # Sin link de producto, buscar precio directo
        p = precio_de_texto(soup.get_text(" "))
        if p:
            r["precio"] = p
            r["disponible"] = True
            return r

        time.sleep(0.5)

    r["error"] = "Producto no encontrado"
    return r


# ══════════════════════════════════════════════
# REAL
# ══════════════════════════════════════════════
def scrape_real(barcode):
    base = "https://www.realonline.com.py"
    r = build_result("Real", base, barcode)
    search_url = f"{base}/search?q={barcode}"
    r["url_producto"] = search_url

    html = fetch(search_url)
    if not html:
        r["error"] = "No se pudo conectar"
        return r

    soup = BeautifulSoup(html, "html.parser")

    prod_link = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(x in href for x in ["/product/", "/item/", "/p/", "/detalle"]):
            prod_link = urljoin(base, href)
            break

    if prod_link:
        r["url_producto"] = prod_link
        time.sleep(1)
        html2 = fetch(prod_link, referer=search_url)
        if html2:
            soup2 = BeautifulSoup(html2, "html.parser")
            nombre_ld, precio_ld = extraer_jsonld(soup2)
            if nombre_ld: r["nombre"] = nombre_ld
            if precio_ld: r["precio"] = precio_ld
            if not r["precio"]:
                h1 = soup2.select_one("h1")
                if h1: r["nombre"] = h1.get_text(strip=True)
                r["precio"] = precio_de_texto(soup2.get_text(" "))
    else:
        p = precio_de_texto(soup.get_text(" "))
        if p: r["precio"] = p

    r["disponible"] = r["precio"] is not None
    if not r["disponible"]: r["error"] = "Producto no encontrado"
    return r


# ══════════════════════════════════════════════
# GENÉRICO
# ══════════════════════════════════════════════
def scrape_generico(barcode, nombre_super, url_base):
    r = build_result(nombre_super, url_base, barcode)
    patterns = [
        f"{url_base.rstrip('/')}/search?q={barcode}",
        f"{url_base.rstrip('/')}/search?search={barcode}",
        f"{url_base.rstrip('/')}/buscar?q={barcode}",
        f"{url_base.rstrip('/')}/catalogsearch/result/?q={barcode}",
    ]
    for url in patterns:
        html = fetch(url)
        if not html or len(html) < 2000:
            time.sleep(0.3)
            continue
        r["url_producto"] = url
        soup = BeautifulSoup(html, "html.parser")

        # JSON-LD directo
        nombre_ld, precio_ld = extraer_jsonld(soup)
        if precio_ld:
            r["nombre"] = nombre_ld
            r["precio"] = precio_ld
            r["disponible"] = True
            return r

        # Buscar link de producto
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if any(x in href for x in ["/product/", "/item/", "/p/", "/producto/", "/detalle"]):
                prod_url = urljoin(url_base, href)
                r["url_producto"] = prod_url
                time.sleep(1)
                html2 = fetch(prod_url, referer=url)
                if html2:
                    soup2 = BeautifulSoup(html2, "html.parser")
                    nombre_ld2, precio_ld2 = extraer_jsonld(soup2)
                    if nombre_ld2: r["nombre"] = nombre_ld2
                    if precio_ld2: r["precio"] = precio_ld2
                    if not r["precio"]:
                        h1 = soup2.select_one("h1")
                        if h1: r["nombre"] = h1.get_text(strip=True)
                        r["precio"] = precio_de_texto(soup2.get_text(" "))
                    if r["precio"]:
                        r["disponible"] = True
                        return r
                break

        p = precio_de_texto(soup.get_text(" "))
        if p:
            r["precio"] = p
            r["disponible"] = True
            return r
        time.sleep(0.5)

    r["error"] = "Producto no encontrado"
    return r


SCRAPERS_MAP = {
    "biggie.com.py": lambda b: scrape_biggie(b),
    "biggie.com": lambda b: scrape_biggie(b),
    "salemmaonline.com.py": lambda b: scrape_salemma(b),
    "stock.com.py": lambda b: scrape_stock(b),
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
    return jsonify({"api": "SuperComparador Paraguay", "version": "7.0", "estado": "online"})

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "version": "7.0"})

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
        nombre_super = item.get("nombre", "Supermercado")
        url = item.get("url", "")
        if not url: continue
        fn = get_scraper(url)
        res = fn(barcode) if fn else scrape_generico(barcode, nombre_super, url)
        res["supermercado"] = nombre_super
        resultados.append(res)
    return jsonify(calcular_resumen(barcode, resultados))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
