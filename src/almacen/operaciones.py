"""Escrituras y consultas sobre la base. Nadie más toca SQL."""

import sqlite3

from fuentes.base import Captura
from nucleo.comparacion import ProductoVigilado


def alta_o_actualiza_producto(con: sqlite3.Connection, captura: Captura) -> int:
    """Devuelve el id del producto, dándolo de alta si es nuevo.

    La identidad del producto es el par (tienda, sku_externo). El título y la
    imagen se refrescan en cada pasada porque las tiendas los cambian.
    """
    try:
        con.execute(
            """
            INSERT INTO productos (tienda, sku_externo, titulo, url, imagen_url, ean,
                                   producto_solotodo, ultima_revision)
                 VALUES (:tienda, :sku, :titulo, :url, :imagen, :ean,
                         :producto_solotodo, datetime('now'))
            ON CONFLICT (tienda, sku_externo) DO UPDATE SET
                    titulo            = excluded.titulo,
                    imagen_url        = COALESCE(excluded.imagen_url, productos.imagen_url),
                    ean               = COALESCE(excluded.ean, productos.ean),
                    producto_solotodo = COALESCE(excluded.producto_solotodo,
                                                 productos.producto_solotodo),
                    ultima_revision   = datetime('now')
            """,
            {
                "tienda": captura.tienda,
                "sku": captura.sku_externo,
                "titulo": captura.titulo,
                "url": captura.url,
                "imagen": captura.imagen_url,
                "ean": captura.ean,
                "producto_solotodo": captura.producto_solotodo,
            },
        )
    except sqlite3.IntegrityError:
        con.execute(
            """
            UPDATE productos
               SET tienda            = :tienda,
                   sku_externo       = :sku,
                   titulo            = :titulo,
                   imagen_url        = COALESCE(:imagen, imagen_url),
                   ean               = COALESCE(:ean, ean),
                   producto_solotodo = COALESCE(:producto_solotodo, producto_solotodo),
                   ultima_revision   = datetime('now')
             WHERE url = :url
            """,
            {
                "tienda": captura.tienda,
                "sku": captura.sku_externo,
                "titulo": captura.titulo,
                "url": captura.url,
                "imagen": captura.imagen_url,
                "ean": captura.ean,
                "producto_solotodo": captura.producto_solotodo,
            },
        )
    fila = con.execute(
        "SELECT id FROM productos WHERE (tienda = ? AND sku_externo = ?) OR url = ?",
        (captura.tienda, captura.sku_externo, captura.url),
    ).fetchone()
    return fila["id"]


def registrar_si_cambio(
    con: sqlite3.Connection, producto_id: int, captura: Captura
) -> bool:
    """Inserta en el histórico solo si algo cambió. Devuelve si insertó.

    El histórico es un registro de VARIACIONES, no de pasadas: insertar una fila
    por ejecución entierra los cambios reales bajo miles de filas idénticas.
    """
    ultima = con.execute(
        """
        SELECT precio_internet, precio_normal, precio_tarjeta, tarjeta_asociada, disponible
          FROM historico_precios
         WHERE producto_id = ?
         ORDER BY capturado_el DESC, id DESC
         LIMIT 1
        """,
        (producto_id,),
    ).fetchone()

    nuevo = (
        captura.precio_internet,
        captura.precio_normal,
        captura.precio_tarjeta,
        captura.tarjeta,
        int(captura.disponible),
    )
    if ultima is not None and tuple(ultima) == nuevo:
        return False

    con.execute(
        """
        INSERT INTO historico_precios (producto_id, precio_internet, precio_normal,
                                       precio_tarjeta, tarjeta_asociada, disponible)
             VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            producto_id,
            captura.precio_internet,
            captura.precio_normal,
            captura.precio_tarjeta,
            captura.tarjeta,
            int(captura.disponible),
        ),
    )
    return True


def minimo_historico(
    con: sqlite3.Connection, producto_id: int, ventana_dias: int
) -> int | None:
    """El precio más bajo vigente en los últimos `ventana_dias` días.

    No basta con mirar las filas dentro de la ventana: como solo se guardan
    variaciones, un precio fijado antes del inicio de la ventana y nunca
    cambiado no tiene ninguna fila dentro, y aun así es el precio vigente. Por
    eso se une la última fila anterior al corte.
    """
    ventana = f"-{int(ventana_dias)} days"
    fila = con.execute(
        """
        SELECT MIN(p) AS minimo FROM (
            SELECT precio_internet AS p
              FROM historico_precios
             WHERE producto_id = :pid
               AND capturado_el >= datetime('now', :ventana)
            UNION ALL
            SELECT p FROM (
                SELECT precio_internet AS p
                  FROM historico_precios
                 WHERE producto_id = :pid
                   AND capturado_el < datetime('now', :ventana)
                 ORDER BY capturado_el DESC, id DESC
                 LIMIT 1
            )
        )
        """,
        {"pid": producto_id, "ventana": ventana},
    ).fetchone()
    return fila["minimo"]


def dias_de_historico(con: sqlite3.Connection, producto_id: int) -> float:
    """Cuántos días hace que se vigila este producto. Cero si no hay histórico."""
    fila = con.execute(
        """
        SELECT (julianday('now') - julianday(MIN(capturado_el))) AS dias
          FROM historico_precios
         WHERE producto_id = ?
        """,
        (producto_id,),
    ).fetchone()
    return float(fila["dias"]) if fila["dias"] is not None else 0.0


def ultima_alerta(con: sqlite3.Connection, producto_id: int, regla: str):
    """La última alerta enviada para ese producto y esa regla, si la hubo."""
    return con.execute(
        """
        SELECT precio_notificado,
               (julianday('now') - julianday(enviado_el)) AS dias_desde
          FROM registro_alertas
         WHERE producto_id = ? AND regla = ?
         ORDER BY enviado_el DESC, id DESC
         LIMIT 1
        """,
        (producto_id, regla),
    ).fetchone()


def ultima_alerta_de_articulo(
    con: sqlite3.Connection, producto_solotodo: int, regla: str
):
    """La última alerta de CUALQUIER fila del mismo artículo.

    El mismo iPhone vigilado en tres tiendas son tres filas de `productos` y un
    solo artículo. Si el descanso entre avisos se lleva por fila, las tres
    encuentran la misma oferta ajena y te avisan tres veces del mismo hallazgo.

    Se consulta por `producto_solotodo` y no por `producto_id`, así que el
    descanso es del artículo: avisa la primera fila a la que le toque turno y
    las demás callan. Vale igual dentro de un ciclo y entre ciclos, porque cada
    escritura se confirma al instante.
    """
    return con.execute(
        """
        SELECT a.precio_notificado,
               (julianday('now') - julianday(a.enviado_el)) AS dias_desde
          FROM registro_alertas a
          JOIN productos p ON p.id = a.producto_id
         WHERE p.producto_solotodo = ? AND a.regla = ?
         ORDER BY a.enviado_el DESC, a.id DESC
         LIMIT 1
        """,
        (producto_solotodo, regla),
    ).fetchone()


def registrar_alerta(
    con: sqlite3.Connection, producto_id: int, regla: str, precio: int
) -> None:
    con.execute(
        """
        INSERT INTO registro_alertas (producto_id, regla, precio_notificado)
             VALUES (?, ?, ?)
        """,
        (producto_id, regla, precio),
    )


def productos_a_comparar(
    con: sqlite3.Connection, desde: str, limite: int
) -> list[ProductoVigilado]:
    """Los productos revisados en este ciclo, el menos comparado primero.

    `desde` es la marca de tiempo con la que arrancó el ciclo: comparar un
    precio nuestro de hace días contra uno ajeno de hoy produce avisos falsos
    en las dos direcciones.

    El orden deja primero los que nunca se compararon: SQLite ordena los nulos
    antes que cualquier valor en ASC, y eso es exactamente lo que queremos. No
    lo "arregles" con NULLS LAST, o un producto nuevo no se comparará jamás.

    Puede devolver menos de `limite`: los productos sin histórico se descartan
    después del corte, porque no hay precio nuestro contra el que comparar.
    """
    if limite <= 0:
        return []

    filas = con.execute(
        """
        SELECT p.id, p.tienda, p.sku_externo, p.titulo, p.url, p.producto_solotodo,
               (SELECT h.precio_internet
                  FROM historico_precios h
                 WHERE h.producto_id = p.id
                 ORDER BY h.capturado_el DESC, h.id DESC
                 LIMIT 1) AS precio_internet
          FROM productos p
         WHERE p.ultima_revision >= ?
         ORDER BY p.ultima_comparacion ASC, p.id ASC
         LIMIT ?
        """,
        (desde, limite),
    ).fetchall()

    return [
        ProductoVigilado(
            id=fila["id"],
            tienda=fila["tienda"],
            sku_externo=fila["sku_externo"],
            titulo=fila["titulo"],
            url=fila["url"],
            precio_internet=fila["precio_internet"],
            producto_solotodo=fila["producto_solotodo"],
        )
        for fila in filas
        if fila["precio_internet"] is not None
    ]


def guardar_identidad(
    con: sqlite3.Connection, producto_id: int, producto_solotodo: int
) -> None:
    """Fija el identificador del agregador. Se resuelve una vez y no cambia."""
    con.execute(
        "UPDATE productos SET producto_solotodo = ? WHERE id = ?",
        (producto_solotodo, producto_id),
    )


def marcar_comparado(con: sqlite3.Connection, producto_id: int) -> None:
    """Anota que a este producto ya le tocó, para que el turno rote."""
    con.execute(
        "UPDATE productos SET ultima_comparacion = datetime('now') WHERE id = ?",
        (producto_id,),
    )
