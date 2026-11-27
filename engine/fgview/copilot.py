"""
engine/fgview/copilot.py
========================
🧭 COPILOTO FG (adaptación del "Grok Bot build prompt" a futuros de Binance).

Qué hace: arma la TRADE CARD con formato fijo, cuida tus reglas (COACH), las guarda versionadas
(RULES) y califica cada tarjeta después (SCRIBE) para calcular la TASA BASE honesta.
Qué nunca hace: inventar números, prometer ganancias, aflojar tus reglas por su cuenta o meter prisa.
Secciones del prompt que implementa: TRUTH RULES, TRADE CARD, COACH (FOMO meter, cooldown, day stop,
cutoff, revancha, tilt), rules.md (versiones + espera de 12 h tras pérdida), SCRIBE (grading, recap,
weekly review con "fugas"), BASE RATE.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from engine.config import get_logger

log = get_logger("fgview.copilot")

# Reglas: nombre -> (valor por defecto, mínimo, máximo, qué cambia). Cada número es TU ajuste.
RULES = {
    "risk_pct": (1.0, 0.25, 5.0, "Cuánto del capital puede perder 1 operación si toca el SL (%)"),
    "max_trades_day": (6, 1, 50, "Máximo de operaciones por día"),
    "max_open": (2, 1, 10, "Máximo de posiciones abiertas a la vez"),
    "day_loss_stop_pct": (6.0, 1.0, 30.0, "Si pierdes este % del capital hoy, se acabó el día"),
    "streak_losses": (2, 1, 6, "Pérdidas seguidas que activan la pausa (cooldown)"),
    "cooldown_min": (30, 5, 240, "Minutos de pausa después de la racha de pérdidas"),
    "cutoff": ("02:00", None, None, "Hora límite: después no entra nada nuevo (tu hora)"),
    "day_start": ("08:00", None, None, "Hora en que el día y los límites se reinician"),
    "tz": ("America/Mexico_City", None, None, "Tu zona horaria"),
    "grade_after_min": (240, 60, 1440, "Cuándo SCRIBE califica una tarjeta (minutos)"),
    "base_rate_window": (200, 30, 2000, "Cuántas tarjetas calificadas usa la tasa base"),
}
HOLD_AFTER_LOSS_H = 12  # Una regla cambiada <30 min después de una pérdida espera 12 h
TILT_WORDS = ("recuper", "una más", "una mas", "all in", "todo o nada", "voy con todo", "doblar", "duplicar",
              "siempre pierdo", "me vale", "a la verga", "chinga", "make it back", "one more", "going bigger",
              "lo saco", "desquit")
DISTRESS_WORDS = ("deuda", "renta", "no puedo pagar", "préstamo", "prestamo", "desesperad", "ya no quiero vivir",
                  "suicid", "perdí todo", "perdi todo")
BANDS = {"OPERAR": "GREEN", "ESPERAR": "YELLOW", "NO OPERAR": "RED"}
DOT = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴", "SKIP": "⚫"}


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


class _Store:
    KEY = ""

    def __init__(self, db) -> None:
        self.db, self._mem = db, {}

    def _get(self) -> dict:
        return (self.db.get_state(self.KEY, {}) if self.db is not None else self._mem) or {}

    def _put(self, st: dict) -> None:
        if self.db is not None:
            self.db.set_state(self.KEY, st)
        else:
            self._mem = st


# =============================================================================
# REGLAS (rules.md versionado)
# =============================================================================
class Rules(_Store):
    KEY = "copilot_rules"

    def state(self) -> dict:
        st = self._get()
        vals = {k: v[0] for k, v in RULES.items()}
        vals.update(st.get("values", {}))
        return {"version": st.get("version", 1), "values": vals, "history": st.get("history", []),
                "pending": st.get("pending", []), "goal": st.get("goal", "Respetar mi stop en cada operación.")}

    def values(self) -> dict:
        self.apply_pending()
        return self.state()["values"]

    def tz(self):
        from zoneinfo import ZoneInfo

        try:
            return ZoneInfo(str(self.state()["values"]["tz"]))
        except Exception:  # noqa: BLE001 - zona inválida o sin base de datos tz
            return timezone.utc

    def validate(self, name: str, value):
        if name not in RULES:
            raise ValueError(f"No existe la regla «{name}».")
        default, lo, hi, _ = RULES[name]
        if lo is None:
            return str(value)
        v = type(default)(float(value)) if isinstance(default, int) else float(value)
        if not lo <= v <= hi:
            raise ValueError(f"{name} debe estar entre {lo} y {hi} (pediste {value}).")
        return v

    def change(self, name: str, value, who: str = "yo", last_loss_min: float | None = None, force: bool = False) -> str:
        v = self.validate(name, value)
        st = self.state()
        old = st["values"][name]
        if v == old:
            return f"{name} ya vale {v}."
        raw = self._get()
        if last_loss_min is not None and last_loss_min < 30 and not force:
            due = (_utc() + timedelta(hours=HOLD_AFTER_LOSS_H)).isoformat()
            raw.setdefault("pending", []).append({"name": name, "value": v, "who": who, "due": due, "old": old})
            self._put(raw)
            return (f"Perdiste hace {last_loss_min:.0f} min: el cambio de {name} ({old} → {v}) espera {HOLD_AFTER_LOSS_H} h. "
                    "Las reglas hechas enojado rara vez son buenas reglas. (Puedes forzarlo y queda en el diario.)")
        vals = {**raw.get("values", {}), name: v}
        hist = raw.get("history", []) + [{"at": _utc().isoformat(), "name": name, "old": old, "new": v, "who": who,
                                          "forced": bool(force)}]
        self._put({**raw, "values": vals, "history": hist[-200:], "version": int(raw.get("version", 1)) + 1})
        return f"Reglas v{int(raw.get('version', 1)) + 1}: {name} {old} → {v}" + (" (forzado, anotado)" if force else "")

    def apply_pending(self) -> None:
        raw = self._get()
        pend = raw.get("pending", [])
        due = [p for p in pend if (_parse(p["due"]) or _utc()) <= _utc()]
        if not due:
            return
        raw["pending"] = [p for p in pend if p not in due]
        self._put(raw)
        for p in due:
            self.change(p["name"], p["value"], who=p["who"] + " (tras espera)")

    def set_goal(self, goal: str) -> None:
        self._put({**self._get(), "goal": goal.strip()[:200]})

    def markdown(self, bankroll: float) -> str:
        st = self.state()
        v = st["values"]
        risk = bankroll * float(v["risk_pct"]) / 100
        return "\n".join([
            f"MIS REGLAS · v{st['version']} · {datetime.now(self.tz()).strftime('%Y-%m-%d')}",
            f"Capital para futuros: ${bankroll:,.2f}. Dinero que puedo perder.",
            f"1 operación arriesga como máximo {v['risk_pct']}% = ${risk:,.2f} si toca el stop.",
            f"Máximo {v['max_open']} posiciones a la vez. Máximo {v['max_trades_day']} operaciones al día.",
            f"Después de {v['streak_losses']} pérdidas seguidas pauso {v['cooldown_min']} min.",
            f"Si pierdo {v['day_loss_stop_pct']}% hoy, paro por el día.",
            f"Nada nuevo después de las {v['cutoff']} ({v['tz']}). El día se reinicia a las {v['day_start']}.",
            "Toda operación lleva SL en el exchange antes de entrar. Nunca lo bajo.",
            "Margen aislado. Nunca promedio a la baja.",
            f"Mi meta de 30 días: {st['goal']}",
        ])


# =============================================================================
# COACH
# =============================================================================
class Coach:
    def __init__(self, services, rules: Rules) -> None:
        self.s, self.rules = services, rules

    def _local(self, dt: datetime | None = None) -> datetime:
        return (dt or _utc()).astimezone(self.rules.tz())

    def day_start(self) -> datetime:
        v = self.rules.values()
        now = self._local()
        h, m = (int(x) for x in str(v["day_start"]).split(":"))
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        return start if now >= start else start - timedelta(days=1)

    def trades(self) -> list[dict]:
        return self.s.journal.entries() if getattr(self.s, "journal", None) else []

    def today(self) -> list[dict]:
        ds = self.day_start()
        return [t for t in self.trades() if (_parse(t.get("opened_at")) or _utc()) >= ds]

    @staticmethod
    def _closed_at(t: dict) -> datetime | None:
        o = _parse(t.get("opened_at"))
        return o + timedelta(minutes=float(t.get("duration_min") or 0)) if o else None

    def last_loss_min(self) -> float | None:
        for t in reversed(self.trades()):
            if (t.get("pnl") or 0) < 0:
                c = self._closed_at(t)
                return (_utc() - c).total_seconds() / 60 if c else None
        return None

    def bankroll(self) -> float:
        from engine.config import SETTINGS

        lad = getattr(self.s, "ladder_desk", None)
        if lad is not None:
            stt = lad.status()
            if stt.enabled:
                return float(stt.balance)
        b = self.s.budget.status() if getattr(self.s, "budget", None) and self.s.budget.state().get("start") else None
        return float(b.balance) if b else float(SETTINGS.account_equity)

    def after_cutoff(self) -> bool:
        v = self.rules.values()
        now = self._local()
        ch, cm = (int(x) for x in str(v["cutoff"]).split(":"))
        sh, sm = (int(x) for x in str(v["day_start"]).split(":"))
        t, cut, start = now.hour * 60 + now.minute, ch * 60 + cm, sh * 60 + sm
        return (cut <= t < start) if cut < start else (t >= cut or t < start)

    def status(self) -> dict:
        v = self.rules.values()
        today = self.today()
        pnl = sum(float(t.get("pnl") or 0) for t in today)
        bank = self.bankroll()
        streak = 0
        for t in reversed(self.trades()):
            if (t.get("pnl") or 0) < 0:
                streak += 1
            else:
                break
        open_n = len(self.s.positions.all()) if getattr(self.s, "positions", None) else 0
        ll = self.last_loss_min()
        cooldown_left = (v["cooldown_min"] - ll) if (streak >= v["streak_losses"] and ll is not None and ll < v["cooldown_min"]) else 0
        pnl_pct = pnl / bank * 100 if bank > 0 else 0.0
        blocked = None
        if pnl_pct <= -float(v["day_loss_stop_pct"]):
            blocked = (f"Vas {pnl_pct:.1f}% hoy: tu stop del día. Se acabó hasta las {v['day_start']}. "
                       "Esta es la regla que protege mañana.")
        elif cooldown_left > 0:
            blocked = f"{streak} pérdidas seguidas. Pausa {cooldown_left:.0f} min más. Tus posiciones siguen vigiladas."
        elif len(today) >= v["max_trades_day"]:
            blocked = f"Límite alcanzado: {len(today)}/{v['max_trades_day']} operaciones hoy."
        elif open_n >= v["max_open"]:
            blocked = f"Límite alcanzado: {open_n}/{v['max_open']} posiciones abiertas."
        elif self.after_cutoff():
            blocked = f"Después de tu hora límite ({v['cutoff']}): nada nuevo hasta las {v['day_start']}."
        return {"trades_today": len(today), "max_trades": v["max_trades_day"], "pnl_today": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2), "streak": streak, "cooldown_left_min": round(max(0, cooldown_left)),
                "open": open_n, "max_open": v["max_open"], "after_cutoff": self.after_cutoff(),
                "bankroll": round(bank, 2), "blocked": blocked}

    def fomo(self, df5=None, direction: str | None = None) -> tuple[int, list[str]]:
        """Medidor FOMO 0-10: mercado (0-6) + tú (0-4). Solo cuenta con números, nunca con "se ve fuerte"."""
        pts, why = 0, []
        if df5 is not None and len(df5) > 60:
            c = df5["close"]
            chg1h = (float(c.iloc[-1]) / float(c.iloc[-13]) - 1) * 100
            d = 1 if direction == "LONG" else -1 if direction == "SHORT" else (1 if chg1h >= 0 else -1)
            if chg1h * d > 3:
                pts += 1
                why.append(f"1h {chg1h:+.1f}% a tu favor (ya corrió)")
            if chg1h * d > 6:
                pts += 1
            hi, lo = float(df5["high"].iloc[-288:].max()), float(df5["low"].iloc[-288:].min())
            px = float(c.iloc[-1])
            if (d > 0 and px >= hi * 0.995) or (d < 0 and px <= lo * 1.005):
                pts += 1
                why.append("pegado al extremo de 24 h")
            run = 0
            for o, cl in zip(df5["open"].iloc[::-1], c.iloc[::-1]):
                if (cl - o) * d > 0:
                    run += 1
                else:
                    break
            if run >= 4:
                pts += 1
                why.append(f"{run} velas seguidas del mismo color")
            vol = df5["volume"]
            if float(vol.iloc[-1]) > 3 * float(vol.iloc[-13:-1].mean() or 1):
                pts += 1
                why.append("volumen 5m > 3× lo normal")
        st = self.status()
        ll = self.last_loss_min()
        if ll is not None and ll < 30:
            pts += 1
            why.append(f"perdiste hace {ll:.0f} min")
        if st["streak"] >= 2:
            pts += 1
            why.append(f"{st['streak']} pérdidas seguidas")
        if st["trades_today"] >= st["max_trades"] - 1:
            pts += 1
            why.append("casi en tu límite de operaciones")
        if st["after_cutoff"]:
            pts += 1
            why.append("después de tu hora límite")
        return min(10, pts), why

    @staticmethod
    def size_factor(meter: int) -> float:
        return 0.5 if meter >= 9 else 0.75 if meter >= 7 else 1.0

    @staticmethod
    def line(meter: int) -> str:
        if meter >= 9:
            return "Esta es la que te vas a arrepentir. Si aún la quieres: mitad de tamaño y el stop primero."
        if meter >= 7:
            return "Estás persiguiendo un poco. ¿Entrada en retroceso o mitad de tamaño?"
        if meter >= 4:
            return "Con cuidado."
        return "Tranquilo."

    def tilt(self, text: str) -> str | None:
        t = (text or "").lower()
        if any(w in t for w in DISTRESS_WORDS):
            return ("Antes que el trading: si esto es dinero que necesitas o te sientes sin salida, aléjate de la pantalla "
                    "un rato y habla con alguien de confianza. Si es serio, busca una línea de ayuda de tu país "
                    "(en México: Línea de la Vida 800 911 2000).")
        if any(w in t for w in TILT_WORDS):
            st = self.status()
            return (f"Vas {st['pnl_today']:+.2f} USDT hoy, {st['trades_today']} operaciones. "
                    "¿Pausamos las tarjetas de compra una hora? [Pausa 1 h] [Pausa hoy] [No, estoy bien]")
        return None


# =============================================================================
# SCRIBE (calificación, tasa base, resúmenes)
# =============================================================================
class Scribe(_Store):
    KEY = "copilot_cards"

    def cards(self) -> list[dict]:
        return self._get().get("cards", [])

    def record(self, card: dict) -> None:
        st = self._get()
        cards = st.get("cards", [])
        last = cards[-1] if cards else None
        if last and last["symbol"] == card["symbol"] and last["direction"] == card["direction"] and \
                time.time() - last.get("ts", 0) < 300:
            cards[-1] = {**card, "id": last["id"]}  # Misma cripto y dirección en 5 min: se edita, no se duplica
        else:
            card["id"] = (cards[-1]["id"] + 1) if cards else 1
            cards.append(card)
        self._put({**st, "cards": cards[-3000:]})

    def grade(self, history, after_min: int = 240) -> int:
        """Califica tarjetas viejas con las velas reales: A (TP3) · B (TP1) · C (stop) · D (hueco peor que el stop)."""
        st = self._get()
        cards, n = st.get("cards", []), 0
        for c in cards:
            if c.get("grade") or not c.get("plan") or c["band"] == "SKIP":
                continue
            t0 = _parse(c["at"])
            if t0 is None or _utc() - t0 < timedelta(minutes=after_min):
                continue
            p = c["plan"]
            try:
                mk = "demo" if c["symbol"].startswith("DEMO") else "crypto"
                df, _ = history.get(c["symbol"], mk, "5m", 3)
                w = df[(df.index > t0) & (df.index <= t0 + timedelta(minutes=max(after_min, p.get("hold_min", 45) * 2)))]
            except Exception:  # noqa: BLE001
                continue
            long, grade, res = c["direction"] == "LONG", "SIN MOVIMIENTO", 0.0
            tp1_hit = False
            for _, r in w.iterrows():
                lo, hi, op = float(r["low"]), float(r["high"]), float(r["open"])
                hit_sl = lo <= p["sl"] if long else hi >= p["sl"]
                if hit_sl:
                    gap = (op < p["sl"]) if long else (op > p["sl"])
                    grade = "B" if tp1_hit else ("D" if gap else "C")
                    res = (0.0 if tp1_hit else (((op if gap else p["sl"]) / p["entry"] - 1) * 100 * (1 if long else -1)))
                    break
                if (hi >= p["tp1"]) if long else (lo <= p["tp1"]):
                    tp1_hit, grade = True, "B"
                    res = abs(p["tp1"] / p["entry"] - 1) * 100
                if p.get("tp3") and ((hi >= p["tp3"]) if long else (lo <= p["tp3"])):
                    grade, res = "A", abs(p["tp3"] / p["entry"] - 1) * 100
                    break
            c["grade"], c["result_pct"], c["graded_at"] = grade, round(res, 3), _utc().isoformat()
            n += 1
        if n:
            self._put({**st, "cards": cards})
        return n

    def base_rate(self, score: float | None, window: int = 200) -> dict:
        graded = [c for c in self.cards() if c.get("grade") and c.get("score") is not None][-window:]
        if score is None:
            return {"n": 0}
        band = [c for c in graded if abs(c["score"] - score) <= 5]
        n = len(band)
        if not n:
            return {"n": 0, "lo": round(score - 5), "hi": round(score + 5)}
        return {"n": n, "lo": round(score - 5), "hi": round(score + 5),
                "tp1_pct": round(sum(c["grade"] in ("A", "B") for c in band) / n * 100),
                "sl_pct": round(sum(c["grade"] in ("C", "D") for c in band) / n * 100),
                "avg_pct": round(sum(c.get("result_pct", 0) for c in band) / n, 2)}

    @staticmethod
    def base_rate_line(br: dict) -> str:
        if not br.get("n"):
            return "TASA BASE  todavía sin tarjetas calificadas con esta puntuación (n=0)."
        txt = (f"TASA BASE  puntuación {br['lo']}-{br['hi']}: n={br['n']} · llegó a TP1 {br['tp1_pct']}% · "
               f"tocó SL {br['sl_pct']}% · promedio {br['avg_pct']:+.2f}%")
        return txt + (" · muy pocas para confiar" if br["n"] < 30 else "")

    def summary(self, coach: Coach, days: int = 1) -> str:
        since = _utc() - timedelta(days=days)
        trades = [t for t in coach.trades() if (_parse(t.get("opened_at")) or _utc()) >= since]
        pnl = sum(float(t.get("pnl") or 0) for t in trades)
        wins = sum((t.get("pnl") or 0) > 0 for t in trades)
        cards = [c for c in self.cards() if (_parse(c["at"]) or _utc()) >= since]
        bands = {b: sum(c["band"] == b for c in cards) for b in ("GREEN", "YELLOW", "RED", "SKIP")}
        graded = [c for c in cards if c.get("grade")]
        grades = {g: sum(c["grade"] == g for c in graded) for g in ("A", "B", "C", "D")}
        title = "AYER" if days == 1 else f"SEMANA ({days} días)"
        lines = [f"{title}",
                 f"Operaciones {len(trades)} · P&L {pnl:+.2f} USDT · ganadas {wins}/{len(trades)}",
                 f"Tarjetas: GREEN {bands['GREEN']} · YELLOW {bands['YELLOW']} · RED {bands['RED']} · SKIP {bands['SKIP']}"]
        if graded:
            lines.append(f"Calificadas {len(graded)}: A {grades['A']} · B {grades['B']} · C {grades['C']} · D {grades['D']}")
        if days >= 7 and trades:
            losses = [float(t.get("pnl") or 0) for t in trades if (t.get("pnl") or 0) < 0]
            gains = [float(t.get("pnl") or 0) for t in trades if (t.get("pnl") or 0) > 0]
            exp = pnl / len(trades)
            lines.append(f"Ganancia media {sum(gains) / len(gains) if gains else 0:+.2f} · pérdida media "
                         f"{sum(losses) / len(losses) if losses else 0:+.2f} · expectativa {exp:+.2f} por operación")
            off = [t for t in trades if not str(t.get("notes", "")).startswith("DESK")]
            if off:
                lines.append(f"FUGA: {len(off)} operaciones sin tarjeta de la mesa · "
                             f"{sum(float(t.get('pnl') or 0) for t in off):+.2f} USDT")
        return "\n".join(lines)


# =============================================================================
# Fachada: la TRADE CARD
# =============================================================================
class Copilot:
    def __init__(self, services) -> None:
        db = getattr(services.positions, "db", None)
        self.s = services
        self.rules = Rules(db)
        self.coach = Coach(services, self.rules)
        self.scribe = Scribe(db)

    def card(self, dec, record: bool = True) -> dict:
        snap = getattr(dec, "snap", None)
        hard = any(r.agent == "GATE" and r.status == "block" for r in dec.results)
        band = "SKIP" if (dec.ticket is None or hard) else BANDS.get(dec.verdict, "RED")
        score = None if band == "SKIP" else round(dec.probability or 0)
        meter, why_fomo = self.coach.fomo(snap.df5 if snap is not None else None, dec.direction)
        st = self.coach.status()
        if st["blocked"] and band in ("GREEN", "YELLOW"):
            paused = st["blocked"]
        else:
            paused = None
        p = dec.plan
        coin = dec.symbol.split("/")[0]
        age = ""
        if snap is not None and getattr(snap, "fetched_at", None) is not None:
            age = f"{max(0, (_utc() - snap.fetched_at).total_seconds()):.0f}s"
        source = "Binance" if snap is None or str(getattr(snap, "source", "")).lower() in ("binance", "") else str(snap.source)
        src = f"{'DEMO (datos de prueba)' if source == 'demo' else source}{' · ' + age if age else ''}"
        passed = sum(r.status == "pass" for r in dec.results)
        flags = [r.agent for r in dec.results if r.status == "block"]
        lines = [f"{DOT[band]} {coin} · {dec.direction or '—'} · " + (f"SCORE {score} · {band}" if score is not None else "SKIP")]
        if snap is not None:
            c = snap.df5["close"]
            ch1 = (float(c.iloc[-1]) / float(c.iloc[-13]) - 1) * 100 if len(c) > 13 else 0.0
            ch5 = (float(c.iloc[-1]) / float(c.iloc[-2]) - 1) * 100 if len(c) > 2 else 0.0
            fund = f" · funding {snap.funding * 100:+.4f}%" if snap.funding is not None else " · funding n/a"
            lines.append(f"{dec.symbol} · precio ${snap.price:,.6g} ({src}){fund}")
        spot = next((r for r in dec.results if r.agent == "SPOTTER"), None)
        if spot:
            lines.append(f"POR QUÉ    {spot.headline.split(' · ⛔')[0][:110]}")
        lines.append(f"SEGURIDAD  {passed}/{len(dec.results)} agentes aprueban · banderas: {', '.join(flags) or 'ninguna'}")
        if snap is not None:
            lines.append(f"MOMENTUM   1h {ch1:+.2f}% · 5m {ch5:+.2f}%")
        b = dec.blocked_by
        read = (f"La mesa está de acuerdo; P(TP1) {dec.probability:.0f}% y ventaja {dec.edge_r:+.2f}R." if dec.approved and dec.probability is not None
                else f"{b.agent} objeta: {(b.headline.split('⛔')[-1] if '⛔' in b.headline else b.headline)[:90].strip()}." if b
                else "Sin lectura.")
        lines.append(f"MI LECTURA {read}")
        plan = None
        if band == "RED":
            lines.append("PLAN       sin plan recomendado (la mesa dice NO OPERAR). Detrás de «Mostrar de todos modos».")
        if band != "SKIP" and p is not None:  # El plan se guarda también en RED (SCRIBE lo califica), pero no se muestra
            f = self.coach.size_factor(meter)
            risk = (p.risk_usd or 0) * f
            plan = {"entry": p.entry, "sl": p.stop_loss, "sl_pct": round(p.sl_pct, 3), "tp1": p.tps[0][0],
                    "tp1_pct": round(p.tps[0][1], 3), "tp3": p.tps[-1][0], "leverage": p.leverage,
                    "risk_usd": round(risk, 2), "risk_pct": round((dec.risk_pct or 0) * f, 3),
                    "size_factor": f, "hold_min": getattr(dec.ticket, "max_hold_min", 45)}
        if band in ("GREEN", "YELLOW") and plan is not None:
            f = plan["size_factor"]
            risk = plan["risk_usd"]
            lines.append(f"PLAN       entrada ${p.entry:,.6g} · SL ${p.stop_loss:,.6g} (−{p.sl_pct:.2f}%) · "
                         f"TP1 ${p.tps[0][0]:,.6g} (+{p.tps[0][1]:.2f}%) · {p.leverage}x")
            lines.append(f"           riesgo ${risk:,.2f} ({plan['risk_pct']:.2f}% del capital)"
                         + (f" · tamaño ×{f} por FOMO" if f < 1 else ""))
        lines.append(f"FOMO {meter}/10 · {self.coach.line(meter)}")
        br = self.scribe.base_rate(score, int(self.rules.values()["base_rate_window"]))
        if band != "SKIP":
            lines.append(Scribe.base_rate_line(br))
        lines.append(f"REGLAS     {st['open']}/{st['max_open']} abiertas · {st['trades_today']}/{st['max_trades']} hoy · "
                     f"P&L del día {st['pnl_pct']:+.2f}%")
        if paused:
            lines.append(f"PAUSADO POR TUS REGLAS · {paused}")
        lines.append("Tu decisión. Tú presionas el botón." if band != "SKIP" else "SKIP: sin plan. " + (b.headline[:90] if b else ""))
        card = {"at": _utc().isoformat(), "ts": time.time(), "symbol": dec.symbol, "direction": dec.direction,
                "band": band, "score": score, "verdict": dec.verdict, "fomo": meter, "fomo_why": why_fomo,
                "plan": plan, "paused": paused, "base_rate": br, "text": "\n".join(lines)}
        if record:
            self.scribe.record(dict(card))
        return card
