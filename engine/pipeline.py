"""
engine/pipeline.py
==================
Fachada que une todo el motor en UNA llamada. La usan app.py (Streamlit),
api.py (FastAPI) y el backtester, así todos ven exactamente el mismo análisis.

Flujo de `AnalysisPipeline.run()`:
    1. data_feeder      -> velas OHLCV (+ OI, funding)
    2. indicators       -> stack técnico completo
    3. chart_analyzer   -> estructura SMC (BOS, CHoCH, OB, FVG, S/R)
    4. strategies       -> estrategia elegida (o automática)
    5. signal_engine    -> score 0-100 y razones
    6. context_analyzer -> ajuste por contexto económico/social/político/cultural
    7. risk_manager     -> SL/TP/tamaño, filtro de noticias, kill switch
    8. database/alerts  -> guarda la señal y alerta si es >= 85

Nunca lanza excepciones: si algo falla devuelve `AnalysisResult(ok=False, error=...)`.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from engine.alerts import AlertManager
from engine.chart_analyzer import ChartAnalyzer, StructureResult
from engine.config import SETTINGS, Settings, get_logger
from engine.context_analyzer import ContextAnalyzer, ContextReport
from engine.data_feeder import (TIMEFRAMES, DataFeeder, DataFeedError, Market, MarketData,
                                get_tradingview_consensus)
from engine.database import Database
from engine.indicators import compute_all
from engine.news_filter import NewsFilter
from engine.risk_manager import RiskCheck, RiskManager, RiskParams, TradePlan
from engine.signal_engine import SignalEngine, SignalResult
from strategies import AUTO_KEY, auto_select, get_strategy

log = get_logger("pipeline")


@dataclass
class AnalysisRequest:
    """Parámetros de un análisis (lo que el usuario elige en la UI)."""

    symbol: str = "BTC/USDT"
    market: str = "crypto"
    timeframe: str = "1h"
    contract: str = "USDT-M"
    exchange: str | None = None
    strategy: str = AUTO_KEY
    equity: float | None = None
    risk_pct: float | None = None
    leverage: float | None = None
    sl_method: str = "auto"
    limit: int | None = None
    use_context: bool = True
    persist: bool = True  # Guardar señales en SQLite y enviar alertas


@dataclass
class AnalysisResult:
    """Todo lo que la UI/API necesita mostrar."""

    request: AnalysisRequest
    ok: bool
    error: str | None = None
    market_data: MarketData | None = None
    df: pd.DataFrame | None = None
    structure: StructureResult | None = None
    signal: SignalResult | None = None
    plan: TradePlan | None = None
    risk_check: RiskCheck | None = None
    context: ContextReport | None = None
    strategy_key: str = ""
    strategy_name: str = ""
    strategy_note: str = ""
    saved_signal_id: int | None = None
    is_new_alert: bool = False
    alert_delivered: bool = False
    closed_signals: list[dict] = field(default_factory=list)
    tv_consensus: dict | None = None
    warnings: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0

    def snapshot(self) -> dict:
        """Valores clave de la última vela (para la API y la UI)."""
        if self.df is None or self.df.empty:
            return {}
        row = self.df.iloc[-1]
        keys = ("open", "high", "low", "close", "volume", "ema_50", "ema_200", "rsi", "adx", "plus_di", "minus_di",
                "macd", "macd_signal", "macd_hist", "stochrsi_k", "stochrsi_d", "atr", "atr_pct", "vwap",
                "bb_upper", "bb_lower", "bb_pctb", "supertrend", "supertrend_dir", "vp_poc", "vp_vah", "vp_val",
                "rvol", "oi_change_pct", "score_long", "score_short")
        out = {}
        for k in keys:
            if k in row.index:
                v = row[k]
                out[k] = None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)
        return out

    def to_dict(self) -> dict:
        """Versión JSON (sin el DataFrame completo)."""
        md = self.market_data
        st = self.structure
        n = len(self.df) if self.df is not None else 0
        return {
            "ok": self.ok,
            "error": self.error,
            "request": self.request.__dict__,
            "market": None if md is None else {
                "symbol": md.symbol, "resolved_symbol": md.resolved_symbol, "market": md.market.value,
                "timeframe": md.timeframe, "source": md.source, "is_futures": md.is_futures,
                "contract": md.contract, "last_price": md.last_price, "change_pct": round(md.change_pct, 3),
                "last_bar_time": md.last_bar_time.isoformat(), "bars": len(md.df), "funding_rate": md.funding_rate,
            },
            "strategy": {"key": self.strategy_key, "name": self.strategy_name, "note": self.strategy_note},
            "signal": self.signal.to_dict() if self.signal else None,
            "plan": self.plan.to_dict() if self.plan else None,
            "risk": None if self.risk_check is None else {
                "allowed": self.risk_check.allowed, "blocks": self.risk_check.blocks,
                "warnings": self.risk_check.warnings, "kill_switch_active": self.risk_check.kill_switch_active,
                "consecutive_losses": self.risk_check.consecutive_losses,
            },
            "structure": None if st is None else {
                "summary": st.summary(n), "trend": st.trend,
                "key_levels": [lv.__dict__ for lv in st.key_levels],
                "last_event": st.last_event().__dict__ if st.last_event() else None,
                "active_order_blocks": [z.__dict__ for z in st.active_zones("OB", limit=6)],
                "active_fvgs": [z.__dict__ for z in st.active_zones("FVG", limit=6)],
            },
            "context": None if self.context is None else {
                "bias": self.context.bias, "label": self.context.label, "confidence": self.context.confidence,
                "pillars": {k: p.__dict__ for k, p in self.context.pillars.items()},
                "fear_greed": self.context.fear_greed, "errors": self.context.errors,
                "headlines": [h.__dict__ for h in self.context.headlines[:12]],
            },
            "indicators": self.snapshot(),
            "saved_signal_id": self.saved_signal_id,
            "is_new_alert": self.is_new_alert,
            "tv_consensus": self.tv_consensus,
            "warnings": self.warnings,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


class AnalysisPipeline:
    """Orquestador del análisis completo."""

    def __init__(self, settings: Settings = SETTINGS, db: Database | None = None,
                 context: ContextAnalyzer | None = None, news: NewsFilter | None = None) -> None:
        self.settings = settings
        self.feeder = DataFeeder(settings)
        self.analyzer = ChartAnalyzer()
        self.engine = SignalEngine(settings.score_valid, settings.score_alert)
        self.db = db or Database(settings.db_path)
        self.news = news or NewsFilter()
        self.context = context or ContextAnalyzer()
        self.alerts = AlertManager()
        self.risk = RiskManager(RiskParams(), db=self.db, news=self.news,
                                kill_switch_losses=settings.kill_switch_losses)

    # ------------------------------------------------------------------ API
    def run(self, req: AnalysisRequest, force_refresh: bool = False) -> AnalysisResult:
        """Ejecuta el análisis completo. Nunca lanza excepciones."""
        t0 = time.perf_counter()
        result = AnalysisResult(request=req, ok=False)

        # 1) Datos
        try:
            data = self.feeder.get_market_data(req.symbol, req.market, req.timeframe, req.contract,
                                               req.exchange, req.limit, force_refresh)
        except DataFeedError as exc:
            result.error = str(exc)
            return self._done(result, t0)
        except Exception as exc:  # noqa: BLE001
            log.exception("Error inesperado obteniendo datos")
            result.error = f"Error inesperado obteniendo datos: {exc}"
            return self._done(result, t0)
        result.market_data = data
        result.warnings.extend(data.warnings)

        # 2-5) Indicadores, estructura, estrategia y score
        try:
            spec = TIMEFRAMES[req.timeframe]
            df = compute_all(data.df, intraday=spec.intraday, open_interest=data.open_interest)
            df, structure = self.analyzer.analyze(df)
            if req.strategy == AUTO_KEY:
                strategy, note = auto_select(df, req.timeframe)
                note = f"Auto: {strategy.name} ({note})"
            else:
                strategy, note = get_strategy(req.strategy), ""
                if not strategy.is_recommended_for(req.timeframe):
                    note = f"{strategy.name} está pensada para {', '.join(strategy.timeframes)}."
            scored = self.engine.score_frame(df, strategy)
            signal = self.engine.evaluate(scored, strategy)
        except Exception as exc:  # noqa: BLE001
            log.exception("Error en el análisis técnico")
            result.error = f"Error en el análisis técnico: {exc}"
            return self._done(result, t0)

        result.df, result.structure, result.signal = scored, structure, signal
        result.strategy_key, result.strategy_name, result.strategy_note = strategy.key, strategy.name, note

        # 6) Contexto
        if req.use_context and self.settings.context_enabled:
            try:
                ctx = self.context.analyze(req.symbol, data.market.value, data.funding_rate)
                result.context = ctx
                points, ctx_note = ctx.adjustment_for(signal.dir_int, self.settings.context_max_adjust)
                signal.apply_context(points, ctx_note, self.settings.score_valid, self.settings.score_alert)
            except Exception as exc:  # noqa: BLE001
                result.warnings.append(f"Contexto no disponible: {exc}")

        # 7) Riesgo
        try:
            params = self.risk.params.with_overrides(
                equity=req.equity, risk_pct=req.risk_pct, leverage=req.leverage,
                sl_method=req.sl_method, atr_mult=strategy.sl_atr_mult,
            )
            persist = req.persist and data.market != Market.DEMO
            if persist:
                result.closed_signals = self.risk.update_open_signals(data.symbol.upper(), req.timeframe, scored)
            check = self.risk.pre_trade_check()
            result.risk_check = check
            if check.blocks:
                if signal.actionable:
                    for block in check.blocks:
                        signal.block(block)
                else:
                    result.warnings.extend(check.blocks)
            if signal.dir_int != 0:
                row = scored.iloc[-1]
                ob = row.get("bull_ob_low") if signal.dir_int == 1 else row.get("bear_ob_high")
                ob_level = float(ob) if ob is not None and np.isfinite(ob) else None
                sizing_symbol = data.resolved_symbol if data.market == Market.CME else data.symbol
                result.plan = self.risk.build_plan(signal.dir_int, float(row["close"]), float(row["atr"]), ob_level,
                                                   symbol=sizing_symbol, market=data.market.value,
                                                   contract=data.contract, params=params)
        except Exception as exc:  # noqa: BLE001
            log.exception("Error en gestión de riesgo")
            result.warnings.append(f"Error en gestión de riesgo: {exc}")

        # 8) Persistencia y alertas
        try:
            if (req.persist and data.market != Market.DEMO and signal.actionable and result.plan
                    and signal.score >= self.settings.track_min_score
                    and not self.db.open_signals(data.symbol.upper(), req.timeframe)):
                plan = result.plan
                result.saved_signal_id = self.db.save_signal({
                    "bar_time": signal.timestamp.isoformat(), "symbol": data.symbol.upper(),
                    "market": data.market.value, "timeframe": req.timeframe, "source": data.source,
                    "strategy": strategy.key, "direction": signal.direction, "score": signal.score,
                    "grade": signal.grade, "entry": plan.entry, "stop_loss": plan.stop_loss, "tp1": plan.tp1,
                    "tp2": plan.tp2, "position_size": plan.position_size, "leverage": plan.leverage,
                    "reasons": signal.reason_text,
                })
                if result.saved_signal_id and signal.grade_code == "alert":
                    result.is_new_alert = True
                    result.alert_delivered = self.alerts.send(data.symbol, req.timeframe, signal, plan)
        except Exception as exc:  # noqa: BLE001
            log.exception("Error guardando la señal")
            result.warnings.append(f"No se pudo guardar la señal: {exc}")

        # Extra: consenso TradingView (informativo)
        if self.settings.use_tradingview_ta:
            try:
                result.tv_consensus = get_tradingview_consensus(req.symbol, data.market, req.timeframe)
            except Exception:  # noqa: BLE001
                result.tv_consensus = None

        result.ok = True
        return self._done(result, t0)

    @staticmethod
    def _done(result: AnalysisResult, t0: float) -> AnalysisResult:
        result.elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return result
