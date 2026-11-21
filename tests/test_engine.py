"""
tests/test_engine.py
====================
Pruebas automáticas (sin internet: usan datos DEMO y fuentes simuladas).

Ejecutar:
    pytest -q
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import indicators as ind  # noqa: E402
from engine.backtester import Backtester, BacktestConfig  # noqa: E402
from engine.chart_analyzer import ChartAnalyzer  # noqa: E402
from engine.context_analyzer import ContextAnalyzer, categorize, score_text  # noqa: E402
from engine.data_feeder import (DataFeeder, generate_demo_data, parse_crypto_symbol,  # noqa: E402
                                to_ccxt_symbol, crypto_to_yahoo)
from engine.database import Database  # noqa: E402
from engine.news_filter import NewsFilter  # noqa: E402
from engine.order_executor import execute_order  # noqa: E402
from engine.pipeline import AnalysisPipeline, AnalysisRequest  # noqa: E402
from engine.risk_manager import RiskManager, RiskParams  # noqa: E402
from engine.signal_engine import FACTORS, SignalEngine, grade_for  # noqa: E402
from strategies import STRATEGIES, auto_select  # noqa: E402


def offline(url: str) -> str:
    raise ConnectionError("sin red en tests")


@pytest.fixture(scope="module")
def analyzed() -> pd.DataFrame:
    df, oi = generate_demo_data("1h", 700)
    out = ind.compute_all(df, open_interest=oi)
    out, _ = ChartAnalyzer().analyze(out)
    return out


@pytest.fixture()
def db(tmp_path) -> Database:
    return Database(tmp_path / "test.db")


# ------------------------------------------------------------------ datos
def test_symbol_parsing():
    assert parse_crypto_symbol("btcusdt") == ("BTC", "USDT")
    assert to_ccxt_symbol("BTC/USDT") == "BTC/USDT:USDT"
    assert to_ccxt_symbol("eth", "COIN-M") == "ETH/USD:ETH"
    assert crypto_to_yahoo("SOL/USDT:USDT") == "SOL-USD"


def test_demo_feed_shape():
    data = DataFeeder().get_market_data("DEMO/USDT", "demo", "15m", limit=400)
    assert len(data.df) == 400 and list(data.df.columns) == ["open", "high", "low", "close", "volume"]
    assert data.df.index.tz is not None and data.df.index.is_monotonic_increasing
    assert (data.df["high"] >= data.df[["open", "close"]].max(axis=1)).all()


# ------------------------------------------------------------- indicadores
def test_indicator_ranges(analyzed):
    tail = analyzed.tail(300)
    assert tail["rsi"].between(0, 100).all()
    assert tail["stochrsi_k"].between(0, 100).all()
    assert (tail["atr"] > 0).all()
    assert set(tail["supertrend_dir"].unique()) <= {1.0, -1.0}
    assert (tail["vp_vah"] >= tail["vp_val"]).all()
    assert "oi_change_pct" in analyzed.columns


def test_rsi_matches_wilder_reference():
    close = pd.Series(np.linspace(100, 120, 60) + np.sin(np.arange(60)) * 2)
    r = ind.rsi(close, 14)
    assert r.iloc[:14].isna().all() and 0 <= r.iloc[-1] <= 100


def test_structure_is_causal(analyzed):
    cols = ["structure_trend", "dist_bull_ob", "dist_bear_fvg", "dist_support", "bars_since_break"]
    short, _ = ChartAnalyzer().analyze(analyzed.iloc[:500][["open", "high", "low", "close", "volume", "atr"]])
    for c in cols:
        assert np.allclose(short[c].astype(float), analyzed[c].iloc[:500].astype(float), equal_nan=True), c


# ---------------------------------------------------------------- scoring
@pytest.mark.parametrize("key", list(STRATEGIES))
def test_scores_bounded(analyzed, key):
    scored = SignalEngine().score_frame(analyzed, STRATEGIES[key])
    assert scored["score_long"].between(0, 100).all() and scored["score_short"].between(0, 100).all()
    sig = SignalEngine().evaluate(scored, STRATEGIES[key])
    assert 0 <= sig.score <= 100 and sig.direction in ("LONG", "SHORT", "NEUTRAL")
    assert abs(sum(SignalEngine().normalized_weights(analyzed, STRATEGIES[key]).values()) - 100) < 1e-6


def test_strategy_weights_use_known_factors():
    for s in STRATEGIES.values():
        assert set(s.weights) <= set(FACTORS), s.key


def test_grades():
    assert grade_for(50)[1] == "none" and grade_for(75)[1] == "valid" and grade_for(90)[1] == "alert"


def test_gate_caps_score(analyzed):
    strat = STRATEGIES["mean_reversion"]
    scored = SignalEngine().score_frame(analyzed, strat)
    capped = scored.loc[~scored["gate_long"], "final_long"]
    assert (capped < 70).all()


def test_auto_select(analyzed):
    strat, why = auto_select(analyzed, "1m")
    assert strat.key == "scalping_futures" and why


# ------------------------------------------------------------------ riesgo
def test_risk_plan_math():
    rm = RiskManager(RiskParams(equity=10_000, risk_pct=1, leverage=10, atr_mult=1.5, sl_method="atr"))
    plan = rm.build_plan(1, entry=100.0, atr=2.0, symbol="BTC/USDT", market="crypto")
    assert plan.stop_loss == pytest.approx(97.0)
    assert plan.tp1 == pytest.approx(106.0) and plan.tp2 == pytest.approx(109.0)
    assert plan.risk_amount == pytest.approx(100.0)  # 1 % de 10.000
    assert plan.position_size == pytest.approx(100 / 3, rel=1e-4)
    short = rm.build_plan(-1, entry=100.0, atr=2.0, symbol="BTC/USDT", market="crypto")
    assert short.stop_loss == pytest.approx(103.0) and short.tp1 == pytest.approx(94.0)


def test_stop_below_order_block():
    rm = RiskManager(RiskParams(sl_method="auto", atr_mult=1.5))
    stop, method = rm.stop_loss(1, entry=100.0, atr=2.0, ob_level=98.0)
    assert method == "Order Block" and stop == pytest.approx(97.8)
    stop, method = rm.stop_loss(1, entry=100.0, atr=2.0, ob_level=80.0)  # demasiado lejos -> ATR
    assert method.startswith("ATR")


def test_cme_contract_sizing():
    rm = RiskManager(RiskParams(equity=50_000, risk_pct=1))
    plan = rm.build_plan(1, entry=5000.0, atr=10.0, symbol="ES=F", market="cme")
    assert plan.size_unit == "contratos" and plan.position_size == 500 // (15 * 50)


def test_kill_switch(db):
    rm = RiskManager(db=db, kill_switch_losses=3)
    for i in range(3):
        sid = db.save_signal({"bar_time": f"2026-01-0{i + 1}T00:00:00+00:00", "symbol": "BTC/USDT",
                              "timeframe": "1h", "strategy": "t", "direction": "LONG", "entry": 100,
                              "stop_loss": 99, "tp1": 102, "tp2": 103})
        db.close_signal(sid, "LOSS", 99, -1.0)
    active, streak = rm.kill_switch_status()
    assert active and streak == 3
    assert not rm.pre_trade_check().allowed
    rm.reset_kill_switch()
    assert rm.kill_switch_status() == (False, 0)


def test_signal_tracking_resolves_loss(db):
    rm = RiskManager(db=db)
    idx = pd.date_range("2026-01-01", periods=5, freq="h", tz="UTC")
    df = pd.DataFrame({"open": 100, "high": [100, 101, 100, 99, 100], "low": [99.5, 99.5, 98.5, 98, 99],
                       "close": 100, "volume": 1}, index=idx)
    db.save_signal({"bar_time": idx[0].isoformat(), "symbol": "X", "timeframe": "1h", "strategy": "t",
                    "direction": "LONG", "entry": 100, "stop_loss": 99, "tp1": 102, "tp2": 103})
    closed = rm.update_open_signals("X", "1h", df)
    assert closed and closed[0]["status"] == "LOSS"


def test_news_filter_blocks(tmp_path):
    cal = tmp_path / "cal.json"
    when = datetime.now(timezone.utc) + timedelta(minutes=10)
    cal.write_text(json.dumps({"events": [{"datetime_utc": when.isoformat(), "title": "CPI", "currency": "USD",
                                           "impact": "High"}]}))
    nf = NewsFilter(enabled=True, window_minutes=30, currencies=["USD"], api_url="", manual_path=cal,
                    cache_path=tmp_path / "cache.json")
    status = nf.check()
    assert status.blocked and "CPI" in status.message
    assert not nf.check(now=when + timedelta(minutes=45)).blocked


# ---------------------------------------------------------------- contexto
def test_sentiment_lexicon():
    assert score_text("Bitcoin surges to record high on ETF inflows") > 0.5
    assert score_text("Bitcoin se desploma tras un hackeo") < -0.5
    assert score_text("Software update released") == 0.0  # "war" dentro de "software" no cuenta
    assert "political" in categorize("New tariffs and sanctions announced")


def test_context_offline_is_neutral():
    report = ContextAnalyzer(fetch_text=offline, use_yfinance=False).analyze("BTC/USDT", "crypto")
    assert report.bias == 0 and report.adjustment_for(1)[0] == 0


# ---------------------------------------------------------------- pipeline
def test_pipeline_demo_end_to_end(tmp_path):
    p = AnalysisPipeline(db=Database(tmp_path / "p.db"),
                         context=ContextAnalyzer(fetch_text=offline, use_yfinance=False),
                         news=NewsFilter(enabled=False))
    res = p.run(AnalysisRequest(symbol="DEMO/USDT", market="demo", timeframe="1h", strategy="auto"))
    assert res.ok and res.signal is not None and res.df is not None
    json.dumps(res.to_dict(), default=str)


def test_backtester_runs(analyzed):
    scored = SignalEngine().score_frame(analyzed, STRATEGIES["trend_following"])
    bt = Backtester().run(scored, BacktestConfig(threshold=70))
    assert bt.metrics["trades"] >= 0 and "win_rate" in bt.metrics


def test_execute_order_is_disabled():
    assert execute_order("BTC/USDT:USDT", "buy", 0.001)["status"] == "disabled"


def test_api_health_and_demo():
    from fastapi.testclient import TestClient

    import api

    api._pipeline = AnalysisPipeline(context=ContextAnalyzer(fetch_text=offline, use_yfinance=False),
                                     news=NewsFilter(enabled=False))
    client = TestClient(api.app)
    assert client.get("/health").json()["status"] == "ok"
    r = client.get("/analyze", params={"symbol": "DEMO/USDT", "market": "demo", "timeframe": "4h"})
    assert r.status_code == 200 and r.json()["signal"]["direction"] in ("LONG", "SHORT", "NEUTRAL")


# ------------------------------------------------- perfiles y microestructura
def test_capital_profiles():
    from engine.risk_manager import get_profile

    assert get_profile(300).key == "bajo"
    assert get_profile(2_000).key == "medio"
    assert get_profile(50_000).key == "alto"


def test_leverage_capped_by_profile_and_liquidation():
    rm = RiskManager(RiskParams(equity=300, risk_pct=2.0, leverage=20, atr_mult=1.5, sl_method="atr"))
    plan = rm.build_plan(1, entry=100.0, atr=2.0, symbol="BTC/USDT", market="crypto")
    assert plan.requested_leverage == 20 and plan.leverage <= 3.0
    assert any("Apalancamiento ajustado" in w for w in plan.warnings)
    assert plan.liquidation_price < plan.stop_loss  # la liquidación queda DETRÁS del SL


def test_orderbook_adjustment():
    from engine.pipeline import orderbook_adjustment

    ob = {"imbalance": 0.5, "spread_pct": 0.02, "wall": {"side": "bid", "ratio": 9}}
    up, note = orderbook_adjustment(ob, 1)
    down, _ = orderbook_adjustment(ob, -1)
    assert up > 0 > down and "Libro" in note
    assert orderbook_adjustment(None, 1) == (0.0, "")


def test_demo_feed_has_order_book():
    data = DataFeeder().get_market_data("DEMO/USDT", "demo", "1h", limit=300)
    assert data.order_book is not None and -1 <= data.order_book["imbalance"] <= 1


# ---------------------------------------------------------------- simulador
def test_simulator_matches_hand_calculation():
    from engine.position_simulator import SimInput, TakeProfit, simulate

    r = simulate(SimInput("BTC", "LONG", 1000, 10, 100000, 98000, [TakeProfit(105000)], margin=100))
    assert r.notional == pytest.approx(1000) and r.liquidation == pytest.approx(90500)
    assert r.fees == pytest.approx(1.0)
    assert r.tp_total.pnl_net == pytest.approx(49.0) and r.tp_total.roi_pct == pytest.approx(49.0)
    assert r.sl.pnl_net == pytest.approx(-21.0) and r.sl.account_pct == pytest.approx(-2.1)
    assert r.rr == pytest.approx(49 / 21) and r.min_winrate_pct == pytest.approx(30.0)
    assert any("Riesgo alto" in a for a in r.alerts)


def test_simulator_short_risk_mode_and_partials():
    from engine.position_simulator import SimInput, TakeProfit, simulate

    r = simulate(SimInput("ETH", "SHORT", 2000, 5, 3000, 3060, [TakeProfit(2910, 50), TakeProfit(2850, 50)],
                          risk_pct=1.0))
    assert r.notional == pytest.approx(2000 * 0.01 / 0.02)  # (capital × riesgo) ÷ distancia al SL
    assert r.margin == pytest.approx(r.notional / 5)
    assert r.liquidation == pytest.approx(3000 * (1 + 1 / 5 - 0.005))
    assert r.tp_total.pnl_net > 0 > r.sl.pnl_net and len(r.tps) == 2


def test_simulator_liquidation_before_sl_alert():
    from engine.position_simulator import SimInput, TakeProfit, simulate

    r = simulate(SimInput("PEPE", "LONG", 500, 50, 0.00001, 0.0000095, [TakeProfit(0.000011)], margin=20))
    assert r.sl_liquidated and r.sl.pnl_net == pytest.approx(-20.0)
    assert any("liquidan ANTES" in a for a in r.alerts) and any("memecoin" in a for a in r.alerts)


def test_simulator_rejects_bad_levels():
    from engine.position_simulator import SimInput, TakeProfit, simulate

    with pytest.raises(ValueError):
        simulate(SimInput("BTC", "LONG", 1000, 10, 100000, 101000, [TakeProfit(105000)], margin=100))


def test_projection_scenarios_ordered():
    from engine.position_simulator import project

    pr = project(1000, 50, 2.0, 1.0, 100)
    assert pr.bad < pr.expected < pr.good and pr.ev_per_trade_pct == pytest.approx(0.5)


def test_api_simulate():
    from fastapi.testclient import TestClient

    import api

    r = TestClient(api.app).get("/simulate", params={"coin": "BTC", "capital": 1000, "leverage": 10,
                                                      "entry": 100000, "sl": 98000, "tp": "105000", "margin": 100})
    assert r.status_code == 200 and r.json()["tp"]["pnl_net"] == pytest.approx(49.0)


# ===================================================== FG VIEW (paquete POO engine/fgview)
@pytest.fixture(scope="module")
def fg():
    from engine.fgview import FGServices

    return FGServices.create(DataFeeder())


def _bars(prices, spread=0.001):
    idx = pd.date_range("2026-01-01", periods=len(prices), freq="5min", tz="UTC")
    c = np.asarray(prices, float)
    return c * (1 + spread), c * (1 - spread), c, idx


def test_fgview_budget_and_tiers(fg):
    assert fg.planner.budget.rule(80) == (2.0, 30.0) and fg.planner.budget.rule(1500) == (1.0, 20.0)
    assert fg.planner.budget.rule(50_000) == (0.5, 10.0)
    assert fg.tiers.for_coin("BTC/USDT")[0].key == "A" and fg.tiers.for_coin("SOL")[0].key == "B"
    assert fg.tiers.for_coin("PEPE")[0].key == "C"
    tier, listed = fg.tiers.for_coin("XYZ")
    assert tier.key == "C" and not listed and fg.tiers.resolve("DEMO/USDT") == "BTC"


def test_fgview_plan_matches_hand_calculation(fg):
    p = fg.planner.plan(1000, "BTC", "LONG", 100000)
    assert p.risk_pct == 1.0 and p.sl_pct == pytest.approx(0.35)
    assert p.notional == pytest.approx(10 / 0.0035)  # (capital × riesgo) ÷ distancia al SL
    assert p.leverage == 15 and p.margin == pytest.approx(p.notional / 15) and p.margin <= 200
    assert [round(tp[2]) for tp in p.tps] == [50, 30, 20]
    assert p.rr_weighted == pytest.approx((0.40 * 0.5 + 0.70 * 0.3 + 1.00 * 0.2) / 0.35)
    assert p.rr_net < p.rr_weighted and any("LÍMITE" in n for n in p.notes)


def test_fgview_liquidation_three_times_sl(fg):
    for coin, entry, atr in (("BTC", 100000, 900), ("SOL", 150, 3.0), ("PEPE", 0.00001, 0.0000004)):
        p = fg.planner.plan(1000, coin, "SHORT", entry, atr5=atr)
        assert abs(p.liquidation - p.entry) / p.entry >= 3 * p.sl_pct / 100 - 1e-9, coin
        assert p.leverage <= p.tier.lev_max


def test_fgview_position_cut_and_tp_scaling(fg):
    p = fg.planner.plan(60, "PEPE", "LONG", 0.00001)  # Nivel C máx. 5x, margen máx. 30 % = $18
    assert p.margin <= 18 + 1e-6 and any("recorta" in n for n in p.notes)
    base = fg.planner.plan(1000, "BTC", "LONG", 100000)
    wide = fg.planner.plan(1000, "BTC", "LONG", 100000, atr5=1000)
    assert wide.sl_source == "ATR" and wide.rr_weighted == pytest.approx(base.rr_weighted)


def test_plan_rebase_keeps_distances(fg):
    p = fg.planner.plan(1000, "SOL", "LONG", 150)
    q = p.rebase(300)
    assert q.entry == 300 and q.stop_loss / 300 == pytest.approx(p.stop_loss / 150)
    assert q.tps[2][0] / 300 == pytest.approx(p.tps[2][0] / 150) and q.tp_pnl == p.tp_pnl


def test_trade_manager_full_ladder_and_stop(fg):
    p = fg.planner.plan(1000, "BTC", "LONG", 100.0)
    h, lo, c, _ = _bars([100, 100.5, 101, 101.5, 102, 102, 102])  # sube y toca TP1, TP2 y TP3
    out = fg.manager.run(p, h, lo, c, 0)
    assert out.tps_hit == 3 and out.pnl == pytest.approx(sum(p.tp_pnl))
    h, lo, c, _ = _bars([100, 99.5, 99, 98, 98, 98, 98])  # cae directo al SL
    out = fg.manager.run(p, h, lo, c, 0)
    assert out.tps_hit == 0 and out.reason == "Stop Loss" and out.pnl == pytest.approx(p.sl_pnl)
    h, lo, c, _ = _bars([100, 100.45, 99.5, 99.0, 99, 99, 99])  # TP1 y vuelta: resto en break-even
    out = fg.manager.run(p, h, lo, c, 0)
    assert out.tps_hit == 1 and out.pnl == pytest.approx(p.tp_pnl[0])


def test_single_source_filters_live_equals_history(fg):
    """El filtro en vivo y la matriz histórica deben coincidir en la última vela (una sola lógica)."""
    snap = fg.loader.load("DEMO/USDT", "demo", bars5=400)
    for direction in ("LONG", "SHORT"):
        live = [c.passed for c in fg.filters.evaluate(snap, direction, "BTC").checks]
        hist = fg.filters.matrix(snap.df5, snap.df1h, direction)[-1].tolist()
        # El de tendencia 1h y el de funding usan datos "en vivo" (última vela de 1h / funding actual)
        assert live[1:5] == hist[1:5]


def test_recommendation_and_daily_guard(fg):
    from engine.fgview.journal import DailyGuard, TradeStats

    r = fg.recommender.recommend("DEMO/USDT", "demo", 1000)
    assert r.light in ("verde", "amarillo", "rojo") and len(r.report.checks) == 6 and 0 <= r.report.score <= 100
    assert r.stats.samples > 0 and len(r.stats.reach) == 3 and "FG VIEW" in fg.recommender.format(r)
    assert DailyGuard().check([{"pnl": -1}] * 3, 1000) is not None and DailyGuard().check([{"pnl": 5}], 1000) is None
    m = TradeStats.summary([{"pnl": 10}, {"pnl": -5}, {"pnl": 8}, {"pnl": 6}, {"pnl": -4}])
    assert m["trades"] == 5 and m["win_rate"] == pytest.approx(60.0) and m["profit_factor"] == pytest.approx(24 / 9)


def test_spread_block(fg):
    from engine.fgview.snapshot import MarketSnapshot

    snap = fg.loader.load("DEMO/USDT", "demo", bars5=400)
    wide = MarketSnapshot(snap.symbol, snap.df5, snap.df1h, 0.0001, {"spread_pct": 0.2}, "demo")
    rep = fg.filters.best(wide, "BTC")
    assert not rep.enter and any("Spread" in b for b in rep.blocks)


def test_easy_scan_parallel_and_roi(fg):
    cands, errors = fg.advisor.scan(500, "demo", ["BTC", "SOL", "DOGE"])
    assert len(cands) == 3 and not errors
    s = fg.advisor.summary(cands[0])
    assert s["headline"] and len(s["steps"]) == 7 and "Stop Loss" in s["loss"]
    roi = fg.advisor.roi_options(cands[0])
    assert {"ROI objetivo", "Apalanc.", "Movimiento necesario"} <= set(roi.columns) and len(roi) >= 3


def test_fg_backtester_runs(fg):
    v = fg.backtester.run_symbol(fg.loader, "SOL", "demo", 3)
    assert v.bars > 0 and "trades" in v.metrics
    if v.metrics["trades"]:
        assert set(v.trades["passes"]) <= {4, 5, 6} and not v.by_confidence.empty


def test_journal_db(db):
    from engine.fgview.journal import TradeJournal

    j = TradeJournal(db)
    j.add({"coin": "BTC", "direction": "LONG", "entry": 100, "exit_price": 101, "pnl": 3.2, "filters": {"VWAP": True}})
    assert j.entries()[0]["result"] == "WIN" and len(j.today()) == 1 and j.summary()["trades"] == 1


def test_ladder_never_liquidates_and_beats_classic(fg):
    from engine.fgview.ladder import LadderConfig

    r = fg.ladder.plan(LadderConfig(capital=20, target=200, coin="SOL", win_rate=70))
    assert r.liq_pct >= 3 * r.sl_pct and -0.15 < r.roi_loss < 0
    assert r.sim_protected["ruin_pct"] < r.sim_classic["ruin_pct"]
    assert r.sim_protected["success_pct"] > r.sim_classic["success_pct"]
    assert r.steps[-1].total_after_win >= 200
    weak = fg.ladder.plan(LadderConfig(capital=20, target=100, coin="BTC", win_rate=35))
    assert weak.notes and "ventaja" in weak.notes[0]


def test_glossary():
    from engine.fgview.glossary import GLOSSARY

    assert "Liquidación" in GLOSSARY and "Apalancamiento (x)" in GLOSSARY


# -----------------------------------------------------------------------------
# Núcleo C++ (fgcore): mismos resultados que Python
# -----------------------------------------------------------------------------
def test_native_status_text():
    from engine.fgview import native

    assert native.status().startswith("C++ activo") == native.AVAILABLE


def test_native_trade_manager_parity(fg):
    from engine.fgview import native
    from engine.fgview.manager import TradeManager

    if not native.AVAILABLE:
        pytest.skip("fgcore no compilado")
    snap = fg.loader.load("DEMO/USDT", "demo", bars5=600)
    df5 = snap.df5
    h, lo, c = (df5[k].to_numpy(float) for k in ("high", "low", "close"))
    starts = list(range(len(c) - 1))
    py_tm, cpp_tm = TradeManager(fg.config, use_native=False), TradeManager(fg.config, use_native=True)
    assert py_tm.backend == "Python" and cpp_tm.backend == "C++"
    for direction in ("LONG", "SHORT"):
        p = fg.planner.plan(1000, "BTC", direction, float(c[-1]))
        a, b = py_tm.evaluate_many(p, h, lo, c, starts), cpp_tm.evaluate_many(p, h, lo, c, starts)
        assert np.mean(a.reason == b.reason) > 0.99 and np.mean(a.tps_hit == b.tps_hit) > 0.99
        assert a.pnl.mean() == pytest.approx(b.pnl.mean(), rel=0.02, abs=0.05)
        ra = py_tm.reach_probability(p.side, h, lo, c, 0.4, 0.3)
        rb = cpp_tm.reach_probability(p.side, h, lo, c, 0.4, 0.3)
        assert ra[2] == rb[2] and ra[0] == pytest.approx(rb[0], abs=0.5)


def test_native_ladder_parity(fg):
    from engine.fgview import native
    from engine.fgview.ladder import LadderConfig, LadderPlanner

    if not native.AVAILABLE:
        pytest.skip("fgcore no compilado")
    lc = LadderConfig(capital=20, target=200, coin="SOL", win_rate=65)
    py_l = LadderPlanner(fg.planner, fg.config, paths=800, use_native=False)
    cpp_l = LadderPlanner(fg.planner, fg.config, paths=800, use_native=True)
    for protected in (True, False):
        a, b = py_l.simulate(lc, 0.4, -0.3, protected), cpp_l.simulate(lc, 0.4, -0.3, protected)
        for k in a:
            assert a[k] == pytest.approx(b[k], nan_ok=True)


# -----------------------------------------------------------------------------
# FG ISRAEL VIEW Build 1: módulos 7–16 + BURN OUT
# -----------------------------------------------------------------------------
@pytest.fixture()
def fgdb(tmp_path):
    from engine.database import Database
    from engine.fgview import FGServices

    return FGServices.create(DataFeeder(), db=Database(tmp_path / "t.db"))


def test_time_machine_parity_and_probabilities(fg):
    from engine.fgview import native
    from engine.fgview.timemachine import TimeMachine

    df, _ = fg.history.get("DEMO/USDT", "demo", "5m", 30)
    p = fg.planner.plan(1000, "BTC", "LONG", float(df["close"].iloc[-1]))
    py = TimeMachine(sims=5000, use_native=False).run(p, df, 5)
    total = py.p_sl + py.p_time + py.p_tp1
    assert total == pytest.approx(100, abs=0.01) and py.p_tp1 >= py.p_tp2 >= py.p_tp3
    assert py.verdict in ("OPERAR", "ESPERAR", "NO OPERAR") and "TIME MACHINE" in py.text()
    if native.AVAILABLE:
        cpp = TimeMachine(sims=5000, use_native=True).run(p, df, 5)
        assert (cpp.p_tp1, cpp.p_sl, cpp.p_liq) == (py.p_tp1, py.p_sl, py.p_liq)
        assert cpp.ev == pytest.approx(py.ev) and cpp.var95 == pytest.approx(py.var95)


def test_burnout_rules(fgdb):
    from datetime import datetime, timedelta, timezone

    bo = fgdb.burnout
    with pytest.raises(ValueError, match="PROHIBIDO"):
        bo.build_plan("DOGE/USDT", "LONG", 0.1, None, 10)
    assert bo.max_bag(1000) == 50 and bo.max_bag(100) == 10
    assert bo.fund(60, 1000) is not None  # Más del 5%
    assert bo.fund(20, 1000) is None and bo.fund(5, 1000) is not None  # Recarga el mismo día: prohibido
    p = bo.build_plan("BTC/USDT", "LONG", 100_000, 120, 20)
    assert 50 <= p.leverage <= 100 and 0.15 <= p.sl_pct <= 0.25
    assert abs(p.entry - p.liquidation) >= 2 * abs(p.entry - p.stop_loss)  # SL antes de la liquidación
    b = fgdb.burnout.build_plan("SOL/USDT", "SHORT", 150, None, 20)
    assert 25 <= b.leverage <= 50
    hard, warn = bo.gate(70, None)  # Señal < 80 y sin Time Machine -> NO bloquea, solo avisa
    assert not hard and any("Puntuación" in w for w in warn)
    assert not bo.confirmed("confirmo ya") and bo.confirmed(" confirmo ")
    now = datetime.now(timezone.utc)
    bo.register_result(-5, "BTC", now)
    bo.register_result(-5, "BTC", now)
    hard, warn = bo.gate(90, None, now=now)
    assert not hard and any("intentos" in w for w in warn)  # Más de 2 intentos: aviso, no bloqueo
    capped = bo.build_plan("BTC/USDT", "LONG", 100_000, 120, 20, lev_cap=20)
    assert capped.leverage == 20  # Anti-squeeze baja el apalancamiento
    bo.register_result(-50, "BTC", now)  # La bolsa llega a $0 -> bloqueo 7 días
    assert bo.gate(90, None, now=now)[0]
    assert bo.bag().balance == 0 and bo.locked(now) and not bo.locked(now + timedelta(days=8))


def test_entry_timer_rules():
    from datetime import datetime, timezone

    from engine.fgview.timer import EntryTimer

    t = EntryTimer()
    s = t.check(datetime(2026, 1, 6, 14, 0, 10, tzinfo=timezone.utc))  # 09:00 NY, recién cerrada la vela
    assert s.in_entry_window and s.session == "Apertura de Nueva York" and s.can_enter
    assert not t.check(datetime(2026, 1, 6, 14, 2, 30, tzinfo=timezone.utc)).can_enter  # Mitad de vela
    assert not t.check(datetime(2026, 1, 6, 7, 55, 5, tzinfo=timezone.utc)).can_enter  # 5 min antes del funding
    c = t.check(datetime(2026, 1, 6, 14, 0, 10, tzinfo=timezone.utc), ideal_entry=100, price=100.5, atr=1)
    assert c.cancelled and not c.can_enter


def test_risk_manager_limits(fgdb):
    rm, book = fgdb.risk, fgdb.positions
    book.add({"coin": "BTC", "direction": "LONG", "entry": 100, "stop_loss": 99, "liquidation": 90})
    r = rm.check("ETH", "LONG", 100, 99, 90, 1000, [])
    assert not r.allowed and any("correlacionadas" in b for b in r.blocks)
    assert rm.check("ETH", "SHORT", 100, 99, 90, 1000, []).allowed
    assert not rm.check("SOL", "SHORT", 100, 99, 98, 1000, []).allowed  # Liquidación < 3× SL
    book.add({"coin": "SOL", "direction": "SHORT", "entry": 100, "stop_loss": 101, "liquidation": 110})
    assert any("máximo 2" in b for b in rm.check("XRP", "SHORT", 100, 101, 110, 1000, []).blocks)


def test_budget_manager_drawdown_and_withdrawal(fgdb):
    bm = fgdb.budget
    bm.set_start(1000)
    dd = bm.status([{"pnl": -60}, {"pnl": -50}])
    assert dd.defensive and dd.risk_pct == dd.base_risk_pct / 2
    rec = bm.status([{"pnl": -110}, {"pnl": 60}])  # +6.7% desde el mínimo -> sale del modo defensivo
    assert not rec.defensive
    up = bm.status([{"pnl": 300}])
    assert up.withdraw_suggestion == pytest.approx(60)  # 20% de 300 de ganancia


def test_executor_paper_flow_and_burn_hook(fgdb):
    import pandas as pd

    fgdb.burnout.fund(20, 1000)
    plan = fgdb.burnout.build_plan("BTC/USDT", "LONG", 100.0, None, 20)
    t = fgdb.executor.build_ticket(plan, "DEMO/USDT", "demo", strategy="BURN OUT", score=90)
    assert fgdb.executor.submit(t, confirmed=False)["status"] == "pending"
    assert fgdb.executor.submit(t, confirmed=True, mode="live")["status"] == "rejected"  # Real desactivado
    pos = fgdb.executor.submit(t, confirmed=True)["position"]
    idx = pd.date_range(pd.Timestamp(pos["opened_at"]).floor("min") + pd.Timedelta(minutes=1), periods=3, freq="1min")
    bars = pd.DataFrame({"open": 100.0, "high": [100.1, 100.0, 100.0], "low": [99.0, 99.0, 99.0],
                         "close": 99.5}, index=idx)
    res = fgdb.executor.paper.evaluate(pos, bars)
    assert res["reason"] == "Stop Loss" and res["pnl"] == pytest.approx(plan.sl_pnl)
    fgdb.executor.close(pos["id"], res["exit_price"], res["pnl"], res["reason"])
    assert fgdb.burnout.bag().balance == pytest.approx(20 + plan.sl_pnl) and not fgdb.positions.all()
    assert fgdb.journal.entries()[-1]["pnl"] == pytest.approx(plan.sl_pnl)


def test_scanner_and_news_classification(fg):
    from engine.fgview.newsmonitor import NewsMonitor

    top, hits = fg.scanner.scan(market="demo")
    assert hits and 0 <= hits[0].score <= 100
    assert NewsMonitor.classify("Major exchange hack: $200M stolen")[0] == "alto"
    assert NewsMonitor.classify("Token unlock next week")[0] == "medio"


def test_price_alerts_sharp_move_and_liq(fgdb, monkeypatch):
    import pandas as pd

    from engine.data_feeder import MarketData

    idx = pd.date_range("2026-01-01", periods=10, freq="1min", tz="UTC")
    df = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "volume": 1.0,
                       "close": [100] * 5 + [100, 101, 102, 103, 104]}, index=idx)
    md = MarketData(df=df, symbol="DEMO/USDT", resolved_symbol="DEMO/USDT", market="demo", timeframe="1m",
                    source="test", is_futures=True)
    monkeypatch.setattr(fgdb.alerts.feeder, "get_market_data", lambda *a, **k: md)
    fgdb.alerts.add("DEMO", "sharp_move", market="demo")
    fgdb.positions.add({"coin": "DEMO", "market": "demo", "direction": "LONG", "entry": 106, "stop_loss": 105,
                        "liquidation": 103.5})
    kinds = {f.kind for f in fgdb.alerts.check(send=False)}
    assert {"sharp_move", "liq_near"} <= kinds
    assert not fgdb.alerts.check(send=False)  # Enfriamiento: no repite enseguida


def test_squeeze_guard_detects_short_squeeze(fg):
    """Caso real: SOL cae a 64.83 y rebota a 68.49 en ~40 min (liquidó un SHORT 55x)."""
    import pandas as pd

    from engine.indicators import compute_all

    base = [70 - 0.02 * i for i in range(250)]  # Tendencia bajista suave
    dump = [65.8 - 0.1 * i for i in range(10)]  # Caída hasta ~64.9
    pump = [64.83 + 0.45 * i for i in range(1, 9)]  # Rebote violento hasta ~68.4
    closes = base + dump + pump
    idx = pd.date_range("2026-11-20", periods=len(closes), freq="5min", tz="UTC")
    c = pd.Series(closes, index=idx)
    vol = [100.0] * (len(closes) - 8) + [400.0] * 8
    df = pd.DataFrame({"open": c.shift(1).fillna(c.iloc[0]), "high": c * 1.002, "low": c * 0.998, "close": c,
                       "volume": vol}, index=idx)
    rep = fg.recommender.squeeze.analyze(compute_all(df), "SHORT", funding=-0.0002)
    assert rep.risk == "alto" and rep.direction == "SHORT"
    assert rep.lev_cap < 55  # A 55x la liquidación (~1.3%) estaba más cerca que el squeeze
    assert fg.recommender.squeeze.analyze(compute_all(df), "LONG").risk == "bajo"
    assert fg.recommender.squeeze.lev_cap_for(5.6) <= 14


def test_burnout_decide_gives_trade_even_if_not_recommended(fgdb):
    bo = fgdb.burnout
    bo.fund(20, 1000)
    plan = bo.build_plan("BTC/USDT", "SHORT", 100_000, None, 20)
    dec = bo.decide(plan, 40, None, ["Noticia de alto impacto"], squeeze_text="Riesgo ALTO de SHORT SQUEEZE")
    assert dec.active and not dec.recommended and len(dec.warnings) >= 3 and "BURN OUT" in dec.warning
