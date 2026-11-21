"""
strategies/trend_following.py
=============================
Seguimiento de tendencia (swing / intradía largo: 1h, 4h, 1D).

Idea: entrar A FAVOR de la tendencia dominante tras un retroceso a una zona de
valor (Order Block / FVG / VWAP), confirmado por estructura (BOS) y momentum.

Filtro obligatorio (gate):
* LONG  solo si el precio está SOBRE la EMA 200 y ADX >= 20
* SHORT solo si el precio está BAJO la EMA 200 y ADX >= 20
"""

from __future__ import annotations

import pandas as pd

from strategies.base import BaseStrategy, _num


class TrendFollowingStrategy(BaseStrategy):
    """Estrategia de seguimiento de tendencia con confluencia SMC."""

    key = "trend_following"
    name = "Trend Following"
    description = "A favor de la tendencia (EMA 200 + ADX) entrando en retrocesos a OB/FVG con BOS."
    timeframes = ("15m", "1h", "4h", "1D")
    min_adx = 20.0

    # Suman 100 (si cambias alguno no importa: se normaliza solo)
    weights = {
        "structure": 15,  # BOS / CHoCH reciente a favor
        "ema_trend": 15,  # Precio > EMA50 > EMA200
        "adx": 10,  # Fuerza de la tendencia
        "supertrend": 10,
        "macd": 8,
        "vwap": 7,
        "volume": 6,
        "order_block": 6,
        "fvg": 5,
        "rsi": 5,
        "stoch_rsi": 3,
        "support_resistance": 3,
        "open_interest": 3,
        "volume_profile": 2,
        "divergence": 1,
        "candles": 1,
    }

    def gate(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        ref = df["ema_200"].fillna(df["ema_50"])
        trending = df["adx"] >= self.min_adx
        return (df["close"] > ref) & trending, (df["close"] < ref) & trending

    def gate_reason(self, row: pd.Series, direction: int) -> str:
        msgs = []
        adx = _num(row, "adx")
        ref = _num(row, "ema_200")
        if ref != ref:  # NaN -> usamos EMA 50
            ref = _num(row, "ema_50")
        close = _num(row, "close")
        if not adx >= self.min_adx:
            msgs.append(f"ADX {adx:.0f} < {self.min_adx:.0f}: mercado sin tendencia")
        if direction == 1 and not close > ref:
            msgs.append("precio bajo la EMA 200: no se buscan LONGs")
        if direction == -1 and not close < ref:
            msgs.append("precio sobre la EMA 200: no se buscan SHORTs")
        return "; ".join(msgs)
