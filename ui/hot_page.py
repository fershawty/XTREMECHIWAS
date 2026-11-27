"""
ui/hot_page.py
==============
Página 🔥 MEJORES PARA OPERAR: qué criptos están mejor AHORA en Binance Futures, explicado en palabras simples.

No dice si van a subir o bajar: dice cuáles se pueden operar bien (hay dinero moviéndose, se mueven lo suficiente
para llegar al objetivo y las operaciones son reales). Desde aquí mandas una a la mesa de 12 agentes.
"""

from __future__ import annotations

from html import escape

import streamlit as st

from ui.mode import guide

TIER_TXT = {"A": ("NIVEL A", "#3DDC97", "la más tranquila · 10–20x"),
            "B": ("NIVEL B", "#5AC8FA", "grande y líquida · 5–10x"),
            "C": ("NIVEL C", "#FF8A3D", "se mueve mucho · 3–5x")}
MOVE_COL = {"tranquila": "#8C95A8", "normal": "#3DDC97", "movida": "#F5A623", "muy movida": "#FF4D6D"}


def _bar(score: float) -> str:
    col = "#3DDC97" if score >= 65 else "#F5A623" if score >= 45 else "#8C95A8"
    return (f'<div style="height:6px;background:#161b27;border-radius:4px;overflow:hidden;margin-top:5px">'
            f'<div style="width:{max(2, min(100, score)):.0f}%;height:100%;background:{col}"></div></div>')


def _card(c, i: int) -> str:
    tn, tc, tdesc = TIER_TXT.get(c.tier, ("NIVEL C", "#FF8A3D", ""))
    chg_col = "#3DDC97" if c.change_24h >= 0 else "#FF4D6D"
    mv_col = MOVE_COL.get(c.movement, "#8C95A8")
    avisos = "".join(f'<div style="color:#F5A623;font-size:11.5px;margin-top:3px">⚠ {escape(a)}</div>' for a in c.flags)
    fuera = "" if c.listed else ('<span style="font-size:9px;color:#8C95A8;border:1px solid #333c4e;border-radius:6px;'
                                 'padding:1px 5px;margin-left:6px">no está en tus listas</span>')
    return f"""
<div style="background:#0a0e16;border:1px solid rgba(148,163,184,.14);border-left:3px solid {tc};border-radius:11px;
 padding:11px 14px;margin-bottom:8px;font-family:'JetBrains Mono',ui-monospace,monospace">
 <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">
  <div><span style="color:#566074;font-size:12px">#{i}</span>
   <b style="font-size:19px;color:#E6E9EF;margin-left:6px">{escape(c.coin)}</b>
   <span style="font-size:10px;color:{tc};border:1px solid {tc}55;border-radius:6px;padding:1px 6px;margin-left:8px">{tn}</span>{fuera}
   <div style="font-size:11px;color:#8C95A8;margin-top:2px">{escape(tdesc)}</div></div>
  <div style="text-align:right"><div style="font-size:17px;color:#E6E9EF">${c.price:,.6g}</div>
   <div style="color:{chg_col};font-size:13px">{c.change_24h:+.2f}% en 24 h</div></div>
 </div>
 <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:9px;font-size:12px">
  <div><span style="color:#566074">Dinero moviéndose</span><br><b style="color:#E6E9EF">{c.volume_txt}</b>
   <span style="color:#566074"> en 24 h</span></div>
  <div><span style="color:#566074">Qué tan movida</span><br><b style="color:{mv_col}">{c.movement}</b>
   <span style="color:#566074"> ({c.range_24h:.1f}% del día)</span></div>
  <div><span style="color:#566074">Qué tan operable</span><br><b style="color:#E6E9EF">{c.score:.0f}/100</b>{_bar(c.score)}</div>
 </div>
 <div style="color:#C9CED8;font-size:12px;margin-top:7px">{escape(c.why)}</div>{avisos}
</div>"""


def render_hot(svc, req) -> None:
    hot = getattr(svc, "hotlist", None)
    if hot is None:
        st.error("La lista de criptos no está disponible en esta instalación.")
        return
    guide("Esta lista te dice qué criptos están MEJOR PARA OPERAR ahora mismo en Binance, no cuáles van a subir. "
          "Mira tres cosas: cuánto dinero se está moviendo (si hay poco, no puedes salir sin perder), cuánto se "
          "mueve el precio (si no se mueve, no llega a tu objetivo) y si las operaciones son reales.",
          ["Pulsa «Actualizar» y mira las primeras de la lista.",
           "Elige una con buen volumen y movimiento «normal» o «movida».",
           "Pulsa «Analizar con la mesa» y los 12 agentes te dan el plan."],
          "Nivel A (BTC, ETH): la más tranquila, aguanta 10–20x. Nivel B: grande y líquida, 5–10x. "
          "Nivel C: se mueve mucho, solo 3–5x y con stop más ancho.")
    c = st.columns([1, 1.1, 1.1, 1], vertical_alignment="bottom")
    n = c[0].slider("Cuántas mostrar", 5, 40, 15, key="hot_n")
    nivel = c[1].selectbox("Nivel", ["Todos", "A · tranquilas", "B · grandes", "C · movidas"], key="hot_tier")
    solo_mias = c[2].checkbox("Solo las de mis listas", value=False, key="hot_mine",
                              help="Las que FG VIEW ya tiene clasificadas con su apalancamiento y su stop.")
    if c[3].button("🔄 Actualizar", type="primary", key="hot_go", width="stretch"):
        with st.spinner("Leyendo Binance Futures…"):
            hot.fetch(force=True)
    coins, src = hot.top(n, only_listed=solo_mias, tier=nivel[0] if nivel != "Todos" else "")
    if not coins:
        st.warning(f"No hay datos ahora mismo: {src}. Revisa tu internet y vuelve a pulsar «Actualizar». "
                   "Nada se inventa: prefiero no mostrarte una lista falsa.")
        return
    st.caption(f"Fuente: {src} · ordenadas por qué tan operables están, no por cuánto subieron.")
    top3 = coins[:3]
    k = st.columns(len(top3))
    for col, c3 in zip(k, top3):
        col.metric(c3.coin, f"{c3.score:.0f}/100", f"{c3.change_24h:+.2f}% · {c3.movement}")
    st.markdown("".join(_card(c2, i + 1) for i, c2 in enumerate(coins)), unsafe_allow_html=True)
    st.markdown("#### 🎯 Analizar una con la mesa de 12 agentes")
    a, b = st.columns([2, 1], vertical_alignment="bottom")
    elegida = a.selectbox("Cripto", [c4.coin for c4 in coins], key="hot_pick")
    if b.button("Analizar con la mesa", key="hot_run", width="stretch"):
        with st.spinner(f"Los 12 agentes analizan {elegida}…"):
            try:
                dec = svc.desk.run(f"{elegida}/USDT", "crypto", float(st.session_state.get("equity", 1000)),
                                   operable=True)
                st.session_state["dk_last"] = dec
                st.success(dec.summary())
                if dec.ticket is not None:
                    st.markdown("  \n".join(dec.ticket.lines()))
                st.caption("El análisis completo, con sus animaciones, está en la página FG DESK.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"No se pudo analizar {elegida}: {exc}")
    with st.expander("❓ Cómo leer esta lista"):
        st.markdown("""
- **Dinero moviéndose (volumen 24 h):** cuánto se compró y vendió. Mucho volumen = puedes entrar y salir sin que el
  precio se te mueva en contra. Poco volumen con apalancamiento es peligroso.
- **Qué tan movida:** cuánto se movió el precio entre su máximo y su mínimo del día.
  *Tranquila* puede no llegar a tu objetivo; *muy movida* salta tu stop con facilidad.
- **Qué tan operable (0–100):** junta volumen, movimiento y número de operaciones reales.
  **No predice el precio**: solo dice si las condiciones son buenas para operar.
- **Nivel:** cuánto apalancamiento y qué stop usa FG VIEW con esa cripto. Las que no están en tus listas se tratan
  como Nivel C, que es lo más prudente.
- **Avisos ⚠:** cosas que conviene mirar antes de entrar (volumen bajo, movimiento extremo, pocas operaciones).
""")
