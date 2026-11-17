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


# ------------------------------------------------- FG VIEW · recomendador (Build 3)
def test_fgview_budget_and_tiers():
    from engine.recommender import budget_rule, tier_for

    assert budget_rule(80) == (2.0, 30.0) and budget_rule(1500) == (1.0, 20.0) and budget_rule(50_000) == (0.5, 10.0)
    assert tier_for("BTC/USDT")[0].key == "A" and tier_for("SOL")[0].key == "B" and tier_for("PEPE")[0].key == "C"
    tier, listed = tier_for("XYZ")
    assert tier.key == "C" and not listed


def test_fgview_plan_matches_hand_calculation():
    from engine.recommender import build_plan

    p = build_plan(1000, "BTC", "LONG", 100000)
    assert p.risk_pct == 1.0 and p.sl_pct == pytest.approx(0.35)
    assert p.notional == pytest.approx(10 / 0.0035)  # (capital × riesgo) ÷ distancia al SL
    assert p.leverage == 15 and p.margin == pytest.approx(p.notional / 15)  # margen ≤ 20 % del capital
    assert p.margin <= 200 and [round(tp[2]) for tp in p.tps] == [50, 30, 20]
    assert p.rr_weighted == pytest.approx((0.40 * 0.5 + 0.70 * 0.3 + 1.00 * 0.2) / 0.35)
    assert p.rr_net < p.rr_weighted and any("LÍMITE" in n for n in p.notes)  # comisiones visibles


def test_fgview_liquidation_three_times_sl():
    from engine.recommender import build_plan

    for coin, atr in (("BTC", 900), ("SOL", 3.0), ("PEPE", 0.0000004)):
        entry = {"BTC": 100000, "SOL": 150, "PEPE": 0.00001}[coin]
        p = build_plan(1000, coin, "SHORT", entry, atr5=atr)
        liq_dist = abs(p.liquidation - p.entry) / p.entry
        assert liq_dist >= 3 * p.sl_pct / 100 - 1e-9, coin
        assert p.tier.lev_min >= 1 and p.leverage <= p.tier.lev_max


def test_fgview_position_cut_when_margin_cap_exceeded():
    from engine.recommender import build_plan

    p = build_plan(60, "PEPE", "LONG", 0.00001)  # Nivel C máx. 5x, margen máx. 30 % = $18
    assert p.margin <= 18 + 1e-6 and any("recorta" in n for n in p.notes)


def test_fgview_filters_and_daily_guard():
    from engine.recommender import best_direction, daily_guard, journal_metrics

    df5, oi = generate_demo_data("5m", 300)
    df5 = ind.compute_all(df5, open_interest=oi)
    df1h, _ = generate_demo_data("1h", 200)
    df1h = ind.compute_all(df1h)
    rep = best_direction(df5, df1h, "BTC", funding=0.0001, order_book={"spread_pct": 0.01})
    assert len(rep.checks) == 6 and 0 <= rep.score <= 100 and rep.confidence in ("Alta", "Media", "Baja")
    blocked = best_direction(df5, df1h, "BTC", funding=0.0001, order_book={"spread_pct": 0.2})
    assert not blocked.enter and any("Spread" in b for b in blocked.blocks)
    assert daily_guard([{"pnl": -1}, {"pnl": -1}, {"pnl": -1}], 1000) is not None
    assert daily_guard([{"pnl": -1}, {"pnl": 5}], 1000) is None
    m = journal_metrics([{"pnl": 10}, {"pnl": -5}, {"pnl": 8}, {"pnl": 6}, {"pnl": -4}])
    assert m["trades"] == 5 and m["win_rate"] == pytest.approx(60.0) and m["profit_factor"] == pytest.approx(24 / 9)


def test_fgview_journal_db(db):
    db.add_journal({"coin": "BTC", "direction": "LONG", "entry": 100, "exit_price": 101, "pnl": 3.2,
                    "filters": {"VWAP": True}})
    rows = db.journal()
    assert rows[0]["result"] == "WIN" and rows[0]["filters"] == {"VWAP": True} and len(db.journal_today()) == 1


def test_fgview_tps_scale_with_atr_sl():
    from engine.recommender import build_plan

    base = build_plan(1000, "BTC", "LONG", 100000)  # SL de tabla 0.35 %
    wide = build_plan(1000, "BTC", "LONG", 100000, atr5=1000)  # SL por ATR 1.2 %
    assert wide.sl_pct > base.sl_pct and wide.sl_source == "ATR"
    assert wide.rr_weighted == pytest.approx(base.rr_weighted)  # el R:R de diseño se conserva
    assert wide.tps[0][1] == pytest.approx(0.40 * wide.sl_pct / 0.35)


# ------------------------------------------------------------------ modo fácil
def test_hit_probability_on_known_path():
    from engine.easy_mode import hit_probability

    idx = pd.date_range("2026-01-01", periods=20, freq="5min", tz="UTC")
    up = pd.DataFrame({"close": np.linspace(100, 110, 20)}, index=idx)
    up["high"], up["low"] = up["close"] * 1.001, up["close"] * 0.999
    st = hit_probability(up, "LONG", tp_pct=1.0, sl_pct=1.0, horizon=6)
    assert st.tp_first == pytest.approx(100.0) and st.sl_first == 0
    st_s = hit_probability(up, "SHORT", tp_pct=1.0, sl_pct=1.0, horizon=6)
    assert st_s.sl_first == pytest.approx(100.0)


def test_easy_scan_demo():
    from engine.easy_mode import GLOSSARY, plain_summary, scan_best

    cands, errors = scan_best(DataFeeder(), 500, "demo", ["BTC", "SOL"])
    assert len(cands) == 2 and not errors
    assert cands[0].light in ("verde", "amarillo", "rojo") and len(cands[0].tp_stats) == 3
    s = plain_summary(cands[0])
    assert s["headline"] and len(s["steps"]) == 7 and "Stop Loss" in s["loss"]
    assert "Liquidación" in GLOSSARY and "Apalancamiento (x)" in GLOSSARY


# ---------------------------------------------------------------- reto escalera
def test_ladder_never_liquidates_and_beats_classic():
    from engine.ladder import LadderConfig, plan_ladder

    r = plan_ladder(LadderConfig(capital=20, target=200, coin="SOL", win_rate=70))
    assert r.liq_pct >= 3 * r.sl_pct  # la liquidación queda >= 3× más lejos que el SL
    assert -0.15 < r.roi_loss < 0  # perder un escalón cuesta poco, nunca el 100 %
    assert r.sim_protected["ruin_pct"] < r.sim_classic["ruin_pct"]
    assert r.sim_protected["success_pct"] > r.sim_classic["success_pct"]
    assert r.steps[-1].total_after_win >= 200 and r.steps[0].stake == 20


def test_ladder_warns_without_edge():
    from engine.ladder import LadderConfig, plan_ladder

    r = plan_ladder(LadderConfig(capital=20, target=100, coin="BTC", win_rate=35))
    assert r.notes and "ventaja" in r.notes[0]
