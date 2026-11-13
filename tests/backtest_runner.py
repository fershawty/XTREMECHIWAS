"""
tests/backtest_runner.py
========================
Backtesting desde la terminal, con dos motores:

* native     (por defecto) -> engine/backtester.py: rápido, mismas reglas que la UI.
* backtrader               -> Backtrader con órdenes bracket (entrada + SL + TP) reales
                              del simulador, para validar los resultados con otro motor.

Ejemplos:
    python tests/backtest_runner.py --market crypto --symbol BTC/USDT --timeframe 1h --limit 1500
    python tests/backtest_runner.py --market etf --symbol SPY --timeframe 15m --strategy mean_reversion
    python tests/backtest_runner.py --market demo --engine backtrader --plot
    python tests/backtest_runner.py --market cme --symbol ES=F --timeframe 1h --threshold 85

Guarda un resumen en SQLite (tabla backtests) y, con --plot, un PNG en data/.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from engine.backtester import Backtester, BacktestConfig  # noqa: E402
from engine.chart_analyzer import ChartAnalyzer  # noqa: E402
from engine.config import DATA_DIR, SETTINGS  # noqa: E402
from engine.data_feeder import TIMEFRAMES, DataFeeder  # noqa: E402
from engine.database import Database  # noqa: E402
from engine.indicators import compute_all  # noqa: E402
from engine.risk_manager import RiskManager, RiskParams  # noqa: E402
from engine.signal_engine import SignalEngine  # noqa: E402
from strategies import AUTO_KEY, STRATEGIES, auto_select, get_strategy  # noqa: E402


# =============================================================================
# Preparación de datos (idéntica al análisis en vivo)
# =============================================================================
def prepare(args: argparse.Namespace) -> tuple[pd.DataFrame, str]:
    """Descarga velas, calcula indicadores/estructura y puntúa con la estrategia."""
    data = DataFeeder().get_market_data(args.symbol, args.market, args.timeframe, args.contract,
                                        args.exchange, args.limit, force_refresh=True)
    for w in data.warnings:
        print(f"  ⚠ {w}")
    df = compute_all(data.df, intraday=TIMEFRAMES[args.timeframe].intraday, open_interest=data.open_interest)
    df, _ = ChartAnalyzer().analyze(df)
    strategy = auto_select(df, args.timeframe)[0] if args.strategy == AUTO_KEY else get_strategy(args.strategy)
    scored = SignalEngine().score_frame(df, strategy)
    print(f"  Datos: {len(df)} velas de {data.source} ({data.resolved_symbol}) · estrategia {strategy.name}")
    return scored, strategy.key


# =============================================================================
# Motor Backtrader
# =============================================================================
def run_backtrader(scored: pd.DataFrame, cfg: BacktestConfig) -> dict:
    """
    Ejecuta la misma señal en Backtrader con órdenes bracket:
    entrada a mercado en la vela siguiente + Stop Loss + Take Profit (TP1, o TP2 si exit=tp2).
    """
    try:
        import backtrader as bt
    except ImportError:
        raise SystemExit("Backtrader no está instalado: pip install backtrader")

    risk = RiskManager(RiskParams(**{**cfg.risk.__dict__}))
    p = cfg.risk
    feed_df = scored[["open", "high", "low", "close", "volume"]].copy()
    sig = scored["signal_dir"].where(scored["signal_score"] >= cfg.threshold, 0).astype(float)
    feed_df["signal"] = sig
    feed_df["atr"] = scored["atr"].fillna(0.0)
    feed_df["ob_long"] = scored.get("bull_ob_low", pd.Series(float("nan"), index=scored.index))
    feed_df["ob_short"] = scored.get("bear_ob_high", pd.Series(float("nan"), index=scored.index))
    feed_df.index = feed_df.index.tz_convert(None)

    class SignalData(bt.feeds.PandasData):
        lines = ("signal", "atr", "ob_long", "ob_short")
        params = (("signal", -1), ("atr", -1), ("ob_long", -1), ("ob_short", -1))

    class FuturesScopeStrategy(bt.Strategy):
        def __init__(self) -> None:
            self.orders = []
            self.bar = 0

        def notify_order(self, order) -> None:
            if order.status in (order.Completed, order.Canceled, order.Margin, order.Rejected):
                self.orders = [o for o in self.orders if o.alive()]

        def next(self) -> None:
            self.bar += 1
            if self.bar < cfg.warmup_bars or self.position or any(o.alive() for o in self.orders):
                return
            d = int(self.data.signal[0])
            if d == 0:
                return
            entry = float(self.data.close[0])  # Aproximación: la ejecución real será en la apertura siguiente
            atr = float(self.data.atr[0]) or entry * 0.01
            ob = float(self.data.ob_long[0] if d == 1 else self.data.ob_short[0])
            stop, _ = risk.stop_loss(d, entry, atr, ob if ob == ob else None, p)
            tp1, tp2 = risk.take_profits(d, entry, stop, p.tp1_rr, p.tp2_rr)
            target = tp2 if cfg.exit_mode == "tp2" else tp1
            size = (self.broker.getvalue() * p.risk_pct / 100.0) / abs(entry - stop)
            maker = self.buy_bracket if d == 1 else self.sell_bracket
            self.orders = list(maker(size=size, exectype=bt.Order.Market, stopprice=stop, limitprice=target))

    cerebro = bt.Cerebro(stdstats=False)
    cerebro.adddata(SignalData(dataname=feed_df))
    cerebro.addstrategy(FuturesScopeStrategy)
    cerebro.broker.setcash(p.equity)
    cerebro.broker.setcommission(commission=cfg.fee_pct / 100.0, leverage=max(1.0, p.leverage))
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="dd")
    result = cerebro.run()[0]
    ta = result.analyzers.trades.get_analysis()
    total = ta.get("total", {}).get("closed", 0) or 0
    won = ta.get("won", {}).get("total", 0) or 0
    final = cerebro.broker.getvalue()
    return {
        "trades": int(total),
        "win_rate": (won / total * 100.0) if total else 0.0,
        "final_equity": round(final, 2),
        "return_pct": (final / p.equity - 1.0) * 100.0,
        "max_dd_pct": float(result.analyzers.dd.get_analysis().get("max", {}).get("drawdown", 0.0)),
        "profit_factor": float("nan"),
        "total_r": float("nan"),
    }


# =============================================================================
# Gráfico PNG (mplfinance)
# =============================================================================
def save_plot(scored: pd.DataFrame, trades: pd.DataFrame, path: Path, title: str) -> Path:
    """Velas + EMAs + marcas de entrada de cada operación."""
    import mplfinance as mpf

    bars = scored.tail(400)
    data = bars[["open", "high", "low", "close", "volume"]].copy()
    data.columns = ["Open", "High", "Low", "Close", "Volume"]
    data.index = data.index.tz_convert(None)
    longs = pd.Series(float("nan"), index=bars.index)
    shorts = pd.Series(float("nan"), index=bars.index)
    if not trades.empty:
        for _, t in trades.iterrows():
            ts = pd.Timestamp(t["entry_time"])
            if ts in longs.index:
                (longs if t["direction"] == "LONG" else shorts)[ts] = t["entry"]
    adds = [mpf.make_addplot(bars["ema_50"], color="#F5C542", width=1),
            mpf.make_addplot(bars["ema_200"], color="#4DA3FF", width=1)]
    if longs.notna().any():
        adds.append(mpf.make_addplot(longs.values, type="scatter", marker="^", markersize=70, color="#00FF88"))
    if shorts.notna().any():
        adds.append(mpf.make_addplot(shorts.values, type="scatter", marker="v", markersize=70, color="#FF3B30"))
    style = mpf.make_mpf_style(base_mpf_style="nightclouds", facecolor="#0E1117", figcolor="#0E1117",
                               gridcolor="#161B26", marketcolors=mpf.make_marketcolors(
                                   up="#00FF88", down="#FF3B30", wick="inherit", edge="inherit", volume="inherit"))
    mpf.plot(data, type="candle", style=style, volume=True, addplot=adds, title=title, figsize=(14, 8),
             savefig=dict(fname=str(path), dpi=120, bbox_inches="tight"))
    return path


# =============================================================================
# CLI
# =============================================================================
def main() -> None:
    ap = argparse.ArgumentParser(description="Backtest de FUTURES-SCOPE")
    ap.add_argument("--market", default="demo", choices=["crypto", "stock", "etf", "cme", "demo"])
    ap.add_argument("--symbol", default=None, help="BTC/USDT, SPY, ES=F... (por defecto según el mercado)")
    ap.add_argument("--timeframe", default="1h", choices=list(TIMEFRAMES))
    ap.add_argument("--contract", default="USDT-M", choices=["USDT-M", "COIN-M"])
    ap.add_argument("--exchange", default=None, choices=["binance", "bybit", "okx"])
    ap.add_argument("--strategy", default=AUTO_KEY, choices=[AUTO_KEY, *STRATEGIES])
    ap.add_argument("--limit", type=int, default=1500, help="velas a descargar")
    ap.add_argument("--threshold", type=float, default=SETTINGS.score_valid, help="score mínimo de entrada")
    ap.add_argument("--exit", dest="exit_mode", default="partial", choices=["partial", "tp1", "tp2"])
    ap.add_argument("--engine", default="native", choices=["native", "backtrader"])
    ap.add_argument("--equity", type=float, default=SETTINGS.account_equity)
    ap.add_argument("--risk", type=float, default=SETTINGS.risk_per_trade_pct)
    ap.add_argument("--plot", action="store_true", help="guardar PNG con las entradas")
    args = ap.parse_args()
    args.symbol = args.symbol or {"crypto": "BTC/USDT", "stock": "NVDA", "etf": "SPY", "cme": "ES=F",
                                  "demo": "DEMO/USDT"}[args.market]

    print(f"\n▶ FUTURES-SCOPE backtest · {args.symbol} · {args.timeframe} · motor {args.engine}")
    try:
        scored, strategy_key = prepare(args)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"✖ No se pudieron preparar los datos: {exc}")

    cfg = BacktestConfig(threshold=args.threshold, exit_mode=args.exit_mode,
                         risk=RiskParams(equity=args.equity, risk_pct=args.risk))
    if args.engine == "backtrader":
        metrics, trades = run_backtrader(scored, cfg), pd.DataFrame()
    else:
        result = Backtester().run(scored, cfg)
        metrics, trades = result.metrics, result.trades

    print("\n  Resultado")
    for k in ("trades", "win_rate", "profit_factor", "total_r", "return_pct", "max_dd_pct", "final_equity"):
        v = metrics.get(k)
        if isinstance(v, float):
            v = "n/d" if v != v else f"{v:,.2f}"
        print(f"  {k:<15} {v}")
    if args.market == "demo":
        print("\n  ⚠ Datos sintéticos: el resultado NO representa rendimiento real.")

    Database().save_backtest({"symbol": args.symbol, "market": args.market, "timeframe": args.timeframe,
                              "strategy": f"{strategy_key}:{args.engine}", "params": vars(args), **metrics})
    if args.plot:
        out = DATA_DIR / f"backtest_{args.symbol.replace('/', '-')}_{args.timeframe}.png"
        save_plot(scored, trades, out, f"{args.symbol} {args.timeframe} · {strategy_key}")
        print(f"  Gráfico guardado en {out}")


if __name__ == "__main__":
    main()
