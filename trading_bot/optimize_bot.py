"""
optimize_bot.py  —  Backtest-Driven Bot Optimizer
===================================================
Reads Backtest_Results.xlsx, grades each strategy on:
  • Profit Factor  (gross wins / gross losses in R)
  • Win Rate
  • Average R per trade
  • Max Drawdown
  • TP1 hit rate (how often TP1 is reached before stop)

Then automatically:
  1. Enables/disables strategies in config.py
  2. Tightens or relaxes the volume-ratio threshold per strategy
  3. Adjusts the 2nd take-profit R-multiple per strategy
  4. Saves a human-readable report  →  backtest_summary.txt

Run after the backtest finishes:
  double-click  OPTIMIZE BOT.bat
  — or —
  python optimize_bot.py

Restart START BOT.bat after this to pick up the changes.
"""
import json
import re
import sys
from pathlib import Path

import pandas as pd

BOT_DIR  = Path(__file__).parent
RESULTS  = BOT_DIR.parent / "Backtest_Results.xlsx"
CONFIG   = BOT_DIR / "config.py"
PATTERN  = BOT_DIR / "pattern_detector.py"
TUNING   = BOT_DIR / "bot_tuning.json"
SUMMARY  = BOT_DIR / "backtest_summary.txt"

# ── Thresholds for strategy grading ──────────────────────────────────────────
GRADE_A_PF     = 1.5    # profit factor  → keep as-is
GRADE_A_WR     = 45.0   # win rate %     → keep as-is
GRADE_B_PF     = 1.0    # PF >= 1.0 but < 1.5 → tighten filters
GRADE_C_PF     = 1.0    # PF < 1.0 → disable
DISABLE_WR     = 35.0   # also disable if win rate < 35%

# Canonical strategy names — must match sig["strategy"] from each detector exactly
ALL_STRATEGIES = [
    "Continuation Model (PRIMARY)",
    "Flat Top Base Breakout",
    "Stage 2 Base Breakout",
    "Downtrend Trendline Reversal",
    "Distribution Base Breakdown (SHORT)",
    "Accumulation Base Breakout",
]


def load_results() -> pd.DataFrame | None:
    if not RESULTS.exists():
        print(f"\n  ERROR: {RESULTS} not found.")
        print("  Run RUN BACKTEST.bat first, then come back here.\n")
        return None
    try:
        df = pd.read_excel(RESULTS, sheet_name="All Trades")
        print(f"  Loaded {len(df):,} trades from {RESULTS.name}")
        return df
    except Exception as e:
        print(f"  ERROR reading Excel: {e}")
        return None


def analyse(df: pd.DataFrame) -> dict:
    """
    Compute per-strategy metrics.
    Returns dict: strategy_name → metrics dict.
    """
    results = {}
    for strat in ALL_STRATEGIES:
        grp = df[df["strategy"] == strat]
        if len(grp) == 0:
            results[strat] = {
                "signals": 0, "win_rate": 0, "profit_factor": 0,
                "total_r": 0, "avg_r": 0, "max_dd": 0,
                "tp1_hit_pct": 0, "tp2_hit_pct": 0,
                "avg_vol_ratio_win": 1.5, "avg_vol_ratio_loss": 1.5,
                "avg_days_held": 0, "grade": "N/A",
            }
            continue

        n        = len(grp)
        winners  = grp[grp["pnl_r"] > 0]
        losers   = grp[grp["pnl_r"] <= 0]
        win_r    = len(winners) / n * 100
        gross_p  = winners["pnl_r"].sum()
        gross_l  = abs(losers["pnl_r"].sum())
        pf       = gross_p / gross_l if gross_l > 0 else 99.0
        total_r  = grp["pnl_r"].sum()
        avg_r    = total_r / n
        tp1_pct  = grp["tp1_hit"].mean() * 100 if "tp1_hit" in grp.columns else 0
        tp2_pct  = (grp["outcome"] == "tp2_hit").mean() * 100

        # Running drawdown
        cumR = grp.sort_values("signal_date")["pnl_r"].cumsum()
        peak = cumR.cummax()
        max_dd = (cumR - peak).min()

        # Average vol ratio split by winners / losers (for threshold tuning)
        avg_vol_win  = winners["vol_ratio"].mean() if len(winners) and "vol_ratio" in winners else 1.5
        avg_vol_loss = losers["vol_ratio"].mean()  if len(losers)  and "vol_ratio" in losers  else 1.5

        # Grade
        if pf >= GRADE_A_PF and win_r >= GRADE_A_WR:
            grade = "A"
        elif pf >= GRADE_B_PF and win_r >= DISABLE_WR:
            grade = "B"
        else:
            grade = "C"

        results[strat] = {
            "signals":          n,
            "win_rate":         round(win_r, 1),
            "profit_factor":    round(pf, 2),
            "total_r":          round(total_r, 2),
            "avg_r":            round(avg_r, 3),
            "max_dd":           round(max_dd, 2),
            "tp1_hit_pct":      round(tp1_pct, 1),
            "tp2_hit_pct":      round(tp2_pct, 1),
            "avg_vol_ratio_win":  round(avg_vol_win,  2),
            "avg_vol_ratio_loss": round(avg_vol_loss, 2),
            "avg_days_held":    round(grp["days_held"].mean(), 1),
            "grade":            grade,
        }
    return results


def compute_tuning(metrics: dict) -> dict:
    """
    From per-strategy metrics, compute the new bot settings.

    Returns a tuning dict that will be saved to bot_tuning.json
    and applied to config.py and pattern_detector.py.
    """
    enabled         = {}  # strategy → True/False
    min_vol_ratio   = {}  # strategy → float threshold
    rr2             = {}  # strategy → float (2nd TP R-multiple)

    for strat, m in metrics.items():
        g = m["grade"]

        # ── Enable / disable ──────────────────────────────────────────────
        enabled[strat] = g in ("A", "B")   # disable grade C

        # ── Volume ratio threshold ─────────────────────────────────────────
        # Grade A: stay at 1.5 (already solid)
        # Grade B: raise to midpoint of avg_win vol to filter out noisy entries
        # Grade C: doesn't matter (disabled), keep 1.5 as default
        if g == "A":
            min_vol = 1.5
        elif g == "B":
            # Raise threshold to avg of winning vol ratios, capped at 2.5
            min_vol = min(round(m["avg_vol_ratio_win"], 1), 2.5)
            min_vol = max(min_vol, 1.5)   # never go below 1.5
        else:
            min_vol = 1.5

        min_vol_ratio[strat] = min_vol

        # ── Second take-profit R-multiple ─────────────────────────────────
        # If TP2 is hit > 40% of the time → aggressive, raise to 2.5
        # If TP2 is hit 20-40% → normal 2.0
        # If TP2 is hit < 20%  → reduce to 1.5 (most exits happen at TP1/trail)
        tp2_pct = m["tp2_hit_pct"]
        if tp2_pct >= 40:
            rr = 2.5
        elif tp2_pct >= 20:
            rr = 2.0
        else:
            rr = 1.5

        rr2[strat] = rr

    return {
        "STRATEGY_ENABLED":       enabled,
        "STRATEGY_MIN_VOL_RATIO": min_vol_ratio,
        "STRATEGY_RR2":           rr2,
    }


def save_tuning(tuning: dict):
    with open(TUNING, "w") as f:
        json.dump(tuning, f, indent=2)
    print(f"  Tuning saved → {TUNING.name}")


def patch_config(tuning: dict):
    """
    Safely update the three STRATEGY_* dicts in config.py.
    Replaces each dict value line-by-line using regex so the rest of the
    file is never touched.  Uses Python True/False (not JSON true/false).
    """
    text = CONFIG.read_text(encoding="utf-8")

    def _replace_dict(text: str, attr: str, new_dict: dict) -> str:
        """Replace the value of a class-level dict attribute."""
        # Build Python-literal representation with proper True/False
        def _pyval(v):
            if isinstance(v, bool):
                return "True" if v else "False"
            return repr(v)

        inner = ",\n".join(
            f'        "{k}": {_pyval(v)}' for k, v in new_dict.items()
        )
        new_block = f"    {attr} = {{\n{inner},\n    }}"

        # Match from "    ATTR = {" to the closing "    }" on its own line
        pattern = rf"    {attr}\s*=\s*\{{[^}}]*\}}"
        replaced = re.sub(pattern, new_block, text, flags=re.DOTALL)
        if replaced == text:
            print(f"  WARNING: {attr} not found in config.py — skipping")
        return replaced

    text = _replace_dict(text, "STRATEGY_ENABLED",       tuning["STRATEGY_ENABLED"])
    text = _replace_dict(text, "STRATEGY_MIN_VOL_RATIO", tuning["STRATEGY_MIN_VOL_RATIO"])
    text = _replace_dict(text, "STRATEGY_RR2",           tuning["STRATEGY_RR2"])

    CONFIG.write_text(text, encoding="utf-8")
    print(f"  config.py patched safely ✓")


def patch_pattern_detector():
    """
    Update scan_symbol to:
      1. Skip disabled strategies (checks Config.STRATEGY_ENABLED)
      2. Pass Config.STRATEGY_MIN_VOL_RATIO to each checker via a thread-local override
      3. Import Config if not already imported
    The cleanest approach: rebuild just the scan_symbol function body.
    """
    text = PATTERN.read_text(encoding="utf-8")

    # Ensure Config is imported
    if "from config import Config" not in text and "import Config" not in text:
        text = "from config import Config\n" + text

    # New scan_symbol implementation
    new_scan = '''def scan_symbol(symbol: str, df_daily, df_intraday=None) -> list[dict]:
    """
    Run all 6 strategy detectors on a symbol.
    Respects Config.STRATEGY_ENABLED (set by optimize_bot.py after backtest).
    Returns list of triggered signals (usually 0 or 1).
    """
    signals = []

    checkers = [
        ("Continuation Model (PRIMARY)",    check_continuation_model),
        ("Flat Top Base Breakout",          check_flat_top_breakout),
        ("Stage 2 Breakout",               check_stage2_breakout),
        ("Downtrend Reversal",             check_downtrend_reversal),
        ("Distribution Breakdown (Short)", check_distribution_breakdown),
        ("Accumulation Base Breakout",     check_accumulation_breakout),
    ]

    # Pull enabled map and vol thresholds from Config (may not exist on first run)
    enabled_map  = getattr(Config, "STRATEGY_ENABLED",       {})
    vol_map      = getattr(Config, "STRATEGY_MIN_VOL_RATIO", {})
    rr2_map      = getattr(Config, "STRATEGY_RR2",           {})

    for name, checker in checkers:
        # Skip if the optimizer disabled this strategy
        if enabled_map and not enabled_map.get(name, True):
            continue

        try:
            sig = checker(symbol, df_daily)
            if not sig:
                continue

            # ── Post-filter: volume ratio must meet strategy-specific threshold ──
            min_vol = vol_map.get(name, 1.5)
            if sig.get("volume_ratio", 0) < min_vol:
                continue

            # ── Adjust TP2 R-multiple if optimizer changed it ─────────────────
            rr2 = rr2_map.get(name, 2.0)
            if rr2 != 2.0:
                entry = sig["entry"]
                stop  = sig["stop"]
                risk  = abs(entry - stop)
                is_short = sig.get("signal_type") == "short"
                sig["tp2"] = round(
                    entry - risk * rr2 if is_short else entry + risk * rr2, 2
                )

            # For flat top: also check intraday volume anomaly if data available
            if sig["strategy"] == "Flat Top Base Breakout" and df_intraday is not None:
                if not check_flat_top_volume_anomaly(symbol, df_intraday):
                    sig["notes"] += " | No 10AM vol anomaly yet"

            signals.append(sig)

        except Exception:
            pass  # Skip silently; logging happens in main

    return signals'''

    # Replace the old scan_symbol function
    text = re.sub(
        r"def scan_symbol\(symbol:.*?\Z",
        new_scan,
        text,
        flags=re.DOTALL,
    )

    PATTERN.write_text(text, encoding="utf-8")
    print(f"  pattern_detector.py patched ✓")


def print_report(metrics: dict, tuning: dict):
    """Print a colour-coded performance report to console and save to file."""
    lines = []
    sep   = "=" * 72

    lines.append(sep)
    lines.append("  BACKTEST OPTIMISATION REPORT")
    lines.append(sep)
    lines.append(f"  {'Strategy':<38} {'Sigs':>5} {'WR%':>6} {'PF':>6} "
                 f"{'TotR':>7} {'AvgR':>6} {'DD':>6} {'Grade':>6}")
    lines.append("  " + "-" * 68)

    for strat, m in metrics.items():
        grade  = m["grade"]
        flag   = "✓" if grade == "A" else ("~" if grade == "B" else "✗")
        prefix = f"  {flag} {strat:<36}"
        row = (f"{prefix} {m['signals']:>5} {m['win_rate']:>5.1f}% "
               f"{m['profit_factor']:>6.2f} {m['total_r']:>+7.2f} "
               f"{m['avg_r']:>+6.3f} {m['max_dd']:>6.2f}  {grade}")
        lines.append(row)

    lines.append("")
    lines.append("  Grade key:  A = keep  |  B = raised vol threshold  |  C = disabled")
    lines.append(sep)
    lines.append("  CHANGES APPLIED:")
    lines.append("")

    for strat in ALL_STRATEGIES:
        m   = metrics[strat]
        en  = tuning["STRATEGY_ENABLED"][strat]
        vol = tuning["STRATEGY_MIN_VOL_RATIO"][strat]
        rr  = tuning["STRATEGY_RR2"][strat]
        status = "ENABLED " if en else "DISABLED"
        lines.append(f"    {strat:<40}  {status}  "
                     f"min_vol≥{vol:.1f}x  TP2 at {rr}R")

    lines.append("")
    lines.append("  Bot has been patched. Restart START BOT.bat to activate.")
    lines.append(sep)

    report = "\n".join(lines)
    print("\n" + report + "\n")
    SUMMARY.write_text(report, encoding="utf-8")
    print(f"  Report saved → {SUMMARY.name}")


def main():
    print("\n" + "=" * 60)
    print("  BOT OPTIMIZER  —  Albert Ray Strategy Suite")
    print("=" * 60 + "\n")

    df = load_results()
    if df is None:
        input("Press Enter to close…")
        return

    print("  Analysing strategies…")
    metrics = analyse(df)
    tuning  = compute_tuning(metrics)

    print("  Patching bot files…")
    save_tuning(tuning)
    patch_config(tuning)
    patch_pattern_detector()

    print_report(metrics, tuning)
    input("Press Enter to close…")


if __name__ == "__main__":
    main()
