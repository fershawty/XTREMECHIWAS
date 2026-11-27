"""
engine/indicators.py
====================
Cálculo de TODOS los indicadores técnicos del stack de confluencia.

Stack obligatorio implementado
------------------------------
1. Estructura (SMC)      -> en chart_analyzer.py (BOS, CHoCH, OB, FVG, S/R)
2. Tendencia             -> EMA 50 / 200 (+ EMA 9/21 para scalping), ADX(14), SuperTrend
3. Momentum              -> RSI(14), MACD(12,26,9), Stochastic RSI
4. Volatilidad / Volumen -> Bollinger Bands, ATR(14), Volume Profile (POC/VAH/VAL),
                            VWAP (de sesión o rodante), Volumen relativo, Open Interest
5. Patrones              -> Divergencias RSI/MACD, Engulfing, Doji, Martillo, Estrella fugaz

Motores ("backends")
--------------------
* native    : pandas/numpy puro (SIEMPRE disponible, por defecto si no hay TA-Lib)
* talib     : TA-Lib en C (más rápido) para EMA, RSI, ATR, ADX, MACD, BB y velas
* pandas_ta : pandas-ta (opcional, requiere Python 3.12+ en entorno aparte)
Si un backend opcional falla en cualquier cálculo, se usa automáticamente el nativo.

Todas las funciones son CAUSALES (no miran el futuro), salvo `find_pivots`, cuyo
resultado se confirma `right` velas después; quien lo use debe respetar ese retraso
(las divergencias ya lo hacen).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from engine.config import SETTINGS, get_logger

log = get_logger("indicators")

try:  # Acelerador opcional
    import talib  # type: ignore
except ImportError:  # pragma: no cover
    talib = None

try:  # Acelerador opcional
    import pandas_ta as pta  # type: ignore
except Exception:  # pragma: no cover - ImportError o incompatibilidades de numpy
    pta = None


def _resolve_backend(requested: str) -> str:
    """Decide qué motor usar según .env y lo que esté instalado."""
    if requested == "talib" and talib is not None:
        return "talib"
    if requested == "pandas_ta" and pta is not None:
        return "pandas_ta"
    if requested == "auto" and talib is not None:
        return "talib"
    if requested not in ("auto", "native"):
        log.warning("Backend '%s' no disponible: usando motor nativo.", requested)
    return "native"


BACKEND: str = _resolve_backend(SETTINGS.indicator_backend)


# =============================================================================
# Parámetros (cámbialos aquí o crea tu propia instancia)
# =============================================================================
@dataclass(frozen=True)
class IndicatorParams:
    """Longitudes y multiplicadores de todos los indicadores."""

    ema_fast: int = 9
    ema_mid: int = 21
    ema_trend: int = 50
    ema_long: int = 200
    adx_length: int = 14
    supertrend_length: int = 10
    supertrend_mult: float = 3.0
    rsi_length: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    stoch_rsi_length: int = 14
    stoch_length: int = 14
    stoch_k: int = 3
    stoch_d: int = 3
    bb_length: int = 20
    bb_std: float = 2.0
    atr_length: int = 14
    rvol_length: int = 20
    vwap_rolling_bars: int = 20  # VWAP rodante para velas diarias
    vp_lookback: int = 150  # Velas usadas para el Volume Profile
    vp_bins: int = 40
    vp_value_area: float = 0.70
    pivot_left: int = 3  # Pivotes para divergencias
    pivot_right: int = 3
    div_max_lookback: int = 60
    div_min_distance: int = 5
    oi_change_bars: int = 5


DEFAULT_PARAMS = IndicatorParams()


# =============================================================================
# Helpers internos
# =============================================================================
def _arr(series: pd.Series) -> np.ndarray:
    """Serie -> array float64 contiguo (formato que exige TA-Lib)."""
    return np.ascontiguousarray(series.to_numpy(dtype="float64"))


def _series(values: np.ndarray, index: pd.Index, name: str | None = None) -> pd.Series:
    """Array -> Serie con el índice original."""
    return pd.Series(np.asarray(values, dtype="float64"), index=index, name=name)


def _safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
    """División que devuelve NaN en lugar de inf cuando el divisor es 0."""
    return a / b.replace(0.0, np.nan)


# =============================================================================
# Medias y volatilidad
# =============================================================================
def sma(series: pd.Series, length: int) -> pd.Series:
    """Media móvil simple."""
    return series.rolling(length, min_periods=length).mean()


def ema(series: pd.Series, length: int) -> pd.Series:
    """Media móvil exponencial."""
    if BACKEND == "talib":
        try:
            return _series(talib.EMA(_arr(series), timeperiod=length), series.index)
        except Exception as exc:  # noqa: BLE001
            log.debug("talib.EMA falló (%s), uso nativo", exc)
    if BACKEND == "pandas_ta":
        try:
            out = pta.ema(series, length=length)
            if out is not None:
                return out.astype(float)
        except Exception:  # noqa: BLE001
            pass
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def rma(series: pd.Series, length: int) -> pd.Series:
    """Media de Wilder (RMA/SMMA): la que usan RSI, ATR y ADX."""
    return series.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    """True Range = max(high-low, |high-cierre previo|, |low-cierre previo|)."""
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Average True Range (Wilder)."""
    if BACKEND == "talib":
        try:
            return _series(talib.ATR(_arr(df["high"]), _arr(df["low"]), _arr(df["close"]), timeperiod=length), df.index)
        except Exception:  # noqa: BLE001
            pass
    if BACKEND == "pandas_ta":
        try:
            out = pta.atr(df["high"], df["low"], df["close"], length=length)
            if out is not None:
                return out.astype(float)
        except Exception:  # noqa: BLE001
            pass
    return rma(true_range(df), length)


def bollinger(close: pd.Series, length: int = 20, std: float = 2.0) -> pd.DataFrame:
    """
    Bandas de Bollinger.
    Devuelve: bb_mid, bb_upper, bb_lower, bb_width (% del precio), bb_pctb (0=banda inf, 1=banda sup).
    """
    mid = upper = lower = None
    if BACKEND == "talib":
        try:
            u, m, lo = talib.BBANDS(_arr(close), timeperiod=length, nbdevup=std, nbdevdn=std, matype=0)
            upper, mid, lower = (_series(x, close.index) for x in (u, m, lo))
        except Exception:  # noqa: BLE001
            mid = None
    if mid is None:
        mid = sma(close, length)
        dev = close.rolling(length, min_periods=length).std(ddof=0)
        upper = mid + std * dev
        lower = mid - std * dev
    width = _safe_div(upper - lower, mid) * 100.0
    pctb = _safe_div(close - lower, upper - lower)
    return pd.DataFrame(
        {"bb_mid": mid, "bb_upper": upper, "bb_lower": lower, "bb_width": width, "bb_pctb": pctb},
        index=close.index,
    )


# =============================================================================
# Tendencia
# =============================================================================
def adx(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
    """ADX con +DI y -DI (Wilder). ADX > 25 = tendencia fuerte."""
    if BACKEND == "talib":
        try:
            h, lo, c = _arr(df["high"]), _arr(df["low"]), _arr(df["close"])
            return pd.DataFrame(
                {
                    "adx": talib.ADX(h, lo, c, timeperiod=length),
                    "plus_di": talib.PLUS_DI(h, lo, c, timeperiod=length),
                    "minus_di": talib.MINUS_DI(h, lo, c, timeperiod=length),
                },
                index=df.index,
            )
        except Exception:  # noqa: BLE001
            pass
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr_smooth = rma(true_range(df), length)
    plus_di = 100.0 * _safe_div(rma(plus_dm, length), tr_smooth)
    minus_di = 100.0 * _safe_div(rma(minus_dm, length), tr_smooth)
    dx = 100.0 * _safe_div((plus_di - minus_di).abs(), plus_di + minus_di)
    return pd.DataFrame({"adx": rma(dx, length), "plus_di": plus_di, "minus_di": minus_di}, index=df.index)


def supertrend(df: pd.DataFrame, length: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """
    SuperTrend clásico.
    Devuelve: supertrend (línea) y supertrend_dir (1 alcista, -1 bajista).
    """
    atr_v = atr(df, length).to_numpy()
    high, low, close = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    hl2 = (high + low) / 2.0
    upper_basic = hl2 + multiplier * atr_v
    lower_basic = hl2 - multiplier * atr_v

    n = len(df)
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    line = np.full(n, np.nan)
    direction = np.zeros(n)

    start = int(np.argmax(~np.isnan(atr_v))) if (~np.isnan(atr_v)).any() else n
    for i in range(start, n):
        if i == start:
            final_upper[i], final_lower[i], direction[i] = upper_basic[i], lower_basic[i], 1
        else:
            # La banda solo se mueve a favor de la tendencia (efecto "trailing stop")
            final_upper[i] = (
                upper_basic[i]
                if upper_basic[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]
                else final_upper[i - 1]
            )
            final_lower[i] = (
                lower_basic[i]
                if lower_basic[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]
                else final_lower[i - 1]
            )
            if direction[i - 1] == -1 and close[i] > final_upper[i]:
                direction[i] = 1
            elif direction[i - 1] == 1 and close[i] < final_lower[i]:
                direction[i] = -1
            else:
                direction[i] = direction[i - 1]
        line[i] = final_lower[i] if direction[i] == 1 else final_upper[i]

    return pd.DataFrame({"supertrend": line, "supertrend_dir": direction}, index=df.index)


# =============================================================================
# Momentum
# =============================================================================
def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """RSI de Wilder (0-100)."""
    if BACKEND == "talib":
        try:
            return _series(talib.RSI(_arr(close), timeperiod=length), close.index)
        except Exception:  # noqa: BLE001
            pass
    if BACKEND == "pandas_ta":
        try:
            out = pta.rsi(close, length=length)
            if out is not None:
                return out.astype(float)
        except Exception:  # noqa: BLE001
            pass
    delta = close.diff()
    avg_gain = rma(delta.clip(lower=0.0), length)
    avg_loss = rma(-delta.clip(upper=0.0), length)
    rs = _safe_div(avg_gain, avg_loss)
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(avg_loss != 0, 100.0)  # Sin pérdidas -> RSI 100
    out = out.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)  # Precio plano -> 50
    return out.where(avg_gain.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD clásico: macd, macd_signal, macd_hist."""
    if BACKEND == "talib":
        try:
            m, s, h = talib.MACD(_arr(close), fastperiod=fast, slowperiod=slow, signalperiod=signal)
            return pd.DataFrame({"macd": m, "macd_signal": s, "macd_hist": h}, index=close.index)
        except Exception:  # noqa: BLE001
            pass
    fast_ema = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    slow_ema = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    line = fast_ema - slow_ema
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig}, index=close.index)


def stoch_rsi(
    close: pd.Series, rsi_length: int = 14, stoch_length: int = 14, k: int = 3, d: int = 3
) -> pd.DataFrame:
    """Stochastic RSI (0-100): stochrsi_k y stochrsi_d."""
    r = rsi(close, rsi_length)
    lowest = r.rolling(stoch_length, min_periods=stoch_length).min()
    highest = r.rolling(stoch_length, min_periods=stoch_length).max()
    raw = _safe_div(r - lowest, highest - lowest) * 100.0
    raw = raw.where(highest != lowest, 50.0).where(lowest.notna())
    k_line = sma(raw, k)
    return pd.DataFrame({"stochrsi_k": k_line, "stochrsi_d": sma(k_line, d)}, index=close.index)


# =============================================================================
# Volumen
# =============================================================================
def vwap(df: pd.DataFrame, intraday: bool = True, rolling_bars: int = 20) -> pd.Series:
    """
    VWAP (precio medio ponderado por volumen), clave en futuros.
    - Intradía: VWAP de sesión que se reinicia cada día (00:00 UTC).
    - Diario o superior: VWAP rodante de `rolling_bars` velas.
    Si no hay volumen, devuelve el precio típico (factor neutral).
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = typical * df["volume"]
    if intraday:
        session = df.index.floor("D")
        cum_pv = pv.groupby(session).cumsum()
        cum_v = df["volume"].groupby(session).cumsum()
    else:
        cum_pv = pv.rolling(rolling_bars, min_periods=1).sum()
        cum_v = df["volume"].rolling(rolling_bars, min_periods=1).sum()
    out = _safe_div(cum_pv, cum_v)
    return out.fillna(typical)


def relative_volume(volume: pd.Series, length: int = 20) -> pd.Series:
    """Volumen relativo = volumen / media de `length` velas (2.0 = el doble de lo normal)."""
    return _safe_div(volume, sma(volume, length))


def _profile_window(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray,
                    bins: int, value_area: float) -> tuple[float, float, float, np.ndarray, np.ndarray]:
    """
    Volume Profile de una ventana. Reparte el volumen de cada vela entre los niveles
    de precio que tocó (proporcional al solapamiento). Devuelve POC, VAH, VAL,
    centros de los bins y volumen por bin.
    """
    lo, hi = float(np.nanmin(low)), float(np.nanmax(high))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        p = float(close[-1])
        return p, p, p, np.array([p]), np.array([float(np.nansum(volume))])

    edges = np.linspace(lo, hi, bins + 1)
    e_lo, e_hi = edges[:-1][None, :], edges[1:][None, :]
    bar_range = (high - low)[:, None]
    overlap = np.clip(np.minimum(high[:, None], e_hi) - np.maximum(low[:, None], e_lo), 0.0, None)
    share = np.divide(overlap, bar_range, out=np.zeros_like(overlap), where=bar_range > 0)
    flat = (high - low) <= 0
    if flat.any():  # Velas sin rango: todo el volumen al bin del cierre
        idx = np.clip(np.searchsorted(edges, close[flat], side="right") - 1, 0, bins - 1)
        share[flat] = 0.0
        share[np.where(flat)[0], idx] = 1.0
    vol_bins = (share * volume[:, None]).sum(axis=0)
    centers = (edges[:-1] + edges[1:]) / 2.0

    total = vol_bins.sum()
    if total <= 0:
        mid = float(centers[len(centers) // 2])
        return mid, hi, lo, centers, vol_bins

    poc_i = int(np.argmax(vol_bins))
    lo_i = hi_i = poc_i
    acc = vol_bins[poc_i]
    # Expandimos el Value Area hacia el lado con más volumen hasta cubrir el 70%
    while acc < value_area * total and (lo_i > 0 or hi_i < bins - 1):
        down = vol_bins[lo_i - 1] if lo_i > 0 else -1.0
        up = vol_bins[hi_i + 1] if hi_i < bins - 1 else -1.0
        if up >= down:
            hi_i += 1
            acc += vol_bins[hi_i]
        else:
            lo_i -= 1
            acc += vol_bins[lo_i]
    return float(centers[poc_i]), float(edges[hi_i + 1]), float(edges[lo_i]), centers, vol_bins


def volume_profile(df: pd.DataFrame, bins: int = 40, value_area: float = 0.70) -> dict:
    """
    Volume Profile de todo el DataFrame recibido (úsalo con df.tail(N)).
    Devuelve {"poc", "vah", "val", "prices", "volumes"} para dibujar en el gráfico.
    """
    poc, vah, val, prices, volumes = _profile_window(
        df["high"].to_numpy(float), df["low"].to_numpy(float), df["close"].to_numpy(float),
        df["volume"].to_numpy(float), bins, value_area,
    )
    return {"poc": poc, "vah": vah, "val": val, "prices": prices, "volumes": volumes}


def rolling_volume_profile(
    df: pd.DataFrame, lookback: int = 150, bins: int = 40, value_area: float = 0.70, step: int | None = None
) -> pd.DataFrame:
    """
    POC / VAH / VAL calculados vela a vela con las `lookback` velas anteriores
    (causal: sirve para backtesting). `step` recalcula cada N velas para ir más rápido.
    """
    n = len(df)
    out = np.full((n, 3), np.nan)
    if n == 0:
        return pd.DataFrame(out, index=df.index, columns=["vp_poc", "vp_vah", "vp_val"])
    step = step or (1 if n <= 1500 else 5)
    lookback = min(lookback, n)
    h, lo = df["high"].to_numpy(float), df["low"].to_numpy(float)
    c, v = df["close"].to_numpy(float), df["volume"].to_numpy(float)
    start = max(lookback - 1, min(30, n - 1))
    for i in range(start, n, step):
        s = max(0, i - lookback + 1)
        poc, vah, val, _, _ = _profile_window(h[s : i + 1], lo[s : i + 1], c[s : i + 1], v[s : i + 1], bins, value_area)
        out[i] = (poc, vah, val)
    if step > 1 and np.isnan(out[n - 1, 0]):  # Garantiza el valor exacto en la última vela
        s = max(0, n - lookback)
        out[n - 1] = _profile_window(h[s:], lo[s:], c[s:], v[s:], bins, value_area)[:3]
    frame = pd.DataFrame(out, index=df.index, columns=["vp_poc", "vp_vah", "vp_val"])
    return frame.ffill()


def attach_open_interest(df: pd.DataFrame, oi: pd.Series | None, change_bars: int = 5) -> pd.DataFrame:
    """
    Alinea el Open Interest a las velas (relleno hacia adelante) y calcula su
    variación %. Si no hay OI, devuelve df sin cambios (el factor queda "no disponible").
    """
    if oi is None or len(oi) < 10:
        return df
    oi = oi.sort_index()
    oi = oi[~oi.index.duplicated(keep="last")]
    aligned = oi.reindex(df.index.union(oi.index)).ffill().reindex(df.index)
    if aligned.notna().sum() < 10:
        return df
    out = df.copy()
    out["oi"] = aligned
    out["oi_change_pct"] = (aligned / aligned.shift(change_bars) - 1.0) * 100.0
    return out


# =============================================================================
# Pivotes y divergencias
# =============================================================================
def find_pivots(series: pd.Series, left: int, right: int, kind: str = "high") -> np.ndarray:
    """
    Marca pivotes (máximos/mínimos locales) en su propia vela.
    OJO: un pivote en la vela j solo se CONOCE en la vela j + right.
    """
    window = left + right + 1
    if kind == "high":
        extreme = series.rolling(window, min_periods=window).max().shift(-right)
    else:
        extreme = series.rolling(window, min_periods=window).min().shift(-right)
    values = series.to_numpy(float)
    ext = extreme.to_numpy(float)
    return np.isfinite(ext) & (values == ext)


def detect_divergences(
    df: pd.DataFrame,
    oscillator: pd.Series,
    left: int = 3,
    right: int = 3,
    max_lookback: int = 60,
    min_distance: int = 5,
) -> tuple[pd.Series, pd.Series]:
    """
    Divergencias regulares precio vs oscilador (RSI o MACD).
    - Alcista: precio hace mínimo más bajo y el oscilador mínimo más alto.
    - Bajista: precio hace máximo más alto y el oscilador máximo más bajo.
    Se marcan en la vela de CONFIRMACIÓN (pivote + right) -> sin mirar el futuro.
    """
    n = len(df)
    bull = np.zeros(n, dtype=bool)
    bear = np.zeros(n, dtype=bool)
    low, high = df["low"].to_numpy(float), df["high"].to_numpy(float)
    osc = oscillator.to_numpy(float)

    prev = None
    for j in np.flatnonzero(find_pivots(df["low"], left, right, "low")):
        if not np.isfinite(osc[j]):
            continue
        if prev is not None and min_distance <= j - prev <= max_lookback:
            if low[j] < low[prev] and osc[j] > osc[prev] and j + right < n:
                bull[j + right] = True
        prev = j

    prev = None
    for j in np.flatnonzero(find_pivots(df["high"], left, right, "high")):
        if not np.isfinite(osc[j]):
            continue
        if prev is not None and min_distance <= j - prev <= max_lookback:
            if high[j] > high[prev] and osc[j] < osc[prev] and j + right < n:
                bear[j + right] = True
        prev = j

    return pd.Series(bull, index=df.index), pd.Series(bear, index=df.index)


# =============================================================================
# Patrones de velas
# =============================================================================
def candle_patterns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Patrones de velas: bull_engulfing, bear_engulfing, doji, hammer, shooting_star.
    (El contexto —si aparece en soporte o resistencia— lo evalúa signal_engine.)
    """
    if BACKEND == "talib":
        try:
            o, h, lo, c = (_arr(df[k]) for k in ("open", "high", "low", "close"))
            engulf = talib.CDLENGULFING(o, h, lo, c)
            return pd.DataFrame(
                {
                    "bull_engulfing": engulf > 0,
                    "bear_engulfing": engulf < 0,
                    "doji": talib.CDLDOJI(o, h, lo, c) != 0,
                    "hammer": talib.CDLHAMMER(o, h, lo, c) != 0,
                    "shooting_star": talib.CDLSHOOTINGSTAR(o, h, lo, c) != 0,
                },
                index=df.index,
            )
        except Exception:  # noqa: BLE001
            pass

    o, h, lo, c = df["open"], df["high"], df["low"], df["close"]
    body = (c - o).abs()
    rng = h - lo
    upper_wick = h - pd.concat([o, c], axis=1).max(axis=1)
    lower_wick = pd.concat([o, c], axis=1).min(axis=1) - lo
    prev_o, prev_c = o.shift(1), c.shift(1)
    prev_body = (prev_c - prev_o).abs()

    bull_engulf = (prev_c < prev_o) & (c > o) & (c >= prev_o) & (o <= prev_c) & (body > prev_body)
    bear_engulf = (prev_c > prev_o) & (c < o) & (o >= prev_c) & (c <= prev_o) & (body > prev_body)
    doji = (rng > 0) & (body <= 0.1 * rng)
    real_body = body > 0.1 * rng
    hammer = real_body & (lower_wick >= 2.0 * body) & (upper_wick <= np.maximum(0.5 * body, 0.1 * rng))
    shooting = real_body & (upper_wick >= 2.0 * body) & (lower_wick <= np.maximum(0.5 * body, 0.1 * rng))

    return pd.DataFrame(
        {
            "bull_engulfing": bull_engulf.fillna(False).astype(bool),
            "bear_engulfing": bear_engulf.fillna(False).astype(bool),
            "doji": doji.fillna(False).astype(bool),
            "hammer": hammer.fillna(False).astype(bool),
            "shooting_star": shooting.fillna(False).astype(bool),
        },
        index=df.index,
    )


# =============================================================================
# Orquestador: añade TODO al DataFrame
# =============================================================================
def compute_all(
    df: pd.DataFrame,
    params: IndicatorParams = DEFAULT_PARAMS,
    intraday: bool = True,
    open_interest: pd.Series | None = None,
) -> pd.DataFrame:
    """
    Calcula todo el stack técnico y lo devuelve como columnas nuevas.

    Args:
        df: OHLCV unificado (de data_feeder).
        params: longitudes de indicadores.
        intraday: True para VWAP de sesión; False (velas diarias) para VWAP rodante.
        open_interest: serie de OI (opcional, solo cripto).

    Returns:
        Copia de df con ~45 columnas de indicadores.
    """
    p = params
    close = df["close"]
    cols: dict[str, pd.Series] = {
        "ema_9": ema(close, p.ema_fast),
        "ema_21": ema(close, p.ema_mid),
        "ema_50": ema(close, p.ema_trend),
        "ema_200": ema(close, p.ema_long),
        "rsi": rsi(close, p.rsi_length),
        "atr": atr(df, p.atr_length),
        "rvol": relative_volume(df["volume"], p.rvol_length),
        "vwap": vwap(df, intraday=intraday, rolling_bars=p.vwap_rolling_bars),
    }
    frames = [
        pd.DataFrame(cols, index=df.index),
        adx(df, p.adx_length),
        supertrend(df, p.supertrend_length, p.supertrend_mult),
        macd(close, p.macd_fast, p.macd_slow, p.macd_signal),
        stoch_rsi(close, p.stoch_rsi_length, p.stoch_length, p.stoch_k, p.stoch_d),
        bollinger(close, p.bb_length, p.bb_std),
        rolling_volume_profile(df, p.vp_lookback, p.vp_bins, p.vp_value_area),
        candle_patterns(df),
    ]
    out = pd.concat([df] + frames, axis=1)
    out["atr_pct"] = _safe_div(out["atr"], close) * 100.0

    # Divergencias RSI y MACD (sobre el histograma)
    div_args = dict(left=p.pivot_left, right=p.pivot_right, max_lookback=p.div_max_lookback,
                    min_distance=p.div_min_distance)
    out["bull_div_rsi"], out["bear_div_rsi"] = detect_divergences(out, out["rsi"], **div_args)
    out["bull_div_macd"], out["bear_div_macd"] = detect_divergences(out, out["macd_hist"], **div_args)

    return attach_open_interest(out, open_interest, p.oi_change_bars)
