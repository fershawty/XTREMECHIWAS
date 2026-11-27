"""
strategies/scalping_futures.py
==============================
Scalping de futuros (1m, 5m, 15m).

Idea: operar impulsos cortos alineados con el VWAP de la sesión y las EMAs
rápidas (9/21), con volumen por encima de lo normal y gatillo de momentum
(Stoch RSI / MACD) + FVG u Order Block cercanos.

Filtro obligatorio (gate):
* Volumen relativo >= 0.8 (el mercado debe estar "vivo")
* Volatilidad mínima: ATR >= 0.03 % del precio
* LONG solo sobre el VWAP | SHORT solo bajo el VWAP
"""

from __future__ import annotations

import pandas as pd

from strategies.base import BaseStrategy, _num


class ScalpingFuturesStrategy(BaseStrategy):
    """Scalping intradía guiado por VWAP + EMAs rápidas + volumen."""

    key = "scalping_futures"
    name = "Scalping Futuros"
    description = "Impulsos cortos alineados con VWAP y EMA 9/21, con volumen alto y gatillo de momentum."
    timeframes = ("1m", "5m", "15m")
    min_rvol = 0.8
    min_atr_pct = 0.03

    weights = {
        "vwap": 14,
        "ema_fast": 12,  # EMA 9 > EMA 21
        "volume": 12,
        "stoch_rsi": 10,
        "macd": 8,
        "fvg": 8,
        "structure": 8,
        "rsi": 6,
        "order_block": 6,
        "candles": 6,
        "supertrend": 5,
        "open_interest": 3,
        "support_resistance": 2,
    }

    def gate(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        # Si la fuente no da volumen (rvol NaN) no bloqueamos por volumen
        active = (df["rvol"] >= self.min_rvol) | df["rvol"].isna()
        volatile = df["atr_pct"] >= self.min_atr_pct
        base = active & volatile
        return base & (df["close"] > df["vwap"]), base & (df["close"] < df["vwap"])

    def gate_reason(self, row: pd.Series, direction: int) -> str:
        msgs = []
        rvol, atr_pct = _num(row, "rvol"), _num(row, "atr_pct")
        close, vwap = _num(row, "close"), _num(row, "vwap")
        if rvol == rvol and rvol < self.min_rvol:
            msgs.append(f"volumen relativo {rvol:.2f}x < {self.min_rvol}x")
        if not atr_pct >= self.min_atr_pct:
            msgs.append(f"volatilidad muy baja (ATR {atr_pct:.3f}%)")
        if direction == 1 and not close > vwap:
            msgs.append("precio bajo el VWAP: no se buscan LONGs")
        if direction == -1 and not close < vwap:
            msgs.append("precio sobre el VWAP: no se buscan SHORTs")
        return "; ".join(msgs)
