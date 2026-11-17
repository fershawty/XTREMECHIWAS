"""
engine/position_simulator.py
============================
Simulador de operaciones LONG / SHORT en futuros perpetuos USDT-M.

Calcula, con números exactos, qué pasa con tu capital en cada salida:
ganancia en el Take Profit (con TP1/TP2/TP3 parciales), pérdida en el Stop Loss,
ROI sobre el margen, impacto en la cuenta, precio de liquidación, R:R, win rate
mínimo, break-even, tabla de escenarios y comparación de apalancamientos.
Incluye la proyección de varias operaciones con interés compuesto (Monte Carlo).

Fórmulas (las mismas que verías en Binance Futures, margen aislado):
    Nocional            = Margen × Apalancamiento
    Monedas             = Nocional ÷ Entrada
    Movimiento LONG     = (Salida − Entrada) ÷ Entrada
    Movimiento SHORT    = (Entrada − Salida) ÷ Entrada
    PnL bruto           = Nocional × Movimiento
    Comisiones          = Nocional × comisión × 2
    PnL neto            = PnL bruto − Comisiones
    ROI                 = PnL neto ÷ Margen × 100
    Impacto en cuenta   = PnL neto ÷ Capital × 100
    Liquidación LONG    = Entrada × (1 − 1/Apalancamiento + 0.005)
    Liquidación SHORT   = Entrada × (1 + 1/Apalancamiento − 0.005)
    R:R                 = Ganancia en TP ÷ Pérdida en SL
    Win rate mínimo     = 1 ÷ (1 + R:R)
    Riesgo fijo         Nocional = (Capital × Riesgo%) ÷ Distancia% al SL

Este módulo no descarga precios: nunca inventa datos. El precio de entrada lo
pone el usuario o sale del análisis en vivo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

MAINTENANCE_MARGIN = 0.005  # 0.5 % (aproximación de margen aislado)
DEFAULT_FEE = 0.0005  # 0.05 % taker por lado
DISCLAIMER = "Simulación educativa, no es asesoría financiera."

# =============================================================================
# Catálogo de criptos
# =============================================================================
CRYPTO_CATALOG: dict[str, list[str]] = {
    "Major": ["BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "TRX", "TON", "AVAX", "DOT", "LINK", "LTC", "BCH",
              "NEAR", "ATOM"],
    "Layer 1 / Layer 2": ["SUI", "APT", "SEI", "INJ", "TIA", "ARB", "OP", "POL", "HYPE", "S"],
    "IA y narrativas": ["FET", "RENDER", "TAO", "WLD", "ONDO", "ENA", "JUP", "PENDLE"],
    "Memecoins": ["DOGE", "SHIB", "PEPE", "WIF", "BONK", "FLOKI", "TRUMP", "POPCAT"],
}
MEMECOINS = set(CRYPTO_CATALOG["Memecoins"])
ALL_COINS = [c for coins in CRYPTO_CATALOG.values() for c in coins]
NON_ALTCOINS = {"BTC", "ETH"}  # Para la regla de apalancamiento en altcoins


def coin_category(coin: str) -> str | None:
    """Categoría de la moneda en el catálogo (None si está fuera de la lista)."""
    coin = base_coin(coin)
    for cat, coins in CRYPTO_CATALOG.items():
        if coin in coins:
            return cat
    return None


def base_coin(symbol: str) -> str:
    """'pepe/usdt' -> 'PEPE'."""
    s = symbol.upper().strip().split(":")[0]
    for sep in ("/", "-", "_"):
        s = s.split(sep)[0]
    for quote in ("USDT", "USDC", "USD"):
        if s.endswith(quote) and len(s) > len(quote):
            s = s[: -len(quote)]
    return s


# =============================================================================
# Formato de números
# =============================================================================
def fmt_usd(v: float, sign: bool = False) -> str:
    """Dólares a 2 decimales: +$1,234.56 / −$12.30."""
    if v is None or not math.isfinite(v):
        return "—"
    s = f"${abs(v):,.2f}"
    if sign:
        return ("+" if v >= 0 else "−") + s
    return ("−" if v < 0 else "") + s


def fmt_px(v: float) -> str:
    """Precio con los decimales que necesita la moneda (memecoins con todos sus decimales)."""
    if v is None or not math.isfinite(v):
        return "—"
    a = abs(v)
    if a >= 1000:
        return f"{v:,.2f}"
    if a >= 1:
        return f"{v:,.4f}"
    if a == 0:
        return "0"
    decimals = min(12, -int(math.floor(math.log10(a))) + 5)  # ~6 cifras significativas
    return f"{v:.{decimals}f}"


def fmt_coins(v: float) -> str:
    """Cantidad de monedas legible: 10,988,950 / 0.01 / 3.2541."""
    if v >= 1000:
        return f"{v:,.0f}"
    if v >= 1:
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    return f"{v:.8f}".rstrip("0").rstrip(".")


def fmt_pct(v: float, sign: bool = True, digits: int = 2) -> str:
    """Porcentaje: +12.34% / −3.10%."""
    if v is None or not math.isfinite(v):
        return "—"
    s = f"{abs(v):.{digits}f}%"
    if sign:
        return ("+" if v >= 0 else "−") + s
    return s


# =============================================================================
# Entradas y resultados
# =============================================================================
@dataclass
class TakeProfit:
    """Nivel de Take Profit con el % de la posición que cierra."""

    price: float
    close_pct: float = 100.0


@dataclass
class SimInput:
    """Datos de la operación."""

    coin: str
    direction: str  # "LONG" | "SHORT"
    capital: float
    leverage: float
    entry: float
    stop_loss: float
    take_profits: list[TakeProfit]
    margin: float | None = None  # Modo margen fijo
    risk_pct: float | None = None  # Modo riesgo fijo (% del capital que se pierde en el SL)
    fee: float = DEFAULT_FEE  # Por lado (0.0005 = 0.05 %)


@dataclass
class ExitResult:
    """Resultado de cerrar (total o parcialmente) en un precio."""

    label: str
    price: float
    move_pct: float  # Movimiento del precio a favor (+) o en contra (−)
    close_pct: float
    pnl_net: float
    roi_pct: float
    account_pct: float


@dataclass
class SimResult:
    """Simulación completa."""

    inp: SimInput
    mode: str  # "margen" | "riesgo"
    margin: float
    notional: float
    coins: float
    liquidation: float
    fees: float
    break_even: float
    tps: list[ExitResult]
    tp_total: ExitResult
    sl: ExitResult
    rr: float
    min_winrate_pct: float
    sl_liquidated: bool
    scenarios: list[dict]
    leverage_table: list[dict]
    alerts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# =============================================================================
# Cálculo
# =============================================================================
def liquidation_price(direction: str, entry: float, leverage: float, mmr: float = MAINTENANCE_MARGIN) -> float:
    """Liquidación aproximada en margen aislado."""
    if direction == "LONG":
        return entry * (1.0 - 1.0 / leverage + mmr)
    return entry * (1.0 + 1.0 / leverage - mmr)


def move_pct(direction: str, entry: float, exit_price: float) -> float:
    """Movimiento del precio a favor de la posición (fracción)."""
    if direction == "LONG":
        return (exit_price - entry) / entry
    return (entry - exit_price) / entry


def _crosses_liq(direction: str, price: float, liq: float) -> bool:
    return price <= liq if direction == "LONG" else price >= liq


def validate(inp: SimInput) -> list[str]:
    """Errores que impiden calcular (datos faltantes o incoherentes)."""
    errs = []
    if inp.direction not in ("LONG", "SHORT"):
        errs.append("Dirección debe ser LONG o SHORT.")
    if not inp.capital or inp.capital <= 0:
        errs.append("Falta el capital total (USDT).")
    if not inp.entry or inp.entry <= 0:
        errs.append("Falta el precio de entrada.")
    if not inp.leverage or not (1 <= inp.leverage <= 125):
        errs.append("El apalancamiento debe estar entre 1x y 125x.")
    if inp.margin is None and inp.risk_pct is None:
        errs.append("Indica el margen por operación o el % de riesgo.")
    if not inp.stop_loss or inp.stop_loss <= 0:
        errs.append("Falta el Stop Loss.")
    if not inp.take_profits:
        errs.append("Falta al menos un Take Profit.")
    if errs:
        return errs
    d, e = inp.direction, inp.entry
    if (d == "LONG" and inp.stop_loss >= e) or (d == "SHORT" and inp.stop_loss <= e):
        errs.append(f"En un {d} el Stop Loss debe estar {'por debajo' if d == 'LONG' else 'por encima'} de la entrada.")
    for i, tp in enumerate(inp.take_profits, 1):
        if (d == "LONG" and tp.price <= e) or (d == "SHORT" and tp.price >= e):
            errs.append(f"En un {d} el TP{i} debe estar {'por encima' if d == 'LONG' else 'por debajo'} de la entrada.")
    if inp.margin is not None and inp.margin <= 0:
        errs.append("El margen debe ser mayor que 0.")
    if inp.risk_pct is not None and inp.margin is None and inp.risk_pct <= 0:
        errs.append("El % de riesgo debe ser mayor que 0.")
    return errs


def _position(inp: SimInput, leverage: float) -> tuple[str, float, float]:
    """(modo, margen, nocional) para un apalancamiento dado."""
    if inp.margin is not None:
        margin = float(inp.margin)
        return "margen", margin, margin * leverage
    sl_dist = abs(inp.entry - inp.stop_loss) / inp.entry
    notional = inp.capital * (inp.risk_pct / 100.0) / sl_dist
    return "riesgo", notional / leverage, notional


def _exit(label: str, inp: SimInput, price: float, notional: float, margin: float, fees: float,
          close_pct: float = 100.0, liq: float | None = None) -> ExitResult:
    """PnL neto de cerrar `close_pct` % de la posición en `price` (comisiones proporcionales)."""
    frac = close_pct / 100.0
    mv = move_pct(inp.direction, inp.entry, price)
    pnl = notional * frac * mv - fees * frac
    if liq is not None and _crosses_liq(inp.direction, price, liq):
        pnl = -margin * frac  # Aislado: como máximo pierdes el margen
    return ExitResult(label, price, mv * 100.0, close_pct, pnl, pnl / margin * 100.0, pnl / inp.capital * 100.0)


def simulate(inp: SimInput) -> SimResult:
    """Simula la operación completa. Lanza ValueError si faltan datos."""
    errs = validate(inp)
    if errs:
        raise ValueError(" ".join(errs))
    d, e = inp.direction, inp.entry
    mode, margin, notional = _position(inp, inp.leverage)
    coins = notional / e
    liq = liquidation_price(d, e, inp.leverage)
    fees = notional * inp.fee * 2.0
    break_even = e * (1 + 2 * inp.fee) if d == "LONG" else e * (1 - 2 * inp.fee)

    # Take Profits parciales: si no suman 100 %, el resto se cierra en el último TP
    tps_in = [TakeProfit(tp.price, max(0.0, tp.close_pct)) for tp in inp.take_profits]
    total_pct = sum(tp.close_pct for tp in tps_in)
    notes = []
    if total_pct <= 0:
        tps_in[-1].close_pct, total_pct = 100.0, 100.0
    if total_pct > 100.0:
        tps_in = [TakeProfit(tp.price, tp.close_pct * 100.0 / total_pct) for tp in tps_in]
        notes.append("Los % de cierre de los TP sumaban más de 100 %: se reescalaron.")
    elif total_pct < 100.0:
        tps_in[-1].close_pct += 100.0 - total_pct
        notes.append(f"El {100.0 - total_pct:.0f} % restante se cierra en el último TP.")
    tps = [_exit(f"TP{i}", inp, tp.price, notional, margin, fees, tp.close_pct) for i, tp in enumerate(tps_in, 1)]
    tp_pnl = sum(t.pnl_net for t in tps)
    avg_tp = sum(tp.price * tp.close_pct for tp in tps_in) / 100.0
    tp_total = ExitResult("TP", avg_tp, move_pct(d, e, avg_tp) * 100.0, 100.0, tp_pnl,
                          tp_pnl / margin * 100.0, tp_pnl / inp.capital * 100.0)

    sl_liquidated = _crosses_liq(d, inp.stop_loss, liq)
    sl = _exit("SL", inp, inp.stop_loss, notional, margin, fees, 100.0, liq)
    loss = abs(sl.pnl_net) if sl.pnl_net < 0 else 0.0
    rr = tp_pnl / loss if loss > 0 else float("inf")
    min_wr = 1.0 / (1.0 + rr) * 100.0 if math.isfinite(rr) and rr > 0 else 0.0

    scenarios = []
    for m in (-10, -5, -2, 2, 5, 10):
        price = e * (1 + m / 100.0) if d == "LONG" else e * (1 - m / 100.0)
        ex = _exit(f"{m:+d}%", inp, price, notional, margin, fees, 100.0, liq)
        scenarios.append({"Movimiento": f"{'+' if m > 0 else '−'}{abs(m)}%", "Precio": price, "PnL": ex.pnl_net, "ROI": ex.roi_pct,
                          "Cuenta": ex.account_pct, "Liquidado": _crosses_liq(d, price, liq)})

    lev_table = []
    for lev in (5, 10, 20, 50):
        _, m_lev, n_lev = _position(inp, lev)
        liq_lev = liquidation_price(d, e, lev)
        fees_lev = n_lev * inp.fee * 2.0
        gain = sum(_exit("", inp, tp.price, n_lev, m_lev, fees_lev, tp.close_pct).pnl_net for tp in tps_in)
        sl_l = _exit("", inp, inp.stop_loss, n_lev, m_lev, fees_lev, 100.0, liq_lev)
        lev_table.append({"Apalancamiento": f"{lev}x", "Margen": m_lev, "Posición": n_lev, "Ganancia TP": gain,
                          "Pérdida SL": sl_l.pnl_net, "Liquidación": liq_lev,
                          "Liquida antes del SL": _crosses_liq(d, inp.stop_loss, liq_lev)})

    if mode == "riesgo":
        notes.append(f"Riesgo fijo {inp.risk_pct:g}%: la pérdida real en el SL incluye comisiones "
                     f"({fmt_usd(fees)}), por eso es algo mayor que el {inp.risk_pct:g}% exacto.")
    res = SimResult(inp, mode, margin, notional, coins, liq, fees, break_even, tps, tp_total, sl, rr, min_wr,
                    sl_liquidated, scenarios, lev_table, notes=notes)
    res.alerts = risk_alerts(res)
    return res


def risk_alerts(r: SimResult) -> list[str]:
    """Reglas de riesgo: avisos cuando se rompen."""
    inp, alerts = r.inp, []
    coin = base_coin(inp.coin)
    if r.sl_liquidated:
        safe = max(1, math.floor(1.0 / (abs(inp.entry - inp.stop_loss) / inp.entry * 1.1 + MAINTENANCE_MARGIN)))
        alerts.append(f"Te liquidan ANTES de tocar el SL (liquidación {fmt_px(r.liquidation)}). "
                      f"Baja el apalancamiento a ≤ {safe}x o acerca el SL.")
    loss_pct = abs(r.sl.account_pct) if r.sl.pnl_net < 0 else 0.0
    if loss_pct > 2.0:
        alerts.append(f"Riesgo alto: en el SL pierdes el {loss_pct:.2f}% de tu capital (máximo recomendado 2%).")
    if math.isfinite(r.rr) and r.rr < 1.5:
        alerts.append(f"R:R de 1:{r.rr:.2f}: la operación no compensa el riesgo (mínimo recomendado 1:1.5).")
    if coin in MEMECOINS and inp.leverage > 10:
        alerts.append(f"{inp.leverage:g}x en una memecoin: alto riesgo de liquidación por mechas (máx. recomendado 10x).")
    elif coin not in NON_ALTCOINS and coin not in MEMECOINS and inp.leverage > 20:
        alerts.append(f"{inp.leverage:g}x en una altcoin: alto riesgo de liquidación por mechas (máx. recomendado 20x).")
    if r.margin > inp.capital:
        alerts.append(f"El margen necesario ({fmt_usd(r.margin)}) supera tu capital ({fmt_usd(inp.capital)}).")
    return alerts


def coin_notes(coin: str) -> list[str]:
    """Avisos de liquidez/volatilidad de la moneda."""
    c = base_coin(coin)
    if c in MEMECOINS:
        return [f"{c} es una memecoin: se mueve entre 10% y 30% al día. Volatilidad extrema."]
    if coin_category(c) is None:
        return [f"{c} está fuera de la lista principal: revisa su liquidez y spread antes de operar."]
    return []


# =============================================================================
# Informe con el formato estándar
# =============================================================================
def format_report(r: SimResult) -> str:
    """Informe en Markdown con el formato fijo del simulador."""
    inp = r.inp
    coin = base_coin(inp.coin)
    lines = [
        f"📊 **{coin} — {inp.direction}**",
        f"Capital: {fmt_usd(inp.capital)} | Margen: {fmt_usd(r.margin)} | Apalancamiento: {inp.leverage:g}x | "
        f"Posición: {fmt_usd(r.notional)} ({fmt_coins(r.coins)} monedas)",
        f"Entrada: ${fmt_px(inp.entry)} | Liquidación: ${fmt_px(r.liquidation)} | Break-even: ${fmt_px(r.break_even)}",
        "",
    ]
    if len(r.tps) > 1:
        lines.append(f"✅ **Take Profit (precio medio): ${fmt_px(r.tp_total.price)} ({fmt_pct(r.tp_total.move_pct)})**")
        for t in r.tps:
            lines.append(f"- {t.label} ${fmt_px(t.price)} ({fmt_pct(t.move_pct)}) · cierra {t.close_pct:.0f}% → "
                         f"{fmt_usd(t.pnl_net, True)}")
        lines.append("")  # Cierra la lista antes del resumen
    else:
        lines.append(f"✅ **Take Profit: ${fmt_px(r.tp_total.price)} ({fmt_pct(r.tp_total.move_pct)})**")
    lines += [
        f"Ganancia neta: {fmt_usd(r.tp_total.pnl_net, True)} | ROI: {fmt_pct(r.tp_total.roi_pct)} | "
        f"Cuenta: {fmt_pct(r.tp_total.account_pct)}",
        "",
        f"❌ **Stop Loss: ${fmt_px(r.sl.price)} ({fmt_pct(r.sl.move_pct)})**"
        + (" — ⚠️ LIQUIDADO ANTES" if r.sl_liquidated else ""),
        f"Pérdida neta: {fmt_usd(r.sl.pnl_net, True)} | ROI: {fmt_pct(r.sl.roi_pct)} | Cuenta: {fmt_pct(r.sl.account_pct)}",
        "",
        f"⚖️ R:R = 1:{r.rr:.2f} | Win rate mínimo: {r.min_winrate_pct:.1f}% | Comisiones: {fmt_usd(r.fees)}",
        "",
        "📈 **Tabla de escenarios** (mismo capital y apalancamiento):",
        "",
        "| Movimiento | Precio | PnL $ | ROI % | Cuenta % |",
        "|---|---|---|---|---|",
    ]
    for s in r.scenarios:
        tag = " 💀 liquidado" if s["Liquidado"] else ""
        lines.append(f"| {s['Movimiento']} | ${fmt_px(s['Precio'])} | {fmt_usd(s['PnL'], True)}{tag} | "
                     f"{fmt_pct(s['ROI'])} | {fmt_pct(s['Cuenta'])} |")
    same = "mismo riesgo: cambia el margen" if r.mode == "riesgo" else "mismo margen"
    lines += [
        "",
        f"🔁 **Comparación de apalancamiento** (mismo TP y SL, {same}):",
        "",
        "| Apalanc. | Margen | Ganancia TP | Pérdida SL | Liquidación |",
        "|---|---|---|---|---|",
    ]
    for row in r.leverage_table:
        warn = " ⚠️ antes del SL" if row["Liquida antes del SL"] else ""
        lines.append(f"| {row['Apalancamiento']} | {fmt_usd(row['Margen'])} | {fmt_usd(row['Ganancia TP'], True)} | "
                     f"{fmt_usd(row['Pérdida SL'], True)} | ${fmt_px(row['Liquidación'])}{warn} |")
    extra = coin_notes(inp.coin)
    if r.alerts or extra:
        lines += ["", "⚠️ **Alertas:**"] + [f"- {a}" for a in r.alerts + extra]
    if r.notes:
        lines += ["", *[f"_{n}_" for n in r.notes]]
    lines += ["", f"_{DISCLAIMER}_"]
    # Salto de línea duro de Markdown ("  ") en las líneas de texto (no en tablas ni listas)
    return "\n".join(ln + "  " if ln and not ln.startswith(("|", "- ")) else ln for ln in lines)


# =============================================================================
# Proyección de varias operaciones (interés compuesto)
# =============================================================================
@dataclass
class Projection:
    """Evolución del capital tras N operaciones."""

    expected: float  # Mediana (escenario esperado)
    good: float  # Percentil 90
    bad: float  # Percentil 10
    mean_final: float
    prob_loss_pct: float  # Probabilidad de terminar con menos capital
    max_dd_median_pct: float
    max_dd_p90_pct: float  # Drawdown máximo probable (peor 10 %)
    ev_per_trade_pct: float  # Esperanza por operación (% del capital)
    bands: np.ndarray  # (3, N+1): percentiles 10 / 50 / 90 por operación


def project(capital: float, winrate_pct: float, rr: float, risk_pct: float, n_trades: int,
            fee_pct_of_risk: float = 0.0, paths: int = 4000, seed: int = 7) -> Projection:
    """
    Monte Carlo: cada operación gana `risk% × R:R` o pierde `risk%` del capital ACTUAL
    (interés compuesto). Devuelve los escenarios esperado, bueno y malo y el drawdown.
    """
    if capital <= 0 or not (0 < winrate_pct < 100) or rr <= 0 or risk_pct <= 0 or n_trades < 1:
        raise ValueError("Datos de proyección no válidos.")
    rng = np.random.default_rng(seed)
    p, r = winrate_pct / 100.0, risk_pct / 100.0
    wins = rng.random((paths, n_trades)) < p
    rets = np.where(wins, r * rr, -r) - r * fee_pct_of_risk
    equity = capital * np.cumprod(1.0 + rets, axis=1)
    equity = np.hstack([np.full((paths, 1), capital), equity])
    peak = np.maximum.accumulate(equity, axis=1)
    dd = ((peak - equity) / peak).max(axis=1) * 100.0
    final = equity[:, -1]
    return Projection(
        expected=float(np.median(final)), good=float(np.percentile(final, 90)), bad=float(np.percentile(final, 10)),
        mean_final=float(final.mean()), prob_loss_pct=float((final < capital).mean() * 100.0),
        max_dd_median_pct=float(np.median(dd)), max_dd_p90_pct=float(np.percentile(dd, 90)),
        ev_per_trade_pct=(p * r * rr - (1 - p) * r) * 100.0,
        bands=np.percentile(equity, [10, 50, 90], axis=0),
    )


def format_projection(capital: float, winrate_pct: float, rr: float, risk_pct: float, n: int, pr: Projection) -> str:
    """Resumen en Markdown de la proyección."""
    def line(name: str, v: float) -> str:
        return f"| {name} | {fmt_usd(v)} | {fmt_pct((v / capital - 1) * 100)} |"

    ev = pr.ev_per_trade_pct
    verdict = "✅ Ventaja positiva" if ev > 0 else "❌ Ventaja negativa: con estos números pierdes a largo plazo"
    return "\n".join([
        f"🧮 **Proyección de {n} operaciones** · win rate {winrate_pct:.1f}% · R:R 1:{rr:.2f} · riesgo {risk_pct:g}% por operación",
        "",
        "| Escenario | Capital final | Rendimiento |",
        "|---|---|---|",
        line("Malo (peor 10 %)", pr.bad),
        line("Esperado (mediana)", pr.expected),
        line("Bueno (mejor 10 %)", pr.good),
        "",
        f"Esperanza por operación: {fmt_pct(ev)} del capital · {verdict}",
        f"Drawdown máximo: {pr.max_dd_median_pct:.1f}% típico · {pr.max_dd_p90_pct:.1f}% en el peor 10 % de los casos",
        f"Probabilidad de terminar en pérdidas: {pr.prob_loss_pct:.1f}%",
        "",
        f"_{DISCLAIMER}_",
    ]).replace("\n", "  \n").replace("|  \n", "|\n")
