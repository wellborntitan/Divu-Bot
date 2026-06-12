"""
Fetches OHLCV bars from Alpaca Data API.
"""
import logging
from datetime import datetime, timedelta

import pandas as pd
import pytz
import requests

from config import Config

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")


def _alpaca_headers() -> dict:
    return {
        "APCA-API-KEY-ID":     Config.ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": Config.ALPACA_SECRET_KEY,
    }


def _safe_json(resp: requests.Response, context: str) -> dict | list | None:
    """
    Parse a JSON response safely, guarding against empty bodies.

    Alpaca occasionally returns 204 No Content or an empty body on
    rate-limits, market-closed windows, or transient errors.  Calling
    resp.json() on an empty body raises:
        JSONDecodeError: Expecting value: line 1 column 1 (char 0)
    This helper checks the HTTP status and body before parsing so callers
    get None instead of an unhandled exception.
    """
    if not resp.ok:
        logger.warning(f"[{context}] HTTP {resp.status_code}")
        return None
    if not resp.text.strip():
        logger.warning(f"[{context}] Empty response body (HTTP {resp.status_code})")
        return None
    try:
        return resp.json()
    except ValueError as exc:
        # Catches json.JSONDecodeError (subclass of ValueError)
        logger.warning(f"[{context}] JSON parse error: {exc} — body: {resp.text[:120]!r}")
        return None


# Preferred historical feed. SIP = full consolidated tape (all exchanges),
# free on Alpaca for data >15 min old. IEX sees only ~2-3% of market volume,
# which badly distorts all volume-based signals. We try SIP first and fall
# back to IEX permanently for the run if SIP is rejected.
_HIST_FEED = "sip"


def get_bars(symbol: str, days: int = 90, timeframe: str = "1Day") -> pd.DataFrame | None:
    """
    Fetch historical OHLCV bars from Alpaca (SIP feed, IEX fallback).
    Returns DataFrame with columns: open, high, low, close, volume
    Indexed by datetime.
    """
    global _HIST_FEED
    # Free-tier SIP requires data >15 min old — cap the end timestamp.
    end_dt = datetime.now(pytz.UTC) - timedelta(minutes=16)
    start  = (end_dt - timedelta(days=days + 10)).strftime("%Y-%m-%d")

    url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"

    feeds = [_HIST_FEED] if _HIST_FEED == "iex" else ["sip", "iex"]
    for feed in feeds:
        params = {
            "timeframe": timeframe,
            "start":     start,
            "end":       end_dt.isoformat(),
            "limit":     1000,
            "feed":      feed,
        }
        try:
            resp = requests.get(url, headers=_alpaca_headers(), params=params, timeout=10)
            if not resp.ok and feed == "sip":
                logger.warning(f"[get_bars/{symbol}] SIP rejected (HTTP {resp.status_code}) — "
                               f"falling back to IEX for this run")
                _HIST_FEED = "iex"
                continue
            payload = _safe_json(resp, f"get_bars/{symbol}/{feed}")
            if payload is None:
                return None
            data = payload.get("bars", [])
            if not data:
                return None

            df = pd.DataFrame(data)
            df["t"] = pd.to_datetime(df["t"])
            df = df.rename(columns={"t": "date", "o": "open", "h": "high",
                                      "l": "low",  "c": "close", "v": "volume"})
            df = df.set_index("date").sort_index()
            df = df[["open", "high", "low", "close", "volume"]]
            df = df.astype(float)
            df["volume"] = df["volume"].astype(int)
            return df.tail(days)
        except Exception as e:
            logger.warning(f"[data_fetcher] {symbol} ({feed}): {e}")
            if feed != "sip":
                return None
    return None


def get_intraday_bars(symbol: str, timeframe: str = "5Min",
                       limit: int = 80) -> pd.DataFrame | None:
    """
    Fetch today's intraday bars (5m or 15m).
    Used for the 10AM volume anomaly check.
    """
    today = datetime.now(ET).strftime("%Y-%m-%d")
    url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
    params = {
        "timeframe": timeframe,
        "start":     today,
        "limit":     limit,
        "feed":      "iex",
    }
    try:
        resp = requests.get(url, headers=_alpaca_headers(), params=params, timeout=10)
        payload = _safe_json(resp, f"get_intraday_bars/{symbol}")
        if payload is None:
            return None
        data = payload.get("bars", [])
        if not data:
            return None

        df = pd.DataFrame(data)
        df["t"] = pd.to_datetime(df["t"]).dt.tz_convert(ET)
        df = df.rename(columns={"t": "date", "o": "open", "h": "high",
                                  "l": "low",  "c": "close", "v": "volume"})
        df = df.set_index("date").sort_index()
        df = df[["open", "high", "low", "close", "volume"]]
        return df.astype(float)
    except Exception as e:
        logger.warning(f"[intraday] {symbol}: {e}")
        return None


def get_latest_quote(symbol: str) -> float | None:
    """Get the latest trade price for a symbol."""
    url = f"https://data.alpaca.markets/v2/stocks/{symbol}/trades/latest"
    try:
        resp = requests.get(url, headers=_alpaca_headers(),
                             params={"feed": "iex"}, timeout=5)
        payload = _safe_json(resp, f"get_latest_quote/{symbol}")
        if payload is None:
            return None
        trade = payload.get("trade")
        if not trade or "p" not in trade:
            logger.warning(f"[get_latest_quote/{symbol}] Unexpected payload shape: {str(payload)[:120]}")
            return None
        return float(trade["p"])
    except Exception as e:
        logger.warning(f"[quote] {symbol}: {e}")
        return None


def get_top_movers(limit: int = 50) -> list[str]:
    """
    Get top active stocks by volume from Alpaca screener.
    Fallback: returns empty list if API not available.
    """
    url = "https://data.alpaca.markets/v1beta1/screener/stocks/most-actives"
    params = {"by": "volume", "top": limit}
    try:
        resp = requests.get(url, headers=_alpaca_headers(), params=params, timeout=10)
        payload = _safe_json(resp, "get_top_movers")
        if payload is None:
            return []
        return [s["symbol"] for s in payload.get("most_actives", [])]
    except Exception as e:
        logger.warning(f"[top_movers] {e}")
        return []


def get_all_market_symbols() -> list[str]:
    """
    Full market scan — returns all US equity symbols that pass
    basic price/volume/ADR filters using Alpaca's bulk snapshot endpoint.

    Flow:
      1. Fetch all active US equity assets (~8,000 symbols)
      2. Batch-fetch snapshots (price + volume) in groups of 100
      3. Keep only symbols meeting MIN_PRICE and MIN_AVG_VOLUME thresholds
      4. Return the survivors for full pattern detection

    Typical result: 400–700 symbols from ~8,000.
    """
    logger.info("[market_scan] Fetching all US equity assets...")

    # Step 1: get all active US equity assets
    url = f"{Config.ALPACA_BASE_URL}/v2/assets"
    params = {"status": "active", "asset_class": "us_equity", "tradable": True}
    try:
        resp = requests.get(url, headers=_alpaca_headers(), params=params, timeout=30)
        assets = _safe_json(resp, "get_all_market_symbols/assets")
        if assets is None:
            return []
    except Exception as e:
        logger.error(f"[market_scan] Failed to fetch assets: {e}")
        return []

    # Keep symbols that look like real stocks (1-5 chars, no dots/slashes for warrants/rights)
    symbols = [
        a["symbol"] for a in assets
        if a.get("tradable") and a.get("fractionable") is not None
        and 1 <= len(a["symbol"]) <= 5
        and a["symbol"].isalpha()
    ]
    logger.info(f"[market_scan] {len(symbols)} tradeable symbols found")

    # Step 2: batch snapshot pre-filter (100 symbols per request)
    BATCH = 100
    qualified: list[str] = []
    snap_url = "https://data.alpaca.markets/v2/stocks/snapshots"

    for i in range(0, len(symbols), BATCH):
        batch = symbols[i: i + BATCH]
        try:
            resp = requests.get(
                snap_url,
                headers=_alpaca_headers(),
                params={"symbols": ",".join(batch), "feed": "iex"},
                timeout=15,
            )
            snaps = _safe_json(resp, f"get_all_market_symbols/snapshots[{i}]")
            if snaps is None:
                continue

            for sym, snap in snaps.items():
                try:
                    day  = snap.get("dailyBar") or snap.get("prevDailyBar") or {}
                    price  = float(snap.get("latestTrade", {}).get("p", 0) or
                                   day.get("c", 0))
                    volume = float(day.get("v", 0))
                    # ADR proxy: (high - low) / close
                    h = float(day.get("h", 0))
                    lo = float(day.get("l", 0))
                    c = float(day.get("c", 1))
                    adr = (h - lo) / c * 100 if c else 0

                    if (price  >= Config.MIN_PRICE and
                            volume >= Config.MIN_AVG_VOLUME and
                            adr    >= Config.MIN_ADR_PCT):
                        qualified.append(sym)
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"[market_scan] snapshot batch {i}-{i+BATCH}: {e}")
            continue

    logger.info(f"[market_scan] {len(qualified)} symbols passed pre-filter "
                f"(price≥${Config.MIN_PRICE}, vol≥{Config.MIN_AVG_VOLUME:,}, "
                f"ADR≥{Config.MIN_ADR_PCT}%)")
    return qualified


def get_account_value() -> float | None:
    """
    Fetch current paper account equity.
    Returns None on failure — callers must SKIP trading rather than size
    positions off a guessed account value.
    """
    url = f"{Config.ALPACA_BASE_URL}/v2/account"
    try:
        resp = requests.get(url, headers=_alpaca_headers(), timeout=5)
        payload = _safe_json(resp, "get_account_value")
        if payload is None:
            return None
        return float(payload["equity"])
    except Exception as e:
        logger.error(f"[account] {e}")
        return None


def is_trading_day() -> bool:
    """
    True if the US market is open today (uses Alpaca's calendar).
    Fails OPEN (returns True) if the API is unreachable, so a calendar
    outage never silently disables the bot on a real trading day.
    """
    today = datetime.now(ET).strftime("%Y-%m-%d")
    url = f"{Config.ALPACA_BASE_URL}/v2/calendar"
    try:
        resp = requests.get(url, headers=_alpaca_headers(),
                            params={"start": today, "end": today}, timeout=10)
        days = _safe_json(resp, "is_trading_day")
        if days is None:
            return True
        return any(d.get("date") == today for d in days)
    except Exception as e:
        logger.warning(f"[calendar] {e} — assuming trading day")
        return True
