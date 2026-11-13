"""
engine/risk_manager.py
======================
GESTIÓN DE RIESGO (lo más importante del bot).

Implementa:
* Stop Loss automático: ATR x 1.5 o justo debajo/encima del último Order Block.
* Take Profit en 1:2 (TP1) y 1:3 (TP2).
* Tamaño de posición por % de riesgo (1 % de la cuenta por defecto).
  - Cripto: unidades de la moneda base.
  - Futuros CME: número de contratos según el multiplicador (ES = 50 $/punto...).
  - Acciones/ETFs: número de acciones.
* Precio de liquidación aproximado y aviso si la liquidación llega antes que el SL.
* Filtro de noticias de alto impacto (±30 min) -> ver news_filter.py
* Kill Switch: 3 pérdidas consecutivas -> bot en pausa hasta reactivarlo a mano.
* Seguimiento automático de señales abiertas: marca WIN/LOSS/EXPIRED con las velas nuevas.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime

import numpy as np
import pandas as pd

from engine.config import SETTINGS, get_logger
from engine.database import Database, utc_now_iso
from engine.news_filter import NewsFilter, NewsStatus

log = get_logger("risk_manager")

# Valor de 1 punto por contrato (USD) en futuros CME (símbolos de Yahoo)
CONTRACT_MULTIPLIERS: dict[str, float] = {
    "ES=F": 50, "MES=F": 5, "NQ=F": 20, "MNQ=F": 2, "YM=F": 5, "MYM=F": 0.5,
    "RTY=F": 50, "M2K=F": 5, "GC=F": 100, "MGC=F": 10, "SI=F": 5000, "SIL=F": 1000,
    "CL=F": 1000, "MCL=F": 100, "NG=F": 10000, "ZN=F": 1000, "ZB=F": 1000, "6E=F": 125000,
}
MICRO_ALTERNATIVE: dict[str, str] = {
    "ES=F": "MES=F", "NQ=F": "MNQ=F", "YM=F": "MYM=F", "RTY=F": "M2K=F", "GC=F": "MGC=F", "CL=F": "MCL=F",
}

LOSS_STATUSES = ("LOSS", "MANUAL_LOSS")


# =============================================================================
# Parámetros y resultados
# =============================================================================
@dataclass(frozen=True)
class RiskParams:
    """Parámetros de riesgo (por defecto los del .env)."""

    equity: float = SETTINGS.account_equity
    risk_pct: float = SETTINGS.risk_per_trade_pct
    leverage: float = SETTINGS.default_leverage
    atr_mult: float = SETTINGS.atr_sl_multiplier
    tp1_rr: float = SETTINGS.tp1_rr
    tp2_rr: float = SETTINGS.tp2_rr
    sl_method: str = "auto"  # auto | atr | order_block
    ob_buffer_atr: float = 0.1  # Margen bajo el OB (en ATR) para evitar barridas
    max_ob_distance_atr: float = 3.0  # SL por OB solo si no está a más de 3 ATR
    min_sl_atr: float = 0.5  # SL nunca más cerca de 0.5 ATR (ruido)
    maintenance_margin_rate: float = 0.005  # 0.5 % (aprox. Binance para tamaños pequeños)
    taker_fee_pct: float = SETTINGS.taker_fee_pct

    def with_overrides(self, **kwargs) -> "RiskParams":
        """Copia con algunos valores cambiados (ignora los None)."""
        return replace(self, **{k: v for k, v in kwargs.items() if v is not None})


@dataclass
class TradePlan:
    """Plan de operación completo para la señal."""

    direction: str  # LONG | SHORT
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    sl_method: str
    sl_distance: float
    sl_pct: float
    risk_amount: float  # Dinero en riesgo si salta el SL
    position_size: float
    size_unit: str  # "BTC", "contratos", "acciones"
    notional: float  # Valor total de la posición
    margin_required: float
    leverage: float
    max_safe_leverage: float  # Apalancamiento máximo para que la liquidación no llegue antes del SL
    liquidation_price: float | None
    rr_tp1: float
    rr_tp2: float
    fees_estimate: float
    profit_tp1: float
    profit_tp2: float
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RiskCheck:
    """Resultado de los controles previos a operar."""

    allowed: bool
    blocks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    news: NewsStatus | None = None
    kill_switch_active: bool = False
    consecutive_losses: int = 0


def _round_price(value: float) -> float:
    """Redondea un precio a una precisión razonable según su magnitud."""
    if value is None or not math.isfinite(value) or value == 0:
        return value
    decimals = max(2, 6 - int(math.floor(math.log10(abs(value)))))
    return round(value, min(decimals, 10))


# =============================================================================
# Gestor de riesgo
# =============================================================================
class RiskManager:
    """Calcula niveles, tamaño de posición y aplica los controles de seguridad."""

    def __init__(
        self,
        params: RiskParams | None = None,
        db: Database | None = None,
        news: NewsFilter | None = None,
        kill_switch_losses: int = SETTINGS.kill_switch_losses,
    ) -> None:
        self.params = params or RiskParams()
        self.db = db
        self.news = news
        self.kill_switch_losses = kill_switch_losses

    # ----------------------------------------------------------- niveles
    def stop_loss(self, direction: int, entry: float, atr: float, ob_level: float | None = None,
                  params: RiskParams | None = None) -> tuple[float, str]:
        """
        Stop Loss:
        * "auto": debajo del último Order Block si está a una distancia sensata
                  (0.5 - 3 ATR); si no, ATR x 1.5.
        * "atr": siempre ATR x multiplicador.
        * "order_block": OB si es válido; si no, ATR (avisando).
        """
        p = params or self.params
        atr_sl = entry - direction * p.atr_mult * atr
        if p.sl_method in ("auto", "order_block") and ob_level is not None and math.isfinite(ob_level):
            ob_sl = ob_level - direction * p.ob_buffer_atr * atr
            dist = (entry - ob_sl) * direction
            if p.min_sl_atr * atr <= dist <= p.max_ob_distance_atr * atr:
                return ob_sl, "Order Block"
        return atr_sl, f"ATR x {p.atr_mult:g}"

    @staticmethod
    def take_profits(direction: int, entry: float, stop: float, tp1_rr: float, tp2_rr: float) -> tuple[float, float]:
        """TP1 = 1:tp1_rr y TP2 = 1:tp2_rr respecto a la distancia del SL."""
        risk = abs(entry - stop)
        return entry + direction * tp1_rr * risk, entry + direction * tp2_rr * risk

    @staticmethod
    def liquidation_price(direction: int, entry: float, leverage: float, mmr: float) -> float | None:
        """Liquidación aproximada en margen AISLADO (sin comisiones ni funding)."""
        if leverage <= 1:
            return None if direction == 1 else entry * (2.0 - mmr)
        return entry * (1.0 - 1.0 / leverage + mmr) if direction == 1 else entry * (1.0 + 1.0 / leverage - mmr)

    @staticmethod
    def max_safe_leverage(direction: int, entry: float, stop: float, mmr: float) -> float:
        """Apalancamiento máximo para que la liquidación quede MÁS ALLÁ del SL (con 10 % de colchón)."""
        dist_pct = abs(entry - stop) / entry * 1.1
        denom = dist_pct + mmr
        return max(1.0, math.floor(1.0 / denom)) if denom > 0 else 1.0

    # ---------------------------------------------------- tamaño posición
    def position_size(self, entry: float, stop: float, symbol: str, market: str,
                      params: RiskParams | None = None) -> tuple[float, str, float, list[str]]:
        """
        Tamaño para arriesgar exactamente `risk_pct` % del capital.

        Returns:
            (tamaño, unidad, nocional, avisos)
        """
        p = params or self.params
        warnings: list[str] = []
        risk_amount = p.equity * p.risk_pct / 100.0
        per_unit = abs(entry - stop)
        if per_unit <= 0:
            return 0.0, "", 0.0, ["Distancia de SL nula: no se puede calcular el tamaño."]

        sym = symbol.upper()
        if market == "cme":
            mult = CONTRACT_MULTIPLIERS.get(sym)
            if mult is None:
                warnings.append(f"Multiplicador de {sym} desconocido: se asume 1 $/punto. Revísalo.")
                mult = 1.0
            contracts = math.floor(risk_amount / (per_unit * mult))
            if contracts < 1:
                micro = MICRO_ALTERNATIVE.get(sym)
                tip = f" Prueba el micro {micro}." if micro else ""
                warnings.append(f"Con {p.risk_pct}% de riesgo no alcanza para 1 contrato de {sym}.{tip}")
            return float(contracts), "contratos", contracts * mult * entry, warnings
        if market in ("stock", "etf"):
            shares = math.floor(risk_amount / per_unit)
            if shares < 1:
                warnings.append("El riesgo permitido no alcanza para 1 acción.")
            return float(shares), "acciones", shares * entry, warnings

        # Cripto (lineal USDT-M). Para COIN-M se muestra el equivalente.
        units = risk_amount / per_unit
        base = sym.split("/")[0].split("-")[0] if sym else "unid."
        return float(f"{units:.6g}"), base, units * entry, warnings

    # ------------------------------------------------------- plan completo
    def build_plan(
        self,
        direction: int,
        entry: float,
        atr: float,
        ob_level: float | None = None,
        symbol: str = "",
        market: str = "crypto",
        contract: str | None = None,
        params: RiskParams | None = None,
    ) -> TradePlan:
        """Calcula SL, TP1, TP2, tamaño, margen, liquidación y avisos."""
        p = params or self.params
        if direction not in (1, -1):
            raise ValueError("direction debe ser 1 (LONG) o -1 (SHORT)")
        if not (atr and math.isfinite(atr) and atr > 0):
            atr = entry * 0.01  # Fallback: 1 % del precio
        stop, method = self.stop_loss(direction, entry, atr, ob_level, p)
        tp1, tp2 = self.take_profits(direction, entry, stop, p.tp1_rr, p.tp2_rr)
        size, unit, notional, warnings = self.position_size(entry, stop, symbol, market, p)

        is_leveraged = market in ("crypto", "demo", "cme")
        leverage = float(p.leverage) if market in ("crypto", "demo") else 1.0
        margin = notional / leverage if leverage > 0 else notional
        liq = self.liquidation_price(direction, entry, leverage, p.maintenance_margin_rate) if market in ("crypto", "demo") else None
        safe_lev = self.max_safe_leverage(direction, entry, stop, p.maintenance_margin_rate)
        risk_amount = abs(entry - stop) * (notional / entry if entry else 0.0)
        fees = notional * p.taker_fee_pct / 100.0 * 2.0
        rr_unit = abs(entry - stop)

        if liq is not None and ((direction == 1 and liq >= stop) or (direction == -1 and liq <= stop)):
            warnings.append(f"⚠ La LIQUIDACIÓN ({_round_price(liq)}) llega antes que el SL: usa ≤ {safe_lev:.0f}x.")
        if market in ("crypto", "demo") and margin > p.equity:
            warnings.append("El margen requerido supera tu capital: baja el apalancamiento o el riesgo.")
        if contract == "COIN-M":
            warnings.append("COIN-M (inverso): el tamaño se muestra como equivalente lineal; 1 contrato = 100 USD (BTC) / 10 USD (resto).")
        if market == "cme":
            warnings.append("Futuros CME: margen según tu broker (no se calcula liquidación).")
        if not is_leveraged:
            warnings.append("Activo de contado: sin apalancamiento en el cálculo.")
        if fees > 0.25 * risk_amount > 0:
            warnings.append(f"Las comisiones estimadas ({fees:.2f}) son >25 % del riesgo: SL demasiado ajustado.")

        return TradePlan(
            direction="LONG" if direction == 1 else "SHORT",
            entry=_round_price(entry),
            stop_loss=_round_price(stop),
            tp1=_round_price(tp1),
            tp2=_round_price(tp2),
            sl_method=method,
            sl_distance=_round_price(rr_unit),
            sl_pct=round(rr_unit / entry * 100.0, 3) if entry else 0.0,
            risk_amount=round(risk_amount, 2),
            position_size=size,
            size_unit=unit,
            notional=round(notional, 2),
            margin_required=round(margin, 2),
            leverage=leverage,
            max_safe_leverage=safe_lev,
            liquidation_price=_round_price(liq) if liq else None,
            rr_tp1=p.tp1_rr,
            rr_tp2=p.tp2_rr,
            fees_estimate=round(fees, 2),
            profit_tp1=round(risk_amount * p.tp1_rr - fees, 2),
            profit_tp2=round(risk_amount * p.tp2_rr - fees, 2),
            warnings=warnings,
        )

    # ------------------------------------------------------- kill switch
    def kill_switch_status(self) -> tuple[bool, int]:
        """
        Devuelve (activo, pérdidas consecutivas). Si se alcanzan
        KILL_SWITCH_LOSSES pérdidas seguidas, se activa y queda guardado en SQLite.
        """
        if self.db is None:
            return False, 0
        active = bool(self.db.get_state("kill_switch_active", False))
        reset_at = self.db.get_state("kill_switch_reset_at")
        streak = 0
        for sig in reversed(self.db.closed_signals(since_iso=reset_at)):
            if sig["status"] in LOSS_STATUSES:
                streak += 1
            else:
                break
        if streak >= self.kill_switch_losses and not active:
            self.db.set_state("kill_switch_active", True)
            self.db.set_state("kill_switch_since", utc_now_iso())
            log.warning("KILL SWITCH activado: %s pérdidas consecutivas", streak)
            active = True
        return active, streak

    def reset_kill_switch(self) -> None:
        """Reactiva el bot. Las pérdidas anteriores dejan de contar."""
        if self.db is None:
            return
        self.db.set_state("kill_switch_active", False)
        self.db.set_state("kill_switch_reset_at", utc_now_iso())
        log.info("Kill switch reiniciado manualmente")

    # --------------------------------------------------- controles previos
    def pre_trade_check(self, now: datetime | None = None) -> RiskCheck:
        """Noticias + kill switch. Nunca lanza excepciones."""
        check = RiskCheck(allowed=True)
        try:
            active, streak = self.kill_switch_status()
            check.kill_switch_active, check.consecutive_losses = active, streak
            if active:
                check.blocks.append(f"KILL SWITCH: {streak} pérdidas consecutivas. Bot en pausa hasta reactivarlo.")
            elif streak:
                check.warnings.append(f"Racha actual: {streak} pérdida(s) consecutiva(s) de {self.kill_switch_losses}.")
        except Exception as exc:  # noqa: BLE001
            check.warnings.append(f"No se pudo leer el kill switch: {exc}")
        if self.news is not None:
            status = self.news.check(now)
            check.news = status
            if status.blocked:
                check.blocks.append(status.message)
            if self.news.last_error:
                check.warnings.append(self.news.last_error)
        check.allowed = not check.blocks
        return check

    # ------------------------------------------- seguimiento de señales
    def update_open_signals(self, symbol: str, timeframe: str, df: pd.DataFrame,
                            expiry_bars: int = SETTINGS.signal_expiry_bars) -> list[dict]:
        """
        Revisa las señales abiertas de este símbolo/temporalidad con las velas nuevas:
        * toca SL primero -> LOSS (-1R)   (si SL y TP caen en la misma vela, se asume SL: conservador)
        * toca TP1        -> WIN (+TP1 R)
        * pasan `expiry_bars` velas sin tocar nada -> EXPIRED (no cuenta para el kill switch)
        """
        if self.db is None or df.empty:
            return []
        closed: list[dict] = []
        for sig in self.db.open_signals(symbol, timeframe):
            try:
                bar_time = pd.Timestamp(sig["bar_time"])
                bar_time = bar_time.tz_localize("UTC") if bar_time.tzinfo is None else bar_time.tz_convert("UTC")
                after = df[df.index > bar_time]
                if after.empty:
                    continue
                d = 1 if sig["direction"] == "LONG" else -1
                entry, sl, tp1 = float(sig["entry"]), float(sig["stop_loss"]), float(sig["tp1"])
                risk = abs(entry - sl) or 1e-12
                sl_hit = (after["low"] <= sl) if d == 1 else (after["high"] >= sl)
                tp_hit = (after["high"] >= tp1) if d == 1 else (after["low"] <= tp1)
                first_sl = int(np.argmax(sl_hit.to_numpy())) if sl_hit.any() else None
                first_tp = int(np.argmax(tp_hit.to_numpy())) if tp_hit.any() else None

                if first_sl is not None and (first_tp is None or first_sl <= first_tp):
                    self.db.close_signal(sig["id"], "LOSS", sl, -1.0, "SL alcanzado")
                    closed.append({**sig, "status": "LOSS"})
                elif first_tp is not None:
                    r = round(abs(tp1 - entry) / risk, 2)
                    self.db.close_signal(sig["id"], "WIN", tp1, r, "TP1 alcanzado")
                    closed.append({**sig, "status": "WIN"})
                elif len(after) >= expiry_bars:
                    last = float(after["close"].iloc[-1])
                    r = round((last - entry) * d / risk, 2)
                    self.db.close_signal(sig["id"], "EXPIRED", last, r, f"Sin resolver en {expiry_bars} velas")
                    closed.append({**sig, "status": "EXPIRED"})
            except Exception as exc:  # noqa: BLE001
                log.error("No se pudo actualizar la señal %s: %s", sig.get("id"), exc)
        if closed:
            self.kill_switch_status()
        return closed

    def record_manual_result(self, signal_id: int, win: bool, result_r: float | None = None) -> None:
        """Registra a mano el resultado real de una operación (si la ejecutaste tú)."""
        if self.db is None:
            return
        status = "MANUAL_WIN" if win else "MANUAL_LOSS"
        self.db.close_signal(signal_id, status, None, result_r if result_r is not None else (2.0 if win else -1.0),
                             "Resultado registrado manualmente")
        self.kill_switch_status()
