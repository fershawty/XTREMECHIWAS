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
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Callable

import pandas as pd

from engine.config import SETTINGS, get_logger
from engine.data_feeder import DataFeeder, to_ccxt_symbol
from engine.fgview.config import DEFAULT_CONFIG, FGConfig
from engine.fgview.journal import TradeJournal
from engine.fgview.manager import TradeManager
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
            f"Entrada a mercado ~${fmt_px(self.entry)} · SL ${fmt_px(self.stop_loss)} (reduce-only) · "
            f"liquidación ${fmt_px(self.liquidation)}",
            f"{tp} (reduce-only)",
        ]


class PaperBroker:
    """Ejecución simulada con precios reales de 1 minuto."""

    def __init__(self, feeder: DataFeeder, config: FGConfig = DEFAULT_CONFIG) -> None:
        self.feeder = feeder
        self.manager = TradeManager(replace(config, bar_minutes=1), use_native=False)
        self.max_bars = config.max_hold_min

    def evaluate(self, pos: dict, bars: pd.DataFrame) -> dict | None:
        """Devuelve el cierre {exit_price, pnl, reason} si la operación terminó; None si sigue abierta."""
        opened = pd.Timestamp(pos["opened_at"])
        after = bars[bars.index > opened.floor("min")]
        if after.empty:
            return None
        h = [pos["entry"]] + after["high"].tolist()
        lo = [pos["entry"]] + after["low"].tolist()
        c = [pos["entry"]] + after["close"].tolist()
        plan = SimpleNamespace(side=1 if pos["direction"] == "LONG" else -1, notional=pos["notional"], fees=pos["fees"],
                               tps=[(p, 0.0, 0.0) for p, _ in pos["tps"]], stop_loss=pos["stop_loss"],
                               break_even=pos["break_even"], tp_pnl=pos["tp_pnl"], sl_pnl=pos["sl_pnl"],
                               entry=pos["entry"])
        import numpy as np

        out = self.manager.run(plan, np.array(h), np.array(lo), np.array(c), 0)
        finished = out.reason != "Tiempo (30 min)" or len(c) - 1 >= self.max_bars
        if not finished:
            return None
        levels = {"Stop Loss": pos["stop_loss"], "TP1 + resto en break-even": pos["break_even"],
                  "TP1+TP2 + resto en TP1": pos["tps"][0][0], "TP1+TP2+TP3": pos["tps"][2][0]}
        return {"exit_price": levels.get(out.reason, c[out.exit_index]), "pnl": out.pnl, "reason": out.reason,
                "minutes": out.exit_index}


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
        return {"status": "filled", "entry": entry, "stop_loss": sl, "take_profits": orders, "qty": qty}


class OrderExecutor:
    def __init__(self, feeder: DataFeeder, book: PositionBook, journal: TradeJournal | None,
                 config: FGConfig = DEFAULT_CONFIG) -> None:
        self.feeder, self.book, self.journal, self.cfg = feeder, book, journal, config
        self.paper = PaperBroker(feeder, config)
        self.on_close: list[Callable[[dict, dict], None]] = []

    @staticmethod
    def build_ticket(plan: Plan, symbol: str, market: str, exchange: str = "binance", strategy: str = "FG VIEW",
                     score: float | None = None, contract: str = "USDT-M") -> OrderTicket:
        return OrderTicket(
            exchange=exchange, symbol=to_ccxt_symbol(symbol, contract) if market == "crypto" else symbol,
            coin=plan.coin, market=market, direction=plan.direction, qty=plan.notional / plan.entry, entry=plan.entry,
            leverage=int(plan.leverage), margin=plan.margin, notional=plan.notional, stop_loss=plan.stop_loss,
            liquidation=plan.liquidation, tps=[(p, close) for p, _, close in plan.tps], strategy=strategy,
            tp_pnl=list(plan.tp_pnl), sl_pnl=plan.sl_pnl, fees=plan.fees, break_even=plan.break_even, score=score)

    def submit(self, ticket: OrderTicket, confirmed: bool, mode: str = "paper", testnet: bool = False) -> dict:
        if not confirmed:
            return {"status": "pending", "message": "Falta tu confirmación."}
        if mode == "live":
            if not live_enabled():
                return {"status": "rejected", "problems": ["Modo real desactivado: pon LIVE_TRADING_ENABLED=true en .env."]}
            if ticket.market != "crypto":
                return {"status": "rejected", "problems": ["Solo se puede operar en real con cripto."]}
            res = LiveBroker(ticket.exchange, testnet).open(ticket)
            if res.get("status") == "filled":
                self.book.add({**asdict(ticket), "mode": "real", "qty": res["qty"]})
            return res
        pos = self.book.add({**asdict(ticket), "mode": "papel"})
        return {"status": "filled", "mode": "papel", "position": pos}

    def refresh_paper(self) -> list[dict]:
        """Cierra las posiciones de papel que ya tocaron SL/TP3 o pasaron 30 min."""
        closed = []
        for pos in self.book.all():
            if pos.get("mode") != "papel":
                continue
            try:
                sym = "DEMO/USDT" if pos["market"] == "demo" else f"{pos['coin']}/USDT"
                bars = self.feeder.get_market_data(sym, pos["market"], "1m", limit=200, force_refresh=True).df
                res = self.paper.evaluate(pos, bars)
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
        if pnl is None:  # Cierre manual a un precio: PnL de lo que quedaba abierto
            s = 1 if pos["direction"] == "LONG" else -1
            pnl = pos["notional"] * s * (exit_price - pos["entry"]) / pos["entry"] - pos["fees"]
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
