"""
engine/fgview/analyst.py
========================
🧠 ANALYST: el agente 13 de FG DESK, con IA real por API: Grok (xAI) por defecto, o Claude (Anthropic).

Recibe todo lo que midieron los otros 12 agentes (señal, ballenas, flujo, funding, squeeze,
Time Machine, ventaja, plan con SL/TPs), las últimas velas y los titulares de noticias, y da una
segunda opinión: APRUEBA · PRECAUCIÓN · VETA, con sus razones y riesgos.

REGLAS DE SEGURIDAD (en código, no en el prompt):
* SOLO PUEDE FRENAR. PRECAUCIÓN baja OPERAR a ESPERAR (y el riesgo a lo mínimo); VETA lo convierte
  en ⛔ NO OPERAR (el piloto nunca lo ejecuta). Nunca sube el riesgo, nunca cambia el SL ni el tamaño.
* Sin API key (XAI_API_KEY para Grok, ANTHROPIC_API_KEY para Claude) o apagado: no hace nada y la
  mesa funciona igual que siempre. OJO: la suscripción de Grok en X / grok.com (≈ $30/mes) NO incluye
  la API; la API se paga aparte, por uso, en console.x.ai.
* Gasto controlado: solo se llama cuando la mesa ya tiene una operación candidata, reutiliza la
  opinión de la misma cripto/dirección durante 10 min y respeta un tope diario en USD.
* Marcador: guarda cada opinión y, una hora después, revisa con las velas reales si la operación
  habría tocado TP1 o SL primero. Así se mide si la IA ayuda o solo cuesta.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

import requests

from engine.config import get_logger

log = get_logger("fgview.analyst")

PROVIDERS = {"grok": "🤖 Grok (xAI)", "claude": "🧠 Claude (Anthropic)"}
MODELS = {  # USD por millón de tokens (entrada, salida). Verifica precios en la consola de cada proveedor.
    "grok": {"grok-4.7": ("Grok 4.7 · el más nuevo de xAI", 2.0, 6.0)},
    "claude": {"claude-opus-5-5": ("Claude Opus 5.5 · muy lista", 4.0, 20.0),
               "claude-fable-5-2": ("Claude Fable 5.2 · la más lista (2× más cara)", 8.0, 40.0),
               "claude-sonnet-5-5": ("Claude Sonnet 5.5 · lista y más barata", 2.0, 10.0)},
}
DEFAULT_MODEL = {"grok": "grok-4.7", "claude": "claude-opus-5-5"}
KEY_ENV = {"grok": ("XAI_API_KEY",), "claude": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
XAI_URL = "https://api.x.ai/v1/chat/completions"
REUSE_S = 600  # Misma cripto y dirección: reutiliza la opinión 10 min
EST_CALL_USD = 0.08  # Estimación conservadora por llamada (para no pasarse del tope)

SYSTEM = """Eres el analista de riesgo de FG DESK, una mesa de trading de futuros perpetuos USDT-M de Binance \
para operaciones cortas (15-30 min) con apalancamiento. Doce agentes de código ya analizaron la operación \
y te pasan sus mediciones, el plan (entrada, stop loss, take profits, apalancamiento, riesgo), las últimas \
velas y titulares de noticias.

Tu trabajo es dar una segunda opinión independiente sobre si conviene abrir ESTA operación AHORA. No puedes \
cambiar el plan: solo aprobar, pedir precaución o vetar. Busca sobre todo lo que los números no capturan: \
noticias o eventos que puedan mover el precio, contradicciones entre agentes, entradas tarde después de un \
movimiento ya hecho, riesgo de squeeze o barrida de liquidez, y si el stop tiene sentido frente a la \
volatilidad.

- APRUEBA: la operación es razonable y no ves un riesgo serio que la mesa haya pasado por alto.
- PRECAUCION: hay dudas o riesgos relevantes; mejor esperar una entrada más clara.
- VETA: ves un problema serio (evento de alto impacto inminente, contradicción fuerte, entrada claramente \
mala). Úsalo cuando de verdad lo amerite.

Basa tu juicio solo en los datos recibidos; si falta información, dilo en los riesgos en lugar de suponer. \
No inventes precios ni noticias. Escribe en español claro y breve: el usuario puede ser principiante. \
Responde únicamente con el JSON pedido."""

SCHEMA = {
    "type": "object",
    "properties": {
        "veredicto": {"type": "string", "enum": ["APRUEBA", "PRECAUCION", "VETA"]},
        "confianza": {"type": "integer", "description": "0-100: qué tan seguro estás de tu veredicto"},
        "resumen": {"type": "string", "description": "Una o dos frases con tu conclusión"},
        "razones": {"type": "array", "items": {"type": "string"}},
        "riesgos": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["veredicto", "confianza", "resumen", "razones", "riesgos"],
    "additionalProperties": False,
}


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class AIAnalyst:
    KEY = "ai_analyst"

    def __init__(self, db=None, client=None, http=None) -> None:
        # `client` (SDK de Anthropic) y `http` (algo con .post, para xAI) se pueden inyectar en tests
        self.db, self._client, self._http = db, client, http
        self._mem: dict = {}
        self._cache: dict[tuple, tuple[float, dict]] = {}

    # ------------------------------------------------------------- estado
    def state(self) -> dict:
        data = (self.db.get_state(self.KEY, {}) if self.db is not None else self._mem) or {}
        st = {"enabled": False, "provider": "grok", "model": "", "effort": "medium", "daily_cap": 1.0, "spent": {},
              "log": [], "last": None, **data}
        if st["provider"] not in PROVIDERS:
            st["provider"] = "grok"
        if st["model"] not in MODELS[st["provider"]]:
            st["model"] = DEFAULT_MODEL[st["provider"]]
        return st

    def save(self, st: dict) -> None:
        st["log"] = st.get("log", [])[-300:]
        keep = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%d")
        st["spent"] = {k: v for k, v in st.get("spent", {}).items() if k >= keep}
        if self.db is not None:
            self.db.set_state(self.KEY, st)
        else:
            self._mem = st

    def set(self, **changes) -> dict:
        st = {**self.state(), **changes}
        self.save(st)
        return self.state()

    def has_key(self, provider: str | None = None) -> bool:
        provider = provider or self.state()["provider"]
        return any(os.environ.get(k) for k in KEY_ENV.get(provider, ()))

    def active(self) -> bool:
        st = self.state()
        injected = self._http if st["provider"] == "grok" else self._client
        return st["enabled"] and (injected is not None or self.has_key(st["provider"]))

    def spent_today(self) -> float:
        return float(self.state()["spent"].get(_today(), 0.0))

    # ------------------------------------------------------------- llamada
    @property
    def client(self):
        if self._client is None:
            import anthropic  # Import perezoso: solo hace falta si se elige Claude

            self._client = anthropic.Anthropic()
        return self._client

    def _post(self, body: dict, timeout: int = 120):
        post = self._http.post if self._http is not None else requests.post
        return post(XAI_URL, json=body, timeout=timeout,
                    headers={"Authorization": f"Bearer {os.environ.get('XAI_API_KEY', '')}",
                             "Content-Type": "application/json"})

    def _charge(self, provider: str, model: str, tin: int, tout: int) -> float:
        _, pin, pout = MODELS[provider].get(model, MODELS[provider][DEFAULT_MODEL[provider]])
        cost = (tin * pin + tout * pout) / 1e6
        st = self.state()
        st["spent"][_today()] = round(st["spent"].get(_today(), 0.0) + cost, 5)
        self.save(st)
        return cost

    @staticmethod
    def context(dec, snap, news: list | None = None) -> dict:
        p = dec.plan
        df = snap.df5
        closes = [round(float(c), 6) for c in df["close"].iloc[-36:]] if len(df) else []

        def chg(n):
            return round((float(df["close"].iloc[-1]) / float(df["close"].iloc[-n - 1]) - 1) * 100, 3) if len(df) > n else None

        return {
            "simbolo": dec.symbol, "direccion": dec.direction, "precio": snap.price,
            "plan": None if p is None else {
                "entrada": p.entry, "stop_loss": p.stop_loss, "sl_pct": round(p.sl_pct, 3),
                "take_profits": [{"precio": tp, "pct": round(pc, 3), "cierra_pct": c} for tp, pc, c in p.tps],
                "apalancamiento": p.leverage, "riesgo_pct_capital": round(dec.risk_pct or 0, 3),
                "nocional_usdt": round(p.notional, 2), "liquidacion": p.liquidation},
            "agentes": [{"agente": r.agent, "estado": r.status, "dato": r.headline} for r in dec.results],
            "probabilidad_tp1_pct": None if dec.probability is None else round(dec.probability, 1),
            "ventaja_R": None if dec.edge_r is None else round(dec.edge_r, 3),
            "time_machine": None if dec.tm is None else {"p_tp1": dec.tm.p_tp1, "p_sl": dec.tm.p_sl, "ev_usd": dec.tm.ev,
                                                          "simulaciones": dec.tm.sims},
            "funding": snap.funding,
            "atr5_pct": round(snap.atr5 / snap.price * 100, 3) if getattr(snap, "atr5", None) and snap.price else None,
            "cambio_pct": {"1h": chg(12), "4h": chg(48)}, "cierres_5m_ultimas_3h": closes,
            "noticias": [{"hora": n.when.isoformat() if n.when else None, "titulo": n.title, "impacto": n.impact,
                          "sentimiento": n.sentiment} for n in (news or [])[:10]],
            "hora_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        }

    def ask(self, ctx: dict) -> dict:
        st = self.state()
        return self._ask_grok(ctx, st["model"]) if st["provider"] == "grok" else self._ask_claude(ctx, st)

    def _ask_grok(self, ctx: dict, model: str) -> dict:
        """Grok por la API de xAI (Chat Completions con salida JSON según el esquema)."""
        r = self._post({"model": model, "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "Datos de la operación (JSON):\n" + json.dumps(ctx, ensure_ascii=False, default=str)}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "veredicto_fg", "strict": True, "schema": SCHEMA}}})
        if r.status_code != 200:
            raise RuntimeError(f"xAI respondió {r.status_code}: {r.text[:160]}")
        data = r.json()
        u = data.get("usage") or {}
        cost = self._charge("grok", model, int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0))
        out = json.loads(data["choices"][0]["message"]["content"])
        out["costo"] = cost
        return out

    def _ask_claude(self, ctx: dict, st: dict) -> dict:
        """Claude por el SDK oficial de Anthropic (salida JSON según el esquema; si un modelo declina, responde otro)."""
        resp = self.client.beta.messages.create(
            model=st["model"], max_tokens=16000, system=SYSTEM,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": st["effort"], "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": "Datos de la operación (JSON):\n"
                       + json.dumps(ctx, ensure_ascii=False, default=str)}],
        )
        served = getattr(resp, "model", st["model"])
        cost = self._charge("claude", served if served in MODELS["claude"] else st["model"],
                            resp.usage.input_tokens, resp.usage.output_tokens)
        if resp.stop_reason == "refusal":
            return {"veredicto": None, "resumen": "La IA no quiso opinar sobre esta operación.", "costo": cost}
        text = next((b.text for b in resp.content if b.type == "text"), "")
        out = json.loads(text)
        out["costo"] = cost
        return out

    def test_connection(self) -> str:
        """Llamada mínima para comprobar la key (cuesta una fracción de centavo)."""
        st = self.state()
        if not self.has_key(st["provider"]) and (self._http if st["provider"] == "grok" else self._client) is None:
            env = KEY_ENV[st["provider"]][0]
            return f"❌ No encuentro {env} en tu archivo .env (reinicia la app después de guardarlo)."
        try:
            if st["provider"] == "grok":
                r = self._post({"model": st["model"], "max_tokens": 5,
                                "messages": [{"role": "user", "content": "Responde solo: OK"}]}, timeout=60)
                if r.status_code != 200:
                    return f"❌ xAI respondió {r.status_code}: {r.text[:160]}"
            else:
                self.client.messages.create(model=st["model"], max_tokens=16,
                                            messages=[{"role": "user", "content": "Responde solo: OK"}])
            return f"✅ Conectado a {st['model']}: la IA está lista."
        except Exception as exc:  # noqa: BLE001
            return f"❌ No se pudo conectar: {str(exc)[:160]}"

    def review(self, dec, snap, news: list | None = None):
        """Devuelve un AgentResult de ANALYST (o None si la IA está apagada)."""
        from engine.fgview.desk import AgentResult

        t0 = time.perf_counter()
        if not self.active():
            return None
        st = self.state()
        name = MODELS[st["provider"]][st["model"]][0].split(" · ")[0]
        key = (dec.symbol, dec.direction)
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < REUSE_S:
            op, reused = hit[1], True
        else:
            if self.spent_today() + EST_CALL_USD > st["daily_cap"]:
                return AgentResult("ANALYST", "info", f"Tope diario de IA alcanzado ({self.spent_today():.2f} de "
                                   f"{st['daily_cap']:.2f} USD): hoy no opina", ms=(time.perf_counter() - t0) * 1000)
            try:
                op, reused = self.ask(self.context(dec, snap, news)), False
            except Exception as exc:  # noqa: BLE001 - sin internet, key inválida, límite de la API…
                log.info("Analista IA no disponible: %s", exc)
                return AgentResult("ANALYST", "info", f"{name} no disponible: {str(exc)[:90]}",
                                   ms=(time.perf_counter() - t0) * 1000)
            self._cache[key] = (time.time(), op)
            self._record(dec, op, name)
        v = op.get("veredicto")
        status = {"APRUEBA": "pass", "PRECAUCION": "block", "VETA": "block"}.get(v, "info")
        icon = {"APRUEBA": "✅", "PRECAUCION": "⚠️ PRECAUCIÓN", "VETA": "⛔ VETA"}.get(v, "•")
        head = f"{name}: {icon} {op.get('resumen', '')}"
        if op.get("confianza") is not None:
            head += f" · confianza {op['confianza']}%"
        if reused:
            head += " · (opinión de hace < 10 min)"
        detail = "\n".join([*(f"✓ {r}" for r in op.get("razones", [])), *(f"⚠ {r}" for r in op.get("riesgos", []))])
        return AgentResult("ANALYST", status, head, detail=detail, value=float(op.get("confianza") or 0),
                           ms=(time.perf_counter() - t0) * 1000)

    def _record(self, dec, op: dict, name: str) -> None:
        if dec.plan is None:
            return
        st = self.state()
        entry = {"at": datetime.now(timezone.utc).isoformat(), "symbol": dec.symbol, "dir": dec.direction,
                 "entry": dec.plan.entry, "sl": dec.plan.stop_loss, "tp1": dec.plan.tps[0][0], "model": name,
                 "ia": op.get("veredicto"), "resumen": op.get("resumen", ""), "razones": op.get("razones", []),
                 "riesgos": op.get("riesgos", []), "confianza": op.get("confianza"),
                 "costo": round(op.get("costo", 0.0), 5), "outcome": None}
        st["log"].append(entry)
        st["last"] = entry
        self.save(st)

    # ------------------------------------------------------------- marcador
    def evaluate(self, history, market: str = "crypto", horizon_min: int = 60) -> None:
        """Con las velas reales, marca si cada operación opinada habría tocado TP1 o SL primero."""
        st = self.state()
        now = datetime.now(timezone.utc)
        changed = False
        for e in st["log"]:
            if e.get("outcome") is not None:
                continue
            at = datetime.fromisoformat(e["at"])
            if now - at < timedelta(minutes=horizon_min):
                continue
            try:
                mk = "demo" if e["symbol"].startswith("DEMO") else market
                df, _ = history.get(e["symbol"], mk, "5m", 3)
                w = df[(df.index > at) & (df.index <= at + timedelta(minutes=horizon_min))]
            except Exception:  # noqa: BLE001
                continue
            out = "NADA"
            for _, row in w.iterrows():
                long = e["dir"] == "LONG"
                hit_sl = row["low"] <= e["sl"] if long else row["high"] >= e["sl"]
                hit_tp = row["high"] >= e["tp1"] if long else row["low"] <= e["tp1"]
                if hit_sl:  # Si ambos caen en la misma vela, se asume lo peor
                    out = "SL"
                    break
                if hit_tp:
                    out = "TP"
                    break
            e["outcome"] = out
            changed = True
        if changed:
            self.save(st)

    def scorecard(self) -> dict:
        st = self.state()
        done = [e for e in st["log"] if e.get("outcome")]
        stops = [e for e in done if e["ia"] in ("VETA", "PRECAUCION")]
        oks = [e for e in done if e["ia"] == "APRUEBA"]
        return {"evaluadas": len(done), "frenadas": len(stops),
                "frenadas_SL": sum(e["outcome"] == "SL" for e in stops),
                "frenadas_TP": sum(e["outcome"] == "TP" for e in stops),
                "aprobadas": len(oks), "aprobadas_TP": sum(e["outcome"] == "TP" for e in oks),
                "aprobadas_SL": sum(e["outcome"] == "SL" for e in oks),
                "gasto_total": round(sum(st["spent"].values()), 4)}
