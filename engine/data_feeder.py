"""
engine/data_feeder.py
=====================
Capa de DATOS de FUTURES-SCOPE.

Responsabilidad única: conseguir velas OHLCV limpias y unificadas, vengan de donde
vengan, para que el resto del motor no tenga que saber nada de exchanges.

Fuentes soportadas
------------------
* Cripto (futuros perpetuos)  -> CCXT: Binance, Bybit, OKX (USDT-M y COIN-M)
  - Extra: Open Interest histórico y Funding Rate (si el exchange lo permite).
  - Si TODOS los exchanges fallan -> fallback a yfinance (precio SPOT, avisado).
* Acciones / ETFs              -> yfinance (SPY, QQQ, NVDA, ...)
* Futuros CME                  -> yfinance (ES=F, NQ=F, GC=F, CL=F, ...)
* Demo                         -> datos sintéticos (para probar sin internet).
                                  NUNCA se usan en silencio: solo si los eliges.

Formato unificado de salida (MarketData.df)
-------------------------------------------
DataFrame con índice DatetimeIndex en UTC (inicio de vela) y columnas float:
    open, high, low, close, volume
"""

from __future__ import annotations

import threading
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

from engine.config import SETTINGS, Settings, get_logger

# Las librerías externas se importan de forma tolerante: si falta alguna, el bot
# sigue funcionando con las fuentes que sí estén disponibles.
try:
    import ccxt  # type: ignore
except ImportError:  # pragma: no cover
    ccxt = None

try:
    import yfinance as yf  # type: ignore
except ImportError:  # pragma: no cover
    yf = None

try:
    from tradingview_ta import Interval, TA_Handler  # type: ignore
except ImportError:  # pragma: no cover
    TA_Handler = None
    Interval = None

log = get_logger("data_feeder")


# =============================================================================
# Tipos y catálogos
# =============================================================================
class Market(str, Enum):
    """Tipos de mercado que entiende el bot."""

    CRYPTO = "crypto"
    STOCK = "stock"
    ETF = "etf"
    CME = "cme"
    DEMO = "demo"


MARKET_LABELS: dict[Market, str] = {
    Market.CRYPTO: "Cripto",
    Market.STOCK: "Acción",
    Market.ETF: "ETF",
    Market.CME: "Futuros CME",
    Market.DEMO: "Demo (sintético)",
}


class ContractType(str, Enum):
    """Tipo de contrato de futuros perpetuo en cripto."""

    USDT_M = "USDT-M"  # Lineal: margen y PnL en USDT (BTC/USDT:USDT)
    COIN_M = "COIN-M"  # Inverso: margen y PnL en la moneda (BTC/USD:BTC)


@dataclass(frozen=True)
class TimeframeSpec:
    """Cómo se traduce cada temporalidad a cada fuente de datos."""

    key: str  # Clave que ve el usuario: "1h"
    ccxt: str  # Código CCXT: "1h"
    yf_interval: str  # Intervalo de yfinance
    yf_period: str  # Historial a pedir a yfinance (tiene límites por intervalo)
    resample: str | None  # Regla pandas si hay que re-muestrear (4h se arma desde 1h)
    delta: pd.Timedelta  # Duración de una vela
    intraday: bool


# Límites de yfinance: 1m -> máx 7 días | 5m/15m -> 60 días | 1h -> 730 días
TIMEFRAMES: dict[str, TimeframeSpec] = {
    "1m": TimeframeSpec("1m", "1m", "1m", "7d", None, pd.Timedelta(minutes=1), True),
    "5m": TimeframeSpec("5m", "5m", "5m", "60d", None, pd.Timedelta(minutes=5), True),
    "15m": TimeframeSpec("15m", "15m", "15m", "60d", None, pd.Timedelta(minutes=15), True),
    "1h": TimeframeSpec("1h", "1h", "1h", "180d", None, pd.Timedelta(hours=1), True),
    "4h": TimeframeSpec("4h", "4h", "1h", "720d", "4h", pd.Timedelta(hours=4), True),
    "1D": TimeframeSpec("1D", "1d", "1d", "5y", None, pd.Timedelta(days=1), False),
}

# Símbolos sugeridos en la UI (el usuario puede escribir cualquier otro)
SYMBOL_PRESETS: dict[Market, list[str]] = {
    Market.CRYPTO: ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
                    "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT", "SUI/USDT"],
    Market.STOCK: ["NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "META", "GOOGL", "AMD", "NFLX", "COIN"],
    Market.ETF: ["SPY", "QQQ", "IWM", "DIA", "GLD", "TLT", "XLF", "XLE", "SMH", "ARKK"],
    Market.CME: ["ES=F", "NQ=F", "YM=F", "RTY=F", "MES=F", "MNQ=F", "GC=F", "CL=F", "SI=F", "NG=F"],
    Market.DEMO: ["DEMO/USDT"],
}

SUPPORTED_EXCHANGES = ("binance", "bybit", "okx")

# Velas mínimas para poder analizar y para que la EMA 200 sea fiable
MIN_BARS = 60
RELIABLE_BARS = 220


class DataFeedError(RuntimeError):
    """Error controlado de la capa de datos (mensaje pensado para el usuario)."""


# =============================================================================
# Contenedor de datos
# =============================================================================
@dataclass
class MarketData:
    """Resultado de una descarga: velas + metadatos + avisos."""

    df: pd.DataFrame
    symbol: str  # Lo que pidió el usuario: "BTC/USDT"
    resolved_symbol: str  # Lo que se usó en la fuente: "BTC/USDT:USDT", "BTC-USD"
    market: Market
    timeframe: str
    source: str  # binance | bybit | okx | yfinance | demo
    is_futures: bool
    contract: str | None = None
    open_interest: pd.Series | None = None
    funding_rate: float | None = None
    warnings: list[str] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def last_price(self) -> float:
        """Último precio de cierre disponible."""
        return float(self.df["close"].iloc[-1])

    @property
    def last_bar_time(self) -> pd.Timestamp:
        """Hora (UTC) de inicio de la última vela analizada."""
        return self.df.index[-1]

    @property
    def change_pct(self) -> float:
        """Variación % de la última vela respecto al cierre anterior."""
        if len(self.df) < 2:
            return 0.0
        prev = float(self.df["close"].iloc[-2])
        return (self.last_price / prev - 1.0) * 100.0 if prev else 0.0


# =============================================================================
# Utilidades de símbolos
# =============================================================================
def parse_crypto_symbol(raw: str) -> tuple[str, str]:
    """
    Normaliza lo que escriba el usuario a (base, quote).

    Acepta: "BTC/USDT", "btcusdt", "BTC-USD", "BTC/USDT:USDT", "eth".
    """
    s = raw.upper().strip().replace(" ", "")
    if ":" in s:  # Formato CCXT completo -> quitamos el settle
        s = s.split(":")[0]
    for sep in ("/", "-", "_"):
        if sep in s:
            base, quote = s.split(sep, 1)
            return base, quote or "USDT"
    for quote in ("USDT", "USDC", "BUSD", "USD"):
        if s.endswith(quote) and len(s) > len(quote):
            return s[: -len(quote)], quote
    return s, "USDT"


def to_ccxt_symbol(raw: str, contract: ContractType | str = ContractType.USDT_M) -> str:
    """
    Convierte a símbolo unificado de CCXT para perpetuos.

    USDT-M -> "BTC/USDT:USDT"   |   COIN-M -> "BTC/USD:BTC"
    """
    base, quote = parse_crypto_symbol(raw)
    if ContractType(contract) == ContractType.COIN_M:
        return f"{base}/USD:{base}"
    if quote in ("USD", "BUSD"):
        quote = "USDT"
    return f"{base}/{quote}:{quote}"


def crypto_to_yahoo(raw: str) -> str:
    """'BTC/USDT' -> 'BTC-USD' (ticker de Yahoo, precio spot)."""
    base, _ = parse_crypto_symbol(raw)
    return f"{base}-USD"


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """
    Limpia y valida un DataFrame OHLCV:
    - índice UTC ordenado y sin duplicados
    - columnas float, sin precios <= 0 ni NaN
    - corrige velas incoherentes (high < close, etc.)
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    out = df[["open", "high", "low", "close", "volume"]].astype(float).copy()
    idx = pd.DatetimeIndex(out.index)
    out.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out["volume"] = out["volume"].fillna(0.0).clip(lower=0.0)
    out = out.dropna(subset=["open", "high", "low", "close"])
    out = out[(out[["open", "high", "low", "close"]] > 0).all(axis=1)]
    # Coherencia: el high debe ser el máximo y el low el mínimo de la vela
    out["high"] = out[["open", "high", "low", "close"]].max(axis=1)
    out["low"] = out[["open", "high", "low", "close"]].min(axis=1)
    out.index.name = "time"
    return out


def drop_incomplete_candle(df: pd.DataFrame, delta: pd.Timedelta) -> pd.DataFrame:
    """Quita la última vela si todavía no ha cerrado (evita señales que 'repintan')."""
    if df.empty:
        return df
    now = pd.Timestamp.now(tz="UTC")
    if df.index[-1] + delta > now:
        return df.iloc[:-1]
    return df


def resample_ohlcv(df: pd.DataFrame, rule: str, market: Market) -> pd.DataFrame:
    """
    Re-muestrea velas (p. ej. 1h -> 4h).

    Para acciones/ETFs alinea los bloques a la apertura de Wall Street (09:30 NY),
    así las velas de 4h son 09:30-13:30 y 13:30-16:00 en lugar de cortes arbitrarios.
    """
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    if market in (Market.STOCK, Market.ETF):
        local = df.tz_convert("America/New_York")
        out = local.resample(rule, origin="start_day", offset="9h30min").agg(agg)
        out = out.dropna(subset=["open", "close"]).tz_convert("UTC")
    else:
        out = df.resample(rule).agg(agg).dropna(subset=["open", "close"])
    return out


# =============================================================================
# Datos sintéticos (modo demo)
# =============================================================================
def generate_demo_data(
    timeframe: str = "1h",
    n: int = 600,
    symbol: str = "DEMO/USDT",
    start_price: float = 50_000.0,
) -> tuple[pd.DataFrame, pd.Series]:
    """
    Genera velas sintéticas realistas (tendencias, rangos y volatilidad variable)
    + una serie de Open Interest correlacionada. Determinista por símbolo/temporalidad.

    Úsalo para probar la interfaz y el motor sin conexión a internet.
    """
    spec = TIMEFRAMES.get(timeframe, TIMEFRAMES["1h"])
    seed = zlib.crc32(f"{symbol}|{timeframe}".encode())
    rng = np.random.default_rng(seed)

    # Régimen de mercado por bloques: bajista fuerte ... alcista fuerte
    block = 70
    drifts = rng.choice([-0.0016, -0.0007, 0.0, 0.0, 0.0007, 0.0016], size=n // block + 2)
    drift = np.repeat(drifts, block)[:n]
    vol = 0.0055 * (1.0 + 0.5 * np.sin(np.arange(n) / 37.0)) * (1.0 + 0.3 * rng.random(n))
    rets = drift + vol * rng.standard_t(df=4, size=n) * 0.6

    close = start_price * np.exp(np.cumsum(rets))
    open_ = np.r_[start_price, close[:-1]] * (1.0 + rng.normal(0, 0.0006, n))
    wick = np.abs(rng.normal(0, 0.0028, n)) + 0.0004
    high = np.maximum(open_, close) * (1.0 + wick)
    low = np.minimum(open_, close) * (1.0 - wick * rng.uniform(0.6, 1.4, n))
    volume = 1_000.0 * (1.0 + 4.0 * np.abs(rets) / vol.mean()) * rng.lognormal(0, 0.35, n)

    end = pd.Timestamp.now(tz="UTC").floor(spec.delta if spec.delta < pd.Timedelta(days=1) else "D")
    index = pd.date_range(end=end - spec.delta, periods=n, freq=spec.delta, tz="UTC")
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=index
    )

    # Open Interest sintético: sube cuando entra dinero en la dirección de la tendencia
    oi = 2.0e5 * np.exp(np.cumsum(0.4 * rets * np.sign(drift + 1e-12) + rng.normal(0, 0.002, n)))
    return normalize_ohlcv(df), pd.Series(oi, index=index, name="open_interest")


# =============================================================================
# Feeder principal
# =============================================================================
class DataFeeder:
    """
    Punto de entrada único para obtener datos de mercado.

    Ejemplo:
        feeder = DataFeeder()
        data = feeder.get_market_data("BTC/USDT", "crypto", "1h")
        print(data.df.tail(), data.source)
    """

    # Instancias CCXT compartidas (cargar mercados es lento: se hace una vez)
    _exchange_cache: dict[str, Any] = {}
    _exchange_lock = threading.Lock()

    def __init__(self, settings: Settings = SETTINGS) -> None:
        self.settings = settings
        self._cache: dict[tuple, tuple[float, MarketData]] = {}
        self._cache_lock = threading.Lock()

    # ------------------------------------------------------------------ API
    def get_market_data(
        self,
        symbol: str,
        market: Market | str,
        timeframe: str,
        contract: ContractType | str = ContractType.USDT_M,
        exchange: str | None = None,
        limit: int | None = None,
        force_refresh: bool = False,
    ) -> MarketData:
        """
        Descarga (o devuelve de caché) las velas del activo.

        Args:
            symbol: "BTC/USDT", "SPY", "ES=F"...
            market: "crypto" | "stock" | "etf" | "cme" | "demo"
            timeframe: "1m" | "5m" | "15m" | "1h" | "4h" | "1D"
            contract: "USDT-M" o "COIN-M" (solo cripto)
            exchange: exchange preferido (solo cripto). None = orden del .env
            limit: número de velas (por defecto OHLCV_LIMIT del .env)
            force_refresh: ignora la caché (botón "Analizar ahora")

        Raises:
            DataFeedError: si ninguna fuente pudo entregar datos válidos.
        """
        market = Market(market)
        if timeframe not in TIMEFRAMES:
            raise DataFeedError(f"Temporalidad no soportada: {timeframe}. Usa {list(TIMEFRAMES)}")
        spec = TIMEFRAMES[timeframe]
        limit = int(limit or self.settings.ohlcv_limit)
        contract = ContractType(contract)
        symbol = (symbol or "").strip()
        if not symbol:
            raise DataFeedError("Escribe un símbolo (por ejemplo BTC/USDT o SPY).")

        key = (symbol.upper(), market.value, timeframe, contract.value, exchange, limit)
        if not force_refresh and self.settings.data_cache_ttl > 0:
            with self._cache_lock:
                hit = self._cache.get(key)
            if hit and (time.time() - hit[0]) < self.settings.data_cache_ttl:
                return hit[1]

        if market == Market.DEMO:
            data = self._get_demo(symbol, spec, limit)
        elif market == Market.CRYPTO:
            data = self._get_crypto(symbol, spec, contract, exchange, limit)
        else:
            data = self._get_yfinance_market(symbol, market, spec, limit)

        data = self._finalize(data, spec)
        with self._cache_lock:
            self._cache[key] = (time.time(), data)
        return data

    def clear_cache(self) -> None:
        """Vacía la caché en memoria."""
        with self._cache_lock:
            self._cache.clear()

    # ------------------------------------------------------------ Cripto
    def _get_crypto(
        self,
        symbol: str,
        spec: TimeframeSpec,
        contract: ContractType,
        preferred: str | None,
        limit: int,
    ) -> MarketData:
        """Intenta exchange por exchange; si todos fallan, usa yfinance (spot)."""
        errors: list[str] = []
        ccxt_symbol = to_ccxt_symbol(symbol, contract)

        order: list[str] = []
        for ex_id in ([preferred] if preferred else []) + list(self.settings.exchange_priority):
            if ex_id and ex_id in SUPPORTED_EXCHANGES and ex_id not in order:
                order.append(ex_id)

        if ccxt is None:
            errors.append("ccxt no está instalado")
        else:
            for ex_id in order:
                try:
                    exchange = self._get_exchange(ex_id)
                    df = self._fetch_ccxt_ohlcv(exchange, ccxt_symbol, spec.ccxt, limit)
                    if len(df) < MIN_BARS:
                        raise DataFeedError(f"solo {len(df)} velas")
                    warnings = [f"{e} (se usó {ex_id})" for e in errors]
                    return MarketData(
                        df=df,
                        symbol=symbol,
                        resolved_symbol=ccxt_symbol,
                        market=Market.CRYPTO,
                        timeframe=spec.key,
                        source=ex_id,
                        is_futures=True,
                        contract=contract.value,
                        open_interest=self._fetch_open_interest(exchange, ccxt_symbol, spec, limit),
                        funding_rate=self._fetch_funding_rate(exchange, ccxt_symbol),
                        warnings=warnings,
                    )
                except Exception as exc:  # noqa: BLE001 - queremos seguir con el siguiente
                    msg = f"{ex_id}: {self._short_error(exc)}"
                    errors.append(msg)
                    log.warning("Fallo CCXT %s", msg)

        # ---- Fallback final: Yahoo Finance (precio spot) ----
        yahoo = crypto_to_yahoo(symbol)
        try:
            df = self._fetch_yfinance(yahoo, spec, Market.CRYPTO, limit)
            warnings = [
                "Exchanges de futuros no disponibles -> usando Yahoo Finance "
                f"({yahoo}, precio SPOT, sin Open Interest ni funding).",
                *errors,
            ]
            return MarketData(
                df=df,
                symbol=symbol,
                resolved_symbol=yahoo,
                market=Market.CRYPTO,
                timeframe=spec.key,
                source="yfinance",
                is_futures=False,
                contract=None,
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"yfinance: {self._short_error(exc)}")

        raise DataFeedError(
            f"No se pudieron obtener datos de {symbol} ({spec.key}). Detalle:\n- " + "\n- ".join(errors)
        )

    def _get_exchange(self, ex_id: str) -> Any:
        """Crea (una sola vez) la instancia CCXT configurada para futuros."""
        with self._exchange_lock:
            if ex_id in self._exchange_cache:
                return self._exchange_cache[ex_id]
            klass = getattr(ccxt, ex_id)
            market_types = {"binance": ["linear", "inverse"], "bybit": ["linear", "inverse"], "okx": ["swap"]}
            exchange = klass(
                {
                    "enableRateLimit": True,
                    "timeout": self.settings.request_timeout_ms,
                    "options": {
                        "defaultType": "swap",
                        # Solo cargamos mercados de futuros: arranque mucho más rápido
                        "fetchMarkets": {"types": market_types[ex_id]},
                    },
                }
            )
            # NOTA: no pasamos API keys a propósito. Para leer datos no hacen falta
            # y así es imposible ejecutar operaciones privadas por accidente.
            self._exchange_cache[ex_id] = exchange
            return exchange

    @staticmethod
    def _fetch_ccxt_ohlcv(exchange: Any, symbol: str, tf: str, limit: int) -> pd.DataFrame:
        """
        Descarga `limit` velas paginando hacia adelante desde `since`.
        (OKX solo da 100-300 velas por llamada; Binance/Bybit hasta 1000.)
        """
        tf_ms = exchange.parse_timeframe(tf) * 1000
        max_batch = {"binance": 1000, "bybit": 1000, "okx": 100}.get(exchange.id, 500)
        now = exchange.milliseconds()
        since = now - (limit + 2) * tf_ms
        rows: list[list[float]] = []

        for _ in range(40):  # tope de seguridad de llamadas
            batch = exchange.fetch_ohlcv(symbol, tf, since=since, limit=max_batch)
            if not batch:
                break
            rows.extend(batch)
            next_since = int(batch[-1][0]) + tf_ms
            if next_since <= since or next_since >= now - tf_ms:
                break
            since = next_since

        if not rows:
            raise DataFeedError("el exchange no devolvió velas")
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df = df.drop_duplicates("ts").sort_values("ts")
        df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return normalize_ohlcv(df.drop(columns="ts")).tail(limit)

    @staticmethod
    def _fetch_open_interest(exchange: Any, symbol: str, spec: TimeframeSpec, limit: int) -> pd.Series | None:
        """Open Interest histórico (no todos los exchanges/temporalidades lo dan)."""
        try:
            if not exchange.has.get("fetchOpenInterestHistory"):
                return None
            tf = "5m" if spec.ccxt == "1m" else spec.ccxt  # 1m no existe para OI
            if tf not in ("5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"):
                return None
            data = exchange.fetch_open_interest_history(symbol, tf, limit=min(limit, 500))
            if not data:
                return None
            ts, values = [], []
            for row in data:
                val = row.get("openInterestAmount")
                if val is None:
                    val = row.get("openInterestValue")
                if row.get("timestamp") is not None and val is not None:
                    ts.append(row["timestamp"])
                    values.append(float(val))
            if len(values) < 10:
                return None
            series = pd.Series(values, index=pd.to_datetime(ts, unit="ms", utc=True), name="open_interest")
            return series[~series.index.duplicated(keep="last")].sort_index()
        except Exception as exc:  # noqa: BLE001
            log.info("Open Interest no disponible en %s: %s", exchange.id, exc)
            return None

    @staticmethod
    def _fetch_funding_rate(exchange: Any, symbol: str) -> float | None:
        """Funding rate actual del perpetuo (positivo = los longs pagan a los shorts)."""
        try:
            if not exchange.has.get("fetchFundingRate"):
                return None
            info = exchange.fetch_funding_rate(symbol)
            rate = info.get("fundingRate")
            return float(rate) if rate is not None else None
        except Exception as exc:  # noqa: BLE001
            log.info("Funding no disponible en %s: %s", exchange.id, exc)
            return None

    # --------------------------------------------- Acciones / ETFs / CME
    def _get_yfinance_market(self, symbol: str, market: Market, spec: TimeframeSpec, limit: int) -> MarketData:
        """Acciones, ETFs y futuros CME vía yfinance."""
        ticker = symbol.upper().replace(" ", "")
        try:
            df = self._fetch_yfinance(ticker, spec, market, limit)
        except Exception as exc:  # noqa: BLE001
            raise DataFeedError(
                f"No se pudieron obtener datos de {ticker} en Yahoo Finance: {self._short_error(exc)}"
            ) from exc
        warnings: list[str] = []
        if market in (Market.STOCK, Market.ETF):
            warnings.append("Datos del subyacente (contado). Para futuros de índices usa 'Futuros CME' (ES=F, NQ=F).")
        return MarketData(
            df=df,
            symbol=symbol,
            resolved_symbol=ticker,
            market=market,
            timeframe=spec.key,
            source="yfinance",
            is_futures=market == Market.CME,
            contract="CME" if market == Market.CME else None,
            warnings=warnings,
        )

    @staticmethod
    def _fetch_yfinance(ticker: str, spec: TimeframeSpec, market: Market, limit: int) -> pd.DataFrame:
        """Descarga con yfinance y unifica formato (incluye re-muestreo a 4h)."""
        if yf is None:
            raise DataFeedError("yfinance no está instalado")
        hist = yf.Ticker(ticker).history(
            period=spec.yf_period,
            interval=spec.yf_interval,
            auto_adjust=False,
            prepost=False,
            actions=False,
            raise_errors=True,
        )
        if hist is None or hist.empty:
            raise DataFeedError(f"Yahoo no devolvió datos para {ticker}")
        df = hist.rename(columns=str.lower)
        missing = {"open", "high", "low", "close"} - set(df.columns)
        if missing:
            raise DataFeedError(f"faltan columnas {missing}")
        if "volume" not in df.columns:
            df["volume"] = 0.0
        df = normalize_ohlcv(df)
        if spec.resample:
            df = resample_ohlcv(df, spec.resample, market)
        return df.tail(limit)

    # -------------------------------------------------------------- Demo
    @staticmethod
    def _get_demo(symbol: str, spec: TimeframeSpec, limit: int) -> MarketData:
        """Datos sintéticos claramente marcados como DEMO."""
        df, oi = generate_demo_data(spec.key, n=max(limit, 300), symbol=symbol)
        return MarketData(
            df=df.tail(limit),
            symbol=symbol,
            resolved_symbol=symbol,
            market=Market.DEMO,
            timeframe=spec.key,
            source="demo",
            is_futures=True,
            contract="USDT-M",
            open_interest=oi,
            funding_rate=0.0001,
            warnings=["MODO DEMO: datos SINTÉTICOS generados localmente. No operar con estas señales."],
        )

    # ---------------------------------------------------------- Helpers
    def _finalize(self, data: MarketData, spec: TimeframeSpec) -> MarketData:
        """Quita vela en curso, valida cantidad de velas y añade avisos."""
        df = data.df
        if self.settings.drop_incomplete_candle and data.source != "demo":
            df = drop_incomplete_candle(df, spec.delta)
        if len(df) < MIN_BARS:
            raise DataFeedError(
                f"{data.symbol}: solo {len(df)} velas disponibles en {spec.key}; se necesitan al menos {MIN_BARS}."
            )
        if len(df) < RELIABLE_BARS:
            data.warnings.append(f"Solo {len(df)} velas: la EMA 200 y la estructura pueden no ser fiables.")
        if float(df["volume"].tail(50).sum()) <= 0:
            data.warnings.append("La fuente no reporta volumen: VWAP, volumen y Volume Profile quedan neutrales.")
        data.df = df
        return data

    @staticmethod
    def _short_error(exc: Exception) -> str:
        """Mensaje de error corto y legible."""
        text = f"{type(exc).__name__}: {exc}"
        return text if len(text) <= 180 else text[:177] + "..."


# =============================================================================
# Consenso de TradingView (opcional, solo informativo)
# =============================================================================
def get_tradingview_consensus(symbol: str, market: Market | str, timeframe: str) -> dict | None:
    """
    Pide a TradingView su resumen técnico (STRONG_BUY ... STRONG_SELL).
    Requiere USE_TRADINGVIEW_TA=true en .env. No afecta al score: es una
    "segunda opinión" que se muestra en la UI. Devuelve None si no está disponible.
    """
    if not SETTINGS.use_tradingview_ta or TA_Handler is None:
        return None
    market = Market(market)
    intervals = {
        "1m": Interval.INTERVAL_1_MINUTE,
        "5m": Interval.INTERVAL_5_MINUTES,
        "15m": Interval.INTERVAL_15_MINUTES,
        "1h": Interval.INTERVAL_1_HOUR,
        "4h": Interval.INTERVAL_4_HOURS,
        "1D": Interval.INTERVAL_1_DAY,
    }
    interval = intervals.get(timeframe)
    if interval is None:
        return None

    if market in (Market.CRYPTO, Market.DEMO):
        if market == Market.DEMO:
            return None
        base, quote = parse_crypto_symbol(symbol)
        candidates = [("crypto", "BINANCE", f"{base}{quote}.P"), ("crypto", "BINANCE", f"{base}{quote}")]
    elif market in (Market.STOCK, Market.ETF):
        tk = symbol.upper()
        candidates = [("america", ex, tk) for ex in ("NASDAQ", "NYSE", "AMEX")]
    else:
        return None

    for screener, exchange, tv_symbol in candidates:
        try:
            handler = TA_Handler(symbol=tv_symbol, screener=screener, exchange=exchange,
                                 interval=interval, timeout=8)
            summary = handler.get_analysis().summary
            return {
                "symbol": f"{exchange}:{tv_symbol}",
                "recommendation": summary.get("RECOMMENDATION"),
                "buy": summary.get("BUY"),
                "sell": summary.get("SELL"),
                "neutral": summary.get("NEUTRAL"),
            }
        except Exception:  # noqa: BLE001 - probamos el siguiente candidato
            continue
    return None
