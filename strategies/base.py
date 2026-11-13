"""
strategies/base.py
==================
Clase base de todas las estrategias.

Una estrategia en FUTURES-SCOPE NO calcula indicadores (eso lo hace engine/), solo
define DOS cosas, lo que la hace muy fácil de modificar:

1. `weights`: cuánto pesa cada factor de confluencia en el score 0-100.
   Las claves válidas son las de engine/signal_engine.py -> FACTORS:
       structure, order_block, fvg, support_resistance,      (Estructura SMC)
       ema_trend, ema_fast, adx, supertrend,                 (Tendencia)
       rsi, macd, stoch_rsi,                                 (Momentum)
       bollinger, vwap, volume, volume_profile, open_interest, (Volatilidad/Volumen)
       divergence, candles                                   (Patrones)
   No hace falta que sumen 100: el motor los normaliza automáticamente y
   redistribuye el peso de los factores no disponibles (p. ej. Open Interest en acciones).

2. `gate()`: filtro obligatorio. Si no se cumple, el score se limita a 69
   (NO OPERAR) aunque la confluencia sea alta. Ej.: "solo longs sobre la EMA 200".
"""

from __future__ import annotations

from abc import ABC

import pandas as pd


class BaseStrategy(ABC):
    """Contrato común de las estrategias."""

    key: str = "base"  # Identificador interno (sin espacios)
    name: str = "Base"  # Nombre visible en la UI
    description: str = ""  # Explicación corta para el usuario
    timeframes: tuple[str, ...] = ()  # Temporalidades recomendadas
    weights: dict[str, float] = {}  # Pesos de confluencia
    sl_atr_mult: float | None = None  # None = usa ATR_SL_MULTIPLIER del .env

    def gate(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        """
        Filtro obligatorio vela a vela.

        Returns:
            (long_ok, short_ok): Series booleanas alineadas con df.
        """
        ok = pd.Series(True, index=df.index)
        return ok, ok

    def gate_reason(self, row: pd.Series, direction: int) -> str:
        """Texto que explica por qué NO se cumple el filtro en la última vela."""
        return ""

    def is_recommended_for(self, timeframe: str) -> bool:
        """¿La temporalidad elegida es de las recomendadas para esta estrategia?"""
        return not self.timeframes or timeframe in self.timeframes

    def __repr__(self) -> str:
        return f"<Strategy {self.key}>"


def _num(row: pd.Series, col: str, default: float = float("nan")) -> float:
    """Lee un valor numérico de una fila tolerando columnas ausentes."""
    try:
        value = row.get(col, default)
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default
