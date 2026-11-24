"""
engine/fgview/webapp.py
=======================
📱 App de iPhone del copiloto FG (web app instalable: Safari → Compartir → Agregar a inicio).

Qué hace: muestra en el teléfono el Radar de tarjetas, armar una tarjeta (/check), tus posiciones con stop y TP1,
el COACH de hoy, el diario y los resultados; se actualiza cada 10 s y avisa si se quedó sin conexión.
Qué nunca hace: abrir órdenes (no existe ningún endpoint para eso). Lo único que "cambia" algo es
«Ya terminé por hoy», que APAGA el piloto automático.
Vive en el mismo servidor y túnel que el MCP de GROK DESK, bajo /app/<token>/ (el token es la llave).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _icon_bytes() -> bytes:
    for base in (Path(getattr(sys, "_MEIPASS", "")), Path(__file__).resolve().parents[2]):
        p = base / "assets" / "icon.png"
        if p.is_file():
            return p.read_bytes()
    return b""


def state_payload(bridge) -> dict:
    s = bridge.s
    cp = s.copilot
    pil = s.autopilot.state()
    cards = cp.scribe.cards()[-30:]
    st = bridge.state()
    return {
        "asOf": datetime.now(timezone.utc).isoformat(),
        "mode": ("PILOTO " + pil["mode"].upper()) if pil["enabled"] else "TÚ OPERAS",
        "coach": cp.coach.status(),
        "cards": [{k: c.get(k) for k in ("at", "symbol", "direction", "band", "score", "fomo", "text", "grade",
                                          "result_pct", "paused")} for c in reversed(cards)],
        "positions": [{k: v for k, v in p.items() if isinstance(v, (str, int, float, bool, list)) or v is None}
                      for p in s.positions.all()],
        "journal": [{k: t.get(k) for k in ("opened_at", "coin", "direction", "pnl", "result", "duration_min", "notes")}
                    for t in reversed((s.journal.entries() if s.journal else [])[-30:])],
        "stats": {"papel": s.autopilot.paper_stats(), "aprendizaje": s.autopilot.paper_stats(learn=True)},
        "pilot": [e["text"] for e in pil.get("events", [])[-12:]][::-1],
        "grok": (st.get("verdicts") or [None])[-1],
        "rules": cp.rules.markdown(cp.coach.bankroll()),
    }


def routes(bridge, token: str) -> list:
    from starlette.requests import Request
    from starlette.responses import HTMLResponse, JSONResponse, Response
    from starlette.routing import Route

    base = f"/app/{token}"

    async def page(request: Request):
        return HTMLResponse(APP_HTML.replace("__BASE__", base))

    async def manifest(request: Request):
        return JSONResponse({"name": "FG VIEW", "short_name": "FG VIEW", "start_url": f"{base}/", "scope": f"{base}/",
                             "display": "standalone", "background_color": "#0b0b12", "theme_color": "#0b0b12",
                             "icons": [{"src": f"{base}/icon.png", "sizes": "512x512", "type": "image/png"}]})

    async def icon(request: Request):
        return Response(_icon_bytes(), media_type="image/png")

    async def state(request: Request):
        return Response(json.dumps(state_payload(bridge), default=str, ensure_ascii=False), media_type="application/json")

    async def check(request: Request):
        try:
            body = await request.json()
            sym = str(body.get("symbol", "")).strip()[:12]
            if not sym:
                return JSONResponse({"error": "Escribe una cripto"}, status_code=400)
            from starlette.concurrency import run_in_threadpool

            out = await run_in_threadpool(bridge.card, sym)
            return Response(json.dumps(out, default=str, ensure_ascii=False), media_type="application/json")
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)[:160]}, status_code=500)

    async def stop(request: Request):
        bridge.s.autopilot.set(enabled=False)
        return JSONResponse({"ok": True, "mensaje": "Piloto apagado. Tus posiciones siguen con su SL."})

    return [Route(f"{base}/", page), Route(f"{base}/manifest.json", manifest), Route(f"{base}/icon.png", icon),
            Route(f"{base}/api/state", state), Route(f"{base}/api/check", check, methods=["POST"]),
            Route(f"{base}/api/stop", stop, methods=["POST"])]


APP_HTML = r"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="FG VIEW"><meta name="theme-color" content="#0b0b12">
<link rel="manifest" href="__BASE__/manifest.json"><link rel="apple-touch-icon" href="__BASE__/icon.png">
<title>FG VIEW</title>
<style>
:root{--bg:#0b0b12;--card:#151522;--tx:#e8e8ee;--mu:#8a8aa0;--g:#3fd97f;--y:#f2c94c;--r:#ff5c6c;--b:#6c8cff;--gold:#D4AF37}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px -apple-system,system-ui,sans-serif;font-variant-numeric:tabular-nums;
 padding:calc(env(safe-area-inset-top) + 8px) 14px calc(env(safe-area-inset-bottom) + 76px)}
h1{font-size:17px;letter-spacing:.14em;margin:4px 0 2px;color:var(--gold)}.mu{color:var(--mu);font-size:12.5px}
.bar{position:sticky;top:0;z-index:5;background:var(--bg);padding:6px 0 8px}
.strip{background:#3a2e08;color:var(--y);border-radius:10px;padding:8px 10px;margin:8px 0;font-size:13px}
.off{background:#2a1218;color:var(--r)}
.card{background:var(--card);border-radius:14px;padding:12px;margin:10px 0}
pre{white-space:pre-wrap;font:12.5px ui-monospace,Menlo,monospace;margin:0;line-height:1.45}
.row{display:flex;justify-content:space-between;align-items:center;gap:8px}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:6px}
.chip{border:1px solid #2a2a3c;border-radius:20px;padding:5px 11px;font-size:13px;color:var(--mu);background:none}
.chip.on{color:var(--tx);border-color:var(--gold)}
input{width:100%;background:#0f0f19;border:1px solid #2a2a3c;color:var(--tx);border-radius:12px;padding:13px;font-size:16px}
button.big{width:100%;min-height:46px;border-radius:12px;border:0;background:var(--gold);color:#05070b;font-weight:700;font-size:15px;margin-top:8px}
button.warn{background:#2a1218;color:var(--r);border:1px solid #4a1d26}
nav{position:fixed;left:0;right:0;bottom:0;display:flex;background:#0f0f19;border-top:1px solid #20202e;padding-bottom:env(safe-area-inset-bottom)}
nav button{flex:1;background:none;border:0;color:var(--mu);padding:10px 0 9px;font-size:11px;min-height:54px}
nav button.on{color:var(--gold)}.k{font-size:22px;font-weight:700}.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.live{animation:p 2s infinite}@keyframes p{50%{opacity:.55}}
</style></head><body>
<div class="bar"><div class="row"><h1>FG VIEW</h1><span class="mu" id="mode">—</span></div>
<div class="mu" id="top">Leyendo…</div><div id="strips"></div></div>
<main id="v"></main>
<nav id="nav"></nav>
<script>
const B='__BASE__'; let S=null, last=0, tab='radar', filt='all';
const TABS=[['radar','Radar'],['check','Tarjeta'],['pos','Posiciones'],['coach','Coach'],['diario','Diario'],['stats','Stats']];
const COL={GREEN:'var(--g)',YELLOW:'var(--y)',RED:'var(--r)',SKIP:'#555'};
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const ago=t=>{const s=Math.max(0,(Date.now()-new Date(t))/1000);return s<90?Math.round(s)+'s':s<5400?Math.round(s/60)+' min':Math.round(s/3600)+' h'};
function nav(){document.getElementById('nav').innerHTML=TABS.map(([k,l])=>`<button class="${k===tab?'on':''}" onclick="go('${k}')">${l}</button>`).join('')}
function go(k){tab=k;nav();render()}
async function load(){try{const r=await fetch(B+'/api/state',{cache:'no-store'}); if(!r.ok) throw 0; S=await r.json(); last=Date.now(); render()}catch(e){strips(true)}}
function strips(off){const el=document.getElementById('strips'); let h='';
 if(off||(last&&Date.now()-last>40000)) h+=`<div class="strip off">sin conexión · última actualización hace ${last?Math.round((Date.now()-last)/1000)+'s':'—'} · ¿FG VIEW y el túnel siguen abiertos?</div>`;
 if(S&&S.coach.blocked) h+=`<div class="strip">Pausado por tus reglas · ${esc(S.coach.blocked)}</div>`; el.innerHTML=h}
function topbar(){if(!S) return; const c=S.coach; document.getElementById('mode').textContent=S.mode;
 document.getElementById('top').innerHTML=`Capital $${c.bankroll.toFixed(2)} · hoy <b style="color:${c.pnl_today>=0?'var(--g)':'var(--r)'}">${c.pnl_today>=0?'+':''}${c.pnl_today.toFixed(2)} (${c.pnl_pct.toFixed(2)}%)</b> · ${c.trades_today}/${c.max_trades} operaciones · ${c.open}/${c.max_open} abiertas`}
function render(){topbar();strips(false);const v=document.getElementById('v'); if(!S){v.innerHTML='<div class="card mu">Leyendo FG VIEW… 3s</div>';return}
 if(tab==='radar'){const chips=[['all','Todas'],['GREEN','GREEN'],['YELLOW','YELLOW'],['RED','RED'],['SKIP','SKIP']].map(([k,l])=>`<button class="chip ${filt===k?'on':''}" onclick="filt='${k}';render()">${l}</button>`).join(' ');
  const cs=S.cards.filter(c=>filt==='all'||c.band===filt);
  v.innerHTML=`<div style="display:flex;gap:6px;flex-wrap:wrap">${chips}</div>`+(cs.length?cs.map(c=>`<div class="card" style="opacity:${c.band==='RED'||c.band==='SKIP'?.6:1}">
   <div class="row"><b><span class="dot" style="background:${COL[c.band]}"></span>${esc(c.symbol)} · ${esc(c.direction||'')}</b><span class="mu">${c.score??''} ${c.band} · hace ${ago(c.at)}${c.grade?' · nota '+c.grade:''}</span></div>
   <details><summary class="mu" style="margin-top:6px">Ver tarjeta</summary><pre style="margin-top:8px">${esc(c.text)}</pre></details></div>`).join(''):
   '<div class="card mu">Nada en el radar todavía. Arma una tarjeta en la pestaña Tarjeta o pídesela a Grok.</div>')}
 else if(tab==='check'){v.innerHTML=`<div class="card"><input id="sym" placeholder="Cripto: SOL, BTC, ETH…" autocapitalize="characters">
   <button class="big" onclick="check()">Armar tarjeta</button><div id="res" class="mu" style="margin-top:10px">La mesa de 12 agentes analiza y el copiloto arma la tarjeta. Tú decides.</div></div>`}
 else if(tab==='pos'){v.innerHTML=S.positions.length?S.positions.map(p=>{const tp=(p.tps&&p.tps[0])?p.tps[0][0]:null;return `<div class="card"><div class="row"><b>${esc(p.coin)} ${esc(p.direction)}</b><span class="mu">${esc(p.mode||'papel')} · ${esc(p.strategy||'')}</span></div>
   <div class="grid" style="margin-top:8px"><div><div class="mu">Entrada</div><div class="k">${(+p.entry).toPrecision(6)}</div></div><div><div class="mu">Stop</div><div class="k" style="color:var(--r)">${(+p.stop_loss).toPrecision(6)}</div></div>
   <div><div class="mu">TP1</div><div class="k" style="color:var(--b)">${tp?(+tp).toPrecision(6):'—'}</div></div><div><div class="mu">Apalancamiento</div><div class="k">${p.leverage}x</div></div></div></div>`}).join(''):
   '<div class="card mu">Sin posiciones abiertas. Cuando entres, aparecen aquí con su stop.</div>'}
 else if(tab==='coach'){const c=S.coach; v.innerHTML=`<div class="card"><div class="grid"><div><div class="mu">Operaciones hoy</div><div class="k">${c.trades_today}/${c.max_trades}</div></div>
   <div><div class="mu">P&amp;L hoy</div><div class="k">${c.pnl_today>=0?'+':''}${c.pnl_today.toFixed(2)}</div></div><div><div class="mu">Racha de pérdidas</div><div class="k">${c.streak}</div></div>
   <div><div class="mu">Abiertas</div><div class="k">${c.open}/${c.max_open}</div></div></div>
   <p class="mu">${c.blocked?esc(c.blocked):'Sin bloqueos: tus reglas permiten operar.'}</p>
   <button class="big warn" onclick="stop()">Ya terminé por hoy (apagar piloto)</button></div>
   <div class="card"><pre>${esc(S.rules)}</pre></div>`+(S.pilot.length?`<div class="card"><div class="mu">Piloto</div>${S.pilot.map(t=>`<div class="mu" style="margin-top:6px">${esc(t)}</div>`).join('')}</div>`:'')}
 else if(tab==='diario'){v.innerHTML=S.journal.length?S.journal.map(t=>`<div class="card"><div class="row"><b>${esc(t.coin)} ${esc(t.direction)}</b><b style="color:${(t.pnl||0)>=0?'var(--g)':'var(--r)'}">${(t.pnl||0)>=0?'+':''}${(+t.pnl||0).toFixed(2)}</b></div>
   <div class="mu">${esc((t.opened_at||'').slice(0,16).replace('T',' '))} · ${esc(t.notes||'')}</div></div>`).join(''):'<div class="card mu">Tu primera operación cerrada aparecerá aquí.</div>'}
 else {const p=S.stats.papel, l=S.stats.aprendizaje; const box=(t,x)=>`<div class="card"><b>${t}</b><div class="grid" style="margin-top:8px"><div><div class="mu">Operaciones</div><div class="k">${x.trades}</div></div>
   <div><div class="mu">Win rate</div><div class="k">${x.win_rate.toFixed(0)}%</div></div><div><div class="mu">Neto</div><div class="k">${x.net>=0?'+':''}${x.net.toFixed(2)}</div></div>
   <div><div class="mu">Profit factor</div><div class="k">${isFinite(x.pf)?x.pf.toFixed(2):'∞'}</div></div></div></div>`;
   v.innerHTML=box('Papel (lo que la mesa aprobó)',p)+box('Aprendizaje (lo más recomendado)',l)+(S.grok?`<div class="card"><b>Último veredicto de Grok</b><p>${esc(S.grok.veredicto)} · ${esc(S.grok.resumen)}</p></div>`:'')}}
async function check(){const s=document.getElementById('sym').value.trim(); const res=document.getElementById('res'); if(!s){res.textContent='Escribe una cripto.';return}
 res.innerHTML='<span class="live">La mesa está analizando…</span>'; try{const r=await fetch(B+'/api/check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:s})});
 const d=await r.json(); res.innerHTML=d.error?esc(d.error):`<pre>${esc(d.tarjeta)}</pre>`; load()}catch(e){res.textContent='Sin conexión con FG VIEW.'}}
async function stop(){if(!confirm('¿Apagar el piloto automático por hoy?')) return; await fetch(B+'/api/stop',{method:'POST'}); load()}
nav(); load(); setInterval(load,10000); setInterval(()=>strips(false),5000);
</script></body></html>"""
