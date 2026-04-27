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
        except Exception:
            continue
    return None, None

def precio_de_texto(texto):
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
# SUPERSEIS ✅ FUNCIONA
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
    prod_link = None
    for a in soup.find_all("a", href=True):
        if "/product/" in a["href"]:
            prod_link = urljoin(base, a["href"])
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
    nombre_ld, precio_ld = extraer_jsonld(soup2)
    if nombre_ld: r["nombre"] = nombre_ld
    if precio_ld:
        r["precio"] = precio_ld
        r["disponible"] = True
        return r

    h1 = soup2.select_one("h1")
    if h1: r["nombre"] = h1.get_text(strip=True)
    r["precio"] = precio_de_texto(html2)
    r["disponible"] = r["precio"] is not None
    if not r["disponible"]: r["error"] = "Precio no encontrado"
    return r

# ══════════════════════════════════════════════
# SALEMMA ✅ FUNCIONA (con verificación de barcode)
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
    prod_links = []
    for a in soup.find_all("a", href=True):
        if "/producto/" in a["href"]:
            prod_links.append(urljoin(base, a["href"]))

    if not prod_links:
        r["error"] = "Producto no encontrado"
        return r

    for prod_link in prod_links[:5]:
        time.sleep(0.8)
        html2 = fetch(prod_link, referer=search_url)
        if not html2: continue
        # Verificar que el barcode está en la página
        if barcode not in html2: continue

        soup2 = BeautifulSoup(html2, "html.parser")
        r["url_producto"] = prod_link
        h1 = soup2.select_one("h1")
        if h1: r["nombre"] = h1.get_text(strip=True)
        r["precio"] = precio_de_texto(html2)

        img = soup2.select_one("img[src*='.webp'], img[src*='.jpg'], img[src*='.png']")
        if img:
            src = img.get("src", "")
            if src and "logo" not in src.lower():
                r["imagen"] = urljoin(base, src)

        r["disponible"] = r["precio"] is not None
        if not r["disponible"]: r["error"] = "Precio no encontrado"
        return r

    r["error"] = "Producto no encontrado"
    return r

# ══════════════════════════════════════════════
# BIGGIE — link manual (IP bloqueada desde USA)
# ══════════════════════════════════════════════
def scrape_biggie(barcode):
    base = "https://biggie.com.py"
    r = build_result("Biggie", base, barcode)
    r["url_producto"] = f"{base}/search?q={barcode}"
    r["error"] = "Ver precio manualmente"
    # Intentar igual por si acaso
    html = fetch(r["url_producto"])
    if html:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if any(x in href for x in ["/product/", "/item/", "/p/"]):
                prod_url = urljoin(base, href)
                time.sleep(1)
                html2 = fetch(prod_url, referer=r["url_producto"])
                if html2 and barcode in html2:
                    soup2 = BeautifulSoup(html2, "html.parser")
                    nombre_ld, precio_ld = extraer_jsonld(soup2)
                    if nombre_ld: r["nombre"] = nombre_ld
                    if precio_ld: r["precio"] = precio_ld
                    if not r["precio"]:
                        h1 = soup2.select_one("h1")
                        if h1: r["nombre"] = h1.get_text(strip=True)
                        r["precio"] = precio_de_texto(html2)
                    if r["precio"]:
                        r["disponible"] = True
                        r["error"] = None
                        r["url_producto"] = prod_url
                    break
    return r

# ══════════════════════════════════════════════
# STOCK — link manual (sistema ASP.NET propio)
# ══════════════════════════════════════════════
def scrape_stock(barcode):
    base = "https://www.stock.com.py"
    r = build_result("Stock", base, barcode)
    r["url_producto"] = f"{base}/search?q={barcode}"
    r["error"] = "Ver precio manualmente"
    for url in [f"{base}/search?q={barcode}", f"{base}/buscar?q={barcode}"]:
        html = fetch(url)
        if not html or len(html) < 2000: continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if any(x in href for x in ["/product/", "/item/", "/p/", "/detalle"]):
                prod_url = urljoin(base, href)
                time.sleep(1)
                html2 = fetch(prod_url, referer=url)
                if html2:
                    soup2 = BeautifulSoup(html2, "html.parser")
                    nombre_ld, precio_ld = extraer_jsonld(soup2)
                    if nombre_ld: r["nombre"] = nombre_ld
                    if precio_ld: r["precio"] = precio_ld
                    if not r["precio"]: r["precio"] = precio_de_texto(html2)
                    if r["precio"]:
                        r["disponible"] = True
                        r["error"] = None
                        r["url_producto"] = prod_url
                break
        if r["disponible"]: break
    return r

# ══════════════════════════════════════════════
# REAL — link manual
# ══════════════════════════════════════════════
def scrape_real(barcode):
    base = "https://www.realonline.com.py"
    r = build_result("Real", base, barcode)
    r["url_producto"] = f"{base}/search?q={barcode}"
    r["error"] = "Ver precio manualmente"
    html = fetch(r["url_producto"])
    if html:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if any(x in href for x in ["/product/", "/item/", "/p/"]):
                prod_url = urljoin(base, href)
                time.sleep(1)
                html2 = fetch(prod_url, referer=r["url_producto"])
                if html2:
                    nombre_ld, precio_ld = extraer_jsonld(BeautifulSoup(html2, "html.parser"))
                    if nombre_ld: r["nombre"] = nombre_ld
                    if precio_ld: r["precio"] = precio_ld
                    if not r["precio"]: r["precio"] = precio_de_texto(html2)
                    if r["precio"]:
                        r["disponible"] = True
                        r["error"] = None
                        r["url_producto"] = prod_url
                break
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
    ]
    for url in patterns:
        html = fetch(url)
        if not html or len(html) < 2000: continue
        r["url_producto"] = url
        soup = BeautifulSoup(html, "html.parser")
        nombre_ld, precio_ld = extraer_jsonld(soup)
        if precio_ld:
            r["nombre"] = nombre_ld
            r["precio"] = precio_ld
            r["disponible"] = True
            return r
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if any(x in href for x in ["/product/", "/item/", "/p/", "/producto/"]):
                prod_url = urljoin(url_base, href)
                time.sleep(1)
                html2 = fetch(prod_url, referer=url)
                if html2:
                    soup2 = BeautifulSoup(html2, "html.parser")
                    nombre_ld2, precio_ld2 = extraer_jsonld(soup2)
                    if nombre_ld2: r["nombre"] = nombre_ld2
                    if precio_ld2: r["precio"] = precio_ld2
                    if not r["precio"]: r["precio"] = precio_de_texto(html2)
                    if r["precio"]:
                        r["disponible"] = True
                        r["url_producto"] = prod_url
                        return r
                break
        time.sleep(0.5)
    if not r["disponible"]: r["error"] = "Producto no encontrado"
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
    return jsonify({"api": "SuperComparador Paraguay", "version": "8.0", "estado": "online"})

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "version": "8.0"})

@app.route("/buscar", methods=["GET"])
def buscar():
    barcode = request.args.get("barcode", "").strip()
    if not barcode:
        return jsonify({"error": "barcode requerido"}), 400
    logger.info(f"Buscando: {barcode}")
    resultados = []
    for fn in [scrape_superseis, scrape_salemma, scrape_biggie, scrape_stock, scrape_real]:
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
