"""
ui/desk_anim.py
===============
Animaciones de FG DESK (HTML + canvas, 60 fps, sin librerías externas):

* synapse_html   la «sinapsis»: los agentes alrededor del logo, dendritas, partículas y
                 pulsos que viajan al centro cuando cada agente decide (verde aprueba,
                 rojo bloquea, dorado = orden aprobada).
* fan_html       el «abanico de futuros»: caminos del Time Machine dibujándose en vivo,
                 niveles de SL/TP e histograma de dónde terminan.
* trace_html     la traza de la decisión, línea a línea, sincronizada con la sinapsis.
* rotation_html  rotación de señales (fuerza vs impulso) con cuadrantes animados.
* kpi_html       barra superior tipo terminal con reloj UTC en vivo.
"""

from __future__ import annotations

import json
from html import escape

from engine.fgview.desk import AGENT_META, AGENTS
from ui.critters import CRITTERS_JS

FONT = "'JetBrains Mono', ui-monospace, Menlo, Consolas, monospace"
FONT_JS = "JetBrains Mono, ui-monospace, Menlo, Consolas, monospace"  # Sin comillas: va dentro de cadenas JS


def _agents_payload(decision) -> list[dict]:
    done = {r.agent: r for r in (decision.results if decision else [])}
    out = []
    for key in AGENTS:
        icon, color, role = AGENT_META[key]
        r = done.get(key)
        out.append({"key": key, "icon": icon, "color": color, "role": role,
                    "status": r.status if r else "idle", "headline": r.headline if r else "—",
                    "ms": round(r.ms) if r else None})
    return out


SYNAPSE_TEMPLATE = r"""
<div id="w" style="position:relative;width:100%;height:__H__px;border-radius:16px;overflow:hidden;background:#04060a;
 border:1px solid rgba(148,163,184,.13);font-family:__FONTCSS__">
 <canvas id="c" style="position:absolute;inset:0;width:100%;height:100%"></canvas>
 <div id="cards" style="position:absolute;left:0;right:0;top:0;height:__STAGE__px;pointer-events:none"></div>
 <div style="position:absolute;left:14px;top:10px;font-size:11px;letter-spacing:.18em;color:#9BA3B4">◆ LA SINAPSIS
  <span style="color:#5D6577">— 7 NEURONAS · UNA DECISIÓN</span></div>
 <div style="position:absolute;right:14px;top:10px;font-size:11px;letter-spacing:.14em;color:#3DDC97">● 7/7 ONLINE</div>
 <div id="sum" style="position:absolute;left:0;right:0;bottom:0;height:26px;line-height:26px;font-size:11.5px;color:#ECE8DC;
  background:linear-gradient(90deg,rgba(212,175,55,0),rgba(212,175,55,.08),rgba(212,175,55,0));
  text-align:center;opacity:0;transition:opacity .8s;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:0 12px"></div>
</div>
<script>
__CRITTERS__
const D=__DATA__, LOGO=__LOGO__, FONT='__FONTJS__', STAGE=__STAGE__;
const MOOD=n=>n.status==='pass'?'pass':n.status==='block'?'block':n.status==='info'?'info':'idle';
const W=document.getElementById('w'),C=document.getElementById('c'),X=C.getContext('2d'),cards=document.getElementById('cards');
let w=0,h=0,dpr=1,t0=performance.now(),nodes=[],trees=[],bokeh=[],BG=null,DEN=null,cx=0,cy=0;
const OK='#3DDC97',BAD='#FF4D6D';
let seed=11; const rnd=()=>{seed=(seed*16807)%2147483647;return (seed-1)/2147483646;};
const img=new Image(); img.src='data:image/svg+xml;utf8,'+encodeURIComponent(LOGO);
const rgba=(hex,a)=>{const n=parseInt(hex.slice(1),16);return 'rgba('+(n>>16&255)+','+(n>>8&255)+','+(n&255)+','+Math.max(0,Math.min(1,a)).toFixed(3)+')';};
const col=n=>n.status==='pass'?OK:n.status==='block'?BAD:n.status==='info'?n.color:'#3a4150';
const bez=(m,t)=>{const u=1-t;return {x:u*u*u*m[0].x+3*u*u*t*m[1].x+3*u*t*t*m[2].x+t*t*t*m[3].x,y:u*u*u*m[0].y+3*u*u*t*m[1].y+3*u*t*t*m[2].y+t*t*t*m[3].y};};
function off(){const c=document.createElement('canvas');c.width=w*dpr;c.height=h*dpr;const x=c.getContext('2d');x.setTransform(dpr,0,0,dpr,0,0);return [c,x];}
// ---------- Árbol de dendritas (ramificación orgánica recursiva) ----------
function grow(segs,paths,x,y,ang,len,wid,depth,path){
  const steps=5; let px=x,py=y; const pts=path?path:[{x,y}];
  for(let s=0;s<steps;s++){ang+=(rnd()-.5)*0.55; const nx=px+Math.cos(ang)*len/steps, ny=py+Math.sin(ang)*len/steps;
    segs.push([px,py,nx,ny,wid*(1-s/steps*0.35),depth]); pts.push({x:nx,y:ny}); px=nx; py=ny;}
  if(depth<=0){paths.push(pts.slice().reverse()); return;}
  const kids=depth>2?2:(rnd()<.75?2:3);
  for(let k=0;k<kids;k++){const a=ang+(k-(kids-1)/2)*(0.5+rnd()*0.5)+(rnd()-.5)*0.3;
    grow(segs,paths,px,py,a,len*(0.62+rnd()*0.16),wid*0.66,depth-1,k===0?pts.slice():[{x:px,y:py}]);}
}
function layout(){
  dpr=Math.min(2,window.devicePixelRatio||1); w=W.clientWidth; h=W.clientHeight; C.width=w*dpr; C.height=h*dpr; X.setTransform(dpr,0,0,dpr,0,0);
  cx=w/2; cy=STAGE/2+4; const rx=Math.min(w*0.355,470), ry=STAGE*0.34; seed=11; nodes=[]; trees=[]; bokeh=[];
  D.agents.forEach((a,i)=>{const ang=-Math.PI*0.80+i*(Math.PI*1.60/(D.agents.length-1));
    nodes.push({...a,i,ang,x:cx+rx*Math.cos(ang),y:cy+ry*Math.sin(ang)*(i%2?0.92:1.08)});});
  nodes.forEach(n=>{
    const segs=[],paths=[]; const toC=Math.atan2(cy-n.y,cx-n.x);
    const trunks=6+Math.floor(rnd()*3);
    for(let k=0;k<trunks;k++){const a=toC+Math.PI+(k/trunks-.5)*Math.PI*1.7+(rnd()-.5)*0.4; grow(segs,paths,n.x,n.y,a,34+rnd()*36,2.6,3);}
    const mx=(n.x+cx)/2, my=(n.y+cy)/2, dist=Math.hypot(cx-n.x,cy-n.y);
    const axon=[{x:n.x,y:n.y},{x:n.x+(mx-n.x)*.6+(rnd()-.5)*dist*.25,y:n.y+(my-n.y)*.6+(rnd()-.5)*dist*.25},
      {x:mx+(cx-mx)*.45+(rnd()-.5)*dist*.2,y:my+(cy-my)*.45+(rnd()-.5)*dist*.2},{x:cx,y:cy}];
    // Ramitas que salen del axón
    for(let k=0;k<9;k++){const t=.12+rnd()*.72, p=bez(axon,t), q=bez(axon,Math.min(1,t+.02)); const a=Math.atan2(q.y-p.y,q.x-p.x)+(rnd()<.5?1:-1)*(0.7+rnd()*0.8);
      grow(segs,paths,p.x,p.y,a,14+rnd()*24,1.1,1);}
    trees.push({segs,paths,axon,sig:Array.from({length:16},()=>({p:Math.floor(rnd()*paths.length),t:rnd(),v:.004+rnd()*.008})),
      ax:Array.from({length:5},()=>({t:rnd(),v:.0025+rnd()*.003}))});
  });
  for(let s=0;s<70;s++) bokeh.push({x:rnd()*w,y:rnd()*h,r:rnd()*2.4+.3,v:rnd()*.12+.02,a:rnd()*.35+.05,c:rnd()<.5?'#5AC8FA':'#D4AF37'});
  paintBG(); paintDendrites(); buildCards();
}
function paintBG(){
  let x; [BG,x]=off();
  const g=x.createRadialGradient(cx,cy,10,cx,cy,Math.max(w,h)*.75); g.addColorStop(0,'#141b2a'); g.addColorStop(.45,'#0a0f19'); g.addColorStop(1,'#030407');
  x.fillStyle=g; x.fillRect(0,0,w,h);
  [[w*.18,STAGE*.25,'rgba(179,136,255,.07)'],[w*.82,STAGE*.75,'rgba(90,200,250,.06)'],[w*.75,STAGE*.2,'rgba(212,175,55,.05)']].forEach(([px,py,c])=>{
    const n=x.createRadialGradient(px,py,0,px,py,w*.35); n.addColorStop(0,c); n.addColorStop(1,'rgba(0,0,0,0)'); x.fillStyle=n; x.fillRect(0,0,w,h);});
  x.strokeStyle='rgba(148,163,184,.045)'; x.lineWidth=1; const R=22;
  for(let yy=0;yy<STAGE+R;yy+=R*1.5) for(let xx=0;xx<w+R*2;xx+=R*Math.sqrt(3)){const ox=xx+((yy/(R*1.5))%2?R*Math.sqrt(3)/2:0);
    x.beginPath(); for(let k=0;k<6;k++){const a=Math.PI/3*k+Math.PI/6; const px=ox+R*.55*Math.cos(a), py=yy+R*.55*Math.sin(a); k?x.lineTo(px,py):x.moveTo(px,py);} x.closePath(); x.stroke();}
  const v=x.createRadialGradient(cx,cy,Math.min(w,h)*.3,cx,cy,Math.max(w,h)*.7); v.addColorStop(0,'rgba(0,0,0,0)'); v.addColorStop(1,'rgba(0,0,0,.55)');
  x.fillStyle=v; x.fillRect(0,0,w,h);
  // Banda inferior de instrumentos
  x.fillStyle='rgba(5,7,11,.86)'; x.fillRect(0,STAGE,w,h-STAGE); x.strokeStyle='rgba(148,163,184,.12)'; x.beginPath(); x.moveTo(0,STAGE); x.lineTo(w,STAGE); x.stroke();
}
function paintDendrites(){
  let x; [DEN,x]=off(); x.lineCap='round';
  nodes.forEach((n,i)=>{const tr=trees[i], on=n.status!=='idle', c=on?col(n):'#3a4150', base=on?n.color:'#3a4150';
    x.shadowColor=c; x.shadowBlur=on?8:0;
    tr.segs.forEach(([a,b,c2,d,wid,dep])=>{x.strokeStyle=rgba(dep>=2?c:base,on?(.25+dep*.13):.18); x.lineWidth=Math.max(.35,wid*.55); x.beginPath(); x.moveTo(a,b); x.lineTo(c2,d); x.stroke();});
    x.shadowBlur=0;});
}
function buildCards(){
  cards.innerHTML='';
  nodes.forEach(n=>{
    const d=document.createElement('div'); const st=n.status, CW=Math.min(210,Math.max(160,w*0.17));
    let lx; const side=Math.abs(Math.cos(n.ang))>=0.5, left=n.x<w/2;
    if(!side) lx=n.x+34; else if(left) lx=(n.x-44>=CW)?n.x-38-CW:n.x+38; else lx=(w-n.x-44>=CW)?n.x+38:n.x-38-CW;
    const ty=Math.max(26,Math.min(STAGE-104,n.y-24));
    const chip=st==='pass'?'<span style="color:#3DDC97">PASA</span>':st==='block'?'<span style="color:#FF4D6D">BLOQUEA</span>':
      st==='info'?'<span style="color:'+n.color+'">INFO</span>':'<span style="color:#5D6577">EN ESPERA</span>';
    d.style.cssText='position:absolute;padding:7px 9px;border-radius:10px;font-size:10.5px;line-height:1.35;color:#C9CED8;'+
      'background:linear-gradient(180deg,rgba(255,255,255,.04),rgba(255,255,255,0)),rgba(8,11,18,.88);backdrop-filter:blur(3px);'+
      'border:1px solid '+(st==='idle'?'rgba(148,163,184,.12)':n.color+'66')+';box-shadow:0 0 26px '+n.color+'22,inset 0 1px 0 rgba(255,255,255,.05);'+
      'opacity:0;transform:translateY(6px) scale(.96);transition:opacity .5s,transform .5s;width:'+CW+'px;box-sizing:border-box;'+
      'left:'+Math.max(4,Math.min(w-CW-4,lx))+'px;top:'+ty+'px';
    d.innerHTML='<div style="display:flex;justify-content:space-between;gap:8px;letter-spacing:.12em;font-size:10px;margin-bottom:3px">'+
      '<b style="color:'+n.color+'">'+n.icon+' '+n.key+'</b>'+chip+'</div><div style="display:-webkit-box;-webkit-line-clamp:3;'+
      '-webkit-box-orient:vertical;overflow:hidden;font-size:10px">'+esc(n.headline)+'</div>'+(n.ms!=null?'<div style="color:#5D6577;margin-top:2px">'+n.ms+' ms</div>':'');
    n.card=d; cards.appendChild(d);});
}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
const STEP=.62; const membrane=new Array(260).fill(0); let lastFire=-1, sparks=[], bolts=[];
function frame(now){
  const T=Math.max(0,(now-t0)/1000); X.clearRect(0,0,w,h); if(BG) X.drawImage(BG,0,0,w,h);
  bokeh.forEach(b=>{b.y-=b.v; if(b.y<-5)b.y=STAGE; const g=X.createRadialGradient(b.x,b.y,0,b.x,b.y,b.r*4);
    g.addColorStop(0,rgba(b.c,b.a*(.6+.4*Math.sin(T+b.x)))); g.addColorStop(1,rgba(b.c,0)); X.fillStyle=g; X.beginPath(); X.arc(b.x,b.y,b.r*4,0,6.283); X.fill();});
  const evaluated=nodes.filter(n=>n.status!=='idle').length, v=D.verdict;
  const vc=v==='approved'?'#F2D27A':v==='operable'?'#F5A623':v==='blocked'?BAD:'#D4AF37';
  // Dendritas (revelado progresivo por agente)
  if(DEN){nodes.forEach((n,i)=>{const act=T-i*STEP; const r=n.status==='idle'?1:Math.max(0,Math.min(1,act/0.7));
    if(r<=0) return; X.save(); X.beginPath(); X.arc(n.x,n.y,30+r*230,0,6.283); X.clip(); X.drawImage(DEN,0,0,w,h); X.restore();});}
  let arrive=null;
  nodes.forEach((n,i)=>{
    const tr=trees[i], act=T-i*STEP, on=n.status!=='idle'&&act>0, c=col(n), fl=Math.sin(T*1.2+i)*2;
    // Axón al centro
    const prog=n.status==='idle'?1:Math.max(0,Math.min(1,act/.5));
    X.lineCap='round'; X.strokeStyle=rgba(on?c:'#2c3240',on?.85:.35); X.lineWidth=on?2.2:1; X.shadowColor=c; X.shadowBlur=on?16:0;
    X.beginPath(); for(let k=0;k<=60*prog;k++){const p=bez(tr.axon,k/60); k?X.lineTo(p.x,p.y):X.moveTo(p.x,p.y);} X.stroke(); X.shadowBlur=0;
    // Señales que recorren las dendritas hacia el soma
    if(on) tr.sig.forEach(s=>{s.t+=s.v; if(s.t>1){s.t=0; s.p=Math.floor(Math.random()*tr.paths.length);}
      const P=tr.paths[s.p]; const f=s.t*(P.length-1), k=Math.floor(f), q=P[Math.min(P.length-1,k)], q2=P[Math.min(P.length-1,k+1)], u=f-k;
      const x=q.x+(q2.x-q.x)*u, y=q.y+(q2.y-q.y)*u; const g=X.createRadialGradient(x,y,0,x,y,5); g.addColorStop(0,rgba('#ffffff',.95)); g.addColorStop(.35,rgba(c,.8)); g.addColorStop(1,rgba(c,0));
      X.fillStyle=g; X.beginPath(); X.arc(x,y,5,0,6.283); X.fill();});
    // Impulsos por el axón (cometas con estela)
    if(on||n.status==='idle') tr.ax.forEach(p=>{p.t=(p.t+p.v*(on?1:.3))%1;
      for(let k=0;k<7;k++){const q=bez(tr.axon,Math.max(0,p.t-k*.012)); X.fillStyle=rgba(on?c:'#56607a',(on?.85:.3)*(1-k/7)); X.beginPath(); X.arc(q.x,q.y,(on?2.4:1.3)*(1-k/9),0,6.283); X.fill();}});
    // Pulso de decisión
    if(on&&act<STEP*1.6){const pt=Math.min(1,act/(STEP*1.25)); const q=bez(tr.axon,pt); const g=X.createRadialGradient(q.x,q.y,0,q.x,q.y,20);
      g.addColorStop(0,'rgba(255,255,255,1)'); g.addColorStop(.3,rgba(c,.9)); g.addColorStop(1,rgba(c,0)); X.fillStyle=g; X.beginPath(); X.arc(q.x,q.y,20,0,6.283); X.fill();
      if(pt>=.97) arrive=c; if(i>lastFire&&act>.05){lastFire=i; for(let s=0;s<14;s++) sparks.push({x:n.x,y:n.y,vx:(Math.random()-.5)*3,vy:(Math.random()-.5)*3,l:1,c});}}
    // Soma (cuerpo de la neurona)
    const R=19+(on?Math.sin(T*3+i)*1.3:0), sx=n.x, sy=n.y+fl;
    if(on&&act<1.3){X.strokeStyle=rgba(c,1-act/1.3); X.lineWidth=2; X.beginPath(); X.arc(sx,sy,R+act*40,0,6.283); X.stroke();}
    const halo=X.createRadialGradient(sx,sy,R*.4,sx,sy,R*3); halo.addColorStop(0,rgba(n.color,on?.45:.12)); halo.addColorStop(1,rgba(n.color,0));
    X.fillStyle=halo; X.beginPath(); X.arc(sx,sy,R*3,0,6.283); X.fill();
    // El personaje del agente en el soma de su neurona
    X.fillStyle='rgba(7,10,16,.85)'; X.beginPath(); X.arc(sx,sy,R+2,0,6.283); X.fill();
    X.save(); X.translate(sx,sy); X.rotate(T*(i%2?.6:-.6)); X.setLineDash([2,5]); X.strokeStyle=rgba(on?c:n.color,on?.9:.35); X.lineWidth=1.2;
    X.beginPath(); X.arc(0,0,R+7,0,6.283); X.stroke(); X.restore(); X.setLineDash([]);
    if(on){X.strokeStyle=c; X.lineWidth=2.4; X.beginPath(); X.arc(sx,sy,R+3,-Math.PI/2,-Math.PI/2+6.283*Math.min(1,act/.8)); X.stroke();}
    drawCritter(X,n.key,sx,sy-1,R*.74,n.color,T+i,on?MOOD(n):'idle',(cx-sx)/240,(cy-sy)/240);
    X.textAlign='center'; X.textBaseline='middle'; X.font='700 9.5px '+FONT; X.fillStyle=on?'#ECE8DC':'#7d8598'; X.fillText(n.key,sx,sy+R+18);
    if(n.card&&(on||n.status==='idle')&&n.card.style.opacity!=='1'){n.card.style.opacity='1'; n.card.style.transform='none';}
  });
  // Chispas
  sparks=sparks.filter(s=>s.l>0); sparks.forEach(s=>{s.x+=s.vx; s.y+=s.vy; s.vx*=.96; s.vy*=.96; s.l-=.025;
    X.fillStyle=rgba(s.c,s.l); X.beginPath(); X.arc(s.x,s.y,1.6,0,6.283); X.fill();});
  // ---------- Orbe central ----------
  const done=T>evaluated*STEP+.8, br=1+Math.sin(T*2)*.03, Rc=52*br;
  const field=X.createRadialGradient(cx,cy,Rc*.6,cx,cy,Rc*3.2); field.addColorStop(0,rgba(arrive||vc,arrive?.6:.3)); field.addColorStop(1,rgba(vc,0));
  X.fillStyle=field; X.beginPath(); X.arc(cx,cy,Rc*3.2,0,6.283); X.fill();
  // Campo de energía ondulante
  for(let L=0;L<3;L++){X.beginPath(); for(let k=0;k<=140;k++){const a=k/140*6.283; const r=Rc+16+L*9+Math.sin(a*6+T*(1.4+L*.5))*3+Math.sin(a*11-T*2.2)*2;
      const x=cx+r*Math.cos(a), y=cy+r*Math.sin(a); k?X.lineTo(x,y):X.moveTo(x,y);} X.strokeStyle=rgba(vc,.32-L*.08); X.lineWidth=1; X.stroke();}
  // Anillos con marcas
  X.save(); X.translate(cx,cy); X.rotate(T*.25);
  for(let k=0;k<72;k++){const a=k/72*6.283, r1=Rc+44, r2=r1+(k%6?4:9); X.strokeStyle=rgba(vc,k%6?.25:.6); X.lineWidth=1;
    X.beginPath(); X.moveTo(r1*Math.cos(a),r1*Math.sin(a)); X.lineTo(r2*Math.cos(a),r2*Math.sin(a)); X.stroke();}
  X.rotate(-T*.7); X.setLineDash([16,10,2,10]); X.strokeStyle=rgba(vc,.45); X.beginPath(); X.arc(0,0,Rc+60,0,6.283); X.stroke(); X.setLineDash([]); X.restore();
  // Rayos: al llegar cada pulso, en la aprobación y chispazos entre neuronas vecinas
  if(arrive&&Math.random()<.5) bolts.push({i:Math.max(0,Math.min(nodes.length-1,lastFire)),l:1});
  if(done&&(v==='approved'||v==='operable')&&Math.random()<.18) bolts.push({i:Math.floor(Math.random()*nodes.length),l:1});
  if(Math.random()<.05){const i=Math.floor(Math.random()*(nodes.length-1)); bolts.push({i,j:i+1,l:1});}
  bolts=bolts.filter(b=>b.l>0); bolts.forEach(b=>{const n=nodes[b.i], A=b.j!=null?{x:nodes[b.j].x,y:nodes[b.j].y}:{x:cx,y:cy}, c=b.j!=null?'#B8E1FF':(n.status==='block'?BAD:'#FFF4C8');
    X.strokeStyle=rgba(c,b.l); X.lineWidth=1.5; X.shadowColor=c; X.shadowBlur=16;
    X.beginPath(); X.moveTo(A.x,A.y); for(let k=1;k<=10;k++){const t=k/10, jx=A.x+(n.x-A.x)*t+(k<10?(Math.random()-.5)*28*(1-Math.abs(.5-t)):0), jy=A.y+(n.y-A.y)*t+(k<10?(Math.random()-.5)*28*(1-Math.abs(.5-t)):0); X.lineTo(jx,jy);
      if(k===4&&Math.random()<.6){X.stroke(); X.beginPath(); X.moveTo(jx,jy); X.lineTo(jx+(Math.random()-.5)*50,jy+(Math.random()-.5)*50); X.stroke(); X.beginPath(); X.moveTo(jx,jy);}}
    X.stroke(); X.shadowBlur=0; b.l-=.1;});
  if(done&&v!=='idle'){const k=((T-evaluated*STEP-.8)%2.4)/2.4; X.strokeStyle=rgba(vc,(1-k)*.85); X.lineWidth=2.5; X.beginPath(); X.arc(cx,cy,Rc+k*150,0,6.283); X.stroke();}
  // Esfera brillante con el logo
  const sph=X.createRadialGradient(cx-Rc*.35,cy-Rc*.4,Rc*.1,cx,cy,Rc); sph.addColorStop(0,'#ffffff'); sph.addColorStop(.55,'#d8dce6'); sph.addColorStop(1,'#7b8496');
  X.shadowColor=vc; X.shadowBlur=40; X.fillStyle=sph; X.beginPath(); X.arc(cx,cy,Rc,0,6.283); X.fill(); X.shadowBlur=0;
  X.fillStyle='rgba(255,255,255,.55)'; X.beginPath(); X.ellipse(cx-Rc*.3,cy-Rc*.45,Rc*.35,Rc*.16,-.5,0,6.283); X.fill();
  if(img.complete){X.save(); X.beginPath(); X.arc(cx,cy,Rc*.78,0,6.283); X.clip(); X.drawImage(img,cx-Rc*.78,cy-Rc*.78,Rc*1.56,Rc*1.56); X.restore();}
  if(done){const lab=(v==='approved'?'◆ ORDEN APROBADA ◆':v==='operable'?'🎲 OPERABLE · NO RECOMENDADA':v==='blocked'?'✕ NO OPERAR':'EN ESPERA')+
      (D.prob!=null?'   '+(D.direction||'')+' '+D.symbol+' · P(TP1) '+D.prob+'%':'');
    X.font='700 10.5px '+FONT; const tw=X.measureText(lab).width+28; X.fillStyle='rgba(5,7,11,.85)'; X.strokeStyle=rgba(vc,.6);
    X.beginPath(); X.roundRect(cx-tw/2,26,tw,22,11); X.fill(); X.stroke(); X.fillStyle=vc; X.textAlign='center'; X.textBaseline='middle'; X.fillText(lab,cx,37);
    const s=document.getElementById('sum'); if(s.style.opacity!=='1'){s.textContent=D.summary; s.style.opacity='1';}}
  // ---------- Instrumentos (banda inferior) ----------
  const by=STAGE+6, bh=h-STAGE-58, third=w/3; X.textBaseline='alphabetic';
  X.font='9px '+FONT; X.textAlign='left'; X.fillStyle='#5D6577';
  X.fillText('MEMBRANA · POTENCIAL',14,by+8); X.fillText('ACTIVIDAD POR NEURONA',third+14,by+8); X.fillText('PESOS SINÁPTICOS',2*third+14,by+8);
  membrane.shift(); let spike=0; nodes.forEach((n,i)=>{const a=T-i*STEP; if(n.status!=='idle'&&a>0&&a<.12) spike=1;});
  membrane.push((spike?1:0)*.9+Math.sin(T*9)*.06+(Math.random()-.5)*.08);
  X.beginPath(); membrane.forEach((m,k)=>{const x=14+(third-28)*k/(membrane.length-1), y=by+14+bh*.55-m*bh*.5; k?X.lineTo(x,y):X.moveTo(x,y);});
  X.strokeStyle=rgba(vc,.85); X.lineWidth=1.2; X.shadowColor=vc; X.shadowBlur=8; X.stroke(); X.shadowBlur=0;
  const bw=(third-40)/nodes.length;
  nodes.forEach((n,i)=>{const on=n.status!=='idle'&&T-i*STEP>0; const lvl=on?(.45+.4*Math.abs(Math.sin(T*2.5+i*1.7))):.08;
    const x=third+18+i*bw, y0=by+12+bh; X.fillStyle=rgba(on?col(n):'#3a4150',.85); X.fillRect(x,y0-lvl*bh,bw-6,lvl*bh);
    X.fillStyle='#5D6577'; X.fillText(n.key.slice(0,3),x,y0+10);});
  const Wt=D.weights||[]; const gx=2*third+14, gw=third-28, cols=Math.max(1,Wt.length), cw2=gw/cols;
  for(let r=0;r<4;r++) Wt.forEach((wv,k)=>{const a=Math.max(.08,Math.min(1,(wv-.3)/1.4))*(0.6+0.4*Math.sin(T*1.5+r+k));
    X.fillStyle=rgba(wv>=1?'#3DDC97':'#FF5C8A',a); X.fillRect(gx+k*cw2+1,by+14+r*(bh/4),cw2-2,bh/4-2);});
  requestAnimationFrame(frame);
}
window.addEventListener('resize',layout); layout(); requestAnimationFrame(frame);
</script>"""


def synapse_html(decision, logo_svg: str, height: int = 600) -> str:
    agents = _agents_payload(decision)
    verdict = ("approved" if decision and decision.approved else "operable" if decision and decision.operable
               else "blocked" if decision and decision.blocked_by else "idle")
    summary = decision.summary() if decision else "Pulsa «Correr la mesa» para que los agentes analicen el mercado."
    weights = [round(r.get("weight", 1.0), 3) for r in (decision.rotation if decision else [])] or [1.0] * 6
    data = json.dumps({"agents": agents, "verdict": verdict, "summary": summary, "weights": weights,
                       "symbol": decision.symbol if decision else "", "direction": (decision.direction or "") if decision else "",
                       "prob": round(decision.probability, 1) if decision and decision.probability else None})
    stage = height - 100
    return (SYNAPSE_TEMPLATE.replace("__CRITTERS__", CRITTERS_JS).replace("__DATA__", data).replace("__LOGO__", json.dumps(logo_svg))
            .replace("__FONTJS__", FONT_JS).replace("__FONTCSS__", FONT).replace("__STAGE__", str(stage))
            .replace("__H__", str(height)))


def fan_html(fan: dict | None, tm, height: int = 330) -> str:
    if not fan:
        return _empty("◆ ABANICO DE FUTUROS", "Cuando la mesa corra, aquí verás las simulaciones del Time Machine dibujándose.", height)
    data = json.dumps({**fan, "p_tp1": round(tm.p_tp1, 1) if tm else None, "p_sl": round(tm.p_sl, 1) if tm else None,
                       "p_time": round(tm.p_time, 1) if tm else None, "ev": round(tm.ev, 2) if tm else None,
                       "sims": tm.sims if tm else None})
    return f"""
<div id="w" style="position:relative;height:{height}px;border-radius:14px;overflow:hidden;background:#070a10;
 border:1px solid rgba(148,163,184,.12);font-family:{FONT}">
 <div style="position:absolute;left:12px;top:8px;font-size:10.5px;letter-spacing:.16em;color:#9BA3B4;z-index:2">◆ ABANICO DE FUTUROS
  <span style="color:#5D6577">— CADA LÍNEA ES UNA SIMULACIÓN</span></div>
 <div id="stats" style="position:absolute;right:12px;top:24px;font-size:10.5px;color:#ECE8DC;z-index:2;text-align:right"></div>
 <canvas id="c" style="position:absolute;inset:0;width:100%;height:100%"></canvas>
</div>
<script>
const D={data}; const W=document.getElementById('w'),C=document.getElementById('c'),X=C.getContext('2d');
let w,h,dpr; const COL={{sl:'#FF4D6D',tp1:'#5AC8FA',tp2:'#3DDC97',tp3:'#9CFFB5',tiempo:'#8790A3'}};
function size(){{dpr=window.devicePixelRatio||1;w=W.clientWidth;h=W.clientHeight;C.width=w*dpr;C.height=h*dpr;X.setTransform(dpr,0,0,dpr,0,0);}}
size(); window.addEventListener('resize',size);
const all=D.paths.flat().concat([D.stop,...D.tps]); let lo=Math.min(...all),hi=Math.max(...all); const pad=(hi-lo)*.08; lo-=pad; hi+=pad;
const L=Math.max(...D.paths.map(p=>p.length)); const PL=58,PR=96,PT=54,PB=22;
const xs=i=>PL+(w-PL-PR)*i/(L-1), ys=v=>PT+(h-PT-PB)*(1-(v-lo)/(hi-lo));
document.getElementById('stats').innerHTML=D.p_tp1!=null?('<span style="color:#3DDC97">P(TP1) '+D.p_tp1+'%</span> · <span style="color:#FF4D6D">P(SL) '+D.p_sl+
 '%</span> · P(tiempo) '+D.p_time+'%<br><span style="color:#9BA3B4">'+(D.sims||0).toLocaleString()+' simulaciones · EV '+(D.ev>=0?'+':'')+D.ev+' USDT</span>'):'';
let t0=performance.now();
function frame(now){{
  const T=Math.max(0,(now-t0)/1000), cyc=7.5, k=(T%cyc)/cyc, prog=Math.min(1,k/0.55), fade=k>0.85?1-(k-0.85)/0.15:1;
  X.clearRect(0,0,w,h);
  // Rejilla y niveles
  X.strokeStyle='rgba(148,163,184,.06)'; X.lineWidth=1; for(let g=0;g<6;g++){{const y=PT+(h-PT-PB)*g/5; X.beginPath(); X.moveTo(PL,y); X.lineTo(w-PR,y); X.stroke();}}
  const lvl=(v,c,lab,dash)=>{{const y=ys(v); X.setLineDash(dash||[]); X.strokeStyle=c; X.lineWidth=1; X.beginPath(); X.moveTo(PL,y); X.lineTo(w-PR,y); X.stroke();
    X.setLineDash([]); X.font='9.5px {FONT_JS}'; X.fillStyle=c; X.textAlign='right'; X.fillText(lab,PL-6,y+3);}};
  lvl(D.entry,'rgba(236,232,220,.7)','ENTRADA',[3,4]); lvl(D.stop,'rgba(255,77,109,.85)','SL');
  D.tps.forEach((v,i)=>lvl(v,'rgba(61,220,151,'+(.85-i*.2)+')','TP'+(i+1)));
  // Caminos
  const ends=[];
  D.paths.forEach((p,j)=>{{ const n=Math.max(2,Math.floor(p.length*prog)); const c=COL[D.outcomes[j]]||'#8790A3';
    X.strokeStyle=c; X.globalAlpha=0.42*fade; X.lineWidth=1.05; X.beginPath();
    for(let i=0;i<n;i++){{ const x=xs(i), y=ys(p[i]); i?X.lineTo(x,y):X.moveTo(x,y); }} X.stroke();
    X.globalAlpha=fade; if(prog<1){{ X.fillStyle=c; X.beginPath(); X.arc(xs(n-1),ys(p[n-1]),1.8,0,6.283); X.fill(); }}
    if(n>=p.length) ends.push([p[p.length-1],c]); }});
  X.globalAlpha=1;
  // Histograma de finales (derecha)
  const B=26, cnt=new Array(B).fill(0), cc=new Array(B).fill('#8790A3');
  ends.forEach(([v,c])=>{{const b=Math.max(0,Math.min(B-1,Math.floor((1-(v-lo)/(hi-lo))*B))); cnt[b]++; cc[b]=c;}});
  const mx=Math.max(1,...cnt), bh=(h-PT-PB)/B;
  cnt.forEach((n,b)=>{{ if(!n) return; X.fillStyle=cc[b]; X.globalAlpha=.75*fade; X.fillRect(w-PR+8,PT+b*bh+1,(PR-18)*n/mx,bh-2); }});
  X.globalAlpha=1; requestAnimationFrame(frame);
}}
requestAnimationFrame(frame);
</script>"""


def trace_html(decision, height: int = 330) -> str:
    if not decision:
        return _empty("◆ LA TRAZA", "Cada paso de la mesa aparecerá aquí, en orden y con su hora.", height)
    rows = []
    for i, r in enumerate(decision.results):
        c = {"pass": "#3DDC97", "block": "#FF4D6D"}.get(r.status, r.color)
        rows.append(f'<div style="animation:in .45s ease {i * 0.62:.2f}s both;border-left:2px solid {c};padding:6px 10px;margin:6px 0;'
                    f'background:linear-gradient(90deg,{c}14,transparent 70%);border-radius:0 8px 8px 0">'
                    f'<div style="display:flex;justify-content:space-between;font-size:10px;letter-spacing:.12em;color:#9BA3B4">'
                    f'<span><b style="color:{r.color}">{r.icon} {r.agent}</b> · {r.at}</span><span>{round(r.ms)} ms</span></div>'
                    f'<div style="font-size:11px;color:#ECE8DC;margin-top:2px">{escape(r.headline)}</div></div>')
    return f"""
<div style="height:{height}px;overflow:auto;border-radius:14px;background:#070a10;border:1px solid rgba(148,163,184,.12);
 padding:8px 10px;font-family:{FONT}">
 <div style="font-size:10.5px;letter-spacing:.16em;color:#9BA3B4;margin:2px 0 4px">◆ LA TRAZA <span style="color:#5D6577">— CADA PASO, UNA LÍNEA</span></div>
 <style>@keyframes in{{from{{opacity:0;transform:translateX(-10px)}}to{{opacity:1;transform:none}}}}</style>
 {''.join(rows)}
</div>"""


def rotation_html(rotation: list[dict], height: int = 300) -> str:
    if not rotation:
        return _empty("◆ ROTACIÓN DE SEÑALES", "Qué señales están funcionando ahora (se calcula al correr la mesa).", height)
    data = json.dumps(rotation)
    return f"""
<div id="w" style="position:relative;height:{height}px;border-radius:14px;overflow:hidden;background:#070a10;
 border:1px solid rgba(148,163,184,.12);font-family:{FONT}">
 <div style="position:absolute;left:12px;top:8px;font-size:10.5px;letter-spacing:.16em;color:#9BA3B4">◆ ROTACIÓN DE SEÑALES
  <span style="color:#5D6577">— FUERZA × IMPULSO (24 H)</span></div>
 <canvas id="c" style="position:absolute;inset:0;width:100%;height:100%"></canvas>
</div>
<script>
const D={data}; const W=document.getElementById('w'),C=document.getElementById('c'),X=C.getContext('2d'); let w,h,dpr;
function size(){{dpr=window.devicePixelRatio||1;w=W.clientWidth;h=W.clientHeight;C.width=w*dpr;C.height=h*dpr;X.setTransform(dpr,0,0,dpr,0,0);}}
size(); window.addEventListener('resize',size);
const PAL=['#3DDC97','#5AC8FA','#B388FF','#F5A623','#FF5C8A','#F2D27A','#FF8A3D','#9CFFB5'];
const sx=Math.max(8,...D.map(d=>Math.abs(d.strength)))*1.25, sy=Math.max(8,...D.map(d=>Math.abs(d.momentum)))*1.25; let t0=performance.now();
function frame(now){{
  const T=Math.max(0,(now-t0)/1000), e=1-Math.pow(1-Math.min(1,T/1.6),3); X.clearRect(0,0,w,h);
  const cx=w/2, cy=h/2+8, ax=w/2-40, ay=h/2-36;
  const quad=[[1,-1,'LIDERANDO','rgba(61,220,151,.05)'],[1,1,'DEBILITÁNDOSE','rgba(245,166,35,.05)'],[-1,1,'REZAGADAS','rgba(255,77,109,.05)'],[-1,-1,'MEJORANDO','rgba(90,200,250,.05)']];
  quad.forEach(([qx,qy,lab,bg])=>{{ X.fillStyle=bg; X.fillRect(qx>0?cx:cx-ax,qy<0?cy-ay:cy,ax,ay);
    X.font='9px {FONT_JS}'; X.fillStyle='#5D6577'; X.textAlign=qx>0?'right':'left'; X.fillText(lab,qx>0?cx+ax-6:cx-ax+6,qy<0?cy-ay+12:cy+ay-6); }});
  X.strokeStyle='rgba(148,163,184,.18)'; X.beginPath(); X.moveTo(cx-ax,cy); X.lineTo(cx+ax,cy); X.moveTo(cx,cy-ay); X.lineTo(cx,cy+ay); X.stroke();
  D.forEach((d,i)=>{{ const c=PAL[i%PAL.length]; const wob=Math.sin(T*.8+i)*2;
    const x=cx+ax*e*d.strength/sx+wob, y=cy-ay*e*d.momentum/sy+Math.cos(T*.7+i)*2;
    const px=cx+ax*e*(d.strength-d.momentum*.35)/sx, py=cy;
    const g=X.createLinearGradient(px,py,x,y); g.addColorStop(0,c+'00'); g.addColorStop(1,c+'cc');
    X.strokeStyle=g; X.lineWidth=2; X.beginPath(); X.moveTo(px,py); X.quadraticCurveTo((px+x)/2,(py+y)/2-14,x,y); X.stroke();
    X.shadowColor=c; X.shadowBlur=12; X.fillStyle=c; X.beginPath(); X.arc(x,y,4.5,0,6.283); X.fill(); X.shadowBlur=0;
    X.font='9.5px {FONT_JS}'; X.fillStyle='#C9CED8'; X.textAlign='left'; X.fillText(d.name.slice(0,22),x+8,y-6);
    X.fillStyle='#5D6577'; X.fillText((d.strength>=0?'+':'')+d.strength+' pts',x+8,y+6); }});
  requestAnimationFrame(frame);
}}
requestAnimationFrame(frame);
</script>"""


def calibration_html(calib: dict | None, height: int = 300) -> str:
    if not calib or not calib.get("bins"):
        return _empty("◆ CALIBRACIÓN", "Compara lo que la mesa predijo con lo que pasó de verdad (se calcula al correr la mesa).", height)
    rows = []
    for i, b in enumerate(calib["bins"]):
        diff = b["real"] - b["pred"]
        c = "#3DDC97" if abs(diff) <= 7 else "#F5A623" if abs(diff) <= 15 else "#FF4D6D"
        rows.append(f'<div style="margin:9px 0;font-size:10.5px;color:#C9CED8">Dijo <b>{b["range"]}</b> '
                    f'<span style="color:#5D6577">({b["n"]} casos)</span>'
                    f'<div style="position:relative;height:9px;background:#11151f;border-radius:6px;margin-top:4px">'
                    f'<div style="position:absolute;left:0;top:0;bottom:0;width:{b["pred"]:.0f}%;background:#5AC8FA55;border-radius:6px;'
                    f'animation:grow .9s ease {i * .12:.2f}s both"></div>'
                    f'<div style="position:absolute;left:0;top:2px;height:5px;width:{b["real"]:.0f}%;background:{c};border-radius:6px;'
                    f'box-shadow:0 0 10px {c};animation:grow 1.1s ease {i * .12 + .2:.2f}s both"></div></div>'
                    f'<div style="font-size:10px;color:{c};margin-top:2px">Real: {b["real"]:.0f}% (predicho {b["pred"]:.0f}%)</div></div>')
    brier = f'{calib["brier"]:.3f}' if calib.get("brier") is not None else "—"
    return f"""
<div style="height:{height}px;overflow:auto;border-radius:14px;background:#070a10;border:1px solid rgba(148,163,184,.12);
 padding:8px 12px;font-family:{FONT}">
 <div style="font-size:10.5px;letter-spacing:.16em;color:#9BA3B4">◆ CALIBRACIÓN <span style="color:#5D6577">— ¿ACIERTA LO QUE DICE?</span></div>
 <div style="font-size:10px;color:#5D6577;margin-top:3px">{calib["n"]} predicciones del pasado sin mirar el futuro · Brier {brier} (menor = mejor)</div>
 <style>@keyframes grow{{from{{transform:scaleX(0);transform-origin:left}}to{{transform:scaleX(1);transform-origin:left}}}}</style>
 {''.join(rows)}
</div>"""


def kpi_html(items: list[tuple[str, str, str]], height: int = 74) -> str:
    """Barra superior: [(etiqueta, valor, color)] + reloj UTC en vivo + agentes online."""
    cells = "".join(f'<div class="k{i}" style="padding:0 12px;border-right:1px solid rgba(148,163,184,.10);white-space:nowrap">'
                    f'<div style="font-size:9px;letter-spacing:.14em;color:#5D6577">{escape(k)}</div>'
                    f'<div style="font-size:17px;font-weight:700;color:{c};margin-top:2px">{escape(v)}</div></div>'
                    for i, (k, v, c) in enumerate(items))
    return f"""
<div style="height:{height - 6}px;display:flex;align-items:center;justify-content:space-between;border-radius:14px;
 background:linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,0) 40%),rgba(13,16,23,.9);
 border:1px solid rgba(212,175,55,.22);font-family:{FONT};overflow:hidden">
 <div style="display:flex;align-items:center">
  <div style="padding:0 16px;font-weight:800;letter-spacing:.2em;font-size:14px;white-space:nowrap;background:linear-gradient(90deg,#F7DC8B,#D4AF37);
   -webkit-background-clip:text;color:transparent">FG DESK</div>{cells}</div>
 <div style="display:flex;align-items:center;gap:18px;padding-right:18px">
  <div id="clk" style="font-size:19px;font-weight:700;color:#ECE8DC;white-space:nowrap">--:--:--</div>
  <div class="utc" style="font-size:10px;color:#5D6577;letter-spacing:.14em">UTC</div>
  <div class="onl" style="font-size:10px;letter-spacing:.12em;color:#3DDC97;border:1px solid rgba(61,220,151,.35);padding:5px 9px;border-radius:8px;white-space:nowrap">
   <span style="animation:p 1.4s infinite">●</span> 7 AGENTES ONLINE</div></div>
</div>
<style>@keyframes p{{0%,100%{{opacity:1}}50%{{opacity:.25}}}}
@media (max-width:1180px){{.k2,.k4,.utc{{display:none}}}} @media (max-width:980px){{.k5,.k3{{display:none}}}}
@media (max-width:820px){{.k6,.onl{{display:none}}}}</style>
<script>const k=document.getElementById('clk');setInterval(()=>{{const d=new Date();k.textContent=d.toISOString().slice(11,19);}},500);</script>"""


def _empty(title: str, text: str, height: int) -> str:
    return (f'<div style="height:{height}px;border-radius:14px;background:#070a10;border:1px dashed rgba(148,163,184,.16);'
            f'font-family:{FONT};padding:10px 12px;color:#5D6577;font-size:11px">'
            f'<div style="letter-spacing:.16em;color:#9BA3B4;font-size:10.5px">{title}</div><div style="margin-top:8px">{escape(text)}</div></div>')


def pipeline_html(decision, height: int = 96) -> str:
    """Barra del pipeline: los 7 agentes en fila con pulsos que viajan de etapa en etapa."""
    data = json.dumps(_agents_payload(decision))
    return (r"""
<div id="w" style="position:relative;height:__H__px;border-radius:14px;overflow:hidden;background:#06080d;
 border:1px solid rgba(148,163,184,.12);font-family:__FONTCSS__">
 <div style="position:absolute;left:12px;top:7px;font-size:10px;letter-spacing:.16em;color:#9BA3B4">◆ PIPELINE <span style="color:#5D6577">— FLUJO DE LA DECISIÓN</span></div>
 <canvas id="c" style="position:absolute;inset:0;width:100%;height:100%"></canvas></div>
<script>
const A=__DATA__, FONT='__FONTJS__'; const W=document.getElementById('w'),C=document.getElementById('c'),X=C.getContext('2d'); let w,h,t0=performance.now();
const rgba=(hex,a)=>{const n=parseInt(hex.slice(1),16);return 'rgba('+(n>>16&255)+','+(n>>8&255)+','+(n&255)+','+a+')';};
function size(){const d=Math.min(2,devicePixelRatio||1);w=W.clientWidth;h=W.clientHeight;C.width=w*d;C.height=h*d;X.setTransform(d,0,0,d,0,0);} size(); addEventListener('resize',size);
function frame(now){const T=Math.max(0,(now-t0)/1000); X.clearRect(0,0,w,h); const n=A.length, y=h*.58, x0=46, x1=w-46, dx=(x1-x0)/(n-1);
  X.strokeStyle='rgba(148,163,184,.14)'; X.lineWidth=2; X.beginPath(); X.moveTo(x0,y); X.lineTo(x1,y); X.stroke();
  const reached=A.filter(a=>a.status!=='idle').length; const prog=Math.min(reached-1,T/.62);
  if(reached>0){const g=X.createLinearGradient(x0,0,x0+dx*Math.max(0,prog),0); g.addColorStop(0,'rgba(212,175,55,.2)'); g.addColorStop(1,'#F2D27A');
    X.strokeStyle=g; X.lineWidth=2.5; X.shadowColor='#F2D27A'; X.shadowBlur=10; X.beginPath(); X.moveTo(x0,y); X.lineTo(x0+dx*Math.max(0,prog),y); X.stroke(); X.shadowBlur=0;}
  for(let k=0;k<3;k++){const p=((T*.35+k/3)%1)*(Math.max(1,reached)-1); const px=x0+dx*p; const g=X.createRadialGradient(px,y,0,px,y,10);
    g.addColorStop(0,'rgba(255,244,200,.95)'); g.addColorStop(1,'rgba(242,210,122,0)'); X.fillStyle=g; X.beginPath(); X.arc(px,y,10,0,6.283); X.fill();}
  A.forEach((a,i)=>{const x=x0+dx*i, on=a.status!=='idle'&&T>i*.62; const c=a.status==='pass'?'#3DDC97':a.status==='block'?'#FF4D6D':a.status==='info'?a.color:'#3a4150';
    const r=13+(on?Math.sin(T*3+i)*1.2:0); const g=X.createRadialGradient(x,y,2,x,y,r*2.4); g.addColorStop(0,rgba(a.color,on?.4:.08)); g.addColorStop(1,rgba(a.color,0));
    X.fillStyle=g; X.beginPath(); X.arc(x,y,r*2.4,0,6.283); X.fill(); X.fillStyle='#0b0f17'; X.strokeStyle=on?c:'#3a4150'; X.lineWidth=2;
    X.beginPath(); X.arc(x,y,r,0,6.283); X.fill(); X.stroke(); X.font='12px system-ui,Apple Color Emoji,Segoe UI Emoji'; X.textAlign='center'; X.textBaseline='middle'; X.fillText(a.icon,x,y+1);
    X.font='700 9px '+FONT; X.fillStyle=on?a.color:'#5D6577'; X.fillText(a.key,x,y-r-9);
    X.font='9px '+FONT; X.fillStyle=on?c:'#3a4150'; X.fillText(on?(a.status==='pass'?'✓ PASA':a.status==='block'?'✕ BLOQUEA':'● INFO')+(a.ms!=null?' · '+a.ms+'ms':''):'—',x,y+r+11);});
  requestAnimationFrame(frame);}
requestAnimationFrame(frame);
</script>""".replace("__DATA__", data).replace("__FONTJS__", FONT_JS).replace("__FONTCSS__", FONT).replace("__H__", str(height)))


def tape_html(tape: dict | None, height: int = 330) -> str:
    """THE TAPE: velas reales de 5 min, VWAP con bandas Z (±1σ, ±2σ), cursor de barrido y libro de órdenes."""
    if not tape:
        return _empty("◆ THE TAPE", "Velas de 5 min con VWAP y bandas Z (se llena al correr la mesa).", height)
    return (r"""
<div id="w" style="position:relative;height:__H__px;border-radius:14px;overflow:hidden;background:#06080d;
 border:1px solid rgba(148,163,184,.12);font-family:__FONTCSS__">
 <div style="position:absolute;left:12px;top:8px;font-size:10.5px;letter-spacing:.16em;color:#9BA3B4;z-index:2">◆ THE TAPE
  <span style="color:#5D6577">— 5M · VWAP · BANDAS Z · LIBRO</span></div>
 <div id="px" style="position:absolute;right:12px;top:6px;font-size:16px;font-weight:700;color:#ECE8DC;z-index:2"></div>
 <canvas id="c" style="position:absolute;inset:0;width:100%;height:100%"></canvas></div>
<script>
const D=__DATA__, FONT='__FONTJS__'; const W=document.getElementById('w'),C=document.getElementById('c'),X=C.getContext('2d'); let w,h,t0=performance.now();
function size(){const d=Math.min(2,devicePixelRatio||1);w=W.clientWidth;h=W.clientHeight;C.width=w*d;C.height=h*d;X.setTransform(d,0,0,d,0,0);} size(); addEventListener('resize',size);
const n=D.c.length, sd=D.sd||0; const vals=D.h.concat(D.l, D.vwap.map(v=>v+2*sd), D.vwap.map(v=>v-2*sd)).filter(x=>x!=null&&isFinite(x));
let lo=Math.min(...vals), hi=Math.max(...vals); const pad=(hi-lo)*.06; lo-=pad; hi+=pad;
document.getElementById('px').innerHTML='$'+D.c[n-1].toLocaleString(undefined,{maximumFractionDigits:6})+'<span style="font-size:10px;color:#9BA3B4;margin-left:8px">z '+(D.z==null?'—':(D.z>=0?'+':'')+D.z.toFixed(2))+'</span>';
function frame(now){const T=Math.max(0,(now-t0)/1000); X.clearRect(0,0,w,h); const PL=10, PR=70, PT=34, PB=34, cw=(w-PL-PR)/n, Y=v=>PT+(h-PT-PB)*(1-(v-lo)/(hi-lo));
  // Bandas Z
  [[2,'rgba(179,136,255,.07)'],[1,'rgba(90,200,250,.08)']].forEach(([k,f])=>{X.fillStyle=f; X.beginPath();
    D.vwap.forEach((v,i)=>{const x=PL+cw*(i+.5); i?X.lineTo(x,Y(v+k*sd)):X.moveTo(x,Y(v+k*sd));});
    for(let i=n-1;i>=0;i--) X.lineTo(PL+cw*(i+.5),Y(D.vwap[i]-k*sd)); X.closePath(); X.fill();});
  X.strokeStyle='rgba(242,210,122,.85)'; X.setLineDash([4,3]); X.lineWidth=1.2; X.beginPath(); D.vwap.forEach((v,i)=>{const x=PL+cw*(i+.5); i?X.lineTo(x,Y(v)):X.moveTo(x,Y(v));}); X.stroke(); X.setLineDash([]);
  // Velas (aparecen en barrido)
  const shown=Math.min(n,Math.floor(T/1.6*n)+1);
  for(let i=0;i<shown;i++){const up=D.c[i]>=D.o[i], c=up?'#3DDC97':'#FF4D6D', x=PL+cw*(i+.5);
    X.strokeStyle=c; X.lineWidth=1; X.beginPath(); X.moveTo(x,Y(D.h[i])); X.lineTo(x,Y(D.l[i])); X.stroke();
    const y1=Y(Math.max(D.o[i],D.c[i])), y2=Y(Math.min(D.o[i],D.c[i])); X.fillStyle=up?'rgba(61,220,151,.85)':'rgba(255,77,109,.85)'; X.fillRect(x-cw*.32,y1,cw*.64,Math.max(1,y2-y1));}
  // Cursor de barrido
  const sx=PL+((T*.18)%1)*(w-PL-PR); const g=X.createLinearGradient(sx-40,0,sx,0); g.addColorStop(0,'rgba(90,200,250,0)'); g.addColorStop(1,'rgba(90,200,250,.18)');
  X.fillStyle=g; X.fillRect(sx-40,PT,40,h-PT-PB); X.strokeStyle='rgba(90,200,250,.5)'; X.beginPath(); X.moveTo(sx,PT); X.lineTo(sx,h-PB); X.stroke();
  // Precio actual
  const py=Y(D.c[n-1]); X.strokeStyle='rgba(236,232,220,.45)'; X.setLineDash([2,3]); X.beginPath(); X.moveTo(PL,py); X.lineTo(w-PR,py); X.stroke(); X.setLineDash([]);
  X.shadowColor='#F2D27A'; X.shadowBlur=14+Math.sin(T*4)*6; X.fillStyle='#F2D27A'; X.beginPath(); X.arc(PL+cw*(n-.5),py,3.5,0,6.283); X.fill(); X.shadowBlur=0;
  X.font='9px '+FONT; X.fillStyle='#9BA3B4'; X.textAlign='left'; [['+2σ',2],['+1σ',1],['VWAP',0],['−1σ',-1],['−2σ',-2]].forEach(([l,k])=>X.fillText(l,w-PR+6,Y(D.vwap[n-1]+k*sd)+3));
  // Libro de órdenes (compradores vs vendedores)
  const b=D.book==null?null:(D.book+1)/2; const bx=PL, bw=w-PL-PR, by=h-20;
  X.fillStyle='rgba(148,163,184,.12)'; X.fillRect(bx,by,bw,7);
  if(b!=null){const wob=Math.sin(T*2)*.01; X.fillStyle='#3DDC97'; X.fillRect(bx,by,bw*(b+wob),7); X.fillStyle='#FF4D6D'; X.fillRect(bx+bw*(b+wob),by,bw*(1-b-wob),7);
    X.fillStyle='#C9CED8'; X.fillText('LIBRO · '+Math.round(b*100)+'% COMPRAS / '+Math.round((1-b)*100)+'% VENTAS',bx,by-4);}
  else {X.fillStyle='#5D6577'; X.fillText('LIBRO DE ÓRDENES NO DISPONIBLE (DEMO / SIN CONEXIÓN)',bx,by-4);}
  requestAnimationFrame(frame);}
requestAnimationFrame(frame);
</script>""".replace("__DATA__", json.dumps(tape)).replace("__FONTJS__", FONT_JS).replace("__FONTCSS__", FONT)
            .replace("__H__", str(height)))


def radar_html(radar: list[dict] | None, height: int = 330) -> str:
    """Radar de señales: cada eje 0–100 (real de la decisión), con barrido giratorio y morfología animada."""
    if not radar:
        return _empty("◆ RADAR DE SEÑALES", "Las señales de la decisión en un vistazo (se llena al correr la mesa).", height)
    return (r"""
<div id="w" style="position:relative;height:__H__px;border-radius:14px;overflow:hidden;background:#06080d;
 border:1px solid rgba(148,163,184,.12);font-family:__FONTCSS__">
 <div style="position:absolute;left:12px;top:8px;font-size:10.5px;letter-spacing:.16em;color:#9BA3B4">◆ RADAR DE SEÑALES
  <span style="color:#5D6577">— 0 A 100</span></div>
 <canvas id="c" style="position:absolute;inset:0;width:100%;height:100%"></canvas></div>
<script>
const D=__DATA__, FONT='__FONTJS__'; const W=document.getElementById('w'),C=document.getElementById('c'),X=C.getContext('2d'); let w,h,t0=performance.now();
function size(){const d=Math.min(2,devicePixelRatio||1);w=W.clientWidth;h=W.clientHeight;C.width=w*d;C.height=h*d;X.setTransform(d,0,0,d,0,0);} size(); addEventListener('resize',size);
function frame(now){const T=Math.max(0,(now-t0)/1000), e=1-Math.pow(1-Math.min(1,T/1.4),3); X.clearRect(0,0,w,h);
  const cx=w/2, cy=h/2+12, R=Math.min(w,h)*.34, n=D.length, ang=i=>-Math.PI/2+i*6.283/n;
  for(let r=1;r<=4;r++){X.strokeStyle='rgba(148,163,184,'+(r===4?.18:.08)+')'; X.beginPath(); for(let i=0;i<=n;i++){const a=ang(i%n), x=cx+R*r/4*Math.cos(a), y=cy+R*r/4*Math.sin(a); i?X.lineTo(x,y):X.moveTo(x,y);} X.stroke();}
  D.forEach((d,i)=>{const a=ang(i); X.strokeStyle='rgba(148,163,184,.10)'; X.beginPath(); X.moveTo(cx,cy); X.lineTo(cx+R*Math.cos(a),cy+R*Math.sin(a)); X.stroke();
    X.font='9px '+FONT; X.fillStyle='#9BA3B4'; X.textAlign=Math.cos(a)>.2?'left':Math.cos(a)<-.2?'right':'center';
    X.fillText(d.name,cx+(R+12)*Math.cos(a),cy+(R+12)*Math.sin(a)+3); X.fillStyle=d.good?'#3DDC97':'#FF4D6D';
    X.fillText(Math.round(d.value),cx+(R+12)*Math.cos(a),cy+(R+12)*Math.sin(a)+14);});
  // Barrido
  const sw=T*1.2; const g=X.createConicGradient?X.createConicGradient(sw,cx,cy):null;
  if(g){g.addColorStop(0,'rgba(61,220,151,.25)'); g.addColorStop(.12,'rgba(61,220,151,0)'); g.addColorStop(1,'rgba(61,220,151,0)'); X.fillStyle=g; X.beginPath(); X.arc(cx,cy,R,0,6.283); X.fill();}
  // Polígono de señales
  X.beginPath(); D.forEach((d,i)=>{const a=ang(i), r=R*e*(d.value/100)*(1+Math.sin(T*2+i)*.015); const x=cx+r*Math.cos(a), y=cy+r*Math.sin(a); i?X.lineTo(x,y):X.moveTo(x,y);}); X.closePath();
  const f=X.createRadialGradient(cx,cy,0,cx,cy,R); f.addColorStop(0,'rgba(242,210,122,.08)'); f.addColorStop(1,'rgba(242,210,122,.30)'); X.fillStyle=f; X.fill();
  X.strokeStyle='#F2D27A'; X.lineWidth=1.6; X.shadowColor='#F2D27A'; X.shadowBlur=12; X.stroke(); X.shadowBlur=0;
  D.forEach((d,i)=>{const a=ang(i), r=R*e*d.value/100; X.fillStyle=d.good?'#3DDC97':'#FF4D6D'; X.beginPath(); X.arc(cx+r*Math.cos(a),cy+r*Math.sin(a),3.2,0,6.283); X.fill();});
  requestAnimationFrame(frame);}
requestAnimationFrame(frame);
</script>""".replace("__DATA__", json.dumps(radar)).replace("__FONTJS__", FONT_JS).replace("__FONTCSS__", FONT)
            .replace("__H__", str(height)))


def livewire_html(decision, height: int = 34) -> str:
    """LIVE WIRE: cinta que corre con cada paso de la mesa."""
    items = (decision.trace() if decision else []) or ["FG DESK en espera · pulsa «Correr la mesa»"]
    text = "   ◆   ".join(escape(t) for t in items)
    return f"""
<div style="height:{height - 4}px;display:flex;align-items:center;border-radius:10px;overflow:hidden;background:#06080d;
 border:1px solid rgba(212,175,55,.18);font-family:{FONT};font-size:11px;color:#C9CED8">
 <div style="padding:0 12px;color:#05070b;background:linear-gradient(90deg,#F7DC8B,#D4AF37);height:100%;display:flex;align-items:center;
  font-weight:800;letter-spacing:.16em;white-space:nowrap">LIVE WIRE</div>
 <div style="overflow:hidden;white-space:nowrap;flex:1"><div style="display:inline-block;padding-left:100%;animation:mq {max(18, len(text) // 9)}s linear infinite">
  {text}   ◆   {text}</div></div>
</div><style>@keyframes mq{{from{{transform:translateX(0)}}to{{transform:translateX(-100%)}}}}</style>"""
