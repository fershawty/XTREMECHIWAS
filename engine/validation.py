"""
engine/validation.py
====================
Validación histórica del recomendador FG VIEW (backtest "walk-forward").

Repite sobre velas pasadas exactamente lo que haría FG VIEW en vivo:
  1. En cada vela de 5 min cerrada evalúa los 6 filtros para LONG y SHORT.
  2. Si la mejor dirección cumple >= 4 filtros (y no hay bloqueos de mecha / fin de
     semana en Nivel C) abre una operación con el plan real (build_plan): SL por ATR,
     TPs escalados, riesgo por capital.
  3. Gestiona la posición como en la vida real: TP1 50 % + SL a break-even, TP2 30 % +
     SL a TP1, TP3 20 %, salida por tiempo a los 30 min. Si SL y TP caen en la misma
     vela, cuenta el SL (conservador).
  4. Resume win rate, profit factor, expectativa, drawdown, resultados por nivel de
     confianza, por sesión del día y qué filtros acompañan a los aciertos.

Aproximaciones (se avisan en la interfaz): el funding histórico no está disponible,
se asume neutro; no hay libro de órdenes ni noticias históricas; la entrada es al
cierre de la vela de señal.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from engine.data_feeder import DataFeeder
from engine.indicators import compute_all
from engine.recommender import (MAX_HOLD_MIN, MIN_FILTERS, Plan, build_plan, filter_effectiveness,
                                journal_metrics, tier_for)

HORIZON = MAX_HOLD_MIN // 5
WARMUP = 120  # Velas de calentamiento de indicadores
FILTER_NAMES = ("Tendencia 1h", "EMA 9/21 (5m)", "VWAP", "RSI zona sana", "Volumen > 1.5×", "Funding + OI")
SESSIONS = (("Asia (00-07 UTC)", 0, 7), ("Europa (07-13:30)", 7, 13.5), ("Nueva York (13:30-21)", 13.5, 21),
            ("Noche (21-24)", 21, 24))


@dataclass
class ValidationResult:
    coin: str
    bars: int
    days: float
    trades: pd.DataFrame
    metrics: dict
    by_confidence: pd.DataFrame
    by_session: pd.DataFrame
    filters: pd.DataFrame
    notes: list[str] = field(default_factory=list)


# =============================================================================
# Filtros vectorizados (misma lógica que recommender.evaluate_filters)
# =============================================================================
def filter_matrix(df5: pd.DataFrame, df1h: pd.DataFrame | None, direction: str) -> np.ndarray:
    """Matriz bool (n_velas × 6) con los filtros cumplidos en cada vela de 5 min."""
    d = 1 if direction == "LONG" else -1
    idx = df5.index
    if df1h is not None and "ema_50" in df1h.columns:
        t = (df1h["close"] > df1h["ema_50"]) if d == 1 else (df1h["close"] < df1h["ema_50"])
        t = t.astype(float).where(df1h["ema_50"].notna())
        t.index = t.index + pd.Timedelta(hours=1)  # La vela de 1h solo se conoce al cerrar
        f1 = (t.reindex(idx.union(t.index)).ffill().reindex(idx) > 0.5).to_numpy()
    else:
        f1 = np.zeros(len(df5), bool)
    f2 = (df5["ema_9"] > df5["ema_21"]).to_numpy() if d == 1 else (df5["ema_9"] < df5["ema_21"]).to_numpy()
    f3 = (df5["close"] > df5["vwap"]).to_numpy() if d == 1 else (df5["close"] < df5["vwap"]).to_numpy()
    lo, hi = (40, 65) if d == 1 else (35, 60)
    f4 = df5["rsi"].between(lo, hi).to_numpy()
    f5 = (df5["rvol"] > 1.5).to_numpy()
    # Funding histórico no disponible: se asume neutro; el OI sí se usa si existe
    f6 = (df5["oi_change_pct"] > 0).to_numpy() if "oi_change_pct" in df5.columns else np.ones(len(df5), bool)
    return np.column_stack([f1, f2, f3, f4, f5, f6]).astype(bool)


# =============================================================================
# Simulación de una operación con la gestión 50/30/20
# =============================================================================
def simulate_trade(plan: Plan, d: int, high: np.ndarray, low: np.ndarray, close: np.ndarray, i: int,
                   horizon: int = HORIZON) -> tuple[float, int, str]:
    """
    Devuelve (PnL neto en USDT, índice de la vela de salida, motivo).
    Etapas: 1 = antes del TP1 · 2 = TP1 cobrado (SL en break-even) · 3 = TP2 cobrado (SL en TP1).
    """
    n, entry, fees = plan.notional, plan.entry, plan.fees
    tp_px = [t[0] for t in plan.tps]
    shares = {1: 1.0, 2: 0.5, 3: 0.2}  # Fracción de la posición que sigue abierta en cada etapa
    pnl, stage, stop = 0.0, 1, plan.stop_loss
    last = min(i + horizon, len(high) - 1)
    for j in range(i + 1, last + 1):
        hit_stop = low[j] <= stop if d == 1 else high[j] >= stop
        if hit_stop:
            if stage == 1:
                return pnl + plan.sl_pnl, j, "SL"
            if stage == 2:
                return pnl, j, "Break-even tras TP1"
            return pnl + n * 0.2 * (plan.tps[0][1] / 100) - fees * 0.2, j, "TP1+TP2, resto en TP1"
        target = tp_px[stage - 1]
        if (high[j] >= target) if d == 1 else (low[j] <= target):
            pnl += plan.tp_pnl[stage - 1]
            if stage == 3:
                return pnl, j, "TP1+TP2+TP3"
            stage += 1
            stop = plan.break_even if stage == 2 else tp_px[0]
    rem = shares[stage]
    move = d * (close[last] - entry) / entry
    return pnl + n * rem * move - fees * rem, last, "Tiempo (30 min)"


def _session(ts: pd.Timestamp) -> str:
    h = ts.hour + ts.minute / 60
    for name, a, b in SESSIONS:
        if a <= h < b:
            return name
    return SESSIONS[-1][0]


# =============================================================================
# Backtest del recomendador
# =============================================================================
def validate_recommender(df5: pd.DataFrame, df1h: pd.DataFrame | None, coin: str, capital: float = 1000.0,
                         min_filters: int = MIN_FILTERS) -> ValidationResult:
    """Repite la recomendación de FG VIEW sobre df5/df1h (con indicadores calculados)."""
    n = len(df5)
    tier, _ = tier_for(coin)
    fl, fs = filter_matrix(df5, df1h, "LONG"), filter_matrix(df5, df1h, "SHORT")
    pl, ps = fl.sum(axis=1), fs.sum(axis=1)
    o, h, lo, c = (df5[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = df5["atr"].to_numpy(float)
    rvol = df5["rvol"].to_numpy(float)
    wick = np.maximum(h - np.maximum(o, c), np.minimum(o, c) - lo)
    rows, i = [], WARMUP
    while i < n - HORIZON - 1:
        d = 1 if pl[i] >= ps[i] else -1
        passes = int(max(pl[i], ps[i]))
        ts = df5.index[i]
        blocked = (atr[i] > 0 and wick[i] > 2 * atr[i]) or (tier.key == "C" and ts.weekday() >= 5 and rvol[i] < 1)
        if passes < min_filters or blocked or not np.isfinite(atr[i]):
            i += 1
            continue
        direction = "LONG" if d == 1 else "SHORT"
        plan = build_plan(capital, coin, direction, c[i], atr[i])
        pnl, exit_i, reason = simulate_trade(plan, d, h, lo, c, i)
        flags = (fl if d == 1 else fs)[i]
        rows.append({"time": ts, "direction": direction, "passes": passes, "pnl": round(pnl, 2),
                     "result": "WIN" if pnl > 0 else "LOSS", "reason": reason, "bars": exit_i - i,
                     "session": _session(ts), "filters": dict(zip(FILTER_NAMES, map(bool, flags)))})
        i = exit_i + 1  # Sin operaciones solapadas
    trades = pd.DataFrame(rows)
    days = n * 5 / 60 / 24
    notes = ["Funding histórico no disponible: se asume neutro. Sin libro de órdenes ni noticias históricas.",
             "Entrada al cierre de la vela de señal; comisiones taker 0.05 % por lado; con SL y TP en la misma vela "
             "cuenta el SL."]
    if len(trades) < 30:
        notes.append(f"Solo {len(trades)} operaciones: la muestra es pequeña. Usa más días para una conclusión fiable.")
    if trades.empty:
        return ValidationResult(coin, n, days, trades, {"trades": 0}, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), notes)

    metrics = journal_metrics(trades.to_dict("records"))
    metrics["pnl_pct"] = metrics["total"] / capital * 100
    metrics["per_day"] = len(trades) / max(days, 0.01)

    def group(col: str) -> pd.DataFrame:
        g = trades.groupby(col).agg(Operaciones=("pnl", "size"), WinRate=("pnl", lambda s: (s > 0).mean() * 100),
                                    PnlMedio=("pnl", "mean"), Total=("pnl", "sum")).reset_index()
        g["WinRate"], g["PnlMedio"], g["Total"] = g["WinRate"].round(1), g["PnlMedio"].round(2), g["Total"].round(2)
        return g

    by_conf = group("passes").rename(columns={"passes": "Filtros cumplidos"})
    by_session = group("session").rename(columns={"session": "Sesión"})
    eff = filter_effectiveness(trades.to_dict("records"))
    return ValidationResult(coin, n, days, trades, metrics, by_conf, by_session, eff, notes)


def load_history(feeder: DataFeeder, coin: str, market: str, days: int, contract: str = "USDT-M",
                 exchange: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame | None, str]:
    """Descarga `days` días de velas de 5 min y las de 1 h necesarias, con indicadores. -> (df5, df1h, fuente)."""
    bars5 = int(days * 288) + WARMUP
    d5 = feeder.get_market_data(f"{coin}/USDT", market, "5m", contract, exchange, bars5, True)
    df5 = compute_all(d5.df, intraday=True, open_interest=d5.open_interest)
    df1h = None
    try:
        d1 = feeder.get_market_data(f"{coin}/USDT", market, "1h", contract, exchange, int(days * 24) + 80, True)
        df1h = compute_all(d1.df, intraday=True)
    except Exception:  # noqa: BLE001 - sin 1 h el filtro de tendencia no se cumple nunca
        pass
    return df5, df1h, d5.source
