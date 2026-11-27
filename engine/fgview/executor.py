"""
engine/fgview/executor.py
=========================
ORDER EXECUTOR (Módulo 13): prepara la orden y, SOLO si tú confirmas, la ejecuta.

Modos
-----
* PAPEL (por defecto): simula la orden con precios reales, sin dinero. Se cierra sola
  al tocar SL, TP3 o al pasar 30 min, y queda en la bitácora.
* REAL (semiautomático): Binance o Bybit por API. El bot prepara entrada + SL + TP1/TP2/TP3
  (reduce-only) y TÚ confirmas. Para activarlo hace falta, en el archivo .env:
      LIVE_TRADING_ENABLED=true
      BINANCE_API_KEY=... / BINANCE_API_SECRET=...   (o BYBIT_...)
  y además la API key debe tener SOLO permiso de trading, NUNCA de retiro, y lista de
  IPs permitidas. El bot lo comprueba con el exchange y se niega a operar si no.

Reglas de seguridad
-------------------
* Margen aislado siempre (nunca cruzado).
* El SL se coloca en el exchange en el mismo momento que la entrada; si el SL falla,
  la posición se cierra a mercado al instante: NUNCA queda una posición sin SL.
* Mover el SL a break-even tras TP1 lo haces tú en el exchange (el bot te avisa).
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Callable

import pandas as pd

from engine.config import SETTINGS, get_logger
from engine.data_feeder import DataFeeder, to_ccxt_symbol
from engine.fgview.config import DEFAULT_CONFIG, FGConfig
from engine.fgview.journal import TradeJournal
from engine.fgview.plan import Plan
from engine.fgview.risk import PositionBook
from engine.position_simulator import fmt_px, fmt_usd

log = get_logger("fgview.executor")

LIVE_EXCHANGES = ("binance", "bybit")


def live_enabled() -> bool:
    return os.getenv("LIVE_TRADING_ENABLED", "false").strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


@dataclass
class OrderTicket:
    exchange: str
    symbol: str  # Símbolo CCXT del perpetuo (BTC/USDT:USDT)
    coin: str
    market: str
    direction: str
    qty: float
    entry: float
    leverage: int
    margin: float
    notional: float
    stop_loss: float
    liquidation: float
    tps: list[tuple[float, float]]  # (precio, % de la posición)
    strategy: str = "FG VIEW"
    margin_mode: str = "isolated"
    tp_pnl: list[float] = field(default_factory=list)
    sl_pnl: float = 0.0
    fees: float = 0.0
    break_even: float = 0.0
    score: float | None = None
    max_hold_min: int = 30  # 30 min scalping · 720 min (12 h) swing
    post_only: bool = False  # Entrada como maker (orden límite que no cruza el libro): comisión 0.02%
    trail: float | None = None  # Distancia del trailing stop (precio). None = sin trailing

    @property
    def side(self) -> str:
        return "buy" if self.direction == "LONG" else "sell"

    @property
    def close_side(self) -> str:
        return "sell" if self.direction == "LONG" else "buy"

    def lines(self) -> list[str]:
        tp = " · ".join(f"TP{i + 1} ${fmt_px(p)} ({pct:g}%)" for i, (p, pct) in enumerate(self.tps))
        return [
            f"**{self.direction} {self.coin}** en {self.exchange.capitalize()} · margen **AISLADO** · {self.leverage}x",
            f"Cantidad {self.qty:.6g} {self.coin} · posición {fmt_usd(self.notional)} · margen {fmt_usd(self.margin)}",
            f"Entrada {'post-only (maker)' if self.post_only else 'a mercado'} ~${fmt_px(self.entry)} · "
            f"SL ${fmt_px(self.stop_loss)} (reduce-only) · "
            f"liquidación ${fmt_px(self.liquidation)}",
            f"{tp} (reduce-only) · tiempo máximo {self.max_hold_min // 60} h" if self.max_hold_min >= 120 else
            f"{tp} (reduce-only) · tiempo máximo {self.max_hold_min} min",
        ] + ([f"Trailing stop a ${fmt_px(self.trail)} del precio (se activa tras TP1)"] if self.trail else [])

    def stop_problem(self) -> str | None:
        """None si el SL es válido: del lado de la pérdida y ANTES de la liquidación."""
        if not self.stop_loss or self.stop_loss <= 0:
            return "La orden no tiene Stop Loss. Sin SL no se opera."
        if self.direction == "LONG" and not (self.liquidation < self.stop_loss < self.entry):
            return "El SL de un LONG debe estar debajo de la entrada y por encima de la liquidación."
        if self.direction == "SHORT" and not (self.entry < self.stop_loss < self.liquidation):
            return "El SL de un SHORT debe estar encima de la entrada y por debajo de la liquidación."
        return None


class PaperBroker:
    """
    Ejecución simulada con precios reales de 1 minuto, PASO A PASO: cada actualización
    solo mira las velas nuevas con el SL vigente en ese momento (si el escudo o el trailing
    lo movieron, no se aplica hacia atrás). Guarda la etapa (TPs cobrados) en la posición.
    """

    REASONS = {0: "Stop Loss", 1: "TP1 + resto en break-even", 2: "TP1+TP2 + resto en TP1"}

    def __init__(self, feeder: DataFeeder, config: FGConfig = DEFAULT_CONFIG) -> None:
        self.feeder = feeder
        self.max_bars = config.max_hold_min

    @staticmethod
    def open_pnl(pos: dict, price: float) -> float:
        """PnL total (cobrado + abierto) si se cerrara ahora a `price`."""
        s = 1 if pos["direction"] == "LONG" else -1
        rem = (1.0, 0.5, 0.2)[min(int(pos.get("stage", 0)), 2)]
        return (pos.get("realized", 0.0) + pos["notional"] * rem * s * (price - pos["entry"]) / pos["entry"]
                - pos["fees"] * rem)

    def evaluate(self, pos: dict, bars: pd.DataFrame) -> dict | None:
        """Procesa las velas nuevas. Devuelve el cierre {exit_price, pnl, reason, minutes} o None (sigue abierta).
        Actualiza `pos` (stage, realized, last_bar) para la próxima vez."""
        s = 1 if pos["direction"] == "LONG" else -1
        best = max if s == 1 else min
        entry, notional, fees = pos["entry"], pos["notional"], pos["fees"]
        tps_px = [p for p, _ in pos["tps"]]
        closes = [c / 100 for _, c in pos["tps"]]
        rem = (1.0, 0.5, 0.2)
        opened = pd.Timestamp(pos["opened_at"])
        last = pd.Timestamp(pos["last_bar"]) if pos.get("last_bar") else opened.floor("min")
        stage, realized = int(pos.get("stage", 0)), float(pos.get("realized", 0.0))
        max_hold = int(pos.get("max_hold_min", self.max_bars))
        original_sl = pos.get("initial_stop", pos["stop_loss"])
        for ts, row in bars[bars.index > last].iterrows():
            sl = pos["stop_loss"]
            stop = sl if stage == 0 else best(sl, pos["break_even"] if stage == 1 else tps_px[0])
            hit_stop = row["low"] <= stop if s == 1 else row["high"] >= stop
            if hit_stop:  # El stop gana si en la misma vela se tocan stop y objetivo (conservador)
                pnl = realized + notional * rem[stage] * s * (stop - entry) / entry - fees * rem[stage]
                moved = stage == 0 and abs(stop - original_sl) > 1e-12
                reason = "Stop movido (escudo/trailing)" if moved else self.REASONS[stage]
                return {"exit_price": stop, "pnl": pnl, "reason": reason, "minutes": (ts - opened).total_seconds() / 60}
            if (row["high"] >= tps_px[stage]) if s == 1 else (row["low"] <= tps_px[stage]):
                realized += notional * closes[stage] * s * (tps_px[stage] - entry) / entry - fees * closes[stage]
                stage += 1
                if stage == 3:
                    return {"exit_price": tps_px[2], "pnl": realized, "reason": "TP1+TP2+TP3",
                            "minutes": (ts - opened).total_seconds() / 60}
            pos.update(stage=stage, realized=realized, last_bar=ts.isoformat())
            if (ts - opened).total_seconds() / 60 >= max_hold:
                px = float(row["close"])
                pnl = realized + notional * rem[stage] * s * (px - entry) / entry - fees * rem[stage]
                label = f"{max_hold // 60} h" if max_hold >= 120 else f"{max_hold} min"
                return {"exit_price": px, "pnl": pnl, "reason": f"Tiempo ({label})",
                        "minutes": (ts - opened).total_seconds() / 60}
        return None


class LiveBroker:
    """Ejecución real con CCXT (Binance / Bybit), siempre tras confirmación del usuario."""

    def __init__(self, exchange_id: str, testnet: bool = False) -> None:
        if exchange_id not in LIVE_EXCHANGES:
            raise ValueError("La ejecución real solo está disponible en Binance y Bybit.")
        self.exchange_id, self.testnet = exchange_id, testnet
        self._ex = None

    def exchange(self):
        if self._ex is None:
            import ccxt

            keys = SETTINGS.api_keys.get(self.exchange_id, {})
            if not keys.get("apiKey") or not keys.get("secret"):
                raise RuntimeError(f"Faltan las API keys de {self.exchange_id} en el archivo .env.")
            ex = getattr(ccxt, self.exchange_id)({"apiKey": keys["apiKey"], "secret": keys["secret"],
                                                  "enableRateLimit": True, "options": {"defaultType": "swap"}})
            if self.testnet:
                ex.set_sandbox_mode(True)
            ex.load_markets()
            self._ex = ex
        return self._ex

    @staticmethod
    def _call(ex, *names):
        for n in names:
            fn = getattr(ex, n, None)
            if fn is not None:
                return fn()
        raise RuntimeError("Esta versión de CCXT no permite consultar los permisos de la API key.")

    def safety_problems(self) -> list[str]:
        """Comprueba con el exchange: sin permiso de retiro y con lista de IPs. Si no se puede verificar, bloquea."""
        ex = self.exchange()
        problems: list[str] = []
        try:
            if self.exchange_id == "binance":
                r = self._call(ex, "sapi_get_account_apirestrictions", "sapiGetAccountApiRestrictions")
                if r.get("enableWithdrawals") in (True, "true"):
                    problems.append("La API key tiene permiso de RETIRO activado. Desactívalo en Binance.")
                if r.get("ipRestrict") not in (True, "true"):
                    problems.append("La API key no tiene lista de IPs permitidas. Añade tu IP en Binance.")
                if r.get("enableFutures") not in (True, "true"):
                    problems.append("La API key no tiene permiso de futuros.")
            else:
                r = self._call(ex, "private_get_v5_user_query_api", "privateGetV5UserQueryApi").get("result", {})
                wallet = (r.get("permissions") or {}).get("Wallet") or []
                if any("withdraw" in str(p).lower() for p in wallet):
                    problems.append("La API key tiene permiso de RETIRO. Desactívalo en Bybit.")
                ips = r.get("ips") or []
                if not ips or "*" in ips:
                    problems.append("La API key no tiene lista de IPs permitidas. Añade tu IP en Bybit.")
                if str(r.get("readOnly")) == "1":
                    problems.append("La API key es de solo lectura: no puede operar.")
        except Exception as exc:  # noqa: BLE001 - si no se puede verificar, no se opera
            problems.append(f"No se pudieron verificar los permisos de la API key ({str(exc)[:120]}).")
        return problems

    def open(self, t: OrderTicket) -> dict:
        ex = self.exchange()
        problems = self.safety_problems()
        if problems:
            return {"status": "rejected", "problems": problems}
        try:
            ex.set_margin_mode("isolated", t.symbol)
        except Exception as exc:  # noqa: BLE001 - "ya estaba en aislado" también lanza error en algunos exchanges
            if "isolated" not in str(exc).lower() and "no need" not in str(exc).lower():
                log.warning("set_margin_mode: %s", exc)
        ex.set_leverage(t.leverage, t.symbol)
        qty = float(ex.amount_to_precision(t.symbol, t.qty))
        if t.post_only:
            entry, qty = self._post_only_entry(ex, t, qty)
            if qty <= 0:
                return {"status": "not_filled", "message": "La orden post-only no se llenó en 20 s y se canceló. "
                                                            "No hay posición abierta: vuelve a intentarlo."}
        else:
            entry = ex.create_order(t.symbol, "market", t.side, qty)
        try:
            sl = ex.create_order(t.symbol, "market", t.close_side, qty, None,
                                 {"stopLossPrice": float(ex.price_to_precision(t.symbol, t.stop_loss)), "reduceOnly": True})
        except Exception as exc:  # noqa: BLE001 - sin SL no se queda abierta
            log.error("Falló el SL: cierre de emergencia: %s", exc)
            ex.create_order(t.symbol, "market", t.close_side, qty, None, {"reduceOnly": True})
            return {"status": "error", "message": f"No se pudo colocar el SL; la posición se cerró a mercado. {exc}"}
        orders, left = [], qty
        for i, (price, pct) in enumerate(t.tps):
            q = left if i == len(t.tps) - 1 else float(ex.amount_to_precision(t.symbol, qty * pct / 100))
            left -= q
            try:
                orders.append(ex.create_order(t.symbol, "limit", t.close_side, q,
                                              float(ex.price_to_precision(t.symbol, price)), {"reduceOnly": True}))
            except Exception as exc:  # noqa: BLE001 - el SL ya protege la posición
                log.warning("TP%d no colocado: %s", i + 1, exc)
        return {"status": "filled", "entry": entry, "stop_loss": sl, "take_profits": orders, "qty": qty,
                "sl_order_id": (sl or {}).get("id")}

    @staticmethod
    def _post_only_entry(ex, t: OrderTicket, qty: float, wait_s: float = 20.0) -> tuple[dict, float]:
        """Orden límite post-only en el mejor precio del libro; espera hasta 20 s. Devuelve (orden, cantidad llena)."""
        import time as _time

        book = ex.fetch_order_book(t.symbol, 5)
        price = book["bids"][0][0] if t.side == "buy" else book["asks"][0][0]
        order = ex.create_order(t.symbol, "limit", t.side, qty, price, {"postOnly": True})
        deadline = _time.time() + wait_s
        while _time.time() < deadline:
            o = ex.fetch_order(order["id"], t.symbol)
            if o.get("status") == "closed":
                return o, float(o.get("filled") or qty)
            _time.sleep(1.0)
        try:
            ex.cancel_order(order["id"], t.symbol)
        except Exception:  # noqa: BLE001 - quizá se llenó justo ahora
            pass
        o = ex.fetch_order(order["id"], t.symbol)
        return o, float(ex.amount_to_precision(t.symbol, float(o.get("filled") or 0))) if o.get("filled") else 0.0

    def move_stop(self, pos: dict, new_stop: float) -> dict:
        """Mueve el SL en el exchange: crea el nuevo y luego cancela el viejo (nunca queda sin SL)."""
        ex = self.exchange()
        close_side = "sell" if pos["direction"] == "LONG" else "buy"
        new = ex.create_order(pos["symbol"], "market", close_side, pos["qty"], None,
                              {"stopLossPrice": float(ex.price_to_precision(pos["symbol"], new_stop)), "reduceOnly": True})
        if pos.get("sl_order_id"):
            try:
                ex.cancel_order(pos["sl_order_id"], pos["symbol"])
            except Exception as exc:  # noqa: BLE001 - el viejo quizá ya no existe; el nuevo protege
                log.warning("No se pudo cancelar el SL anterior: %s", exc)
        return {"sl_order_id": new.get("id")}


class OrderExecutor:
    def __init__(self, feeder: DataFeeder, book: PositionBook, journal: TradeJournal | None,
                 config: FGConfig = DEFAULT_CONFIG) -> None:
        self.feeder, self.book, self.journal, self.cfg = feeder, book, journal, config
        self.paper = PaperBroker(feeder, config)
        self.on_close: list[Callable[[dict, dict], None]] = []

    @staticmethod
    def build_ticket(plan: Plan, symbol: str, market: str, exchange: str = "binance", strategy: str = "FG VIEW",
                     score: float | None = None, contract: str = "USDT-M", post_only: bool = False,
                     max_hold_min: int = 30, trailing: bool = True, config: FGConfig = DEFAULT_CONFIG) -> OrderTicket:
        fees, tp_pnl, sl_pnl = plan.fees, list(plan.tp_pnl), plan.sl_pnl
        if post_only:  # La entrada paga comisión maker en lugar de taker
            delta = plan.notional * (config.fee - config.maker_fee)
            fees -= delta
            tp_pnl = [x + delta * close / 100 for x, (_, _, close) in zip(tp_pnl, plan.tps)]
            sl_pnl += delta
        return OrderTicket(
            exchange=exchange, symbol=to_ccxt_symbol(symbol, contract) if market == "crypto" else symbol,
            coin=plan.coin, market=market, direction=plan.direction, qty=plan.notional / plan.entry, entry=plan.entry,
            leverage=int(plan.leverage), margin=plan.margin, notional=plan.notional, stop_loss=plan.stop_loss,
            liquidation=plan.liquidation, tps=[(p, close) for p, _, close in plan.tps], strategy=strategy,
            tp_pnl=tp_pnl, sl_pnl=sl_pnl, fees=fees, break_even=plan.break_even, score=score,
            max_hold_min=max_hold_min, post_only=post_only, trail=plan.trailing if trailing else None)

    def submit(self, ticket: OrderTicket, confirmed: bool, mode: str = "paper", testnet: bool = False) -> dict:
        if not confirmed:
            return {"status": "pending", "message": "Falta tu confirmación."}
        if (problem := ticket.stop_problem()):  # Sin SL válido NO hay orden (ni en papel ni en real)
            return {"status": "rejected", "problems": [problem]}
        if mode == "live":
            if not live_enabled():
                return {"status": "rejected", "problems": ["Modo real desactivado: pon LIVE_TRADING_ENABLED=true en .env."]}
            if ticket.market != "crypto":
                return {"status": "rejected", "problems": ["Solo se puede operar en real con cripto."]}
            res = LiveBroker(ticket.exchange, testnet).open(ticket)
            if res.get("status") == "filled":
                self.book.add({**asdict(ticket), "mode": "real", "qty": res["qty"], "initial_stop": ticket.stop_loss,
                               "sl_order_id": res.get("sl_order_id"), "testnet": testnet})
            return res
        pos = self.book.add({**asdict(ticket), "mode": "papel", "initial_stop": ticket.stop_loss})
        return {"status": "filled", "mode": "papel", "position": pos}

    # ------------------------------------------------------- escudo anti-squeeze
    SHIELD_KEY = "squeeze_shield"

    def shield_enabled(self) -> bool:
        db = getattr(self.book, "db", None)
        return bool(db.get_state(self.SHIELD_KEY, True)) if db is not None else True

    def set_shield(self, on: bool) -> None:
        db = getattr(self.book, "db", None)
        if db is not None:
            db.set_state(self.SHIELD_KEY, bool(on))

    def shield(self, pos: dict, price: float, atr: float | None) -> str | None:
        """
        Squeeze contra la posición: si va ganando, SL a break-even; si va perdiendo, SL a
        precio ± 0.5 × ATR para perder menos. NUNCA aleja el SL. Devuelve qué hizo.
        """
        if not self.shield_enabled():
            return None
        s = 1 if pos["direction"] == "LONG" else -1
        profit = s * (price - pos["entry"])
        if profit > 0:
            target, why = pos["break_even"], "SL a break-even (ya no puedes perder)"
        elif atr and atr > 0:
            target, why = price - s * 0.5 * atr, "SL ajustado a 0.5 × ATR del precio (pierdes menos)"
        else:
            return None
        return self._apply_stop(pos, price, target, f"🛡️ Escudo anti-squeeze {pos['coin']} {pos['direction']}: {why}")

    def _apply_stop(self, pos: dict, price: float, target: float, label: str) -> str | None:
        """Mueve el SL solo si lo APRIETA y queda del lado correcto del precio (nunca lo aleja)."""
        s = 1 if pos["direction"] == "LONG" else -1
        tighter = (target > pos["stop_loss"]) if s == 1 else (target < pos["stop_loss"])
        valid_side = (target < price) if s == 1 else (target > price)
        if not (tighter and valid_side):
            return None
        # El PnL si salta el SL cambia con el nuevo nivel (lo usa la simulación de papel)
        changes = {"stop_loss": target, "sl_pnl": pos["notional"] * s * (target - pos["entry"]) / pos["entry"] - pos["fees"]}
        if pos.get("mode") == "real":
            try:
                changes.update(LiveBroker(pos["exchange"], bool(pos.get("testnet"))).move_stop(pos, target))
            except Exception as exc:  # noqa: BLE001
                return f"🛡️ No se pudo mover el SL en el exchange ({str(exc)[:80]}): MUÉVELO A MANO a ${fmt_px(target)}"
        self.book.update(pos["id"], **changes)
        pos.update(changes)
        return f"{label} → ${fmt_px(target)}"

    # ------------------------------------------------------------ trailing stop
    TRAIL_KEY = "trailing_stop"

    def trailing_enabled(self) -> bool:
        db = getattr(self.book, "db", None)
        return bool(db.get_state(self.TRAIL_KEY, True)) if db is not None else True

    def set_trailing(self, on: bool) -> None:
        db = getattr(self.book, "db", None)
        if db is not None:
            db.set_state(self.TRAIL_KEY, bool(on))

    def trail(self, pos: dict, price: float, atr: float | None = None) -> str | None:
        """
        Trailing stop: tras cobrar TP1 (o con ganancia ≥ 1.5 × la distancia), el SL sigue al
        precio a la distancia `trail` (0.5 × ATR de 5 min en scalping, 1 × ATR de 1 h en swing).
        """
        if not self.trailing_enabled():
            return None
        dist = pos.get("trail") or (0.5 * atr if atr else None)
        if not dist or dist <= 0:
            return None
        s = 1 if pos["direction"] == "LONG" else -1
        profit = s * (price - pos["entry"])
        if int(pos.get("stage", 0)) < 1 and profit < 1.5 * dist:
            return None
        return self._apply_stop(pos, price, price - s * dist, f"📈 Trailing stop {pos['coin']} {pos['direction']}")

    def refresh_paper(self) -> list[dict]:
        """Cierra las posiciones de papel que ya tocaron SL/TP3 o pasaron su tiempo máximo."""
        closed = []
        for pos in self.book.all():
            if pos.get("mode") != "papel":
                continue
            try:
                sym = "DEMO/USDT" if pos["market"] == "demo" else f"{pos['coin']}/USDT"
                since = pd.Timestamp(pos.get("last_bar") or pos["opened_at"])
                gap = (pd.Timestamp.now(tz="UTC") - since).total_seconds() / 60
                bars = self.feeder.get_market_data(sym, pos["market"], "1m", limit=int(min(1500, max(150, gap + 10))),
                                                   force_refresh=True).df
                res = self.paper.evaluate(pos, bars)
                if not res:  # Guarda la etapa para la próxima actualización
                    self.book.update(pos["id"], stage=pos.get("stage", 0), realized=pos.get("realized", 0.0),
                                     last_bar=pos.get("last_bar"))
            except Exception as exc:  # noqa: BLE001 - se reintenta en la próxima actualización
                log.warning("No se pudo actualizar la posición de papel %s: %s", pos.get("id"), exc)
                continue
            if res:
                closed.append(self.close(pos["id"], res["exit_price"], res["pnl"], res["reason"]))
        return [c for c in closed if c]

    def close(self, pos_id: str, exit_price: float, pnl: float | None = None, reason: str = "Cierre manual") -> dict | None:
        pos = self.book.remove(pos_id)
        if pos is None:
            return None
        if pnl is None:  # Cierre manual: lo ya cobrado en TPs + lo que quedaba abierto
            pnl = PaperBroker.open_pnl(pos, exit_price)
        opened = pd.Timestamp(pos["opened_at"])
        minutes = (datetime.now(timezone.utc) - opened.to_pydatetime()).total_seconds() / 60
        result = {**pos, "exit_price": exit_price, "pnl": pnl, "reason": reason}
        if self.journal is not None:
            self.journal.add({"opened_at": pos["opened_at"], "coin": pos["coin"], "direction": pos["direction"],
                              "entry": pos["entry"], "exit_price": exit_price, "leverage": pos["leverage"],
                              "margin": pos["margin"], "pnl": pnl, "score": pos.get("score"),
                              "duration_min": round(minutes, 1),
                              "notes": f"{pos.get('strategy', 'FG VIEW')} · {pos.get('mode', 'papel')} · {reason}"})
        for hook in self.on_close:
            try:
                hook(pos, result)
            except Exception as exc:  # noqa: BLE001
                log.warning("Hook de cierre falló: %s", exc)
        return result
