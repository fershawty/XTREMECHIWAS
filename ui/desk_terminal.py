"""
ui/desk_terminal.py
===================
FG DESK · TERMINAL: la mesa completa en una sola pantalla animada (canvas, 60 fps).

Paneles (todos con datos reales de la mesa, del mercado y de tu bitácora de papel):
  barra superior · estado de la mesa · LIVE WIRE
  MESA PNL · THE TAPE (velas, bandas σ del VWAP, perfil de volumen, escalera del libro)
  ORBITALES (esfera armilar 3D con los 12 agentes orbitando el núcleo FG; el «ticket» salta
             de agente en agente; radar de rastreo; fuentes de datos; Mission Control;
             cuenta regresiva al cierre de vela; telemetría; registro de vuelo)
  PIPELINE · LA TRAZA · EL PRICER (tablero de Galton con la distribución real del Time
  Machine) · CORDILLERAS DE SEÑALES · cinta inferior
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from engine.fgview.desk import AGENT_META, AGENTS
from ui.critters import CRITTERS_JS

CODES = {"SPOTTER": "SP", "WHALE": "WH", "FLOW": "FL", "FUNDING": "FU", "GATE": "GT", "PRIOR": "PR", "PRICER": "PX",
         "EDGE": "ED", "KELLY": "KE", "GUARD": "GD", "TAKER": "TK", "CLOSER": "CL"}


def build_payload(svc, dec, ranked=None, capital: float = 1000.0) -> dict:
    """Junta en un dict todo lo que dibuja la terminal (nada inventado: si falta un dato, el panel lo dice)."""
    pilot = svc.autopilot
    ps = pilot.paper_stats()
    trades = [t for t in (svc.journal.entries() if svc.journal else []) if str(t.get("notes", "")).startswith("DESK")]
    done = {r.agent: r for r in (dec.results if dec else [])}
    agents = []
    for k in AGENTS:
        icon, color, role = AGENT_META[k]
        r = done.get(k)
        agents.append({"key": k, "code": CODES[k], "color": color, "role": role, "status": r.status if r else "idle",
                       "headline": r.headline if r else "en espera", "ms": round(r.ms) if r else None,
                       "at": r.at if r else ""})
    tm = dec.tm if dec else None
    plan = dec.plan if dec else None
    snap_tape = dec.tape if dec else None
    raw = dec.raw if dec else {}
    bal = svc.budget.status().balance if svc.budget and svc.budget.state().get("start") else capital
    feeds = []
    if snap_tape:
        c = snap_tape["c"]
        chg = (c[-1] / c[-13] - 1) * 100 if len(c) > 13 else 0.0
        feeds.append(("TAPE", f"{len(c)} velas · {chg:+.2f}% 1h", min(1, abs(chg) / 2), "#3DDC97"))
    feeds.append(("LIBRO", f"{50 + 50 * raw['book']:.0f}% compras" if raw.get("book") is not None else "sin libro",
                  (raw["book"] + 1) / 2 if raw.get("book") is not None else 0.05, "#F5A623"))
    feeds.append(("ORÁCULO", f"{tm.sims:,} caminos · {tm.backend}" if tm else "en espera",
                  1.0 if tm else 0.05, "#FF5C8A"))
    feeds.append(("FLUJO", f"squeeze {raw.get('squeeze', 0)}/8 pts" if "squeeze" in raw else "en espera",
                  min(1, raw.get("squeeze", 0) / 6) if "squeeze" in raw else 0.05, "#B388FF"))
    feeds.append(("VOLATILIDAD", f"VWAP z {raw['z']:+.2f}" if raw.get("z") is not None else "en espera",
                  min(1, abs(raw.get("z") or 0) / 3), "#5AC8FA"))
    feeds.append(("CADENA", f"{raw['crowd']:.0f}% del lado de la entrada" if raw.get("crowd") is not None else "L/S sin datos",
                  (raw.get("crowd") or 5) / 100, "#FF8A3D"))
    steps = {}
    hot = max((r for r in (dec.ridges if dec else [])), key=lambda r: r["now"], default=None)
    prior_v = done["PRIOR"].value if "PRIOR" in done else None
    if dec:
        steps = {
            "SPOTTER": (f"ponderado {raw.get('wscore', 0):.0f}/100", "LEYENDO LA CINTA"),
            "PRIOR": (f"TP1 base {prior_v:.0f}%" if prior_v is not None else "pesando", "PESANDO SEÑALES"),
            "PRICER": (f"P(TP1) {dec.probability:.1f}%" if dec.probability is not None else "simulando",
                       f"SIMULANDO {tm.sims:,} CAMINOS" if tm else "SIMULANDO CAMINOS"),
            "EDGE": (f"ventaja {dec.edge_r:+.2f}R" if dec.edge_r is not None else
                     (f"spread {snap_tape['spread']:.3f}%" if snap_tape and snap_tape.get("spread") is not None else "midiendo"),
                     "MIDIENDO EL SPREAD"),
            "KELLY": (f"riesgo {dec.risk_pct:.2f}% · ¼K" if dec.risk_pct is not None else "dimensionando", "DIMENSIONANDO"),
            "TAKER": (f"{plan.direction} {plan.entry:,.4g} post-only" if plan else "esperando orden", "COLOCANDO LA ORDEN"),
            "CLOSER": (f"{len(svc.positions.all())} abiertas · trailing", "ARCHIVANDO EN LA BITÁCORA"),
        }
        for k, task in (("WHALE", "SIGUIENDO BALLENAS"), ("FLOW", "MIDIENDO EL FLUJO"), ("FUNDING", "REVISANDO EL FUNDING"),
                        ("GATE", "VIGILANDO SQUEEZES"), ("GUARD", "COLOCANDO EL STOP")):
            r = done.get(k)
            val = "sin datos" if r is None else (f"{'a favor' if (r.value or 0) > 0.15 else 'en contra' if (r.value or 0) < -0.15 else 'neutral'} {r.value:+.2f}"
                                                 if k in ("WHALE", "FLOW", "FUNDING") and r.value is not None else r.headline.split(" · ")[0][:34])
            steps[k] = (val, task)
    for a in agents:
        a["value"], a["task"] = steps.get(a["key"], ("en espera", "PATRULLANDO"))
    return {
        "hot": {"name": hot["name"], "now": hot["now"]} if hot else None,
        "now": datetime.now(timezone.utc).isoformat(),
        "symbol": dec.symbol if dec else "—", "direction": (dec.direction or "") if dec else "",
        "verdict": ("approved" if dec and dec.approved else ("operable" if dec.verdict == "ESPERAR" else "blocked") if dec and dec.operable
                    else "blocked" if dec and dec.blocked_by else "idle"),
        "summary": dec.summary() if dec else "Pulsa «Correr la mesa»: los 12 agentes analizarán el mercado.",
        "agents": agents, "trace": dec.trace() if dec else [],
        "prob": round(dec.probability, 1) if dec and dec.probability is not None else None,
        "edge_r": round(dec.edge_r, 3) if dec and dec.edge_r is not None else None,
        "risk_pct": round(dec.risk_pct, 3) if dec and dec.risk_pct is not None else None,
        "tm": ({"p_tp1": round(tm.p_tp1, 1), "p_sl": round(tm.p_sl, 1), "p_time": round(tm.p_time, 1), "ev": round(tm.ev, 2),
                "var95": round(tm.var95, 2), "sims": tm.sims, "kelly": round(tm.kelly, 4), "hist": tm.hist,
                "backend": tm.backend, "prior": raw.get("score")} if tm else None),
        "plan": ({"entry": plan.entry, "stop": plan.stop_loss, "tps": [t[0] for t in plan.tps], "lev": plan.leverage,
                  "margin": round(plan.margin, 2), "notional": round(plan.notional, 2), "risk_usd": round(plan.risk_usd, 2),
                  "sl_pct": round(plan.sl_pct, 3), "coin": plan.coin} if plan else None),
        "tape": snap_tape, "ridges": dec.ridges if dec else [], "radar": dec.radar if dec else [],
        "feeds": [{"name": n, "text": t, "lvl": round(float(lv), 3), "color": c} for n, t, lv, c in feeds],
        "ranked": [{"sym": s, "score": round(sc, 1)} for s, sc, _ in (ranked or [])][:12],
        "pnl": {"balance": round(bal, 2), "trades": ps["trades"], "win_rate": round(ps["win_rate"], 1),
                "pf": None if ps["pf"] == float("inf") else round(ps["pf"], 2), "max_dd": round(ps["max_dd_pct"], 2),
                "net": round(ps["net"], 2), "series": [round(float(t.get("pnl") or 0), 4) for t in trades[-80:]],
                "longs": sum(t.get("direction") == "LONG" for t in trades), "tp_exits": sum("TP" in str(t.get("notes")) for t in trades)},
        "pilot": {"on": pilot.state()["enabled"], "mode": pilot.state()["mode"]},
        "open": len(svc.positions.all()),
    }


def terminal_html(payload: dict, logo_svg: str, height: int = 1462) -> str:
    return (TEMPLATE.replace("__CRITTERS__", CRITTERS_JS).replace("__DATA__", json.dumps(payload))
            .replace("__LOGO__", json.dumps(logo_svg))
            .replace("__H__", str(height)))


TEMPLATE = r"""<!doctype html><html><head><meta charset="utf-8">
<style>
:root{--bg:#04060a;--p:#080b12;--l:rgba(148,163,184,.13);--t:#E6E9EF;--m:#8C95A8;--f:#566074;--g:#F2D27A;--ok:#3DDC97;--bad:#FF4D6D}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--t);font-family:"JetBrains Mono",ui-monospace,Menlo,Consolas,monospace;font-size:11px;overflow:hidden}
.T{position:relative;display:grid;gap:7px;padding:2px;height:__H__px;grid-template-rows:54px 24px 24px 350px 548px 64px 318px 24px}
.pn{position:relative;background:linear-gradient(180deg,rgba(255,255,255,.028),rgba(255,255,255,0) 30%),var(--p);border:1px solid var(--l);border-radius:12px;overflow:hidden}
.pn.hot{border-color:rgba(242,210,122,.35);box-shadow:0 0 0 1px rgba(242,210,122,.08),0 0 28px rgba(242,210,122,.07)}
.hd{position:absolute;left:11px;top:7px;font-size:10px;letter-spacing:.15em;color:#C9CED8;z-index:3;white-space:nowrap}
.hd b{color:#fff} .hd span{color:var(--f)} .hr{position:absolute;right:11px;top:7px;font-size:9.5px;letter-spacing:.12em;color:var(--f);z-index:3}
canvas{position:absolute;inset:0;width:100%;height:100%}
.row{display:grid;gap:7px} .tick{display:flex;align-items:center;gap:0;border:1px solid var(--l);border-radius:8px;background:#06080d;overflow:hidden;white-space:nowrap}
.tick .lab{padding:0 10px;height:100%;display:flex;align-items:center;font-weight:800;letter-spacing:.14em;font-size:10px}
.mq{display:inline-block;padding-left:100%;animation:mq 38s linear infinite} @keyframes mq{to{transform:translateX(-100%)}}
.chip{display:inline-block;padding:1px 7px;border-radius:6px;margin:0 6px;font-weight:700}
.top{display:flex;align-items:center;justify-content:space-between;padding:0 12px;border:1px solid rgba(242,210,122,.22);border-radius:12px;
 background:linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,0)),#070a10}
.brand{display:flex;align-items:center;gap:10px} .brand svg{width:34px;height:34px;filter:drop-shadow(0 0 10px rgba(212,175,55,.45))}
.bn{font-weight:800;font-size:17px;letter-spacing:.12em} .bs{font-size:8.5px;letter-spacing:.16em;color:var(--f);margin-top:2px}
.st{display:flex;gap:18px;align-items:center} .st div{text-align:right} .st b{display:block;font-size:15px} .st i{font-style:normal;font-size:8.5px;letter-spacing:.14em;color:var(--f)}
.clk{font-size:24px;font-weight:800;letter-spacing:.04em} .on{border:1px solid rgba(61,220,151,.4);color:var(--ok);padding:5px 9px;border-radius:8px;font-size:9.5px;letter-spacing:.14em}
.pulse{animation:pu 1.4s infinite} @keyframes pu{50%{opacity:.25}}
.big{font-size:38px;font-weight:800;letter-spacing:-.02em} .kgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}
.kc{border:1px solid var(--l);border-radius:8px;padding:5px 8px;background:#06080d} .kc i{font-style:normal;font-size:8.5px;letter-spacing:.14em;color:var(--f)} .kc b{display:block;font-size:14px;margin-top:2px}
.bar{height:6px;border-radius:4px;background:#121722;overflow:hidden;margin-top:3px} .bar>div{height:100%;border-radius:4px;transition:width 1.2s}
.feed{display:flex;justify-content:space-between;align-items:center;border:1px solid var(--l);border-left-width:3px;border-radius:7px;padding:5px 8px;margin-bottom:5px;
 background:linear-gradient(90deg,rgba(255,255,255,.03),transparent)} .feed b{font-size:10px;letter-spacing:.1em}
.card{border:1px solid var(--l);border-radius:10px;padding:7px 9px;background:rgba(6,8,13,.9);margin-bottom:6px}
.card.gold{border-color:rgba(242,210,122,.55);box-shadow:0 0 22px rgba(242,210,122,.10)}
.g7{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;justify-items:center}
.gauge{width:40px;height:40px;border-radius:50%;display:grid;place-items:center;position:relative}
.gauge:after{content:"";position:absolute;inset:4px;border-radius:50%;background:#070a10}
.gauge span{position:relative;z-index:2;font-size:8.5px;font-weight:800}
.stc{display:grid;grid-template-columns:repeat(6,1fr);gap:6px;padding:0 4px}
.sc{border:1px solid var(--l);border-radius:8px;padding:5px 8px;background:rgba(6,8,13,.88);clip-path:polygon(0 0,100% 0,96% 100%,4% 100%)}
.sc i{font-style:normal;font-size:8px;letter-spacing:.12em;color:var(--f)} .sc b{display:block;font-size:13.5px;margin-top:1px}
.tl{position:relative;padding-left:18px} .tl:before{content:"";position:absolute;left:6px;top:4px;bottom:4px;width:2px;background:linear-gradient(#3DDC97,#F5A623,#B388FF,#FF5C8A,#5AC8FA)}
.ti{position:relative;margin:0 0 7px;opacity:0;animation:in .5s ease forwards} .ti:before{content:"";position:absolute;left:-16px;top:3px;width:8px;height:8px;border-radius:50%;background:var(--c);box-shadow:0 0 8px var(--c)}
@keyframes in{from{opacity:0;transform:translateX(-8px)}to{opacity:1;transform:none}}
@media (max-width:1250px){.navx{display:none}.st{gap:12px}.clk{font-size:20px}}
@media (max-width:1120px){.bs{display:none}.hd span{display:none}.st div:nth-child(3){display:none}.big{font-size:32px}}
.bn,.bs,.st b,.st i{white-space:nowrap}
.lg .r{display:grid;grid-template-columns:1.3fr 1fr .6fr;gap:4px;font-size:9.5px;padding:2px 0;border-bottom:1px dashed rgba(148,163,184,.08)}
</style></head><body>
<div class="T" id="T"><canvas id="cFly" style="position:absolute;inset:0;z-index:30;pointer-events:none"></canvas>
 <div class="top">
  <div class="brand"><span id="logo"></span><div><div class="bn">FG DESK</div><div class="bs">12 AGENTES · BINANCE · FUTUROS PERPETUOS</div></div>
   <div class="navx" style="margin-left:22px;font-size:10px;letter-spacing:.18em;color:var(--m)">ORBITALES · TAPE · LIBRO · PRICER</div></div>
  <div class="st"><div><b id="tBal">—</b><i>SALDO</i></div><div><b id="tRisk" style="color:#5AC8FA">—</b><i>RIESGO ¼ KELLY</i></div>
   <div><b id="tSims" style="color:#B388FF">—</b><i>SIMULACIONES</i></div><div class="clk" id="clk">--:--:--</div><i style="font-size:9px;color:var(--f)">UTC</i>
   <div class="on"><span class="pulse">●</span> MESA ONLINE</div></div>
 </div>
 <div class="tick"><div class="lab" style="color:#3DDC97;border-right:1px solid var(--l)">● 12 AGENTES LIVE</div><div style="overflow:hidden;flex:1"><div class="mq" id="status" style="animation-duration:44s"></div></div></div>
 <div class="tick"><div class="lab" style="background:linear-gradient(90deg,#F7DC8B,#D4AF37);color:#05070b">LIVE WIRE</div><div style="overflow:hidden;flex:1"><div class="mq" id="wire"></div></div></div>
 <div class="row" style="grid-template-columns:37% 1fr">
  <div class="pn" id="pnlP"><div class="hd">◆ <b>MESA PNL</b> <span>— UN LIBRO · HISTORIAL DE PAPEL</span></div><div class="hr" id="pnlR"></div>
   <div style="position:absolute;left:11px;right:11px;top:26px;z-index:2">
    <div style="display:flex;justify-content:space-between;align-items:flex-end"><div><div style="font-size:10px;color:var(--m)">FG DESK <span style="color:var(--f)">· 12 AGENTES · BINANCE</span></div>
     <div class="big" id="pBal">$0</div></div><div style="text-align:right"><div id="pDelta" class="chip" style="background:rgba(61,220,151,.12);color:#3DDC97;margin:0">—</div>
     <div style="font-size:9px;color:var(--f);margin-top:4px" id="pSub"></div></div></div>
    <div class="kgrid" style="margin-top:6px"><div class="kc"><i>OPERACIONES</i><b id="pT">0</b></div><div class="kc"><i>WIN RATE</i><b id="pW" style="color:#3DDC97">0%</b></div>
     <div class="kc"><i>PROFIT FACTOR</i><b id="pPF" style="color:#B388FF">—</b></div><div class="kc"><i>MAX DD</i><b id="pDD" style="color:#F5A623">0%</b></div></div></div>
   <canvas id="cPnl"></canvas>
   <div style="position:absolute;left:11px;right:11px;bottom:8px;z-index:2" id="bias"></div></div>
  <div class="pn" id="tapeP"><div class="hd">◆ <b>THE TAPE</b> <span>— 5M · VWAP σ-BANDAS · PERFIL DE VOLUMEN · LIBRO EN VIVO</span></div>
   <div class="hr" id="tapeR"></div><canvas id="cTape"></canvas></div>
 </div>
 <div class="pn hot" id="orbP"><div class="hd">◆ <b>ORBITALES</b> <span>— 12 AGENTES · 12 ÓRBITAS · EL TICKET SALTA ENTRE ELLOS · FG EN EL NÚCLEO</span></div>
  <div class="hr" id="orbR"></div><canvas id="cOrb"></canvas>
  <div style="position:absolute;left:10px;top:30px;width:20%;z-index:2"><div style="font-size:10px;color:var(--f)">PRECIO</div><div id="oPx" style="font-size:22px;font-weight:800">—</div>
   <div id="oSub" style="font-size:9px;color:#FF4D6D"></div></div>
  <div style="position:absolute;right:10px;top:30px;z-index:2;text-align:right"><div style="font-size:9px;color:var(--f)">MODELO · P(TP1) CALIBRADA</div>
   <div id="oProb" style="font-size:24px;font-weight:800">—</div><div id="oEdge" style="font-size:9px;color:var(--f)"></div></div>
  <div style="position:absolute;left:21%;right:24%;top:30px;z-index:2;text-align:center;font-size:8.5px;letter-spacing:.14em;color:var(--f)">
   <div id="oTurn">TURNO · ÓRBITAS FIJAS · UN TICKET EN VUELO</div><div style="color:#C9CED8;font-weight:800;margin-top:2px">ORBITALES 01 · ARMILAR FG</div></div>
  <div class="stc" id="stc" style="position:absolute;left:21%;right:24%;top:62px;z-index:2"></div>
  <div style="position:absolute;left:10px;bottom:10px;width:20%;z-index:2"><div style="font-size:9px;letter-spacing:.14em;color:var(--f);margin-bottom:5px">RASTREO · FUENTES DE DATOS</div><div id="feeds"></div></div>
  <div style="position:absolute;right:10px;top:84px;width:23%;z-index:2">
   <div class="card gold"><div style="display:flex;justify-content:space-between;font-size:9px;letter-spacing:.12em"><b style="color:#F2D27A">MISSION CONTROL</b><span id="mcS" style="color:var(--f)"></span></div><div id="mc" style="margin-top:4px"></div></div>
   <div class="card"><div style="font-size:9px;letter-spacing:.12em;color:var(--f)">T-MINUS · CIERRE DE VELA 5M</div><div id="tmin" style="font-size:26px;font-weight:800">T-0:00</div>
    <div style="display:flex;gap:2px;margin-top:3px" id="tdash"></div></div>
   <div class="card"><div style="font-size:9px;letter-spacing:.12em;color:var(--f);margin-bottom:5px">TELEMETRÍA · POR AGENTE</div><div class="g7" id="gauges"></div></div>
   <div class="card lg"><div style="font-size:9px;letter-spacing:.12em;color:var(--f);margin-bottom:3px">REGISTRO DE VUELO</div><div id="flog"></div></div></div>
 </div>
 <div class="pn" id="pipeP"><canvas id="cPipe"></canvas></div>
 <div class="row" style="grid-template-columns:35% 31% 1fr">
  <div class="pn" id="traceP"><div class="hd">◆ <b>LA TRAZA</b> <span>— CADA PASO · UNA LÍNEA</span></div>
   <canvas id="cAct" style="position:absolute;inset:auto;right:11px;top:28px;width:31%;height:38px"></canvas>
   <div style="position:absolute;left:11px;right:35%;top:27px;display:grid;grid-template-columns:repeat(4,1fr);gap:5px" id="tc"></div>
   <div class="tl" id="trace" style="position:absolute;left:11px;right:11px;top:74px;bottom:8px;overflow:hidden"></div></div>
  <div class="pn" id="galP"><div class="hd">◆ <b>EL PRICER</b> <span>— PRECIO · VOL · TIEMPO → P(TP1) · GALTON</span></div><canvas id="cGal"></canvas></div>
  <div class="pn" id="ridP"><div class="hd">◆ <b>CORDILLERAS DE SEÑALES</b> <span>— FUERZA EN EL TIEMPO · CRESTA CALIENTE</span></div><canvas id="cRid"></canvas></div>
 </div>
 <div class="tick"><div class="lab" style="color:#05070b;background:#E6E9EF">FG DESK · ONLINE</div><div style="overflow:hidden;flex:1"><div class="mq" id="foot" style="animation-duration:52s"></div></div></div>
</div>
<script>
__CRITTERS__
const D=__DATA__, LOGO=__LOGO__; const $=id=>document.getElementById(id);
const AMBER='#F5A623'; const VC=v=>v==='approved'?'#F2D27A':v==='operable'?AMBER:v==='blocked'?'#FF4D6D':'#D4AF37';
const mood=a=>a.status==='pass'?'pass':a.status==='block'?'block':a.status==='info'?'info':'idle';
const COL={}; D.agents.forEach(a=>COL[a.key]=a.color); const OK='#3DDC97',BAD='#FF4D6D',GOLD='#F2D27A';
const rgba=(h,a)=>{const n=parseInt(h.slice(1),16);return 'rgba('+(n>>16&255)+','+(n>>8&255)+','+(n&255)+','+Math.max(0,Math.min(1,a)).toFixed(3)+')';};
const fm=(v,d=2)=>v==null?'—':Number(v).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const usd=(v,s)=>v==null?'—':(s&&v>=0?'+':v<0?'−':'')+'$'+fm(Math.abs(v));
const stCol=a=>a.status==='pass'?OK:a.status==='block'?BAD:a.status==='info'?a.color:'#3a4150';
$('logo').innerHTML=LOGO;
// ---------------- Textos y paneles HTML ----------------
const P=D.pnl, act=D.agents.filter(a=>a.status!=='idle'), holder=act.length?act[act.length-1].key:'SPOTTER';
$('tBal').textContent=usd(P.balance); $('tRisk').textContent=D.risk_pct!=null?D.risk_pct.toFixed(2)+'%':'—'; $('tSims').textContent=D.tm?fm(D.tm.sims,0):'—';
$('status').innerHTML=['HOLDER <b style="color:'+COL[holder]+'">'+holder+'</b>','TRASPASOS '+act.length+'/'+D.agents.length,
 'MODELO P(TP1) '+(D.prob!=null?D.prob+'%':'—')+' VS BASE '+(D.tm&&D.tm.prior!=null?D.tm.prior+'%':'—'),'VENTAJA '+(D.edge_r!=null?(D.edge_r>=0?'+':'')+D.edge_r.toFixed(2)+'R':'—'),
 'POSICIONES ABIERTAS '+D.open,'PILOTO '+(D.pilot.on?'ON · '+(D.pilot.mode==='live'?'REAL':'PAPEL'):'OFF'),'APROBACIÓN HUMANA EN REAL','VEREDICTO '+(D.verdict==='approved'?'<b style="color:#3DDC97">✅ OPERAR</b>':D.verdict==='operable'?'<b style="color:#F5A623">⏸️ ESPERAR</b>':D.verdict==='blocked'?'<b style="color:#FF4D6D">NO OPERAR</b>':'EN ESPERA')]
 .map(x=>'<span style="margin:0 18px;color:#C9CED8">'+x+'</span>').join('<span style="color:#3a4150">◆</span>');
const wire=(D.agents.filter(a=>a.status!=='idle').map(a=>'<span class="chip" style="background:'+rgba(a.color,.16)+';color:'+a.color+'">'+a.key+'</span><span style="color:#8C95A8">'+a.at+'</span> <span style="color:#C9CED8">'+esc(a.headline)+'</span>').join('   '))||'<span style="color:#8C95A8">La mesa espera. Corre la mesa para ver a los agentes trabajar.</span>';
$('wire').innerHTML=wire+'   '+wire;
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
$('pBal').textContent=usd(P.balance); $('pBal').style.color=P.net>=0?'#3DDC97':'#FF4D6D';
const last=P.series.length?P.series[P.series.length-1]:null; $('pDelta').textContent=last==null?'SIN OPERACIONES':(last>=0?'▲ ':'▼ ')+usd(last,true);
if(last!=null&&last<0){$('pDelta').style.background='rgba(255,77,109,.12)';$('pDelta').style.color='#FF4D6D';}
$('pSub').textContent='NETO PAPEL '+usd(P.net,true); $('pT').textContent=P.trades; $('pW').textContent=fm(P.win_rate,1)+'%';
$('pPF').textContent=P.pf==null?'∞':fm(P.pf); $('pDD').textContent=fm(P.max_dd,1)+'%'; $('pnlR').textContent='PILOTO '+(D.pilot.on?'ON':'OFF');
const bias=[['SESGO LONG / SHORT',P.trades?P.longs/P.trades:0.5,'#3DDC97','#FF4D6D',(P.trades?Math.round(P.longs/P.trades*100):50)+'% LONG / '+(P.trades?100-Math.round(P.longs/P.trades*100):50)+'% SHORT'],
 ['SALIDAS EN TP',P.trades?P.tp_exits/P.trades:0,'#5AC8FA','#2a3142',(P.trades?Math.round(P.tp_exits/P.trades*100):0)+'% EN TP'],
 ['VENTAJA DE LA MESA',D.edge_r!=null?Math.max(0,Math.min(1,.5+D.edge_r*2)):.5,'#B388FF','#2a3142',D.edge_r!=null?(D.edge_r>=0?'+':'')+D.edge_r.toFixed(2)+'R':'—']];
$('bias').innerHTML=bias.map(b=>'<div style="display:flex;justify-content:space-between;font-size:8.5px;letter-spacing:.1em;color:#8C95A8;margin-top:5px"><span>'+b[0]+'</span><span style="color:'+b[2]+'">'+b[4]+'</span></div>'+
 '<div class="bar" style="background:'+b[3]+'"><div style="width:'+(b[1]*100)+'%;background:'+b[2]+'"></div></div>').join('');
// Orbitales: textos
const T=D.tape; $('oPx').textContent=T?'$'+fm(T.c[T.c.length-1],T.c[T.c.length-1]<10?4:2):'—';
$('oSub').textContent=D.plan?'SL $'+fm(D.plan.stop,D.plan.stop<10?4:2)+' · '+D.plan.lev+'x · '+D.plan.coin:''; $('oSub').style.color='#FF4D6D';
$('oProb').textContent=D.prob!=null?D.prob.toFixed(1)+'%':'—'; $('oProb').style.color=VC(D.verdict);
$('oEdge').textContent=D.edge_r!=null?'VENTAJA '+(D.edge_r>=0?'+':'')+D.edge_r.toFixed(2)+'R · RIESGO '+(D.risk_pct||0).toFixed(2)+'%':'';
$('orbR').textContent=(D.verdict==='approved'?'TRABAJANDO EL LIBRO':D.verdict==='operable'?'🎲 OPERABLE · RIESGO REDUCIDO':D.verdict==='blocked'?'MESA EN PAUSA':'ESCANEANDO')+' · ETAPA 0'+act.length+' · '+holder;
const sc=[['SÍMBOLO',(D.direction?D.direction+' ':'')+(D.plan?D.plan.coin:D.symbol)],['ENTRADA',D.plan?'$'+fm(D.plan.entry,D.plan.entry<10?4:2):'—'],
 ['SL',D.plan?'−'+D.plan.sl_pct.toFixed(2)+'%':'—'],['RIESGO',D.plan?usd(D.plan.risk_usd):'—'],['P(TP1)',D.prob!=null?D.prob.toFixed(1)+'%':'—'],['VENTAJA',D.edge_r!=null?(D.edge_r>=0?'+':'')+D.edge_r.toFixed(2)+'R':'—']];
$('stc').innerHTML=sc.map((s,i)=>'<div class="sc"><i>'+s[0]+'</i><b style="color:'+(i===4?'#3DDC97':i===5?(D.edge_r!=null&&D.edge_r<0?'#FF4D6D':'#F2D27A'):'#E6E9EF')+'">'+s[1]+'</b></div>').join('');
$('feeds').innerHTML=D.feeds.map(f=>'<div class="feed" style="border-left-color:'+f.color+'"><b style="color:'+f.color+'">'+f.name+'</b><span style="font-size:9px;color:#C9CED8">'+esc(f.text)+'</span></div>'+
 '<div class="bar" style="margin:-3px 0 6px"><div style="width:'+(f.lvl*100)+'%;background:'+f.color+'"></div></div>').join('');
$('mcS').textContent=D.verdict==='approved'?'LISTA':D.verdict==='operable'?'🎲 OPERABLE':D.verdict==='blocked'?'BLOQUEADA':'EN ESPERA'; $('mcS').style.color=VC(D.verdict);
$('mc').innerHTML='<div style="display:flex;justify-content:space-between;align-items:center"><b style="font-size:13px">'+(D.direction||'—')+' '+(D.plan?D.plan.coin:'')+'</b><span class="chip" style="margin:0;background:'+(D.direction==='LONG'?'rgba(61,220,151,.15)':'rgba(255,77,109,.15)')+';color:'+(D.direction==='LONG'?OK:BAD)+'">'+(D.direction||'—')+'</span></div>'+
 '<div style="display:grid;grid-template-columns:1fr 1fr;gap:3px 10px;margin-top:5px;font-size:10px"><span style="color:#8C95A8">P(TP1)</span><b>'+(D.prob!=null?D.prob.toFixed(1)+'%':'—')+'</b>'+
 '<span style="color:#8C95A8">VENTAJA</span><b style="color:#F2D27A">'+(D.edge_r!=null?D.edge_r.toFixed(2)+'R':'—')+'</b><span style="color:#8C95A8">RIESGO</span><b>'+(D.plan?usd(D.plan.risk_usd):'—')+'</b>'+
 '<span style="color:#8C95A8">MARGEN</span><b>'+(D.plan?usd(D.plan.margin)+' · '+D.plan.lev+'x':'—')+'</b><span style="color:#8C95A8">TRASPASO</span><b style="color:'+COL[holder]+'">'+holder+'</b></div>';
$('gauges').innerHTML=D.agents.map(a=>{const p=a.status==='idle'?6:a.status==='block'?100:Math.min(100,30+(a.ms||0)/2+40); const c=stCol(a);
 return '<div style="text-align:center"><div class="gauge" style="background:conic-gradient('+c+' '+p+'%,#161b27 0)"><span style="color:'+a.color+'">'+a.code+'</span></div><div style="font-size:7.5px;color:#566074;margin-top:2px">'+(a.ms!=null?a.ms+'ms':'—')+'</div></div>';}).join('');
$('flog').innerHTML=(act.length?act:D.agents.slice(0,4)).slice(-5).map(a=>'<div class="r"><span style="color:'+a.color+'">› '+a.key+'</span><span style="color:#8C95A8">'+(a.at||'--:--')+'</span><b style="color:'+stCol(a)+'">'+(a.status==='pass'?'OK':a.status==='block'?'STOP':a.status==='info'?'INFO':'…')+'</b></div>').join('');
// Traza
const steps=act.length; $('tc').innerHTML=[['PASOS',steps+'/'+D.agents.length],['MS',fm(act.reduce((s,a)=>s+(a.ms||0),0),0)],['BLOQ.',act.filter(a=>a.status==='block').length],['SIMS',D.tm?fm(D.tm.sims/1000,0)+'K':'0']]
 .map(k=>'<div class="kc"><i>'+k[0]+'</i><b>'+k[1]+'</b></div>').join('');
$('trace').innerHTML=(act.length?act:[{key:'FG DESK',color:'#566074',headline:'Esperando la primera decisión de la mesa…',at:'',status:'idle'}]).map((a,i)=>
 '<div class="ti" style="--c:'+(a.color||'#566074')+';animation-delay:'+(i*.62)+'s"><div style="display:flex;justify-content:space-between;font-size:9px"><span class="chip" style="margin:0;background:'+rgba(a.color||'#566074',.16)+';color:'+(a.color||'#888')+'">'+a.key+'</span>'+
 '<b style="color:'+stCol(a)+'">'+(a.status==='pass'?'✓ '+(a.ms||0)+'ms':a.status==='block'?'✕ BLOQUEA':a.status==='info'?'● INFO':'…')+'</b></div>'+
 '<div style="display:grid;grid-template-columns:1fr auto;gap:8px;align-items:baseline"><div style="font-size:9.5px;color:#C9CED8;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'+esc(a.headline)+'</div>'+
 '<div style="text-align:right;white-space:nowrap"><b style="font-size:10.5px;color:'+(a.color||'#888')+'">'+esc(a.value||'')+'</b><div style="font-size:8px;color:#566074">'+(i<act.length-1?a.key+'→'+act[i+1].key:a.status==='block'?'DETENIDO':'FIN')+'</div></div></div></div>').join('');
$('foot').innerHTML=['FG VIEW V2 · FG DESK','UN LIBRO · 12 AGENTES · SIN HUMANO EN EL BUCLE EN PAPEL','ÚLTIMA OPERACIÓN '+(last==null?'—':usd(last,true)),'NETO PAPEL '+usd(P.net,true),
 'WIN RATE '+fm(P.win_rate,1)+'%','PROFIT FACTOR '+(P.pf==null?'∞':fm(P.pf)),D.summary].map(x=>'<span style="margin:0 22px;color:#C9CED8">'+esc(x)+'</span>').join('<span style="color:#3a4150">◆</span>');
// ---------------- Canvas ----------------
const cv={}; function setup(id){const c=$(id),x=c.getContext('2d'); cv[id]={c,x,w:0,h:0}; size(id);}
function size(id){const o=cv[id], d=Math.min(2,devicePixelRatio||1); o.w=o.c.clientWidth; o.h=o.c.clientHeight; o.c.width=o.w*d; o.c.height=o.h*d; o.x.setTransform(d,0,0,d,0,0);}
['cPnl','cTape','cOrb','cPipe','cGal','cRid','cAct','cFly'].forEach(setup); addEventListener('resize',()=>{Object.keys(cv).forEach(size); initOrb(); initGal();});
const t0=performance.now();
// --- MESA PNL: curva de capital + barras por operación ---
function drawPnl(t){const {x,w,h}=cv.cPnl; x.clearRect(0,0,w,h); const s=P.series, top=128, bot=h-112, L=10, R=w-10;
  x.strokeStyle='rgba(148,163,184,.07)'; for(let k=0;k<4;k++){const y=top+(bot-top)*k/3; x.beginPath(); x.moveTo(L,y); x.lineTo(R,y); x.stroke();}
  if(!s.length){x.fillStyle='#566074'; x.font='10px monospace'; x.textAlign='center'; x.fillText('SIN OPERACIONES DE LA MESA TODAVÍA · ENCIENDE EL PILOTO EN PAPEL',w/2,(top+bot)/2);
    x.strokeStyle=rgba('#3DDC97',.4); x.setLineDash([3,4]); x.beginPath(); x.moveTo(L,(top+bot)/2+14); x.lineTo(R,(top+bot)/2+14); x.stroke(); x.setLineDash([]); return;}
  const cum=[]; s.reduce((a,v,i)=>(cum[i]=a+v),0); const lo=Math.min(0,...cum), hi=Math.max(0,...cum), Y=v=>bot-(v-lo)/((hi-lo)||1)*(bot-top-10);
  const n=cum.length, k=Math.min(n,Math.floor((t/1.8)*n)+1), dx=(R-L)/Math.max(1,n-1);
  MAP.pnl=cum.map((v,i)=>[L+i*dx,Y(v)]);
  const mx=Math.max(...s.map(Math.abs))||1; s.slice(0,k).forEach((v,i)=>{x.fillStyle=v>=0?rgba(OK,.55):rgba(BAD,.55); const bh=Math.abs(v)/mx*14; x.fillRect(L+i*dx-1.5,bot+4+(v>=0?14-bh:14),3,bh);});
  const g=x.createLinearGradient(0,top,0,bot); g.addColorStop(0,rgba(OK,.28)); g.addColorStop(1,rgba(OK,0));
  x.beginPath(); for(let i=0;i<k;i++){const px=L+i*dx, py=Y(cum[i]); i?x.lineTo(px,py):x.moveTo(px,py);} x.lineTo(L+(k-1)*dx,bot); x.lineTo(L,bot); x.fillStyle=g; x.fill();
  x.beginPath(); for(let i=0;i<k;i++){const px=L+i*dx, py=Y(cum[i]); i?x.lineTo(px,py):x.moveTo(px,py);} x.strokeStyle=OK; x.lineWidth=1.6; x.shadowColor=OK; x.shadowBlur=10; x.stroke(); x.shadowBlur=0;
  const ex=L+(k-1)*dx, ey=Y(cum[k-1]); x.fillStyle='#fff'; x.beginPath(); x.arc(ex,ey,3+Math.sin(t*5),0,6.283); x.fill();}
// --- THE TAPE ---
function drawTape(t){const {x,w,h}=cv.cTape; x.clearRect(0,0,w,h);
  if(!T){x.fillStyle='#566074'; x.font='11px monospace'; x.textAlign='center'; x.fillText('CORRE LA MESA PARA CARGAR LA CINTA',w/2,h/2); return;}
  const book=T.book, lad=T.levels, LW=lad?Math.min(210,w*.27):0, L=8, R=w-LW-48, top=58, bot=h-58, n=T.c.length, cw=(R-L)/n, sd=T.sd||0;
  const vals=T.h.concat(T.l,T.vwap.map(v=>v+2*sd),T.vwap.map(v=>v-2*sd)); let lo=Math.min(...vals), hi=Math.max(...vals); const pd=(hi-lo)*.05; lo-=pd; hi+=pd; const Y=v=>top+(bot-top)*(1-(v-lo)/(hi-lo));
  const last=T.c[n-1], chg=(last/T.c[Math.max(0,n-13)]-1)*100;
  x.font='800 26px monospace'; x.textAlign='left'; x.fillStyle='#E6E9EF'; x.fillText('$'+fm(last,last<10?4:2),12,46);
  const tw=x.measureText('$'+fm(last,last<10?4:2)).width; x.font='700 10px monospace'; x.fillStyle=chg>=0?OK:BAD; x.fillText((chg>=0?'▲ +':'▼ ')+chg.toFixed(2)+'% 1h',22+tw,44);
  const cnt=[['RSI',T.rsi==null?'—':T.rsi.toFixed(0),'#3DDC97'],['VWAP',fm(T.vwap[n-1],T.vwap[n-1]<10?4:1),'#5AC8FA'],['Z',T.z==null?'—':(T.z>=0?'+':'')+T.z.toFixed(2),'#B388FF'],['SEÑALES',(T.marks||[]).length,'#F2D27A']];
  x.textAlign='right'; let cx2=R+40; for(let i=cnt.length-1;i>=0;i--){const q=cnt[i]; x.font='800 13px monospace'; const wv=Math.max(x.measureText(String(q[1])).width,34);
    x.fillStyle=q[2]; x.fillText(q[1],cx2,40); x.font='7.5px monospace'; x.fillStyle='#566074'; x.fillText(q[0],cx2,50); cx2-=wv+16;}
  // Perfil de volumen (calor de liquidez por precio)
  if(T.vp){const mx=Math.max(...T.vp.v)||1; T.vp.v.forEach((v,i)=>{const y1=Y(T.vp.p[i+1]), y0=Y(T.vp.p[i]); const a=v/mx;
    const g=x.createLinearGradient(L,0,R,0); g.addColorStop(0,'rgba(179,136,255,0)'); g.addColorStop(1,'rgba(179,136,255,'+(a*.22)+')'); x.fillStyle=g; x.fillRect(L,y1,R-L,Math.max(1,y0-y1));
    x.fillStyle='rgba(245,166,35,'+(a*.55)+')'; x.fillRect(R+2,y1,a*36,Math.max(1,y0-y1-1));});}
  if(T.va){const y1=Y(T.va[1]), y2=Y(T.va[0]); x.fillStyle='rgba(242,210,122,.045)'; x.fillRect(L,y1,R-L,y2-y1); x.strokeStyle='rgba(242,210,122,.25)'; x.setLineDash([2,5]);
    x.beginPath(); x.moveTo(L,y1); x.lineTo(R,y1); x.moveTo(L,y2); x.lineTo(R,y2); x.stroke(); x.setLineDash([]); x.font='7.5px monospace'; x.fillStyle='rgba(242,210,122,.7)'; x.textAlign='left'; x.fillText('VALUE AREA 70%',L+4,y1-3);}
  // Humo de liquidez: ondas sobre el perfil de volumen
  if(T.vp){for(let k=0;k<3;k++){x.beginPath(); for(let i=0;i<=60;i++){const px=L+(R-L)*i/60, vy=Y(T.vwap[Math.min(n-1,Math.floor(i/60*(n-1)))]); const yy=vy+Math.sin(i*.35+t*(1+k*.4)+k)*(10+k*7)+Math.sin(i*.9-t*1.7)*4;
    i?x.lineTo(px,yy):x.moveTo(px,yy);} x.strokeStyle='rgba(179,136,255,'+(.10-k*.025)+')'; x.lineWidth=6-k*1.5; x.stroke();} x.lineWidth=1;}
  [[2,'rgba(179,136,255,.08)'],[1,'rgba(90,200,250,.09)']].forEach(([k,f])=>{x.fillStyle=f; x.beginPath(); T.vwap.forEach((v,i)=>{const px=L+cw*(i+.5); i?x.lineTo(px,Y(v+k*sd)):x.moveTo(px,Y(v+k*sd));});
    for(let i=n-1;i>=0;i--) x.lineTo(L+cw*(i+.5),Y(T.vwap[i]-k*sd)); x.closePath(); x.fill();});
  x.strokeStyle='rgba(242,210,122,.9)'; x.setLineDash([4,3]); x.lineWidth=1.2; x.beginPath(); T.vwap.forEach((v,i)=>{const px=L+cw*(i+.5); i?x.lineTo(px,Y(v)):x.moveTo(px,Y(v));}); x.stroke(); x.setLineDash([]);
  const shown=Math.min(n,Math.floor(t/1.4*n)+1), vmax=Math.max(...T.vol||[1]);
  MAP.tape=T.c.map((v,i)=>[L+cw*(i+.5),Y(v)]); MAP.ladder=null;
  for(let i=0;i<shown;i++){const up=T.c[i]>=T.o[i], px=L+cw*(i+.5), c=up?OK:BAD; x.strokeStyle=c; x.lineWidth=1; x.beginPath(); x.moveTo(px,Y(T.h[i])); x.lineTo(px,Y(T.l[i])); x.stroke();
    const y1=Y(Math.max(T.o[i],T.c[i])), y2=Y(Math.min(T.o[i],T.c[i])); x.fillStyle=up?rgba(OK,.9):rgba(BAD,.9); x.fillRect(px-cw*.33,y1,cw*.66,Math.max(1,y2-y1));
    if(T.vol){const vh=T.vol[i]/vmax*34; x.fillStyle=up?rgba(OK,.4):rgba(BAD,.4); x.fillRect(px-cw*.33,h-12-vh,cw*.66,vh);}}
  (T.marks||[]).forEach(([i,d])=>{if(i>=shown) return; const px=L+cw*(i+.5), py=d>0?Y(T.l[i])+10:Y(T.h[i])-10; x.fillStyle=d>0?OK:BAD; x.beginPath();
    if(d>0){x.moveTo(px,py-5); x.lineTo(px-4.5,py+3); x.lineTo(px+4.5,py+3);} else {x.moveTo(px,py+5); x.lineTo(px-4.5,py-3); x.lineTo(px+4.5,py-3);} x.fill();});
  const sx=L+((t*.16)%1)*(R-L); const sg=x.createLinearGradient(sx-50,0,sx,0); sg.addColorStop(0,'rgba(90,200,250,0)'); sg.addColorStop(1,'rgba(90,200,250,.16)'); x.fillStyle=sg; x.fillRect(sx-50,top,50,bot-top);
  const py=Y(last); x.strokeStyle='rgba(230,233,239,.4)'; x.setLineDash([2,3]); x.beginPath(); x.moveTo(L,py); x.lineTo(R,py); x.stroke(); x.setLineDash([]);
  x.fillStyle=GOLD; x.shadowColor=GOLD; x.shadowBlur=16+Math.sin(t*4)*6; x.beginPath(); x.arc(L+cw*(n-.5),py,3.6,0,6.283); x.fill(); x.shadowBlur=0;
  x.font='8.5px monospace'; x.textAlign='left'; x.fillStyle='#8C95A8'; [['+2σ',2],['+1σ',1],['VWAP',0],['−1σ',-1],['−2σ',-2]].forEach(([l,k])=>x.fillText(l,R+40-30,Y(T.vwap[n-1]+k*sd)+3));
  // Escalera del libro
  if(lad){const X0=w-LW-6, rows=lad.asks.slice(0,10).reverse().concat([null]).concat(lad.bids.slice(0,10)), rh=(h-70)/rows.length;
    MAP.ladder={x0:X0,w:LW,top:62,rh,n:rows.length}; const _unused=0, mxs=Math.max(...lad.asks.concat(lad.bids).map(r=>r[1]));
    x.fillStyle='#8C95A8'; x.font='8.5px monospace'; x.fillText('LIBRO DE ÓRDENES',X0,56);
    rows.forEach((r,i)=>{const y=62+i*rh; if(!r){x.fillStyle='#F2D27A'; x.fillText('SPREAD '+fm(T.spread,3)+'%',X0,y+rh*.7); return;}
      const isA=i<10, a=r[1]/mxs*(.92+.08*Math.sin(t*3+i)); x.fillStyle=isA?rgba(BAD,.30):rgba(OK,.30); x.fillRect(X0+LW*.45+(LW*.53)*(1-a),y+1,LW*.53*a,rh-2);
      x.fillStyle=isA?'#FF8DA1':'#8BF0C4'; x.textAlign='left'; x.fillText(fm(r[0],r[0]<10?4:2),X0,y+rh*.72); x.textAlign='right'; x.fillStyle='#C9CED8'; x.fillText(fm(r[1],r[1]<10?3:1),X0+LW-2,y+rh*.72); x.textAlign='left';});
    const b=(book+1)/2; x.fillStyle='#161b27'; x.fillRect(X0,h-12,LW-4,6); x.fillStyle=OK; x.fillRect(X0,h-12,(LW-4)*b,6); x.fillStyle=BAD; x.fillRect(X0+(LW-4)*b,h-12,(LW-4)*(1-b),6);}
  else {x.fillStyle='#566074'; x.font='9px monospace'; x.textAlign='right'; x.fillText('LIBRO NO DISPONIBLE (DEMO / SIN CONEXIÓN)',w-10,h-8);}}
// --- ORBITALES: esfera armilar 3D ---
let ORB=null; const RINGS=[]; const HOME=[]; const MAP={};
function initOrb(){const {w,h}=cv.cOrb; const cx=w*.505, cy=h*.6, R=Math.min(w*.2,h*.36); ORB={cx,cy,R};
  RINGS.length=0; const cols=['#3DDC97','#3B82F6','#2DD4BF','#A3E635','#FF4D6D','#F5A623','#B388FF','#FF5C8A','#5AC8FA','#A5B4FC','#F2D27A','#FF8A3D','#9CFFB5','#B8E1FF'];
  for(let i=0;i<14;i++) RINGS.push({a:(i*0.61)%Math.PI,b:(i*1.37)%(2*Math.PI),s:(i%2?1:-1)*(0.12+i*0.017),r:R*(0.82+((i*7)%5)*0.06),c:cols[i]});
  ORB.nodes=[]; for(let i=0;i<64;i++){const y=1-2*(i+.5)/64, r=Math.sqrt(1-y*y), th=i*2.399963, rr=R*(1.28+((i*37)%10)/28); ORB.nodes.push([Math.cos(th)*r*rr,y*rr,Math.sin(th)*r*rr]);}
  ORB.edges=[]; ORB.nodes.forEach((a,i)=>{const d=ORB.nodes.map((b,j)=>[j,(a[0]-b[0])**2+(a[1]-b[1])**2+(a[2]-b[2])**2]).filter(q=>q[0]!==i).sort((p,q)=>p[1]-q[1]); ORB.edges.push([i,d[0][0]],[i,d[1][0]]);});
  ORB.bolts=[]; ORB.pulses=ORB.edges.filter((_,k)=>k%5===0).map((e,k)=>({e,t:(k*.137)%1}));
  ORB.dots=[]; for(let i=0;i<340;i++){const y=1-2*(i+.5)/340, r=Math.sqrt(1-y*y), th=i*2.399963; ORB.dots.push([Math.cos(th)*r,y,Math.sin(th)*r]);}}
function rot([px,py,pz],a,b,c){let y=py*Math.cos(a)-pz*Math.sin(a), z=py*Math.sin(a)+pz*Math.cos(a); let x=px*Math.cos(b)+z*Math.sin(b); z=-px*Math.sin(b)+z*Math.cos(b);
  const x2=x*Math.cos(c)-y*Math.sin(c), y2=x*Math.sin(c)+y*Math.cos(c); return [x2,y2,z];}
const logoImg=new Image(); logoImg.src='data:image/svg+xml;utf8,'+encodeURIComponent(LOGO);
function agentPos(i,t){const r=RINGS[i]; const th=t*r.s*2.2+i*0.9; const p=rot([Math.cos(th)*r.r,Math.sin(th)*r.r,0],r.a+t*.05,r.b+t*r.s*.3,0.2); return p;}
function proj(p){const {cx,cy}=ORB, f=600/(600+p[2]); return [cx+p[0]*f, cy+p[1]*f*0.92, f];}
function drawOrb(t){const {x,w,h}=cv.cOrb; x.clearRect(0,0,w,h); const {cx,cy,R}=ORB;
  const bg=x.createRadialGradient(cx,cy,10,cx,cy,Math.max(w,h)*.6); bg.addColorStop(0,'#121a2b'); bg.addColorStop(.5,'#070b13'); bg.addColorStop(1,'#04060a'); x.fillStyle=bg; x.fillRect(0,0,w,h);
  for(let i=0;i<60;i++){const sx=(i*137.5%w), sy=(i*89.3%h); x.fillStyle='rgba(200,210,230,'+(.08+.08*Math.sin(t+i))+')'; x.fillRect(sx,sy,1.2,1.2);}
  // Radar de rastreo (izquierda arriba)
  const rx=w*.105, ry=h*.33, rr=Math.min(w*.085,h*.19);
  x.strokeStyle='rgba(148,163,184,.18)'; for(let k=1;k<=3;k++){x.beginPath(); x.arc(rx,ry,rr*k/3,0,6.283); x.stroke();} x.beginPath(); x.moveTo(rx-rr,ry); x.lineTo(rx+rr,ry); x.moveTo(rx,ry-rr); x.lineTo(rx,ry+rr); x.stroke();
  const sw=t*1.1; if(x.createConicGradient){const g=x.createConicGradient(sw,rx,ry); g.addColorStop(0,'rgba(61,220,151,.35)'); g.addColorStop(.1,'rgba(61,220,151,0)'); g.addColorStop(1,'rgba(61,220,151,0)'); x.fillStyle=g; x.beginPath(); x.arc(rx,ry,rr,0,6.283); x.fill();}
  x.strokeStyle='rgba(61,220,151,.8)'; x.beginPath(); x.moveTo(rx,ry); x.lineTo(rx+rr*Math.cos(sw),ry+rr*Math.sin(sw)); x.stroke();
  const blips=(D.ranked.length?D.ranked:D.feeds.map((f,i)=>({sym:f.name,score:40+i*9}))).slice(0,8);
  blips.forEach((b,i)=>{const a=i*0.83+.4, d=rr*(.25+.7*(1-Math.max(0,b.score)/100)), bx=rx+d*Math.cos(a), by=ry+d*Math.sin(a), c=(D.feeds[i%D.feeds.length]||{color:'#3DDC97'}).color;
    const lit=((sw-a)%6.283+6.283)%6.283<1.2; x.fillStyle=rgba(c,lit?1:.55); x.beginPath(); x.arc(bx,by,lit?3.6:2.4,0,6.283); x.fill();
    x.font='7.5px monospace'; x.fillStyle=rgba(c,.8); x.fillText(String(b.sym).replace('/USDT',''),bx+5,by-4);
    // Curva hacia la esfera
    const tt=(t*.25+i*.13)%1; x.strokeStyle=rgba(c,.32); x.lineWidth=1.1; x.beginPath(); x.moveTo(bx,by);
    const c1x=bx+(cx-bx)*.35, c1y=by-40+i*8, c2x=cx-R*1.2, c2y=cy-30+i*10; x.bezierCurveTo(c1x,c1y,c2x,c2y,cx-R*.55,cy-R*.2+i*7); x.stroke();
    const u=1-tt, qx=u*u*u*bx+3*u*u*tt*c1x+3*u*tt*tt*c2x+tt*tt*tt*(cx-R*.55), qy=u*u*u*by+3*u*u*tt*c1y+3*u*tt*tt*c2y+tt*tt*tt*(cy-R*.2+i*7);
    x.fillStyle=rgba(c,.95); x.beginPath(); x.arc(qx,qy,2,0,6.283); x.fill();});
  // Esfera de puntos
  const ry0=t*.15; ORB.dots.forEach(d=>{const p=rot([d[0]*R*.95,d[1]*R*.95,d[2]*R*.95],0.35,ry0,0); const [px,py,f]=proj(p); x.fillStyle='rgba(160,180,220,'+(0.05+0.18*(p[2]<0?1:.4))+')'; x.fillRect(px,py,1.2*f,1.2*f);});
  // Constelación de nodos conectados con pulsos de energía
  const nr=ORB.nodes.map(n=>rot(n,0.35,ry0*1.3,0)), np=nr.map(proj);
  ORB.edges.forEach(([i,j])=>{const z=(nr[i][2]+nr[j][2])/2; x.strokeStyle='rgba(120,170,255,'+(z<0?.16:.08)+')'; x.lineWidth=.7; x.beginPath(); x.moveTo(np[i][0],np[i][1]); x.lineTo(np[j][0],np[j][1]); x.stroke();});
  np.forEach((p,i)=>{const on=(Math.sin(t*2+i*1.7)+1)/2; x.fillStyle='rgba(170,200,255,'+(.25+.5*on)*(nr[i][2]<0?1:.55)+')'; x.beginPath(); x.arc(p[0],p[1],1.2+on*1.6,0,6.283); x.fill();});
  ORB.pulses.forEach(q=>{q.t=(q.t+.012)%1; const [i,j]=q.e, a=np[i], b=np[j]; const px=a[0]+(b[0]-a[0])*q.t, py=a[1]+(b[1]-a[1])*q.t;
    const g=x.createRadialGradient(px,py,0,px,py,6); g.addColorStop(0,'rgba(255,255,255,.95)'); g.addColorStop(1,'rgba(90,200,250,0)'); x.fillStyle=g; x.beginPath(); x.arc(px,py,6,0,6.283); x.fill();});
  // Anillos (líneas de energía), atrás
  x.globalCompositeOperation='lighter';
  const ringPts=RINGS.map(r=>{const pts=[]; for(let k=0;k<=96;k++){const th=k/96*6.283; pts.push(rot([Math.cos(th)*r.r,Math.sin(th)*r.r,0],r.a+t*.05,r.b+t*r.s*.3,0.2));} return pts;});
  ringPts.forEach((pts,i)=>{const r=RINGS[i]; for(let k=0;k<pts.length-1;k++){const a=pts[k], b=pts[k+1]; const z=(a[2]+b[2])/2; const [x1,y1]=proj(a), [x2,y2]=proj(b);
    x.strokeStyle=rgba(r.c,z<0?.30:.12); x.lineWidth=z<0?1.4:.8; x.beginPath(); x.moveTo(x1,y1); x.lineTo(x2,y2); x.stroke();}});
  x.globalCompositeOperation='source-over';
  // Agentes (detrás del núcleo)
  const ags=D.agents.map((a,i)=>({a,i,p:agentPos(i,t)})); const draw=(o)=>{const [px,py,f]=proj(o.p), s=15*f;
    drawCritter(x,o.a.key,px,py,s,o.a.color,t+o.i,mood(o.a),(cx-px)/200,(cy-py)/200);
    o.scr=[px,py]; HOME[o.i]=[px,py];};
  ags.filter(o=>o.p[2]>0).forEach(draw);
  // Núcleo FG
  const v=D.verdict, vc=VC(v), Rc=R*.36*(1+Math.sin(t*2)*.02);
  const fl=x.createRadialGradient(cx,cy,Rc*.5,cx,cy,Rc*3); fl.addColorStop(0,rgba(vc,.35)); fl.addColorStop(1,rgba(vc,0)); x.fillStyle=fl; x.beginPath(); x.arc(cx,cy,Rc*3,0,6.283); x.fill();
  const sp=x.createRadialGradient(cx-Rc*.35,cy-Rc*.4,Rc*.1,cx,cy,Rc); sp.addColorStop(0,'#ffffff'); sp.addColorStop(.6,'#dfe3ec'); sp.addColorStop(1,'#8a93a6');
  x.shadowColor=vc; x.shadowBlur=36; x.fillStyle=sp; x.beginPath(); x.arc(cx,cy,Rc,0,6.283); x.fill(); x.shadowBlur=0;
  x.fillStyle='rgba(255,255,255,.6)'; x.beginPath(); x.ellipse(cx-Rc*.32,cy-Rc*.45,Rc*.32,Rc*.14,-.5,0,6.283); x.fill();
  if(logoImg.complete){x.save(); x.beginPath(); x.arc(cx,cy,Rc*.74,0,6.283); x.clip(); x.drawImage(logoImg,cx-Rc*.74,cy-Rc*.74,Rc*1.48,Rc*1.48); x.restore();}
  // Anillos delanteros
  x.globalCompositeOperation='lighter';
  ringPts.forEach((pts,i)=>{const r=RINGS[i]; for(let k=0;k<pts.length-1;k++){const a=pts[k], b=pts[k+1]; if((a[2]+b[2])/2<0) continue; const [x1,y1]=proj(a), [x2,y2]=proj(b);
    x.strokeStyle=rgba(r.c,.55); x.lineWidth=1.7; x.beginPath(); x.moveTo(x1,y1); x.lineTo(x2,y2); x.stroke();}});
  x.globalCompositeOperation='source-over';
  // Rayos: del núcleo a los agentes y entre agentes (ramificados)
  if(Math.random()<.16){const o=ags[Math.floor(Math.random()*ags.length)]; if(o.scr) ORB.bolts.push({a:[cx,cy],b:null,i:o.i,l:1,c:o.a.status==='block'?BAD:o.a.color});}
  if(Math.random()<.06){const i=Math.floor(Math.random()*ags.length), j=(i+1+Math.floor(Math.random()*5))%ags.length; ORB.bolts.push({a:null,b:null,i,j,l:1,c:'#B8E1FF'});}
  ORB.bolts=ORB.bolts.filter(b=>b.l>0); ORB.bolts.forEach(b=>{const A=b.a||ags[b.i].scr, B=b.j!=null?ags[b.j].scr:ags[b.i].scr; if(!A||!B) return;
    const seg=(p,q,amp,depth)=>{x.beginPath(); x.moveTo(p[0],p[1]); let last=p; for(let k=1;k<=9;k++){const u=k/9, nx=p[0]+(q[0]-p[0])*u+(k<9?(Math.random()-.5)*amp:0), ny=p[1]+(q[1]-p[1])*u+(k<9?(Math.random()-.5)*amp:0);
      x.lineTo(nx,ny); if(depth&&Math.random()<.22){const bq=[nx+(Math.random()-.5)*60,ny+(Math.random()-.5)*60]; x.stroke(); seg([nx,ny],bq,amp*.5,0); x.beginPath(); x.moveTo(nx,ny);} last=[nx,ny];} x.stroke();};
    x.strokeStyle=rgba(b.c,b.l); x.lineWidth=1.6; x.shadowColor=b.c; x.shadowBlur=16; seg(A,B,26,1); x.shadowBlur=0;
    x.strokeStyle='rgba(255,255,255,'+(b.l*.8)+')'; x.lineWidth=.6; seg(A,B,10,0); b.l-=.09;});
  ags.filter(o=>o.p[2]<=0).forEach(draw);
  // El ticket salta entre agentes
  const seq=D.agents.map((a,i)=>i).filter(i=>D.agents[i].status!=='idle'); const S=seq.length?seq:D.agents.map((_,i)=>i);
  if(S.length===1){const o=ags[S[0]]; if(o.scr){const [qx,qy]=o.scr; const lab='TICKET · DETENIDO EN '+o.a.key; x.font='700 8.5px monospace'; const lw=x.measureText(lab).width+14;
    x.fillStyle='rgba(5,7,11,.85)'; x.strokeStyle=rgba(BAD,.8); x.beginPath(); x.roundRect(qx-lw/2,qy-36,lw,16,8); x.fill(); x.stroke(); x.fillStyle=BAD; x.textAlign='center'; x.fillText(lab,qx,qy-25);}}
  const hop=1.5, k=Math.floor(t/hop)%S.length, nx=S[(k+1)%S.length], u=Math.min(1,(t%hop)/.9);
  const A=ags[S[k]].scr, B=ags[nx].scr; if(S.length>1&&A&&B){const e=u<.5?2*u*u:1-Math.pow(-2*u+2,2)/2; const mx=(A[0]+B[0])/2, my=Math.min(A[1],B[1])-60;
    const qx=(1-e)*(1-e)*A[0]+2*(1-e)*e*mx+e*e*B[0], qy=(1-e)*(1-e)*A[1]+2*(1-e)*e*my+e*e*B[1];
    x.strokeStyle='rgba(242,210,122,.25)'; x.setLineDash([3,4]); x.beginPath(); x.moveTo(A[0],A[1]); x.quadraticCurveTo(mx,my,B[0],B[1]); x.stroke(); x.setLineDash([]);
    const g=x.createRadialGradient(qx,qy,0,qx,qy,14); g.addColorStop(0,'#fff'); g.addColorStop(.35,GOLD); g.addColorStop(1,'rgba(242,210,122,0)'); x.fillStyle=g; x.beginPath(); x.arc(qx,qy,14,0,6.283); x.fill();
    const lab='TICKET #'+(k+1)+' · '+D.agents[S[k]].key+' → '+D.agents[nx].key; x.font='700 8.5px monospace'; const lw=x.measureText(lab).width+14;
    x.fillStyle='rgba(5,7,11,.85)'; x.strokeStyle='rgba(242,210,122,.7)'; x.beginPath(); x.roundRect(qx-lw/2,qy-30,lw,16,8); x.fill(); x.stroke(); x.fillStyle=GOLD; x.textAlign='center'; x.fillText(lab,qx,qy-19);}
  // Etiquetas de agentes en los bordes del escenario
  ags.forEach((o,i)=>{const left=i<3||i===6, row=left?(i===6?3:i):i-3, lx=left?w*.235:w*.735, ly=h*.32+row*h*.15; const a=o.a, c=a.color;
    if(o.scr){x.strokeStyle=rgba(c,.22); x.setLineDash([2,4]); x.beginPath(); x.moveTo(lx+(left?95:-6),ly); x.lineTo(o.scr[0],o.scr[1]); x.stroke(); x.setLineDash([]);}
    x.fillStyle='rgba(6,8,13,.86)'; x.strokeStyle=rgba(c,.5); x.beginPath(); x.roundRect(left?lx-8:lx-100,ly-14,108,30,8); x.fill(); x.stroke();
    x.font='800 9px monospace'; x.textAlign='left'; x.fillStyle=c; x.fillText(a.key,(left?lx-2:lx-94),ly-1); x.font='8px monospace'; x.fillStyle=stCol(a);
    x.fillText(a.status==='pass'?'✓ PASA':a.status==='block'?'✕ BLOQUEA':a.status==='info'?'● INFO':'· EN ESPERA',(left?lx-2:lx-94),ly+10);});
  // Pedestal
  const pw=150, px0=cx-pw/2, py0=h-44; x.strokeStyle='rgba(242,210,122,.5)'; x.beginPath(); x.ellipse(cx,py0-6,46,8,0,0,6.283); x.stroke();
  x.fillStyle='rgba(6,8,13,.9)'; x.strokeStyle='rgba(242,210,122,.55)'; x.beginPath(); x.roundRect(px0,py0,pw,32,8); x.fill(); x.stroke();
  x.font='800 9px monospace'; x.fillStyle=GOLD; x.textAlign='left'; x.fillText('BINANCE FUTURES',px0+9,py0+13); x.font='8.5px monospace'; x.fillStyle='#C9CED8';
  x.fillText((D.direction||'—')+' '+(D.plan?D.plan.coin+'/USDT':D.symbol),px0+9,py0+25); x.textAlign='right'; x.fillStyle=D.edge_r!=null&&D.edge_r<0?BAD:OK; x.fillText(D.edge_r!=null?(D.edge_r>=0?'+':'')+D.edge_r.toFixed(2)+'R':'',px0+pw-8,py0+25);}
// --- PIPELINE ---
function drawPipe(t){const {x,w,h}=cv.cPipe; x.clearRect(0,0,w,h); const n=D.agents.length, y=h*.56, x0=46, x1=w-92, dx=(x1-x0)/(n-1);
  x.strokeStyle='rgba(148,163,184,.14)'; x.lineWidth=2; x.beginPath(); x.moveTo(x0,y); x.lineTo(x1,y); x.stroke();
  const reached=act.length, prog=Math.min(Math.max(0,reached-1),t/.62);
  if(reached){const g=x.createLinearGradient(x0,0,x0+dx*prog+1,0); g.addColorStop(0,'rgba(242,210,122,.15)'); g.addColorStop(1,GOLD); x.strokeStyle=g; x.lineWidth=2.6; x.shadowColor=GOLD; x.shadowBlur=12; x.beginPath(); x.moveTo(x0,y); x.lineTo(x0+dx*prog,y); x.stroke(); x.shadowBlur=0;}
  for(let k=0;k<3;k++){const p=((t*.33+k/3)%1)*Math.max(1,reached-1)*dx; const g=x.createRadialGradient(x0+p,y,0,x0+p,y,10); g.addColorStop(0,'rgba(255,244,200,.95)'); g.addColorStop(1,'rgba(242,210,122,0)'); x.fillStyle=g; x.beginPath(); x.arc(x0+p,y,10,0,6.283); x.fill();}
  D.agents.forEach((a,i)=>{const px=x0+dx*i, on=a.status!=='idle'&&t>i*.62, c=stCol(a), cur=a.key===holder; const r=12+(cur?Math.sin(t*4)*1.5+2:0);
    if(cur){x.strokeStyle=rgba(a.color,.6); x.lineWidth=1.5; x.beginPath(); x.arc(px,y,r+7+Math.sin(t*3)*2,0,6.283); x.stroke();}
    drawCritter(x,a.key,px,y,11,a.color,t+i,on?mood(a):'idle',1,0);
    x.font='800 9.5px monospace'; x.fillStyle=on?'#E6E9EF':'#566074'; x.textAlign='left'; x.fillText(a.key,px+r+8,y-2); x.font='8px monospace'; x.fillStyle=on?c:'#3a4150';
    x.fillText(on?(a.status==='pass'?'✓ '+(a.ms||0)+'ms':a.status==='block'?'✕ STOP':'● INFO'):'—',px+r+8,y+9);});}
// --- EL PRICER: tablero de Galton con la distribución real ---
let GAL=null;
function initGal(){const {w,h}=cv.cGal; const rows=12, top=50, L=86, R=w-34, bot=h-118; const hist=(D.tm&&D.tm.hist&&D.tm.hist.reduce((a,b)=>a+b,0))?D.tm.hist:[1,2,4,7,10,12,11,9,6,4,2,1,1];
  const tot=hist.reduce((a,b)=>a+b,0); GAL={rows,top,L,R,bot,hist,tot,balls:[],bins:new Array(hist.length).fill(0),spawn:0};}
function pickBin(){let r=Math.random()*GAL.tot; for(let i=0;i<GAL.hist.length;i++){r-=GAL.hist[i]; if(r<=0) return i;} return GAL.hist.length-1;}
function drawGal(t,dt){const {x,w,h}=cv.cGal; x.clearRect(0,0,w,h); const G=GAL, nb=G.hist.length, cx=(G.L+G.R)/2, rowH=(G.bot-G.top)/(G.rows+1), sp=(G.R-G.L)/(G.rows+2);
  // Columna izquierda de datos
  const boxes=[['PRECIO',T?'$'+fm(T.c[T.c.length-1],T.c[T.c.length-1]<10?4:2):'—','#3DDC97'],['STOP',D.plan?'$'+fm(D.plan.stop,D.plan.stop<10?4:2):'—','#FF4D6D'],
    ['VOL (σ)',T?fm(T.sd,T.sd<1?4:2):'—','#B388FF'],['T-MINUS',tminus(),'#F5A623']];
  boxes.forEach((b,i)=>{const y=34+i*48; x.fillStyle='rgba(6,8,13,.9)'; x.strokeStyle=rgba(b[2],.45); x.beginPath(); x.roundRect(8,y,72,40,6); x.fill(); x.stroke();
    x.font='7.5px monospace'; x.fillStyle='#566074'; x.textAlign='left'; x.fillText(b[0],14,y+12); x.font='800 10.5px monospace'; x.fillStyle=b[2]; x.fillText(b[1],14,y+29);});
  // Clavos
  for(let r=0;r<G.rows;r++) for(let k=0;k<=r;k++){const px=cx+(k-r/2)*sp, py=G.top+(r+1)*rowH; x.fillStyle='rgba(242,210,122,'+(.25+.2*Math.sin(t*2+r+k))+')'; x.beginPath(); x.arc(px,py,1.6,0,6.283); x.fill();}
  x.fillStyle='#F2D27A'; x.beginPath(); x.moveTo(cx-6,G.top-6); x.lineTo(cx+6,G.top-6); x.lineTo(cx,G.top+4); x.fill(); MAP.gal={cx,top:G.top,w:G.R-G.L};
  // Bolitas
  G.spawn+=dt; while(G.spawn>.07){G.spawn-=.07; const bin=pickBin(); const k=Math.round(bin*G.rows/(nb-1));
    const steps=[...Array(G.rows)].map((_,i)=>i<k); for(let i=steps.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1)); [steps[i],steps[j]]=[steps[j],steps[i]];}
    const cum=[0]; steps.forEach(st=>cum.push(cum[cum.length-1]+(st?1:0))); G.balls.push({bin,t:0,cum});}
  const XA=r=>cx; G.balls.forEach(b=>{b.t+=dt*6; const r=Math.min(G.rows,b.t), ri=Math.min(G.rows-1,Math.floor(r)), fr=Math.min(1,r-ri);
    const xa=(b.cum[ri]-ri/2)*sp, xb=(b.cum[Math.min(G.rows,ri+1)]-(ri+1)/2)*sp, e=fr*fr*(3-2*fr);
    const px=cx+xa+(xb-xa)*e, py=G.top+(ri+fr)*rowH+rowH*.5-Math.sin(Math.PI*fr)*rowH*.35;
    const c=b.bin<3?'#FF4D6D':b.bin<4?'#F5A623':b.bin>=6?'#3DDC97':'#E6E9EF'; x.fillStyle=c; x.shadowColor=c; x.shadowBlur=7; x.beginPath(); x.arc(px,py,2.3,0,6.283); x.fill(); x.shadowBlur=0;
    if(b.t>=G.rows+.4){G.bins[b.bin]++; b.dead=1;}}); G.balls=G.balls.filter(b=>!b.dead).slice(-200);
  // Histograma
  const yb=h-48, mx=Math.max(...G.bins)||1; G.bins.forEach((v,i)=>{const px=cx+(i-(nb-1)/2)*sp*(G.rows/(nb-1)), bh=v/mx*(yb-G.bot-14); const c=i<3?'#FF4D6D':i<4?'#F5A623':i>=6?'#3DDC97':'#8C95A8';
    const g=x.createLinearGradient(0,yb-bh,0,yb); g.addColorStop(0,rgba(c,.95)); g.addColorStop(1,rgba(c,.25)); x.fillStyle=g; x.fillRect(px-sp*.34,yb-bh,sp*.68,bh);});
  if(G.bins.reduce((a,b)=>a+b,0)>900) G.bins=G.bins.map(v=>v*.5);
  const sc2=sp*(G.rows/(nb-1)); const tot=G.hist.reduce((a,b)=>a+b,0)||1, mu=G.hist.reduce((a,b,i)=>a+b*i,0)/tot, va=G.hist.reduce((a,b,i)=>a+b*(i-mu)*(i-mu),0)/tot, sg=Math.sqrt(va)||1;
  const peak=Math.max(...G.hist)/tot; x.beginPath(); for(let k=0;k<=80;k++){const ib=k/80*(nb-1), gy=Math.exp(-((ib-mu)**2)/(2*sg*sg))/(sg*2.5066)/peak, px=cx+(ib-(nb-1)/2)*sc2, py=yb-Math.min(1.1,gy)*(yb-G.bot-14); k?x.lineTo(px,py):x.moveTo(px,py);}
  x.strokeStyle='rgba(230,233,239,.75)'; x.lineWidth=1.3; x.shadowColor='#fff'; x.shadowBlur=6; x.stroke(); x.shadowBlur=0; x.lineWidth=1;
  x.font='7.5px monospace'; x.fillStyle='#8C95A8'; x.textAlign='center'; x.fillText('μ '+(-1.5+(mu+.5)*5/nb).toFixed(2)+'R · σ '+(sg*5/nb).toFixed(2)+'R · n '+(D.tm?fm(D.tm.sims,0):'—')+' · ¼K '+(D.tm?(D.tm.kelly*25).toFixed(2)+'%':'—'),cx,G.top-14);
  x.strokeStyle='rgba(242,210,122,.5)'; x.setLineDash([3,3]); const z0=cx+(3-(nb-1)/2)*sc2; x.beginPath(); x.moveTo(z0,G.top); x.lineTo(z0,yb); x.stroke(); x.setLineDash([]);
  x.font='7.5px monospace'; x.fillStyle='#566074'; x.textAlign='center'; x.fillText('−1.5R',cx-(nb-1)/2*sc2,yb+10); x.fillText('0R',z0,yb+10); x.fillText('+3.5R',cx+(nb-1)/2*sc2,yb+10);
  // Barra vertical P(TP1)
  const p=(D.prob||0)/100, bx=w-20, by0=40, by1=yb; x.fillStyle='#161b27'; x.beginPath(); x.roundRect(bx,by0,10,by1-by0,5); x.fill();
  const g=x.createLinearGradient(0,by1,0,by0); g.addColorStop(0,OK); g.addColorStop(1,BAD); x.fillStyle=g; x.beginPath(); x.roundRect(bx,by1-(by1-by0)*p,10,(by1-by0)*p,5); x.fill();
  x.strokeStyle='#fff'; x.beginPath(); x.moveTo(bx-4,by1-(by1-by0)*.55); x.lineTo(bx+14,by1-(by1-by0)*.55); x.stroke();
  // Pie
  const f=[['P(TP1)',D.prob!=null?D.prob.toFixed(1)+'%':'—',OK],['P(SL)',D.tm?D.tm.p_sl+'%':'—',BAD],['EV',D.tm?usd(D.tm.ev,true):'—',D.tm&&D.tm.ev<0?BAD:GOLD],['¼ KELLY',D.tm?(D.tm.kelly*25).toFixed(2)+'%':'—','#5AC8FA']];
  f.forEach((q,i)=>{const fx=12+i*(w-24)/4; x.font='7.5px monospace'; x.fillStyle='#566074'; x.textAlign='left'; x.fillText(q[0],fx,h-14); x.font='800 12px monospace'; x.fillStyle=q[2]; x.fillText(q[1],fx,h-2);});}
function tminus(){const s=Math.floor(Date.now()/1000)%300, r=300-s; return 'T-'+Math.floor(r/60)+':'+String(r%60).padStart(2,'0');}
// --- CORDILLERAS DE SEÑALES ---
function drawRid(t){const {x,w,h}=cv.cRid; x.clearRect(0,0,w,h); const R=D.ridges||[]; if(!R.length){x.fillStyle='#566074'; x.font='10px monospace'; x.textAlign='center'; x.fillText('CORRE LA MESA PARA VER LAS SEÑALES',w/2,h/2); return;}
  const LW=Math.min(150,w*.3), L=10, Rr=w-LW-10, top=66, bot=h-24, n=R[0].v.length, step=(bot-top)/(R.length+1.5), amp=step*2.4;
  const hot=R.reduce((b,r,i)=>r.now>R[b].now?i:b,0); const reveal=Math.min(1,t/1.8);
  const hd=[[R.filter(r=>r.now>.6).length+'/'+R.length,'ACTIVAS','#E6E9EF'],[String(R.filter(r=>r.now>.8).length),'CALIENTES','#F5A623'],[R[hot].name.slice(0,12).toUpperCase()+' '+R[hot].now.toFixed(2),'CRESTA','#F2D27A']];
  hd.forEach((q,i)=>{const hx=12+i*(i===2?78:74); x.textAlign='left'; x.font='800 13px monospace'; x.fillStyle=q[2]; x.fillText(q[0],hx,42); x.font='7.5px monospace'; x.fillStyle='#566074'; x.fillText(q[1],hx,52);});
  MAP.ridge=R[hot].v.map((v,k)=>[L+(Rr-L)*k/(n-1),top+(hot+1.2)*step-v*amp]);
  R.forEach((r,i)=>{const base=top+(i+1.2)*step, c=i===hot?GOLD:(i%2?'#B388FF':'#F5A623'); const m=Math.floor(n*reveal);
    x.beginPath(); x.moveTo(L,base); for(let k=0;k<m;k++){const px=L+(Rr-L)*k/(n-1), py=base-r.v[k]*amp*(1+(i===hot?.06*Math.sin(t*3+k*.2):0)); x.lineTo(px,py);} x.lineTo(L+(Rr-L)*(m-1)/(n-1),base); x.closePath();
    x.fillStyle='#080b12'; x.fill(); x.strokeStyle=rgba(c,i===hot?1:.75); x.lineWidth=i===hot?1.8:1.1; if(i===hot){x.shadowColor=GOLD; x.shadowBlur=12;} x.stroke(); x.shadowBlur=0;});
  const sx=L+((t*.12)%1)*(Rr-L); x.strokeStyle='rgba(90,200,250,.35)'; x.beginPath(); x.moveTo(sx,top); x.lineTo(sx,bot); x.stroke();
  const hb=top+(hot+1.2)*step-R[hot].now*amp; x.fillStyle='rgba(5,7,11,.9)'; x.strokeStyle=GOLD; x.beginPath(); x.roundRect(Rr-118,hb-22,112,16,6); x.fill(); x.stroke();
  x.fillStyle=GOLD; x.font='700 8px monospace'; x.fillText('CRESTA · '+R[hot].name.slice(0,14).toUpperCase(),Rr-112,hb-11);
  R.forEach((r,i)=>{const y=top+i*((bot-top)/R.length)+8; x.fillStyle=i===hot?GOLD:'#8C95A8'; x.font='8px monospace'; x.textAlign='left'; x.fillText(r.name.slice(0,15),w-LW+2,y);
    x.fillStyle='#161b27'; x.fillRect(w-LW+2,y+3,LW-14,4); x.fillStyle=i===hot?GOLD:(r.now>.6?'#F5A623':'#B388FF'); x.fillRect(w-LW+2,y+3,(LW-14)*r.now*(.95+.05*Math.sin(t*2+i)),4);});}
// --- Actividad de la traza (ms por agente) ---
function drawAct(t){const o=cv.cAct; if(!o) return; const {x,w,h}=o; x.clearRect(0,0,w,h); const v=D.agents.map(a=>a.ms||0), mx=Math.max(10,...v);
  x.strokeStyle='rgba(148,163,184,.15)'; x.strokeRect(.5,.5,w-1,h-1); x.beginPath(); v.forEach((m,i)=>{const px=4+(w-8)*i/(v.length-1), py=h-5-(m/mx)*(h-12)*(.85+.15*Math.sin(t*3+i)); i?x.lineTo(px,py):x.moveTo(px,py);});
  x.strokeStyle='#5AC8FA'; x.lineWidth=1.4; x.stroke(); x.lineTo(w-4,h-4); x.lineTo(4,h-4); x.closePath(); x.fillStyle='rgba(90,200,250,.12)'; x.fill();
  x.font='7px monospace'; x.fillStyle='#566074'; x.textAlign='right'; x.fillText('ACTIVIDAD · MS',w-4,9);}
// --- Agentes que viajan a su panel ---
const STATION={WHALE:['tapeP',.88,.6],FLOW:['tapeP',.6,.6],FUNDING:['pnlP',.5,.5],GATE:['ridP',.6,.5],GUARD:['tapeP',.7,.7],SPOTTER:['tapeP',.33,.62],PRIOR:['ridP',.42,.5],PRICER:['galP',.55,.32],EDGE:['tapeP',.88,.52],KELLY:['pnlP',.72,.58],TAKER:['tapeP',.88,.32],CLOSER:['traceP',.62,.66]};
function along(pts,u){if(!pts||!pts.length) return null; const f=Math.max(0,Math.min(1,u))*(pts.length-1), k=Math.floor(f), q=pts[Math.min(pts.length-1,k+1)], p=pts[k], e=f-k;
  return [p[0]+(q[0]-p[0])*e,p[1]+(q[1]-p[1])*e];}
function workPos(a,u,t,pr){const k=a.key, P=(fx,fy)=>[pr.x+pr.w*fx,pr.y+pr.h*fy];
  if(k==='SPOTTER'){const q=along(MAP.tape,.15+u*.8); return q?[pr.x+q[0],pr.y+q[1]-30]:P(.2+u*.5,.6);}
  if(k==='EDGE'){const L=MAP.ladder; if(!L) return P(.88,.5); const row=(Math.sin(u*Math.PI*3-Math.PI/2)+1)/2*(L.n-1); return [pr.x+L.x0+L.w*.35,pr.y+L.top+row*L.rh+L.rh/2];}
  if(k==='TAKER'){const L=MAP.ladder; if(!L) return P(.88,.35); const mid=(L.n-1)/2, dip=(D.direction==='LONG'?1:-1)*Math.sin(u*Math.PI)*3; return [pr.x+L.x0+L.w*.55,pr.y+L.top+(mid+dip)*L.rh+L.rh/2];}
  if(k==='PRIOR'){const q=along(MAP.ridge,.1+u*.85); return q?[pr.x+q[0],pr.y+q[1]-24]:P(.2+u*.5,.5);}
  if(k==='PRICER'){const G=MAP.gal; return G?[pr.x+G.cx+Math.sin(t*2.2)*G.w*.18,pr.y+G.top-4]:P(.5,.3);}
  if(k==='KELLY'){const q=along(MAP.pnl,.1+u*.85); return q?[pr.x+q[0],pr.y+q[1]-26]:P(.15+u*.7,.55);}
  if(k==='WHALE'){const L=MAP.ladder; if(!L) return P(.85,.6); const row=(Math.cos(u*Math.PI*2)+1)/2*(L.n-1); return [pr.x+L.x0+L.w*.15,pr.y+L.top+row*L.rh+L.rh/2];}
  if(k==='FLOW'){const q=along(MAP.tape,.55+u*.4); return q?[pr.x+q[0],pr.y+q[1]+34]:P(.5+u*.3,.7);}
  if(k==='FUNDING'){const q=along(MAP.pnl,.85-u*.7); return q?[pr.x+q[0],pr.y+q[1]-26]:P(.8-u*.6,.5);}
  if(k==='GATE'){const q=along(MAP.ridge,.9-u*.8); return q?[pr.x+q[0],pr.y+q[1]-24]:P(.8-u*.6,.45);}
  if(k==='GUARD'){return P(.12+u*.6,.86+Math.sin(u*9)*.02);}
  if(k==='CLOSER'){const n=Math.max(1,act.length); return [pr.x+pr.w*.06,pr.y+95+u*Math.min(pr.h-130,n*46)];}
  return P(.5,.5);}
function rel(id){const T0=$('T').getBoundingClientRect(), r=$(id).getBoundingClientRect(); return {x:r.left-T0.left,y:r.top-T0.top,w:r.width,h:r.height};}
function drawFly(t){const o=cv.cFly; const {x,w,h}=o; x.clearRect(0,0,w,h); const orb=rel('orbP');
  const crew=D.agents.map((a,i)=>({a,i})).filter(q=>q.a.status!=='idle'); const team=crew.length?crew:D.agents.map((a,i)=>({a,i}));
  const CYC=3.6, STAG=1.25; team.forEach((q,k)=>{const a=q.a, st=STATION[a.key]; if(!st) return; const ph=((t-k*STAG)%(CYC*team.length*STAG/ (team.length*STAG/CYC)) );
    const local=(t-k*STAG)-Math.floor((t-k*STAG)/(STAG*team.length))*(STAG*team.length); if(local<0||local>CYC) return;
    const hm=HOME[q.i]||[orb.w/2,orb.h*.6]; const H=[orb.x+hm[0],orb.y+hm[1]]; const pr=rel(st[0]);
    let px,py,phase; const fly=.95, back=.8, stay=CYC-fly-back; const S=workPos(a,0,t,pr), E=workPos(a,1,t,pr);
    const arc=(A,B,u)=>{const e=u<.5?4*u*u*u:1-Math.pow(-2*u+2,3)/2, mx=(A[0]+B[0])/2, my=Math.min(A[1],B[1])-120; return [(1-e)*(1-e)*A[0]+2*(1-e)*e*mx+e*e*B[0],(1-e)*(1-e)*A[1]+2*(1-e)*e*my+e*e*B[1]];};
    if(local<fly){[px,py]=arc(H,S,local/fly); phase=0;}
    else if(local<fly+stay){const u=(local-fly)/stay; [px,py]=workPos(a,u,t,pr); py+=Math.sin(t*5+k)*2; phase=1;}
    else {[px,py]=arc(E,H,(local-fly-stay)/back); phase=2;}
    const c=a.color, sc=a.status==='pass'?OK:a.status==='block'?BAD:a.status==='info'?a.color:'#8C95A8';
    // Estela
    if(phase!==1){for(let j=1;j<10;j++){const lu=Math.max(0,(phase===0?local:local-fly-stay)-j*.025), q2=phase===0?arc(H,S,lu/fly):arc(E,H,lu/back);
      x.fillStyle=rgba(c,.5*(1-j/10)); x.beginPath(); x.arc(q2[0],q2[1],7*(1-j/12),0,6.283); x.fill();}}
    // Panel iluminado mientras trabaja
    if(phase===1){const g=Math.min(1,(local-fly)/.3)*Math.min(1,(fly+stay-local)/.3); x.strokeStyle=rgba(c,.75*g); x.lineWidth=2; x.shadowColor=c; x.shadowBlur=22*g;
      x.beginPath(); x.roundRect(pr.x+1,pr.y+1,pr.w-2,pr.h-2,12); x.stroke(); x.shadowBlur=0; x.lineWidth=1;
      x.strokeStyle=rgba(c,.55*g); x.setLineDash([3,4]); x.beginPath(); x.arc(px,py,26+Math.sin(t*4)*3,0,6.283); x.stroke(); x.setLineDash([]);
      for(let r=0;r<2;r++){const kk=((t*1.2+r*.5)%1); x.strokeStyle=rgba(c,(1-kk)*.6*g); x.beginPath(); x.arc(px,py,16+kk*34,0,6.283); x.stroke();}}
    // Avatar
    if(phase===1){ // Haz de escaneo hacia los datos + burbuja de pensamiento
      const bx=px, by=py+20, g2=x.createLinearGradient(bx,by,bx,by+60); g2.addColorStop(0,rgba(c,.35)); g2.addColorStop(1,rgba(c,0));
      x.fillStyle=g2; x.beginPath(); x.moveTo(bx-4,by); x.lineTo(bx+4,by); x.lineTo(bx+26,by+60); x.lineTo(bx-26,by+60); x.closePath(); x.fill();
      for(let d=0;d<3;d++){const ph2=((t*1.6+d*.33)%1); x.fillStyle=rgba('#E6E9EF',(1-ph2)*.8); x.beginPath(); x.arc(px+20+d*7,py-22-ph2*16-d*4,1.6+d*.9,0,6.283); x.fill();}}
    const R=17; drawCritter(x,a.key,px,py,R,c,t+k,phase===1?mood(a):'info',phase===0?(S[0]-px)/80:phase===2?(H[0]-px)/80:0,phase===1?.6:0);
    // Etiqueta con la tarea y el dato
    if(phase===1){const l1=a.key+' · '+(a.task||''), l2=(a.status==='block'?'✕ ':'')+(a.value||''); x.font='800 9px monospace'; const lw=Math.max(x.measureText(l1).width,x.measureText(l2).width)+18;
      const lx=Math.min(w-lw-4,Math.max(4,px-lw/2)), ly=py-58; x.fillStyle='rgba(5,7,11,.92)'; x.strokeStyle=rgba(c,.85); x.beginPath(); x.roundRect(lx,ly,lw,32,8); x.fill(); x.stroke();
      x.fillStyle=c; x.textAlign='left'; x.fillText(l1,lx+9,ly+13); x.font='9px monospace'; x.fillStyle=a.status==='block'?BAD:'#E6E9EF'; x.fillText(l2,lx+9,ly+26);
      x.strokeStyle=rgba(c,.6); x.beginPath(); x.moveTo(px,py-R-3); x.lineTo(px,ly+32); x.stroke();}});}
// --- Bucle ---
initOrb(); initGal(); let prev=performance.now(), lastSec=-1;
function frame(now){const t=Math.max(0,(now-t0)/1000), dt=Math.max(0,Math.min(.05,(now-prev)/1000)); prev=now; $('clk').textContent=new Date().toISOString().slice(11,19);
  const s=Math.floor(Date.now()/1000)%300; if(s!==lastSec){lastSec=s; $('tmin').textContent=tminus();
    $('tdash').innerHTML=[...Array(30)].map((_,i)=>'<div style="flex:1;height:5px;border-radius:2px;background:'+(i<s/10?'#F2D27A':'#1b2130')+'"></div>').join('');}
  drawPnl(t); drawTape(t); drawOrb(t); drawPipe(t); drawGal(t,dt); drawRid(t); drawAct(t); drawFly(t); requestAnimationFrame(frame);}
requestAnimationFrame(frame);
</script></body></html>"""
