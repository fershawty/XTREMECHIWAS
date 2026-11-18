"""
engine/fgview/recommender.py
============================
ScalpRecommender: une datos + filtros + plan + estadística en una recomendación.
Recibe sus dependencias por inyección (fácil de probar y de cambiar piezas).
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.fgview.config import DEFAULT_CONFIG, FGConfig
from engine.fgview.filters import FilterReport, FilterSet
from engine.fgview.journal import DailyGuard, TradeJournal
from engine.fgview.plan import Plan, PositionPlanner
from engine.fgview.snapshot import MarketSnapshot, SnapshotLoader
from engine.fgview.stats import OutcomeStats, SetupStatistics
from engine.position_simulator import fmt_pct, fmt_px, fmt_usd

LIGHTS = ("verde", "amarillo", "rojo")


@dataclass
class Recommendation:
    symbol: str
    snapshot: MarketSnapshot
    plan: Plan
    report: FilterReport
    stats: OutcomeStats
    light: str  # verde = entrar · amarillo = esperar · rojo = no operar

    @property
    def coin(self) -> str:
        return self.plan.coin

    @property
    def ev(self) -> float:
        return self.stats.ev

    @property
    def reasons(self) -> list[str]:
        return [c.plain for c in self.report.checks if c.passed]


class ScalpRecommender:
    def __init__(self, loader: SnapshotLoader, filters: FilterSet, planner: PositionPlanner, stats: SetupStatistics,
                 journal: TradeJournal | None = None, guard: DailyGuard | None = None, news=None,
                 config: FGConfig = DEFAULT_CONFIG) -> None:
        self.loader, self.filters, self.planner, self.stats = loader, filters, planner, stats
        self.journal, self.guard, self.news, self.cfg = journal, guard, news, config

    # ----------------------------------------------------------------- núcleo
    def external_blocks(self, capital: float) -> list[str]:
        """Noticias de alto impacto + stop diario de la bitácora."""
        out: list[str] = []
        if self.news is not None:
            status = self.news.check()
            if status.blocked:
                out.append(status.message)
        if self.journal is not None and self.guard is not None:
            msg = self.guard.check(self.journal.today(), capital)
            if msg:
                out.append(msg)
        return out

    def classify(self, report: FilterReport, plan: Plan, stats: OutcomeStats) -> str:
        if report.blocks:
            return "rojo"
        if report.enter and plan.rr_net >= self.cfg.min_rr_net and stats.ev > 0:
            return "verde"
        return "amarillo"

    def from_snapshot(self, snap: MarketSnapshot, capital: float, direction: str = "Auto",
                      blocks: list[str] | None = None) -> Recommendation:
        symbol = snap.symbol
        report = (self.filters.best(snap, symbol, blocks) if direction == "Auto"
                  else self.filters.evaluate(snap, direction, symbol, blocks))
        plan = self.planner.plan(capital, symbol, report.direction, snap.price, snap.atr5)
        stats = self.stats.evaluate(plan, snap.df5)
        return Recommendation(symbol, snap, plan, report, stats, self.classify(report, plan, stats))

    def recommend(self, symbol: str, market: str, capital: float, direction: str = "Auto", contract: str = "USDT-M",
                  exchange: str | None = None, force: bool = False) -> Recommendation:
        snap = self.loader.load(symbol, market, contract, exchange, force=force)
        return self.from_snapshot(snap, capital, direction, self.external_blocks(capital))

    # ---------------------------------------------------------------- textos
    def format(self, rec: Recommendation) -> str:
        p, rep, st = rec.plan, rec.report, rec.stats
        tp = p.tps
        lines = [
            f"🟡 **FG VIEW | {p.coin} {p.direction}** | Confianza: **{rep.score}/100 ({rep.confidence})** · "
            f"{self.planner.tiers.label(rec.symbol)}",
            f"Capital {fmt_usd(p.capital)} | Riesgo {fmt_usd(p.risk_usd)} ({p.risk_usd / p.capital * 100:.2f}%) | "
            f"{p.leverage}x | Margen {fmt_usd(p.margin)} | Posición {fmt_usd(p.notional)}",
            f"Entrada ${fmt_px(p.entry)} | SL ${fmt_px(p.stop_loss)} (−{p.sl_pct:.2f}%, {p.sl_source}) | "
            f"Liquidación ${fmt_px(p.liquidation)}",
            f"TP1 ${fmt_px(tp[0][0])} ({fmt_pct(tp[0][1])}) → {fmt_usd(p.tp_pnl[0], True)} | "
            f"TP2 ${fmt_px(tp[1][0])} → {fmt_usd(p.tp_pnl[1], True)} | TP3 ${fmt_px(tp[2][0])} → {fmt_usd(p.tp_pnl[2], True)}",
            f"Pérdida máxima {fmt_usd(p.sl_pnl, True)} | R:R 1:{p.rr_weighted:.2f} (neto 1:{p.rr_net:.2f}) | "
            f"Win rate de equilibrio {p.breakeven_wr:.1f}% | Tiempo máximo {self.cfg.max_hold_min} minutos",
            f"Historial ({st.samples} {'casos parecidos' if st.similar else 'casos'}): TP1 {st.reach[0]:.0f}% · "
            f"SL primero {st.sl_first:.0f}% · valor esperado {fmt_usd(st.ev, True)}",
            f"Filtros: {rep.icons} ({rep.passed}/{len(rep.checks)})",
        ]
        if rep.blocks:
            lines.append("⛔ **NO OPERAR:** " + " · ".join(rep.blocks))
        elif not rep.enter:
            lines.append(f"⏸️ **Esperar:** solo {rep.passed} de {len(rep.checks)} filtros (mínimo {rep.min_filters}).")
        elif p.rr_net < self.cfg.min_rr_net:
            lines.append(f"⏸️ **Esperar:** R:R neto 1:{p.rr_net:.2f} < 1:{self.cfg.min_rr_net:g} (las comisiones no compensan).")
        elif st.ev <= 0:
            lines.append("⏸️ **Esperar:** en situaciones parecidas este plan perdió dinero de media.")
        else:
            lines.append(f"✅ **Entrada válida** ({rep.passed} de {len(rep.checks)} filtros, valor esperado positivo).")
        lines += [f"_{n}_" for n in p.notes]
        lines.append("_Simulación educativa, no es asesoría financiera._")
        return "  \n".join(lines)

    def management_text(self, plan: Plan) -> str:
        trail = f"trailing de {fmt_px(plan.trailing)} (0.5 × ATR 5m)" if plan.trailing else "trailing de 0.5 × ATR 5m"
        tp1, tp2 = fmt_px(plan.tps[0][0]), fmt_px(plan.tps[1][0])
        return "  \n".join([
            "🛠️ **Gestión de la posición**",
            f"1. **TP1** ${tp1}: cierra el 50% y mueve el SL a break-even (${fmt_px(plan.break_even)}).",
            f"2. **TP2** ${tp2}: cierra el 30% y mueve el SL a TP1 (${tp1}).",
            f"3. **TP3** ${fmt_px(plan.tps[2][0])}: cierra el 20% restante o déjalo correr con {trail}.",
            f"4. **Tiempo**: si en {self.cfg.max_hold_min} minutos no tocó el TP1, cierra a mercado.",
            f"5. **Stop diario**: {self.cfg.daily_max_losses} pérdidas seguidas o −{self.cfg.daily_max_loss_pct:g}% "
            f"del capital → no operes más hoy.",
        ])
