"""
engine/fgview/positioning.py
============================
PositioningFeed: cuántos traders están en LARGO y cuántos en CORTO (Binance, gratis).

* Cuentas (global): % de cuentas con posición larga / corta.
* Top traders (posiciones): reparto del dinero de las cuentas más grandes.

Si muchísimas cuentas están en corto, un rebote las obliga a cerrar a la vez: es el
combustible de un SHORT SQUEEZE (y al revés para los largos). Lo usa el SqueezeGuard.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import requests

from engine.config import get_logger

log = get_logger("fgview.positioning")

BASE = "https://fapi.binance.com/futures/data"


@dataclass
class Positioning:
    coin: str
    long_pct: float  # % de cuentas en largo
    short_pct: float  # % de cuentas en corto
    top_long_pct: float | None = None  # % del dinero de los top traders en largo
    change_short_pts: float | None = None  # Cambio del % de cortos en la última hora (puntos)

    @property
    def text(self) -> str:
        top = f" · top traders {self.top_long_pct:.0f}% largos" if self.top_long_pct is not None else ""
        chg = (f" ({self.change_short_pts:+.1f} pts en 1 h)" if self.change_short_pts is not None else "")
        return f"Cuentas: {self.long_pct:.0f}% largos / {self.short_pct:.0f}% cortos{chg}{top}"


class PositioningFeed:
    def __init__(self, ttl_s: float = 120.0, timeout: int = 6) -> None:
        self.ttl, self.timeout = ttl_s, timeout
        self._cache: dict[str, tuple[float, Positioning | None]] = {}
        self._lock = threading.Lock()

    def _get(self, path: str, symbol: str) -> list[dict]:
        resp = requests.get(f"{BASE}/{path}", params={"symbol": symbol, "period": "5m", "limit": 13},
                            timeout=self.timeout, headers={"User-Agent": "FGView/4"})
        resp.raise_for_status()
        return resp.json() or []

    def get(self, coin: str) -> Positioning | None:
        """Último reparto de largos/cortos del perpetuo COIN/USDT (None si no hay datos)."""
        coin = coin.upper()
        if coin in ("DEMO", ""):
            return None
        with self._lock:
            hit = self._cache.get(coin)
        if hit and time.time() - hit[0] < self.ttl:
            return hit[1]
        out = None
        try:
            rows = self._get("globalLongShortAccountRatio", f"{coin}USDT")
            if rows:
                last = rows[-1]
                long_pct, short_pct = float(last["longAccount"]) * 100, float(last["shortAccount"]) * 100
                chg = short_pct - float(rows[0]["shortAccount"]) * 100 if len(rows) > 1 else None
                top = None
                try:
                    t = self._get("topLongShortPositionRatio", f"{coin}USDT")
                    top = float(t[-1]["longAccount"]) * 100 if t else None
                except Exception:  # noqa: BLE001 - dato extra opcional
                    pass
                out = Positioning(coin, long_pct, short_pct, top, chg)
        except Exception as exc:  # noqa: BLE001 - sin internet o símbolo sin datos
            log.info("Long/short ratio de %s no disponible: %s", coin, exc)
        with self._lock:
            self._cache[coin] = (time.time(), out)
        return out
