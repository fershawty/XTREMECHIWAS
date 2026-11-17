"""
engine/recommender.py
=====================
FG VIEW V1 · Motor de recomendaciones para scalping de criptos en futuros
(operaciones de 15 a 30 minutos).

Módulo 1 · Recomendación por presupuesto: riesgo y margen máximo según capital,
           niveles A/B/C por volatilidad, SL dinámico por ATR(14) en velas de 5 min.
Módulo 2 · Fórmulas: posición por riesgo fijo, apalancamiento automático dentro del
           rango del nivel, liquidación a ≥ 3× la distancia del SL, R:R ponderado.
Módulo 3 · Gestión: TP1 50 % (SL a break-even), TP2 30 % (SL a TP1), TP3 20 % o
           trailing 0.5 × ATR, salida por tiempo a los 30 min, stop diario.
Módulo 4 · Filtros de entrada (mínimo 4 de 6) y bloqueos. Confianza 0-100.

Todos los cálculos son deterministas y auditables. Nunca inventa precios: la
entrada sale de los datos en vivo o la escribe el usuario.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from engine.position_simulator import (BUDGET_TABLE, MAINTENANCE_MARGIN, base_coin, budget_rule, fmt_pct,  # noqa: F401
                                       fmt_px, fmt_usd)

FEE = 0.0005  # 0.05 % taker por lado
MAX_HOLD_MIN = 30
TP_SPLIT = (50.0, 30.0, 20.0)  # % de cierre en TP1 / TP2 / TP3
ATR_SL_MULT = 1.2  # SL dinámico = 1.2 × ATR(14) de 5 min
ATR_TRAIL_MULT = 0.5  # Trailing del TP3 = 0.5 × ATR(14) de 5 min
LIQ_SAFETY = 3.0  # La liquidación debe estar ≥ 3× más lejos que el SL
MIN_FILTERS = 4  # Mínimo de filtros cumplidos para recomendar entrar
MIN_RR_NET = 1.0  # R:R neto mínimo (después de comisiones) para recomendar entrar
DAILY_MAX_LOSSES = 3
DAILY_MAX_LOSS_PCT = 3.0

# =============================================================================
# Módulo 1 · Tablas
# =============================================================================
@dataclass(frozen=True)
class Tier:
    """Nivel de volatilidad."""

    key: str
    lev_min: int
    lev_max: int
    sl: float  # % mínimo del SL
    tp: tuple[float, float, float]  # % de TP1, TP2, TP3
    coins: tuple[str, ...]


TIERS: dict[str, Tier] = {
    "A": Tier("A", 10, 20, 0.35, (0.40, 0.70, 1.00), ("BTC", "ETH")),
    "B": Tier("B", 5, 10, 0.70, (0.80, 1.40, 2.00),
              ("SOL", "BNB", "XRP", "LINK", "AVAX", "SUI", "LTC", "ADA", "TON", "HYPE", "NEAR", "APT", "DOT",
               "TRX", "BCH")),
    "C": Tier("C", 3, 5, 1.20, (1.50, 2.50, 3.50),
              ("DOGE", "PEPE", "WIF", "BONK", "SHIB", "FLOKI", "TRUMP", "POPCAT", "TAO", "FET", "RENDER", "ENA",
               "WLD", "ONDO", "JUP", "INJ", "SEI", "TIA", "ARB", "OP")),
}
TIER_COINS = [c for t in TIERS.values() for c in t.coins]


def tier_for(coin: str) -> tuple[Tier, bool]:
    """(nivel, está_en_la_lista). Las criptos fuera de la lista van al nivel C (el más prudente)."""
    c = base_coin(coin)
    for tier in TIERS.values():
        if c in tier.coins:
            return tier, True
    return TIERS["C"], False


def tier_label(coin: str) -> str:
    tier, listed = tier_for(coin)
    return f"Nivel {tier.key}" + ("" if listed else " (fuera de la lista)")


# =============================================================================
# Módulo 2 · Cálculo del plan
# =============================================================================
def liquidation(direction: str, entry: float, lev: float) -> float:
    if direction == "LONG":
        return entry * (1 - 1 / lev + MAINTENANCE_MARGIN)
    return entry * (1 + 1 / lev - MAINTENANCE_MARGIN)


def max_safe_leverage(sl_frac: float) -> int:
    """Apalancamiento máximo para que la liquidación quede ≥ 3× más lejos que el SL."""
    return max(1, math.floor(1.0 / (LIQ_SAFETY * sl_frac + MAINTENANCE_MARGIN)))


@dataclass
class LeverageOption:
    leverage: int
    margin: float
    roi_tp1: float
    roi_tp3: float
    roi_sl: float
    liquidation: float
    liq_ok: bool  # liquidación ≥ 3× SL
    margin_ok: bool  # margen ≤ tope del presupuesto
    recommended: bool = False


@dataclass
class Plan:
    """Plan de operación recomendado."""

    coin: str
    direction: str
    capital: float
    entry: float
    tier: Tier
    listed: bool
    risk_pct: float
    risk_usd: float
    max_margin_pct: float
    sl_pct: float
    sl_source: str  # "ATR" | "tabla"
    stop_loss: float
    tps: list[tuple[float, float, float]]  # (precio, % movimiento, % cierre)
    leverage: int
    margin: float
    notional: float
    coins_qty: float
    liquidation: float
    fees: float
    tp_pnl: list[float]  # PnL neto de cada TP (parte que cierra)
    sl_pnl: float  # PnL neto si salta el SL con toda la posición
    rr_weighted: float  # R:R ponderado bruto (fórmula: Σ(TPi × %cierre) ÷ SL)
    rr_net: float  # R:R real descontando comisiones
    breakeven_wr: float  # Win rate de equilibrio CON comisiones
    break_even: float
    trailing: float | None  # Distancia del trailing (precio) para el TP3
    options: list[LeverageOption]
    notes: list[str] = field(default_factory=list)


def build_plan(capital: float, coin: str, direction: str, entry: float, atr5: float | None = None,
               fee: float = FEE) -> Plan:
    """Plan completo de la recomendación (Módulos 1 y 2)."""
    if capital <= 0 or entry <= 0 or direction not in ("LONG", "SHORT"):
        raise ValueError("Capital, entrada y dirección (LONG/SHORT) son obligatorios.")
    tier, listed = tier_for(coin)
    risk_pct, max_margin_pct = budget_rule(capital)
    notes: list[str] = []
    if not listed:
        notes.append(f"{base_coin(coin)} no está en los niveles A/B/C: se trata como Nivel C (máxima prudencia).")

    # SL dinámico: 1.2 × ATR(14) de 5 min, nunca menor que el % de la tabla
    sl_frac, sl_source = tier.sl / 100.0, "tabla"
    if atr5 and atr5 > 0:
        atr_frac = ATR_SL_MULT * atr5 / entry
        if atr_frac > sl_frac:
            sl_frac, sl_source = atr_frac, "ATR"
    d = 1 if direction == "LONG" else -1
    stop = entry * (1 - d * sl_frac)

    risk_usd = capital * risk_pct / 100.0
    notional = risk_usd / sl_frac
    max_margin = capital * max_margin_pct / 100.0

    # Apalancamiento: el mínimo del rango que respete el tope de margen,
    # sin superar el máximo seguro para la liquidación.
    lev_safe = max_safe_leverage(sl_frac)
    lev_hi = min(tier.lev_max, lev_safe)
    lev_lo = min(tier.lev_min, lev_hi)
    if lev_hi < tier.lev_min:
        notes.append(f"El SL es amplio: el apalancamiento baja a {lev_hi}x para que la liquidación quede "
                     f"≥ {LIQ_SAFETY:g}× más lejos que el SL.")
    needed = math.ceil(notional / max_margin) if max_margin > 0 else lev_hi
    lev = int(min(max(needed, lev_lo), lev_hi))
    if notional / lev > max_margin + 1e-9:
        new_notional = max_margin * lev
        notes.append(f"La posición se recorta de {fmt_usd(notional)} a {fmt_usd(new_notional)} para respetar el "
                     f"margen máximo ({max_margin_pct:g}% = {fmt_usd(max_margin)}); el riesgo real baja a "
                     f"{fmt_usd(new_notional * sl_frac)}.")
        notional = new_notional
    margin = notional / lev
    fees = notional * fee * 2

    # Si el ATR amplía el SL, los TP se escalan en la misma proporción para conservar el R:R del nivel
    # (con TP fijos y SL ancho el R:R se hunde: p. ej. SL 1.23 % con TP1 0.40 % = R:R 1:0.3).
    scale = sl_frac / (tier.sl / 100.0)
    if scale > 1.0 + 1e-9:
        notes.append(f"Volatilidad alta: SL por ATR ({sl_frac * 100:.2f}%) → TPs escalados ×{scale:.2f} para "
                     f"mantener el R:R del Nivel {tier.key}.")
    tps = []
    for pct, close in zip(tier.tp, TP_SPLIT):
        pct = pct * max(1.0, scale)
        tps.append((entry * (1 + d * pct / 100.0), pct, close))
    tp_pnl = [notional * (close / 100.0) * (pct / 100.0) - fees * close / 100.0 for _, pct, close in tps]
    sl_pnl = -notional * sl_frac - fees
    rr_weighted = sum(pct * close / 100.0 for _, pct, close in tps) / (sl_frac * 100.0)
    rr_net = sum(tp_pnl) / abs(sl_pnl) if sl_pnl < 0 else float("inf")
    fee_share = fees / (notional * sl_frac) * 100
    if fee_share > 20:
        notes.append(f"Las comisiones taker ({fmt_usd(fees)}) son el {fee_share:.0f}% del riesgo: el R:R neto baja a "
                     f"1:{rr_net:.2f}. Entra con orden LÍMITE (maker ~0.02%) para recuperar casi todo ese margen.")

    options = []
    for L in sorted({tier.lev_min, (tier.lev_min + tier.lev_max) // 2, tier.lev_max, lev}):
        m = notional / L
        liq_L = liquidation(direction, entry, L)
        liq_dist = abs(entry - liq_L) / entry
        options.append(LeverageOption(
            leverage=L, margin=m,
            roi_tp1=(notional * tps[0][1] / 100 - fees) / m * 100,
            roi_tp3=(notional * tps[2][1] / 100 - fees) / m * 100,
            roi_sl=sl_pnl / m * 100,
            liquidation=liq_L, liq_ok=liq_dist >= LIQ_SAFETY * sl_frac - 1e-12,
            margin_ok=m <= max_margin + 1e-9, recommended=(L == lev),
        ))

    return Plan(
        coin=base_coin(coin), direction=direction, capital=capital, entry=entry, tier=tier, listed=listed,
        risk_pct=risk_pct, risk_usd=notional * sl_frac, max_margin_pct=max_margin_pct,
        sl_pct=sl_frac * 100, sl_source=sl_source, stop_loss=stop, tps=tps, leverage=lev, margin=margin,
        notional=notional, coins_qty=notional / entry, liquidation=liquidation(direction, entry, lev), fees=fees,
        tp_pnl=tp_pnl, sl_pnl=sl_pnl, rr_weighted=rr_weighted, rr_net=rr_net,
        breakeven_wr=1 / (1 + rr_net) * 100 if rr_net > 0 else 100.0,
        break_even=entry * (1 + d * 2 * fee),
        trailing=ATR_TRAIL_MULT * atr5 if atr5 and atr5 > 0 else None, options=options, notes=notes,
    )


# =============================================================================
# Módulo 4 · Filtros y bloqueos
# =============================================================================
@dataclass
class FilterCheck:
    name: str
    passed: bool
    detail: str


@dataclass
class FilterReport:
    direction: str
    checks: list[FilterCheck]
    blocks: list[str]
    score: int  # 0-100
    confidence: str  # Alta | Media | Baja

    @property
    def passed(self) -> int:
        return sum(c.passed for c in self.checks)

    @property
    def icons(self) -> str:
        return "".join("✅" if c.passed else "⬜" for c in self.checks)

    @property
    def enter(self) -> bool:
        return self.passed >= MIN_FILTERS and not self.blocks


def _v(row: pd.Series, col: str) -> float:
    try:
        v = float(row[col])
        return v if math.isfinite(v) else float("nan")
    except (KeyError, TypeError, ValueError):
        return float("nan")


def evaluate_filters(direction: str, df5: pd.DataFrame, df1h: pd.DataFrame | None, coin: str,
                     funding: float | None = None, order_book: dict | None = None,
                     news_blocked: str | None = None, now: datetime | None = None) -> FilterReport:
    """
    Los 6 filtros sobre la última vela cerrada de 5 min (+ tendencia de 1 h).
    df5 debe traer los indicadores de engine.indicators.compute_all.
    """
    d = 1 if direction == "LONG" else -1
    row = df5.iloc[-1]
    close = _v(row, "close")
    checks: list[FilterCheck] = []

    # 1. Tendencia de 1 hora (EMA 50)
    if df1h is not None and len(df1h) and "ema_50" in df1h.columns and pd.notna(df1h["ema_50"].iloc[-1]):
        c1, e1 = float(df1h["close"].iloc[-1]), float(df1h["ema_50"].iloc[-1])
        ok = c1 > e1 if d == 1 else c1 < e1
        checks.append(FilterCheck("Tendencia 1h (EMA 50)", ok,
                                  f"precio {'sobre' if c1 > e1 else 'bajo'} la EMA 50 de 1h"))
    else:
        checks.append(FilterCheck("Tendencia 1h (EMA 50)", False, "sin datos de 1h"))

    # 2. EMA 9 vs EMA 21 en 5 min
    e9, e21 = _v(row, "ema_9"), _v(row, "ema_21")
    ok = (e9 > e21) if d == 1 else (e9 < e21)
    checks.append(FilterCheck("EMA 9 / EMA 21 (5m)", bool(ok), f"EMA9 {'>' if e9 > e21 else '<'} EMA21"))

    # 3. VWAP de la sesión
    vw = _v(row, "vwap")
    ok = (close > vw) if d == 1 else (close < vw)
    checks.append(FilterCheck("VWAP de sesión", bool(ok), f"precio {'sobre' if close > vw else 'bajo'} el VWAP"))

    # 4. RSI(14) en zona sana
    rsi = _v(row, "rsi")
    lo, hi = (40, 65) if d == 1 else (35, 60)
    checks.append(FilterCheck(f"RSI 5m entre {lo} y {hi}", lo <= rsi <= hi, f"RSI {rsi:.1f}"))

    # 5. Volumen de la vela de señal
    rv = _v(row, "rvol")
    checks.append(FilterCheck("Volumen > 1.5× media 20", rv > 1.5, f"{rv:.2f}× la media" if rv == rv else "sin volumen"))

    # 6. Funding neutro y open interest acompañando
    oi = _v(row, "oi_change_pct")
    if funding is None:
        checks.append(FilterCheck("Funding neutro + OI", False, "sin datos de funding (solo exchanges de futuros)"))
    else:
        f_ok = -0.0003 <= funding <= 0.0003
        oi_ok = oi > 0 if oi == oi else False
        checks.append(FilterCheck("Funding neutro + OI", f_ok and oi_ok,
                                  f"funding {funding * 100:.4f}% · OI {oi:+.2f}%" if oi == oi
                                  else f"funding {funding * 100:.4f}% · sin OI"))

    # Bloqueos
    blocks: list[str] = []
    if news_blocked:
        blocks.append(news_blocked)
    if order_book and order_book.get("spread_pct") is not None and order_book["spread_pct"] > 0.05:
        blocks.append(f"Spread {order_book['spread_pct']:.3f}% > 0.05%: liquidez insuficiente")
    atr = _v(row, "atr")
    o, h, lw = _v(row, "open"), _v(row, "high"), _v(row, "low")
    wick = max(h - max(o, close), min(o, close) - lw) if all(x == x for x in (o, h, lw)) else 0.0
    if atr == atr and atr > 0 and wick > 2 * atr:
        blocks.append(f"Mecha de la última vela ({wick / atr:.1f}× ATR) > 2× ATR: movimiento errático")
    tier, _ = tier_for(coin)
    now = now or datetime.now(timezone.utc)
    if tier.key == "C" and now.weekday() >= 5 and (rv != rv or rv < 1.0):
        blocks.append("Fin de semana con volumen bajo en una cripto de Nivel C")

    passed = sum(c.passed for c in checks)
    score = round(passed / len(checks) * 100)
    confidence = "Alta" if passed >= 5 else ("Media" if passed == 4 else "Baja")
    return FilterReport(direction, checks, blocks, score, confidence)


def best_direction(df5: pd.DataFrame, df1h: pd.DataFrame | None, coin: str, **kw) -> FilterReport:
    """Evalúa LONG y SHORT y devuelve la dirección con más filtros cumplidos."""
    rep_l = evaluate_filters("LONG", df5, df1h, coin, **kw)
    rep_s = evaluate_filters("SHORT", df5, df1h, coin, **kw)
    return rep_l if rep_l.passed >= rep_s.passed else rep_s


# =============================================================================
# Módulo 3 · Gestión diaria
# =============================================================================
def daily_guard(trades_today: list[dict], capital: float) -> str | None:
    """Stop diario: 3 pérdidas seguidas o pérdida diaria ≥ 3 % del capital."""
    streak = 0
    for t in trades_today:  # ordenadas por hora
        streak = streak + 1 if (t.get("pnl") or 0) < 0 else 0
    net = sum(t.get("pnl") or 0.0 for t in trades_today)
    if streak >= DAILY_MAX_LOSSES:
        return f"Stop diario: {streak} pérdidas seguidas hoy. FG VIEW se detiene hasta mañana."
    if capital > 0 and net < 0 and abs(net) / capital * 100 >= DAILY_MAX_LOSS_PCT:
        return f"Stop diario: pérdida de hoy {fmt_usd(net)} (≥ {DAILY_MAX_LOSS_PCT:g}% del capital)."
    return None


def management_text(plan: Plan) -> str:
    trail = (f"trailing de {fmt_px(plan.trailing)} (0.5 × ATR 5m)" if plan.trailing else "trailing de 0.5 × ATR 5m")
    tp1, tp2 = fmt_px(plan.tps[0][0]), fmt_px(plan.tps[1][0])
    return "  \n".join([
        "🛠️ **Gestión de la posición**",
        f"1. **TP1** ${tp1}: cierra el 50% y mueve el SL a break-even (${fmt_px(plan.break_even)}).",
        f"2. **TP2** ${tp2}: cierra el 30% y mueve el SL a TP1 (${tp1}).",
        f"3. **TP3** ${fmt_px(plan.tps[2][0])}: cierra el 20% restante o déjalo correr con {trail}.",
        f"4. **Tiempo**: si en {MAX_HOLD_MIN} minutos no tocó el TP1, cierra a mercado.",
        f"5. **Stop diario**: {DAILY_MAX_LOSSES} pérdidas seguidas o −{DAILY_MAX_LOSS_PCT:g}% del capital → no operes más hoy.",
    ])


# =============================================================================
# Formato de salida
# =============================================================================
def format_recommendation(plan: Plan, rep: FilterReport | None = None) -> str:
    """Formato fijo de FG VIEW."""
    score = f"{rep.score}/100 ({rep.confidence})" if rep else "—"
    tp = plan.tps
    lines = [
        f"🟡 **FG VIEW | {plan.coin} {plan.direction}** | Confianza: **{score}** · {tier_label(plan.coin)}",
        f"Capital {fmt_usd(plan.capital)} | Riesgo {fmt_usd(plan.risk_usd)} ({plan.risk_usd / plan.capital * 100:.2f}%) | "
        f"{plan.leverage}x | Margen {fmt_usd(plan.margin)} | Posición {fmt_usd(plan.notional)}",
        f"Entrada ${fmt_px(plan.entry)} | SL ${fmt_px(plan.stop_loss)} (−{plan.sl_pct:.2f}%, {plan.sl_source}) | "
        f"Liquidación ${fmt_px(plan.liquidation)}",
        f"TP1 ${fmt_px(tp[0][0])} ({fmt_pct(tp[0][1])}) → {fmt_usd(plan.tp_pnl[0], True)} | "
        f"TP2 ${fmt_px(tp[1][0])} → {fmt_usd(plan.tp_pnl[1], True)} | TP3 ${fmt_px(tp[2][0])} → {fmt_usd(plan.tp_pnl[2], True)}",
        f"Pérdida máxima {fmt_usd(plan.sl_pnl, True)} | R:R 1:{plan.rr_weighted:.2f} (neto 1:{plan.rr_net:.2f}) | "
        f"Win rate de equilibrio {plan.breakeven_wr:.1f}% | Tiempo máximo {MAX_HOLD_MIN} minutos",
    ]
    if rep:
        lines.append(f"Filtros: {rep.icons} ({rep.passed}/6)")
        if rep.blocks:
            lines.append("⛔ **NO OPERAR:** " + " · ".join(rep.blocks))
        elif not rep.enter:
            lines.append(f"⏸️ **Esperar:** solo {rep.passed} de 6 filtros (mínimo {MIN_FILTERS}).")
        elif plan.rr_net < MIN_RR_NET:
            lines.append(f"⏸️ **Esperar:** R:R neto 1:{plan.rr_net:.2f} < 1:{MIN_RR_NET:g} (las comisiones no compensan).")
        else:
            lines.append(f"✅ **Entrada válida** ({rep.passed} de 6 filtros).")
    for n in plan.notes:
        lines.append(f"_{n}_")
    lines.append("_Simulación educativa, no es asesoría financiera._")
    return "  \n".join(lines)


def options_table(plan: Plan) -> pd.DataFrame:
    """Tabla de apalancamientos del nivel (Módulo 1)."""
    rows = []
    for o in plan.options:
        status = "⭐ recomendado" if o.recommended else ("✅" if o.liq_ok and o.margin_ok else
                                                         ("⚠️ liquidación cerca" if not o.liq_ok else "⚠️ margen alto"))
        rows.append({
            "Apalanc.": f"{o.leverage}x", "Margen": fmt_usd(o.margin), "Posición": fmt_usd(plan.notional),
            "SL": f"${fmt_px(plan.stop_loss)}", "TP1": f"${fmt_px(plan.tps[0][0])}", "TP2": f"${fmt_px(plan.tps[1][0])}",
            "TP3": f"${fmt_px(plan.tps[2][0])}", "Ganancia TP3 total": fmt_usd(sum(plan.tp_pnl), True),
            "Pérdida SL": fmt_usd(plan.sl_pnl, True), "ROI TP1": f"{o.roi_tp1:+.1f}%", "ROI SL": f"{o.roi_sl:+.1f}%",
            "Liquidación": f"${fmt_px(o.liquidation)}", "Estado": status,
        })
    return pd.DataFrame(rows)


# =============================================================================
# Módulo 6 · Métricas de la bitácora
# =============================================================================
def journal_metrics(trades: list[dict]) -> dict:
    """Win rate, profit factor, expectativa, drawdown máximo y racha actual."""
    closed = [t for t in trades if t.get("pnl") is not None]
    if not closed:
        return {"trades": 0}
    pnl = np.array([float(t["pnl"]) for t in closed])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    equity = np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[0.0], equity]))[1:]
    max_dd = float((peak - equity).max()) if len(equity) else 0.0
    streak, kind = 0, None
    for v in pnl[::-1]:
        k = "W" if v > 0 else "L"
        if kind in (None, k):
            kind, streak = k, streak + 1
        else:
            break
    return {
        "trades": len(closed),
        "win_rate": float((pnl > 0).mean() * 100),
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf"),
        "expectancy": float(pnl.mean()),
        "total": float(pnl.sum()),
        "max_drawdown": max_dd,
        "streak": f"{streak} {'ganadoras' if kind == 'W' else 'perdedoras'} seguidas",
    }


def filter_effectiveness(trades: list[dict]) -> pd.DataFrame:
    """Para cada filtro: win rate cuando se cumplió vs cuando no (qué filtros predicen mejor)."""
    rows = []
    names = sorted({n for t in trades for n in (t.get("filters") or {})})
    for name in names:
        on = [t for t in trades if (t.get("filters") or {}).get(name) is True and t.get("pnl") is not None]
        off = [t for t in trades if (t.get("filters") or {}).get(name) is False and t.get("pnl") is not None]
        wr = lambda xs: (sum(1 for t in xs if t["pnl"] > 0) / len(xs) * 100) if xs else float("nan")  # noqa: E731
        rows.append({"Filtro": name, "Operaciones con filtro": len(on), "Win rate con filtro %": round(wr(on), 1),
                     "Operaciones sin filtro": len(off), "Win rate sin filtro %": round(wr(off), 1)})
    return pd.DataFrame(rows)


# =============================================================================
# Datos: velas de 5 min + 1 h para el scalping
# =============================================================================
@dataclass
class ScalpContext:
    df5: pd.DataFrame
    df1h: pd.DataFrame | None
    funding: float | None
    order_book: dict | None
    source: str
    warnings: list[str] = field(default_factory=list)

    @property
    def price(self) -> float:
        return float(self.df5["close"].iloc[-1])

    @property
    def atr5(self) -> float | None:
        v = self.df5["atr"].iloc[-1] if "atr" in self.df5.columns else float("nan")
        return float(v) if pd.notna(v) and v > 0 else None


def scalp_context(feeder, symbol: str, market: str, contract: str = "USDT-M", exchange: str | None = None,
                  force_refresh: bool = False) -> ScalpContext:
    """Descarga y calcula indicadores en 5 min (señal) y 1 h (tendencia)."""
    from engine.indicators import compute_all

    d5 = feeder.get_market_data(symbol, market, "5m", contract, exchange, 300, force_refresh)
    df5 = compute_all(d5.df, intraday=True, open_interest=d5.open_interest)
    df1h, warnings = None, list(d5.warnings)
    try:
        d1 = feeder.get_market_data(symbol, market, "1h", contract, exchange, 200, force_refresh)
        df1h = compute_all(d1.df, intraday=True)
    except Exception as exc:  # noqa: BLE001 - el filtro de 1h queda sin datos
        warnings.append(f"Sin datos de 1h: {exc}")
    return ScalpContext(df5, df1h, d5.funding_rate, d5.order_book, d5.source, warnings)
