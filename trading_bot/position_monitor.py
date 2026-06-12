"""
Tracks open signal positions and fires Discord alerts + trims
when TP1, TP2, or SL is hit.

Positions are persisted in positions.json so they survive restarts.
"""
import json
import logging
import os
import shutil
import tempfile
import time
from datetime import datetime

import pytz

from config import Config
from data_fetcher import get_latest_quote
from risk_manager import trim_shares
from trade_executor import (
    submit_market_order, close_position, submit_stop_order,
    cancel_order, get_open_positions,
)

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

POSITIONS_FILE = os.path.join(os.path.dirname(__file__), "positions.json")


def load_positions() -> dict:
    """
    Load positions from JSON, guarding against empty or corrupt files.

    The file can become empty/truncated if the process is killed mid-write.
    Calling json.load() on an empty file raises:
        JSONDecodeError: Expecting value: line 1 column 1 (char 0)
    This helper reads the raw text first, checks it, and backs up the file
    before returning {} if anything is wrong.
    """
    if not os.path.exists(POSITIONS_FILE):
        return {}
    try:
        with open(POSITIONS_FILE) as f:
            content = f.read().strip()
        if not content:
            logger.warning("[load_positions] positions.json is empty — returning {}")
            return {}
        return json.loads(content)
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        # OSError covers PermissionError (OneDrive lock), JSONDecodeError covers
        # partial writes from a concurrent process.
        backup = POSITIONS_FILE + ".corrupt"
        try:
            shutil.copy2(POSITIONS_FILE, backup)
            logger.error(
                f"[load_positions] Bad positions.json — backed up to {os.path.basename(backup)}. "
                f"Error: {exc}"
            )
        except Exception:
            logger.error(f"[load_positions] Bad positions.json and backup failed: {exc}")
        return {}


def save_positions(positions: dict):
    """
    Write positions atomically to avoid partial-write corruption.

    Writes to a temp file in the same directory, then renames it over
    the target.  os.replace() is atomic on POSIX and Windows (same drive),
    so a crash mid-write never leaves positions.json in a bad state.
    """
    dir_name = os.path.dirname(POSITIONS_FILE) or "."
    try:
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp", prefix="positions_")
        try:
            with os.fdopen(fd, "w") as tmp:
                json.dump(positions, tmp, indent=2)
            os.replace(tmp_path, POSITIONS_FILE)  # atomic rename
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as exc:
        logger.error(f"[save_positions] Atomic write failed ({exc}), falling back to direct write")
        for attempt in range(3):
            try:
                with open(POSITIONS_FILE, "w") as f:
                    json.dump(positions, f, indent=2)
                break
            except PermissionError:
                if attempt < 2:
                    time.sleep(0.3)   # wait for OneDrive/other process to release
                else:
                    logger.error("[save_positions] All fallback attempts failed — positions not saved")
                    raise


def add_position(signal: dict, shares: int, dollar_risk: float,
                 stop_order_id: str | None = None):
    """Record a new position after entry."""
    positions = load_positions()
    positions[signal["symbol"]] = {
        "symbol":       signal["symbol"],
        "strategy":     signal["strategy"],
        "signal_type":  signal["signal_type"],
        "entry":        signal["entry"],
        "stop":         signal["stop"],
        "tp1":          signal["tp1"],
        "tp2":          signal["tp2"],
        "shares_total": shares,
        "shares_remaining": shares,
        "dollar_risk":  dollar_risk,
        "tp1_hit":      False,
        "tp2_hit":      False,
        "opened_at":    datetime.now(ET).isoformat(),
        "breakeven_set": False,
        "stop_order_id": stop_order_id,
    }
    save_positions(positions)
    logger.info(f"[position_add] {signal['symbol']} {shares} shares @ ${signal['entry']}")


def _stop_side(pos: dict) -> str:
    """Side of the protective stop order (opposite of position direction)."""
    return "buy" if pos["signal_type"] == "short" else "sell"


def _place_protective_stop(pos: dict) -> str | None:
    """
    Place a broker-side GTC stop so the position is protected even if the
    bot goes offline. Returns the order id or None.
    """
    order = submit_stop_order(pos["symbol"], pos["shares_remaining"],
                              _stop_side(pos), pos["stop"])
    if order and order.get("id"):
        logger.info(f"[protective_stop] {pos['symbol']} {pos['shares_remaining']} sh "
                    f"@ ${pos['stop']} (id {order['id'][:8]}…)")
        return order["id"]
    return None


def ensure_protective_stops():
    """
    Make sure every tracked position has a live broker-side stop order.
    Self-heals positions entered premarket (when a stop can't be placed
    because the entry order hasn't filled yet) and any prior failures.
    """
    positions = load_positions()
    broker_syms = {p["symbol"] for p in get_open_positions()}
    changed = False
    for sym, pos in positions.items():
        if pos.get("stop_order_id"):
            continue
        if sym not in broker_syms:
            continue   # entry not filled yet — try again next cycle
        oid = _place_protective_stop(pos)
        if oid:
            pos["stop_order_id"] = oid
            changed = True
    if changed:
        save_positions(positions)


def reconcile_positions(notifier=None) -> list[str]:
    """
    Remove tracked positions that no longer exist at the broker
    (stop order filled while bot was offline, or manual close).
    Skips positions <45 min old whose entry order may simply not be
    filled yet (premarket entries fill at the open).
    """
    broker_syms = {p["symbol"] for p in get_open_positions()}
    positions = load_positions()
    removed = []
    now = datetime.now(ET)
    for sym, pos in list(positions.items()):
        if sym in broker_syms:
            continue
        try:
            opened = datetime.fromisoformat(pos["opened_at"])
            age_min = (now - opened).total_seconds() / 60
        except (KeyError, ValueError):
            age_min = 999
        if age_min < 45:
            continue
        cancel_order(pos.get("stop_order_id"))   # clean up dangling stop
        positions.pop(sym)
        removed.append(sym)
        logger.warning(f"[reconcile] {sym} closed at broker (stop filled or "
                       f"manual close) — removed from tracking")
    if removed:
        save_positions(positions)
    return removed


def remove_position(symbol: str):
    positions = load_positions()
    positions.pop(symbol, None)
    save_positions(positions)


def check_all_positions(notifier) -> list[dict]:
    """
    Called every 5 minutes during market hours.
    Returns list of alert events for this cycle.
    """
    ensure_protective_stops()
    positions = load_positions()
    events = []

    for symbol, pos in list(positions.items()):
        price = get_latest_quote(symbol)
        if price is None:
            continue

        is_short = pos["signal_type"] == "short"
        entry    = pos["entry"]
        stop     = pos["stop"]
        tp1      = pos["tp1"]
        tp2      = pos["tp2"]
        shares   = pos["shares_remaining"]
        pct_chg  = ((price - entry) / entry * 100) * (-1 if is_short else 1)

        # ── STOP LOSS HIT ──────────────────────────────────
        sl_hit = (price <= stop) if not is_short else (price >= stop)
        if sl_hit:
            # Cancel broker stop first so the close isn't blocked by
            # shares being held for the open stop order (and to avoid
            # a double-sell if the stop fires simultaneously).
            cancel_order(pos.get("stop_order_id"))
            close_position(symbol)
            # Pop from the local dict too — otherwise the final
            # save_positions(positions) below resurrects the closed position
            # and the stop re-fires every cycle.
            positions.pop(symbol, None)
            remove_position(symbol)
            event = {
                "type": "stop_loss", "symbol": symbol, "price": price,
                "entry": entry, "stop": stop, "pct_chg": pct_chg,
                "strategy": pos["strategy"], "shares": shares,
                "dollar_loss": round(abs(price - entry) * shares, 2),
            }
            notifier.send_stop_loss(event)
            events.append(event)
            continue

        # ── TP1 HIT ────────────────────────────────────────
        tp1_hit_now = (price >= tp1) if not is_short else (price <= tp1)
        if tp1_hit_now and not pos["tp1_hit"]:
            # Free the held shares before trimming, then re-protect
            cancel_order(pos.get("stop_order_id"))
            trim = trim_shares(pos["shares_total"], Config.TRIM_TP1)
            side = "sell" if not is_short else "buy"
            submit_market_order(symbol, trim, side)
            pos["tp1_hit"]       = True
            pos["shares_remaining"] = shares - trim
            # Move stop to breakeven
            pos["stop"]          = entry
            pos["breakeven_set"] = True
            pos["stop_order_id"] = _place_protective_stop(pos)
            event = {
                "type": "tp1", "symbol": symbol, "price": price,
                "entry": entry, "tp1": tp1, "tp2": tp2, "pct_chg": pct_chg,
                "strategy": pos["strategy"], "shares_trimmed": trim,
                "shares_remaining": pos["shares_remaining"],
            }
            notifier.send_tp_hit(event)
            events.append(event)

        # ── TP2 HIT ────────────────────────────────────────
        tp2_hit_now = (price >= tp2) if not is_short else (price <= tp2)
        if tp2_hit_now and pos["tp1_hit"] and not pos["tp2_hit"]:
            cancel_order(pos.get("stop_order_id"))
            trim = trim_shares(pos["shares_total"], Config.TRIM_TP2)
            trim = min(trim, pos["shares_remaining"])
            side = "sell" if not is_short else "buy"
            submit_market_order(symbol, trim, side)
            pos["tp2_hit"]       = True
            pos["shares_remaining"] = pos["shares_remaining"] - trim
            pos["stop_order_id"] = (_place_protective_stop(pos)
                                    if pos["shares_remaining"] > 0 else None)
            event = {
                "type": "tp2", "symbol": symbol, "price": price,
                "entry": entry, "tp2": tp2, "pct_chg": pct_chg,
                "strategy": pos["strategy"], "shares_trimmed": trim,
                "shares_remaining": pos["shares_remaining"],
            }
            notifier.send_tp_hit(event)
            events.append(event)

        positions[symbol] = pos

    save_positions(positions)
    return events


def get_open_summary() -> list[dict]:
    """Return current open positions with unrealized P&L."""
    positions = load_positions()
    summary = []
    for symbol, pos in positions.items():
        price = get_latest_quote(symbol)
        if price is None:
            continue
        is_short = pos["signal_type"] == "short"
        pct = ((price - pos["entry"]) / pos["entry"] * 100) * (-1 if is_short else 1)
        summary.append({
            "symbol":   symbol,
            "strategy": pos["strategy"],
            "entry":    pos["entry"],
            "current":  price,
            "pct_chg":  round(pct, 2),
            "shares":   pos["shares_remaining"],
            "tp1_hit":  pos["tp1_hit"],
            "tp2_hit":  pos["tp2_hit"],
        })
    return summary
