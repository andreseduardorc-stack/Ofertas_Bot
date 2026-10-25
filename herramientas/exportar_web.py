"""Exporta las ofertas y alertas del tracker a un archivo JSON para la interfaz web."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).parent.parent
BASE = RAIZ / "data" / "tracker.db"
DESTINO = RAIZ / "web" / "data" / "ofertas.json"


def determinar_categoria(titulo: str) -> str:
    """Clasifica el producto en una categoría comercial clara."""
    t = (titulo or "").lower()
    if any(k in t for k in ["zapatilla", "zapato", "botin", "bota", "sandalia", "sneaker", "running"]):
        return "Zapatillas y Calzado"
    if any(k in t for k in [
        "poleron", "polerón", "chaqueta", "parka", "jeans", "polera", 
        "pantalon", "pantalón", "chaleco", "camisa", "blusa", "vestido", 
        "buzo", "ropa deportiva", "polar hombre", "polar mujer", "casaca",
        "sweater", "cardigan", "short", "legging", "cortaviento"
    ]):
        return "Ropa y Vestuario"
    if any(k in t for k in [
        "notebook", "laptop", "procesador", "ryzen", "core i", "geforce", "radeon", 
        "ddr4", "ddr5", "nvme", "ssd", "tarjeta de video", "placa madre", "monitor", 
        "teclado", "mouse", "all-in-one", "gabinete", "fuente de poder", "silla gamer"
    ]):
        return "Computación"
    if any(k in t for k in ["celular", "smartphone", "iphone", "galaxy", "xiaomi", "redmi", "smartwatch", "wearable"]):
        return "Telefonía y Wearables"
    if any(k in t for k in [
        "smart tv", "televisor", "tv ", " audifono", "audífono", "headset", "parlante", 
        "soundbar", "barra de sonido", "consola", "playstation", "ps5", "xbox", "nintendo"
    ]):
        return "Audio, TV y Consolas"
    if any(k in t for k in [
        "refrigerador", "lavadora", "secadora", "microondas", "freidora", "air fryer", 
        "cafetera", "aspiradora", "estufa", "aire acondicionado", "ventilador", "horno",
        "lavavajillas", "licuadora"
    ]):
        return "Electrohogar"
    if any(k in t for k in ["perfume", "fragancia", "secador", "alisador", "afeitadora"]):
        return "Belleza y Cuidado Personal"
    return "Otros"


def exportar_ofertas(ruta_base: Path = BASE, ruta_destino: Path = DESTINO) -> dict:
    if not ruta_base.exists():
        datos = {
            "actualizado_el": datetime.now().isoformat(),
            "total_ofertas": 0,
            "ofertas": [],
            "estadisticas": {"total_productos": 0, "tiendas": 0, "alertas_enviadas": 0},
        }
        ruta_destino.parent.mkdir(parents=True, exist_ok=True)
        ruta_destino.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
        return datos

    con = sqlite3.connect(ruta_base)
    con.row_factory = sqlite3.Row

    # 1. Total estadísticas generales
    tot_prod = con.execute("SELECT COUNT(*) FROM productos").fetchone()[0]
    tot_tiendas = con.execute("SELECT COUNT(DISTINCT tienda) FROM productos").fetchone()[0]
    tot_alertas = con.execute("SELECT COUNT(*) FROM registro_alertas").fetchone()[0]

    # 2. Obtener productos con alertas registradas o precios con descuento
    # Seleccionamos la alerta más reciente por producto para no duplicar tarjetas innecesariamente
    filas_alertas = con.execute(
        """
        WITH UltimaAlerta AS (
            SELECT producto_id, regla, precio_notificado, enviado_el,
                   ROW_NUMBER() OVER (PARTITION BY producto_id ORDER BY id DESC) as rn
            FROM registro_alertas
        ),
        UltimoPrecio AS (
            SELECT producto_id, precio_normal, precio_internet, precio_tarjeta, tarjeta_asociada, disponible, capturado_el,
                   ROW_NUMBER() OVER (PARTITION BY producto_id ORDER BY capturado_el DESC, id DESC) as rn
            FROM historico_precios
        ),
        PrecioAnterior AS (
            SELECT producto_id, precio_internet as precio_antiguo, precio_normal as normal_antiguo,
                   ROW_NUMBER() OVER (PARTITION BY producto_id ORDER BY capturado_el DESC, id DESC) as rn
            FROM historico_precios
            WHERE id NOT IN (SELECT id FROM UltimoPrecio WHERE rn = 1)
        ),
        MinimoHistorico AS (
            SELECT producto_id, MIN(precio_internet) as precio_minimo
            FROM historico_precios
            GROUP BY producto_id
        )
        SELECT 
            p.id,
            p.tienda,
            p.sku_externo,
            p.titulo,
            p.url,
            p.imagen_url,
            p.ultima_revision,
            ua.regla as alerta_regla,
            ua.precio_notificado as alerta_precio,
            ua.enviado_el as alerta_fecha,
            up.precio_normal,
            up.precio_internet,
            up.precio_tarjeta,
            up.tarjeta_asociada,
            up.disponible,
            mh.precio_minimo,
            pa.precio_antiguo
        FROM productos p
        LEFT JOIN UltimaAlerta ua ON ua.producto_id = p.id AND ua.rn = 1
        LEFT JOIN UltimoPrecio up ON up.producto_id = p.id AND up.rn = 1
        LEFT JOIN MinimoHistorico mh ON mh.producto_id = p.id
        LEFT JOIN PrecioAnterior pa ON pa.producto_id = p.id AND pa.rn = 1
        WHERE up.precio_internet IS NOT NULL
        ORDER BY 
            (ua.enviado_el IS NOT NULL) DESC,
            ua.enviado_el DESC,
            p.ultima_revision DESC
        LIMIT 300
        """
    ).fetchall()

    ofertas = []
    for f in filas_alertas:
        p_normal = f["precio_normal"]
        p_internet = f["precio_internet"]
        p_tarjeta = f["precio_tarjeta"]
        p_antiguo = f["precio_antiguo"]
        
        # Calcular precio de referencia base para el descuento
        precio_referencia = None
        if p_normal and p_normal > p_internet:
            precio_referencia = p_normal
        elif p_antiguo and p_antiguo > p_internet:
            precio_referencia = p_antiguo

        descuento_pct = 0
        ahorro = 0
        if precio_referencia and precio_referencia > p_internet:
            ahorro = precio_referencia - p_internet
            descuento_pct = round(((precio_referencia - p_internet) / precio_referencia) * 100)

        # Regla o tipo de badge
        regla_humana = "Oferta"
        regla_raw = f["alerta_regla"]
        if regla_raw == "mejor_precio":
            regla_humana = "Mejor Precio Retail"
        elif regla_raw == "precio_maximo":
            regla_humana = "Bajo Precio Máximo"
        elif regla_raw == "descuento_minimo_pct":
            regla_humana = "Descuento Histórico"
        elif f["precio_minimo"] and p_internet <= f["precio_minimo"]:
            regla_humana = "Mínimo Histórico"

        ofertas.append({
            "id": f["id"],
            "tienda": f["tienda"],
            "titulo": f["titulo"],
            "url": f["url"],
            "imagen_url": f["imagen_url"] or "",
            "categoria": determinar_categoria(f["titulo"]),
            "precio_internet": p_internet,
            "precio_normal": p_normal,
            "precio_tarjeta": p_tarjeta,
            "tarjeta_asociada": f["tarjeta_asociada"],
            "precio_referencia": precio_referencia,
            "descuento_pct": descuento_pct,
            "ahorro_pesos": ahorro,
            "disponible": bool(f["disponible"]),
            "regla": regla_humana,
            "tiene_alerta": bool(f["alerta_regla"]),
            "fecha_alerta": f["alerta_fecha"],
            "ultima_revision": f["ultima_revision"],
        })

    datos = {
        "actualizado_el": datetime.now().isoformat(),
        "total_ofertas": len(ofertas),
        "ofertas": ofertas,
        "estadisticas": {
            "total_productos": tot_prod,
            "tiendas": tot_tiendas,
            "alertas_enviadas": tot_alertas,
            "descuento_maximo": max([o["descuento_pct"] for o in ofertas], default=0),
        },
    }

    ruta_destino.parent.mkdir(parents=True, exist_ok=True)
    ruta_destino.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    con.close()
    return datos


if __name__ == "__main__":
    resultado = exportar_ofertas()
    print(f"Exportadas {len(resultado['ofertas'])} ofertas a {DESTINO}")
