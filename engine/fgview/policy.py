"""
engine/fgview/policy.py
=======================
Políticas de riesgo: niveles de volatilidad (A/B/C) y riesgo por capital.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.position_simulator import BUDGET_TABLE, base_coin


@dataclass(frozen=True)
class Tier:
    """Nivel de volatilidad: rango de apalancamiento y distancias de SL/TP."""

    key: str
    lev_min: int
    lev_max: int
    sl: float  # % mínimo del SL
    tp: tuple[float, float, float]  # % de TP1, TP2, TP3
    coins: tuple[str, ...]


DEFAULT_TIERS: tuple[Tier, ...] = (
    Tier("A", 10, 20, 0.35, (0.40, 0.70, 1.00), ("BTC", "ETH")),
    Tier("B", 5, 10, 0.70, (0.80, 1.40, 2.00),
         # Grandes y líquidas: se mueven menos y aguantan más apalancamiento
         ("SOL", "BNB", "XRP", "LINK", "AVAX", "SUI", "LTC", "ADA", "TON", "HYPE", "NEAR", "APT", "DOT", "TRX", "BCH",
          "ZEC", "DASH", "ETC", "XLM", "HBAR", "ATOM", "FIL", "ICP", "AAVE", "UNI", "ALGO", "VET", "EGLD",
          "THETA", "MKR", "NEO", "EOS", "STX", "IMX", "RUNE", "KAS", "TAO", "OP", "ARB")),
    Tier("C", 3, 5, 1.20, (1.50, 2.50, 3.50),
         # Memecoins, IA y small caps: mucho movimiento, apalancamiento bajo y SL ancho
         ("DOGE", "PEPE", "WIF", "BONK", "SHIB", "FLOKI", "TRUMP", "POPCAT", "FET", "RENDER", "ENA", "WLD",
          "ONDO", "JUP", "INJ", "SEI", "TIA", "CRV", "LDO", "DYDX", "GRT", "SNX", "COMP", "GALA", "SAND", "MANA",
          "AXS", "KAVA", "ORDI", "PENGU", "PNUT", "VIRTUAL", "SPX", "FARTCOIN", "MOODENG", "GOAT", "ZEREBRO",
          "AI16Z", "MOVE", "BERA", "KAITO", "ME", "IP", "S", "ZRO", "STRK", "BLUR", "APE", "CHZ", "ROSE", "ZIL")),
)


class TierCatalog:
    """Busca el nivel de cada cripto. Las que no están en la lista van al nivel más prudente."""

    DEMO_ALIAS = "BTC"  # El modo demo simula un BTC

    def __init__(self, tiers: tuple[Tier, ...] = DEFAULT_TIERS, fallback: str = "C") -> None:
        self._tiers = {t.key: t for t in tiers}
        self._fallback = self._tiers[fallback]

    def __getitem__(self, key: str) -> Tier:
        return self._tiers[key]

    @property
    def tiers(self) -> list[Tier]:
        return list(self._tiers.values())

    @property
    def all_coins(self) -> list[str]:
        return [c for t in self._tiers.values() for c in t.coins]

    def resolve(self, symbol: str) -> str:
        """Moneda base que se usa para clasificar ('DEMO/USDT' -> 'BTC')."""
        coin = base_coin(symbol)
        return self.DEMO_ALIAS if coin == "DEMO" else coin

    def for_coin(self, symbol: str) -> tuple[Tier, bool]:
        """(nivel, está_en_la_lista)."""
        coin = self.resolve(symbol)
        for tier in self._tiers.values():
            if coin in tier.coins:
                return tier, True
        return self._fallback, False

    def label(self, symbol: str) -> str:
        tier, listed = self.for_coin(symbol)
        return f"Nivel {tier.key}" + ("" if listed else " (fuera de la lista)")


class BudgetPolicy:
    """Riesgo por operación y margen máximo según el capital."""

    def __init__(self, table: tuple[tuple[float, float, float], ...] = BUDGET_TABLE) -> None:
        self._table = table

    def rule(self, capital: float) -> tuple[float, float]:
        """(riesgo %, margen máximo % del capital)."""
        for max_cap, risk, margin in self._table:
            if capital <= max_cap:
                return risk, margin
        return self._table[-1][1], self._table[-1][2]
