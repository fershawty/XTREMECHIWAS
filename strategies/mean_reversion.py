"""
strategies/mean_reversion.py
============================
Reversión a la media (mercados laterales).

Idea: cuando NO hay tendencia (ADX bajo), el precio tiende a volver a su media.
Se busca agotamiento en los extremos: Banda de Bollinger exterior + RSI / Stoch RSI
extremos + divergencia + soporte/resistencia o borde del Value Area + vela de giro.

Filtro obligatorio (gate):
* ADX < 25 (mercado lateral; en tendencia fuerte la reversión es peligrosa)
* LONG solo con %B < 0.35 (tercio inferior de las bandas)
* SHORT solo con %B > 0.65 (tercio superior)
"""

from __future__ import annotations

import pandas as pd

from strategies.base import BaseStrategy, _num


class MeanReversionStrategy(BaseStrategy):
    """Reversión a la media en rangos."""

    key = "mean_reversion"
    name = "Mean Reversion"
    description = "En rangos (ADX bajo): compra en el extremo inferior y vende en el superior con divergencias."
    timeframes = ("5m", "15m", "1h", "4h")
    max_adx = 25.0
    long_pctb = 0.35
    short_pctb = 0.65

    weights = {
        "bollinger": 22,
        "rsi": 16,
        "divergence": 12,
        "support_resistance": 12,
        "stoch_rsi": 10,
        "volume_profile": 10,
        "candles": 8,
        "order_block": 5,
        "fvg": 3,
        "volume": 2,
    }

    def gate(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        ranging = df["adx"] < self.max_adx
        return ranging & (df["bb_pctb"] < self.long_pctb), ranging & (df["bb_pctb"] > self.short_pctb)

    def gate_reason(self, row: pd.Series, direction: int) -> str:
        msgs = []
        adx, pctb = _num(row, "adx"), _num(row, "bb_pctb")
        if not adx < self.max_adx:
            msgs.append(f"ADX {adx:.0f} >= {self.max_adx:.0f}: hay tendencia, no se opera reversión")
        if direction == 1 and not pctb < self.long_pctb:
            msgs.append(f"%B {pctb:.2f}: el precio no está en el extremo inferior")
        if direction == -1 and not pctb > self.short_pctb:
            msgs.append(f"%B {pctb:.2f}: el precio no está en el extremo superior")
        return "; ".join(msgs)
