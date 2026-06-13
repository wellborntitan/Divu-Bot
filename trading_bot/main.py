"""
Trading Bot — Main Scheduler
============================
Schedule:
  09:15 ET  Morning scan   — daily chart pattern detection
  10:05 ET  Intraday scan  — flat top 10AM volume anomaly check
  Every 5m  Position check — TP/SL monitoring during market hours
  16:15 ET  EOD summary    — open positions + daily stats

Usage:
  1. Copy .env.example → .env and fill in your keys
  2. pip install -r requirements.txt
  3. python main.py
"""
import atexit
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from config import Config
from data_fetcher import (
    get_bars, get_intraday_bars, get_top_movers, get_account_value,
    get_all_market_symbols, is_trading_day,
)
from pattern_detector import scan_symbol
import decision_logger as _dlog
from risk_manager import calculate_shares, calculate_dollar_risk
from trade_executor import submit_market_order, submit_stop_order
from position_monitor import (
    add_position, check_all_positions, get_open_summary, load_positions,
    reconcile_positions, in_cooldown,
)
from discord_notifier import (
    send_new_signal, send_eod_summary, send_scan_start,
    send_no_signals, send_error,
)

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("main")
ET = pytz.timezone("America/New_York")

# ─── Single-instance lock ─────────────────────────────────────────────────────
_PID_FILE = Path(__file__).parent / "bot.pid"


def _acquire_lock():
    """Exit immediately if another bot process is already running."""
    if _PID_FILE.exists():
        try:
            old_pid = int(_PID_FILE.read_text().strip())
            try:
                os.kill(old_pid, 0)   # signal 0 = existence check
                # Process is alive → block startup
                print(f"\n{'='*55}")
                print(f"  Bot is already running (PID {old_pid}).")
                print(f"  Close the other terminal window first,")
                print(f"  then try again.")
                print(f"{'='*55}\n")
                sys.exit(1)
            except OSError:
                # Dead process — stale lock, safe to take over
                pass
        except (ValueError, IOError):
            pass  # unreadable lock → overwrite it

    _PID_FILE.write_text(str(os.getpid()))
    atexit.register(lambda: _PID_FILE.unlink(missing_ok=True))

# Track signals fired today
_signals_today: list[dict] = []

# Signals fired today, persisted to disk so the 5-min session loop,
# morning scan, and EOD summary (separate processes on GitHub Actions)
# all share the same dedup list.
_SIGNALS_FILE = Path(__file__).parent / "signals_today.json"


def _load_signals_file() -> list[dict]:
    """Return today's recorded signals (empty list if stale or missing)."""
    try:
        data = json.loads(_SIGNALS_FILE.read_text())
        if data.get("date") == datetime.now(ET).strftime("%Y-%m-%d"):
            return data.get("signals", [])
    except (OSError, ValueError):
        pass
    return []


def _record_signal_file(sig: dict):
    signals = _load_signals_file()
    signals.append({"symbol": sig["symbol"], "strategy": sig["strategy"]})
    _SIGNALS_FILE.write_text(json.dumps({
        "date": datetime.now(ET).strftime("%Y-%m-%d"),
        "signals": signals,
    }, indent=2))


def _already_signaled_today(sig: dict) -> bool:
    return any(s["symbol"] == sig["symbol"] and s["strategy"] == sig["strategy"]
               for s in _load_signals_file())


def _git_sync_state(message: str = "chore: bot state update [skip ci]"):
    """
    Commit positions.json + signals_today.json back to the repo.
    Only runs inside GitHub Actions (no-op locally).
    """
    if os.getenv("GITHUB_ACTIONS") != "true":
        return
    repo = Path(__file__).resolve().parent.parent

    def g(*args):
        return subprocess.run(["git", "-C", str(repo), *args],
                              capture_output=True, text=True)

    g("add", "trading_bot/positions.json", "trading_bot/signals_today.json",
      "trading_bot/trade_log.json")
    if g("diff", "--staged", "--quiet").returncode == 0:
        return  # nothing changed
    g("-c", "user.name=Trading Bot",
      "-c", "user.email=github-actions[bot]@users.noreply.github.com",
      "commit", "-m", message)
    for _ in range(3):
        g("pull", "--rebase", "-X", "theirs", "origin", "main")
        if g("push", "origin", "main").returncode == 0:
            return
        time.sleep(5)
    logger.error("[git_sync] Failed to push state after 3 attempts")

# Import the notifier as an object so position_monitor can use it
import discord_notifier as _notifier


class _Notifier:
    """Thin wrapper so position_monitor can call methods on a passed object."""
    send_tp_hit    = staticmethod(_notifier.send_tp_hit)
    send_stop_loss = staticmethod(_notifier.send_stop_loss)
    send_ema_trail = staticmethod(_notifier.send_ema_trail)

NOTIFIER = _Notifier()


def _build_universe() -> list[str]:
    """
    Full market scan universe.
    Priority order:
      1. Full market via snapshot pre-filter (~400-700 symbols)
      2. Fallback: base watchlist + top movers if market scan fails
    Deduplicates and always includes the core watchlist.
    """
    # Always include the core watchlist
    core = list(Config.BASE_WATCHLIST)

    # Try full market scan
    market = get_all_market_symbols()

    if market:
        # Merge: core first (priority), then market symbols not already included
        universe = core[:]
        for sym in market:
            if sym not in universe:
                universe.append(sym)
        logger.info(f"[universe] Full market scan: {len(universe)} symbols total")
    else:
        # Fallback to watchlist + top movers
        logger.warning("[universe] Market scan unavailable, using watchlist + movers")
        universe = core[:]
        for sym in get_top_movers(limit=50):
            if sym not in universe:
                universe.append(sym)

    return [s for s in universe if len(s) <= 5]


def _filter_symbol(df) -> bool:
    """Pre-filter: skip if price, volume, or ADR doesn't meet minimums."""
    if df is None or len(df) < 20:
        return False
    last  = df.iloc[-1]
    avg_v = df["volume"].iloc[-20:].mean()
    avg_r = ((df["high"] - df["low"]) / df["close"]).iloc[-20:].mean() * 100
    return (last["close"] >= Config.MIN_PRICE and
            avg_v >= Config.MIN_AVG_VOLUME and
            avg_r >= Config.MIN_ADR_PCT)


# ─── MORNING SCAN (9:15 AM ET) ───────────────────────────────────────────────
def morning_scan():
    if not is_trading_day():
        logger.info("[morning_scan] Market holiday — skipping.")
        return
    logger.info("=== MORNING SCAN START ===")
    universe = _build_universe()
    send_scan_start(len(universe))

    account_equity = get_account_value()
    if account_equity is None:
        logger.error("[morning_scan] Cannot fetch account equity — aborting scan.")
        send_error("Morning scan aborted: account equity unavailable")
        return
    logger.info(f"Account equity: ${account_equity:,.2f}")

    new_signals = []
    for symbol in universe:
        try:
            df = get_bars(symbol, days=Config.LOOKBACK_DAYS)
            if not _filter_symbol(df):
                continue
            signals = scan_symbol(symbol, df, df_intraday=None)
            for sig in signals:
                if in_cooldown(sig["symbol"], sig["strategy"]):
                    continue
                shares       = calculate_shares(sig["entry"], sig["stop"], account_equity,
                                                is_short=(sig["signal_type"] == "short"))
                dollar_risk  = calculate_dollar_risk(shares, sig["entry"], sig["stop"])
                if shares < 1:
                    continue

                logger.info(f"SIGNAL: {sig['strategy']} on {symbol} | "
                            f"Entry ${sig['entry']} Stop ${sig['stop']} "
                            f"TP1 ${sig['tp1']} TP2 ${sig['tp2']} | {shares} shares")

                # Send Discord alert
                send_new_signal(sig, shares, dollar_risk)

                # Execute paper trade
                side = "buy" if sig["signal_type"] == "long" else "sell"
                order = submit_market_order(symbol, shares, side)
                if order:
                    add_position(sig, shares, dollar_risk)
                    _signals_today.append(sig)
                    _record_signal_file(sig)
                    new_signals.append(sig)

        except Exception as e:
            logger.error(f"[scan] {symbol}: {e}")
            continue

    if not new_signals:
        send_no_signals()
    logger.info(f"=== MORNING SCAN DONE — {len(new_signals)} signals ===")


# ─── INTRADAY SCAN (10:05 AM ET) ─────────────────────────────────────────────
def intraday_scan():
    """
    Secondary scan focused on Flat Top + volume anomaly.
    Re-checks symbols near flat resistance with 10AM volume spike.
    """
    logger.info("=== INTRADAY (10AM) SCAN ===")
    universe = _build_universe()
    account_equity = get_account_value()
    if account_equity is None:
        logger.error("[intraday_scan] Account equity unavailable — aborting.")
        return

    for symbol in universe:
        try:
            df_daily    = get_bars(symbol, days=Config.LOOKBACK_DAYS)
            df_intraday = get_intraday_bars(symbol, timeframe="5Min", limit=20)

            if not _filter_symbol(df_daily):
                continue
            if df_intraday is None:
                continue

            signals = scan_symbol(symbol, df_daily, df_intraday=df_intraday)
            for sig in signals:
                # Only act on flat-top with confirmed volume anomaly
                if "Flat Top" not in sig["strategy"]:
                    continue
                if "No 10AM vol anomaly" in sig.get("notes", ""):
                    continue
                # Avoid duplicate if already fired in morning scan
                if any(s["symbol"] == symbol and s["strategy"] == sig["strategy"]
                       for s in _signals_today):
                    continue

                shares      = calculate_shares(sig["entry"], sig["stop"], account_equity)
                dollar_risk = calculate_dollar_risk(shares, sig["entry"], sig["stop"])
                if shares < 1:
                    continue

                logger.info(f"INTRADAY SIGNAL: {sig['strategy']} on {symbol}")
                send_new_signal(sig, shares, dollar_risk)

                order = submit_market_order(symbol, shares, "buy")
                if order:
                    add_position(sig, shares, dollar_risk)
                    _signals_today.append(sig)

        except Exception as e:
            logger.error(f"[intraday_scan] {symbol}: {e}")

    logger.info("=== INTRADAY SCAN DONE ===")


# ─── POSITION MONITOR (every 5 min during market hours) ──────────────────────
def monitor_positions():
    now = datetime.now(ET)
    # Only run between 9:30 and 16:00 ET
    if not (9 * 60 + 30 <= now.hour * 60 + now.minute <= 16 * 60):
        return
    # Loop every 10 s for 50 s (5 checks per GitHub Actions run — Actions min interval is 1 min)
    for _ in range(5):
        try:
            check_all_positions(NOTIFIER)
        except Exception as e:
            logger.error(f"[monitor] {e}")
            send_error(f"Position monitor error: {e}")
        time.sleep(10)


# ─── EOD SUMMARY (4:15 PM ET) ────────────────────────────────────────────────
def eod_summary():
    if not is_trading_day():
        logger.info("[eod] Market holiday — skipping.")
        return
    logger.info("=== EOD SUMMARY ===")
    open_pos = get_open_summary()
    # Read from the shared file so the count survives across separate
    # GitHub Actions processes (in-memory list is empty in cloud mode).
    n_signals = len(_load_signals_file()) or len(_signals_today)
    send_eod_summary(open_pos, signals_today=n_signals)
    _signals_today.clear()


# ─── CONTINUOUS SESSION LOOP (every 5 min, 9:30-16:00 ET) ────────────────────
def _project_today_volume(df):
    """
    Mid-session, today's daily bar holds only PARTIAL volume, which makes
    every volume-ratio check unfairly hard (e.g. at 11 AM a stock has had
    ~25% of the day to trade). Scale today's volume up to a full-day
    projection based on elapsed session time so breakout volume checks are
    apples-to-apples. No-op if the last bar isn't today.
    """
    try:
        now = datetime.now(ET)
        last_ts = df.index[-1]
        last_date = last_ts.date() if hasattr(last_ts, "date") else None
        if last_date != now.date():
            return df
        elapsed = (now.hour * 60 + now.minute) - (9 * 60 + 30)
        frac = max(0.15, min(1.0, elapsed / 390))   # 390 min session; floor avoids wild early projections
        if frac >= 1.0:
            return df
        df = df.copy()
        vcol = df.columns.get_loc("volume")
        df.iloc[-1, vcol] = int(df.iloc[-1, vcol] / frac)
        return df
    except Exception:
        return df


def _focused_universe() -> list[str]:
    """Core watchlist + today's top movers — small enough to scan in <5 min."""
    uni = list(Config.BASE_WATCHLIST)
    try:
        for s in get_top_movers(limit=50):
            if s not in uni:
                uni.append(s)
    except Exception as e:
        logger.warning(f"[session] top movers unavailable: {e}")
    return [s for s in uni if len(s) <= 5]


def _scan_universe_once(universe: list[str], account_equity: float) -> int:
    """One scan pass. Skips symbols already open or already signaled today."""
    open_pos = load_positions()
    fired = 0

    # ── Regime filter: only take longs when SPY is in an uptrend ─────────────
    # Compute once per scan pass (not per-symbol) to avoid redundant API calls.
    # If SPY data is unavailable, fail open (don't block trades on data error).
    try:
        spy_df = get_bars("SPY", days=70)
        if spy_df is not None and len(spy_df) >= 50:
            spy_ema50 = float(spy_df["close"].ewm(span=50, adjust=False).mean().iloc[-1])
            spy_in_uptrend = float(spy_df["close"].iloc[-1]) > spy_ema50
            logger.info(f"[regime] SPY ${spy_df['close'].iloc[-1]:.2f} vs 50EMA ${spy_ema50:.2f} — "
                        f"{'UPTREND ✓' if spy_in_uptrend else 'DOWNTREND — longs skipped'}")
        else:
            spy_in_uptrend = True
    except Exception as e:
        logger.warning(f"[regime] SPY fetch failed ({e}) — failing open")
        spy_in_uptrend = True
    # ─────────────────────────────────────────────────────────────────────────

    for symbol in universe:
        if symbol in open_pos:
            continue
        try:
            df = get_bars(symbol, days=Config.LOOKBACK_DAYS)
            if not _filter_symbol(df):
                continue
            df = _project_today_volume(df)   # fair volume comparison mid-session
            for sig in scan_symbol(symbol, df, df_intraday=None):
                if _already_signaled_today(sig):
                    continue
                if in_cooldown(sig["symbol"], sig["strategy"]):
                    continue
                # Regime filter: skip long signals when SPY is in downtrend
                if sig.get("signal_type") == "long" and not spy_in_uptrend:
                    logger.info(f"[regime] {symbol} long skipped — SPY below 50 EMA")
                    continue
                shares      = calculate_shares(sig["entry"], sig["stop"], account_equity,
                                               is_short=(sig["signal_type"] == "short"))
                dollar_risk = calculate_dollar_risk(shares, sig["entry"], sig["stop"])
                if shares < 1:
                    continue
                logger.info(f"SESSION SIGNAL: {sig['strategy']} on {symbol} | "
                            f"Entry ${sig['entry']} Stop ${sig['stop']}")
                send_new_signal(sig, shares, dollar_risk)
                side  = "buy" if sig["signal_type"] == "long" else "sell"
                order = submit_market_order(symbol, shares, side)
                if order:
                    # Broker-side GTC stop: protects the position even if
                    # the bot/Actions goes offline. If it rejects because
                    # the entry hasn't filled yet, ensure_protective_stops()
                    # self-heals on the next 5-min cycle.
                    stop_side  = "sell" if side == "buy" else "buy"
                    stop_order = submit_stop_order(symbol, shares, stop_side, sig["stop"])
                    add_position(sig, shares, dollar_risk,
                                 stop_order_id=(stop_order or {}).get("id"))
                    _record_signal_file(sig)
                    fired += 1
        except Exception as e:
            logger.error(f"[session_scan] {symbol}: {e}")
    return fired


def session_loop():
    """
    Long-running market-session job for GitHub Actions.
    Waits for 9:30 ET, then every 5 minutes: checks open positions (TP/SL)
    and re-scans a focused universe for new entries. Commits state back to
    the repo whenever something changes.

    GitHub caps a job at 6 hours, so the trading day is split into two
    sessions: the run started in the morning ends at 12:50 ET, and the
    afternoon run (cron-started ~12:15 ET) covers 12:50-16:00 ET.
    """
    now = datetime.now(ET)
    if now.weekday() >= 5:
        logger.info("[session] Weekend — exiting.")
        return
    if not is_trading_day():
        logger.info("[session] Market holiday — exiting.")
        return

    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if now < open_t:
        wait = (open_t - now).total_seconds()
        logger.info(f"[session] Sleeping {wait/60:.0f} min until 9:30 ET")
        time.sleep(wait)

    # Pull latest repo state — the 9:15 morning scan (separate job) may have
    # committed new positions/signals while this job was sleeping.
    if os.getenv("GITHUB_ACTIONS") == "true":
        subprocess.run(["git", "-C", str(Path(__file__).resolve().parent.parent),
                        "pull", "--rebase", "origin", "main"],
                       capture_output=True, text=True)

    start = datetime.now(ET)
    if start.hour * 60 + start.minute < 12 * 60:
        end = start.replace(hour=12, minute=50, second=0, microsecond=0)
    else:
        end = start.replace(hour=16, minute=0, second=0, microsecond=0)
    if start >= end:
        logger.info("[session] Started past session end — exiting.")
        return

    logger.info(f"=== SESSION LOOP {start.strftime('%H:%M')} -> "
                f"{end.strftime('%H:%M')} ET (5-min cadence) ===")
    universe = _focused_universe()
    logger.info(f"[session] Focused universe: {len(universe)} symbols")
    cycles = 0

    while datetime.now(ET) < end:
        changed = False
        try:
            # Drop tracked positions the broker no longer holds
            # (stop filled while offline / manual close)
            removed = reconcile_positions(NOTIFIER)
            if removed:
                changed = True
                send_error(f"Reconciled (closed at broker): {', '.join(removed)}")
        except Exception as e:
            logger.error(f"[session] reconcile: {e}")
        try:
            if check_all_positions(NOTIFIER):
                changed = True
        except Exception as e:
            logger.error(f"[session] position check: {e}")
            send_error(f"Session position check error: {e}")
        try:
            equity = get_account_value()
            if equity is None:
                logger.warning("[session] equity unavailable — skipping scan pass")
            elif _scan_universe_once(universe, equity) > 0:
                changed = True
        except Exception as e:
            logger.error(f"[session] scan pass: {e}")
        if changed:
            _git_sync_state()

        cycles += 1
        if cycles % 6 == 0:            # refresh movers every ~30 min
            universe = _focused_universe()

        sleep_s = 300 - (time.time() % 300)   # align to next 5-min mark
        if datetime.now(ET) + timedelta(seconds=sleep_s) >= end:
            break
        time.sleep(sleep_s)

    _git_sync_state("chore: end-of-session state [skip ci]")
    logger.info("=== SESSION LOOP DONE ===")


# ─── SCHEDULER ───────────────────────────────────────────────────────────────
def main():
    # Block if another instance is already running
    _acquire_lock()

    # Start decision logger (signals_only by default — set DECISION_LOG_LEVEL=full in .env for verbose)
    _dlog.set_logger(_dlog.DecisionLogger(mode="bot"))
    logger.info(f"Decision log → bot_decisions.log  (level: {_dlog.LOG_LEVEL})")
    logger.info(f"Bot starting | Paper={Config.PAPER_TRADING} | Risk={Config.RISK_PCT*100:.0f}%")

    if not Config.ALPACA_API_KEY:
        logger.error("ALPACA_API_KEY not set. Copy .env.example → .env and fill in your keys.")
        return
    if not Config.DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL not set — Discord alerts disabled.")

    # misfire_grace_time=300 means APScheduler will still fire a job that was
    # missed by up to 5 minutes (e.g. if the bot starts at 9:16 it will still
    # run the 9:15 morning scan).
    scheduler = BlockingScheduler(timezone=ET, misfire_grace_time=300)

    # Morning scan: 9:15 AM ET Mon–Fri
    scheduler.add_job(morning_scan, CronTrigger(
        day_of_week="mon-fri", hour=9, minute=15, timezone=ET))

    # Intraday (flat top volume anomaly): 10:05 AM ET Mon–Fri
    scheduler.add_job(intraday_scan, CronTrigger(
        day_of_week="mon-fri", hour=10, minute=5, timezone=ET))

    # Position monitor: every 5 min Mon-Fri 9:30-16:00 ET
    scheduler.add_job(monitor_positions, CronTrigger(
        day_of_week="mon-fri", minute="*/5", timezone=ET))

    # EOD summary: 4:15 PM ET Mon-Fri
    scheduler.add_job(eod_summary, CronTrigger(
        day_of_week="mon-fri", hour=16, minute=15, timezone=ET))

    # Catch-up: if we start between 9:15 and 10:00 on a weekday and the
    # morning scan hasn t run yet today (misfire window exceeded), trigger it now.
    _now = datetime.now(ET)
    if _now.weekday() < 5 and 9 * 60 + 15 <= _now.hour * 60 + _now.minute < 10 * 60:
        logger.info("[startup] Late start detected -- running morning scan immediately.")
        import threading
        threading.Thread(target=morning_scan, daemon=True).start()

    logger.info("Scheduler running. Press Ctrl+C to stop.")
    logger.info("Jobs scheduled:")
    logger.info("  09:15 ET -- Morning scan (all strategies)")
    logger.info("  10:05 ET -- Intraday scan (flat top volume anomaly)")
    logger.info("  Every 5m -- Position monitor (TP/SL)")
    logger.info("  16:15 ET -- EOD summary")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")


if __name__ == "__main__":
    main()
