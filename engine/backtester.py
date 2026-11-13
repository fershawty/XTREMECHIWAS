"""
engine/backtester.py
====================
Backtester nativo (rápido, sin dependencias) que usa EXACTAMENTE el mismo motor
que el análisis en vivo: indicadores causales + estructura causal + scoring.

Reglas de simulación (conservadoras, para no engañarte):
* La señal se evalúa al CIERRE de la vela i y se entra en la APERTURA de la vela i+1.
* SL y TP se calculan con el risk_manager (ATR x 1.5 / Order Block, TP 1:2 y 1:3).
* Si en una misma vela se tocan SL y TP, se asume que saltó el SL primero.
* Comisiones (taker x2) y deslizamiento descontados en cada operación.
* Kill switch: tras N pérdidas seguidas, pausa `cooldown_bars` velas.
* Modos de salida: "tp1" (todo en 1:2), "tp2" (todo en 1:3) o "partial"
  (50 % en TP1, SL a break-even, 50 % en TP2).

Limitación: no hay datos históricos de noticias ni de contexto, así que el
backtest evalúa solo la parte técnica.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from engine.config import SETTINGS
from engine.risk_manager import RiskManager, RiskParams


@dataclass
class BacktestConfig:
    """Parámetros del backtest."""

    threshold: float = SETTINGS.score_valid  # Score mínimo para entrar
    exit_mode: str = "partial"  # tp1 | tp2 | partial
    max_hold_bars: int = 100  # Cierre por tiempo
    warmup_bars: int = 200  # Velas iniciales sin operar (calentamiento de la EMA 200)
    cooldown_bars: int = 24  # Pausa tras activarse el kill switch
    kill_switch_losses: int = SETTINGS.kill_switch_losses
    fee_pct: float = SETTINGS.taker_fee_pct  # Comisión por lado (%)
    slippage_pct: float = 0.02  # Deslizamiento por lado (%)
    risk: RiskParams = field(default_factory=RiskParams)


@dataclass
class Trade:
    """Operación simulada."""

    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: str
    score: float
    entry: float
    exit: float
    stop: float
    tp1: float
    tp2: float
    sl_method: str
    result_r: float  # Resultado neto en múltiplos de R
    bars_held: int
    exit_reason: str


@dataclass
class BacktestResult:
    """Resultado del backtest."""

    trades: pd.DataFrame
    equity: pd.Series
    metrics: dict
    config: BacktestConfig

    def summary_text(self) -> str:
        m = self.metrics
        return (f"{m['trades']} operaciones · win rate {m['win_rate']:.1f}% · PF {m['profit_factor']:.2f} · "
                f"{m['total_r']:+.2f}R · retorno {m['return_pct']:+.1f}% · DD máx {m['max_dd_pct']:.1f}%")


class Backtester:
    """Simulador vela a vela sobre un DataFrame ya puntuado (score_frame)."""

    def __init__(self, risk: RiskManager | None = None) -> None:
        self.risk = risk or RiskManager()

    def run(self, scored: pd.DataFrame, config: BacktestConfig | None = None) -> BacktestResult:
        """
        Args:
            scored: DataFrame con signal_dir, signal_score, atr, bull_ob_low, bear_ob_high (de SignalEngine.score_frame).
            config: parámetros de simulación.
        """
        cfg = config or BacktestConfig()
        p = cfg.risk
        n = len(scored)
        o, h, lo, c = (scored[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        atr = scored["atr"].to_numpy(float)
        sd = scored["signal_dir"].to_numpy(int)
        sc = scored["signal_score"].to_numpy(float)
        ob_long = scored["bull_ob_low"].to_numpy(float) if "bull_ob_low" in scored else np.full(n, np.nan)
        ob_short = scored["bear_ob_high"].to_numpy(float) if "bear_ob_high" in scored else np.full(n, np.nan)
        idx = scored.index

        equity = p.equity
        equity_curve: list[tuple[pd.Timestamp, float]] = [(idx[min(cfg.warmup_bars, n - 1)], equity)] if n else []
        trades: list[Trade] = []
        pos: dict | None = None
        streak, paused_until, kill_triggers = 0, -1, 0
        cost_pct = (cfg.fee_pct + cfg.slippage_pct) / 100.0

        for i in range(min(cfg.warmup_bars, n), n):
            # ---------------- Gestión de la posición abierta (vela i) ----------------
            if pos is not None:
                d, risk_u = pos["dir"], pos["risk"]
                hit_sl = lo[i] <= pos["stop"] if d == 1 else h[i] >= pos["stop"]
                hit_tp1 = h[i] >= pos["tp1"] if d == 1 else lo[i] <= pos["tp1"]
                hit_tp2 = h[i] >= pos["tp2"] if d == 1 else lo[i] <= pos["tp2"]
                exit_price, reason, r_gross = None, "", 0.0

                if hit_sl:  # Conservador: SL antes que TP en la misma vela
                    leg = (pos["stop"] - pos["entry"]) * d / risk_u
                    r_gross = pos["realized"] + pos["remaining"] * leg
                    exit_price = pos["stop"]
                    reason = "Break-even tras TP1" if pos["partial_done"] else "Stop Loss"
                elif cfg.exit_mode == "tp1" and hit_tp1:
                    r_gross, exit_price, reason = p.tp1_rr, pos["tp1"], "TP1"
                elif cfg.exit_mode == "tp2" and hit_tp2:
                    r_gross, exit_price, reason = p.tp2_rr, pos["tp2"], "TP2"
                elif cfg.exit_mode == "partial":
                    if not pos["partial_done"] and hit_tp1:
                        pos.update(partial_done=True, realized=0.5 * p.tp1_rr, remaining=0.5, stop=pos["entry"])
                    if pos["partial_done"] and hit_tp2:
                        r_gross, exit_price, reason = pos["realized"] + 0.5 * p.tp2_rr, pos["tp2"], "TP1 + TP2"

                if exit_price is None and i - pos["entry_idx"] >= cfg.max_hold_bars:
                    leg = (c[i] - pos["entry"]) * d / risk_u
                    r_gross, exit_price, reason = pos["realized"] + pos["remaining"] * leg, c[i], "Tiempo máximo"

                if exit_price is not None:
                    fee_r = 2.0 * cost_pct * pos["entry"] / risk_u
                    r_net = r_gross - fee_r
                    equity *= 1.0 + r_net * p.risk_pct / 100.0
                    trades.append(Trade(idx[pos["entry_idx"]], idx[i], "LONG" if d == 1 else "SHORT", pos["score"],
                                        pos["entry"], float(exit_price), pos["orig_stop"], pos["tp1"], pos["tp2"],
                                        pos["sl_method"], round(r_net, 3), i - pos["entry_idx"], reason))
                    equity_curve.append((idx[i], equity))
                    streak = streak + 1 if r_net < 0 else 0
                    if streak >= cfg.kill_switch_losses:
                        paused_until, streak, kill_triggers = i + cfg.cooldown_bars, 0, kill_triggers + 1
                    pos = None

            # ---------------- Nueva entrada (señal en i, entrada en i+1) ----------------
            if pos is None and i < n - 1 and i >= paused_until and sd[i] != 0 and sc[i] >= cfg.threshold:
                d = int(sd[i])
                entry = o[i + 1] * (1.0 + d * cfg.slippage_pct / 100.0)
                a = atr[i] if np.isfinite(atr[i]) and atr[i] > 0 else entry * 0.01
                ob = ob_long[i] if d == 1 else ob_short[i]
                stop, method = self.risk.stop_loss(d, entry, a, ob if np.isfinite(ob) else None, p)
                if (entry - stop) * d <= 0:  # Hueco de apertura que ya atraviesa el SL
                    continue
                tp1, tp2 = self.risk.take_profits(d, entry, stop, p.tp1_rr, p.tp2_rr)
                pos = {"dir": d, "entry": entry, "stop": stop, "orig_stop": stop, "tp1": tp1, "tp2": tp2,
                       "risk": abs(entry - stop), "entry_idx": i + 1, "score": float(sc[i]), "sl_method": method,
                       "partial_done": False, "realized": 0.0, "remaining": 1.0}

        trades_df = pd.DataFrame([asdict(t) for t in trades])
        eq = pd.Series([v for _, v in equity_curve], index=[t for t, _ in equity_curve], name="equity", dtype=float)
        return BacktestResult(trades_df, eq, self._metrics(trades_df, eq, p.equity, kill_triggers), cfg)

    @staticmethod
    def _metrics(trades: pd.DataFrame, equity: pd.Series, start_equity: float, kill_triggers: int) -> dict:
        """Métricas estándar de rendimiento."""
        if trades.empty:
            return {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "avg_r": 0.0, "total_r": 0.0,
                    "profit_factor": 0.0, "expectancy_r": 0.0, "max_dd_pct": 0.0, "final_equity": start_equity,
                    "return_pct": 0.0, "avg_bars": 0.0, "longs": 0, "shorts": 0, "kill_switch_triggers": kill_triggers}
        r = trades["result_r"]
        gains, losses = r[r > 0].sum(), -r[r < 0].sum()
        running_max = equity.cummax()
        dd = ((equity - running_max) / running_max * 100.0).min() if len(equity) else 0.0
        final = float(equity.iloc[-1]) if len(equity) else start_equity
        return {
            "trades": int(len(trades)),
            "wins": int((r > 0).sum()),
            "losses": int((r <= 0).sum()),
            "win_rate": float((r > 0).mean() * 100.0),
            "avg_r": float(r.mean()),
            "total_r": float(r.sum()),
            "profit_factor": float(gains / losses) if losses > 0 else float("inf") if gains > 0 else 0.0,
            "expectancy_r": float(r.mean()),
            "max_dd_pct": float(abs(dd)),
            "final_equity": round(final, 2),
            "return_pct": float((final / start_equity - 1.0) * 100.0),
            "avg_bars": float(trades["bars_held"].mean()),
            "longs": int((trades["direction"] == "LONG").sum()),
            "shorts": int((trades["direction"] == "SHORT").sum()),
            "kill_switch_triggers": kill_triggers,
        }
