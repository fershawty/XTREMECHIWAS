"""
ui/israel_pages.py
==================
Páginas de FG ISRAEL VIEW Build 1 (módulos 7–16 y la estrategia 🔥 BURN OUT 🔥).

Todas reciben los servicios POO (FGServices) y la petición de la barra lateral, y se
adaptan al modo 🎓 PRINCIPIANTE GOY / 🕎 EXPERTO RABBI.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from engine.config import SETTINGS
from engine.fgview.burnout import BURN_RULES, CONFIRM_WORD
from engine.fgview.executor import live_enabled
from engine.fgview.pricealerts import KINDS
from engine.fgview.timemachine import TimeMachineResult
from engine.position_simulator import fmt_px, fmt_usd
from ui.mode import beginner, details, guide, say

RED = "#FF3B3B"


# =============================================================================
# Utilidades comunes
# =============================================================================
def _capital() -> float:
    return float(st.session_state.get("equity", SETTINGS.account_equity))


def _coin_picker(svc, req, key: str, tiers: tuple[str, ...] = ("A", "B", "C")) -> tuple[str, str]:
    """(símbolo, mercado). En demo siempre DEMO/USDT."""
    if req.market == "demo":
        st.caption("Mercado demo: se usa DEMO/USDT (datos sintéticos).")
        return "DEMO/USDT", "demo"
    if req.market != "crypto":
        st.info("Estos módulos son para futuros cripto: elige «Cripto» o «Demo» en la barra lateral.")
        st.stop()
    coins = [c for t in svc.tiers.tiers if t.key in tiers for c in t.coins]
    base = req.symbol.split("/")[0].upper()
    idx = coins.index(base) if base in coins else 0
    coin = st.selectbox("Cripto", coins, index=idx, key=f"{key}_coin",
                        format_func=lambda c: f"{c} · {svc.tiers.label(c)}")
    return f"{coin}/USDT", "crypto"


def _banner(text: str, color: str = RED, bg: str = "rgba(255,59,59,.10)") -> None:
    st.markdown(f'<div style="border:2px solid {color};background:{bg};border-radius:12px;padding:.8rem 1rem;'
                f'margin:.4rem 0;font-weight:600">{text}</div>', unsafe_allow_html=True)


def run_time_machine(svc, plan, symbol: str, market: str, exchange: str | None, days: int = 90,
                     timeframe: str = "1m", sims: int = 100_000) -> TimeMachineResult:
    """
    Descarga (o lee de caché) el historial y corre el Time Machine para un plan.
    Si es la primera vez y aún no hay velas de 1 min guardadas, usa las de 5 min (18
    peticiones, segundos) y deja bajando las de 1 min en segundo plano para la próxima.
    """
    import time as _t

    notes = []
    if market == "crypto" and timeframe == "1m" and not svc.history.has_cache(symbol, "1m", exchange):
        svc.history.prefetch(symbol, market, ("1m",), days, exchange)
        timeframe = "5m"
        notes.append("Primera vez con esta cripto: se usaron velas de 5 min para darte el resultado YA. Las de 1 min "
                     "se están descargando en segundo plano y se usarán la próxima vez (más precisión).")
    bar = st.progress(0.0, text="Preparando historial…")
    t0 = _t.perf_counter()
    try:
        df, _src = svc.history.get(symbol, market, timeframe, days, exchange=exchange,
                                   progress=lambda f, txt: bar.progress(min(1.0, f), text=txt))
        if timeframe == "1m" and len(df) < 2000:  # Yahoo solo da 7 días de 1m: se pasa a 5m
            df, _src = svc.history.get(symbol, market, "5m", days, exchange=exchange)
            timeframe = "5m"
    finally:
        bar.empty()
    load = _t.perf_counter() - t0
    res = svc.time_machine.run(plan, df, bar_minutes=int(timeframe[:-1]), sims=sims)
    res.load_s = load
    res.notes = notes + res.notes
    return res


def _tm_block(res: TimeMachineResult) -> None:
    color = {"OPERAR": "#3FB950", "ESPERAR": "#F2D27A", "NO OPERAR": "#F85149"}[res.verdict]
    c = st.columns(5)
    c[0].metric("P(TP1)", f"{res.p_tp1:.1f}%")
    c[1].metric("P(TP2)", f"{res.p_tp2:.1f}%")
    c[2].metric("P(TP3)", f"{res.p_tp3:.1f}%")
    c[3].metric("P(SL)", f"{res.p_sl:.1f}%")
    c[4].metric("Salida por tiempo", f"{res.p_time:.1f}%")
    _banner(f"{res.verdict_icon} Veredicto: {res.verdict} · Expectativa {fmt_usd(res.ev, True)} por trade · "
            f"Peor 5%: −{fmt_usd(max(0.0, res.var95))}", color, "rgba(0,0,0,.25)")
    say(res.beginner_text(), res.text())
    for n in res.notes:
        st.caption(f"ℹ️ {n}")
    st.caption("Las probabilidades son estimaciones con datos históricos, no garantías.")


# =============================================================================
# MÓDULO 7 — TIME MACHINE
# =============================================================================
def render_time_machine(svc, req) -> None:
    st.markdown("### ⏳ Time Machine · 100,000 simulaciones")
    guide("Antes de operar, prueba tu operación 100,000 veces con el comportamiento REAL del precio de los últimos "
          "90 días (solo en momentos con una volatilidad parecida a la de hoy) y te dice cuántas veces habría ganado.",
          ["Elige la cripto y tu capital.", "Pulsa «Viajar en el tiempo».",
           "Mira el veredicto: ✅ OPERAR, ⚠️ ESPERAR o ❌ NO OPERAR."],
          "La primera vez descarga 90 días de velas (~1 min). Luego es instantáneo.")
    c1, c2, c3 = st.columns([1.4, 1, 1])
    with c1:
        symbol, market = _coin_picker(svc, req, "tm")
    direction = c2.selectbox("Dirección", ["Auto", "LONG", "SHORT"], key="tm_dir")
    capital = c3.number_input("Capital (USDT)", 10.0, 10_000_000.0, _capital(), step=50.0, key="tm_cap")
    days, tf = 90, "1m"
    if not beginner():
        e1, e2 = st.columns(2)
        days = e1.select_slider("Días de historia", [30, 60, 90], 90, key="tm_days")
        tf = e2.selectbox("Velas", ["1m", "5m"], key="tm_tf")
    if st.button("⏳ Viajar en el tiempo", type="primary", key="tm_go"):
        try:
            with st.spinner("Descargando historia y corriendo 100,000 simulaciones…"):
                snap = svc.loader.load(symbol, market, exchange=req.exchange)
                rep = (svc.filters.best(snap, symbol) if direction == "Auto"
                       else svc.filters.evaluate(snap, direction, symbol))
                plan = svc.planner.plan(capital, symbol, rep.direction, snap.price, snap.atr5)
                res = run_time_machine(svc, plan, symbol, market, req.exchange, days, tf)
            st.session_state["tm_last"] = (plan, rep, res)
        except Exception as exc:  # noqa: BLE001
            st.error(f"No se pudo correr el Time Machine: {exc}")
    last = st.session_state.get("tm_last")
    if last:
        plan, rep, res = last
        st.markdown(f"**{plan.coin} {plan.direction}** · entrada ${fmt_px(plan.entry)} · SL ${fmt_px(plan.stop_loss)} · "
                    f"TP1 ${fmt_px(plan.tps[0][0])} · {plan.leverage}x · señal {rep.score}/100")
        _tm_block(res)


# =============================================================================
# MÓDULO 8 — CHART SCANNER
# =============================================================================
def render_scanner(svc, req) -> None:
    st.markdown("### 📡 Chart Scanner · niveles A, B y C en 5m, 15m y 1h")
    guide("Revisa todas las criptos de la lista y te muestra las 5 con mejores oportunidades ahora mismo.",
          ["Pulsa «Escanear ahora» (o activa el auto-escaneo cada minuto).",
           "Las de arriba tienen más puntuación (0 a 100).", "Abre «Time Machine» con la que te guste antes de entrar."])
    market = "demo" if req.market == "demo" else "crypto"
    c1, c2 = st.columns([1, 1])
    go = c1.button("📡 Escanear ahora", type="primary", key="sc_go")
    auto = c2.toggle("Auto-escaneo cada 1 min", key="sc_auto")

    def body() -> None:
        if not (go or auto) and "sc_last" not in st.session_state:
            st.caption("Pulsa «Escanear ahora». La primera vez tarda ~1 min (37 criptos × 3 temporalidades).")
            return
        if go or auto:
            with st.spinner(f"Escaneando {1 if market == 'demo' else len(svc.tiers.all_coins)} criptos × 3 temporalidades…"):
                st.session_state["sc_last"] = (datetime.now(timezone.utc), *svc.scanner.scan(market=market,
                                                                                             exchange=req.exchange))
        when, top, hits = st.session_state["sc_last"]
        st.caption(f"Último escaneo: {when:%H:%M:%S} UTC")
        for i, h in enumerate(top, 1):
            color = "#3FB950" if h.direction == "LONG" else "#F85149"
            st.markdown(f'<div style="border:1px solid {color};border-radius:10px;padding:.55rem .9rem;margin:.3rem 0">'
                        f'<b>#{i} {h.coin} {h.direction}</b> · puntuación <b>{h.score}/100</b> · nivel {h.tier} · '
                        f'${fmt_px(h.price)}<br><span style="color:#aaa">{h.summary}</span></div>', unsafe_allow_html=True)
            if beginner() and h.detections:
                st.caption(" · ".join(f"{d.plain}" for d in h.detections[:3]))
        with details("🔬 Ver todas las criptos escaneadas"):
            st.dataframe(pd.DataFrame([{"Cripto": h.coin, "Nivel": h.tier, "Dirección": h.direction,
                                        "Puntuación": h.score, "Filtros": h.filters_score, "Detecciones": h.summary,
                                        "Error": h.error or ""} for h in hits]), hide_index=True, width="stretch")

    st.fragment(body, run_every=60 if auto else None)()


# =============================================================================
# MÓDULO 9 — NEWS MONITOR
# =============================================================================
def render_news_monitor(svc, req) -> None:
    st.markdown("### 📰 News Monitor")
    guide("Junta el calendario económico (CPI, FOMC, NFP), las noticias cripto (hackeos, listados, desbloqueos, ETFs) "
          "y los anuncios de exchanges. Las de impacto alto 🔴 bloquean las entradas 30 minutos antes y después.")
    if st.button("🔄 Actualizar noticias", key="nm_go") or "nm_last" not in st.session_state:
        with st.spinner("Leyendo noticias…"):
            st.session_state["nm_last"] = svc.news_monitor.all()
    items = st.session_state["nm_last"]
    blocks = svc.news_monitor.blocks(items)
    if blocks:
        for b in blocks:
            _banner(f"⛔ {b}")
    else:
        st.success("Sin noticias de impacto alto en la ventana de ±30 min: no hay bloqueo por noticias.")
    imp = st.segmented_control("Impacto", ["alto", "medio", "bajo"], selection_mode="multi",
                               default=["alto", "medio"] if beginner() else ["alto", "medio", "bajo"], key="nm_imp")
    sent_icon = {"alcista": "🟢", "bajista": "🔴", "neutro": "⚪"}
    shown = [n for n in items if n.impact in (imp or [])]
    if not shown:
        st.caption("No hay noticias con ese filtro (o no hay conexión).")
    for n in shown[:60]:
        when = n.when.strftime("%d/%m %H:%M UTC") if n.when else "—"
        title = f"[{n.title}]({n.link})" if n.link else n.title
        st.markdown(f"{n.icon} **{n.impact.upper()}** · {sent_icon[n.sentiment]} {n.sentiment} · _{n.category}_ · "
                    f"{when}  \n{title} — {n.source}")


# =============================================================================
# MÓDULO 10 — ENTRY TIMER
# =============================================================================
def render_entry_timer(svc, req) -> None:
    st.markdown("### ⏱️ Entry Timer")
    guide("Te dice el momento exacto para entrar: al cierre de la vela de 5 minutos, en buen horario, lejos del cambio "
          "de funding y sin perseguir el precio.",
          ["Elige la cripto (usa la entrada ideal de tu última recomendación o escríbela).",
           "Espera a que diga ✅ ENTRA AHORA.", "Si dice ❌ cancelada, no persigas el precio: espera otra señal."])
    symbol, market = _coin_picker(svc, req, "et")
    last = st.session_state.get("tm_last")
    default_entry = float(last[0].entry) if last and last[0].coin in symbol else 0.0
    ideal = st.number_input("Entrada ideal (0 = sin comprobar distancia)", 0.0, value=default_entry, format="%.6f",
                            key="et_ideal")

    @st.fragment(run_every=5)
    def live() -> None:
        try:
            snap = svc.loader.load(symbol, market, exchange=req.exchange)
            price, atr = snap.price, snap.atr5
        except Exception as exc:  # noqa: BLE001
            st.error(f"Sin datos: {exc}")
            return
        stt = svc.timer.check(ideal_entry=ideal or None, price=price, atr=atr)
        if stt.can_enter:
            _banner("✅ ENTRA AHORA: vela cerrada y todo en orden.", "#3FB950", "rgba(63,185,80,.12)")
        elif stt.cancelled:
            _banner("❌ SEÑAL CANCELADA: el precio se escapó.")
        else:
            _banner(f"⏳ ESPERA · próximo cierre en {stt.seconds_to_close // 60}:{stt.seconds_to_close % 60:02d}",
                    "#F2D27A", "rgba(242,210,122,.08)")
        c = st.columns(3)
        c[0].metric("Precio", f"${fmt_px(price)}")
        c[1].metric("Funding en", f"{stt.minutes_to_funding:.0f} min")
        c[2].metric("Sesión", stt.session or "fuera de horario")
        for m in stt.messages:
            st.markdown(f"- {m}")

    live()


# =============================================================================
# MÓDULO 11 — RISK MANAGER
# =============================================================================
def render_risk_manager(svc, req) -> None:
    st.markdown("### 🛡️ Risk Manager")
    guide("Vigila que nunca arriesgues de más: máximo 3% de pérdida al día, máximo 3 pérdidas seguidas, máximo 2 "
          "posiciones abiertas, nunca dos criptos que se mueven igual en la misma dirección y la liquidación siempre "
          "lejos (≥ 3× la distancia del SL).")
    capital = _capital()
    today = svc.journal.today() if svc.journal else []
    msg = svc.recommender.guard.check(today, capital) if svc.recommender.guard else None
    net = sum(t.get("pnl") or 0 for t in today)
    c = st.columns(3)
    c[0].metric("PnL de hoy", fmt_usd(net, True), f"{net / capital * 100:+.2f}% (límite −3%)" if capital else None)
    c[1].metric("Operaciones hoy", len(today))
    c[2].metric("Posiciones abiertas", f"{len(svc.positions.all())} / {svc.risk.max_open}")
    if msg:
        _banner(f"⛔ {msg}")
    else:
        st.success("Stop diario: OK. Puedes operar.")
    st.markdown("#### Comprobar una operación nueva")
    symbol, market = _coin_picker(svc, req, "rm")
    direction = st.radio("Dirección", ["LONG", "SHORT"], horizontal=True, key="rm_dir")
    if st.button("🛡️ Comprobar", key="rm_go"):
        try:
            snap = svc.loader.load(symbol, market, exchange=req.exchange)
            plan = svc.planner.plan(capital, symbol, direction, snap.price, snap.atr5)
            data = {plan.coin: snap.df5}
            for p in svc.positions.all():
                try:
                    sym = "DEMO/USDT" if p.get("market") == "demo" else f"{p['coin']}/USDT"
                    data[p["coin"]] = svc.loader.load(sym, p.get("market", "crypto"), exchange=req.exchange).df5
                except Exception:  # noqa: BLE001
                    pass
            rc = svc.risk.check(plan.coin, direction, plan.entry, plan.stop_loss, plan.liquidation, capital, today, data)
            if rc.allowed:
                st.success(f"✅ Permitida: {plan.coin} {direction} {plan.leverage}x · liquidación a "
                           f"{abs(plan.entry - plan.liquidation) / abs(plan.entry - plan.stop_loss):.1f}× el SL.")
            for b in rc.blocks:
                _banner(f"⛔ {b}")
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))


# =============================================================================
# MÓDULO 12 — BUDGET MANAGER
# =============================================================================
def render_budget(svc, req) -> None:
    st.markdown("### 💰 Budget Manager")
    guide("Lleva tu saldo real después de cada operación (de la Bitácora) y ajusta cuánto arriesgar: si pierdes un 10% "
          "baja el riesgo a la mitad, y cada vez que creces un 25% te sugiere retirar el 20% de la ganancia.",
          ["Escribe tu capital inicial y guárdalo.", "Registra tus operaciones en la Bitácora (o usa el modo papel).",
           "Sigue el riesgo por trade que te marca aquí."])
    b = svc.budget
    st_ = b.status()
    c1, c2 = st.columns([1, 1])
    start = c1.number_input("Capital inicial (USDT)", 0.0, value=float(st_.start or _capital()), step=50.0, key="bm_start")
    if c2.button("Guardar capital inicial", key="bm_save"):
        b.set_start(start)
        st.rerun()
    st_ = b.status()
    k = st.columns(4)
    k[0].metric("Saldo", fmt_usd(st_.balance))
    k[1].metric("Drawdown", f"{st_.drawdown_pct:.1f}%")
    k[2].metric("Riesgo por trade", f"{st_.risk_pct:g}%", "mitad (defensivo)" if st_.defensive else None,
                delta_color="inverse")
    k[3].metric("Retirado", fmt_usd(st_.withdrawn))
    for m in st_.messages:
        st.info(m)
    if st_.withdraw_suggestion > 0 and st.button(f"Marcar retiro de {fmt_usd(st_.withdraw_suggestion)}", key="bm_wd"):
        b.register_withdrawal(st_.withdraw_suggestion)
        st.rerun()
    if len(st_.curve) > 1:
        with details("🔬 Curva de capital"):
            st.line_chart(pd.Series(st_.curve, name="Saldo"), height=240)


# =============================================================================
# MÓDULO 13 — ORDER EXECUTOR
# =============================================================================
def _positions_table(svc) -> None:
    pos = svc.positions.all()
    if not pos:
        st.caption("No hay posiciones abiertas.")
        return
    for p in pos:
        cols = st.columns([4, 1.2, 1])
        cols[0].markdown(f"**{p['coin']} {p['direction']}** · {p.get('strategy', '')} · _{p.get('mode', '')}_ · "
                         f"{p['leverage']}x · entrada ${fmt_px(p['entry'])} · SL ${fmt_px(p['stop_loss'])} · "
                         f"liq ${fmt_px(p['liquidation'])}")
        px = cols[1].number_input("Precio de salida", 0.0, value=float(p["entry"]), format="%.6f",
                                  key=f"px_{p['id']}", label_visibility="collapsed")
        if cols[2].button("Cerrar", key=f"cl_{p['id']}"):
            res = svc.executor.close(p["id"], px)
            st.toast(f"Cerrada {p['coin']}: {fmt_usd(res['pnl'], True)}")
            st.rerun()


def render_executor(svc, req) -> None:
    st.markdown("### 🤖 Order Executor · semiautomático")
    guide("Prepara la orden completa (entrada + SL + 3 TPs reduce-only, margen aislado) y SOLO la envía si tú "
          "confirmas. Empieza SIEMPRE en modo PAPEL (sin dinero) hasta que confíes.",
          ["Elige la cripto y pulsa «Preparar orden».", "Revisa la orden y el chequeo de riesgo.",
           "Marca la casilla de confirmación y envía."])
    live = live_enabled()
    mode = st.radio("Modo", ["papel", "real"], horizontal=True, key="ex_mode",
                    format_func={"papel": "📝 Papel (sin dinero)", "real": "💵 Real (Binance/Bybit)"}.get,
                    disabled=not live)
    if not live:
        st.caption("Modo real desactivado. Para activarlo: LIVE_TRADING_ENABLED=true y tus API keys en .env "
                   "(solo trading, SIN retiros, con lista de IPs). El bot verifica los permisos antes de operar.")
    symbol, market = _coin_picker(svc, req, "ex")
    capital = _capital()
    exch = st.selectbox("Exchange", ["binance", "bybit"], key="ex_exch")
    if st.button("🧾 Preparar orden", type="primary", key="ex_prep"):
        try:
            rec = svc.recommender.recommend(symbol, market, capital, exchange=req.exchange)
            ticket = svc.executor.build_ticket(rec.plan, symbol, market, exch, "FG VIEW", rec.report.score)
            st.session_state["ex_ticket"] = (ticket, rec)
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
    if "ex_ticket" in st.session_state:
        ticket, rec = st.session_state["ex_ticket"]
        st.markdown("  \n".join(ticket.lines()))
        st.caption(f"Semáforo FG VIEW: {rec.light} · confianza {rec.report.score}/100")
        today = svc.journal.today() if svc.journal else []
        rc = svc.risk.check(ticket.coin, ticket.direction, ticket.entry, ticket.stop_loss, ticket.liquidation, capital,
                            today)
        for b in rc.blocks + ([] if rec.light != "rojo" else ["El semáforo FG VIEW está en rojo."]):
            _banner(f"⛔ {b}")
        ok = st.checkbox("Confirmo que revisé la orden y acepto el riesgo", key="ex_ok")
        if st.button("🚀 Enviar orden", disabled=not ok or not rc.allowed, key="ex_send"):
            res = svc.executor.submit(ticket, confirmed=ok, mode="live" if mode == "real" else "paper")
            if res.get("status") == "filled":
                st.success("Orden enviada." + (" (papel)" if mode == "papel" else " Recuerda mover el SL a break-even "
                                                                                   "en el exchange cuando se cobre TP1."))
                st.session_state.pop("ex_ticket", None)
            else:
                for p in res.get("problems", [res.get("message", "Error")]):
                    _banner(f"⛔ {p}")
    st.markdown("#### Posiciones abiertas")
    on = st.toggle("🛡️ Escudo anti-squeeze automático", value=svc.executor.shield_enabled(), key="ex_shield",
                   help="Si detecta una subida/caída brusca contra tu posición: con ganancia mueve el SL a break-even; "
                        "con pérdida lo acerca a 0.5 × ATR del precio. Nunca aleja el SL. En real mueve la orden en el "
                        "exchange. Funciona mientras la app está abierta (o con python -m engine.fgview.pricealerts).")
    if on != svc.executor.shield_enabled():
        svc.executor.set_shield(on)
    if st.button("🔄 Actualizar posiciones de papel", key="ex_ref"):
        for c in svc.executor.refresh_paper():
            st.toast(f"{c['coin']} cerrada ({c['reason']}): {fmt_usd(c['pnl'], True)}")
    _positions_table(svc)


# =============================================================================
# MÓDULO 14 — PRICE ALERTS
# =============================================================================
def render_alerts(svc, req) -> None:
    st.markdown("### 🔔 Alertas de precio")
    guide("Te avisa por Telegram o notificación push cuando el precio llega a un nivel, cuando cruzan las medias, si "
          "hay un movimiento brusco (> 2% en 5 min) o si tu posición se acerca a la liquidación.",
          ["Configura Telegram o un webhook en el archivo .env (TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID).",
           "Crea tus alertas aquí.", "Deja activada «Vigilar cada minuto» o ejecuta: python -m engine.fgview.pricealerts"])
    ch = svc.alerts.sender.channels
    st.caption(f"Canales activos: {', '.join(ch) if ch else 'ninguno (solo se verán aquí)'}")
    symbol, market = _coin_picker(svc, req, "al")
    c1, c2, c3 = st.columns([1.6, 1, 0.8], vertical_alignment="bottom")
    kind = c1.selectbox("Tipo", list(KINDS), format_func=KINDS.get, key="al_kind")
    value = c2.number_input("Precio", 0.0, format="%.6f", key="al_val", disabled=kind not in ("above", "below"))
    if c3.button("➕ Añadir", key="al_add"):
        svc.alerts.add(symbol.split("/")[0], kind, value if kind in ("above", "below") else None, market)
        st.rerun()
    for r in svc.alerts.rules():
        cols = st.columns([5, 1])
        val = f" ${fmt_px(r['value'])}" if r.get("value") else ""
        cols[0].markdown(f"• **{r['coin']}** · {KINDS[r['kind']]}{val} · último aviso: {r.get('last_fired') or '—'}")
        if cols[1].button("🗑️", key=f"al_del_{r['id']}"):
            svc.alerts.remove(r["id"])
            st.rerun()
    st.caption("La alerta de liquidación cerca (< 1.5× la distancia del SL) es automática para cada posición abierta.")
    watch = st.toggle("Vigilar cada minuto", key="al_watch")

    def check() -> None:
        if watch:
            for f in svc.alerts.check():
                st.toast(f.message, icon="🔔")
            st.caption(f"Última revisión: {datetime.now(timezone.utc):%H:%M:%S} UTC")

    st.fragment(check, run_every=60 if watch else None)()


# =============================================================================
# MÓDULO 15 — FUNDAMENTALES CRIPTO
# =============================================================================
def render_fundamentals(svc, req) -> None:
    st.markdown("### 💎 Earnings Analyst · versión cripto")
    guide("Lo equivalente a los reportes de ganancias de una empresa, pero en cripto: cuánto cobra cada red en "
          "comisiones, desbloqueos de tokens (más tokens a la venta = presión bajista), quemas, ETFs y flujos de exchanges.")
    f = svc.fundamentals
    warns = f.warnings()
    for w in warns:
        _banner(w, "#F2D27A", "rgba(242,210,122,.08)")
    st.markdown("#### 🔓 Desbloqueos de los próximos 7 días")
    ul = f.unlocks()
    if ul:
        st.dataframe(pd.DataFrame([{"Cripto": u.coin, "Fecha": u.when, "% circulante": u.pct_circulating,
                                    "Aviso": "⚠️" if u.warning else "", "Nota": u.note} for u in ul]),
                     hide_index=True, width="stretch")
    else:
        st.caption("Sin desbloqueos registrados para los próximos 7 días.")
    if st.button("💵 Cargar ingresos (fees) de las redes", key="fu_fees_btn"):
        with st.spinner("Consultando DefiLlama…"):
            st.session_state["fu_fees"] = f.fees_many([c for t in svc.tiers.tiers for c in t.coins])
    fees = st.session_state.get("fu_fees")
    if fees:
        st.dataframe(pd.DataFrame([{"Cripto": x.coin, "Red/protocolo": x.name,
                                    "Fees 24 h": fmt_usd(x.fees_24h or 0), "Fees 7 días": fmt_usd(x.fees_7d or 0),
                                    "Cambio 1 d": f"{x.change_1d:+.1f}%" if x.change_1d is not None else "—"}
                                   for x in fees]), hide_index=True, width="stretch")
    elif fees is not None:
        st.caption("DefiLlama no respondió (¿sin internet?).")
    manual = f.manual()
    with details("📂 Quemas, ETFs y flujos de exchanges (archivo editable)"):
        for key, title in (("burns", "🔥 Quemas"), ("etf_flows", "🏦 Flujos de ETFs"),
                           ("exchange_flows", "🔄 Entradas/salidas de exchanges")):
            rows = manual.get(key) or []
            st.markdown(f"**{title}**")
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch") if rows else st.caption("Sin datos.")
        st.caption(f"Edita {f.path} con datos de Tokenomist, Farside o CryptoQuant.")


# =============================================================================
# MÓDULO 16 — SMART MONEY TRACKER
# =============================================================================
def render_smart_money(svc, req) -> None:
    st.markdown("### 🐋 Smart Money Tracker")
    guide("Mira qué hacen los que mueven mucho dinero: congresistas de EE. UU. comprando o vendiendo acciones ligadas a "
          "cripto (COIN, MSTR, mineras, ETFs) y ballenas moviendo millones hacia o desde los exchanges.",
          tip="Es solo contexto: nunca entres solo porque una ballena se movió.")
    _banner("ℹ️ Solo contexto. Nunca es una señal de entrada por sí sola.", "#F2D27A", "rgba(242,210,122,.06)")
    if st.button("🏛️ Cargar operaciones del Congreso", key="sm_cg_btn"):
        with st.spinner("Descargando reportes públicos (STOCK Act)…"):
            st.session_state["sm_cg"] = svc.smart_money.congress()
    if "sm_cg" in st.session_state:
        rows, note = st.session_state["sm_cg"]
        st.caption(f"Fuente: {note}")
        if rows:
            st.dataframe(pd.DataFrame([r.__dict__ for r in rows[:100]]).rename(columns={
                "when": "Fecha", "person": "Congresista", "chamber": "Cámara", "ticker": "Ticker", "kind": "Tipo",
                "amount": "Monto"}), hide_index=True, width="stretch")
        else:
            st.caption("Sin operaciones recientes en acciones cripto (o la fuente no respondió).")
    st.markdown("#### 🐋 Ballenas (última hora, > $5M)")
    moves, note = svc.smart_money.whales()
    st.caption(note)
    for w in moves[:30]:
        st.markdown(f"- {w.when:%H:%M} · **{w.coin}** {fmt_usd(w.usd)} · {w.flow} → _{w.reading}_ · {w.detail}")


# =============================================================================
# 🔥 BURN OUT 🔥
# =============================================================================
def render_burnout(svc, req) -> None:
    st.markdown(f'<div style="background:linear-gradient(90deg,#3a0000,#7a0000);border:2px solid {RED};border-radius:14px;'
                'padding:1rem 1.2rem;margin-bottom:.8rem"><div style="font-size:1.8rem;font-weight:900;color:#fff">'
                '🔥 BURN OUT 🔥</div><div style="color:#ffd0d0;font-weight:700">⛔ PELIGRO EXTREMO · presupuesto bajo + '
                'apalancamiento alto · puedes perder el 100% del monto</div></div>', unsafe_allow_html=True)
    guide("Usas una «bolsa de quema» pequeña y separada (máx. 5% de tu capital) con apalancamiento de 50x a 100x para "
          "intentar multiplicarla rápido. Es RECOMENDADO cuando el Time Machine y la señal son muy buenos; si no, te da la "
          "operación igual con un aviso. Si la bolsa llega a $0 se bloquea 7 días.",
          ["Carga la bolsa de quema (dinero que aceptas perder).", "Elige BTC o ETH (o una de nivel B).",
           "Pulsa «Analizar BURN OUT»: si todo da verde, lee la advertencia y escribe CONFIRMO."],
          "Si algo no se cumple te lo marca ⚠️ NO RECOMENDADO, pero la operación se muestra igual: tú decides.")
    bo, capital = svc.burnout, _capital()
    bag = bo.bag()
    today = datetime.now(timezone.utc).date().isoformat()
    k = st.columns(4)
    k[0].metric("Bolsa de quema", fmt_usd(bag.balance))
    k[1].metric("Máximo permitido", fmt_usd(bo.max_bag(capital)), "5% del capital")
    k[2].metric("Intentos hoy", f"{bag.attempts_today(today)} / {bo.MAX_ATTEMPTS}")
    k[3].metric("Estado", "🔒 BLOQUEADA" if bo.locked() else "abierta")
    if (lock := bo.locked()):
        _banner(f"🔒 {lock}")
    with st.expander("➕ Cargar la bolsa de quema", expanded=bag.balance < bo.EMPTY):
        amt = st.number_input("Monto (USDT)", 0.0, float(bo.max_bag(capital)), min(15.0, bo.max_bag(capital)),
                              step=5.0, key="bo_amt")
        if st.button("Cargar bolsa", key="bo_fund"):
            err = bo.fund(amt, capital)
            st.error(err) if err else st.success(f"Bolsa cargada: {fmt_usd(bo.bag().balance)}")
    symbol, market = _coin_picker(svc, req, "bo", tiers=("A", "B"))
    st.caption("⛔ PROHIBIDO en memecoins (nivel C) · margen AISLADO obligatorio · "
               + " · ".join(f"Nivel {t}: {r.lev_min}–{r.lev_max}x, SL {r.sl_min}–{r.sl_max}%, TP {'/'.join(map(str, r.tps))}%"
                            for t, r in BURN_RULES.items()))
    stake_pct = 100 if beginner() else st.slider("% de la bolsa en este intento", 25, 100, 100, step=25, key="bo_pct")
    if bo.bag().balance < bo.EMPTY:
        st.info("Carga primero la bolsa de quema para analizar un BURN OUT.")
    if st.button("🔥 Analizar BURN OUT", type="primary", key="bo_go", disabled=bo.bag().balance < bo.EMPTY):
        try:
            with st.spinner("Señal + anti-squeeze + Time Machine (100,000 simulaciones)…"):
                snap = svc.loader.load(symbol, market, exchange=req.exchange)
                ext = svc.recommender.external_blocks(capital)
                rep = svc.filters.best(snap, symbol, ext)
                sq = svc.recommender.squeeze.analyze(snap.df5, rep.direction, snap.funding)
                plan = bo.build_plan(symbol, rep.direction, snap.price, snap.atr5,
                                     bo.bag().balance * stake_pct / 100, lev_cap=sq.lev_cap)
                tm = run_time_machine(svc, plan, symbol, market, req.exchange)
                dec = bo.decide(plan, rep.score, tm, rep.blocks, squeeze_text=sq.text() if sq.danger else None)
            st.session_state["bo_last"] = (plan, rep, tm, dec, symbol, market)
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
    if "bo_last" not in st.session_state:
        return
    plan, rep, tm, dec, symbol, market = st.session_state["bo_last"]
    if dec.recommended:
        _banner("✅ BURN OUT RECOMENDADO: la señal y el Time Machine cumplen las condiciones.", "#3FB950",
                "rgba(63,185,80,.12)")
    elif dec.active:
        _banner("⚠️ BURN OUT NO RECOMENDADO · la operación se muestra igual porque así lo elegiste. Lee los motivos.",
                "#F2D27A", "rgba(242,210,122,.10)")
        for w in dec.warnings:
            st.markdown(f"- ⚠️ {w}")
    arrow = "🟢 LONG" if plan.direction == "LONG" else "🔴 SHORT"
    st.markdown(f"### {arrow} {plan.coin} · {plan.leverage}x AISLADO")
    st.markdown(f"Margen {fmt_usd(plan.margin)} → posición {fmt_usd(plan.notional)} · entrada **${fmt_px(plan.entry)}** · "
                f"SL **${fmt_px(plan.stop_loss)}** (−{plan.sl_pct:.2f}%) · liquidación ${fmt_px(plan.liquidation)}  \n"
                f"TP1 **${fmt_px(plan.tps[0][0])}** (50%) · TP2 **${fmt_px(plan.tps[1][0])}** (30%) · "
                f"TP3 **${fmt_px(plan.tps[2][0])}** (20%)")
    st.markdown(f"Si sale bien (TP1+TP2+TP3): **×{bo.multiplier(plan):.2f}** la bolsa ({fmt_usd(sum(plan.tp_pnl), True)}) · "
                f"si salta el SL: **{fmt_usd(plan.sl_pnl, True)}** ({plan.sl_pnl / plan.margin * 100:.0f}% del monto)")
    for n in plan.notes[1:]:
        st.caption(n)
    _tm_block(tm)
    _banner(dec.warning)
    _banner("🛡️ Pon SIEMPRE el SL en el exchange al entrar. Con apalancamiento alto, sin SL un squeeze te liquida en "
            "minutos.", "#F2D27A", "rgba(242,210,122,.06)")
    if not dec.active:
        st.markdown("**No se puede operar ahora:**")
        for r in dec.reasons:
            st.markdown(f"- ⛔ {r}")
        return
    rc = svc.risk.check(plan.coin, plan.direction, plan.entry, plan.stop_loss, plan.liquidation, capital,
                        svc.journal.today() if svc.journal else [], liq_safety=bo.LIQ_SAFETY)
    for b in rc.blocks:  # En BURN OUT el Risk Manager avisa pero no bloquea
        st.markdown(f"- ⚠️ {b}")
    typed = st.text_input(f'Escribe "{CONFIRM_WORD}" para continuar', key="bo_confirm")
    mode = "live" if live_enabled() and st.toggle("💵 Enviar en REAL (si no, papel)", key="bo_live") else "paper"
    if st.button("🔥 EJECUTAR BURN OUT", disabled=not bo.confirmed(typed), key="bo_exec"):
        ticket = svc.executor.build_ticket(plan, symbol, market, "binance", "BURN OUT", rep.score)
        res = svc.executor.submit(ticket, confirmed=bo.confirmed(typed), mode=mode)
        if res.get("status") == "filled":
            st.success(f"🔥 BURN OUT enviado ({'real' if mode == 'live' else 'papel'}). Se gestiona en «Order Executor».")
            st.session_state.pop("bo_last", None)
        else:
            for p in res.get("problems", [res.get("message", "Error")]):
                _banner(f"⛔ {p}")
    with details("🔬 Historial de intentos"):
        st.dataframe(pd.DataFrame(bo.bag().attempts), hide_index=True, width="stretch")
