"""
engine/fgview/scanner.py
========================
CHART SCANNER (Módulo 8): escanea las criptos de los niveles A, B y C en 5m, 15m y 1h.

Detecta en la última vela cerrada:
* cruce de EMA 9/21
* ruptura del máximo o mínimo de la última hora
* rebote en el VWAP
* divergencia de RSI
* pico de volumen (> 2× el promedio de 20 velas)
* compresión de Bollinger (squeeze: ancho en el 10% más bajo de las últimas 120 velas)

Puntuación 0–100 = 70% confianza de los 6 filtros FG VIEW (Módulo 4) en la dirección
detectada + 30% fuerza de las detecciones (cuántas y en cuántas temporalidades).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from engine.data_feeder import DataFeeder
from engine.fgview.filters import FilterSet
from engine.fgview.policy import TierCatalog
from engine.fgview.snapshot import SnapshotLoader
from engine.indicators import compute_all

SCAN_TIMEFRAMES = ("5m", "15m", "1h")
TF_MIN = {"5m": 5, "15m": 15, "1h": 60}


@dataclass
class Detection:
    timeframe: str
    name: str
    direction: str  # LONG | SHORT | AMBOS
    plain: str


@dataclass
class ScanHit:
    coin: str
    tier: str
    direction: str
    score: int
    filters_score: int
    detections: list[Detection] = field(default_factory=list)
    price: float = float("nan")
    error: str | None = None

    @property
    def summary(self) -> str:
        return " · ".join(f"{d.name} ({d.timeframe})" for d in self.detections) or "sin detecciones"


class ChartScanner:
    def __init__(self, feeder: DataFeeder, loader: SnapshotLoader, filters: FilterSet, tiers: TierCatalog,
                 workers: int = 6) -> None:
        self.feeder, self.loader, self.filters, self.tiers = feeder, loader, filters, tiers
        self.workers = workers

    # ------------------------------------------------------------ detección
    @staticmethod
    def detect(df: pd.DataFrame, timeframe: str) -> list[Detection]:
        """Detecciones en la última vela cerrada de un DataFrame con indicadores."""
        out: list[Detection] = []
        if len(df) < 130:
            return out
        last, prev = df.iloc[-1], df.iloc[-2]
        e9, e21, pe9, pe21 = last.get("ema_9"), last.get("ema_21"), prev.get("ema_9"), prev.get("ema_21")
        if pd.notna(e9) and pd.notna(pe9):
            if pe9 <= pe21 and e9 > e21:
                out.append(Detection(timeframe, "Cruce EMA 9/21 ↑", "LONG", "La media rápida cruzó hacia arriba."))
            elif pe9 >= pe21 and e9 < e21:
                out.append(Detection(timeframe, "Cruce EMA 9/21 ↓", "SHORT", "La media rápida cruzó hacia abajo."))
        bars_hour = max(1, 60 // TF_MIN[timeframe])
        window = df.iloc[-1 - bars_hour:-1]
        if len(window):
            if last["close"] > window["high"].max():
                out.append(Detection(timeframe, "Ruptura máx. 1h", "LONG", "Rompió el máximo de la última hora."))
            elif last["close"] < window["low"].min():
                out.append(Detection(timeframe, "Ruptura mín. 1h", "SHORT", "Rompió el mínimo de la última hora."))
        vw = last.get("vwap")
        if pd.notna(vw):
            if last["low"] <= vw < last["close"] and prev["close"] > prev.get("vwap", vw):
                out.append(Detection(timeframe, "Rebote VWAP ↑", "LONG", "Tocó el precio medio del día y rebotó."))
            elif last["high"] >= vw > last["close"] and prev["close"] < prev.get("vwap", vw):
                out.append(Detection(timeframe, "Rechazo VWAP ↓", "SHORT", "Tocó el precio medio del día y bajó."))
        recent = df.iloc[-3:]
        if "bull_div_rsi" in df and bool(recent["bull_div_rsi"].fillna(False).astype(bool).any()):
            out.append(Detection(timeframe, "Divergencia RSI alcista", "LONG", "El precio baja pero la fuerza sube."))
        if "bear_div_rsi" in df and bool(recent["bear_div_rsi"].fillna(False).astype(bool).any()):
            out.append(Detection(timeframe, "Divergencia RSI bajista", "SHORT", "El precio sube pero la fuerza baja."))
        vol_avg = df["volume"].iloc[-21:-1].mean()
        if vol_avg > 0 and last["volume"] > 2 * vol_avg:
            d = "LONG" if last["close"] >= last["open"] else "SHORT"
            out.append(Detection(timeframe, f"Volumen {last['volume'] / vol_avg:.1f}×", d,
                                 "Entró mucho más dinero de lo normal."))
        if "bb_width" in df:
            bw = df["bb_width"].iloc[-120:].dropna()
            if len(bw) >= 60 and bw.iloc[-1] <= np.nanpercentile(bw, 10):
                out.append(Detection(timeframe, "Squeeze Bollinger", "AMBOS",
                                     "El precio está muy comprimido: suele venir un movimiento fuerte."))
        return out

    # --------------------------------------------------------------- escaneo
    def scan_coin(self, coin: str, market: str = "crypto", exchange: str | None = None) -> ScanHit:
        tier, _ = self.tiers.for_coin(coin)
        symbol = "DEMO/USDT" if market == "demo" else f"{coin}/USDT"
        try:
            dets: list[Detection] = []
            for tf in SCAN_TIMEFRAMES:
                md = self.feeder.get_market_data(symbol, market, tf, exchange=exchange, limit=300)
                dets += self.detect(compute_all(md.df, intraday=True), tf)
            votes = {"LONG": 0.0, "SHORT": 0.0}
            weight = {"5m": 1.0, "15m": 1.3, "1h": 1.6}
            for d in dets:
                for side in (("LONG", "SHORT") if d.direction == "AMBOS" else (d.direction,)):
                    votes[side] += weight[d.timeframe] * (0.5 if d.direction == "AMBOS" else 1.0)
            snap = self.loader.load(symbol, market, exchange=exchange, bars5=400)
            direction = max(votes, key=votes.get) if any(votes.values()) else self.filters.best(snap, symbol).direction
            rep = self.filters.evaluate(snap, direction, symbol)
            strength = min(100.0, votes[direction] / 4.0 * 100)
            score = int(round(0.7 * rep.score + 0.3 * strength))
            if rep.blocks:
                score = min(score, 40)
            return ScanHit(coin, tier.key, direction, score, rep.score,
                           [d for d in dets if d.direction in (direction, "AMBOS")], snap.price)
        except Exception as exc:  # noqa: BLE001 - una cripto con error no detiene el escaneo
            return ScanHit(coin, tier.key, "-", 0, 0, [], error=str(exc)[:160])

    def scan(self, coins: list[str] | None = None, market: str = "crypto", exchange: str | None = None,
             top: int = 5) -> tuple[list[ScanHit], list[ScanHit]]:
        """(top N ordenado por puntuación, lista completa)."""
        coins = coins or self.tiers.all_coins
        if market == "demo":
            coins = ["DEMO"]
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            hits = list(pool.map(lambda c: self.scan_coin(c, market, exchange), coins))
        ranked = sorted([h for h in hits if not h.error], key=lambda h: h.score, reverse=True)
        return ranked[:top], hits
