"""
engine/context_analyzer.py
==========================
Análisis de CONTEXTO: económico, social, político y cultural.

El análisis técnico dice "dónde" entrar; el contexto ayuda a decidir si el viento
sopla a favor o en contra. Este módulo resume el entorno en un SESGO de -1 a +1
(+1 = entorno favorable para subir) y lo convierte en un ajuste PEQUEÑO y
transparente del score técnico (± CONTEXT_MAX_ADJUST puntos, 8 por defecto).

Pilares y fuentes (todas gratuitas, sin API key)
------------------------------------------------
* ECONÓMICO  -> Mercado: VIX, Índice Dólar (DXY), bono 10 años, S&P 500 (yfinance)
                Macro FRED: inflación (CPI), tipos de la Fed, desempleo, curva 10y-2y
                + titulares económicos (Fed, inflación, empleo, PIB...)
* SOCIAL     -> Crypto Fear & Greed Index (alternative.me), funding rate
                (posicionamiento de la masa) + titulares de sentimiento inversor
* POLÍTICO   -> Titulares: elecciones, aranceles, sanciones, guerras, regulación (SEC...)
* CULTURAL   -> Titulares: adopción masiva, tendencias, celebridades, memes, marcas
* ACTIVO     -> Titulares específicos del símbolo analizado

Los titulares vienen de Google News RSS y se puntúan con un léxico financiero
bilingüe (español/inglés) editable más abajo (BULLISH_TERMS / BEARISH_TERMS).

LIMITACIONES (importante): esto NO predice el mercado. El sentimiento por léxico
es aproximado (no entiende ironía ni negaciones complejas) y lo "cultural" solo se
puede medir de forma indirecta. Úsalo como contexto, nunca como señal única.
"""

from __future__ import annotations

import io
import math
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.parse import quote_plus

import numpy as np
import pandas as pd
import requests

from engine.config import SETTINGS, get_logger

try:
    import yfinance as yf  # type: ignore
except ImportError:  # pragma: no cover
    yf = None

log = get_logger("context")

HTTP_TIMEOUT = 6
MAX_RSS_BYTES = 2_000_000  # Límite de tamaño por feed (protección)
USER_AGENT = "Mozilla/5.0 (futures-scope context analyzer)"

# =============================================================================
# Léxicos (edítalos libremente). Frases primero: se buscan antes que palabras.
# =============================================================================
BULLISH_TERMS: dict[str, float] = {
    # Inglés
    "all-time high": 1.5, "record high": 1.3, "rate cut": 1.2, "cuts rates": 1.2, "inflation cools": 1.2,
    "inflation eases": 1.2, "etf approval": 1.5, "approved": 1.0, "approval": 1.0, "inflows": 1.0,
    "surge": 1.0, "surges": 1.0, "soar": 1.0, "soars": 1.0, "rally": 1.0, "rallies": 1.0, "jumps": 0.8,
    "gains": 0.6, "bullish": 1.0, "breakout": 0.8, "adoption": 0.8, "upgrade": 0.8, "beats": 0.8,
    "stimulus": 0.8, "easing": 0.6, "recovery": 0.7, "optimism": 0.8, "growth": 0.5, "partnership": 0.5,
    "ceasefire": 1.0, "peace deal": 1.0, "trade deal": 1.0, "accumulate": 0.7, "buying": 0.5,
    # Español
    "máximo histórico": 1.5, "maximo historico": 1.5, "récord": 1.0, "recorte de tasas": 1.2,
    "recorte de tipos": 1.2, "baja la inflación": 1.2, "inflación se modera": 1.2, "aprobación": 1.0,
    "aprueba": 1.0, "entradas de capital": 1.0, "se dispara": 1.0, "dispara": 0.9, "repunta": 0.8,
    "repunte": 0.8, "sube": 0.6, "subida": 0.6, "alcista": 1.0, "adopción": 0.8, "supera": 0.6,
    "estímulo": 0.8, "recuperación": 0.7, "optimismo": 0.8, "crecimiento": 0.5, "acuerdo comercial": 1.0,
    "alto el fuego": 1.0, "acuerdo de paz": 1.0, "compras": 0.4,
}
BEARISH_TERMS: dict[str, float] = {
    # Inglés
    "rate hike": 1.2, "hikes rates": 1.2, "inflation rises": 1.2, "hot inflation": 1.2, "sell-off": 1.0,
    "selloff": 1.0, "crash": 1.4, "crashes": 1.4, "plunge": 1.2, "plunges": 1.2, "tumble": 1.0,
    "tumbles": 1.0, "slump": 1.0, "drops": 0.6, "falls": 0.6, "bearish": 1.0, "ban": 1.0, "bans": 1.0,
    "hack": 1.2, "hacked": 1.2, "exploit": 1.0, "lawsuit": 0.9, "sues": 0.9, "fraud": 1.2,
    "bankruptcy": 1.4, "default": 1.0, "recession": 1.2, "tariff": 0.8, "tariffs": 0.8, "sanctions": 0.9,
    "war": 1.0, "invasion": 1.3, "attack": 0.9, "outflows": 1.0, "downgrade": 0.9, "misses": 0.8,
    "layoffs": 0.8, "crisis": 1.0, "liquidations": 0.8, "investigation": 0.7, "probe": 0.7,
    "shutdown": 0.8, "fear": 0.6, "panic": 1.0, "delisting": 1.0,
    # Español
    "alza de tasas": 1.2, "subida de tipos": 1.2, "sube la inflación": 1.2, "se desploma": 1.4,
    "desplome": 1.4, "desploma": 1.3, "se hunde": 1.2, "hunde": 1.0, "caída": 0.7, "cae": 0.6,
    "bajista": 1.0, "prohibición": 1.0, "prohíbe": 1.0, "hackeo": 1.2, "demanda judicial": 0.9,
    "fraude": 1.2, "quiebra": 1.4, "impago": 1.0, "recesión": 1.2, "aranceles": 0.8, "sanciones": 0.9,
    "guerra": 1.0, "invasión": 1.3, "ataque": 0.9, "salidas de capital": 1.0, "rebaja": 0.6,
    "despidos": 0.8, "crisis": 1.0, "liquidaciones": 0.8, "investigación": 0.6, "pánico": 1.0, "miedo": 0.6,
}
CATEGORY_TERMS: dict[str, tuple[str, ...]] = {
    "economic": (
        "fed", "federal reserve", "fomc", "powell", "inflation", "cpi", "pce", "gdp", "jobs", "payrolls",
        "unemployment", "interest rate", "rates", "yields", "treasury", "recession", "economy", "earnings",
        "reserva federal", "inflación", "ipc", "pib", "empleo", "desempleo", "tasas", "tipos de interés",
        "bonos", "recesión", "economía", "resultados", "bce", "banco central",
    ),
    "political": (
        "election", "president", "congress", "senate", "government", "regulation", "regulator", "sec",
        "cftc", "law", "bill", "tariff", "sanction", "war", "geopolit", "china", "russia", "ukraine",
        "israel", "iran", "white house", "trump", "policy", "elecciones", "presidente", "congreso",
        "senado", "gobierno", "regulación", "regulador", "ley", "arancel", "sancion", "guerra",
        "geopolít", "casa blanca", "política",
    ),
    "social": (
        "retail", "investors", "sentiment", "fear", "greed", "social media", "twitter", "reddit",
        "community", "viral", "influencer", "protest", "strike", "traders", "minoristas", "inversores",
        "sentimiento", "miedo", "codicia", "redes sociales", "comunidad", "protesta", "huelga",
    ),
    "cultural": (
        "adoption", "mainstream", "celebrity", "meme", "culture", "generation", "gen z", "millennial",
        "brand", "nft", "gaming", "fashion", "sports", "payments", "adopción", "masiva", "celebridad",
        "cultura", "generación", "marca", "deporte", "moda", "pagos", "tendencia",
    ),
}

CRYPTO_NAMES = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "BNB": "BNB Binance", "XRP": "XRP Ripple",
    "DOGE": "Dogecoin", "ADA": "Cardano", "AVAX": "Avalanche crypto", "LINK": "Chainlink", "SUI": "Sui crypto",
    "DOT": "Polkadot", "LTC": "Litecoin", "TON": "Toncoin", "TRX": "Tron crypto", "PEPE": "Pepe memecoin",
}
CME_NAMES = {
    "ES=F": "S&P 500 futures", "MES=F": "S&P 500 futures", "NQ=F": "Nasdaq futures", "MNQ=F": "Nasdaq futures",
    "YM=F": "Dow Jones futures", "RTY=F": "Russell 2000", "GC=F": "gold price", "MGC=F": "gold price",
    "SI=F": "silver price", "CL=F": "oil price", "MCL=F": "oil price", "NG=F": "natural gas price",
}
SAFE_HAVENS = {"GC=F", "MGC=F", "SI=F", "SIL=F", "ZN=F", "ZB=F", "GLD", "TLT", "IAU", "SLV"}

MACRO_TICKERS = {
    "^VIX": "VIX (miedo)",
    "DX-Y.NYB": "Índice Dólar",
    "^TNX": "Bono 10 años (%)",
    "^GSPC": "S&P 500",
    "GC=F": "Oro",
    "CL=F": "Petróleo WTI",
}
FRED_SERIES = {
    "CPIAUCSL": "Inflación (CPI)",
    "FEDFUNDS": "Tipos Fed",
    "UNRATE": "Desempleo",
    "T10Y2Y": "Curva 10y-2y",
}


# =============================================================================
# Estructuras
# =============================================================================
@dataclass
class Headline:
    """Titular de noticias puntuado."""

    title: str
    source: str
    published: str | None
    link: str
    sentiment: float  # -1 (muy negativo) .. +1 (muy positivo)
    categories: list[str]
    feed: str  # asset | macro | politics | social


@dataclass
class ContextPillar:
    """Puntuación de un pilar de contexto."""

    key: str
    name: str
    score: float  # -1 .. +1
    confidence: float  # 0 .. 1 (cuántos datos lo respaldan)
    summary: str
    details: list[str] = field(default_factory=list)


@dataclass
class ContextReport:
    """Informe completo de contexto."""

    bias: float  # -1 .. +1 ponderado
    confidence: float
    label: str
    pillars: dict[str, ContextPillar]
    headlines: list[Headline]
    macro: dict[str, dict]
    fred: dict[str, dict]
    fear_greed: dict | None
    funding_rate: float | None
    asset_profile: str  # "risk" | "safe_haven"
    generated_at: str
    errors: list[str] = field(default_factory=list)

    def adjustment_for(self, direction: int, max_points: float = SETTINGS.context_max_adjust) -> tuple[float, str]:
        """
        Puntos a sumar (+) o restar (-) al score técnico para una dirección.
        LONG con sesgo positivo suma; SHORT con sesgo positivo resta.
        """
        if direction == 0 or max_points <= 0 or self.confidence <= 0:
            return 0.0, "Contexto sin datos suficientes (no ajusta el score)"
        points = round(max_points * self.bias * direction * min(1.0, 0.5 + self.confidence / 2), 1)
        favor = "a favor" if points > 0 else ("en contra" if points < 0 else "neutral")
        return points, f"Contexto {self.label} ({self.bias:+.2f}) {favor}: {points:+.1f} pts"

    def to_dict(self) -> dict:
        return asdict(self)


def _label(bias: float) -> str:
    if bias >= 0.4:
        return "MUY FAVORABLE"
    if bias >= 0.15:
        return "FAVORABLE"
    if bias <= -0.4:
        return "MUY DESFAVORABLE"
    if bias <= -0.15:
        return "DESFAVORABLE"
    return "NEUTRAL"


# =============================================================================
# Puntuación de texto (léxico)
# =============================================================================
_PUNCT = re.compile(r"[^\w\s\-]", re.UNICODE)


def _term_regex(term: str) -> re.Pattern:
    """
    Regex con límites de palabra: los términos cortos (<= 4 letras) deben coincidir
    completos ("war" no coincide con "software"); los largos admiten sufijos
    ("arancel" coincide con "aranceles").
    """
    esc = re.escape(term.strip())
    return re.compile(rf"(?<!\w){esc}(?!\w)" if len(term.strip()) <= 4 else rf"(?<!\w){esc}")


# Precompilados, de más largo a más corto (las frases antes que sus palabras)
_SENTIMENT_RULES: list[tuple[re.Pattern, float]] = [
    (_term_regex(t), w) for t, w in sorted(BULLISH_TERMS.items(), key=lambda kv: -len(kv[0]))
] + [
    (_term_regex(t), -w) for t, w in sorted(BEARISH_TERMS.items(), key=lambda kv: -len(kv[0]))
]
_SENTIMENT_RULES.sort(key=lambda rule: -len(rule[0].pattern))
_CATEGORY_RULES: dict[str, list[re.Pattern]] = {
    cat: [_term_regex(t) for t in terms] for cat, terms in CATEGORY_TERMS.items()
}


def _normalize(text: str) -> str:
    return f" {_PUNCT.sub(' ', text.lower())} "


def score_text(text: str) -> float:
    """
    Sentimiento de un titular por léxico: (positivo - negativo) / total, en [-1, 1].
    Las frases se evalúan primero y se eliminan para no contar dos veces sus palabras.
    """
    t = _normalize(text)
    pos = neg = 0.0
    for pattern, weight in _SENTIMENT_RULES:
        hits = len(pattern.findall(t))
        if hits:
            if weight > 0:
                pos += weight * hits
            else:
                neg += -weight * hits
            t = pattern.sub(" ", t)
    total = pos + neg
    return float(np.clip((pos - neg) / total, -1, 1)) if total > 0 else 0.0


def categorize(text: str) -> list[str]:
    """Categorías del titular (económico, político, social, cultural)."""
    t = _normalize(text)
    return [cat for cat, rules in _CATEGORY_RULES.items() if any(r.search(t) for r in rules)]


# =============================================================================
# Analizador
# =============================================================================
class ContextAnalyzer:
    """
    Construye el informe de contexto con caché (CONTEXT_CACHE_MINUTES).

    `fetch_text` es inyectable (para tests sin internet): recibe una URL y devuelve texto.
    """

    def __init__(
        self,
        enabled: bool = SETTINGS.context_enabled,
        cache_minutes: int = SETTINGS.context_cache_minutes,
        lang: str = SETTINGS.context_news_lang,
        fetch_text: Callable[[str], str] | None = None,
        use_yfinance: bool = True,
    ) -> None:
        self.enabled = enabled
        self.cache_s = cache_minutes * 60
        self.lang = lang if lang in ("es", "en") else "es"
        self.fetch_text = fetch_text or self._http_get
        self.use_yfinance = use_yfinance and yf is not None
        self._cache: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------- caché
    def _cached(self, key: str, builder: Callable[[], object]) -> object:
        with self._lock:
            hit = self._cache.get(key)
        if hit and time.time() - hit[0] < self.cache_s:
            return hit[1]
        value = builder()
        with self._lock:
            self._cache[key] = (time.time(), value)
        return value

    @staticmethod
    def _http_get(url: str) -> str:
        resp = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}, stream=True)
        resp.raise_for_status()
        content = resp.raw.read(MAX_RSS_BYTES + 1, decode_content=True)
        if len(content) > MAX_RSS_BYTES:
            raise ValueError("respuesta demasiado grande")
        return content.decode(resp.encoding or "utf-8", errors="replace")

    # ----------------------------------------------------------- fuentes
    def _google_news(self, query: str, feed: str, limit: int = 15) -> list[Headline]:
        """Titulares de Google News RSS para una búsqueda."""
        if self.lang == "es":
            params = "hl=es-419&gl=US&ceid=US:es-419"
        else:
            params = "hl=en-US&gl=US&ceid=US:en"
        url = f"https://news.google.com/rss/search?q={quote_plus(query)}&{params}"
        root = ET.fromstring(self.fetch_text(url))
        out: list[Headline] = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            source = (item.findtext("source") or "").strip()
            pub = item.findtext("pubDate")
            try:
                published = parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat() if pub else None
            except (TypeError, ValueError):
                published = None
            cats = categorize(title)
            out.append(Headline(title, source, published, (item.findtext("link") or "").strip(),
                                round(score_text(title), 3), cats, feed))
            if len(out) >= limit:
                break
        return out

    def _macro_market(self) -> dict[str, dict]:
        """Variaciones de VIX, DXY, bono 10y, S&P 500, oro y petróleo."""
        if not self.use_yfinance:
            return {}

        def one(ticker: str) -> tuple[str, dict | None]:
            try:
                hist = yf.Ticker(ticker).history(period="6mo", interval="1d", auto_adjust=False, timeout=HTTP_TIMEOUT)
                close = hist["Close"].dropna()
                if len(close) < 25:
                    return ticker, None
                last = float(close.iloc[-1])
                return ticker, {
                    "name": MACRO_TICKERS[ticker],
                    "last": round(last, 4),
                    "chg_5d": round((last / float(close.iloc[-6]) - 1) * 100, 2),
                    "chg_20d": round((last / float(close.iloc[-21]) - 1) * 100, 2),
                    "above_sma50": bool(last > float(close.tail(50).mean())),
                }
            except Exception as exc:  # noqa: BLE001
                log.info("Macro %s no disponible: %s", ticker, exc)
                return ticker, None

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = dict(pool.map(one, MACRO_TICKERS))
        return {k: v for k, v in results.items() if v}

    def _fred(self) -> dict[str, dict]:
        """Series macro de la FRED (CSV público, sin API key)."""

        def one(series: str) -> tuple[str, dict | None]:
            try:
                text = self.fetch_text(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}")
                df = pd.read_csv(io.StringIO(text))
                df.columns = ["date", "value"]
                df["value"] = pd.to_numeric(df["value"], errors="coerce")
                values = df.dropna()["value"].to_numpy()
                if len(values) < 14:
                    return series, None
                info = {"name": FRED_SERIES[series], "last": round(float(values[-1]), 3)}
                if series == "CPIAUCSL":  # Inflación interanual y su tendencia
                    yoy = (values[-1] / values[-13] - 1) * 100
                    yoy_prev = (values[-4] / values[-16] - 1) * 100 if len(values) >= 16 else yoy
                    info.update({"yoy": round(yoy, 2), "trend": round(yoy - yoy_prev, 2)})
                else:  # Cambio frente a hace ~6 observaciones
                    info["trend"] = round(float(values[-1] - values[-7]), 3)
                return series, info
            except Exception as exc:  # noqa: BLE001
                log.info("FRED %s no disponible: %s", series, exc)
                return series, None

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = dict(pool.map(one, FRED_SERIES))
        return {k: v for k, v in results.items() if v}

    def _fear_greed(self) -> dict | None:
        """Crypto Fear & Greed Index (0 = pánico extremo, 100 = euforia)."""
        import json

        try:
            data = json.loads(self.fetch_text("https://api.alternative.me/fng/?limit=7"))
            rows = data.get("data", [])
            if not rows:
                return None
            values = [int(r["value"]) for r in rows]
            return {"value": values[0], "label": rows[0].get("value_classification", ""),
                    "avg_7d": round(float(np.mean(values)), 1)}
        except Exception as exc:  # noqa: BLE001
            log.info("Fear & Greed no disponible: %s", exc)
            return None

    # ------------------------------------------------------------ pilares
    @staticmethod
    def _avg_sentiment(headlines: list[Headline], category: str | None = None,
                       feed: str | None = None) -> tuple[float, int]:
        pool = [h for h in headlines if (category is None or category in h.categories)
                and (feed is None or h.feed == feed)]
        scored = [h.sentiment for h in pool if h.sentiment != 0]
        return (float(np.mean(scored)) if scored else 0.0), len(pool)

    def _pillar_economic(self, macro: dict, fred: dict, headlines: list[Headline]) -> ContextPillar:
        parts: list[float] = []
        details: list[str] = []
        vix = macro.get("^VIX")
        if vix:
            level = vix["last"]
            s = 0.5 if level < 15 else 0.1 if level < 20 else -0.3 if level < 25 else -0.7
            if vix["chg_5d"] > 15:
                s -= 0.2
            parts.append(s)
            details.append(f"VIX {level:.1f} ({vix['chg_5d']:+.1f}% 5d)")
        dxy = macro.get("DX-Y.NYB")
        if dxy:
            parts.append(float(np.clip(-dxy["chg_20d"] / 3.0, -0.6, 0.6)))
            details.append(f"Dólar {dxy['chg_20d']:+.1f}% 20d")
        tnx = macro.get("^TNX")
        if tnx:
            parts.append(float(np.clip(-tnx["chg_20d"] / 8.0, -0.5, 0.5)))
            details.append(f"Bono 10y {tnx['last']:.2f}% ({tnx['chg_20d']:+.1f}% 20d)")
        spx = macro.get("^GSPC")
        if spx:
            parts.append(0.3 if spx["above_sma50"] else -0.3)
            details.append(f"S&P 500 {'sobre' if spx['above_sma50'] else 'bajo'} su media de 50 días")
        cpi = fred.get("CPIAUCSL")
        if cpi:
            parts.append(float(np.clip(-cpi["trend"] / 0.5, -0.4, 0.4)))
            details.append(f"Inflación {cpi['yoy']:.1f}% interanual ({'bajando' if cpi['trend'] < 0 else 'subiendo'})")
        ff = fred.get("FEDFUNDS")
        if ff:
            parts.append(0.3 if ff["trend"] < -0.1 else (-0.3 if ff["trend"] > 0.1 else 0.0))
            details.append(f"Tipos Fed {ff['last']:.2f}% ({'recortando' if ff['trend'] < -0.1 else 'subiendo' if ff['trend'] > 0.1 else 'estables'})")
        curve = fred.get("T10Y2Y")
        if curve:
            parts.append(-0.15 if curve["last"] < 0 else 0.05)
            details.append(f"Curva 10y-2y {curve['last']:+.2f} ({'invertida' if curve['last'] < 0 else 'normal'})")
        unrate = fred.get("UNRATE")
        if unrate:
            parts.append(-0.25 if unrate["trend"] > 0.3 else 0.05)
            details.append(f"Desempleo {unrate['last']:.1f}%")
        news, n = self._avg_sentiment(headlines, "economic")
        if n >= 2:
            parts.append(news)
            details.append(f"Titulares económicos: {news:+.2f} ({n})")
        score = float(np.clip(np.mean(parts), -1, 1)) if parts else 0.0
        conf = min(1.0, len(parts) / 6.0)
        return ContextPillar("economic", "Económico", round(score, 3), round(conf, 2),
                             self._tone(score, "Entorno macro"), details)

    def _pillar_social(self, fng: dict | None, funding: float | None, headlines: list[Headline]) -> ContextPillar:
        parts: list[float] = []
        details: list[str] = []
        if fng:
            v = fng["value"]
            if v >= 80:
                s, note = 0.2, "euforia: riesgo de corrección"
            elif v <= 20:
                s, note = -0.2, "pánico: posibles rebotes contrarian"
            else:
                s, note = (v - 50) / 60.0, fng["label"]
            parts.append(s)
            details.append(f"Fear & Greed {v} ({note})")
        if funding is not None:
            # Funding alto = longs saturados (contrarian)
            s = float(np.clip(-(funding - 0.0001) / 0.0004, -0.6, 0.6))
            parts.append(s)
            details.append(f"Funding {funding * 100:.4f}% ({'longs saturados' if funding > 0.0003 else 'shorts saturados' if funding < -0.0001 else 'equilibrado'})")
        news, n = self._avg_sentiment(headlines, "social")
        if n >= 2:
            parts.append(news)
            details.append(f"Titulares de sentimiento: {news:+.2f} ({n})")
        score = float(np.clip(np.mean(parts), -1, 1)) if parts else 0.0
        return ContextPillar("social", "Social", round(score, 3), round(min(1.0, len(parts) / 2.5), 2),
                             self._tone(score, "Sentimiento de la masa"), details)

    def _pillar_from_news(self, key: str, name: str, category: str, headlines: list[Headline],
                          prefix: str = "Noticias") -> ContextPillar:
        score, n = self._avg_sentiment(headlines, category)
        pool = [h for h in headlines if category in h.categories]
        details = [f"{h.sentiment:+.2f} · {h.title[:110]}" for h in sorted(pool, key=lambda h: abs(h.sentiment), reverse=True)[:4]]
        return ContextPillar(key, name, round(float(np.clip(score, -1, 1)), 3), round(min(1.0, n / 6.0), 2),
                             self._tone(score, prefix) if n else "Sin titulares relevantes", details)

    def _pillar_asset(self, headlines: list[Headline]) -> ContextPillar:
        score, n = self._avg_sentiment(headlines, feed="asset")
        pool = [h for h in headlines if h.feed == "asset"]
        details = [f"{h.sentiment:+.2f} · {h.title[:110]}" for h in sorted(pool, key=lambda h: abs(h.sentiment), reverse=True)[:4]]
        return ContextPillar("asset", "Activo", round(score, 3), round(min(1.0, n / 8.0), 2),
                             self._tone(score, "Noticias del activo") if n else "Sin titulares del activo", details)

    @staticmethod
    def _tone(score: float, prefix: str) -> str:
        if score >= 0.3:
            return f"{prefix}: claramente positivo"
        if score >= 0.1:
            return f"{prefix}: ligeramente positivo"
        if score <= -0.3:
            return f"{prefix}: claramente negativo"
        if score <= -0.1:
            return f"{prefix}: ligeramente negativo"
        return f"{prefix}: neutral"

    # ---------------------------------------------------------------- API
    def asset_query(self, symbol: str, market: str) -> str:
        """Búsqueda de noticias para el activo."""
        sym = symbol.upper()
        if market == "crypto":
            base = sym.replace("-", "/").split("/")[0]
            return CRYPTO_NAMES.get(base, f"{base} crypto")
        if market == "cme":
            return CME_NAMES.get(sym, sym.replace("=F", " futures"))
        if market == "demo":
            return "Bitcoin"
        return f"{sym} stock"

    def analyze(self, symbol: str, market: str, funding_rate: float | None = None) -> ContextReport:
        """Informe de contexto completo (nunca lanza excepciones)."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        profile = "safe_haven" if symbol.upper() in SAFE_HAVENS else "risk"
        if not self.enabled:
            return ContextReport(0.0, 0.0, "DESACTIVADO", {}, [], {}, {}, None, funding_rate, profile, now,
                                 ["Análisis de contexto desactivado (CONTEXT_ENABLED=false)"])
        errors: list[str] = []
        is_crypto = market in ("crypto", "demo")
        es = self.lang == "es"
        queries = {
            "asset": self.asset_query(symbol, market),
            "macro": "Reserva Federal inflación economía EEUU" if es else "Federal Reserve inflation US economy",
            "politics": "aranceles sanciones elecciones regulación mercados" if es
            else "tariffs sanctions election regulation markets",
            "social": ("adopción cripto inversores minoristas tendencia" if is_crypto else "inversores minoristas sentimiento bolsa")
            if es else ("crypto adoption retail investors trend" if is_crypto else "retail investors sentiment stocks"),
        }

        def safe(name: str, fn: Callable[[], object], default: object) -> object:
            try:
                return self._cached(name, fn)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {type(exc).__name__}")
                # Recordamos el fallo 2 min para no esperar timeouts en cada recarga
                with self._lock:
                    self._cache[name] = (time.time() - self.cache_s + 120, default)
                return default

        with ThreadPoolExecutor(max_workers=7) as pool:
            fut_news = {feed: pool.submit(safe, f"news:{feed}:{q}", lambda q=q, feed=feed: self._google_news(q, feed), [])
                        for feed, q in queries.items()}
            fut_macro = pool.submit(safe, "macro", self._macro_market, {})
            fut_fred = pool.submit(safe, "fred", self._fred, {})
            fut_fng = pool.submit(safe, "fng", self._fear_greed, None) if is_crypto else None
            headlines: list[Headline] = []
            seen: set[str] = set()
            for fut in fut_news.values():
                for h in fut.result() or []:
                    if h.title not in seen:
                        seen.add(h.title)
                        headlines.append(h)
            macro = fut_macro.result() or {}
            fred = fut_fred.result() or {}
            fng = fut_fng.result() if fut_fng else None

        pillars = {
            "asset": self._pillar_asset(headlines),
            "economic": self._pillar_economic(macro, fred, headlines),
            "social": self._pillar_social(fng, funding_rate, headlines),
            "political": self._pillar_from_news("political", "Político", "political", headlines, "Noticias políticas"),
            "cultural": self._pillar_from_news("cultural", "Cultural", "cultural", headlines, "Tendencias culturales"),
        }
        weights = {"asset": 0.25, "economic": 0.30, "social": 0.20, "political": 0.15, "cultural": 0.10}
        num = den = 0.0
        for key, pillar in pillars.items():
            score = pillar.score
            # En refugios (oro, bonos) el miedo macro/político es POSITIVO para el activo
            if profile == "safe_haven" and key in ("economic", "political", "social"):
                score = -score
            num += weights[key] * pillar.confidence * score
            den += weights[key] * pillar.confidence
        bias = float(np.clip(num / den, -1, 1)) if den > 0 else 0.0
        confidence = float(min(1.0, den / sum(weights.values())))
        if not headlines and not macro and not fred:
            errors.append("Sin conexión a las fuentes de contexto: el ajuste será 0.")
        if math.isnan(bias):
            bias = 0.0
        return ContextReport(
            bias=round(bias, 3), confidence=round(confidence, 2), label=_label(bias), pillars=pillars,
            headlines=sorted(headlines, key=lambda h: h.published or "", reverse=True)[:40],
            macro=macro, fred=fred, fear_greed=fng, funding_rate=funding_rate, asset_profile=profile,
            generated_at=now, errors=errors,
        )
