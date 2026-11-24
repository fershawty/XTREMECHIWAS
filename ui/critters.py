"""
ui/critters.py
==============
Personajes de los agentes de FG DESK (originales de FG VIEW, dibujados en canvas).

    SPOTTER  explorador de un solo ojo grande con antena de radar  (verde)
    PRIOR    búho sabio con orejas de plumas y anteojos             (naranja)
    PRICER   mago con sombrero de estrella y bola de cristal         (violeta)
    EDGE     aleta afilada con cejas decididas                       (rosa)
    KELLY    robot cubo con visor LED y antena                       (cian)
    TAKER    cangrejo dorado que abre y cierra las pinzas            (dorado)
    CLOSER   fantasma guardián con escudo                            (naranja rojizo)
    WHALE    ballena con chorro de agua                              (azul)
    FLOW     gota de agua con olas por dentro                        (turquesa)
    FUNDING  moneda que gira con borde dentado                       (lima)
    GATE     señal octagonal con pluma de barrera (sube si pasa)     (rojo)
    GUARD    tortuga con caparazón hexagonal                         (índigo)

Expresiones: pass = sonríe · block = ceño fruncido y X roja · info = neutral · idle = dormido.
Los ojos parpadean y miran hacia donde trabajan.

CRITTERS_JS define `drawCritter(x, key, px, py, s, color, t, mood, lookX, lookY)` para usarla en
cualquier canvas (terminal, sinapsis, pipeline).
"""

CRITTERS_JS = r"""
function _rgba(h,a){const n=parseInt(h.slice(1),16);return 'rgba('+(n>>16&255)+','+(n>>8&255)+','+(n&255)+','+Math.max(0,Math.min(1,a)).toFixed(3)+')';}
function _shade(h,f){const n=parseInt(h.slice(1),16);const r=Math.min(255,Math.max(0,(n>>16&255)*f)),g=Math.min(255,Math.max(0,(n>>8&255)*f)),b=Math.min(255,Math.max(0,(n&255)*f));
  return 'rgb('+r.toFixed(0)+','+g.toFixed(0)+','+b.toFixed(0)+')';}
function _body(x,px,py,s,color){const g=x.createRadialGradient(px-s*.35,py-s*.45,s*.08,px,py,s*1.15); g.addColorStop(0,'#ffffff');
  g.addColorStop(.28,_shade(color,1.25)); g.addColorStop(.75,color); g.addColorStop(1,_shade(color,.55)); return g;}
function _eye(x,ex,ey,r,t,mood,lx,ly,seed){
  const blink=((t+seed*1.7)%3.6)<.13; if(mood==='idle'||blink){x.strokeStyle='#14161c';x.lineWidth=Math.max(1,r*.32);x.beginPath();x.arc(ex,ey,r*.75,.15*Math.PI,.85*Math.PI);x.stroke();return;}
  if(mood==='block'){x.strokeStyle='#14161c';x.lineWidth=Math.max(1,r*.32);x.beginPath();x.moveTo(ex-r*.6,ey-r*.6);x.lineTo(ex+r*.6,ey+r*.6);x.moveTo(ex+r*.6,ey-r*.6);x.lineTo(ex-r*.6,ey+r*.6);x.stroke();return;}
  x.fillStyle='#ffffff'; x.beginPath(); x.ellipse(ex,ey,r,r*1.12,0,0,6.283); x.fill();
  const dx=Math.max(-1,Math.min(1,lx))*r*.32, dy=Math.max(-1,Math.min(1,ly))*r*.28;
  x.fillStyle='#12141a'; x.beginPath(); x.arc(ex+dx,ey+dy,r*.58,0,6.283); x.fill();
  x.fillStyle='#ffffff'; x.beginPath(); x.arc(ex+dx-r*.2,ey+dy-r*.25,r*.2,0,6.283); x.fill();}
function _mouth(x,mx,my,w,mood){x.strokeStyle='#14161c';x.lineWidth=Math.max(1,w*.16);x.lineCap='round';x.beginPath();
  if(mood==='pass') x.arc(mx,my-w*.35,w*.55,.2*Math.PI,.8*Math.PI); else if(mood==='block') x.arc(mx,my+w*.35,w*.45,1.2*Math.PI,1.8*Math.PI);
  else {x.moveTo(mx-w*.35,my); x.lineTo(mx+w*.35,my);} x.stroke();}
function _brows(x,ex1,ex2,ey,r,mood,angle){if(mood!=='block'&&!angle) return; const a=mood==='block'?.5:angle; x.strokeStyle='#14161c'; x.lineWidth=Math.max(1,r*.35); x.lineCap='round';
  x.beginPath(); x.moveTo(ex1-r*.8,ey-r*1.35-a*r); x.lineTo(ex1+r*.6,ey-r*1.35+a*r*.6); x.moveTo(ex2+r*.8,ey-r*1.35-a*r); x.lineTo(ex2-r*.6,ey-r*1.35+a*r*.6); x.stroke();}
function drawCritter(x,key,px,py,s,color,t,mood,lx,ly){
  lx=lx||0; ly=ly||0; const bob=Math.sin(t*2.4+key.length)*s*.06; py+=bob; x.save(); x.lineJoin='round'; x.lineCap='round';
  // Sombra y aura
  x.fillStyle='rgba(0,0,0,.35)'; x.beginPath(); x.ellipse(px,py+s*1.18-bob,s*.75,s*.16,0,0,6.283); x.fill();
  const au=x.createRadialGradient(px,py,s*.4,px,py,s*2.4); au.addColorStop(0,_rgba(color,mood==='idle'?.12:.35)); au.addColorStop(1,_rgba(color,0)); x.fillStyle=au; x.beginPath(); x.arc(px,py,s*2.4,0,6.283); x.fill();
  const outline=()=>{x.strokeStyle=_rgba('#ffffff',.25); x.lineWidth=Math.max(1,s*.06); x.stroke();};
  if(key==='SPOTTER'){ // Explorador de un ojo con antena de radar
    x.strokeStyle=_shade(color,.7); x.lineWidth=s*.12; x.beginPath(); x.moveTo(px,py-s*.85); x.lineTo(px+s*.25,py-s*1.45); x.stroke();
    const ra=t*3; x.fillStyle='#e9fff6'; x.beginPath(); x.ellipse(px+s*.28,py-s*1.5,s*.26,s*.14,ra,0,6.283); x.fill(); x.strokeStyle=color; x.lineWidth=s*.06; x.stroke();
    x.fillStyle=_body(x,px,py,s,color); x.beginPath(); x.ellipse(px,py,s,s*.95,0,0,6.283); x.fill(); outline();
    x.fillStyle=_shade(color,.6); x.beginPath(); x.arc(px,py-s*.08,s*.56,0,6.283); x.fill();
    _eye(x,px,py-s*.08,s*.46,t,mood,lx,ly,1); x.strokeStyle=_rgba('#ffffff',.6); x.lineWidth=s*.05; x.beginPath(); x.arc(px,py-s*.08,s*.56,-.9,.3); x.stroke();
    _mouth(x,px,py+s*.62,s*.42,mood);
  } else if(key==='PRIOR'){ // Búho sabio
    x.fillStyle=_shade(color,.8); [-1,1].forEach(d=>{x.beginPath(); x.moveTo(px+d*s*.45,py-s*.7); x.lineTo(px+d*s*.85,py-s*1.35); x.lineTo(px+d*s*.95,py-s*.55); x.fill();});
    x.fillStyle=_body(x,px,py,s,color); x.beginPath(); x.ellipse(px,py+s*.05,s*.95,s*1.02,0,0,6.283); x.fill(); outline();
    x.fillStyle=_rgba('#fff3dc',.85); x.beginPath(); x.ellipse(px,py+s*.42,s*.5,s*.42,0,0,6.283); x.fill();
    [-1,1].forEach(d=>{x.fillStyle=_shade(color,.6); x.beginPath(); x.arc(px+d*s*.38,py-s*.22,s*.36,0,6.283); x.fill();});
    _eye(x,px-s*.38,py-s*.22,s*.27,t,mood,lx,ly,2); _eye(x,px+s*.38,py-s*.22,s*.27,t,mood,lx,ly,2.1);
    x.strokeStyle='#2a1a05'; x.lineWidth=s*.05; x.beginPath(); x.arc(px-s*.38,py-s*.22,s*.34,0,6.283); x.moveTo(px-s*.04,py-s*.22); x.lineTo(px+s*.04,py-s*.22); x.arc(px+s*.38,py-s*.22,s*.34,Math.PI,Math.PI*3); x.stroke();
    x.fillStyle='#3a2405'; x.beginPath(); x.moveTo(px-s*.12,py+s*.08); x.lineTo(px+s*.12,py+s*.08); x.lineTo(px,py+s*.3); x.fill(); _brows(x,px-s*.38,px+s*.38,py-s*.22,s*.27,mood,0);
  } else if(key==='PRICER'){ // Mago con bola de cristal
    x.fillStyle=_body(x,px,py,s,color); x.beginPath(); x.moveTo(px,py-s*1.1); x.bezierCurveTo(px+s*1.1,py-s*.4,px+s*1.05,py+s*.95,px,py+s*1.02); x.bezierCurveTo(px-s*1.05,py+s*.95,px-s*1.1,py-s*.4,px,py-s*1.1); x.fill(); outline();
    x.fillStyle=_shade(color,.45); x.beginPath(); x.moveTo(px-s*.62,py-s*.62); x.lineTo(px+s*.62,py-s*.62); x.lineTo(px+s*.08,py-s*1.75); x.closePath(); x.fill();
    x.fillStyle=_shade(color,.35); x.fillRect(px-s*.75,py-s*.68,s*1.5,s*.14);
    const st=(cx,cy,r)=>{x.fillStyle='#FFE9A8'; x.beginPath(); for(let k=0;k<10;k++){const a=-Math.PI/2+k*Math.PI/5, rr=k%2?r*.45:r; x.lineTo(cx+rr*Math.cos(a),cy+rr*Math.sin(a));} x.fill();};
    st(px+s*.02,py-s*1.12,s*.2);
    _eye(x,px-s*.3,py-s*.1,s*.24,t,mood,lx,ly,3); _eye(x,px+s*.3,py-s*.1,s*.24,t,mood,lx,ly,3.2); _brows(x,px-s*.3,px+s*.3,py-s*.1,s*.24,mood,0); _mouth(x,px,py+s*.38,s*.36,mood);
    const oa=t*2.2, ox=px+Math.cos(oa)*s*1.25, oy=py+s*.2+Math.sin(oa)*s*.35; const cb=x.createRadialGradient(ox-s*.06,oy-s*.06,1,ox,oy,s*.22); cb.addColorStop(0,'#fff'); cb.addColorStop(1,_rgba('#5AC8FA',.6));
    x.fillStyle=cb; x.beginPath(); x.arc(ox,oy,s*.2,0,6.283); x.fill();
  } else if(key==='EDGE'){ // Aleta afilada
    x.fillStyle=_body(x,px,py,s,color); x.beginPath(); x.moveTo(px-s*.2,py-s*1.35); x.quadraticCurveTo(px+s*1.15,py-s*.1,px+s*.85,py+s*.95); x.lineTo(px-s*.95,py+s*.95);
    x.quadraticCurveTo(px-s*1.05,py-s*.2,px-s*.2,py-s*1.35); x.fill(); outline();
    x.strokeStyle=_rgba('#ffffff',.5); x.lineWidth=s*.06; x.beginPath(); x.moveTo(px-s*.1,py-s*1.1); x.quadraticCurveTo(px+s*.7,py-s*.2,px+s*.6,py+s*.6); x.stroke();
    _eye(x,px-s*.32,py+s*.05,s*.23,t,mood,lx,ly,4); _eye(x,px+s*.28,py+s*.05,s*.23,t,mood,lx,ly,4.1); _brows(x,px-s*.32,px+s*.28,py+s*.05,s*.23,mood,.35); _mouth(x,px,py+s*.55,s*.34,mood);
  } else if(key==='KELLY'){ // Robot cubo
    x.strokeStyle=_shade(color,.65); x.lineWidth=s*.1; x.beginPath(); x.moveTo(px,py-s*.9); x.lineTo(px,py-s*1.3); x.stroke();
    x.fillStyle=(Math.sin(t*6)>0)?'#ff5c8a':'#fff'; x.beginPath(); x.arc(px,py-s*1.38,s*.12,0,6.283); x.fill();
    x.fillStyle=_body(x,px,py,s,color); x.beginPath(); x.roundRect(px-s*.92,py-s*.9,s*1.84,s*1.8,s*.38); x.fill(); outline();
    x.fillStyle='#071019'; x.beginPath(); x.roundRect(px-s*.66,py-s*.48,s*1.32,s*.56,s*.2); x.fill();
    const sh=Math.max(-1,Math.min(1,lx))*s*.1; if(mood==='idle'){x.fillStyle=_rgba(color,.6); x.fillRect(px-s*.42+sh,py-s*.22,s*.26,s*.06); x.fillRect(px+s*.16+sh,py-s*.22,s*.26,s*.06);}
    else if(mood==='block'){x.fillStyle='#FF4D6D'; x.font='800 '+(s*.4).toFixed(0)+'px monospace'; x.textAlign='center'; x.textBaseline='middle'; x.fillText('✕ ✕',px,py-s*.2);}
    else {x.fillStyle='#9CFFE8'; x.shadowColor='#9CFFE8'; x.shadowBlur=s*.4; x.beginPath(); x.arc(px-s*.28+sh,py-s*.2,s*.11,0,6.283); x.arc(px+s*.28+sh,py-s*.2,s*.11,0,6.283); x.fill(); x.shadowBlur=0;}
    x.strokeStyle=_rgba('#071019',.7); x.lineWidth=s*.07; for(let k=0;k<3;k++){x.beginPath(); x.moveTo(px-s*.5+k*s*.38,py+s*.35); x.lineTo(px-s*.5+k*s*.38,py+s*.6); x.stroke();}
    _mouth(x,px,py+s*.25,s*.3,mood==='pass'?'pass':mood==='block'?'block':'info');
  } else if(key==='TAKER'){ // Cangrejo con pinzas
    const op=(Math.sin(t*4)+1)/2*.6;
    [-1,1].forEach(d=>{x.strokeStyle=_shade(color,.7); x.lineWidth=s*.12; x.beginPath(); x.moveTo(px+d*s*.75,py+s*.1); x.quadraticCurveTo(px+d*s*1.25,py-s*.1,px+d*s*1.2,py-s*.55); x.stroke();
      x.fillStyle=_shade(color,.9); x.beginPath(); x.arc(px+d*s*1.22,py-s*.72,s*.3,(d>0?-.4:-.4)+Math.PI*(d>0?1.5:1.5)-op,Math.PI*(d>0?1.5:1.5)+op+Math.PI,false); x.fill();
      x.strokeStyle=_shade(color,.6); x.lineWidth=s*.08; for(let k=0;k<3;k++){x.beginPath(); x.moveTo(px+d*s*.55,py+s*.45+k*s*.12); x.lineTo(px+d*s*1.0,py+s*.7+k*s*.15); x.stroke();}});
    x.fillStyle=_body(x,px,py,s,color); x.beginPath(); x.ellipse(px,py+s*.15,s*.9,s*.72,0,0,6.283); x.fill(); outline();
    [-1,1].forEach(d=>{x.strokeStyle=_shade(color,.6); x.lineWidth=s*.08; x.beginPath(); x.moveTo(px+d*s*.25,py-s*.4); x.lineTo(px+d*s*.3,py-s*.7); x.stroke();});
    _eye(x,px-s*.3,py-s*.78,s*.2,t,mood,lx,ly,5); _eye(x,px+s*.3,py-s*.78,s*.2,t,mood,lx,ly,5.2); _mouth(x,px,py+s*.35,s*.36,mood);
  } else if(key==='WHALE'){ // Ballena con chorro
    for(let k=0;k<5;k++){const ph=((t*1.3+k*.2)%1); x.fillStyle=_rgba('#BFE3FF',(1-ph)*.9); x.beginPath(); x.arc(px-s*.15+(k-2)*s*.14*ph,py-s*.9-ph*s*.9,s*(.1+.05*(1-ph)),0,6.283); x.fill();}
    x.fillStyle=_shade(color,.8); x.beginPath(); x.moveTo(px+s*.8,py+s*.1); x.quadraticCurveTo(px+s*1.25,py-s*.1+Math.sin(t*3)*s*.08,px+s*1.5,py-s*.55); x.quadraticCurveTo(px+s*1.25,py-s*.05,px+s*1.55,py+s*.2); x.quadraticCurveTo(px+s*1.2,py+s*.3,px+s*.8,py+s*.4); x.fill();
    x.fillStyle=_body(x,px,py,s,color); x.beginPath(); x.ellipse(px-s*.05,py+s*.1,s*1.0,s*.82,0,0,6.283); x.fill(); outline();
    x.fillStyle=_rgba('#E8F4FF',.8); x.beginPath(); x.ellipse(px-s*.1,py+s*.55,s*.72,s*.32,0,0,Math.PI); x.fill();
    x.strokeStyle=_rgba('#ffffff',.35); x.lineWidth=s*.04; for(let k=0;k<3;k++){x.beginPath(); x.moveTo(px-s*.55+k*s*.3,py+s*.62); x.lineTo(px-s*.5+k*s*.3,py+s*.82); x.stroke();}
    _eye(x,px-s*.42,py-s*.12,s*.22,t,mood,lx,ly,7); _eye(x,px+s*.12,py-s*.12,s*.22,t,mood,lx,ly,7.2); _brows(x,px-s*.42,px+s*.12,py-s*.12,s*.22,mood,0); _mouth(x,px-s*.15,py+s*.3,s*.34,mood);
  } else if(key==='FLOW'){ // Gota con olas
    x.fillStyle=_body(x,px,py,s,color); x.beginPath(); x.moveTo(px,py-s*1.3); x.bezierCurveTo(px+s*.35,py-s*.75,px+s*1.0,py-s*.2,px+s*.95,py+s*.35); x.arc(px,py+s*.35,s*.95,0,Math.PI); x.bezierCurveTo(px-s*1.0,py-s*.2,px-s*.35,py-s*.75,px,py-s*1.3); x.fill(); outline();
    x.save(); x.clip(); x.strokeStyle=_rgba('#ffffff',.45); x.lineWidth=s*.07; for(let k=0;k<2;k++){x.beginPath(); for(let i=0;i<=20;i++){const wx=px-s+i*s*.1, wy=py+s*(.62+k*.25)+Math.sin(i*.7+t*4+k)*s*.06; i?x.lineTo(wx,wy):x.moveTo(wx,wy);} x.stroke();} x.restore();
    x.fillStyle=_rgba('#ffffff',.55); x.beginPath(); x.ellipse(px-s*.45,py-s*.25,s*.12,s*.24,.5,0,6.283); x.fill();
    _eye(x,px-s*.3,py+s*.02,s*.22,t,mood,lx,ly,8); _eye(x,px+s*.3,py+s*.02,s*.22,t,mood,lx,ly,8.2); _brows(x,px-s*.3,px+s*.3,py+s*.02,s*.22,mood,0); _mouth(x,px,py+s*.42,s*.32,mood);
  } else if(key==='FUNDING'){ // Moneda que gira
    const sq=.72+.28*Math.abs(Math.cos(t*1.4)); x.save(); x.translate(px,py); x.scale(sq,1);
    x.fillStyle=_shade(color,.55); x.beginPath(); for(let k=0;k<36;k++){const a=k/36*6.283, rr=k%2?s*1.02:s*1.1; x.lineTo(rr*Math.cos(a),rr*Math.sin(a));} x.fill();
    x.fillStyle=_body(x,0,0,s,color); x.beginPath(); x.arc(0,0,s*.92,0,6.283); x.fill(); x.strokeStyle=_rgba('#ffffff',.45); x.lineWidth=s*.06; x.beginPath(); x.arc(0,0,s*.74,0,6.283); x.stroke();
    x.fillStyle=_shade(color,.45); x.font='800 '+(s*.42).toFixed(0)+'px monospace'; x.textAlign='center'; x.textBaseline='middle'; x.fillText('%',0,-s*.48);
    _eye(x,-s*.3,-s*.02,s*.2,t,mood,lx,ly,9); _eye(x,s*.3,-s*.02,s*.2,t,mood,lx,ly,9.2); _brows(x,-s*.3,s*.3,-s*.02,s*.2,mood,0); _mouth(x,0,s*.4,s*.32,mood); x.restore();
  } else if(key==='GATE'){ // Señal octagonal con pluma de barrera
    const up=mood==='pass'?-1.05:mood==='block'?0:-.35+Math.sin(t*1.5)*.1; x.save(); x.translate(px+s*.95,py-s*.95); x.rotate(Math.PI+up);
    x.fillStyle='#F4F4F6'; x.fillRect(0,-s*.09,s*1.6,s*.18); x.fillStyle='#FF4D6D'; for(let k=0;k<4;k++) x.fillRect(k*s*.4+s*.1,-s*.09,s*.18,s*.18); x.restore();
    x.fillStyle='#E6E9EF'; x.beginPath(); for(let k=0;k<8;k++){const a=Math.PI/8+k*Math.PI/4; x.lineTo(px+s*1.05*Math.cos(a),py+s*1.05*Math.sin(a));} x.closePath(); x.fill();
    x.fillStyle=_body(x,px,py,s,color); x.beginPath(); for(let k=0;k<8;k++){const a=Math.PI/8+k*Math.PI/4; x.lineTo(px+s*.92*Math.cos(a),py+s*.92*Math.sin(a));} x.closePath(); x.fill();
    _eye(x,px-s*.3,py-s*.15,s*.22,t,mood,lx,ly,10); _eye(x,px+s*.3,py-s*.15,s*.22,t,mood,lx,ly,10.2); _brows(x,px-s*.3,px+s*.3,py-s*.15,s*.22,mood,.25); _mouth(x,px,py+s*.35,s*.34,mood);
  } else if(key==='GUARD'){ // Tortuga con caparazón
    [-1,1].forEach(d=>{x.fillStyle=_shade(color,.7); x.beginPath(); x.ellipse(px+d*s*.7,py+s*.82+Math.sin(t*3+d)*s*.04,s*.22,s*.14,0,0,6.283); x.fill();});
    x.fillStyle=_body(x,px,py,s,color); x.beginPath(); x.ellipse(px,py+s*.05,s*1.08,s*.95,0,Math.PI,0); x.lineTo(px+s*1.08,py+s*.5); x.quadraticCurveTo(px,py+s*.75,px-s*1.08,py+s*.5); x.closePath(); x.fill(); outline();
    x.strokeStyle=_rgba('#1b1f3a',.45); x.lineWidth=s*.06; const hx=(cx,cy,r)=>{x.beginPath(); for(let k=0;k<6;k++){const a=k*Math.PI/3; x.lineTo(cx+r*Math.cos(a),cy+r*Math.sin(a));} x.closePath(); x.stroke();};
    hx(px,py-s*.45,s*.28); hx(px-s*.5,py-s*.15,s*.24); hx(px+s*.5,py-s*.15,s*.24);
    const fx=px, fy=py+s*.38; x.fillStyle=_shade(color,1.15); x.beginPath(); x.ellipse(fx,fy,s*.62,s*.42,0,0,6.283); x.fill();
    _eye(x,fx-s*.24,fy-s*.06,s*.17,t,mood,lx,ly,11); _eye(x,fx+s*.24,fy-s*.06,s*.17,t,mood,lx,ly,11.2); _brows(x,fx-s*.24,fx+s*.24,fy-s*.06,s*.17,mood,0); _mouth(x,fx,fy+s*.22,s*.26,mood);
  } else { // CLOSER: fantasma guardián con escudo
    x.fillStyle=_body(x,px,py,s,color); x.beginPath(); x.moveTo(px-s*.9,py+s*.9); x.lineTo(px-s*.9,py-s*.1); x.arc(px,py-s*.1,s*.9,Math.PI,0); x.lineTo(px+s*.9,py+s*.9);
    for(let k=0;k<4;k++){const wx=px+s*.9-(k+1)*s*.45; x.quadraticCurveTo(wx+s*.22,py+s*(k%2?.7:1.15)+Math.sin(t*5+k)*s*.06,wx,py+s*.9);} x.fill(); outline();
    _eye(x,px-s*.32,py-s*.2,s*.24,t,mood,lx,ly,6); _eye(x,px+s*.32,py-s*.2,s*.24,t,mood,lx,ly,6.3); _brows(x,px-s*.32,px+s*.32,py-s*.2,s*.24,mood,0); _mouth(x,px,py+s*.25,s*.32,mood);
    const sx=px+s*.78, sy=py+s*.35; x.fillStyle='#E6E9EF'; x.beginPath(); x.moveTo(sx,sy-s*.4); x.lineTo(sx+s*.32,sy-s*.26); x.lineTo(sx+s*.26,sy+s*.18); x.lineTo(sx,sy+s*.38); x.lineTo(sx-s*.26,sy+s*.18); x.lineTo(sx-s*.32,sy-s*.26); x.closePath(); x.fill();
    x.fillStyle=_shade(color,.7); x.font='800 '+(s*.3).toFixed(0)+'px monospace'; x.textAlign='center'; x.textBaseline='middle'; x.fillText('✓',sx,sy);
  }
  if(mood==='block'){x.strokeStyle='#FF4D6D'; x.lineWidth=Math.max(1.5,s*.09); x.setLineDash([s*.25,s*.15]); x.beginPath(); x.arc(px,py,s*1.42,t*1.5,t*1.5+6.283); x.stroke(); x.setLineDash([]);}
  if(mood==='idle'){x.fillStyle=_rgba('#E6E9EF',.6); x.font=(s*.42).toFixed(0)+'px monospace'; x.textAlign='center'; x.fillText('z',px+s*.95,py-s*.95-((t*8)%10)); x.fillText('z',px+s*1.25,py-s*1.25-((t*8+5)%10));}
  x.restore(); x.textBaseline='alphabetic';
}
"""
