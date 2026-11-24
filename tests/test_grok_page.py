"""La página GROK DESK tiene que abrir sin claves de Streamlit repetidas."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HARNESS = Path(__file__).resolve().parent / "grok_page_harness.py"


def test_grok_page_renders_without_exception():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(HARNESS), default_timeout=150)
    at.run()
    assert not at.exception, at.exception
