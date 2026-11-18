"""
engine/fgview/snapshot.py
=========================
MarketSnapshot: velas de 5 min (señal) + 1 h (tendencia) con indicadores, funding y
libro de órdenes de una cripto.

SnapshotLoader: descarga con caché compartida (FG VIEW, Modo fácil y validación usan
los mismos datos sin repetir descargas) y en paralelo para revisar varias criptos.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import pandas as pd

from engine.data_feeder import DataFeeder
from engine.indicators import compute_all


@dataclass
class MarketSnapshot:
    symbol: str
    df5: pd.DataFrame
    df1h: pd.DataFrame | None
    funding: float | None
    order_book: dict | None
    source: str
    warnings: list[str] = field(default_factory=list)

    @property
    def price(self) -> float:
        return float(self.df5["close"].iloc[-1])

    @property
    def atr5(self) -> float | None:
        v = self.df5["atr"].iloc[-1] if "atr" in self.df5.columns else float("nan")
        return float(v) if pd.notna(v) and v > 0 else None


class SnapshotLoader:
    """Carga snapshots con caché (TTL) y en paralelo."""

    def __init__(self, feeder: DataFeeder, ttl: float = 30.0, workers: int = 4) -> None:
        self.feeder = feeder
        self.ttl = ttl
        self._cache: dict[tuple, tuple[float, MarketSnapshot]] = {}
        self._lock = threading.Lock()
        # Pool persistente: sus hilos reutilizan su conexión al exchange entre búsquedas
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="fgview")

    @staticmethod
    def symbol_for(coin_or_symbol: str) -> str:
        s = coin_or_symbol.upper().strip()
        return s if "/" in s or "-" in s or "=" in s else f"{s}/USDT"

    def load(self, coin_or_symbol: str, market: str, contract: str = "USDT-M", exchange: str | None = None,
             bars5: int = 1000, bars1h: int = 200, force: bool = False) -> MarketSnapshot:
        symbol = self.symbol_for(coin_or_symbol)
        key = (symbol, market, contract, exchange, bars5, bars1h)
        if not force:
            with self._lock:
                hit = self._cache.get(key)
            if hit and time.time() - hit[0] < self.ttl:
                return hit[1]
        d5 = self.feeder.get_market_data(symbol, market, "5m", contract, exchange, bars5, force)
        df5 = compute_all(d5.df, intraday=True, open_interest=d5.open_interest)
        df1h, warnings = None, list(d5.warnings)
        try:
            d1 = self.feeder.get_market_data(symbol, market, "1h", contract, exchange, bars1h, force)
            df1h = compute_all(d1.df, intraday=True)
        except Exception as exc:  # noqa: BLE001 - el filtro de 1 h queda sin datos
            warnings.append(f"Sin datos de 1h: {exc}")
        snap = MarketSnapshot(symbol, df5, df1h, d5.funding_rate, d5.order_book, d5.source, warnings)
        with self._lock:
            self._cache[key] = (time.time(), snap)
        return snap

    def load_many(self, coins: list[str], market: str, contract: str = "USDT-M", exchange: str | None = None,
                  bars5: int = 1000, force: bool = False) -> dict[str, MarketSnapshot | Exception]:
        """Descarga varias criptos a la vez. Devuelve {cripto: snapshot o el error}."""
        futures = {c: self._pool.submit(self.load, c, market, contract, exchange, bars5, 200, force) for c in coins}
        out: dict[str, MarketSnapshot | Exception] = {}
        for coin, fut in futures.items():
            try:
                out[coin] = fut.result()
            except Exception as exc:  # noqa: BLE001
                out[coin] = exc
        return out
