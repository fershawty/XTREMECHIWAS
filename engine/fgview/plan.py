"""
engine/fgview/plan.py
=====================
PositionPlanner: convierte capital + cripto + dirección + precio en un plan completo
(SL, TP1/TP2/TP3, apalancamiento, margen, liquidación, R:R bruto y neto).

Reglas:
* Posición = (capital × riesgo %) ÷ distancia al SL.
* SL dinámico = 1.2 × ATR(14) de 5 min, nunca menor que el del nivel. Si el ATR lo
  amplía, los TPs se escalan en la misma proporción para conservar el R:R.
* Apalancamiento: el mínimo del rango del nivel que respete el margen máximo, sin
  superar el que deja la liquidación >= 3× más lejos que el SL.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import pandas as pd

from engine.fgview.config import DEFAULT_CONFIG, FGConfig
from engine.fgview.policy import BudgetPolicy, Tier, TierCatalog
from engine.position_simulator import MAINTENANCE_MARGIN, fmt_px, fmt_usd


@dataclass
class LeverageOption:
    leverage: int
    margin: float
    roi_tp1: float
    roi_tp3: float
    roi_sl: float
    liquidation: float
    liq_ok: bool  # Liquidación >= 3× la distancia del SL
    margin_ok: bool  # Margen dentro del tope del presupuesto
    recommended: bool = False


@dataclass
class Plan:
    """Plan de operación. Los PnL están en USDT y ya descuentan comisiones."""

    coin: str
    direction: str
    capital: float
    entry: float
    tier: Tier
    listed: bool
    risk_pct: float
    risk_usd: float
    max_margin_pct: float
    sl_pct: float
    sl_source: str  # "ATR" | "tabla"
    stop_loss: float
    tps: list[tuple[float, float, float]]  # (precio, % movimiento, % cierre)
    leverage: int
    margin: float
    notional: float
    coins_qty: float
    liquidation: float
    fees: float
    tp_pnl: list[float]  # PnL neto de la parte que cierra en cada TP
    sl_pnl: float  # PnL neto si salta el SL con toda la posición
    rr_weighted: float  # R:R ponderado bruto: Σ(TPi × %cierre) ÷ SL
    rr_net: float  # R:R real descontando comisiones
    breakeven_wr: float  # Win rate de equilibrio con comisiones
    break_even: float
    trailing: float | None
    options: list[LeverageOption] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def side(self) -> int:
        return 1 if self.direction == "LONG" else -1

    @property
    def total_tp_pnl(self) -> float:
        return sum(self.tp_pnl)

    def rebase(self, entry: float) -> "Plan":
        """
        Mismo plan (mismas distancias % y mismo tamaño) con otra entrada. Sirve para
        medir cómo habría salido ESTE plan en situaciones pasadas.
        """
        r = entry / self.entry
        return replace(self, entry=entry, stop_loss=self.stop_loss * r,
                       tps=[(p * r, pct, close) for p, pct, close in self.tps], coins_qty=self.notional / entry,
                       liquidation=self.liquidation * r, break_even=self.break_even * r,
                       trailing=self.trailing * r if self.trailing else None, options=[], notes=[])


class PositionPlanner:
    """Calcula el plan de cada operación según nivel, presupuesto y volatilidad."""

    def __init__(self, tiers: TierCatalog | None = None, budget: BudgetPolicy | None = None,
                 config: FGConfig = DEFAULT_CONFIG) -> None:
        self.tiers = tiers or TierCatalog()
        self.budget = budget or BudgetPolicy()
        self.cfg = config

    # ------------------------------------------------------------- utilidades
    @staticmethod
    def liquidation(direction: str, entry: float, leverage: float) -> float:
        """Liquidación aproximada en margen aislado (mantenimiento 0.5 %)."""
        if direction == "LONG":
            return entry * (1 - 1 / leverage + MAINTENANCE_MARGIN)
        return entry * (1 + 1 / leverage - MAINTENANCE_MARGIN)

    def max_safe_leverage(self, sl_frac: float) -> int:
        """Apalancamiento máximo para que la liquidación quede >= liq_safety × la distancia del SL."""
        return max(1, math.floor(1.0 / (self.cfg.liq_safety * sl_frac + MAINTENANCE_MARGIN)))

    # ------------------------------------------------------------------- plan
    def plan(self, capital: float, symbol: str, direction: str, entry: float, atr5: float | None = None,
             lev_cap: int | None = None, risk_pct: float | None = None, risk_max: float | None = None,
             margin_max_pct: float | None = None) -> Plan:
        """`lev_cap`: tope extra de apalancamiento (squeeze). `risk_pct`: riesgo pedido (Kelly), nunca mayor que la tabla."""
        if capital <= 0 or entry <= 0 or direction not in ("LONG", "SHORT"):
            raise ValueError("Capital, entrada y dirección (LONG/SHORT) son obligatorios.")
        cfg = self.cfg
        tier, listed = self.tiers.for_coin(symbol)
        coin = self.tiers.resolve(symbol)
        table_risk, max_margin_pct = self.budget.rule(capital)
        cap_risk = table_risk if risk_max is None else risk_max  # La escalera puede subir el tope en micro-capital
        if margin_max_pct is not None:
            max_margin_pct = margin_max_pct
        risk_pct = table_risk if risk_pct is None else max(0.05, min(cap_risk, risk_pct))
        notes: list[str] = []
        if not listed:
            notes.append(f"{coin} no está en los niveles A/B/C: se trata como Nivel {tier.key} (máxima prudencia).")

        sl_frac, sl_source = tier.sl / 100.0, "tabla"
        if atr5 and atr5 > 0 and cfg.atr_sl_mult * atr5 / entry > sl_frac:
            sl_frac, sl_source = cfg.atr_sl_mult * atr5 / entry, "ATR"
        d = 1 if direction == "LONG" else -1
        stop = entry * (1 - d * sl_frac)

        notional = capital * risk_pct / 100.0 / sl_frac
        max_margin = capital * max_margin_pct / 100.0
        lev_safe = self.max_safe_leverage(sl_frac)
        lev_hi = min(tier.lev_max, lev_safe, lev_cap or tier.lev_max)
        if lev_cap and lev_cap < min(tier.lev_max, lev_safe):
            notes.append(f"Subidas/bajadas bruscas recientes: apalancamiento máximo {lev_cap}x para que la "
                         "liquidación quede más lejos que el peor movimiento de 30 min de las últimas 24 h.")
        lev_lo = min(tier.lev_min, lev_hi)
        if lev_hi < tier.lev_min:
            notes.append(f"El SL es amplio: el apalancamiento baja a {lev_hi}x para que la liquidación quede "
                         f"≥ {cfg.liq_safety:g}× más lejos que el SL.")
        lev = int(min(max(math.ceil(notional / max_margin) if max_margin > 0 else lev_hi, lev_lo), lev_hi))
        if notional / lev > max_margin + 1e-9:
            new_notional = max_margin * lev
            notes.append(f"La posición se recorta de {fmt_usd(notional)} a {fmt_usd(new_notional)} para respetar el "
                         f"margen máximo ({max_margin_pct:g}% = {fmt_usd(max_margin)}); el riesgo real baja a "
                         f"{fmt_usd(new_notional * sl_frac)}.")
            notional = new_notional
        margin = notional / lev
        fees = notional * cfg.fee * 2

        scale = sl_frac / (tier.sl / 100.0)
        if scale > 1.0 + 1e-9:
            notes.append(f"Volatilidad alta: SL por ATR ({sl_frac * 100:.2f}%) → TPs escalados ×{scale:.2f} para "
                         f"mantener el R:R del Nivel {tier.key}.")
        tps = [(entry * (1 + d * pct * max(1.0, scale) / 100.0), pct * max(1.0, scale), close)
               for pct, close in zip(tier.tp, cfg.tp_split)]
        tp_pnl = [notional * (close / 100.0) * (pct / 100.0) - fees * close / 100.0 for _, pct, close in tps]
        sl_pnl = -notional * sl_frac - fees
        rr_weighted = sum(pct * close / 100.0 for _, pct, close in tps) / (sl_frac * 100.0)
        rr_net = sum(tp_pnl) / abs(sl_pnl) if sl_pnl < 0 else float("inf")
        fee_share = fees / (notional * sl_frac) * 100
        if fee_share > 20:
            notes.append(f"Las comisiones taker ({fmt_usd(fees)}) son el {fee_share:.0f}% del riesgo: el R:R neto baja a "
                         f"1:{rr_net:.2f}. Entra con orden LÍMITE (maker ~0.02%) para recuperar casi todo ese margen.")

        options = []
        for L in sorted({tier.lev_min, (tier.lev_min + tier.lev_max) // 2, tier.lev_max, lev}):
            m = notional / L
            liq_L = self.liquidation(direction, entry, L)
            options.append(LeverageOption(
                leverage=L, margin=m, roi_tp1=(notional * tps[0][1] / 100 - fees) / m * 100,
                roi_tp3=(notional * tps[2][1] / 100 - fees) / m * 100, roi_sl=sl_pnl / m * 100, liquidation=liq_L,
                liq_ok=abs(entry - liq_L) / entry >= cfg.liq_safety * sl_frac - 1e-12,
                margin_ok=m <= max_margin + 1e-9, recommended=(L == lev)))

        return Plan(
            coin=coin, direction=direction, capital=capital, entry=entry, tier=tier, listed=listed,
            risk_pct=risk_pct, risk_usd=notional * sl_frac, max_margin_pct=max_margin_pct, sl_pct=sl_frac * 100,
            sl_source=sl_source, stop_loss=stop, tps=tps, leverage=lev, margin=margin, notional=notional,
            coins_qty=notional / entry, liquidation=self.liquidation(direction, entry, lev), fees=fees, tp_pnl=tp_pnl,
            sl_pnl=sl_pnl, rr_weighted=rr_weighted, rr_net=rr_net,
            breakeven_wr=1 / (1 + rr_net) * 100 if rr_net > 0 else 100.0, break_even=entry * (1 + d * 2 * cfg.fee),
            trailing=cfg.atr_trail_mult * atr5 if atr5 and atr5 > 0 else None, options=options, notes=notes,
        )

    # ------------------------------------------------------------------ tabla
    @staticmethod
    def options_table(plan: Plan) -> pd.DataFrame:
        rows = []
        for o in plan.options:
            status = "⭐ recomendado" if o.recommended else ("✅" if o.liq_ok and o.margin_ok else
                                                             ("⚠️ liquidación cerca" if not o.liq_ok else "⚠️ margen alto"))
            rows.append({
                "Apalanc.": f"{o.leverage}x", "Margen": fmt_usd(o.margin), "Posición": fmt_usd(plan.notional),
                "SL": f"${fmt_px(plan.stop_loss)}", "TP1": f"${fmt_px(plan.tps[0][0])}",
                "TP2": f"${fmt_px(plan.tps[1][0])}", "TP3": f"${fmt_px(plan.tps[2][0])}",
                "Ganancia TP3 total": fmt_usd(plan.total_tp_pnl, True), "Pérdida SL": fmt_usd(plan.sl_pnl, True),
                "ROI TP1": f"{o.roi_tp1:+.1f}%", "ROI SL": f"{o.roi_sl:+.1f}%", "Liquidación": f"${fmt_px(o.liquidation)}",
                "Estado": status,
            })
        return pd.DataFrame(rows)
