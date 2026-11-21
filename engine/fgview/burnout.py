"""
engine/fgview/burnout.py
========================
ESTRATEGIA ESPECIAL 🔥 BURN OUT 🔥  ·  ⛔ PELIGRO EXTREMO

Presupuesto bajo + apalancamiento alto para buscar un multiplicador rápido,
ACEPTANDO PERDER EL 100% DEL MONTO.

Reglas (todas se comprueban en código, no son opcionales):
* Bolsa de quema separada del capital principal: máximo el 5% del capital total
  (se permite un mínimo fijo de $10). Nunca se recarga el mismo día desde el capital.
* Apalancamiento: 50x–100x en BTC/ETH (nivel A), 25x–50x en nivel B. PROHIBIDO en nivel C.
* Margen AISLADO obligatorio.
* SL: 0.15%–0.25% en BTC/ETH (0.30%–0.50% en nivel B), siempre antes de la liquidación
  (liquidación ≥ 2× más lejos que el SL). TP: 0.4% / 0.8% / 1.2% (nivel B el doble).
* Solo se activa si el TIME MACHINE da P(TP1) ≥ 65% y la señal tiene puntuación ≥ 80.
* Máximo 2 intentos por día. Si la bolsa llega a $0, queda bloqueada 7 días.
* Antes de cada operación: advertencia obligatoria y el usuario escribe "CONFIRMO".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from engine.database import Database
from engine.fgview.config import DEFAULT_CONFIG, FGConfig
from engine.fgview.plan import Plan, PositionPlanner
from engine.fgview.timemachine import TimeMachineResult
from engine.position_simulator import MAINTENANCE_MARGIN, fmt_usd

CONFIRM_WORD = "CONFIRMO"


@dataclass(frozen=True)
class BurnRule:
    lev_min: int
    lev_max: int
    sl_min: float  # %
    sl_max: float  # %
    tps: tuple[float, float, float]  # %


BURN_RULES = {
    "A": BurnRule(50, 100, 0.15, 0.25, (0.4, 0.8, 1.2)),
    "B": BurnRule(25, 50, 0.30, 0.50, (0.8, 1.6, 2.4)),
}


@dataclass
class BurnBag:
    balance: float = 0.0
    funded_on: str | None = None  # Fecha (UTC) de la última recarga
    locked_until: str | None = None
    attempts: list[dict] = field(default_factory=list)  # {date, coin, pnl}

    def attempts_today(self, today: str) -> int:
        return sum(1 for a in self.attempts if a.get("date") == today)


@dataclass
class BurnDecision:
    active: bool  # Se puede ejecutar (solo lo impiden la bolsa vacía o bloqueada)
    plan: Plan | None
    reasons: list[str]  # Bloqueos duros (bolsa vacía / bloqueada)
    warnings: list[str] = field(default_factory=list)  # Por qué NO es recomendable (no bloquea)
    warning: str = ""
    liq_move_pct: float = 0.0
    p_lose_all: float = 0.0

    @property
    def recommended(self) -> bool:
        return self.active and not self.warnings


class BurnOutStrategy:
    KEY = "burn_bag"
    MIN_P_TP1 = 65.0
    MIN_SCORE = 80
    MAX_ATTEMPTS = 2
    LOCK_DAYS = 7
    LIQ_SAFETY = 2.0  # Liquidación ≥ 2× la distancia del SL
    EMPTY = 1.0  # Por debajo de $1 la bolsa se considera vacía

    def __init__(self, planner: PositionPlanner, db: Database | None, config: FGConfig = DEFAULT_CONFIG) -> None:
        self.planner, self.db, self.cfg = planner, db, config
        self._mem: dict = {}

    # ------------------------------------------------------------------ bolsa
    def bag(self) -> BurnBag:
        data = (self.db.get_state(self.KEY, {}) if self.db is not None else self._mem) or {}
        return BurnBag(**{k: v for k, v in data.items() if k in BurnBag.__dataclass_fields__})

    def _save(self, bag: BurnBag) -> None:
        data = bag.__dict__.copy()
        if self.db is not None:
            self.db.set_state(self.KEY, data)
        else:
            self._mem = data

    @staticmethod
    def _today(now: datetime | None = None) -> str:
        return (now or datetime.now(timezone.utc)).date().isoformat()

    @staticmethod
    def max_bag(capital: float) -> float:
        """5% del capital total, con un mínimo permitido de $10 (nunca más que el capital)."""
        return min(capital, max(capital * 0.05, 10.0))

    def locked(self, now: datetime | None = None) -> str | None:
        b = self.bag()
        now = now or datetime.now(timezone.utc)
        if b.locked_until and now < datetime.fromisoformat(b.locked_until):
            return f"Bolsa de quema bloqueada hasta el {datetime.fromisoformat(b.locked_until):%d/%m %H:%M} UTC (llegó a $0)."
        return None

    def fund(self, amount: float, capital: float, now: datetime | None = None) -> str | None:
        """Recarga la bolsa. Devuelve el motivo si no se permite (None = hecho)."""
        today = self._today(now)
        b = self.bag()
        if (lock := self.locked(now)):
            return lock
        if amount <= 0:
            return "El monto debe ser mayor que cero."
        if b.funded_on == today or b.attempts_today(today) > 0:
            return "Nunca se recarga la bolsa de quema el mismo día (ya hubo recarga o intentos hoy). Vuelve mañana."
        if b.balance + amount > self.max_bag(capital) + 1e-9:
            return (f"Máximo de la bolsa: {fmt_usd(self.max_bag(capital))} (5% de tu capital de {fmt_usd(capital)}, "
                    f"mínimo permitido $10). Ya tienes {fmt_usd(b.balance)}.")
        b.balance += amount
        b.funded_on = today
        self._save(b)
        return None

    def register_result(self, pnl: float, coin: str, now: datetime | None = None) -> BurnBag:
        now = now or datetime.now(timezone.utc)
        b = self.bag()
        b.balance = max(0.0, b.balance + pnl)
        b.attempts.append({"date": self._today(now), "coin": coin, "pnl": round(pnl, 4)})
        if b.balance < self.EMPTY:
            b.balance = 0.0
            b.locked_until = (now + timedelta(days=self.LOCK_DAYS)).isoformat()
        self._save(b)
        return b

    # ------------------------------------------------------------------ plan
    def build_plan(self, symbol: str, direction: str, entry: float, atr5: float | None, stake: float,
                   lev_cap: int | None = None) -> Plan:
        tier, listed = self.planner.tiers.for_coin(symbol)
        rule = BURN_RULES.get(tier.key)
        if rule is None or not listed:
            raise ValueError(f"BURN OUT está PROHIBIDO en {self.planner.tiers.resolve(symbol)} (nivel {tier.key}: "
                             "memecoins o criptos fuera de la lista).")
        if stake <= 0:
            raise ValueError("La bolsa de quema está vacía.")
        atr_pct = (atr5 / entry * 100) if atr5 and atr5 > 0 else rule.sl_min
        sl_pct = min(rule.sl_max, max(rule.sl_min, atr_pct))
        sl = sl_pct / 100
        lev = rule.lev_max
        while lev > rule.lev_min and (1 / lev - MAINTENANCE_MARGIN) < self.LIQ_SAFETY * sl:
            lev -= 1
        if (1 / lev - MAINTENANCE_MARGIN) < self.LIQ_SAFETY * sl:
            raise ValueError("El SL necesario no cabe antes de la liquidación ni con el apalancamiento mínimo.")
        squeezed = bool(lev_cap and lev_cap < lev)
        if squeezed:  # Anti-squeeze: la liquidación debe quedar más lejos que el peor movimiento reciente
            lev = max(1, int(lev_cap))
        d = 1 if direction == "LONG" else -1
        notional = stake * lev
        fees = notional * self.cfg.fee * 2
        split = self.cfg.tp_split
        tps = [(entry * (1 + d * pct / 100), pct, close) for pct, close in zip(rule.tps, split)]
        tp_pnl = [notional * close / 100 * pct / 100 - fees * close / 100 for _, pct, close in tps]
        sl_pnl = -notional * sl - fees
        rr_w = sum(pct * close / 100 for _, pct, close in tps) / sl_pct
        rr_net = sum(tp_pnl) / abs(sl_pnl)
        notes = [f"⛔ BURN OUT: margen AISLADO {fmt_usd(stake)} × {lev}x = posición {fmt_usd(notional)}."]
        if squeezed:
            notes.append(f"🛡️ Anti-squeeze: apalancamiento reducido a {lev}x porque hubo movimientos bruscos en las "
                         "últimas 24 h que habrían alcanzado la liquidación con más apalancamiento.")
        if atr_pct > rule.sl_max:
            notes.append(f"La volatilidad (ATR {atr_pct:.2f}%) supera el SL máximo ({rule.sl_max}%): el ruido normal "
                         "puede sacarte. El Time Machine ya lo tiene en cuenta.")
        return Plan(
            coin=self.planner.tiers.resolve(symbol), direction=direction, capital=stake, entry=entry, tier=tier,
            listed=listed, risk_pct=100.0, risk_usd=abs(sl_pnl), max_margin_pct=100.0, sl_pct=sl_pct,
            sl_source="BURN OUT", stop_loss=entry * (1 - d * sl), tps=tps, leverage=lev, margin=stake,
            notional=notional, coins_qty=notional / entry, liquidation=self.planner.liquidation(direction, entry, lev),
            fees=fees, tp_pnl=tp_pnl, sl_pnl=sl_pnl, rr_weighted=rr_w, rr_net=rr_net,
            breakeven_wr=1 / (1 + rr_net) * 100 if rr_net > 0 else 100.0, break_even=entry * (1 + d * 2 * self.cfg.fee),
            trailing=None, options=[], notes=notes)

    # -------------------------------------------------------------- decisión
    def gate(self, signal_score: int, tm: TimeMachineResult | None, external_blocks: list[str] | None = None,
             now: datetime | None = None, squeeze_text: str | None = None) -> tuple[list[str], list[str]]:
        """
        (bloqueos, avisos). Solo bloquea lo que hace imposible operar (bolsa vacía o bloqueada).
        Todo lo demás son avisos: la operación se da igual, marcada como NO RECOMENDADA.
        """
        hard: list[str] = []
        warn = list(external_blocks or [])
        b = self.bag()
        if (lock := self.locked(now)):
            hard.append(lock)
        if b.balance < self.EMPTY:
            hard.append("La bolsa de quema está vacía: recárgala (máximo 5% de tu capital).")
        if b.attempts_today(self._today(now)) >= self.MAX_ATTEMPTS:
            warn.append(f"Ya hiciste {b.attempts_today(self._today(now))} intentos hoy (lo recomendado es máximo "
                        f"{self.MAX_ATTEMPTS}).")
        if signal_score < self.MIN_SCORE:
            warn.append(f"Puntuación de la señal {signal_score}/100 (recomendado ≥ {self.MIN_SCORE}).")
        if tm is None:
            warn.append("Sin Time Machine.")
        elif tm.p_tp1 < self.MIN_P_TP1:
            warn.append(f"Time Machine: P(TP1) {tm.p_tp1:.1f}% (recomendado ≥ {self.MIN_P_TP1:.0f}%).")
        if squeeze_text:
            warn.append(squeeze_text)
        return hard, warn

    @staticmethod
    def warning(plan: Plan, tm: TimeMachineResult | None) -> tuple[str, float, float]:
        liq_move = abs(plan.entry - plan.liquidation) / plan.entry * 100
        p_all = tm.p_liq if tm is not None else float("nan")
        text = (f"🔥 BURN OUT: con {plan.leverage}x, un movimiento de −{liq_move:.2f}% contra ti te liquida. "
                f"Probabilidad estimada de perder todo el monto: {p_all:.1f}%. "
                "Solo usa dinero que estés dispuesto a perder.")
        return text, liq_move, p_all

    def decide(self, plan: Plan, signal_score: int, tm: TimeMachineResult | None,
               external_blocks: list[str] | None = None, now: datetime | None = None,
               squeeze_text: str | None = None) -> BurnDecision:
        hard, warn = self.gate(signal_score, tm, external_blocks, now, squeeze_text)
        text, liq_move, p_all = self.warning(plan, tm)
        return BurnDecision(not hard, plan, hard, warn, text, liq_move, p_all)

    @staticmethod
    def confirmed(typed: str) -> bool:
        return (typed or "").strip().upper() == CONFIRM_WORD

    @staticmethod
    def multiplier(plan: Plan) -> float:
        """Cuántas veces se multiplica el monto si se cobran los 3 TPs."""
        return 1 + sum(plan.tp_pnl) / plan.margin if plan.margin > 0 else math.nan
