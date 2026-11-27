"""
engine/fgview/grok_pick.py
==========================
🏆 GROK PICK: "la operación más rentable ahora", elegida por el equipo de agentes de Grok, a pedido (un botón).

Dos modos:
* 🤖 ELIGE GROK (por MCP): FG VIEW prepara las candidatas con todos sus números y Grok, desde grok.com con sus agentes,
  elige una con `fg_pick_elige` y dice qué espera. La página muestra lo que dijo Grok.
* 🖥️ ELIGE LA MESA (local): los 6 votantes G-* en código votan y G-JUDGE decide, sin internet ni Grok.

En los dos casos el PLAN (entrada, SL, TPs, apalancamiento, riesgo) y las probabilidades salen de FG VIEW y de las
100,000 simulaciones; Grok NUNCA pone precios. Se ordena por veredicto → ganancia esperada neta → P(TP1).
Qué nunca hace: abrir nada por su cuenta (abrir en papel es un botón aparte, con las reglas de siempre), prometer
ganancias o inventar datos. "Más rentable" = mayor ganancia esperada según los datos, no una garantía.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.fgview.grok_team import GJudge

RANK = {"APRUEBA": 2, "PRECAUCION": 1, "VETA": 0}
PICK_TTL_MIN = 20  # Una elección de Grok caduca: después hay que volver a pedir datos


def _f(v, nd=4):
    try:
        x = float(v)
        return round(x, nd) if x == x else None
    except (TypeError, ValueError):
        return None


class GrokPick:
    KEY = "grok_pick"

    def __init__(self, services, team) -> None:
        self.s, self.team = services, team
        self.last: dict | None = None
        self._ctx: dict[int, object] = {}  # id de ronda -> contexto (para abrir en papel después)
        self._mem: dict = {}

    # ------------------------------------------------------------- estado compartido (página ↔ MCP)
    @property
    def _db(self):
        return getattr(self.s.positions, "db", None)

    def state(self) -> dict:
        data = (self._db.get_state(self.KEY, {}) if self._db is not None else self._mem) or {}
        return {"brief": None, "grok": None, "history": [], **data}

    def save(self, st: dict) -> None:
        st["history"] = st.get("history", [])[-40:]
        if self._db is not None:
            self._db.set_state(self.KEY, st)
        else:
            self._mem = st

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

    # ------------------------------------------------------------- modo 🤖 ELIGE GROK (por MCP)
    def brief(self, top: int = 5, both_sides: bool = False, symbol: str = "") -> dict:
        """Prepara las candidatas con sus números y las deja esperando a que Grok elija por MCP."""
        out = self.find(top, both_sides, symbol)
        cands = [{k: r[k] for k in ("id", "cripto", "simbolo", "direccion", "prob_tp1", "ventaja_r", "ev_usd", "p_sl",
                                    "riesgo_pct", "mesa", "fomo", "plan")} | {"mesa_votos": {k: v.get("voto") for k, v in (r["votos"] or {}).items()},
                                                                               "mesa_lineas": {k: v.get("linea") for k, v in (r["votos"] or {}).items()}}
                  for r in out["ranking"]]
        brief = {"at": datetime.now(timezone.utc).isoformat(), "candidatas": cands,
                 "fuente": out["ranking"][0]["fuente"] if out["ranking"] else "n/a",
                 "pausado": (out["mejor"] or {}).get("pausado"),
                 "sugerencia_mesa": None if not out["mejor"] else {k: out["mejor"][k] for k in ("id", "cripto", "direccion", "veredicto", "suma")}}
        st = self.state()
        st["brief"], st["grok"] = brief, None
        self.save(st)
        return brief

    def waiting(self) -> dict | None:
        """Las candidatas que esperan la elección de Grok (None si ya eligió o caducaron)."""
        st = self.state()
        b = st.get("brief")
        if not b or st.get("grok"):
            return None
        at = datetime.fromisoformat(b["at"])
        return None if datetime.now(timezone.utc) - at > timedelta(minutes=PICK_TTL_MIN) else b

    def submit(self, ronda_id, veredicto: str, confianza: int, lo_esperado: str, razones=None, riesgos=None,
               movimiento_esperado_pct=None, horizonte_min=None) -> dict:
        """Grok elige (por MCP). El plan y las probabilidades son de FG VIEW; de Grok solo el juicio y lo que espera."""
        v = (veredicto or "").upper().replace("Ó", "O").strip()
        if v not in ("APRUEBA", "PRECAUCION", "VETA"):
            return {"error": "veredicto debe ser APRUEBA, PRECAUCION o VETA"}
        st = self.state()
        b = st.get("brief")
        if not b:
            return {"error": "No hay candidatas esperando. Pide primero fg_pick_datos."}
        if datetime.now(timezone.utc) - datetime.fromisoformat(b["at"]) > timedelta(minutes=PICK_TTL_MIN):
            return {"error": f"Las candidatas caducaron (más de {PICK_TTL_MIN} min). Pide fg_pick_datos otra vez."}
        try:
            rid = int(ronda_id)
        except (TypeError, ValueError):
            return {"error": "ronda_id inválido"}
        cand = next((c for c in b["candidatas"] if c["id"] == rid), None)
        if cand is None:
            return {"error": f"La ronda {rid} no está entre las candidatas: {[c['id'] for c in b['candidatas']]}"}
        full = next((r for r in (self.last or {}).get("ranking", []) if r["id"] == rid), None)
        pick = {"at": datetime.now(timezone.utc).isoformat(), "ronda_id": rid, "cripto": cand["cripto"],
                "simbolo": cand["simbolo"], "direccion": cand["direccion"], "veredicto": v,
                "confianza": max(0, min(100, int(confianza or 0))), "lo_esperado": str(lo_esperado or "")[:600],
                "razones": [str(x)[:200] for x in (razones or [])][:6], "riesgos": [str(x)[:200] for x in (riesgos or [])][:6],
                "movimiento_esperado_pct": _f(movimiento_esperado_pct, 3), "horizonte_min": _f(horizonte_min, 0),
                "mesa": cand["mesa"], "pausado": b.get("pausado"), "datos": full or cand, "quien": "GROK"}
        st["grok"] = pick
        st["history"] = st.get("history", []) + [{k: pick[k] for k in ("at", "cripto", "direccion", "veredicto", "confianza", "lo_esperado")}]
        self.save(st)
        return {"ok": True, "elegida": f"{v} {pick['direccion']} {pick['cripto']} (ronda {rid})",
                "nota": "Queda en la página GROK PICK. No se abrió nada: el usuario decide.", "modo": "solo análisis"}

    def open_paper(self, ronda_id) -> str:
        """Abre en PAPEL la operación elegida (solo si G-JUDGE aprobó y la mesa no dijo NO OPERAR)."""
        ctx = self._ctx.get(ronda_id)
        if ctx is None:
            return "Primero busca la operación (la ronda ya no está en memoria)."
        best = next((r for r in (self.last or {}).get("ranking", []) if r["id"] == ronda_id), None)
        gp = self.state().get("grok")
        approved = (best is not None and best["veredicto"] == "APRUEBA") or (
            gp is not None and gp["ronda_id"] == ronda_id and gp["veredicto"] == "APRUEBA")
        if not approved:
            return "Solo se abre en papel lo que aprobó quien eligió (Grok o G-JUDGE)."
        msg, _pos = self.team._open_paper(ctx.decision, (ctx.plan or {}).get("size_factor", 1.0))
        return msg

    @staticmethod
    def grok_prompt(best: dict) -> str:
        """Para pedirle a Grok su opinión sobre una elección que ya hizo la mesa."""
        if not best:
            return ""
        return (f"Usa FG VIEW. Revisa la operación que eligió la mesa: {best['direccion']} {best['cripto']} "
                f"(ronda #{best['id']}). Corre fg_tarjeta en {best['cripto']} dirección {best['direccion']} y fg_reporte "
                f"de la ronda {best['id']}. Dime con números si de verdad es la operación más rentable ahora, qué riesgos "
                "ves que los números no capturan, y registra tu veredicto con grok_veredicto (sin abrir en papel). "
                "Termina con mi opción: ENTRO A MANO / ESPERO / PASO.")

    @staticmethod
    def pick_prompt(brief: dict) -> str:
        """Para que GROK elija: él corre su equipo y manda la elección por MCP."""
        if not brief:
            return ""
        ids = ", ".join(f"#{c['id']} {c['direccion']} {c['cripto']}" for c in brief["candidatas"][:8]) or "ninguna"
        return (
            "Usa FG VIEW y trabaja como tu equipo de agentes (G-SCOUT, G-MARKET, G-WHALE, G-FLOW, G-FUNDING, G-TRAP, "
            "G-ODDS, G-RISK, G-JUDGE).\n"
            "1) Llama a fg_pick_datos para ver las candidatas con todos sus números (ya están corridas por la mesa de "
            f"12 agentes). Candidatas de esta tanda: {ids}.\n"
            "2) Haz votar a tu equipo de −2 a +2 con esos números y deja que G-JUDGE decida cuál es la MÁS RENTABLE "
            "ahora (mayor ganancia esperada neta, no la más emocionante).\n"
            "3) Manda tu elección con fg_pick_elige: ronda_id de la elegida, veredicto (APRUEBA / PRECAUCION / VETA), "
            "confianza 0-100, lo_esperado (qué esperas que pase, en una o dos frases, con números), razones, riesgos, "
            "y si puedes movimiento_esperado_pct y horizonte_min.\n"
            "No inventes precios: el plan (entrada, SL, TPs) lo pone FG VIEW. No abras nada. "
            "Después dime en el chat tu reporte con el formato de siempre.")
