"""
engine/fgview/review.py
=======================
🔍 REVISOR DE HISTORIAL: tus operaciones reales de Binance Futures, revisadas.

Acepta el historial de órdenes ("Futures Order History") como:
* CSV o Excel exportado de Binance (columnas en español o inglés), o
* texto pegado (copia el PDF de Binance y pégalo tal cual).

Reconstruye cada operación (apertura -> cierre) y marca los errores que más cuentas hunden:
💀 liquidación · 🚫 sin Stop Loss · 😤 revancha (volver a entrar justo después de perder)
🌙 abierta horas sin SL · 🎰 memecoin · 📈 short después de una caída / long después de una subida
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pandas as pd

from engine.fgview.policy import TierCatalog
from engine.position_simulator import MAINTENANCE_MARGIN, fmt_usd

TAKER, MAKER = 0.0005, 0.0002
STOP_TYPES = ("STOP", "STOP_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_MARKET", "TRAILING_STOP_MARKET")
LINE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})\s+\d+\s+([A-Z0-9]+)\s+"
    r"(LIMIT|MARKET|LIQUIDATION|ADL|STOP_MARKET|TAKE_PROFIT_MARKET|TRAILING_STOP_MARKET|STOP|TAKE_PROFIT)\s+"
    r"(BUY|SELL)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+"
    r"(FILLED|PARTIALLY_FILLED|CANCELED|CANCELLED|EXPIRED|NEW|REJECTED)")


@dataclass
class ReviewedTrade:
    symbol: str
    direction: str
    opened: pd.Timestamp
    closed: pd.Timestamp
    qty: float
    entry: float
    exit: float
    pnl: float  # Neto estimado (con comisiones)
    liquidated: bool
    has_stop: bool
    flags: list[str] = field(default_factory=list)

    @property
    def minutes(self) -> float:
        return (self.closed - self.opened).total_seconds() / 60


@dataclass
class ReviewReport:
    trades: list[ReviewedTrade]
    lessons: list[str]

    @property
    def net(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def win_rate(self) -> float:
        return sum(t.pnl > 0 for t in self.trades) / len(self.trades) * 100 if self.trades else 0.0

    def table(self) -> pd.DataFrame:
        return pd.DataFrame([{
            "Apertura": t.opened.strftime("%d/%m %H:%M:%S"), "Cripto": t.symbol, "Dirección": t.direction,
            "Entrada": round(t.entry, 6), "Salida": round(t.exit, 6), "Duración": f"{t.minutes:.0f} min",
            "PnL estimado": round(t.pnl, 2), "Errores": " · ".join(t.flags) or "✅",
        } for t in self.trades])


def _num(x) -> float:
    if isinstance(x, (int, float)):
        return float(x)
    return float(str(x).replace(",", "").strip() or 0)


class HistoryReviewer:
    def __init__(self, tiers: TierCatalog | None = None, revenge_min: float = 5.0) -> None:
        self.tiers = tiers or TierCatalog()
        self.revenge_min = revenge_min

    # ------------------------------------------------------------------ lectura
    @staticmethod
    def parse_text(text: str) -> pd.DataFrame:
        rows = []
        for m in LINE_RE.finditer(text.replace(" ", " ")):
            t, sym, typ, side, price, avg, qty, execd, quote, stop, status = m.groups()
            rows.append({"time": pd.Timestamp(t.replace("T", " ")), "symbol": sym, "type": typ, "side": side,
                         "price": _num(price), "avg": _num(avg), "qty": _num(qty), "executed": _num(execd),
                         "status": status.replace("CANCELLED", "CANCELED")})
        return pd.DataFrame(rows)

    @staticmethod
    def parse_table(df: pd.DataFrame) -> pd.DataFrame:
        """CSV/Excel de Binance en español o inglés -> columnas estándar."""
        cols = {c: str(c).strip().lower() for c in df.columns}

        def pick(*keys, avoid=()):
            for c, low in cols.items():
                if any(k in low for k in keys) and not any(a in low for a in avoid):
                    return c
            return None

        m = {
            "time": pick("time", "tiempo", "fecha", "date", avoid=("actualiz", "update")),
            "symbol": pick("symbol", "símbolo", "simbolo", "pair", "par"),
            "type": pick("type", "tipo"),
            "side": pick("side", "lado"),
            "avg": pick("average", "avg", "precio medio", "promedio"),
            "price": pick("price", "precio", avoid=("medio", "average", "avg", "parada", "stop", "trigger")),
            "executed": pick("executed", "ejecutada", "filled", avoid=("cotiz", "quote", "amount", "total")),
            "qty": pick("quantity", "cantidad", "amount", "orig", avoid=("ejecut", "executed", "cotiz", "quote")),
            "status": pick("status", "estado"),
        }
        missing = [k for k in ("time", "symbol", "side", "executed") if m[k] is None]
        if missing:
            raise ValueError(f"No encuentro las columnas: {', '.join(missing)}. Exporta el «Historial de órdenes» de futuros.")
        out = pd.DataFrame({k: df[c] for k, c in m.items() if c is not None})
        out["time"] = pd.to_datetime(out["time"])
        for k in ("avg", "price", "executed", "qty"):
            if k in out:
                out[k] = out[k].map(_num)
        out["type"] = out.get("type", "MARKET").astype(str).str.upper().str.replace(" ", "_")
        out["side"] = out["side"].astype(str).str.upper().map(lambda s: "BUY" if s.startswith(("BUY", "COMPRA")) else "SELL")
        out["status"] = out.get("status", "FILLED").astype(str).str.upper().str.replace("CANCELLED", "CANCELED")
        out["symbol"] = out["symbol"].astype(str).str.upper().str.replace("/", "").str.replace("-", "")
        return out

    def load(self, data: bytes | str, filename: str = "") -> pd.DataFrame:
        if isinstance(data, str):
            return self.parse_text(data)
        name = filename.lower()
        if name.endswith((".xlsx", ".xls")):
            return self.parse_table(pd.read_excel(io.BytesIO(data)))
        text = data.decode("utf-8-sig", errors="replace")
        parsed = self.parse_text(text)
        return parsed if len(parsed) else self.parse_table(pd.read_csv(io.StringIO(text)))

    # ---------------------------------------------------------- reconstrucción
    def reconstruct(self, orders: pd.DataFrame) -> list[ReviewedTrade]:
        if orders.empty:
            return []
        orders = orders.sort_values("time").reset_index(drop=True)
        fills = orders[(orders["executed"] > 0) & orders["status"].isin(["FILLED", "PARTIALLY_FILLED"])]
        stops = orders[orders["type"].isin(STOP_TYPES)]
        trades: list[ReviewedTrade] = []
        for sym, grp in fills.groupby("symbol", sort=False):
            pos, cash, fee, opened, open_qty, open_cost, liq, max_qty = 0.0, 0.0, 0.0, None, 0.0, 0.0, False, 0.0
            direction = None
            for _, f in grp.iterrows():
                qty = f["executed"] * (1 if f["side"] == "BUY" else -1)
                px = f["avg"] if f.get("avg", 0) else f.get("price", 0)
                if pos == 0:
                    opened, direction, cash, fee, open_qty, open_cost, liq, max_qty = (
                        f["time"], "LONG" if qty > 0 else "SHORT", 0.0, 0.0, 0.0, 0.0, False, 0.0)
                opening = (pos >= 0 and qty > 0) or (pos <= 0 and qty < 0)
                if opening:
                    open_qty += abs(qty)
                    open_cost += abs(qty) * px
                cash -= qty * px
                fee += abs(qty) * px * (MAKER if f["type"] == "LIMIT" else TAKER)
                liq |= f["type"] in ("LIQUIDATION", "ADL")
                pos = round(pos + qty, 10)
                max_qty = max(max_qty, abs(pos))
                if abs(pos) < 1e-9:
                    entry = open_cost / open_qty if open_qty else px
                    exit_qty = open_qty
                    exit_px = (cash + (open_cost if direction == "LONG" else -open_cost)) / exit_qty if exit_qty else px
                    exit_px = abs(exit_px)
                    window = stops[(stops["symbol"] == sym) & (stops["time"] >= opened) & (stops["time"] <= f["time"])]
                    trades.append(ReviewedTrade(sym, direction, opened, f["time"], open_qty, entry, exit_px,
                                                cash - fee, liq, not window.empty))
                    pos = 0.0
        trades.sort(key=lambda t: t.opened)
        self._flag(trades)
        return trades

    def _flag(self, trades: list[ReviewedTrade]) -> None:
        for i, t in enumerate(trades):
            if t.liquidated:
                move = abs(t.exit / t.entry - 1)
                lev = 1 / (move + MAINTENANCE_MARGIN) if move > 0 else float("nan")
                t.flags.append(f"💀 Liquidado (≈{lev:.0f}x: bastó un {move * 100:.1f}% en contra)")
            if not t.has_stop:
                t.flags.append("🚫 Sin SL en el historial")
            if not t.has_stop and t.minutes > 60:
                t.flags.append(f"🌙 {t.minutes / 60:.1f} h abierta sin SL")
            coin = re.sub(r"USDT?$", "", t.symbol)
            tier, listed = self.tiers.for_coin(coin)
            if tier.key == "C" or not listed:
                t.flags.append("🎰 Memecoin / alta volatilidad")
            prev = [p for p in trades[:i] if p.closed <= t.opened]
            if prev:
                last = max(prev, key=lambda p: p.closed)
                gap = (t.opened - last.closed).total_seconds()
                if last.pnl < 0 and gap <= self.revenge_min * 60 and (last.symbol == t.symbol or last.direction == t.direction):
                    t.flags.append(f"😤 Revancha ({gap:.0f} s después de perder)")

    # --------------------------------------------------------------- informe
    def review(self, data: bytes | str, filename: str = "") -> ReviewReport:
        trades = self.reconstruct(self.load(data, filename))
        n = len(trades)
        lessons: list[str] = []
        if not n:
            return ReviewReport([], ["No encontré operaciones completas. Exporta o copia el «Historial de órdenes» de futuros."])
        no_sl = sum(not t.has_stop for t in trades)
        liqs = [t for t in trades if t.liquidated]
        revenge = [t for t in trades if any(f.startswith("😤") for f in t.flags)]
        net = sum(t.pnl for t in trades)
        if liqs:
            lost = sum(t.pnl for t in liqs)
            lessons.append(f"💀 {len(liqs)} liquidación(es) = {fmt_usd(lost, True)}. Con un SL en el exchange habrías perdido "
                           "solo el SL. Regla: NUNCA entrar sin SL y apalancamiento ≤ el tope anti-squeeze.")
        if no_sl:
            lessons.append(f"🚫 {no_sl} de {n} operaciones sin orden de Stop Loss en el historial. Es el error nº 1: sin SL, "
                           "una mecha te puede liquidar mientras duermes. (Si pusiste TP/SL en la posición y no aparece, "
                           "exporta incluyendo las órdenes canceladas.)")
        if revenge:
            lessons.append(f"😤 {len(revenge)} entrada(s) de revancha justo después de perder. Activa el anti-revancha "
                           "(30 min de pausa) y no persigas lo perdido.")
        shorts = [t for t in trades if t.direction == "SHORT"]
        if len(shorts) >= 0.8 * n and n >= 3:
            lessons.append(f"📉 {len(shorts)} de {n} operaciones fueron SHORT. Operar siempre en una dirección te expone a "
                           "los squeezes: sigue la tendencia de 4 h / 1 h (Modo SWING).")
        memes = [t for t in trades if any(f.startswith("🎰") for f in t.flags)]
        if memes:
            lessons.append(f"🎰 {len(memes)} operación(es) en memecoins: más volatilidad y squeezes. Mejor BTC, ETH o SOL.")
        days = max(1, len({t.opened.date() for t in trades}))
        if n / days > 8:
            lessons.append(f"⚡ {n / days:.0f} operaciones por día: demasiadas. Las comisiones y el cansancio se comen la cuenta.")
        lessons.append(f"Resultado: {n} operaciones · win rate {sum(t.pnl > 0 for t in trades) / n * 100:.0f}% · "
                       f"neto estimado {fmt_usd(net, True)} (comisiones estimadas; la liquidación cobra un extra).")
        return ReviewReport(trades, lessons)
