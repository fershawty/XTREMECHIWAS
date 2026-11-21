"""
ui/easy_tab.py
==============
Pestaña "✨ Modo fácil": pones tu capital, pulsas un botón y FG VIEW te dice en
palabras simples cuál es la operación más recomendada, cómo hacerla en Binance,
qué ROI es realista y si la estrategia funcionó en los últimos días.
"""

from __future__ import annotations

import streamlit as st

from engine.fgview.advisor import EASY_COINS
from engine.fgview.glossary import GLOSSARY
from engine.position_simulator import fmt_usd

LIGHT_STYLE = {"verde": ("#3FB950", "rgba(63,185,80,0.08)"), "amarillo": ("#F2D27A", "rgba(242,210,122,0.07)"),
               "rojo": ("#F85149", "rgba(248,81,73,0.08)")}
ICON = {"verde": "🟢", "amarillo": "🟡", "rojo": "🔴"}


def render_glossary(key: str = "gl") -> None:
    """Glosario plegable con todos los términos técnicos."""
    with st.expander("📖 ¿Qué significa cada cosa? (glosario)"):
        term = st.selectbox("Busca un término", ["Todos"] + list(GLOSSARY), key=f"{key}_term")
        items = GLOSSARY.items() if term == "Todos" else [(term, GLOSSARY[term])]
        st.markdown("\n".join(f"- **{k}**: {v}" for k, v in items))


def _card(advisor, r) -> None:
    s = advisor.summary(r)
    color, bg = LIGHT_STYLE[r.light]
    wins = "".join(f"<li>{w}</li>" for w in s["win"])
    st.markdown(
        f'<div style="border:1px solid {color};background:{bg};border-radius:14px;padding:1.1rem 1.3rem">'
        f'<div style="font-size:1.45rem;font-weight:800;color:{color}">{s["headline"]}</div>'
        f'<div style="margin-top:.6rem;font-size:1rem">{s["what"]}</div>'
        f'<div style="margin-top:.8rem;line-height:1.7">💰 {s["money"]}<br>🎯 {s["entry"]}'
        f'<ul style="margin:.3rem 0 .3rem 1rem">{wins}</ul>🛡️ {s["loss"]}<br>🏆 {s["best"]}<br>'
        f'📊 {s["odds"]}<br>⚖️ {s["ev"]}<br>🔒 {s["liq"]}</div></div>',
        unsafe_allow_html=True,
    )
    if r.light == "verde":
        st.markdown("**¿Por qué?**  \n" + "  \n".join(f"✅ {x}" for x in r.reasons))
        st.markdown("**Cómo hacerlo en Binance**  \n" + "  \n".join(f"{i}. {t}" for i, t in enumerate(s["steps"], 1)))
    else:
        st.markdown("**¿Por qué no entrar todavía?**  \n" + "  \n".join(f"• {w}" for w in advisor.why_not(r)))


def _roi_section(advisor, r) -> None:
    with st.expander("🎯 ¿Y si quiero un ROI de 10%, 15% o 20%?"):
        st.caption("El ROI es sobre el margen. Para ganar más hace falta más movimiento o más apalancamiento: aquí ves "
                   "cuántas veces se alcanzó cada objetivo antes que el Stop Loss en 30 minutos.")
        st.dataframe(advisor.roi_options(r), hide_index=True, width="stretch")
        st.caption(f"Recuerda: un ROI del 15% con un margen de {fmt_usd(r.plan.margin)} es "
                   f"{fmt_usd(r.plan.margin * 0.15, True)}, el {r.plan.margin * 0.15 / r.plan.capital * 100:.1f}% "
                   f"de tu cuenta.")


def _validation_section(services, req, market: str) -> None:
    with st.expander("🔬 ¿Funcionó la estrategia en los últimos días? (validación histórica)"):
        st.caption("Repite FG VIEW sobre velas pasadas con los mismos filtros, el mismo plan y la misma gestión "
                   "(TP 50/30/20, SL a break-even, 30 min) y mide si ganó dinero.")
        c1, c2, c3 = st.columns([1, 1, 1], vertical_alignment="bottom")
        coin = c1.selectbox("Cripto", EASY_COINS, key="val_coin")
        days = c2.selectbox("Días de historia", [3, 7, 14], index=1, key="val_days")
        go = c3.button("Validar", key="val_go", width="stretch")
        if go:
            with st.spinner("Descargando historia y simulando operaciones…"):
                try:
                    st.session_state["val_result"] = services.backtester.run_symbol(
                        services.loader, coin, market, int(days), req.contract, req.exchange)
                except Exception as exc:  # noqa: BLE001
                    st.error(f"No se pudo validar: {exc}")
        v = st.session_state.get("val_result")
        if not v:
            return
        m = v.metrics
        if not m.get("trades"):
            st.info("No hubo ninguna señal válida en ese periodo.")
        else:
            pf = "∞" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
            verdict = ("✅ La estrategia ganó dinero en este periodo." if m["total"] > 0 else
                       "❌ La estrategia perdió dinero en este periodo: mejor no operar esta cripto así ahora.")
            cols = st.columns(5)
            for col, (k, val) in zip(cols, [("Operaciones", f"{m['trades']} ({m['per_day']:.1f}/día)"),
                                            ("Win rate", f"{m['win_rate']:.1f}%"), ("Profit factor", pf),
                                            ("Resultado", f"{m['pnl_pct']:+.1f}% cuenta"),
                                            ("Drawdown", fmt_usd(-m["max_drawdown"]))]):
                col.metric(k, val)
            st.markdown(f"**{verdict}**  \n{v.symbol} · {v.days:.1f} días · capital simulado $1,000")
            a, b = st.columns(2)
            a.markdown("**Por nivel de confianza**")
            a.dataframe(v.by_confidence, hide_index=True, width="stretch")
            b.markdown("**Por sesión del día**")
            b.dataframe(v.by_session, hide_index=True, width="stretch")
            st.markdown("**Qué filtros acompañan a los aciertos**")
            st.dataframe(v.by_filter, hide_index=True, width="stretch")
        for n in v.notes:
            st.caption(f"ℹ️ {n}")


def render_easy(services, req) -> None:
    advisor = services.advisor
    st.markdown("### ✨ La operación más recomendada para tu capital")
    st.caption("Pon tu capital y pulsa el botón. FG VIEW revisa las criptos más líquidas a la vez, elige la mejor y te "
               "la explica sin tecnicismos.")
    c1, c2 = st.columns([1, 1])
    capital = c1.number_input("¿Cuánto capital tienes? (USDT)", min_value=10.0, value=float(req.equity or 500.0),
                              step=50.0, key="easy_capital")
    coins = c2.multiselect("Criptos a revisar", EASY_COINS, default=EASY_COINS, key="easy_coins")
    market = req.market if req.market in ("crypto", "demo") else "crypto"
    if st.button("🔍 Buscar la mejor operación ahora", type="primary", width="stretch", key="easy_go"):
        with st.spinner("Revisando criptos y midiendo probabilidades…"):
            cands, errors = advisor.scan(capital, market, coins or EASY_COINS, req.contract, req.exchange)
        st.session_state["easy_result"] = (cands, errors, market)

    stored = st.session_state.get("easy_result")
    if stored:
        cands, errors, mk = stored
        if mk == "demo":
            st.warning("Modo DEMO: datos sintéticos. Cambia el mercado a Cripto para operaciones reales.")
        if not cands:
            st.error("No se pudieron obtener datos de ninguna cripto. " + " · ".join(errors[:3]))
        else:
            best = cands[0]
            if best.light != "verde":
                st.info("Ahora mismo **ninguna cripto da una entrada clara**. Lo mejor es esperar: no operar también "
                        "es una decisión ganadora. Esta es la que está más cerca:")
            _card(advisor, best)
            _roi_section(advisor, best)
            with st.expander("Ver el resto de criptos revisadas"):
                for c in cands[1:]:
                    st.markdown(f"{ICON[c.light]} **{c.coin}** {c.plan.direction} · confianza {c.report.score}/100 · "
                                f"TP1 histórico {c.stats.reach[0]:.0f}% · valor esperado {c.ev:+.2f} USDT")
            if errors:
                st.caption("Sin datos: " + " · ".join(errors))
    _validation_section(services, req, market)
    render_glossary("easy")
    st.caption("Las probabilidades son estadística de los últimos días, no una garantía. "
               "Simulación educativa, no es asesoría financiera.")
