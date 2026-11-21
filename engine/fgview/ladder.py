"""
engine/fgview/ladder.py
=======================
LadderPlanner: crecimiento exponencial (reto escalera) SIN liquidaciones.

* La escalera clásica mete todo el saldo con apalancamiento alto y sin un SL que
  salte antes que la liquidación: una mecha = −100 % del escalón.
* La escalera protegida limita el apalancamiento (PositionPlanner.max_safe_leverage)
  para que la liquidación quede >= 3× más lejos que el SL, aparta un % de cada
  ganancia a una caja fuerte y frena tras 2 pérdidas seguidas.
* Monte Carlo para comparar ambas con tu % de aciertos.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from engine.fgview import native
from engine.fgview.config import DEFAULT_CONFIG, FGConfig
from engine.fgview.plan import PositionPlanner
from engine.position_simulator import fmt_usd

MIN_STAKE = 5.0


@dataclass
class LadderConfig:
    capital: float
    target: float
    coin: str = "BTC"
    win_rate: float = 55.0
    vault_pct: float = 30.0
    tp_level: int = 2
    classic_leverage: float = 20.0
    max_trades: int = 100


@dataclass
class LadderStep:
    n: int
    stake: float
    leverage: int
    position: float
    win: float
    loss: float
    vault_add: float
    stake_after_win: float
    vault_total: float
    total_after_win: float


@dataclass
class LadderResult:
    cfg: LadderConfig
    tier_key: str
    sl_pct: float
    tp_pct: float
    leverage: int
    liq_pct: float
    roi_win: float
    roi_loss: float
    breakeven_wr: float
    steps: list[LadderStep]
    sim_protected: dict
    sim_classic: dict
    notes: list[str] = field(default_factory=list)


class LadderPlanner:
    def __init__(self, planner: PositionPlanner, config: FGConfig = DEFAULT_CONFIG, paths: int = 5000,
                 seed: int = 11, use_native: bool | None = None) -> None:
        self.planner, self.cfg, self.paths, self.seed = planner, config, paths, seed
        self.native = native.AVAILABLE if use_native is None else (use_native and native.AVAILABLE)
        self._core = native.core.LadderSimulator(MIN_STAKE) if self.native else None

    def economics(self, lc: LadderConfig) -> tuple[str, float, float, int, float, float, float]:
        """(nivel, SL %, TP %, apalancamiento, distancia a liquidación %, ROI si gana, ROI si pierde)."""
        tier, _ = self.planner.tiers.for_coin(lc.coin)
        sl = tier.sl / 100
        tp = tier.tp[max(0, min(2, lc.tp_level - 1))] / 100
        lev = int(min(tier.lev_max, self.planner.max_safe_leverage(sl)))
        fee = self.cfg.fee
        return tier.key, sl * 100, tp * 100, lev, (1 / lev - 0.005) * 100, (tp - 2 * fee) * lev, -(sl + 2 * fee) * lev

    def simulate(self, lc: LadderConfig, roi_win: float, roi_loss: float, protected: bool) -> dict:
        rng = np.random.default_rng(self.seed)
        wins = rng.random((self.paths, lc.max_trades)) < lc.win_rate / 100
        if self._core is not None:  # Mismos aciertos aleatorios -> mismo resultado, en C++
            return dict(self._core.simulate(wins, lc.capital, lc.target, roi_win, roi_loss, lc.vault_pct, protected))
        reached, ruined = np.zeros(self.paths, bool), np.zeros(self.paths, bool)
        trades_needed, finals = np.full(self.paths, np.nan), np.zeros(self.paths)
        vault_pct = lc.vault_pct if protected else 0.0
        for k in range(self.paths):
            stake, vault, streak = lc.capital, 0.0, 0
            for t in range(lc.max_trades):
                at_risk = stake * (0.5 if protected and streak >= 2 else 1.0)
                if wins[k, t]:
                    gain = at_risk * roi_win
                    stake += gain * (1 - vault_pct / 100)
                    vault += gain * vault_pct / 100
                    streak = 0
                else:
                    stake += at_risk * roi_loss
                    streak += 1
                if stake + vault >= lc.target:
                    reached[k], trades_needed[k] = True, t + 1
                    break
                if stake < MIN_STAKE:
                    if protected and vault >= MIN_STAKE * 2:  # Recarga con la mitad de la caja fuerte
                        stake, vault = stake + vault / 2, vault / 2
                    else:
                        ruined[k] = True
                        break
            finals[k] = stake + vault
        return {"success_pct": float(reached.mean() * 100), "ruin_pct": float(ruined.mean() * 100),
                "median_trades": float(np.nanmedian(trades_needed)) if reached.any() else float("nan"),
                "kept_if_fail": float(np.median(finals[~reached])) if (~reached).any() else float("nan")}

    def plan(self, lc: LadderConfig) -> LadderResult:
        if lc.capital <= 0 or lc.target <= lc.capital:
            raise ValueError("El objetivo debe ser mayor que el capital inicial.")
        if not 1 <= lc.win_rate <= 99:
            raise ValueError("El win rate debe estar entre 1% y 99%.")
        tier_key, sl_pct, tp_pct, lev, liq_pct, roi_win, roi_loss = self.economics(lc)
        if roi_win <= 0:
            raise ValueError("Con estas comisiones el TP no cubre costes: elige un TP más alto.")
        steps, stake, vault = [], lc.capital, 0.0
        for n in range(1, 101):
            win = stake * roi_win
            to_vault = win * lc.vault_pct / 100
            new_stake = stake + win - to_vault
            vault += to_vault
            steps.append(LadderStep(n, stake, lev, stake * lev, win, stake * roi_loss, to_vault, new_stake, vault,
                                    new_stake + vault))
            stake = new_stake
            if stake + vault >= lc.target:
                break
        breakeven = abs(roi_loss) / (roi_win + abs(roi_loss)) * 100
        notes = []
        if lc.win_rate <= breakeven:
            notes.append(f"Con {lc.win_rate:g}% de aciertos no hay ventaja: necesitas más de {breakeven:.1f}% para que "
                         f"la escalera crezca. Registra operaciones en la Bitácora para conocer tu win rate real.")
        classic_win = (tp_pct / 100 - 2 * self.cfg.fee) * lc.classic_leverage
        sim_c = self.simulate(lc, classic_win, -1.0, protected=False)
        sim_c.update(leverage=lc.classic_leverage, roi_win=classic_win)
        return LadderResult(lc, tier_key, sl_pct, tp_pct, lev, liq_pct, roi_win, roi_loss, breakeven, steps,
                            self.simulate(lc, roi_win, roi_loss, protected=True), sim_c, notes)

    @staticmethod
    def rules(r: LadderResult) -> list[str]:
        return [
            f"Cada escalón: margen AISLADO, apalancamiento máximo {r.leverage}x y Stop Loss SIEMPRE puesto antes de "
            f"entrar (a −{r.sl_pct:.2f}%). La liquidación queda a −{r.liq_pct:.1f}%: el SL salta mucho antes.",
            f"Cierra TODO en el TP (+{r.tp_pct:.2f}%). En el reto no se deja correr: escalón ganado, escalón cobrado.",
            f"Pasa el {r.cfg.vault_pct:g}% de cada ganancia a la caja fuerte (otra subcuenta o spot). Ese dinero ya no se arriesga.",
            "Tras 2 pérdidas seguidas, el siguiente escalón arriesga la mitad. Tras 3, para el día (stop diario).",
            "Nunca subas el apalancamiento para recuperar una pérdida: es la forma más rápida de liquidarte.",
            "Solo entra con semáforo 🟢 en el Modo fácil o con 4+ filtros en FG VIEW.",
        ]

    @staticmethod
    def summary(r: LadderResult) -> str:
        sp, sc = r.sim_protected, r.sim_classic
        trades = "—" if math.isnan(sp["median_trades"]) else f"{sp['median_trades']:.0f}"
        return (f"De {fmt_usd(r.cfg.capital)} a {fmt_usd(r.cfg.target)} en {len(r.steps)} escalones ganados · protegida: "
                f"{sp['success_pct']:.0f}% de éxito, {sp['ruin_pct']:.0f}% de quemar la cuenta, ~{trades} operaciones · "
                f"clásica {sc['leverage']:g}x: {sc['success_pct']:.0f}% de éxito, {sc['ruin_pct']:.0f}% quemada.")
