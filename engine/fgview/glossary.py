"""
engine/fgview/glossary.py
=========================
Explicación en lenguaje sencillo de cada término técnico de FG VIEW.
"""

GLOSSARY: dict[str, str] = {
    "LONG": "Apuestas a que el precio SUBE. Ganas si sube, pierdes si baja.",
    "SHORT": "Apuestas a que el precio BAJA. Ganas si baja, pierdes si sube.",
    "Apalancamiento (x)": "Multiplica tu dinero. Con 10x, $100 mueven $1.000: las ganancias y las pérdidas son 10 veces "
                          "más grandes. Más apalancamiento = más riesgo de liquidación.",
    "Margen": "El dinero tuyo que pones en la operación. Es lo máximo que puedes perder si te liquidan.",
    "Posición": "El tamaño real de la operación (margen × apalancamiento).",
    "Precio de entrada": "El precio al que abres la operación.",
    "Stop Loss (SL)": "Precio donde la operación se cierra sola para cortar la pérdida. Es tu cinturón de seguridad: "
                      "ponlo SIEMPRE.",
    "Take Profit (TP)": "Precio donde cierras para cobrar la ganancia. FG VIEW usa 3: TP1 cierra el 50 %, TP2 el 30 % "
                        "y TP3 el 20 %.",
    "Liquidación": "Precio en el que el exchange cierra tu operación y pierdes TODO el margen. FG VIEW la deja siempre "
                   "mucho más lejos que el Stop Loss.",
    "Break-even": "Precio en el que no ganas ni pierdes (cubre las comisiones). Tras el TP1 se mueve el SL ahí: "
                  "la operación ya no puede perder.",
    "ROI": "Ganancia o pérdida comparada con tu margen. +20 % de ROI con $50 de margen = +$10.",
    "% de la cuenta": "Ganancia o pérdida comparada con TODO tu capital. Es el número que de verdad importa.",
    "R:R (riesgo/beneficio)": "Cuánto puedes ganar por cada $1 que arriesgas. R:R 1:2 = arriesgas $1 para ganar $2.",
    "Win rate": "De cada 100 operaciones, cuántas ganas.",
    "Win rate de equilibrio": "El mínimo de aciertos para no perder dinero con ese R:R.",
    "Probabilidad histórica": "De todas las veces recientes en que se dio esta situación, cuántas el precio llegó al "
                              "TP antes que al SL en 30 minutos. Es estadística del pasado, no una garantía.",
    "Confianza": "Cuántos de los 6 filtros de FG VIEW se cumplen (de 0 a 100). Alta = 5 o 6, Media = 4, Baja = 3 o menos.",
    "Comisión maker / taker": "Lo que cobra el exchange. Taker (orden a mercado) ~0.05 %; maker (orden límite) ~0.02 %. "
                              "Con orden límite pagas menos y ganas más.",
    "Orden límite": "Pones tú el precio exacto de entrada y esperas a que llegue. Es más barata que la orden a mercado.",
    "Margen aislado": "Cada operación usa solo su propio margen: si sale mal, no toca el resto de tu cuenta. Úsalo siempre.",
    "ATR": "Cuánto se mueve el precio normalmente en una vela. FG VIEW lo usa para que el SL no salte por ruido.",
    "EMA": "Media del precio de las últimas velas. Si el precio está por encima, la tendencia suele ser alcista.",
    "VWAP": "El precio medio del día ponderado por volumen. Por encima suelen mandar los compradores; por debajo, los vendedores.",
    "RSI": "Mide si el precio subió o bajó demasiado rápido (0-100). Por encima de 70 está 'caro'; por debajo de 30, 'barato'.",
    "Volumen": "Cuánto dinero se está moviendo. Un movimiento con mucho volumen es más fiable.",
    "Funding": "Pago entre compradores y vendedores en los futuros. Si es muy alto, hay demasiados en una dirección (peligro de giro).",
    "Open Interest (OI)": "Cuántos contratos hay abiertos. Si sube junto al precio, está entrando dinero nuevo a favor.",
    "Spread": "Diferencia entre el precio de compra y el de venta. Si es grande, entrar y salir cuesta más.",
}
