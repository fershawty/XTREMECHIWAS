"""
ui/grok_page.py
===============
Página 🤖 GROK DESK: idéntica a FG DESK (red de agentes, terminal con orbitales, tape, Galton…),
pero manejada por los agentes de Grok desde grok.com a través de MCP.

Grok se conecta a FG VIEW como "conector personalizado"; cada herramienta que usa (correr la mesa,
ver el mercado, consultar un agente, dejar su veredicto) se anima aquí en vivo.
"""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from ui.grok_desk_view import build_desk_payload, desk_html
from ui.mode import guide

TUTORIAL = """
**Qué necesitas:** tu cuenta de **Grok** (la suscripción de ~$30 sirve para usar Grok en grok.com; revisa en
**grok.com → Connectors** que tu plan permita conectores personalizados) y **FG VIEW abierto en tu PC**.
No hace falta API key de xAI: Grok usa tu suscripción y FG VIEW pone los datos, la mesa de 12 agentes y el equipo de 11.

**1 · Enciende el servidor MCP** (aquí abajo, «🔌 Servidor MCP»). Queda escuchando en tu PC, en `127.0.0.1`.

**2 · Abre un túnel para que Grok llegue a tu PC.** Grok solo acepta direcciones públicas `https://`. Lo más fácil
y gratis es **Cloudflare Tunnel**:
- Descarga `cloudflared` (Windows: el `.exe` desde la página oficial de Cloudflare; Mac: `brew install cloudflared`).
- En una terminal ejecuta el comando que aparece abajo (`cloudflared tunnel --url http://127.0.0.1:PUERTO`).
- Te mostrará una dirección como `https://algo-raro.trycloudflare.com`. Cópiala y pégala en el campo «Dirección del túnel».

**3 · Agrega FG VIEW en Grok.** En **grok.com → Connectors → New Connector → Custom**, ponle de nombre `FG VIEW` y pega la
**dirección del conector** que te da esta página (termina en `/mcp/<token>`). Guarda.

**4 · Úsalo desde el chat de Grok.** Ejemplos:
- «ronda» (G-SCOUT elige hasta 3) o «ronda SOL».
- «¿cómo vamos?» y «revisión».
- «Usa FG VIEW: fg_ronda en DEMO y pégame el reporte.»
- «Si el juez aprueba, ábrela en papel.» El juez no mueve el stop ni el tamaño.

Mientras Grok trabaja, aquí verás a los agentes moverse y su veredicto en vivo.

**Seguridad**
- Por MCP **no existen órdenes reales**: solo análisis y modo **papel**. Grok no puede tocar tus keys de Binance.
- Grok puede **frenar** pero no **forzar**: si la mesa dice ⛔ NO OPERAR, ni en papel se abre.
- La dirección lleva un **token secreto**. No la compartas. Si se filtró, pulsa «🔁 Nuevo token» y actualízala en Grok.
- El túnel rápido de Cloudflare cambia de dirección cada vez que lo abres: vuelve a pegarla en Grok. Cierra el túnel
  cuando no lo uses.
"""


def render_grok(svc, req) -> None:
    br = getattr(svc, "grok", None)
    if br is None:
        st.error("El puente MCP no está disponible en esta instalación.")
        return
    st.markdown(_PAGE_CSS, unsafe_allow_html=True)
    guide("GROK DESK es el puesto del equipo de 11 agentes. Orbitan el núcleo, votan de −2 a +2 y G-JUDGE decide "
          "con reglas fijas. El interruptor automático no toca tus reglas ni la hora límite. Solo papel.",
          ["Enciende el servidor y, si quieres, el modo automático.",
           "Una ronda manual desde aquí corre aunque el automático esté apagado.",
           "Si un número no está, la terminal dice n/a."],
          "Por MCP y por la app de iPhone no hay órdenes reales. El token del conector se muestra enmascarado.")
    _controls(br)
    def _paint() -> None:
        components.html(desk_html(build_desk_payload(svc, br)), height=1080, scrolling=False)
    st.fragment(_paint, run_every=5 if br.running() else None)()
    _connection(br)
    _mobile(svc, br)
    _copilot(svc, br)
    _feed(br)


_PAGE_CSS = """
<style>
section.main [data-testid="stVerticalBlock"]{gap:0.4rem}
div[data-testid="stMarkdownContainer"] h3{letter-spacing:.08em}
</style>
"""


def _controls(br) -> None:
    from engine.fgview.grok_mcp import mask_secret

    stt = br.state()
    on = br.running()
    try:
        cutoff = br.s.copilot.rules.values().get("cutoff") or "n/a"
        every = int(getattr(br.s.autopilot, "every_s", 60) or 60)
    except Exception:  # noqa: BLE001
        cutoff, every = "n/a", "n/a"
    c = st.columns([1.1, 1.3, 1.1, 1.5])
    c[0].caption(f"Servidor MCP · {'encendido' if on else 'apagado'}")
    if not on:
        if c[0].button("▶ Encender servidor", type="primary", key="gk_start", width="stretch"):
            try:
                st.toast(br.start(int(stt["port"])))
            except Exception as exc:  # noqa: BLE001
                st.error(f"No se pudo encender: {exc}")
            st.rerun()
    elif c[0].button("⏹ Apagar servidor", key="gk_stop", width="stretch"):
        br.stop()
        st.rerun()
    auto = c[1].toggle("Modo automático Grok", value=br.auto_on(), key="gk_auto",
                       help="Si está apagado, fg_ronda por el conector no corre. No cambia las reglas.")
    if auto != br.auto_on():
        br.set(auto=bool(auto))
        st.rerun()
    c[2].caption(f"Intervalo {every}s · hora límite {cutoff}")
    c[2].caption("Solo información. El interruptor no los cambia.")
    sym = c[3].text_input("Ronda manual", value="DEMO", key="gk_round_sym", label_visibility="collapsed",
                          placeholder="DEMO")
    b1, b2 = c[3].columns(2)
    paper = b1.checkbox("Papel si aprueba", value=False, key="gk_round_paper")
    if b2.button("Correr", key="gk_round_go", width="stretch"):
        with st.spinner("La mesa calcula y el equipo vota…"):
            try:
                br.ronda(sym, "Auto", abrir_en_papel=paper, manual=True)
            except Exception as exc:  # noqa: BLE001
                st.error(f"La ronda no se pudo cerrar: {exc}")
            else:
                st.rerun()
    st.caption("Conector " + mask_secret(br.public_url() or br.local_url())
               + " · el token no se muestra completo. Túnel: "
               + (stt.get("tunnel") or "sin túnel"))


def _team(svc, br) -> None:
    """El equipo y la última ronda. Negro y dorado: el tema de la terminal ya es ese."""
    from engine.fgview.grok_team import TEAM, fmt_sum, fmt_vote

    st.markdown("### 🧠 Equipo Grok · 11 agentes")
    st.caption("Cada agente responde una pregunta. Seis votan. G-JUDGE suma y decide. G-EXEC solo anota, o abre en papel. "
               "Un dato que no llegó se queda en n/a.")
    last = None
    try:
        last = br.team.latest()
    except Exception:  # noqa: BLE001
        last = None
    votes = (last or {}).get("votos") or {}
    judge = (last or {}).get("juez") or {}
    lines = ["| Agente | Pregunta | Última ronda |", "|---|---|---|"]
    for meta in TEAM:
        key = meta["key"]
        vote = votes.get(key) or {}
        if vote:
            cell = f"`{fmt_vote(vote.get('voto', 0))}` · {(vote.get('linea') or 'n/a').replace('|', '/')}"
        elif key == "G-JUDGE" and judge:
            cell = f"**{judge.get('veredicto') or 'n/a'}** · suma {fmt_sum(int(judge.get('suma') or 0))}"
        elif key == "G-EXEC" and last:
            cell = last.get("ejecucion") or "n/a"
        elif key == "G-RISK" and last:
            cell = "plan de la tarjeta" if last.get("plan") else "plan: n/a"
        elif key == "G-SCOUT" and last:
            cell = last.get("cripto") or "n/a"
        elif key == "G-WATCH":
            cell = "fg_posiciones · fg_resultados · fg_resumen"
        else:
            cell = "todavía sin ronda"
        lines.append(f"| **{key}** | {meta['pregunta']} | {cell} |")
    st.markdown("\n".join(lines))
    c1, c2, c3, c4 = st.columns([1.3, 1, 1.6, 1], vertical_alignment="bottom")
    sym = c1.text_input("Cripto de la ronda", value="DEMO", key="gk_round_sym",
                        help="DEMO usa datos sintéticos. Un nombre real lee Binance.")
    dire = c2.selectbox("Dirección", ["Auto", "LONG", "SHORT"], key="gk_round_dir")
    paper = c3.checkbox("Abrir en papel si G-JUDGE aprueba", value=False, key="gk_round_paper")
    if c4.button("Correr ronda", type="primary", key="gk_round_go", width="stretch"):
        with st.spinner("La mesa calcula y el equipo vota…"):
            try:
                br.ronda(sym, dire, abrir_en_papel=paper)
            except Exception as exc:  # noqa: BLE001
                st.error(f"La ronda no se pudo cerrar: {exc}")
            else:
                st.rerun()
    if last and last.get("reporte"):
        st.code(last["reporte"], language="text")
        if (judge.get("veredicto") or "") != "APRUEBA":
            st.caption("Nada aprobado esta ronda. Esperar también es operar bien.")
    else:
        st.caption("Todavía no hay un reporte. Corre una ronda o pídesela a Grok con fg_ronda.")
    with st.expander("Revisión · ¿cómo vamos?"):
        if st.button("Medir al equipo", key="gk_rev_go"):
            try:
                st.session_state["gk_rev"] = br.revision()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))
        rev = st.session_state.get("gk_rev")
        if rev:
            st.write(rev.get("sugerencia") or "n/a")
            st.caption(f"Acertó más: {rev.get('acerto_mas') or 'n/a'} · se equivocó más: "
                       f"{rev.get('equivoco_mas') or 'n/a'} · rondas con resultado: {rev.get('muestras', 0)}")
            st.code(rev.get("resumen_semana") or "n/a", language="text")
            st.caption(rev.get("nota") or "")
    desk = getattr(svc, "desk", None)
    registry = getattr(desk, "agents", None) if desk is not None else None
    if registry is not None:
        with st.expander("Fichas de la mesa · para diseñar un agente nuevo"):
            names = [s.name for s in registry.specs()]
            picked = st.selectbox("Agente", names, key="gk_ficha")
            st.code(registry.ficha(picked), language="text")
            st.caption("Para sumar uno: una ficha (AGENTE, PREGUNTA, DATOS, CÁLCULO, VOTO, BLOQUEA SI, NO HACE, "
                       "LUGAR EN LA CADENA, CÓMO PROBARLO, PERSONAJE) y desk.agents.add(...). "
                       "Puede aprobar, informar o frenar. No sube el riesgo ni quita el stop.")


def _connection(br) -> None:
    stt = br.state()
    on = br.running()
    st.markdown(f"### 🔌 Servidor MCP · {'🟢 ENCENDIDO' if on else '⚪ apagado'}")
    with st.expander("📘 Tutorial: cómo conectar FG VIEW con los agentes de Grok (MCP)", expanded=not stt.get("tunnel")):
        st.markdown(TUTORIAL)
    c = st.columns([1, 1, 2.2], vertical_alignment="bottom")
    port = c[0].number_input("Puerto", 1024, 65535, int(stt["port"]), key="gk_port", disabled=on)
    if not on:
        if c[1].button("▶ Encender servidor", type="primary", key="gk_start", width="stretch"):
            try:
                st.toast(br.start(int(port)))
            except Exception as exc:  # noqa: BLE001
                st.error(f"No se pudo encender: {exc}")
            st.rerun()
    elif c[1].button("⏹ Apagar servidor", key="gk_stop", width="stretch"):
        br.stop()
        st.rerun()
    tunnel = c[2].text_input("Dirección del túnel (https://…trycloudflare.com)", value=stt.get("tunnel", ""), key="gk_tunnel",
                             placeholder="https://algo-raro.trycloudflare.com")
    if tunnel != stt.get("tunnel", ""):
        stt = br.set(tunnel=tunnel.strip())
    st.caption("1) Comando del túnel (en otra terminal):")
    st.code(f"cloudflared tunnel --url http://127.0.0.1:{int(stt['port'])}", language="bash")
    from engine.fgview.grok_mcp import mask_secret

    url = br.public_url()
    if url:
        st.caption("2) Dirección del conector (token enmascarado) para grok.com → Connectors → Custom:")
        st.code(mask_secret(url), language="text")
    else:
        st.caption("2) Pega arriba la dirección https:// del túnel. El token del conector queda enmascarado.")
    x, y = st.columns([1, 3])
    if x.button("🔁 Nuevo token", key="gk_tok", help="Invalida la dirección anterior (hay que actualizarla en Grok)."):
        br.new_token()
        if br.running():
            br.stop()
            br.start(int(stt["port"]))
        st.rerun()
    from engine.fgview.grok_mcp import mask_secret

    y.caption("Dirección local (token enmascarado): `" + mask_secret(br.local_url())
              + "` · Sin la app: `python -m engine.fgview.grok_mcp`")


TELEGRAM_TUTORIAL = """
1. En Telegram busca **@BotFather** → `/newbot` → ponle un nombre y un usuario que termine en `bot`.
2. Copia el **token** que te da y agrégalo al `.env` junto a FGView: `TELEGRAM_BOT_TOKEN=123456:ABC…`
3. Reinicia FG VIEW, vuelve aquí y pulsa **▶ Encender Telegram**.
4. Escríbele **/start** a tu bot: te responde con tu **chat id**. Ponlo en el `.env`: `TELEGRAM_CHAT_ID=…` y reinicia.
5. Listo: el bot solo responde a TU chat. Escribe **/help** para ver los comandos.

El token es secreto: no lo compartas ni lo subas a internet. Si se filtra, en @BotFather usa `/revoke`.
"""
IPHONE_TUTORIAL = """
1. Enciende el **servidor MCP** y abre el **túnel** (sección de arriba), y pega la dirección del túnel.
2. En el iPhone abre en **Safari** la dirección de la app que aparece aquí abajo.
3. Toca **Compartir** → **Agregar a inicio**. Queda como app, a pantalla completa.
4. Pestañas: **Radar** (tarjetas), **Tarjeta** (armar una), **Posiciones** (stop y TP1), **Coach**, **Diario**, **Stats**.

La app solo muestra y arma tarjetas: no abre órdenes. Lo único que cambia algo es «Ya terminé por hoy», que apaga
el piloto. Los avisos al teléfono llegan por **Telegram**. La dirección lleva tu token secreto: no la compartas.
Con el túnel rápido de Cloudflare la dirección cambia cada vez que lo abres: vuelve a abrirla en Safari.
"""


def _mobile(svc, br) -> None:
    tg = getattr(svc, "telegram", None)
    a, b = st.columns(2)
    with a:
        st.markdown("### 📲 Telegram")
        if tg is None:
            st.caption("No disponible.")
        else:
            on = tg.running()
            st.caption(("🟢 Encendido" if on else "⚪ Apagado") + " · "
                       + ("token ✅" if tg.configured() else "falta TELEGRAM_BOT_TOKEN") + " · "
                       + ("chat ✅" if tg.chat_id else "falta TELEGRAM_CHAT_ID (escribe /start al bot)"))
            x, y = st.columns(2)
            if not on and x.button("▶ Encender Telegram", key="tg_on", disabled=not tg.configured(), width="stretch"):
                st.toast(tg.start())
                st.rerun()
            if on and x.button("⏹ Apagar Telegram", key="tg_off", width="stretch"):
                tg.stop()
                st.rerun()
            if y.button("📌 Enviar tablero", key="tg_dash", disabled=not tg.ready(), width="stretch"):
                tg.update_dashboard()
                st.toast("Tablero enviado y fijado.")
            with st.expander("📘 Tutorial de Telegram"):
                st.markdown(TELEGRAM_TUTORIAL)
            ign = tg.state().get("ignored", 0)
            if ign:
                st.caption(f"⚠️ {ign} mensajes de otros chats ignorados.")
    with b:
        st.markdown("### 📱 App de iPhone")
        from engine.fgview.grok_mcp import mask_secret

        url = br.app_url()
        if url:
            st.caption("Ábrela en Safari y agrégala a inicio (token enmascarado):")
            st.code(mask_secret(url), language="text")
        else:
            st.caption("Pega la dirección del túnel arriba para obtener el link de la app.")
        st.caption("En esta PC (token enmascarado): `" + mask_secret(br.app_url(local=True) or "") + "`")
        with st.expander("📘 Tutorial de la app de iPhone"):
            st.markdown(IPHONE_TUTORIAL)


def _copilot(svc, br) -> None:
    from engine.fgview.copilot import RULES
    from engine.fgview.grok_mcp import copilot_prompt

    cp = getattr(svc, "copilot", None)
    if cp is None:
        return
    st.markdown("### 🧭 Copiloto · Trade Card · COACH · Reglas")
    with st.expander("📜 Prompt del copiloto para Grok (cópialo y pégalo en grok.com)"):
        st.caption("Adaptación a futuros de Binance del prompt de 21 páginas. En Grok di: «Sigue este prompt». "
                   "También está dentro del conector como prompt «copiloto_fg».")
        st.code(copilot_prompt() or "No se encontró docs/GROK_COPILOT_PROMPT.md", language="markdown")
    cst = cp.coach.status()
    a, b = st.columns([1.25, 1])
    with a:
        st.markdown("#### 📇 Trade Card")
        c1, c2 = st.columns([1.4, 1], vertical_alignment="bottom")
        sym = c1.text_input("Cripto", value="BTC", key="cp_sym")
        if c2.button("Armar tarjeta", key="cp_card", width="stretch"):
            with st.spinner("La mesa analiza y el copiloto arma la tarjeta…"):
                try:
                    out = br.card(sym)
                    st.session_state["cp_last"] = out["tarjeta"]
                except Exception as exc:  # noqa: BLE001
                    st.error(f"No se pudo armar la tarjeta: {exc}")
        txt = st.session_state.get("cp_last") or br.state().get("last_card")
        st.code(txt or "Todavía no hay tarjetas. Arma una aquí o pídesela a Grok («usa fg_tarjeta en SOL»).",
                language="text")
    with b:
        st.markdown("#### 🧘 COACH · hoy")
        k = st.columns(3)
        k[0].metric("Operaciones", f"{cst['trades_today']}/{cst['max_trades']}")
        k[1].metric("P&L hoy", f"{cst['pnl_today']:+.2f}", f"{cst['pnl_pct']:+.2f}%")
        k[2].metric("Abiertas", f"{cst['open']}/{cst['max_open']}")
        st.caption(f"Racha de pérdidas: {cst['streak']} · capital de referencia ${cst['bankroll']:,.2f}"
                   + (f" · pausa {cst['cooldown_left_min']} min" if cst["cooldown_left_min"] else ""))
        if cst["blocked"]:
            st.warning("PAUSADO POR TUS REGLAS · " + cst["blocked"] + " El piloto automático tampoco entra.")
        else:
            st.success("Sin bloqueos: tus reglas permiten operar.")
    with st.expander("📜 Mis reglas (rules.md) · cambiar"):
        vals = cp.rules.values()
        st.code(cp.rules.markdown(cp.coach.bankroll()), language="text")
        cols = st.columns(3)
        edits = {}
        for i, (name, (default, lo, hi, help_)) in enumerate(RULES.items()):
            col = cols[i % 3]
            if lo is None:
                edits[name] = col.text_input(name, value=str(vals[name]), help=help_, key=f"cp_r_{name}")
            elif isinstance(default, int):
                edits[name] = col.number_input(name, int(lo), int(hi), int(vals[name]), help=help_, key=f"cp_r_{name}")
            else:
                edits[name] = col.number_input(name, float(lo), float(hi), float(vals[name]), step=0.25, help=help_,
                                               key=f"cp_r_{name}")
        goal = st.text_input("Mi meta de 30 días (mejor un hábito que una cifra)", value=cp.rules.state()["goal"], key="cp_goal")
        force = st.checkbox("Forzar aunque haya perdido hace poco (queda anotado)", key="cp_force")
        if st.button("Guardar reglas", key="cp_save"):
            msgs = []
            for name, v in edits.items():
                if str(v) != str(vals[name]):
                    try:
                        msgs.append(cp.rules.change(name, v, who="yo", last_loss_min=cp.coach.last_loss_min(), force=force))
                    except ValueError as exc:
                        msgs.append(f"❌ {exc}")
            if goal != cp.rules.state()["goal"]:
                cp.rules.set_goal(goal)
                msgs.append("Meta actualizada.")
            st.info("  \n".join(msgs) if msgs else "Sin cambios.")
        pend = cp.rules.state()["pending"]
        if pend:
            st.caption("En espera de 12 h: " + " · ".join(f"{p['name']} → {p['value']}" for p in pend))
    with st.expander("🗒️ SCRIBE · resumen y calificaciones"):
        try:
            n = cp.scribe.grade(svc.history, int(cp.rules.values()["grade_after_min"]))
            if n:
                st.caption(f"{n} tarjetas recién calificadas.")
        except Exception:  # noqa: BLE001
            pass
        x, y = st.columns(2)
        x.code(cp.scribe.summary(cp.coach, 1), language="text")
        y.code(cp.scribe.summary(cp.coach, 7), language="text")
        graded = [c for c in cp.scribe.cards() if c.get("grade")][-8:]
        for c in reversed(graded):
            st.caption(f"{c['at'][:16]} · {c['symbol']} {c['direction']} · {c['band']} {c.get('score')} → nota {c['grade']} "
                       f"({c.get('result_pct', 0):+.2f}%)")


def _feed(br) -> None:
    def loop() -> None:
        stt = br.state()
        ev = stt["events"]
        n = len(ev)
        if st.session_state.get("gk_seen") is None:
            st.session_state["gk_seen"] = n
        elif n != st.session_state["gk_seen"]:
            st.session_state["gk_seen"] = n
            st.rerun(scope="app")  # Grok hizo algo nuevo: refresca la red y la terminal con su corrida
        c1, c2 = st.columns([1.3, 1])
        with c1:
            st.markdown("#### 📡 Actividad de Grok (MCP)")
            if not ev:
                st.caption("Sin llamadas todavía. Pídele a Grok en grok.com: «Usa FG VIEW y corre la mesa en SOL».")
            for e in reversed(ev[-10:]):
                ag = " ".join(e.get("agents", [])[:4])
                st.markdown(f"`{e['at']}` **{e['tool']}** · {e['summary'][:150]}" + (f"  \n<small>🛰️ {ag}</small>" if ag else ""),
                            unsafe_allow_html=True)
        with c2:
            st.markdown("#### 🧠 Veredicto de Grok")
            v = stt["verdicts"][-1] if stt["verdicts"] else None
            if not v:
                st.caption("Grok todavía no da su veredicto (herramienta grok_veredicto).")
            else:
                icon = {"APRUEBA": "✅", "PRECAUCION": "⚠️", "VETA": "⛔"}.get(v["veredicto"], "•")
                st.markdown(f"**{icon} {v['veredicto']}** · confianza {v['confianza']}% · {v.get('direccion') or ''} "
                            f"{v.get('simbolo') or ''} · mesa: {v.get('mesa') or '—'}")
                st.write(v["resumen"])
                for r in v.get("razones", [])[:4]:
                    st.caption(f"✓ {r}")
                for r in v.get("riesgos", [])[:4]:
                    st.caption(f"⚠ {r}")
                st.caption(f"`{v['at']}` · {v.get('nota', '')}")

    st.fragment(loop, run_every=3 if br.running() else None)()
