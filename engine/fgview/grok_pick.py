"""
engine/fgview/grok_pick.py
==========================
🏆 GROK PICK: "la operación más rentable ahora", elegida por el equipo de agentes de Grok, a pedido (un botón).

Qué hace: G-SCOUT escoge las mejores candidatas (niveles A/B), la mesa de 12 agentes corre cada una, los 6 votantes
G-* votan (−2…+2), G-JUDGE decide, y se ordena por: veredicto (APRUEBA > PRECAUCION > VETA) → ganancia esperada neta
(EV en USDT, de las 100,000 simulaciones) → probabilidad de TP1. Devuelve la mejor con su plan completo y el ranking.
Qué nunca hace: abrir nada por su cuenta (abrir en papel es un botón aparte, con las reglas de siempre), prometer
ganancias o inventar datos. "Más rentable" = mayor ganancia esperada según los datos, no una garantía.
"""

from __future__ import annotations

from datetime import datetime, timezone

from engine.fgview.grok_team import GJudge

RANK = {"APRUEBA": 2, "PRECAUCION": 1, "VETA": 0}


def _f(v, nd=4):
    try:
        x = float(v)
        return round(x, nd) if x == x else None
    except (TypeError, ValueError):
        return None


class GrokPick:
    def __init__(self, services, team) -> None:
        self.s, self.team = services, team
        self.last: dict | None = None
        self._ctx: dict[int, object] = {}  # id de ronda -> contexto (para abrir en papel después)

    def _evaluate(self, symbol: str, direction: str) -> dict | None:
        ctx = self.team._prepare(symbol, direction)
        if isinstance(ctx, dict):
            return None
        votes = self.team._votes(ctx)
        judge = GJudge.decide({k: v["voto"] for k, v in votes.items()}, paused=bool(ctx.paused),
                              desk_verdict=ctx.decision.verdict, direction=ctx.direction)
        dec = ctx.decision
        tm, p = dec.tm, dec.plan
        rnd = self.team.save_round({
            "at": ctx.read_at, "hora": self.team._hora(), "simbolo": ctx.symbol, "cripto": ctx.symbol.split("/")[0],
            "direccion": ctx.direction, "votos": votes, "suma": judge.suma, "juez": judge.as_dict(), "plan": ctx.plan,
            "fomo": ctx.fomo, "ejecucion": "registrada sin abrir (GROK PICK)", "posicion_id": None,
            "mesa": dec.verdict, "pausado": ctx.paused, "outcome": None, "modo": "papel", "origen": "grok_pick",
            "reglas_version": self.s.copilot.rules.state().get("version")})
        self._ctx[rnd.get("id")] = ctx
        return {
            "id": rnd.get("id"), "simbolo": ctx.symbol, "cripto": ctx.symbol.split("/")[0], "direccion": ctx.direction,
            "veredicto": judge.veredicto, "suma": judge.suma, "confianza": judge.confianza, "razon": judge.porque,
            "mesa": dec.verdict, "pausado": ctx.paused, "votos": votes,
            "prob_tp1": _f(dec.probability, 1), "ventaja_r": _f(dec.edge_r, 3), "ev_usd": _f(getattr(tm, "ev", None), 2),
            "p_sl": _f(getattr(tm, "p_sl", None), 1), "riesgo_pct": _f(dec.risk_pct, 3),
            "plan": None if p is None else {
                "entrada": _f(p.entry, 6), "sl": _f(p.stop_loss, 6), "sl_pct": _f(p.sl_pct, 3),
                "tps": [{"precio": _f(tp, 6), "pct": _f(pc, 3), "cierra": c} for tp, pc, c in p.tps],
                "apalancamiento": p.leverage, "margen": _f(p.margin, 2), "riesgo_usd": _f(p.risk_usd, 2),
                "liquidacion": _f(p.liquidation, 6), "nocional": _f(p.notional, 2)},
            "tarjeta": (ctx.card or {}).get("text"), "fomo": ctx.fomo,
            "fuente": "DEMO (datos de prueba)" if ctx.market == "demo" else "Binance Futures",
        }

    @staticmethod
    def score(c: dict) -> tuple:
        return (RANK.get(c["veredicto"], 0), c["ev_usd"] if c["ev_usd"] is not None else -1e9,
                c["prob_tp1"] if c["prob_tp1"] is not None else -1)

    def find(self, top: int = 5, both_sides: bool = False, symbol: str = "") -> dict:
        """Busca la operación más rentable. `symbol` vacío = G-SCOUT elige; `both_sides` prueba LONG y SHORT."""
        if symbol:
            cands = [{"cripto": symbol.upper().replace("/USDT", ""), "spotter": "pedida"}]
        else:
            cands = self._scout(max(1, top))
        dirs = ("LONG", "SHORT") if both_sides else ("Auto",)
        results = []
        for c in cands:
            if c.get("spotter") == "sin datos":
                continue
            for d in dirs:
                r = self._evaluate(c["cripto"], d)
                if r is not None:
                    results.append(r)
        results.sort(key=self.score, reverse=True)
        best = results[0] if results else None
        out = {"at": datetime.now(timezone.utc).isoformat(), "mejor": best, "ranking": results,
               "candidatas": cands,
               "nota": ("Nada aprobado: esta es la menos mala, solo para estudiar." if best and best["veredicto"] != "APRUEBA"
                        else "Sin datos para elegir." if not best else "La de mayor ganancia esperada entre las aprobadas.")}
        self.last = out
        return out

    def _scout(self, top: int) -> list[dict]:
        coins = [c for t in self.s.tiers.tiers if t.key in ("A", "B") for c in t.coins]
        ranked = []
        cap = self.team._capital()
        for coin in coins:
            try:
                res, *_r, ws = self.s.desk.spot(f"{coin}/USDT", "crypto", cap)
                ranked.append({"cripto": coin, "puntos": round(float(ws), 1), "spotter": res.status})
            except Exception:  # noqa: BLE001
                ranked.append({"cripto": coin, "puntos": None, "spotter": "sin datos"})
        ranked.sort(key=lambda r: (r["spotter"] == "pass", r["puntos"] if r["puntos"] is not None else -1), reverse=True)
        return ranked[:top]

    def open_paper(self, ronda_id) -> str:
        """Abre en PAPEL la operación elegida (solo si G-JUDGE aprobó y la mesa no dijo NO OPERAR)."""
        ctx = self._ctx.get(ronda_id)
        if ctx is None:
            return "Primero busca la operación (la ronda ya no está en memoria)."
        best = next((r for r in (self.last or {}).get("ranking", []) if r["id"] == ronda_id), None)
        if best is None or best["veredicto"] != "APRUEBA":
            return "Solo se abre en papel lo que G-JUDGE aprobó."
        msg, _pos = self.team._open_paper(ctx.decision, (ctx.plan or {}).get("size_factor", 1.0))
        return msg

    @staticmethod
    def grok_prompt(best: dict) -> str:
        if not best:
            return ""
        return (f"Usa FG VIEW. Revisa la operación que eligió tu equipo: {best['direccion']} {best['cripto']} "
                f"(ronda #{best['id']}). Corre fg_tarjeta en {best['cripto']} dirección {best['direccion']} y fg_reporte "
                f"de la ronda {best['id']}. Dime con números si de verdad es la operación más rentable ahora, qué riesgos "
                "ves que los números no capturan, y registra tu veredicto con grok_veredicto (sin abrir en papel). "
                "Termina con mi opción: ENTRO A MANO / ESPERO / PASO.")
