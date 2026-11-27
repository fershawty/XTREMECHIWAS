"""
app.py
======
Entrada principal de FG VIEW (interfaz Streamlit).

Ejecutar:
    streamlit run app.py

Distribución de la pantalla (rediseño Build 1):
* Barra lateral: menú por secciones (Operar, Escanear, Riesgo y capital, Contexto,
  Herramientas), interruptor 🎓 PRINCIPIANTE / 🧠 EXPERTO, mercado y capital.
* Cada módulo es una página: Modo fácil, Time Machine, Entry Timer, 🔥 BURN OUT 🔥,
  Chart Scanner, News Monitor, Alertas, Risk/Budget Manager, Order Executor, Bitácora,
  Análisis técnico (gráfico + pestañas), Fundamentales cripto, Smart Money…

La UI nunca se cae: cualquier error del motor se muestra como aviso.
"""

from __future__ import annotations

import streamlit as st

from pathlib import Path as _Path  # noqa: E402

_ICON = _Path(__file__).with_name("assets") / "icon.png"
from engine.config import APP_TITLE as _APP_TITLE  # noqa: E402

st.set_page_config(page_title=_APP_TITLE, page_icon=str(_ICON) if _ICON.exists() else "🟡", layout="wide",
                   initial_sidebar_state="expanded")

from engine.backtester import Backtester, BacktestConfig  # noqa: E402
from engine.config import APP_NAME, BUILD_TAG, SETTINGS  # noqa: E402
from engine.data_feeder import MARKET_LABELS, SYMBOL_PRESETS, TIMEFRAMES, Market  # noqa: E402
from engine.fgview import FGServices  # noqa: E402
from engine.pipeline import AnalysisPipeline, AnalysisRequest, AnalysisResult  # noqa: E402
from engine.risk_manager import get_profile  # noqa: E402
from strategies import AUTO_KEY, STRATEGIES  # noqa: E402
from ui import components as ui  # noqa: E402
from ui.simulator_tab import render_simulator  # noqa: E402
from ui.fgview_tab import render_fgview, render_journal  # noqa: E402
from ui.easy_tab import render_easy, render_glossary  # noqa: E402
from ui.ladder_tab import render_ladder  # noqa: E402
from ui import pro_pages as ip  # noqa: E402
from ui.desk_page import render_desk  # noqa: E402
from ui.grok_page import render_grok  # noqa: E402
from ui.grok_pick_page import render_grok_pick  # noqa: E402
from ui.mode import beginner, mode_switch  # noqa: E402
from ui import sidebar_ui  # noqa: E402
from engine.config import APP_VERSION  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402

ui.load_css()


@st.cache_resource(show_spinner=False)
def get_services() -> FGServices:
    """Servicios FG VIEW (POO) conectados una sola vez: comparten caché de datos con toda la app."""
    p = get_pipeline()
    return FGServices.create(p.feeder, news=p.news, db=p.db)


@st.cache_resource(show_spinner=False)
def get_pipeline() -> AnalysisPipeline:
    """Una sola instancia del motor para toda la sesión (mantiene cachés y conexiones)."""
    return AnalysisPipeline()


def _request_refresh() -> None:
    """Callback del botón: la próxima ejecución ignora la caché de datos."""
    st.session_state["_force_refresh"] = True


def _apply_query_params() -> None:
    """
    Preselección por URL (solo en la primera carga), útil para guardar enlaces:
        http://localhost:8501/?market=crypto&symbol=ETH/USDT&tf=4h&strategy=trend_following
    """
    if st.session_state.get("_qp_applied"):
        return
    st.session_state["_qp_applied"] = True
    st.session_state.setdefault("timeframe", "1h")
    qp = st.query_params
    try:
        if "market" in qp:
            market = Market(qp["market"])
            st.session_state["market"] = market
            if "symbol" in qp:
                st.session_state[f"custom_{market.value}"] = qp["symbol"][:30]
        if qp.get("tf") in TIMEFRAMES:
            st.session_state["timeframe"] = qp["tf"]
        if qp.get("strategy") in [AUTO_KEY, *STRATEGIES]:
            st.session_state["strategy"] = qp["strategy"]
    except ValueError:
        pass


# =============================================================================
# Barra lateral
# =============================================================================
def sidebar() -> tuple[AnalysisRequest, dict, bool, object]:
    """Dibuja el panel lateral y devuelve (petición, capas, auto-análisis, contenedor del menú)."""
    with st.sidebar:
        components.html(sidebar_ui.brand_html(ui._logo_svg(), APP_VERSION), height=74)
        st.markdown(sidebar_ui.market_card(st.session_state.get("brief")), unsafe_allow_html=True)
        nav_box = st.container()  # El menú se dibuja aquí cuando ya existen las páginas
        st.markdown('<div class="fg-sec"><span>MESA DE AGENTES</span><i></i></div>', unsafe_allow_html=True)
        components.html(sidebar_ui.agents_html(st.session_state.get("dk_last")), height=168)
        st.markdown('<div class="fg-sec"><span>CONFIGURACIÓN</span><i></i></div>', unsafe_allow_html=True)
        mode_switch()

        st.markdown('<div class="fs-side-title">Mercado</div>', unsafe_allow_html=True)
        markets = [Market.CRYPTO, Market.STOCK, Market.ETF, Market.CME, Market.DEMO]
        market = st.selectbox("Mercado", markets, format_func=lambda m: MARKET_LABELS[m],
                              key="market", label_visibility="collapsed")
        preset = st.selectbox("Símbolo", SYMBOL_PRESETS[market], key=f"preset_{market.value}")
        hint = {"crypto": "ej. PEPE/USDT", "stock": "ej. PLTR", "etf": "ej. VOO", "cme": "ej. MGC=F", "demo": "solo DEMO/USDT"}
        custom = st.text_input("Otro símbolo", key=f"custom_{market.value}", placeholder=hint[market.value])
        symbol = (custom or "").strip() or preset

        exchange, contract = None, "USDT-M"
        if market == Market.CRYPTO:
            c1, c2 = st.columns(2)
            exchange = c1.selectbox("Exchange", ["binance", "bybit", "okx"], format_func=str.capitalize, key="exchange")
            contract_label = c2.selectbox("Futuro", ["Perpetuo USDT-M", "Perpetuo COIN-M"], key="contract")
            contract = "COIN-M" if "COIN" in contract_label else "USDT-M"
        else:
            label = {"cme": "Contrato continuo", "demo": "Perpetuo (simulado)"}.get(market.value, "Contado (subyacente)")
            st.selectbox("Tipo de futuro", [label], disabled=True, key=f"contract_{market.value}")

        equity = st.number_input("💰 Tu capital (USDT / USD)", min_value=10.0, value=float(SETTINGS.account_equity),
                                 step=100.0, key="equity")

        adv = st.expander("📊 Ajustes del análisis técnico", expanded=not beginner())
        adv.__enter__()
        timeframe = st.segmented_control("Temporalidad", list(TIMEFRAMES), key="timeframe") or "1h"
        strategy_keys = [AUTO_KEY] + list(STRATEGIES)
        strategy = st.selectbox(
            "Estrategia", strategy_keys, key="strategy",
            format_func=lambda k: "Auto (recomendado)" if k == AUTO_KEY else STRATEGIES[k].name,
        )

        st.markdown('<div style="height:0.4rem"></div>', unsafe_allow_html=True)
        st.button("ANALIZAR AHORA", type="primary", width="stretch", on_click=_request_refresh)
        auto = st.toggle("Auto-análisis cada 60 s", key="auto")

        adv.__exit__(None, None, None)
        with st.expander("Gestión de riesgo"):
            prof = get_profile(equity)
            st.caption(f"Perfil {prof.label}: riesgo sugerido {prof.risk_pct:g}% · apalancamiento máx. "
                       f"{prof.max_leverage:g}x. {prof.note} El plan lo aplica automáticamente.")
            risk_pct = st.number_input("Riesgo por operación (%)", min_value=0.1, max_value=5.0,
                                       value=float(SETTINGS.risk_per_trade_pct), step=0.1, key="risk_pct")
            leverage = st.slider("Apalancamiento", 1, 50, int(SETTINGS.default_leverage), key="leverage")
            sl_method = st.selectbox("Stop Loss", ["auto", "atr", "order_block"], key="sl_method",
                                     format_func={"auto": "Auto (OB si es válido, si no ATR)",
                                                  "atr": f"ATR x {SETTINGS.atr_sl_multiplier:g}",
                                                  "order_block": "Bajo el Order Block"}.get)
            limit = st.select_slider("Velas a analizar", [300, 500, 1000, 1500, 2000],
                                     value=min([300, 500, 1000, 1500, 2000], key=lambda v: abs(v - SETTINGS.ohlcv_limit)),
                                     key="limit")
            use_context = st.toggle("Ajuste por contexto macro/social/político", value=SETTINGS.context_enabled,
                                    key="use_context")

        with st.expander("Capas del gráfico"):
            layers = {key: st.checkbox(label, value=ui.DEFAULT_LAYERS[key], key=f"layer_{key}")
                      for key, label in ui.LAYER_LABELS.items()}

    req = AnalysisRequest(symbol=symbol, market=market.value, timeframe=timeframe, contract=contract,
                          exchange=exchange, strategy=strategy, equity=equity, risk_pct=risk_pct,
                          leverage=leverage, sl_method=sl_method, limit=limit, use_context=use_context)
    return req, layers, auto, nav_box


def remember_brief(result) -> None:
    """Guarda un resumen del último análisis para la tarjeta de mercado de la barra lateral."""
    try:
        md, sig = result.market_data, result.signal
        closes = [float(x) for x in result.df["close"].iloc[-40:]]
        st.session_state["brief"] = {"symbol": md.symbol.upper(), "timeframe": md.timeframe, "price": ui.fmt_price(md.last_price),
                                     "change": float(md.change_pct), "closes": closes,
                                     "direction": sig.direction if sig is not None else None,
                                     "score": float(sig.score) if sig is not None else 0.0}
    except Exception:  # noqa: BLE001 - es un extra visual
        pass


# =============================================================================
# Panel principal
# =============================================================================
def journal_callback(pipeline: AnalysisPipeline, result: AnalysisResult):
    """Devuelve la función que guarda la recomendación actual en el diario (SQLite)."""

    def _save() -> None:
        sig, plan, md = result.signal, result.plan, result.market_data
        if md.market == Market.DEMO:
            st.toast("Las señales DEMO no se registran en el diario.")
            return
        sid = pipeline.db.save_signal({
            "bar_time": sig.timestamp.isoformat(), "symbol": md.symbol.upper(), "market": md.market.value,
            "timeframe": md.timeframe, "source": md.source, "strategy": f"manual:{result.strategy_key}",
            "direction": sig.direction, "score": sig.score, "grade": "MANUAL", "entry": plan.entry,
            "stop_loss": plan.stop_loss, "tp1": plan.tp1, "tp2": plan.tp2, "position_size": plan.position_size,
            "leverage": plan.leverage, "reasons": sig.reason_text,
        })
        st.toast("Registrado en el diario: se seguirá solo (TP1 / SL)." if sid else "Esta señal ya estaba registrada.")

    return _save


def render_backtest_tab(pipeline: AnalysisPipeline, result: AnalysisResult, req: AnalysisRequest) -> None:
    """Backtest sobre las velas cargadas con el mismo motor."""
    c1, c2, c3, c4 = st.columns([1.2, 1.2, 1, 1], vertical_alignment="bottom")
    threshold = c1.slider("Score mínimo de entrada", 60, 95, int(SETTINGS.score_valid), key="bt_threshold")
    exit_mode = c2.selectbox("Salida", ["partial", "tp1", "tp2"], key="bt_exit",
                             format_func={"partial": "50% TP1 + 50% TP2", "tp1": "Todo en TP1 (1:2)",
                                          "tp2": "Todo en TP2 (1:3)"}.get)
    max_hold = c3.number_input("Máx. velas", 10, 500, 100, step=10, key="bt_hold")
    run = c4.button("Ejecutar", width="stretch", key="bt_run")
    key = (req.symbol, req.market, req.timeframe, result.strategy_key, len(result.df))
    if run:
        params = pipeline.risk.params.with_overrides(equity=req.equity, risk_pct=req.risk_pct,
                                                     leverage=req.leverage, sl_method=req.sl_method)
        bt = Backtester(pipeline.risk).run(result.df, BacktestConfig(threshold=threshold, exit_mode=exit_mode,
                                                                     max_hold_bars=int(max_hold), risk=params))
        st.session_state["bt_result"] = (key, bt)
        try:
            pipeline.db.save_backtest({"symbol": req.symbol, "market": req.market, "timeframe": req.timeframe,
                                       "strategy": result.strategy_key, "params": bt.config.__dict__, **bt.metrics})
        except Exception:  # noqa: BLE001 - guardar el resumen es opcional
            pass
    stored = st.session_state.get("bt_result")
    if stored and stored[0] == key:
        ui.render_backtest_result(stored[1])
    else:
        ui.render_empty("Backtest", f"Simula la estrategia {result.strategy_name} sobre las {len(result.df)} velas cargadas "
                                    "(las primeras 200 son de calentamiento). Sube 'Velas a analizar' para más historia.")
    if result.market_data.source == "demo":
        ui.render_banners(["Datos sintéticos: el resultado NO representa rendimiento real."])


def simulator_prefill(result: AnalysisResult | None, req: AnalysisRequest) -> dict | None:
    """Setup de la señal actual para el simulador (solo si hay dirección y plan)."""
    if result is None or not result.ok or result.plan is None or result.signal is None or result.signal.dir_int == 0:
        return None
    plan = result.plan
    return {"coin": req.symbol, "direction": plan.direction, "entry": plan.entry, "sl": plan.stop_loss,
            "tp1": plan.tp1, "tp2": plan.tp2, "capital": req.equity, "leverage": plan.leverage}


def main_panel(req: AnalysisRequest, layers: dict) -> None:
    """Analiza y dibuja todo el panel principal (se re-ejecuta solo en modo auto)."""
    pipeline = get_pipeline()
    force = bool(st.session_state.pop("_force_refresh", False))
    with st.spinner("Analizando el mercado…"):
        result = pipeline.run(req, force_refresh=force)

    ui.render_header(result)
    if not result.ok:
        ui.render_banners([result.error or "Error desconocido"], "err")
        ui.render_empty("No se pudo analizar",
                        "Revisa el símbolo y tu conexión. Para probar sin internet elige el mercado «Demo (sintético)».")
        st.markdown("#### Simulador de operaciones")
        render_simulator(None)  # El simulador no necesita datos de mercado
        ui.render_footer()
        return

    remember_brief(result)
    ui.render_banners(result.warnings)
    squeeze_banner(req)
    if result.is_new_alert:
        st.toast(f"🚨 {result.signal.direction} {req.symbol} · score {result.signal.score:.0f}/100", icon="🚨")

    col_chart, col_card = st.columns([3.1, 1.15], gap="medium")
    with col_chart:
        try:
            fig = ui.build_price_chart(result, layers)
            add_liquidation_zones(fig, req, result.market_data.last_price)
            st.plotly_chart(fig, width="stretch", key="main_chart",
                            config={"displayModeBar": False, "scrollZoom": True})
        except Exception as exc:  # noqa: BLE001 - el gráfico nunca debe tumbar la UI
            ui.render_banners([f"No se pudo dibujar el gráfico: {exc}"], "err")
    with col_card:
        ui.render_signal_card(result)
        ui.render_kpis(result)

    tabs = st.tabs(["Desglose", "Terminal futuros", "Contexto", "TradingView en vivo", "Backtest", "Historial",
                    "Noticias", "Simulador"])
    with tabs[0]:
        ui.render_factors(result)
    with tabs[1]:
        ui.render_trade_terminal(result, on_journal=journal_callback(pipeline, result))
    with tabs[2]:
        ui.render_context(result.context)
    with tabs[3]:
        ui.render_tradingview_widget(req.symbol, req.market, req.timeframe, req.contract, req.exchange)
    with tabs[4]:
        render_backtest_tab(pipeline, result, req)
    with tabs[5]:
        ui.render_history(pipeline.db, pipeline.risk)
    with tabs[6]:
        ui.render_news(pipeline.news, result.risk_check)
    with tabs[7]:
        render_simulator(simulator_prefill(result, req))
    ui.render_footer()


def squeeze_banner(req: AnalysisRequest) -> None:
    """Aviso permanente de SHORT/LONG SQUEEZE (subidas o caídas bruscas que liquidan)."""
    if req.market not in ("crypto", "demo"):
        return
    try:
        svc = get_services()
        snap = svc.loader.load(req.symbol if req.market == "crypto" else "DEMO/USDT", req.market,
                               exchange=req.exchange)
        pos = (svc.recommender.positioning.get(svc.tiers.resolve(req.symbol))
               if req.market == "crypto" and svc.recommender.positioning else None)
        if pos is not None:
            st.caption(f"👥 Long/Short Binance · {pos.text}")
        for side in ("SHORT", "LONG"):
            rep = svc.recommender.squeeze.analyze(snap.df5, side, snap.funding, pos)
            if rep.danger:
                color = "#F85149" if rep.risk == "alto" else "#F2D27A"
                st.markdown(f'<div style="border:2px solid {color};background:rgba(248,81,73,.08);border-radius:10px;'
                            f'padding:.55rem .9rem;margin:.3rem 0;font-weight:600">{ui.esc(rep.text())}</div>',
                            unsafe_allow_html=True)
    except Exception:  # noqa: BLE001 - el aviso es un extra: nunca rompe la gráfica
        pass


def add_liquidation_zones(fig, req: AnalysisRequest, ref_price: float | None = None) -> None:
    """Dibuja en la gráfica las zonas ESTIMADAS de liquidación de cortos (naranja) y largos (azul)."""
    if req.market not in ("crypto", "demo") or not ref_price:
        return
    try:
        from engine.fgview.squeeze import liquidation_zones

        snap = get_services().loader.load(req.symbol if req.market == "crypto" else "DEMO/USDT", req.market,
                                          exchange=req.exchange)
        for side, lev, px in liquidation_zones(snap.df5):
            if abs(px / ref_price - 1) > 0.08:  # Solo las cercanas al precio de la gráfica
                continue
            color = "#FF9F1C" if side == "cortos" else "#4EA8DE"
            fig.add_hline(y=px, row=1, col=1, line=dict(color=color, width=1, dash="dot"), opacity=0.8,
                          annotation_text=f"liq. {side} {lev}x (est.)", annotation_position="top left",
                          annotation_font=dict(size=9, color=color))
    except Exception:  # noqa: BLE001 - es un extra visual
        pass


def guard_positions() -> None:
    """Revisa alertas y el escudo anti-squeeze de tus posiciones (como mucho una vez por minuto)."""
    try:
        for f in get_services().alerts.maybe_check():
            st.toast(f.message, icon="🛡️" if f.kind == "shield" else "🔔")
    except Exception:  # noqa: BLE001
        pass


def chart_strip(req: AnalysisRequest, layers: dict) -> None:
    """
    Gráfica + recomendación de operación fija arriba de TODAS las páginas.
    Usa el mismo análisis (y la misma caché) que la página «Gráfica y señal».
    """
    pipeline = get_pipeline()
    force = bool(st.session_state.pop("_force_refresh", False))
    with st.spinner("Analizando el mercado…"):
        result = pipeline.run(req, force_refresh=force)
    ui.render_header(result)
    if not result.ok:
        ui.render_banners([result.error or "Error desconocido"], "err")
        return
    remember_brief(result)
    ui.render_banners(result.warnings[:1])
    if req.market == "crypto":  # Precarga el historial del Time Machine en segundo plano
        get_services().history.prefetch(req.symbol, "crypto", ("5m", "1m"), 90, req.exchange)
    squeeze_banner(req)
    guard_positions()
    if result.is_new_alert:
        st.toast(f"🚨 {result.signal.direction} {req.symbol} · score {result.signal.score:.0f}/100", icon="🚨")
    col_chart, col_card = st.columns([3.1, 1.15], gap="medium")
    with col_chart:
        try:
            fig = ui.build_price_chart(result, layers, height=500)
            add_liquidation_zones(fig, req, result.market_data.last_price)
            st.plotly_chart(fig, width="stretch", key="top_chart",
                            config={"displayModeBar": False, "scrollZoom": True})
        except Exception as exc:  # noqa: BLE001 - el gráfico nunca debe tumbar la UI
            ui.render_banners([f"No se pudo dibujar el gráfico: {exc}"], "err")
    with col_card:
        ui.render_signal_card(result)
    st.divider()


def _safe(fn, req: AnalysisRequest, layers: dict, auto: bool):
    """Envuelve una página: gráfica fija arriba y cualquier error como aviso (la app nunca se cae)."""

    def run() -> None:
        if fn.__name__ != "analysis":
            try:  # En modo auto la gráfica se refresca cada 60 s sin recargar el módulo de abajo
                st.fragment(chart_strip, run_every=60 if auto else None)(req, layers)
            except Exception as exc:  # noqa: BLE001
                ui.render_banners([f"No se pudo dibujar la gráfica: {exc}"], "err")
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - última red de seguridad
            ui.render_banners([f"Error inesperado: {exc}"], "err")
        ui.render_footer() if fn.__name__ != "analysis" else None

    run.__name__ = fn.__name__
    return run


def build_pages(req: AnalysisRequest, layers: dict, auto: bool) -> dict[str, list]:
    """Menú por secciones. Cada módulo es una página."""
    svc = get_services

    def analysis() -> None:
        # En modo auto el panel se re-ejecuta cada 60 s sin recargar la barra lateral
        st.fragment(main_panel, run_every=60 if auto else None)(req, layers)

    def easy() -> None:
        render_easy(svc(), req)
        render_glossary("easy")

    def fgview() -> None:
        render_fgview(svc(), req)
        render_glossary("fg")

    P = lambda fn, title, icon, path, default=False: st.Page(_safe(fn, req, layers, auto), title=title, icon=icon,  # noqa: E731
                                                              url_path=path, default=default)
    page = lambda render: (lambda: render(svc(), req))  # noqa: E731
    return {
        "Operar": [
            P(analysis, "Gráfica y señal", ":material/candlestick_chart:", "grafica", default=True),
            P(page(render_desk), "FG DESK", ":material/hub:", "desk"),
            P(page(render_grok), "GROK DESK (MCP)", ":material/smart_toy:", "grok"),
            P(page(render_grok_pick), "GROK PICK", ":material/emoji_events:", "grok-pick"),
            P(easy, "Modo fácil", ":material/auto_awesome:", "facil"),
            P(page(ip.render_time_machine), "Time Machine", ":material/history:", "time-machine"),
            P(page(ip.render_swing), "Modo Swing (noche)", ":material/dark_mode:", "swing"),
            P(page(ip.render_entry_timer), "Entry Timer", ":material/timer:", "entry-timer"),
            P(fgview, "FG VIEW pro", ":material/insights:", "fgview"),
            P(page(ip.render_burnout), "Burn Out", ":material/local_fire_department:", "burn-out"),
        ],
        "Escanear": [
            P(page(ip.render_scanner), "Chart Scanner", ":material/radar:", "scanner"),
            P(page(ip.render_news_monitor), "News Monitor", ":material/newspaper:", "news"),
            P(page(ip.render_alerts), "Alertas", ":material/notifications:", "alertas"),
        ],
        "Riesgo y capital": [
            P(page(ip.render_risk_manager), "Risk Manager", ":material/shield:", "riesgo"),
            P(page(ip.render_budget), "Budget Manager", ":material/account_balance_wallet:", "budget"),
            P(page(ip.render_executor), "Order Executor", ":material/send:", "executor"),
            P(lambda: render_journal(svc().journal), "Bitácora", ":material/menu_book:", "bitacora"),
            P(page(ip.render_review), "Revisor de historial", ":material/manage_search:", "revisor"),
        ],
        "Contexto": [
            P(page(ip.render_fundamentals), "Fundamentales cripto", ":material/diamond:", "fundamentales"),
            P(page(ip.render_smart_money), "Smart Money", ":material/monitoring:", "smart-money"),
        ],
        "Herramientas": [
            P(lambda: render_ladder(svc()), "Crecimiento exponencial", ":material/trending_up:", "exponencial"),
            P(lambda: render_simulator(None), "Simulador", ":material/calculate:", "simulador"),
        ],
    }


def nav_counts() -> dict[str, str]:
    """Contadores en vivo para el menú."""
    out: dict[str, str] = {}
    try:
        svc = get_services()
        n_open = len(svc.positions.all())
        if n_open:
            out["executor"] = f"{n_open} abiertas"
        n_alerts = len(svc.alerts.rules())
        if n_alerts:
            out["alertas"] = f"{n_alerts} activas"
        today = svc.journal.today() if svc.journal else []
        if today:
            out["bitacora"] = f"{len(today)} hoy"
        if svc.autopilot.state()["enabled"]:
            out["desk"] = "🤖 piloto ON"
        dec = st.session_state.get("dk_last")
        if dec is not None and "desk" not in out:
            out["desk"] = "✅" if dec.approved else "🎲" if dec.operable else "⛔"
    except Exception:  # noqa: BLE001
        pass
    return out


def main() -> None:
    _apply_query_params()
    req, layers, auto, nav_box = sidebar()
    pages = build_pages(req, layers, auto)
    pg = st.navigation(pages, position="hidden")
    with nav_box:
        sidebar_ui.render_nav(pages, nav_counts(), pg.url_path)
    pg.run()


main()
