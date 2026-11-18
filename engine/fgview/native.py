"""
engine/fgview/native.py
=======================
Puente con el núcleo C++ (`fgcore`, compilado desde cpp/fgcore.cpp con pybind11).

Si el módulo está compilado, TradeManager y LadderPlanner usan C++ (decenas de veces
más rápido). Si no, todo funciona igual con la versión Python: misma lógica y mismos
resultados (lo verifican los tests de paridad).

Variable de entorno FGVIEW_NATIVE=0 -> fuerza la versión Python.
"""

from __future__ import annotations

import os

try:  # pragma: no cover - depende de si se compiló la extensión
    import fgcore as _fgcore
except Exception:  # noqa: BLE001 - cualquier fallo de carga -> modo Python
    _fgcore = None

AVAILABLE: bool = _fgcore is not None and os.getenv("FGVIEW_NATIVE", "1") != "0"
core = _fgcore if AVAILABLE else None

# Códigos de salida del núcleo C++ -> texto que muestra la app
REASONS: dict[int, str] = {
    0: "Stop Loss",
    1: "TP1 + resto en break-even",
    2: "TP1+TP2 + resto en TP1",
    3: "TP1+TP2+TP3",
    4: "Tiempo (30 min)",
}
REASON_CODES: dict[str, int] = {v: k for k, v in REASONS.items()}


def status() -> str:
    """Texto corto para la app y el autodiagnóstico."""
    if AVAILABLE:
        return f"C++ activo (fgcore {_fgcore.__version__})"
    return "Python (núcleo C++ no compilado)"
