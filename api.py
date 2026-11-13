"""
api.py
======
API REST del motor de análisis (FastAPI). Opcional: la interfaz Streamlit NO la
necesita (usa el motor directamente). Sirve para integrar FUTURES-SCOPE con otros
programas, bots o webhooks.

Ejecutar:
    uvicorn api:app --reload --port 8000
Documentación interactiva:
    http://localhost:8000/docs

Ejemplos:
    GET  /analyze?symbol=BTC/USDT&market=crypto&timeframe=1h
    GET  /analyze?symbol=SPY&market=etf&timeframe=15m&strategy=mean_reversion
    GET  /signals?limit=20
    POST /kill-switch/reset
"""

from __future__ import annotations

import json
import math
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from engine.config import APP_NAME, APP_VERSION, BUILD_TAG
from engine.data_feeder import SYMBOL_PRESETS, TIMEFRAMES, Market
from engine.pipeline import AnalysisPipeline, AnalysisRequest
from strategies import AUTO_KEY, STRATEGIES

app = FastAPI(
    title=f"{APP_NAME} API",
    version=APP_VERSION,
    description=f"Motor de análisis de futuros · {BUILD_TAG}. Solo genera señales; no ejecuta órdenes.",
)
_pipeline: AnalysisPipeline | None = None


def pipeline() -> AnalysisPipeline:
    """Instancia perezosa (se crea en la primera petición)."""
    global _pipeline
    if _pipeline is None:
        _pipeline = AnalysisPipeline()
    return _pipeline


def _clean(obj):
    """Convierte NaN/inf a None y tipos raros a texto para que el JSON sea válido."""
    if isinstance(obj, float):
        return None if not math.isfinite(obj) else obj
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (str, int, bool)) or obj is None:
        return obj
    try:
        return _clean(json.loads(json.dumps(obj, default=str)))
    except (TypeError, ValueError):
        return str(obj)


@app.get("/health")
def health() -> dict:
    """Comprobación de vida."""
    return {"status": "ok", "app": APP_NAME, "version": APP_VERSION, "build": BUILD_TAG}


@app.get("/strategies")
def strategies() -> dict:
    """Estrategias disponibles con sus pesos."""
    return {
        "auto": AUTO_KEY,
        "strategies": [
            {"key": s.key, "name": s.name, "description": s.description, "timeframes": list(s.timeframes),
             "weights": s.weights}
            for s in STRATEGIES.values()
        ],
    }


@app.get("/markets")
def markets() -> dict:
    """Mercados, temporalidades y símbolos sugeridos."""
    return {"markets": {m.value: SYMBOL_PRESETS[m] for m in Market}, "timeframes": list(TIMEFRAMES)}


@app.get("/analyze")
def analyze(
    symbol: str = Query("BTC/USDT", max_length=30),
    market: Literal["crypto", "stock", "etf", "cme", "demo"] = "crypto",
    timeframe: Literal["1m", "5m", "15m", "1h", "4h", "1D"] = "1h",
    contract: Literal["USDT-M", "COIN-M"] = "USDT-M",
    exchange: Literal["binance", "bybit", "okx"] | None = None,
    strategy: str = AUTO_KEY,
    equity: float | None = Query(None, gt=0),
    risk_pct: float | None = Query(None, gt=0, le=10),
    leverage: float | None = Query(None, ge=1, le=125),
    sl_method: Literal["auto", "atr", "order_block"] = "auto",
    context: bool = True,
    refresh: bool = False,
) -> JSONResponse:
    """Análisis completo: señal, score, razones, plan de riesgo, estructura y contexto."""
    if strategy != AUTO_KEY and strategy not in STRATEGIES:
        raise HTTPException(400, f"Estrategia desconocida. Usa: {[AUTO_KEY, *STRATEGIES]}")
    req = AnalysisRequest(symbol=symbol, market=market, timeframe=timeframe, contract=contract, exchange=exchange,
                          strategy=strategy, equity=equity, risk_pct=risk_pct, leverage=leverage,
                          sl_method=sl_method, use_context=context)
    result = pipeline().run(req, force_refresh=refresh)
    payload = _clean(result.to_dict())
    return JSONResponse(payload, status_code=200 if result.ok else 502)


@app.get("/signals")
def signals(limit: int = Query(50, ge=1, le=500)) -> dict:
    """Últimas señales registradas en SQLite."""
    df = pipeline().db.recent_signals(limit)
    return {"stats": pipeline().db.stats(), "signals": _clean(df.to_dict(orient="records"))}


@app.get("/kill-switch")
def kill_switch() -> dict:
    """Estado del kill switch."""
    active, streak = pipeline().risk.kill_switch_status()
    return {"active": active, "consecutive_losses": streak, "limit": pipeline().risk.kill_switch_losses}


@app.post("/kill-switch/reset")
def kill_switch_reset() -> dict:
    """Reactiva el bot tras una pausa por pérdidas."""
    pipeline().risk.reset_kill_switch()
    return {"active": False, "message": "Kill switch reiniciado"}


@app.get("/news/upcoming")
def news_upcoming(hours: int = Query(48, ge=1, le=168)) -> dict:
    """Próximas noticias de alto impacto."""
    return {"events": [e.to_dict() for e in pipeline().news.upcoming(hours)],
            "status": pipeline().news.check().message}
