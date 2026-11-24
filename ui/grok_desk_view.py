"""
ui/grok_desk_view.py
====================
Terminal animada de GROK DESK. Todo el dibujo vive en un iframe (canvas): Streamlit no
recalcula cada fotograma. Los números salen del programa. Si no hay dato, se pinta «n/a».
"""

from __future__ import annotations

import json
import math

from engine.fgview.grok_team import TEAM

_SHORT = {
    "G-SCOUT": "SC", "G-MARKET": "MK", "G-WHALE": "WH", "G-FLOW": "FL", "G-FUNDING": "FU",
    "G-TRAP": "TR", "G-ODDS": "OD", "G-RISK": "RK", "G-JUDGE": "JU", "G-EXEC": "EX", "G-WATCH": "WA",
}
_VOTERS = {"G-MARKET", "G-WHALE", "G-FLOW", "G-FUNDING", "G-TRAP", "G-ODDS"}


def _num(value, nd: int = 4):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, nd)


def _series(df, column: str, n: int = 56) -> list | None:
    if df is None or column not in getattr(df, "columns", []):
        return None
    out = []
    for value in df[column].tail(n):
        try:
            number = float(value)
        except (TypeError, ValueError):
            out.append(None)
            continue
        out.append(None if not math.isfinite(number) else round(number, 6))
    return out if any(v is not None for v in out) else None


def _candles(df, n: int = 56) -> list | None:
    if df is None or len(df) == 0:
        return None
    rows = []
    for _, bar in df.tail(n).iterrows():
        try:
            rows.append([round(float(bar["open"]), 6), round(float(bar["high"]), 6),
                         round(float(bar["low"]), 6), round(float(bar["close"]), 6)])
        except (TypeError, ValueError, KeyError):
            continue
    return rows or None


def _origin(text: str | None) -> str:
    return "Grok" if "GROK" in str(text or "").upper() else "manual"


def build_desk_payload(svc, br) -> dict:
    """Junta lo que la terminal puede mostrar. Nada de esta función inventa un precio."""
    coach, cutoff, tz = {}, "n/a", "UTC"
    try:
        coach = svc.copilot.coach.status()
        vals = svc.copilot.rules.values()
        cutoff = str(vals.get("cutoff") or "n/a")
        tz = str(vals.get("tz") or "UTC")
    except Exception:  # noqa: BLE001
        coach = {}
    paper = {}
    try:
        paper = svc.autopilot.paper_stats()
    except Exception:  # noqa: BLE001
        paper = {}
    snap, source, symbol = None, "n/a", "n/a"
    dec = getattr(br, "last_dec", None)
    snap = getattr(dec, "snap", None) if dec is not None else None
    if snap is not None:
        symbol = getattr(dec, "symbol", None) or "n/a"
        source = "DEMO (datos de prueba)" if str(getattr(snap, "source", "")).lower() == "demo" else (
            str(getattr(snap, "source", "") or "FG VIEW"))
    else:
        try:
            snap = svc.loader.load("DEMO/USDT", "demo")
            symbol, source = "DEMO/USDT", "DEMO (datos de prueba)"
        except Exception:  # noqa: BLE001
            snap = None
    df = snap.df5 if snap is not None else None
    book = (getattr(snap, "order_book", None) or {}) if snap is not None else {}
    has_book = book.get("bid") is not None or book.get("ask") is not None or book.get("imbalance") is not None
    last = None
    try:
        last = br.team.latest()
    except Exception:  # noqa: BLE001
        last = None
    votes = (last or {}).get("votos") or {}
    judge = (last or {}).get("juez") or {}
    agents = []
    for meta in TEAM:
        key = meta["key"]
        item = votes.get(key) or {}
        vote = item.get("voto") if key in _VOTERS and item else None
        agents.append({"key": key, "short": _SHORT.get(key, key[-2:]), "vote": vote,
                       "line": (item.get("linea") or "n/a") if item else "n/a",
                       "question": meta["pregunta"]})
    equity = None
    try:
        pnls = [float(t.get("pnl") or 0) for t in (svc.journal.entries() if svc.journal else [])]
        if pnls:
            acc, curve = 0.0, []
            for pnl in pnls[-80:]:
                acc += pnl
                curve.append(round(acc, 4))
            equity = curve
    except Exception:  # noqa: BLE001
        equity = None
    closes = _series(df, "close")
    watch = []
    try:
        for pos in svc.positions.all():
            watch.append({"coin": pos.get("coin") or "n/a", "price": _num(pos.get("entry")),
                          "spark": None, "tag": _origin(pos.get("strategy")), "chg": None})
    except Exception:  # noqa: BLE001
        watch = []
    if symbol and symbol != "n/a":
        coin = str(symbol).split("/")[0]
        if not any(w["coin"] == coin for w in watch):
            chg = None
            if closes and closes[0] not in (None, 0) and closes[-1] is not None:
                chg = round((closes[-1] / closes[0] - 1) * 100, 2)
            watch.insert(0, {"coin": coin, "price": _num(getattr(snap, "price", None)),
                             "spark": closes[-24:] if closes else None, "tag": source, "chg": chg})
    ops = []
    try:
        for pos in svc.positions.all():
            tps = pos.get("tps") or []
            tp1 = tps[0][0] if tps else None
            ops.append({"when": str(pos.get("opened_at") or "")[11:19] or "n/a", "coin": pos.get("coin"),
                        "side": pos.get("direction"), "entry": _num(pos.get("entry")),
                        "sl": _num(pos.get("stop_loss")), "tp1": _num(tp1), "lev": pos.get("leverage"),
                        "risk": _num(abs(pos["sl_pnl"])) if pos.get("sl_pnl") not in (None, "") else None,
                        "pnl": None, "r": None, "state": "abierta", "who": _origin(pos.get("strategy"))})
    except Exception:  # noqa: BLE001
        ops = []
    try:
        for trade in (svc.journal.entries() if svc.journal else [])[-12:]:
            ops.append({"when": str(trade.get("opened_at") or "")[11:19] or "n/a", "coin": trade.get("coin"),
                        "side": trade.get("direction"), "entry": _num(trade.get("entry")), "sl": None, "tp1": None,
                        "lev": trade.get("leverage"), "risk": None, "pnl": _num(trade.get("pnl"), 2), "r": None,
                        "state": "cerrada", "who": _origin(trade.get("notes"))})
    except Exception:  # noqa: BLE001
        pass
    verdicts = []
    try:
        for item in (br.state().get("verdicts") or [])[-10:]:
            verdicts.append({"at": item.get("at") or "n/a", "symbol": item.get("simbolo") or "n/a",
                             "side": item.get("direccion") or "n/a", "veredicto": item.get("veredicto") or "n/a",
                             "suma": item.get("suma"), "confianza": item.get("confianza"),
                             "resumen": (item.get("resumen") or item.get("nota") or "n/a")[:140]})
    except Exception:  # noqa: BLE001
        verdicts = []
    every = getattr(getattr(svc, "autopilot", None), "every_s", None)
    return {
        "bankroll": _num(coach.get("bankroll"), 2),
        "pnl": _num(coach.get("pnl_today"), 2),
        "pnl_pct": _num(coach.get("pnl_pct"), 2),
        "trades_today": coach.get("trades_today"),
        "max_trades": coach.get("max_trades"),
        "open": coach.get("open"),
        "max_open": coach.get("max_open"),
        "streak": coach.get("streak"),
        "paused": coach.get("blocked"),
        "cutoff": cutoff,
        "tz": tz,
        "interval_s": None if every is None else int(every),
        "auto": bool(br.auto_on()) if hasattr(br, "auto_on") else None,
        "mcp": "ENCENDIDO" if br.running() else "APAGADO",
        "paper_net": _num(paper.get("net"), 2) if paper.get("trades") else None,
        "paper_trades": paper.get("trades"),
        "symbol": symbol,
        "source": source,
        "price": _num(getattr(snap, "price", None)) if snap is not None else None,
        "candles": _candles(df),
        "ema9": _series(df, "ema_9"),
        "ema21": _series(df, "ema_21"),
        "rsi": _series(df, "rsi", 48),
        "book": None if not has_book else {
            "bid": _num(book.get("bid")), "ask": _num(book.get("ask")),
            "bid_vol": _num(book.get("bid_vol10"), 2), "ask_vol": _num(book.get("ask_vol10"), 2),
            "imbalance": _num(book.get("imbalance"), 3)},
        "equity": equity,
        "agents": agents,
        "judge": judge.get("veredicto"),
        "suma": (last or {}).get("suma"),
        "cripto": (last or {}).get("cripto"),
        "direccion": (last or {}).get("direccion"),
        "watch": watch,
        "ops": ops[-12:],
        "verdicts": list(reversed(verdicts)),
    }


def desk_html(payload: dict, height: int = 1040) -> str:
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return _TEMPLATE.replace("__DATA__", data).replace("__H__", str(int(height)))


_TEMPLATE = r"""
<div id="app">
  <header>
    <div class="brand"><span class="dot"></span> GROK DESK <small>USDT-M · PAPEL</small></div>
    <div class="clock"><b id="clk">--:--:--</b><span id="pills"></span></div>
  </header>
  <section class="kpis" id="kpis"></section>
  <section class="mid">
    <article class="card">
      <h3>BANKROLL · PAPEL</h3>
      <div class="big" id="big">n/a</div>
      <div class="sub" id="sub">PnL hoy n/a</div>
      <canvas id="eq" width="520" height="150"></canvas>
    </article>
    <article class="card orbit">
      <h3>EQUIPO GROK · EN ÓRBITA</h3>
      <canvas id="orb"></canvas>
    </article>
    <article class="card">
      <h3 id="ch">VELAS · n/a</h3>
      <canvas id="cd" width="520" height="168"></canvas>
      <div class="book" id="book"></div>
    </article>
  </section>
  <section class="low">
    <article class="card"><h3>PRESIÓN LIBRO</h3><canvas id="py" width="520" height="120"></canvas></article>
    <article class="card"><h3>OSCILADOR RSI</h3><canvas id="os" width="520" height="120"></canvas></article>
    <article class="card"><h3>WATCHLIST</h3><div id="watch" class="watch"></div></article>
  </section>
  <section class="votes" id="votes"></section>
  <section class="logs">
    <article class="card"><h3>VEREDICTOS GROK</h3><div id="vers" class="tbl"></div></article>
    <article class="card"><h3>OPERACIONES · PAPEL</h3><div id="ops" class="tbl"></div></article>
  </section>
</div>
<style>
  html,body{margin:0;background:#070b14;color:#d5deea;font-family:'Segoe UI',Inter,sans-serif}
  #app{padding:8px 10px 12px;background:radial-gradient(1200px 500px at 50% -10%,#14203a 0%,#070b14 55%)}
  header{display:flex;justify-content:space-between;align-items:center;height:34px;border-bottom:1px solid rgba(120,160,255,.18)}
  .brand{letter-spacing:.22em;font-weight:700;font-size:13px;color:#e8eef8}
  .brand small{letter-spacing:.14em;color:#7d8cab;font-weight:500;margin-left:8px}
  .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#3ddc97;box-shadow:0 0 10px #3ddc97;margin-right:8px}
  .clock{font-family:ui-monospace,monospace;font-size:12px;color:#9fb0c8;display:flex;gap:10px;align-items:center}
  .clock b{color:#f2d27a;font-size:14px}
  .pill{border:1px solid rgba(179,136,255,.45);border-radius:99px;padding:2px 8px;color:#d7c6ff}
  .pill.on{border-color:rgba(61,220,151,.6);color:#b9ffd8}
  .pill.off{border-color:rgba(255,77,109,.55);color:#ffb3c0}
  .kpis{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin:8px 0}
  .kpi{background:#0d1424;border:1px solid rgba(90,140,255,.22);border-radius:10px;padding:7px 9px;animation:glow 5.5s ease-in-out infinite}
  .kpi .k{font-size:10px;letter-spacing:.12em;color:#7f8da6}
  .kpi .v{font-family:ui-monospace,monospace;font-size:16px;margin-top:2px}
  .mid{display:grid;grid-template-columns:1.05fr 1.25fr .95fr;gap:8px}
  .low{display:grid;grid-template-columns:1fr 1fr .8fr;gap:8px;margin-top:8px}
  .card{background:linear-gradient(180deg,rgba(16,24,42,.96),rgba(8,12,22,.96));border:1px solid rgba(110,150,255,.22);
        border-radius:12px;padding:8px 10px;position:relative;overflow:hidden;animation:glow 6.5s ease-in-out infinite}
  .card h3{margin:0 0 4px;font-size:10px;letter-spacing:.16em;color:#8ea0be;font-weight:600}
  .big{font-family:ui-monospace,monospace;font-size:34px;line-height:1;color:#f4f7fb;text-shadow:0 0 18px rgba(61,220,151,.25)}
  .sub{font-family:ui-monospace,monospace;font-size:12px;margin:4px 0 2px}
  .up{color:#3ddc97}.dn{color:#ff4d6d}.flat{color:#9aa8be}
  canvas{width:100%;display:block}
  .orbit canvas{height:248px}
  .book{font-family:ui-monospace,monospace;font-size:11px;margin-top:4px}
  .book div{display:flex;justify-content:space-between;padding:2px 4px;border-radius:4px}
  .bid{color:#3ddc97;animation:blink 1.6s steps(2) infinite}
  .ask{color:#ff4d6d;animation:blink 1.9s steps(2) infinite}
  .watch{font-family:ui-monospace,monospace;font-size:11px}
  .watch .row{display:grid;grid-template-columns:18px 54px 1fr 70px;gap:6px;align-items:center;padding:3px 0;border-bottom:1px solid rgba(255,255,255,.04)}
  .votes{display:grid;grid-template-columns:repeat(6,1fr);gap:6px;margin-top:8px}
  .vcard{background:#0c1322;border-radius:10px;padding:6px 8px;border:1px solid rgba(255,255,255,.06);min-height:52px}
  .vcard b{font-size:11px;letter-spacing:.08em}
  .vcard span{display:block;color:#93a0b5;font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .logs{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px}
  .tbl{font-family:ui-monospace,monospace;font-size:10.5px;max-height:132px;overflow:auto}
  .tbl .r{display:grid;grid-template-columns:52px 64px 52px 78px 1fr;gap:4px;padding:3px 0;border-bottom:1px solid rgba(255,255,255,.04)}
  .badge{padding:0 5px;border-radius:6px}
  .APRUEBA{color:#04140c;background:#3ddc97}.PRECAUCION{color:#1a1203;background:#f5a623}.VETA{color:#fff;background:#ff4d6d}
  @keyframes glow{0%,100%{box-shadow:0 0 0 1px rgba(61,220,151,.18),0 0 16px rgba(61,220,151,.05)}50%{box-shadow:0 0 0 1px rgba(179,136,255,.55),0 0 20px rgba(179,136,255,.16)}}
  @keyframes blink{50%{opacity:.45}}
</style>
<script>
const D = __DATA__;
const $=id=>document.getElementById(id);
const na=v=>v===null||v===undefined||v===""?"n/a":v;
const money=v=>{if(v===null||v===undefined)return "n/a";const s=Math.abs(v).toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2});return (v<0?"−":"")+"$"+s;};
const col=v=>v>0?"up":v<0?"dn":"flat";
function tickClock(){
  try{ $("clk").textContent=new Intl.DateTimeFormat("es-MX",{timeZone:D.tz||"UTC",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(new Date()); }
  catch(e){ $("clk").textContent=new Date().toISOString().slice(11,19)+" UTC"; }
}
tickClock(); setInterval(tickClock,1000);
$("pills").innerHTML=
  `<span class="pill ${D.mcp==="ENCENDIDO"?"on":"off"}">MCP ${D.mcp}</span>`+
  `<span class="pill ${D.auto?"on":"off"}">AUTO ${D.auto?"ON":"OFF"}</span>`+
  `<span class="pill ${D.paused?"off":"on"}">COACH ${D.paused?"PAUSA":"LIBRE"}</span>`;
const kpis=[
  ["BANKROLL", money(D.bankroll), "flat"],
  ["PNL HOY", money(D.pnl)+(D.pnl_pct===null?"":` (${D.pnl_pct}%)`), col(D.pnl||0)],
  ["HOY", `${na(D.trades_today)}/${na(D.max_trades)}`, "flat"],
  ["ABIERTAS", `${na(D.open)}/${na(D.max_open)}`, "flat"],
  ["RACHA", na(D.streak), "flat"],
  ["HORA LÍMITE", na(D.cutoff), "flat"],
];
$("kpis").innerHTML=kpis.map((k,i)=>`<div class="kpi" style="animation-delay:${i*.25}s"><div class="k">${k[0]}</div><div class="v ${k[2]}">${k[1]}</div></div>`).join("");
function countTo(el, value){
  if(value===null||value===undefined){el.textContent="n/a";return;}
  const t0=performance.now(), from=value*0.92;
  const step=now=>{const p=Math.min(1,(now-t0)/420); const x=from+(value-from)*(1-Math.pow(1-p,3));
    el.textContent=money(x); if(p<1) requestAnimationFrame(step);};
  requestAnimationFrame(step);
}
countTo($("big"), D.bankroll);
$("sub").innerHTML=`<span class="${col(D.pnl||0)}">PnL hoy ${money(D.pnl)}</span> · papel neto ${money(D.paper_net)} · intervalo ${na(D.interval_s)}s · ${D.source}`;
function lineChart(cv, series, color){
  const x=cv.getContext("2d"), w=cv.width, h=cv.height;
  x.clearRect(0,0,w,h);
  const pts=(series||[]).filter(v=>v!==null&&v!==undefined);
  if(pts.length<2){x.fillStyle="#7f8da6";x.font="12px ui-monospace,monospace";x.fillText("n/a",12,h/2);return;}
  const lo=Math.min(...pts), hi=Math.max(...pts), span=(hi-lo)||1;
  x.beginPath();
  pts.forEach((v,i)=>{const px=i/(pts.length-1)*(w-8)+4, py=h-8-(v-lo)/span*(h-16); i?x.lineTo(px,py):x.moveTo(px,py);});
  const g=x.createLinearGradient(0,0,0,h); g.addColorStop(0,color+"55"); g.addColorStop(1,"transparent");
  x.strokeStyle=color; x.lineWidth=1.6; x.stroke();
  x.lineTo(w-4,h); x.lineTo(4,h); x.closePath(); x.fillStyle=g; x.fill();
}
lineChart($("eq"), D.equity, (D.pnl||0)<0?"#ff4d6d":"#3ddc97");
$("ch").textContent=`VELAS · ${D.symbol} · ${D.source}`;
function candles(cv){
  const x=cv.getContext("2d"), w=cv.width, h=cv.height, rows=D.candles;
  x.clearRect(0,0,w,h);
  if(!rows||rows.length<2){x.fillStyle="#7f8da6";x.font="12px ui-monospace,monospace";x.fillText("n/a",12,h/2);return;}
  let lo=Infinity,hi=-Infinity;
  rows.forEach(r=>{lo=Math.min(lo,r[2]);hi=Math.max(hi,r[1]);});
  const span=(hi-lo)||1, n=rows.length, cw=w/n;
  const Y=v=>h-6-(v-lo)/span*(h-12);
  const ema=(arr,color)=>{ if(!arr) return; x.beginPath(); let started=false;
    arr.slice(-n).forEach((v,i)=>{ if(v===null){started=false;return;} const px=i*cw+cw/2, py=Y(v);
      started?x.lineTo(px,py):(x.moveTo(px,py),started=true); });
    x.strokeStyle=color; x.lineWidth=1.2; x.stroke(); };
  rows.forEach((r,i)=>{
    const [o,hh,l,c]=r, up=c>=o, px=i*cw+cw/2;
    x.strokeStyle=up?"#3ddc97":"#ff4d6d"; x.fillStyle=x.strokeStyle;
    x.beginPath(); x.moveTo(px,Y(hh)); x.lineTo(px,Y(l)); x.stroke();
    const y0=Y(Math.max(o,c)), y1=Y(Math.min(o,c));
    x.globalAlpha = i===n-1 ? (0.55+0.45*Math.abs(Math.sin(performance.now()/280))) : 0.95;
    x.fillRect(px-Math.max(1.2,cw*0.28), y0, Math.max(2.4,cw*0.56), Math.max(1,y1-y0));
    x.globalAlpha=1;
  });
  ema(D.ema9,"#f5a623"); ema(D.ema21,"#b388ff");
  requestAnimationFrame(()=>candles(cv));
}
candles($("cd"));
function book(){
  const b=D.book;
  if(!b){$("book").innerHTML='<div class="flat">libro n/a</div>';return;}
  const imb=b.imbalance===null?"n/a":(b.imbalance>0?"+":"")+b.imbalance;
  $("book").innerHTML=
    `<div class="ask"><span>ASK ${na(b.ask)}</span><span>${na(b.ask_vol)}</span></div>`+
    `<div class="flat">desequilibrio ${imb}</div>`+
    `<div class="bid"><span>BID ${na(b.bid)}</span><span>${na(b.bid_vol)}</span></div>`;
}
book();
function pyramid(){
  const cv=$("py"), x=cv.getContext("2d"), w=cv.width, h=cv.height, b=D.book;
  x.clearRect(0,0,w,h);
  if(!b||b.imbalance===null){x.fillStyle="#7f8da6";x.font="12px ui-monospace,monospace";x.fillText("n/a",12,h/2);return;}
  const buy=Math.max(0,Math.min(1,(b.imbalance+1)/2)), sell=1-buy;
  x.fillStyle="#3ddc97"; x.beginPath(); x.moveTo(w*0.5,18); x.lineTo(w*0.5-buy*w*0.42,h-16); x.lineTo(w*0.5,h-16); x.fill();
  x.fillStyle="#ff4d6d"; x.beginPath(); x.moveTo(w*0.5,18); x.lineTo(w*0.5+sell*w*0.42,h-16); x.lineTo(w*0.5,h-16); x.fill();
  x.fillStyle="#d5deea"; x.font="11px ui-monospace,monospace";
  x.fillText("COMPRA "+Math.round(buy*100)+"%", 12, 16);
  x.fillText("VENTA "+Math.round(sell*100)+"%", w-110, 16);
}
pyramid();
function osc(){
  const cv=$("os"), x=cv.getContext("2d"), w=cv.width, h=cv.height, s=(D.rsi||[]).filter(v=>v!==null);
  x.clearRect(0,0,w,h);
  if(s.length<2){x.fillStyle="#7f8da6";x.font="12px ui-monospace,monospace";x.fillText("n/a",12,h/2);return;}
  const Y=v=>h-8-(v/100)*(h-16);
  x.strokeStyle="rgba(255,255,255,.12)"; x.strokeRect(8,Y(70),w-16,Y(30)-Y(70));
  x.beginPath(); s.forEach((v,i)=>{const px=8+i/(s.length-1)*(w-16), py=Y(v); i?x.lineTo(px,py):x.moveTo(px,py);});
  x.strokeStyle="#b388ff"; x.lineWidth=1.6; x.stroke();
  x.fillStyle="#f2d27a"; x.font="11px ui-monospace,monospace"; x.fillText("RSI "+s[s.length-1].toFixed(1), 12, 14);
}
osc();
function spark(values){
  if(!values||values.filter(v=>v!==null).length<2) return "n/a";
  const w=68,h=18, pts=values.filter(v=>v!==null), lo=Math.min(...pts), hi=Math.max(...pts), span=(hi-lo)||1;
  const d=pts.map((v,i)=>`${i===0?"M":"L"}${(i/(pts.length-1)*w).toFixed(1)},${(h-(v-lo)/span*h).toFixed(1)}`).join(" ");
  const up=pts[pts.length-1]>=pts[0];
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><path d="${d}" fill="none" stroke="${up?"#3ddc97":"#ff4d6d"}" stroke-width="1.4"/></svg>`;
}
const watch=D.watch||[];
$("watch").innerHTML=watch.length?watch.map(w=>`<div class="row"><span>${(w.tag==="Grok")?"✦":"•"}</span><b>${na(w.coin)}</b><span>${spark(w.spark)}</span><span class="${col(w.chg||0)}">${w.price===null?"n/a":w.price}${w.chg===null?"":" "+(w.chg>0?"+":"")+w.chg+"%"}</span></div>`).join(""):'<div class="flat">n/a</div>';
const voteColor=v=>v===null||v===undefined?"#8b93a7":v>0?"#3ddc97":v<0?"#ff4d6d":"#8b93a7";
const jcol=v=>v==="APRUEBA"?"#3ddc97":v==="PRECAUCION"?"#f5a623":v==="VETA"?"#ff4d6d":"#b388ff";
$("votes").innerHTML=(D.agents||[]).map(a=>{
  const shown=a.vote===null||a.vote===undefined?"n/a":(a.vote>0?"+"+a.vote:String(a.vote)).replace("-","−");
  return `<div class="vcard" style="border-color:${voteColor(a.vote)}55"><b style="color:${a.key==="G-JUDGE"?jcol(D.judge):voteColor(a.vote)}">${a.short} ${shown}</b><span>${a.line||"n/a"}</span></div>`;
}).join("");
function badge(v){return `<span class="badge ${v||""}">${na(v)}</span>`;}
const vers=D.verdicts||[];
$("vers").innerHTML=vers.length?vers.map(v=>`<div class="r"><span>${na(v.at)}</span><span>${na(v.symbol)}</span><span>${na(v.side)}</span>${badge(v.veredicto)}<span>Σ ${v.suma===null||v.suma===undefined?"n/a":v.suma} · ${na(v.confianza)}% · ${na(v.resumen)}</span></div>`).join(""):'<div class="flat">n/a · todavía no hay veredictos</div>';
const ops=D.ops||[];
$("ops").innerHTML=ops.length?ops.map(o=>`<div class="r"><span>${na(o.when)}</span><span>${na(o.coin)} ${na(o.side)}</span><span>${o.who}</span><span class="${col(o.pnl||0)}">${o.pnl===null?"abierta":money(o.pnl)}</span><span>e ${na(o.entry)} sl ${na(o.sl)} tp1 ${na(o.tp1)} ${na(o.lev)}x R ${o.r===null?"n/a":o.r}</span></div>`).join(""):'<div class="flat">n/a · sin operaciones de papel</div>';
const orb=$("orb"), ox=orb.getContext("2d");
function fit(){const r=Math.min(2,devicePixelRatio||1); orb.width=orb.clientWidth*r; orb.height=orb.clientHeight*r; ox.setTransform(r,0,0,r,0,0);}
fit(); addEventListener("resize",fit);
const parts=Array.from({length:64},(_,i)=>({a:Math.random()*6.28,r:40+Math.random()*150,s:0.15+Math.random()*0.45,h:Math.random()*18}));
function frame(t){
  const dpr=Math.min(2,devicePixelRatio||1);
  if(orb.clientWidth && Math.abs(orb.width/dpr-orb.clientWidth)>1) fit();
  const w=orb.clientWidth||640,h=orb.clientHeight||248,cx=w/2,cy=h/2+6;
  ox.clearRect(0,0,w,h);
  ox.fillStyle="#07101c"; ox.fillRect(0,0,w,h);
  const grd=ox.createRadialGradient(cx,cy,10,cx,cy,180); grd.addColorStop(0,"rgba(80,120,255,.18)"); grd.addColorStop(1,"transparent");
  ox.fillStyle=grd; ox.fillRect(0,0,w,h);
  parts.forEach(p=>{
    const ang=p.a+t*0.00015*p.s;
    ox.fillStyle=Math.sin(ang)>0?"rgba(61,220,151,.7)":"rgba(179,136,255,.65)";
    ox.fillRect(cx+Math.cos(ang)*p.r, cy+Math.sin(ang)*p.r*0.38+Math.sin(t*0.001+p.a)*p.h, 1.6, 1.6);
  });
  for(let k=0;k<3;k++){
    ox.beginPath(); ox.ellipse(cx,cy,78+k*46,26+k*16,t*0.0002*(k%2?1:-1),0,6.283);
    ox.strokeStyle=`rgba(${k===1?"179,136,255":"80,160,255"},${0.35-k*0.06})`; ox.lineWidth=1.2; ox.stroke();
  }
  const list=(D.agents||[]).map((a,i)=>{
    const rad=86+(i%3)*42, ang=t*0.00035*(0.6+ (i%5)*0.15)+i*(6.283/11);
    const z=Math.sin(ang);
    return {a,x:cx+Math.cos(ang)*rad,y:cy+z*(28+(i%3)*10),z,sc:0.72+ (z+1)*0.22};
  }).sort((p,q)=>p.z-q.z);
  list.forEach(p=>{
    const judge=p.a.key==="G-JUDGE";
    const pulse=judge?(1+Math.sin(t*0.006)*0.12):1;
    const r=13*p.sc*pulse;
    const c=judge?jcol(D.judge):voteColor(p.a.vote);
    ox.beginPath(); ox.fillStyle=c; ox.shadowColor=c; ox.shadowBlur=judge?18:8;
    ox.arc(p.x,p.y,r,0,6.283); ox.fill(); ox.shadowBlur=0;
    ox.fillStyle="#07101c"; ox.font=`700 ${Math.max(8,r)}px ui-monospace,monospace`; ox.textAlign="center"; ox.textBaseline="middle";
    ox.fillText(p.a.short,p.x,p.y+0.5);
  });
  const R=34+Math.sin(t*0.002)*2;
  const face=ox.createRadialGradient(cx-8,cy-10,4,cx,cy,R);
  face.addColorStop(0,"#fff"); face.addColorStop(0.6,"#f3e2a4"); face.addColorStop(1,"#c9a24a");
  ox.beginPath(); ox.fillStyle=face; ox.shadowColor="#f2d27a"; ox.shadowBlur=22; ox.arc(cx,cy,R,0,6.283); ox.fill(); ox.shadowBlur=0;
  ox.fillStyle="#1a1408"; ox.beginPath(); ox.ellipse(cx-12,cy-4,4.2,7,0,0,6.283); ox.ellipse(cx+12,cy-4,4.2,7,0,0,6.283); ox.fill();
  ox.strokeStyle="#1a1408"; ox.lineWidth=1.4; ox.beginPath(); ox.arc(cx,cy+8,8,0.15*Math.PI,0.85*Math.PI); ox.stroke();
  ox.fillStyle="#f2d27a"; ox.font="700 11px ui-monospace,monospace"; ox.textAlign="center"; ox.fillText("GROK",cx,cy+R+16);
  ox.fillStyle="#8ea0be"; ox.font="10px ui-monospace,monospace";
  ox.fillText((D.cripto||"n/a")+" "+(D.direccion||"")+" · "+(D.judge||"sin ronda")+(D.suma===null||D.suma===undefined?"":" · Σ "+D.suma), cx, cy+R+30);
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
</script>
"""
