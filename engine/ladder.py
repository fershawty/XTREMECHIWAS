"""
engine/ladder.py
================
Reto escalera (crecimiento exponencial) SIN liquidaciones.

Por qué la escalera clásica liquida
-----------------------------------
En la escalera clásica se mete TODO el saldo en cada escalón con apalancamiento
alto y sin un Stop Loss que salte antes que la liquidación. Una sola mecha en
contra = −100 % del escalón. Con 10 escalones y 80 % de aciertos solo se completa
el reto el 0.8^10 = 10.7 % de las veces.

Escalera protegida FG
---------------------
1. Apalancamiento limitado para que la liquidación quede ≥ 3× más lejos que el SL:
   perder un escalón cuesta (SL + comisiones) × apalancamiento (≈ −8 %), nunca el 100 %.
2. Caja fuerte: tras cada escalón ganado se guarda un % de la ganancia que ya no se
   arriesga. Aunque falles después, lo guardado se queda.
3. Freno: tras 2 pérdidas seguidas se baja medio escalón (se arriesga la mitad).
4. Probabilidades con Monte Carlo para comparar clásica vs protegida con tu win rate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from engine.position_simulator import fmt_usd
from engine.recommender import FEE, TIERS, max_safe_leverage, tier_for

MIN_STAKE = 5.0  # Por debajo de esto ya no se puede operar (mínimos del exchange)


@dataclass
class LadderConfig:
    """Parámetros del reto."""

    capital: float  # Capital inicial del reto
    target: float  # Objetivo final
    coin: str = "BTC"
    win_rate: float = 55.0  # % estimado (ideal: el de tu Bitácora)
    vault_pct: float = 30.0  # % de cada ganancia que va a la caja fuerte
    tp_level: int = 2  # Salida de cada escalón: TP1, TP2 o TP3 del nivel
    classic_leverage: float = 20.0  # Apalancamiento de la escalera clásica (para comparar)
    max_trades: int = 60
    fee: float = FEE


@dataclass
class LadderStep:
    n: int
    stake: float  # Margen del escalón
    leverage: int
    position: float
    win: float  # Ganancia si toca el TP
    loss: float  # Pérdida si toca el SL (negativa)
    vault_add: float  # Lo que entra a la caja fuerte si gana
    stake_after_win: float
    vault_total: float
    total_after_win: float  # Escalera + caja fuerte


@dataclass
class LadderResult:
    cfg: LadderConfig
    tier_key: str
    sl_pct: float
    tp_pct: float
    leverage: int
    liq_pct: float  # Distancia a la liquidación (% del precio)
    roi_win: float  # ROI del escalón si gana (fracción)
    roi_loss: float  # ROI del escalón si pierde (fracción, negativa)
    breakeven_wr: float
    steps: list[LadderStep]
    sim_protected: dict
    sim_classic: dict
    notes: list[str] = field(default_factory=list)


def _economics(cfg: LadderConfig) -> tuple[str, float, float, int, float, float, float]:
    """(nivel, SL %, TP %, apalancamiento, distancia liq %, ROI ganar, ROI perder)."""
    tier, _ = tier_for(cfg.coin)
    sl = tier.sl / 100
    tp = tier.tp[max(0, min(2, cfg.tp_level - 1))] / 100
    lev = int(min(tier.lev_max, max_safe_leverage(sl)))
    liq = 1 / lev - 0.005
    roi_win = (tp - 2 * cfg.fee) * lev
    roi_loss = -(sl + 2 * cfg.fee) * lev
    return tier.key, sl * 100, tp * 100, lev, liq * 100, roi_win, roi_loss


def _simulate(start: float, target: float, p: float, roi_win: float, roi_loss: float, vault_pct: float,
              max_trades: int, protected: bool, paths: int = 5000, seed: int = 11) -> dict:
    """Monte Carlo del reto: % que llega al objetivo, % que quema la cuenta y operaciones medianas."""
    rng = np.random.default_rng(seed)
    wins = rng.random((paths, max_trades)) < p
    reached = np.zeros(paths, bool)
    ruined = np.zeros(paths, bool)
    trades_needed = np.full(paths, np.nan)
    finals = np.zeros(paths)
    for k in range(paths):
        stake, vault, streak = start, 0.0, 0
        for t in range(max_trades):
            risk_mult = 0.5 if (protected and streak >= 2) else 1.0
            at_risk = stake * risk_mult
            if wins[k, t]:
                gain = at_risk * roi_win
                to_vault = gain * vault_pct / 100 if protected else 0.0
                stake += gain - to_vault
                vault += to_vault
                streak = 0
            else:
                stake += at_risk * roi_loss
                streak += 1
            if stake + vault >= target:
                reached[k], trades_needed[k] = True, t + 1
                break
            if stake < MIN_STAKE:
                if protected and vault >= MIN_STAKE * 2:  # Se recarga con la mitad de la caja fuerte
                    refill = vault / 2
                    vault -= refill
                    stake += refill
                else:
                    ruined[k] = True
                    break
        finals[k] = stake + vault
    return {
        "success_pct": float(reached.mean() * 100),
        "ruin_pct": float(ruined.mean() * 100),
        "median_trades": float(np.nanmedian(trades_needed)) if reached.any() else float("nan"),
        "median_final": float(np.median(finals)),
        "kept_if_fail": float(np.median(finals[~reached])) if (~reached).any() else float("nan"),
    }


def plan_ladder(cfg: LadderConfig) -> LadderResult:
    """Plan escalón por escalón + comparación clásica vs protegida."""
    if cfg.capital <= 0 or cfg.target <= cfg.capital:
        raise ValueError("El objetivo debe ser mayor que el capital inicial.")
    if not 1 <= cfg.win_rate <= 99:
        raise ValueError("El win rate debe estar entre 1% y 99%.")
    tier_key, sl_pct, tp_pct, lev, liq_pct, roi_win, roi_loss = _economics(cfg)
    notes: list[str] = []
    if roi_win <= 0:
        raise ValueError("Con estas comisiones el TP no cubre costes: elige un TP más alto.")

    steps: list[LadderStep] = []
    stake, vault = cfg.capital, 0.0
    for n in range(1, 101):
        win = stake * roi_win
        to_vault = win * cfg.vault_pct / 100
        loss = stake * roi_loss
        new_stake = stake + win - to_vault
        vault += to_vault
        steps.append(LadderStep(n, stake, lev, stake * lev, win, loss, to_vault, new_stake, vault, new_stake + vault))
        stake = new_stake
        if stake + vault >= cfg.target:
            break

    p = cfg.win_rate / 100
    breakeven = abs(roi_loss) / (roi_win + abs(roi_loss)) * 100
    if cfg.win_rate <= breakeven:
        notes.append(f"Con {cfg.win_rate:g}% de aciertos no hay ventaja: necesitas más de {breakeven:.1f}% para que "
                     f"la escalera crezca. Registra operaciones en la Bitácora para conocer tu win rate real.")
    classic_win = (tp_pct / 100 - 2 * cfg.fee) * cfg.classic_leverage
    sim_p = _simulate(cfg.capital, cfg.target, p, roi_win, roi_loss, cfg.vault_pct, cfg.max_trades, True)
    sim_c = _simulate(cfg.capital, cfg.target, p, classic_win, -1.0, 0.0, cfg.max_trades, False)
    sim_c["leverage"] = cfg.classic_leverage
    sim_c["roi_win"] = classic_win
    return LadderResult(cfg, tier_key, sl_pct, tp_pct, lev, liq_pct, roi_win, roi_loss, breakeven, steps,
                        sim_p, sim_c, notes)


def ladder_rules(r: LadderResult) -> list[str]:
    """Reglas de oro de la escalera protegida."""
    return [
        f"Cada escalón: margen AISLADO, apalancamiento máximo {r.leverage}x y Stop Loss SIEMPRE puesto antes de entrar "
        f"(a −{r.sl_pct:.2f}%). La liquidación queda a −{r.liq_pct:.1f}%: el SL salta mucho antes.",
        f"Cierra TODO en el TP (+{r.tp_pct:.2f}%). En el reto no se deja correr: escalón ganado, escalón cobrado.",
        f"Pasa el {r.cfg.vault_pct:g}% de cada ganancia a la caja fuerte (otra subcuenta o spot). Ese dinero ya no se arriesga.",
        "Tras 2 pérdidas seguidas, el siguiente escalón arriesga la mitad. Tras 3, para el día (stop diario).",
        "Nunca subas el apalancamiento para recuperar una pérdida: es la forma más rápida de liquidarte.",
        "Solo entra con semáforo 🟢 en el Modo fácil o con 4+ filtros en FG VIEW.",
    ]


def steps_needed(r: LadderResult) -> int:
    return len(r.steps)


def summary_text(r: LadderResult) -> str:
    sp, sc = r.sim_protected, r.sim_classic
    trades = "—" if math.isnan(sp["median_trades"]) else f"{sp['median_trades']:.0f}"
    return (f"De {fmt_usd(r.cfg.capital)} a {fmt_usd(r.cfg.target)} en {steps_needed(r)} escalones ganados · "
            f"protegida: {sp['success_pct']:.0f}% de éxito, {sp['ruin_pct']:.0f}% de quemar la cuenta, "
            f"~{trades} operaciones · clásica {sc['leverage']:g}x: {sc['success_pct']:.0f}% de éxito, "
            f"{sc['ruin_pct']:.0f}% de quemar la cuenta.")


__all__ = ["LadderConfig", "LadderResult", "plan_ladder", "ladder_rules", "summary_text", "TIERS"]
