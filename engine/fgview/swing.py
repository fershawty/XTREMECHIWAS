"""
engine/fgview/swing.py
======================
🌙 MODO SWING / NOCTURNO: operaciones para dejar abiertas horas (hasta 12 h).

El scalping de FG VIEW dura 15–30 min con SL muy corto; dejarlo abierto de noche con
apalancamiento alto termina en liquidación. El swing cambia las reglas:

* Dirección: tendencia de 4 h (precio vs EMA 50/200) confirmada en 1 h (EMA 21/50).
  Si 4 h y 1 h no coinciden -> NO OPERAR (esperar).
* SL estructural: detrás del último máximo/mínimo de 12 h + 0.5 × ATR(1 h), mínimo
  1.5 × ATR(1 h). Así el ruido normal de la noche no lo toca.
* Apalancamiento: máximo 5x (3x por defecto) y nunca más que el que deja la
  liquidación ≥ 3× el SL y más lejos que el peor movimiento de 8 h de la última semana.
* TPs: 1.5R / 2.5R / 4R (cierres 50/30/20), SL a break-even al tocar TP1.
* Anti-squeeze en velas de 1 h y Long/Short ratio.
* Tiempo máximo: 12 h. Validación con el Time Machine en caminos de 8–12 h.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import pandas as pd

from engine.data_feeder import DataFeeder
from engine.fgview.config import DEFAULT_CONFIG, FGConfig
from engine.fgview.plan import Plan, PositionPlanner
from engine.fgview.positioning import PositioningFeed
from engine.fgview.squeeze import SqueezeGuard, SqueezeReport
from engine.indicators import compute_all
from engine.position_simulator import fmt_px, fmt_usd

SWING_MAX_LEV = 5
SWING_DEFAULT_LEV = 3
SWING_HOLD_MIN = 720  # 12 h
SWING_R = (1.5, 2.5, 4.0)


@dataclass
class SwingSetup:
    symbol: str
    direction: str | None  # None = no hay tendencia clara
    trend_4h: str
    trend_1h: str
    plan: Plan | None
    squeeze: SqueezeReport | None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def tradeable(self) -> bool:
        return self.direction is not None and self.plan is not None and not (self.squeeze and self.squeeze.risk == "alto")

    def text(self) -> str:
        if self.plan is None:
            return f"🌙 **SWING {self.symbol}: ESPERAR** — 4 h {self.trend_4h} · 1 h {self.trend_1h}. " + " · ".join(self.warnings)
        p = self.plan
        return "  \n".join([
            f"🌙 **SWING {p.coin} {p.direction}** · {p.leverage}x AISLADO · hasta 12 h",
            f"Entrada ${fmt_px(p.entry)} · SL ${fmt_px(p.stop_loss)} (−{p.sl_pct:.2f}%, estructural) · "
            f"liquidación ${fmt_px(p.liquidation)}",
            f"TP1 ${fmt_px(p.tps[0][0])} (50%) · TP2 ${fmt_px(p.tps[1][0])} (30%) · TP3 ${fmt_px(p.tps[2][0])} (20%)",
            f"Riesgo {fmt_usd(p.risk_usd)} · posición {fmt_usd(p.notional)} · margen {fmt_usd(p.margin)} · "
            f"R:R neto 1:{p.rr_net:.2f}",
        ])


class SwingPlanner:
    def __init__(self, feeder: DataFeeder, planner: PositionPlanner, config: FGConfig = DEFAULT_CONFIG,
                 positioning: PositioningFeed | None = None) -> None:
        self.feeder, self.planner, self.cfg = feeder, planner, config
        self.positioning = positioning
        # Anti-squeeze en velas de 1 h: ventana 12 h, horizonte 8 h, 7 días, caída en 48 h
        self.guard = SqueezeGuard(window=12, horizon=8, lookback=168, drop_bars=48,
                                  labels=("las últimas 12 h", "48 h", "8 h", "7 días"))

    @staticmethod
    def trend(df: pd.DataFrame, fast: str, slow: str) -> str:
        last = df.iloc[-1]
        if pd.isna(last.get(fast)) or pd.isna(last.get(slow)):
            return "sin datos"
        if last["close"] > last[fast] > last[slow]:
            return "alcista"
        if last["close"] < last[fast] < last[slow]:
            return "bajista"
        return "lateral"

    def load(self, symbol: str, market: str, exchange: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame, float | None]:
        d1 = self.feeder.get_market_data(symbol, market, "1h", exchange=exchange, limit=400)
        d4 = self.feeder.get_market_data(symbol, market, "4h", exchange=exchange, limit=300)
        return compute_all(d1.df, intraday=False), compute_all(d4.df, intraday=False), d1.funding_rate

    def build(self, symbol: str, market: str, capital: float, exchange: str | None = None,
              direction: str = "Auto", leverage: int = SWING_DEFAULT_LEV) -> SwingSetup:
        df1, df4, funding = self.load(symbol, market, exchange)
        return self.from_frames(symbol, df1, df4, capital, funding, direction, leverage, market)

    def from_frames(self, symbol: str, df1: pd.DataFrame, df4: pd.DataFrame, capital: float,
                    funding: float | None = None, direction: str = "Auto", leverage: int = SWING_DEFAULT_LEV,
                    market: str = "crypto") -> SwingSetup:
        t4, t1 = self.trend(df4, "ema_50", "ema_200"), self.trend(df1, "ema_21", "ema_50")
        warnings: list[str] = []
        if direction == "Auto":
            if t4 == "alcista" and t1 == "alcista":
                direction = "LONG"
            elif t4 == "bajista" and t1 == "bajista":
                direction = "SHORT"
            else:
                warnings.append("La tendencia de 4 h y la de 1 h no coinciden: de noche solo se opera A FAVOR de ambas.")
                return SwingSetup(symbol, None, t4, t1, None, None, [], warnings)
        elif (direction == "LONG" and "bajista" in (t4, t1)) or (direction == "SHORT" and "alcista" in (t4, t1)):
            warnings.append(f"{direction} va contra la tendencia (4 h {t4} · 1 h {t1}).")
        last = df1.iloc[-1]
        price, atr = float(last["close"]), float(last["atr"])
        d = 1 if direction == "LONG" else -1
        swing = df1.iloc[-12:]
        level = float(swing["low"].min()) if d == 1 else float(swing["high"].max())
        stop = level - d * 0.5 * atr
        dist = max(abs(price - stop), 1.5 * atr)
        stop = price - d * dist
        sl_frac = dist / price
        coin = self.planner.tiers.resolve(symbol)
        pos = self.positioning.get(coin) if (self.positioning is not None and market == "crypto") else None
        sq = self.guard.analyze(df1, direction, funding, pos)
        if sq.danger:
            warnings.append(sq.text())
        lev_safe = self.planner.max_safe_leverage(sl_frac)
        lev = max(1, min(int(leverage), SWING_MAX_LEV, lev_safe, sq.lev_cap))
        risk_pct, max_margin_pct = self.planner.budget.rule(capital)
        notional = capital * risk_pct / 100 / sl_frac
        max_margin = capital * max_margin_pct / 100
        notes = []
        if notional / lev > max_margin:
            notional = max_margin * lev
            notes.append(f"Posición recortada al margen máximo ({max_margin_pct:g}% del capital).")
        if lev < leverage:
            notes.append(f"Apalancamiento limitado a {lev}x (liquidación ≥ 3× el SL y más lejos que el peor movimiento "
                         "de 8 h de la última semana).")
        fees = notional * self.cfg.fee * 2
        tps = [(price + d * r * dist, r * sl_frac * 100, close) for r, close in zip(SWING_R, self.cfg.tp_split)]
        tp_pnl = [notional * close / 100 * pct / 100 - fees * close / 100 for _, pct, close in tps]
        sl_pnl = -notional * sl_frac - fees
        rr_net = sum(tp_pnl) / abs(sl_pnl)
        tier, listed = self.planner.tiers.for_coin(symbol)
        plan = Plan(
            coin=coin, direction=direction, capital=capital, entry=price, tier=tier, listed=listed,
            risk_pct=risk_pct, risk_usd=notional * sl_frac, max_margin_pct=max_margin_pct, sl_pct=sl_frac * 100,
            sl_source="estructura 1 h", stop_loss=stop, tps=tps, leverage=lev, margin=notional / lev,
            notional=notional, coins_qty=notional / price, liquidation=self.planner.liquidation(direction, price, lev),
            fees=fees, tp_pnl=tp_pnl, sl_pnl=sl_pnl, rr_weighted=sum(r * c / 100 for r, c in zip(SWING_R, self.cfg.tp_split)),
            rr_net=rr_net, breakeven_wr=1 / (1 + rr_net) * 100, break_even=price * (1 + d * 2 * self.cfg.fee),
            trailing=atr, options=[], notes=notes)
        reasons = [f"Tendencia 4 h {t4} y 1 h {t1}", f"SL detrás del {'mínimo' if d == 1 else 'máximo'} de 12 h "
                   f"(${fmt_px(level)}) + 0.5 × ATR"]
        if pos is not None:
            reasons.append(pos.text)
        return SwingSetup(symbol, direction, t4, t1, plan, sq, reasons, warnings)

    def time_machine_config(self) -> dict:
        """Parámetros del Time Machine para validar un swing (caminos de 8–12 h con bloques de 2–4 h)."""
        return {"horizon_min": (480, SWING_HOLD_MIN), "block_min": (120, 240)}

    @staticmethod
    def with_capital(setup: SwingSetup, capital: float) -> SwingSetup:  # pragma: no cover - utilidad
        return replace(setup, plan=replace(setup.plan, capital=capital)) if setup.plan else setup
