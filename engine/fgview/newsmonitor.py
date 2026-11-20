"""
engine/fgview/newsmonitor.py
============================
NEWS MONITOR (Módulo 9): calendario económico + noticias cripto + anuncios de exchanges.

Cada noticia se clasifica por:
* Impacto: alto / medio / bajo (palabras clave: hackeo, ETF, FOMC, CPI, deslistado…)
* Sentimiento: alcista / bajista / neutro (puntuación de texto del analizador de contexto)

Impacto alto -> bloquea entradas 30 min antes y después (calendario) o 30 min después
de publicada (noticias). Si no hay internet, el monitor sigue funcionando con lo que tenga.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from engine.context_analyzer import ContextAnalyzer, score_text
from engine.news_filter import NewsFilter

QUERIES = {
    "cripto": ("crypto hack OR exploit OR stolen", "token unlock crypto", "bitcoin ETF OR ether ETF flows",
               "crypto listing OR delisting"),
    "exchanges": ("Binance announcement", "Bybit announcement", "Coinbase listing"),
}
HIGH = ("hack", "exploit", "stolen", "robo", "hackeo", "sec ", "lawsuit", "demanda", "etf approv", "etf reject",
        "aprueba", "rechaza", "delist", "deslist", "fomc", "fed ", "cpi", "inflation", "inflación", "nfp", "payroll",
        "nóminas", "bankrupt", "quiebra", "halt", "suspend", "liquidation cascade", "ban ")
MEDIUM = ("listing", "lista", "unlock", "desbloqueo", "partnership", "alianza", "upgrade", "actualización", "launch",
          "lanza", "etf", "burn", "quema", "airdrop", "whale", "ballena", "outflow", "inflow")


@dataclass
class NewsItem:
    when: datetime | None
    title: str
    source: str
    category: str  # calendario | cripto | exchanges
    impact: str  # alto | medio | bajo
    sentiment: str  # alcista | bajista | neutro
    link: str = ""

    @property
    def icon(self) -> str:
        return {"alto": "🔴", "medio": "🟠", "bajo": "⚪"}[self.impact]


class NewsMonitor:
    def __init__(self, news: NewsFilter | None, analyzer: ContextAnalyzer | None = None, window_min: int = 30) -> None:
        self.news = news
        self.analyzer = analyzer or ContextAnalyzer(enabled=True, cache_minutes=10)
        self.window = timedelta(minutes=window_min)

    @staticmethod
    def classify(title: str) -> tuple[str, str]:
        t = f" {title.lower()} "
        impact = "alto" if any(k in t for k in HIGH) else ("medio" if any(k in t for k in MEDIUM) else "bajo")
        s = score_text(title)
        sentiment = "alcista" if s > 0.15 else ("bajista" if s < -0.15 else "neutro")
        return impact, sentiment

    def calendar(self, hours: int = 48) -> list[NewsItem]:
        if self.news is None:
            return []
        out = []
        for e in self.news.upcoming(hours):
            imp = {"high": "alto", "medium": "medio"}.get(str(e.impact).lower(), "bajo")
            out.append(NewsItem(e.time_utc, f"{e.currency} · {e.title}", e.source, "calendario", imp, "neutro"))
        return out

    def headlines(self, limit: int = 8) -> list[NewsItem]:
        jobs = [(cat, q) for cat, qs in QUERIES.items() for q in qs]

        def one(job):
            cat, q = job
            try:
                return [(cat, h) for h in self.analyzer._cached(f"nm:{q}", lambda: self.analyzer._google_news(q, cat, limit))]  # noqa: SLF001
            except Exception:  # noqa: BLE001 - sin internet o feed caído: se ignora esa búsqueda
                return []

        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = [r for part in pool.map(one, jobs) for r in part]
        seen, out = set(), []
        for cat, h in rows:
            key = re.sub(r"\W+", "", h.title.lower())[:80]
            if key in seen:
                continue
            seen.add(key)
            impact, sentiment = self.classify(h.title)
            when = datetime.fromisoformat(h.published) if h.published else None
            out.append(NewsItem(when, h.title, h.source, cat, impact, sentiment, h.link))
        out.sort(key=lambda n: n.when or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return out

    def all(self) -> list[NewsItem]:
        return self.calendar() + self.headlines()

    def blocks(self, items: list[NewsItem] | None = None, now: datetime | None = None) -> list[str]:
        """Mensajes de bloqueo por noticias de impacto alto dentro de la ventana de 30 min."""
        now = now or datetime.now(timezone.utc)
        items = self.all() if items is None else items
        out = []
        for n in items:
            if n.impact != "alto" or n.when is None:
                continue
            delta = n.when - now
            if n.category == "calendario" and abs(delta) <= self.window:
                out.append(f"Noticia de alto impacto {'en' if delta.total_seconds() > 0 else 'hace'} "
                           f"{abs(delta.total_seconds()) / 60:.0f} min: {n.title}")
            elif n.category != "calendario" and timedelta(0) <= -delta <= self.window:
                out.append(f"Noticia de alto impacto hace {-delta.total_seconds() / 60:.0f} min: {n.title[:90]}")
        return out
