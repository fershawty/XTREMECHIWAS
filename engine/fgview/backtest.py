"""
engine/fgview/backtest.py
=========================
RecommenderBacktester: repite FG VIEW sobre días de velas pasadas con los MISMOS
filtros (FilterSet), el MISMO plan (PositionPlanner) y la MISMA gestión
(TradeManager) que en vivo, y mide si la estrategia gana dinero de verdad.

Aproximaciones (se avisan en la interfaz): sin funding ni libro de órdenes ni
noticias históricas; entrada al cierre de la vela de señal; comisiones taker.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from engine.fgview.config import DEFAULT_CONFIG, FGConfig
from engine.fgview.filters import FilterSet
from engine.fgview.journal import TradeStats
from engine.fgview.manager import TradeManager
from engine.fgview.plan import PositionPlanner
from engine.fgview.snapshot import SnapshotLoader

WARMUP = 120
SESSIONS = (("Asia (00-07 UTC)", 0, 7), ("Europa (07-13:30)", 7, 13.5), ("Nueva York (13:30-21)", 13.5, 21),
            ("Noche (21-24)", 21, 24))


@dataclass
class ValidationResult:
    symbol: str
    bars: int
    days: float
    trades: pd.DataFrame
    metrics: dict
    by_confidence: pd.DataFrame
    by_session: pd.DataFrame
    by_filter: pd.DataFrame
    notes: list[str] = field(default_factory=list)


def session_of(ts: pd.Timestamp) -> str:
    h = ts.hour + ts.minute / 60
    return next((name for name, a, b in SESSIONS if a <= h < b), SESSIONS[-1][0])


class RecommenderBacktester:
    def __init__(self, filters: FilterSet, planner: PositionPlanner, manager: TradeManager,
                 config: FGConfig = DEFAULT_CONFIG) -> None:
        self.filters, self.planner, self.manager, self.cfg = filters, planner, manager, config

    def run(self, df5: pd.DataFrame, df1h: pd.DataFrame | None, symbol: str, capital: float = 1000.0) -> ValidationResult:
        n, horizon = len(df5), self.cfg.horizon_bars
        fl, fs = self.filters.matrix(df5, df1h, "LONG"), self.filters.matrix(df5, df1h, "SHORT")
        pl, ps = fl.sum(axis=1), fs.sum(axis=1)
        blocked = self.filters.blocked(df5, symbol)
        h, lo, c = (df5[k].to_numpy(float) for k in ("high", "low", "close"))
        atr = df5["atr"].to_numpy(float)
        rows, i = [], WARMUP
        while i < n - horizon - 1:
            side_long = pl[i] >= ps[i]
            passes = int(pl[i] if side_long else ps[i])
            if passes < self.filters.min_filters or blocked[i] or not np.isfinite(atr[i]):
                i += 1
                continue
            direction = "LONG" if side_long else "SHORT"
            plan = self.planner.plan(capital, symbol, direction, c[i], atr[i])
            out = self.manager.run(plan, h, lo, c, i)
            flags = (fl if side_long else fs)[i]
            rows.append({"time": df5.index[i], "direction": direction, "passes": passes, "pnl": round(out.pnl, 2),
                         "tps_hit": out.tps_hit, "reason": out.reason, "bars": out.exit_index - i,
                         "session": session_of(df5.index[i]), "filters": dict(zip(self.filters.names, map(bool, flags)))})
            i = out.exit_index + 1  # Una operación a la vez
        trades = pd.DataFrame(rows)
        days = n * self.cfg.bar_minutes / 60 / 24
        notes = ["Funding histórico no disponible (se asume neutro); sin libro de órdenes ni noticias históricas.",
                 "Entrada al cierre de la vela de señal, comisiones taker y, si SL y TP caen en la misma vela, cuenta el SL."]
        if len(trades) < 30:
            notes.append(f"Solo {len(trades)} operaciones: muestra pequeña. Usa más días para una conclusión fiable.")
        if trades.empty:
            return ValidationResult(symbol, n, days, trades, {"trades": 0}, pd.DataFrame(), pd.DataFrame(),
                                    pd.DataFrame(), notes)
        records = trades.to_dict("records")
        metrics = TradeStats.summary(records)
        metrics["pnl_pct"] = metrics["total"] / capital * 100
        metrics["per_day"] = len(trades) / max(days, 0.01)

        def group(col: str, label: str) -> pd.DataFrame:
            g = trades.groupby(col)["pnl"].agg(Operaciones="size", WinRate=lambda s: round((s > 0).mean() * 100, 1),
                                               PnlMedio=lambda s: round(s.mean(), 2), Total=lambda s: round(s.sum(), 2))
            return g.reset_index().rename(columns={col: label, "WinRate": "Win rate %", "PnlMedio": "PnL medio",
                                                   "Total": "PnL total"})

        return ValidationResult(symbol, n, days, trades, metrics, group("passes", "Filtros cumplidos"),
                                group("session", "Sesión"), TradeStats.by_filter(records), notes)

    def run_symbol(self, loader: SnapshotLoader, symbol: str, market: str, days: int, contract: str = "USDT-M",
                   exchange: str | None = None, capital: float = 1000.0) -> ValidationResult:
        """Descarga `days` días y valida."""
        snap = loader.load(symbol, market, contract, exchange, bars5=int(days * 288) + WARMUP,
                           bars1h=int(days * 24) + 80, force=True)
        return self.run(snap.df5, snap.df1h, snap.symbol, capital)
