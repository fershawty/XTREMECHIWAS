"""
engine/fgview/risk.py
=====================
RISK MANAGER (Módulo 11) y PositionBook (posiciones abiertas, papel o reales).

Reglas antes de abrir cada operación:
* Stop diario del Módulo 3: 3% de pérdida o 3 pérdidas seguidas hoy (DailyGuard).
* Máximo 2 posiciones abiertas a la vez.
* Nunca 2 posiciones muy correlacionadas en la MISMA dirección (ej. BTC y ETH LONG).
  Correlación de retornos de 5 min > 0.8, o pareja conocida (BTC/ETH).
* La liquidación debe quedar ≥ 3× más lejos que el SL (BURN OUT usa su propia regla: ≥ 2×).
* 🧊 ANTI-REVANCHA: después de una pérdida, 30 min sin volver a entrar en la misma cripto
  ni en la misma dirección (las operaciones de "recuperar lo perdido" son las que hunden cuentas).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from engine.database import Database
from engine.fgview.config import DEFAULT_CONFIG, FGConfig
from engine.fgview.journal import DailyGuard

KNOWN_CORRELATED = ({"BTC", "ETH"},)


class PositionBook:
    """Posiciones abiertas guardadas en SQLite (sobreviven a reinicios de la app)."""

    KEY = "open_positions"

    def __init__(self, db: Database | None) -> None:
        self.db = db
        self._mem: list[dict] = []

    def all(self) -> list[dict]:
        return list(self.db.get_state(self.KEY, []) or []) if self.db is not None else list(self._mem)

    def _save(self, rows: list[dict]) -> None:
        if self.db is not None:
            self.db.set_state(self.KEY, rows)
        else:
            self._mem = rows

    def add(self, pos: dict) -> dict:
        pos = {"id": uuid.uuid4().hex[:10], "opened_at": datetime.now(timezone.utc).isoformat(), **pos}
        self._save(self.all() + [pos])
        return pos

    def remove(self, pos_id: str) -> dict | None:
        rows = self.all()
        hit = next((p for p in rows if p["id"] == pos_id), None)
        self._save([p for p in rows if p["id"] != pos_id])
        return hit

    def update(self, pos_id: str, **changes) -> None:
        self._save([{**p, **changes} if p["id"] == pos_id else p for p in self.all()])


@dataclass
class RiskCheck:
    allowed: bool
    blocks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class RiskManager:
    def __init__(self, book: PositionBook, guard: DailyGuard, config: FGConfig = DEFAULT_CONFIG,
                 max_open: int = 2, corr_limit: float = 0.8, cooldown_min: int = 30) -> None:
        self.book, self.guard, self.cfg = book, guard, config
        self.max_open, self.corr_limit, self.cooldown_min = max_open, corr_limit, cooldown_min

    def revenge_block(self, coin: str, direction: str, trades: list[dict], now: datetime | None = None) -> str | None:
        """Bloqueo anti-revancha si la última pérdida cerró hace menos de `cooldown_min` minutos."""
        now = now or datetime.now(timezone.utc)
        for t in reversed(trades):
            if (t.get("pnl") or 0) >= 0:
                continue
            try:
                opened = datetime.fromisoformat(str(t["opened_at"]).replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            closed = opened + timedelta(minutes=float(t.get("duration_min") or 0))
            ago = (now - closed).total_seconds() / 60
            if 0 <= ago < self.cooldown_min and (t.get("coin") == coin or t.get("direction") == direction):
                return (f"🧊 Anti-revancha: perdiste en {t.get('coin')} {t.get('direction')} hace {ago:.0f} min. "
                        f"Espera {self.cooldown_min - ago:.0f} min antes de volver a entrar.")
            break  # Solo cuenta la pérdida más reciente
        return None

    @staticmethod
    def correlation(a: pd.DataFrame | None, b: pd.DataFrame | None, bars: int = 288) -> float | None:
        """Correlación de retornos de 5 min (último día por defecto)."""
        if a is None or b is None:
            return None
        ra = a["close"].pct_change().iloc[-bars:]
        rb = b["close"].pct_change().iloc[-bars:]
        joined = pd.concat([ra, rb], axis=1, join="inner").dropna()
        if len(joined) < 30:
            return None
        return float(np.corrcoef(joined.iloc[:, 0], joined.iloc[:, 1])[0, 1])

    def liquidation_ok(self, entry: float, stop: float, liquidation: float, safety: float | None = None) -> bool:
        safety = self.cfg.liq_safety if safety is None else safety
        return abs(entry - liquidation) >= safety * abs(entry - stop) - 1e-12

    def check(self, coin: str, direction: str, entry: float, stop: float, liquidation: float, capital: float,
              trades_today: list[dict], price_data: dict[str, pd.DataFrame] | None = None,
              liq_safety: float | None = None) -> RiskCheck:
        blocks, warnings = [], []
        msg = self.guard.check(trades_today, capital)
        if msg:
            blocks.append(msg)
        if (rv := self.revenge_block(coin, direction, trades_today)):
            blocks.append(rv)
        open_pos = self.book.all()
        if len(open_pos) >= self.max_open:
            blocks.append(f"Ya tienes {len(open_pos)} posiciones abiertas (máximo {self.max_open}).")
        for p in open_pos:
            if p.get("direction") != direction:
                continue
            other = p.get("coin", "")
            if other == coin:
                blocks.append(f"Ya tienes {coin} {direction} abierta.")
                continue
            pair_known = any({coin, other} <= grp for grp in KNOWN_CORRELATED)
            corr = self.correlation((price_data or {}).get(coin), (price_data or {}).get(other))
            if pair_known or (corr is not None and corr > self.corr_limit):
                detail = f"correlación {corr:.2f}" if corr is not None else "se mueven casi igual"
                blocks.append(f"{coin} y {other} {direction} a la vez: están muy correlacionadas ({detail}). "
                              "Sería duplicar el mismo riesgo.")
        safety = self.cfg.liq_safety if liq_safety is None else liq_safety
        if not self.liquidation_ok(entry, stop, liquidation, safety):
            ratio = abs(entry - liquidation) / abs(entry - stop) if entry != stop else 0
            blocks.append(f"La liquidación está a solo {ratio:.1f}× la distancia del SL (mínimo {safety:g}×). "
                          "Baja el apalancamiento.")
        return RiskCheck(not blocks, blocks, warnings)
