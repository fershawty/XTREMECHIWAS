"""
engine/fgview/pricealerts.py
============================
PRICE ALERTS (Módulo 14).

Tipos de alerta:
* precio por encima / por debajo de un nivel
* cruce de EMA 9/21 (5 min)
* movimiento brusco: más de 2% en 5 minutos
* liquidación cerca: precio a menos de 1.5× la distancia del SL de la liquidación
  (se vigila automáticamente en cada posición abierta)
* squeeze: subida brusca contra tu SHORT (o caída contra tu LONG), automática

Se envían por Telegram y/o webhook (sirve para notificaciones push con ntfy.sh,
Discord, Pushover…). Configúralo en .env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
ALERT_WEBHOOK_URL. Para vigilar en segundo plano sin la app abierta:
    python -m engine.fgview.pricealerts
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from engine.alerts import AlertManager
from engine.data_feeder import DataFeeder
from engine.database import Database
from engine.fgview.risk import PositionBook
from engine.fgview.squeeze import SqueezeGuard
from engine.indicators import compute_all
from engine.position_simulator import fmt_px

KINDS = {
    "above": "Precio por encima de",
    "below": "Precio por debajo de",
    "ema_cross": "Cruce EMA 9/21 (5m)",
    "sharp_move": "Movimiento brusco (> 2% en 5 min)",
}


@dataclass
class FiredAlert:
    coin: str
    kind: str
    message: str


class PriceAlerts:
    KEY = "price_alerts"

    def __init__(self, feeder: DataFeeder, book: PositionBook, db: Database | None,
                 sender: AlertManager | None = None, sharp_pct: float = 2.0, liq_factor: float = 1.5,
                 cooldown_min: int = 15) -> None:
        self.feeder, self.book, self.db = feeder, book, db
        self.sender = sender or AlertManager()
        self.sharp_pct, self.liq_factor, self.cooldown = sharp_pct, liq_factor, timedelta(minutes=cooldown_min)
        self._mem: list[dict] = []
        self.guard = SqueezeGuard()
        self.on_squeeze = None  # Callback (pos, precio, atr) -> texto: escudo del Order Executor
        self.on_tick = None  # Callback (pos, precio, atr) -> texto: trailing stop
        self._last_check = 0.0

    # ----------------------------------------------------------------- reglas
    def rules(self) -> list[dict]:
        return list(self.db.get_state(self.KEY, []) or []) if self.db is not None else list(self._mem)

    def _save(self, rows: list[dict]) -> None:
        if self.db is not None:
            self.db.set_state(self.KEY, rows)
        else:
            self._mem = rows

    def add(self, coin: str, kind: str, value: float | None = None, market: str = "crypto") -> dict:
        if kind not in KINDS:
            raise ValueError(f"Tipo de alerta desconocido: {kind}")
        rule = {"id": uuid.uuid4().hex[:8], "coin": coin.upper(), "kind": kind, "value": value, "market": market,
                "active": True, "last_fired": None}
        self._save(self.rules() + [rule])
        return rule

    def remove(self, rule_id: str) -> None:
        self._save([r for r in self.rules() if r["id"] != rule_id])

    # ---------------------------------------------------------------- chequeo
    def _cooling(self, last: str | None, now: datetime) -> bool:
        return bool(last) and now - datetime.fromisoformat(last) < self.cooldown

    def check(self, now: datetime | None = None, send: bool = True) -> list[FiredAlert]:
        now = now or datetime.now(timezone.utc)
        fired: list[FiredAlert] = []
        rules = self.rules()
        cache: dict[tuple, object] = {}

        def bars(coin: str, market: str, tf: str):
            key = (coin, market, tf)
            if key not in cache:
                sym = "DEMO/USDT" if market == "demo" else f"{coin}/USDT"
                cache[key] = self.feeder.get_market_data(sym, market, tf, limit=60).df
            return cache[key]

        for r in rules:
            if not r.get("active") or self._cooling(r.get("last_fired"), now):
                continue
            try:
                msg = self._eval_rule(r, bars)
            except Exception:  # noqa: BLE001 - sin datos ahora: se reintenta en el siguiente ciclo
                continue
            if msg:
                r["last_fired"] = now.isoformat()
                fired.append(FiredAlert(r["coin"], r["kind"], msg))
        self._save(rules)
        for pos in self.book.all():  # Liquidación cerca (automática)
            try:
                df = bars(pos["coin"], pos.get("market", "crypto"), "1m")
            except Exception:  # noqa: BLE001
                continue
            price = float(df["close"].iloc[-1])
            sl_dist = abs(pos["entry"] - pos["stop_loss"])
            liq_dist = abs(price - pos["liquidation"])
            if sl_dist > 0 and liq_dist < self.liq_factor * sl_dist and not self._cooling(pos.get("liq_alert"), now):
                self.book.update(pos["id"], liq_alert=now.isoformat())
                fired.append(FiredAlert(pos["coin"], "liq_near",
                                        f"⚠️ {pos['coin']} {pos['direction']}: el precio ${fmt_px(price)} está a "
                                        f"{liq_dist / sl_dist:.2f}× la distancia del SL de tu liquidación "
                                        f"(${fmt_px(pos['liquidation'])}). Revisa la posición YA."))
            if self.on_tick is not None:  # Trailing stop
                try:
                    act = self.on_tick(pos, price, None)
                    if act:
                        fired.append(FiredAlert(pos["coin"], "trail", act))
                except Exception:  # noqa: BLE001
                    pass
            try:  # Squeeze contra la posición abierta
                sq = self.guard.analyze(compute_all(bars(pos["coin"], pos.get("market", "crypto"), "5m")),
                                        pos["direction"])
            except Exception:  # noqa: BLE001
                sq = None
            if sq is not None and sq.risk == "alto":
                if self.on_squeeze is not None:  # El escudo actúa en cada revisión (solo aprieta el SL)
                    try:
                        df5 = bars(pos["coin"], pos.get("market", "crypto"), "5m")
                        atr = float((df5["high"] - df5["low"]).iloc[-14:].mean())
                        act = self.on_squeeze(pos, price, atr)
                        if act:
                            fired.append(FiredAlert(pos["coin"], "shield", act))
                    except Exception:  # noqa: BLE001
                        pass
                if not self._cooling(pos.get("sq_alert"), now):
                    self.book.update(pos["id"], sq_alert=now.isoformat())
                    fired.append(FiredAlert(pos["coin"], "squeeze", f"🚨 {pos['coin']} {pos['direction']}: {sq.text()}"))
        if send:
            for f in fired:
                self.sender.send_text(f"🔔 FG ISRAEL VIEW · {f.message}")
        return fired

    def maybe_check(self, every_s: float = 60.0) -> list[FiredAlert]:
        """Revisión con freno (la app la llama en cada refresco; como mucho una vez por minuto)."""
        if time.time() - self._last_check < every_s or (not self.rules() and not self.book.all()):
            return []
        self._last_check = time.time()
        return self.check()

    def _eval_rule(self, r: dict, bars) -> str | None:
        coin, kind, value, market = r["coin"], r["kind"], r.get("value"), r.get("market", "crypto")
        if kind in ("above", "below"):
            price = float(bars(coin, market, "1m")["close"].iloc[-1])
            if kind == "above" and price >= value:
                return f"{coin} superó ${fmt_px(value)} (ahora ${fmt_px(price)})."
            if kind == "below" and price <= value:
                return f"{coin} bajó de ${fmt_px(value)} (ahora ${fmt_px(price)})."
        elif kind == "sharp_move":
            df = bars(coin, market, "1m")
            if len(df) >= 6:
                chg = (df["close"].iloc[-1] / df["close"].iloc[-6] - 1) * 100
                if abs(chg) >= self.sharp_pct:
                    return f"{coin} se movió {chg:+.2f}% en 5 minutos."
        elif kind == "ema_cross":
            c = bars(coin, market, "5m")["close"]
            e9, e21 = c.ewm(span=9, adjust=False).mean(), c.ewm(span=21, adjust=False).mean()
            if e9.iloc[-2] <= e21.iloc[-2] and e9.iloc[-1] > e21.iloc[-1]:
                return f"{coin}: cruce alcista de EMA 9/21 en 5 min."
            if e9.iloc[-2] >= e21.iloc[-2] and e9.iloc[-1] < e21.iloc[-1]:
                return f"{coin}: cruce bajista de EMA 9/21 en 5 min."
        return None


def main() -> None:  # pragma: no cover - bucle en segundo plano
    from engine.fgview import FGServices

    alerts = FGServices.create(DataFeeder(), db=Database()).alerts  # Incluye el escudo anti-squeeze
    print("Vigilando alertas y escudo anti-squeeze cada 60 s (Ctrl+C para salir)…")
    while True:
        for f in alerts.check():
            print(datetime.now().strftime("%H:%M:%S"), f.message)
        time.sleep(60)


if __name__ == "__main__":  # pragma: no cover
    main()
