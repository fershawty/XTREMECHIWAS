"""
engine/fgview/squeeze.py
========================
SqueezeGuard: protección contra subidas y bajadas bruscas que liquidan.

Un SHORT SQUEEZE es un rebote violento después de una caída (los cortos cierran a la
vez y empujan el precio arriba); un LONG SQUEEZE es lo contrario. Con apalancamiento
alto, un squeeze de 2–5 % en minutos liquida.

Qué mide (velas de 5 min):
* Rebote/caída desde el extremo de la última hora, en múltiplos de ATR.
* Racha de velas a favor del squeeze con volumen alto.
* RSI saliendo de sobreventa (<30) o sobrecompra (>70) en la última hora (giro en V).
* Precio recuperando (o perdiendo) la EMA 9 y el VWAP.
* Funding negativo (muchos cortos pagando) o muy positivo (muchos largos).
* AGOTAMIENTO (antes del rebote): ya cayó ≥ 4% (o ≥ 8 ATR) en 4 h, RSI en sobreventa o
  precio fuera de la banda de Bollinger -> vender en corto AHÍ es llegar tarde.
* Movimiento máximo en 30 min de las últimas 24 h -> tope de apalancamiento para que la
  liquidación quede MÁS LEJOS que ese movimiento (× 1.2).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from engine.position_simulator import MAINTENANCE_MARGIN

SAFETY = 1.2  # La liquidación debe quedar 1.2× más lejos que el peor movimiento de 30 min


@dataclass
class SqueezeReport:
    direction: str  # Dirección EN PELIGRO (LONG o SHORT)
    risk: str  # alto | medio | bajo
    points: int
    reasons: list[str] = field(default_factory=list)
    max_move_pct: float = 0.0  # Peor movimiento en contra en 30 min (24 h), %
    lev_cap: int = 125

    @property
    def danger(self) -> bool:
        return self.risk in ("alto", "medio")

    @property
    def label(self) -> str:
        kind = "SHORT SQUEEZE (subida brusca)" if self.direction == "SHORT" else "LONG SQUEEZE (caída brusca)"
        return f"{'🚨' if self.risk == 'alto' else '⚠️'} Riesgo {self.risk.upper()} de {kind}"

    def text(self) -> str:
        return (f"{self.label}: no abras {self.direction} con apalancamiento alto. " + " · ".join(self.reasons)
                + f" · Peor movimiento en 30 min (24 h): {self.max_move_pct:.2f}% → apalancamiento máximo "
                f"seguro {self.lev_cap}x.")


class SqueezeGuard:
    def __init__(self, window: int = 12, horizon: int = 6, lookback: int = 288) -> None:
        self.window, self.horizon, self.lookback = window, horizon, lookback  # 1 h, 30 min, 24 h en velas de 5 min

    @staticmethod
    def lev_cap_for(move_pct: float, safety: float = SAFETY) -> int:
        """Apalancamiento máximo para que la liquidación quede más lejos que `move_pct` × safety."""
        need = safety * move_pct / 100 + MAINTENANCE_MARGIN
        return max(1, min(125, math.floor(1 / need))) if need > 0 else 125

    def max_adverse_move(self, df: pd.DataFrame, direction: str) -> float:
        """Mayor movimiento en contra (en %) dentro de 30 min, en las últimas 24 h."""
        d = df.iloc[-self.lookback:]
        c = d["close"].to_numpy(float)
        if len(c) <= self.horizon:
            return 0.0
        h = self.horizon
        if direction == "SHORT":
            fut = pd.Series(d["high"].to_numpy(float)[::-1]).rolling(h, min_periods=1).max().to_numpy()[::-1]
            move = fut[1:] / c[:-1] - 1
        else:
            fut = pd.Series(d["low"].to_numpy(float)[::-1]).rolling(h, min_periods=1).min().to_numpy()[::-1]
            move = 1 - fut[1:] / c[:-1]
        return float(np.nanmax(move) * 100) if len(move) else 0.0

    def analyze(self, df5: pd.DataFrame, direction: str, funding: float | None = None) -> SqueezeReport:
        """Riesgo de squeeze CONTRA `direction` (la posición que se quiere abrir)."""
        reasons: list[str] = []
        pts = 0
        if len(df5) < self.window + 5:
            return SqueezeReport(direction, "bajo", 0)
        w = df5.iloc[-self.window:]
        last = df5.iloc[-1]
        price = float(last["close"])
        atr = float(last.get("atr", np.nan)) if "atr" in df5 else float((df5["high"] - df5["low"]).iloc[-14:].mean())
        up = direction == "SHORT"  # Para un SHORT el peligro es que suba
        if atr > 0:
            ext = float(w["low"].min()) if up else float(w["high"].max())
            run = (price - ext) / atr if up else (ext - price) / atr
            if run >= 4:
                pts += 2
                reasons.append(f"{'rebotó' if up else 'cayó'} {run:.1f}× ATR desde el {'mínimo' if up else 'máximo'} de la última hora")
            elif run >= 2.5:
                pts += 1
                reasons.append(f"{'rebote' if up else 'caída'} de {run:.1f}× ATR en la última hora")
        last3 = df5.iloc[-3:]
        green = (last3["close"] > last3["open"]) if up else (last3["close"] < last3["open"])
        vol_avg = df5["volume"].iloc[-30:-3].mean()
        if bool(green.all()) and vol_avg > 0 and last3["volume"].mean() > 1.5 * vol_avg:
            pts += 1
            reasons.append(f"3 velas seguidas {'verdes' if up else 'rojas'} con volumen alto")
        if "rsi" in df5:
            r = w["rsi"].dropna()
            if len(r) and ((up and r.min() < 30 and r.iloc[-1] > 45) or (not up and r.max() > 70 and r.iloc[-1] < 55)):
                pts += 1
                reasons.append(f"RSI salió de {'sobreventa' if up else 'sobrecompra'} (giro en V)")
        for col, name in (("ema_9", "EMA 9"), ("vwap", "VWAP")):
            if col in df5 and pd.notna(last.get(col)):
                prev = df5.iloc[-self.window]
                was_other_side = (prev["close"] < prev[col]) if up else (prev["close"] > prev[col])
                now_side = (price > last[col]) if up else (price < last[col])
                if was_other_side and now_side:
                    pts += 1
                    reasons.append(f"recuperó la {name}" if up else f"perdió la {name}")
        if funding is not None:
            if up and funding < 0:
                pts += 1
                reasons.append(f"funding negativo ({funding * 100:.3f}%): muchos cortos que pueden cerrar a la vez")
            elif not up and funding > 0.0005:
                pts += 1
                reasons.append(f"funding alto ({funding * 100:.3f}%): muchos largos apalancados")
        # --- Agotamiento: el squeeze aún no empezó, pero el terreno está listo ---
        d4 = df5.iloc[-48:]
        if up:
            drop = (float(d4["high"].max()) / price - 1) * 100
            drop_atr = (float(d4["high"].max()) - price) / atr if atr > 0 else 0
        else:
            drop = (price / float(d4["low"].min()) - 1) * 100
            drop_atr = (price - float(d4["low"].min())) / atr if atr > 0 else 0
        if drop >= 4 or drop_atr >= 8:
            pts += 1
            reasons.append(f"ya {'cayó' if up else 'subió'} {drop:.1f}% en 4 h: {'vender en corto' if up else 'comprar'} "
                           "ahí es llegar tarde")
        rsi_now = last.get("rsi") if "rsi" in df5 else None
        if rsi_now is not None and pd.notna(rsi_now) and ((up and rsi_now < 30) or (not up and rsi_now > 70)):
            pts += 1
            reasons.append(f"RSI {rsi_now:.0f} ({'sobreventa' if up else 'sobrecompra'}): rebote probable")
        pctb = last.get("bb_pctb") if "bb_pctb" in df5 else None
        if pctb is not None and pd.notna(pctb) and ((up and pctb < 0) or (not up and pctb > 1)):
            pts += 1
            reasons.append(f"precio fuera de la banda {'inferior' if up else 'superior'} de Bollinger")
        move = self.max_adverse_move(df5, direction)
        risk = "alto" if pts >= 3 else ("medio" if pts == 2 else "bajo")
        return SqueezeReport(direction, risk, pts, reasons, move, self.lev_cap_for(move))


def liquidation_zones(df5: pd.DataFrame, levs: tuple[int, ...] = (100, 50, 25), bars: int = 288,
                      max_dist_pct: float = 8.0) -> list[tuple[str, int, float]]:
    """
    Zonas ESTIMADAS donde se liquidarían los cortos y los largos abiertos en las últimas 24 h.
    Entrada media de cortos = precio medio ponderado por volumen de las velas rojas; de
    largos = de las velas verdes. Liquidación = entrada × (1 ± 1/apalancamiento ∓ 0.5%).
    Devuelve [(lado, apalancamiento, precio)] a menos de `max_dist_pct` del precio actual.
    """
    d = df5.iloc[-bars:]
    price = float(d["close"].iloc[-1])
    tp = (d["high"] + d["low"] + d["close"]) / 3
    out: list[tuple[str, int, float]] = []
    for side, mask in (("cortos", d["close"] < d["open"]), ("largos", d["close"] > d["open"])):
        vol = d["volume"][mask]
        if vol.sum() <= 0:
            continue
        entry = float((tp[mask] * vol).sum() / vol.sum())
        for lev in levs:
            liq = entry * (1 + 1 / lev - MAINTENANCE_MARGIN) if side == "cortos" else entry * (1 - 1 / lev + MAINTENANCE_MARGIN)
            if abs(liq / price - 1) * 100 <= max_dist_pct:
                out.append((side, lev, liq))
    return out
