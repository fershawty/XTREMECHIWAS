"""
engine/alerts.py
================
Alertas para señales de ALTA PROBABILIDAD (score >= 85).

Canales:
* Log (siempre) y notificación visual en la UI (toast).
* Telegram (opcional): TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID en .env
  1. Habla con @BotFather en Telegram -> /newbot -> copia el token.
  2. Escribe a tu bot y abre https://api.telegram.org/bot<TOKEN>/getUpdates para ver tu chat_id.
* Webhook genérico (opcional): ALERT_WEBHOOK_URL (Discord / Slack / Make / n8n).
"""

from __future__ import annotations

import requests

from engine.config import SETTINGS, get_logger

log = get_logger("alerts")


class AlertManager:
    """Envía alertas por los canales configurados. Nunca lanza excepciones."""

    def __init__(
        self,
        telegram_token: str = SETTINGS.telegram_bot_token,
        telegram_chat_id: str = SETTINGS.telegram_chat_id,
        webhook_url: str = SETTINGS.alert_webhook_url,
    ) -> None:
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self.webhook_url = webhook_url

    @property
    def channels(self) -> list[str]:
        """Canales externos activos."""
        out = []
        if self.telegram_token and self.telegram_chat_id:
            out.append("telegram")
        if self.webhook_url:
            out.append("webhook")
        return out

    @staticmethod
    def format_message(symbol: str, timeframe: str, signal, plan) -> str:
        """Texto de la alerta (sirve para Telegram, Discord y logs)."""
        arrow = "🟢 LONG" if signal.direction == "LONG" else "🔴 SHORT"
        lines = [
            f"🚨 FUTURES-SCOPE · {arrow} {symbol} ({timeframe})",
            f"Score: {signal.score:.0f}/100 · {signal.grade}",
            f"Estrategia: {signal.strategy_name}",
            f"Razón: {signal.reason_text}",
        ]
        if plan is not None:
            lines += [
                f"Entrada: {plan.entry}",
                f"SL: {plan.stop_loss} ({plan.sl_method})",
                f"TP1 (1:{plan.rr_tp1:g}): {plan.tp1}",
                f"TP2 (1:{plan.rr_tp2:g}): {plan.tp2}",
                f"Tamaño: {plan.position_size} {plan.size_unit} · Riesgo: {plan.risk_amount}",
            ]
        if signal.context_note:
            lines.append(signal.context_note)
        lines.append("⚠ Señal informativa, no es consejo financiero. Verifica antes de operar.")
        return "\n".join(lines)

    def send(self, symbol: str, timeframe: str, signal, plan) -> bool:
        """Envía la alerta. Devuelve True si al menos un canal externo la recibió."""
        text = self.format_message(symbol, timeframe, signal, plan)
        log.warning("ALERTA\n%s", text)
        delivered = False
        if "telegram" in self.channels:
            try:
                resp = requests.post(
                    f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                    json={"chat_id": self.telegram_chat_id, "text": text},
                    timeout=8,
                )
                delivered |= resp.ok
                if not resp.ok:
                    log.error("Telegram respondió %s", resp.status_code)
            except requests.RequestException as exc:
                log.error("No se pudo enviar a Telegram: %s", exc)
        if "webhook" in self.channels:
            try:
                resp = requests.post(self.webhook_url, json={"content": text, "text": text}, timeout=8)
                delivered |= resp.ok
            except requests.RequestException as exc:
                log.error("No se pudo enviar al webhook: %s", exc)
        return delivered
