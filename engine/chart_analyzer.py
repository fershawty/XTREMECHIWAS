"""
engine/chart_analyzer.py
========================
"Lee" la gráfica como lo haría un trader de Smart Money Concepts (SMC).

Detecta, vela a vela y SIN mirar el futuro:
* Swings (máximos/mínimos estructurales confirmados)
* BOS   (Break of Structure): ruptura a favor de la tendencia -> continuación
* CHoCH (Change of Character): primera ruptura en contra -> posible giro
* Order Blocks (OB): última vela contraria antes del impulso que rompió estructura
* FVG (Fair Value Gaps): huecos de 3 velas que el precio suele volver a rellenar
* Soportes / Resistencias clave (agrupando swings cercanos)

Salida
------
1. El DataFrame con columnas por vela (útiles para el scoring y el backtest):
   structure_trend, bos_up, bos_down, choch_up, choch_down, last_break_dir,
   bars_since_break, last_break_kind, in_bull_ob, in_bear_ob, dist_bull_ob,
   dist_bear_ob, bull_ob_low, bear_ob_high, in_bull_fvg, in_bear_fvg,
   dist_bull_fvg, dist_bear_fvg, support, resistance, dist_support, dist_resistance
   (todas las distancias están expresadas en múltiplos de ATR)
2. Un `StructureResult` con las zonas y eventos para dibujarlos en el gráfico.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from engine.config import get_logger
from engine.indicators import atr as atr_fn
from engine.indicators import find_pivots

log = get_logger("chart_analyzer")


# =============================================================================
# Estructuras de datos
# =============================================================================
@dataclass
class Zone:
    """Zona de precio (Order Block o FVG)."""

    kind: str  # "OB" | "FVG"
    direction: int  # 1 alcista (demanda) | -1 bajista (oferta)
    top: float
    bottom: float
    origin_idx: int  # Vela que forma la zona
    created_idx: int  # Vela en la que la zona queda confirmada
    mitigated_idx: int | None = None  # Vela en la que se invalida / rellena
    touched: bool = False  # ¿El precio ya volvió a la zona?
    expired: bool = False  # Retirada por antigüedad o por exceso de zonas

    @property
    def active(self) -> bool:
        """True si la zona sigue vigente."""
        return self.mitigated_idx is None

    @property
    def mid(self) -> float:
        """Precio medio de la zona."""
        return (self.top + self.bottom) / 2.0


@dataclass
class StructureEvent:
    """Ruptura de estructura (BOS o CHoCH)."""

    idx: int  # Vela que rompe
    kind: str  # "BOS" | "CHoCH"
    direction: int  # 1 alcista | -1 bajista
    level: float  # Precio del swing roto
    swing_idx: int  # Vela del swing roto


@dataclass
class SwingPoint:
    """Máximo o mínimo estructural confirmado."""

    idx: int
    price: float
    kind: str  # "high" | "low"
    confirmed_idx: int


@dataclass
class KeyLevel:
    """Soporte o resistencia relevante (agrupación de swings)."""

    price: float
    kind: str  # "support" | "resistance"
    touches: int
    last_idx: int


@dataclass
class StructureResult:
    """Todo lo detectado en la gráfica."""

    events: list[StructureEvent] = field(default_factory=list)
    order_blocks: list[Zone] = field(default_factory=list)
    fvgs: list[Zone] = field(default_factory=list)
    swings: list[SwingPoint] = field(default_factory=list)
    key_levels: list[KeyLevel] = field(default_factory=list)
    trend: int = 0  # Tendencia estructural en la última vela

    def active_zones(self, kind: str = "OB", direction: int | None = None, limit: int | None = None) -> list[Zone]:
        """Zonas vigentes (más recientes primero)."""
        pool = self.order_blocks if kind == "OB" else self.fvgs
        zones = [z for z in pool if z.active and (direction is None or z.direction == direction)]
        zones.sort(key=lambda z: z.created_idx, reverse=True)
        return zones[:limit] if limit else zones

    def last_event(self) -> StructureEvent | None:
        """Última ruptura de estructura."""
        return self.events[-1] if self.events else None

    def summary(self, n_bars: int) -> str:
        """Resumen legible de la estructura actual."""
        trend_txt = {1: "ALCISTA", -1: "BAJISTA"}.get(self.trend, "INDEFINIDA")
        parts = [f"Estructura {trend_txt}"]
        ev = self.last_event()
        if ev:
            dir_txt = "alcista" if ev.direction == 1 else "bajista"
            parts.append(f"último {ev.kind} {dir_txt} hace {n_bars - 1 - ev.idx} velas")
        bull_ob, bear_ob = len(self.active_zones("OB", 1)), len(self.active_zones("OB", -1))
        bull_fvg, bear_fvg = len(self.active_zones("FVG", 1)), len(self.active_zones("FVG", -1))
        parts.append(f"OB activos {bull_ob}▲/{bear_ob}▼ · FVG {bull_fvg}▲/{bear_fvg}▼")
        return " · ".join(parts)


# =============================================================================
# Analizador
# =============================================================================
class ChartAnalyzer:
    """
    Detector de estructura de mercado SMC.

    Parámetros principales (ajústalos para hacerlo más o menos sensible):
        swing_length: velas a cada lado para confirmar un swing (5 = estructura media).
        fvg_min_atr: tamaño mínimo del FVG en ATR (filtra huecos insignificantes).
        ob_lookback: velas hacia atrás para buscar el Order Block del impulso.
        max_zone_age: velas tras las que una zona sin tocar caduca.
        max_active_zones: zonas activas máximas por tipo y dirección.
        sr_swings: cuántos swings recientes se usan como soporte/resistencia.
    """

    def __init__(
        self,
        swing_length: int = 5,
        fvg_min_atr: float = 0.15,
        ob_lookback: int = 30,
        max_zone_age: int = 300,
        max_active_zones: int = 8,
        sr_swings: int = 12,
    ) -> None:
        self.swing_length = swing_length
        self.fvg_min_atr = fvg_min_atr
        self.ob_lookback = ob_lookback
        self.max_zone_age = max_zone_age
        self.max_active_zones = max_active_zones
        self.sr_swings = sr_swings

    # ------------------------------------------------------------------ API
    def analyze(self, df: pd.DataFrame) -> tuple[pd.DataFrame, StructureResult]:
        """
        Recorre las velas una a una (como si fuera en vivo) y construye la estructura.

        Args:
            df: DataFrame OHLCV (idealmente ya con la columna 'atr').

        Returns:
            (df con columnas de estructura, StructureResult)
        """
        n = len(df)
        o = df["open"].to_numpy(float)
        h = df["high"].to_numpy(float)
        lo = df["low"].to_numpy(float)
        c = df["close"].to_numpy(float)
        atr_raw = (df["atr"] if "atr" in df.columns else atr_fn(df, 14)).to_numpy(float)
        # Primeras velas sin ATR: usamos el rango medio para no dividir por NaN
        fallback = pd.Series(h - lo).expanding().mean().to_numpy()
        atr_v = np.where(np.isfinite(atr_raw) & (atr_raw > 0), atr_raw, np.maximum(fallback, 1e-12))

        L = R = self.swing_length
        is_ph = find_pivots(df["high"], L, R, "high")
        is_pl = find_pivots(df["low"], L, R, "low")

        res = StructureResult()
        last_sh: dict | None = None
        last_sl: dict | None = None
        recent_highs: list[float] = []
        recent_lows: list[float] = []
        active_obs: list[Zone] = []
        active_fvgs: list[Zone] = []
        trend = 0
        last_break_idx: int | None = None
        last_break_dir = 0
        last_break_kind = 0  # 1 = BOS, 2 = CHoCH

        cols = {
            name: np.zeros(n, dtype=bool)
            for name in ("bos_up", "bos_down", "choch_up", "choch_down",
                         "in_bull_ob", "in_bear_ob", "in_bull_fvg", "in_bear_fvg")
        }
        nan_cols = {
            name: np.full(n, np.nan)
            for name in ("bars_since_break", "dist_bull_ob", "dist_bear_ob", "bull_ob_low", "bear_ob_high",
                         "dist_bull_fvg", "dist_bear_fvg", "support", "resistance",
                         "dist_support", "dist_resistance")
        }
        structure_trend = np.zeros(n, dtype=int)
        last_dir_arr = np.zeros(n, dtype=int)
        last_kind_arr = np.zeros(n, dtype=int)

        for i in range(n):
            # 1) Confirmar swings que ya tienen R velas a la derecha
            j = i - R
            if j >= 0:
                if is_ph[j]:
                    res.swings.append(SwingPoint(j, h[j], "high", i))
                    last_sh = {"idx": j, "price": h[j], "broken": False}
                    recent_highs.append(h[j])
                if is_pl[j]:
                    res.swings.append(SwingPoint(j, lo[j], "low", i))
                    last_sl = {"idx": j, "price": lo[j], "broken": False}
                    recent_lows.append(lo[j])

            # 2) Mitigación / relleno de zonas existentes con la vela actual
            self._update_zones(active_obs, i, h[i], lo[i], c[i], kind="OB")
            self._update_zones(active_fvgs, i, h[i], lo[i], c[i], kind="FVG")
            active_obs = [z for z in active_obs if z.active]
            active_fvgs = [z for z in active_fvgs if z.active]

            # 3) Rupturas de estructura (por CIERRE, no por mecha)
            if last_sh and not last_sh["broken"] and c[i] > last_sh["price"]:
                kind = "CHoCH" if trend == -1 else "BOS"
                trend, last_sh["broken"] = 1, True
                res.events.append(StructureEvent(i, kind, 1, last_sh["price"], last_sh["idx"]))
                cols["choch_up" if kind == "CHoCH" else "bos_up"][i] = True
                last_break_idx, last_break_dir, last_break_kind = i, 1, (2 if kind == "CHoCH" else 1)
                ob = self._find_order_block(o, h, lo, c, last_sh["idx"], i, direction=1)
                if ob:
                    res.order_blocks.append(ob)
                    active_obs.append(ob)

            if last_sl and not last_sl["broken"] and c[i] < last_sl["price"]:
                kind = "CHoCH" if trend == 1 else "BOS"
                trend, last_sl["broken"] = -1, True
                res.events.append(StructureEvent(i, kind, -1, last_sl["price"], last_sl["idx"]))
                cols["choch_down" if kind == "CHoCH" else "bos_down"][i] = True
                last_break_idx, last_break_dir, last_break_kind = i, -1, (2 if kind == "CHoCH" else 1)
                ob = self._find_order_block(o, h, lo, c, last_sl["idx"], i, direction=-1)
                if ob:
                    res.order_blocks.append(ob)
                    active_obs.append(ob)

            # 4) Fair Value Gaps (patrón de 3 velas que termina en i)
            if i >= 2:
                min_gap = self.fvg_min_atr * atr_v[i]
                if lo[i] - h[i - 2] > min_gap and c[i - 1] > o[i - 1]:
                    z = Zone("FVG", 1, top=lo[i], bottom=h[i - 2], origin_idx=i - 1, created_idx=i)
                    res.fvgs.append(z)
                    active_fvgs.append(z)
                if lo[i - 2] - h[i] > min_gap and c[i - 1] < o[i - 1]:
                    z = Zone("FVG", -1, top=lo[i - 2], bottom=h[i], origin_idx=i - 1, created_idx=i)
                    res.fvgs.append(z)
                    active_fvgs.append(z)

            active_obs = self._cap_zones(active_obs, i)
            active_fvgs = self._cap_zones(active_fvgs, i)

            # 5) Métricas por vela
            structure_trend[i] = trend
            if last_break_idx is not None:
                nan_cols["bars_since_break"][i] = i - last_break_idx
                last_dir_arr[i] = last_break_dir
                last_kind_arr[i] = last_break_kind

            a = atr_v[i]
            for prefix, zones in (("ob", active_obs), ("fvg", active_fvgs)):
                bull = [z for z in zones if z.direction == 1 and z.bottom <= c[i]]
                if bull:
                    dists = [max(0.0, lo[i] - z.top) / a for z in bull]
                    k = int(np.argmin(dists))
                    nan_cols[f"dist_bull_{prefix}"][i] = dists[k]
                    cols[f"in_bull_{prefix}"][i] = dists[k] == 0.0
                    if prefix == "ob":
                        nan_cols["bull_ob_low"][i] = bull[k].bottom
                bear = [z for z in zones if z.direction == -1 and z.top >= c[i]]
                if bear:
                    dists = [max(0.0, z.bottom - h[i]) / a for z in bear]
                    k = int(np.argmin(dists))
                    nan_cols[f"dist_bear_{prefix}"][i] = dists[k]
                    cols[f"in_bear_{prefix}"][i] = dists[k] == 0.0
                    if prefix == "ob":
                        nan_cols["bear_ob_high"][i] = bear[k].top

            supports = [p for p in recent_lows[-self.sr_swings:] if p < c[i]]
            resistances = [p for p in recent_highs[-self.sr_swings:] if p > c[i]]
            if supports:
                s = max(supports)
                nan_cols["support"][i] = s
                nan_cols["dist_support"][i] = (c[i] - s) / a
            if resistances:
                r = min(resistances)
                nan_cols["resistance"][i] = r
                nan_cols["dist_resistance"][i] = (r - c[i]) / a

        res.trend = trend
        if n:
            res.key_levels = self._key_levels(res.swings, c[-1], atr_v[-1], n)

        extra = pd.DataFrame(
            {**cols, **nan_cols, "structure_trend": structure_trend,
             "last_break_dir": last_dir_arr, "last_break_kind": last_kind_arr},
            index=df.index,
        )
        out = pd.concat([df.drop(columns=[c_ for c_ in extra.columns if c_ in df.columns]), extra], axis=1)
        return out, res

    # -------------------------------------------------------------- Zonas
    def _update_zones(self, zones: list[Zone], i: int, high: float, low: float, close: float, kind: str) -> None:
        """
        Marca zonas tocadas o invalidadas por la vela i.
        - OB: se invalida si el precio CIERRA al otro lado.
        - FVG: se considera rellenado si la mecha lo atraviesa por completo.
        """
        for z in zones:
            if i - z.created_idx > self.max_zone_age:
                z.mitigated_idx, z.expired = i, True
                continue
            if z.direction == 1:
                broken = close < z.bottom if kind == "OB" else low <= z.bottom
                if broken:
                    z.mitigated_idx = i
                elif low <= z.top:
                    z.touched = True
            else:
                broken = close > z.top if kind == "OB" else high >= z.top
                if broken:
                    z.mitigated_idx = i
                elif high >= z.bottom:
                    z.touched = True

    def _cap_zones(self, zones: list[Zone], i: int) -> list[Zone]:
        """Mantiene como máximo `max_active_zones` por dirección (descarta las más viejas)."""
        out: list[Zone] = []
        for direction in (1, -1):
            same = sorted([z for z in zones if z.direction == direction], key=lambda z: z.created_idx)
            for z in same[: max(0, len(same) - self.max_active_zones)]:
                z.mitigated_idx, z.expired = i, True
            out.extend(same[-self.max_active_zones:])
        return out

    def _find_order_block(
        self, o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, swing_idx: int, break_idx: int, direction: int
    ) -> Zone | None:
        """
        Order Block = última vela contraria en el origen del impulso que rompió.
        - Alcista: buscamos el mínimo más bajo entre el swing roto y la ruptura, y la
          última vela bajista en ese punto (o hasta 3 velas antes).
        - Bajista: simétrico con el máximo más alto y la última vela alcista.
        """
        start = max(swing_idx, break_idx - self.ob_lookback)
        if break_idx - start < 1:
            start = max(0, break_idx - 3)
        if break_idx - start < 1:
            return None
        if direction == 1:
            k = start + int(np.argmin(lo[start:break_idx]))
            cand = next((m for m in range(k, max(start, k - 3) - 1, -1) if c[m] < o[m]), k)
            top, bottom = h[cand], float(lo[cand : k + 1].min())
        else:
            k = start + int(np.argmax(h[start:break_idx]))
            cand = next((m for m in range(k, max(start, k - 3) - 1, -1) if c[m] > o[m]), k)
            top, bottom = float(h[cand : k + 1].max()), lo[cand]
        if not top > bottom:
            return None
        return Zone("OB", direction, float(top), float(bottom), origin_idx=cand, created_idx=break_idx)

    # ------------------------------------------------- Niveles clave (S/R)
    @staticmethod
    def _key_levels(swings: list[SwingPoint], last_close: float, last_atr: float, n: int,
                    lookback: int = 300, per_side: int = 3) -> list[KeyLevel]:
        """
        Agrupa swings cercanos (dentro de 0.6 ATR) en niveles. Más toques = más fuerte.
        Devuelve hasta `per_side` soportes (debajo) y resistencias (encima) del precio.
        """
        points = sorted((s.price, s.idx) for s in swings if s.idx >= n - lookback)
        if not points:
            return []
        tol = 0.6 * last_atr
        clusters: list[dict] = []
        for price, idx in points:
            if clusters and abs(price - clusters[-1]["mean"]) <= tol:
                cl = clusters[-1]
                cl["prices"].append(price)
                cl["mean"] = float(np.mean(cl["prices"]))
                cl["last_idx"] = max(cl["last_idx"], idx)
            else:
                clusters.append({"prices": [price], "mean": price, "last_idx": idx})

        levels = [
            KeyLevel(cl["mean"], "support" if cl["mean"] < last_close else "resistance", len(cl["prices"]), cl["last_idx"])
            for cl in clusters
        ]
        # Priorizamos por toques y, a igualdad, por cercanía al precio actual
        def rank(lv: KeyLevel) -> tuple:
            return (-lv.touches, abs(lv.price - last_close))

        supports = sorted([lv for lv in levels if lv.kind == "support"], key=rank)[:per_side]
        resistances = sorted([lv for lv in levels if lv.kind == "resistance"], key=rank)[:per_side]
        return supports + resistances
