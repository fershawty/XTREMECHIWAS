"""
strategies/
===========
Registro de estrategias. Para añadir una nueva:
    1. Crea strategies/mi_estrategia.py con una clase que herede de BaseStrategy.
    2. Impórtala aquí y añádela a STRATEGIES.
    3. Aparecerá automáticamente en la UI, la API y el backtester.
"""

from __future__ import annotations

import pandas as pd

from strategies.base import BaseStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.scalping_futures import ScalpingFuturesStrategy
from strategies.trend_following import TrendFollowingStrategy

STRATEGIES: dict[str, BaseStrategy] = {
    s.key: s for s in (TrendFollowingStrategy(), ScalpingFuturesStrategy(), MeanReversionStrategy())
}

AUTO_KEY = "auto"


def get_strategy(key: str) -> BaseStrategy:
    """Devuelve la estrategia por clave (por defecto Trend Following)."""
    return STRATEGIES.get(key, STRATEGIES["trend_following"])


def auto_select(df: pd.DataFrame, timeframe: str) -> tuple[BaseStrategy, str]:
    """
    Elige la estrategia más adecuada al contexto actual:
    * 1m / 5m                -> Scalping Futuros
    * ADX >= 22 (tendencia)  -> Trend Following
    * ADX <  22 (rango)      -> Mean Reversion

    Returns:
        (estrategia, explicación)
    """
    if timeframe in ("1m", "5m"):
        return STRATEGIES["scalping_futures"], f"Temporalidad {timeframe}: scalping"
    adx = float(df["adx"].iloc[-1]) if "adx" in df.columns else float("nan")
    if adx != adx or adx >= 22:
        return STRATEGIES["trend_following"], f"ADX {adx:.0f} >= 22: mercado en tendencia"
    return STRATEGIES["mean_reversion"], f"ADX {adx:.0f} < 22: mercado lateral"


__all__ = ["BaseStrategy", "STRATEGIES", "AUTO_KEY", "get_strategy", "auto_select"]
