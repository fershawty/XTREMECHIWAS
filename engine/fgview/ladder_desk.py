"""
engine/fgview/ladder_desk.py
============================
🪜 MODO ESCALERA de FG DESK: empezar con muy poco capital ($10–20) y crecer en automático.

Reglas (todas en código):
* Riesgo por escalón: 4% del saldo hasta $50, 3% hasta $100, 2% hasta $250 y luego la tabla
  normal. Siempre con SL en el exchange y liquidación ≥ 3× la distancia del SL.
* Margen máximo 50% del saldo mientras el capital es pequeño (para llegar al mínimo de orden).
* Freno: tras 2 pérdidas seguidas, riesgo a la mitad hasta la siguiente ganancia.
* Caja fuerte: cada vez que el saldo se duplica respecto al último hito, el 20% de la ganancia
  se guarda y ya no se arriesga.
* Pausa: si el saldo operativo cae al 50% de lo que pusiste, la escalera se detiene sola.
* Mínimo de orden de Binance: si la posición no llega al mínimo de la cripto, se sube solo si el
  riesgo no pasa del doble del previsto; si no, la mesa busca otra cripto.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from engine.database import Database
from engine.fgview.journal import TradeJournal

# Mínimos aproximados de nocional en Binance USDT-M (el modo real comprueba el valor exacto del exchange)
MIN_NOTIONAL = {"BTC": 100.0, "ETH": 20.0}
DEFAULT_MIN_NOTIONAL = 5.0
RISK_STEPS = ((50.0, 4.0), (100.0, 3.0), (250.0, 2.0))
MICRO_LIVE_MAX = 25.0  # Con ≤ $25 se permite la prueba real sin las 50 operaciones de papel


def min_notional(coin: str) -> float:
    return MIN_NOTIONAL.get(coin.upper(), DEFAULT_MIN_NOTIONAL)


@dataclass
class LadderStatus:
    enabled: bool
    start: float
    balance: float  # Saldo operativo (sin la caja fuerte)
    vault: float
    next_target: float
    risk_pct: float | None  # None = tabla normal
    streak: int  # Pérdidas seguidas
    paused: str | None
    trades: int
    history: list[float] = field(default_factory=list)

    @property
    def progress(self) -> float:
        base = self.next_target / 2
        return max(0.0, min(1.0, (self.balance - base) / (self.next_target - base))) if self.next_target > base else 0.0


class DeskLadder:
    KEY = "desk_ladder"

    def __init__(self, db: Database | None, journal: TradeJournal | None) -> None:
        self.db, self.journal = db, journal
        self._mem: dict = {}

    def state(self) -> dict:
        data = (self.db.get_state(self.KEY, {}) if self.db is not None else self._mem) or {}
        return {"enabled": False, "start": 15.0, "since": None, "vault": 0.0, "milestone": None, **data}

    def save(self, st: dict) -> None:
        if self.db is not None:
            self.db.set_state(self.KEY, st)
        else:
            self._mem = st

    def start(self, capital: float) -> dict:
        st = {"enabled": True, "start": float(capital), "since": datetime.now(timezone.utc).isoformat(), "vault": 0.0,
              "milestone": float(capital)}
        self.save(st)
        return st

    def stop(self) -> None:
        self.save({**self.state(), "enabled": False})

    def trades(self) -> list[dict]:
        st = self.state()
        if not st.get("since") or self.journal is None:
            return []
        return [t for t in self.journal.entries() if str(t.get("notes", "")).startswith("DESK")
                and str(t.get("opened_at", "")) >= st["since"][:19]]

    @staticmethod
    def risk_for(balance: float) -> float | None:
        for limit, risk in RISK_STEPS:
            if balance <= limit:
                return risk
        return None

    def status(self) -> LadderStatus:
        st = self.state()
        trades = self.trades()
        pnl = [float(t.get("pnl") or 0) for t in trades]
        gross = st["start"] + sum(pnl)
        # Caja fuerte: en cada duplicación desde el último hito se guarda el 20% de la ganancia
        milestone, vault, curve = st.get("milestone") or st["start"], float(st.get("vault") or 0), [st["start"]]
        run = st["start"]
        for p in pnl:
            run += p
            curve.append(run - vault)
            if run - vault >= 2 * milestone:
                vault += (run - vault - milestone) * 0.20
                milestone = run - vault
        if milestone != st.get("milestone") or abs(vault - float(st.get("vault") or 0)) > 1e-9:
            self.save({**st, "milestone": milestone, "vault": vault})
        balance = gross - vault
        streak = 0
        for p in reversed(pnl):
            if p < 0:
                streak += 1
            else:
                break
        risk = self.risk_for(balance)
        if risk is not None and streak >= 2:
            risk /= 2
        paused = None
        if st["enabled"] and balance <= st["start"] * 0.5:
            paused = (f"La escalera cayó al 50% de lo que pusiste ({balance:.2f} de {st['start']:.2f} USDT). "
                      "Se pausó sola: revisa la bitácora antes de volver a empezar.")
        return LadderStatus(st["enabled"], st["start"], balance, vault, 2 * milestone, risk, streak, paused, len(pnl), curve)

    def micro_live_ok(self) -> bool:
        stt = self.status()
        return stt.enabled and stt.start <= MICRO_LIVE_MAX
