"""
ui/mode.py
==========
Interruptor 🎓 PRINCIPIANTE / 🧠 EXPERTO.

* PRINCIPIANTE: cada módulo explica qué hace y cómo usarlo paso a paso, los
  resultados salen en frases simples y las tablas técnicas quedan plegadas.
* EXPERTO: todo el detalle técnico a la vista, sin explicaciones.
"""

from __future__ import annotations

from contextlib import contextmanager

import streamlit as st

BEGINNER = "🎓 PRINCIPIANTE"
EXPERT = "🧠 EXPERTO"
MODES = (BEGINNER, EXPERT)


def mode_switch() -> str:
    """Dibuja el interruptor (barra lateral) y devuelve el modo activo."""
    st.session_state.setdefault("ui_mode", BEGINNER)
    choice = st.segmented_control("Modo", MODES, key="ui_mode", label_visibility="collapsed")
    if choice is None:  # El control permite deseleccionar: se mantiene principiante
        st.session_state["ui_mode"] = BEGINNER
    st.caption("Explicaciones simples paso a paso." if beginner() else "Todo el detalle técnico a la vista.")
    return st.session_state["ui_mode"]


def beginner() -> bool:
    return st.session_state.get("ui_mode", BEGINNER) != EXPERT


def guide(what: str, steps: list[str] | None = None, tip: str | None = None) -> None:
    """Caja de ayuda para principiantes (no aparece en modo experto)."""
    if not beginner():
        return
    items = "".join(f"<li>{s}</li>" for s in (steps or []))
    st.markdown(
        '<div style="border:1px solid rgba(212,175,55,.45);background:rgba(212,175,55,.06);border-radius:12px;'
        f'padding:.8rem 1rem;margin-bottom:.8rem"><b>🎓 ¿Qué hace esto?</b> {what}'
        + (f'<ol style="margin:.4rem 0 0 1.1rem">{items}</ol>' if items else "")
        + (f'<div style="margin-top:.4rem;color:#F2D27A">💡 {tip}</div>' if tip else "")
        + "</div>",
        unsafe_allow_html=True,
    )


@contextmanager
def details(label: str = "🔬 Ver detalles técnicos"):
    """Plegado en modo principiante, abierto en modo experto."""
    if beginner():
        with st.expander(label):
            yield
    else:
        yield


def say(beginner_text: str, expert_text: str) -> None:
    """Muestra el texto adecuado según el modo."""
    st.markdown(beginner_text if beginner() else expert_text)
