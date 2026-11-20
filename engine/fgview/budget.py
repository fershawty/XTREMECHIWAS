"""
engine/fgview/budget.py
=======================
BUDGET MANAGER (Módulo 12): tu saldo real y cuánto arriesgar.

* Saldo = capital inicial + PnL de la bitácora − retiros.
* Riesgo por trade según la tabla de capital del Módulo 1 (BudgetPolicy).
* Drawdown ≥ 10% desde el máximo -> riesgo a la MITAD hasta recuperar un 5% desde el mínimo.
* Cada vez que el capital crece un 25% sobre la base, sugiere retirar el 20% de la ganancia.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from engine.database import Database
from engine.fgview.journal import TradeJournal
from engine.fgview.policy import BudgetPolicy
from engine.position_simulator import fmt_usd


@dataclass
class BudgetStatus:
    start: float
    balance: float
    peak: float
    drawdown_pct: float
    defensive: bool  # Riesgo a la mitad
    base_risk_pct: float
    risk_pct: float
    max_margin_pct: float
    withdraw_suggestion: float  # USDT a retirar (0 = nada)
    withdrawn: float
    curve: list[float] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


class BudgetManager:
    KEY = "budget_manager"

    def __init__(self, db: Database | None, journal: TradeJournal | None, policy: BudgetPolicy | None = None,
                 dd_limit: float = 10.0, recover_pct: float = 5.0, growth_step: float = 25.0,
                 withdraw_share: float = 20.0) -> None:
        self.db, self.journal = db, journal
        self.policy = policy or BudgetPolicy()
        self.dd_limit, self.recover_pct = dd_limit, recover_pct
        self.growth_step, self.withdraw_share = growth_step, withdraw_share
        self._mem: dict = {}

    # ----------------------------------------------------------------- estado
    def state(self) -> dict:
        data = (self.db.get_state(self.KEY, {}) if self.db is not None else self._mem) or {}
        return {"start": 0.0, "withdrawals": [], **data}

    def _save(self, data: dict) -> None:
        if self.db is not None:
            self.db.set_state(self.KEY, data)
        else:
            self._mem = data

    def set_start(self, capital: float) -> None:
        s = self.state()
        s["start"] = float(capital)
        s.setdefault("base", float(capital))
        s["base"] = float(capital)
        self._save(s)

    def register_withdrawal(self, amount: float) -> None:
        s = self.state()
        s["withdrawals"].append({"at": datetime.now(timezone.utc).isoformat(), "amount": float(amount)})
        self._save(s)
        s["base"] = self.status().balance  # La nueva base es el saldo tras retirar
        self._save(s)

    # ---------------------------------------------------------------- cálculo
    def status(self, trades: list[dict] | None = None) -> BudgetStatus:
        s = self.state()
        start = float(s.get("start") or 0.0)
        trades = trades if trades is not None else (self.journal.entries() if self.journal else [])
        withdrawn = sum(w["amount"] for w in s["withdrawals"])
        curve, bal = [start], start
        peak, trough, defensive = start, start, False
        for t in trades:
            bal += float(t.get("pnl") or 0.0)
            curve.append(bal)
            peak = max(peak, bal)
            dd = (peak - bal) / peak * 100 if peak > 0 else 0.0
            if not defensive and dd >= self.dd_limit:
                defensive, trough = True, bal
            elif defensive:
                trough = min(trough, bal)
                if trough > 0 and (bal / trough - 1) * 100 >= self.recover_pct:
                    defensive, peak = False, bal  # Recuperado: nuevo punto de partida
        balance = bal - withdrawn
        base_risk, max_margin = self.policy.rule(max(balance, 1.0))
        risk = base_risk * (0.5 if defensive else 1.0)
        dd_now = (peak - bal) / peak * 100 if peak > 0 else 0.0
        msgs = []
        if start <= 0:
            msgs.append("Escribe tu capital inicial para empezar a llevar el saldo.")
        if defensive:
            msgs.append(f"🛡️ Modo defensivo: drawdown ≥ {self.dd_limit:g}%. Riesgo por trade a la mitad "
                        f"({risk:g}%) hasta recuperar un {self.recover_pct:g}% desde el mínimo.")
        base = float(s.get("base") or start)
        suggestion = 0.0
        if base > 0 and balance >= base * (1 + self.growth_step / 100):
            suggestion = (balance - base) * self.withdraw_share / 100
            msgs.append(f"💸 Tu capital creció {(balance / base - 1) * 100:.0f}% desde la última base: retira "
                        f"{fmt_usd(suggestion)} ({self.withdraw_share:g}% de la ganancia) y guárdalo.")
        return BudgetStatus(start, balance, peak - withdrawn, dd_now, defensive, base_risk, risk, max_margin,
                            suggestion, withdrawn, curve, msgs)
