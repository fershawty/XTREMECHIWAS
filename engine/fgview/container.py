"""
engine/fgview/container.py
==========================
Raíz de composición: crea y conecta todas las piezas de FG VIEW una sola vez.
La interfaz, la API o los tests piden aquí sus servicios en lugar de construirlos.

    services = FGServices.create(feeder, news=news_filter, db=database)
    rec = services.recommender.recommend("SOL/USDT", "crypto", capital=500)
"""

from __future__ import annotations

from dataclasses import dataclass

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
        return cls(config, tiers, loader, filters, planner, manager, stats, journal, recommender,
                   EasyModeAdvisor(recommender, manager, config), RecommenderBacktester(filters, planner, manager, config),
                   LadderPlanner(planner, config))
