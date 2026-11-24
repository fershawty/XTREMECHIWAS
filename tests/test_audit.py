"""Auditoría, tamaño al centavo, vigilante y pausa. No modifica rules.md."""

from __future__ import annotations

import inspect
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.data_feeder import DataFeeder  # noqa: E402
from engine.database import Database  # noqa: E402
from engine.fgview import FGServices  # noqa: E402
from engine.fgview.audit import (AuditBook, PauseBook, WatchedEntry, base_rate_note, calibrate_suggestion,  # noqa: E402
                                 celebrates_wins, detect_once, grade_execution, grade_plan, health, postmortem,
                                 rules_score, score_parts, screen_state, size_ticket, sourced, tighten,
                                 trap_checks, watch_position, weekly_leaks)


@pytest.fixture()
def fgdb(tmp_path):
    return FGServices.create(DataFeeder(), db=Database(tmp_path / "a.db"))


def test_sourced_number_keeps_source_age_and_na():
    now = datetime(2026, 11, 24, tzinfo=timezone.utc)
    row = sourced("funding", -0.012, "Binance", now - timedelta(seconds=6), now=now)
    assert "Binance" in row["linea"] and "6s" in row["linea"] and "−0.012" in row["linea"]
    missing = sourced("mark", None, "Binance", now)
    assert missing["texto"] == "n/a" and missing["valor"] is None
    assert base_rate_note(0).startswith("TASA BASE n=0")
    assert "muy pocos para confiar" in base_rate_note(12, 40, 30, 0.1)
    assert "muy pocos" not in base_rate_note(30, 50, 20, 0.2)


def test_trap_fail_warn_and_unknown():
    failed = trap_checks({"spread_pct": 0.2, "direction": "LONG", "gate_block": False, "late": False})
    assert failed["voto"] == -2 and failed["veredicto"] == "trampa"
    warned = trap_checks({
        "spread_pct": 0.08, "depth_usd": 10, "oi_change_pct": 1, "price_change_pct": -0.4,
        "direction": "LONG", "funding_pct": 0.03, "liq_against": False, "wick_ratio": 0.2,
        "volume_ratio": 2, "trade_count": 100, "mark_gap_pct": 0.05, "gate_block": False, "late": False,
    })
    assert warned["warns"] >= 3 and warned["voto"] == -1 and warned["veredicto"] == "warn"
    unknown = trap_checks({})
    assert unknown["n/a"] > 4 and unknown["veredicto"] == "desconocido" and unknown["voto"] == 0
    assert "sin banderas rojas" in trap_checks({
        "spread_pct": 0.01, "depth_usd": 80_000, "oi_change_pct": 0.1, "price_change_pct": 0.2,
        "direction": "LONG", "funding_pct": 0.01, "liq_against": False, "wick_ratio": 0.1,
        "volume_ratio": 1, "trade_count": 100, "mark_gap_pct": 0.01, "gate_block": False, "late": False,
    })["porque"]


def test_score_bands_and_weights_are_not_rules(fgdb):
    verde = score_parts({"mercado": 80, "flujo": 80, "funding": 80, "trampa": 90, "probabilidad": 80, "ventaja": 70})
    assert verde["banda"] == "VERDE" and verde["score"] >= 70 and "¿Por qué?" in verde["por_que"]
    assert "Auditoría completa" in verde["auditoria"]
    assert score_parts({"mercado": 80, "trampa": 0, "trampa_estado": "trampa", "probabilidad": 90})["banda"] == "SKIP"
    parcial = score_parts({"probabilidad": 90, "ventaja": None})
    assert any(p["crudo"] is None for p in parcial["partes"])
    version = fgdb.copilot.rules.state()["version"]
    assert fgdb.copilot.rules.values()["risk_pct"] == 1.0
    assert fgdb.copilot.rules.state()["version"] == version


def test_size_ticket_matches_cents_and_does_not_move_stop():
    q = size_ticket(bankroll=1000, risk_pct=1, entry=100, stop=99, fomo=0, fee_pct=0.05,
                    funding_pct=0.01, pays_funding=True)
    assert q["riesgo_usd"] == Decimal("10.00")
    assert q["nocional"] == Decimal("1000.00")
    assert q["comision"] == Decimal("1.00")
    assert q["funding"] == Decimal("0.10")
    assert q["factor_banda"] == Decimal("1") and q["factor_fomo"] == Decimal("1")
    assert q["stop"] == 99 and q["liq_ok"] is True and q["leverage"] == 28
    yellow = size_ticket(bankroll=1000, risk_pct=1, entry=100, stop=99, band="AMARILLO")
    assert yellow["nocional"] == Decimal("500.00") and yellow["factor_banda"] == Decimal("0.5")
    assert size_ticket(bankroll=1000, risk_pct=1, entry=100, stop=99, fomo=9)["nocional"] == Decimal("500.00")
    assert size_ticket(bankroll=1000, risk_pct=1, entry=100, stop=99, fomo=7)["nocional"] == Decimal("750.00")
    odd = size_ticket(bankroll=1000, risk_pct=1, entry=100, stop=99.3)
    assert odd["nocional"] == Decimal("1428.57")
    tiny = size_ticket(bankroll=100, risk_pct=0.25, entry=100, stop=99, min_ticket=30)
    assert tiny["ok"] is False and tiny["motivo"] == "muy chico para valer la pena"
    far = size_ticket(bankroll=1000, risk_pct=1, entry=100, stop=60)
    assert far["liq_ok"] is False and far["stop"] == 60


def test_coach_detects_one_event_and_pause_does_not_change_rules(fgdb):
    assert detect_once({"sin_stop": True, "promediar": True})["evento"] == "sin stop"
    assert detect_once({"minutos_tras_perdida": 4, "tamano_vs_anterior": 1.4})["evento"] == "revancha"
    assert detect_once({"lejos_pct": 0.8})["evento"] == "persecucion"
    assert detect_once({"cambio_regla_tras_perdida": True})["mensaje"].endswith("12 h. Las reglas no se tocan en caliente.")
    assert detect_once({}) is None
    book = PauseBook()
    now = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)
    assert "no cambiaron" in book.press("Pausar 1h", now)
    assert book.active(now) and book.press("Estoy bien", now) and book.active(now) is None
    version = fgdb.copilot.rules.state()["version"]
    fgdb.audit.pause.press("hoy", now)
    fgdb.audit.save()
    assert fgdb.copilot.rules.state()["version"] == version
    assert fgdb.copilot.rules.values()["cutoff"] == "02:00"
    assert rules_score({"con_stop": True, "stop_sin_alejar": True})["puntos"] == 2


def test_watcher_only_tightens_and_stop_jump_is_explicit():
    assert tighten(99, 98, "LONG") == 99
    assert tighten(99, 99.4, "LONG") == 99.4
    assert tighten(101, 102, "SHORT") == 101
    assert tighten(101, 100.5, "SHORT") == 100.5
    alerts = watch_position({"direction": "LONG", "stop_loss": 100, "liquidation": 90, "tp1": 102}, 98)
    assert alerts[0]["texto"].startswith("STOP SALTADO: precio 98")
    repeats = {}
    for _ in range(6):
        watch_position({"direction": "LONG", "stop_loss": 90}, 100, repeats=repeats)
    assert repeats["stop"] == 5


def test_scribe_grades_leaks_and_calibration_stay_suggestions():
    assert grade_plan({"entro_en_zona": False}) == "F"
    assert grade_plan({"entro_en_zona": True, "primero": "tp1"}) == "B"
    assert grade_plan({"primero": "gap"}) == "E"
    assert grade_execution(10, 10, False) == "E1"
    assert grade_execution(10, 11, False) == "E2"
    assert grade_execution(10, 20, False) == "E3"
    assert grade_execution(10, 10, True) == "E3"
    assert "2×" in postmortem(-25, 10)
    assert postmortem(-5, 10) is None
    leaks = weekly_leaks([{"pnl": -4, "banda": "ROJO"}, {"pnl": -9, "banda": "AMARILLO"}, {"pnl": 3, "banda": "VERDE"}])
    assert leaks["aplicada"] is False and leaks["fugas"][0]["donde"] == "AMARILLO"
    cal = calibrate_suggestion([{"parte": "flujo", "grupo": "alto", "resultado": 1},
                                {"parte": "flujo", "grupo": "bajo", "resultado": -1}])
    assert cal["aplicada"] is False and "ruido" in cal["aviso"]
    assert "muy pocos" in cal["notas"][0]


def test_watched_entry_expires_or_fires_without_ordering():
    now = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)
    arm = WatchedEntry()
    arm.arm("SOL", "LONG", 100, 101, "VERDE", now=now)
    assert arm.tick(102, "VERDE", now + timedelta(minutes=5))["state"] == "vigilando"
    assert arm.tick(100.4, "VERDE", now + timedelta(minutes=6))["state"] == "en_zona"
    arm.arm("SOL", "LONG", 100, 101, "VERDE", now=now)
    assert "banda bajó" in arm.tick(100.2, "ROJO", now + timedelta(minutes=1))["mensaje"].lower() or \
        "bajó" in arm.tick(100.2, "ROJO", now + timedelta(minutes=1))["mensaje"]
    arm.arm("SOL", "LONG", 100, 101, "VERDE", now=now)
    expired = arm.tick(110, "VERDE", now + timedelta(minutes=20))
    assert expired["mensaje"] == "Entrada vigilada expiró, no bajó a la zona. No operar también es operar bien."


def test_screen_states_health_and_no_casino():
    assert screen_state(loading=True)["estado"] == "cargando"
    assert screen_state(rows=[])["estado"] == "vacio"
    assert screen_state(error="Binance no respondió")["estado"] == "error"
    old = datetime.now(timezone.utc) - timedelta(seconds=45)
    assert screen_state(rows=[1], fetched_at=old)["estado"] == "viejo"
    report = health(binance_ok=False)
    assert report["degradado"] and "n/a" in report["banner"]
    from ui import grok_desk_view
    assert celebrates_wins(inspect.getsource(grok_desk_view)) is False


def test_mcp_audit_tools_are_read_only(fgdb):
    br = fgdb.grok
    version = fgdb.copilot.rules.state()["version"]
    out = br.auditoria("DEMO")
    assert "checks" in out["trampa"] and out["modo"] == "papel"
    assert br.tamano()["motivo"]
    assert br.vigilancia()["modo"] == "papel"
    assert "mcp" in br.salud()
    assert br.entrada_vigilada()["entrada"]["state"] == "vacia"
    assert fgdb.copilot.rules.state()["version"] == version
    names = set(br.build()._tool_manager._tools)
    for tool in ("fg_auditoria", "fg_tamano", "fg_vigilancia", "fg_salud", "fg_entrada_vigilada"):
        assert tool in names
