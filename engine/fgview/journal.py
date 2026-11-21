"""
engine/fgview/journal.py
========================
TradeStats: métricas de rendimiento (win rate, profit factor, expectativa, drawdown,
racha y efectividad de cada filtro). Las usan la Bitácora y la validación histórica.
TradeJournal: repositorio de la bitácora (SQLite).
DailyGuard: stop diario (3 pérdidas seguidas o −3 % del capital).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engine.database import Database
from engine.fgview.config import DEFAULT_CONFIG, FGConfig
from engine.position_simulator import fmt_usd


class TradeStats:
    @staticmethod
    def summary(trades: list[dict]) -> dict:
        closed = [t for t in trades if t.get("pnl") is not None]
        if not closed:
            return {"trades": 0}
        pnl = np.array([float(t["pnl"]) for t in closed])
        wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
        equity = np.cumsum(pnl)
        peak = np.maximum.accumulate(np.concatenate([[0.0], equity]))[1:]
        streak, kind = 0, None
        for v in pnl[::-1]:
            k = "W" if v > 0 else "L"
            if kind in (None, k):
                kind, streak = k, streak + 1
            else:
                break
        return {
            "trades": len(closed),
            "win_rate": float((pnl > 0).mean() * 100),
            "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf"),
            "expectancy": float(pnl.mean()),
            "total": float(pnl.sum()),
            "max_drawdown": float((peak - equity).max()) if len(equity) else 0.0,
            "streak": f"{streak} {'ganadoras' if kind == 'W' else 'perdedoras'} seguidas",
        }

    @staticmethod
    def by_filter(trades: list[dict]) -> pd.DataFrame:
        """Win rate con y sin cada filtro: qué filtros acompañan a los aciertos."""
        def wr(xs: list[dict]) -> float:
            return round(sum(1 for t in xs if t["pnl"] > 0) / len(xs) * 100, 1) if xs else float("nan")

        rows = []
        for name in sorted({n for t in trades for n in (t.get("filters") or {})}):
            on = [t for t in trades if (t.get("filters") or {}).get(name) is True and t.get("pnl") is not None]
            off = [t for t in trades if (t.get("filters") or {}).get(name) is False and t.get("pnl") is not None]
            rows.append({"Filtro": name, "Operaciones con filtro": len(on), "Win rate con filtro %": wr(on),
                         "Operaciones sin filtro": len(off), "Win rate sin filtro %": wr(off)})
        return pd.DataFrame(rows)


class TradeJournal:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, entry: dict) -> int:
        return self.db.add_journal(entry)

    def entries(self) -> list[dict]:
        return self.db.journal()

    def today(self) -> list[dict]:
        return self.db.journal_today()

    def delete(self, entry_id: int) -> None:
        self.db.delete_journal(entry_id)

    def summary(self) -> dict:
        return TradeStats.summary(self.entries())

    def by_filter(self) -> pd.DataFrame:
        return TradeStats.by_filter(self.entries())


class DailyGuard:
    def __init__(self, config: FGConfig = DEFAULT_CONFIG) -> None:
        self.cfg = config

    def check(self, trades_today: list[dict], capital: float) -> str | None:
        streak = 0
        for t in trades_today:
            streak = streak + 1 if (t.get("pnl") or 0) < 0 else 0
        net = sum(t.get("pnl") or 0.0 for t in trades_today)
        if streak >= self.cfg.daily_max_losses:
            return f"Stop diario: {streak} pérdidas seguidas hoy. FG VIEW se detiene hasta mañana."
        if capital > 0 and net < 0 and abs(net) / capital * 100 >= self.cfg.daily_max_loss_pct:
            return f"Stop diario: pérdida de hoy {fmt_usd(net)} (≥ {self.cfg.daily_max_loss_pct:g}% del capital)."
        return None
