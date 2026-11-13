"""
engine/database.py
==================
Persistencia local en SQLite (archivo data/futures_scope.db).

Tablas
------
* signals    -> cada señal >= TRACK_MIN_SCORE con sus niveles y su resultado final
                (OPEN, WIN, LOSS, EXPIRED, MANUAL_WIN, MANUAL_LOSS)
* bot_state  -> estado clave/valor (kill switch, fecha de reinicio...)
* backtests  -> resumen de cada backtest ejecutado

Se abre una conexión nueva por operación: es seguro con los hilos de Streamlit
y de FastAPI. Puedes abrir el .db con "DB Browser for SQLite" para explorarlo.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from engine.config import SETTINGS, get_logger

log = get_logger("database")

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,          -- cuándo se registró (UTC ISO)
    bar_time      TEXT NOT NULL,          -- vela que generó la señal (UTC ISO)
    symbol        TEXT NOT NULL,
    market        TEXT,
    timeframe     TEXT NOT NULL,
    source        TEXT,
    strategy      TEXT,
    direction     TEXT NOT NULL,          -- LONG | SHORT
    score         REAL,
    grade         TEXT,
    entry         REAL,
    stop_loss     REAL,
    tp1           REAL,
    tp2           REAL,
    position_size REAL,
    leverage      REAL,
    reasons       TEXT,
    status        TEXT NOT NULL DEFAULT 'OPEN',
    result_r      REAL,                   -- resultado en múltiplos de R (riesgo)
    exit_price    REAL,
    closed_at     TEXT,
    notes         TEXT,
    UNIQUE(symbol, timeframe, direction, bar_time, strategy)
);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol, timeframe);

CREATE TABLE IF NOT EXISTS bot_state (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS backtests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    symbol        TEXT,
    market        TEXT,
    timeframe     TEXT,
    strategy      TEXT,
    params        TEXT,
    trades        INTEGER,
    win_rate      REAL,
    profit_factor REAL,
    total_r       REAL,
    max_dd_pct    REAL,
    final_equity  REAL
);
"""

CLOSED_STATUSES = ("WIN", "LOSS", "MANUAL_WIN", "MANUAL_LOSS")


def utc_now_iso() -> str:
    """Fecha/hora actual en UTC en formato ISO."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """Acceso sencillo a la base de datos SQLite del bot."""

    def __init__(self, path: Path | str = SETTINGS.db_path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ---------------------------------------------------------- conexión
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.executescript(SCHEMA)

    def _execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with closing(self._connect()) as conn, conn:
            return conn.execute(sql, params)

    def _query(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with closing(self._connect()) as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    # ----------------------------------------------------------- señales
    def save_signal(self, record: dict[str, Any]) -> int | None:
        """
        Guarda una señal. Devuelve su id, o None si ya existía (misma vela,
        símbolo, temporalidad, dirección y estrategia) -> evita duplicados.
        """
        fields = ("bar_time", "symbol", "market", "timeframe", "source", "strategy", "direction", "score",
                  "grade", "entry", "stop_loss", "tp1", "tp2", "position_size", "leverage", "reasons")
        data = {k: record.get(k) for k in fields}
        data["created_at"] = utc_now_iso()
        cols = ", ".join(data)
        marks = ", ".join(f":{k}" for k in data)
        try:
            cur = self._execute(f"INSERT OR IGNORE INTO signals ({cols}) VALUES ({marks})", data)
            return int(cur.lastrowid) if cur.rowcount else None
        except sqlite3.Error as exc:
            log.error("No se pudo guardar la señal: %s", exc)
            return None

    def open_signals(self, symbol: str | None = None, timeframe: str | None = None) -> list[dict]:
        """Señales abiertas (opcionalmente filtradas)."""
        sql, params = "SELECT * FROM signals WHERE status = 'OPEN'", []
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        if timeframe:
            sql += " AND timeframe = ?"
            params.append(timeframe)
        return self._query(sql + " ORDER BY bar_time", tuple(params))

    def close_signal(self, signal_id: int, status: str, exit_price: float | None = None,
                     result_r: float | None = None, notes: str = "") -> None:
        """Cierra una señal con su resultado."""
        self._execute(
            "UPDATE signals SET status = ?, exit_price = ?, result_r = ?, closed_at = ?, notes = ? WHERE id = ?",
            (status, exit_price, result_r, utc_now_iso(), notes, int(signal_id)),
        )

    def recent_signals(self, limit: int = 50) -> pd.DataFrame:
        """Últimas señales como DataFrame (para la tabla de historial)."""
        rows = self._query("SELECT * FROM signals ORDER BY id DESC LIMIT ?", (int(limit),))
        return pd.DataFrame(rows)

    def closed_signals(self, since_iso: str | None = None) -> list[dict]:
        """Señales cerradas (WIN/LOSS) en orden cronológico de cierre."""
        marks = ",".join("?" for _ in CLOSED_STATUSES)
        sql = f"SELECT * FROM signals WHERE status IN ({marks})"
        params: list[Any] = list(CLOSED_STATUSES)
        if since_iso:
            sql += " AND closed_at > ?"
            params.append(since_iso)
        return self._query(sql + " ORDER BY closed_at, id", tuple(params))

    def stats(self) -> dict[str, Any]:
        """Estadísticas globales de las señales registradas."""
        rows = self._query("SELECT status, result_r FROM signals")
        closed = [r for r in rows if r["status"] in CLOSED_STATUSES]
        wins = [r for r in closed if r["status"] in ("WIN", "MANUAL_WIN")]
        total_r = sum((r["result_r"] or 0.0) for r in closed)
        return {
            "total": len(rows),
            "open": sum(1 for r in rows if r["status"] == "OPEN"),
            "closed": len(closed),
            "wins": len(wins),
            "losses": len(closed) - len(wins),
            "win_rate": (len(wins) / len(closed) * 100.0) if closed else 0.0,
            "total_r": round(total_r, 2),
        }

    # ------------------------------------------------------------- estado
    def get_state(self, key: str, default: Any = None) -> Any:
        """Lee un valor del estado (JSON)."""
        rows = self._query("SELECT value FROM bot_state WHERE key = ?", (key,))
        if not rows:
            return default
        try:
            return json.loads(rows[0]["value"])
        except (TypeError, ValueError):
            return rows[0]["value"]

    def set_state(self, key: str, value: Any) -> None:
        """Guarda un valor en el estado (JSON)."""
        self._execute(
            "INSERT INTO bot_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(value), utc_now_iso()),
        )

    # ----------------------------------------------------------- backtests
    def save_backtest(self, record: dict[str, Any]) -> int:
        """Guarda el resumen de un backtest."""
        data = {
            "created_at": utc_now_iso(),
            "symbol": record.get("symbol"),
            "market": record.get("market"),
            "timeframe": record.get("timeframe"),
            "strategy": record.get("strategy"),
            "params": json.dumps(record.get("params", {}), default=str),
            "trades": record.get("trades"),
            "win_rate": record.get("win_rate"),
            "profit_factor": record.get("profit_factor"),
            "total_r": record.get("total_r"),
            "max_dd_pct": record.get("max_dd_pct"),
            "final_equity": record.get("final_equity"),
        }
        cols = ", ".join(data)
        marks = ", ".join(f":{k}" for k in data)
        return int(self._execute(f"INSERT INTO backtests ({cols}) VALUES ({marks})", data).lastrowid)

    def recent_backtests(self, limit: int = 20) -> pd.DataFrame:
        """Últimos backtests guardados."""
        return pd.DataFrame(self._query("SELECT * FROM backtests ORDER BY id DESC LIMIT ?", (int(limit),)))
