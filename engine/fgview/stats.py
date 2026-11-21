"""
engine/fgview/stats.py
======================
SetupStatistics: ¿cómo le habría ido a ESTE plan en situaciones parecidas?

Toma el plan actual, lo "rebasa" a cada vela pasada parecida (mismas distancias % y
mismo tamaño) y lo gestiona con el TradeManager real. De ahí salen:
* % de veces que se cobró TP1, TP2 y TP3
* % de veces que saltó el SL antes del TP1
* valor esperado por operación (media de los PnL simulados)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from engine.fgview.config import DEFAULT_CONFIG, FGConfig
from engine.fgview.filters import FilterSet
from engine.fgview.manager import TradeManager
from engine.fgview.plan import Plan


@dataclass
class OutcomeStats:
    samples: int
    similar: bool  # True = solo situaciones parecidas (>= min_similar casos)
    reach: tuple[float, float, float]  # % que cobró TP1, TP2, TP3
    sl_first: float  # % que tocó el SL antes del TP1
    time_exit: float  # % que salió por tiempo sin TP1
    ev: float  # PnL medio por operación (USDT)
    win_rate: float  # % de operaciones con PnL > 0


class SetupStatistics:
    def __init__(self, manager: TradeManager, filters: FilterSet, config: FGConfig = DEFAULT_CONFIG) -> None:
        self.manager = manager
        self.filters = filters
        self.cfg = config

    def evaluate(self, plan: Plan, df5: pd.DataFrame) -> OutcomeStats:
        h, lo, c = (df5[k].to_numpy(float) for k in ("high", "low", "close"))
        horizon = self.cfg.horizon_bars
        mask = self.filters.similar_mask(df5, plan.direction)
        similar = int(mask[: max(0, len(c) - horizon)].sum()) >= self.cfg.min_similar
        idx = [i for i in range(len(c) - horizon) if (mask[i] if similar else True)]
        if not idx:
            return OutcomeStats(0, False, (0.0, 0.0, 0.0), 0.0, 0.0, 0.0, 0.0)
        b = self.manager.evaluate_many(plan, h, lo, c, idx)  # C++ si está compilado
        tps, pnl, n = b.tps_hit, b.pnl, len(idx)
        reach = tuple(float((tps >= k).mean() * 100) for k in (1, 2, 3))
        sl_first = float(((tps == 0) & (b.reason == 0)).mean() * 100)
        time_exit = float(((tps == 0) & (b.reason == 4)).mean() * 100)
        return OutcomeStats(n, similar, reach, sl_first, time_exit, float(pnl.mean()), float((pnl > 0).mean() * 100))
