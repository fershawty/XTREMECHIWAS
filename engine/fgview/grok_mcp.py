"""
engine/fgview/grok_mcp.py
=========================
🤖 GROK DESK · puente MCP: FG VIEW como servidor MCP para que los agentes de Grok (grok.com →
Connectors → Custom) usen la mesa de 12 agentes desde el chat.

Herramientas que ve Grok (TODAS de solo lectura o de PAPEL):
    fg_mercado          precio, cambio, ATR, funding, flujo y ballenas de una cripto
    fg_correr_mesa      corre los 12 agentes y devuelve el veredicto completo (no envía órdenes)
    fg_escanear         ranking del Spotter sobre las criptos de nivel A/B
    fg_agente           el detalle de un agente de la última corrida
    fg_posiciones       posiciones abiertas en papel
    fg_resultados       estadísticas del papel y de la escalera
    grok_veredicto      Grok deja su veredicto (APRUEBA/PRECAUCION/VETA) y, si quiere, abre en PAPEL

REGLAS DE SEGURIDAD (en código):
* Nunca hay órdenes reales por MCP: ni siquiera existe una herramienta para eso.
* grok_veredicto solo abre en PAPEL y solo si la mesa NO dijo ⛔ NO OPERAR (Grok puede frenar, no forzar).
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
}


def copilot_prompt() -> str:
    """El prompt del copiloto adaptado (docs/GROK_COPILOT_PROMPT.md), también dentro del ejecutable."""
    import sys
    from pathlib import Path

    for base in (Path(getattr(sys, "_MEIPASS", "")), Path(__file__).resolve().parents[2]):
        p = base / "docs" / "GROK_COPILOT_PROMPT.md"
        if p.is_file():
            return p.read_text(encoding="utf-8")
    return ""
INSTRUCTIONS = """FG VIEW es una terminal de análisis de futuros perpetuos USDT-M de Binance para operaciones cortas \
(15-30 min). Tiene una mesa de 12 agentes de código (Spotter, Whale, Flow, Funding, Gate, Prior, Pricer con 100,000 \
simulaciones, Edge, Kelly, Guard, Taker, Closer) que dan un veredicto OPERAR / ESPERAR / NO OPERAR con un plan \
(entrada, stop loss, take profits, apalancamiento, riesgo).

Sigue las 10 REGLAS DE VERDAD del prompt del copiloto (prompt «copiloto_fg»): nunca prometas ganancias, cada número \
con su fuente y edad, la tasa base siempre visible, ESPERAR/PASAR son respuestas normales, y cada respuesta termina con \
"Tu decisión. Tú presionas el botón.". Empieza por fg_tarjeta. Si el usuario suena alterado, usa fg_coach con su texto.
Usa fg_mercado y fg_correr_mesa para analizar; fg_agente para ver el detalle de un agente; fg_escanear para buscar la \
mejor cripto. Cuando tengas una conclusión, registra tu opinión con grok_veredicto. Todo es MODO PAPEL: no existen \
órdenes reales por aquí. No puedes cambiar el plan ni el stop; solo aprobar, pedir precaución o vetar. Responde al \
usuario en español claro: puede ser principiante. No prometas ganancias."""


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
        self.s = services
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
                 "mesa": dec.verdict if dec else None, "abierta": False}
        msg = "Veredicto registrado."
        if abrir_en_papel:
            if dec is None or dec.ticket is None:
                msg = "No hay plan de la mesa: corre fg_correr_mesa primero."
            elif dec.verdict == "NO OPERAR":
                msg = "La mesa dice ⛔ NO OPERAR: no se abre (Grok puede frenar, no forzar)."
            elif v != "APRUEBA":
                msg = "Solo se abre en papel con APRUEBA."
            else:
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

    def coach(self, texto: str = "") -> dict:
        cp = self.s.copilot
        out = {"estado": cp.coach.status()}
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

    # ------------------------------------------------------------- servidor MCP
    def build(self):
        from mcp.server.mcpserver import MCPServer
        from mcp.types import ToolAnnotations

        ro = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
        srv = MCPServer(name="FG VIEW · GROK DESK", instructions=INSTRUCTIONS, version="5.0")

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

        @srv.tool(description="Las reglas del usuario (rules.md versionado). Solo lectura.", annotations=ro)
        def fg_reglas() -> dict:
            return self.rules()

        @srv.tool(description="Resumen de SCRIBE: periodo 'dia' o 'semana' (operaciones, P&L, tarjetas por banda, "
                              "calificaciones A-D, fugas).", annotations=ro)
        def fg_resumen(periodo: str = "dia") -> dict:
            return self.summary(periodo)

        @srv.prompt(name="copiloto_fg", description="Prompt del copiloto FG VIEW para futuros de Binance (reglas de "
                                                    "verdad, Trade Card, COACH, preguntas y lo que nunca hace).")
        def copiloto_fg() -> str:
            return copilot_prompt()

        return srv

    def app(self):
        from mcp.server.transport_security import TransportSecuritySettings

        token = self.state()["token"]
        # Protección por token secreto en la ruta; el Host lo pone el túnel (dominio público), por eso no se filtra Host
        return self.build().streamable_http_app(
            streamable_http_path=f"/mcp/{token}", stateless_http=True, json_response=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

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
