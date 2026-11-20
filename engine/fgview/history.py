"""
engine/fgview/history.py
========================
HistoryStore: velas largas (hasta 90 días de 1m y 5m) con caché en disco.

La primera descarga de 90 días de 1 minuto puede tardar ~1 minuto (130 llamadas al
exchange); después solo se bajan las velas nuevas y es casi instantáneo.
Los archivos viven en data/history/ y se pueden borrar sin problema.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pandas as pd

from engine.config import DATA_DIR, get_logger
from engine.data_feeder import (
    DataFeeder,
    DataFeedError,
    crypto_to_yahoo,
    generate_demo_data,
    normalize_ohlcv,
    to_ccxt_symbol,
)

log = get_logger("fgview.history")

TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}


class HistoryStore:
    def __init__(self, feeder: DataFeeder, folder: Path = DATA_DIR / "history", max_calls: int = 160) -> None:
        self.feeder = feeder
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        self.max_calls = max_calls
        self._lock = threading.Lock()
        self._mem: dict[tuple, tuple[float, pd.DataFrame]] = {}

    # ------------------------------------------------------------------ API
    def get(self, symbol: str, market: str, timeframe: str = "1m", days: int = 90, exchange: str | None = None,
            contract: str = "USDT-M", ttl: float = 60.0) -> tuple[pd.DataFrame, str]:
        """(velas, fuente). Nunca más de `days` días."""
        if timeframe not in TF_MINUTES:
            raise ValueError(f"Temporalidad no soportada: {timeframe}")
        key = (symbol.upper(), market, timeframe, days, exchange, contract)
        with self._lock:
            hit = self._mem.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1], "caché"
        if market == "demo":
            n = int(days * 1440 / TF_MINUTES[timeframe])
            df, _ = generate_demo_data(timeframe, n=min(n, 140_000), symbol=symbol)
            out, src = df, "demo"
        elif market == "crypto":
            out, src = self._crypto(symbol, timeframe, days, exchange, contract)
        else:
            raise DataFeedError("El historial largo solo está disponible para cripto y demo.")
        with self._lock:
            self._mem[key] = (time.time(), out)
        return out, src

    # -------------------------------------------------------------- interno
    def _path(self, ex_id: str, symbol: str, timeframe: str) -> Path:
        safe = symbol.replace("/", "_").replace(":", "_")
        return self.folder / f"{ex_id}_{safe}_{timeframe}.pkl"

    def _crypto(self, symbol: str, timeframe: str, days: int, preferred: str | None,
                contract: str) -> tuple[pd.DataFrame, str]:
        ccxt_symbol = to_ccxt_symbol(symbol, contract)
        order = [e for e in ([preferred] if preferred else []) + list(self.feeder.settings.exchange_priority) if e]
        errors = []
        for ex_id in dict.fromkeys(order):
            try:
                ex = self.feeder._get_exchange(ex_id)  # noqa: SLF001 - reutiliza la conexión compartida
                return self._update(ex, ex_id, ccxt_symbol, timeframe, days), ex_id
            except Exception as exc:  # noqa: BLE001 - probamos el siguiente exchange
                errors.append(f"{ex_id}: {str(exc)[:120]}")
                log.warning("Historial %s %s falló en %s: %s", symbol, timeframe, ex_id, exc)
        # Último recurso: Yahoo (1m solo 7 días, 5m 60 días)
        try:
            import yfinance as yf

            period = "7d" if timeframe == "1m" else "60d"
            raw = yf.Ticker(crypto_to_yahoo(symbol)).history(period=period, interval=timeframe)
            if raw is None or raw.empty:
                raise DataFeedError("sin datos")
            return normalize_ohlcv(raw), "yfinance"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"yfinance: {exc}")
        raise DataFeedError("No se pudo descargar el historial:\n- " + "\n- ".join(errors))

    def _update(self, ex, ex_id: str, ccxt_symbol: str, timeframe: str, days: int) -> pd.DataFrame:
        path = self._path(ex_id, ccxt_symbol, timeframe)
        tf_ms = TF_MINUTES[timeframe] * 60_000
        now = ex.milliseconds()
        start = now - days * 86_400_000
        cached = None
        if path.exists():
            try:
                cached = pd.read_pickle(path)
            except Exception:  # noqa: BLE001 - archivo dañado: se vuelve a bajar
                cached = None
        since = start
        if cached is not None and len(cached):
            since = max(start, int(cached.index[-1].value // 1_000_000) + tf_ms)
        batch_size = {"binance": 1500, "bybit": 1000, "okx": 100}.get(ex_id, 500)
        rows: list[list[float]] = []
        for _ in range(self.max_calls):
            if since >= now - tf_ms:
                break
            batch = ex.fetch_ohlcv(ccxt_symbol, timeframe, since=since, limit=batch_size)
            if not batch:
                break
            rows.extend(batch)
            nxt = int(batch[-1][0]) + tf_ms
            if nxt <= since:
                break
            since = nxt
        frames = [cached] if cached is not None else []
        if rows:
            df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).drop_duplicates("ts")
            df.index = pd.to_datetime(df.pop("ts"), unit="ms", utc=True)
            frames.append(normalize_ohlcv(df))
        if not frames:
            raise DataFeedError("el exchange no devolvió velas")
        out = pd.concat(frames)
        out = out[~out.index.duplicated(keep="last")].sort_index()
        out = out[out.index >= pd.Timestamp(start, unit="ms", tz="UTC")]
        # La última vela puede estar a medias: no se guarda ni se usa
        out = out[out.index <= pd.Timestamp(now - tf_ms, unit="ms", tz="UTC")]
        try:
            out.to_pickle(path)
        except Exception as exc:  # noqa: BLE001 - la caché en disco es opcional
            log.warning("No se pudo guardar la caché %s: %s", path, exc)
        return out
