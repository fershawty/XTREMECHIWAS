"""
engine/fgview/audit.py
======================
Ideas del copiloto adaptadas a futuros USDT-M.

No toca rules.md ni sus umbrales. Los pesos del score y los factores de tamaño
viven en settings aparte. Nada de aquí abre una orden, ni en papel ni en real:
solo calcula, avisa y sugiere. Una sugerencia no se aplica sola.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN

# Pesos del score (suman 100). No son reglas del usuario.
SCORE_WEIGHTS = {
    "mercado": 15,
    "flujo": 15,
    "funding": 10,
    "trampa": 20,
    "probabilidad": 25,
    "ventaja": 15,
}
SCORE_GREEN = 70
SCORE_YELLOW = 45
BAND_FACTOR = {"VERDE": Decimal("1"), "AMARILLO": Decimal("0.5"), "ROJO": Decimal("0.25"), "SKIP": Decimal("0")}
LIQ_RATIO = Decimal("3")
MAINTENANCE = Decimal("0.005")
STALE_S = 30
WATCH_MINUTES = 20


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts) -> datetime | None:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def age_seconds(at, now: datetime | None = None) -> int | None:
    stamp = _parse(at)
    if stamp is None:
        return None
    return max(0, int(((now or _utc()) - stamp).total_seconds()))


def sourced(name: str, value, source: str, at=None, *, now: datetime | None = None) -> dict:
    """Un número con fuente y edad. None se muestra como n/a, nunca como estimación."""
    age = age_seconds(at, now)
    missing = value is None or (isinstance(value, float) and not math.isfinite(value))
    return {"nombre": name, "valor": None if missing else value, "fuente": source or "sin datos",
            "edad_s": age, "texto": "n/a" if missing else _plain(value),
            "linea": _line(name, None if missing else value, source, age)}


def _plain(value) -> str:
    if isinstance(value, float):
        text = f"{value:.4g}"
        return text.replace("-", "−")
    if isinstance(value, str) and value.startswith("-"):
        return "−" + value[1:]
    return str(value)


def _line(name: str, value, source: str, age: int | None) -> str:
    if value is None:
        return f"{name} n/a · {source or 'sin datos'}"
    edad = "n/a" if age is None else f"{age}s"
    return f"{name} {_plain(value)} · {source} · {edad}"


def base_rate_note(n: int | None, tp1_pct=None, sl_pct=None, mediana=None) -> str:
    """Tasa base visible. Con menos de 30 casos avisa que no alcanza para confiar."""
    if not n:
        return "TASA BASE n=0, muy pocos para confiar."
    cuerpo = f"TASA BASE n={int(n)}"
    if tp1_pct is not None:
        cuerpo += f" · TP1 {tp1_pct}%"
    if sl_pct is not None:
        cuerpo += f" · SL {sl_pct}%"
    if mediana is not None:
        cuerpo += f" · mediana {mediana}"
    if int(n) < 30:
        cuerpo += ", muy pocos para confiar"
    return cuerpo + "."


def cent(value) -> Decimal:
    """Redondeo hacia abajo al centavo."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def _d(value) -> Decimal:
    return Decimal(str(value))


# -----------------------------------------------------------------------------
# G-TRAP / GATE
# -----------------------------------------------------------------------------
def _status_spread(spread_pct):
    if spread_pct is None:
        return "n/a"
    if spread_pct >= 0.15:
        return "fail"
    if spread_pct >= 0.05:
        return "warn"
    return "pass"


def _status_funding(funding_pct):
    if funding_pct is None:
        return "n/a"
    mag = abs(float(funding_pct))
    if mag >= 0.05:
        return "fail"
    if mag >= 0.02:
        return "warn"
    return "pass"


def _status_oi(oi_change, price_change, direction):
    """OI sube mientras el precio va en contra de la operación: warn. Falta un lado: n/a."""
    if oi_change is None or price_change is None or direction not in ("LONG", "SHORT"):
        return "n/a"
    against = (direction == "LONG" and price_change < 0) or (direction == "SHORT" and price_change > 0)
    if oi_change > 0 and against:
        return "warn" if abs(price_change) < 1 else "fail"
    return "pass"


def _status_wick(wick_ratio, volume_ratio):
    if wick_ratio is None or volume_ratio is None:
        return "n/a"
    if wick_ratio >= 0.6 and volume_ratio < 1:
        return "fail"
    if wick_ratio >= 0.45 and volume_ratio < 1.2:
        return "warn"
    return "pass"


def trap_checks(data: dict | None) -> dict:
    """Checks fijos. Cualquier fail veta. Tres warns avisan. Más de 4 n/a no puede quedar limpio."""
    d = data or {}
    rows = [
        ("spread", d.get("spread_pct"), "libro FG VIEW", _status_spread(d.get("spread_pct"))),
        ("profundidad", d.get("depth_usd"), "libro FG VIEW",
         "n/a" if d.get("depth_usd") is None else ("warn" if d.get("depth_usd") < 50_000 else "pass")),
        ("oi_precio", d.get("oi_change_pct"), "Binance fapi · openInterestHist",
         _status_oi(d.get("oi_change_pct"), d.get("price_change_pct"), d.get("direction"))),
        ("funding", d.get("funding_pct"), "Binance fapi · premiumIndex", _status_funding(d.get("funding_pct"))),
        ("liquidaciones", d.get("liq_against"), "Binance fapi · forceOrders",
         "n/a" if d.get("liq_against") is None else ("fail" if d.get("liq_against") else "pass")),
        ("mecha", d.get("wick_ratio"), "velas 5m FG VIEW", _status_wick(d.get("wick_ratio"), d.get("volume_ratio"))),
        ("volumen", d.get("trade_count"), "aggTrades",
         "n/a" if d.get("trade_count") is None else (
             "fail" if d.get("volume_ratio") and d["volume_ratio"] > 3 and d["trade_count"] < 20 else "pass")),
        ("mark", d.get("mark_gap_pct"), "Binance fapi · premiumIndex",
         "n/a" if d.get("mark_gap_pct") is None else ("fail" if abs(d["mark_gap_pct"]) >= 0.3 else (
             "warn" if abs(d["mark_gap_pct"]) >= 0.15 else "pass"))),
        ("compuerta", "bloquea" if d.get("gate_block") else ("estirado" if d.get("late") else "pasa"), "FG DESK · GATE",
         "fail" if d.get("gate_block") or d.get("late") else ("n/a" if d.get("gate_block") is None and d.get("late") is None else "pass")),
    ]
    checks = [{"nombre": n, "valor": v, "fuente": src, "estado": st} for n, v, src, st in rows]
    fails = sum(c["estado"] == "fail" for c in checks)
    warns = sum(c["estado"] == "warn" for c in checks)
    nas = sum(c["estado"] == "n/a" for c in checks)
    passed = sum(c["estado"] == "pass" for c in checks)
    if fails:
        voto, veredicto = -2, "trampa"
        porque = f"hay {fails} fail"
    elif nas > 4:
        voto, veredicto = 0, "desconocido"
        porque = f"{nas} checks en n/a: no puede quedar limpio"
    elif warns >= 3:
        voto, veredicto = -1, "warn"
        porque = f"{warns} warns"
    else:
        voto, veredicto = (1 if warns == 0 else 0), "limpio"
        porque = f"sin banderas rojas en {passed} de {len(checks)} checks"
    return {"checks": checks, "voto": voto, "veredicto": veredicto, "porque": porque,
            "fails": fails, "warns": warns, "n/a": nas,
            "linea": f"{veredicto} · {porque}"}


# -----------------------------------------------------------------------------
# Score por partes
# -----------------------------------------------------------------------------
def score_parts(parts: dict | None, weights: dict | None = None) -> dict:
    """Score 0–100. Una parte en n/a no se inventa: no entra y el peso se declara."""
    w = dict(SCORE_WEIGHTS)
    if weights:
        for key, value in weights.items():
            if key in w and value is not None:
                w[key] = float(value)
    detail = []
    got = total_w = 0.0
    for key, weight in w.items():
        raw = None if not parts else parts.get(key)
        if raw is None:
            detail.append({"parte": key, "crudo": None, "peso": weight, "aporte": None})
            continue
        raw = max(0.0, min(100.0, float(raw)))
        aporte = raw * weight / 100.0
        got += aporte
        total_w += weight
        detail.append({"parte": key, "crudo": round(raw, 2), "peso": weight, "aporte": round(aporte, 2)})
    score = None if total_w <= 0 else round(got * 100.0 / total_w, 1)
    trap = (parts or {}).get("trampa_estado")
    if trap in ("trampa", "fail") or (parts or {}).get("skip"):
        banda = "SKIP"
    elif score is None:
        banda = "SKIP"
    elif score >= SCORE_GREEN and trap not in ("warn", "trampa"):
        banda = "VERDE"
    elif score >= SCORE_YELLOW:
        banda = "AMARILLO"
    else:
        banda = "ROJO"
    why = "¿Por qué? " + " · ".join(
        f"{d['parte']} {'n/a' if d['crudo'] is None else d['crudo']} (peso {d['peso']})" for d in detail)
    audit = "Auditoría completa\n" + "\n".join(
        f"{d['parte']}: crudo {'n/a' if d['crudo'] is None else d['crudo']} · peso {d['peso']} · aporte {'n/a' if d['aporte'] is None else d['aporte']}"
        for d in detail)
    return {"score": score, "banda": banda, "partes": detail, "por_que": why, "auditoria": audit,
            "nota": "Los pesos son settings, no reglas. Una parte en n/a no se rellena."}


# -----------------------------------------------------------------------------
# Tamaño, paso a paso
# -----------------------------------------------------------------------------
def fomo_factor(meter: int | None) -> Decimal:
    if meter is None:
        return Decimal("1")
    if meter >= 9:
        return Decimal("0.5")
    if meter >= 7:
        return Decimal("0.75")
    return Decimal("1")


def liquidation(entry: Decimal, leverage: int, direction: str) -> Decimal:
    if leverage < 1:
        return entry
    if direction == "SHORT":
        return entry * (1 + (Decimal(1) / leverage) - MAINTENANCE)
    return entry * (1 - (Decimal(1) / leverage) + MAINTENANCE)


def max_safe_leverage(entry: Decimal, stop: Decimal, direction: str, ratio: Decimal = LIQ_RATIO) -> int | None:
    dist = abs(entry - stop) / entry
    room = ratio * dist + MAINTENANCE
    if room <= 0:
        return None
    raw = int((Decimal(1) / room).to_integral_value(rounding=ROUND_DOWN))
    return raw if raw >= 1 else None


def size_ticket(*, bankroll, risk_pct, entry, stop, direction="LONG", band="VERDE", fomo=0,
                fee_pct=0.05, funding_pct=0.0, pays_funding=False, min_ticket=5.0,
                max_notional=None, leverage_cap=125) -> dict:
    """Cada paso queda escrito. El stop que entra es el que sale: no se aleja."""
    steps = []
    bank, risk, px, sl = _d(bankroll), _d(risk_pct), _d(entry), _d(stop)
    riesgo = cent(bank * risk / Decimal(100))
    steps.append(f"riesgo {riesgo} = bankroll {bank} × {risk}%")
    if px <= 0 or sl <= 0 or px == sl:
        return {"ok": False, "motivo": "distancia al stop n/a", "stop": float(sl), "pasos": steps, "banda": "SKIP"}
    dist = abs(px - sl) / px
    steps.append(f"distancia {dist}")
    bruto = riesgo / dist
    steps.append(f"nocional bruto {bruto} = riesgo / distancia")
    fb = BAND_FACTOR.get(band, Decimal("0"))
    ff = fomo_factor(fomo)
    steps.append(f"factor banda {band} ×{fb}")
    steps.append(f"factor FOMO {fomo} ×{ff}")
    nocional = cent(bruto * fb * ff)
    if max_notional is not None:
        nocional = min(nocional, cent(max_notional))
        steps.append(f"tope de nocional {cent(max_notional)}")
    steps.append(f"nocional {nocional} (hacia abajo al centavo)")
    if nocional < _d(min_ticket) or fb == 0:
        steps.append("muy chico para valer la pena" if fb else "banda SKIP")
        return {"ok": False, "motivo": "muy chico para valer la pena" if fb else "banda SKIP",
                "nocional": nocional, "riesgo_usd": riesgo, "stop": float(sl), "pasos": steps,
                "factor_banda": fb, "factor_fomo": ff, "banda": "SKIP"}
    comision = cent(nocional * _d(fee_pct) / Decimal(100) * 2)
    funding = cent(nocional * _d(funding_pct) / Decimal(100)) if pays_funding else Decimal("0")
    steps.append(f"comisión taker ida y vuelta {comision}")
    steps.append(f"funding esperado {funding}" if pays_funding else "funding 0 (no paga o n/a)")
    lev = max_safe_leverage(px, sl, direction)
    if leverage_cap:
        lev = None if lev is None else min(lev, int(leverage_cap))
    liq = None if not lev else liquidation(px, lev, direction)
    liq_ok = lev is not None
    if not liq_ok:
        steps.append("la liquidación no queda más allá del stop ni bajando el apalancamiento a 1x. El stop no se mueve.")
    else:
        steps.append(f"apalancamiento {lev}x sale del tamaño · liquidación {liq}")
    return {"ok": liq_ok, "motivo": None if liq_ok else "liquidación demasiado cerca del stop",
            "riesgo_usd": riesgo, "distancia": dist, "nocional": nocional, "comision": comision,
            "funding": funding, "factor_banda": fb, "factor_fomo": ff, "leverage": lev,
            "liquidacion": None if liq is None else liq, "liq_ok": liq_ok, "stop": float(sl),
            "entry": float(px), "pasos": steps, "banda": band}


# -----------------------------------------------------------------------------
# COACH: una detección y botones de pausa
# -----------------------------------------------------------------------------
def detect_once(ctx: dict | None) -> dict | None:
    """Como máximo un evento y una frase. No regaña y no habla del P&L."""
    c = ctx or {}
    if c.get("sin_stop"):
        return {"evento": "sin stop", "mensaje": "Sin stop no hay operación. El plan se queda en el papel."}
    if c.get("stop_alejado"):
        return {"evento": "stop movido", "mensaje": "Ese stop queda más lejos. Las líneas solo se aprietan."}
    if c.get("sin_plan"):
        return {"evento": "sin plan", "mensaje": "No hay plan escrito. Primero la tarjeta, después el clic."}
    if c.get("minutos_tras_perdida") is not None and c["minutos_tras_perdida"] < 10 and (c.get("tamano_vs_anterior") or 0) > 1.3:
        return {"evento": "revancha", "mensaje": "Van menos de 10 min de una pérdida y el tamaño pasó 1.3×. Pausa corta."}
    zona = c.get("lejos_pct")
    if zona is not None and zona > float(c.get("persecucion_pct") or 0.4):
        return {"evento": "persecucion", "mensaje": f"El precio está {zona:.2f}% fuera de la zona. No se persigue."}
    if (c.get("tamano_vs_tarjeta") or 0) > 1.25:
        return {"evento": "sobretamano", "mensaje": "El tamaño pasa 1.25× el de la tarjeta. Se queda el de la tarjeta."}
    if c.get("promediar"):
        return {"evento": "promediar", "mensaje": "Promediar no mueve el stop ni sube el tamaño."}
    if c.get("despues_del_corte"):
        return {"evento": "despues del corte", "mensaje": "Ya pasó tu hora límite. Nada nuevo hasta el día siguiente."}
    if c.get("enfriamiento"):
        return {"evento": "enfriamiento", "mensaje": "Sigues en enfriamiento por la racha. La pausa sigue su reloj."}
    if c.get("cambio_regla_tras_perdida"):
        return {"evento": "regla tras perdida", "mensaje": "Ese cambio de regla espera 12 h. Las reglas no se tocan en caliente."}
    return None


def rules_score(flags: dict | None) -> dict:
    """Puntaje de reglas cumplidas. No premia el P&L."""
    f = flags or {}
    keys = ("con_stop", "stop_sin_alejar", "tamano_de_tarjeta", "dentro_de_hora", "sin_promediar")
    hechos = [k for k in keys if f.get(k)]
    return {"puntos": len(hechos), "de": len(keys), "cumplidas": hechos,
            "nota": "Se cuenta la regla, no la ganancia."}


class PauseBook:
    """Pausa pedida con botones. No escribe rules.md."""

    def __init__(self) -> None:
        self.until: datetime | None = None
        self.label: str | None = None

    def press(self, which: str, now: datetime | None = None, day_end: datetime | None = None) -> str:
        now = now or _utc()
        key = (which or "").lower()
        if "bien" in key:
            self.until, self.label = None, None
            return "De acuerdo. Sin pausa extra."
        if "hoy" in key:
            self.until = day_end or (now + timedelta(hours=8))
            self.label = "hoy"
            return "Pausa hasta que reinicie tu día. Las reglas no cambiaron."
        self.until = now + timedelta(hours=1)
        self.label = "1h"
        return "Pausa de 1 h. Las reglas no cambiaron."

    def active(self, now: datetime | None = None) -> str | None:
        now = now or _utc()
        if self.until and now < self.until:
            return f"Pausa ({self.label}) hasta {self.until.strftime('%H:%M')} UTC"
        return None

    def buttons(self) -> list[str]:
        return ["Pausar 1h", "Pausar hoy", "Estoy bien"]


# -----------------------------------------------------------------------------
# Vigilante: las líneas solo se aprietan
# -----------------------------------------------------------------------------
def tighten(old: float, new: float, direction: str) -> float:
    """Devuelve el stop más cerca del precio. Nunca el más lejos."""
    if direction == "SHORT":
        return old if new > old else new
    return old if new < old else new


def watch_position(pos: dict | None, price, extras: dict | None = None, repeats: dict | None = None) -> list[dict]:
    """Alertas en orden. Cada clave se calla después de 5 veces. El stop no se propone más lejos."""
    p, x = pos or {}, extras or {}
    direction = p.get("direction") or "LONG"
    stop = p.get("stop_loss")
    alerts = []

    def add(key, text):
        n = (repeats or {}).get(key, 0)
        if n >= 5:
            return
        if repeats is not None:
            repeats[key] = n + 1
        alerts.append({"clave": key, "texto": text, "veces": n + 1})

    if price is None:
        add("precio", "Precio n/a: no se vigila con un número inventado.")
        return alerts
    price = float(price)
    if stop is not None:
        stop = float(stop)
        jumped = price < stop if direction == "LONG" else price > stop
        if jumped:
            beyond = abs(price - stop) / stop * 100 if stop else 0
            add("stop_saltado", f"STOP SALTADO: precio {price}, {beyond:.2f}% más allá de tu stop")
        else:
            dist = abs(price - stop) / price * 100
            add("stop", f"Stop en {stop}. Falta {dist:.2f}% al precio. No se aleja.")
    else:
        add("sin_stop", "Posición sin stop. No se gestiona hasta tener uno.")
    liq = p.get("liquidation")
    if liq is not None and stop is not None:
        if abs(float(liq) - price) < abs(float(stop) - price):
            add("liquidacion", f"Liquidación {liq} está más cerca que el stop. El stop no se mueve para 'dar aire'.")
    tp1 = p.get("tp1")
    if tp1 is not None:
        add("tp1", f"TP1 en {tp1}. Ahí se cierra la parte del plan, no todo por emoción.")
    if p.get("past_tp1") and stop is not None and p.get("entry") is not None:
        proposed = tighten(float(stop), float(p["entry"]), direction)
        if proposed != float(stop):
            add("trailing", f"Tras TP1 el stop puede apretarse a {proposed}. No al revés.")
    if x.get("funding_flip"):
        add("funding", "El funding cambió de lado. Hecho, no es una orden.")
    if x.get("oi_drop_pct") is not None and x["oi_drop_pct"] <= -5:
        add("oi", f"Open interest {x['oi_drop_pct']}% . Sale dinero de la posición.")
    hours = x.get("horas_sin_progreso")
    if hours is not None and hours >= float(x.get("muerto_horas") or 2):
        add("muerto", f"Sin progreso en {hours} h. Dinero quieto, no una señal para promediar.")
    return alerts


# -----------------------------------------------------------------------------
# SCRIBE extendido: notas aparte de la calificación A–D que ya existe
# -----------------------------------------------------------------------------
def grade_plan(path: dict | None) -> str:
    """A–F según qué tocó primero el plan. No entrar a la zona no es una pérdida."""
    p = path or {}
    if not p.get("entro_en_zona", True):
        return "F"
    order = p.get("primero")
    return {"tp3": "A", "tp1": "B", "be": "C", "sl": "D", "gap": "E"}.get(order, "C")


def grade_execution(planned_qty, real_qty, stop_loosened: bool) -> str:
    if stop_loosened:
        return "E3"
    if planned_qty in (None, 0) or real_qty is None:
        return "E1"
    ratio = float(real_qty) / float(planned_qty)
    if ratio > 1.25 or ratio < 0.5:
        return "E3"
    if abs(ratio - 1) > 0.05:
        return "E2"
    return "E1"


def postmortem(pnl, planned_risk) -> str | None:
    if pnl is None or not planned_risk or planned_risk <= 0:
        return None
    if float(pnl) < -2 * float(planned_risk):
        return "Post-mortem: la pérdida pasó 2× el riesgo del plan. ¿El stop se respetó?"
    return None


def weekly_leaks(trades: list[dict] | None) -> dict:
    """Top 3 fugas en dinero. La sugerencia no se aplica."""
    buckets: dict[str, float] = {}
    for t in trades or []:
        pnl = float(t.get("pnl") or 0)
        if pnl >= 0:
            continue
        key = str(t.get("banda") or t.get("setup") or "sin banda")
        buckets[key] = buckets.get(key, 0.0) + pnl
    top = sorted(buckets.items(), key=lambda kv: kv[1])[:3]
    fuga = [{"donde": k, "usd": round(v, 2)} for k, v in top]
    if not fuga:
        sugerencia = "Sin fugas en pérdidas esta ventana. No hay nada que ajustar."
    else:
        sugerencia = (f"Sugerencia, no es un cambio: la fuga mayor está en {fuga[0]['donde']} "
                      f"({fuga[0]['usd']} USDT). Tú decides si se toca algo.")
    return {"fugas": fuga, "sugerencia": sugerencia, "aplicada": False}


def calibrate_suggestion(rows: list[dict] | None, *, min_n: int = 30) -> dict:
    """A los 14 días, compara grupo alto y bajo. Solo texto. Avisa si n es chico."""
    usable = [r for r in (rows or []) if r.get("parte") and r.get("grupo") in ("alto", "bajo") and r.get("resultado") is not None]
    by = {}
    for r in usable:
        by.setdefault(r["parte"], {"alto": [], "bajo": []})[r["grupo"]].append(float(r["resultado"]))
    notas = []
    for parte, grupos in by.items():
        n = len(grupos["alto"]) + len(grupos["bajo"])
        if n < min_n:
            notas.append(f"{parte}: n={n}, muy pocos para confiar. Puede ser ruido.")
            continue
        alto = sum(grupos["alto"]) / len(grupos["alto"]) if grupos["alto"] else None
        bajo = sum(grupos["bajo"]) / len(grupos["bajo"]) if grupos["bajo"] else None
        notas.append(f"{parte}: alto {alto} · bajo {bajo}. Sugerencia de peso, no un cambio.")
    return {"notas": notas or ["n=0, muy pocos para confiar."], "aplicada": False,
            "aviso": "Sobreajuste: n pequeño puede ser ruido. No se versiona solo."}


# -----------------------------------------------------------------------------
# Entrada vigilada, 20 minutos
# -----------------------------------------------------------------------------
class WatchedEntry:
    def __init__(self) -> None:
        self.item: dict | None = None

    def arm(self, symbol: str, direction: str, low: float, high: float, band: str,
            now: datetime | None = None, minutes: int = WATCH_MINUTES) -> dict:
        now = now or _utc()
        self.item = {"symbol": symbol, "direction": direction, "low": float(low), "high": float(high),
                     "band": band, "until": (now + timedelta(minutes=minutes)).isoformat(), "state": "vigilando"}
        return dict(self.item)

    def tick(self, price, band: str | None = None, now: datetime | None = None) -> dict:
        now = now or _utc()
        if not self.item:
            return {"state": "vacia", "mensaje": "No hay entrada vigilada."}
        until = _parse(self.item["until"])
        if until and now >= until:
            self.item["state"] = "expiro"
            msg = "Entrada vigilada expiró, no bajó a la zona. No operar también es operar bien."
            self.item["mensaje"] = msg
            return {"state": "expiro", "mensaje": msg}
        order = {"VERDE": 3, "AMARILLO": 2, "ROJO": 1, "SKIP": 0}
        if band and order.get(band, 0) < order.get(self.item["band"], 0):
            self.item["state"] = "cancelada"
            msg = "La banda bajó. Entrada vigilada cancelada."
            self.item["mensaje"] = msg
            return {"state": "cancelada", "mensaje": msg}
        if price is None:
            return {"state": "vigilando", "mensaje": "Precio n/a. Sigue la vigilancia, sin disparar."}
        low, high = self.item["low"], self.item["high"]
        if low <= float(price) <= high:
            self.item["state"] = "en_zona"
            return {"state": "en_zona", "mensaje": "El precio entró a la zona y la banda se sostiene. Sigue siendo tu clic."}
        return {"state": "vigilando", "mensaje": "Sigue fuera de la zona."}


# -----------------------------------------------------------------------------
# Estados de pantalla y salud
# -----------------------------------------------------------------------------
def screen_state(*, loading=False, error: str | None = None, rows=None, fetched_at=None,
                 now: datetime | None = None, stale_s: int = STALE_S) -> dict:
    now = now or _utc()
    if loading:
        return {"estado": "cargando", "mensaje": "Cargando…"}
    if error:
        return {"estado": "error", "mensaje": error, "desde": (fetched_at.isoformat() if isinstance(fetched_at, datetime) else fetched_at)}
    if rows is not None and len(rows) == 0:
        return {"estado": "vacio", "mensaje": "Nada que mostrar. Esperar también cuenta."}
    age = age_seconds(fetched_at, now)
    if age is not None and age > stale_s:
        return {"estado": "viejo", "mensaje": f"Dato viejo · {age}s", "edad_s": age}
    return {"estado": "listo", "mensaje": None, "edad_s": age}


def health(services=None, *, binance_ok: bool | None = None, now: datetime | None = None) -> dict:
    """Chequeo corto. Si una fuente cae, el banner lo dice y no se rellena con otra."""
    now = now or _utc()
    db_ok = None
    mcp = "apagado"
    tunnel = False
    try:
        db = getattr(getattr(services, "positions", None), "db", None) if services else None
        if db is not None:
            db.get_state("health_probe", {})
            db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False
    try:
        grok = getattr(services, "grok", None) if services else None
        if grok is not None:
            mcp = "encendido" if grok.running() else "apagado"
            tunnel = bool((grok.state().get("tunnel") or "").startswith("https://"))
    except Exception:  # noqa: BLE001
        mcp = "n/a"
    degraded = binance_ok is False or db_ok is False
    banner = None
    if binance_ok is False:
        banner = "Fuente degradada: Binance no respondió. Los números que falten quedan en n/a."
    elif db_ok is False:
        banner = "Fuente degradada: la base local no respondió."
    return {"en": now.isoformat(), "binance": "n/a" if binance_ok is None else ("ok" if binance_ok else "caido"),
            "db": "n/a" if db_ok is None else ("ok" if db_ok else "caido"),
            "mcp": mcp, "tunel": "ok" if tunnel else "sin túnel",
            "degradado": degraded, "banner": banner}


# Lo que no debe aparecer al ganar. Las animaciones de agentes no entran aquí.
class AuditBook:
    """Pausa, entrada vigilada y repeticiones del vigilante. No guarda reglas."""

    KEY = "fg_audit"

    def __init__(self, db=None) -> None:
        self.db = db
        self.pause = PauseBook()
        self.entry = WatchedEntry()
        self.repeats: dict = {}
        self._load()

    def _load(self) -> None:
        raw = {}
        try:
            raw = (self.db.get_state(self.KEY, {}) if self.db is not None else {}) or {}
        except Exception:  # noqa: BLE001
            raw = {}
        until = _parse(raw.get("pause_until"))
        if until:
            self.pause.until, self.pause.label = until, raw.get("pause_label")
        item = raw.get("entrada")
        if isinstance(item, dict):
            self.entry.item = item
        self.repeats = dict(raw.get("repeats") or {})

    def save(self) -> None:
        if self.db is None:
            return
        self.db.set_state(self.KEY, {
            "pause_until": self.pause.until.isoformat() if self.pause.until else None,
            "pause_label": self.pause.label,
            "entrada": self.entry.item,
            "repeats": self.repeats,
        })


CASINO = ("confeti", "confetti", "sonido de victoria", "racha de fuego", "lluvia de billetes")


def celebrates_wins(source: str) -> bool:
    text = (source or "").lower()
    return any(word in text for word in CASINO)
