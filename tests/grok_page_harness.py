"""Abre GROK DESK dentro de AppTest. No es una página del menú."""

from types import SimpleNamespace

import app
from ui.grok_page import render_grok

render_grok(
    app.get_services(),
    SimpleNamespace(market="demo", symbol="DEMO/USDT", exchange=None),
)
