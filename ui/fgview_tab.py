"""
ui/fgview_tab.py
================
Pestañas de FG VIEW V1:
* "🟡 FG VIEW": recomendación de scalping (15-30 min) para la cripto elegida, con
  plan por presupuesto, filtros, apalancamientos, gestión y escalera de precios.
* "Bitácora": registro de operaciones y métricas para validar el win rate.
"""

from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine.position_simulator import fmt_px, fmt_usd
from engine.recommender import (MAX_HOLD_MIN, best_direction, build_plan, daily_guard, evaluate_filters,
                                filter_effectiveness, format_recommendation, journal_metrics, management_text,
                                options_table, scalp_context)

GOLD, GOLD_LIGHT, GREEN, RED, MUTED, BG = "#D4AF37", "#F2D27A", "#3FB950", "#F85149", "#A89F8A", "#0A0A0A"


# =============================================================================
# Escalera visual de precios (también la usa el simulador)
# =============================================================================
def price_ladder(levels: list[tuple[str, float, str]], entry: float | None = None, height: int = 360) -> go.Figure:
    """
    Escalera Liquidación → SL → Entrada → TP1 → TP2 → TP3 con escalones a igual distancia
    (si no, la liquidación lejana aplasta los TP) y el % de cada nivel respecto a la entrada.
    """
    levels = sorted([lv for lv in levels if lv[1] and math.isfinite(lv[1])], key=lambda lv: lv[1])
    fig = go.Figure()
    n = len(levels)
    fig.add_shape(type="line", x0=0.5, x1=0.5, y0=0, y1=max(0, n - 1), line=dict(color="#2A2418", width=6))
    for i, (label, price, color) in enumerate(levels):
        pct = f"  ({(price / entry - 1) * 100:+.2f}%)" if entry and label != "ENTRADA" else ""
        fig.add_shape(type="line", x0=0.2, x1=0.8, y0=i, y1=i, line=dict(color=color, width=3))
        fig.add_annotation(x=0.85, y=i, text=f"<b>{label}</b>  ${fmt_px(price)}{pct}", showarrow=False,
                           xanchor="left", font=dict(color=color, size=12, family="JetBrains Mono, monospace"))
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=8, b=8), paper_bgcolor=BG, plot_bgcolor=BG,
                      xaxis=dict(visible=False, range=[0, 3.2]),
                      yaxis=dict(visible=False, range=[-0.6, max(0, n - 1) + 0.6]), showlegend=False)
    return fig


def plan_ladder(plan) -> go.Figure:
    levels = [("LIQUIDACIÓN", plan.liquidation, RED), ("SL", plan.stop_loss, RED),
              ("ENTRADA", plan.entry, "#F5F1E6")]
    levels += [(f"TP{i}", tp[0], GREEN if i < 3 else GOLD_LIGHT) for i, tp in enumerate(plan.tps, 1)]
    return price_ladder(levels, plan.entry)


# =============================================================================
# Envío al simulador
# =============================================================================
def _send_to_simulator(plan, capital: float) -> None:
    from engine.position_simulator import ALL_COINS

    k = "sim_"
    if plan.coin in ALL_COINS:
        st.session_state[k + "coin"] = plan.coin
    else:
        st.session_state[k + "coin"], st.session_state[k + "coin_other"] = "Otra…", plan.coin
    st.session_state.update({
        k + "dir": plan.direction, k + "unit": "Precio", k + "mode": "Margen fijo", k + "capital": float(capital),
        k + "margin": round(float(plan.margin), 2), k + "lev": int(plan.leverage), k + "entry": f"{plan.entry}",
        k + "sl": f"{plan.stop_loss}",
    })
    for i, (price, _, close) in enumerate(plan.tps, 1):
        st.session_state[k + f"tp{i}"], st.session_state[k + f"tp{i}p"] = f"{price}", float(close)
    st.session_state["fg_sent"] = True


def coin_tier_symbol(req) -> str:
    return "BTC" if req.market == "demo" else req.symbol


# =============================================================================
# Pestaña principal FG VIEW
# =============================================================================
def render_fgview(pipeline, req) -> None:
    if req.market not in ("crypto", "demo"):
        st.info("El recomendador FG VIEW es para **criptos en futuros perpetuos**. Elige el mercado Cripto (o Demo).")
        return

    c1, c2, c3 = st.columns([1, 1, 1])
    capital = c1.number_input("Capital (USDT)", min_value=10.0, value=float(req.equity or 1000.0), step=50.0,
                              key="fg_capital")
    mode = c2.segmented_control("Dirección", ["Auto", "LONG", "SHORT"], default="Auto", key="fg_dir") or "Auto"
    refresh = c3.button("Actualizar recomendación", key="fg_refresh", width="stretch")

    try:
        ctx = scalp_context(pipeline.feeder, req.symbol, req.market, req.contract, req.exchange, refresh)
    except Exception as exc:  # noqa: BLE001
        st.error(f"No se pudieron obtener velas de 5 min de {req.symbol}: {exc}")
        return

    news = pipeline.news.check()
    kw = dict(funding=ctx.funding, order_book=ctx.order_book, news_blocked=news.message if news.blocked else None)
    rep = best_direction(ctx.df5, ctx.df1h, coin_tier_symbol(req), **kw) if mode == "Auto" else \
        evaluate_filters(mode, ctx.df5, ctx.df1h, coin_tier_symbol(req), **kw)
    guard = daily_guard(pipeline.db.journal_today(), capital)
    if guard:
        rep.blocks.insert(0, guard)

    coin = "BTC" if req.market == "demo" else req.symbol  # El modo demo simula un BTC
    plan = build_plan(capital, coin, rep.direction, ctx.price, ctx.atr5)
    for w in ctx.warnings:
        st.caption(f"⚠️ {w}")

    left, right = st.columns([1.7, 1], gap="large")
    with left:
        st.markdown(format_recommendation(plan, rep))
        st.markdown("**Filtros de entrada** (mínimo 4 de 6)")
        st.dataframe(pd.DataFrame([{"": "✅" if c.passed else "⬜", "Filtro": c.name, "Detalle": c.detail}
                                   for c in rep.checks]), hide_index=True, width="stretch")
        st.markdown(f"**Apalancamientos del Nivel {plan.tier.key}** "
                    f"({plan.tier.lev_min}x–{plan.tier.lev_max}x · riesgo {plan.risk_pct:g}% · margen máx. "
                    f"{plan.max_margin_pct:g}%)")
        st.dataframe(options_table(plan), hide_index=True, width="stretch")
        st.markdown(management_text(plan))
    with right:
        st.plotly_chart(plan_ladder(plan), width="stretch", config={"displayModeBar": False}, key="fg_ladder")
        st.button("Enviar al Simulador", key="fg_to_sim", width="stretch", on_click=_send_to_simulator,
                  args=(plan, capital))
        if st.session_state.pop("fg_sent", False):
            st.success("Plan enviado: ábrelo en la pestaña Simulador.")
        with st.expander("📒 Registrar el resultado en la bitácora"):
            exit_px = st.text_input("Precio de salida", key="fg_exit", placeholder=fmt_px(plan.tps[0][0]))
            dur = st.number_input("Duración (min)", 1, 600, 20, key="fg_dur")
            if st.button("Guardar operación", key="fg_save", width="stretch"):
                try:
                    px = float(str(exit_px).replace(",", "."))
                except ValueError:
                    st.warning("Escribe el precio de salida.")
                else:
                    d = 1 if plan.direction == "LONG" else -1
                    pnl = plan.notional * d * (px - plan.entry) / plan.entry - plan.fees
                    pipeline.db.add_journal({
                        "coin": plan.coin, "direction": plan.direction, "entry": plan.entry, "exit_price": px,
                        "leverage": plan.leverage, "margin": plan.margin, "pnl": round(pnl, 2), "score": rep.score,
                        "filters": {c.name: c.passed for c in rep.checks}, "duration_min": dur,
                    })
                    st.toast(f"Guardado: {fmt_usd(pnl, True)}")
        st.caption(f"Datos: {ctx.source} · velas de 5 min (señal) y 1 h (tendencia) · tiempo máximo {MAX_HOLD_MIN} min")


# =============================================================================
# Bitácora
# =============================================================================
def render_journal(db) -> None:
    trades = db.journal()
    m = journal_metrics(trades)
    n = m.get("trades", 0)
    st.progress(min(1.0, n / 30), text=f"{n} de 30 operaciones para validar el win rate (ideal 50)")
    if not n:
        st.info("Aún no hay operaciones. Regístralas desde la pestaña 🟡 FG VIEW al cerrar cada trade.")
        return
    pf = "∞" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
    cols = st.columns(6)
    for col, (k, v) in zip(cols, [("Operaciones", n), ("Win rate", f"{m['win_rate']:.1f}%"), ("Profit factor", pf),
                                  ("Expectativa", fmt_usd(m["expectancy"], True)), ("Total", fmt_usd(m["total"], True)),
                                  ("Drawdown máx.", fmt_usd(-m["max_drawdown"]))]):
        col.metric(k, v)
    st.caption(f"Racha actual: {m['streak']}")
    df = pd.DataFrame(trades)
    show = [c for c in ("id", "opened_at", "coin", "direction", "entry", "exit_price", "leverage", "pnl", "result",
                        "score", "duration_min") if c in df.columns]
    st.dataframe(df[show].iloc[::-1], hide_index=True, width="stretch", height=260)
    st.markdown("**¿Qué filtros predicen mejor los aciertos?**")
    if n < 20:
        st.caption(f"Con {n} operaciones todavía es pronto: la revisión fiable empieza a las 20.")
    st.dataframe(filter_effectiveness(trades), hide_index=True, width="stretch")
    with st.expander("Borrar una operación"):
        sid = st.selectbox("Operación", [t["id"] for t in trades][::-1], key="fg_del_id")
        if st.button("Borrar", key="fg_del"):
            db.delete_journal(int(sid))
            st.rerun()
