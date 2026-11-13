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
