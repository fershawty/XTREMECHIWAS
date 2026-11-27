"""
ui/grok_pick_page.py
====================
Página 🏆 GROK PICK: "dame la operación más rentable ahora", elegida por el equipo de agentes de Grok, a pedido.
No es automático: tú pulsas el botón, ves el plan y decides. Abrir en papel es opcional y respeta las reglas.
"""

from __future__ import annotations

from html import escape

import streamlit as st

from ui.mode import guide

VC = {"APRUEBA": "#3DDC97", "PRECAUCION": "#F5A623", "VETA": "#FF4D6D"}
VOTER_NAMES = {"G-MARKET": "MARKET", "G-WHALE": "WHALE", "G-FLOW": "FLOW", "G-FUNDING": "FUNDING", "G-TRAP": "TRAP",
               "G-ODDS": "ODDS"}


def _px(v) -> str:
    if v is None:
        return "n/a"
    return f"{v:,.6g}" if abs(v) < 1000 else f"{v:,.2f}"


def _hero(b: dict) -> str:
    vc = VC.get(b["veredicto"], "#8C95A8")
    dc = "#3DDC97" if b["direccion"] == "LONG" else "#FF4D6D"
    p = b.get("plan") or {}
    tps = p.get("tps") or []
    votes = "".join(
        f'<div class="v"><i>{VOTER_NAMES.get(k, k)}</i><b style="color:{"#3DDC97" if (b["votos"].get(k) or {}).get("voto", 0) > 0 else "#FF4D6D" if (b["votos"].get(k) or {}).get("voto", 0) < 0 else "#8C95A8"}">'
        f'{(b["votos"].get(k) or {}).get("voto", 0):+d}</b><s>{escape(str((b["votos"].get(k) or {}).get("linea", "n/a")))[:70]}</s></div>'
        for k in VOTER_NAMES)
    cells = [("ENTRADA", _px(p.get("entrada")), "#E6E9EF"), ("STOP LOSS", f'{_px(p.get("sl"))} (−{p.get("sl_pct") or 0:.2f}%)', "#FF4D6D")]
    for i, tp in enumerate(tps[:3]):
        cells.append((f"TP{i + 1} · cierra {tp.get('cierra')}%", f'{_px(tp.get("precio"))} (+{tp.get("pct") or 0:.2f}%)', "#3DDC97"))
    cells += [("APALANCAMIENTO", f'{p.get("apalancamiento", "n/a")}x', "#F2D27A"), ("MARGEN", f'${p.get("margen") or 0:,.2f}', "#E6E9EF"),
              ("RIESGO SI TOCA SL", f'${p.get("riesgo_usd") or 0:,.2f} ({b.get("riesgo_pct") or 0:.2f}%)', "#FF8A3D"),
              ("LIQUIDACIÓN", _px(p.get("liquidacion")), "#8C95A8")]
    grid = "".join(f'<div class="c"><i>{k}</i><b style="color:{c}">{v}</b></div>' for k, v, c in cells)
    kpis = [("P(TP1)", f'{b["prob_tp1"] if b["prob_tp1"] is not None else "n/a"}%', "#3DDC97"),
            ("GANANCIA ESPERADA", f'{"+" if (b["ev_usd"] or 0) >= 0 else "−"}${abs(b["ev_usd"] or 0):,.2f}', "#F2D27A"),
            ("VENTAJA", f'{(b["ventaja_r"] or 0):+.2f}R', "#B388FF"), ("P(SL)", f'{b["p_sl"] if b["p_sl"] is not None else "n/a"}%', "#FF4D6D")]
    kp = "".join(f'<div class="k"><i>{k}</i><b style="color:{c}">{v}</b></div>' for k, v, c in kpis)
    return f"""
<style>
.gp{{font-family:'JetBrains Mono',ui-monospace,monospace;background:radial-gradient(ellipse at 20% 0%,#16203a 0%,#0a0e16 55%,#070a10 100%);
 border:1px solid {vc}88;border-radius:14px;padding:16px 18px;box-shadow:0 0 34px {vc}22;animation:gpp 2.4s ease-in-out infinite}}
@keyframes gpp{{50%{{box-shadow:0 0 46px {vc}3a}}}}
.gp .hd{{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}}
.gp .t{{font-size:11px;letter-spacing:.2em;color:#8C95A8}} .gp .big{{font-size:38px;font-weight:800;color:#E6E9EF;letter-spacing:-.01em}}
.gp .pill{{display:inline-block;padding:3px 11px;border-radius:12px;font-weight:800;font-size:13px;margin-left:6px}}
.gp .ks{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:12px 0}}
.gp .k,.gp .c{{background:#06080d;border:1px solid rgba(148,163,184,.14);border-radius:9px;padding:7px 10px}}
.gp i{{font-style:normal;font-size:9px;letter-spacing:.14em;color:#566074;display:block}} .gp .k b{{font-size:22px}} .gp .c b{{font-size:14px}}
.gp .grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}}
.gp .vs{{display:grid;grid-template-columns:repeat(6,1fr);gap:6px;margin-top:12px}}
.gp .v{{background:#06080d;border:1px solid rgba(148,163,184,.12);border-radius:9px;padding:6px 8px}} .gp .v b{{font-size:18px;display:block}}
.gp .v s{{text-decoration:none;font-size:9px;color:#8C95A8;display:block;line-height:1.3;max-height:36px;overflow:hidden}}
.gp .why{{margin-top:10px;font-size:12px;color:#C9CED8}}
@media (max-width:900px){{.gp .ks,.gp .grid{{grid-template-columns:repeat(2,1fr)}} .gp .vs{{grid-template-columns:repeat(3,1fr)}}}}
</style>
<div class="gp">
 <div class="hd"><div><div class="t">🏆 LA OPERACIÓN MÁS RENTABLE AHORA · EQUIPO GROK · {escape(b["fuente"])}</div>
  <div class="big">{escape(b["cripto"])} <span class="pill" style="background:{dc}22;color:{dc}">{escape(str(b["direccion"]))}</span></div></div>
  <div style="text-align:right"><div class="t">G-JUDGE</div><span class="pill" style="background:{vc}22;color:{vc};font-size:16px">{escape(b["veredicto"])} · Σ {b["suma"]:+d}</span>
  <div class="t" style="margin-top:4px">confianza {b["confianza"]}% · mesa: {escape(str(b["mesa"]))}</div></div></div>
 <div class="ks">{kp}</div>
 <div class="grid">{grid}</div>
 <div class="vs">{votes}</div>
 <div class="why"><b style="color:{vc}">Por qué:</b> {escape(str(b["razon"]))}</div>
</div>"""


def render_grok_pick(svc, req) -> None:
    br = getattr(svc, "grok", None)
    if br is None or getattr(br, "pick", None) is None:
        st.error("GROK PICK no está disponible en esta instalación.")
        return
    pick = br.pick
    guide("Pulsa el botón y el equipo de agentes de Grok busca la operación más rentable ahora: revisa las mejores "
          "criptos, la mesa de 12 agentes corre cada una, los 6 votantes G-* votan de −2 a +2 y G-JUDGE decide. Se "
          "elige la de mayor ganancia esperada entre las aprobadas. No es automático: tú decides.",
          ["Elige cuántas criptos revisar (o una en particular) y pulsa «Buscar».",
           "Lee el plan: entrada, stop, TPs, apalancamiento y cuánto pierdes si toca el stop.",
           "Si quieres la opinión de Grok (el chat), copia el mensaje de abajo y pégalo en grok.com."],
          "«Más rentable» es la mayor ganancia esperada según 100,000 simulaciones con datos reales. No es una garantía.")
    c = st.columns([1, 1, 1.2, 1.3], vertical_alignment="bottom")
    top = c[0].slider("Criptos a revisar", 1, 8, 5, key="gp_top")
    both = c[1].checkbox("Probar LONG y SHORT", value=False, key="gp_both", help="Tarda el doble, pero revisa las dos direcciones.")
    sym = c[2].text_input("O una cripto en particular", value="", key="gp_sym", placeholder="SOL, ETH… (vacío = Grok elige)")
    if c[3].button("🏆 Buscar la operación más rentable", type="primary", key="gp_go", width="stretch"):
        with st.spinner("G-SCOUT elige candidatas · la mesa corre cada una · los G-* votan · G-JUDGE decide…"):
            try:
                st.session_state["gp_out"] = pick.find(top, both, sym.strip())
            except Exception as exc:  # noqa: BLE001
                st.error(f"No se pudo completar la búsqueda: {exc}")
    out = st.session_state.get("gp_out") or pick.last
    if not out:
        st.info("Todavía no hay búsqueda. Pulsa «Buscar la operación más rentable».")
        return
    best = out.get("mejor")
    if not best:
        st.warning("No hubo datos para elegir (¿sin conexión a Binance?). Nada se inventa.")
        return
    if best["veredicto"] != "APRUEBA":
        st.warning("⚠️ Ninguna candidata fue aprobada por G-JUDGE. Esta es la **menos mala**, solo para estudiar: "
                   "esperar también es operar bien.")
    if best.get("pausado"):
        st.error("PAUSADO POR TUS REGLAS · " + str(best["pausado"]))
    st.markdown(_hero(best), unsafe_allow_html=True)
    a, b, cc = st.columns([1, 1, 2])
    if a.button("📝 Abrir en PAPEL", key="gp_paper", disabled=best["veredicto"] != "APRUEBA",
                help="Solo si G-JUDGE aprobó y la mesa no dijo NO OPERAR. Nunca es real."):
        msg = pick.open_paper(best["id"])
        (st.success if "PAPEL" in msg.upper() else st.error)(msg)
    if b.button("🔁 Volver a buscar", key="gp_again"):
        st.session_state.pop("gp_out", None)
        st.rerun()
    cc.caption(f"Ronda #{best['id']} guardada · {out['nota']}")
    st.markdown("#### 🧠 Pídele su opinión a Grok (chat)")
    st.caption("Copia esto y pégalo en tu chat de Grok con el conector FG VIEW activo. Su veredicto aparece abajo.")
    st.code(pick.grok_prompt(best), language="text")
    gv = [v for v in (br.state().get("verdicts") or []) if (v.get("simbolo") or "") == best["simbolo"]]
    if gv:
        v = gv[-1]
        col = VC.get(v.get("veredicto"), "#8C95A8")
        st.markdown(f"**Opinión de Grok** · <span style='color:{col};font-weight:800'>{escape(str(v.get('veredicto')))}</span> · "
                    f"confianza {v.get('confianza')}% · {escape(str(v.get('at')))}", unsafe_allow_html=True)
        st.write(v.get("resumen", ""))
        for r in v.get("razones", [])[:4]:
            st.caption(f"✓ {r}")
        for r in v.get("riesgos", [])[:4]:
            st.caption(f"⚠ {r}")
    else:
        st.caption("Grok todavía no opina sobre esta operación.")
    st.markdown("#### 📊 Ranking de candidatas")
    rows = [{"#": i + 1, "Cripto": r["cripto"], "Dirección": r["direccion"], "G-JUDGE": r["veredicto"], "Σ": r["suma"],
             "P(TP1) %": r["prob_tp1"], "Ventaja R": r["ventaja_r"], "Ganancia esperada $": r["ev_usd"], "Mesa": r["mesa"]}
            for i, r in enumerate(out["ranking"])]
    st.dataframe(rows, hide_index=True, width="stretch")
    if best.get("tarjeta"):
        with st.expander("📇 Trade Card completa"):
            st.code(best["tarjeta"], language="text")
