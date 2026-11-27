"""
engine/signal_engine.py
=======================
El CEREBRO: convierte la confluencia técnica en un SCORE de 0 a 100.

Cómo funciona
-------------
1. Cada FACTOR (BOS, RSI, VWAP, FVG...) devuelve, vela a vela, dos valores entre
   0 y 1: cuánto apoya un LONG y cuánto apoya un SHORT.
2. La estrategia elegida asigna un PESO a cada factor.
3. Score = suma ponderada (pesos normalizados a 100 entre los factores disponibles).
4. El filtro obligatorio de la estrategia (gate) puede limitar el score a 69.
5. Clasificación:
       score <  70  -> NO OPERAR
       70 - 85      -> SETUP VÁLIDO (esperar confirmación)
       score >= 85  -> ALTA PROBABILIDAD (genera alerta)

Todo está vectorizado: el mismo cálculo sirve para la última vela (en vivo) y para
todas las velas históricas (backtest y marcadores en el gráfico).

Para añadir un factor nuevo: escribe una función `_f_mi_factor(df) -> (long, short)`,
una `_d_mi_factor(row, d) -> str` y regístralo en FACTORS. Luego dale peso en una estrategia.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from engine.config import SETTINGS, get_logger
from strategies.base import BaseStrategy

log = get_logger("signal_engine")

FactorFn = Callable[[pd.DataFrame], tuple[pd.Series, pd.Series]]
DescribeFn = Callable[[pd.Series, int], str]


# =============================================================================
# Helpers
# =============================================================================
def _clip01(x: pd.Series | np.ndarray) -> pd.Series:
    """Limita a [0, 1] y convierte NaN en 0 (un dato ausente no suma puntos)."""
    return pd.Series(x).clip(0.0, 1.0).fillna(0.0)


def _select(df: pd.DataFrame, conditions: list, values: list[float], default: float = 0.0) -> pd.Series:
    """np.select devolviendo una Serie con el índice del DataFrame."""
    conds = [np.asarray(pd.Series(c).fillna(False), dtype=bool) for c in conditions]
    return pd.Series(np.select(conds, values, default=default), index=df.index, dtype=float)


def _recent(flag: pd.Series, bars: int) -> pd.Series:
    """True si el flag ocurrió en alguna de las últimas `bars` velas."""
    return flag.fillna(False).astype(float).rolling(bars, min_periods=1).max().astype(bool)


def _v(row: pd.Series, col: str) -> float:
    """Lee un número de una fila (NaN si falta)."""
    try:
        return float(row[col])
    except (KeyError, TypeError, ValueError):
        return float("nan")


def _side(d: int, long_txt: str, short_txt: str) -> str:
    return long_txt if d == 1 else short_txt


# =============================================================================
# FACTORES: cálculo vectorizado (long, short) en [0, 1]
# =============================================================================
# --- 1. Estructura de mercado (SMC) ------------------------------------------
def _f_structure(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """BOS/CHoCH a favor: 0.5 por tendencia estructural + hasta 0.5 por ruptura reciente."""
    recency = (1.0 - df["bars_since_break"] / 40.0).clip(0, 1).fillna(0.0)
    st = df["structure_trend"]
    return _clip01((st == 1) * (0.5 + 0.5 * recency)), _clip01((st == -1) * (0.5 + 0.5 * recency))


def _d_structure(row: pd.Series, d: int) -> str:
    if _v(row, "structure_trend") != d:
        return _side(d, "Estructura bajista (en contra)", "Estructura alcista (en contra)")
    kind = "CHoCH" if _v(row, "last_break_kind") == 2 else "BOS"
    bars = _v(row, "bars_since_break")
    when = f" (hace {int(bars)} velas)" if bars == bars else ""
    return f"{kind} {_side(d, 'alcista', 'bajista')}{when}"


def _zone_factor(df: pd.DataFrame, prefix: str) -> tuple[pd.Series, pd.Series]:
    """Dentro de la zona = 1.0; cerca (<1.5 ATR) = hasta 0.7."""
    out = []
    for side in ("bull", "bear"):
        inside = df[f"in_{side}_{prefix}"].fillna(False).astype(bool)
        near = (1.0 - df[f"dist_{side}_{prefix}"] / 1.5).clip(0, 1).fillna(0.0) * 0.7
        out.append(_clip01(np.where(inside, 1.0, near)))
    return out[0].set_axis(df.index), out[1].set_axis(df.index)


def _f_order_block(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    return _zone_factor(df, "ob")


def _d_order_block(row: pd.Series, d: int) -> str:
    side = "bull" if d == 1 else "bear"
    name = _side(d, "Order Block alcista", "Order Block bajista")
    if bool(row.get(f"in_{side}_ob", False)):
        return f"Precio en {name}"
    dist = _v(row, f"dist_{side}_ob")
    return f"{name} a {dist:.1f} ATR" if dist == dist else f"Sin {name} cercano"


def _f_fvg(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    return _zone_factor(df, "fvg")


def _d_fvg(row: pd.Series, d: int) -> str:
    side = "bull" if d == 1 else "bear"
    name = _side(d, "FVG alcista", "FVG bajista")
    if bool(row.get(f"in_{side}_fvg", False)):
        return f"Precio en {name}"
    dist = _v(row, f"dist_{side}_fvg")
    return f"{name} a {dist:.1f} ATR" if dist == dist else f"Sin {name} cercano"


def _f_support_resistance(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Cerca de soporte (long) / resistencia (short), penalizando si no hay recorrido."""
    near_sup = (1.0 - df["dist_support"] / 1.0).clip(0, 1).fillna(0.0)
    near_res = (1.0 - df["dist_resistance"] / 1.0).clip(0, 1).fillna(0.0)
    room_up = (df["dist_resistance"] / 1.5).clip(0.3, 1.0).fillna(1.0)
    room_down = (df["dist_support"] / 1.5).clip(0.3, 1.0).fillna(1.0)
    return _clip01(near_sup * room_up), _clip01(near_res * room_down)


def _d_support_resistance(row: pd.Series, d: int) -> str:
    if d == 1:
        lvl, dist = _v(row, "support"), _v(row, "dist_support")
        return f"Soporte {lvl:,.2f} a {dist:.1f} ATR" if lvl == lvl else "Sin soporte cercano"
    lvl, dist = _v(row, "resistance"), _v(row, "dist_resistance")
    return f"Resistencia {lvl:,.2f} a {dist:.1f} ATR" if lvl == lvl else "Sin resistencia cercana"


# --- 2. Tendencia --------------------------------------------------------------
def _f_ema_trend(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Alineación precio / EMA 50 / EMA 200 (retroceso dentro de tendencia = 0.7)."""
    c, e50 = df["close"], df["ema_50"]
    e200 = df["ema_200"].fillna(e50)
    long = _select(df, [(c > e50) & (e50 > e200), (e50 > e200) & (c > e200), c > e200, c > e50], [1.0, 0.7, 0.4, 0.2])
    short = _select(df, [(c < e50) & (e50 < e200), (e50 < e200) & (c < e200), c < e200, c < e50], [1.0, 0.7, 0.4, 0.2])
    return long, short


def _d_ema_trend(row: pd.Series, d: int) -> str:
    c, e50, e200 = _v(row, "close"), _v(row, "ema_50"), _v(row, "ema_200")
    if d == 1:
        if c > e50 > e200:
            return "Precio > EMA50 > EMA200"
        if e50 > e200 and c > e200:
            return "Retroceso en tendencia alcista (EMA50 > EMA200)"
        return "Precio sobre EMA200" if c > e200 else "Precio bajo EMA200"
    if c < e50 < e200:
        return "Precio < EMA50 < EMA200"
    if e50 < e200 and c < e200:
        return "Retroceso en tendencia bajista (EMA50 < EMA200)"
    return "Precio bajo EMA200" if c < e200 else "Precio sobre EMA200"


def _f_ema_fast(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """EMA 9 vs EMA 21 (scalping)."""
    c, e9, e21 = df["close"], df["ema_9"], df["ema_21"]
    long = _select(df, [(e9 > e21) & (c > e9), e9 > e21], [1.0, 0.6])
    short = _select(df, [(e9 < e21) & (c < e9), e9 < e21], [1.0, 0.6])
    return long, short


def _d_ema_fast(row: pd.Series, d: int) -> str:
    up = _v(row, "ema_9") > _v(row, "ema_21")
    return "EMA9 > EMA21" if up else "EMA9 < EMA21"


def _f_adx(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Fuerza de tendencia (ADX 15 -> 0, ADX 35 -> 1) en la dirección de los DI."""
    strength = ((df["adx"] - 15.0) / 20.0).clip(0, 1).fillna(0.0)
    return _clip01(strength * (df["plus_di"] > df["minus_di"])), _clip01(strength * (df["minus_di"] > df["plus_di"]))


def _d_adx(row: pd.Series, d: int) -> str:
    adx = _v(row, "adx")
    di = "+DI > -DI" if _v(row, "plus_di") > _v(row, "minus_di") else "-DI > +DI"
    level = "fuerte" if adx >= 25 else ("moderada" if adx >= 20 else "débil")
    return f"ADX {adx:.0f} (tendencia {level}, {di})"


def _f_supertrend(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    st = df["supertrend_dir"]
    return _clip01((st == 1).astype(float)), _clip01((st == -1).astype(float))


def _d_supertrend(row: pd.Series, d: int) -> str:
    return "SuperTrend alcista" if _v(row, "supertrend_dir") == 1 else "SuperTrend bajista"


# --- 3. Momentum -----------------------------------------------------------------
def _f_rsi(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """
    RSI como zona de entrada: para LONG puntúa más cuanto más bajo (30 = 1.0, 70 = 0)
    y suma 0.15 si está girando al alza. Simétrico para SHORT.
    """
    r = df["rsi"]
    rising, falling = r.diff() > 0, r.diff() < 0
    long = ((70.0 - r) / 40.0).clip(0, 1) + 0.15 * rising
    short = ((r - 30.0) / 40.0).clip(0, 1) + 0.15 * falling
    return _clip01(long.where(r <= 70, 0.0)), _clip01(short.where(r >= 30, 0.0))


def _d_rsi(row: pd.Series, d: int) -> str:
    r = _v(row, "rsi")
    tag = " (sobreventa)" if r < 30 else (" (sobrecompra)" if r > 70 else "")
    return f"RSI en {r:.0f}{tag}"


def _f_macd(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Histograma positivo (0.5) + creciente (0.3) + MACD sobre cero (0.2)."""
    h, m = df["macd_hist"], df["macd"]
    long = 0.5 * (h > 0) + 0.3 * (h > h.shift(1)) + 0.2 * (m > 0)
    short = 0.5 * (h < 0) + 0.3 * (h < h.shift(1)) + 0.2 * (m < 0)
    return _clip01(long), _clip01(short)


def _d_macd(row: pd.Series, d: int) -> str:
    h, m = _v(row, "macd_hist"), _v(row, "macd")
    if d == 1:
        if h > 0:
            return "MACD alcista (histograma > 0)"
        return "MACD perdiendo fuerza bajista" if m < 0 else "MACD sobre cero"
    if h < 0:
        return "MACD bajista (histograma < 0)"
    return "MACD perdiendo fuerza alcista" if m > 0 else "MACD bajo cero"


def _f_stoch_rsi(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Cruce de K sobre D desde sobreventa = 1.0 (simétrico para short)."""
    k, d = df["stochrsi_k"], df["stochrsi_d"]
    cross_up = _recent((k > d) & (k.shift(1) <= d.shift(1)), 2)
    cross_dn = _recent((k < d) & (k.shift(1) >= d.shift(1)), 2)
    long = _select(df, [cross_up & (k < 35), k < 20, (k > d) & (k < 50), k > d], [1.0, 0.7, 0.5, 0.3])
    short = _select(df, [cross_dn & (k > 65), k > 80, (k < d) & (k > 50), k < d], [1.0, 0.7, 0.5, 0.3])
    return long, short


def _d_stoch_rsi(row: pd.Series, d: int) -> str:
    k, dd = _v(row, "stochrsi_k"), _v(row, "stochrsi_d")
    if d == 1:
        state = "sobreventa" if k < 20 else ("girando al alza" if k > dd else "a la baja")
    else:
        state = "sobrecompra" if k > 80 else ("girando a la baja" if k < dd else "al alza")
    return f"Stoch RSI {k:.0f} ({state})"


# --- 4. Volatilidad / volumen ------------------------------------------------------
def _f_bollinger(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """%B: banda inferior favorece long (1.0), superior favorece short (1.0)."""
    pb = df["bb_pctb"]
    return _clip01((0.5 - pb) / 0.5), _clip01((pb - 0.5) / 0.5)


def _d_bollinger(row: pd.Series, d: int) -> str:
    pb = _v(row, "bb_pctb")
    zone = "banda inferior" if pb < 0.2 else ("banda superior" if pb > 0.8 else "zona media")
    return f"Bollinger %B {pb:.2f} ({zone})"


def _f_vwap(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Sobre VWAP favorece long; si está muy extendido (>3 ATR) solo 0.5."""
    dist = (df["close"] - df["vwap"]) / df["atr"]
    long = np.where(dist > 0, np.where(dist > 3, 0.5, 1.0), 0.0)
    short = np.where(dist < 0, np.where(dist < -3, 0.5, 1.0), 0.0)
    return _clip01(long).set_axis(df.index), _clip01(short).set_axis(df.index)


def _d_vwap(row: pd.Series, d: int) -> str:
    above = _v(row, "close") > _v(row, "vwap")
    return f"Precio {'sobre' if above else 'bajo'} VWAP ({_v(row, 'vwap'):,.2f})"


def _f_volume(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Volumen relativo alto (2x = 1.0) confirmando la dirección de la vela."""
    strength = (df["rvol"] - 1.0).clip(0, 1).fillna(0.0)
    return _clip01(strength * (df["close"] > df["open"])), _clip01(strength * (df["close"] < df["open"]))


def _d_volume(row: pd.Series, d: int) -> str:
    rv = _v(row, "rvol")
    return f"Volumen {'alto' if rv >= 1.3 else 'normal' if rv >= 0.8 else 'bajo'} ({rv:.1f}x)"


def _f_volume_profile(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Rebote en VAL / aceptación sobre VAH / sobre POC favorecen long (simétrico)."""
    c, a = df["close"], df["atr"]
    poc, vah, val = df["vp_poc"], df["vp_vah"], df["vp_val"]
    near_val, near_vah = (c - val).abs() <= 0.5 * a, (c - vah).abs() <= 0.5 * a
    long = _select(df, [near_val, c > vah, c > poc], [0.9, 0.7, 0.6], 0.1)
    short = _select(df, [near_vah, c < val, c < poc], [0.9, 0.7, 0.6], 0.1)
    return long, short


def _d_volume_profile(row: pd.Series, d: int) -> str:
    c, a = _v(row, "close"), _v(row, "atr")
    poc, vah, val = _v(row, "vp_poc"), _v(row, "vp_vah"), _v(row, "vp_val")
    if abs(c - val) <= 0.5 * a:
        return f"En VAL {val:,.2f} (borde inferior del valor)"
    if abs(c - vah) <= 0.5 * a:
        return f"En VAH {vah:,.2f} (borde superior del valor)"
    return f"Precio {'sobre' if c > poc else 'bajo'} el POC {poc:,.2f}"


def _f_open_interest(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """OI sube + precio sube = dinero nuevo long (1.0). OI baja + precio sube = cierre de shorts (0.5)."""
    oi = df["oi_change_pct"]
    px = (df["close"] / df["close"].shift(5) - 1.0) * 100.0
    long = _select(df, [(oi > 0.5) & (px > 0), (oi < 0) & (px > 0)], [1.0, 0.5])
    short = _select(df, [(oi > 0.5) & (px < 0), (oi < 0) & (px < 0)], [1.0, 0.5])
    return long, short


def _d_open_interest(row: pd.Series, d: int) -> str:
    oi = _v(row, "oi_change_pct")
    if oi > 0.5:
        meaning = "entra dinero nuevo"
    elif oi < 0:
        meaning = "cierre de shorts" if d == 1 else "liquidación de longs"
    else:
        meaning = "sin cambios relevantes"
    return f"Open Interest {oi:+.1f}% ({meaning})"


# --- 5. Patrones -------------------------------------------------------------------
def _f_divergence(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Divergencia RSI (0.7) y/o MACD (0.5) en las últimas 5 velas."""
    long = 0.7 * _recent(df["bull_div_rsi"], 5) + 0.5 * _recent(df["bull_div_macd"], 5)
    short = 0.7 * _recent(df["bear_div_rsi"], 5) + 0.5 * _recent(df["bear_div_macd"], 5)
    return _clip01(long), _clip01(short)


def _d_divergence(row: pd.Series, d: int) -> str:
    return _side(d, "Divergencia alcista (RSI/MACD)", "Divergencia bajista (RSI/MACD)")


def _f_candles(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Envolvente (1.0) / martillo o estrella fugaz (0.8) en 2 velas; doji = 0.3 (indecisión)."""
    long = _select(df, [_recent(df["bull_engulfing"], 2), _recent(df["hammer"], 2), df["doji"]], [1.0, 0.8, 0.3])
    short = _select(df, [_recent(df["bear_engulfing"], 2), _recent(df["shooting_star"], 2), df["doji"]], [1.0, 0.8, 0.3])
    return long, short


def _d_candles(row: pd.Series, d: int) -> str:
    if d == 1:
        if row.get("bull_engulfing", False):
            return "Vela envolvente alcista"
        if row.get("hammer", False):
            return "Martillo"
    else:
        if row.get("bear_engulfing", False):
            return "Vela envolvente bajista"
        if row.get("shooting_star", False):
            return "Estrella fugaz"
    return "Doji (indecisión)" if row.get("doji", False) else "Patrón de vela reciente"


# =============================================================================
# Registro de factores
# =============================================================================
@dataclass(frozen=True)
class Factor:
    """Definición de un factor de confluencia."""

    key: str
    label: str
    category: str
    required: tuple[str, ...]
    compute: FactorFn
    describe: DescribeFn
    needs_volume: bool = False


FACTORS: dict[str, Factor] = {f.key: f for f in (
    Factor("structure", "Estructura BOS/CHoCH", "Estructura", ("structure_trend", "bars_since_break"), _f_structure, _d_structure),
    Factor("order_block", "Order Block", "Estructura", ("in_bull_ob", "dist_bull_ob"), _f_order_block, _d_order_block),
    Factor("fvg", "Fair Value Gap", "Estructura", ("in_bull_fvg", "dist_bull_fvg"), _f_fvg, _d_fvg),
    Factor("support_resistance", "Soporte/Resistencia", "Estructura", ("dist_support", "dist_resistance"),
           _f_support_resistance, _d_support_resistance),
    Factor("ema_trend", "EMA 50/200", "Tendencia", ("ema_50", "ema_200"), _f_ema_trend, _d_ema_trend),
    Factor("ema_fast", "EMA 9/21", "Tendencia", ("ema_9", "ema_21"), _f_ema_fast, _d_ema_fast),
    Factor("adx", "ADX (14)", "Tendencia", ("adx", "plus_di", "minus_di"), _f_adx, _d_adx),
    Factor("supertrend", "SuperTrend", "Tendencia", ("supertrend_dir",), _f_supertrend, _d_supertrend),
    Factor("rsi", "RSI (14)", "Momentum", ("rsi",), _f_rsi, _d_rsi),
    Factor("macd", "MACD", "Momentum", ("macd", "macd_hist"), _f_macd, _d_macd),
    Factor("stoch_rsi", "Stochastic RSI", "Momentum", ("stochrsi_k", "stochrsi_d"), _f_stoch_rsi, _d_stoch_rsi),
    Factor("bollinger", "Bollinger Bands", "Volatilidad/Volumen", ("bb_pctb",), _f_bollinger, _d_bollinger),
    Factor("vwap", "VWAP", "Volatilidad/Volumen", ("vwap", "atr"), _f_vwap, _d_vwap, needs_volume=True),
    Factor("volume", "Volumen relativo", "Volatilidad/Volumen", ("rvol",), _f_volume, _d_volume, needs_volume=True),
    Factor("volume_profile", "Volume Profile", "Volatilidad/Volumen", ("vp_poc", "vp_vah", "vp_val"),
           _f_volume_profile, _d_volume_profile, needs_volume=True),
    Factor("open_interest", "Open Interest", "Volatilidad/Volumen", ("oi_change_pct",), _f_open_interest, _d_open_interest),
    Factor("divergence", "Divergencias RSI/MACD", "Patrones", ("bull_div_rsi", "bull_div_macd"), _f_divergence, _d_divergence),
    Factor("candles", "Patrones de velas", "Patrones", ("bull_engulfing", "doji"), _f_candles, _d_candles),
)}


# Texto cuando un factor de "evento" no está presente (evita razones engañosas)
NO_SIGNAL_TEXT: dict[str, str] = {
    "divergence": "Sin divergencia reciente a favor",
    "candles": "Sin patrón de vela a favor",
    "open_interest": "El Open Interest no confirma",
    "volume": "Sin volumen de confirmación",
}


# =============================================================================
# Resultado
# =============================================================================
@dataclass
class FactorScore:
    """Contribución de un factor al score de la dirección elegida."""

    key: str
    label: str
    category: str
    weight: float  # Peso normalizado (los disponibles suman 100)
    value: float  # 0..1
    points: float  # weight * value
    reason: str
    available: bool = True


def grade_for(score: float, valid: float = SETTINGS.score_valid, alert: float = SETTINGS.score_alert) -> tuple[str, str]:
    """Traduce un score a (texto, código)."""
    if score >= alert:
        return "ALTA PROBABILIDAD", "alert"
    if score >= valid:
        return "SETUP VÁLIDO · ESPERAR CONFIRMACIÓN", "valid"
    return "NO OPERAR", "none"


@dataclass
class SignalResult:
    """Señal de la ÚLTIMA vela, lista para mostrar en la UI / API."""

    timestamp: pd.Timestamp
    direction: str  # LONG | SHORT | NEUTRAL
    dir_int: int  # 1 | -1 | 0
    score: float  # Score final (técnico + contexto, con filtros)
    technical_score: float  # Score técnico tras el filtro de la estrategia
    raw_score: float  # Suma ponderada pura (antes de filtros)
    opposite_score: float  # Score de la dirección contraria
    grade: str
    grade_code: str  # none | valid | alert | blocked
    reasons: list[str]
    factors: list[FactorScore]
    gate_ok: bool
    gate_message: str
    strategy_key: str
    strategy_name: str
    context_adjust: float = 0.0
    context_note: str = ""
    blocked_by: list[str] = field(default_factory=list)

    @property
    def reason_text(self) -> str:
        """Ej.: 'BOS alcista + RSI en 38 + FVG alcista + Volumen alto (1.8x)'."""
        return " + ".join(self.reasons) if self.reasons else "Sin confluencia suficiente"

    @property
    def actionable(self) -> bool:
        """True si la señal es operable (>= 70 y no bloqueada)."""
        return self.grade_code in ("valid", "alert")

    def apply_context(self, points: float, note: str, valid: float = SETTINGS.score_valid,
                      alert: float = SETTINGS.score_alert) -> None:
        """Suma/resta los puntos del análisis de contexto y recalcula la clasificación."""
        if self.dir_int == 0:
            return
        self.context_adjust = float(points)
        self.context_note = note
        score = float(np.clip(self.technical_score + points, 0.0, 100.0))
        if not self.gate_ok:  # El contexto nunca salta el filtro obligatorio
            score = min(score, valid - 1.0)
        self.score = round(score, 1)
        if not self.blocked_by:
            self.grade, self.grade_code = grade_for(self.score, valid, alert)

    def block(self, reason: str) -> None:
        """Bloquea la señal (noticias, kill switch...) manteniendo el score informativo."""
        self.blocked_by.append(reason)
        self.grade, self.grade_code = "BLOQUEADO", "blocked"

    def to_dict(self) -> dict:
        """Versión serializable (JSON) para la API."""
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat() if self.timestamp is not None else None
        data["reason_text"] = self.reason_text
        return data


# =============================================================================
# Motor
# =============================================================================
class SignalEngine:
    """Calcula los scores de confluencia según la estrategia."""

    def __init__(self, score_valid: float = SETTINGS.score_valid, score_alert: float = SETTINGS.score_alert) -> None:
        self.score_valid = score_valid
        self.score_alert = score_alert

    # ---------------------------------------------------------- utilidades
    @staticmethod
    def is_available(df: pd.DataFrame, factor: Factor) -> bool:
        """Un factor está disponible si existen sus columnas con datos (y volumen si lo necesita)."""
        if any(col not in df.columns for col in factor.required):
            return False
        tail = df[list(factor.required)].tail(50)
        if tail.isna().mean().max() > 0.5:
            return False
        if factor.needs_volume and float(df["volume"].tail(50).sum()) <= 0:
            return False
        return True

    def normalized_weights(self, df: pd.DataFrame, strategy: BaseStrategy) -> dict[str, float]:
        """Pesos de la estrategia normalizados a 100 entre los factores disponibles."""
        usable = {}
        for key, w in strategy.weights.items():
            factor = FACTORS.get(key)
            if factor is None:
                log.warning("Factor desconocido '%s' en la estrategia %s (se ignora)", key, strategy.key)
                continue
            if w > 0 and self.is_available(df, factor):
                usable[key] = float(w)
        total = sum(usable.values())
        return {k: v / total * 100.0 for k, v in usable.items()} if total > 0 else {}

    # -------------------------------------------------------------- scoring
    def score_frame(self, df: pd.DataFrame, strategy: BaseStrategy) -> pd.DataFrame:
        """
        Scores de TODAS las velas. Añade al DataFrame:
            score_long, score_short          -> suma ponderada pura
            gate_long, gate_short            -> filtro de la estrategia
            final_long, final_short          -> con filtro aplicado (máx. 69 si no pasa)
            signal_dir (1/-1/0), signal_score
        """
        weights = self.normalized_weights(df, strategy)
        score_long = pd.Series(0.0, index=df.index)
        score_short = pd.Series(0.0, index=df.index)
        for key, w in weights.items():
            try:
                lg, sh = FACTORS[key].compute(df)
                score_long += w * lg.reindex(df.index).fillna(0.0)
                score_short += w * sh.reindex(df.index).fillna(0.0)
            except Exception as exc:  # noqa: BLE001 - un factor roto no tumba el motor
                log.error("Factor %s falló: %s", key, exc)

        try:
            gate_long, gate_short = strategy.gate(df)
            gate_long = gate_long.reindex(df.index).fillna(False).astype(bool)
            gate_short = gate_short.reindex(df.index).fillna(False).astype(bool)
        except Exception as exc:  # noqa: BLE001
            log.error("Gate de %s falló: %s", strategy.key, exc)
            gate_long = gate_short = pd.Series(True, index=df.index)

        cap = self.score_valid - 1.0
        final_long = score_long.where(gate_long, np.minimum(score_long, cap))
        final_short = score_short.where(gate_short, np.minimum(score_short, cap))

        signal_dir = pd.Series(
            np.select(
                [(final_long >= self.score_valid) & (final_long > final_short),
                 (final_short >= self.score_valid) & (final_short > final_long)],
                [1, -1], default=0,
            ),
            index=df.index,
        )
        out = df.copy()
        out["score_long"] = score_long.round(2)
        out["score_short"] = score_short.round(2)
        out["gate_long"] = gate_long
        out["gate_short"] = gate_short
        out["final_long"] = final_long.round(2)
        out["final_short"] = final_short.round(2)
        out["signal_dir"] = signal_dir
        out["signal_score"] = np.maximum(final_long, final_short).round(2)
        return out

    def evaluate(self, scored: pd.DataFrame, strategy: BaseStrategy) -> SignalResult:
        """
        Construye la señal de la ÚLTIMA vela a partir de `score_frame`.
        Elige la dirección con más score, explica cada factor y clasifica.
        """
        if "final_long" not in scored.columns:
            scored = self.score_frame(scored, strategy)
        row = scored.iloc[-1]
        fl, fs = float(row["final_long"]), float(row["final_short"])
        sl, ss = float(row["score_long"]), float(row["score_short"])

        if abs(sl - ss) < 5.0 and max(fl, fs) < self.score_valid:
            dir_int, direction = 0, "NEUTRAL"
        elif fl > fs or (fl == fs and sl >= ss):
            dir_int, direction = 1, "LONG"
        else:
            dir_int, direction = -1, "SHORT"

        side = dir_int if dir_int != 0 else (1 if sl >= ss else -1)
        weights = self.normalized_weights(scored, strategy)
        factors: list[FactorScore] = []
        for key, w_raw in strategy.weights.items():
            factor = FACTORS.get(key)
            if factor is None:
                continue
            w = weights.get(key, 0.0)
            available = key in weights
            value, reason = 0.0, "No disponible para este mercado/fuente"
            if available:
                try:
                    lg, sh = factor.compute(scored)
                    value = float((lg if side == 1 else sh).iloc[-1])
                    reason = factor.describe(row, side)
                    if value < 0.05 and key in NO_SIGNAL_TEXT:  # No afirmar algo que no está pasando
                        reason = NO_SIGNAL_TEXT[key]
                except Exception as exc:  # noqa: BLE001
                    reason = f"Error: {exc}"
            factors.append(FactorScore(key, factor.label, factor.category, round(w, 2),
                                       round(value, 3), round(w * value, 2), reason, available))
        factors.sort(key=lambda f: f.points, reverse=True)
        reasons = [f.reason for f in factors if f.available and f.value >= 0.5][:5]

        gate_ok = bool(row["gate_long"] if side == 1 else row["gate_short"])
        gate_msg = "" if gate_ok else strategy.gate_reason(row, side)
        raw = sl if side == 1 else ss
        technical = fl if side == 1 else fs
        opposite = fs if side == 1 else fl
        grade, code = grade_for(technical, self.score_valid, self.score_alert) if dir_int != 0 else ("NO OPERAR", "none")

        return SignalResult(
            timestamp=scored.index[-1],
            direction=direction,
            dir_int=dir_int,
            score=round(technical, 1),
            technical_score=round(technical, 1),
            raw_score=round(raw, 1),
            opposite_score=round(opposite, 1),
            grade=grade,
            grade_code=code,
            reasons=reasons,
            factors=factors,
            gate_ok=gate_ok,
            gate_message=gate_msg,
            strategy_key=strategy.key,
            strategy_name=strategy.name,
        )
