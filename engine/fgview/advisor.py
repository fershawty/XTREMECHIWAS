"""
engine/fgview/advisor.py
========================
EasyModeAdvisor: el "Modo fácil". Revisa varias criptos en paralelo, elige la más
recomendada y la explica en lenguaje sencillo. También calcula el ROI objetivo:
qué movimiento y apalancamiento hacen falta para un 10/15/20 % y su probabilidad.
"""

from __future__ import annotations

import pandas as pd

from engine.fgview.config import DEFAULT_CONFIG, FGConfig
from engine.fgview.manager import TradeManager
from engine.fgview.recommender import Recommendation, ScalpRecommender
from engine.position_simulator import fmt_px, fmt_usd

EASY_COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "LINK", "AVAX"]


class EasyModeAdvisor:
    def __init__(self, recommender: ScalpRecommender, manager: TradeManager, config: FGConfig = DEFAULT_CONFIG,
                 coins: list[str] | None = None) -> None:
        self.rec = recommender
        self.manager = manager
        self.cfg = config
        self.coins = coins or EASY_COINS

    def scan(self, capital: float, market: str, coins: list[str] | None = None, contract: str = "USDT-M",
             exchange: str | None = None, force: bool = False) -> tuple[list[Recommendation], list[str]]:
        """Descarga en paralelo, evalúa y ordena: primero verdes, luego por valor esperado y confianza."""
        coins = coins or self.coins
        snaps = self.rec.loader.load_many(coins, market, contract, exchange, force=force)
        blocks = self.rec.external_blocks(capital)
        out, errors = [], []
        for coin in coins:
            snap = snaps[coin]
            if isinstance(snap, Exception):
                errors.append(f"{coin}: {snap}")
                continue
            try:
                out.append(self.rec.from_snapshot(snap, capital, "Auto", blocks))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{coin}: {exc}")
        rank = {"verde": 0, "amarillo": 1, "rojo": 2}
        out.sort(key=lambda r: (rank[r.light], -r.ev, -r.report.score))
        return out, errors

    # ------------------------------------------------------------ ROI objetivo
    def roi_options(self, r: Recommendation, targets: tuple[float, ...] = (10.0, 15.0, 20.0)) -> pd.DataFrame:
        """
        Para cada ROI objetivo y apalancamiento del nivel: movimiento necesario y % de veces
        que en situaciones parecidas se alcanzó antes que el SL en 30 minutos.
        """
        p, df5 = r.plan, r.snapshot.df5
        h, lo, c = (df5[k].to_numpy(float) for k in ("high", "low", "close"))
        mask = self.rec.filters.similar_mask(df5, p.direction) if r.stats.similar else None
        safe = self.rec.planner.max_safe_leverage(p.sl_pct / 100)
        levs = sorted({lv for lv in (p.tier.lev_min, (p.tier.lev_min + p.tier.lev_max) // 2, p.tier.lev_max) if lv <= safe}
                      or {min(p.tier.lev_min, safe)})
        rows = []
        for roi in targets:
            for lev in levs:
                move = roi / lev + 2 * self.cfg.fee * 100  # % de precio necesario (incluye comisiones)
                prob, sl_prob, n = self.manager.reach_probability(p.side, h, lo, c, move, p.sl_pct, mask)
                verdict = "✅ realista" if prob >= 35 else ("🟡 difícil" if prob >= 20 else "❌ improbable")
                rows.append({"ROI objetivo": f"{roi:.0f}%", "Apalanc.": f"{lev}x", "Movimiento necesario": f"{move:.2f}%",
                             "Lo alcanzó antes que el SL": f"{prob:.0f}%", "SL antes": f"{sl_prob:.0f}%",
                             "Pérdida si SL (ROI)": f"−{(p.sl_pct / 100 + 2 * self.cfg.fee) * lev * 100:.1f}%",
                             "Veredicto": verdict, "Casos": n})
        return pd.DataFrame(rows)

    # ------------------------------------------------------ lenguaje sencillo
    def summary(self, r: Recommendation) -> dict:
        p, st = r.plan, r.stats
        up = p.direction == "LONG"
        headline = {"verde": f"🟢 ENTRAR: {'LONG' if up else 'SHORT'} en {p.coin}",
                    "amarillo": f"🟡 ESPERAR: {p.coin} todavía no da una entrada clara",
                    "rojo": f"🔴 NO OPERAR {p.coin} ahora"}[r.light]
        saving = p.notional * (self.cfg.fee - self.cfg.maker_fee) * 2
        origin = (f"En situaciones parecidas de los últimos días ({st.samples} casos)" if st.similar else
                  f"Con este mismo plan en los últimos días ({st.samples} casos; pocas situaciones parecidas)")
        return {
            "headline": headline,
            "what": f"Apuesta a que {p.coin} {'SUBE' if up else 'BAJA'} en los próximos {self.cfg.max_hold_min} minutos.",
            "money": f"Pon {fmt_usd(p.margin)} de tus {fmt_usd(p.capital)} con apalancamiento {p.leverage}x "
                     f"(posición de {fmt_usd(p.notional)}).",
            "entry": f"Entra en ${fmt_px(p.entry)} con orden LÍMITE (ahorras ~{fmt_usd(saving)} en comisiones).",
            "win": [f"TP{i}: ${fmt_px(tp[0])} → cobras {fmt_usd(g, True)} (cierra el {tp[2]:.0f}%) · "
                    f"se cobró el {reach:.0f}% de las veces"
                    for i, (tp, g, reach) in enumerate(zip(p.tps, p.tp_pnl, st.reach), 1)],
            "loss": f"Stop Loss en ${fmt_px(p.stop_loss)}: si sale mal pierdes como máximo {fmt_usd(abs(p.sl_pnl))} "
                    f"({abs(p.sl_pnl) / p.capital * 100:.1f}% de tu cuenta).",
            "best": f"Si llega a los 3 objetivos ganas {fmt_usd(p.total_tp_pnl, True)} "
                    f"({p.total_tp_pnl / p.capital * 100:+.1f}% de tu cuenta).",
            "odds": f"{origin}, este plan cobró el TP1 el {st.reach[0]:.0f}% de las veces "
                    f"(SL primero: {st.sl_first:.0f}%, salida por tiempo: {st.time_exit:.0f}%).",
            "ev": f"Valor esperado por operación: {fmt_usd(st.ev, True)} "
                  f"({'a favor' if st.ev > 0 else 'en contra'}: repetida muchas veces, "
                  f"{'gana' if st.ev > 0 else 'pierde'} de media esto por operación).",
            "liq": f"Te liquidarían en ${fmt_px(p.liquidation)}: muy lejos de tu Stop Loss, estás protegido.",
            "steps": [
                f"Abre Binance → Futuros → USDⓈ-M y busca el par {p.coin}USDT.",
                f"Arriba elige **Aislado** y apalancamiento **{p.leverage}x**.",
                f"Elige orden **Límite**, precio **{fmt_px(p.entry)}** y cantidad **{p.coins_qty:.6g} {p.coin}** "
                f"(≈ {fmt_usd(p.notional)}).",
                f"Activa **TP/SL**: Stop Loss **{fmt_px(p.stop_loss)}** y Take Profit **{fmt_px(p.tps[0][0])}**.",
                f"Pulsa **{'Comprar/Long' if up else 'Vender/Short'}**.",
                f"Cuando toque el TP1, cierra la mitad y sube el SL a **{fmt_px(p.break_even)}** (ya no puedes perder).",
                f"Si en {self.cfg.max_hold_min} minutos no llegó al TP1, cierra a mercado.",
            ],
        }

    @staticmethod
    def why_not(r: Recommendation) -> list[str]:
        if r.report.blocks:
            return r.report.blocks
        if r.report.enter and r.ev <= 0:
            return ["Las señales se cumplen, pero en situaciones parecidas este plan perdió dinero de media."]
        missing = [c.name for c in r.report.checks if not c.passed]
        return [f"Solo se cumplen {r.report.passed} de {len(r.report.checks)} señales (faltan: {', '.join(missing)})."]
