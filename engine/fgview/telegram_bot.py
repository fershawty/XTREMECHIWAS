"""
engine/fgview/telegram_bot.py
=============================
📲 Telegram del copiloto FG (el "carril rápido" del prompt): comandos, tarjetas, COACH y un tablero
fijado que se edita solo.

Qué hace: responde SOLO a tu chat (TELEGRAM_CHAT_ID), arma Trade Cards, muestra tus posiciones, tus reglas,
el COACH y los resúmenes, avisa cuando una posición se cierra y mantiene 1 mensaje fijado con el estado.
Qué nunca hace: abrir órdenes reales, aceptar órdenes de otro chat, mostrar tus keys.
Comandos: /tarjeta /check <cripto> · /radar · /plan · /coach · /reglas · /resumen · /semana · /stop · /help
"""

from __future__ import annotations

import html
import re
import threading
import time
from datetime import datetime, timezone

import requests

from engine.config import SETTINGS, get_logger

log = get_logger("fgview.telegram")

API = "https://api.telegram.org/bot{token}/{method}"
HELP = """<b>FG VIEW · copiloto</b> (futuros de Binance · tú operas)
/tarjeta SOL — Trade Card de una cripto (también /check)
/radar — las 5 mejores del Spotter ahora
/plan — tus posiciones abiertas con stop y TP1
/coach — hoy: operaciones, P&amp;L, racha, pausa, límites
/reglas — tus reglas (rules.md)
/resumen — resumen del día · /semana — de la semana
/stop — "ya terminé por hoy": apaga el piloto automático
/help — esta lista
También puedes escribir solo el nombre de una cripto (ej. «sol»)."""


def _pre(text: str) -> str:
    return f"<pre>{html.escape(text)}</pre>"


class TelegramCopilot:
    KEY = "telegram_copilot"

    def __init__(self, services, token: str | None = None, chat_id: str | None = None, http=None) -> None:
        self.s = services
        self.token = token if token is not None else SETTINGS.telegram_bot_token
        self.chat_id = str(chat_id if chat_id is not None else SETTINGS.telegram_chat_id or "")
        self.http = http or requests
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._mem: dict = {}
        self.sent: list[str] = []  # Últimos textos enviados (para la página y los tests)

    # ------------------------------------------------------------- estado
    @property
    def db(self):
        return getattr(self.s.positions, "db", None)

    def state(self) -> dict:
        data = (self.db.get_state(self.KEY, {}) if self.db is not None else self._mem) or {}
        return {"offset": 0, "pinned_id": None, "last_dash": 0.0, "ignored": 0, **data}

    def save(self, st: dict) -> None:
        if self.db is not None:
            self.db.set_state(self.KEY, st)
        else:
            self._mem = st

    def configured(self) -> bool:
        return bool(self.token)

    def ready(self) -> bool:
        return bool(self.token and self.chat_id)

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------- API de Telegram
    def _call(self, method: str, http_timeout: int = 15, **params) -> dict:
        r = self.http.post(API.format(token=self.token, method=method), json=params, timeout=http_timeout)
        data = r.json() if hasattr(r, "json") else {}
        if not data.get("ok", False):
            raise RuntimeError(f"Telegram {method}: {str(data.get('description', data))[:120]}")
        return data.get("result") or {}

    def send(self, text: str, chat_id: str | None = None, pre: bool = False) -> dict | None:
        chat = chat_id or self.chat_id
        if not (self.token and chat):
            return None
        body = _pre(text) if pre else text
        self.sent = (self.sent + [text])[-30:]
        try:
            return self._call("sendMessage", chat_id=chat, text=body[:4000], parse_mode="HTML",
                              disable_web_page_preview=True)
        except Exception as exc:  # noqa: BLE001 - Telegram caído: la app sigue
            log.info("No se pudo enviar a Telegram: %s", exc)
            return None

    # ------------------------------------------------------------- comandos
    def _coins(self) -> list[str]:
        return [c for t in self.s.tiers.tiers for c in t.coins]

    def handle(self, update: dict) -> str | None:
        """Procesa 1 update. Devuelve el texto respondido (o None si se ignoró)."""
        msg = update.get("message") or update.get("edited_message") or {}
        chat = str((msg.get("chat") or {}).get("id", ""))
        text = (msg.get("text") or "").strip()
        if not chat or not text:
            return None
        if not self.chat_id:  # Primera vez: solo decimos el chat id para que lo pongas en .env
            if text.startswith("/start"):
                reply = (f"Hola. Tu chat id es <code>{chat}</code>. Ponlo en el .env como TELEGRAM_CHAT_ID={chat} y "
                         "reinicia FG VIEW. Hasta entonces no respondo nada más.")
                self.send(reply, chat_id=chat)
                return reply
            return None
        if chat != self.chat_id:  # Solo tu chat. A cualquier otro: ni respuesta.
            st = self.state()
            st["ignored"] += 1
            self.save(st)
            log.warning("Telegram: mensaje ignorado de un chat ajeno")
            return None
        reply, pre = self.route(text)
        if reply:
            self.send(reply, pre=pre)
        return reply

    def route(self, text: str) -> tuple[str, bool]:
        cp, br = self.s.copilot, self.s.grok
        low = text.lower()
        tilt = cp.coach.tilt(text)
        if tilt:  # El COACH responde antes que nadie
            return tilt, False
        cmd, _, arg = low.partition(" ")
        arg = arg.strip()
        if cmd in ("/start", "/help", "/ayuda"):
            return HELP, False
        if cmd in ("/tarjeta", "/check", "/card"):
            if not arg:
                return "Dime la cripto: /tarjeta SOL", False
            return br.card(arg)["tarjeta"], True
        if cmd == "/radar":
            rows = br.scan()["ranking"][:5]
            return "RADAR · Spotter ahora\n" + "\n".join(
                f"{r['cripto']:<6} {r['puntos'] if r['puntos'] is not None else 'n/a':>5} · {r['spotter']}" for r in rows), True
        if cmd == "/plan":
            return self.positions_text(), True
        if cmd == "/coach":
            st = cp.coach.status()
            return (f"COACH · hoy\nOperaciones {st['trades_today']}/{st['max_trades']} · P&L {st['pnl_today']:+.2f} USDT "
                    f"({st['pnl_pct']:+.2f}%)\nAbiertas {st['open']}/{st['max_open']} · racha de pérdidas {st['streak']}\n"
                    + (f"PAUSADO POR TUS REGLAS · {st['blocked']}" if st["blocked"] else "Sin bloqueos.")), True
        if cmd == "/reglas":
            return cp.rules.markdown(cp.coach.bankroll()), True
        if cmd in ("/resumen", "/report"):
            return br.summary("dia")["resumen"], True
        if cmd == "/semana":
            return br.summary("semana")["resumen"], True
        if cmd == "/stop":
            self.s.autopilot.set(enabled=False)
            return "Listo por hoy. Apagué el piloto automático; tus posiciones siguen vigiladas con su SL.", False
        if cmd.startswith("/"):
            return "No conozco ese comando. /help", False
        words = re.findall(r"[a-zA-Z]{2,10}", text.upper())
        coin = next((w for w in words if w in self._coins()), None)
        if coin:
            return br.card(coin)["tarjeta"], True
        return "Te leo. Para una tarjeta escribe el nombre de una cripto (ej. «SOL») o /help.", False

    def positions_text(self) -> str:
        pos = self.s.positions.all()
        if not pos:
            return "Sin posiciones abiertas."
        lines = ["POSICIONES"]
        for p in pos:
            tp1 = (p.get("tps") or [[None]])[0][0]
            lines.append(f"{p.get('coin')} {p.get('direction')} · {p.get('mode', 'papel')} · entrada {p.get('entry'):,.6g} · "
                         f"SL {p.get('stop_loss'):,.6g}" + (f" · TP1 {tp1:,.6g}" if tp1 else ""))
        return "\n".join(lines)

    def dashboard_text(self) -> str:
        cp = self.s.copilot
        st = cp.coach.status()
        now = datetime.now(cp.rules.tz()).strftime("%H:%M")
        pil = self.s.autopilot.state()
        lines = [f"COPILOTO FG · {'PILOTO ' + pil['mode'].upper() if pil['enabled'] else 'TÚ OPERAS'} · {now}",
                 f"Capital ${st['bankroll']:,.2f} · hoy {st['pnl_today']:+.2f} ({st['pnl_pct']:+.2f}%) · "
                 f"{st['trades_today']}/{st['max_trades']} operaciones · {st['open']}/{st['max_open']} abiertas"]
        if st["blocked"]:
            lines.append("PAUSADO · " + st["blocked"])
        for p in self.s.positions.all()[:5]:
            lines.append(f"ABIERTA: {p.get('coin')} {p.get('direction')} · SL {p.get('stop_loss'):,.6g}")
        cards = cp.scribe.cards()[-20:]
        g = sum(c["band"] == "GREEN" for c in cards)
        y = sum(c["band"] == "YELLOW" for c in cards)
        lines.append(f"TARJETAS recientes: {g} GREEN · {y} YELLOW")
        return "\n".join(lines)

    def update_dashboard(self) -> None:
        """1 mensaje fijado que se edita; si no se puede editar, se fija uno nuevo."""
        st = self.state()
        text = _pre(self.dashboard_text())
        if st.get("pinned_id"):
            try:
                self._call("editMessageText", chat_id=self.chat_id, message_id=st["pinned_id"], text=text, parse_mode="HTML")
                st["last_dash"] = time.time()
                self.save(st)
                return
            except Exception as exc:  # noqa: BLE001
                if "not modified" in str(exc):
                    return
        try:
            m = self._call("sendMessage", chat_id=self.chat_id, text=text, parse_mode="HTML")
            self._call("pinChatMessage", chat_id=self.chat_id, message_id=m["message_id"], disable_notification=True)
            st["pinned_id"], st["last_dash"] = m["message_id"], time.time()
            self.save(st)
        except Exception as exc:  # noqa: BLE001
            log.info("No se pudo fijar el tablero: %s", exc)

    def on_close(self, pos: dict, result: dict) -> None:
        """Hook del ejecutor: avisa cada cierre con P&L y motivo."""
        pnl = float(result.get("pnl") or 0)
        self.send(f"{'✅' if pnl > 0 else '🔻'} Cerrada {pos.get('coin')} {pos.get('direction')} ({pos.get('mode', 'papel')}) · "
                  f"{result.get('reason', '')} · P&L {pnl:+.2f} USDT")

    # ------------------------------------------------------------- bucle
    def poll_once(self, timeout: int = 25) -> int:
        st = self.state()
        ups = self._call("getUpdates", http_timeout=timeout + 10, offset=st["offset"], timeout=timeout)
        n = 0
        for u in ups or []:
            st = self.state()
            st["offset"] = int(u["update_id"]) + 1
            self.save(st)
            try:
                self.handle(u)
            except Exception as exc:  # noqa: BLE001 - un comando roto no tumba el bot
                log.warning("Telegram: error con un comando: %s", exc)
                self.send(f"No pude con eso: {html.escape(str(exc)[:120])}")
            n += 1
        return n

    def _loop(self) -> None:
        backoff = 1
        while not self._stop.is_set():
            try:
                self.poll_once()
                if self.ready() and time.time() - self.state().get("last_dash", 0) > 60:
                    self.update_dashboard()
                backoff = 1
            except Exception as exc:  # noqa: BLE001 - sin internet: reintenta con espera creciente
                log.info("Telegram sin conexión: %s", exc)
                self._stop.wait(backoff)
                backoff = min(60, backoff * 2)

    def start(self) -> str:
        if not self.configured():
            return "Falta TELEGRAM_BOT_TOKEN en el .env."
        if self.running():
            return "ya estaba encendido"
        if self.on_close not in self.s.executor.on_close:
            self.s.executor.on_close.append(self.on_close)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="fg-telegram", daemon=True)
        self._thread.start()
        if self.ready():
            self.send("FG VIEW conectado. /help para ver los comandos.")
        return "encendido" if self.ready() else "encendido: escribe /start al bot para ver tu chat id"

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None
