"""
app.py
======
Entrada principal de FG ISRAEL VIEW (interfaz Streamlit).

Ejecutar:
    streamlit run app.py

Distribución de la pantalla (rediseño Build 1):
* Barra lateral: menú por secciones (Operar, Escanear, Riesgo y capital, Contexto,
  Herramientas), interruptor 🎓 PRINCIPIANTE GOY / 🕎 EXPERTO RABBI, mercado y capital.
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
from ui import israel_pages as ip  # noqa: E402
from ui.mode import beginner, mode_switch  # noqa: E402

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
def sidebar() -> tuple[AnalysisRequest, dict, bool]:
    """Dibuja los controles y devuelve (petición, capas, auto-análisis)."""
    with st.sidebar:
        st.markdown(
            f'<div class="fs-brand" style="margin-bottom:0.6rem">{ui.logo_img(small=True)}'
            f'<div><div class="fs-logo" style="font-size:1rem">{APP_NAME}</div>'
            f'<div class="fs-credit">{BUILD_TAG}</div></div></div>',
            unsafe_allow_html=True,
        )
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
    return req, layers, auto


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

    ui.render_banners(result.warnings)
    if result.is_new_alert:
        st.toast(f"🚨 {result.signal.direction} {req.symbol} · score {result.signal.score:.0f}/100", icon="🚨")

    col_chart, col_card = st.columns([3.1, 1.15], gap="medium")
    with col_chart:
        try:
            fig = ui.build_price_chart(result, layers)
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
    ui.render_banners(result.warnings[:1])
    if req.market == "crypto":  # Precarga el historial del Time Machine en segundo plano
        get_services().history.prefetch(req.symbol, "crypto", ("5m", "1m"), 90, req.exchange)
    if result.is_new_alert:
        st.toast(f"🚨 {result.signal.direction} {req.symbol} · score {result.signal.score:.0f}/100", icon="🚨")
    col_chart, col_card = st.columns([3.1, 1.15], gap="medium")
    with col_chart:
        try:
            fig = ui.build_price_chart(result, layers, height=500)
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
            P(analysis, "Gráfica y señal", "📊", "grafica", default=True),
            P(easy, "Modo fácil", "✨", "facil"),
            P(page(ip.render_time_machine), "Time Machine", "⏳", "time-machine"),
            P(page(ip.render_entry_timer), "Entry Timer", "⏱️", "entry-timer"),
            P(fgview, "FG VIEW pro", "🟡", "fgview"),
            P(page(ip.render_burnout), "BURN OUT ⛔", "🔥", "burn-out"),
        ],
        "Escanear": [
            P(page(ip.render_scanner), "Chart Scanner", "📡", "scanner"),
            P(page(ip.render_news_monitor), "News Monitor", "📰", "news"),
            P(page(ip.render_alerts), "Alertas", "🔔", "alertas"),
        ],
        "Riesgo y capital": [
            P(page(ip.render_risk_manager), "Risk Manager", "🛡️", "riesgo"),
            P(page(ip.render_budget), "Budget Manager", "💰", "budget"),
            P(page(ip.render_executor), "Order Executor", "🤖", "executor"),
            P(lambda: render_journal(svc().journal), "Bitácora", "📒", "bitacora"),
        ],
        "Contexto": [
            P(page(ip.render_fundamentals), "Fundamentales cripto", "💎", "fundamentales"),
            P(page(ip.render_smart_money), "Smart Money", "🐋", "smart-money"),
        ],
        "Herramientas": [
            P(lambda: render_ladder(svc()), "Crecimiento exponencial", "📈", "exponencial"),
            P(lambda: render_simulator(None), "Simulador", "🧮", "simulador"),
        ],
    }


def main() -> None:
    _apply_query_params()
    req, layers, auto = sidebar()
    st.navigation(build_pages(req, layers, auto), position="sidebar", expanded=True).run()


main()
