"""
engine/fgview/flowfeed.py
=========================
FlowFeed: el «flujo de dinero» de un perpetuo de Binance USDT-M, gratis y sin API key.

* Ballenas: operaciones agresivas grandes recientes (aggTrades) → compras vs ventas de ballenas.
* Taker buy/sell: quién está pegando a mercado en los últimos 30 min (takerlongshortRatio).
* Open interest: si entra o sale dinero en la última hora (openInterestHist).
* Funding: tasa actual y minutos para el próximo cobro (premiumIndex).

Sin internet o en DEMO devuelve un FlowSnapshot con lo que haya (los agentes dicen «sin datos»
en vez de inventar). Lo usan los agentes WHALE, FLOW y FUNDING de FG DESK.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import requests

from engine.config import get_logger

log = get_logger("fgview.flowfeed")

FAPI = "https://fapi.binance.com"
# Tamaño mínimo (USDT) para que una operación cuente como «ballena»
WHALE_USD = {"BTC": 250_000.0, "ETH": 150_000.0}
WHALE_USD_DEFAULT = 40_000.0


@dataclass
class FlowSnapshot:
    coin: str
    whale_buy_usd: float | None = None
    whale_sell_usd: float | None = None
    whale_n: int = 0
    taker_ratio: float | None = None  # > 1 = más compras agresivas que ventas (media 30 min)
    oi_change_pct: float | None = None  # Cambio del open interest en 1 h (%)
    funding: float | None = None  # Tasa por periodo (0.0001 = 0.01%)
    funding_min: float | None = None  # Minutos para el próximo cobro

    @property
    def whale_net(self) -> float | None:
        """−1 (solo ventas de ballenas) … +1 (solo compras)."""
        if self.whale_buy_usd is None or (self.whale_buy_usd + self.whale_sell_usd) <= 0:
            return None
        return (self.whale_buy_usd - self.whale_sell_usd) / (self.whale_buy_usd + self.whale_sell_usd)

    @property
    def has_data(self) -> bool:
        return any(v is not None for v in (self.whale_buy_usd, self.taker_ratio, self.oi_change_pct, self.funding))


class FlowFeed:
    def __init__(self, ttl_s: float = 45.0, timeout: int = 6) -> None:
        self.ttl, self.timeout = ttl_s, timeout
        self._cache: dict[str, tuple[float, FlowSnapshot]] = {}
        self._lock = threading.Lock()

    def _get(self, path: str, **params):
        resp = requests.get(f"{FAPI}{path}", params=params, timeout=self.timeout, headers={"User-Agent": "FGView/4"})
        resp.raise_for_status()
        return resp.json()

    def get(self, coin: str) -> FlowSnapshot:
        coin = coin.upper()
        if coin in ("DEMO", ""):
            return FlowSnapshot(coin)
        with self._lock:
            hit = self._cache.get(coin)
        if hit and time.time() - hit[0] < self.ttl:
            return hit[1]
        sym, out = f"{coin}USDT", FlowSnapshot(coin)
        try:
            trades = self._get("/fapi/v1/aggTrades", symbol=sym, limit=1000) or []
            lim = WHALE_USD.get(coin, WHALE_USD_DEFAULT)
            buy = sell = 0.0
            for t in trades:
                usd = float(t["p"]) * float(t["q"])
                if usd >= lim:
                    out.whale_n += 1
                    if t.get("m"):  # El comprador era el «maker» → la agresión fue una VENTA
                        sell += usd
                    else:
                        buy += usd
            out.whale_buy_usd, out.whale_sell_usd = buy, sell
        except Exception as exc:  # noqa: BLE001 - sin internet o símbolo sin datos
            log.info("aggTrades de %s no disponible: %s", coin, exc)
        try:
            rows = self._get("/futures/data/takerlongshortRatio", symbol=sym, period="5m", limit=6) or []
            if rows:
                out.taker_ratio = sum(float(r["buySellRatio"]) for r in rows) / len(rows)
        except Exception as exc:  # noqa: BLE001
            log.info("Taker ratio de %s no disponible: %s", coin, exc)
        try:
            rows = self._get("/futures/data/openInterestHist", symbol=sym, period="5m", limit=13) or []
            if len(rows) > 1:
                a, b = float(rows[0]["sumOpenInterestValue"]), float(rows[-1]["sumOpenInterestValue"])
                out.oi_change_pct = (b / a - 1) * 100 if a > 0 else None
        except Exception as exc:  # noqa: BLE001
            log.info("Open interest de %s no disponible: %s", coin, exc)
        try:
            p = self._get("/fapi/v1/premiumIndex", symbol=sym) or {}
            out.funding = float(p["lastFundingRate"])
            out.funding_min = max(0.0, (float(p["nextFundingTime"]) / 1000 - time.time()) / 60)
        except Exception as exc:  # noqa: BLE001
            log.info("Funding de %s no disponible: %s", coin, exc)
        with self._lock:
            self._cache[coin] = (time.time(), out)
        return out
