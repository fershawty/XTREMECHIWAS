"""
engine/fgview/timer.py
======================
ENTRY TIMER (Módulo 10): cuándo SÍ y cuándo NO entrar.

* Espera el cierre de la vela de 5 min que confirma la señal (nunca a mitad de vela).
  Ventana de entrada: los primeros 45 s después del cierre.
* Horarios preferidos (hora de Nueva York): apertura de Londres 02:00–04:00 y de
  Nueva York 08:30–11:00. Fuera de ellos solo se avisa.
* Bloquea los 5 minutos previos al cambio de funding (00:00, 08:00 y 16:00 UTC).
* Si el precio se alejó más de 0.3 × ATR de la entrada ideal, la señal se cancela.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SESSIONS = (("Apertura de Londres", time(2, 0), time(4, 0)), ("Apertura de Nueva York", time(8, 30), time(11, 0)))


@dataclass
class TimerStatus:
    can_enter: bool  # Todo en orden para entrar YA
    cancelled: bool  # El precio se escapó: no perseguir
    seconds_to_close: int  # Hasta el próximo cierre de vela de 5 min
    in_entry_window: bool
    session: str | None
    minutes_to_funding: float
    messages: list[str] = field(default_factory=list)


class EntryTimer:
    def __init__(self, bar_minutes: int = 5, entry_window_s: int = 45, drift_atr: float = 0.3,
                 funding_hours: tuple[int, ...] = (0, 8, 16), funding_guard_min: int = 5) -> None:
        self.bar_minutes, self.entry_window_s, self.drift_atr = bar_minutes, entry_window_s, drift_atr
        self.funding_hours, self.funding_guard_min = funding_hours, funding_guard_min

    def next_funding(self, now: datetime) -> datetime:
        now = now.astimezone(timezone.utc)
        for day in (0, 1):
            for h in self.funding_hours:
                t = (now + timedelta(days=day)).replace(hour=h, minute=0, second=0, microsecond=0)
                if t > now:
                    return t
        return now + timedelta(hours=8)  # pragma: no cover

    @staticmethod
    def session(now: datetime) -> str | None:
        t = now.astimezone(NY).time()
        for name, start, end in SESSIONS:
            if start <= t < end:
                return name
        return None

    def check(self, now: datetime | None = None, ideal_entry: float | None = None, price: float | None = None,
              atr: float | None = None) -> TimerStatus:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        period = self.bar_minutes * 60
        since_close = int(now.timestamp()) % period
        to_close = period - since_close
        in_window = since_close <= self.entry_window_s
        funding_min = (self.next_funding(now) - now).total_seconds() / 60
        sess = self.session(now)
        msgs: list[str] = []
        ok = True
        if in_window:
            msgs.append(f"✅ La vela de {self.bar_minutes} min acaba de cerrar: estás en la ventana de entrada "
                        f"({self.entry_window_s - since_close} s restantes).")
        else:
            ok = False
            msgs.append(f"⏳ Espera el cierre de la vela de {self.bar_minutes} min en {to_close // 60}:{to_close % 60:02d}. "
                        "No entres a mitad de vela.")
        if funding_min <= self.funding_guard_min:
            ok = False
            msgs.append(f"⛔ Cambio de funding en {funding_min:.0f} min: no se entra en los {self.funding_guard_min} min previos.")
        if sess:
            msgs.append(f"🕐 Horario preferido: {sess} (hora de NY {now.astimezone(NY):%H:%M}).")
        else:
            msgs.append(f"🕐 Fuera de los horarios preferidos (hora de NY {now.astimezone(NY):%H:%M}): "
                        "Londres 02:00–04:00 y Nueva York 08:30–11:00. Menos volumen = más riesgo.")
        cancelled = False
        if ideal_entry and price and atr and atr > 0:
            drift = abs(price - ideal_entry)
            if drift > self.drift_atr * atr:
                cancelled, ok = True, False
                msgs.append(f"❌ Señal cancelada: el precio se alejó {drift / atr:.2f} × ATR de la entrada ideal "
                            f"(máximo {self.drift_atr:g}). No persigas el precio.")
            else:
                msgs.append(f"🎯 Precio a {drift / atr:.2f} × ATR de la entrada ideal (máximo {self.drift_atr:g}).")
        return TimerStatus(ok, cancelled, to_close, in_window, sess, funding_min, msgs)
