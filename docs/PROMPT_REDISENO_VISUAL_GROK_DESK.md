# PROMPT · REDISEÑO VISUAL DE GROK DESK (FG VIEW)

> Para el agente de código (Grok/Cursor). Repo `fershawty/XTREMECHIWAS`, rama base **`futures-scope`**. Trabaja en
> una rama nueva y abre un pull request; no unas nada. Archivos principales: `ui/grok_page.py`,
> `ui/grok_desk_view.py`, `ui/desk_terminal.py`, `ui/desk_hub.py`, `ui/critters.py`.

## OBJETIVO

Que la página **GROK DESK** se vea y se sienta como una **terminal de trading profesional en vivo, de una sola
pantalla**: densa, oscura, con neón, números grandes que cambian, y en el centro una **escena orbital animada**
donde los agentes (personajes) giran alrededor de un núcleo, lanzan rayos y van a "trabajar" a los paneles de datos.
Todo lo que se ve son **datos reales** de FG VIEW (nunca números de relleno); si un dato falta, el panel lo dice.

**Identidad:** es **FG VIEW**, no una copia de otra marca. Usa el logo FG y los **personajes propios** que ya
existen en `ui/critters.py` (explorador, búho, mago, aleta, robot, cangrejo, fantasma, ballena, gota, moneda,
señal, tortuga, cerebro). No uses logos, mascotas ni nombres de otras empresas.

---

## 1. ESTILO GENERAL

- **Fondo:** negro azulado `#05070b` con viñeta radial muy sutil y un campo de estrellas/puntos casi invisible.
- **Paneles:** fondo `#0a0e16`, borde 1px `rgba(148,163,184,.14)`, esquinas 10–12px, título del panel en
  mayúsculas pequeñas con espaciado ancho (`letter-spacing .18em`, 10px, gris `#8C95A8`) precedido de un rombo `◆`,
  y un subtítulo gris más tenue en la misma línea ("— UN LIBRO · HISTORIAL DE PAPEL").
- **Panel activo:** el panel donde está trabajando un agente se ilumina con borde del color de ese agente y
  `box-shadow` neón suave (glow 20–28px).
- **Tipografía:** `JetBrains Mono` (o monoespaciada del sistema) para TODO lo numérico, con cifras tabulares.
  Números héroe grandes (40–56px, peso 800).
- **Paleta neón:**
  - Ganancia / LONG / aprueba: verde `#3DDC97`.
  - Pérdida / SHORT / bloquea: rosa-rojo `#FF4D6D`.
  - Marca / acento: dorado FG `#F2D27A` → `#D4AF37`.
  - Secundarios: morado `#B388FF`, cian `#5AC8FA`, naranja `#FF8A3D`, azul `#3B82F6`, lima `#A3E635`.
  - Texto: `#E6E9EF`; tenue `#8C95A8`; muy tenue `#566074`.
- **Densidad:** grid apretado (gap 8px), cero espacios muertos, cada píxel con información. Debe caber en una
  pantalla de escritorio 1440–1920px de ancho; en pantallas angostas los paneles se apilan sin encimarse.
- **Sin efectos de casino:** nada de confeti, fuego ni sonidos al ganar. La animación va en agentes y datos.

---

## 2. LAYOUT (de arriba a abajo)

### 2.1 Barra superior (fija, ~48px)
- Izquierda: logo FG circular + **"GROK DESK"** (peso 800) y debajo, en gris pequeño, "FG VIEW · futuros USDT-M ·
  Binance".
- Centro: pestañas de vista (ORBITALES · TAPE · LIBRO · PRICER), la activa subrayada en dorado.
- Derecha: 3 mini-KPIs en columna (BANKROLL papel, RIESGO × KELLY, SIMULACIONES), **reloj grande** `HH:MM:SS` con
  "UTC", y una píldora verde **"● MESA ONLINE"** (o gris "SERVIDOR APAGADO").

### 2.2 Ticker de estado (1 línea, ~26px)
- Chip verde "● 12 AGENTES LIVE" + marquesina que corre con: agente que tiene el ticket, traspasos N/12, modelo,
  último veredicto, abiertas, "solo papel".

### 2.3 Live wire (1 línea, ~26px)
- Chip dorado "LIVE WIRE" + marquesina con los últimos eventos de los agentes, cada uno con su color de agente
  ("FLOW · compras/ventas 1.21 · a favor +0.42", "GATE · squeeze 1 pts").

### 2.4 Fila 1: dos paneles grandes (≈ 42% / 58%)

**A. MESA PNL (izquierda)**
- Número héroe del bankroll de papel (verde si va arriba, rojo si abajo) con **conteo animado** cuando cambia
  (sube cifra por cifra en 600ms, no salto).
- Píldora con el cambio del día (+$ y %).
- 4 KPIs en fila: OPERACIONES · WIN RATE · PROFIT FACTOR · MAX DD.
- **Curva de capital** (línea con área degradada) y un **personaje caminando sobre la curva** mientras "piensa".
- Mini-histograma de resultados por operación (barras verdes/rojas).
- Barras horizontales de color: sesgo LONG/SHORT, salidas en TP, ventaja de la mesa, uso del límite del día.

**B. THE TAPE (derecha)**
- Precio grande de la cripto activa + píldora de cambio 1h.
- 4 contadores arriba a la derecha (RSI · VWAP · Z · SEÑALES).
- **Velas de 5 min** verdes/rojas con VWAP y bandas ±1σ/±2σ en nube morada translúcida, marcas de entrada/SL/TP1.
- Volumen abajo (barras verdes/rojas).
- **Libro de órdenes a la derecha** (escalera de precios): bids en barras verdes y asks en barras rosas que
  crecen/encogen con animación, el spread marcado en dorado.
- Personajes trabajando aquí: SPOTTER sobre las velas, WHALE y EDGE subiendo/bajando por el libro, TAKER
  asomado al spread, GUARD en la línea del stop.

### 2.5 Fila 2: ORBITALES (ancho completo, la pieza central, ~520px de alto)

- **Columna izquierda (≈ 18%):**
  - **Radar de rastreo** circular con barrido verde que gira, blips de las criptos del escáner y curvas de energía
    que salen de cada blip hacia el núcleo, con una chispa viajando por cada curva.
  - Debajo, lista de **fuentes de datos** (TAPE, LIBRO, ORÁCULO, FLUJO, VOLATILIDAD, CADENA) como filas con borde
    izquierdo de color y barra de intensidad.
- **Centro (≈ 60%):**
  - Arriba, 6 tarjetas de datos del ticket en fila (SÍMBOLO, ENTRADA, SL, RIESGO, P(TP1), VENTAJA).
  - **Núcleo:** esfera grande blanca/plateada con el **logo FG** (o un personaje propio de FG como mascota del
    núcleo), con halo del color del veredicto (verde OPERAR, ámbar ESPERAR, rojo NO OPERAR) y una ligera
    respiración (escala ±2%).
  - **Anillos orbitales 3D:** 10–14 anillos de energía de colores distintos, inclinados, que rotan a velocidades
    diferentes; la parte de atrás más tenue que la de adelante (mezcla aditiva / `lighter`).
  - **Esfera de puntos** y **constelación de nodos** conectados con pulsos de luz viajando por las conexiones.
  - **Los agentes** (personajes, 26–34px) orbitan en esos anillos. Cada uno con su color. Expresión según su voto:
    sonrisa = aprueba, ✕ y ceño + anillo rojo punteado = bloquea, neutral = informa, dormido "z z" = en espera.
  - **Rayos ramificados** (lightning) del núcleo a los agentes y entre agentes, con glow; más frecuentes cuando hay
    decisión.
  - **El ticket salta** de agente en agente en el orden de la cadena: una etiqueta dorada "TICKET #N · AGENTE A →
    AGENTE B" que viaja con una estela.
  - **Agentes que vuelan a su panel:** por turnos, un agente sale del anillo con estela, vuela en arco hasta su panel
    (MESA PNL, THE TAPE, Pricer, Cordilleras, Traza), camina sobre su gráfica 2–3 s con un **haz de escaneo** y una
    **burbuja de pensamiento** con su tarea y su dato ("FLOW · MIDIENDO EL FLUJO · a favor +0.42"), ilumina ese
    panel y regresa a su órbita.
  - Abajo del núcleo, una tarjeta "PRE-TRADE / MISIÓN" con cripto, dirección y veredicto.
- **Columna derecha (≈ 22%):**
  - **MISSION CONTROL:** tarjeta con borde dorado brillante: SYMBOL + dirección (píldora verde LONG / rosa SHORT),
    P(TP1), ventaja, riesgo, margen y apalancamiento, agente que tiene el ticket.
  - **T-MINUS:** cuenta regresiva grande al cierre de la vela de 5 min (`T-3:56`) con 30 segmentos que se llenan.
  - **TELEMETRÍA:** grid de mini-gauges circulares (uno por agente, con su código y su tiempo en ms) que se llenan en
    su color.
  - **REGISTRO DE VUELO:** últimas líneas del log con hora y "OK".

### 2.6 Pipeline (1 fila, ~70px)
- Los 12 agentes en línea, cada uno con su personaje pequeño, nombre y estado (✓ ms / ✕ STOP / ● INFO).
- Una línea dorada que se va llenando de izquierda a derecha según avanza la cadena, con 3 chispas viajando.
- El agente actual con anillo pulsante.

### 2.7 Fila 3: tres paneles (≈ 34% / 33% / 33%)

**THE TRACE (izquierda)**
- 4 KPIs (PASOS N/12, MS totales, BLOQUEOS, SIMULACIONES) + mini-gráfica de actividad en ms.
- Línea de tiempo vertical: cada paso con un punto del color del agente, la línea que dijo y su valor a la derecha
  ("ponderado 48/100", "ventaja +0.25R"), y debajo el traspaso "SPOTTER→WHALE".

**EL PRICER (centro)**
- Columna izquierda de 4 tarjetas: PRECIO, STOP, VOL (σ), T-MINUS.
- **Tablero de Galton (plinko)** con bolitas doradas cayendo y rebotando en los clavos, acumulándose en barras
  según el **histograma real** de las 100,000 simulaciones (rojo a la izquierda pérdidas, verde a la derecha
  ganancias) y la **campana** encima con μ y σ.
- Barra vertical a la derecha (termómetro rojo→verde) con la probabilidad.
- Abajo, 4 números: P(TP1), P(SL), EV, ¼ KELLY.

**CORDILLERAS DE SEÑALES (derecha)**
- 4 KPIs (ACTIVAS n/16, CALIENTES, CRESTA, valor).
- **Ridgeline plot**: 16 líneas apiladas (RSI, volumen relativo, ADX, ATR, Bollinger, MACD, VWAP z, EMAs, momentum,
  rango, tendencia 1h…) en dorado/naranja/morado, con la cresta resaltada y su etiqueta.
- A la derecha, la lista de señales con barras de intensidad.

### 2.8 Pie (1 línea)
- Chip "FG DESK · ONLINE" y textos: "FG VIEW V2 · GROK DESK · UN LIBRO · 12 AGENTES · SOLO PAPEL POR MCP",
  última operación, neto papel.

---

## 3. LO ESPECÍFICO DE GROK (equipo G-*)

- Un **personaje propio para Grok** (el cerebro con chispas de `critters.py`, lila `#F5D0FE`) orbita el núcleo en el
  arco superior y lanza **rayos** a los agentes que está consultando por MCP; esas tarjetas/esferas se encienden.
- Panel **"EQUIPO GROK"**: las 11 esferas G-* (G-SCOUT, G-MARKET, G-WHALE, G-FLOW, G-FUNDING, G-TRAP, G-ODDS, G-RISK,
  G-JUDGE, G-EXEC, G-WATCH) en un anillo propio; cada esfera se ilumina **verde/rojo/gris según su voto (−2…+2)** y
  muestra el número; **G-JUDGE pulsa** con el color del veredicto (APRUEBA/PRECAUCION/VETA) y la suma.
- **Registro en vivo de Grok:** tabla con hora, cripto, dirección, veredicto, suma, confianza, y si se abrió en
  papel; las operaciones marcadas "GROK" vs "MANUAL".
- KPIs del equipo: bankroll papel, P&L del día, operaciones hoy/6, abiertas/2, estado del COACH (franja ámbar
  "PAUSADO POR TUS REGLAS · …" cuando aplique).

---

## 4. ANIMACIÓN Y RENDIMIENTO

- Canvas 2D a 60 fps con `requestAnimationFrame`; `devicePixelRatio` hasta 2; nada de librerías pesadas.
- Mezcla aditiva para anillos y rayos; `shadowBlur` para glow con moderación (es caro).
- Conteo animado de números, barras del libro con transición, marquesinas a velocidad constante.
- Pausar animaciones fuera de pantalla (`IntersectionObserver`) para no gastar CPU.
- Que no haya errores de JavaScript (ojo: no nombres funciones o constantes `top`, `name`, `status`, `parent`,
  que chocan con `window`).

---

## 5. REGLAS QUE NO SE ROMPEN

- **Datos reales o "n/a"**: nada de números de relleno, ni en DEMO (en DEMO se rotula "DEMO · datos de prueba").
- No tocar `engine/fgview/executor.py`, el modo real, keys, `.env` ni `rules.md`.
- Por MCP y en la página: **solo papel**.
- No romper lo que ya funciona: botón de servidor MCP único, túnel, token enmascarado, Telegram, app de iPhone.
- Ningún `key` de Streamlit repetido.
- Agrega una prueba con `AppTest` que abra GROK DESK y falle con cualquier excepción, y corre toda la suite
  (debe seguir en verde).
- Rama nueva + pull request hacia `futures-scope`. No unas: lo revisa Claude y lo aprueba el usuario.

## 6. ENTREGA

- Capturas de la página en 1440px y 1920px de ancho y un GIF/video corto de la escena orbital.
- Lista de archivos cambiados y qué hace cada cambio.
- Resultado de las pruebas.
