"""
engine/fgview/config.py
=======================
Parámetros del motor FG VIEW en un único objeto inmutable que se inyecta en cada
clase. Para cambiar el comportamiento, crea tu propia instancia:

    cfg = FGConfig(min_filters=5, atr_sl_mult=1.5)
    services = FGServices.create(feeder, config=cfg)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FGConfig:
    fee: float = 0.0005  # Comisión taker por lado (0.05 %)
    maker_fee: float = 0.0002  # Comisión maker por lado (orden límite)
    bar_minutes: int = 5  # Velas de la señal
    max_hold_min: int = 30  # Tiempo máximo de la operación
    tp_split: tuple[float, float, float] = (50.0, 30.0, 20.0)  # % de cierre en TP1 / TP2 / TP3
    atr_sl_mult: float = 1.2  # SL dinámico = 1.2 × ATR(14) de 5 min
    atr_trail_mult: float = 0.5  # Trailing del TP3
    liq_safety: float = 3.0  # La liquidación debe quedar >= 3× más lejos que el SL
    min_filters: int = 4  # Filtros mínimos para recomendar entrar
    min_rr_net: float = 1.0  # R:R neto mínimo (después de comisiones)
    min_similar: int = 30  # Casos parecidos mínimos para usar la estadística condicionada
    daily_max_losses: int = 3  # Stop diario: pérdidas seguidas
    daily_max_loss_pct: float = 3.0  # Stop diario: % de pérdida del capital
    scan_workers: int = 4  # Descargas en paralelo del Modo fácil

    @property
    def horizon_bars(self) -> int:
        """Velas que dura como máximo una operación (30 min / 5 min = 6)."""
        return max(1, self.max_hold_min // self.bar_minutes)


DEFAULT_CONFIG = FGConfig()
