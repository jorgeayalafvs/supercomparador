"""
SuperComparador Paraguay - Backend con Playwright
Usa requests+BeautifulSoup primero (rápido).
Si falla o el sitio tiene anti-bot, usa Playwright (navegador real headless).
Deploy en Render.com — plan free.
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re
import time
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_OK = True
    logger.info("Playwright disponible")
except ImportError:
    PLAYWRIGHT_OK = False
    logger.warning("Playwright no disponible, usando solo requests")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-PY,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def playwright_get_price(search_url, prod_selectors, price_selectors):
    if not PLAYWRIGHT_OK:
        return {"nombre": None, "precio": None, "url": search_url, "error": "Playwright no instalado"}
    resultado = {"nombre": None, "precio": None, "url": search_url, "error": None}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu", "--window-size=1280,800"]
            )
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=HEADERS["User-Agent"],
                locale="es-PY"
            )
            page = ctx.new_page()
            page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf}", lambda r: r.abort())
            page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)

            for sel in prod_selectors:
                try:
                    el = page.query_selector(sel)
                    if el:
                        href = el.get_attribute("href")
                        if href:
                            from urllib.parse import urljoin
                            if not href.startswith("http"):
                                href = urljoin(search_url, href)
                            resultado["url"] = href
                            page.goto(href, wait_until="domcontentloaded", timeout=25000)
                            page.wait_for_timeout(2000)
                            break
                except Exception:
                    continue

            h1 = page.query_selector("h1")
            if h1:
                resultado["nombre"] = h1.inner_text().strip()

            for sel in price_selectors:
                try:
                    el = page.query_selector(sel)
                    if el:
                        txt = el.inner_text().strip()
                        precio = limpiar_precio(txt)
                        if precio and precio > 0:
                            resultado["precio"] = precio
                            break
                except Exception:
                    continue

            browser.close()
    except Exception as e:
        resultado["error"] = str(e)
        logger.error(f"Playwright error: {e}")
    return resultado


def build_result(supermercado, url_base, barcode):
    return {
        "supermercado": supermercado,
        "url_supermercado": url_base,
        "barcode": barcode,
        "nombre": None,
        "precio": None,
        "url_producto": f"{url_base.rstrip('/')}/search?q={barcode}",
        "imagen": None,
        "disponible": False,
        "metodo": None,
        "error": None,
    }


def try_requests(r, search_url, prod_selectors_css, price_selectors_css, base_url):
    try:
        resp = SESSION.get(search_url, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        link = None
        for sel in prod_selectors_css:
            link = soup.select_one(sel)
            if link:
                break
        if link:
            prod_url = link.get("href", "")
            if not prod_url.startswith("http"):
                from urllib.parse import urljoin
                prod_url = urljoin(base_url, prod_url)
            r["url_producto"] = prod_url
            pr = SESSION.get(prod_url, timeout=15)
            ps = BeautifulSoup(pr.text, "html.parser")
            h1 = ps.select_one("h1")
            if h1:
                r["nombre"] = h1.get_text(strip=True)
            for sel in price_selectors_css:
                pe = ps.select_one(sel)
                if pe:
                    p = limpiar_precio(pe.get_text(strip=True))
                    if p and p > 0:
                        r["precio"] = p
                        r["disponible"] = True
                        r["metodo"] = "requests"
                        return True
    except Exception as e:
        logger.warning(f"requests falló para {r['supermercado']}: {e}")
    return False


def try_playwright_fallback(r, search_url, prod_sel, price_sel):
    if not PLAYWRIGHT_OK:
        r["error"] = "No encontrado"
        return
    pw = playwright_get_price(search_url, prod_sel, price_sel)
    r["url_producto"] = pw["url"]
    if pw["nombre"]:
        r["nombre"] = pw["nombre"]
    r["precio"] = pw["precio"]
    r["disponible"] = pw["precio"] is not None
    r["metodo"] = "playwright" if pw["precio"] else None
    r["error"] = pw["error"]


def scrape_biggie(barcode):
    base = "https://biggie.com.py"
    r = build_result("Biggie", base, barcode)
    url = f"{base}/search?q={barcode}"
    prod_sel = ["a[href*='/products/']", "a.product-item__title"]
    price_sel = ["span.price-item--regular", "span.money", "[class*='price']"]
    if not try_requests(r, url, prod_sel, price_sel, base):
        try_playwright_fallback(r, url, prod_sel, price_sel)
    return r


def scrape_stock(barcode):
    base = "https://www.stock.com.py"
    r = build_result("Stock", base, barcode)
    url = f"{base}/search?q={barcode}"
    prod_sel = ["a.product-name", "[class*='ProductName'] a", "h2 a"]
    price_sel = [".priceText", "[class*='sellingPrice']", ".skuBestPrice"]
    if not try_requests(r, url, prod_sel, price_sel, base):
        try_playwright_fallback(r, url, prod_sel, price_sel)
    return r


def scrape_superseis(barcode):
    base = "https://www.superseis.com.py"
    r = build_result("Superseis", base, barcode)
    url = f"{base}/catalogsearch/result/?q={barcode}"
    r["url_producto"] = url
    prod_sel = ["a.product-item-link", ".product-item-name a", "h2.product-name a"]
    price_sel = [".special-price .price", ".regular-price .price", "span.price"]
    if not try_requests(r, url, prod_sel, price_sel, base):
        try_playwright_fallback(r, url, prod_sel, price_sel)
    return r


def scrape_salemma(barcode):
    base = "https://www.salemmaonline.com.py"
    r = build_result("Salemma", base, barcode)
    url = f"{base}/search?q={barcode}"
    prod_sel = ["a[href*='/products/']", "a.product-item__title"]
    price_sel = ["span.money", ".product__price", "[class*='price']"]
    if not try_requests(r, url, prod_sel, price_sel, base):
        try_playwright_fallback(r, url, prod_sel, price_sel)
    return r


def scrape_real(barcode):
    base = "https://www.realonline.com.py"
    r = build_result("Real", base, barcode)
    url = f"{base}/search?q={barcode}"
    prod_sel = ["a[href*='/p/']", "a.product-name", "[class*='ProductName'] a"]
    price_sel = [".priceText", "[class*='sellingPrice']", "[class*='Price']"]
    if not try_requests(r, url, prod_sel, price_sel, base):
        try_playwright_fallback(r, url, prod_sel, price_sel)
    return r


def scrape_generico(barcode, nombre_super, url_base):
    r = build_result(nombre_super, url_base, barcode)
    patterns = [
        f"{url_base.rstrip('/')}/search?q={barcode}",
        f"{url_base.rstrip('/')}/catalogsearch/result/?q={barcode}",
        f"{url_base.rstrip('/')}/buscar?q={barcode}",
    ]
    prod_sel = ["a[href*='/products/']", "a[href*='/p/']", "a.product-name", "h2 a"]
    price_sel = ["span.money", "[class*='price']", ".precio"]

    for url in patterns:
        r["url_producto"] = url
        if try_requests(r, url, prod_sel, price_sel, url_base):
            return r

    try_playwright_fallback(r, patterns[0], prod_sel, price_sel)
    return r


def limpiar_precio(texto):
    if not texto:
        return None
    limpio = re.sub(r"[^\d.,]", "", texto)
    if not limpio:
        return None
    if "," in limpio:
        partes = limpio.split(",")
        entero = partes[0].replace(".", "")
        decimal = partes[1] if len(partes) > 1 else "0"
        limpio = f"{entero}.{decimal}"
    else:
        partes = limpio.split(".")
        if len(partes) > 1 and len(partes[-1]) == 3:
            limpio = limpio.replace(".", "")
        else:
            limpio = limpio.replace(".", "")
    try:
        val = float(limpio)
        return val if val > 0 else None
    except ValueError:
        return None


SCRAPERS_MAP = {
    "biggie.com.py": scrape_biggie,
    "biggie.com": scrape_biggie,
    "stock.com.py": scrape_stock,
    "superseis.com.py": scrape_superseis,
    "salemmaonline.com.py": scrape_salemma,
    "realonline.com.py": scrape_real,
}


def get_scraper(url):
    from urllib.parse import urlparse
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
        "ahorro_maximo": (max(precios) - min(precios)) if len(precios) >= 2 else 0,
        "playwright_disponible": PLAYWRIGHT_OK,
        "resultados": resultados,
    }


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "api": "SuperComparador Paraguay",
        "version": "2.0",
        "playwright": PLAYWRIGHT_OK,
        "endpoints": ["/ping", "/buscar?barcode=...", "/buscar-custom (POST)"]
    })


@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "playwright": PLAYWRIGHT_OK})


@app.route("/buscar", methods=["GET"])
def buscar():
    barcode = request.args.get("barcode", "").strip()
    if not barcode:
        return jsonify({"error": "Parametro barcode requerido"}), 400
    logger.info(f"Buscando: {barcode}")
    resultados = []
    for fn in [scrape_biggie, scrape_stock, scrape_superseis, scrape_salemma, scrape_real]:
        try:
            resultados.append(fn(barcode))
        except Exception as e:
            logger.error(f"{fn.__name__}: {e}")
    return jsonify(calcular_resumen(barcode, resultados))


@app.route("/buscar-custom", methods=["POST"])
def buscar_custom():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Body JSON requerido"}), 400
    barcode = data.get("barcode", "").strip()
    supermercados = data.get("supermercados", [])
    if not barcode:
        return jsonify({"error": "Campo barcode requerido"}), 400
    if not supermercados:
        return jsonify({"error": "Campo supermercados requerido"}), 400
    resultados = []
    for item in supermercados:
        nombre = item.get("nombre", "Supermercado")
        url = item.get("url", "")
        if not url:
            continue
        fn = get_scraper(url)
        res = fn(barcode) if fn else scrape_generico(barcode, nombre, url)
        res["supermercado"] = nombre
        resultados.append(res)
    return jsonify(calcular_resumen(barcode, resultados))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
