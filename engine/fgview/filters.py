"""
engine/fgview/filters.py
========================
Filtros de entrada y bloqueos como clases (patrón Strategy).

Cada Filter implementa UNA sola lógica vectorizada `series()` que sirve a la vez:
* en vivo     -> evaluate(): el valor de la última vela + un texto de detalle
* en histórico -> FilterSet.matrix(): el valor en cada vela (validación y estadística)
Así lo que se valida en el pasado es exactamente lo que se usa en vivo.

Para añadir un filtro: crea una subclase de Filter y añádela a DEFAULT_FILTERS.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from engine.fgview.policy import TierCatalog

if TYPE_CHECKING:
    from engine.fgview.snapshot import MarketSnapshot


@dataclass
class FilterCheck:
    name: str
    passed: bool
    detail: str
    plain: str  # Explicación en lenguaje sencillo


@dataclass
class FilterReport:
    direction: str
    checks: list[FilterCheck]
    blocks: list[str]
    min_filters: int

    @property
    def passed(self) -> int:
        return sum(c.passed for c in self.checks)

    @property
    def score(self) -> int:
        return round(self.passed / len(self.checks) * 100) if self.checks else 0

    @property
    def confidence(self) -> str:
        return "Alta" if self.passed >= 5 else ("Media" if self.passed == 4 else "Baja")

    @property
    def icons(self) -> str:
        return "".join("✅" if c.passed else "⬜" for c in self.checks)

    @property
    def enter(self) -> bool:
        return self.passed >= self.min_filters and not self.blocks


def _last(row: pd.Series, col: str) -> float:
    try:
        v = float(row[col])
        return v if np.isfinite(v) else float("nan")
    except (KeyError, TypeError, ValueError):
        return float("nan")


# =============================================================================
# Filtros
# =============================================================================
class Filter(ABC):
    name: str = ""
    plain: str = ""

    @abstractmethod
    def series(self, df5: pd.DataFrame, df1h: pd.DataFrame | None, direction: str) -> np.ndarray:
        """Bool por vela de 5 min (True = el filtro se cumple)."""

    def detail(self, snap: "MarketSnapshot", direction: str) -> str:
        return ""

    def plain_text(self, direction: str) -> str:
        return self.plain.format(side="los compradores" if direction == "LONG" else "los vendedores")

    def evaluate(self, snap: "MarketSnapshot", direction: str) -> FilterCheck:
        passed = bool(self.series(snap.df5, snap.df1h, direction)[-1])
        return FilterCheck(self.name, passed, self.detail(snap, direction), self.plain_text(direction))


class TrendFilter1h(Filter):
    name = "Tendencia 1h (EMA 50)"
    plain = "La tendencia de la última hora va a favor de la operación."

    def series(self, df5, df1h, direction):
        idx = df5.index
        if df1h is None or "ema_50" not in df1h.columns:
            return np.zeros(len(df5), bool)
        up = df1h["close"] > df1h["ema_50"]
        t = (up if direction == "LONG" else ~up).astype(float).where(df1h["ema_50"].notna())
        t.index = t.index + pd.Timedelta(hours=1)  # Una vela de 1 h solo se conoce al cerrar
        return (t.reindex(idx.union(t.index)).ffill().reindex(idx) > 0.5).to_numpy()

    def evaluate(self, snap, direction):
        # En vivo se usa la última vela de 1 h disponible (como en un gráfico)
        df1h = snap.df1h
        if df1h is None or not len(df1h) or "ema_50" not in df1h.columns or pd.isna(df1h["ema_50"].iloc[-1]):
            return FilterCheck(self.name, False, "sin datos de 1h", self.plain_text(direction))
        c, e = float(df1h["close"].iloc[-1]), float(df1h["ema_50"].iloc[-1])
        ok = c > e if direction == "LONG" else c < e
        return FilterCheck(self.name, ok, f"precio {'sobre' if c > e else 'bajo'} la EMA 50 de 1h",
                           self.plain_text(direction))


class EmaCrossFilter(Filter):
    name = "EMA 9 / EMA 21 (5m)"
    plain = "El precio de los últimos minutos empuja en la misma dirección."

    def series(self, df5, df1h, direction):
        up = df5["ema_9"] > df5["ema_21"]
        return (up if direction == "LONG" else df5["ema_9"] < df5["ema_21"]).to_numpy()

    def detail(self, snap, direction):
        row = snap.df5.iloc[-1]
        return f"EMA9 {'>' if _last(row, 'ema_9') > _last(row, 'ema_21') else '<'} EMA21"


class VwapFilter(Filter):
    name = "VWAP de sesión"
    plain = "El precio está del lado bueno de su media del día (mandan {side})."

    def series(self, df5, df1h, direction):
        return (df5["close"] > df5["vwap"] if direction == "LONG" else df5["close"] < df5["vwap"]).to_numpy()

    def detail(self, snap, direction):
        row = snap.df5.iloc[-1]
        return f"precio {'sobre' if _last(row, 'close') > _last(row, 'vwap') else 'bajo'} el VWAP"


class RsiFilter(Filter):
    name = "RSI 5m en zona sana"
    plain = "El precio no está ni demasiado caro ni demasiado barato: hay recorrido."
    bands = {"LONG": (40, 65), "SHORT": (35, 60)}

    def series(self, df5, df1h, direction):
        lo, hi = self.bands[direction]
        return df5["rsi"].between(lo, hi).to_numpy()

    def detail(self, snap, direction):
        lo, hi = self.bands[direction]
        return f"RSI {_last(snap.df5.iloc[-1], 'rsi'):.1f} (zona {lo}-{hi})"


class VolumeFilter(Filter):
    name = "Volumen > 1.5× media 20"
    plain = "Está entrando mucho dinero en este movimiento."
    threshold = 1.5

    def series(self, df5, df1h, direction):
        return (df5["rvol"] > self.threshold).to_numpy()

    def detail(self, snap, direction):
        rv = _last(snap.df5.iloc[-1], "rvol")
        return f"{rv:.2f}× la media" if rv == rv else "sin volumen"


class FundingOIFilter(Filter):
    name = "Funding neutro + OI"
    plain = "No hay demasiada gente apostando en la misma dirección y entra dinero nuevo."
    max_abs_funding = 0.0003

    def series(self, df5, df1h, direction):
        # Histórico: no hay funding por vela (se asume neutro); el Open Interest sí se usa
        if "oi_change_pct" not in df5.columns:
            return np.ones(len(df5), bool)
        return (df5["oi_change_pct"] > 0).to_numpy()

    def evaluate(self, snap, direction):
        oi_ok = bool(self.series(snap.df5, snap.df1h, direction)[-1])
        oi = _last(snap.df5.iloc[-1], "oi_change_pct")
        if snap.funding is None:
            return FilterCheck(self.name, False, "sin datos de funding (solo exchanges de futuros)",
                               self.plain_text(direction))
        f_ok = abs(snap.funding) <= self.max_abs_funding
        detail = f"funding {snap.funding * 100:.4f}% · " + (f"OI {oi:+.2f}%" if oi == oi else "sin OI")
        return FilterCheck(self.name, f_ok and oi_ok and oi == oi, detail, self.plain_text(direction))


DEFAULT_FILTERS: tuple[Filter, ...] = (TrendFilter1h(), EmaCrossFilter(), VwapFilter(), RsiFilter(), VolumeFilter(),
                                       FundingOIFilter())


# =============================================================================
# Bloqueos
# =============================================================================
class BlockRule(ABC):
    """Situación en la que no se debe operar aunque los filtros se cumplan."""

    def check(self, snap: "MarketSnapshot", tier_key: str, now: datetime) -> str | None:
        series = self.series(snap.df5, tier_key)
        return self.message(snap) if series is not None and bool(series[-1]) else None

    def series(self, df5: pd.DataFrame, tier_key: str) -> np.ndarray | None:
        """Bool por vela (None si la regla no se puede evaluar en histórico)."""
        return None

    def message(self, snap: "MarketSnapshot") -> str:
        return ""


class SpreadBlock(BlockRule):
    max_spread_pct = 0.05

    def check(self, snap, tier_key, now):
        ob = snap.order_book or {}
        sp = ob.get("spread_pct")
        return f"Spread {sp:.3f}% > {self.max_spread_pct}%: liquidez insuficiente" if sp is not None and sp > self.max_spread_pct else None


class WickBlock(BlockRule):
    max_atr = 2.0

    def series(self, df5, tier_key):
        o, h, lo, c = (df5[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        wick = np.maximum(h - np.maximum(o, c), np.minimum(o, c) - lo)
        atr = df5["atr"].to_numpy(float)
        with np.errstate(invalid="ignore"):
            return np.nan_to_num(wick > self.max_atr * atr, nan=False).astype(bool)

    def message(self, snap):
        row = snap.df5.iloc[-1]
        o, h, lo, c, atr = (_last(row, k) for k in ("open", "high", "low", "close", "atr"))
        return f"Mecha de la última vela ({max(h - max(o, c), min(o, c) - lo) / atr:.1f}× ATR) > 2× ATR: movimiento errático"


class WeekendLowVolumeBlock(BlockRule):
    def series(self, df5, tier_key):
        if tier_key != "C":
            return np.zeros(len(df5), bool)
        weekend = df5.index.day_of_week >= 5
        rv = df5["rvol"].to_numpy(float)
        return weekend & ~(rv >= 1.0)

    def message(self, snap):
        return "Fin de semana con volumen bajo en una cripto de Nivel C"


DEFAULT_BLOCKS: tuple[BlockRule, ...] = (SpreadBlock(), WickBlock(), WeekendLowVolumeBlock())


# =============================================================================
# Conjunto de filtros
# =============================================================================
class FilterSet:
    def __init__(self, filters: tuple[Filter, ...] = DEFAULT_FILTERS, blocks: tuple[BlockRule, ...] = DEFAULT_BLOCKS,
                 tiers: TierCatalog | None = None, min_filters: int = 4) -> None:
        self.filters = filters
        self.blocks = blocks
        self.tiers = tiers or TierCatalog()
        self.min_filters = min_filters

    @property
    def names(self) -> list[str]:
        return [f.name for f in self.filters]

    def evaluate(self, snap: "MarketSnapshot", direction: str, symbol: str, extra_blocks: list[str] | None = None,
                 now: datetime | None = None) -> FilterReport:
        tier, _ = self.tiers.for_coin(symbol)
        now = now or datetime.now(timezone.utc)
        blocks = [b for b in (extra_blocks or []) if b]
        blocks += [m for m in (rule.check(snap, tier.key, now) for rule in self.blocks) if m]
        return FilterReport(direction, [f.evaluate(snap, direction) for f in self.filters], blocks, self.min_filters)

    def best(self, snap: "MarketSnapshot", symbol: str, extra_blocks: list[str] | None = None) -> FilterReport:
        """Evalúa LONG y SHORT y devuelve la dirección con más filtros cumplidos."""
        rl = self.evaluate(snap, "LONG", symbol, extra_blocks)
        rs = self.evaluate(snap, "SHORT", symbol, extra_blocks)
        return rl if rl.passed >= rs.passed else rs

    def matrix(self, df5: pd.DataFrame, df1h: pd.DataFrame | None, direction: str) -> np.ndarray:
        """(n_velas × n_filtros) bool, para validación y estadística."""
        return np.column_stack([f.series(df5, df1h, direction) for f in self.filters]).astype(bool)

    def blocked(self, df5: pd.DataFrame, symbol: str) -> np.ndarray:
        """Bool por vela con los bloqueos evaluables en histórico."""
        tier, _ = self.tiers.for_coin(symbol)
        out = np.zeros(len(df5), bool)
        for rule in self.blocks:
            s = rule.series(df5, tier.key)
            if s is not None:
                out |= s
        return out

    def similar_mask(self, df5: pd.DataFrame, direction: str) -> np.ndarray:
        """Velas parecidas a la situación de entrada: EMA 9/21, VWAP y RSI a favor."""
        core = [f for f in self.filters if isinstance(f, (EmaCrossFilter, VwapFilter, RsiFilter))]
        return np.logical_and.reduce([f.series(df5, None, direction) for f in core])
