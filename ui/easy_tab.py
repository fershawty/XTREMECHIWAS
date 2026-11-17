"""
ui/easy_tab.py
==============
Pestaña "✨ Modo fácil": pones tu capital, pulsas un botón y FG VIEW te dice en
palabras simples cuál es la operación más recomendada ahora mismo, cómo hacerla
en Binance y qué significa cada término.
"""

from __future__ import annotations

import streamlit as st

from engine.easy_mode import EASY_COINS, GLOSSARY, plain_summary, scan_best
from engine.recommender import daily_guard

LIGHT_STYLE = {"verde": ("#3FB950", "rgba(63,185,80,0.08)"), "amarillo": ("#F2D27A", "rgba(242,210,122,0.07)"),
               "rojo": ("#F85149", "rgba(248,81,73,0.08)")}


def render_glossary(key: str = "gl") -> None:
    """Glosario plegable con todos los términos técnicos."""
    with st.expander("📖 ¿Qué significa cada cosa? (glosario)"):
        term = st.selectbox("Busca un término", ["Todos"] + list(GLOSSARY), key=f"{key}_term")
        items = GLOSSARY.items() if term == "Todos" else [(term, GLOSSARY[term])]
        st.markdown("\n".join(f"- **{k}**: {v}" for k, v in items))


def _card(c) -> None:
    s = plain_summary(c)
    color, bg = LIGHT_STYLE[c.light]
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
    if c.light == "verde":
        st.markdown("**¿Por qué?**  \n" + "  \n".join(f"✅ {r}" for r in c.reasons))
        st.markdown("**Cómo hacerlo en Binance**  \n" + "  \n".join(f"{i}. {t}" for i, t in enumerate(s["steps"], 1)))
    else:
        missing = [ch.name for ch in c.report.checks if not ch.passed]
        why = c.report.blocks or [f"Solo se cumplen {c.report.passed} de 6 señales (faltan: {', '.join(missing)})."]
        if c.report.enter and c.ev <= 0:
            why = ["Las señales se cumplen, pero en situaciones parecidas tocó más veces el Stop Loss que el objetivo."]
        st.markdown("**¿Por qué no entrar todavía?**  \n" + "  \n".join(f"• {w}" for w in why))


def render_easy(pipeline, req) -> None:
    st.markdown("### ✨ La operación más recomendada para tu capital")
    st.caption("Pon tu capital y pulsa el botón. FG VIEW revisa las criptos más líquidas, elige la mejor y te la "
               "explica sin tecnicismos.")
    c1, c2 = st.columns([1, 1])
    capital = c1.number_input("¿Cuánto capital tienes? (USDT)", min_value=10.0, value=float(req.equity or 500.0),
                              step=50.0, key="easy_capital")
    coins = c2.multiselect("Criptos a revisar", EASY_COINS, default=EASY_COINS, key="easy_coins")
    if st.button("🔍 Buscar la mejor operación ahora", type="primary", width="stretch", key="easy_go"):
        market = req.market if req.market in ("crypto", "demo") else "crypto"
        news = pipeline.news.check()
        guard = daily_guard(pipeline.db.journal_today(), capital)
        with st.spinner("Revisando criptos y midiendo probabilidades…"):
            cands, errors = scan_best(pipeline.feeder, capital, market, coins or EASY_COINS, req.contract,
                                      req.exchange, news.message if news.blocked else None, guard)
        st.session_state["easy_result"] = (capital, cands, errors, market)

    stored = st.session_state.get("easy_result")
    if stored:
        cap, cands, errors, market = stored
        if market == "demo":
            st.warning("Modo DEMO: datos sintéticos. Cambia el mercado a Cripto para operaciones reales.")
        if not cands:
            st.error("No se pudieron obtener datos de ninguna cripto. " + " · ".join(errors[:3]))
        else:
            best = cands[0]
            if best.light != "verde":
                st.info("Ahora mismo **ninguna cripto da una entrada clara**. Lo mejor es esperar: no operar también "
                        "es una decisión ganadora. Esta es la que está más cerca:")
            _card(best)
            with st.expander("Ver el resto de criptos revisadas"):
                for c in cands[1:]:
                    icon = {"verde": "🟢", "amarillo": "🟡", "rojo": "🔴"}[c.light]
                    st.markdown(f"{icon} **{c.coin}** {c.plan.direction} · confianza {c.report.score}/100 · "
                                f"TP1 histórico {c.tp_stats[0].tp_first:.0f}% · valor esperado {c.ev:+.2f} USDT")
            if errors:
                st.caption("Sin datos: " + " · ".join(errors))
    render_glossary("easy")
    st.caption("Las probabilidades son estadística de los últimos días, no una garantía. "
               "Simulación educativa, no es asesoría financiera.")
