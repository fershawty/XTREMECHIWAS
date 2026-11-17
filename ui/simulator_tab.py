"""
ui/simulator_tab.py
===================
Pestaña "Simulador": operaciones LONG / SHORT con tu capital.

Rellena capital, margen (o % de riesgo), apalancamiento, entrada, TP y SL y verás
cuánto ganas en el TP, cuánto pierdes en el SL, el ROI, la liquidación, el R:R,
una tabla de escenarios, la comparación de apalancamientos y la proyección de
varias operaciones. Nunca inventa precios: la entrada la pones tú o la carga la
señal actual.
"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from engine.position_simulator import (ALL_COINS, DISCLAIMER, CRYPTO_CATALOG, SimInput, TakeProfit, base_coin,
                                       coin_category, format_projection, format_report, project, simulate)

OTHER = "Otra…"
K = "sim_"  # Prefijo de las claves de estado


def parse_num(text: str | None) -> float | None:
    """Convierte '0,000012', '100 000' o '1,234.5' en número (None si está vacío o no es válido)."""
    if text is None:
        return None
    s = str(text).strip().replace(" ", "").replace("$", "")
    if not s:
        return None
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


def _coin_label(c: str) -> str:
    if c == OTHER:
        return OTHER
    cat = coin_category(c)
    return f"{c} · {cat}" if cat else c


def _load_prefill(pre: dict) -> None:
    """Callback: copia el setup de la señal actual a los campos del simulador."""
    coin = base_coin(pre.get("coin", ""))
    if coin in ALL_COINS:
        st.session_state[K + "coin"] = coin
    else:
        st.session_state[K + "coin"] = OTHER
        st.session_state[K + "coin_other"] = coin
    st.session_state[K + "dir"] = pre["direction"]
    st.session_state[K + "unit"] = "Precio"
    st.session_state[K + "entry"] = f"{pre['entry']}"
    st.session_state[K + "sl"] = f"{pre['sl']}"
    st.session_state[K + "tp1"] = f"{pre['tp1']}"
    st.session_state[K + "tp1p"] = 50.0 if pre.get("tp2") else 100.0
    st.session_state[K + "tp2"] = f"{pre['tp2']}" if pre.get("tp2") else ""
    st.session_state[K + "tp2p"] = 50.0 if pre.get("tp2") else 0.0
    st.session_state[K + "tp3"] = ""
    st.session_state[K + "tp3p"] = 0.0
    if pre.get("capital"):
        st.session_state[K + "capital"] = float(pre["capital"])
    if pre.get("leverage"):
        st.session_state[K + "lev"] = int(round(pre["leverage"]))


def _projection_chart(bands, capital: float) -> go.Figure:
    x = list(range(bands.shape[1]))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=bands[2], line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=bands[0], fill="tonexty", fillcolor="rgba(212,175,55,0.12)", line=dict(width=0),
                             name="Rango 10 %–90 %", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=bands[1], line=dict(color="#D4AF37", width=2), name="Esperado",
                             hovertemplate="Operación %{x}: $%{y:,.2f}<extra></extra>"))
    fig.add_hline(y=capital, line=dict(color="#6B6352", dash="dot"))
    fig.update_layout(height=280, margin=dict(l=8, r=8, t=8, b=8), paper_bgcolor="#0A0A0A", plot_bgcolor="#0A0A0A",
                      font=dict(color="#A89F8A", size=10), legend=dict(orientation="h", y=1.08),
                      xaxis=dict(title="Operaciones", gridcolor="#1A1712"),
                      yaxis=dict(gridcolor="#1A1712", side="right", tickprefix="$"))
    return fig


def render_simulator(prefill: dict | None = None) -> None:
    """Dibuja la pestaña completa."""
    from ui.easy_tab import render_glossary

    render_glossary("sim")
    st.session_state.setdefault(K + "coin", "BTC")
    st.session_state.setdefault(K + "dir", "LONG")
    st.session_state.setdefault(K + "mode", "Margen fijo")
    st.session_state.setdefault(K + "unit", "Precio")
    st.session_state.setdefault(K + "tp1p", 100.0)
    st.session_state.setdefault(K + "tp2p", 0.0)
    st.session_state.setdefault(K + "tp3p", 0.0)
    for key, default in (("capital", 1000.0), ("lev", 10), ("margin", 100.0), ("risk", 1.0), ("fee", 0.05)):
        st.session_state.setdefault(K + key, default)

    if prefill:
        st.button(f"Cargar el setup de la señal actual ({prefill['direction']} {base_coin(prefill['coin'])})",
                  key=K + "load", on_click=_load_prefill, args=(prefill,))

    # --- Activo, dirección y modo ---
    c1, c2, c3, c4 = st.columns([1.3, 1, 1.2, 1])
    coins = ALL_COINS + [OTHER]
    coin = c1.selectbox("Cripto", coins, key=K + "coin", format_func=_coin_label)
    if coin == OTHER:
        coin = c1.text_input("Símbolo", key=K + "coin_other", placeholder="ej. NOT, KAITO…").strip().upper() or "?"
    direction = c2.segmented_control("Dirección", ["LONG", "SHORT"], key=K + "dir") or "LONG"
    mode = c3.segmented_control("Tamaño por", ["Margen fijo", "Riesgo fijo %"], key=K + "mode") or "Margen fijo"
    leverage = c4.number_input("Apalancamiento (x)", min_value=1, max_value=125, step=1, key=K + "lev")

    # --- Capital, margen / riesgo y comisión ---
    c1, c2, c3, c4 = st.columns(4)
    capital = c1.number_input("Capital total (USDT)", min_value=1.0, step=50.0, key=K + "capital")
    margin = risk_pct = None
    if mode == "Margen fijo":
        margin = c2.number_input("Margen por operación (USDT)", min_value=0.01, step=10.0, key=K + "margin")
        c2.caption(f"= {margin / capital * 100:.2f}% del capital")
    else:
        risk_pct = c2.number_input("Riesgo por operación (% del capital)", min_value=0.05, max_value=100.0,
                                   step=0.25, key=K + "risk")
        c2.caption(f"= pierdes {capital * risk_pct / 100:,.2f} USDT si salta el SL")
    fee_pct = c3.number_input("Comisión por lado (%)", min_value=0.0, max_value=1.0, step=0.01,
                              format="%.3f", key=K + "fee")
    unit = c4.segmented_control("TP / SL en", ["Precio", "%"], key=K + "unit") or "Precio"

    # --- Niveles ---
    c1, c2 = st.columns(2)
    entry = parse_num(c1.text_input("Precio de entrada", key=K + "entry", placeholder="precio actual"))
    sl_raw = parse_num(c2.text_input("Stop Loss" + (" (% de distancia)" if unit == "%" else ""), key=K + "sl",
                                     placeholder="ej. 2" if unit == "%" else "precio"))
    tp_cols = st.columns(6)
    tps_raw = []
    for i in range(3):
        price_txt = tp_cols[i * 2].text_input(f"TP{i + 1}" + (" (%)" if unit == "%" else ""), key=K + f"tp{i + 1}",
                                              placeholder="opcional" if i else ("ej. 4" if unit == "%" else "precio"))
        close = tp_cols[i * 2 + 1].number_input(f"% cierre TP{i + 1}", 0.0, 100.0, step=5.0, key=K + f"tp{i + 1}p")
        tps_raw.append((parse_num(price_txt), close))

    if entry is None:
        st.info("Introduce el **precio actual de entrada** (o carga el setup de la señal). "
                "El simulador nunca inventa precios.")
        return

    sign = 1 if direction == "LONG" else -1
    if unit == "%":
        sl = entry * (1 - sign * sl_raw / 100) if sl_raw else None
        tps = [TakeProfit(entry * (1 + sign * v / 100), p) for v, p in tps_raw if v]
    else:
        sl = sl_raw
        tps = [TakeProfit(v, p) for v, p in tps_raw if v]

    missing = [name for name, ok in (("Stop Loss", sl), ("al menos un Take Profit", tps)) if not ok]
    if missing:
        st.info("Falta: " + ", ".join(missing) + ".")
        return

    inp = SimInput(coin=coin, direction=direction, capital=capital, leverage=float(leverage), entry=entry,
                   stop_loss=sl, take_profits=tps, margin=margin, risk_pct=risk_pct, fee=fee_pct / 100.0)
    try:
        res = simulate(inp)
    except ValueError as exc:
        st.warning(str(exc))
        return

    left, right = st.columns([1.7, 1], gap="large")
    left.markdown(format_report(res))
    with right:
        from ui.fgview_tab import GOLD_LIGHT, GREEN, RED, price_ladder

        levels = [("LIQUIDACIÓN", res.liquidation, RED), ("SL", res.sl.price, RED), ("ENTRADA", entry, "#F5F1E6"),
                  ("BREAK-EVEN", res.break_even, "#A89F8A")]
        levels += [(t.label, t.price, GREEN if i < len(res.tps) else GOLD_LIGHT) for i, t in enumerate(res.tps, 1)]
        st.plotly_chart(price_ladder(levels, entry), width="stretch", config={"displayModeBar": False}, key="sim_ladder")

    # --- Proyección de varias operaciones ---
    with st.expander("🧮 Proyección de varias operaciones (interés compuesto)"):
        rr_default = round(res.rr, 2) if res.rr == res.rr and res.rr < 100 else 2.0
        risk_default = round(abs(res.sl.account_pct), 2) if res.sl.pnl_net < 0 else 1.0
        c1, c2, c3, c4 = st.columns(4)
        wr = c1.number_input("Win rate (%)", 1.0, 99.0, 50.0, step=1.0, key=K + "pwr")
        rr = c2.number_input("R:R (1:x)", 0.1, 20.0, float(max(0.1, rr_default)), step=0.1, key=K + "prr")
        rk = c3.number_input("Riesgo por operación (%)", 0.05, 50.0, float(max(0.05, risk_default)), step=0.25,
                             key=K + "prk")
        n = c4.number_input("Nº de operaciones", 1, 1000, 50, step=10, key=K + "pn")
        try:
            pr = project(capital, wr, rr, rk, int(n))
            st.markdown(format_projection(capital, wr, rr, rk, int(n), pr))
            st.plotly_chart(_projection_chart(pr.bands, capital), width="stretch", config={"displayModeBar": False})
        except ValueError as exc:
            st.warning(str(exc))


__all__ = ["render_simulator", "parse_num", "CRYPTO_CATALOG", "DISCLAIMER"]
