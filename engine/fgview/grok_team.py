"""
engine/fgview/grok_team.py
==========================
Equipo Grok: 11 agentes, votos de −2 a +2 y G-JUDGE en código.

G-JUDGE aplica estas reglas, en este orden, y no hace ninguna otra cosa:

1. Si el COACH dice que hay pausa (racha, stop del día, límites, hora límite) → VETA
   («pausado por tus reglas»). No se salta al COACH.
2. Si la mesa dijo NO OPERAR, o G-TRAP votó −2 → VETA.
3. Si la suma de los seis votos es ≥ +4 y ninguno votó −2 → APRUEBA.
4. Si la suma está entre +1 y +3 → PRECAUCION.
5. Si la suma es ≤ 0 → VETA.
6. Suma ≥ +4 con algún −2 que no sea ya el veto de G-TRAP → PRECAUCION
   (la regla de APRUEBA exige cero votos −2; no se inventa un APRUEBA).

G-JUDGE no cambia el tamaño ni el stop. Si se abre, es el plan de la tarjeta
(con el recorte de FOMO que ya trae) y solo en papel. Un dato que no está es «n/a».
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

from engine.fgview.copilot import _parse, _utc
from engine.position_simulator import fmt_px

# Los seis que votan. G-SCOUT elige, G-RISK trae el plan, G-JUDGE decide,
# G-EXEC registra y G-WATCH mira lo abierto: esos cinco no entran en la suma.
VOTERS = ("G-MARKET", "G-WHALE", "G-FLOW", "G-FUNDING", "G-TRAP", "G-ODDS")

# Umbrales de los votos. Viven aquí para que una revisión los pueda señalar.
# Ningún camino de este módulo los modifica.
MKT_MILD = 0.4       # % de 1 h a favor → +1; en contra → −1
MKT_STRONG = 1.5     # % de 1 h, y 4 h del mismo lado → +2 (en contra, −2)
DESK_MILD = 0.15     # voto −1..+1 de la mesa
DESK_STRONG = 0.60
ODDS_P_BAD = 50.0
ODDS_P_OK = 55.0
ODDS_P_STRONG = 65.0
ODDS_E_OK = 0.05
ODDS_E_STRONG = 0.15
TRAP_Z = 2.5         # Mismo corte del Spotter: |z| > 2.5 a favor es entrada tarde
TRAP_POINTS = 4      # Squeeze de 4 puntos o más, sin ser ALTO, es −1

TEAM = (
    {"key": "G-SCOUT", "pregunta": "¿Qué criptos vale la pena mirar ahora?",
     "herramientas": ("fg_escanear",), "vota": False},
    {"key": "G-MARKET", "pregunta": "¿Cómo está el mercado: tendencia, momentum, volatilidad?",
     "herramientas": ("fg_mercado",), "vota": True},
    {"key": "G-WHALE", "pregunta": "¿Qué hacen las ballenas y los top traders?",
     "herramientas": ("fg_mercado", "fg_agente"), "vota": True},
    {"key": "G-FLOW", "pregunta": "¿Quién empuja a mercado y entra o sale dinero (OI)?",
     "herramientas": ("fg_mercado", "fg_agente"), "vota": True},
    {"key": "G-FUNDING", "pregunta": "¿La gente ya está demasiado de un lado? ¿Viene un cobro?",
     "herramientas": ("fg_mercado", "fg_agente"), "vota": True},
    {"key": "G-TRAP", "pregunta": "¿Es una trampa? Squeeze, entrada tarde, precio estirado",
     "herramientas": ("fg_agente",), "vota": True},
    {"key": "G-ODDS", "pregunta": "¿Qué tan probable es que funcione y cuánto se gana neto?",
     "herramientas": ("fg_correr_mesa", "fg_agente"), "vota": True},
    {"key": "G-RISK", "pregunta": "¿Cuánto arriesgar, dónde va el stop, cabe en las reglas?",
     "herramientas": ("fg_tarjeta", "fg_coach", "fg_reglas"), "vota": False},
    {"key": "G-JUDGE", "pregunta": "¿APRUEBA, PRECAUCION o VETA?",
     "herramientas": (), "vota": False},
    {"key": "G-EXEC", "pregunta": "¿Quedó registrada y, si aprueba, abierta en papel?",
     "herramientas": ("grok_veredicto",), "vota": False},
    {"key": "G-WATCH", "pregunta": "¿Qué pasa con lo abierto? ¿Cómo voy?",
     "herramientas": ("fg_posiciones", "fg_resultados", "fg_resumen"), "vota": False},
)


def normalize(symbol: str) -> tuple[str, str]:
    sym = (symbol or "").upper().replace("USDT", "").replace("/", "").replace("-", "").strip()
    if not sym:
        return "", ""
    if sym == "DEMO":
        return "DEMO/USDT", "demo"
    return f"{sym}/USDT", "crypto"


def fmt_vote(value: int) -> str:
    value = max(-2, min(2, int(value)))
    if value > 0:
        return f"+{value}"
    if value < 0:
        return f"−{abs(value)}"
    return "0"


def fmt_sum(value: int) -> str:
    if value > 0:
        return f"+{value}"
    if value < 0:
        return f"−{abs(value)}"
    return "0"


def _finite(value):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        number = float(value)
        return None if not math.isfinite(number) else value
    return value


def _json_val(value):
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:  # noqa: BLE001
            pass
    value = _finite(value)
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, float):
        return round(float(value), 6)
    return str(value)


def _texto(value, unit: str = "") -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "sí" if value else "no"
    if isinstance(value, float):
        return f"{value:.4g}{unit}"
    return f"{value}{unit}"


def reading(nombre: str, valor, fuente: str, leido: str, unidad: str = "") -> dict:
    valor = _json_val(valor)
    return {"nombre": nombre, "valor": valor, "fuente": fuente or "sin datos", "leido": leido,
            "unidad": unidad, "texto": _texto(valor, unidad)}


def _pct(value, digits: int = 2, signed: bool = True) -> str:
    if value is None:
        return "n/a"
    number = float(value)
    sign = "+" if signed and number > 0 else ("−" if number < 0 else "")
    return f"{sign}{abs(number):.{digits}f}%"


def _px(value) -> str:
    if value is None:
        return "n/a"
    try:
        return "$" + fmt_px(float(value))
    except (TypeError, ValueError):
        return "n/a"


def _agent(dec, name: str):
    if dec is None:
        return None
    return next((r for r in dec.results if r.agent == name), None)


def _chg(df, bars: int):
    if df is None or len(df) <= bars:
        return None
    last = float(df["close"].iloc[-1])
    prev = float(df["close"].iloc[-bars - 1])
    if prev == 0:
        return None
    return (last / prev - 1.0) * 100.0


def scale_desk_vote(value: float | None, blocked: bool) -> int:
    """Pasa el voto −1..+1 de un agente de la mesa a −2..+2. Un bloqueo es −2."""
    if blocked:
        return -2
    if value is None:
        return 0
    v = float(value)
    if v <= -DESK_STRONG:
        return -2
    if v <= -DESK_MILD:
        return -1
    if v >= DESK_STRONG:
        return 2
    if v >= DESK_MILD:
        return 1
    return 0


@dataclass
class JudgeResult:
    veredicto: str
    suma: int
    porque: str
    confianza: int
    regla: str

    def as_dict(self) -> dict:
        return {"veredicto": self.veredicto, "suma": int(self.suma), "porque": self.porque,
                "confianza": int(self.confianza), "regla": self.regla}


class GJudge:
    """Reglas fijas. No recibe tamaño ni stop, y no los devuelve."""

    @staticmethod
    def decide(votes: dict | None, *, paused: bool, desk_verdict: str | None,
               direction: str | None = None) -> JudgeResult:
        clean: dict[str, int] = {}
        for key in VOTERS:
            raw = (votes or {}).get(key, 0)
            if isinstance(raw, dict):
                raw = raw.get("voto", 0)
            try:
                number = int(raw)
            except (TypeError, ValueError):
                number = 0
            clean[key] = max(-2, min(2, number))
        total = int(sum(clean.values()))
        trap = clean["G-TRAP"]
        hard_against = any(v <= -2 for v in clean.values())
        if paused:
            return JudgeResult("VETA", total, "pausado por tus reglas", 90, "coach")
        if desk_verdict == "NO OPERAR" or trap <= -2:
            why = "la mesa dijo NO OPERAR" if desk_verdict == "NO OPERAR" else "G-TRAP votó −2"
            return JudgeResult("VETA", total, why, 88, "veto")
        if not direction:
            return JudgeResult("VETA", total, "sin dirección de la mesa", 80, "direccion")
        if not desk_verdict:
            return JudgeResult("VETA", total, "sin veredicto de la mesa", 80, "mesa")
        if total >= 4 and not hard_against:
            conf = max(0, min(100, 60 + (total - 4) * 4))
            return JudgeResult("APRUEBA", total, f"suma {fmt_sum(total)} y ningún voto −2", conf, "aprueba")
        if 1 <= total <= 3:
            conf = max(0, min(100, 40 + total * 5))
            return JudgeResult("PRECAUCION", total, f"suma {fmt_sum(total)}", conf, "precaucion")
        if total <= 0:
            conf = max(0, min(100, 70 + max(0, -total) * 3))
            return JudgeResult("VETA", total, f"suma {fmt_sum(total)}", conf, "suma")
        # Suma alta, pero alguien votó −2 y no era el caso de G-TRAP (ese ya salió arriba).
        return JudgeResult("PRECAUCION", total, f"suma {fmt_sum(total)} pero hay un voto −2: no se aprueba",
                           50, "disenso")


@dataclass
class GrokVote:
    agente: str
    voto: int
    linea: str
    numeros: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"voto": int(self.voto), "linea": self.linea, "numeros": list(self.numeros)}


@dataclass
class RoundContext:
    services: object
    symbol: str
    market: str
    direction: str | None
    decision: object
    card: dict
    flow: object
    positioning: object
    paused: str | None
    read_at: str
    snap_at: str
    plan: dict | None
    fomo: int | None


class _Voter:
    key = ""

    def readings(self, ctx: RoundContext) -> list[dict]:
        return []

    def vote(self, ctx: RoundContext) -> GrokVote:
        raise NotImplementedError


class MarketVoter(_Voter):
    key = "G-MARKET"

    def readings(self, ctx: RoundContext) -> list[dict]:
        snap = getattr(ctx.decision, "snap", None)
        df = snap.df5 if snap is not None else None
        at = ctx.snap_at
        atr = None
        if snap is not None and snap.atr5 and snap.price:
            atr = snap.atr5 / snap.price * 100.0
        return [
            reading("cambio_1h_pct", _chg(df, 12), "velas 5m FG VIEW", at, "%"),
            reading("cambio_4h_pct", _chg(df, 48), "velas 5m FG VIEW", at, "%"),
            reading("atr5_pct", atr, "velas 5m FG VIEW · ATR(14)", at, "%"),
        ]

    def vote(self, ctx: RoundContext) -> GrokVote:
        nums = self.readings(ctx)
        by = {n["nombre"]: n["valor"] for n in nums}
        ch1, ch4 = by.get("cambio_1h_pct"), by.get("cambio_4h_pct")
        d = 1 if ctx.direction == "LONG" else -1 if ctx.direction == "SHORT" else None
        aligned = None if ch1 is None or d is None else ch1 * d
        aligned4 = None if ch4 is None or d is None else ch4 * d
        if aligned is None:
            voto = 0
        elif aligned <= -MKT_STRONG:
            voto = -2
        elif aligned <= -MKT_MILD:
            voto = -1
        elif aligned >= MKT_STRONG and aligned4 is not None and aligned4 > 0:
            voto = 2
        elif aligned >= MKT_MILD:
            voto = 1
        else:
            voto = 0
        linea = f"1h {_pct(ch1)} · 4h {_pct(ch4)} · ATR5 {_pct(by.get('atr5_pct'), signed=False)}"
        return GrokVote(self.key, voto, linea, nums)


class WhaleVoter(_Voter):
    key = "G-WHALE"

    def readings(self, ctx: RoundContext) -> list[dict]:
        flow, pos = ctx.flow, ctx.positioning
        looked = ctx.market == "crypto"
        src = "Binance fapi · aggTrades" if looked else "sin datos · DEMO no consulta Binance"
        top_src = "Binance fapi · topLongShortPositionRatio" if looked else "sin datos · DEMO no consulta Binance"
        top = getattr(pos, "top_long_pct", None) if pos is not None else None
        return [
            reading("ballenas_compran_usd", getattr(flow, "whale_buy_usd", None), src, ctx.read_at, " USDT"),
            reading("ballenas_venden_usd", getattr(flow, "whale_sell_usd", None), src, ctx.read_at, " USDT"),
            reading("ballenas_n", None if flow is None or flow.whale_buy_usd is None else flow.whale_n, src, ctx.read_at),
            reading("top_traders_largos_pct", top, top_src, ctx.read_at, "%"),
        ]

    def vote(self, ctx: RoundContext) -> GrokVote:
        nums = self.readings(ctx)
        desk = _agent(ctx.decision, "WHALE")
        blocked = bool(desk and desk.status == "block")
        value = desk.value if desk is not None else None
        voto = scale_desk_vote(value, blocked)
        buy, sell, top = nums[0]["texto"], nums[1]["texto"], nums[3]["texto"]
        if all(n["valor"] is None for n in nums):
            linea = "n/a · sin datos de ballenas"
        else:
            linea = f"ballenas compran {buy} / venden {sell} · top traders {top} largos"
            if blocked:
                linea += " · en contra"
        return GrokVote(self.key, voto, linea, nums)


class FlowVoter(_Voter):
    key = "G-FLOW"

    def readings(self, ctx: RoundContext) -> list[dict]:
        flow = ctx.flow
        snap = getattr(ctx.decision, "snap", None)
        df = snap.df5 if snap is not None else None
        if flow is not None and flow.taker_ratio is not None:
            ratio, src = flow.taker_ratio, "Binance fapi · takerlongshortRatio"
        elif df is not None and len(df) >= 6:
            w = df.iloc[-6:]
            up = float(w.loc[w["close"] >= w["open"], "volume"].sum())
            dn = float(w.loc[w["close"] < w["open"], "volume"].sum())
            ratio, src = (up / dn if dn > 0 else 2.0), "FG VIEW · velas 5m (aprox. taker)"
        else:
            ratio, src = None, "sin datos"
        oi = flow.oi_change_pct if flow is not None else None
        oi_src = "Binance fapi · openInterestHist" if oi is not None else (
            "sin datos · DEMO no consulta Binance" if ctx.market != "crypto" else "sin datos")
        return [
            reading("taker_compra_venta", ratio, src, ctx.snap_at if "velas" in src else ctx.read_at),
            reading("open_interest_1h_pct", oi, oi_src, ctx.read_at, "%"),
        ]

    def vote(self, ctx: RoundContext) -> GrokVote:
        nums = self.readings(ctx)
        desk = _agent(ctx.decision, "FLOW")
        voto = scale_desk_vote(desk.value if desk else None, bool(desk and desk.status == "block"))
        taker, oi = nums[0], nums[1]
        if taker["valor"] is None and oi["valor"] is None:
            linea = "n/a · sin datos de flujo"
        else:
            linea = f"taker {taker['texto']} ({taker['fuente']}) · OI 1h {_pct(oi['valor'])}"
        return GrokVote(self.key, voto, linea, nums)


class FundingVoter(_Voter):
    key = "G-FUNDING"

    def readings(self, ctx: RoundContext) -> list[dict]:
        flow = ctx.flow
        snap = getattr(ctx.decision, "snap", None)
        if flow is not None and flow.funding is not None:
            rate, src = flow.funding, "Binance fapi · premiumIndex"
        elif snap is not None and snap.funding is not None:
            rate, src = snap.funding, "snapshot FG VIEW"
        else:
            rate, src = None, "sin datos"
        mins = flow.funding_min if flow is not None else None
        mins_src = src if mins is not None else ("sin datos · DEMO no consulta Binance" if ctx.market != "crypto" else "sin datos")
        return [
            reading("funding_pct", None if rate is None else rate * 100.0, src, ctx.read_at, "%"),
            reading("minutos_al_cobro", mins, mins_src, ctx.read_at, " min"),
        ]

    def vote(self, ctx: RoundContext) -> GrokVote:
        nums = self.readings(ctx)
        desk = _agent(ctx.decision, "FUNDING")
        voto = scale_desk_vote(desk.value if desk else None, bool(desk and desk.status == "block"))
        rate, mins = nums[0], nums[1]
        if rate["valor"] is None and mins["valor"] is None:
            linea = "n/a · sin dato de funding"
        else:
            cobro = "n/a" if mins["valor"] is None else f"{mins['valor']:.0f} min"
            linea = f"funding {_pct(rate['valor'], 4)} · cobro {cobro}"
        return GrokVote(self.key, voto, linea, nums)


class TrapVoter(_Voter):
    key = "G-TRAP"

    def readings(self, ctx: RoundContext) -> list[dict]:
        raw = getattr(ctx.decision, "raw", {}) or {}
        gate = _agent(ctx.decision, "GATE")
        risk = None
        if gate is not None:
            for word in ("alto", "medio", "bajo"):
                if f"({word})" in gate.headline:
                    risk = word
                    break
        return [
            reading("squeeze_puntos", raw.get("squeeze"), "FG DESK · GATE", ctx.read_at),
            reading("squeeze_riesgo", risk, "FG DESK · GATE", ctx.read_at),
            reading("vwap_z", raw.get("z"), "FG DESK · VWAP z", ctx.snap_at),
            reading("spotter", None if _agent(ctx.decision, "SPOTTER") is None else _agent(ctx.decision, "SPOTTER").status,
                    "FG DESK · SPOTTER", ctx.read_at),
        ]

    def vote(self, ctx: RoundContext) -> GrokVote:
        nums = self.readings(ctx)
        by = {n["nombre"]: n["valor"] for n in nums}
        points, risk, z = by.get("squeeze_puntos"), by.get("squeeze_riesgo"), by.get("vwap_z")
        gate = _agent(ctx.decision, "GATE")
        late = False
        if z is not None and ctx.direction == "LONG" and z > TRAP_Z:
            late = True
        if z is not None and ctx.direction == "SHORT" and z < -TRAP_Z:
            late = True
        spot = _agent(ctx.decision, "SPOTTER")
        if spot is not None and "estirado" in (spot.headline or ""):
            late = True
        if gate is not None and gate.status == "block":
            voto = -2
        elif late:
            voto = -2
        elif points is None and z is None:
            voto = 0
        elif points is not None and points >= TRAP_POINTS:
            voto = -1
        elif points is not None and points <= 1 and gate is not None and gate.status == "pass" and not late:
            voto = 1
        else:
            voto = 0
        if points is None and z is None and risk is None:
            linea = "n/a · sin lectura de squeeze ni de VWAP"
        else:
            pts = "n/a" if points is None else f"{points:g}"
            linea = f"squeeze {pts} pts ({risk or 'n/a'}) · VWAP z {_texto(None if z is None else round(float(z), 2))}"
            if late:
                linea += " · precio estirado"
            if gate is not None and gate.status == "block":
                linea += " · GATE bloquea"
        return GrokVote(self.key, voto, linea, nums)


class OddsVoter(_Voter):
    key = "G-ODDS"

    def readings(self, ctx: RoundContext) -> list[dict]:
        dec = ctx.decision
        br = (ctx.card or {}).get("base_rate") or {}
        n = br.get("n")
        return [
            reading("p_tp1_pct", None if dec is None or dec.probability is None else dec.probability,
                    "FG DESK · PRICER", ctx.read_at, "%"),
            reading("ventaja_R", None if dec is None or dec.edge_r is None else dec.edge_r,
                    "FG DESK · EDGE", ctx.read_at, "R"),
            reading("tasa_base_n", 0 if n is None else n, "SCRIBE", ctx.read_at),
        ]

    def vote(self, ctx: RoundContext) -> GrokVote:
        nums = self.readings(ctx)
        p, edge, n = nums[0]["valor"], nums[1]["valor"], nums[2]["valor"] or 0
        if p is None and edge is None:
            voto = 0
        elif (p is not None and p < ODDS_P_BAD) or (edge is not None and edge < 0):
            voto = -2
        elif (p is not None and p < ODDS_P_OK) or (edge is not None and edge < ODDS_E_OK):
            voto = -1
        elif p is not None and edge is not None and p >= ODDS_P_STRONG and edge >= ODDS_E_STRONG:
            voto = 2
        elif p is not None and edge is not None and p >= ODDS_P_OK and edge >= ODDS_E_OK:
            voto = 1
        else:
            voto = 0
        p_txt = "n/a" if p is None else f"{float(p):.1f}%"
        e_txt = "n/a" if edge is None else f"{float(edge):+.2f}R"
        linea = f"P(TP1) {p_txt} · ventaja {e_txt} · tasa base n={int(n)}"
        return GrokVote(self.key, voto, linea, nums)


VOTER_TYPES = (MarketVoter, WhaleVoter, FlowVoter, FundingVoter, TrapVoter, OddsVoter)


def render_report(rnd: dict) -> str:
    """El formato fijo del reporte. Lo que no está en la ronda sale como n/a."""
    cripto = rnd.get("cripto") or "n/a"
    direccion = rnd.get("direccion") or "n/a"
    hora = rnd.get("hora") or "n/a"
    lines = [f"GROK AGENTS · {cripto} {direccion} · {hora}"]
    votos = rnd.get("votos") or {}
    for key in VOTERS:
        item = votos.get(key) or {}
        voto = item.get("voto", 0)
        linea = item.get("linea") or "n/a"
        lines.append(f"{key:<9} {fmt_vote(voto)} · {linea}")
    plan, fomo = rnd.get("plan"), rnd.get("fomo")
    fomo_txt = "n/a" if fomo is None else str(int(fomo))
    if plan and plan.get("entry") is not None and plan.get("sl") is not None and plan.get("tp1") is not None:
        sl_pct = plan.get("sl_pct")
        sl_txt = "n/a" if sl_pct is None else f"{float(sl_pct):.2f}"
        risk = plan.get("risk_usd")
        risk_txt = "n/a" if risk is None else f"{float(risk):.2f}"
        lev = plan.get("leverage")
        lev_txt = "n/a" if lev is None else f"{lev}"
        lines.append(f"{'G-RISK':<9} plan: entrada {plan.get('entry_txt') or _px(plan.get('entry'))} · "
                     f"SL {plan.get('sl_txt') or _px(plan.get('sl'))} (−{sl_txt}%) · "
                     f"TP1 {plan.get('tp1_txt') or _px(plan.get('tp1'))} · {lev_txt}x · "
                     f"riesgo ${risk_txt} · FOMO {fomo_txt}/10")
    else:
        lines.append(f"{'G-RISK':<9} plan: n/a · FOMO {fomo_txt}/10")
    judge = rnd.get("juez") or {}
    lines.append(f"{'G-JUDGE':<9} {judge.get('veredicto') or 'n/a'} · suma {fmt_sum(int(judge.get('suma') or 0))} · "
                 f"confianza {judge.get('confianza') if judge.get('confianza') is not None else 'n/a'}% · "
                 f"{judge.get('porque') or 'n/a'}")
    lines.append(f"{'G-EXEC':<9} {rnd.get('ejecucion') or 'n/a'}")
    return "\n".join(lines)


def _shrink_ticket(ticket, factor: float):
    """Recorta el tamaño (FOMO). Nunca lo sube y nunca toca entrada, stop, TPs ni apalancamiento."""
    try:
        f = float(factor)
    except (TypeError, ValueError):
        f = 1.0
    if not math.isfinite(f):
        f = 1.0
    f = min(1.0, max(0.0, f))
    if f <= 0:
        return None
    if f >= 0.999:
        return replace(ticket, strategy="GROK · PAPEL")
    return replace(ticket, qty=ticket.qty * f, notional=ticket.notional * f, margin=ticket.margin * f,
                   fees=ticket.fees * f, sl_pnl=ticket.sl_pnl * f, tp_pnl=[x * f for x in (ticket.tp_pnl or [])],
                   strategy="GROK · PAPEL")


class GrokTeam:
    """El equipo, de punta a punta: datos, votos, juez, papel y revisión."""

    KEY = "grok_rounds"
    MAX = 400

    def __init__(self, services) -> None:
        self.s = services
        self.voters = [cls() for cls in VOTER_TYPES]
        self.last_dec = None
        self._mem: dict = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- estado
    def _db(self):
        return getattr(getattr(self.s, "positions", None), "db", None)

    def _load(self) -> dict:
        db = self._db()
        data = (db.get_state(self.KEY, {}) if db is not None else self._mem) or {}
        return {"rounds": list(data.get("rounds") or [])}

    def _save_state(self, st: dict) -> None:
        st["rounds"] = list(st.get("rounds") or [])[-self.MAX:]
        with self._lock:
            db = self._db()
            if db is not None:
                db.set_state(self.KEY, st)
            else:
                self._mem = st

    def rounds(self) -> list[dict]:
        return self._load()["rounds"]

    def latest(self) -> dict | None:
        rows = self.rounds()
        return rows[-1] if rows else None

    def get(self, ronda_id) -> dict | None:
        if ronda_id in (None, ""):
            return self.latest()
        try:
            wanted = int(ronda_id)
        except (TypeError, ValueError):
            return None
        return next((r for r in self.rounds() if r.get("id") == wanted), None)

    def save_round(self, rnd: dict) -> dict:
        st = self._load()
        rows = st["rounds"]
        rnd = dict(rnd)
        rnd["id"] = (max((r.get("id") or 0) for r in rows) + 1) if rows else 1
        rnd["reporte"] = render_report(rnd)
        rows.append(rnd)
        st["rounds"] = rows
        self._save_state(st)
        return rnd

    # ---------------------------------------------------------------- lecturas
    def _capital(self) -> float:
        lad = getattr(self.s, "ladder_desk", None)
        if lad is not None:
            stt = lad.status()
            if stt.enabled and not stt.paused:
                return float(stt.balance)
        budget = self.s.budget.status() if getattr(self.s, "budget", None) and self.s.budget.state().get("start") else None
        from engine.config import SETTINGS
        return float(budget.balance) if budget else float(SETTINGS.account_equity)

    def _hora(self) -> str:
        try:
            tz = self.s.copilot.rules.tz()
        except Exception:  # noqa: BLE001
            tz = timezone.utc
        return datetime.now(tz).strftime("%H:%M")

    def _flow(self, symbol: str, market: str):
        from engine.fgview.flowfeed import FlowSnapshot

        coin = symbol.split("/")[0]
        feed = getattr(getattr(self.s, "desk", None), "flow", None)
        if market != "crypto" or feed is None:
            return FlowSnapshot(coin)
        return feed.get(coin)

    def _positioning(self, symbol: str, market: str):
        if market != "crypto":
            return None
        rec = getattr(self.s, "recommender", None)
        if rec is None or not getattr(rec, "positioning", None):
            return None
        return rec.positioning.get(symbol.split("/")[0])

    def _plan(self, card: dict | None, fomo) -> dict | None:
        p = (card or {}).get("plan")
        if not p or p.get("entry") is None:
            return None
        return {"entry": p.get("entry"), "sl": p.get("sl"), "sl_pct": p.get("sl_pct"), "tp1": p.get("tp1"),
                "leverage": p.get("leverage"), "risk_usd": p.get("risk_usd"),
                "size_factor": p.get("size_factor", 1.0), "entry_txt": _px(p.get("entry")),
                "sl_txt": _px(p.get("sl")), "tp1_txt": _px(p.get("tp1"))}

    def _prepare(self, symbol: str, direction: str) -> RoundContext | dict:
        sym, market = normalize(symbol)
        if not sym:
            return {"error": "falta el símbolo"}
        direction_in = direction.upper() if direction and str(direction).upper() in ("LONG", "SHORT") else "Auto"
        try:
            dec = self.s.desk.run(sym, market, self._capital(), direction=direction_in, operable=True)
        except Exception as exc:  # noqa: BLE001 - sin datos no se inventa una ronda
            return {"error": f"sin datos: {exc}"[:180], "simbolo": sym, "mercado": market}
        self.last_dec = dec
        try:
            card = self.s.copilot.card(dec)
        except Exception as exc:  # noqa: BLE001
            card = {"fomo": None, "plan": None, "base_rate": {"n": 0}, "error": str(exc)[:160]}
        paused = None
        try:
            paused = self.s.copilot.coach.status().get("blocked")
        except Exception:  # noqa: BLE001
            paused = None
        snap = getattr(dec, "snap", None)
        snap_at = ctx_at = datetime.now(timezone.utc).isoformat()
        if snap is not None and getattr(snap, "fetched_at", None) is not None:
            fetched = snap.fetched_at
            snap_at = fetched.isoformat() if isinstance(fetched, datetime) else str(fetched)
        fomo = card.get("fomo") if isinstance(card, dict) else None
        return RoundContext(self.s, sym, market, dec.direction, dec, card, self._flow(sym, market),
                            self._positioning(sym, market), paused, ctx_at, snap_at, self._plan(card, fomo), fomo)

    def _votes(self, ctx: RoundContext) -> dict[str, dict]:
        out = {}
        for voter in self.voters:
            vote = voter.vote(ctx)
            out[vote.agente] = vote.as_dict()
        for key in VOTERS:
            out.setdefault(key, {"voto": 0, "linea": "n/a", "numeros": []})
        return out

    def _pack_inputs(self, ctx: RoundContext, votes: dict | None = None) -> dict:
        """Todo lo que cada agente lee, con fuente y hora. Sin voto si `votes` es None."""
        agentes = {}
        by_key = {v.key: v for v in self.voters}
        for meta in TEAM:
            key = meta["key"]
            block = {"pregunta": meta["pregunta"], "herramientas": list(meta["herramientas"]), "numeros": []}
            if key in by_key:
                block["numeros"] = by_key[key].readings(ctx)
            elif key == "G-SCOUT":
                block["numeros"] = [reading("candidata", ctx.symbol.split("/")[0], "pedida en la ronda", ctx.read_at)]
            elif key == "G-RISK":
                plan = ctx.plan or {}
                block["numeros"] = [
                    reading("entrada", plan.get("entry"), "Trade Card · plan", ctx.read_at),
                    reading("stop", plan.get("sl"), "Trade Card · plan", ctx.read_at),
                    reading("sl_pct", plan.get("sl_pct"), "Trade Card · plan", ctx.read_at, "%"),
                    reading("tp1", plan.get("tp1"), "Trade Card · plan", ctx.read_at),
                    reading("apalancamiento", plan.get("leverage"), "Trade Card · plan", ctx.read_at),
                    reading("riesgo_usd", plan.get("risk_usd"), "Trade Card · plan (ya con FOMO)", ctx.read_at, " USDT"),
                    reading("fomo", ctx.fomo, "COACH", ctx.read_at),
                    reading("pausado", bool(ctx.paused), "COACH · fg_coach", ctx.read_at),
                ]
            elif key == "G-JUDGE":
                block["numeros"] = [
                    reading("veredicto_mesa", ctx.decision.verdict if ctx.decision else None, "FG DESK", ctx.read_at),
                    reading("pausado", bool(ctx.paused), "COACH · fg_coach", ctx.read_at),
                ]
            elif key == "G-EXEC":
                block["numeros"] = [reading("modo", "papel", "FG VIEW · el conector no tiene órdenes reales", ctx.read_at)]
            elif key == "G-WATCH":
                try:
                    n_open = len(self.s.positions.all())
                except Exception:  # noqa: BLE001
                    n_open = None
                block["numeros"] = [reading("abiertas_papel", n_open, "FG VIEW · libro de papel", ctx.read_at)]
            if votes and key in votes:
                block["voto_calculado"] = votes[key]["voto"]
                block["linea"] = votes[key]["linea"]
            agentes[key] = block
        return {"simbolo": ctx.symbol, "direccion": ctx.direction, "mercado": ctx.market, "leido": ctx.read_at,
                "veredicto_mesa": ctx.decision.verdict if ctx.decision else None,
                "pausado_por_reglas": ctx.paused, "agentes": agentes,
                "nota": "Cada número trae fuente y hora. Lo que no se pudo leer es n/a."}

    def datos(self, symbol: str, direction: str = "Auto") -> dict:
        ctx = self._prepare(symbol, direction)
        if isinstance(ctx, dict):
            return {**ctx, "agentes": {}, "nota": "Sin datos: no se inventa ninguno."}
        return self._pack_inputs(ctx)

    def scout(self, limit: int = 3) -> list[dict]:
        coins = [c for t in self.s.tiers.tiers if t.key in ("A", "B") for c in t.coins]
        ranked = []
        capital = self._capital()
        for coin in coins:
            try:
                res, *_rest, wscore = self.s.desk.spot(f"{coin}/USDT", "crypto", capital)
                ranked.append({"cripto": coin, "puntos": round(float(wscore), 1), "spotter": res.status,
                               "dato": (res.headline or "")[:120]})
            except Exception as exc:  # noqa: BLE001
                ranked.append({"cripto": coin, "puntos": None, "spotter": "sin datos", "dato": str(exc)[:80]})
        ranked.sort(key=lambda r: (r["spotter"] == "pass", r["puntos"] if r["puntos"] is not None else -1), reverse=True)
        return ranked[: max(1, min(3, int(limit or 3)))]

    # ---------------------------------------------------------------- ronda
    def _open_paper(self, dec, factor: float) -> tuple[str, str | None]:
        """Abre solo en papel, con el plan tal cual. Si FG VIEW rechaza, se acepta."""
        ticket = getattr(dec, "ticket", None)
        if dec is None or ticket is None:
            return "rechazada por FG VIEW: no hay plan", None
        if dec.verdict == "NO OPERAR":
            return "rechazada por FG VIEW: la mesa dijo NO OPERAR", None
        sized = _shrink_ticket(ticket, factor)
        if sized is None:
            return "rechazada por FG VIEW: el tamaño del plan quedó en cero", None
        if (sized.stop_loss != ticket.stop_loss or sized.entry != ticket.entry or sized.leverage > ticket.leverage
                or sized.qty > ticket.qty + 1e-9):
            return "rechazada por FG VIEW: el plan no se puede alterar", None
        out = self.s.executor.submit(sized, confirmed=True, mode="paper")
        if out.get("status") == "filled":
            pos = out.get("position") or {}
            return "abierta en PAPEL", pos.get("id")
        reason = (out.get("problems") or [out.get("message") or "sin motivo"])[0]
        return f"rechazada por FG VIEW: {reason}", None

    def _blank(self, symbol: str, why: str) -> dict:
        sym, _market = normalize(symbol)
        cripto = (sym or symbol or "n/a").split("/")[0]
        votos = {key: {"voto": 0, "linea": "n/a", "numeros": []} for key in VOTERS}
        judge = JudgeResult("VETA", 0, why or "sin datos", 80, "datos")
        rnd = {"at": _utc().isoformat(), "hora": self._hora(), "simbolo": sym or None, "cripto": cripto,
               "direccion": None, "votos": votos, "suma": 0, "juez": judge.as_dict(), "plan": None, "fomo": None,
               "ejecucion": "registrada sin abrir", "posicion_id": None, "mesa": None, "pausado": None,
               "outcome": "n/a", "modo": "papel"}
        return rnd

    def _one(self, symbol: str, direction: str, abrir: bool) -> dict:
        ctx = self._prepare(symbol, direction)
        if isinstance(ctx, dict):
            return self._blank(ctx.get("simbolo") or symbol, ctx.get("error") or "sin datos")
        votes = self._votes(ctx)
        judge = GJudge.decide({k: v["voto"] for k, v in votes.items()}, paused=bool(ctx.paused),
                              desk_verdict=ctx.decision.verdict, direction=ctx.direction)
        ejecucion, pos_id = "registrada sin abrir", None
        if judge.veredicto == "APRUEBA" and abrir:
            factor = (ctx.plan or {}).get("size_factor", 1.0)
            ejecucion, pos_id = self._open_paper(ctx.decision, factor)
        rnd = {"at": ctx.read_at, "hora": self._hora(), "simbolo": ctx.symbol, "cripto": ctx.symbol.split("/")[0],
               "direccion": ctx.direction, "votos": votes, "suma": judge.suma, "juez": judge.as_dict(),
               "plan": ctx.plan, "fomo": ctx.fomo, "ejecucion": ejecucion, "posicion_id": pos_id,
               "mesa": ctx.decision.verdict, "pausado": ctx.paused, "outcome": None, "modo": "papel",
               "reglas_version": self.s.copilot.rules.state().get("version")}
        return self.save_round(rnd)

    def ronda(self, symbol: str = "", direction: str = "Auto", abrir_en_papel: bool = False) -> dict:
        abrir = bool(abrir_en_papel)
        if normalize(symbol)[0]:
            built = [self._one(symbol, direction, abrir)]
            candidatas = [{"cripto": built[0].get("cripto"), "nota": "pedida en la ronda"}]
        else:
            candidatas = self.scout(3)
            picked = [c for c in candidatas if c.get("spotter") == "pass"] or [
                c for c in candidatas if c.get("spotter") != "sin datos"]
            picked = picked[:3]
            if not picked:
                empty = "Nada aprobado esta ronda. Esperar también es operar bien."
                return {"reporte": empty, "rondas": [], "candidatas": candidatas, "nota": empty, "modo": "papel",
                        "por_cripto": []}
            built = [self._one(c["cripto"], direction, abrir) for c in picked]
        text = "\n\n".join(r.get("reporte") or render_report(r) for r in built)
        approved = any((r.get("juez") or {}).get("veredicto") == "APRUEBA" for r in built)
        nota = None if approved else "Nada aprobado esta ronda. Esperar también es operar bien."
        if nota:
            text = f"{text}\n\n{nota}" if text else nota
        one = built[0] if len(built) == 1 else None
        return {"reporte": text, "rondas": [r.get("id") for r in built], "candidatas": candidatas, "nota": nota,
                "modo": "papel", "por_cripto": built, "juez": None if one is None else one.get("juez"),
                "ejecucion": None if one is None else one.get("ejecucion"),
                "suma": None if one is None else one.get("suma"),
                "votos": None if one is None else one.get("votos")}

    def reporte(self, ronda_id=None) -> dict:
        rnd = self.get(ronda_id)
        if rnd is None:
            return {"reporte": "n/a", "nota": "Todavía no hay una ronda guardada."}
        text = rnd.get("reporte") or render_report(rnd)
        return {"reporte": text, "id": rnd.get("id"), "juez": rnd.get("juez"), "ejecucion": rnd.get("ejecucion"),
                "modo": "papel"}

    # ---------------------------------------------------------------- revisión
    def grade(self, after_min: int | None = None) -> int:
        """Mide rondas viejas con velas reales. No inventa un resultado si las velas no se pueden leer."""
        try:
            wait = int(after_min if after_min is not None else self.s.copilot.rules.values()["grade_after_min"])
        except Exception:  # noqa: BLE001
            wait = 240
        st = self._load()
        rows, n = st["rounds"], 0
        history = getattr(self.s, "history", None)
        for rnd in rows:
            if rnd.get("outcome"):
                continue
            plan = rnd.get("plan")
            if not plan or plan.get("entry") is None or plan.get("sl") is None or not rnd.get("direccion"):
                rnd["outcome"] = "n/a"
                n += 1
                continue
            t0 = _parse(rnd.get("at"))
            if t0 is None or history is None or _utc() - t0 < timedelta(minutes=wait):
                continue
            try:
                mk = "demo" if str(rnd.get("simbolo") or "").startswith("DEMO") else "crypto"
                df, _bm = history.get(rnd["simbolo"], mk, "5m", 3)
                hold = max(wait, int((plan.get("hold_min") or 45) * 2))
                window = df[(df.index > t0) & (df.index <= t0 + timedelta(minutes=hold))]
            except Exception:  # noqa: BLE001
                continue
            long = rnd["direccion"] == "LONG"
            outcome, tp1_hit = "sin_movimiento", False
            tp1, sl = float(plan["tp1"]) if plan.get("tp1") is not None else None, float(plan["sl"])
            for _, bar in window.iterrows():
                lo, hi = float(bar["low"]), float(bar["high"])
                hit_sl = lo <= sl if long else hi >= sl
                if hit_sl:
                    outcome = "gano" if tp1_hit else "perdio"
                    break
                if tp1 is not None and ((hi >= tp1) if long else (lo <= tp1)):
                    tp1_hit, outcome = True, "gano"
            rnd["outcome"] = outcome
            rnd["graded_at"] = _utc().isoformat()
            n += 1
        if n:
            self._save_state(st)
        return n

    @staticmethod
    def scoreboard(rounds: list[dict]) -> dict:
        stats = {key: {"aciertos": 0, "errores": 0, "neutros": 0} for key in VOTERS}
        used = 0
        for rnd in rounds or []:
            outcome = rnd.get("outcome")
            if outcome not in ("gano", "perdio"):
                continue
            used += 1
            votos = rnd.get("votos") or {}
            for key in VOTERS:
                raw = votos.get(key, 0)
                raw = raw.get("voto", 0) if isinstance(raw, dict) else raw
                try:
                    voto = int(raw)
                except (TypeError, ValueError):
                    voto = 0
                if voto == 0:
                    stats[key]["neutros"] += 1
                elif (voto > 0 and outcome == "gano") or (voto < 0 and outcome == "perdio"):
                    stats[key]["aciertos"] += 1
                else:
                    stats[key]["errores"] += 1

        def decisive(key: str) -> int:
            return stats[key]["aciertos"] + stats[key]["errores"]

        def accuracy(key: str) -> float:
            n = decisive(key)
            return stats[key]["aciertos"] / n if n else -1.0

        ranked = [key for key in VOTERS if decisive(key)]
        best = max(ranked, key=lambda k: (accuracy(k), stats[k]["aciertos"])) if ranked else None
        worst = min(ranked, key=lambda k: (accuracy(k), -stats[k]["errores"])) if ranked else None
        if best is not None and best == worst:
            if stats[best]["errores"] == 0:
                worst = None
            elif stats[best]["aciertos"] == 0:
                best = None
        if used == 0:
            suggestion = "Todavía no hay rondas calificadas (n=0). Esperar también es operar bien."
        elif best or worst:
            who_best = best or "n/a"
            who_worst = worst or "n/a"
            suggestion = (f"Sugerencia, no es un cambio: {who_best} acertó más y {who_worst} se equivocó más "
                          f"en {used} rondas con resultado. Los umbrales de voto no se mueven solos; tú decides.")
        else:
            suggestion = f"{used} rondas calificadas, pero todos los votos fueron 0. Nada que ajustar."
        return {"muestras": used, "agentes": stats, "acerto_mas": best, "equivoco_mas": worst,
                "sugerencia": suggestion}

    def _paper_view(self) -> dict:
        stats = self.s.autopilot.paper_stats()
        pf = stats.get("pf")
        if isinstance(pf, float) and not math.isfinite(pf):
            pf = None
        return {"operaciones": stats.get("trades"), "win_rate": stats.get("win_rate"), "profit_factor": pf,
                "drawdown_pct": stats.get("max_dd_pct"), "neto": stats.get("net"),
                "nota": "n/a" if not stats.get("trades") else None}

    def revision(self) -> dict:
        graded = self.grade()
        try:
            resumen = self.s.copilot.scribe.summary(self.s.copilot.coach, 7)
        except Exception as exc:  # noqa: BLE001
            resumen = f"n/a · {exc}"[:180]
        board = self.scoreboard(self.rounds())
        version = None
        try:
            version = self.s.copilot.rules.state().get("version")
        except Exception:  # noqa: BLE001
            version = None
        return {"calificadas_ahora": graded, "resultados": self._paper_view(), "resumen_semana": resumen,
                "agentes": board["agentes"], "muestras": board["muestras"], "acerto_mas": board["acerto_mas"],
                "equivoco_mas": board["equivoco_mas"], "sugerencia": board["sugerencia"],
                "reglas_version": version,
                "nota": "Sugerencia nada más. Las reglas no se tocan desde aquí."}
