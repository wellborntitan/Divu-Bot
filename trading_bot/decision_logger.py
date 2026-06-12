"""
decision_logger.py  —  Human-Readable Decision Log
====================================================
Logs every strategy check (pass/fail + value) for every symbol scanned,
plus trade outcomes for the backtest.

Output files:
  bot_decisions.log       — rolling log from the live bot (appended each session)
  backtest_journal.txt    — per-trade reasoning from backtest.py

Usage:
  from decision_logger import DecisionLogger
  dlog = DecisionLogger(mode="bot")   # or mode="backtest"
  dlog.start_symbol("NVDA", "Continuation Model (PRIMARY)")
  dlog.check("EMA stack bullish", True,  "8>21>50")
  dlog.check("Base tightness",    False, "9.2% > 8.0% max")  # ← failure
  dlog.rejected()     # logs the rejection with first failed check highlighted
  # — or —
  dlog.signal_fired(entry=285.40, stop=278.50, tp1=292.30, tp2=299.20)
"""
import logging
import os
from datetime import datetime
from pathlib import Path

BOT_DIR = Path(__file__).parent

# ── File paths ────────────────────────────────────────────────────────────────
BOT_LOG_PATH       = BOT_DIR / "bot_decisions.log"
BACKTEST_LOG_PATH  = BOT_DIR / "backtest_journal.txt"

# ── Control verbosity ─────────────────────────────────────────────────────────
# "signals_only"  → only log signals that fired (quiet, good for live trading)
# "rejections"    → log every rejection + all signals
# "full"          → log every check for every symbol (very verbose, use for debugging)
LOG_LEVEL = os.getenv("DECISION_LOG_LEVEL", "signals_only")


class DecisionLogger:
    """
    Context object for one symbol × strategy check.
    Collect checks, then call .rejected() or .signal_fired().
    """

    def __init__(self, mode: str = "bot"):
        self.mode    = mode          # "bot" or "backtest"
        self._sym    = ""
        self._strat  = ""
        self._checks: list[tuple[str, bool, str]] = []  # (label, passed, detail)
        self._date   = ""

        self._file = (BOT_LOG_PATH if mode == "bot" else BACKTEST_LOG_PATH)
        # Write session header on first use
        if not self._file.exists() or mode == "backtest":
            with open(self._file, "w", encoding="utf-8") as f:
                f.write(f"{'='*70}\n")
                f.write(f"  Decision log — {mode.upper()} — {datetime.now():%Y-%m-%d %H:%M}\n")
                f.write(f"{'='*70}\n\n")

    def start_symbol(self, symbol: str, strategy: str, date: str = ""):
        """Begin collecting checks for a new symbol/strategy pair."""
        self._sym    = symbol
        self._strat  = strategy
        self._checks = []
        self._date   = date or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def check(self, label: str, passed: bool, detail: str = ""):
        """Record one condition check."""
        self._checks.append((label, passed, detail))

    def rejected(self):
        """Log a rejection. Only writes to file if LOG_LEVEL != 'signals_only'."""
        if LOG_LEVEL == "signals_only":
            return

        # Find the first failed check
        first_fail = next(
            (c for c in self._checks if not c[1]), None
        )

        if LOG_LEVEL == "rejections" and first_fail is None:
            return  # all passed somehow? shouldn't happen

        lines = [f"[{self._date}]  {self._sym:<6}  {self._strat}  →  REJECTED"]

        if LOG_LEVEL == "full":
            for label, passed, detail in self._checks:
                mark   = "  PASS" if passed else "  FAIL"
                detail_str = f"  ({detail})" if detail else ""
                lines.append(f"    {mark}  {label}{detail_str}")
        else:
            # Just show the blocking condition
            if first_fail:
                lines.append(f"    BLOCKED BY: {first_fail[0]}"
                              + (f"  ({first_fail[2]})" if first_fail[2] else ""))

        self._write(lines)

    def signal_fired(self, entry: float, stop: float,
                     tp1: float, tp2: float,
                     notes: str = "", direction: str = "LONG"):
        """Log a fired signal — always written regardless of LOG_LEVEL."""
        risk = abs(entry - stop)
        lines = [
            f"[{self._date}]  {self._sym:<6}  {self._strat}  →  SIGNAL FIRED ({direction})",
        ]
        # Always show all passing conditions for a signal
        for label, passed, detail in self._checks:
            mark = "  PASS" if passed else "  FAIL"
            detail_str = f"  ({detail})" if detail else ""
            lines.append(f"    {mark}  {label}{detail_str}")

        lines += [
            f"    Entry  : ${entry:.2f}",
            f"    Stop   : ${stop:.2f}   (risk ${risk:.2f})",
            f"    TP1    : ${tp1:.2f}   (+{abs(tp1-entry)/risk:.1f}R)",
            f"    TP2    : ${tp2:.2f}   (+{abs(tp2-entry)/risk:.1f}R)",
        ]
        if notes:
            lines.append(f"    Notes  : {notes}")
        lines.append("")
        self._write(lines)

    def trade_outcome(self, symbol: str, strategy: str, signal_date: str,
                      entry: float, exit_price: float,
                      pnl_r: float, outcome: str, days_held: int):
        """Log a completed backtest trade outcome."""
        direction = "+" if pnl_r >= 0 else ""
        lines = [
            f"[{signal_date}]  {symbol:<6}  {strategy}",
            f"    Entry ${entry:.2f}  →  Exit ${exit_price:.2f}  "
            f"({direction}{pnl_r:.2f}R)  via {outcome}  held {days_held}d",
            "",
        ]
        self._write(lines)

    def scan_summary(self, total: int, signals: int, skipped: int):
        """Log a summary line at the end of a scan."""
        lines = [
            f"[{datetime.now():%Y-%m-%d %H:%M:%S}]  SCAN COMPLETE  "
            f"{total} symbols  {signals} signals  {skipped} skipped",
            "",
        ]
        self._write(lines)

    def _write(self, lines: list[str]):
        with open(self._file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


# ── Module-level singleton used by pattern_detector ───────────────────────────
# pattern_detector.py calls  dlog.start_symbol / dlog.check / dlog.rejected
# main.py creates the logger at startup and assigns it here
_active: DecisionLogger | None = None

def set_logger(logger: DecisionLogger):
    global _active
    _active = logger

def get_logger() -> DecisionLogger | None:
    return _active
