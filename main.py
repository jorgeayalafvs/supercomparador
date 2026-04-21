"""
SuperComparador Paraguay - Backend API
Servidor Flask que hace scraping de precios en supermercados paraguayos.
Deploy en Render.com (gratis).
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # Permite que la app Android/HTML consuma esta API

# ─────────────────────────────────────────────
# HEADERS que simulan un navegador real
# ─────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 12; Pixel 6) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-PY,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ─────────────────────────────────────────────
# SCRAPERS POR SUPERMERCADO
# Cada función recibe el código de barras y
# devuelve: { nombre, precio, url, imagen, disponible }
# ─────────────────────────────────────────────

def scrape_biggie(barcode: str) -> dict:
    """biggie.com.py — busca por código de barras en la URL"""
    base = "https://biggie.com.py"
    search_url = f"{base}/search?q={barcode}"
    result = {
        "supermercado": "Biggie",
        "url_supermercado": base,
        "barcode": barcode,
        "nombre": None,
        "precio": None,
        "url_producto": search_url,
        "imagen": None,
        "disponible": False,
        "error": None,
    }
    try:
        r = SESSION.get(search_url, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Intentar extraer primer producto de resultados
        # Biggie usa Shopify-like structure
        product_link = soup.select_one("a.product-item__title, a[href*='/products/']")
        if product_link:
            prod_url = base + product_link["href"] if product_link["href"].startswith("/") else product_link["href"]
            result["url_producto"] = prod_url
            time.sleep(0.5)
            pr = SESSION.get(prod_url, timeout=15)
            ps = BeautifulSoup(pr.text, "html.parser")

            # Nombre
            name_el = ps.select_one("h1.product__title, h1.product-single__title, h1")
            if name_el:
                result["nombre"] = name_el.get_text(strip=True)

            # Precio
            price_el = ps.select_one(
                "span.price-item--regular, span.product__price, "
                "[class*='price'] span, span.money"
            )
            if price_el:
                raw = price_el.get_text(strip=True)
                result["precio"] = limpiar_precio(raw)

            # Imagen
            img_el = ps.select_one("img.product__image, img.product-single__photo")
            if img_el:
                src = img_el.get("src") or img_el.get("data-src", "")
                result["imagen"] = ("https:" + src) if src.startswith("//") else src

            result["disponible"] = result["precio"] is not None
    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Biggie error: {e}")
    return result


def scrape_stock(barcode: str) -> dict:
    """stock.com.py — supermercado Stock Paraguay"""
    base = "https://www.stock.com.py"
    search_url = f"{base}/search?q={barcode}"
    result = {
        "supermercado": "Stock",
        "url_supermercado": base,
        "barcode": barcode,
        "nombre": None,
        "precio": None,
        "url_producto": search_url,
        "imagen": None,
        "disponible": False,
        "error": None,
    }
    try:
        r = SESSION.get(search_url, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Stock usa VTEX
        product_el = soup.select_one(
            ".product-name a, h2.product-name a, "
            "a.product-item-link, [class*='ProductName'] a"
        )
        if product_el:
            prod_url = product_el.get("href", "")
            if prod_url and not prod_url.startswith("http"):
                prod_url = base + prod_url
            result["url_producto"] = prod_url

            time.sleep(0.5)
            pr = SESSION.get(prod_url, timeout=15)
            ps = BeautifulSoup(pr.text, "html.parser")

            name_el = ps.select_one("h1.productName, h1[class*='ProductName'], h1")
            if name_el:
                result["nombre"] = name_el.get_text(strip=True)

            price_el = ps.select_one(
                ".priceText, [class*='sellingPrice'], "
                "[class*='Price'] .price, .skuBestPrice"
            )
            if price_el:
                result["precio"] = limpiar_precio(price_el.get_text(strip=True))

            img_el = ps.select_one("img#image-main, img.product-image, img[class*='product']")
            if img_el:
                src = img_el.get("src") or img_el.get("data-src", "")
                result["imagen"] = ("https:" + src) if src.startswith("//") else src

            result["disponible"] = result["precio"] is not None
        else:
            result["error"] = "Producto no encontrado en resultados"
    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Stock error: {e}")
    return result


def scrape_superseis(barcode: str) -> dict:
    """superseis.com.py"""
    base = "https://www.superseis.com.py"
    search_url = f"{base}/search?q={barcode}"
    result = {
        "supermercado": "Superseis",
        "url_supermercado": base,
        "barcode": barcode,
        "nombre": None,
        "precio": None,
        "url_producto": search_url,
        "imagen": None,
        "disponible": False,
        "error": None,
    }
    try:
        r = SESSION.get(search_url, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        product_el = soup.select_one(
            "a.product-name, h2.product-name a, "
            "[class*='product-title'] a, .product-item-name a"
        )
        if product_el:
            prod_url = product_el.get("href", "")
            if not prod_url.startswith("http"):
                prod_url = base + prod_url
            result["url_producto"] = prod_url

            time.sleep(0.5)
            pr = SESSION.get(prod_url, timeout=15)
            ps = BeautifulSoup(pr.text, "html.parser")

            name_el = ps.select_one("h1")
            if name_el:
                result["nombre"] = name_el.get_text(strip=True)

            price_el = ps.select_one(
                "[class*='price']:not([class*='old']):not([class*='was']), "
                ".special-price .price, .regular-price .price"
            )
            if price_el:
                result["precio"] = limpiar_precio(price_el.get_text(strip=True))

            img_el = ps.select_one(".product-image-main img, img.gallery-placeholder__image")
            if img_el:
                src = img_el.get("src") or img_el.get("data-src", "")
                result["imagen"] = ("https:" + src) if src.startswith("//") else src

            result["disponible"] = result["precio"] is not None
    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Superseis error: {e}")
    return result


def scrape_salemma(barcode: str) -> dict:
    """salemmaonline.com.py"""
    base = "https://www.salemmaonline.com.py"
    search_url = f"{base}/search?q={barcode}"
    result = {
        "supermercado": "Salemma",
        "url_supermercado": base,
        "barcode": barcode,
        "nombre": None,
        "precio": None,
        "url_producto": search_url,
        "imagen": None,
        "disponible": False,
        "error": None,
    }
    try:
        r = SESSION.get(search_url, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        product_el = soup.select_one(
            "a[href*='/products/'], a.product-item__title, "
            ".grid-product__title, h3.grid-product__title a"
        )
        if product_el:
            prod_url = product_el.get("href", "")
            if not prod_url.startswith("http"):
                prod_url = base + prod_url
            result["url_producto"] = prod_url

            time.sleep(0.5)
            pr = SESSION.get(prod_url, timeout=15)
            ps = BeautifulSoup(pr.text, "html.parser")

            name_el = ps.select_one("h1")
            if name_el:
                result["nombre"] = name_el.get_text(strip=True)

            price_el = ps.select_one("span.money, .product__price, [class*='price']")
            if price_el:
                result["precio"] = limpiar_precio(price_el.get_text(strip=True))

            img_el = ps.select_one("img.product__image, img.photoswipe__image")
            if img_el:
                src = img_el.get("src") or img_el.get("data-src", "")
                result["imagen"] = ("https:" + src) if src.startswith("//") else src

            result["disponible"] = result["precio"] is not None
    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Salemma error: {e}")
    return result


def scrape_real(barcode: str) -> dict:
    """realonline.com.py"""
    base = "https://www.realonline.com.py"
    search_url = f"{base}/search?q={barcode}"
    result = {
        "supermercado": "Real",
        "url_supermercado": base,
        "barcode": barcode,
        "nombre": None,
        "precio": None,
        "url_producto": search_url,
        "imagen": None,
        "disponible": False,
        "error": None,
    }
    try:
        r = SESSION.get(search_url, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        product_el = soup.select_one(
            "a[href*='/p/'], a.product-name, "
            "[class*='ProductName'] a, h2 a"
        )
        if product_el:
            prod_url = product_el.get("href", "")
            if not prod_url.startswith("http"):
                prod_url = base + prod_url
            result["url_producto"] = prod_url

            time.sleep(0.5)
            pr = SESSION.get(prod_url, timeout=15)
            ps = BeautifulSoup(pr.text, "html.parser")

            name_el = ps.select_one("h1")
            if name_el:
                result["nombre"] = name_el.get_text(strip=True)

            price_el = ps.select_one(
                ".priceText, [class*='sellingPrice'], "
                "[class*='Price'] .price"
            )
            if price_el:
                result["precio"] = limpiar_precio(price_el.get_text(strip=True))

            img_el = ps.select_one("img.product-image, img[class*='product']")
            if img_el:
                src = img_el.get("src") or img_el.get("data-src", "")
                result["imagen"] = ("https:" + src) if src.startswith("//") else src

            result["disponible"] = result["precio"] is not None
    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Real error: {e}")
    return result


def scrape_generico(barcode: str, nombre_super: str, url_base: str) -> dict:
    """
    Scraper genérico para supermercados agregados manualmente.
    Prueba patrones comunes de búsqueda.
    """
    result = {
        "supermercado": nombre_super,
        "url_supermercado": url_base,
        "barcode": barcode,
        "nombre": None,
        "precio": None,
        "url_producto": f"{url_base.rstrip('/')}/search?q={barcode}",
        "imagen": None,
        "disponible": False,
        "error": None,
    }
    search_patterns = [
        f"{url_base.rstrip('/')}/search?q={barcode}",
        f"{url_base.rstrip('/')}/catalogsearch/result/?q={barcode}",
        f"{url_base.rstrip('/')}/buscar?q={barcode}",
    ]
    try:
        html = None
        for url in search_patterns:
            try:
                r = SESSION.get(url, timeout=12)
                if r.status_code == 200:
                    html = r.text
                    result["url_producto"] = url
                    break
            except Exception:
                continue

        if not html:
            result["error"] = "No se pudo acceder al supermercado"
            return result

        soup = BeautifulSoup(html, "html.parser")

        # Intentar encontrar el primer producto
        product_link = soup.select_one(
            "a[href*='/products/'], a[href*='/p/'], "
            "a.product-name, h2 a, h3 a, .product-title a"
        )
        if product_link:
            prod_url = product_link.get("href", "")
            if not prod_url.startswith("http"):
                from urllib.parse import urljoin
                prod_url = urljoin(url_base, prod_url)
            result["url_producto"] = prod_url
            time.sleep(0.5)
            pr = SESSION.get(prod_url, timeout=12)
            ps = BeautifulSoup(pr.text, "html.parser")

            name_el = ps.select_one("h1")
            if name_el:
                result["nombre"] = name_el.get_text(strip=True)

            for sel in [
                "span.money", "[class*='price']", "[class*='Price']",
                ".price", ".precio"
            ]:
                price_el = ps.select_one(sel)
                if price_el:
                    p = limpiar_precio(price_el.get_text(strip=True))
                    if p and p > 0:
                        result["precio"] = p
                        break

            result["disponible"] = result["precio"] is not None
        else:
            result["error"] = "Producto no encontrado"
    except Exception as e:
        result["error"] = str(e)
    return result


# ─────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────

def limpiar_precio(texto: str) -> float | None:
    """Extrae el número de un string de precio como 'Gs. 12.500' o '12,500'"""
    if not texto:
        return None
    # Remover todo excepto dígitos, puntos y comas
    limpio = re.sub(r"[^\d.,]", "", texto)
    if not limpio:
        return None
    # Paraguay usa punto como separador de miles: 12.500
    # Quitar puntos de miles, reemplazar coma decimal
    if "," in limpio:
        partes = limpio.split(",")
        entero = partes[0].replace(".", "")
        decimal = partes[1] if len(partes) > 1 else "0"
        limpio = f"{entero}.{decimal}"
    else:
        # Solo puntos: si el último grupo tiene 3 dígitos, es separador de miles
        partes = limpio.split(".")
        if len(partes) > 1 and len(partes[-1]) == 3:
            limpio = limpio.replace(".", "")
        else:
            limpio = limpio.replace(".", "")
    try:
        return float(limpio)
    except ValueError:
        return None


SCRAPERS_CONOCIDOS = {
    "biggie.com.py": scrape_biggie,
    "biggie.com": scrape_biggie,
    "stock.com.py": scrape_stock,
    "superseis.com.py": scrape_superseis,
    "salemmaonline.com.py": scrape_salemma,
    "realonline.com.py": scrape_real,
}

def get_scraper(url: str):
    """Devuelve el scraper específico si existe, sino el genérico"""
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lower().replace("www.", "")
    return SCRAPERS_CONOCIDOS.get(domain, None)


# ─────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "api": "SuperComparador Paraguay",
        "version": "1.0",
        "endpoints": {
            "GET /buscar": "Busca un producto por código de barras en todos los supermercados configurados",
            "POST /buscar-custom": "Busca en una lista personalizada de supermercados",
            "GET /ping": "Health check"
        }
    })


@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok"})


@app.route("/buscar", methods=["GET"])
def buscar():
    """
    GET /buscar?barcode=7840058002549
    Busca en todos los supermercados predefinidos.
    """
    barcode = request.args.get("barcode", "").strip()
    if not barcode:
        return jsonify({"error": "Parámetro 'barcode' requerido"}), 400

    logger.info(f"Buscando código: {barcode}")

    scrapers = [
        scrape_biggie,
        scrape_stock,
        scrape_superseis,
        scrape_salemma,
        scrape_real,
    ]

    resultados = []
    for scraper in scrapers:
        try:
            resultado = scraper(barcode)
            resultados.append(resultado)
        except Exception as e:
            logger.error(f"Error en scraper {scraper.__name__}: {e}")

    precios_disponibles = [r["precio"] for r in resultados if r["precio"] is not None]

    resumen = {
        "barcode": barcode,
        "total_supermercados": len(resultados),
        "encontrado_en": len(precios_disponibles),
        "precio_minimo": min(precios_disponibles) if precios_disponibles else None,
        "precio_maximo": max(precios_disponibles) if precios_disponibles else None,
        "ahorro_maximo": (max(precios_disponibles) - min(precios_disponibles)) if len(precios_disponibles) >= 2 else 0,
        "resultados": resultados,
    }
    return jsonify(resumen)


@app.route("/buscar-custom", methods=["POST"])
def buscar_custom():
    """
    POST /buscar-custom
    Body: { "barcode": "...", "supermercados": [{"nombre": "...", "url": "..."}, ...] }
    Busca en una lista personalizada de supermercados.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Body JSON requerido"}), 400

    barcode = data.get("barcode", "").strip()
    supermercados = data.get("supermercados", [])

    if not barcode:
        return jsonify({"error": "Campo 'barcode' requerido"}), 400
    if not supermercados:
        return jsonify({"error": "Campo 'supermercados' requerido (lista)"}), 400

    resultados = []
    for super_item in supermercados:
        nombre = super_item.get("nombre", "Supermercado")
        url = super_item.get("url", "")
        if not url:
            continue

        scraper_fn = get_scraper(url)
        if scraper_fn:
            resultado = scraper_fn(barcode)
        else:
            resultado = scrape_generico(barcode, nombre, url)

        resultado["supermercado"] = nombre
        resultados.append(resultado)

    precios_disponibles = [r["precio"] for r in resultados if r["precio"] is not None]

    return jsonify({
        "barcode": barcode,
        "total_supermercados": len(resultados),
        "encontrado_en": len(precios_disponibles),
        "precio_minimo": min(precios_disponibles) if precios_disponibles else None,
        "precio_maximo": max(precios_disponibles) if precios_disponibles else None,
        "ahorro_maximo": (max(precios_disponibles) - min(precios_disponibles)) if len(precios_disponibles) >= 2 else 0,
        "resultados": resultados,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
