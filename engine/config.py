"""
engine/config.py
================
Configuración central de FUTURES-SCOPE.

Lee las variables del archivo `.env` (vía python-dotenv) y las expone en un único
objeto inmutable `SETTINGS`. Si una variable no existe o tiene un valor inválido,
se usa un valor por defecto seguro: el bot NUNCA debe caerse por configuración.

Para cambiar un parámetro:
    1. Edita tu archivo .env (copia de .env.example), o
    2. Cambia el valor por defecto en `Settings.from_env()`.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# --- Marca / versión (cámbialo aquí y se actualiza en la app y el .exe) -------
APP_NAME = "FUTURES-SCOPE"
APP_VERSION = "1.0.0"
BUILD_TAG = "BUILD 1 · GUS X SHAWTY X ALFREDOBONUS"

# --- Rutas ---------------------------------------------------------------------
# BASE_DIR: donde está el código (en el .exe es la carpeta interna del paquete).
# APP_HOME: donde viven tus datos y tu .env (en el .exe, la carpeta del .exe).
IS_FROZEN: bool = bool(getattr(sys, "frozen", False))
BASE_DIR: Path = Path(__file__).resolve().parent.parent
if IS_FROZEN and sys.platform == "darwin":
    # En macOS la .app puede ejecutarse desde una ubicación de solo lectura (App Translocation):
    # los datos van a la carpeta estándar del usuario.
    APP_HOME: Path = Path.home() / "Library" / "Application Support" / "FuturesScope"
elif IS_FROZEN:
    APP_HOME = Path(sys.executable).resolve().parent  # Windows: junto al .exe
else:
    APP_HOME = BASE_DIR
APP_HOME.mkdir(parents=True, exist_ok=True)
DATA_DIR: Path = APP_HOME / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# En el .exe copiamos los archivos editables por defecto la primera vez
if IS_FROZEN:
    for _name in ("news_calendar.json",):
        _src, _dst = BASE_DIR / "data" / _name, DATA_DIR / _name
        if _src.exists() and not _dst.exists():
            shutil.copy(_src, _dst)
    if not (APP_HOME / ".env").exists() and (BASE_DIR / ".env.example").exists():
        shutil.copy(BASE_DIR / ".env.example", APP_HOME / ".env")

# Cargamos el .env (si no existe, simplemente no hace nada)
load_dotenv(APP_HOME / ".env")


# -----------------------------------------------------------------------------
# Helpers de lectura tolerantes a errores
# -----------------------------------------------------------------------------
def _env_str(name: str, default: str = "") -> str:
    """Devuelve la variable de entorno como texto (sin espacios) o el default."""
    value = os.getenv(name)
    return value.strip() if value is not None and value.strip() != "" else default


def _env_float(name: str, default: float) -> float:
    """Devuelve la variable como float; si no es un número válido, el default."""
    try:
        return float(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    """Devuelve la variable como int; si no es válida, el default."""
    try:
        return int(float(_env_str(name, str(default))))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    """Interpreta true/false, 1/0, yes/no, si/no."""
    raw = _env_str(name, str(default)).lower()
    return raw in {"1", "true", "yes", "si", "sí", "on"}


def _env_list(name: str, default: list[str]) -> list[str]:
    """Lista separada por comas -> ['a', 'b']."""
    raw = _env_str(name, "")
    if not raw:
        return list(default)
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


# -----------------------------------------------------------------------------
# Objeto de configuración
# -----------------------------------------------------------------------------
@dataclass(frozen=True)
class Settings:
    """Todos los parámetros ajustables del bot en un solo lugar."""

    # Exchanges / datos
    exchange_priority: list[str] = field(default_factory=lambda: ["binance", "bybit", "okx"])
    api_keys: dict[str, dict[str, str]] = field(default_factory=dict)
    ohlcv_limit: int = 500
    data_cache_ttl: int = 20
    request_timeout_ms: int = 15000
    drop_incomplete_candle: bool = True

    # Indicadores
    indicator_backend: str = "auto"

    # Scoring
    score_valid: float = 70.0
    score_alert: float = 85.0
    track_min_score: float = 70.0
    signal_expiry_bars: int = 96

    # Riesgo
    account_equity: float = 1000.0
    risk_per_trade_pct: float = 1.0
    default_leverage: int = 5
    atr_sl_multiplier: float = 1.5
    tp1_rr: float = 2.0
    tp2_rr: float = 3.0
    kill_switch_losses: int = 3
    taker_fee_pct: float = 0.05

    # Noticias
    news_filter_enabled: bool = True
    news_window_minutes: int = 30
    news_currencies: list[str] = field(default_factory=lambda: ["USD"])
    news_api_url: str = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

    # Contexto macro / social / político / cultural
    context_enabled: bool = True
    context_max_adjust: float = 8.0
    context_cache_minutes: int = 15
    context_news_lang: str = "es"

    # Alertas
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    alert_webhook_url: str = ""

    # Extras
    use_tradingview_ta: bool = False
    log_level: str = "INFO"
    db_path: Path = DATA_DIR / "futures_scope.db"

    @classmethod
    def from_env(cls) -> "Settings":
        """Construye la configuración leyendo el entorno (.env)."""
        db_raw = _env_str("DB_PATH", "data/futures_scope.db")
        db_path = Path(db_raw)
        if not db_path.is_absolute():
            db_path = APP_HOME / db_path

        api_keys = {
            "binance": {"apiKey": _env_str("BINANCE_API_KEY"), "secret": _env_str("BINANCE_API_SECRET")},
            "bybit": {"apiKey": _env_str("BYBIT_API_KEY"), "secret": _env_str("BYBIT_API_SECRET")},
            "okx": {
                "apiKey": _env_str("OKX_API_KEY"),
                "secret": _env_str("OKX_API_SECRET"),
                "password": _env_str("OKX_API_PASSWORD"),
            },
        }

        return cls(
            exchange_priority=_env_list("EXCHANGE_PRIORITY", ["binance", "bybit", "okx"]),
            api_keys=api_keys,
            ohlcv_limit=max(150, _env_int("OHLCV_LIMIT", 500)),
            data_cache_ttl=max(0, _env_int("DATA_CACHE_TTL_SECONDS", 20)),
            request_timeout_ms=_env_int("REQUEST_TIMEOUT_MS", 15000),
            drop_incomplete_candle=_env_bool("DROP_INCOMPLETE_CANDLE", True),
            indicator_backend=_env_str("INDICATOR_BACKEND", "auto").lower(),
            score_valid=_env_float("SCORE_VALID", 70.0),
            score_alert=_env_float("SCORE_ALERT", 85.0),
            track_min_score=_env_float("TRACK_MIN_SCORE", 70.0),
            signal_expiry_bars=_env_int("SIGNAL_EXPIRY_BARS", 96),
            account_equity=_env_float("ACCOUNT_EQUITY", 1000.0),
            risk_per_trade_pct=_env_float("RISK_PER_TRADE_PCT", 1.0),
            default_leverage=max(1, _env_int("DEFAULT_LEVERAGE", 5)),
            atr_sl_multiplier=_env_float("ATR_SL_MULTIPLIER", 1.5),
            tp1_rr=_env_float("TP1_RR", 2.0),
            tp2_rr=_env_float("TP2_RR", 3.0),
            kill_switch_losses=max(1, _env_int("KILL_SWITCH_LOSSES", 3)),
            taker_fee_pct=_env_float("TAKER_FEE_PCT", 0.05),
            news_filter_enabled=_env_bool("NEWS_FILTER_ENABLED", True),
            news_window_minutes=_env_int("NEWS_WINDOW_MINUTES", 30),
            news_currencies=[c.upper() for c in _env_list("NEWS_CURRENCIES", ["USD"])],
            news_api_url=_env_str("NEWS_API_URL", "https://nfs.faireconomy.media/ff_calendar_thisweek.json"),
            context_enabled=_env_bool("CONTEXT_ENABLED", True),
            context_max_adjust=max(0.0, _env_float("CONTEXT_MAX_ADJUST", 8.0)),
            context_cache_minutes=max(1, _env_int("CONTEXT_CACHE_MINUTES", 15)),
            context_news_lang=_env_str("CONTEXT_NEWS_LANG", "es").lower(),
            telegram_bot_token=_env_str("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_env_str("TELEGRAM_CHAT_ID"),
            alert_webhook_url=_env_str("ALERT_WEBHOOK_URL"),
            use_tradingview_ta=_env_bool("USE_TRADINGVIEW_TA", False),
            log_level=_env_str("LOG_LEVEL", "INFO").upper(),
            db_path=db_path,
        )


# Instancia global (importa SETTINGS desde cualquier módulo)
SETTINGS: Settings = Settings.from_env()

# Logging básico común a todo el proyecto
logging.basicConfig(
    level=getattr(logging, SETTINGS.log_level, logging.INFO),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)


def get_logger(name: str) -> logging.Logger:
    """Devuelve un logger con el prefijo del proyecto."""
    return logging.getLogger(f"futures_scope.{name}")
