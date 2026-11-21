"""
engine/fgview/fundamentals.py
=============================
EARNINGS ANALYST versión cripto (Módulo 15).

En lugar de reportes trimestrales:
* Ingresos de los protocolos (fees de 24 h y 7 días) -> DefiLlama (gratis, automático).
* Desbloqueos de tokens, quemas, flujos de ETFs de BTC/ETH y entradas/salidas de
  exchanges -> archivo editable data/crypto_fundamentals.json (no hay fuente gratuita
  fiable sin API key; copia los datos de Tokenomist, Farside o CryptoQuant).
* ⚠️ cuando un desbloqueo supera el 2% del suministro circulante en los próximos 7 días.
"""

from __future__ import annotations

import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from engine.config import BASE_DIR, DATA_DIR, get_logger

log = get_logger("fgview.fundamentals")

FEES_SOURCES = {
    "ETH": ("chain", "Ethereum"), "SOL": ("chain", "Solana"), "TRX": ("chain", "Tron"), "BNB": ("chain", "BSC"),
    "AVAX": ("chain", "Avalanche"), "SUI": ("chain", "Sui"), "APT": ("chain", "Aptos"), "TON": ("chain", "TON"),
    "NEAR": ("chain", "Near"), "BTC": ("chain", "Bitcoin"), "HYPE": ("protocol", "hyperliquid"),
    "ARB": ("chain", "Arbitrum"), "OP": ("chain", "OP Mainnet"), "SEI": ("chain", "Sei"), "INJ": ("chain", "Injective"),
    "JUP": ("protocol", "jupiter"), "ENA": ("protocol", "ethena"), "ONDO": ("protocol", "ondo-finance"),
}
FILE_NAME = "crypto_fundamentals.json"


@dataclass
class FeeInfo:
    coin: str
    name: str
    fees_24h: float | None
    fees_7d: float | None
    change_1d: float | None


@dataclass
class UnlockInfo:
    coin: str
    when: date
    pct_circulating: float
    note: str

    @property
    def warning(self) -> bool:
        return self.pct_circulating > 2.0


class CryptoFundamentals:
    def __init__(self, path: Path | None = None, ttl_s: int = 1800, timeout: int = 8) -> None:
        self.path = path or DATA_DIR / FILE_NAME
        src = BASE_DIR / "data" / FILE_NAME
        if not self.path.exists() and src.exists() and src.resolve() != self.path.resolve():
            shutil.copy(src, self.path)
        self.ttl, self.timeout = ttl_s, timeout
        self._cache: dict[str, tuple[float, object]] = {}

    # ---------------------------------------------------------------- manual
    def manual(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - archivo ausente o mal escrito
            return {}

    def unlocks(self, days: int = 7, today: date | None = None) -> list[UnlockInfo]:
        today = today or datetime.now(timezone.utc).date()
        out = []
        for u in self.manual().get("unlocks", []):
            try:
                when = date.fromisoformat(u["date"])
            except (KeyError, ValueError):
                continue
            if today <= when <= today + timedelta(days=days):
                out.append(UnlockInfo(u.get("coin", "?").upper(), when, float(u.get("pct_circulating", 0)),
                                      u.get("note", "")))
        return sorted(out, key=lambda x: x.when)

    def warnings(self, coin: str | None = None) -> list[str]:
        return [f"⚠️ Desbloqueo de {u.coin} el {u.when:%d/%m}: {u.pct_circulating:.1f}% del suministro circulante "
                "(> 2%): presión de venta posible." for u in self.unlocks() if u.warning and (coin is None or u.coin == coin)]

    # ------------------------------------------------------------- DefiLlama
    def _get_json(self, url: str):
        hit = self._cache.get(url)
        if hit and time.time() - hit[0] < self.ttl:
            return hit[1]
        resp = requests.get(url, timeout=self.timeout, headers={"User-Agent": "FGView/4"})
        resp.raise_for_status()
        data = resp.json()
        self._cache[url] = (time.time(), data)
        return data

    def fees(self, coin: str) -> FeeInfo | None:
        src = FEES_SOURCES.get(coin.upper())
        if not src:
            return None
        kind, name = src
        url = (f"https://api.llama.fi/overview/fees/{name}?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true"
               if kind == "chain" else f"https://api.llama.fi/summary/fees/{name}?dataType=dailyFees")
        try:
            d = self._get_json(url)
            return FeeInfo(coin.upper(), name, d.get("total24h"), d.get("total7d"), d.get("change_1d"))
        except Exception as exc:  # noqa: BLE001 - sin internet o la fuente cambió
            log.info("Fees de %s no disponibles: %s", coin, exc)
            return None

    def fees_many(self, coins: list[str]) -> list[FeeInfo]:
        with ThreadPoolExecutor(max_workers=6) as pool:
            return [f for f in pool.map(self.fees, coins) if f]
