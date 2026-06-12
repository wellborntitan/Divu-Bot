"""
Order execution via Alpaca paper trading API.
"""
import logging
import requests
from config import Config

logger = logging.getLogger(__name__)


def _headers() -> dict:
    return {
        "APCA-API-KEY-ID":     Config.ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": Config.ALPACA_SECRET_KEY,
        "Content-Type":        "application/json",
    }


def submit_market_order(symbol: str, shares: int, side: str) -> dict | None:
    """
    Submit a market order.
    side: "buy" or "sell"
    """
    url = f"{Config.ALPACA_BASE_URL}/v2/orders"
    payload = {
        "symbol":        symbol,
        "qty":           str(shares),
        "side":          side,
        "type":          "market",
        "time_in_force": "day",
    }
    try:
        resp = requests.post(url, headers=_headers(), json=payload, timeout=10)
        resp.raise_for_status()
        order = resp.json()
        logger.info(f"[ORDER] {side.upper()} {shares} {symbol} — id: {order.get('id')}")
        return order
    except Exception as e:
        logger.error(f"[order_error] {symbol} {side} {shares}: {e}")
        return None


def submit_limit_order(symbol: str, shares: int, side: str,
                        limit_price: float) -> dict | None:
    url = f"{Config.ALPACA_BASE_URL}/v2/orders"
    payload = {
        "symbol":        symbol,
        "qty":           str(shares),
        "side":          side,
        "type":          "limit",
        "limit_price":   str(round(limit_price, 2)),
        "time_in_force": "day",
    }
    try:
        resp = requests.post(url, headers=_headers(), json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"[limit_order] {symbol}: {e}")
        return None


def submit_stop_order(symbol: str, shares: int, side: str,
                       stop_price: float) -> dict | None:
    url = f"{Config.ALPACA_BASE_URL}/v2/orders"
    payload = {
        "symbol":        symbol,
        "qty":           str(shares),
        "side":          side,
        "type":          "stop",
        "stop_price":    str(round(stop_price, 2)),
        "time_in_force": "gtc",
    }
    try:
        resp = requests.post(url, headers=_headers(), json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"[stop_order] {symbol}: {e}")
        return None


def cancel_order(order_id: str) -> bool:
    """Cancel an open order. Returns True if cancelled (or already gone)."""
    if not order_id:
        return False
    url = f"{Config.ALPACA_BASE_URL}/v2/orders/{order_id}"
    try:
        resp = requests.delete(url, headers=_headers(), timeout=10)
        # 204 = cancelled; 404 = already filled/cancelled; 422 = not cancelable
        return resp.status_code in (200, 204, 404, 422)
    except Exception as e:
        logger.error(f"[cancel_order] {order_id}: {e}")
        return False


def get_order(order_id: str) -> dict | None:
    """Fetch a single order's status."""
    if not order_id:
        return None
    url = f"{Config.ALPACA_BASE_URL}/v2/orders/{order_id}"
    try:
        resp = requests.get(url, headers=_headers(), timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"[get_order] {order_id}: {e}")
        return None


def get_open_positions() -> list[dict]:
    url = f"{Config.ALPACA_BASE_URL}/v2/positions"
    try:
        resp = requests.get(url, headers=_headers(), timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"[positions] {e}")
        return []


def close_position(symbol: str) -> bool:
    url = f"{Config.ALPACA_BASE_URL}/v2/positions/{symbol}"
    try:
        resp = requests.delete(url, headers=_headers(), timeout=10)
        return resp.status_code in (200, 204)
    except Exception as e:
        logger.error(f"[close_position] {symbol}: {e}")
        return False
