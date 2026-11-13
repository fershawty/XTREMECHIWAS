"""
ui/components.py
================
Piezas visuales de la interfaz (Streamlit + Plotly). Diseño minimalista:
la información importante a la vista, el detalle en pestañas.

Funciones principales:
    load_css()                   -> aplica ui/styles.css
    render_header()              -> cabecera con precio, fuente y build
    build_price_chart()          -> gráfico de velas estilo TradingView con zonas y señales
    render_signal_card()         -> tarjeta LONG/SHORT con score, razón y niveles
    render_factors()             -> desglose del score por factor
    render_trade_terminal()      -> ticket de orden estilo exchange de futuros (solo recomendación)
    render_context()             -> contexto económico / social / político / cultural
    render_tradingview_widget()  -> gráfico oficial de TradingView en vivo
    render_backtest_panel(), render_history(), render_news()
"""

from __future__ import annotations

import html
import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from plotly.subplots import make_subplots

from engine.config import APP_NAME, BUILD_TAG, SETTINGS

CSS_PATH = Path(__file__).with_name("styles.css")

C = {
    "bg": "#0E1117", "panel": "#12161F", "grid": "#161B26", "line": "#1F2633", "text": "#E6EDF3",
    "muted": "#7D8590", "faint": "#4A5361", "long": "#00FF88", "short": "#FF3B30",
    "ema50": "#F5C542", "ema200": "#4DA3FF", "vwap": "#B98CFF", "st": "#8B949E",
}

DEFAULT_LAYERS = {
    "ema": True, "vwap": True, "order_blocks": True, "fvg": True, "structure": True,
    "levels": True, "signals": True, "plan": True, "supertrend": False, "bollinger": False,
    "volume_profile": False,
}
LAYER_LABELS = {
    "ema": "EMA 50 / 200", "vwap": "VWAP", "order_blocks": "Order Blocks", "fvg": "Fair Value Gaps",
    "structure": "BOS / CHoCH", "levels": "Soportes / Resistencias", "signals": "Señales históricas",
    "plan": "Entrada / SL / TP", "supertrend": "SuperTrend", "bollinger": "Bandas de Bollinger",
    "volume_profile": "Volume Profile (POC/VAH/VAL)",
}


# =============================================================================
# Utilidades
# =============================================================================
def load_css() -> None:
    """Inyecta la hoja de estilos (si falta el archivo, la app sigue funcionando)."""
    try:
        st.markdown(f"<style>{CSS_PATH.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)
    except OSError:
        pass


def esc(text: object) -> str:
    """Escapa texto para HTML (evita que un titular rompa el diseño)."""
    return html.escape(str(text), quote=True)


def fmt_price(value: float | None, ref: float | None = None) -> str:
    """Formatea precios con decimales según su magnitud."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "—"
    ref = abs(ref if ref else value) or 1.0
    decimals = 2 if ref >= 100 else 3 if ref >= 10 else 4 if ref >= 1 else 6
    return f"{value:,.{decimals}f}"


def fmt_money(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return f"{value:,.2f}"


def _html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def _display_index(index: pd.DatetimeIndex, market: str) -> pd.DatetimeIndex:
    """Horas del gráfico: Nueva York para acciones/ETF/CME, UTC para cripto."""
    tz = "America/New_York" if market in ("stock", "etf", "cme") else "UTC"
    return index.tz_convert(tz).tz_localize(None)


def _rangebreaks(market: str, timeframe: str) -> list[dict]:
    """Oculta fines de semana y horas sin mercado (como TradingView)."""
    if market in ("stock", "etf"):
        breaks = [dict(bounds=["sat", "mon"])]
        if timeframe != "1D":
            breaks.append(dict(bounds=[16, 9.5], pattern="hour"))
        return breaks
    if market == "cme":
        return [dict(bounds=["sat", "sun"])]
    return []


# =============================================================================
# Cabecera y avisos
# =============================================================================
def render_header(result=None) -> None:
    """Barra superior: marca, build, cotización y estado de la fuente."""
    quote = ""
    chips = []
    if result is not None and result.market_data is not None:
        md = result.market_data
        chg = md.change_pct
        cls = "up" if chg >= 0 else "down"
        quote = (f'<div class="fs-quote"><span class="fs-symbol">{esc(md.symbol.upper())} · {esc(md.timeframe)}</span>'
                 f'<span class="fs-price">{fmt_price(md.last_price)}</span>'
                 f'<span class="fs-change {cls}">{chg:+.2f}%</span></div>')
        kind = {"crypto": f"PERP {md.contract or ''}" if md.is_futures else "SPOT (fallback)",
                "cme": "CME", "demo": "PERP SIM"}.get(md.market.value, "CONTADO")
        chips.append(f'<span class="fs-chip {"demo" if md.source == "demo" else "live"}">{esc(md.source.upper())}</span>')
        chips.append(f'<span class="fs-chip">{esc(kind)}</span>')
        chips.append(f'<span class="fs-chip">{len(md.df)} velas</span>')
        chips.append(f'<span class="fs-chip">{md.last_bar_time.strftime("%d %b %H:%M")} UTC</span>')
        chips.append(f'<span class="fs-chip">{result.elapsed_ms:.0f} ms</span>')
    _html(
        f'<div class="fs-header"><div class="fs-brand"><span class="fs-logo-dot"></span>'
        f'<span class="fs-logo">{APP_NAME}</span><span class="fs-build">{esc(BUILD_TAG)}</span></div>'
        f'{quote}<div class="fs-chips">{"".join(chips)}</div></div>'
    )


def render_banners(messages: list[str], kind: str = "warn") -> None:
    """Avisos compactos (deduplicados)."""
    seen = set()
    for msg in messages:
        if msg and msg not in seen:
            seen.add(msg)
            _html(f'<div class="fs-banner {kind}">{esc(msg)}</div>')


def render_empty(title: str, text: str) -> None:
    _html(f'<div class="fs-empty"><b>{esc(title)}</b>{esc(text)}</div>')


def render_footer() -> None:
    _html(
        f'<div class="fs-footer"><span>{APP_NAME} · {esc(BUILD_TAG)}</span>'
        '<span>Herramienta de análisis. No ejecuta órdenes ni es consejo financiero. '
        'Los futuros apalancados pueden liquidar tu cuenta.</span></div>'
    )


# =============================================================================
# Gráfico principal (estilo TradingView)
# =============================================================================
def build_price_chart(result, layers: dict | None = None, bars: int = 220, height: int = 660) -> go.Figure:
    """
    Velas + volumen + RSI con zonas SMC, EMAs, VWAP, señales y niveles del plan.
    Solo dibuja lo más reciente para no saturar.
    """
    layers = {**DEFAULT_LAYERS, **(layers or {})}
    full = result.df
    md = result.market_data
    market, tf = md.market.value, md.timeframe
    n_full = len(full)
    start = max(0, n_full - bars)
    df = full.iloc[start:]
    x = _display_index(df.index, market)
    x_full = _display_index(full.index, market)
    last_x = x[-1]

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.015, row_heights=[0.70, 0.12, 0.18])

    # --- Velas ---
    fig.add_trace(go.Candlestick(
        x=x, open=df["open"], high=df["high"], low=df["low"], close=df["close"], name="Precio",
        increasing=dict(line=dict(color=C["long"], width=1), fillcolor=C["long"]),
        decreasing=dict(line=dict(color=C["short"], width=1), fillcolor=C["short"]),
        whiskerwidth=0, showlegend=False,
    ), row=1, col=1)

    def line(col: str, name: str, color: str, width: float = 1.3, dash: str | None = None) -> None:
        if col in df.columns and df[col].notna().any():
            fig.add_trace(go.Scatter(x=x, y=df[col], name=name, mode="lines",
                                     line=dict(color=color, width=width, dash=dash), hoverinfo="skip"), row=1, col=1)

    if layers["ema"]:
        line("ema_50", "EMA 50", C["ema50"])
        line("ema_200", "EMA 200", C["ema200"], 1.6)
    if layers["vwap"]:
        line("vwap", "VWAP", C["vwap"], 1.1, "dot")
    if layers["bollinger"]:
        line("bb_upper", "BB sup", C["faint"], 0.8, "dash")
        line("bb_lower", "BB inf", C["faint"], 0.8, "dash")
    if layers["supertrend"] and "supertrend" in df.columns:
        for d, color in ((1, C["long"]), (-1, C["short"])):
            y = df["supertrend"].where(df["supertrend_dir"] == d)
            fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name=f"SuperTrend {'▲' if d == 1 else '▼'}",
                                     line=dict(color=color, width=1, dash="dot"), opacity=0.6, hoverinfo="skip"), row=1, col=1)

    st_res = result.structure

    def zx(i: int) -> pd.Timestamp:
        return x_full[min(max(i, start), n_full - 1)]

    # --- Order Blocks y FVG (solo vigentes y recientes) ---
    zone_specs = []
    if layers["order_blocks"] and st_res:
        zone_specs += [(z, "OB", 0.13) for d in (1, -1) for z in st_res.active_zones("OB", d, limit=3)]
    if layers["fvg"] and st_res:
        zone_specs += [(z, "FVG", 0.07) for d in (1, -1) for z in st_res.active_zones("FVG", d, limit=3)]
    for z, label, alpha in zone_specs:
        rgb = "0,255,136" if z.direction == 1 else "255,59,48"
        fig.add_shape(type="rect", x0=zx(z.origin_idx), x1=last_x, y0=z.bottom, y1=z.top, xref="x", yref="y",
                      fillcolor=f"rgba({rgb},{alpha})", line=dict(width=0 if label == "OB" else 1,
                      color=f"rgba({rgb},0.25)", dash="dot"), layer="below")
        fig.add_annotation(x=zx(z.origin_idx), y=z.top if z.direction == -1 else z.bottom, text=label,
                           showarrow=False, xanchor="left", yanchor="bottom" if z.direction == -1 else "top",
                           font=dict(size=9, color=f"rgba({rgb},0.8)", family="JetBrains Mono"), row=1, col=1)

    # --- BOS / CHoCH ---
    if layers["structure"] and st_res:
        for ev in [e for e in st_res.events if e.idx >= start][-6:]:
            color = C["long"] if ev.direction == 1 else C["short"]
            x0, x1 = zx(ev.swing_idx), zx(ev.idx)
            fig.add_shape(type="line", x0=x0, x1=x1, y0=ev.level, y1=ev.level, xref="x", yref="y",
                          line=dict(color=color, width=1, dash="dot"), opacity=0.7)
            fig.add_annotation(x=x1, y=ev.level, text=ev.kind, showarrow=False, xanchor="right",
                               yanchor="bottom" if ev.direction == 1 else "top",
                               font=dict(size=9, color=color, family="JetBrains Mono"), row=1, col=1)

    # --- Soportes / resistencias clave ---
    if layers["levels"] and st_res:
        for lv in st_res.key_levels:
            if lv.touches < 2:
                continue
            color = "rgba(0,255,136,0.35)" if lv.kind == "support" else "rgba(255,59,48,0.35)"
            fig.add_hline(y=lv.price, line=dict(color=color, width=1, dash="dash"), row=1, col=1)

    if layers["volume_profile"] and "vp_poc" in df.columns:
        last = df.iloc[-1]
        for col, name in (("vp_poc", "POC"), ("vp_vah", "VAH"), ("vp_val", "VAL")):
            if pd.notna(last[col]):
                fig.add_hline(y=float(last[col]), line=dict(color=C["muted"], width=1, dash="dot"),
                              annotation_text=name, annotation_position="left",
                              annotation_font=dict(size=9, color=C["muted"]), row=1, col=1)

    # --- Señales históricas (flanco de subida del score >= umbral) ---
    if layers["signals"] and "signal_dir" in df.columns:
        sd = df["signal_dir"]
        fresh = (sd != 0) & (sd != sd.shift(1))
        for d, symbol, color, ycol, offset in ((1, "triangle-up", C["long"], "low", -1), (-1, "triangle-down", C["short"], "high", 1)):
            mask = fresh & (sd == d)
            if mask.any():
                ys = df.loc[mask, ycol] + offset * df.loc[mask, "atr"].fillna(0) * 0.6
                scores = df.loc[mask, "signal_score"]
                fig.add_trace(go.Scatter(
                    x=x[mask.to_numpy()], y=ys, mode="markers", name="Señal LONG" if d == 1 else "Señal SHORT",
                    marker=dict(symbol=symbol, size=np.where(scores >= SETTINGS.score_alert, 13, 9), color=color,
                                line=dict(width=0)),
                    customdata=scores, hovertemplate="Score %{customdata:.0f}<extra></extra>",
                ), row=1, col=1)

    # --- Niveles del plan (entrada / SL / TP) ---
    plan, sig = result.plan, result.signal
    if layers["plan"] and plan is not None and sig is not None and sig.dir_int != 0:
        actionable = sig.actionable
        alpha = 1.0 if actionable else 0.35
        x0 = x[max(0, len(x) - 30)]
        for price, name, color, dash in ((plan.entry, "ENTRADA", C["text"], "dash"),
                                         (plan.stop_loss, "SL", C["short"], "solid"),
                                         (plan.tp1, f"TP1 1:{plan.rr_tp1:g}", C["long"], "solid"),
                                         (plan.tp2, f"TP2 1:{plan.rr_tp2:g}", C["long"], "dot")):
            fig.add_shape(type="line", x0=x0, x1=last_x, y0=price, y1=price, xref="x", yref="y",
                          line=dict(color=color, width=1.2, dash=dash), opacity=alpha)
            fig.add_annotation(x=last_x, y=price, text=f"{name} {fmt_price(price)}", showarrow=False,
                               xanchor="left", xshift=4, font=dict(size=10, color=color, family="JetBrains Mono"),
                               opacity=alpha, bgcolor=C["bg"], row=1, col=1)

    # --- Último precio ---
    last_close = float(df["close"].iloc[-1])
    last_color = C["long"] if df["close"].iloc[-1] >= df["open"].iloc[-1] else C["short"]
    fig.add_hline(y=last_close, line=dict(color=last_color, width=1, dash="dot"), opacity=0.6, row=1, col=1)

    # --- Volumen ---
    vol_colors = np.where(df["close"] >= df["open"], "rgba(0,255,136,0.45)", "rgba(255,59,48,0.45)")
    fig.add_trace(go.Bar(x=x, y=df["volume"], marker_color=vol_colors, name="Volumen", showlegend=False,
                         hovertemplate="Vol %{y:,.0f}<extra></extra>"), row=2, col=1)

    # --- RSI ---
    if "rsi" in df.columns:
        fig.add_trace(go.Scatter(x=x, y=df["rsi"], mode="lines", name="RSI", line=dict(color=C["vwap"], width=1.2),
                                 showlegend=False, hovertemplate="RSI %{y:.1f}<extra></extra>"), row=3, col=1)
        for level in (70, 30):
            fig.add_hline(y=level, line=dict(color=C["faint"], width=1, dash="dot"), row=3, col=1)

    # --- Estilo TradingView ---
    spike = dict(showspikes=True, spikemode="across", spikesnap="cursor", spikecolor=C["faint"],
                 spikethickness=1, spikedash="dot")
    fig.update_layout(
        height=height, margin=dict(l=8, r=86, t=6, b=6), paper_bgcolor=C["bg"], plot_bgcolor=C["bg"],
        font=dict(family="Inter, sans-serif", color=C["muted"], size=11), dragmode="pan", hovermode="x unified",
        hoverlabel=dict(bgcolor=C["panel"], bordercolor=C["line"], font=dict(family="JetBrains Mono", size=11)),
        legend=dict(orientation="h", x=0, y=1.0, yanchor="bottom", bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
        xaxis_rangeslider_visible=False, uirevision=f"{md.symbol}-{tf}-{market}", bargap=0.15,
    )
    breaks = _rangebreaks(market, tf)
    x_range = None
    if not breaks:  # Sin huecos de mercado: dejamos ~12 % libre a la derecha para las etiquetas
        x_range = [x[0], last_x + (last_x - x[0]) * 0.12]
    fig.update_xaxes(gridcolor=C["grid"], zeroline=False, showline=False, rangebreaks=breaks, range=x_range, **spike)
    fig.update_yaxes(gridcolor=C["grid"], zeroline=False, side="right", tickfont=dict(family="JetBrains Mono", size=10), **spike)
    fig.update_yaxes(row=2, col=1, showticklabels=False, showgrid=False)
    fig.update_yaxes(row=3, col=1, range=[0, 100], tickvals=[30, 70])
    return fig


# =============================================================================
# Tarjeta de señal
# =============================================================================
def render_signal_card(result) -> None:
    """Dirección, score, clasificación, razón y niveles (panel derecho)."""
    sig, plan = result.signal, result.plan
    if sig is None:
        render_empty("Sin señal", "No hay datos suficientes para evaluar.")
        return
    cls = {1: "long", -1: "short"}.get(sig.dir_int, "neutral")
    arrow = {1: "▲ LONG", -1: "▼ SHORT"}.get(sig.dir_int, "— NEUTRAL")
    bar_color = C["long"] if sig.dir_int == 1 else C["short"] if sig.dir_int == -1 else C["faint"]
    if not sig.actionable:
        bar_color = C["faint"] if sig.grade_code != "blocked" else C["short"]
    valid_pct, alert_pct = SETTINGS.score_valid, SETTINGS.score_alert

    ctx = ""
    if sig.context_adjust:
        ctx = f'<div class="fs-sub">Técnico {sig.technical_score:.0f} · contexto {sig.context_adjust:+.1f}</div>'
    gate = f'<div class="fs-warn">Filtro {esc(sig.strategy_name)}: {esc(sig.gate_message)}</div>' if not sig.gate_ok and sig.gate_message else ""
    blocks = "".join(f'<div class="fs-block">⛔ {esc(b)}</div>' for b in sig.blocked_by)

    levels = ""
    if plan is not None and sig.dir_int != 0:
        ref = plan.entry
        muted = "" if sig.actionable else " fs-muted-levels"
        sl_pct = (plan.stop_loss / plan.entry - 1) * 100
        tp1_pct = (plan.tp1 / plan.entry - 1) * 100
        tp2_pct = (plan.tp2 / plan.entry - 1) * 100
        levels = (
            f'<div class="fs-levels{muted}">'
            f'<div class="fs-level"><span>Entrada</span><b>{fmt_price(plan.entry, ref)}</b></div>'
            f'<div class="fs-level sl"><span>Stop Loss</span><b>{fmt_price(plan.stop_loss, ref)}<em>{sl_pct:+.2f}%</em></b></div>'
            f'<div class="fs-level tp"><span>TP1 · 1:{plan.rr_tp1:g}</span><b>{fmt_price(plan.tp1, ref)}<em>{tp1_pct:+.2f}%</em></b></div>'
            f'<div class="fs-level tp"><span>TP2 · 1:{plan.rr_tp2:g}</span><b>{fmt_price(plan.tp2, ref)}<em>{tp2_pct:+.2f}%</em></b></div>'
            f'<div class="fs-level"><span>Tamaño</span><b>{plan.position_size:g} {esc(plan.size_unit)}</b></div>'
            f'<div class="fs-level"><span>Riesgo</span><b>{fmt_money(plan.risk_amount)}</b></div>'
            f'</div>'
            f'<div class="fs-sub">SL por {esc(plan.sl_method)}'
            + (" · niveles referenciales (no operar)" if not sig.actionable else "") + '</div>'
        )

    _html(
        f'<div class="fs-card {cls if sig.actionable else ""}">'
        f'<div class="fs-card-label">{"Señal" if sig.actionable else "Sesgo actual"} · {esc(sig.strategy_name)}</div>'
        f'<div class="fs-direction {cls}{"" if sig.actionable else " dim"}">{arrow}</div>'
        f'<div class="fs-score-row"><span class="fs-score">{sig.score:.0f}<small>/100</small></span>'
        f'<span class="fs-sub">opuesto {sig.opposite_score:.0f}</span></div>'
        f'<div class="fs-bar"><span style="width:{max(2, min(100, sig.score)):.0f}%;background:{bar_color}"></span>'
        f'<i class="tick" style="left:{valid_pct}%"></i><i class="tick" style="left:{alert_pct}%"></i></div>'
        f'{ctx}<span class="fs-grade {sig.grade_code}">{esc(sig.grade)}</span>'
        f'<div class="fs-reason">{esc(sig.reason_text)}</div>{gate}{blocks}{levels}</div>'
    )
    if plan is not None and plan.warnings and sig.dir_int != 0:
        for w in plan.warnings[:3]:
            _html(f'<div class="fs-warn">{esc(w)}</div>')


def render_kpis(result) -> None:
    """Mini métricas bajo la tarjeta."""
    snap = result.snapshot()
    md = result.market_data

    def kpi(k: str, v: str, cls: str = "") -> str:
        return f'<div class="fs-kpi"><div class="k">{esc(k)}</div><div class="v {cls}">{v}</div></div>'

    items = [
        kpi("RSI", f"{snap.get('rsi', float('nan')):.0f}" if snap.get("rsi") is not None else "—"),
        kpi("ADX", f"{snap.get('adx', float('nan')):.0f}" if snap.get("adx") is not None else "—"),
        kpi("ATR %", f"{snap.get('atr_pct', 0):.2f}" if snap.get("atr_pct") is not None else "—"),
    ]
    if md.funding_rate is not None:
        fr = md.funding_rate * 100
        items.append(kpi("Funding", f"{fr:.4f}%", "down" if fr > 0.03 else "up" if fr < 0 else ""))
    if snap.get("oi_change_pct") is not None:
        oi = snap["oi_change_pct"]
        items.append(kpi("OI 5v", f"{oi:+.1f}%", "up" if oi > 0 else "down"))
    if result.context is not None and result.context.confidence > 0:
        b = result.context.bias
        items.append(kpi("Contexto", f"{b:+.2f}", "up" if b > 0.1 else "down" if b < -0.1 else ""))
    _html(f'<div class="fs-kpis" style="margin-top:0.6rem">{"".join(items)}</div>')


# =============================================================================
# Desglose del score
# =============================================================================
def render_factors(result) -> None:
    """Tabla compacta: cuánto aporta cada factor."""
    sig = result.signal
    if sig is None:
        return
    side_cls = "short" if sig.dir_int == -1 else ""
    rows = []
    for f in sig.factors:
        rows.append(
            f'<div class="fs-factor{"" if f.available else " off"}">'
            f'<div><div class="name">{esc(f.label)}</div><div class="why">{esc(f.category)} · peso {f.weight:.0f}</div></div>'
            f'<div><div class="why">{esc(f.reason)}</div><div class="fs-mini-bar {side_cls}"><span style="width:{f.value * 100:.0f}%"></span></div></div>'
            f'<div class="pts">{f.points:.1f}</div></div>'
        )
    head = (f'<div class="fs-sub" style="margin-bottom:0.5rem">Score técnico = suma ponderada · '
            f'bruto {sig.raw_score:.1f} → filtro {sig.technical_score:.1f}'
            + (f' → contexto {sig.score:.1f}' if sig.context_adjust else "") + "</div>")
    _html(head + "".join(rows))
    if result.strategy_note:
        _html(f'<div class="fs-sub" style="margin-top:0.6rem">{esc(result.strategy_note)}</div>')
    if result.structure is not None:
        _html(f'<div class="fs-sub">{esc(result.structure.summary(len(result.df)))}</div>')


# =============================================================================
# Terminal de futuros estilo exchange (solo recomendación)
# =============================================================================
def render_trade_terminal(result, on_journal=None) -> None:
    """
    Ticket de orden con la recomendación ya rellenada, simulación de PnL y orden
    lista para copiar en tu exchange. NO envía nada.
    """
    sig, plan, md = result.signal, result.plan, result.market_data
    if sig is None or plan is None or sig.dir_int == 0:
        render_empty("Sin recomendación", "El mercado no ofrece una dirección clara ahora mismo.")
        return
    d = sig.dir_int
    lev = plan.leverage
    quote_ccy = "USDT" if md.market.value in ("crypto", "demo") else "USD"
    left, right = st.columns([1, 1.25], gap="large")

    with left:
        _html(
            '<div class="fs-term">'
            f'<div class="fs-term-head"><span class="fs-pill on">Aislado</span><span class="fs-pill on">{lev:g}x</span>'
            f'<span class="fs-pill on">Límite</span><span class="fs-pill">{esc(md.symbol.upper())} {esc(md.contract or "")}</span></div>'
            f'<div class="fs-sides"><div class="fs-side long {"on" if d == 1 else ""}">ABRIR LONG</div>'
            f'<div class="fs-side short {"on" if d == -1 else ""}">ABRIR SHORT</div></div>'
            f'<div class="fs-field"><span>Precio</span>{fmt_price(plan.entry)} {quote_ccy}</div>'
            f'<div class="fs-field"><span>Tamaño</span>{plan.position_size:g} {esc(plan.size_unit)}</div>'
            f'<div class="fs-field"><span>Valor</span>{fmt_money(plan.notional)} {quote_ccy}</div>'
            f'<div class="fs-field"><span>Margen</span>{fmt_money(plan.margin_required)} {quote_ccy}</div>'
            f'<div class="fs-field"><span>Take Profit</span>{fmt_price(plan.tp1)} / {fmt_price(plan.tp2)}</div>'
            f'<div class="fs-field"><span>Stop Loss</span>{fmt_price(plan.stop_loss)}</div>'
            f'<div class="fs-field"><span>Liquidación aprox.</span>{fmt_price(plan.liquidation_price) if plan.liquidation_price else "—"}</div>'
            f'<div class="fs-sub">Apalancamiento máx. seguro para este SL: {plan.max_safe_leverage:.0f}x</div>'
            '</div>'
        )

    with right:
        margin = plan.margin_required or 1.0
        scenarios = []
        for name, price in (("Stop Loss", plan.stop_loss), ("Entrada", plan.entry), ("TP1", plan.tp1), ("TP2", plan.tp2)):
            units = plan.notional / plan.entry if plan.entry else 0.0
            pnl = (price - plan.entry) * d * units - (plan.fees_estimate if name != "Entrada" else 0.0)
            scenarios.append({"Escenario": name, "Precio": fmt_price(price), "PnL": round(pnl, 2),
                              "ROE %": round(pnl / margin * 100.0, 1)})
        _html('<div class="fs-card-label" style="margin-bottom:0.4rem">Simulación de resultado</div>')
        st.dataframe(pd.DataFrame(scenarios), hide_index=True, width="stretch")
        _html(
            '<div class="fs-kpis">'
            f'<div class="fs-kpi"><div class="k">Riesgo</div><div class="v down">-{fmt_money(plan.risk_amount)}</div></div>'
            f'<div class="fs-kpi"><div class="k">Ganancia TP1</div><div class="v up">+{fmt_money(plan.profit_tp1)}</div></div>'
            f'<div class="fs-kpi"><div class="k">Ganancia TP2</div><div class="v up">+{fmt_money(plan.profit_tp2)}</div></div>'
            f'<div class="fs-kpi"><div class="k">Comisiones</div><div class="v">{fmt_money(plan.fees_estimate)}</div></div>'
            '</div>'
        )
        side_txt = "LONG (Comprar)" if d == 1 else "SHORT (Vender)"
        order = (
            f"{md.symbol.upper()} · {md.contract or 'Futuros'} · {side_txt}\n"
            f"Margen: Aislado · Apalancamiento: {lev:g}x · Orden: Límite\n"
            f"Precio:      {fmt_price(plan.entry)}\n"
            f"Cantidad:    {plan.position_size:g} {plan.size_unit}\n"
            f"Stop Loss:   {fmt_price(plan.stop_loss)}  ({plan.sl_method})\n"
            f"Take Profit: TP1 {fmt_price(plan.tp1)} (50%) · TP2 {fmt_price(plan.tp2)} (50%)\n"
            f"Score: {sig.score:.0f}/100 · {sig.grade}"
        )
        _html('<div class="fs-card-label" style="margin:0.8rem 0 0.3rem">Orden para copiar en tu exchange</div>')
        st.code(order, language=None)
        if not sig.actionable:
            render_banners(["Score por debajo de 70 o señal bloqueada: la orden es solo referencial."])
        if on_journal is not None and st.button("Registrar en el diario", key="journal_btn", width="stretch"):
            on_journal()
    _html('<div class="fs-sub">FUTURES-SCOPE no ejecuta órdenes. Revisa siempre el tamaño mínimo y las comisiones de tu exchange.</div>')


# =============================================================================
# Contexto económico / social / político / cultural
# =============================================================================
def render_context(ctx) -> None:
    """Panel de contexto con pilares, macro y titulares."""
    if ctx is None:
        render_empty("Contexto desactivado", "Actívalo en .env (CONTEXT_ENABLED=true) o en la barra lateral.")
        return
    if ctx.confidence <= 0:
        render_banners(ctx.errors or ["Sin datos de contexto."])
        render_empty("Contexto no disponible", "Se necesita conexión a internet (Google News, FRED, Yahoo, alternative.me).")
        return

    cls = "up" if ctx.bias > 0.1 else "down" if ctx.bias < -0.1 else ""
    profile = "refugio (se invierte el miedo macro)" if ctx.asset_profile == "safe_haven" else "activo de riesgo"
    _html(
        '<div class="fs-kpis" style="margin-bottom:0.8rem">'
        f'<div class="fs-kpi"><div class="k">Sesgo de contexto</div><div class="v {cls}">{ctx.bias:+.2f} · {esc(ctx.label)}</div></div>'
        f'<div class="fs-kpi"><div class="k">Confianza</div><div class="v">{ctx.confidence * 100:.0f}%</div></div>'
        f'<div class="fs-kpi"><div class="k">Perfil</div><div class="v" style="font-size:0.8rem">{esc(profile)}</div></div>'
        + (f'<div class="fs-kpi"><div class="k">Fear &amp; Greed</div><div class="v">{ctx.fear_greed["value"]} · {esc(ctx.fear_greed["label"])}</div></div>' if ctx.fear_greed else "")
        + '</div>'
    )
    cols = st.columns(len(ctx.pillars))
    for col, pillar in zip(cols, ctx.pillars.values()):
        pcls = "up" if pillar.score > 0.1 else "down" if pillar.score < -0.1 else ""
        details = "<br>".join(esc(dt) for dt in pillar.details[:3])
        with col:
            _html(f'<div class="fs-pillar"><div class="t">{esc(pillar.name)}</div>'
                  f'<div class="s {pcls}">{pillar.score:+.2f}</div>'
                  f'<div class="d">{esc(pillar.summary)} · conf. {pillar.confidence * 100:.0f}%<br>{details}</div></div>')

    if ctx.macro:
        rows = [{"Indicador": v["name"], "Último": v["last"], "5 días %": v["chg_5d"], "20 días %": v["chg_20d"]}
                for v in ctx.macro.values()]
        _html('<div class="fs-card-label" style="margin:1rem 0 0.4rem">Mercados macro</div>')
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    if ctx.headlines:
        _html('<div class="fs-card-label" style="margin:1rem 0 0.4rem">Titulares analizados</div>')
        items = []
        for h in ctx.headlines[:18]:
            scls = "up" if h.sentiment > 0.05 else "down" if h.sentiment < -0.05 else "flat"
            cats = " · ".join(h.categories) if h.categories else h.feed
            link = esc(h.link) if h.link.startswith("http") else "#"
            items.append(f'<div class="fs-headline"><span class="sent {scls}">{h.sentiment:+.2f}</span>'
                         f'<div><a href="{link}" target="_blank" rel="noopener">{esc(h.title)}</a>'
                         f'<div class="src">{esc(h.source)} · {esc(cats)}</div></div></div>')
        _html("".join(items))
    _html('<div class="fs-sub" style="margin-top:0.6rem">El contexto es orientativo: resume datos y titulares públicos '
          'con reglas simples. No predice el mercado y nunca salta los filtros de riesgo.</div>')


# =============================================================================
# TradingView en vivo (widget oficial gratuito)
# =============================================================================
CME_TV = {
    "ES=F": "CME_MINI:ES1!", "MES=F": "CME_MINI:MES1!", "NQ=F": "CME_MINI:NQ1!", "MNQ=F": "CME_MINI:MNQ1!",
    "YM=F": "CBOT_MINI:YM1!", "MYM=F": "CBOT_MINI:MYM1!", "RTY=F": "CME_MINI:RTY1!", "M2K=F": "CME_MINI:M2K1!",
    "GC=F": "COMEX:GC1!", "MGC=F": "COMEX:MGC1!", "SI=F": "COMEX:SI1!", "CL=F": "NYMEX:CL1!",
    "MCL=F": "NYMEX:MCL1!", "NG=F": "NYMEX:NG1!", "ZN=F": "CBOT:ZN1!", "ZB=F": "CBOT:ZB1!", "6E=F": "CME:6E1!",
}
TV_INTERVALS = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1D": "D"}


def tradingview_symbol(symbol: str, market: str, contract: str | None = "USDT-M", exchange: str | None = "binance") -> str:
    """Traduce el símbolo del bot al formato de TradingView."""
    sym = re.sub(r"[^A-Z0-9/:._=!\-]", "", symbol.upper().strip())[:30]  # solo caracteres de ticker
    if market == "demo":
        return "BINANCE:BTCUSDT.P"
    if market == "crypto":
        from engine.data_feeder import parse_crypto_symbol

        base, quote = parse_crypto_symbol(sym)
        venue = (exchange or "binance").upper()
        if contract == "COIN-M":
            return f"{venue}:{base}USD.P"
        return f"{venue}:{base}{'USDT' if quote in ('USD', 'BUSD') else quote}.P"
    if market == "cme":
        return CME_TV.get(sym, sym.replace("=F", "1!"))
    if market == "etf" and sym in ("SPY", "DIA", "IWM", "GLD", "SLV", "XLF", "XLE", "ARKK"):
        return f"AMEX:{sym}"
    return sym


def render_tradingview_widget(symbol: str, market: str, timeframe: str, contract: str | None = None,
                              exchange: str | None = None, height: int = 640) -> None:
    """Incrusta el Advanced Chart oficial de TradingView (requiere internet en tu PC)."""
    tv_symbol = tradingview_symbol(symbol, market, contract, exchange)
    config = {
        "autosize": True, "symbol": tv_symbol, "interval": TV_INTERVALS.get(timeframe, "60"),
        "timezone": "Etc/UTC", "theme": "dark", "style": "1", "locale": "es",
        "backgroundColor": C["bg"], "gridColor": "rgba(31, 38, 51, 0.6)", "hide_side_toolbar": False,
        "allow_symbol_change": True, "save_image": True, "calendar": False,
        "studies": ["STD;VWAP", "STD;RSI"], "support_host": "https://www.tradingview.com",
    }
    markup = f"""
    <div class="tradingview-widget-container" style="height:{height}px;width:100%">
      <div class="tradingview-widget-container__widget" style="height:calc(100% - 24px);width:100%"></div>
      <div class="tradingview-widget-copyright" style="font:11px Inter,sans-serif;color:#4A5361;padding-top:4px">
        <a href="https://www.tradingview.com/" rel="noopener nofollow" target="_blank" style="color:#7D8590">Gráfico de TradingView</a>
      </div>
      <script type="text/javascript" src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js" async>
      {json.dumps(config)}
      </script>
    </div>
    """
    if hasattr(st, "iframe"):  # Streamlit >= 1.5x
        st.iframe(markup, height=height + 10)
    else:  # Compatibilidad con versiones anteriores
        components.html(markup, height=height + 10)
    _html(f'<div class="fs-sub">Símbolo en TradingView: <span class="mono">{esc(tv_symbol)}</span> · '
          'puedes cambiarlo, dibujar y añadir indicadores dentro del widget.</div>')


# =============================================================================
# Backtest, historial y noticias
# =============================================================================
def equity_chart(equity: pd.Series, height: int = 260) -> go.Figure:
    """Curva de capital minimalista."""
    fig = go.Figure(go.Scatter(x=equity.index, y=equity.values, mode="lines", line=dict(color=C["long"], width=1.6),
                               fill="tozeroy", fillcolor="rgba(0,255,136,0.06)", hovertemplate="%{y:,.2f}<extra></extra>"))
    lo, hi = float(equity.min()), float(equity.max())
    pad = (hi - lo) * 0.1 or hi * 0.01
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=6, b=6), paper_bgcolor=C["bg"], plot_bgcolor=C["bg"],
                      font=dict(color=C["muted"], size=10), showlegend=False)
    fig.update_xaxes(gridcolor=C["grid"])
    fig.update_yaxes(gridcolor=C["grid"], side="right", range=[lo - pad, hi + pad])
    return fig


def render_backtest_result(bt) -> None:
    """Métricas + curva de capital + operaciones."""
    m = bt.metrics
    if m["trades"] == 0:
        render_empty("Sin operaciones", "Ninguna vela superó el umbral con el filtro de la estrategia. Prueba un umbral menor.")
        return
    pf = "∞" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
    ret_cls = "up" if m["return_pct"] >= 0 else "down"
    _html(
        '<div class="fs-kpis" style="margin-bottom:0.6rem">'
        f'<div class="fs-kpi"><div class="k">Operaciones</div><div class="v">{m["trades"]}</div></div>'
        f'<div class="fs-kpi"><div class="k">Win rate</div><div class="v">{m["win_rate"]:.1f}%</div></div>'
        f'<div class="fs-kpi"><div class="k">Profit factor</div><div class="v">{pf}</div></div>'
        f'<div class="fs-kpi"><div class="k">Total R</div><div class="v {ret_cls}">{m["total_r"]:+.2f}R</div></div>'
        f'<div class="fs-kpi"><div class="k">Retorno</div><div class="v {ret_cls}">{m["return_pct"]:+.1f}%</div></div>'
        f'<div class="fs-kpi"><div class="k">DD máx.</div><div class="v down">{m["max_dd_pct"]:.1f}%</div></div>'
        f'<div class="fs-kpi"><div class="k">Kill switch</div><div class="v">{m["kill_switch_triggers"]}x</div></div>'
        '</div>'
    )
    if len(bt.equity) > 1:
        st.plotly_chart(equity_chart(bt.equity), width="stretch", config={"displayModeBar": False})
    t = bt.trades.iloc[::-1]
    ref = float(t["entry"].iloc[0]) if len(t) else None
    table = pd.DataFrame({
        "Entrada (UTC)": pd.to_datetime(t["entry_time"]).dt.strftime("%d %b %H:%M"),
        "Lado": t["direction"],
        "Score": t["score"].round(0).astype(int),
        "Precio": [fmt_price(v, ref) for v in t["entry"]],
        "SL": [fmt_price(v, ref) for v in t["stop"]],
        "TP1": [fmt_price(v, ref) for v in t["tp1"]],
        "Salida": [fmt_price(v, ref) for v in t["exit"]],
        "Resultado R": t["result_r"].round(2),
        "Velas": t["bars_held"],
        "Motivo": t["exit_reason"],
    })
    st.dataframe(table, hide_index=True, width="stretch", height=260)


def render_history(db, risk) -> None:
    """Estadísticas, kill switch y últimas señales registradas."""
    stats = db.stats()
    active, streak = risk.kill_switch_status()
    ks_txt = f"PAUSADO ({streak} pérdidas)" if active else f"Activo · racha {streak}/{risk.kill_switch_losses}"
    _html(
        '<div class="fs-kpis" style="margin-bottom:0.6rem">'
        f'<div class="fs-kpi"><div class="k">Señales</div><div class="v">{stats["total"]}</div></div>'
        f'<div class="fs-kpi"><div class="k">Abiertas</div><div class="v">{stats["open"]}</div></div>'
        f'<div class="fs-kpi"><div class="k">Win rate</div><div class="v">{stats["win_rate"]:.1f}%</div></div>'
        f'<div class="fs-kpi"><div class="k">Resultado</div><div class="v {"up" if stats["total_r"] >= 0 else "down"}">{stats["total_r"]:+.2f}R</div></div>'
        f'<div class="fs-kpi"><div class="k">Kill switch</div><div class="v {"down" if active else "up"}" style="font-size:0.8rem">{esc(ks_txt)}</div></div>'
        '</div>'
    )
    if active and st.button("Reactivar bot (reiniciar kill switch)", key="reset_ks", type="secondary"):
        risk.reset_kill_switch()
        st.rerun()
    df = db.recent_signals(100)
    if df.empty:
        render_empty("Sin señales registradas", "Las señales con score ≥ 70 en mercados reales se guardan aquí y se siguen solas (TP1 / SL).")
        return
    cols = [c for c in ("id", "bar_time", "symbol", "timeframe", "direction", "score", "entry", "stop_loss", "tp1",
                        "tp2", "status", "result_r", "strategy", "reasons") if c in df.columns]
    st.dataframe(df[cols], hide_index=True, width="stretch", height=300)
    open_ids = df.loc[df["status"] == "OPEN", "id"].tolist()
    if open_ids:
        with st.expander("Registrar resultado manual (si operaste la señal)"):
            c1, c2, c3 = st.columns([1, 1, 1])
            sid = c1.selectbox("Señal", open_ids, key="manual_sid")
            outcome = c2.selectbox("Resultado", ["Ganadora", "Perdedora"], key="manual_outcome")
            if c3.button("Guardar", key="manual_save", width="stretch"):
                risk.record_manual_result(int(sid), outcome == "Ganadora")
                st.rerun()


def render_news(news_filter, risk_check) -> None:
    """Estado del filtro y próximos eventos de alto impacto."""
    if risk_check is not None and risk_check.news is not None:
        status = risk_check.news
        render_banners([status.message], "err" if status.blocked else "ok")
    events = news_filter.upcoming(hours=72)
    if not events:
        render_empty("Sin eventos de alto impacto próximos",
                     "Fuente: calendario de ForexFactory + data/news_calendar.json (añade FOMC, CPI, NFP...).")
        return
    rows = [{"Fecha (UTC)": e.time_utc.strftime("%a %d %b %H:%M"), "Moneda": e.currency, "Evento": e.title,
             "Impacto": e.impact, "Fuente": e.source} for e in events]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def export_static_chart(df: pd.DataFrame, path: str | Path, title: str = "", bars: int = 150) -> Path:
    """Exporta un PNG con mplfinance (velas + EMA 50/200 + volumen)."""
    import mplfinance as mpf

    data = df.tail(bars)[["open", "high", "low", "close", "volume"]].copy()
    data.columns = ["Open", "High", "Low", "Close", "Volume"]
    data.index = data.index.tz_convert(None) if data.index.tz is not None else data.index
    adds = [mpf.make_addplot(df.tail(bars)[c], color=col, width=1) for c, col in
            (("ema_50", C["ema50"]), ("ema_200", C["ema200"])) if c in df.columns and df.tail(bars)[c].notna().any()]
    style = mpf.make_mpf_style(base_mpf_style="nightclouds", marketcolors=mpf.make_marketcolors(
        up=C["long"], down=C["short"], wick="inherit", edge="inherit", volume="inherit"), facecolor=C["bg"],
        figcolor=C["bg"], gridcolor=C["grid"])
    path = Path(path)
    mpf.plot(data, type="candle", style=style, volume=True, addplot=adds or None, title=title,
             savefig=dict(fname=str(path), dpi=130, bbox_inches="tight"))
    return path
