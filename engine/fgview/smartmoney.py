"""
engine/fgview/smartmoney.py
===========================
PELOSI TRACKER versión cripto: SMART MONEY TRACKER (Módulo 16).

* Congresistas de EE. UU. (STOCK Act): compras y ventas en acciones ligadas a cripto
  (COIN, MSTR, mineras y ETFs). Fuente: Quiver Quant si pones QUIVER_API_KEY en .env;
  si no, los datos públicos de House/Senate Stock Watcher (pueden ir con retraso).
* Ballenas on-chain: movimientos grandes hacia o desde exchanges. Fuente: Whale Alert
  (WHALE_ALERT_API_KEY en .env, tiene plan gratuito).

Se muestra SOLO como contexto. Nunca es una señal de entrada por sí sola.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

from engine.config import get_logger

log = get_logger("fgview.smartmoney")

CRYPTO_TICKERS = ("COIN", "MSTR", "HOOD", "MARA", "RIOT", "CLSK", "HUT", "CIFR", "WULF", "BITF", "CORZ", "IREN",
                  "IBIT", "FBTC", "GBTC", "ARKB", "BITB", "ETHA", "FETH", "BITO", "SQ", "XYZ", "GLXY", "CRCL")
HOUSE_URL = "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json"
SENATE_URL = "https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json"


@dataclass
class CongressTrade:
    when: str
    person: str
    chamber: str
    ticker: str
    kind: str  # compra | venta
    amount: str


@dataclass
class WhaleMove:
    when: datetime
    coin: str
    usd: float
    flow: str  # hacia exchange | desde exchange | entre billeteras
    detail: str

    @property
    def reading(self) -> str:
        return {"hacia exchange": "posible venta (presión bajista)",
                "desde exchange": "retiro a custodia (suele ser alcista)"}.get(self.flow, "neutral")


class SmartMoneyTracker:
    def __init__(self, timeout: int = 15, ttl_s: int = 6 * 3600) -> None:
        self.timeout, self.ttl = timeout, ttl_s
        self._cache: dict[str, tuple[float, object]] = {}

    def _get(self, url: str, headers: dict | None = None, ttl: int | None = None):
        hit = self._cache.get(url)
        if hit and time.time() - hit[0] < (ttl or self.ttl):
            return hit[1]
        resp = requests.get(url, timeout=self.timeout, headers={"User-Agent": "FGView/4", **(headers or {})})
        resp.raise_for_status()
        data = resp.json()
        self._cache[url] = (time.time(), data)
        return data

    # ------------------------------------------------------------ congreso
    def congress(self, days: int = 120) -> tuple[list[CongressTrade], str]:
        """(operaciones recientes en acciones cripto, nota sobre la fuente)."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        key = os.getenv("QUIVER_API_KEY", "").strip()
        rows: list[CongressTrade] = []
        if key:
            try:
                data = self._get("https://api.quiverquant.com/beta/live/congresstrading",
                                 {"Authorization": f"Bearer {key}", "Accept": "application/json"})
                for r in data:
                    t = str(r.get("Ticker", "")).upper()
                    when = str(r.get("TransactionDate") or r.get("ReportDate") or "")[:10]
                    if t in CRYPTO_TICKERS and when >= since:
                        kind = "compra" if "purchase" in str(r.get("Transaction", "")).lower() else "venta"
                        rows.append(CongressTrade(when, r.get("Representative", "?"), r.get("House", "?"), t, kind,
                                                  str(r.get("Range") or r.get("Amount") or "")))
                return sorted(rows, key=lambda x: x.when, reverse=True), "Quiver Quant"
            except Exception as exc:  # noqa: BLE001
                log.info("Quiver no disponible: %s", exc)
        for url, chamber, who in ((HOUSE_URL, "Cámara", "representative"), (SENATE_URL, "Senado", "senator")):
            try:
                for r in self._get(url):
                    t = str(r.get("ticker", "")).upper()
                    when = str(r.get("transaction_date", ""))
                    if "/" in when:  # MM/DD/YYYY -> ISO
                        m, d, y = when.split("/")
                        when = f"{y}-{m.zfill(2)}-{d.zfill(2)}"
                    if t in CRYPTO_TICKERS and when >= since:
                        kind = "compra" if "purchase" in str(r.get("type", "")).lower() else "venta"
                        rows.append(CongressTrade(when, r.get(who, "?"), chamber, t, kind, str(r.get("amount", ""))))
            except Exception as exc:  # noqa: BLE001 - fuente pública caída
                log.info("Stock Watcher (%s) no disponible: %s", chamber, exc)
        note = "House/Senate Stock Watcher (datos públicos, pueden ir con retraso)"
        return sorted(rows, key=lambda x: x.when, reverse=True), note

    # -------------------------------------------------------------- ballenas
    def whales(self, min_usd: float = 5_000_000, hours: int = 1) -> tuple[list[WhaleMove], str]:
        key = os.getenv("WHALE_ALERT_API_KEY", "").strip()
        if not key:
            return [], "Añade WHALE_ALERT_API_KEY en .env para ver ballenas (whale-alert.io tiene plan gratuito)."
        start = int(time.time()) - hours * 3600
        try:
            data = self._get(f"https://api.whale-alert.io/v1/transactions?api_key={key}&min_value={int(min_usd)}"
                             f"&start={start}", ttl=120)
        except Exception as exc:  # noqa: BLE001
            return [], f"Whale Alert no disponible: {str(exc)[:100]}"
        out = []
        for tx in data.get("transactions", []):
            frm, to = tx.get("from", {}), tx.get("to", {})
            if to.get("owner_type") == "exchange" and frm.get("owner_type") != "exchange":
                flow = "hacia exchange"
            elif frm.get("owner_type") == "exchange" and to.get("owner_type") != "exchange":
                flow = "desde exchange"
            else:
                flow = "entre billeteras"
            out.append(WhaleMove(datetime.fromtimestamp(tx.get("timestamp", 0), timezone.utc),
                                 str(tx.get("symbol", "?")).upper(), float(tx.get("amount_usd", 0)), flow,
                                 f"{frm.get('owner') or frm.get('owner_type', '?')} → {to.get('owner') or to.get('owner_type', '?')}"))
        return sorted(out, key=lambda w: w.when, reverse=True), "Whale Alert"
