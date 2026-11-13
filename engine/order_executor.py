"""
engine/order_executor.py
========================
Ejecución de órdenes: DESACTIVADA A PROPÓSITO.

FUTURES-SCOPE es un sistema de ANÁLISIS: genera señales y alertas, pero NO opera.
`execute_order()` existe para que en el futuro puedas conectarlo a un exchange,
pero su cuerpo real está comentado. Hoy solo registra el intento y devuelve
{"status": "disabled"}.

Antes de activarlo (bajo tu responsabilidad):
1. Prueba primero en TESTNET (Binance Futures testnet / Bybit testnet).
2. Crea API keys SOLO con permiso de trading de futuros, SIN retiros, con lista blanca de IP.
3. Pon las keys en .env (BINANCE_API_KEY / BINANCE_API_SECRET).
4. Descomenta el bloque de abajo y cambia ENABLE_LIVE_TRADING a True.
5. Empieza con el tamaño mínimo.
"""

from __future__ import annotations

from engine.config import get_logger

log = get_logger("order_executor")

ENABLE_LIVE_TRADING = False  # <- NO lo cambies sin haber leído el docstring


def execute_order(
    symbol: str,
    side: str,
    amount: float,
    entry: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    leverage: int = 5,
    exchange_id: str = "binance",
    testnet: bool = True,
) -> dict:
    """
    Envía una orden de futuros con SL y TP (DESACTIVADA).

    Args:
        symbol: símbolo CCXT del perpetuo, p. ej. "BTC/USDT:USDT".
        side: "buy" (LONG) o "sell" (SHORT).
        amount: cantidad en unidades del activo base.
        entry: precio límite; None = orden a mercado.
        stop_loss / take_profit: precios de protección.
        leverage: apalancamiento a configurar.
        exchange_id: "binance" | "bybit" | "okx".
        testnet: usar la red de pruebas del exchange.

    Returns:
        dict con el estado. Hoy siempre {"status": "disabled", ...}.
    """
    order = {"symbol": symbol, "side": side, "amount": amount, "entry": entry, "stop_loss": stop_loss,
             "take_profit": take_profit, "leverage": leverage, "exchange": exchange_id, "testnet": testnet}
    if not ENABLE_LIVE_TRADING:
        log.warning("execute_order() llamada pero la ejecución está DESACTIVADA: %s", order)
        return {"status": "disabled", "message": "Ejecución de órdenes desactivada (solo análisis).", "order": order}

    # ------------------------------------------------------------------------
    # CÓDIGO DE REFERENCIA (descomentar bajo tu responsabilidad)
    # ------------------------------------------------------------------------
    # import ccxt
    # from engine.config import SETTINGS
    #
    # keys = SETTINGS.api_keys.get(exchange_id, {})
    # exchange = getattr(ccxt, exchange_id)({
    #     "apiKey": keys.get("apiKey"),
    #     "secret": keys.get("secret"),
    #     "password": keys.get("password") or None,      # solo OKX
    #     "enableRateLimit": True,
    #     "options": {"defaultType": "swap"},
    # })
    # if testnet:
    #     exchange.set_sandbox_mode(True)
    #
    # exchange.load_markets()
    # exchange.set_leverage(leverage, symbol)
    # amount = float(exchange.amount_to_precision(symbol, amount))
    # order_type = "limit" if entry else "market"
    # params = {}
    # if stop_loss:
    #     params["stopLoss"] = {"triggerPrice": exchange.price_to_precision(symbol, stop_loss)}
    # if take_profit:
    #     params["takeProfit"] = {"triggerPrice": exchange.price_to_precision(symbol, take_profit)}
    # result = exchange.create_order(symbol, order_type, side, amount, entry, params)
    # log.info("Orden enviada: %s", result.get("id"))
    # return {"status": "sent", "order": result}
    return {"status": "disabled", "order": order}
