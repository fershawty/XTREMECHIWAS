"""
ui/sidebar_ui.py
================
Barra lateral de FG VIEW V2, convertida en panel de control:

* Cabecera animada: logo con brillo, reloj UTC en vivo y estado ONLINE.
* Mini-mesa: los 7 personajes con su estado de la última decisión de FG DESK.
* Tarjeta de mercado: símbolo, precio, cambio, mini-gráfica y señal actual.
* Menú propio por secciones con subtítulos y contadores en vivo (posiciones, alertas…).
"""

from __future__ import annotations

import json
from html import escape

import streamlit as st
import streamlit.components.v1 as components

from engine.fgview.desk import AGENT_META, AGENTS
from ui.critters import CRITTERS_JS

SUBTITLES = {
    "grafica": "velas · señal · niveles", "desk": "12 agentes · red Binance", "facil": "tu capital → 1 operación",
    "time-machine": "100,000 simulaciones", "swing": "mantener hasta 12 h", "entry-timer": "cuándo entrar",
    "fgview": "plan por capital", "burn-out": "⛔ peligro extremo", "scanner": "top 5 · 5m/15m/1h",
    "news": "impacto · sentimiento", "alertas": "precio · squeeze · liq.", "riesgo": "límites del día",
    "budget": "saldo · retiros", "executor": "papel / real", "bitacora": "tus operaciones",
    "revisor": "historial de Binance", "fundamentales": "fees · desbloqueos", "smart-money": "congreso · ballenas",
    "exponencial": "escalera sin liquidar", "simulador": "LONG / SHORT",
}
SECTION_ICONS = {}


def brand_html(logo_svg: str, version: str) -> str:
    return f"""
<div style="display:flex;align-items:center;gap:10px;font-family:'JetBrains Mono',ui-monospace,monospace;padding:4px 2px">
 <div id="lg" style="width:46px;height:46px;position:relative;flex:none">
  <canvas id="ring" width="92" height="92" style="position:absolute;inset:-0px;width:46px;height:46px"></canvas>
  <div style="position:absolute;inset:5px">{logo_svg}</div></div>
 <div style="flex:1;min-width:0">
  <div style="font-weight:800;font-size:16px;letter-spacing:.18em;background:linear-gradient(90deg,#F7DC8B,#D4AF37 40%,#FFF4C8 50%,#D4AF37 60%,#9C7A1E) 0 0/200% 100%;
   -webkit-background-clip:text;color:transparent;animation:sh 6s linear infinite">FG VIEW</div>
  <div style="font-size:8.5px;letter-spacing:.16em;color:#566074;margin-top:2px">V2 · {escape(version)} · TERMINAL</div>
  <div style="display:flex;gap:6px;margin-top:5px;font-size:9px;letter-spacing:.1em">
   <span style="color:#3DDC97;border:1px solid rgba(61,220,151,.35);padding:1px 6px;border-radius:6px"><span style="animation:p 1.4s infinite">●</span> ONLINE</span>
   <span id="clk" style="color:#E6E9EF;border:1px solid rgba(148,163,184,.2);padding:1px 6px;border-radius:6px">--:--:-- UTC</span></div></div>
</div>
<style>body{{margin:0;background:transparent;overflow:hidden}} @keyframes sh{{to{{background-position:-200% 0}}}} @keyframes p{{50%{{opacity:.25}}}}
#lg svg{{width:100%;height:100%;filter:drop-shadow(0 0 8px rgba(212,175,55,.5))}}</style>
<script>
const c=document.getElementById('ring').getContext('2d'); const k=document.getElementById('clk');
function f(n){{const t=n/1000; c.clearRect(0,0,92,92); c.save(); c.translate(46,46); c.rotate(t*.8);
 for(let i=0;i<24;i++){{const a=i/24*6.283; c.strokeStyle='rgba(242,210,122,'+(i%6?.25:.8)+')'; c.lineWidth=2; c.beginPath(); c.moveTo(40*Math.cos(a),40*Math.sin(a)); c.lineTo((i%6?44:45)*Math.cos(a),(i%6?44:45)*Math.sin(a)); c.stroke();}}
 c.restore(); const g=c.createRadialGradient(46,46,20,46,46,46); g.addColorStop(0,'rgba(212,175,55,0)'); g.addColorStop(1,'rgba(212,175,55,'+(.10+.08*Math.sin(t*2))+')'); c.fillStyle=g; c.fillRect(0,0,92,92);
 if(Math.floor(t*2)%2===0) k.textContent=new Date().toISOString().slice(11,19)+' UTC'; requestAnimationFrame(f);}}
requestAnimationFrame(f);
</script>"""


def agents_html(decision) -> str:
    done = {r.agent: r for r in (decision.results if decision else [])}
    agents = [{"key": k, "color": AGENT_META[k][1], "status": done[k].status if k in done else "idle"} for k in AGENTS]
    verdict = (decision.verdict if decision and (decision.ticket is not None or decision.blocked_by) else "EN ESPERA")
    pct = f" · {decision.probability:.0f}%" if decision and decision.probability is not None else ""
    vcol = {"OPERAR": "#3DDC97", "ESPERAR": "#F5A623", "NO OPERAR": "#FF4D6D"}.get(verdict, "#8C95A8")
    verdict += pct
    sym = escape(f"{decision.direction or ''} {decision.symbol}") if decision else "corre la mesa en FG DESK"
    return ("""
<div style="font-family:'JetBrains Mono',ui-monospace,monospace;border:1px solid rgba(148,163,184,.13);border-radius:12px;
 background:linear-gradient(180deg,rgba(255,255,255,.03),rgba(255,255,255,0)),#070a10;overflow:hidden">
 <div style="display:flex;justify-content:space-between;padding:6px 9px 0;font-size:8.5px;letter-spacing:.16em;color:#8C95A8">
  <span>◆ LA MESA</span><span style="color:__VC__">__V__</span></div>
 <canvas id="m" style="width:100%;height:128px;display:block"></canvas>
 <div style="padding:0 9px 7px;font-size:9px;color:#C9CED8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">__SYM__</div>
</div>
<style>body{margin:0;background:transparent;overflow:hidden}</style>
<script>
__CRITTERS__
const A=__A__; const cv=document.getElementById('m'), x=cv.getContext('2d'); let w,h;
function size(){const d=Math.min(2,devicePixelRatio||1); w=cv.clientWidth; h=cv.clientHeight; cv.width=w*d; cv.height=h*d; x.setTransform(d,0,0,d,0,0);} size(); addEventListener('resize',size);
const MO=s=>s==='pass'?'pass':s==='block'?'block':s==='info'?'info':'idle';
function f(n){const t=n/1000; x.clearRect(0,0,w,h);
 // Línea de latido que conecta a los agentes
 x.beginPath(); for(let i=0;i<=80;i++){const px=8+(w-16)*i/80, beat=(i/80*7-(t*1.5)%7); const yy=h*.5+(Math.abs(beat)<.15?-18*Math.cos(beat*10):0)+Math.sin(i*.4+t*3)*1.2; i?x.lineTo(px,yy):x.moveTo(px,yy);}
 x.strokeStyle='rgba(242,210,122,.35)'; x.lineWidth=1; x.stroke();
 const half=Math.ceil(A.length/2); A.forEach((a,i)=>{const row=i<half?0:1, col=row?i-half:i, n=row?A.length-half:half, px=w*(col+.5)/n, py=row?h*.72:h*.27;
   drawCritter(x,a.key,px,py,9.5,a.color,t+i,MO(a.status),Math.sin(t*.7+i)*.6,0);
   x.font='700 6.5px monospace'; x.textAlign='center'; x.fillStyle=a.status==='idle'?'#566074':a.color; x.fillText(a.key,px,py+20);});
 requestAnimationFrame(f);}
requestAnimationFrame(f);
</script>""".replace("__CRITTERS__", CRITTERS_JS).replace("__A__", json.dumps(agents)).replace("__V__", escape(verdict))
            .replace("__VC__", vcol).replace("__SYM__", sym))


def market_card(brief: dict | None) -> str:
    if not brief:
        return ('<div class="fg-mk"><div class="fg-mk-h">◆ MERCADO</div><div style="color:#566074;font-size:.72rem">'
                'Abre «Gráfica y señal» para cargar el mercado.</div></div>')
    closes = brief.get("closes") or []
    path = ""
    if len(closes) > 2:
        lo, hi = min(closes), max(closes)
        pts = [(i / (len(closes) - 1) * 200, 36 - (v - lo) / ((hi - lo) or 1) * 32) for i, v in enumerate(closes)]
        path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    up = (brief.get("change") or 0) >= 0
    col = "#3DDC97" if up else "#FF4D6D"
    d = brief.get("direction") or "—"
    dcol = "#3DDC97" if d == "LONG" else "#FF4D6D" if d == "SHORT" else "#8C95A8"
    return (f'<div class="fg-mk"><div class="fg-mk-h"><span>◆ MERCADO</span><span>{escape(brief.get("timeframe", ""))}</span></div>'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline;margin-top:2px">'
            f'<b style="font-size:.8rem;color:#E6E9EF">{escape(brief.get("symbol", ""))}</b>'
            f'<span style="color:{col};font-size:.72rem">{"▲" if up else "▼"} {brief.get("change", 0):+.2f}%</span></div>'
            f'<div style="font-size:1.15rem;font-weight:800;color:#E6E9EF;letter-spacing:-.01em">{escape(brief.get("price", ""))}</div>'
            f'<svg viewBox="0 0 200 40" preserveAspectRatio="none" style="width:100%;height:38px;margin:2px 0">'
            f'<defs><linearGradient id="mkg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{col}" stop-opacity=".35"/>'
            f'<stop offset="1" stop-color="{col}" stop-opacity="0"/></linearGradient></defs>'
            f'<path d="{path} L200,40 L0,40 Z" fill="url(#mkg)"/><path d="{path}" fill="none" stroke="{col}" stroke-width="1.6"/></svg>'
            f'<div style="display:flex;justify-content:space-between;font-size:.68rem"><span style="color:#8C95A8">SEÑAL</span>'
            f'<b style="color:{dcol}">{escape(d)} · {brief.get("score", 0):.0f}/100</b></div></div>')


def render_nav(pages: dict[str, list], counts: dict[str, str], current: str = "") -> None:
    """Menú compacto: iconos de línea, contador a la derecha, ayuda al pasar el ratón y la página actual resaltada."""
    for section, items in pages.items():
        st.markdown(f'<div class="fg-sec"><span>{escape(section.upper())}</span><i></i></div>', unsafe_allow_html=True)
        for page in items:
            path = page.url_path or "grafica"
            badge = counts.get(path)
            icon = (page.icon or "").replace(":material/", "").rstrip(":")
            if (page.url_path or "") == (current or ""):
                st.markdown('<div class="fg-act-mark"></div>', unsafe_allow_html=True)  # El siguiente enlace es el activo
            label = page.title + (f"  :gray[{badge}]" if badge else "")
            st.page_link(page, label=label, icon=page.icon, help=SUBTITLES.get(path))
