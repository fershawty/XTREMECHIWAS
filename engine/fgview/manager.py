"""
engine/fgview/manager.py
========================
TradeManager: la ÚNICA implementación de la gestión de la posición de FG VIEW.
La usan la estadística de setups, la validación histórica y la proyección de ROI.

    TP1 → cierra 50 % y SL a break-even
    TP2 → cierra 30 % y SL a TP1
    TP3 → cierra el 20 % restante
    30 min sin terminar → cierra a mercado lo que quede
Si en una vela se tocan el stop y el objetivo, cuenta el stop (conservador).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from engine.fgview.config import DEFAULT_CONFIG, FGConfig
from engine.fgview.plan import Plan


@dataclass
class TradeOutcome:
    pnl: float  # USDT netos
    exit_index: int
    reason: str
    tps_hit: int  # 0 = el SL saltó antes del TP1; 1, 2 o 3 = TPs cobrados


class TradeManager:
    def __init__(self, config: FGConfig = DEFAULT_CONFIG) -> None:
        self.cfg = config

    @staticmethod
    def first_hit(side: int, high: np.ndarray, low: np.ndarray, start: int, end: int, target: float,
                  stop: float) -> tuple[int, int]:
        """
        Primitivo común: recorre las velas (start, end] y devuelve (resultado, índice):
        +1 si toca antes el objetivo, −1 si toca antes el stop, 0 si ninguno.
        """
        for j in range(start + 1, end + 1):
            if (low[j] <= stop) if side == 1 else (high[j] >= stop):
                return -1, j
            if (high[j] >= target) if side == 1 else (low[j] <= target):
                return 1, j
        return 0, end

    def run(self, plan: Plan, high: np.ndarray, low: np.ndarray, close: np.ndarray, start: int) -> TradeOutcome:
        """Simula la operación completa desde la vela `start` (entrada al cierre)."""
        side, n, fees = plan.side, plan.notional, plan.fees
        end = min(start + self.cfg.horizon_bars, len(high) - 1)
        tp_px = [t[0] for t in plan.tps]
        remaining = (1.0, 0.5, 0.2)  # Parte abierta tras 0, 1 o 2 TPs cobrados
        stops = (plan.stop_loss, plan.break_even, tp_px[0])
        pnl, i = 0.0, start
        for k in range(3):
            res, i = self.first_hit(side, high, low, i, end, tp_px[k], stops[k])
            if res == 1:
                pnl += plan.tp_pnl[k]
                if k == 2:
                    return TradeOutcome(pnl, i, "TP1+TP2+TP3", 3)
                continue
            if res == -1:
                if k == 0:
                    return TradeOutcome(pnl + plan.sl_pnl, i, "Stop Loss", 0)
                if k == 1:
                    return TradeOutcome(pnl, i, "TP1 + resto en break-even", 1)
                return TradeOutcome(pnl + n * 0.2 * plan.tps[0][1] / 100 - fees * 0.2, i, "TP1+TP2 + resto en TP1", 2)
            rem = remaining[k]
            move = side * (close[end] - plan.entry) / plan.entry
            return TradeOutcome(pnl + n * rem * move - fees * rem, end, "Tiempo (30 min)", k)
        return TradeOutcome(pnl, i, "TP1+TP2+TP3", 3)

    def reach_probability(self, side: int, high: np.ndarray, low: np.ndarray, close: np.ndarray, tp_pct: float,
                          sl_pct: float, mask: np.ndarray | None = None) -> tuple[float, float, int]:
        """% de veces que un objetivo de +tp_pct % se alcanzó antes que el SL en 30 min. -> (tp %, sl %, casos)."""
        hits = stops = total = 0
        for i in range(len(close) - self.cfg.horizon_bars):
            if mask is not None and not mask[i]:
                continue
            tgt = close[i] * (1 + side * tp_pct / 100)
            stp = close[i] * (1 - side * sl_pct / 100)
            res, _ = self.first_hit(side, high, low, i, i + self.cfg.horizon_bars, tgt, stp)
            total += 1
            hits += res == 1
            stops += res == -1
        return (hits / total * 100 if total else 0.0), (stops / total * 100 if total else 0.0), total
