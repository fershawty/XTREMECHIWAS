"""
engine/fgview/grok_mcp.py
=========================
🤖 GROK DESK · puente MCP: FG VIEW como servidor MCP para el equipo de 11 agentes Grok
(G-SCOUT … G-WATCH). La mesa de 12 agentes de FG VIEW sigue siendo la fuente de los números.

Herramientas (TODAS de solo lectura o de PAPEL). Las 11 originales siguen igual.
Además:
    fg_ronda_datos      todo lo que lee cada agente Grok, con fuente y hora por número
    fg_ronda            ronda completa: votos, G-JUDGE, reporte y, si aprueba, papel
    fg_reporte          el formato fijo de una ronda ya guardada
    fg_revision         quién del equipo acertó más y quién se equivocó más (no cambia reglas)

REGLAS DE SEGURIDAD (en código):
* Nunca hay órdenes reales por MCP: ni siquiera existe una herramienta para eso.
* G-JUDGE no cambia el tamaño ni el stop. El papel usa el plan de la tarjeta, con su FOMO.
* grok_veredicto solo abre en PAPEL, solo con APRUEBA, solo si el COACH no pausó y la mesa
  no dijo ⛔ NO OPERAR (Grok puede frenar, no forzar, no saltarse al COACH).
* fg_reglas es de solo lectura.
* La dirección lleva un token secreto (https://…/mcp/<token>); sin él, el servidor responde 404.
* Escucha en 127.0.0.1: para que Grok lo alcance hace falta un túnel (p. ej. Cloudflare Tunnel) que tú abres.

Cada llamada queda en un registro que la página GROK DESK anima en vivo (qué agente fue a qué gráfica).
"""

from __future__ import annotations

import secrets
import threading
import time
from datetime import datetime, timezone

from engine.config import get_logger

log = get_logger("fgview.grok_mcp")

DEFAULT_PORT = 8765
TOOL_AGENTS = {  # Qué agentes "se mueven" en la animación cuando Grok usa cada herramienta
    "fg_mercado": ["SPOTTER", "WHALE", "FLOW", "FUNDING"],
    "fg_correr_mesa": ["SPOTTER", "WHALE", "FLOW", "FUNDING", "GATE", "PRIOR", "PRICER", "EDGE", "KELLY", "GUARD", "TAKER"],
    "fg_escanear": ["SPOTTER"],
    "fg_agente": [],
    "fg_posiciones": ["CLOSER"],
    "fg_resultados": ["KELLY", "CLOSER"],
    "grok_veredicto": ["TAKER"],
    "fg_tarjeta": ["SPOTTER", "WHALE", "FLOW", "FUNDING", "GATE", "PRIOR", "PRICER", "EDGE", "KELLY", "GUARD", "TAKER"],
    "fg_coach": ["CLOSER"],
    "fg_reglas": ["KELLY"],
    "fg_resumen": ["CLOSER"],
    "fg_ronda_datos": ["SPOTTER", "WHALE", "FLOW", "FUNDING", "GATE", "PRICER", "EDGE", "KELLY"],
    "fg_ronda": ["SPOTTER", "WHALE", "FLOW", "FUNDING", "GATE", "PRIOR", "PRICER", "EDGE", "KELLY", "GUARD", "TAKER"],
    "fg_reporte": [],
    "fg_revision": ["CLOSER"],
    "fg_estado": ["CLOSER"],
    "fg_auditoria": ["GATE", "PRICER", "EDGE"],
    "fg_tamano": ["KELLY"],
    "fg_vigilancia": ["CLOSER"],
    "fg_salud": [],
    "fg_entrada_vigilada": ["TAKER"],
}


def mask_secret(text: str | None) -> str:
    """Enmascara el token de /mcp/ y /app/. La interfaz nunca muestra el secreto completo."""
    import re

    if not text:
        return "n/a"
    return re.sub(r"(/(?:mcp|app)/)[^/\s?#]+", r"\1••••••••", str(text))


def _read_doc(name: str) -> str:
    import sys
    from pathlib import Path

    for base in (Path(getattr(sys, "_MEIPASS", "")), Path(__file__).resolve().parents[2]):
        p = base / "docs" / name
        if p.is_file():
            return p.read_text(encoding="utf-8")
    return ""


def copilot_prompt() -> str:
    """El prompt del copiloto adaptado (docs/GROK_COPILOT_PROMPT.md), también dentro del ejecutable."""
    return _read_doc("GROK_COPILOT_PROMPT.md")


def agents_prompt() -> str:
    """El prompt del equipo (docs/GROK_AGENTS_PROMPT.md). Reemplaza a la sección 27 del contexto."""
    return _read_doc("GROK_AGENTS_PROMPT.md")


INSTRUCTIONS = """FG VIEW es una terminal de análisis de futuros perpetuos USDT-M de Binance. Tú eres el equipo de 11 agentes \
Grok (prompt «agentes_fg»): G-SCOUT, G-MARKET, G-WHALE, G-FLOW, G-FUNDING, G-TRAP, G-ODDS, G-RISK, G-JUDGE, G-EXEC \
y G-WATCH. Cada uno responde una sola pregunta. Si un dato no viene, di «n/a»: no lo inventes.

Para una ronda usa fg_ronda (o fg_ronda_datos y luego fg_reporte). G-JUDGE ya está en el código y no se negocia: \
pausa del COACH → VETA; mesa NO OPERAR o G-TRAP en −2 → VETA; suma ≥ +4 sin ningún −2 → APRUEBA; suma +1 a +3 → \
PRECAUCION; suma ≤ 0 → VETA. El juez no cambia el tamaño ni el stop. Solo APRUEBA puede abrir, y solo en PAPEL. \
«¿cómo vamos?» y «revisión» usan fg_revision: la sugerencia no cambia las reglas. fg_reglas es de solo lectura.

Las 11 herramientas originales siguen: fg_tarjeta, fg_correr_mesa, fg_agente, fg_mercado, fg_escanear, fg_coach, \
fg_reglas, fg_resumen, fg_posiciones, fg_resultados, grok_veredicto. fg_estado es solo lectura (incluye si el modo \
automático está encendido). Si está apagado, fg_ronda no corre: no insistas. No existen órdenes reales por aquí. \
No prometas ganancias. No puedes cambiar las reglas ni la hora límite. Responde en español."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def decision_dict(dec) -> dict | None:
    if dec is None:
        return None
    p = dec.plan
    return {
        "simbolo": dec.symbol, "direccion": dec.direction, "veredicto_mesa": dec.verdict,
        "probabilidad_tp1_pct": None if dec.probability is None else round(dec.probability, 1),
        "ventaja_R": None if dec.edge_r is None else round(dec.edge_r, 3),
        "riesgo_pct_capital": None if dec.risk_pct is None else round(dec.risk_pct, 3),
        "plan": None if p is None else {
            "entrada": p.entry, "stop_loss": p.stop_loss, "sl_pct": round(p.sl_pct, 3), "apalancamiento": p.leverage,
            "take_profits": [{"precio": tp, "pct": round(pc, 3), "cierra_pct": c} for tp, pc, c in p.tps],
            "margen_usdt": round(p.margin, 2), "liquidacion": p.liquidation},
        "agentes": [{"agente": r.agent, "estado": {"pass": "aprueba", "block": "bloquea", "info": "informa"}.get(r.status, r.status),
                     "dato": r.headline} for r in dec.results],
        "resumen": dec.summary(),
    }


class GrokBridge:
    KEY = "grok_mcp"

    def __init__(self, services) -> None:
        from engine.fgview.grok_team import GrokTeam

        self.s = services
        self.team = GrokTeam(services)
        from engine.fgview.grok_pick import GrokPick

        self.pick = GrokPick(services, self.team)  # 🏆 La operación más rentable a pedido
        self._mem: dict = {}
        self.last_dec = None  # Última DeskDecision pedida por Grok (para animar la página)
        self._server = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------- estado
    @property
    def db(self):
        return getattr(self.s.positions, "db", None)

    def state(self) -> dict:
        data = (self.db.get_state(self.KEY, {}) if self.db is not None else self._mem) or {}
        st = {"token": "", "port": DEFAULT_PORT, "tunnel": "", "events": [], "verdicts": [], "last": None, **data}
        if not st["token"]:
            st["token"] = secrets.token_urlsafe(24)
            self.save(st)
        return st

    def save(self, st: dict) -> None:
        st["events"] = st.get("events", [])[-200:]
        st["verdicts"] = st.get("verdicts", [])[-100:]
        with self._lock:
            if self.db is not None:
                self.db.set_state(self.KEY, st)
            else:
                self._mem = st

    def set(self, **changes) -> dict:
        st = {**self.state(), **changes}
        self.save(st)
        return st

    def new_token(self) -> str:
        return self.set(token=secrets.token_urlsafe(24))["token"]

    def _log(self, tool: str, args: dict, summary: str, agents: list[str] | None = None) -> None:
        st = self.state()
        st["events"].append({"at": _now(), "ts": time.time(), "tool": tool, "args": args, "summary": summary[:220],
                             "agents": agents if agents is not None else TOOL_AGENTS.get(tool, [])})
        self.save(st)

    # ------------------------------------------------------------- herramientas
    def _symbol(self, symbol: str) -> tuple[str, str]:
        sym = (symbol or "BTC").upper().replace("USDT", "").replace("/", "").strip() or "BTC"
        return ("DEMO/USDT", "demo") if sym == "DEMO" else (f"{sym}/USDT", "crypto")

    def _capital(self) -> float:
        lad = getattr(self.s, "ladder_desk", None)
        if lad is not None:
            stt = lad.status()
            if stt.enabled and not stt.paused:
                return stt.balance
        b = self.s.budget.status() if getattr(self.s, "budget", None) and self.s.budget.state().get("start") else None
        from engine.config import SETTINGS

        return float(b.balance) if b else float(SETTINGS.account_equity)

    def market(self, symbol: str) -> dict:
        sym, market = self._symbol(symbol)
        snap = self.s.loader.load(sym, market)
        df = snap.df5
        chg = lambda n: round((float(df["close"].iloc[-1]) / float(df["close"].iloc[-n - 1]) - 1) * 100, 3) if len(df) > n else None
        out = {"simbolo": sym, "precio": snap.price, "cambio_pct": {"1h": chg(12), "4h": chg(48), "24h": chg(288)},
               "atr5_pct": round(snap.atr5 / snap.price * 100, 3) if snap.atr5 and snap.price else None,
               "funding": snap.funding, "libro_compras_pct": None}
        book = (snap.order_book or {}).get("imbalance")
        if book is not None:
            out["libro_compras_pct"] = round(50 + 50 * book, 1)
        flow = self.s.desk.flow.get(sym.split("/")[0]) if (market == "crypto" and self.s.desk.flow) else None
        if flow is not None and flow.has_data:
            out["flujo"] = {"taker_compra_venta": flow.taker_ratio, "open_interest_1h_pct": flow.oi_change_pct,
                            "ballenas_compran_usd": flow.whale_buy_usd, "ballenas_venden_usd": flow.whale_sell_usd,
                            "funding_min_para_cobro": flow.funding_min}
        self._log("fg_mercado", {"simbolo": sym}, f"{sym} {snap.price:,.6g} · 1h {out['cambio_pct']['1h']}%")
        return out

    def run_desk(self, symbol: str, direction: str = "Auto") -> dict:
        sym, market = self._symbol(symbol)
        direction = direction.upper() if direction and direction.upper() in ("LONG", "SHORT") else "Auto"
        dec = self.s.desk.run(sym, market, self._capital(), direction=direction, operable=True)
        self.last_dec = dec
        d = decision_dict(dec)
        self.set(last=d)
        self._log("fg_correr_mesa", {"simbolo": sym, "direccion": direction}, d["resumen"],
                  [r.agent for r in dec.results])
        return d

    def scan(self) -> dict:
        coins = [c for t in self.s.tiers.tiers if t.key in ("A", "B") for c in t.coins]
        ranked = []
        for c in coins:
            try:
                res, *_r, wscore = self.s.desk.spot(f"{c}/USDT", "crypto", self._capital())
                ranked.append({"cripto": c, "puntos": round(wscore, 1), "spotter": res.status, "dato": res.headline[:120]})
            except Exception as exc:  # noqa: BLE001
                ranked.append({"cripto": c, "puntos": None, "spotter": "sin datos", "dato": str(exc)[:80]})
        ranked.sort(key=lambda r: (r["spotter"] == "pass", r["puntos"] or -1), reverse=True)
        self._log("fg_escanear", {}, " · ".join(f"{r['cripto']} {r['puntos']}" for r in ranked[:5]))
        return {"ranking": ranked}

    def agent(self, name: str) -> dict:
        name = (name or "").upper()
        if self.last_dec is None:
            return {"error": "Primero corre la mesa con fg_correr_mesa."}
        r = next((r for r in self.last_dec.results if r.agent == name), None)
        if r is None:
            return {"error": f"{name} no participó en la última corrida.",
                    "agentes": [x.agent for x in self.last_dec.results]}
        self._log("fg_agente", {"agente": name}, r.headline, [name])
        return {"agente": name, "estado": r.status, "dato": r.headline, "detalle": r.detail, "valor": r.value,
                "ms": round(r.ms, 1)}

    def positions(self) -> dict:
        rows = [{k: v for k, v in p.items() if isinstance(v, (str, int, float, bool)) or v is None}
                for p in self.s.positions.all()]
        self._log("fg_posiciones", {}, f"{len(rows)} abiertas en papel")
        return {"modo": "papel", "posiciones": rows}

    def results(self) -> dict:
        ps = self.s.autopilot.paper_stats()
        lad = getattr(self.s, "ladder_desk", None)
        out = {"papel": ps}
        if lad is not None:
            stt = lad.status()
            out["escalera"] = {"activa": stt.enabled, "inicio": stt.start, "saldo": round(stt.balance, 2),
                               "caja_fuerte": round(stt.vault, 2), "meta": round(stt.next_target, 2),
                               "riesgo_pct": stt.risk_pct, "operaciones": stt.trades}
        self._log("fg_resultados", {}, f"papel: {ps.get('trades', 0)} operaciones")
        return out

    def verdict(self, veredicto: str, confianza: int, resumen: str, razones: list[str] | None = None,
                riesgos: list[str] | None = None, abrir_en_papel: bool = False) -> dict:
        v = (veredicto or "").upper().replace("Ó", "O")
        if v not in ("APRUEBA", "PRECAUCION", "VETA"):
            return {"error": "veredicto debe ser APRUEBA, PRECAUCION o VETA"}
        dec = self.last_dec
        entry = {"at": _now(), "veredicto": v, "confianza": int(confianza or 0), "resumen": resumen[:400],
                 "razones": (razones or [])[:6], "riesgos": (riesgos or [])[:6],
                 "simbolo": dec.symbol if dec else None, "direccion": dec.direction if dec else None,
                 "mesa": dec.verdict if dec else None, "suma": None, "origen": "Grok", "abierta": False}
        msg = "Veredicto registrado."
        if abrir_en_papel:
            paused = None
            cp = getattr(self.s, "copilot", None)
            if cp is not None:
                try:
                    paused = cp.coach.status().get("blocked")
                except Exception:  # noqa: BLE001
                    paused = None
            if paused:
                msg = "Pausado por tus reglas: no se abre. " + paused
            elif dec is None or dec.ticket is None:
                msg = "No hay plan de la mesa: corre fg_correr_mesa primero."
            elif dec.verdict == "NO OPERAR":
                msg = "La mesa dice ⛔ NO OPERAR: no se abre (Grok puede frenar, no forzar)."
            elif v != "APRUEBA":
                msg = "Solo se abre en papel con APRUEBA."
            else:
                # El ticket sale de la mesa. Esta herramienta no acepta otro tamaño ni otro stop.
                out = self.s.executor.submit(dec.ticket, confirmed=True, mode="paper")
                entry["abierta"] = out.get("status") == "filled"
                msg = ("Abierta en PAPEL." if entry["abierta"]
                       else f"No se abrió: {(out.get('problems') or [out.get('message', '')])[0]}")
        st = self.state()
        st["verdicts"].append({**entry, "nota": msg})
        self.save(st)
        self._log("grok_veredicto", {"veredicto": v, "papel": abrir_en_papel}, f"{v} · {resumen[:120]} · {msg}")
        return {"ok": True, "mensaje": msg, "modo": "papel"}

    # ------------------------------------------------------------- copiloto (tarjeta, COACH, reglas, SCRIBE)
    def card(self, symbol: str, direction: str = "Auto") -> dict:
        sym, market = self._symbol(symbol)
        direction = direction.upper() if direction and direction.upper() in ("LONG", "SHORT") else "Auto"
        dec = self.s.desk.run(sym, market, self._capital(), direction=direction, operable=True)
        self.last_dec = dec
        self.set(last=decision_dict(dec))
        c = self.s.copilot.card(dec)
        self._log("fg_tarjeta", {"simbolo": sym, "direccion": direction}, c["text"].splitlines()[0],
                  [r.agent for r in dec.results])
        st = self.state()
        st["last_card"] = c["text"]
        self.save(st)
        return {"tarjeta": c["text"], "banda": c["band"], "score": c["score"], "fomo": c["fomo"],
                "por_que_fomo": c["fomo_why"], "pausado_por_reglas": c["paused"], "tasa_base": c["base_rate"],
                "plan": c["plan"], "modo": "papel / tú operas"}

    def auto_on(self) -> bool:
        """Modo automático de las rondas que llegan por el conector. Por defecto encendido."""
        return bool(self.state().get("auto", True))

    def estado(self) -> dict:
        """Lectura del puesto. No acepta cambios: el interruptor vive en la app."""
        coach = {}
        cutoff = None
        try:
            coach = self.s.copilot.coach.status()
            cutoff = self.s.copilot.rules.values().get("cutoff")
        except Exception:  # noqa: BLE001
            coach = {}
        every = getattr(getattr(self.s, "autopilot", None), "every_s", None)
        out = {"modo_automatico_grok": self.auto_on(),
               "servidor_mcp": "encendido" if self.running() else "apagado",
               "intervalo_s": None if every is None else int(every),
               "hora_limite": cutoff or "n/a",
               "pausado_por_reglas": coach.get("blocked"),
               "racha": coach.get("streak"),
               "modo": "papel",
               "nota": "Solo lectura. El interruptor no cambia las reglas ni la hora límite."}
        self._log("fg_estado", {}, f"auto {'ON' if out['modo_automatico_grok'] else 'OFF'} · {out['servidor_mcp']}")
        return out

    def coach(self, texto: str = "") -> dict:
        cp = self.s.copilot
        out = {"estado": cp.coach.status(), "modo_automatico_grok": self.auto_on()}
        if texto:
            reply = cp.coach.tilt(texto)
            if reply:
                out["responde_primero_el_coach"] = reply
        self._log("fg_coach", {"texto": (texto or "")[:60]}, out["estado"]["blocked"] or "sin bloqueos")
        return out

    def rules(self) -> dict:
        cp = self.s.copilot
        st = cp.rules.state()
        self._log("fg_reglas", {}, f"reglas v{st['version']}")
        return {"version": st["version"], "rules_md": cp.rules.markdown(cp.coach.bankroll()), "valores": st["values"],
                "pendientes": st["pending"], "nota": "Las reglas solo las cambia el usuario desde la app."}

    def summary(self, periodo: str = "dia") -> dict:
        cp = self.s.copilot
        try:
            cp.scribe.grade(self.s.history, int(cp.rules.values()["grade_after_min"]))
        except Exception:  # noqa: BLE001
            pass
        days = 7 if (periodo or "").lower().startswith(("sem", "week")) else 1
        txt = cp.scribe.summary(cp.coach, days)
        self._log("fg_resumen", {"periodo": periodo}, txt.splitlines()[1] if "\n" in txt else txt)
        return {"resumen": txt}

    # ------------------------------------------------------------- equipo Grok (ronda, reporte, revisión)
    def _mirror_round(self, rnd: dict) -> None:
        """Deja el fallo de G-JUDGE también en el panel viejo de veredictos."""
        judge = rnd.get("juez") or {}
        if not judge:
            return
        st = self.state()
        st["verdicts"].append({"at": _now(), "veredicto": judge.get("veredicto"), "confianza": judge.get("confianza") or 0,
                               "resumen": (judge.get("porque") or "")[:400], "razones": [], "riesgos": [],
                               "simbolo": rnd.get("simbolo"), "direccion": rnd.get("direccion"), "mesa": rnd.get("mesa"),
                               "suma": rnd.get("suma"), "origen": "Grok",
                               "abierta": rnd.get("ejecucion") == "abierta en PAPEL", "nota": rnd.get("ejecucion") or ""})
        self.save(st)

    def ronda_datos(self, symbol: str, direction: str = "Auto") -> dict:
        out = self.team.datos(symbol, direction)
        if self.team.last_dec is not None:
            self.last_dec = self.team.last_dec
        self._log("fg_ronda_datos", {"simbolo": out.get("simbolo") or symbol, "direccion": direction},
                  f"{out.get('simbolo') or symbol} · mesa {out.get('veredicto_mesa') or 'n/a'}")
        return out

    def ronda(self, symbol: str = "", direction: str = "Auto", abrir_en_papel: bool = False, *,
              manual: bool = False) -> dict:
        if not manual and not self.auto_on():
            self._log("fg_ronda", {"simbolo": symbol or "escaneo", "saltada": True}, "Modo automático Grok apagado")
            return {"saltada": True, "motivo": "Modo automático Grok apagado.",
                    "reporte": "Modo automático Grok apagado.", "rondas": [], "candidatas": [], "por_cripto": [],
                    "nota": "Modo automático Grok apagado. Enciéndelo en GROK DESK para que el conector corra rondas.",
                    "modo": "papel", "juez": None, "ejecucion": "registrada sin abrir", "suma": None, "votos": None}
        out = self.team.ronda(symbol, direction, abrir_en_papel=bool(abrir_en_papel))
        if self.team.last_dec is not None:
            self.last_dec = self.team.last_dec
        for rnd in out.get("por_cripto") or []:
            self._mirror_round(rnd)
        head = (out.get("reporte") or "").splitlines()
        self._log("fg_ronda", {"simbolo": symbol or "escaneo", "direccion": direction, "papel": bool(abrir_en_papel)},
                  head[0] if head else (out.get("nota") or "ronda"))
        return out

    def reporte(self, ronda_id: str = "") -> dict:
        out = self.team.reporte(ronda_id or None)
        self._log("fg_reporte", {"id": ronda_id or "ultima"}, (out.get("reporte") or "n/a").splitlines()[0][:160])
        return out

    def auditoria(self, symbol: str = "") -> dict:
        """Checks de G-TRAP y score por partes. No cambia filtros ni reglas."""
        from engine.fgview.audit import score_parts, trap_checks

        dec = self.last_dec
        if symbol and dec is not None and symbol.upper().split("/")[0] not in str(dec.symbol):
            dec = None
        raw = getattr(dec, "raw", {}) if dec is not None else {}
        gate = None
        if dec is not None:
            gate = next((r for r in dec.results if r.agent == "GATE"), None)
        data = {"direction": getattr(dec, "direction", None), "gate_block": bool(gate and gate.status == "block"),
                "late": False, "spread_pct": None}
        book = getattr(getattr(dec, "snap", None), "order_book", None) or {}
        if book.get("spread_pct") is not None:
            data["spread_pct"] = book.get("spread_pct")
        if book.get("bid_vol10") is not None and book.get("ask_vol10") is not None:
            data["depth_usd"] = float(book["bid_vol10"]) + float(book["ask_vol10"])
        trap = trap_checks(data)
        parts = {"mercado": raw.get("wscore"), "flujo": None, "funding": None,
                 "trampa": 0 if trap["veredicto"] == "trampa" else (40 if trap["veredicto"] == "warn" else (
                     None if trap["veredicto"] == "desconocido" else 80)),
                 "trampa_estado": trap["veredicto"],
                 "probabilidad": getattr(dec, "probability", None),
                 "ventaja": None if getattr(dec, "edge_r", None) is None else max(0.0, min(100.0, 50 + dec.edge_r * 100))}
        scored = score_parts(parts)
        self._log("fg_auditoria", {"simbolo": symbol or (dec.symbol if dec else "")}, trap["linea"][:160])
        return {"trampa": trap, "score": scored, "modo": "papel",
                "nota": "Solo lectura. Los pesos no se guardan desde aquí."}

    def tamano(self) -> dict:
        """Pasos del tamaño con el riesgo de las reglas. No acepta un riesgo mayor."""
        from decimal import Decimal

        from engine.fgview.audit import size_ticket

        dec = self.last_dec
        plan = getattr(dec, "plan", None)
        if dec is None or plan is None:
            return {"ok": False, "motivo": "n/a · no hay plan de la mesa", "pasos": []}
        try:
            risk = float(self.s.copilot.rules.values()["risk_pct"])
            fomo, _ = self.s.copilot.coach.fomo(getattr(getattr(dec, "snap", None), "df5", None), dec.direction)
            bank = float(self.s.copilot.coach.bankroll())
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "motivo": f"n/a · {exc}"[:160], "pasos": []}
        quote = size_ticket(bankroll=bank, risk_pct=risk, entry=plan.entry, stop=plan.stop_loss,
                            direction=plan.direction or dec.direction, band="VERDE", fomo=fomo)
        quote["stop"] = plan.stop_loss
        quote["riesgo_reglas_pct"] = risk
        quote["nota"] = "Usa el riesgo de tus reglas. Este cálculo no las cambia ni abre la orden."
        self._log("fg_tamano", {}, (quote.get("motivo") or "tamaño")[:160])
        for key in ("riesgo_usd", "nocional", "comision", "funding", "factor_banda", "factor_fomo", "liquidacion"):
            if isinstance(quote.get(key), Decimal):
                quote[key] = float(quote[key])
        if isinstance(quote.get("distancia"), Decimal):
            quote["distancia"] = float(quote["distancia"])
        return quote

    def vigilancia(self) -> dict:
        from engine.fgview.audit import watch_position

        book = getattr(self.s, "audit", None)
        repeats = book.repeats if book is not None else {}
        rows = []
        for pos in self.s.positions.all():
            rows.append({"id": pos.get("id"), "coin": pos.get("coin"),
                         "alertas": watch_position(pos, pos.get("last_price"), repeats=repeats)})
        if book is not None:
            book.save()
        self._log("fg_vigilancia", {}, f"{len(rows)} posiciones")
        return {"posiciones": rows, "modo": "papel",
                "nota": "Solo avisa. El stop no se aleja y no se abre ni se cierra nada desde aquí."}

    def salud(self) -> dict:
        from engine.fgview.audit import health

        out = health(self.s)
        self._log("fg_salud", {}, out.get("banner") or "salud")
        return out

    def entrada_vigilada(self) -> dict:
        book = getattr(self.s, "audit", None)
        item = book.entry.item if book is not None else None
        self._log("fg_entrada_vigilada", {}, "lectura")
        return {"entrada": item or {"state": "vacia", "mensaje": "No hay entrada vigilada."},
                "nota": "Solo lectura. Armar la vigilancia se hace en la app, no por el conector."}

    def revision(self) -> dict:
        out = self.team.revision()
        best, worst = out.get("acerto_mas") or "n/a", out.get("equivoco_mas") or "n/a"
        self._log("fg_revision", {}, f"acertó más {best} · se equivocó más {worst}")
        return out

    # ------------------------------------------------------------- servidor MCP
    def build(self):
        from mcp.server.mcpserver import MCPServer
        from mcp.types import ToolAnnotations

        ro = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
        srv = MCPServer(name="FG VIEW · GROK DESK", instructions=INSTRUCTIONS, version="5.2")

        @srv.tool(description="Precio, cambios 1h/4h/24h, ATR, funding, libro, flujo taker, open interest y ballenas de "
                              "una cripto de Binance Futures (ej. 'SOL', 'BTC'). Usa 'DEMO' para datos de prueba.",
                  annotations=ro)
        def fg_mercado(simbolo: str) -> dict:
            return self.market(simbolo)

        @srv.tool(description="Corre la mesa de 12 agentes de FG VIEW sobre una cripto y devuelve el veredicto "
                              "OPERAR/ESPERAR/NO OPERAR, la probabilidad de TP1, la ventaja, el plan y lo que dijo cada "
                              "agente. direccion: 'Auto', 'LONG' o 'SHORT'. No envía órdenes.", annotations=ro)
        def fg_correr_mesa(simbolo: str, direccion: str = "Auto") -> dict:
            return self.run_desk(simbolo, direccion)

        @srv.tool(description="Ranking rápido del Spotter sobre las criptos principales (nivel A y B).", annotations=ro)
        def fg_escanear() -> dict:
            return self.scan()

        @srv.tool(description="Detalle de un agente de la última corrida (SPOTTER, WHALE, FLOW, FUNDING, GATE, PRIOR, "
                              "PRICER, EDGE, KELLY, GUARD, TAKER, CLOSER).", annotations=ro)
        def fg_agente(agente: str) -> dict:
            return self.agent(agente)

        @srv.tool(description="Posiciones abiertas en modo papel.", annotations=ro)
        def fg_posiciones() -> dict:
            return self.positions()

        @srv.tool(description="Resultados del modo papel (operaciones, win rate, profit factor, drawdown) y estado de "
                              "la escalera de capital.", annotations=ro)
        def fg_resultados() -> dict:
            return self.results()

        @srv.tool(description="Registra tu veredicto sobre la última corrida de la mesa: APRUEBA, PRECAUCION o VETA, con "
                              "confianza 0-100, resumen, razones y riesgos. Con abrir_en_papel=true y APRUEBA abre la "
                              "operación en MODO PAPEL (nunca real), solo si la mesa no dijo NO OPERAR.",
                  annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
        def grok_veredicto(veredicto: str, confianza: int, resumen: str, razones: list[str] | None = None,
                           riesgos: list[str] | None = None, abrir_en_papel: bool = False) -> dict:
            return self.verdict(veredicto, confianza, resumen, razones, riesgos, abrir_en_papel)

        @srv.tool(description="TRADE CARD con formato fijo de una cripto (corre la mesa de 12 agentes): banda GREEN/YELLOW/"
                              "RED/SKIP, score, por qué, seguridad, momentum, mi lectura, plan, FOMO, TASA BASE y reglas. "
                              "Úsala primero y cópiala tal cual.", annotations=ro)
        def fg_tarjeta(simbolo: str, direccion: str = "Auto") -> dict:
            return self.card(simbolo, direccion)

        @srv.tool(description="Estado del COACH: operaciones y P&L de hoy, racha de pérdidas, pausa, límites, hora "
                              "límite. Pasa en `texto` lo que escribió el usuario si suena alterado (tilt).", annotations=ro)
        def fg_coach(texto: str = "") -> dict:
            return self.coach(texto)

        @srv.tool(description="Estado del puesto GROK DESK, solo lectura: modo automático (ON/OFF), servidor MCP, "
                              "intervalo del piloto y hora límite. No cambia reglas ni enciende el modo automático. "
                              "Si modo_automatico_grok es false, no llames fg_ronda: la ronda se salta.",
                  annotations=ro)
        def fg_estado() -> dict:
            return self.estado()

        @srv.tool(description="Auditoría de G-TRAP (pass/warn/fail/n/a) y score por partes de la última corrida. "
                              "Solo lectura: no cambia filtros, pesos ni reglas.", annotations=ro)
        def fg_auditoria(simbolo: str = "") -> dict:
            return self.auditoria(simbolo)

        @srv.tool(description="Pasos del tamaño usando el riesgo de las reglas y el stop del plan. No sube el riesgo, "
                              "no mueve el stop y no abre la orden.", annotations=ro)
        def fg_tamano() -> dict:
            return self.tamano()

        @srv.tool(description="Vigilancia de las posiciones en papel: stop, TP1, liquidación. Solo avisa. "
                              "El stop no se aleja.", annotations=ro)
        def fg_vigilancia() -> dict:
            return self.vigilancia()

        @srv.tool(description="Salud de Binance (si se conoce), base local, MCP y túnel. Si una fuente cae, lo dice "
                              "y no inventa el dato.", annotations=ro)
        def fg_salud() -> dict:
            return self.salud()

        @srv.tool(description="Estado de la entrada vigilada de 20 min, si hay una. Solo lectura: no la arma.",
                  annotations=ro)
        def fg_entrada_vigilada() -> dict:
            return self.entrada_vigilada()

        @srv.tool(description="Las reglas del usuario (rules.md versionado). Solo lectura.", annotations=ro)
        def fg_reglas() -> dict:
            return self.rules()

        @srv.tool(description="Resumen de SCRIBE: periodo 'dia' o 'semana' (operaciones, P&L, tarjetas por banda, "
                              "calificaciones A-D, fugas).", annotations=ro)
        def fg_resumen(periodo: str = "dia") -> dict:
            return self.summary(periodo)

        @srv.tool(description="Insumos de una ronda para los 11 agentes Grok. Cada número trae fuente y hora de lectura. "
                              "Lo que no se pudo leer viene como n/a. No abre nada. simbolo: 'SOL', 'BTC' o 'DEMO'. "
                              "direccion: 'Auto', 'LONG' o 'SHORT'.", annotations=ro)
        def fg_ronda_datos(simbolo: str, direccion: str = "Auto") -> dict:
            return self.ronda_datos(simbolo, direccion)

        @srv.tool(description="Ronda del equipo Grok. Sin simbolo, G-SCOUT elige hasta 3 candidatas de nivel A/B. "
                              "Calcula los votos −2..+2, G-JUDGE decide (no cambia tamaño ni stop) y devuelve el reporte "
                              "con el formato fijo. abrir_en_papel=true solo abre en PAPEL si el juez dice APRUEBA, el "
                              "COACH no pausó y la mesa no dijo NO OPERAR. Nunca abre en real.",
                  annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
        def fg_ronda(simbolo: str = "", direccion: str = "Auto", abrir_en_papel: bool = False) -> dict:
            return self.ronda(simbolo, direccion, abrir_en_papel, manual=False)

        @srv.tool(description="Reporte con el formato fijo de una ronda ya guardada. Sin id, usa la última. "
                              "No abre órdenes.", annotations=ro)
        def fg_reporte(ronda_id: str = "") -> dict:
            return self.reporte(ronda_id)

        @srv.tool(description="Revisión: resultados de papel, resumen de la semana y qué agente Grok acertó más o se "
                              "equivocó más. La sugerencia no cambia las reglas.", annotations=ro)
        def fg_revision() -> dict:
            return self.revision()

        @srv.prompt(name="copiloto_fg", description="Prompt del copiloto FG VIEW para futuros de Binance (reglas de "
                                                    "verdad, Trade Card, COACH, preguntas y lo que nunca hace).")
        def copiloto_fg() -> str:
            return copilot_prompt()

        @srv.prompt(name="agentes_fg", description="Prompt del equipo de 11 agentes Grok: votos, G-JUDGE, ronda, "
                                                   "revisión y la ficha para diseñar agentes nuevos.")
        def agentes_fg() -> str:
            return agents_prompt()

        @srv.tool(description="La operación MÁS RENTABLE ahora según el equipo de agentes de Grok: G-SCOUT elige "
                              "candidatas A/B, la mesa corre cada una, los G-* votan y G-JUDGE decide; se ordena por "
                              "veredicto, ganancia esperada neta (EV) y P(TP1). No abre nada. top = cuántas criptos revisar "
                              "(1-8); ambos_lados = probar LONG y SHORT.", annotations=ro)
        def fg_mejor_operacion(top: int = 5, ambos_lados: bool = False) -> dict:
            out = self.pick.find(max(1, min(8, int(top or 5))), bool(ambos_lados))
            best = out.get("mejor")
            self._log("fg_mejor_operacion", {"top": top, "ambos_lados": ambos_lados},
                      f"{best['veredicto']} {best['direccion']} {best['cripto']} · EV {best['ev_usd']}" if best else "sin datos",
                      ["SPOTTER", "WHALE", "FLOW", "FUNDING", "GATE", "PRIOR", "PRICER", "EDGE", "KELLY"])
            slim = [{k: r[k] for k in ("id", "cripto", "direccion", "veredicto", "suma", "prob_tp1", "ventaja_r", "ev_usd")}
                    for r in out["ranking"]]
            return {"mejor": best, "ranking": slim, "nota": out["nota"], "modo": "solo análisis (no abre)"}

        @srv.tool(description="Candidatas para que TÚ elijas la operación más rentable: FG VIEW corre la mesa de 12 "
                              "agentes en varias criptos y te da sus números (P(TP1), ventaja, ganancia esperada, P(SL), "
                              "plan con entrada/SL/TPs, votos de la mesa). Luego manda tu elección con fg_pick_elige. "
                              "top = cuántas criptos (1-8); ambos_lados = probar LONG y SHORT; simbolo = una en concreto.",
                  annotations=ro)
        def fg_pick_datos(top: int = 5, ambos_lados: bool = False, simbolo: str = "") -> dict:
            out = self.pick.brief(max(1, min(8, int(top or 5))), bool(ambos_lados), str(simbolo or "").strip())
            self._log("fg_pick_datos", {"top": top, "ambos_lados": ambos_lados, "simbolo": simbolo},
                      f"{len(out['candidatas'])} candidatas para que elija Grok",
                      ["SPOTTER", "WHALE", "FLOW", "FUNDING", "GATE", "PRIOR", "PRICER", "EDGE", "KELLY"])
            return {**out, "siguiente_paso": "Elige una con fg_pick_elige(ronda_id, veredicto, confianza, lo_esperado, "
                                             "razones, riesgos, movimiento_esperado_pct, horizonte_min).",
                    "nota": "El plan y las probabilidades son de FG VIEW: no inventes precios. Caduca en 20 min."}

        @srv.tool(description="TU elección de la operación más rentable (la toma la página GROK PICK). ronda_id es el id "
                              "de una candidata de fg_pick_datos. veredicto: APRUEBA, PRECAUCION o VETA. lo_esperado: qué "
                              "esperas que pase, con números. No abre ninguna operación: el usuario decide.",
                  annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
        def fg_pick_elige(ronda_id: int, veredicto: str, confianza: int, lo_esperado: str,
                          razones: list[str] | None = None, riesgos: list[str] | None = None,
                          movimiento_esperado_pct: float | None = None, horizonte_min: int | None = None) -> dict:
            out = self.pick.submit(ronda_id, veredicto, confianza, lo_esperado, razones, riesgos,
                                   movimiento_esperado_pct, horizonte_min)
            self._log("fg_pick_elige", {"ronda_id": ronda_id, "veredicto": veredicto},
                      out.get("elegida") or out.get("error") or "sin datos", ["TAKER"])
            return out

        return srv

    def app(self):
        from mcp.server.transport_security import TransportSecuritySettings

        token = self.state()["token"]
        # Protección por token secreto en la ruta; el Host lo pone el túnel (dominio público), por eso no se filtra Host
        app = self.build().streamable_http_app(
            streamable_http_path=f"/mcp/{token}", stateless_http=True, json_response=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
        from engine.fgview.webapp import routes

        app.router.routes.extend(routes(self, token))  # 📱 App de iPhone en el mismo servidor y túnel
        return app

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, port: int | None = None) -> str:
        if self.running():
            return "ya estaba encendido"
        import uvicorn

        port = int(port or self.state()["port"])
        self.set(port=port)
        cfg = uvicorn.Config(self.app(), host="127.0.0.1", port=port, log_level="warning", lifespan="on")
        self._server = uvicorn.Server(cfg)
        self._thread = threading.Thread(target=self._server.run, name="fg-grok-mcp", daemon=True)
        self._thread.start()
        for _ in range(50):
            if getattr(self._server, "started", False):
                return f"encendido en http://127.0.0.1:{port}"
            time.sleep(0.1)
        return "arrancando…"

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server, self._thread = None, None

    def local_url(self) -> str:
        st = self.state()
        return f"http://127.0.0.1:{st['port']}/mcp/{st['token']}"

    def public_url(self) -> str | None:
        st = self.state()
        base = (st.get("tunnel") or "").strip().rstrip("/")
        return f"{base}/mcp/{st['token']}" if base.startswith("https://") else None

    def app_url(self, local: bool = False) -> str | None:
        st = self.state()
        base = f"http://127.0.0.1:{st['port']}" if local else (st.get("tunnel") or "").strip().rstrip("/")
        return f"{base}/app/{st['token']}/" if (local or base.startswith("https://")) else None


def main() -> None:  # pragma: no cover - servidor independiente: python -m engine.fgview.grok_mcp
    from engine.data_feeder import DataFeeder
    from engine.database import Database
    from engine.fgview import FGServices

    svc = FGServices.create(DataFeeder(), db=Database())
    br = GrokBridge(svc)
    print("FG VIEW · servidor MCP para Grok")
    print(br.start())
    print("Dirección local :", br.local_url())
    print("Abre tu túnel (cloudflared tunnel --url http://127.0.0.1:%d) y pega en grok.com la dirección https://…/mcp/<token>"
          % br.state()["port"])
    try:
        while br.running():
            time.sleep(1)
    except KeyboardInterrupt:
        br.stop()


if __name__ == "__main__":  # pragma: no cover
    main()
