"""
engine/fgview
=============
Motor FG VIEW orientado a objetos. Cada pieza tiene una sola responsabilidad y se
conecta por inyección de dependencias en FGServices (container.py):

    FGConfig                parámetros
    TierCatalog/BudgetPolicy niveles A/B/C y riesgo por capital
    SnapshotLoader          datos 5m + 1h con caché y descarga en paralelo
    Filter / FilterSet      6 filtros + bloqueos (una sola lógica en vivo e histórico)
    PositionPlanner         SL, TPs, apalancamiento, liquidación, R:R
    TradeManager            gestión 50/30/20 (única implementación)
    SetupStatistics         cómo le fue a este plan en situaciones parecidas
    ScalpRecommender        la recomendación completa
    EasyModeAdvisor         Modo fácil + ROI objetivo
    RecommenderBacktester   validación histórica
    LadderPlanner           crecimiento exponencial sin liquidaciones
    TradeJournal/DailyGuard bitácora y stop diario
"""

from engine.fgview.config import DEFAULT_CONFIG, FGConfig
from engine.fgview.container import FGServices

__all__ = ["FGConfig", "DEFAULT_CONFIG", "FGServices"]
