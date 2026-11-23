"""
engine/fgview/desk.py
=====================
FG DESK: mesa de 6 agentes para operaciones CORTAS en futuros (15–30 min).

Cada decisión pasa por una cadena; si un agente bloquea, no se opera:

    🔭 SPOTTER  lee el mercado: 6 filtros (ponderados por la rotación de señales), libro de
                órdenes (desequilibrio compradores/vendedores) y bandas Z del VWAP.
    📚 PRIOR    probabilidad base: cómo le fue a ESTE plan en situaciones parecidas (90 días).
    🔮 PRICER   Time Machine (100,000 caminos) + calibración -> probabilidad real de TP1.
    ⚖️ EDGE     ventaja neta (después de comisiones) en R + guardias: squeeze, long/short ratio,
                noticias, stop diario, anti-revancha, correlación, liquidación.
    📐 KELLY    tamaño: ¼ de Kelly de la distribución simulada, nunca más que la tabla de capital.
    🎯 TAKER    prepara la orden (post-only, SL obligatorio, trailing) y la envía si el piloto está activo.
    🧹 CLOSER   gestiona lo abierto: escudo anti-squeeze, trailing y cierres de papel.

PRECISIÓN: el Calibrator compara lo que la mesa predijo con lo que pasó de verdad en las
últimas semanas (sin mirar el futuro) y corrige la probabilidad. Ninguna probabilidad es una
garantía: es la mejor estimación con datos reales.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from engine.fgview.config import DEFAULT_CONFIG, FGConfig
from engine.fgview.manager import TradeManager
from engine.fgview.plan import Plan
from engine.fgview.timemachine import TimeMachine, TimeMachineResult
from engine.position_simulator import fmt_pct, fmt_px, fmt_usd

AGENTS = ("SPOTTER", "PRIOR", "PRICER", "EDGE", "KELLY", "TAKER", "CLOSER")
AGENT_META = {
    "SPOTTER": ("🔭", "#3DDC97", "Lee el mercado"),
    "PRIOR": ("📚", "#F5A623", "Probabilidad base"),
    "PRICER": ("🔮", "#B388FF", "Time Machine"),
    "EDGE": ("⚖️", "#FF5C8A", "Ventaja neta + guardias"),
    "KELLY": ("📐", "#5AC8FA", "Tamaño óptimo"),
    "TAKER": ("🎯", "#F2D27A", "Ejecución"),
    "CLOSER": ("🧹", "#FF8A3D", "Gestión y cierre"),
}


@dataclass
class AgentResult:
    agent: str
    status: str  # pass | block | info
    headline: str
    detail: str = ""
    value: float | None = None
    ms: float = 0.0
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%H:%M:%S"))

    @property
    def icon(self) -> str:
        return AGENT_META[self.agent][0]

    @property
    def color(self) -> str:
        return AGENT_META[self.agent][1]


@dataclass
class DeskDecision:
    symbol: str
    direction: str | None
    results: list[AgentResult]
    plan: Plan | None = None
    tm: TimeMachineResult | None = None
    probability: float | None = None  # P(TP1) calibrada (%)
    edge_r: float | None = None
    risk_pct: float | None = None
    ticket: object | None = None
    fan: dict | None = None
    rotation: list[dict] = field(default_factory=list)
    calibration: dict | None = None
    sent: bool = False
    tape: dict | None = None  # Velas, VWAP y bandas Z para «THE TAPE»
    ridges: list = field(default_factory=list)  # Señales en el tiempo («SIGNAL RIDGES»)
    raw: dict = field(default_factory=dict)  # Valores crudos para el radar

    @property
    def radar(self) -> list[dict]:
        """8 ejes 0–100 con lo que la mesa midió (los que aún no se calcularon no aparecen)."""
        r, out = self.raw, []

        def add(name, value, good):
            out.append({"name": name, "value": float(np.clip(value, 0, 100)), "good": bool(good)})

        if "score" in r:
            add("Filtros", r["score"], r["score"] >= 67)
        if "wscore" in r:
            add("Ponderado", r["wscore"], r["wscore"] >= 55)
        if r.get("book") is not None:
            v = 50 + 50 * r["book"] * (1 if self.direction == "LONG" else -1)
            add("Libro", v, v >= 50)
        if r.get("z") is not None:
            add("VWAP", 100 - abs(r["z"]) / 3 * 100, abs(r["z"]) <= 2.5)
        if r.get("crowd") is not None:
            lim = (60, 30) if self.direction == "SHORT" else (82, 50)
            v = (lim[0] - r["crowd"]) / (lim[0] - lim[1]) * 100
            add("Long/Short", v, v >= 30)
        if "squeeze" in r:
            add("Anti-squeeze", 100 - r["squeeze"] * 25, r["squeeze"] < 3)
        if self.probability is not None:
            add("P(TP1)", self.probability, self.probability >= 55)
        if self.edge_r is not None:
            add("Ventaja", 50 + self.edge_r * 200, self.edge_r > 0)
        return out

    @property
    def approved(self) -> bool:
        return bool(self.results) and all(r.status != "block" for r in self.results) and self.ticket is not None

    @property
    def verdict(self) -> str:
        """OPERAR (la mesa aprueba) · ESPERAR (objeciones leves) · NO OPERAR (objeciones fuertes). Siempre hay plan."""
        if self.approved:
            return "OPERAR"
        if self.ticket is None:
            return "NO OPERAR"
        hard = (self.edge_r is not None and self.edge_r < -0.02) or (self.probability or 0) < 50 or any(
            r.agent == "EDGE" and r.status == "block" and "⛔" in r.headline and "ventaja" not in r.headline.split("⛔")[-1]
            for r in self.results)
        return "NO OPERAR" if hard else "ESPERAR"

    @property
    def verdict_icon(self) -> str:
        return {"OPERAR": "✅", "ESPERAR": "⏸️", "NO OPERAR": "⛔"}[self.verdict]

    @property
    def operable(self) -> bool:
        """Hay orden lista aunque algún agente la objetara (🎲 modo operable, riesgo reducido)."""
        return self.ticket is not None and not self.approved

    @property
    def blocked_by(self) -> AgentResult | None:
        return next((r for r in self.results if r.status == "block"), None)

    def trace(self) -> list[str]:
        return [f"{r.at} · {r.icon} {r.agent} · {r.headline}" for r in self.results]

    def summary(self) -> str:
        if self.approved:
            return (f"✅ La mesa APRUEBA {self.direction} {self.symbol}: P(TP1) {self.probability or 0:.0f}% · ventaja "
                    f"{self.edge_r or 0:+.2f}R · riesgo {self.risk_pct or 0:.2f}% del capital.")
        b = self.blocked_by
        if self.operable:
            return (f"{self.verdict_icon} {self.verdict} · lo más recomendado: {self.direction} {self.symbol} · P(TP1) "
                    f"{self.probability or 0:.0f}% · ventaja {self.edge_r or 0:+.2f}R · riesgo reducido {self.risk_pct or 0:.2f}% · "
                    f"objetó {b.agent}: {b.headline[:90]}")
        return f"⛔ La mesa NO opera {self.symbol}: {b.agent} bloqueó → {b.headline}" if b else "Sin decisión."


# =============================================================================
# Rotación de señales: qué filtros están funcionando AHORA
# =============================================================================
class SignalRotation:
    """
    Fuerza de cada señal = acierto cuando la señal está activa − acierto de base (puntos), en
    las últimas 24 h; impulso = fuerza de las últimas 12 h − fuerza de las 12 h anteriores.
    Acierto = el precio se movió a favor en los siguientes 30 min.
    """

    def __init__(self, filters, horizon: int = 6, window: int = 288) -> None:
        self.filters, self.horizon, self.window = filters, horizon, window

    def measure(self, df5: pd.DataFrame, df1h: pd.DataFrame | None, direction: str) -> list[dict]:
        n = len(df5)
        if n < self.window + self.horizon + 10:
            return []
        s = 1 if direction == "LONG" else -1
        c = df5["close"].to_numpy(float)
        fwd = np.full(n, np.nan)
        fwd[:-self.horizon] = s * (c[self.horizon:] / c[:-self.horizon] - 1)
        win = fwd > 0
        mat = self.filters.matrix(df5, df1h, direction)
        out = []
        lo, mid, hi = n - self.horizon - self.window, n - self.horizon - self.window // 2, n - self.horizon
        for j, name in enumerate(self.filters.names):
            col = mat[:, j]

            def strength(a, b):
                m = col[a:b]
                base = np.nanmean(win[a:b]) * 100
                hit = np.nanmean(win[a:b][m]) * 100 if m.sum() >= 8 else base
                return hit - base

            st_all, st_new, st_old = strength(lo, hi), strength(mid, hi), strength(lo, mid)
            out.append({"name": name, "strength": round(float(st_all), 2), "momentum": round(float(st_new - st_old), 2),
                        "weight": float(np.clip(1 + st_all / 15, 0.3, 1.7))})
        return out

    @staticmethod
    def weighted_score(report, rotation: list[dict]) -> float:
        w = {r["name"]: r["weight"] for r in rotation}
        tot = sum(w.get(c.name, 1.0) for c in report.checks) or 1.0
        return sum(w.get(c.name, 1.0) for c in report.checks if c.passed) / tot * 100


# =============================================================================
# Calibración: ¿cuando la mesa dice 60%, gana el 60%?
# =============================================================================
class Calibrator:
    BINS = (0, 40, 50, 60, 70, 101)

    def __init__(self, time_machine: TimeMachine, config: FGConfig = DEFAULT_CONFIG, ttl_s: float = 6 * 3600,
                 points: int = 48, sims: int = 4000) -> None:
        self.tm, self.cfg, self.ttl, self.points, self.sims = time_machine, config, ttl_s, points, sims
        self._cache: dict[tuple, tuple[float, dict]] = {}

    def build(self, plan: Plan, df: pd.DataFrame, bar_minutes: int, key: tuple) -> dict:
        """Recorre el pasado SIN mirar el futuro: predice con datos anteriores y compara con lo que pasó."""
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < self.ttl:
            return hit[1]
        horizon = max(1, round(30 / bar_minutes))
        warm = max(2000, round(3 * 1440 / bar_minutes))  # Al menos 3 días de historia antes de cada punto
        n = len(df)
        if n < warm + horizon + 50:
            return {"pairs": [], "bins": [], "brier": None, "n": 0}
        idx = np.linspace(warm, n - horizon - 1, num=min(self.points if self.tm.native else self.points // 2,
                                                         n - warm - horizon - 1)).astype(int)
        h, lo, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
        mgr = TradeManager(replace(self.cfg, bar_minutes=bar_minutes), use_native=False)
        pairs = []
        for i in idx:
            past = df.iloc[: i + 1]
            p_i = plan.rebase(float(c[i]))
            try:
                pred = self.tm.run(p_i, past, bar_minutes, sims=self.sims).p_tp1
            except ValueError:
                continue
            out = mgr.run(p_i, h, lo, c, int(i))
            pairs.append((pred, 1.0 if out.tps_hit >= 1 else 0.0))
        bins = []
        for a, b in zip(self.BINS[:-1], self.BINS[1:]):
            sel = [o for p, o in pairs if a <= p < b]
            if sel:
                preds = [p for p, _ in pairs if a <= p < b]
                bins.append({"range": f"{a}–{min(b, 100)}%", "pred": float(np.mean(preds)),
                             "real": float(np.mean(sel) * 100), "n": len(sel)})
        brier = float(np.mean([(p / 100 - o) ** 2 for p, o in pairs])) if pairs else None
        res = {"pairs": pairs, "bins": bins, "brier": brier, "n": len(pairs)}
        self._cache[key] = (time.time(), res)
        return res

    @staticmethod
    def adjust(p: float, calib: dict | None, prior_weight: float = 20.0) -> float:
        """Corrige la probabilidad con lo observado en su rango (con prudencia si hay pocos casos)."""
        if not calib or not calib.get("bins"):
            return p
        for b in calib["bins"]:
            a, z = (float(x) for x in b["range"].rstrip("%").split("–"))
            if a <= p < z + (1 if z >= 100 else 0):
                bias = b["real"] - b["pred"]
                return float(np.clip(p + bias * b["n"] / (b["n"] + prior_weight), 1, 99))
        return p


# =============================================================================
# La mesa
# =============================================================================
class Desk:
    OPERABLE_RISK = 0.5  # % de riesgo máximo para operaciones NO recomendadas (🎲 modo operable)
    def __init__(self, services, config: FGConfig = DEFAULT_CONFIG, min_prob: float = 55.0, min_edge_r: float = 0.05,
                 kelly_fraction: float = 0.25, min_prior: float = 40.0, operable: bool = True) -> None:
        self.s, self.cfg = services, config
        self.min_prob, self.min_edge_r, self.kelly_fraction = min_prob, min_edge_r, kelly_fraction
        self.min_prior, self.operable = min_prior, operable
        self.send_wait = False  # El piloto también entra en ESPERAR (riesgo reducido) si el usuario lo activa
        self.rotation = SignalRotation(services.filters)
        self.calibrator = Calibrator(services.time_machine, config)

    @staticmethod
    def tape(snap, bars: int = 80) -> dict | None:
        """Datos reales para «THE TAPE»: últimas velas de 5 min, VWAP, desviación (bandas Z) y libro."""
        df = snap.df5.iloc[-bars:]
        if df.empty or "vwap" not in df:
            return None
        dev = (snap.df5["close"] - snap.df5["vwap"]).iloc[-48:]
        rnd = lambda col: [round(float(x), 8) for x in df[col].ffill().bfill()]  # noqa: E731
        ob = snap.order_book or {}
        # Perfil de volumen (volumen negociado por nivel de precio) de las últimas 24 h
        last = snap.df5.iloc[-288:]
        lo_p, hi_p = float(last["low"].min()), float(last["high"].max())
        edges = np.linspace(lo_p, hi_p, 25)
        mid = ((last["high"] + last["low"]) / 2).to_numpy(float)
        vp = np.histogram(mid, bins=edges, weights=last["volume"].to_numpy(float))[0] if hi_p > lo_p else np.zeros(24)
        # Cruces reales de EMA 9/21 dentro de la ventana (marcas ▲▼) y zona de valor (70% del volumen)
        marks = []
        if "ema_9" in df and "ema_21" in df:
            d = np.sign((df["ema_9"] - df["ema_21"]).to_numpy(float))
            marks = [[int(i), 1 if d[i] > 0 else -1] for i in range(1, len(d)) if d[i] != d[i - 1] and d[i] != 0]
        order = np.argsort(vp)[::-1]
        keep, acc = set(), 0.0
        for k in order:
            keep.add(int(k))
            acc += float(vp[k])
            if acc >= 0.7 * float(vp.sum() or 1):
                break
        va = [float(edges[min(keep)]), float(edges[max(keep) + 1])] if keep else None
        rsi_now = float(df["rsi"].iloc[-1]) if "rsi" in df and pd.notna(df["rsi"].iloc[-1]) else None
        return {"o": rnd("open"), "h": rnd("high"), "l": rnd("low"), "c": rnd("close"), "vwap": rnd("vwap"),
                "marks": marks, "va": va, "rsi": rsi_now,
                "vol": rnd("volume"), "sd": float(dev.std()) if len(dev) > 2 else 0.0, "z": Desk.vwap_z(snap.df5),
                "book": ob.get("imbalance"), "spread": ob.get("spread_pct"),
                "levels": {"bids": ob["bids"], "asks": ob["asks"]} if ob.get("bids") and ob.get("asks") else None,
                "vp": {"p": [round(float(e), 8) for e in edges], "v": [round(float(v), 4) for v in vp]}}

    def ridges(self, snap, direction: str, bars: int = 120) -> list[dict]:
        """Señales reales normalizadas 0–1 en las últimas `bars` velas de 5 min (indicadores + filtros)."""
        df = snap.df5.iloc[-bars:]
        out = []

        def add(name, series, lo=None, hi=None):
            s = pd.Series(series, dtype=float).ffill().bfill().fillna(0)
            a, b = (s.min() if lo is None else lo), (s.max() if hi is None else hi)
            v = ((s - a) / (b - a)).clip(0, 1) if b > a else s * 0 + .5
            out.append({"name": name, "v": [round(float(x), 3) for x in v], "now": round(float(v.iloc[-1]), 3)})

        if len(df) < 20:
            return out
        for col, name, lo, hi in (("rsi", "RSI", 0, 100), ("rvol", "Volumen rel.", 0, 3), ("adx", "ADX", 0, 50),
                                  ("atr_pct", "ATR %", None, None), ("bb_width", "Ancho Bollinger", None, None),
                                  ("macd_hist", "MACD hist.", None, None)):
            if col in df:
                add(name, df[col], lo, hi)
        if "vwap" in df:
            dev = df["close"] - df["vwap"]
            sd = dev.rolling(48, min_periods=10).std()
            add("VWAP z", (dev / sd).clip(-3, 3), -3, 3)
        if "ema_9" in df and "ema_21" in df:
            add("EMA 9−21", (df["ema_9"] - df["ema_21"]) / df["close"] * 1000)
        add("Momentum 1h", df["close"].pct_change(12) * 100)
        add("Rango vela", (df["high"] - df["low"]) / df["close"] * 100)
        try:
            mat = self.s.filters.matrix(snap.df5, snap.df1h, direction)[-bars:]
            for j, name in enumerate(self.s.filters.names):
                add(name[:22], pd.Series(mat[:, j].astype(float)).rolling(6, min_periods=1).mean(), 0, 1)
        except Exception:  # noqa: BLE001 - las cordilleras son visuales
            pass
        return out

    @staticmethod
    def vwap_z(df5: pd.DataFrame, bars: int = 48) -> float | None:
        if "vwap" not in df5:
            return None
        dev = (df5["close"] - df5["vwap"]).iloc[-bars:]
        sd = float(dev.std())
        return float(dev.iloc[-1] / sd) if sd > 0 else None

    def _history(self, symbol: str, market: str, exchange: str | None) -> tuple[pd.DataFrame, int]:
        h = self.s.history
        if market == "crypto" and not h.has_cache(symbol, "1m", exchange):
            h.prefetch(symbol, market, ("1m",), 90, exchange)
            df, _ = h.get(symbol, market, "5m", 90, exchange=exchange)
            return df, 5
        df, _ = h.get(symbol, market, "1m", 90, exchange=exchange)
        if len(df) < 2000:
            df, _ = h.get(symbol, market, "5m", 90, exchange=exchange)
            return df, 5
        return df, 1

    # ---------------------------------------------------------------- agentes
    def spot(self, symbol: str, market: str, capital: float, exchange: str | None = None,
             direction: str = "Auto") -> tuple:
        t0 = time.perf_counter()
        s = self.s
        snap = s.loader.load(symbol, market, exchange=exchange)
        ext = s.recommender.external_blocks(capital)
        rep = (s.filters.best(snap, symbol, ext) if direction == "Auto" else s.filters.evaluate(snap, direction, symbol, ext))
        rot = self.rotation.measure(snap.df5, snap.df1h, rep.direction)
        wscore = SignalRotation.weighted_score(rep, rot) if rot else rep.score
        book = (snap.order_book or {}).get("imbalance")
        z = self.vwap_z(snap.df5)
        bits = [f"{rep.passed}/{len(rep.checks)} filtros", f"ponderado {wscore:.0f}/100"]
        if book is not None:
            bits.append(f"libro {50 + book * 50:.0f}% compras")
        if z is not None:
            bits.append(f"VWAP z {z:+.1f}")
        ok = rep.passed >= rep.min_filters and wscore >= 55 and not rep.blocks
        if book is not None and ((rep.direction == "LONG" and book < -0.35) or (rep.direction == "SHORT" and book > 0.35)):
            ok, bits = False, bits + ["el libro va en contra"]
        if z is not None and ((rep.direction == "LONG" and z > 2.5) or (rep.direction == "SHORT" and z < -2.5)):
            ok, bits = False, bits + ["precio demasiado estirado del VWAP"]
        head = f"{rep.direction} {s.tiers.resolve(symbol)} · " + " · ".join(bits)
        if rep.blocks:
            head += f" · ⛔ {rep.blocks[0][:80]}"
        res = AgentResult("SPOTTER", "pass" if ok else "block", head, value=wscore,
                          ms=(time.perf_counter() - t0) * 1000)
        return res, snap, rep, rot, wscore

    def run(self, symbol: str, market: str, capital: float, exchange: str | None = None, direction: str = "Auto",
            calibrate: bool = True, fan: bool = True, send_mode: str | None = None,
            operable: bool | None = None) -> DeskDecision:
        """
        Corre la cadena completa. `send_mode`: None = solo preparar; "paper"/"live" = enviar (SOLO si aprueba).
        `operable` (por defecto el de la mesa): aunque un agente bloquee, la cadena sigue y entrega la mejor
        operación disponible marcada como NO RECOMENDADA, con riesgo reducido (máx. OPERABLE_RISK %) y SL.
        """
        operable = self.operable if operable is None else operable
        s = self.s
        res, snap, rep, rot, wscore = self.spot(symbol, market, capital, exchange, direction)
        dec = DeskDecision(symbol, rep.direction, [res], rotation=rot, tape=self.tape(snap),
                           ridges=self.ridges(snap, rep.direction))
        dec.raw.update(score=rep.score, wscore=wscore, book=(snap.order_book or {}).get("imbalance"),
                       z=self.vwap_z(snap.df5))
        if res.status == "block" and not operable:
            return dec
        coin = s.tiers.resolve(symbol)
        # --- PRIOR ---
        t0 = time.perf_counter()
        pos = s.recommender.positioning.get(coin) if (market == "crypto" and s.recommender.positioning) else None
        sq = s.recommender.squeeze.analyze(snap.df5, rep.direction, snap.funding, pos)
        dec.raw["squeeze"] = sq.points
        if pos is not None:
            dec.raw["crowd"] = pos.short_pct if rep.direction == "SHORT" else pos.long_pct
        base_plan = s.planner.plan(capital, symbol, rep.direction, snap.price, snap.atr5, lev_cap=sq.lev_cap)
        prior = s.stats.evaluate(base_plan, snap.df5)
        p_prior = prior.reach[0]
        st_p = "block" if prior.samples >= 30 and p_prior < self.min_prior else "pass"
        dec.results.append(AgentResult("PRIOR", st_p, f"TP1 en {p_prior:.0f}% de {prior.samples} casos "
                                       f"{'parecidos' if prior.similar else 'del historial'} · SL primero {prior.sl_first:.0f}%",
                                       value=p_prior, ms=(time.perf_counter() - t0) * 1000))
        if st_p == "block" and not operable:
            return dec
        # --- PRICER ---
        t0 = time.perf_counter()
        df, bm = self._history(symbol, market, exchange)
        tm = s.time_machine.run(base_plan, df, bm)
        calib = self.calibrator.build(base_plan, df, bm, (symbol, rep.direction, bm, round(base_plan.sl_pct, 3))) \
            if calibrate else None
        blend = 0.65 * tm.p_tp1 + 0.35 * p_prior
        p_final = Calibrator.adjust(blend, calib)
        dec.tm, dec.probability, dec.calibration = tm, p_final, calib
        if fan:
            dec.fan = s.time_machine.fan(base_plan, df, bm)
        cal_txt = (f" · calibrada con {calib['n']} casos reales (Brier {calib['brier']:.2f})"
                   if calib and calib.get("n") else "")
        st_pr = "pass" if p_final >= self.min_prob else "block"
        dec.results.append(AgentResult("PRICER", st_pr, f"P(TP1) {p_final:.1f}% (Time Machine {tm.p_tp1:.1f}% · base "
                                       f"{p_prior:.0f}%){cal_txt} · {tm.sims:,} caminos",
                                       value=p_final, ms=(time.perf_counter() - t0) * 1000))
        if st_pr == "block" and not operable:
            return dec
        # --- EDGE ---
        t0 = time.perf_counter()
        guards = []
        if sq.risk == "alto":
            guards.append(sq.label)
        rc = s.risk.check(coin, rep.direction, base_plan.entry, base_plan.stop_loss, base_plan.liquidation, capital,
                          s.journal.today() if s.journal else [])
        guards += rc.blocks
        dec.edge_r = tm.ev_r
        edge_ok = tm.ev_r >= self.min_edge_r and tm.ev > 0
        head = f"Ventaja {tm.ev_r:+.2f}R ({fmt_usd(tm.ev, True)} por operación, ya con comisiones) · peor 5% −{fmt_usd(max(0, tm.var95))}"
        if pos is not None:
            head += f" · {pos.long_pct:.0f}/{pos.short_pct:.0f} L/S"
        if guards:
            head += f" · ⛔ {guards[0][:90]}"
        elif not edge_ok:
            head += " · ⛔ ventaja negativa: en promedio esta operación pierde" if tm.ev <= 0 else \
                f" · ⛔ ventaja menor que el mínimo ({self.min_edge_r:+.2f}R)"
        st_e = "pass" if edge_ok and not guards else "block"
        dec.results.append(AgentResult("EDGE", st_e, head, value=tm.ev_r, ms=(time.perf_counter() - t0) * 1000))
        if st_e == "block" and not operable:
            return dec
        # --- KELLY ---
        t0 = time.perf_counter()
        table_risk, _ = s.planner.budget.rule(capital)
        kelly_risk = tm.kelly * self.kelly_fraction * 100
        budget = s.budget.status() if s.budget else None
        if budget is not None and budget.defensive:
            kelly_risk /= 2
        risk = min(table_risk, kelly_risk)
        objected = any(r.status == "block" for r in dec.results)
        st_k = "pass" if risk >= 0.1 and not objected else "block"
        if operable and (objected or risk < 0.1):  # 🎲 Operable: tamaño mínimo porque la mesa no lo respalda
            risk, st_k = min(table_risk, self.OPERABLE_RISK), "info"
        plan = s.planner.plan(capital, symbol, rep.direction, snap.price, snap.atr5, lev_cap=sq.lev_cap, risk_pct=risk)
        dec.plan, dec.risk_pct = plan, risk
        dec.results.append(AgentResult(
            "KELLY", st_k, f"¼ Kelly {kelly_risk:.2f}% · tabla {table_risk:g}% → riesgo {risk:.2f}% "
            f"({fmt_usd(plan.risk_usd)}) · {plan.leverage}x · margen {fmt_usd(plan.margin)}"
            + (" · modo defensivo" if budget is not None and budget.defensive else "")
            + (" · 🎲 OPERABLE: riesgo reducido" if st_k == "info" else ""),
            value=risk, ms=(time.perf_counter() - t0) * 1000))
        if st_k == "block":
            return dec
        # --- TAKER ---
        t0 = time.perf_counter()
        ticket = s.executor.build_ticket(plan, symbol, market, exchange or "binance", "DESK", rep.score, post_only=True)
        dec.ticket = ticket
        objected = any(r.status == "block" for r in dec.results)
        sent_txt = ("🎲 OPERABLE · NO RECOMENDADA: solo tú decides" if objected
                    else "orden lista: esperando tu confirmación")
        if send_mode in ("paper", "live") and (not objected or (self.send_wait and not self._hard_objection(dec))):
            out = s.executor.submit(ticket, confirmed=True, mode=send_mode)
            dec.sent = out.get("status") == "filled"
            sent_txt = (f"ENVIADA ({'papel' if send_mode == 'paper' else 'REAL'})" if dec.sent
                        else f"no enviada: {(out.get('problems') or [out.get('message', '')])[0]}")
        dec.results.append(AgentResult("TAKER", "info" if objected else "pass", f"{plan.direction} {fmt_px(plan.entry)} post-only · SL "
                                       f"{fmt_px(plan.stop_loss)} · TP1 {fmt_px(plan.tps[0][0])} ({fmt_pct(plan.tps[0][1])}) "
                                       f"· {sent_txt}", ms=(time.perf_counter() - t0) * 1000))
        dec.results.append(self.close_agent())
        return dec

    @staticmethod
    def _hard_objection(dec: DeskDecision) -> bool:
        return (dec.edge_r is not None and dec.edge_r < -0.02) or (dec.probability or 0) < 50

    def close_agent(self) -> AgentResult:
        t0 = time.perf_counter()
        closed = self.s.executor.refresh_paper()
        open_n = len(self.s.positions.all())
        last = closed[-1] if closed else None
        txt = f"{open_n} abierta(s) con escudo + trailing"
        if last:
            txt += f" · cerró {last['coin']} {last['reason']} {fmt_usd(last['pnl'], True)}"
        return AgentResult("CLOSER", "info", txt, ms=(time.perf_counter() - t0) * 1000)

    def scan(self, coins: list[str], market: str, capital: float, exchange: str | None = None,
             top: int = 3, send_mode: str | None = None) -> tuple[DeskDecision | None, list[tuple[str, float, str]]]:
        """Spotter rápido sobre todas; cadena completa solo para las `top` mejores."""
        ranked = []
        for c in coins:
            sym = "DEMO/USDT" if market == "demo" else f"{c}/USDT"
            try:
                res, *_rest, wscore = self.spot(sym, market, capital, exchange)
                ranked.append((sym, wscore if res.status == "pass" else -1, res.headline))
            except Exception as exc:  # noqa: BLE001
                ranked.append((sym, -2, f"sin datos: {str(exc)[:60]}"))
            if market == "demo":
                break
        ranked.sort(key=lambda x: x[1], reverse=True)
        best = None
        cands = [r for r in ranked if r[1] >= 0][:top] or ([r for r in ranked if r[1] > -2][:top] if self.operable else [])
        for sym, sc, _ in cands:
            dec = self.run(sym, market, capital, exchange, send_mode=send_mode)
            if dec.approved:
                return dec, ranked
            if best is None or self._operable_score(dec) > self._operable_score(best):
                best = dec  # 🎲 la más operable: mayor probabilidad y ventaja
        return best, ranked

    @staticmethod
    def _operable_score(dec: DeskDecision) -> float:
        return (dec.probability or 0) + (dec.edge_r or -1) * 100 + (50 if dec.ticket is not None else 0)


# =============================================================================
# Piloto automático (papel primero)
# =============================================================================
class Autopilot:
    KEY = "desk_autopilot"
    UNLOCK = {"trades": 50, "pf": 1.2, "max_dd_pct": 10.0}

    def __init__(self, desk: Desk, every_s: float = 60.0) -> None:
        self.desk, self.every_s = desk, every_s
        self._mem: dict = {}

    @property
    def db(self):
        return getattr(self.desk.s.positions, "db", None)

    def state(self) -> dict:
        data = (self.db.get_state(self.KEY, {}) if self.db is not None else self._mem) or {}
        return {"enabled": False, "mode": "paper", "max_trades_day": 6, "max_loss_pct": 3.0, "tiers": ["A", "B"],
                "min_level": "OPERAR",
                "last_run": 0.0, "events": [], **data}

    def save(self, st: dict) -> None:
        st["events"] = st.get("events", [])[-60:]
        if self.db is not None:
            self.db.set_state(self.KEY, st)
        else:
            self._mem = st

    def set(self, **changes) -> dict:
        st = {**self.state(), **changes}
        self.save(st)
        return st

    def paper_stats(self) -> dict:
        trades = [t for t in (self.desk.s.journal.entries() if self.desk.s.journal else [])
                  if str(t.get("notes", "")).startswith("DESK · papel")]
        pnl = [float(t.get("pnl") or 0) for t in trades]
        wins, losses = sum(p for p in pnl if p > 0), -sum(p for p in pnl if p < 0)
        pf = wins / losses if losses > 0 else (float("inf") if wins > 0 else 0.0)
        curve = np.cumsum(pnl) if pnl else np.array([0.0])
        start = (self.desk.s.budget.state().get("start") if self.desk.s.budget else 0) or 1000.0
        dd = float(np.max(np.maximum.accumulate(curve) - curve)) / start * 100 if len(pnl) else 0.0
        return {"trades": len(trades), "pf": pf, "max_dd_pct": dd, "net": float(sum(pnl)),
                "win_rate": sum(p > 0 for p in pnl) / len(pnl) * 100 if pnl else 0.0}

    def live_unlocked(self) -> tuple[bool, str]:
        st, u = self.paper_stats(), self.UNLOCK
        ok = st["trades"] >= u["trades"] and st["pf"] >= u["pf"] and st["max_dd_pct"] <= u["max_dd_pct"]
        return ok, (f"Papel: {st['trades']}/{u['trades']} operaciones · PF {st['pf']:.2f} (mín. {u['pf']}) · "
                    f"drawdown {st['max_dd_pct']:.1f}% (máx. {u['max_dd_pct']:g}%)")

    def kill(self) -> None:
        self.set(enabled=False)
        self._log("⛔ Piloto APAGADO con el botón rojo.")

    def _log(self, text: str) -> None:
        st = self.state()
        st["events"].append({"at": datetime.now(timezone.utc).strftime("%d/%m %H:%M:%S"), "text": text})
        self.save(st)

    def tick(self, capital: float, market: str = "crypto", exchange: str | None = None,
             force: bool = False) -> DeskDecision | None:
        """Una vuelta del piloto: gestiona lo abierto y, si todo lo permite, busca y abre UNA operación."""
        st = self.state()
        if not st["enabled"] or (not force and time.time() - st["last_run"] < self.every_s):
            return None
        st["last_run"] = time.time()
        self.save(st)
        s = self.desk.s
        self.desk.close_agent()
        today = [t for t in (s.journal.today() if s.journal else []) if str(t.get("notes", "")).startswith("DESK")]
        if len(today) >= st["max_trades_day"]:
            return None
        loss = -sum(float(t.get("pnl") or 0) for t in today)
        if capital > 0 and loss / capital * 100 >= st["max_loss_pct"]:
            self._log(f"🛑 Pérdida diaria de la mesa {loss / capital * 100:.1f}% ≥ {st['max_loss_pct']:g}%: pausa hasta mañana.")
            self.set(enabled=False)
            return None
        if len(s.positions.all()) >= s.risk.max_open:
            return None
        mode = st["mode"]
        if mode == "live":
            from engine.fgview.executor import live_enabled

            ok, why = self.live_unlocked()
            if not (ok and live_enabled()):
                self._log(f"🔒 Modo real bloqueado ({why}). Sigo en papel.")
                mode = "paper"
        coins = [c for t in s.tiers.tiers if t.key in st["tiers"] for c in t.coins]
        self.desk.send_wait = st.get("min_level") == "ESPERAR"
        dec, _ = self.desk.scan(coins, market, capital, exchange, send_mode=mode)
        if dec is not None and dec.sent:
            self._log(f"🎯 {dec.summary()} → enviada en {'papel' if mode == 'paper' else 'REAL'}.")
            try:
                s.alerts.sender.send_text(f"🤖 FG DESK · {dec.summary()}")
            except Exception:  # noqa: BLE001
                pass
        elif dec is not None:
            self._log(dec.summary())
        else:
            self._log("🔭 Sin oportunidades: ninguna cripto pasó al Spotter. Esperar también es operar bien.")
        return dec


def main() -> None:  # pragma: no cover - bucle en segundo plano
    import os

    from engine.data_feeder import DataFeeder
    from engine.database import Database
    from engine.fgview import FGServices

    svc = FGServices.create(DataFeeder(), db=Database())
    pilot = Autopilot(Desk(svc))
    capital = float(os.getenv("DESK_CAPITAL") or (svc.budget.status().balance or 500))
    print(f"FG DESK piloto automático · capital {capital:.2f} · Ctrl+C para salir")
    while True:
        dec = pilot.tick(capital, "crypto")
        if dec is not None:
            print(datetime.now().strftime("%H:%M:%S"), dec.summary())
        time.sleep(15)


if __name__ == "__main__":  # pragma: no cover
    main()
