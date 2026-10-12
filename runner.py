"""Orquesta un ciclo completo: capturar, guardar, evaluar, avisar."""

import logging
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Bajo pytest, `src` entra por el pythonpath del pyproject.toml. Al ejecutar
# `python runner.py` a secas no hay tal cosa, así que se añade aquí.
sys.path.insert(0, str(Path(__file__).parent / "src"))

from almacen.conexion import abrir                 # noqa: E402
from almacen.operaciones import (                  # noqa: E402
    alta_o_actualiza_producto,
    dias_de_historico,
    guardar_identidad,
    ultima_alerta_de_articulo,
    marcar_comparado,
    minimo_historico,
    productos_a_comparar,
    registrar_alerta,
    registrar_si_cambio,
    ultima_alerta,
)
from avisos.archivo import NotificadorArchivo      # noqa: E402
from avisos.telegram import (                      # noqa: E402
    NotificadorTelegram,
    TelegramMalConfigurado,
)
from fuentes.base import Referencia                # noqa: E402
from fuentes.falabella import Falabella            # noqa: E402
from fuentes.paris import Paris                    # noqa: E402
from fuentes.solotodo import SoloTodo              # noqa: E402
from fuentes.transporte import crear_transporte    # noqa: E402
from nucleo.comparacion import ReglasComparacion, mejor_oferta  # noqa: E402
from nucleo.evaluador import Reglas, evaluar       # noqa: E402
from nucleo.frecuencia import debe_notificar       # noqa: E402

registro = logging.getLogger("rastreador")

RAIZ = Path(__file__).parent


def cargar_config(ruta: Path | str) -> dict:
    return yaml.safe_load(Path(ruta).read_text(encoding="utf-8"))


def _reglas_de(entrada: dict, que: str) -> Reglas | None:
    """Las Reglas de una entrada, o None si declara alguna que no existe.

    Una errata en el YAML debe costar ese producto, no el ciclo entero.
    """
    try:
        return Reglas(**(entrada.get("reglas") or {}))
    except TypeError as error:
        registro.warning("se salta %s por una regla desconocida: %s", que, error)
        return None


def _entrada_valida(entrada, obligatorias: tuple[str, ...], donde: str) -> bool:
    """Si a una entrada le falta lo imprescindible, se avisa y se salta."""
    if not isinstance(entrada, dict):
        registro.warning("se salta una entrada de %s que no es un mapa: %r", donde, entrada)
        return False
    faltan = [clave for clave in obligatorias if not entrada.get(clave)]
    if faltan:
        registro.warning(
            "se salta una entrada de %s sin %s: %r", donde, ", ".join(faltan), entrada
        )
        return False
    return True


def cargar_lista_deseos(ruta: Path | str) -> tuple[list[dict], list[dict]]:
    """Devuelve (vigilados, busquedas) con las reglas ya convertidas."""
    datos = yaml.safe_load(Path(ruta).read_text(encoding="utf-8")) or {}

    vigilados = []
    for entrada in datos.get("vigilados") or []:
        if not _entrada_valida(entrada, ("tienda", "url"), "vigilados"):
            continue
        reglas = _reglas_de(entrada, entrada["url"])
        if reglas is None:
            continue
        vigilados.append(
            {
                "tienda": entrada["tienda"],
                "fuente": entrada.get("fuente") or entrada["tienda"],
                "url": entrada["url"],
                "reglas": reglas,
            }
        )

    busquedas = []
    for entrada in datos.get("busquedas") or []:
        if not _entrada_valida(entrada, ("tienda",), "busquedas"):
            continue
        termino = entrada.get("termino")
        categoria = entrada.get("categoria")
        if not termino and categoria is None:
            # Una de las dos hace falta: sin ninguna no hay nada que buscar, y
            # la entrada se saltaría en silencio dentro de la fuente.
            registro.warning(
                "se salta una búsqueda sin termino ni categoria: %r", entrada
            )
            continue
        reglas = _reglas_de(entrada, termino or f"categoría {categoria}")
        if reglas is None:
            continue
        busquedas.append(
            {
                "tienda": entrada["tienda"],
                "fuente": entrada.get("fuente") or entrada["tienda"],
                "termino": termino,
                "categoria": categoria,
                "reglas": reglas,
            }
        )

    return vigilados, busquedas


def _confirmada(captura, fuente):
    """La captura leída de la ficha, o None si no se pudo leer.

    Un precio inferido del orden en que la tienda lista sus ofertas no puede
    disparar una alerta —mandaría al usuario a pagar más de lo que decía el
    aviso— ni entrar en el histórico, donde contaminaría el mínimo que sostiene
    todas las alertas de descuento.
    """
    referencia = Referencia(
        tienda=captura.tienda, url=captura.url, sku_externo=captura.sku_externo
    )
    try:
        exactas = fuente.obtener([referencia])
    except Exception as error:  # noqa: BLE001
        registro.warning("no se pudo confirmar %s: %s", captura.url, error, exc_info=True)
        return None

    if not exactas:
        registro.warning("no se pudo confirmar %s: la ficha no dio datos", captura.url)
        return None

    exacta = exactas[0]
    if not exacta.precios_etiquetados:
        # La confirmación existe para obtener un precio etiquetado. Si la ficha
        # tampoco lo trae, no hay nada que confirmar: antes ninguna alerta que
        # una basada en un precio adivinado.
        registro.warning(
            "no se pudo confirmar %s: la ficha tampoco trae precios etiquetados",
            captura.url,
        )
        return None
    return exacta


def _procesar(con, capturas, reglas, notificador, config, fuente) -> int:
    """Guarda las capturas, evalúa y avisa. Devuelve cuántas alertas mandó."""
    ventana = config["historico"]["ventana_minimo_dias"]
    dias_minimos = config["historico"]["dias_minimos_para_descuento"]
    cooldown = config["alertas"]["cooldown_dias"]

    enviadas = 0
    for captura in capturas:
        try:
            if not captura.precios_etiquetados:
                # Filtro barato: se evalúa el dato inferido SIN escribir nada.
                # Si no promete una oferta, se descarta sin gastar una petición.
                # Sin histórico, `evaluar` solo puede responder a reglas de
                # precio absoluto. Un producto descubierto con una regla de
                # solo descuento se descarta, y es correcto: tampoco podría
                # alertar, porque la guarda de arranque en frío lo impediría.
                if evaluar(captura, reglas, None, 0.0, dias_minimos) is None:
                    continue
                captura = _confirmada(captura, fuente)
                if captura is None:
                    continue

            producto_id = alta_o_actualiza_producto(con, captura)

            # El mínimo se consulta ANTES de insertar la captura de hoy: si no,
            # el precio actual sería su propio mínimo y el descuento siempre
            # cero.
            minimo = minimo_historico(con, producto_id, ventana)
            dias = dias_de_historico(con, producto_id)

            registrar_si_cambio(con, producto_id, captura)

            veredicto = evaluar(captura, reglas, minimo, dias, dias_minimos)
            if veredicto is None:
                continue
            anterior = ultima_alerta(con, producto_id, veredicto.regla)
            if not debe_notificar(anterior, veredicto.precio, cooldown):
                continue

            try:
                notificador.enviar(captura, veredicto)
            except Exception as error:
                # La captura ya está en el histórico, así que el dato no se
                # pierde. No se registra la alerta: el próximo ciclo volverá a
                # intentarlo.
                registro.warning(
                    "no se pudo avisar de %s (%s); se reintentará en el próximo ciclo",
                    captura.url,
                    error,
                )
                continue

            registrar_alerta(con, producto_id, veredicto.regla, veredicto.precio)
            enviadas += 1
        except Exception as error:  # noqa: BLE001
            # Con traza: este `except` es ancho a propósito, y sin el tipo y el
            # fichero un error nuestro se confundiría con una captura saltada.
            registro.warning(
                "no se pudo procesar %s: %s", captura.url, error, exc_info=True
            )
            continue

    return enviadas


def ejecutar_ciclo(con, fuentes, vigilados, busquedas, notificador, config) -> int:
    """Un ciclo completo. Un fallo de una tienda nunca detiene a las demás."""
    enviadas = 0

    for vigilado in vigilados:
        nombre_fuente = vigilado.get("fuente") or vigilado["tienda"]
        fuente = fuentes.get(nombre_fuente)
        if fuente is None:
            registro.warning(
                "sin fuente %r para la tienda %r", nombre_fuente, vigilado["tienda"]
            )
            continue
        try:
            capturas = fuente.obtener(
                [Referencia(tienda=vigilado["tienda"], url=vigilado["url"])]
            )
        except Exception as error:  # noqa: BLE001
            # Con traza: aquí escapa cualquier excepción de la fuente (un
            # `JSONDecodeError` si la API devuelve HTML, por ejemplo), y sin el
            # tipo y el fichero un fallo nuestro se confunde con un tropiezo de
            # la tienda. El sujeto es la URL y la tienda vigiladas, no
            # `fuente.nombre`: con una fuente compartida como solotodo, el
            # nombre no dice qué tienda falló.
            registro.warning(
                "no se pudo capturar %s (tienda %s) con la fuente %s: %s",
                vigilado["url"], vigilado["tienda"], fuente.nombre, error,
                exc_info=True,
            )
            continue
        enviadas += _procesar(con, capturas, vigilado["reglas"], notificador, config, fuente)

    limite = config.get("ejecucion", {}).get("limite_por_busqueda", 15)
    for busqueda in busquedas:
        nombre_fuente = busqueda.get("fuente") or busqueda["tienda"]
        fuente = fuentes.get(nombre_fuente)
        if fuente is None:
            registro.warning(
                "sin fuente %r para la tienda %r", nombre_fuente, busqueda["tienda"]
            )
            continue
        try:
            capturas = fuente.buscar(
                busqueda["termino"], limite, busqueda.get("categoria")
            )
        except Exception as error:  # noqa: BLE001
            # Con traza, por el mismo motivo que en el bucle de vigilados: el
            # sujeto es el término y la tienda de la búsqueda, no
            # `fuente.nombre`.
            registro.warning(
                "no se pudo buscar %r/categoría %s (tienda %s) con la fuente %s: %s",
                busqueda["termino"], busqueda.get("categoria"),
                busqueda["tienda"], fuente.nombre, error,
                exc_info=True,
            )
            continue
        enviadas += _procesar(con, capturas, busqueda["reglas"], notificador, config, fuente)

    return enviadas


def ejecutar_comparacion(con, fuente, notificador, config, desde: str) -> int:
    """Compara contra el resto del retail lo capturado en este ciclo.

    Es un paso aparte a propósito: si el agregador está caído, la captura que
    acaba de guardarse sigue siendo válida. Devuelve cuántos avisos mandó.
    """
    ajustes = config.get("comparacion") or {}
    if not ajustes.get("activa", False):
        return 0

    reglas = ReglasComparacion(
        caida_minima_pct=float(ajustes.get("caida_minima_pct", 10)),
        ahorro_minimo_pesos=int(ajustes.get("ahorro_minimo_pesos", 10000)),
    )
    cooldown = int(ajustes.get("cooldown_dias", config["alertas"]["cooldown_dias"]))
    tope = int(ajustes.get("maximo_por_ciclo", 25))

    enviadas = 0
    for producto in productos_a_comparar(con, desde, tope):
        try:
            identificador = producto.producto_solotodo
            if identificador is None:
                identificador = fuente.identidad_de(producto.tienda, producto.sku_externo)
                if identificador is None:
                    # No hay identidad y no la habrá preguntando otra vez hoy.
                    # Se marca para que el turno rote.
                    marcar_comparado(con, producto.id)
                    continue
                guardar_identidad(con, producto.id, identificador)

            ofertas = fuente.ofertas_de(identificador)
            veredicto = mejor_oferta(
                producto.precio_internet, producto.tienda, ofertas, reglas
            )
            # Se marca aquí, con veredicto o sin él: la comparación se hizo.
            # Si la petición hubiera fallado estaríamos en el `except` de
            # abajo, que TAMBIÉN marca: ver allí por qué.
            marcar_comparado(con, producto.id)
            if veredicto is None:
                continue

            # Por ARTÍCULO, no por fila: el mismo modelo vigilado en varias
            # tiendas encuentra la misma oferta ajena, y el hallazgo es uno.
            anterior = ultima_alerta_de_articulo(con, identificador, "mejor_precio")
            if not debe_notificar(anterior, veredicto.oferta.precio, cooldown):
                continue

            try:
                notificador.enviar_mejor_precio(producto, veredicto)
            except Exception as error:
                # No se registra la alerta: el próximo ciclo lo reintentará.
                registro.warning(
                    "no se pudo avisar del mejor precio de %s (%s); se reintentará",
                    producto.url, error,
                )
                continue

            registrar_alerta(con, producto.id, "mejor_precio", veredicto.oferta.precio)
            enviadas += 1
        except Exception as error:  # noqa: BLE001
            # Con traza, por el mismo motivo que en `ejecutar_ciclo`: el sujeto
            # es el producto, no la fuente, que siempre es la misma.
            registro.warning(
                "no se pudo comparar %s (tienda %s): %s",
                producto.url, producto.tienda, error, exc_info=True,
            )
            # Lo que llega aquí es persistente, no transitorio: `identidad_de`
            # y `ofertas_de` ya atrapan `TiendaBloqueada` por dentro y
            # devuelven `None` / `[]`, así que un bloqueo o una caída de red
            # nunca caen en este `except` y siguen el camino normal, donde ya
            # se marcan. Lo que sí llega es un `JSONDecodeError` sobre un 200
            # que no es JSON, o un `AttributeError` si la API cambia de forma:
            # fallos que se repetirán mañana igual que hoy. Combinado con
            # `ORDER BY ultima_comparacion ASC` (los no marcados van primero),
            # no marcar aquí formaría un trinquete: este producto roto se
            # quedaría para siempre en la cabecera de la cola, robándole el
            # turno a los demás en cuanto el tope del ciclo sea bajo. Se marca
            # en su propio try/except para que un fallo al escribir no vuelva
            # a estallar dentro de este manejador.
            try:
                marcar_comparado(con, producto.id)
            except Exception as error_marca:  # noqa: BLE001
                registro.warning(
                    "no se pudo marcar %s como comparado tras el fallo: %s",
                    producto.url, error_marca,
                )
            continue

    return enviadas


def construir_notificador(config):
    if config["alertas"].get("canal", "archivo") == "telegram":
        return NotificadorTelegram(
            token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        )
    return NotificadorArchivo(RAIZ / config["alertas"]["ruta_archivo"])


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # urllib3 registra la ruta de cada petición en DEBUG, y la de Telegram
    # lleva el token incrustado. No depender del nivel por defecto.
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    load_dotenv(RAIZ / ".env")

    config = cargar_config(RAIZ / "config" / "config.yaml")
    vigilados, busquedas = cargar_lista_deseos(RAIZ / "config" / "lista_deseos.yaml")

    transporte = crear_transporte(
        reintentos=config["ejecucion"]["reintentos"],
        pausa=tuple(config["ejecucion"]["espera_entre_peticiones_seg"]),
    )
    fuentes = {
        "paris": Paris(transporte),
        "falabella": Falabella(transporte),
        "solotodo": SoloTodo(
            transporte,
            excluidas=(config.get("solotodo") or {}).get("tiendas_excluidas", []),
        ),
    }

    try:
        notificador = construir_notificador(config)
    except TelegramMalConfigurado as error:
        print(f"Error de configuración: {error}")
        return 1

    con = abrir(RAIZ / "data" / "tracker.db")
    inicio = con.execute("SELECT datetime('now') AS ahora").fetchone()["ahora"]

    enviadas = ejecutar_ciclo(con, fuentes, vigilados, busquedas, notificador, config)
    registro.info("ciclo terminado: %s alertas enviadas", enviadas)

    comparadas = ejecutar_comparacion(
        con, fuentes["solotodo"], notificador, config, inicio
    )
    registro.info("comparación terminada: %s avisos de mejor precio", comparadas)

    try:
        from herramientas.exportar_web import exportar_ofertas
        exportar_ofertas(RAIZ / "data" / "tracker.db", RAIZ / "web" / "data" / "ofertas.json")
        registro.info("datos web exportados a web/data/ofertas.json")
    except Exception as error:
        registro.warning("no se pudo exportar datos para la web: %s", error)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
