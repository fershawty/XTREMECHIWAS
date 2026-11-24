"""
Reglas de G-JUDGE, la ronda con DEMO y la garantía de solo papel.
"""

from __future__ import annotations

import inspect
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.data_feeder import DataFeeder  # noqa: E402
from engine.database import Database  # noqa: E402
from engine.fgview import FGServices  # noqa: E402
from engine.fgview.agentspec import AgentRegistry, AgentSpec, DeskAgent  # noqa: E402
from engine.fgview.desk import AGENTS, AgentResult, DeskDecision  # noqa: E402
from engine.fgview.grok_team import (GJudge, GrokTeam, RoundContext, TrapVoter, VOTERS,  # noqa: E402
                                     _shrink_ticket, render_report)


@pytest.fixture()
def fgdb(tmp_path):
    return FGServices.create(DataFeeder(), db=Database(tmp_path / "t.db"))


def _desk_forced(fgdb, p_tp1=70.0, ev=2.0, kelly=0.08):
    from engine.fgview.timemachine import TimeMachineResult

    desk = fgdb.desk
    desk.min_prior = 0
    desk.min_prob = 45
    orig = desk.spot
    desk.spot = lambda *a, **k: (lambda r: (AgentResult("SPOTTER", "pass", r[0].headline),) + r[1:])(orig(*a, **k))
    tm = TimeMachineResult(sims=100_000, p_tp1=p_tp1, p_tp2=40, p_tp3=20, p_sl=100 - p_tp1, p_time=0, p_liq=0, ev=ev,
                           var95=8, eligible=5000, regime_band=20, bars="5m", days=90, backend="test",
                           kelly=kelly, ev_r=ev / 8)
    fgdb.time_machine.run = lambda *a, **k: tm
    desk.calibrator.build = lambda *a, **k: None
    return desk


def _votes(**overrides):
    base = {key: 0 for key in VOTERS}
    base.update(overrides)
    return base


@pytest.mark.parametrize("votes,paused,mesa,direction,esperado,regla", [
    (_votes(G_MARKET=2, G_WHALE=2, G_FLOW=2, G_FUNDING=2, G_TRAP=2, G_ODDS=2), True, "OPERAR", "LONG", "VETA", "coach"),
    (_votes(G_MARKET=2, G_WHALE=2, G_FLOW=2, G_FUNDING=2, G_TRAP=2, G_ODDS=2), False, "NO OPERAR", "LONG", "VETA", "veto"),
    (_votes(G_MARKET=2, G_WHALE=2, G_FLOW=2, G_FUNDING=2, G_TRAP=-2, G_ODDS=2), False, "OPERAR", "SHORT", "VETA", "veto"),
    (_votes(G_MARKET=1, G_WHALE=1, G_FLOW=1, G_FUNDING=1), False, "ESPERAR", "LONG", "APRUEBA", "aprueba"),
    (_votes(G_MARKET=2, G_WHALE=2, G_FLOW=2, G_FUNDING=2, G_ODDS=2), False, "OPERAR", "LONG", "APRUEBA", "aprueba"),
    (_votes(G_MARKET=1, G_WHALE=1, G_FLOW=1), False, "OPERAR", "LONG", "PRECAUCION", "precaucion"),
    (_votes(G_MARKET=-2, G_WHALE=2, G_FLOW=2), False, "OPERAR", "LONG", "PRECAUCION", "precaucion"),
    (_votes(G_MARKET=1), False, "ESPERAR", "LONG", "PRECAUCION", "precaucion"),
    (_votes(), False, "OPERAR", "LONG", "VETA", "suma"),
    (_votes(G_MARKET=-2, G_WHALE=-1), False, "OPERAR", "LONG", "VETA", "suma"),
    (_votes(G_MARKET=-2, G_WHALE=2, G_FLOW=2, G_FUNDING=2, G_ODDS=2), False, "OPERAR", "LONG", "PRECAUCION", "disenso"),
    (_votes(G_MARKET=2, G_WHALE=2, G_FLOW=2, G_FUNDING=2, G_TRAP=2, G_ODDS=2), False, "OPERAR", None, "VETA", "direccion"),
    (_votes(G_MARKET=2, G_WHALE=2, G_FLOW=2, G_FUNDING=2, G_TRAP=2, G_ODDS=2), False, None, "LONG", "VETA", "mesa"),
])
def test_gjudge_rules_in_order(votes, paused, mesa, direction, esperado, regla):
    # Los nombres del parametrize usan G_MARKET; el juez espera G-MARKET.
    named = {k.replace("_", "-"): v for k, v in votes.items()}
    out = GJudge.decide(named, paused=paused, desk_verdict=mesa, direction=direction)
    assert out.veredicto == esperado and out.regla == regla
    assert 0 <= out.confianza <= 100
    assert not hasattr(out, "stop") and "tamaño" not in out.porque
    if paused:
        assert out.porque == "pausado por tus reglas"
    if mesa == "NO OPERAR":
        assert "NO OPERAR" in out.porque
    if votes.get("G_TRAP") == -2 and mesa != "NO OPERAR" and not paused:
        assert "G-TRAP" in out.porque


def test_gjudge_clamps_votes_and_ignores_size():
    out = GJudge.decide({"G-MARKET": 9, "G-WHALE": -9, "G-FLOW": "x"}, paused=False, desk_verdict="OPERAR",
                        direction="LONG")
    # +2 y −2 y el resto 0 → suma 0 → VETA. Un voto fuera de rango no empuja la suma.
    assert out.suma == 0 and out.veredicto == "VETA"


def test_trap_vote_is_minus_two_when_gate_blocks():
    dec = DeskDecision("SOL/USDT", "LONG", [
        AgentResult("SPOTTER", "pass", "LONG · ponderado 70"),
        AgentResult("GATE", "block", "squeeze 6 pts (alto) · ⛔ SHORT"),
    ])
    dec.raw = {"squeeze": 6, "z": 0.4}
    ctx = RoundContext(None, "SOL/USDT", "crypto", "LONG", dec, {}, None, None, None,
                       "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", None, 1)
    vote = TrapVoter().vote(ctx)
    assert vote.voto == -2 and "GATE" in vote.linea
    late = DeskDecision("SOL/USDT", "LONG", [AgentResult("GATE", "pass", "squeeze 0 pts (bajo)")])
    late.raw = {"squeeze": 0, "z": 3.1}
    ctx.decision, ctx.direction = late, "LONG"
    assert TrapVoter().vote(ctx).voto == -2


def test_shrink_never_raises_size_or_moves_stop(fgdb):
    dec = _desk_forced(fgdb).run("DEMO/USDT", "demo", 1000)
    ticket = dec.ticket
    half = _shrink_ticket(ticket, 0.5)
    assert half.stop_loss == ticket.stop_loss and half.entry == ticket.entry
    assert half.tps == ticket.tps and half.leverage == ticket.leverage
    assert half.qty == pytest.approx(ticket.qty * 0.5)
    raised = _shrink_ticket(ticket, 4)
    assert raised.qty == pytest.approx(ticket.qty) and raised.stop_loss == ticket.stop_loss
    assert _shrink_ticket(ticket, 0) is None


def test_registry_adds_a_spec_and_rejects_a_duplicate(fgdb):
    class Eco(DeskAgent):
        spec = AgentSpec(
            name="ECO", question="¿BTC acompaña?", why="Nadie mira la correlación con BTC.",
            data="Velas 5m de FG VIEW.", calculation="Correlación 2 h.", vote="0 si no hay datos.",
            blocks_if="Nunca en esta prueba.", never="No sube el tamaño ni quita el stop.",
            place="Después de SPOTTER.", how_to_test="30 operaciones en papel.",
            character="Un murciélago.", after="SPOTTER")

        def evaluate(self, decision, services):
            return AgentResult("ECO", "info", "n/a · sin correlación")

    desk = _desk_forced(fgdb)
    desk.agents.add(Eco())
    dec = desk.run("DEMO/USDT", "demo", 1000)
    names = [r.agent for r in dec.results]
    assert names[names.index("SPOTTER") + 1] == "ECO"
    assert [n for n in names if n in AGENTS] == list(AGENTS)
    assert "AGENTE: ECO" in desk.agents.ficha("ECO") and "BLOQUEA SI:" in desk.agents.ficha("SPOTTER")
    with pytest.raises(ValueError):
        desk.agents.add(_Named("SPOTTER"))
    with pytest.raises(ValueError):
        desk.agents.add(_Named("dos palabras"))


class _Named(DeskAgent):
    def __init__(self, name):
        self.spec = AgentSpec(name=name, question="q", why="w", data="d", calculation="c", vote="v",
                              blocks_if="b", never="n", place="p", how_to_test="h", character="k", after="FLOW")

    def evaluate(self, decision, services):
        return None


def test_hard_extra_turns_verdict_into_no_operar():
    dec = DeskDecision("SOL/USDT", "LONG", [AgentResult("SPOTTER", "block", "objeta leve")], ticket=object(),
                       probability=70, edge_r=0.2)
    assert dec.verdict == "ESPERAR"
    dec.raw["hard_block"] = True
    assert dec.verdict == "NO OPERAR"


def test_ronda_demo_report_na_and_persists(fgdb):
    _desk_forced(fgdb)
    fgdb.copilot.rules.change("cutoff", "23:59")
    fgdb.copilot.rules.change("day_start", "00:00")
    br = fgdb.grok
    datos = br.ronda_datos("DEMO")
    whale = {n["nombre"]: n for n in datos["agentes"]["G-WHALE"]["numeros"]}
    assert whale["ballenas_compran_usd"]["valor"] is None
    assert whale["ballenas_compran_usd"]["texto"] == "n/a"
    assert whale["ballenas_compran_usd"]["fuente"] and whale["ballenas_compran_usd"]["leido"]
    assert "DEMO" in whale["ballenas_compran_usd"]["fuente"]
    for meta_key in ("G-SCOUT", "G-MARKET", "G-WHALE", "G-FLOW", "G-FUNDING", "G-TRAP", "G-ODDS", "G-RISK",
                     "G-JUDGE", "G-EXEC", "G-WATCH"):
        assert meta_key in datos["agentes"]
        for number in datos["agentes"][meta_key]["numeros"]:
            assert "fuente" in number and "leido" in number and "texto" in number
    out = br.ronda("DEMO", abrir_en_papel=False)
    text = out["reporte"]
    assert text.startswith("GROK AGENTS · DEMO ")
    for key in ("G-MARKET", "G-WHALE", "G-FLOW", "G-FUNDING", "G-TRAP", "G-ODDS", "G-RISK", "G-JUDGE", "G-EXEC"):
        assert key in text
    assert "tasa base n=" in text and "FOMO " in text
    assert br.reporte()["reporte"].split("\n\n")[0] == text.split("\n\n")[0]
    assert br.team.rounds()[-1]["id"] == out["rondas"][-1]
    assert out["modo"] == "papel" and out["ejecucion"] != "abierta en PAPEL"


def test_ronda_opens_paper_only_when_judge_approves(fgdb):
    _desk_forced(fgdb)
    fgdb.copilot.rules.change("cutoff", "23:59")
    fgdb.copilot.rules.change("day_start", "00:00")
    br = fgdb.grok
    modes = []
    original = fgdb.executor.submit

    def wrapped(ticket, confirmed, mode="paper", testnet=False):
        modes.append(mode)
        assert mode == "paper"
        return original(ticket, confirmed, mode=mode, testnet=testnet)

    fgdb.executor.submit = wrapped
    br.team._votes = lambda ctx: {key: {"voto": 2, "linea": "a favor", "numeros": []} for key in VOTERS}
    before = len(fgdb.positions.all())
    out = br.ronda("DEMO", abrir_en_papel=True)
    assert out["juez"]["veredicto"] == "APRUEBA"
    assert out["ejecucion"] == "abierta en PAPEL"
    assert modes == ["paper"] and len(fgdb.positions.all()) == before + 1
    pos = fgdb.positions.all()[-1]
    ticket = br.last_dec.ticket
    assert pos["mode"] == "papel" and pos["stop_loss"] == ticket.stop_loss and pos["entry"] == ticket.entry
    assert pos["qty"] <= ticket.qty + 1e-9 and pos["leverage"] == ticket.leverage
    assert "live" not in inspect.getsource(GrokTeam._open_paper)
    version = fgdb.copilot.rules.state()["version"]
    risk = fgdb.copilot.rules.values()["risk_pct"]
    assert br.revision()["reglas_version"] == version
    assert fgdb.copilot.rules.values()["risk_pct"] == risk


def test_coach_pause_and_desk_veto_block_paper(fgdb):
    _desk_forced(fgdb)
    fgdb.copilot.rules.change("cutoff", "23:59")
    fgdb.copilot.rules.change("day_start", "00:00")
    fgdb.copilot.rules.change("max_trades_day", 1)
    fgdb.journal.add({"opened_at": datetime.now(timezone.utc).isoformat(), "coin": "SOL", "direction": "LONG",
                      "pnl": 1.0, "duration_min": 1, "notes": "DESK · papel"})
    br = fgdb.grok
    br.team._votes = lambda ctx: {key: {"voto": 2, "linea": "a favor", "numeros": []} for key in VOTERS}
    before = len(fgdb.positions.all())
    paused = br.ronda("DEMO", abrir_en_papel=True)
    assert paused["juez"]["veredicto"] == "VETA" and paused["juez"]["porque"] == "pausado por tus reglas"
    assert paused["ejecucion"] == "registrada sin abrir" and len(fgdb.positions.all()) == before

    fgdb.copilot.rules.change("max_trades_day", 6)
    fgdb.desk.gate_agent = lambda *a, **k: AgentResult("GATE", "block", "squeeze 6 pts (alto) · ⛔ alto")
    blocked = br.ronda("DEMO", abrir_en_papel=True)
    assert blocked["juez"]["veredicto"] == "VETA" and "NO OPERAR" in blocked["juez"]["porque"]
    assert blocked["ejecucion"] == "registrada sin abrir" and len(fgdb.positions.all()) == before


def test_legacy_verdict_still_respects_coach_and_desk(fgdb):
    _desk_forced(fgdb)
    fgdb.copilot.rules.change("cutoff", "23:59")
    fgdb.copilot.rules.change("day_start", "00:00")
    br = fgdb.grok
    br.run_desk("DEMO")
    assert br.verdict("VETA", 80, "no")["ok"]
    fgdb.copilot.rules.change("max_trades_day", 1)
    fgdb.journal.add({"opened_at": datetime.now(timezone.utc).isoformat(), "coin": "SOL", "direction": "LONG",
                      "pnl": 1.0, "notes": "DESK · papel"})
    msg = br.verdict("APRUEBA", 90, "quiero abrir", abrir_en_papel=True)["mensaje"]
    assert "Pausado" in msg and fgdb.positions.all() == []


def test_revision_names_who_was_right_and_does_not_touch_rules(fgdb):
    team = fgdb.grok.team
    votos = {key: {"voto": 0, "linea": "0", "numeros": []} for key in VOTERS}
    votos["G-MARKET"] = {"voto": 2, "linea": "1h +1.2%", "numeros": []}
    votos["G-WHALE"] = {"voto": -2, "linea": "en contra", "numeros": []}
    base = {"hora": "12:00", "simbolo": "DEMO/USDT", "cripto": "DEMO", "direccion": "LONG", "votos": votos,
            "juez": {"veredicto": "VETA", "suma": 0, "porque": "suma 0", "confianza": 70, "regla": "suma"},
            "plan": None, "fomo": 0, "ejecucion": "registrada sin abrir", "outcome": "gano", "mesa": "OPERAR"}
    team.save_round({**base, "at": "2026-01-01T00:00:00+00:00"})
    team.save_round({**base, "at": "2026-01-02T00:00:00+00:00"})
    board = GrokTeam.scoreboard(team.rounds())
    assert board["acerto_mas"] == "G-MARKET" and board["equivoco_mas"] == "G-WHALE"
    assert "no es un cambio" in board["sugerencia"]
    version = fgdb.copilot.rules.state()["version"]
    risk = fgdb.copilot.rules.values()["risk_pct"]
    rev = fgdb.grok.revision()
    assert rev["acerto_mas"] == "G-MARKET" and rev["equivoco_mas"] == "G-WHALE"
    assert fgdb.copilot.rules.state()["version"] == version and fgdb.copilot.rules.values()["risk_pct"] == risk
    assert "no se tocan" in rev["nota"]


def test_grade_round_with_demo_candles(fgdb):
    df, _ = fgdb.history.get("DEMO/USDT", "demo", "5m", 3)
    t0 = df.index[-60].to_pydatetime()
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=timezone.utc)
    px = float(df["close"].iloc[-60])
    hi = float(df["high"].iloc[-59:].max())
    team = fgdb.grok.team
    votos = {key: {"voto": 1, "linea": "a favor", "numeros": []} for key in VOTERS}
    team.save_round({"at": t0.isoformat(), "hora": "12:00", "simbolo": "DEMO/USDT", "cripto": "DEMO",
                     "direccion": "LONG", "votos": votos,
                     "juez": {"veredicto": "PRECAUCION", "suma": 6, "porque": "suma +6", "confianza": 50, "regla": "x"},
                     "plan": {"entry": px, "sl": px * 0.5, "sl_pct": 50, "tp1": min(hi, px * 1.0001),
                              "leverage": 5, "risk_usd": 1, "size_factor": 1, "entry_txt": "x", "sl_txt": "x",
                              "tp1_txt": "x"},
                     "fomo": 1, "ejecucion": "registrada sin abrir", "outcome": None, "mesa": "ESPERAR"})
    assert team.grade(after_min=60) == 1
    assert team.rounds()[-1]["outcome"] in ("gano", "sin_movimiento")


def test_report_format_and_mcp_tools_keep_the_original_eleven(fgdb):
    rnd = {"cripto": "SOL", "direccion": "SHORT", "hora": "21:04",
           "votos": {key: {"voto": 1, "linea": "dato 1"} for key in VOTERS}, "plan": None, "fomo": 3,
           "juez": {"veredicto": "PRECAUCION", "suma": 6, "porque": "suma +6", "confianza": 70},
           "ejecucion": "registrada sin abrir"}
    rnd["votos"]["G-ODDS"]["linea"] = "P(TP1) 61.0% · ventaja +0.10R · tasa base n=0"
    text = render_report(rnd)
    assert text.splitlines()[0] == "GROK AGENTS · SOL SHORT · 21:04"
    assert "G-ODDS    +1 · P(TP1) 61.0%" in text
    assert "G-RISK    plan: n/a · FOMO 3/10" in text
    assert "G-JUDGE   PRECAUCION · suma +6 · confianza 70% · suma +6" in text
    assert text.endswith("G-EXEC    registrada sin abrir")
    br = fgdb.grok
    srv = br.build()
    names = set()
    tools = getattr(srv, "_tools", None) or getattr(srv, "tools", None) or {}
    if isinstance(tools, dict):
        names = set(tools)
    else:
        for item in tools or []:
            names.add(getattr(item, "name", None) or getattr(item, "key", None) or str(item))
    # El SDK guarda las herramientas en el manager; si el atributo cambió, el build igual no debe fallar.
    if not names:
        manager = getattr(srv, "_tool_manager", None) or getattr(srv, "tool_manager", None)
        listing = getattr(manager, "_tools", None) or getattr(manager, "tools", None) or {}
        names = set(listing)
    expected = {"fg_mercado", "fg_correr_mesa", "fg_escanear", "fg_agente", "fg_posiciones", "fg_resultados",
                "grok_veredicto", "fg_tarjeta", "fg_coach", "fg_reglas", "fg_resumen", "fg_ronda_datos", "fg_ronda",
                "fg_reporte", "fg_revision", "fg_estado"}
    assert expected <= names
    from engine.fgview.grok_mcp import agents_prompt, copilot_prompt
    assert "G-JUDGE" in agents_prompt() and "REGLAS DE VERDAD" in copilot_prompt()
    import engine.fgview.webapp as webapp
    assert "executor.submit" not in inspect.getsource(webapp)
    assert "mode=\"live\"" not in inspect.getsource(webapp) and "mode='live'" not in inspect.getsource(webapp)


def test_auto_mode_skips_connector_rounds_and_masks_token(fgdb):
    from engine.fgview.grok_mcp import mask_secret
    from ui.grok_desk_view import build_desk_payload, desk_html

    br = fgdb.grok
    assert br.auto_on() and br.estado()["modo_automatico_grok"] is True
    assert br.coach()["modo_automatico_grok"] is True
    assert "Solo lectura" in br.estado()["nota"]
    secret = "https://abc.trycloudflare.com/mcp/" + br.state()["token"]
    masked = mask_secret(secret)
    assert br.state()["token"] not in masked and "••••••••" in masked
    assert mask_secret(br.app_url(local=True)) .count("••••") == 1 or "••••" in mask_secret(br.local_url())
    br.set(auto=False)
    before = len(fgdb.positions.all())
    skipped = br.ronda("DEMO", abrir_en_papel=True)
    assert skipped["saltada"] is True and len(fgdb.positions.all()) == before
    assert fgdb.copilot.rules.values()["risk_pct"] == 1.0
    pay = build_desk_payload(fgdb, br)
    assert pay["auto"] is False and pay["source"] == "DEMO (datos de prueba)"
    assert pay["candles"] and pay["book"]["bid"] is not None
    html = desk_html(pay)
    assert "GROK" in html and "G-JUDGE" in html and br.state()["token"] not in html
    for key in ("SC", "MK", "WH", "FL", "FU", "TR", "OD", "RK", "JU", "EX", "WA"):
        assert key in html


def test_registry_builtin_fichas_cover_the_twelve():
    reg = AgentRegistry()
    assert [s.name for s in reg.specs()[:12]] == list(AGENTS)
    for spec in reg.specs():
        text = spec.ficha()
        for label in ("AGENTE:", "PREGUNTA:", "DATOS:", "CÁLCULO:", "VOTO:", "BLOQUEA SI:", "NO HACE:",
                      "LUGAR EN LA CADENA:", "CÓMO PROBARLO:", "PERSONAJE:"):
            assert label in text
