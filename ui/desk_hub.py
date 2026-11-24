"""
ui/desk_hub.py
==============
«LA RED»: los 13 agentes de FG DESK alrededor del núcleo FG, con su dato en vivo.

Cada tarjeta muestra el personaje del agente, su nombre y lo que acaba de medir; las líneas
llevan pulsos al núcleo (verde = aprueba, rojo = bloquea, gris = sin datos). El agente que
bloqueó (o el último que habló) se ilumina. Todo dibujado en canvas, a 60 fps.
"""

from __future__ import annotations

import json

from engine.fgview.desk import AGENT_META, AGENTS
from ui.critters import CRITTERS_JS


def _short(r) -> str:
    h = r.headline.replace("⛔", "").strip()
    return h.split(" · ")[0][:38]


def hub_payload(dec) -> dict:
    done = {r.agent: r for r in (dec.results if dec else [])}
    agents = []
    for k in AGENTS:
        icon, color, role = AGENT_META[k]
        r = done.get(k)
        val = _short(r) if r else role.lower()
        if r is not None and k == "PRICER" and dec.probability is not None:
            val = f"P(TP1) {dec.probability:.1f}%"
        elif r is not None and k == "EDGE" and dec.edge_r is not None:
            val = f"ventaja {dec.edge_r:+.2f}R"
        elif r is not None and k == "KELLY" and dec.risk_pct is not None:
            val = f"riesgo {dec.risk_pct:.2f}% · {dec.plan.leverage}x" if dec.plan else f"riesgo {dec.risk_pct:.2f}%"
        elif r is not None and k == "ANALYST":
            val = r.headline.split(": ", 1)[-1][:40]
        elif r is not None and k in ("WHALE", "FLOW", "FUNDING") and r.value is not None:
            val = f"{_short(r)[:26]} · {r.value:+.2f}"
        agents.append({"key": k, "color": color, "status": r.status if r else "idle", "value": val, "role": role})
    hot = None
    if dec:
        b = dec.blocked_by
        hot = b.agent if b else ("TAKER" if dec.ticket is not None else dec.results[-1].agent if dec.results else None)
    verdict = dec.verdict if dec and (dec.ticket is not None or dec.blocked_by) else "EN ESPERA"
    return {"agents": agents, "hot": hot, "verdict": verdict,
            "symbol": f"{dec.direction or ''} {dec.symbol}".strip() if dec else "corre la mesa",
            "prob": dec.probability if dec else None}


def hub_html(dec, logo_svg: str, height: int = 600) -> str:
    return (_TEMPLATE.replace("__CRITTERS__", CRITTERS_JS).replace("__D__", json.dumps(hub_payload(dec)))
            .replace("__LOGO__", json.dumps(logo_svg)).replace("__H__", str(height)))


_TEMPLATE = r"""
<div id="wrap" style="position:relative;height:__H__px;border-radius:16px;overflow:hidden;border:1px solid rgba(212,175,55,.18);
 background:radial-gradient(ellipse at 50% 50%,#101624 0%,#070a10 60%,#04060a 100%);font-family:'JetBrains Mono',ui-monospace,monospace">
 <canvas id="c" style="position:absolute;inset:0;width:100%;height:100%"></canvas>
 <div style="position:absolute;left:16px;top:12px;font-size:11px;letter-spacing:.2em;color:#C9CED8">13 AGENTES → UN NÚCLEO FG · <span style="color:#F2D27A">SEÑALES EN TIEMPO REAL</span></div>
 <div id="on" style="position:absolute;right:16px;top:12px;font-size:11px;letter-spacing:.1em;color:#3DDC97"></div>
</div>
<style>body{margin:0;background:transparent;overflow:hidden}</style>
<script>
__CRITTERS__
const D=__D__, LOGO=__LOGO__; const cv=document.getElementById('c'), x=cv.getContext('2d'); let w,h;
const OK='#3DDC97', BAD='#FF4D6D', GOLD='#F2D27A';
const rgba=(hx,a)=>{const n=parseInt(hx.slice(1),16);return 'rgba('+(n>>16&255)+','+(n>>8&255)+','+(n&255)+','+Math.max(0,Math.min(1,a)).toFixed(3)+')';};
const VC=v=>v==='OPERAR'?OK:v==='ESPERAR'?'#F5A623':v==='NO OPERAR'?BAD:GOLD;
const moodOf=s=>s==='pass'?'pass':s==='block'?'block':s==='info'?'info':'idle';
document.getElementById('on').textContent='● '+D.agents.filter(a=>a.status!=='idle').length+'/'+D.agents.length+' EN LÍNEA';
const logo=new Image(); logo.src='data:image/svg+xml;utf8,'+encodeURIComponent(LOGO);
function size(){const d=Math.min(2,devicePixelRatio||1); w=cv.clientWidth; h=cv.clientHeight; cv.width=w*d; cv.height=h*d; x.setTransform(d,0,0,d,0,0); layout();}
let cards=[], pk=[];
function layout(){const n=D.agents.length, cw=Math.min(206,w*.23), ch=w<640?42:50, rx=Math.max(cw*.9,w*.5-cw*.5-14), ry=h*.5-ch*.5-40, cx=w/2, cy=h/2+12;
  cards=D.agents.map((a,i)=>{const ang=-Math.PI/2+Math.PI/n+i*2*Math.PI/n; const px=cx+rx*Math.cos(ang), py=cy+ry*Math.sin(ang);
    return {a,i,ang,x:px-cw/2,y:py-ch/2,w:cw,h:ch,cx:px,cy:py};});}
size(); addEventListener('resize',size);
function wrapText(s,max){x.font=(w<640?'8.5':'9.5')+'px monospace'; if(x.measureText(s).width<=max) return s; while(s.length>3&&x.measureText(s+'…').width>max) s=s.slice(0,-1); return s+'…';}
const t0=performance.now();
function frame(now){const t=(now-t0)/1000, cx=w/2, cy=h/2+12, R=Math.min(w,h)*.13; x.clearRect(0,0,w,h);
  // Estrellas y anillos concéntricos
  for(let i=0;i<70;i++){x.fillStyle='rgba(200,210,230,'+(.05+.07*Math.sin(t+i))+')'; x.fillRect((i*137.5)%w,(i*71.3)%h,1.2,1.2);}
  const vc=VC(D.verdict);
  for(let k=0;k<5;k++){const rr=R*(1.25+k*.32)+Math.sin(t*1.5+k)*2; x.strokeStyle=rgba(k%2?vc:GOLD,.16-k*.02); x.lineWidth=1; if(k%2) x.setLineDash([4,7]);
    x.save(); x.translate(cx,cy); x.rotate((k%2?-1:1)*t*.15*(k+1)); x.beginPath(); x.arc(0,0,rr,0,6.283); x.stroke(); x.restore(); x.setLineDash([]);}
  const sw=((t*.9)%1); x.strokeStyle=rgba(vc,(1-sw)*.5); x.lineWidth=2; x.beginPath(); x.arc(cx,cy,R*(1+sw*1.6),0,6.283); x.stroke();
  // Conexiones y pulsos
  cards.forEach(c=>{const a=c.a, on=a.status!=='idle', col=a.status==='block'?BAD:on?a.color:'#3a4150', hot=a.key===D.hot;
    const ex=cx+Math.cos(c.ang)*R*1.05, ey=cy+Math.sin(c.ang)*R*1.05; const sx=c.cx-Math.cos(c.ang)*c.w*.12, sy=c.cy-Math.sin(c.ang)*c.h*.2;
    x.strokeStyle=rgba(col,hot?.9:on?.45:.18); x.lineWidth=hot?2.4:1.2; if(!on) x.setLineDash([3,5]); x.beginPath(); x.moveTo(sx,sy); x.lineTo(ex,ey); x.stroke(); x.setLineDash([]);
    const mx=(sx+ex)/2, my=(sy+ey)/2; x.fillStyle=rgba(col,on?.9:.35); x.fillRect(mx-2.5,my-2.5,5,5);
    if(on){for(let k=0;k<(hot?3:2);k++){const u=((t*(a.status==='block'?.35:.55)+k/(hot?3:2)+c.i*.07)%1), uu=a.status==='block'?Math.min(u,.5):u;
      const px=sx+(ex-sx)*uu, py=sy+(ey-sy)*uu, g=x.createRadialGradient(px,py,0,px,py,8); g.addColorStop(0,'rgba(255,255,255,.95)'); g.addColorStop(.4,rgba(col,.8)); g.addColorStop(1,rgba(col,0));
      x.fillStyle=g; x.beginPath(); x.arc(px,py,8,0,6.283); x.fill();
      if(a.status==='block'&&u>=.5){x.strokeStyle=rgba(BAD,.9); x.lineWidth=2; x.beginPath(); x.moveTo(px-5,py-5); x.lineTo(px+5,py+5); x.moveTo(px+5,py-5); x.lineTo(px-5,py+5); x.stroke();}}}});
  // Núcleo FG
  const fl=x.createRadialGradient(cx,cy,R*.4,cx,cy,R*2.2); fl.addColorStop(0,rgba(vc,.32)); fl.addColorStop(1,rgba(vc,0)); x.fillStyle=fl; x.beginPath(); x.arc(cx,cy,R*2.2,0,6.283); x.fill();
  x.fillStyle='#05070b'; x.strokeStyle=rgba(GOLD,.9); x.lineWidth=3; x.shadowColor=vc; x.shadowBlur=28; x.beginPath(); x.arc(cx,cy,R,0,6.283); x.fill(); x.stroke(); x.shadowBlur=0;
  x.strokeStyle=rgba(vc,.7); x.lineWidth=1.5; x.beginPath(); x.arc(cx,cy,R*.86,t*1.2,t*1.2+4.2); x.stroke();
  if(logo.complete&&logo.naturalWidth){const L=R*1.15; x.drawImage(logo,cx-L/2,cy-L/2,L,L);}
  const lab=D.verdict+(D.prob!=null?' · '+D.prob.toFixed(0)+'%':'')+' · '+D.symbol; x.font='700 11px monospace'; const lw=Math.max(x.measureText(lab).width,60)+24;
  x.fillStyle='rgba(5,7,11,.92)'; x.strokeStyle=rgba(vc,.6); x.lineWidth=1; x.beginPath(); x.roundRect(cx-lw/2,cy+R+8,lw,38,10); x.fill(); x.stroke();
  x.textAlign='center'; x.font='800 12px monospace'; x.fillStyle='#E6E9EF'; x.fillText('FG DESK',cx,cy+R+23);
  x.font='700 11px monospace'; x.fillStyle=vc; x.fillText(lab,cx,cy+R+39);
  // Tarjetas
  cards.forEach(c=>{const a=c.a, on=a.status!=='idle', hot=a.key===D.hot, col=a.status==='block'?BAD:a.color;
    x.save(); if(hot){x.shadowColor=col; x.shadowBlur=22+Math.sin(t*4)*6;}
    x.fillStyle='#0a0e16'; x.beginPath(); x.roundRect(c.x,c.y,c.w,c.h,12); x.fill();
    x.fillStyle=hot?rgba(col,.12):'rgba(10,14,22,.92)'; x.strokeStyle=hot?rgba(col,.95):on?rgba(a.color,.35):'rgba(148,163,184,.14)'; x.lineWidth=hot?1.8:1;
    x.beginPath(); x.roundRect(c.x,c.y,c.w,c.h,12); x.fill(); x.stroke(); x.restore();
    const ic=c.h-14, ix=c.x+8, iy=c.y+7; x.fillStyle=rgba(a.color,on?.22:.08); x.beginPath(); x.roundRect(ix,iy,ic,ic,9); x.fill();
    drawCritter(x,a.key,ix+ic/2,iy+ic/2+1,ic*.34,on?a.color:'#5a6273',t+c.i,moodOf(a.status),Math.cos(c.ang+Math.PI)*.6,Math.sin(c.ang+Math.PI)*.6);
    const tx=ix+ic+9, tw=c.w-(tx-c.x)-8; x.textAlign='left'; x.font='800 '+(w<640?'9.5':'11')+'px monospace'; x.fillStyle=on?col:'#8C95A8'; x.fillText(a.key,tx,c.y+c.h*.42);
    x.fillStyle=on?'#B4BCCB':'#566074'; x.fillText(wrapText((a.status==='block'?'✕ ':'')+a.value,tw),tx,c.y+c.h*.78);
    const dot=a.status==='pass'?OK:a.status==='block'?BAD:a.status==='info'?a.color:'#3a4150'; x.fillStyle=dot; x.beginPath(); x.arc(c.x+c.w-9,c.y+9,3+(on?Math.sin(t*4+c.i):0),0,6.283); x.fill();});
  requestAnimationFrame(frame);}
requestAnimationFrame(frame);
</script>"""
