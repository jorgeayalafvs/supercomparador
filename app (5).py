from flask import Flask, jsonify, request
from flask_cors import CORS
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup
import re, os, logging, json
from urllib.parse import urlparse, urljoin

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
        return r.text if r.status_code == 200 else None
    except Exception as e:
        logger.warning(f"fetch {url}: {e}")
        return None

def limpiar_precio(txt):
    if not txt: return None
    txt = re.sub(r"[^\d]", "", str(txt))
    try:
        n = float(txt)
        return n if n > 500 else None
    except: return None

def base_result(nombre, url, barcode):
    return {
        "supermercado": nombre, "url_supermercado": url, "barcode": barcode,
        "nombre": None, "precio": None,
        "url_producto": f"{url.rstrip('/')}/search?q={barcode}",
        "imagen": None, "disponible": False, "error": "No encontrado"
    }

def jsonld_precio(html):
    try:
        soup = BeautifulSoup(html, "html.parser")
        for sc in soup.find_all("script", type="application/ld+json"):
            try:
                d = json.loads(sc.string or "")
                if isinstance(d, list): d = d[0]
                if d.get("@type") == "Product":
                    offers = d.get("offers", {})
                    if isinstance(offers, list): offers = offers[0]
                    p = limpiar_precio(str(offers.get("price", "")))
                    if p: return d.get("name"), p
            except: continue
    except: pass
    return None, None

def precio_texto(html):
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["nav","header","footer","script","style"]):
            tag.decompose()
        txt = soup.get_text(" ", strip=True)
        for pat in [r'[₲Gg][Ss]?\.?\s*([\d\.]+)', r'([\d]{4,7})\s*[₲Gg]']:
            m = re.search(pat, txt, re.I)
            if m:
                p = limpiar_precio(m.group(1))
                if p: return p
    except: pass
    return None

def seguir_link(html, base_url, barcode, pattern):
    """Sigue el primer link que contiene el pattern en la URL"""
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        if pattern in a["href"]:
            url = urljoin(base_url, a["href"])
            html2 = fetch(url)
            if html2:
                return url, html2
    return None, None

# ══════════════════════════════════════════════
# SUPERSEIS ✅
# ══════════════════════════════════════════════
def scrape_superseis(barcode):
    base = "https://www.superseis.com.py"
    r = base_result("Superseis", base, barcode)
    html = fetch(f"{base}/search?search={barcode}")
    if not html: r["error"] = "Sin conexión"; return r
    prod_url, html2 = seguir_link(html, base, barcode, "/product/")
    if html2:
        r["url_producto"] = prod_url
        nombre, precio = jsonld_precio(html2)
        if not precio: precio = precio_texto(html2)
        if precio:
            r["precio"] = precio
            r["nombre"] = nombre or BeautifulSoup(html2,"html.parser").select_one("h1") and BeautifulSoup(html2,"html.parser").select_one("h1").get_text(strip=True)
            r["disponible"] = True
            r["error"] = None
    else:
        p = precio_texto(html)
        if p: r["precio"] = p; r["disponible"] = True; r["error"] = None
    return r

# ══════════════════════════════════════════════
# SALEMMA ✅
# ══════════════════════════════════════════════
def scrape_salemma(barcode):
    base = "https://www.salemmaonline.com.py"
    r = base_result("Salemma", base, barcode)
    html = fetch(f"{base}/search?q={barcode}")
    if not html: r["error"] = "Sin conexión"; return r
    soup = BeautifulSoup(html, "html.parser")
    links = [urljoin(base, a["href"]) for a in soup.find_all("a", href=True) if "/producto/" in a["href"]]
    for lnk in links[:5]:
        html2 = fetch(lnk)
        if html2 and barcode in html2:
            r["url_producto"] = lnk
            soup2 = BeautifulSoup(html2, "html.parser")
            h1 = soup2.select_one("h1")
            if h1: r["nombre"] = h1.get_text(strip=True)
            r["precio"] = precio_texto(html2)
            if r["precio"]: r["disponible"] = True; r["error"] = None
            return r
    r["error"] = "Producto no encontrado"
    return r

# ══════════════════════════════════════════════
# CASA RICA ✅ - usa /catalogo?q=
# ══════════════════════════════════════════════
def scrape_casarica(barcode):
    base = "https://www.casarica.com.py"
    r = base_result("Casa Rica", base, barcode)
    search_url = f"{base}/catalogo?q={barcode}"
    r["url_producto"] = search_url
    html = fetch(search_url)
    if not html: r["error"] = "Sin conexión"; return r

    soup = BeautifulSoup(html, "html.parser")
    # Casa Rica usa URLs tipo /nombre-producto-pNUMERO
    prod_link = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r'-p\d+$', href):
            prod_link = urljoin(base, href)
            break

    if not prod_link:
        p = precio_texto(html)
        if p: r["precio"] = p; r["disponible"] = True; r["error"] = None
        return r

    r["url_producto"] = prod_link
    html2 = fetch(prod_link)
    if html2:
        nombre, precio = jsonld_precio(html2)
        if not precio: precio = precio_texto(html2)
        if precio:
            r["precio"] = precio
            r["nombre"] = nombre
            if not nombre:
                soup2 = BeautifulSoup(html2, "html.parser")
                h1 = soup2.select_one("h1")
                if h1: r["nombre"] = h1.get_text(strip=True)
            r["disponible"] = True
            r["error"] = None
    return r

# ══════════════════════════════════════════════
# LOS JARDINES - usa /search?q=
# ══════════════════════════════════════════════
def scrape_losjardines(barcode):
    base = "https://www.losjardinesonline.com.py"
    r = base_result("Los Jardines", base, barcode)
    search_url = f"{base}/search?q={barcode}"
    r["url_producto"] = search_url
    html = fetch(search_url)
    if not html: r["error"] = "Sin conexión"; return r

    soup = BeautifulSoup(html, "html.parser")
    prod_link = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(x in href for x in ["/product/", "/item/", "/p/", "/producto/"]):
            prod_link = urljoin(base, href)
            break

    if prod_link:
        r["url_producto"] = prod_link
        html2 = fetch(prod_link)
        if html2:
            nombre, precio = jsonld_precio(html2)
            if not precio: precio = precio_texto(html2)
            if precio:
                r["precio"] = precio; r["nombre"] = nombre
                r["disponible"] = True; r["error"] = None
    else:
        p = precio_texto(html)
        if p: r["precio"] = p; r["disponible"] = True; r["error"] = None

    if not r["disponible"]: r["error"] = "Producto no encontrado"
    return r

# ══════════════════════════════════════════════
# GRAN VÍA - usa /search?q=
# ══════════════════════════════════════════════
def scrape_granvia(barcode):
    base = "https://www.granvia.com.py"
    r = base_result("Gran Vía", base, barcode)
    search_url = f"{base}/search?q={barcode}"
    r["url_producto"] = search_url
    html = fetch(search_url)
    if not html: r["error"] = "Sin conexión"; return r

    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(x in href for x in ["/product/", "/item/", "/p/", "/producto/"]):
            prod_url = urljoin(base, href)
            r["url_producto"] = prod_url
            html2 = fetch(prod_url)
            if html2:
                nombre, precio = jsonld_precio(html2)
                if not precio: precio = precio_texto(html2)
                if precio:
                    r["precio"] = precio; r["nombre"] = nombre
                    r["disponible"] = True; r["error"] = None
            break

    if not r["disponible"]:
        p = precio_texto(html)
        if p: r["precio"] = p; r["disponible"] = True; r["error"] = None
        else: r["error"] = "Producto no encontrado"
    return r

# ══════════════════════════════════════════════
# MANUAL (bloqueados desde USA)
# ══════════════════════════════════════════════
def manual(nombre, sitio, barcode):
    r = base_result(nombre, sitio, barcode)
    r["url_producto"] = f"{sitio}/search?q={barcode}"
    r["error"] = "Consultar manualmente"
    return r

# ══════════════════════════════════════════════
# GENÉRICO para supers agregados desde la app
# ══════════════════════════════════════════════
def scrape_generico(nombre, url_base, barcode):
    r = base_result(nombre, url_base, barcode)
    for url in [
        f"{url_base.rstrip('/')}/search?q={barcode}",
        f"{url_base.rstrip('/')}/search?search={barcode}",
        f"{url_base.rstrip('/')}/catalogo?q={barcode}",
        f"{url_base.rstrip('/')}/buscar?q={barcode}",
    ]:
        html = fetch(url)
        if not html or len(html) < 1000: continue
        r["url_producto"] = url
        nombre_ld, precio_ld = jsonld_precio(html)
        if precio_ld:
            r["nombre"] = nombre_ld; r["precio"] = precio_ld
            r["disponible"] = True; r["error"] = None; return r
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if any(x in href for x in ["/product/", "/item/", "/p/", "/producto/", "-p"]):
                prod_url = urljoin(url_base, href)
                html2 = fetch(prod_url)
                if html2:
                    n, p = jsonld_precio(html2)
                    if not p: p = precio_texto(html2)
                    if p:
                        r["precio"] = p; r["nombre"] = n
                        r["disponible"] = True; r["error"] = None
                        r["url_producto"] = prod_url; return r
                break
        p = precio_texto(html)
        if p:
            r["precio"] = p; r["disponible"] = True; r["error"] = None; return r
    r["error"] = "Consultar manualmente"
    return r

SCRAPERS = {
    "superseis.com.py": lambda b: scrape_superseis(b),
    "salemmaonline.com.py": lambda b: scrape_salemma(b),
    "casarica.com.py": lambda b: scrape_casarica(b),
    "losjardinesonline.com.py": lambda b: scrape_losjardines(b),
    "granvia.com.py": lambda b: scrape_granvia(b),
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
        "ahorro_maximo": round(max(precios)-min(precios), 0) if len(precios) >= 2 else 0,
        "resultados": resultados
    }

@app.route("/")
def home():
    return jsonify({"api": "SuperComparador Paraguay", "version": "10.0", "estado": "online"})

@app.route("/ping")
def ping():
    return jsonify({"status": "ok", "version": "10.0"})

@app.route("/buscar")
def buscar():
    barcode = request.args.get("barcode", "").strip()
    if not barcode: return jsonify({"error": "barcode requerido"}), 400

    tareas = [
        lambda: scrape_superseis(barcode),
        lambda: scrape_salemma(barcode),
        lambda: scrape_casarica(barcode),
        lambda: scrape_losjardines(barcode),
        lambda: scrape_granvia(barcode),
        lambda: manual("Biggie", "https://biggie.com.py", barcode),
        lambda: manual("Stock", "https://www.stock.com.py", barcode),
        lambda: manual("Real", "https://www.realonline.com.py", barcode),
    ]

    resultados = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(t) for t in tareas]
        for f in as_completed(futures):
            try: resultados.append(f.result())
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

    def hacer(item):
        nombre = item.get("nombre", "Supermercado")
        url = item.get("url", "")
        if not url: return None
        fn = get_scraper(url)
        res = fn(barcode) if fn else scrape_generico(nombre, url, barcode)
        res["supermercado"] = nombre
        return res

    resultados = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(hacer, item) for item in supermercados]
        for f in as_completed(futures):
            try:
                res = f.result()
                if res: resultados.append(res)
            except Exception as e: logger.error(str(e))

    return jsonify(calcular_resumen(barcode, resultados))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
