"""
ui/ladder_tab.py
================
Pestaña "📈 Crecimiento exponencial": reto escalera sin liquidaciones (con easter egg ✡).
"""

from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from engine.ladder import LadderConfig, ladder_rules, plan_ladder
from engine.position_simulator import ALL_COINS, fmt_usd
from engine.recommender import journal_metrics, tier_label


def render_ladder(db) -> None:
    st.markdown("### 📈 Crecimiento exponencial · reto escalera protegido")
    st.caption("Haz crecer un capital pequeño escalón a escalón SIN que te liquiden: el Stop Loss siempre salta mucho "
               "antes que la liquidación y una parte de cada ganancia va a una caja fuerte.")

    m = journal_metrics(db.journal())
    wr_default = round(m["win_rate"]) if m.get("trades", 0) >= 10 else 55
    c1, c2, c3 = st.columns(3)
    capital = c1.number_input("Capital inicial del reto (USDT)", min_value=5.0, value=20.0, step=5.0, key="lad_cap")
    target = c2.number_input("Objetivo (USDT)", min_value=10.0, value=200.0, step=50.0, key="lad_target")
    coin = c3.selectbox("Cripto", ALL_COINS, index=ALL_COINS.index("SOL"), key="lad_coin",
                        format_func=lambda c: f"{c} · {tier_label(c)}")
    c1, c2, c3, c4 = st.columns(4)
    wr = c1.slider("Tu % de aciertos", 30, 90, int(wr_default), key="lad_wr",
                   help="Usa el de tu Bitácora (se rellena solo a partir de 10 operaciones).")
    vault = c2.slider("% de cada ganancia a la caja fuerte", 0, 70, 30, step=5, key="lad_vault")
    tp_level = c3.selectbox("Salida de cada escalón", [1, 2, 3], index=1, key="lad_tp",
                            format_func=lambda x: f"TP{x} del nivel")
    max_trades = c4.number_input("Máx. operaciones", 10, 300, 100, step=10, key="lad_max")
    if m.get("trades", 0) >= 10:
        st.caption(f"% de aciertos tomado de tu Bitácora ({m['trades']} operaciones).")

    try:
        r = plan_ladder(LadderConfig(capital, target, coin, float(wr), float(vault), int(tp_level),
                                     max_trades=int(max_trades)))
    except ValueError as exc:
        st.warning(str(exc))
        return

    sp, sc = r.sim_protected, r.sim_classic
    left, right = st.columns(2)
    with left:
        trades = "—" if math.isnan(sp["median_trades"]) else f"~{sp['median_trades']:.0f}"
        kept = "—" if math.isnan(sp["kept_if_fail"]) else fmt_usd(sp["kept_if_fail"])
        st.markdown(
            f'<div style="border:1px solid #3FB950;background:rgba(63,185,80,.07);border-radius:14px;padding:1rem 1.2rem">'
            f'<div style="color:#3FB950;font-weight:800;font-size:1.15rem">🛡️ ESCALERA PROTEGIDA FG</div>'
            f'<div style="font-size:2rem;font-weight:800;margin:.3rem 0">{sp["success_pct"]:.0f}% de éxito</div>'
            f'Quemar la cuenta: <b>{sp["ruin_pct"]:.0f}%</b> · Operaciones: <b>{trades}</b><br>'
            f'Si no llegas al objetivo, conservas de media <b>{kept}</b><br>'
            f'{r.leverage}x · SL −{r.sl_pct:.2f}% · TP +{r.tp_pct:.2f}% · liquidación a −{r.liq_pct:.1f}%<br>'
            f'Escalón ganado <b>+{r.roi_win * 100:.1f}%</b> · escalón perdido <b>{r.roi_loss * 100:.1f}%</b></div>',
            unsafe_allow_html=True)
    with right:
        st.markdown(
            f'<div style="border:1px solid #F85149;background:rgba(248,81,73,.07);border-radius:14px;padding:1rem 1.2rem">'
            f'<div style="color:#F85149;font-weight:800;font-size:1.15rem">💀 ESCALERA CLÁSICA (todo dentro, {sc["leverage"]:g}x)</div>'
            f'<div style="font-size:2rem;font-weight:800;margin:.3rem 0">{sc["success_pct"]:.0f}% de éxito</div>'
            f'Quemar la cuenta: <b>{sc["ruin_pct"]:.0f}%</b><br>'
            f'Escalón ganado +{sc["roi_win"] * 100:.1f}% · escalón perdido <b>−100% (liquidación)</b><br>'
            f'Una sola operación mala te devuelve a cero. Por eso te liquidaban.</div>',
            unsafe_allow_html=True)
    st.caption(f"Win rate mínimo para que la escalera protegida crezca: {r.breakeven_wr:.1f}% · "
               f"simulación de 5.000 caminos con tu % de aciertos.")
    for n in r.notes:
        st.warning(n)

    st.markdown(f"**Plan escalón por escalón** ({len(r.steps)} escalones ganados hasta {fmt_usd(target)})")
    st.dataframe(pd.DataFrame([{
        "Escalón": s.n, "Margen": fmt_usd(s.stake), "Apalanc.": f"{s.leverage}x", "Posición": fmt_usd(s.position),
        "Si gana": fmt_usd(s.win, True), "A caja fuerte": fmt_usd(s.vault_add), "Si pierde": fmt_usd(s.loss, True),
        "Caja fuerte": fmt_usd(s.vault_total), "Total": fmt_usd(s.total_after_win),
    } for s in r.steps]), hide_index=True, width="stretch", height=320)
    st.markdown("**Reglas de oro**  \n" + "  \n".join(f"{i}. {t}" for i, t in enumerate(ladder_rules(r), 1)))
    st.caption("Las probabilidades dependen de tu % de aciertos real. Simulación educativa, no es asesoría financiera.")
    st.markdown(STAR_OF_DAVID, unsafe_allow_html=True)


# Easter egg: estrella de David dorada, discreta en la esquina del panel (brilla al pasar el ratón)
STAR_OF_DAVID = """
<style>
.fg-egg { position: relative; height: 0; }
.fg-egg svg { position: absolute; right: 4px; bottom: 4px; width: 26px; height: 26px; opacity: .14;
  transition: opacity .4s ease, transform .6s ease, filter .4s ease; cursor: default; }
.fg-egg svg:hover { opacity: 1; transform: rotate(30deg) scale(1.35); filter: drop-shadow(0 0 6px #D4AF37); }
</style>
<div class="fg-egg"><svg viewBox="0 0 100 100" role="img" aria-label="Estrella de David">
<title>✡</title>
<polygon points="50,6 89,73 11,73" fill="none" stroke="#D4AF37" stroke-width="6" stroke-linejoin="round"/>
<polygon points="50,94 11,27 89,27" fill="none" stroke="#F2D27A" stroke-width="6" stroke-linejoin="round"/>
</svg></div>
"""
