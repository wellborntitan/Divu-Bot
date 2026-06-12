"""
Discord webhook notifications.
Sends rich embeds for: new signals, TP hits, SL hits, EOD summary.
"""
import logging
from datetime import datetime

import pytz
import requests

from config import Config

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

COLORS = {
    "long":    0x00C805,   # Green
    "short":   0xFF3B30,   # Red
    "tp":      0x34C759,   # Light green
    "sl":      0xFF453A,   # Orange-red
    "summary": 0x007AFF,   # Blue
    "scan":    0xAF52DE,   # Purple
}


def _post(payload: dict):
    if not Config.DISCORD_WEBHOOK_URL:
        logger.warning("[discord] No webhook URL configured.")
        return
    try:
        resp = requests.post(Config.DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"[discord] {e}")


def _now_et() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")


def send_new_signal(signal: dict, shares: int, dollar_risk: float):
    """🟢/🔴 New entry signal alert."""
    is_short   = signal["signal_type"] == "short"
    color      = COLORS["short"] if is_short else COLORS["long"]
    direction  = "🔴 SHORT" if is_short else "🟢 LONG"
    entry      = signal["entry"]
    stop       = signal["stop"]
    tp1        = signal["tp1"]
    tp2        = signal["tp2"]
    stop_pct   = round(abs(entry - stop) / entry * 100, 2)
    tp1_pct    = round(abs(entry - tp1) / entry * 100, 2)
    tp2_pct    = round(abs(entry - tp2) / entry * 100, 2)
    mode_tag   = "📄 PAPER" if Config.PAPER_TRADING else "💰 LIVE"

    embed = {
        "title":       f"{direction} SIGNAL — ${signal['symbol']} {mode_tag}",
        "description": f"**{signal['strategy']}**",
        "color":       color,
        "fields": [
            {"name": "📍 Entry",       "value": f"`${entry}`",               "inline": True},
            {"name": "🛑 Stop Loss",   "value": f"`${stop}` (–{stop_pct}%)", "inline": True},
            {"name": "​",         "value": "​",                    "inline": True},
            {"name": "🎯 Target 1",    "value": f"`${tp1}` (+{tp1_pct}%) — Trim 25%",  "inline": True},
            {"name": "🎯 Target 2",    "value": f"`${tp2}` (+{tp2_pct}%) — Trim 50%",  "inline": True},
            {"name": "🔁 Target 3",    "value": "Trail 8 EMA — Hold remainder", "inline": True},
            {"name": "📦 Position",    "value": f"{shares} shares | Risk: ${dollar_risk}", "inline": True},
            {"name": "📈 Vol Ratio",   "value": f"{signal.get('volume_ratio', '?')}x avg", "inline": True},
            {"name": "📊 ADR%",        "value": f"{signal.get('adr_pct', '?')}%", "inline": True},
            {"name": "📝 Notes",       "value": signal.get("notes", "—"), "inline": False},
        ],
        "footer": {"text": _now_et()},
    }
    _post({"embeds": [embed]})


def send_tp_hit(event: dict):
    """🎯 Take profit alert."""
    tp_num = "TP1" if event["type"] == "tp1" else "TP2"
    tp_val = event.get("tp1") if event["type"] == "tp1" else event.get("tp2")
    pct    = event.get("pct_chg", 0)

    embed = {
        "title":  f"🎯 {tp_num} HIT — ${event['symbol']}",
        "color":  COLORS["tp"],
        "fields": [
            {"name": "Entry",            "value": f"`${event['entry']}`",  "inline": True},
            {"name": "Exit",             "value": f"`${event['price']}`",  "inline": True},
            {"name": "P&L",              "value": f"`+{pct:.2f}%`",        "inline": True},
            {"name": "Shares Trimmed",   "value": str(event.get("shares_trimmed", "?")),  "inline": True},
            {"name": "Shares Remaining", "value": str(event.get("shares_remaining", "?")), "inline": True},
            {"name": "Stop → Breakeven", "value": "✅ Stop moved to entry", "inline": True} if event["type"] == "tp1" else
            {"name": "Strategy",         "value": event.get("strategy", "—"), "inline": True},
        ],
        "footer": {"text": _now_et()},
    }
    _post({"embeds": [embed]})


def send_stop_loss(event: dict):
    """🛑 Stop loss hit alert."""
    embed = {
        "title":  f"🛑 STOPPED OUT — ${event['symbol']}",
        "color":  COLORS["sl"],
        "fields": [
            {"name": "Entry",     "value": f"`${event['entry']}`",  "inline": True},
            {"name": "Stop",      "value": f"`${event['price']}`",  "inline": True},
            {"name": "P&L",       "value": f"`{event['pct_chg']:.2f}%`",      "inline": True},
            {"name": "$ Loss",    "value": f"`–${event.get('dollar_loss', '?')}`", "inline": True},
            {"name": "Strategy",  "value": event.get("strategy", "—"), "inline": True},
        ],
        "footer": {"text": _now_et()},
    }
    _post({"embeds": [embed]})


def send_eod_summary(open_positions: list[dict], signals_today: int):
    """📊 End-of-day summary."""
    if not open_positions:
        desc = "No open positions."
    else:
        lines = []
        for p in open_positions:
            sign  = "+" if p["pct_chg"] >= 0 else ""
            tp_status = ""
            if p.get("tp2_hit"):   tp_status = " ✅✅"
            elif p.get("tp1_hit"): tp_status = " ✅"
            lines.append(
                f"**${p['symbol']}** {sign}{p['pct_chg']}% | "
                f"Entry ${p['entry']} → ${p['current']} | "
                f"{p['shares']} shares{tp_status}"
            )
        desc = "\n".join(lines)

    embed = {
        "title":       "📊 End of Day Summary",
        "description": desc,
        "color":       COLORS["summary"],
        "fields": [
            {"name": "Signals fired today", "value": str(signals_today), "inline": True},
            {"name": "Open positions",      "value": str(len(open_positions)), "inline": True},
        ],
        "footer": {"text": _now_et()},
    }
    _post({"embeds": [embed]})


def send_scan_start(n_symbols: int):
    _post({"content": f"🔍 **Morning scan** starting on {n_symbols} symbols..."})


def send_no_signals():
    _post({"content": f"✅ Morning scan complete — no new signals. {_now_et()}"})


# Rate-limit error alerts: same error at most once per 15 min,
# so a persistent API failure doesn't flood the channel every 5-min cycle.
_last_error_at: dict[str, float] = {}


def send_error(msg: str):
    import time as _time
    key = msg[:60]
    now = _time.time()
    if now - _last_error_at.get(key, 0) < 900:
        return
    _last_error_at[key] = now
    _post({"content": f"⚠️ **Bot error:** {msg} | {_now_et()}"})
