"""
engine/news_filter.py
=====================
Filtro de noticias de ALTO IMPACTO.

Regla: no operar `NEWS_WINDOW_MINUTES` (30 por defecto) antes ni después de una
noticia de alto impacto de las monedas configuradas (USD por defecto: el dólar
mueve cripto, índices y acciones de EE. UU.).

Fuentes (se combinan):
1. API gratuita del calendario de ForexFactory (JSON semanal, sin API key).
   Se guarda en caché en disco (data/news_cache.json) y se refresca como mucho
   una vez por hora para no ser bloqueados. Si falla, se usa la última caché.
2. Calendario MANUAL: data/news_calendar.json (añade FOMC, CPI, NFP, etc.).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from engine.config import DATA_DIR, SETTINGS, get_logger

log = get_logger("news_filter")

MANUAL_CALENDAR = DATA_DIR / "news_calendar.json"
CACHE_FILE = DATA_DIR / "news_cache.json"
CACHE_MAX_AGE_S = 3600


@dataclass(frozen=True)
class NewsEvent:
    """Evento económico."""

    time_utc: datetime
    title: str
    currency: str
    impact: str
    source: str  # "forexfactory" | "manual"

    def to_dict(self) -> dict:
        return {"time_utc": self.time_utc.isoformat(), "title": self.title, "currency": self.currency,
                "impact": self.impact, "source": self.source}


@dataclass
class NewsStatus:
    """Resultado de la comprobación del filtro."""

    blocked: bool
    event: NewsEvent | None = None
    minutes_to_event: float | None = None  # negativo = ya pasó
    message: str = ""


def _parse_dt(value: str) -> datetime | None:
    """Parsea fechas ISO (con 'Z' u offset) a UTC."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


class NewsFilter:
    """Descarga, combina y evalúa el calendario de noticias."""

    def __init__(
        self,
        enabled: bool = SETTINGS.news_filter_enabled,
        window_minutes: int = SETTINGS.news_window_minutes,
        currencies: list[str] | None = None,
        api_url: str = SETTINGS.news_api_url,
        manual_path: Path = MANUAL_CALENDAR,
        cache_path: Path = CACHE_FILE,
    ) -> None:
        self.enabled = enabled
        self.window = timedelta(minutes=window_minutes)
        self.currencies = [c.upper() for c in (currencies or SETTINGS.news_currencies)]
        self.api_url = api_url
        self.manual_path = Path(manual_path)
        self.cache_path = Path(cache_path)
        self._memory: tuple[float, list[NewsEvent]] | None = None
        self.last_error: str = ""

    # -------------------------------------------------------------- fuentes
    def _load_api(self) -> list[NewsEvent]:
        """Calendario de ForexFactory con caché en disco (1 h)."""
        raw: list = []
        cache_ok = False
        if self.cache_path.exists():
            try:
                cached = json.loads(self.cache_path.read_text(encoding="utf-8"))
                raw = cached.get("events", [])
                cache_ok = time.time() - float(cached.get("fetched_at", 0)) < CACHE_MAX_AGE_S
            except (OSError, ValueError):
                raw = []
        if not cache_ok and self.api_url:
            try:
                resp = requests.get(self.api_url, timeout=8, headers={"User-Agent": "futures-scope/1.0"})
                resp.raise_for_status()
                raw = resp.json()
                self.cache_path.write_text(json.dumps({"fetched_at": time.time(), "events": raw}), encoding="utf-8")
                self.last_error = ""
            except Exception as exc:  # noqa: BLE001 - usamos la caché antigua si existe
                self.last_error = f"Calendario online no disponible ({type(exc).__name__}); usando caché/manual."
                log.info(self.last_error)

        events = []
        for item in raw if isinstance(raw, list) else []:
            dt = _parse_dt(item.get("date", ""))
            if dt is None:
                continue
            events.append(NewsEvent(dt, str(item.get("title", "")), str(item.get("country", "")).upper(),
                                    str(item.get("impact", "")), "forexfactory"))
        return events

    def _load_manual(self) -> list[NewsEvent]:
        """Calendario manual editable por el usuario."""
        if not self.manual_path.exists():
            return []
        try:
            data = json.loads(self.manual_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("news_calendar.json inválido: %s", exc)
            return []
        events = []
        for item in data.get("events", []):
            dt = _parse_dt(item.get("datetime_utc", ""))
            if dt is None:
                continue
            events.append(NewsEvent(dt, str(item.get("title", "Evento manual")), str(item.get("currency", "USD")).upper(),
                                    str(item.get("impact", "High")), "manual"))
        return events

    def events(self) -> list[NewsEvent]:
        """Eventos de alto impacto de las monedas configuradas (caché en memoria 5 min)."""
        if self._memory and time.time() - self._memory[0] < 300:
            return self._memory[1]
        all_events = self._load_api() + self._load_manual()
        relevant = [
            e for e in all_events
            if e.impact.lower() == "high" and (e.currency in self.currencies or e.currency == "ALL")
        ]
        relevant.sort(key=lambda e: e.time_utc)
        self._memory = (time.time(), relevant)
        return relevant

    # ------------------------------------------------------------ consultas
    def check(self, now: datetime | None = None) -> NewsStatus:
        """¿Estamos dentro de la ventana de una noticia de alto impacto?"""
        if not self.enabled:
            return NewsStatus(False, message="Filtro de noticias desactivado")
        now = now or datetime.now(timezone.utc)
        try:
            for event in self.events():
                delta_min = (event.time_utc - now).total_seconds() / 60.0
                if abs(delta_min) <= self.window.total_seconds() / 60.0:
                    when = f"en {delta_min:.0f} min" if delta_min >= 0 else f"hace {abs(delta_min):.0f} min"
                    return NewsStatus(True, event, delta_min,
                                      f"Noticia de alto impacto {when}: {event.currency} · {event.title}")
        except Exception as exc:  # noqa: BLE001 - el filtro nunca debe romper el análisis
            log.error("Error en filtro de noticias: %s", exc)
            return NewsStatus(False, message="Filtro de noticias con error (no bloquea)")
        return NewsStatus(False, message="Sin noticias de alto impacto en la ventana")

    def upcoming(self, hours: int = 48, now: datetime | None = None) -> list[NewsEvent]:
        """Próximos eventos (para el panel de la UI)."""
        now = now or datetime.now(timezone.utc)
        limit = now + timedelta(hours=hours)
        try:
            return [e for e in self.events() if now - self.window <= e.time_utc <= limit]
        except Exception:  # noqa: BLE001
            return []
