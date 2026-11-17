"""
engine/easy_mode.py
===================
Modo fácil de FG VIEW: la operación más recomendada para tu capital, explicada
en lenguaje sencillo y respaldada por datos.

* hit_probability(): con las últimas velas de 5 min mide, para cada vela pasada,
  si el precio tocó antes el TP o el SL en los siguientes 30 minutos. Es una
  probabilidad HISTÓRICA (no una garantía), pero convierte "parece buena" en
  "pasó X de cada 100 veces".
* scan_best(): revisa varias criptos líquidas, aplica el recomendador de FG VIEW
  (filtros, riesgo, apalancamiento) y las ordena por valor esperado.
* GLOSSARY / FILTER_PLAIN: explicación sencilla de cada término técnico.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from engine.position_simulator import fmt_px, fmt_usd
from engine.recommender import (MAX_HOLD_MIN, MIN_RR_NET, FilterReport, Plan, best_direction, build_plan,
                                scalp_context)

EASY_COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "LINK", "AVAX"]
HORIZON_BARS = MAX_HOLD_MIN // 5  # 30 min en velas de 5 min


# =============================================================================
# Glosario en lenguaje sencillo
# =============================================================================
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

FILTER_PLAIN: dict[str, str] = {
    "Tendencia 1h": "La tendencia de la última hora va a favor de la operación.",
    "EMA 9 / EMA 21": "El precio de los últimos minutos empuja en la misma dirección.",
    "VWAP": "El precio está del lado bueno de su media del día (mandan {side}).",
    "RSI": "El precio no está ni demasiado caro ni demasiado barato: hay recorrido.",
    "Volumen": "Está entrando mucho dinero en este movimiento.",
    "Funding": "No hay demasiada gente apostando en la misma dirección y entra dinero nuevo.",
}


def plain_filter(name: str, direction: str) -> str:
    side = "los compradores" if direction == "LONG" else "los vendedores"
    for key, text in FILTER_PLAIN.items():
        if name.startswith(key):
            return text.format(side=side)
    return name


# =============================================================================
# Probabilidad histórica
# =============================================================================
@dataclass
class HitStats:
    samples: int
    tp_first: float  # % de veces que tocó el TP antes que el SL
    sl_first: float  # % de veces que tocó el SL antes
    neither: float  # % que no tocó ninguno en 30 min (salida por tiempo)


def similar_mask(df: pd.DataFrame, direction: str) -> np.ndarray:
    """Velas pasadas con una situación parecida: EMA 9/21, VWAP y RSI a favor de la dirección."""
    if direction == "LONG":
        m = (df["ema_9"] > df["ema_21"]) & (df["close"] > df["vwap"]) & df["rsi"].between(40, 65)
    else:
        m = (df["ema_9"] < df["ema_21"]) & (df["close"] < df["vwap"]) & df["rsi"].between(35, 60)
    return m.fillna(False).to_numpy(bool)


def hit_probability(df: pd.DataFrame, direction: str, tp_pct: float, sl_pct: float,
                    horizon: int = HORIZON_BARS, mask: np.ndarray | None = None) -> HitStats:
    """
    Recorre las velas pasadas: entrada al cierre de cada vela, y mira en las
    siguientes `horizon` velas qué toca primero. Si TP y SL caen en la misma vela,
    cuenta como SL (conservador).
    """
    c, h, lo = (df[k].to_numpy(float) for k in ("close", "high", "low"))
    n = len(df) - horizon
    tp = sl = none = 0
    d = 1 if direction == "LONG" else -1
    for i in range(max(0, n)):
        if mask is not None and not mask[i]:
            continue
        tp_px = c[i] * (1 + d * tp_pct / 100)
        sl_px = c[i] * (1 - d * sl_pct / 100)
        outcome = 0
        for j in range(i + 1, i + 1 + horizon):
            hit_sl = lo[j] <= sl_px if d == 1 else h[j] >= sl_px
            hit_tp = h[j] >= tp_px if d == 1 else lo[j] <= tp_px
            if hit_sl:
                outcome = -1
                break
            if hit_tp:
                outcome = 1
                break
        tp += outcome == 1
        sl += outcome == -1
        none += outcome == 0
    total = max(1, tp + sl + none)
    return HitStats(tp + sl + none, tp / total * 100, sl / total * 100, none / total * 100)


# =============================================================================
# La operación más recomendada
# =============================================================================
@dataclass
class Candidate:
    coin: str
    plan: Plan
    report: FilterReport
    tp_stats: list[HitStats]  # Para TP1, TP2, TP3
    similar: bool  # True = calculado solo con situaciones parecidas
    ev: float  # Valor esperado conservador en USD
    light: str  # "verde" | "amarillo" | "rojo"
    reasons: list[str] = field(default_factory=list)
    source: str = ""


def expected_value(plan: Plan, stats: list[HitStats]) -> float:
    """
    Valor esperado por operación con las probabilidades medidas:
    + P(TP1) × ganancia TP1 + P(TP2) × ganancia TP2 + P(TP3) × ganancia TP3
    − P(SL antes que TP1) × pérdida completa
    − P(ni TP ni SL en 30 min) × comisiones (salida por tiempo ≈ sin movimiento).
    """
    s1 = stats[0]
    gain = sum(st.tp_first / 100 * g for st, g in zip(stats, plan.tp_pnl))
    return gain - s1.sl_first / 100 * abs(plan.sl_pnl) - s1.neither / 100 * plan.fees


def evaluate_coin(feeder, coin: str, capital: float, market: str, contract: str = "USDT-M",
                  exchange: str | None = None, news_blocked: str | None = None,
                  daily_block: str | None = None) -> Candidate:
    """Plan + filtros + probabilidad histórica de una cripto."""
    ctx = scalp_context(feeder, f"{coin}/USDT", market, contract, exchange, limit5=1000)
    rep = best_direction(ctx.df5, ctx.df1h, coin, funding=ctx.funding, order_book=ctx.order_book,
                         news_blocked=news_blocked)
    if daily_block:
        rep.blocks.insert(0, daily_block)
    plan = build_plan(capital, coin, rep.direction, ctx.price, ctx.atr5)
    mask = similar_mask(ctx.df5, rep.direction)
    similar = int(mask[:-HORIZON_BARS].sum()) >= 30  # Mínimo 30 casos parecidos para fiarse
    stats = [hit_probability(ctx.df5, rep.direction, tp[1], plan.sl_pct, mask=mask if similar else None)
             for tp in plan.tps]
    ev = expected_value(plan, stats)
    if rep.blocks:
        light = "rojo"
    elif rep.enter and plan.rr_net >= MIN_RR_NET and ev > 0:
        light = "verde"
    else:
        light = "amarillo"
    reasons = [plain_filter(c.name, rep.direction) for c in rep.checks if c.passed]
    return Candidate(coin, plan, rep, stats, similar, ev, light, reasons, ctx.source)


def scan_best(feeder, capital: float, market: str, coins: list[str] | None = None, contract: str = "USDT-M",
              exchange: str | None = None, news_blocked: str | None = None,
              daily_block: str | None = None) -> tuple[list[Candidate], list[str]]:
    """Evalúa varias criptos y las ordena: primero las de semáforo verde, luego por valor esperado."""
    out, errors = [], []
    for coin in coins or EASY_COINS:
        try:
            out.append(evaluate_coin(feeder, coin, capital, market, contract, exchange, news_blocked, daily_block))
        except Exception as exc:  # noqa: BLE001 - una moneda sin datos no frena al resto
            errors.append(f"{coin}: {exc}")
    rank = {"verde": 0, "amarillo": 1, "rojo": 2}
    out.sort(key=lambda c: (rank[c.light], -c.ev, -c.report.score))
    return out, errors


# =============================================================================
# Texto en lenguaje sencillo
# =============================================================================
def plain_summary(c: Candidate) -> dict:
    """Piezas de la tarjeta del modo fácil."""
    p, s1 = c.plan, c.tp_stats[0]
    up = p.direction == "LONG"
    headline = {
        "verde": f"🟢 ENTRAR: {'LONG' if up else 'SHORT'} en {p.coin}",
        "amarillo": f"🟡 ESPERAR: {p.coin} todavía no da una entrada clara",
        "rojo": f"🔴 NO OPERAR {p.coin} ahora",
    }[c.light]
    maker_saving = p.notional * (0.0005 - 0.0002) * 2
    return {
        "headline": headline,
        "what": f"Apuesta a que {p.coin} {'SUBE' if up else 'BAJA'} en los próximos {MAX_HOLD_MIN} minutos.",
        "money": f"Pon {fmt_usd(p.margin)} de tus {fmt_usd(p.capital)} con apalancamiento {p.leverage}x "
                 f"(posición de {fmt_usd(p.notional)}).",
        "entry": f"Entra en ${fmt_px(p.entry)} con orden LÍMITE (ahorras ~{fmt_usd(maker_saving)} en comisiones).",
        "win": [f"TP{i}: ${fmt_px(tp[0])} → cobras {fmt_usd(g, True)} (cierra el {tp[2]:.0f}%) · "
                f"llegó el {st.tp_first:.0f}% de las veces"
                for i, (tp, g, st) in enumerate(zip(p.tps, p.tp_pnl, c.tp_stats), 1)],
        "loss": f"Stop Loss en ${fmt_px(p.stop_loss)}: si sale mal pierdes como máximo {fmt_usd(abs(p.sl_pnl))} "
                f"({abs(p.sl_pnl) / p.capital * 100:.1f}% de tu cuenta).",
        "best": f"Si llega a los 3 objetivos ganas {fmt_usd(sum(p.tp_pnl), True)} "
                f"({sum(p.tp_pnl) / p.capital * 100:+.1f}% de tu cuenta).",
        "odds": (f"En situaciones parecidas de los últimos días ({s1.samples} casos)" if c.similar else
                 f"Con estos mismos TP/SL en los últimos días ({s1.samples} casos; pocas situaciones parecidas)")
                + f", el precio tocó el TP1 antes que el SL el {s1.tp_first:.0f}% de las veces "
                  f"(SL primero: {s1.sl_first:.0f}%, ninguno en 30 min: {s1.neither:.0f}%).",
        "ev": f"Valor esperado por operación: {fmt_usd(c.ev, True)} "
              f"({'a favor' if c.ev > 0 else 'en contra'}: con estas probabilidades, repetida muchas veces, "
              f"{'gana' if c.ev > 0 else 'pierde'} de media esto por operación).",
        "liq": f"Te liquidarían en ${fmt_px(p.liquidation)}: muy lejos de tu Stop Loss, estás protegido.",
        "steps": [
            "Abre Binance → Futuros → USDⓈ-M y busca el par " + f"{p.coin}USDT.",
            f"Arriba elige **Aislado** y apalancamiento **{p.leverage}x**.",
            f"Elige orden **Límite**, precio **{fmt_px(p.entry)}** y cantidad **{p.coins_qty:.6g} {p.coin}** "
            f"(≈ {fmt_usd(p.notional)}).",
            f"Activa **TP/SL**: Stop Loss **{fmt_px(p.stop_loss)}** y Take Profit **{fmt_px(p.tps[0][0])}**.",
            f"Pulsa **{'Comprar/Long' if up else 'Vender/Short'}**.",
            f"Cuando toque el TP1, cierra la mitad y sube el SL a **{fmt_px(p.break_even)}** (ya no puedes perder).",
            f"Si en {MAX_HOLD_MIN} minutos no llegó al TP1, cierra a mercado.",
        ],
    }
