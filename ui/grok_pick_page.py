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


def _hero(b: dict, who: str = "MESA", grok: dict | None = None) -> str:
    gp = (grok or {}).get("plan_grok")
    if gp:  # Grok puso sus precios: el plan y la expectativa son los suyos (ya validados y simulados por FG VIEW)
        b = {**b, "plan": gp["plan"], **{k: gp["esperado"].get(k, b.get(k)) for k in ("prob_tp1", "p_sl", "ev_usd", "ventaja_r")},
             "riesgo_pct": b.get("riesgo_pct")}
    vj = (grok or {}).get("veredicto") or b["veredicto"]
    vc = VC.get(vj, "#8C95A8")
    dc = "#3DDC97" if b["direccion"] == "LONG" else "#FF4D6D"
    p = b.get("plan") or {}
    tps = p.get("tps") or []
    votes = "".join(
        f'<div class="v"><i>{VOTER_NAMES.get(k, k)}</i><b style="color:{"#3DDC97" if (b["votos"].get(k) or {}).get("voto", 0) > 0 else "#FF4D6D" if (b["votos"].get(k) or {}).get("voto", 0) < 0 else "#8C95A8"}">'
        f'{(b["votos"].get(k) or {}).get("voto", 0):+d}</b><s>{escape(str((b["votos"].get(k) or {}).get("linea", "n/a")))[:70]}</s></div>'
        for k in VOTER_NAMES)
    pre = "🤖 " if (grok or {}).get("plan_grok") else ""
    cells = [(f"{pre}ENTRADA", _px(p.get("entrada")), "#E6E9EF"),
             (f"{pre}STOP LOSS", f'{_px(p.get("sl"))} (−{p.get("sl_pct") or 0:.2f}%)', "#FF4D6D")]
    for i, tp in enumerate(tps[:3]):
        cells.append((f"{pre}TP{i + 1} · cierra {tp.get('cierra')}%", f'{_px(tp.get("precio"))} (+{tp.get("pct") or 0:.2f}%)', "#3DDC97"))
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
 <div class="hd"><div><div class="t">🏆 LA OPERACIÓN MÁS RENTABLE AHORA · ELIGIÓ {escape(who)} · {escape(b["fuente"])}</div>
  <div class="big">{escape(b["cripto"])} <span class="pill" style="background:{dc}22;color:{dc}">{escape(str(b["direccion"]))}</span></div></div>
  <div style="text-align:right"><div class="t">{("GROK (MCP) · PRECIOS DE GROK" if (grok or {}).get("plan_grok") else "GROK (MCP)") if grok else "G-JUDGE"}</div>
  <span class="pill" style="background:{vc}22;color:{vc};font-size:16px">{escape(vj)}{"" if grok else f" · Σ {b['suma']:+d}"}</span>
  <div class="t" style="margin-top:4px">confianza {(grok or b)["confianza"]}% · mesa: {escape(str(b["mesa"]))}</div></div></div>
 <div class="ks">{kp}</div>
 <div class="grid">{grid}</div>
 <div class="vs">{votes}</div>
 <div class="why"><b style="color:{vc}">Por qué:</b> {escape(str((grok or {}).get("lo_esperado") or b["razon"]))}</div>
</div>"""


def render_grok_pick(svc, req) -> None:
    br = getattr(svc, "grok", None)
    if br is None or getattr(br, "pick", None) is None:
        st.error("GROK PICK no está disponible en esta instalación.")
        return
    pick = br.pick
    guide("Dos formas de obtener la operación más rentable ahora. En 🤖 ELIGE GROK, FG VIEW corre la mesa de 12 agentes "
          "en varias criptos y le manda los números a Grok por MCP; Grok usa SUS agentes, elige una y dice qué espera. "
          "En 🖥️ ELIGE LA MESA, deciden los votantes en código, sin internet. En los dos casos el plan (entrada, stop, "
          "TPs) lo pone FG VIEW: Grok nunca pone precios.",
          ["Elige el modo, cuántas criptos revisar y pulsa el botón.",
           "En modo Grok: copia el mensaje, pégalo en grok.com y espera; la página se actualiza sola cuando Grok elija.",
           "Lee el plan y decide tú. Abrir en papel es opcional."],
          "«Más rentable» = mayor ganancia esperada según 100,000 simulaciones con datos reales. No es una garantía.")
    modo = st.radio("¿Quién elige?", ["🤖 Elige Grok (MCP)", "🖥️ Elige la mesa (local)"], horizontal=True, key="gp_modo",
                    help="Grok necesita el servidor MCP encendido, el túnel abierto y el conector puesto en grok.com.")
    es_grok = modo.startswith("🤖")
    if es_grok and not br.running():
        st.warning("El servidor MCP está apagado: Grok no puede recibir los datos ni mandar su elección. "
                   "Enciéndelo en GROK DESK (MCP) y abre el túnel.")
    c = st.columns([1, 1, 1.2, 1.3], vertical_alignment="bottom")
    top = c[0].slider("Criptos a revisar", 1, 8, 5, key="gp_top")
    both = c[1].checkbox("Probar LONG y SHORT", value=False, key="gp_both", help="Tarda el doble, pero revisa las dos direcciones.")
    sym = c[2].text_input("O una cripto en particular", value="", key="gp_sym", placeholder="SOL, ETH… (vacío = Grok elige)")
    label = "🤖 Preparar datos y pedirle a Grok que elija" if es_grok else "🏆 Buscar la operación más rentable"
    if c[3].button(label, type="primary", key="gp_go", width="stretch"):
        with st.spinner("G-SCOUT elige candidatas · la mesa de 12 agentes corre cada una…"):
            try:
                if es_grok:
                    st.session_state["gp_brief"] = pick.brief(top, both, sym.strip())
                    st.session_state.pop("gp_out", None)
                else:
                    st.session_state["gp_out"] = pick.find(top, both, sym.strip())
            except Exception as exc:  # noqa: BLE001
                st.error(f"No se pudo completar la búsqueda: {exc}")
    gp = pick.state().get("grok")
    if es_grok and gp is None:
        _esperando(pick, br)
        return
    if es_grok and gp is not None:
        _de_grok(pick, br, gp)
        return
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


def _plan_card(d: dict, who: str, grok: dict | None = None) -> None:
    st.markdown(_hero(d, who, grok), unsafe_allow_html=True)


def _esperando(pick, br) -> None:
    """Modo Grok: candidatas listas, esperando que Grok elija por MCP."""
    brief = pick.waiting()
    if not brief:
        st.info("Pulsa «Preparar datos y pedirle a Grok que elija». FG VIEW corre la mesa en varias criptos y deja los "
                "números listos para que Grok los tome por MCP.")
        return
    st.success(f"✅ {len(brief['candidatas'])} candidatas listas y esperando a Grok · caducan en 20 min · "
               f"fuente: {brief['fuente']}")
    if brief.get("pausado"):
        st.error("PAUSADO POR TUS REGLAS · " + str(brief["pausado"]))
    st.markdown("#### 1 · Copia esto y pégalo en tu chat de Grok")
    st.code(pick.pick_prompt(brief), language="text")
    st.markdown("#### 2 · Esperando la elección de Grok…")
    st.caption("Esta página se actualiza sola. Grok verá las candidatas con `fg_pick_datos` y mandará su elección con "
               "`fg_pick_elige`.")
    rows = [{"Ronda": c["id"], "Cripto": c["cripto"], "Dirección": c["direccion"], "P(TP1) %": c["prob_tp1"],
             "Ventaja R": c["ventaja_r"], "Ganancia esperada $": c["ev_usd"], "P(SL) %": c["p_sl"], "Mesa": c["mesa"]}
            for c in brief["candidatas"]]
    st.dataframe(rows, hide_index=True, width="stretch")
    sug = brief.get("sugerencia_mesa")
    if sug:
        st.caption(f"Para comparar, la mesa local habría elegido: {sug['veredicto']} {sug['direccion']} {sug['cripto']} "
                   f"(ronda #{sug['id']}, Σ {sug['suma']:+d}). Grok puede elegir otra.")

    def _wait() -> None:
        if pick.state().get("grok") is not None:
            st.rerun(scope="app")
        st.caption("⏳ Esperando a Grok…")

    st.fragment(_wait, run_every=4)()


def _de_grok(pick, br, gp: dict) -> None:
    """Modo Grok: ya eligió. Se muestra su elección con el plan de FG VIEW."""
    d = gp.get("datos") or {}
    st.markdown(f"### 🤖 Eligió Grok · {gp['at'][11:19]} UTC")
    if gp["veredicto"] != "APRUEBA":
        st.warning(f"Grok no la respalda del todo ({gp['veredicto']}): tómala como estudio, no como señal.")
    if gp.get("pausado"):
        st.error("PAUSADO POR TUS REGLAS · " + str(gp["pausado"]))
    if d.get("plan"):
        _plan_card(d, "GROK (MCP)", gp)
    else:
        st.info(f"{gp['veredicto']} {gp['direccion']} {gp['cripto']} · sin plan guardado para esa ronda.")
    gpl = gp.get("plan_grok")
    if gpl:
        e = gpl.get("esperado") or {}
        if e.get("error"):
            st.warning(f"Los precios de Grok se validaron, pero no se pudieron simular: {e['error']}")
        else:
            st.success(f"✅ Precios puestos por Grok y validados por FG VIEW · simulados de nuevo "
                       f"({e.get('sims', 0):,} caminos sobre SUS niveles) · R:R neto 1:{gpl['plan'].get('rr_neto')}")
        for n in gpl.get("notas", [])[:3]:
            st.caption(f"• {n}")
    else:
        st.caption("Grok no puso precios: el plan es el de la mesa.")
    st.markdown("#### 🔮 Lo que espera Grok")
    st.write(gp.get("lo_esperado") or "n/a")
    k = st.columns(3)
    k[0].metric("Confianza de Grok", f"{gp['confianza']}%")
    k[1].metric("Movimiento esperado", f"{gp['movimiento_esperado_pct']:+.2f}%" if gp.get("movimiento_esperado_pct") is not None else "n/a")
    k[2].metric("Horizonte", f"{gp['horizonte_min']:.0f} min" if gp.get("horizonte_min") is not None else "n/a")
    for razon in gp.get("razones", [])[:6]:
        st.caption(f"✓ {razon}")
    for riesgo in gp.get("riesgos", [])[:6]:
        st.caption(f"⚠ {riesgo}")
    st.caption("Lo que dice Grok es su opinión. El plan, las probabilidades y la ganancia esperada salen de FG VIEW y de "
               "las 100,000 simulaciones.")
    a, b, c = st.columns([1, 1, 2])
    if a.button("📝 Abrir en PAPEL", key="gp_paper_g", disabled=gp["veredicto"] != "APRUEBA",
                help="Solo si Grok aprobó y la mesa no dijo NO OPERAR. Nunca es real."):
        msg = pick.open_paper(gp["ronda_id"])
        (st.success if "PAPEL" in msg.upper() else st.error)(msg)
    if b.button("🔁 Pedir otra elección", key="gp_again_g"):
        stt = pick.state()
        stt["grok"], stt["brief"] = None, None
        pick.save(stt)
        st.rerun()
    c.caption(f"Ronda #{gp['ronda_id']} · la mesa decía: {d.get('veredicto', 'n/a')} (Σ {d.get('suma', 'n/a')})")
    if d.get("tarjeta"):
        with st.expander("📇 Trade Card completa (FG VIEW)"):
            st.code(d["tarjeta"], language="text")
    hist = pick.state().get("history", [])[:-1]
    if hist:
        with st.expander(f"🗂️ Elecciones anteriores de Grok ({len(hist)})"):
            for h in reversed(hist[-10:]):
                st.caption(f"{h['at'][11:19]} · {h['veredicto']} {h['direccion']} {h['cripto']} · {h['confianza']}% · "
                           f"{(h.get('lo_esperado') or '')[:110]}")
