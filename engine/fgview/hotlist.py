"""
engine/fgview/hotlist.py
========================
🔥 HOT LIST: qué criptos están mejor para operar AHORA en Binance Futures, en lenguaje simple.

Lee el resumen público de 24 h de Binance (sin API key) de TODOS los perpetuos USDT-M y, para cada uno, mira
tres cosas: cuánto dinero se mueve (volumen), cuánto se está moviendo el precio (rango del día) y hacia dónde va
(cambio de 24 h). Con eso arma una lista ordenada por "qué tan operable está", con una frase que explica por qué.

Nada se inventa: si no hay conexión, lo dice y muestra la lista fija de siempre.
Volumen alto = puedes entrar y salir sin que el precio se te mueva en contra. Mucho movimiento sin volumen = trampa.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import requests

from engine.config import get_logger

log = get_logger("fgview.hotlist")

URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"
MIN_VOL_USD = 20_000_000.0  # Menos de esto es ilíquido para apalancamiento: no entra en la lista
STABLES = ("USDC", "FDUSD", "TUSD", "BUSD", "DAI", "EUR", "USDP", "USD1")


def _f(v, d=0.0) -> float:
    try:
        x = float(v)
        return x if x == x else d
    except (TypeError, ValueError):
        return d


@dataclass
class HotCoin:
    coin: str
    price: float
    change_24h: float  # %
    range_24h: float  # % entre el máximo y el mínimo del día
    volume_usd: float
    trades: int
    tier: str  # Nivel del catálogo (A/B/C) o el sugerido por volatilidad
    listed: bool  # ¿Está en tus listas A/B/C?
    score: float = 0.0
    why: str = ""
    flags: list[str] = field(default_factory=list)

    @property
    def movement(self) -> str:
        """Qué tan movida está, en palabras."""
        r = self.range_24h
        return "tranquila" if r < 2.5 else "normal" if r < 5 else "movida" if r < 9 else "muy movida"

    @property
    def direction(self) -> str:
        return "subiendo" if self.change_24h > 1 else "bajando" if self.change_24h < -1 else "de lado"

    @property
    def volume_txt(self) -> str:
        v = self.volume_usd
        return f"${v / 1e9:.1f}B" if v >= 1e9 else f"${v / 1e6:.0f}M"


class HotList:
    """Top de criptos operables de Binance Futures, con caché (los datos de 24 h no cambian cada segundo)."""

    def __init__(self, tiers=None, ttl_s: float = 300.0, timeout: int = 10) -> None:
        self.tiers, self.ttl, self.timeout = tiers, ttl_s, timeout
        self._cache: tuple[float, list[HotCoin], str] | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------- clasificación
    def _tier_of(self, coin: str, range_24h: float) -> tuple[str, bool]:
        """El nivel que FG VIEW usaría de verdad: el del catálogo, y C (el más prudente) si no está en tus listas."""
        if self.tiers is not None:
            tier, listed = self.tiers.for_coin(f"{coin}/USDT")
            return tier.key, listed
        return "C", False

    @staticmethod
    def _score(c: HotCoin) -> tuple[float, str, list[str]]:
        """0-100: cuánto volumen hay, cuánto se mueve y si el movimiento es sano. Con su explicación."""
        import math

        vol_pts = min(45.0, max(0.0, (math.log10(max(c.volume_usd, 1)) - 7.0) * 22))  # 10M→0 · 100M→22 · 1B→45
        move_pts = min(35.0, c.range_24h * 5.5)  # 2%→11 · 4%→22 · 6.4%→35
        if c.range_24h > 14:
            move_pts -= (c.range_24h - 14) * 2.0  # Demasiado movimiento también castiga
        trade_pts = min(20.0, max(0.0, (math.log10(max(c.trades, 1)) - 4.0) * 9))  # Operaciones reales, no volumen falso
        flags, why = [], []
        if c.volume_usd >= 5e8:
            why.append("mucho dinero moviéndose")
        elif c.volume_usd >= 1e8:
            why.append("buen volumen")
        else:
            why.append("volumen justo")
            flags.append("Volumen bajo: entra con tamaño pequeño.")
        if c.range_24h < 1.5:
            flags.append("Casi no se mueve: puede que no llegue al objetivo.")
            why.append("poco movimiento")
        elif c.range_24h > 12:
            flags.append("Se mueve muchísimo: el stop salta fácil, baja el apalancamiento.")
            why.append("movimiento fuerte")
        else:
            why.append(f"rango sano de {c.range_24h:.1f}%")
        if c.trades and c.volume_usd / max(c.trades, 1) > 50_000:
            flags.append("Pocas operaciones para tanto volumen: revisa la profundidad.")
        why.append(c.direction)
        return max(0.0, min(100.0, vol_pts + move_pts + trade_pts)), " · ".join(why), flags

    # ------------------------------------------------------------- datos
    def fetch(self, force: bool = False) -> tuple[list[HotCoin], str]:
        """(lista, fuente). `fuente` dice de dónde salió y cuándo, o por qué no hay datos."""
        with self._lock:
            hit = self._cache
        if hit and not force and time.time() - hit[0] < self.ttl:
            age = int(time.time() - hit[0])
            return hit[1], f"{hit[2]} · hace {age}s"
        try:
            resp = requests.get(URL, timeout=self.timeout, headers={"User-Agent": "FGView/5"})
            resp.raise_for_status()
            rows = resp.json() or []
        except Exception as exc:  # noqa: BLE001 - sin internet o Binance caído
            log.info("Hot list no disponible: %s", exc)
            return [], f"sin conexión con Binance ({str(exc)[:60]})"
        out = []
        for r in rows:
            sym = str(r.get("symbol", ""))
            if not sym.endswith("USDT"):
                continue
            coin = sym[:-4]
            if not coin or coin in STABLES or coin.endswith("DOWN") or coin.endswith("UP"):
                continue
            vol = _f(r.get("quoteVolume"))
            if vol < MIN_VOL_USD:
                continue
            price, hi, lo = _f(r.get("lastPrice")), _f(r.get("highPrice")), _f(r.get("lowPrice"))
            if price <= 0 or lo <= 0:
                continue
            c = HotCoin(coin=coin, price=price, change_24h=_f(r.get("priceChangePercent")),
                        range_24h=(hi - lo) / lo * 100.0, volume_usd=vol, trades=int(_f(r.get("count"))),
                        tier="C", listed=False)
            c.tier, c.listed = self._tier_of(coin, c.range_24h)
            c.score, c.why, c.flags = self._score(c)
            out.append(c)
        out.sort(key=lambda c: c.score, reverse=True)
        src = f"Binance Futures · {len(out)} perpetuos con volumen"
        with self._lock:
            self._cache = (time.time(), out, src)
        return out, src

    def top(self, n: int = 20, only_listed: bool = False, tier: str = "") -> tuple[list[HotCoin], str]:
        coins, src = self.fetch()
        if only_listed:
            coins = [c for c in coins if c.listed]
        if tier:
            coins = [c for c in coins if c.tier == tier.upper()]
        return coins[: max(1, n)], src

    def as_dicts(self, n: int = 20) -> dict:
        """Para el MCP y la app: lista simple, en palabras."""
        coins, src = self.top(n)
        return {"fuente": src, "criptos": [
            {"cripto": c.coin, "precio": round(c.price, 6), "cambio_24h_pct": round(c.change_24h, 2),
             "rango_24h_pct": round(c.range_24h, 2), "volumen_usd": round(c.volume_usd), "volumen": c.volume_txt,
             "operaciones_24h": c.trades, "nivel": c.tier, "en_tus_listas": c.listed,
             "puntos_operable": round(c.score, 1), "movimiento": c.movement, "va": c.direction,
             "por_que": c.why, "avisos": c.flags} for c in coins],
        "nota": "Puntos = volumen + movimiento + operaciones reales. No dice si va a subir: dice si se puede operar."}
