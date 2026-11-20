"""
engine/fgview/container.py
==========================
Raíz de composición: crea y conecta todas las piezas de FG VIEW una sola vez.
La interfaz, la API o los tests piden aquí sus servicios en lugar de construirlos.

    services = FGServices.create(feeder, news=news_filter, db=database)
    rec = services.recommender.recommend("SOL/USDT", "crypto", capital=500)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.data_feeder import DataFeeder
from engine.database import Database
from engine.fgview.advisor import EasyModeAdvisor
from engine.fgview.backtest import RecommenderBacktester
from engine.fgview.config import DEFAULT_CONFIG, FGConfig
from engine.fgview.filters import FilterSet
from engine.fgview.journal import DailyGuard, TradeJournal
from engine.fgview.ladder import LadderPlanner
from engine.fgview.manager import TradeManager
from engine.fgview.plan import PositionPlanner
from engine.fgview.policy import BudgetPolicy, TierCatalog
from engine.fgview.recommender import ScalpRecommender
from engine.fgview.snapshot import SnapshotLoader
from engine.fgview.stats import SetupStatistics
from engine.fgview.budget import BudgetManager
from engine.fgview.burnout import BurnOutStrategy
from engine.fgview.executor import OrderExecutor
from engine.fgview.fundamentals import CryptoFundamentals
from engine.fgview.history import HistoryStore
from engine.fgview.newsmonitor import NewsMonitor
from engine.fgview.pricealerts import PriceAlerts
from engine.fgview.risk import PositionBook, RiskManager
from engine.fgview.scanner import ChartScanner
from engine.fgview.smartmoney import SmartMoneyTracker
from engine.fgview.timemachine import TimeMachine
from engine.fgview.timer import EntryTimer


@dataclass
class FGServices:
    config: FGConfig
    tiers: TierCatalog
    loader: SnapshotLoader
    filters: FilterSet
    planner: PositionPlanner
    manager: TradeManager
    stats: SetupStatistics
    journal: TradeJournal | None
    recommender: ScalpRecommender
    advisor: EasyModeAdvisor
    backtester: RecommenderBacktester
    ladder: LadderPlanner
    # --- FG ISRAEL VIEW Build 1 (módulos 7–16 + BURN OUT) ---
    history: HistoryStore | None = None
    time_machine: TimeMachine | None = None
    scanner: ChartScanner | None = None
    news_monitor: NewsMonitor | None = None
    timer: EntryTimer | None = None
    positions: PositionBook | None = None
    risk: RiskManager | None = None
    budget: BudgetManager | None = None
    executor: OrderExecutor | None = None
    alerts: PriceAlerts | None = None
    fundamentals: CryptoFundamentals | None = None
    smart_money: SmartMoneyTracker | None = None
    burnout: BurnOutStrategy | None = None
    extras: dict = field(default_factory=dict)

    @classmethod
    def create(cls, feeder: DataFeeder, news=None, db: Database | None = None,
               config: FGConfig = DEFAULT_CONFIG) -> "FGServices":
        tiers = TierCatalog()
        loader = SnapshotLoader(feeder, workers=config.scan_workers)
        filters = FilterSet(tiers=tiers, min_filters=config.min_filters)
        planner = PositionPlanner(tiers, BudgetPolicy(), config)
        manager = TradeManager(config)
        stats = SetupStatistics(manager, filters, config)
        journal = TradeJournal(db) if db is not None else None
        recommender = ScalpRecommender(loader, filters, planner, stats, journal, DailyGuard(config), news, config)
        guard = recommender.guard
        positions = PositionBook(db)
        executor = OrderExecutor(feeder, positions, journal, config)
        burnout = BurnOutStrategy(planner, db, config)

        def _burn_hook(pos: dict, result: dict) -> None:  # Cada cierre de BURN OUT actualiza la bolsa
            if pos.get("strategy") == "BURN OUT":
                burnout.register_result(result["pnl"], pos["coin"])

        executor.on_close.append(_burn_hook)
        return cls(config, tiers, loader, filters, planner, manager, stats, journal, recommender,
                   EasyModeAdvisor(recommender, manager, config), RecommenderBacktester(filters, planner, manager, config),
                   LadderPlanner(planner, config),
                   history=HistoryStore(feeder), time_machine=TimeMachine(),
                   scanner=ChartScanner(feeder, loader, filters, tiers), news_monitor=NewsMonitor(news),
                   timer=EntryTimer(config.bar_minutes), positions=positions,
                   risk=RiskManager(positions, guard, config), budget=BudgetManager(db, journal),
                   executor=executor, alerts=PriceAlerts(feeder, positions, db), fundamentals=CryptoFundamentals(),
                   smart_money=SmartMoneyTracker(), burnout=burnout)
