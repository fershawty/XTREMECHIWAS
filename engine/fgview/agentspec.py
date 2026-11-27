"""
engine/fgview/agentspec.py
==========================
Registro de agentes de la mesa.

Los 12 agentes de FG DESK siguen en `desk.py` (su cálculo no se mueve). Aquí queda
su ficha —la misma que pide el prompt para diseñar uno nuevo— y el punto por donde
entra un agente adicional con poco código:

    class Eco(DeskAgent):
        spec = AgentSpec(
            name="ECO",
            question="¿El movimiento de BTC acompaña esta cripto?",
            why="Ningún agente mira la correlación con BTC.",
            data="Velas 5m de BTC y de la cripto (FG VIEW). SIN VERIFICAR si se pide otra fuente.",
            calculation="Correlación de retornos 5m en 2 h. A favor de la dirección: +1.",
            vote="+1 si la correlación va con la operación, −1 si va en contra, 0 si no hay datos.",
            blocks_if="Correlación ≤ −0.7 en las últimas 2 h.",
            never="No sube el tamaño, no mueve el stop, no abre órdenes.",
            place="Después de FLOW: el flujo ya se midió y todavía no entra la compuerta.",
            how_to_test="En papel, 30 operaciones: ¿baja el drawdown sin quitar las que la mesa aprueba?",
            character="Un murciélago que escucha el eco de BTC.",
            after="FLOW",
        )

        def evaluate(self, decision, services):
            return AgentResult("ECO", "info", "n/a · sin datos de correlación")

    services.desk.agents.add(Eco())

Un agente nuevo solo puede aprobar, informar o frenar. No hay un método para subir
el riesgo ni para quitar el stop: la ficha lo declara y la mesa no le ofrece esa palanca.
"""

from __future__ import annotations

from dataclasses import dataclass

# Los 12 nombres canónicos. Se repiten aquí para no importar desk al cargar este módulo.
BUILTIN_NAMES = ("SPOTTER", "WHALE", "FLOW", "FUNDING", "GATE", "PRIOR", "PRICER", "EDGE", "KELLY", "GUARD",
                 "TAKER", "CLOSER")


@dataclass(frozen=True)
class AgentSpec:
    """Ficha exacta de un agente de la mesa (AGENTE / PREGUNTA / DATOS / …)."""

    name: str
    question: str
    why: str
    data: str
    calculation: str
    vote: str
    blocks_if: str
    never: str
    place: str
    how_to_test: str
    character: str
    icon: str = "•"
    color: str = "#F2D27A"
    after: str | None = None  # Solo agentes nuevos: se insertan después de este nombre
    hard: bool = False  # Si bloquea, el veredicto pasa a NO OPERAR (como GATE)

    def ficha(self) -> str:
        return "\n".join([
            f"AGENTE: {self.name}",
            f"PREGUNTA: {self.question}",
            f"POR QUÉ HACE FALTA: {self.why}",
            f"DATOS: {self.data}",
            f"CÁLCULO: {self.calculation}",
            f"VOTO: {self.vote}",
            f"BLOQUEA SI: {self.blocks_if}",
            f"NO HACE: {self.never}",
            f"LUGAR EN LA CADENA: {self.place}",
            f"CÓMO PROBARLO: {self.how_to_test}",
            f"PERSONAJE: {self.character}",
        ])


def _spec(name, question, why, data, calculation, vote, blocks_if, never, place, how_to_test, character,
          icon, color) -> AgentSpec:
    return AgentSpec(name, question, why, data, calculation, vote, blocks_if, never, place, how_to_test, character,
                     icon, color)


# Fichas de los 12 agentes que ya calcula la mesa. El cálculo vive en Desk; esto es la ficha.
BUILTIN_SPECS: tuple[AgentSpec, ...] = (
    _spec("SPOTTER", "¿Hay una dirección operable ahora?",
          "Alguien tiene que leer el mercado antes de que el resto vote.",
          "Velas 5m y 1h de FG VIEW, libro de órdenes y VWAP.",
          "6 filtros; se piden ≥ 4 y puntuación ponderada ≥ 55. Libro en contra > 35% o |VWAP z| > 2.5 bloquean.",
          "No vota −1..+1: elige LONG o SHORT y aprueba o bloquea.",
          "Menos de 4 filtros, ponderado < 55, libro fuerte en contra o precio estirado del VWAP.",
          "No elige el tamaño ni el stop.",
          "Primero. Sin dirección el resto no tiene qué votar.",
          "En papel: las operaciones con Spotter en contra deben perder más que las que aprobó.",
          "Explorador de un ojo, verde.", "🔭", "#3DDC97"),
    _spec("WHALE", "¿Las ballenas y los top traders van con la operación?",
          "El precio de corto plazo lo empujan las operaciones grandes.",
          "Binance fapi aggTrades y topLongShortPositionRatio. Sin datos: no vota.",
          "60% neto de ballenas (−1..+1) + 40% top traders. Umbral de ballena: 250k BTC, 150k ETH, 40k el resto.",
          "−1..+1 respecto a la dirección. Sin datos informa y no vota.",
          "Voto ≤ −0.45.",
          "No abre la orden ni mueve el stop.",
          "Después de SPOTTER.",
          "Comparar en papel las entradas con voto ≤ −0.45 (no deberían tomarse) contra las de voto positivo.",
          "Ballena, azul.", "🐋", "#3B82F6"),
    _spec("FLOW", "¿Quién pega a mercado y entra o sale dinero (OI)?",
          "Una vela sin agresor y sin open interest nuevo es un movimiento débil.",
          "Binance fapi takerlongshortRatio y openInterestHist. Si falta el taker, aproximación con velas y se dice.",
          "Voto = clip((taker − 1) × 4) a favor de la dirección; el OI de 1 h confirma o debilita (25%).",
          "−1..+1. Sin datos no vota.",
          "Voto ≤ −0.45.",
          "No decide el tamaño.",
          "Después de WHALE.",
          "Medir si el voto ≤ −0.45 evita pérdidas en papel.",
          "Gota de agua, turquesa.", "🌊", "#2DD4BF"),
    _spec("FUNDING", "¿La gente ya está demasiado de un lado, o el cobro está encima?",
          "Pagar funding caro, y además pronto, es el escenario típico de una barrida.",
          "Binance fapi premiumIndex (tasa y minutos al cobro), o el funding del snapshot.",
          "Funding a favor suma. Pagar ≥ 0.05% bloquea. Pagar ≥ 0.02% con cobro en < 10 min bloquea.",
          "−1..+1 según quién cobra.",
          "Pagas ≥ 0.05%, o pagas ≥ 0.02% y el cobro llega en menos de 10 minutos.",
          "No quita el stop para «aguantar el funding».",
          "Después de FLOW.",
          "Contar cuántas veces un bloqueo de funding evitó una operación que tocaba el stop en la hora siguiente.",
          "Moneda que gira, lima.", "💸", "#A3E635"),
    _spec("GATE", "¿Esto es un squeeze contra la operación?",
          "Abrir en la dirección del squeeze es como se liquidó el sistema antes.",
          "Velas 5m (agotamiento, RSI, Bollinger), long/short de Binance y funding.",
          "Puntos 0–8. Riesgo alto bloquea. El apalancamiento máximo deja la liquidación 1.2× más lejos que el peor movimiento de 30 min.",
          "No vota a favor: o pasa o bloquea. El bloqueo es duro (NO OPERAR).",
          "Riesgo de squeeze ALTO.",
          "Nunca aleja el stop ni sube el apalancamiento por encima del tope.",
          "Después de FUNDING, antes de la probabilidad.",
          "Un bloqueo de GATE no debe terminar en una orden real. En papel se puede medir aparte, marcado aprendizaje.",
          "Señal de alto con barrera, rojo.", "🚧", "#FF4D6D"),
    _spec("PRIOR", "¿A este plan le fue bien en situaciones parecidas?",
          "La probabilidad no puede salir solo de una simulación: hace falta la base histórica.",
          "Velas de FG VIEW, hasta 90 días, casos parecidos al plan.",
          "Porcentaje de veces que TP1 llegó antes que el stop.",
          "Informa el porcentaje. No empuja el tamaño.",
          "≥ 30 casos y la probabilidad base < 40%.",
          "No inventa casos cuando el historial es corto: muestra n.",
          "Después de GATE.",
          "Con n ≥ 30, la tasa real de TP1 no debería quedar muy por debajo de lo que PRIOR dijo.",
          "Búho, naranja.", "📚", "#F5A623"),
    _spec("PRICER", "¿Qué probabilidad hay de tocar TP1?",
          "Sin un número de probabilidad la mesa no puede hablar de ventaja.",
          "Hasta 90 días de velas de 1 minuto (o 5 si no alcanzan), 100,000 caminos.",
          "65% Time Machine + 35% base, corregido por el calibrador.",
          "Informa P(TP1). No promete que vaya a ocurrir.",
          "P(TP1) < 55%.",
          "No sube la probabilidad para que la operación pase.",
          "Después de PRIOR.",
          "El calibrador (Brier) compara lo dicho con lo que pasó, sin mirar el futuro.",
          "Mago con bola de cristal, violeta.", "🔮", "#B388FF"),
    _spec("EDGE", "¿Queda ventaja neta después de comisiones, y caben las guardias?",
          "Una probabilidad alta con ventaja negativa sigue perdiendo dinero.",
          "Resultado del Time Machine y las guardias del Risk Manager.",
          "Ventaja esperada en R, ya con comisiones. Peor 5% aparte.",
          "Informa la ventaja en R.",
          "Ventaja < +0.05R o negativa, o una guardia (stop del día, anti-revancha, posiciones, correlación, liquidación).",
          "No baja el mínimo de ventaja para tener más operaciones.",
          "Después de PRICER.",
          "Las operaciones con ventaja negativa, medidas en papel, deben seguir siendo negativas.",
          "Aleta, rosa.", "⚖️", "#FF5C8A"),
    _spec("KELLY", "¿Cuánto se arriesga, sin pasar la tabla de capital?",
          "El tamaño es la parte que más daño hace cuando uno quiere revancha.",
          "Kelly de la simulación, tabla de capital, Budget Manager y mínimo de orden de Binance.",
          "¼ de Kelly, nunca más que la tabla. Si alguien objetó: máximo 0.5%.",
          "Informa el riesgo en %. No lo sube por encima de la tabla.",
          "No llega al mínimo de Binance ni siquiera al doble del riesgo previsto.",
          "No sube el riesgo por encima de la tabla ni quita el recorte del modo operable.",
          "Después de EDGE, cuando ya se sabe si la mesa objeta.",
          "Ninguna orden de la mesa debe arriesgar más que la tabla de capital del saldo.",
          "Robot cubo, cian.", "📐", "#5AC8FA"),
    _spec("GUARD", "¿El stop queda fuera del ruido y la escalera de TPs está armada?",
          "Un stop dentro del ruido se lo lleva una vela normal.",
          "Plan (SL, TPs) y ATR de 5 minutos.",
          "SL / ruido de 5 min. Tiempo máximo 30 min, o 45 si el SL es amplio (≥ 2.5× el ruido).",
          "Informa. No vota en contra solo por avisar.",
          "No bloquea la cadena: avisa si el SL quedó dentro del ruido (relación < 0.8).",
          "Nunca aleja el stop. Nunca quita el SL.",
          "Después de KELLY, con el plan ya calculado.",
          "Revisar en la bitácora cuántos stops saltan en menos de 5 minutos cuando la relación es < 0.8.",
          "Tortuga, índigo.", "🛡️", "#A5B4FC"),
    _spec("TAKER", "¿La orden está lista, con entrada post-only y stop obligatorio?",
          "Preparar no es lo mismo que enviar. Alguien deja la orden escrita.",
          "El plan aprobado o, en modo operable, el plan con riesgo reducido.",
          "Arma el ticket: post-only, SL obligatorio, TPs reduce-only.",
          "Informa si la orden está lista. No la envía solo.",
          "Sin stop válido no hay ticket.",
          "No envía órdenes reales por su cuenta ni quita el post-only para entrar más rápido.",
          "Después de GUARD. El piloto decide después si se envía.",
          "Toda orden de la mesa, en papel o real, debe traer stop del lado correcto.",
          "Cangrejo dorado.", "🎯", "#F2D27A"),
    _spec("CLOSER", "¿Qué pasa con lo que ya está abierto?",
          "Abrir sin quien cierre (escudo, trailing, tiempo) es dejar la posición sola.",
          "Posiciones de papel y el precio que va llegando.",
          "Escudo anti-squeeze (nunca aleja el SL), trailing tras TP1, cierre por TP, SL o tiempo.",
          "Informa. No abre posiciones nuevas.",
          "No bloquea una entrada: gestiona lo abierto.",
          "Nunca aleja un stop. Nunca cierra en real algo que el usuario no habilitó.",
          "Último. También corre al principio de cada vuelta del piloto.",
          "En papel, ningún stop debe quedar más lejos de lo que estaba al abrir.",
          "Fantasma guardián con escudo, naranja rojizo.", "🧹", "#FF8A3D"),
)


class DeskAgent:
    """Agente nuevo. `spec` es la ficha; `evaluate` devuelve un AgentResult o None si no habla."""

    spec: AgentSpec

    def evaluate(self, decision, services):
        raise NotImplementedError(f"{self.spec.name} no implementa evaluate")


class AgentRegistry:
    """Fichas de la mesa y agentes nuevos que se insertan en la cadena."""

    def __init__(self) -> None:
        self._specs: dict[str, AgentSpec] = {s.name: s for s in BUILTIN_SPECS}
        self._extras: list[DeskAgent] = []

    def specs(self) -> list[AgentSpec]:
        return list(self._specs.values())

    def get(self, name: str) -> AgentSpec | None:
        return self._specs.get((name or "").upper())

    def ficha(self, name: str) -> str:
        spec = self.get(name)
        if spec is None:
            return f"AGENTE: {name or 'n/a'}\nDATOS: n/a"
        return spec.ficha()

    def extras_after(self, anchor: str) -> list[DeskAgent]:
        anchor = (anchor or "").upper()
        return [a for a in self._extras if (a.spec.after or "").upper() == anchor]

    def add(self, agent: DeskAgent) -> AgentSpec:
        """Registra un agente nuevo. No puede tapar a uno de los 12 ni quedarse sin ficha."""
        spec = getattr(agent, "spec", None)
        if not isinstance(spec, AgentSpec):
            raise TypeError("El agente necesita un AgentSpec en `spec`.")
        name = (spec.name or "").strip()
        if not name.isupper() or not name.replace("_", "").isalnum() or " " in name or len(name) > 16:
            raise ValueError("AGENTE debe ser UNA palabra en mayúsculas, sin espacios.")
        if name in BUILTIN_NAMES:
            raise ValueError(f"{name} ya está en la mesa. Un agente nuevo no repite a uno de los 12.")
        missing = [label for label, value in (
            ("PREGUNTA", spec.question), ("POR QUÉ HACE FALTA", spec.why), ("DATOS", spec.data),
            ("CÁLCULO", spec.calculation), ("VOTO", spec.vote), ("BLOQUEA SI", spec.blocks_if),
            ("NO HACE", spec.never), ("LUGAR EN LA CADENA", spec.place), ("CÓMO PROBARLO", spec.how_to_test),
            ("PERSONAJE", spec.character), ("after", spec.after)) if not str(value or "").strip()]
        if missing:
            raise ValueError("Ficha incompleta: " + ", ".join(missing))
        known = set(BUILTIN_NAMES) | {a.spec.name for a in self._extras}
        if spec.after.upper() not in known and spec.after.upper() != name:
            raise ValueError(f"Lugar desconocido: después de {spec.after} no hay ningún agente.")
        if not callable(getattr(agent, "evaluate", None)):
            raise TypeError(f"{name} no tiene evaluate.")
        self._extras = [a for a in self._extras if a.spec.name != name]
        self._specs[name] = spec
        self._extras.append(agent)
        return spec
