"""
ui/desk_page.py
===============
Página 🧠 FG DESK: la mesa de 12 agentes para operaciones cortas en futuros, con sus
animaciones (sinapsis, abanico de futuros, traza, rotación de señales y calibración) y el
🤖 piloto automático (papel primero; el modo real solo se desbloquea con resultados).
"""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from engine.config import SETTINGS
from engine.fgview.executor import live_enabled
from engine.position_simulator import fmt_usd
from ui import desk_anim as anim
from ui.desk_hub import hub_html
from ui.desk_terminal import build_payload, terminal_html
from ui.components import _logo_svg
from ui.mode import beginner, guide


def _capital() -> float:
    return float(st.session_state.get("equity", SETTINGS.account_equity))


def render_desk(svc, req) -> None:
    guide("Doce agentes analizan cada operación en cadena, como una mesa de traders profesionales: además del mercado, "
          "siguen a las ballenas de Binance, el flujo de compras/ventas, el open interest y el funding. Si UNO dice que no, "
          "no se opera. La animación te muestra en vivo qué decidió cada uno.",
          ["Elige la cripto (o «Escanear todas») y pulsa «▶ Correr la mesa».",
           "Mira la red: verde = aprueba, rojo = bloquea, gris = sin datos; el agente que decidió se ilumina.",
           "Si la mesa aprueba, envía la orden al Order Executor o deja que el 🤖 piloto la abra en papel."],
          "La calibración compara lo que la mesa predijo con lo que pasó de verdad: así sabes cuánto confiar en sus números.")
    market = "demo" if req.market == "demo" else "crypto"
    coins = [c for t in svc.tiers.tiers if t.key in ("A", "B") for c in t.coins]
    c1, c2, c3 = st.columns([1.5, 1, 1], vertical_alignment="bottom")
    if market == "demo":
        target = c1.selectbox("Cripto", ["DEMO"], key="dk_coin")
    else:
        base = req.symbol.split("/")[0].upper()
        opts = ["🔎 Escanear todas"] + coins
        target = c1.selectbox("Cripto", opts, index=opts.index(base) if base in opts else 0, key="dk_coin",
                              format_func=lambda c: c if c.startswith("🔎") else f"{c} · {svc.tiers.label(c)}")
    direction = c2.selectbox("Dirección", ["Auto", "LONG", "SHORT"], key="dk_dir")
    svc.desk.operable = st.toggle("🎲 Modo operable: dame siempre la mejor operación aunque la mesa no la recomiende",
                                  value=True, key="dk_operable",
                                  help="Si un agente la objeta, igual te da la operación más operable, marcada NO RECOMENDADA, "
                                       f"con riesgo reducido (máx. {svc.desk.OPERABLE_RISK}%) y SL obligatorio. El piloto "
                                       "automático solo abre las APROBADAS.")
    go = c3.button("▶ Correr la mesa", type="primary", key="dk_go", width="stretch")
    capital = _capital()
    lstat = svc.ladder_desk.status() if getattr(svc, "ladder_desk", None) is not None else None
    lrisk = None
    if lstat is not None and lstat.enabled and not lstat.paused:
        capital, lrisk = lstat.balance, lstat.risk_pct
        st.caption(f"🪜 Escalera activa: la mesa opera con el saldo de la escalera ({fmt_usd(capital)}) y "
                   f"{'la tabla normal' if lrisk is None else f'{lrisk:g}% de riesgo'} por operación.")
    if go:
        try:
            with st.spinner("Los agentes están analizando: Spotter → Ballenas → Flujo → Funding → Compuerta → Prior → "
                            "Pricer (100,000 caminos + calibración) → Edge → Kelly → Guard → Taker…"):
                if target.startswith("🔎"):
                    dec, ranked = svc.desk.scan(coins, market, capital, req.exchange, ladder_risk=lrisk)
                    st.session_state["dk_ranked"] = ranked
                else:
                    sym = "DEMO/USDT" if market == "demo" else f"{target}/USDT"
                    dec = svc.desk.run(sym, market, capital, req.exchange, direction, ladder_risk=lrisk)
            st.session_state["dk_last"] = dec
            st.rerun()  # Para que la mini-mesa de la barra lateral muestre esta decisión al instante
        except Exception as exc:  # noqa: BLE001
            st.error(f"La mesa no pudo analizar: {exc}")
    dec = st.session_state.get("dk_last")
    if dec is not None and beginner():
        st.info(dec.summary())
    st.markdown("### 🕸️ La red · 12 agentes → un núcleo FG")
    components.html(hub_html(dec, _logo_svg()), height=604)
    # Terminal completa: tickers, MESA PNL, THE TAPE, ORBITALES, pipeline, traza, Pricer (Galton) y cordilleras
    components.html(terminal_html(build_payload(svc, dec, st.session_state.get("dk_ranked"), capital), _logo_svg()),
                    height=1470)
    a, b = st.columns([1.55, 1])
    with a:
        components.html(anim.fan_html(dec.fan if dec else None, dec.tm if dec else None), height=338)
    with b:
        components.html(anim.radar_html(dec.radar if dec else None), height=338)
    a, b = st.columns(2)
    with a:
        components.html(anim.rotation_html(dec.rotation if dec else []), height=308)
    with b:
        components.html(anim.calibration_html(dec.calibration if dec else None), height=308)
    ranked = st.session_state.get("dk_ranked")
    if ranked and not beginner():
        with st.expander("🔎 Ranking del Spotter (escaneo)"):
            for sym, sc, head in ranked[:15]:
                st.caption(f"{sym} · {'⛔' if sc < 0 else f'{sc:.0f}/100'} · {head}")
    if dec is not None and dec.ticket is not None:
        st.markdown(f"### 🎯 Lo más recomendado · {dec.verdict_icon} {dec.verdict} · P(TP1) {dec.probability or 0:.0f}%")
        if dec.operable:
            b = dec.blocked_by
            (st.warning if dec.verdict == "ESPERAR" else st.error)(f"{dec.verdict_icon} {dec.verdict}: la mesa no la respalda del todo: {b.agent} la objetó ({b.headline[:140]}). "
                       f"Va con riesgo reducido ({dec.risk_pct:.2f}% del capital) y SL obligatorio. "
                       "Trátala como una apuesta: solo con dinero que aceptes perder.")
        st.markdown("  \n".join(dec.ticket.lines()))
        x, y = st.columns(2)
        if x.button("🧾 Llevar al Order Executor", key="dk_to_ex"):
            st.session_state["ex_ticket"] = (dec.ticket, None)
            st.success("Lista en «Riesgo y capital → Order Executor» para que la confirmes.")
        if y.button("📝 Abrir en PAPEL ahora", key="dk_paper"):
            out = svc.executor.submit(dec.ticket, confirmed=True, mode="paper")
            st.success("Abierta en papel.") if out.get("status") == "filled" else st.error(
                (out.get("problems") or [out.get("message")])[0])
    _ladder(svc)
    _ai_analyst(svc)
    _autopilot(svc, req, market, capital)


def _ladder(svc) -> None:
    lad = getattr(svc, "ladder_desk", None)
    if lad is None:
        return
    st.markdown("### 🪜 Modo escalera · de $10–20 hacia arriba")
    guide("Empiezas con poco y la mesa hace crecer el saldo operación por operación. Arriesga más en proporción cuando el "
          "capital es pequeño (4% hasta $50, 3% hasta $100, 2% hasta $250) y se vuelve más conservadora conforme sube.",
          ["Pon cuánto quieres arriesgar en total (10–20 USDT) y enciende la escalera.",
           "Enciende el 🤖 piloto abajo: él opera con el saldo de la escalera.",
           "Cada vez que el saldo se duplica, el 20% de la ganancia va a la caja fuerte y ya no se arriesga."],
          "Tras 2 pérdidas seguidas el riesgo baja a la mitad; si el saldo cae al 50% de lo que pusiste, la escalera se pausa sola.")
    stt = lad.status()
    c = st.columns([1, 1, 1.2], vertical_alignment="bottom")
    start = c[0].number_input("Capital inicial (USDT)", 5.0, 500.0, float(stt.start), step=1.0, key="lad_start")
    if not stt.enabled:
        if c[1].button("🪜 Empezar escalera", type="primary", key="lad_go", width="stretch"):
            lad.start(start)
            st.rerun()
    elif c[1].button("⏹ Detener escalera", key="lad_stop", width="stretch"):
        lad.stop()
        st.rerun()
    c[2].caption("🔓 Prueba real permitida con ≤ 25 USDT (con tus API keys sin retiro y LIVE_TRADING_ENABLED=true)."
                 if start <= 25 else "Más de 25 USDT: el modo real pide antes 50 operaciones en papel con buenos resultados.")
    if not stt.enabled:
        return
    if stt.paused:
        st.error("🪜 " + stt.paused)
    k = st.columns(5)
    k[0].metric("Saldo escalera", fmt_usd(stt.balance), f"{(stt.balance / stt.start - 1) * 100:+.1f}%")
    k[1].metric("Caja fuerte", fmt_usd(stt.vault))
    k[2].metric("Siguiente meta", fmt_usd(stt.next_target))
    k[3].metric("Riesgo/operación", "tabla" if stt.risk_pct is None else f"{stt.risk_pct:g}%",
                "freno: 2 pérdidas" if stt.streak >= 2 else None, delta_color="inverse")
    k[4].metric("Operaciones", stt.trades)
    st.progress(stt.progress, text=f"Hacia {fmt_usd(stt.next_target)} · {stt.progress * 100:.0f}%")
    if len(stt.history) > 1:
        st.line_chart(stt.history, height=140)


GROK_TUTORIAL = """
**Antes de pagar:** la suscripción de Grok en X o grok.com (≈ **$30/mes**) sirve para platicar con Grok en la app,
pero **no incluye la API**. Para que FG VIEW hable con Grok se necesita la **API de xAI**, que se paga **aparte y por uso**
(≈ 1 centavo de dólar por análisis con Grok 4.7). No hace falta la suscripción para usar la API.

1. Entra a **console.x.ai** e inicia sesión (puedes usar la misma cuenta de X).
2. Ve a **Billing / Facturación** y carga saldo prepagado. Con **$5–10 USD** alcanza para cientos de análisis.
3. Ve a **API Keys** → **Create API Key**. Ponle un nombre (por ejemplo `FG VIEW`) y copia la key (empieza con `xai-`).
   Guárdala bien: solo se muestra una vez.
4. Abre el archivo **`.env`** que está junto a FGView (el mismo donde van las keys de Binance) y agrega una línea:
   `XAI_API_KEY=xai-tu-key-aquí`
5. **Cierra y vuelve a abrir FG VIEW** para que lea la key.
6. Aquí abajo: elige **🤖 Grok (xAI)**, pulsa **🔌 Probar conexión** y, si sale ✅, enciende **Analista IA**.
7. Pon un **tope diario** (por ejemplo $1). Cuando se alcanza, la IA deja de opinar ese día y la mesa sigue sola.

**Seguridad:** la key de xAI solo sirve para hablar con Grok; no toca tu dinero de Binance. No la compartas ni la subas a
internet. Si crees que se filtró, bórrala en console.x.ai y crea otra.
"""


def _ai_analyst(svc) -> None:
    from engine.fgview.analyst import MODELS, PROVIDERS

    ai = getattr(svc.desk, "analyst", None)
    if ai is None:
        return
    st = ai.state()
    st_title = "🧠 Analista IA · agente 13" + (" · ENCENDIDO" if ai.active() else " · apagado")
    import streamlit as _st

    _st.markdown(f"### {st_title}")
    guide("Una IA real (Grok o Claude) revisa la operación que la mesa ya armó, junto con noticias y contexto, y da su "
          "segunda opinión. Solo puede FRENAR: ⚠️ PRECAUCIÓN baja a ESPERAR con riesgo mínimo y ⛔ VETA la convierte en "
          "NO OPERAR. Nunca sube el riesgo ni cambia tu SL.")
    with _st.expander("📘 Tutorial: cómo conectar Grok (xAI) paso a paso", expanded=not ai.has_key("grok")):
        _st.markdown(GROK_TUTORIAL)
    c = _st.columns([1.1, 1.3, 1, 1], vertical_alignment="bottom")
    prov = c[0].selectbox("Proveedor", list(PROVIDERS), index=list(PROVIDERS).index(st["provider"]),
                          format_func=PROVIDERS.get, key="ai_prov")
    models = list(MODELS[prov])
    model = c[1].selectbox("Modelo", models, index=models.index(st["model"]) if st["model"] in models else 0,
                           format_func=lambda m: MODELS[prov][m][0], key="ai_model")
    cap = c[2].number_input("Tope diario (USD)", 0.1, 50.0, float(st["daily_cap"]), step=0.5, key="ai_cap")
    on = c[3].toggle("Analista IA", value=st["enabled"], key="ai_on", disabled=not ai.has_key(prov))
    if (prov, model, cap, on) != (st["provider"], st["model"], st["daily_cap"], st["enabled"]):
        st = ai.set(provider=prov, model=model if model in MODELS[prov] else "", daily_cap=float(cap),
                    enabled=bool(on and ai.has_key(prov)))
    key_name = "XAI_API_KEY" if prov == "grok" else "ANTHROPIC_API_KEY"
    _, pin, pout = MODELS[prov][st["model"]]
    _st.caption(("🔑 Key detectada. " if ai.has_key(prov) else f"🔒 Falta {key_name} en el .env (sigue el tutorial). ")
                + f"Gastado hoy: ${ai.spent_today():.3f} de ${st['daily_cap']:.2f} · precio aprox. ${pin:g}/${pout:g} "
                "por millón de tokens · solo se usa cuando la mesa tiene una operación candidata.")
    if _st.button("🔌 Probar conexión", key="ai_test", disabled=not ai.has_key(prov)):
        msg = ai.test_connection()
        (_st.success if msg.startswith("✅") else _st.error)(msg)
    last = st.get("last")
    if last:
        icon = {"APRUEBA": "✅", "PRECAUCION": "⚠️", "VETA": "⛔"}.get(last["ia"], "•")
        _st.markdown(f"**Última opinión** · {last.get('model', '')} · {last['symbol']} {last['dir']} · {icon} {last['ia']} "
                     f"· confianza {last.get('confianza')}%  \n{last.get('resumen', '')}")
        for r in last.get("razones", [])[:4]:
            _st.caption(f"✓ {r}")
        for r in last.get("riesgos", [])[:4]:
            _st.caption(f"⚠ {r}")
    try:
        ai.evaluate(svc.history)
    except Exception:  # noqa: BLE001
        pass
    sc = ai.scorecard()
    if sc["evaluadas"]:
        k = _st.columns(4)
        k[0].metric("Opiniones revisadas", sc["evaluadas"])
        k[1].metric("Frenadas que iban a SL ✅", f"{sc['frenadas_SL']}/{sc['frenadas']}")
        k[2].metric("Aprobadas que llegaron a TP1", f"{sc['aprobadas_TP']}/{sc['aprobadas']}")
        k[3].metric("Gasto total IA", f"${sc['gasto_total']:.2f}")
        _st.caption("Marcador: una hora después de cada opinión, la app revisa con las velas reales si la operación habría "
                    "tocado TP1 o SL primero. Si las frenadas casi nunca iban a SL, la IA no está ayudando: apágala.")


def _autopilot(svc, req, market: str, capital: float) -> None:
    st.markdown("### 🤖 Piloto automático de la mesa")
    pilot = svc.autopilot
    stt = pilot.state()
    guide("El piloto corre la mesa cada minuto y, si los 12 agentes aprueban, abre la operación SOLO. Empieza en PAPEL. "
          "El modo real se desbloquea cuando el papel demuestra resultados (50 operaciones, profit factor ≥ 1.2, "
          "drawdown ≤ 10%) y además tú lo activas.")
    unlocked, why = pilot.live_unlocked()
    c = st.columns([1, 1.2, 1, 1])
    on = c[0].toggle("Piloto encendido", value=stt["enabled"], key="ap_on")
    mode = c[1].radio("Modo", ["paper", "live"], horizontal=True, key="ap_mode",
                      index=1 if stt["mode"] == "live" else 0,
                      format_func={"paper": "📝 Papel", "live": "💵 Real"}.get, disabled=not (unlocked and live_enabled()))
    maxt = c[2].number_input("Máx. operaciones/día", 1, 30, int(stt["max_trades_day"]), key="ap_max")
    lvl = st.radio("El piloto entra en", ["OPERAR", "ESPERAR"], horizontal=True, key="ap_lvl",
                   index=1 if stt.get("min_level") == "ESPERAR" else 0,
                   format_func={"OPERAR": "✅ Solo OPERAR (recomendado)", "ESPERAR": "⏸️ OPERAR y ESPERAR (riesgo reducido)"}.get,
                   help="⛔ NO OPERAR nunca se ejecuta automáticamente.")
    if lvl != stt.get("min_level", "OPERAR"):
        stt = pilot.set(min_level=lvl)
    maxl = c[3].number_input("Pérdida diaria máx. %", 0.5, 10.0, float(stt["max_loss_pct"]), step=0.5, key="ap_loss")
    if (on, mode, maxt, maxl) != (stt["enabled"], stt["mode"], stt["max_trades_day"], stt["max_loss_pct"]):
        stt = pilot.set(enabled=on, mode=mode if (unlocked and live_enabled()) else "paper", max_trades_day=int(maxt),
                        max_loss_pct=float(maxl))
    st.caption(("🔓 Modo real desbloqueado. " if unlocked else "🔒 Modo real bloqueado. ") + why
               + ("" if live_enabled() else " · Además hace falta LIVE_TRADING_ENABLED=true en .env."))
    if st.button("⛔ APAGAR PILOTO YA", key="ap_kill", type="secondary"):
        pilot.kill()
        st.rerun()

    def loop() -> None:
        if pilot.state()["enabled"]:
            try:
                dec = pilot.tick(capital, market, req.exchange)
                if dec is not None:
                    st.session_state["dk_last"] = dec
            except Exception as exc:  # noqa: BLE001
                st.caption(f"Piloto: {exc}")
        ev = pilot.state()["events"][-12:]
        if ev:
            st.markdown("  \n".join(f"`{e['at']}` {e['text']}" for e in reversed(ev)))
        else:
            st.caption("Sin actividad del piloto todavía.")

    st.fragment(loop, run_every=30 if stt["enabled"] else None)()
    st.caption("Para que el piloto siga sin la app abierta: `python -m engine.fgview.desk` en una terminal.")
