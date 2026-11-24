"""La página GROK DESK tiene que abrir sin claves de Streamlit repetidas."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HARNESS = Path(__file__).resolve().parent / "grok_page_harness.py"


def test_equipo_limit_reads_rules_not_a_fixed_six(tmp_path):
    from engine.data_feeder import DataFeeder
    from engine.database import Database
    from engine.fgview import FGServices
    from ui.grok_desk_view import equipo_html, team_snapshot

    svc = FGServices.create(DataFeeder(), db=Database(tmp_path / "g.db"))
    svc.copilot.rules.change("max_trades_day", 20)
    snap = team_snapshot(svc, svc.grok)
    assert snap["max_trades"] == 20
    html = equipo_html(snap)
    assert '"max_trades": 20' in html
    assert "confetti" not in html.lower() and "jackpot" not in html.lower()
    assert all(a["vote"] is None for a in snap["agents"] if a["key"] == "G-SCOUT")


def test_grok_page_renders_without_exception():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(HARNESS), default_timeout=150)
    at.run()
    assert not at.exception, at.exception
