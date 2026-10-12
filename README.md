# Rastreador de ofertas — retail chileno

Vigila precios de tiendas chilenas, guarda el histórico de variaciones y avisa
cuando algo baja de verdad.

## Puesta en marcha

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edita `config/lista_deseos.yaml` con lo que quieras vigilar y ejecuta:

```
python runner.py
```

## Pruebas

```
python -m pytest
```

No tocan la red. Para comprobar contra las tiendas de verdad:

```
python herramientas/sonda_paris.py notebook
python herramientas/sonda_falabella.py notebook
python herramientas/sonda_solotodo.py notebook
python herramientas/sonda_comparacion.py paris 106450999
```

## Cómo está hecho

Cada tienda es una `Fuente` que devuelve `Captura`. Las que leen JSON-LD heredan
de `FuenteJsonLd`, que hace el andamiaje común —bucle, deduplicado, corte por
límite— y les deja tres ganchos: la URL de búsqueda, cómo sacar candidatos de
una página de resultados, y cómo leer una ficha.

Una búsqueda en Falabella cuesta **una** petición: su página de resultados trae
los productos dentro. A cambio, sus precios vienen sin etiquetar y se infieren
del orden, así que antes de alertar —o de escribir en el histórico— se confirman
contra la ficha. Un precio adivinado no dispara un aviso ni contamina el mínimo
histórico.

Además de las tiendas que se leen en directo, **SoloTodo** aporta anchura: es un
agregador con API pública que cubre cientos de tiendas chilenas. Sus capturas
llevan la tienda real —`abcdin`, no `solotodo`—, porque el producto está allí.
Las tiendas que ya tenemos en directo se excluyen: su `external_url` es el mismo
que el nuestro y las dos fuentes discrepan en el precio de tarjeta, así que
alternándose harían crecer el histórico una fila por ciclo.

De SoloTodo viene también el identificador del mismo artículo en todas las
tiendas, y no se puede recuperar hacia atrás si falta.

Con el identificador de SoloTodo guardado, un segundo paso del ciclo compara lo
capturado hoy contra el precio del mismo artículo en el resto del retail: una
petición por producto trae lo que cuesta en todas las tiendas que el agregador
cubre. Avisa solo cuando la diferencia es grande de las dos maneras a la vez
—al menos un 10 % y al menos 10.000 pesos—, porque un umbral solo deja pasar
ruido: el relativo avisa por 800 pesos de un mouse, y el absoluto por 10.000 de
un notebook de 900.000.

El histórico y el mínimo **no** cambian: siguen siendo por tienda. Un precio
ajeno nunca entra en el histórico, porque no lo capturamos nosotros y puede ir
rezagado; el aviso lo dice y enlaza a la tienda ajena para comprobarlo.

El histórico guarda **variaciones**, no pasadas: una fila solo cuando el precio o
el stock cambian.

No se evade la detección de robots. Si una tienda bloquea, queda registrada y el
ciclo sigue con las demás.

El diseño está en `docs/specs/` y el plan en `docs/plans/`.

