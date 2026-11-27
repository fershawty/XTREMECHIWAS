"""
engine/fgview/timemachine.py
============================
TIME MACHINE (Módulo 7): 100,000 simulaciones Monte Carlo del trade propuesto.

Método
------
1. Datos: últimos 90 días de velas de 1 minuto (si no hay, 5 minutos).
2. Régimen: solo se usan tramos cuyo ATR (en % del precio) está a ±20% del actual,
   para simular con una volatilidad parecida a la de ahora.
3. Bootstrap por bloques: cada simulación encadena bloques de 15–30 minutos de velas
   reales seguidas (conserva la volatilidad y las rachas) durante 15–30 minutos.
4. Cada camino se gestiona con las reglas de siempre (TP1 50% + SL a break-even,
   TP2 30% + SL a TP1, TP3 20%, salida por tiempo) y se anota qué tocó primero.

Las probabilidades son estimaciones con datos históricos, NO garantías.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import time

import numpy as np
import pandas as pd

from engine.fgview import native
from engine.fgview.plan import Plan
from engine.position_simulator import fmt_usd

PY_MAX_SIMS = 50_000  # Sin el núcleo C++ se limita para que la app no se congele


@dataclass
class TimeMachineResult:
    sims: int
    p_tp1: float
    p_tp2: float
    p_tp3: float
    p_sl: float
    p_time: float
    p_liq: float  # % de caminos que tocaron el precio de liquidación antes del TP1
    ev: float  # Expectativa por trade (USDT)
    var95: float  # Pérdida del peor 5% de los casos (USDT, positiva = pérdida)
    eligible: int  # Puntos de partida históricos con volatilidad parecida
    regime_band: float  # ±% de ATR usado (20 % normalmente)
    bars: str  # "1m" o "5m"
    days: float
    backend: str
    notes: list[str] = field(default_factory=list)
    p_win: float = 0.0  # % de caminos que terminan con ganancia
    kelly: float = 0.0  # Fracción de Kelly (riesgo óptimo por operación, en tanto por uno del capital)
    ev_r: float = 0.0  # Expectativa en unidades de riesgo (R)
    hist: list = field(default_factory=list)  # Conteo de resultados en R (13 cajones de −1.5R a +3.5R)
    sim_s: float = 0.0  # Segundos de simulación
    load_s: float = 0.0  # Segundos cargando el historial (lo rellena quien descarga)

    @property
    def verdict(self) -> str:
        if self.p_tp1 >= 60 and self.ev > 0:
            return "OPERAR"
        if self.ev <= 0 and self.p_tp1 < 50:
            return "NO OPERAR"
        return "ESPERAR"

    @property
    def verdict_icon(self) -> str:
        return {"OPERAR": "✅", "ESPERAR": "⚠️", "NO OPERAR": "❌"}[self.verdict]

    def text(self) -> str:
        return "  \n".join([
            f"⏳ **TIME MACHINE** | {self.sims:,} simulaciones".replace(",", ","),
            f"P(TP1) **{self.p_tp1:.1f}%** | P(TP2) {self.p_tp2:.1f}% | P(TP3) {self.p_tp3:.1f}% | "
            f"P(SL) {self.p_sl:.1f}% | P(salida por tiempo) {self.p_time:.1f}%",
            f"Expectativa por trade: **{fmt_usd(self.ev, True)}** | Peor 5% de casos (VaR 95%): "
            f"**−{fmt_usd(max(0.0, self.var95))}**",
            f"Veredicto: {self.verdict_icon} **{self.verdict}**",
            f"_Estimación con {self.days:.0f} días de velas de {self.bars} ({self.eligible:,} puntos de partida con "
            f"volatilidad ±{self.regime_band:.0f}% de la actual · motor {self.backend} · historial {self.load_s:.1f} s · "
            f"simulación {self.sim_s:.2f} s). No es una garantía._",
        ])

    def beginner_text(self) -> str:
        win = self.p_tp1
        return "  \n".join([
            f"Probé tu operación **{self.sims:,} veces** con el comportamiento real del precio de los últimos "
            f"{self.days:.0f} días.",
            f"- En **{win:.0f} de cada 100** veces se llegó a la primera ganancia (TP1).",
            f"- En **{self.p_sl:.0f} de cada 100** veces saltó el Stop Loss antes.",
            f"- De media ganarías/perderías **{fmt_usd(self.ev, True)}** por operación.",
            f"- En un día muy malo (el peor 5%) perderías unos **{fmt_usd(max(0.0, self.var95))}**.",
            f"**Conclusión: {self.verdict_icon} {self.verdict}.** Es una estimación, no una promesa.",
        ])


class TimeMachine:
    def __init__(self, sims: int = 100_000, regime_band: float = 20.0, seed: int = 7,
                 use_native: bool | None = None) -> None:
        self.sims = sims
        self.regime_band = regime_band
        self.seed = seed
        self.native = native.AVAILABLE if use_native is None else (use_native and native.AVAILABLE)
        self._core = native.core.TimeMachine() if self.native else None

    @property
    def backend(self) -> str:
        return "C++" if self.native else "Python"

    # ---------------------------------------------------------------- datos
    @staticmethod
    def prepare(df: pd.DataFrame, bar_minutes: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Retornos relativos al cierre anterior (high, low, close) y ATR % de 'velas de 5 min'."""
        h, lo, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
        prev = c[:-1]
        rel_h, rel_l, rel_c = h[1:] / prev - 1, lo[1:] / prev - 1, c[1:] / prev - 1
        tr = np.maximum(h[1:], prev) - np.minimum(lo[1:], prev)
        window = max(14, int(round(14 * 5 / bar_minutes)))  # ATR(14) de 5 min equivalente
        atr_pct = pd.Series(tr / prev).rolling(window, min_periods=window).mean().to_numpy() * 100
        return rel_h, rel_l, rel_c, atr_pct

    def eligible_starts(self, atr_pct: np.ndarray, max_block: int) -> tuple[np.ndarray, float, list[str]]:
        notes: list[str] = []
        current = atr_pct[~np.isnan(atr_pct)][-1] if np.any(~np.isnan(atr_pct)) else np.nan
        n = len(atr_pct)
        valid = np.arange(n) < n - max_block
        valid &= ~np.isnan(atr_pct)
        band = self.regime_band
        # ATR medido justo ANTES del inicio del bloque
        prior = np.concatenate([[np.nan], atr_pct[:-1]])
        for b in (band, band * 2, None):
            if b is None:
                idx = np.flatnonzero(valid)
                notes.append("Pocos tramos con volatilidad parecida: se usó todo el historial.")
                return idx, 100.0, notes
            ok = valid & (np.abs(prior / current - 1) <= b / 100)
            idx = np.flatnonzero(ok)
            if len(idx) >= 300:
                if b != band:
                    notes.append(f"Se amplió el filtro de volatilidad a ±{b:.0f}% (pocos tramos a ±{band:.0f}%).")
                return idx, b, notes
        return np.flatnonzero(valid), 100.0, notes  # pragma: no cover

    # ------------------------------------------------------------ simulación
    def run(self, plan: Plan, df: pd.DataFrame, bar_minutes: int = 1, sims: int | None = None,
            liq_frac: float | None = None, horizon_min: tuple[int, int] = (15, 30),
            block_min: tuple[int, int] = (15, 30)) -> TimeMachineResult:
        """`horizon_min`: duración de cada simulación (15–30 min scalping; 480–720 min swing nocturno)."""
        if len(df) < 200:
            raise ValueError("Hacen falta al menos 200 velas para el Time Machine.")
        sims = int(sims or self.sims)
        notes: list[str] = []
        if not self.native and sims > PY_MAX_SIMS:
            notes.append(f"Núcleo C++ no disponible: {PY_MAX_SIMS:,} simulaciones en lugar de {sims:,}.")
            sims = PY_MAX_SIMS
        t0 = time.perf_counter()
        rel_h, rel_l, rel_c, atr_pct = self.prepare(df, bar_minutes)
        bmin, bmax = max(1, round(block_min[0] / bar_minutes)), max(1, round(block_min[1] / bar_minutes))
        hmin, hmax = max(1, round(horizon_min[0] / bar_minutes)), max(1, round(horizon_min[1] / bar_minutes))
        if not self.native and hmax > 60 and sims > 20_000:  # Caminos largos en Python: menos simulaciones
            notes.append(f"Núcleo C++ no disponible: 20,000 simulaciones (horizonte largo) en lugar de {sims:,}.")
            sims = 20_000
        starts, band, extra = self.eligible_starts(atr_pct, bmax)
        notes += extra
        rng = np.random.default_rng(self.seed)
        n_blocks = int(np.ceil(hmax / bmin)) + 1
        block_starts = rng.choice(starts, size=(sims, n_blocks))
        block_lens = rng.integers(bmin, bmax + 1, size=(sims, n_blocks))
        horizons = rng.integers(hmin, hmax + 1, size=sims)

        s, e = plan.side, plan.entry
        params = dict(side=s, sl=s * (e - plan.stop_loss) / e, tp=[s * (t[0] - e) / e for t in plan.tps],
                      tp_pnl=list(plan.tp_pnl), sl_pnl=plan.sl_pnl, fees=plan.fees, notional=plan.notional,
                      fee=s * (plan.break_even - e) / e / 2,
                      liq=liq_frac if liq_frac is not None else abs(e - plan.liquidation) / e)
        if self._core is not None:
            pnl, tps, reason, liq = self._core.run(**params, rel_h=rel_h, rel_l=rel_l, rel_c=rel_c,
                                                   block_starts=block_starts, block_lens=block_lens, horizons=horizons)
        else:
            pnl, tps, reason, liq = self._walk_python(params, rel_h, rel_l, rel_c, block_starts, block_lens, horizons)
        tps, reason = np.asarray(tps), np.asarray(reason)
        days = (df.index[-1] - df.index[0]).total_seconds() / 86_400 if isinstance(df.index, pd.DatetimeIndex) else 0
        risk = abs(plan.sl_pnl) or 1.0
        r = np.asarray(pnl, float) / risk  # Resultado de cada camino en R (1R = pérdida si salta el SL)
        m2 = float(np.mean(r ** 2)) or 1.0
        kelly = max(0.0, float(np.mean(r)) / m2)  # Kelly continuo: E[r] / E[r²]
        hist = np.histogram(np.clip(r, -1.5, 3.499), bins=13, range=(-1.5, 3.5))[0].tolist()
        return TimeMachineResult(
            sims=sims, p_tp1=float((tps >= 1).mean() * 100), p_tp2=float((tps >= 2).mean() * 100),
            p_tp3=float((tps >= 3).mean() * 100), p_sl=float(((tps == 0) & (reason == 0)).mean() * 100),
            p_time=float(((tps == 0) & (reason == 4)).mean() * 100), p_liq=float(np.asarray(liq).mean() * 100),
            ev=float(np.mean(pnl)), var95=float(-np.percentile(pnl, 5)), eligible=len(starts), regime_band=band,
            bars=f"{bar_minutes}m", days=days, backend=self.backend, notes=notes,
            p_win=float((np.asarray(pnl) > 0).mean() * 100), kelly=kelly, ev_r=float(np.mean(r)), hist=hist,
            sim_s=time.perf_counter() - t0)

    def fan(self, plan: Plan, df: pd.DataFrame, bar_minutes: int = 1, n: int = 70,
            horizon_min: tuple[int, int] = (15, 30), seed: int = 3) -> dict:
        """
        Caminos de ejemplo para dibujar el «abanico de futuros» (mismo método del Time Machine:
        bloques reales con volatilidad parecida). Devuelve precios y cómo terminó cada camino.
        """
        rel_h, rel_l, rel_c, atr_pct = self.prepare(df, bar_minutes)
        bmin, bmax = max(1, round(15 / bar_minutes)), max(1, round(30 / bar_minutes))
        hmax = max(1, round(horizon_min[1] / bar_minutes))
        starts, _, _ = self.eligible_starts(atr_pct, bmax)
        rng = np.random.default_rng(seed)
        s, e = plan.side, plan.entry
        tp = [t[0] for t in plan.tps]
        paths, outcomes = [], []
        for _ in range(n):
            p, path, out, k = e, [e], "tiempo", 0
            while len(path) <= hmax:
                i0, L = int(rng.choice(starts)), int(rng.integers(bmin, bmax + 1))
                for i in range(i0, min(i0 + L, len(rel_c))):
                    hi, lo = p * (1 + rel_h[i]), p * (1 + rel_l[i])
                    p *= 1 + rel_c[i]
                    path.append(p)
                    if out == "tiempo":
                        if (lo <= plan.stop_loss) if s == 1 else (hi >= plan.stop_loss):
                            out = "sl" if k == 0 else f"tp{k}"
                        elif (hi >= tp[k]) if s == 1 else (lo <= tp[k]):
                            k += 1
                            if k == 3:
                                out = "tp3"
                    if len(path) > hmax:
                        break
            if out == "tiempo" and k > 0:
                out = f"tp{k}"
            paths.append([round(x, 8) for x in path])
            outcomes.append(out)
        return {"entry": e, "stop": plan.stop_loss, "tps": tp, "paths": paths, "outcomes": outcomes,
                "bar_minutes": bar_minutes}

    @staticmethod
    def _walk_python(p: dict, rh, rl, rc, bs, bl, hz):
        """Misma lógica que fgcore.TimeMachine (respaldo sin C++)."""
        side, s = p["side"], float(p["side"])
        tp_px = [1 + s * t for t in p["tp"]]
        stops = [1 - s * p["sl"], 1 + s * 2 * p["fee"], tp_px[0]]
        remaining = (1.0, 0.5, 0.2)
        liq_px = 1 - s * p["liq"]
        n, nb = len(rc), bs.shape[1]
        sims = bs.shape[0]
        pnl_o, tps_o, rea_o, liq_o = np.zeros(sims), np.zeros(sims, int), np.zeros(sims, int), np.zeros(sims, int)
        for k in range(sims):
            price, pnl, stage, reason, tps, liq_hit, done = 1.0, 0.0, 0, 4, 0, 0, False
            b = off = 0
            for _ in range(int(hz[k])):
                if off >= bl[k, b]:
                    if b + 1 < nb:
                        b += 1
                    off = 0
                i = int(bs[k, b]) + off
                off += 1
                if i < 0 or i >= n:
                    i = n - 1
                hi, lo = price * (1 + rh[i]), price * (1 + rl[i])
                price *= 1 + rc[i]
                if stage == 0 and (lo <= liq_px if side == 1 else hi >= liq_px):
                    liq_hit = 1
                if (lo <= stops[stage]) if side == 1 else (hi >= stops[stage]):
                    if stage == 0:
                        pnl += p["sl_pnl"]
                        reason, tps = 0, 0
                    elif stage == 1:
                        reason, tps = 1, 1
                    else:
                        pnl += p["notional"] * 0.2 * p["tp"][0] - p["fees"] * 0.2
                        reason, tps = 2, 2
                    done = True
                    break
                if (hi >= tp_px[stage]) if side == 1 else (lo <= tp_px[stage]):
                    pnl += p["tp_pnl"][stage]
                    stage += 1
                    if stage == 3:
                        reason, tps, done = 3, 3, True
                        break
            if not done:
                rem = remaining[stage]
                pnl += p["notional"] * rem * s * (price - 1.0) - p["fees"] * rem
                tps, reason = stage, 4
            pnl_o[k], tps_o[k], rea_o[k], liq_o[k] = pnl, tps, reason, liq_hit
        return pnl_o, tps_o, rea_o, liq_o
