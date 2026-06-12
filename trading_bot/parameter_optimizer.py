"""
parameter_optimizer.py  —  Grid-Search Parameter Tuner
========================================================
Runs a walk-forward backtest on the BASE_WATCHLIST (60 stocks, 3 years)
for every combination of key threshold parameters.

Finds the combination that maximises:
    Score = Profit_Factor × Win_Rate%  (subject to min. 15 signals)

Then writes the winning parameters directly into config.py so the bot
uses them immediately.  Run this BEFORE the full-market backtest.

Typical runtime: 5–20 minutes on watchlist (~60 stocks).

Usage:
    double-click  RUN PARAMETER OPTIMIZER.bat
    — or —
    python parameter_optimizer.py
"""
import sys
import time
import itertools
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import pytz
import requests

BOT_DIR = Path(__file__).parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

from dotenv import load_dotenv
load_dotenv(BOT_DIR / ".env")

import config as cfg_module   # import module so we can monkey-patch attrs
from config import Config
from indicators import add_emas
from pattern_detector import scan_symbol

ET = pytz.timezone("America/New_York")

# ── Parameter grid ────────────────────────────────────────────────────────────
# Each list is the set of values to test for that config attribute.
# Combinations = product of all list lengths.
PARAM_GRID = {
    # Continuation Model (primary strategy — most sensitive to tuning)
    "CONT_TIGHT_PCT":    [5.0, 6.5, 8.0],      # max base tightness %
    "CONT_MIN_VOL":      [1.5, 2.0, 2.5],       # min vol ratio on breakout
    "CONT_EMA_PROX":     [0.03, 0.05],          # max distance from 8 EMA

    # Global take-profit R-multiple (applies to ALL strategies)
    "RR2":               [1.5, 2.0, 2.5, 3.0],
}

# Backtest settings
LOOKBACK_DAYS  = 3 * 365 + 30
MIN_BARS       = 100
MAX_HOLD_DAYS  = 30
COOLDOWN_DAYS  = 15
MIN_SIGNALS    = 15    # minimum signals needed to score a combo


# ── Data fetching ─────────────────────────────────────────────────────────────

def _headers():
    return {
        "APCA-API-KEY-ID":     Config.ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": Config.ALPACA_SECRET_KEY,
    }


def fetch_bars(symbol: str) -> pd.DataFrame | None:
    end   = datetime.now(ET).strftime("%Y-%m-%d")
    start = (datetime.now(ET) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    url   = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
    params = {"timeframe": "1Day", "start": start, "end": end,
               "limit": 1500, "feed": "iex"}
    try:
        r = requests.get(url, headers=_headers(), params=params, timeout=15)
        if r.status_code == 422:
            return None
        r.raise_for_status()
        data = r.json().get("bars", [])
        if not data:
            return None
        df = pd.DataFrame(data)
        df["t"] = pd.to_datetime(df["t"])
        df = df.rename(columns={"t":"date","o":"open","h":"high",
                                  "l":"low","c":"close","v":"volume"})
        df = df.set_index("date").sort_index()
        return df[["open","high","low","close","volume"]].astype(float)
    except Exception:
        return None


# ── Trade simulation (same as backtest.py) ────────────────────────────────────

def simulate_trade(df, signal_idx, entry_plan, stop, tp1, tp2, direction):
    is_long = direction == "long"
    start   = signal_idx + 1
    if start >= len(df):
        return 0.0

    actual_entry = df.iloc[start]["open"]
    risk = abs(actual_entry - stop) or abs(entry_plan - stop) or 0.01
    tp1_hit      = False
    current_stop = stop

    for i in range(start, min(start + MAX_HOLD_DAYS, len(df))):
        bar       = df.iloc[i]
        ema8      = bar.get("ema8", None)
        days_held = i - start + 1

        if is_long:
            if bar["open"] <= current_stop or bar["low"] <= current_stop:
                exit_p = max(bar["open"], current_stop)
                return (exit_p - actual_entry) / risk
            if tp1_hit and bar["high"] >= tp2:
                return (tp2 - actual_entry) / risk
            if not tp1_hit and bar["high"] >= tp1:
                tp1_hit      = True
                current_stop = actual_entry
            if tp1_hit and ema8 is not None and days_held >= 2 and bar["close"] < ema8:
                return (bar["close"] - actual_entry) / risk
        else:
            if bar["open"] >= current_stop or bar["high"] >= current_stop:
                exit_p = min(bar["open"], current_stop)
                return (actual_entry - exit_p) / risk
            if tp1_hit and bar["low"] <= tp2:
                return (actual_entry - tp2) / risk
            if not tp1_hit and bar["low"] <= tp1:
                tp1_hit      = True
                current_stop = actual_entry
            if tp1_hit and ema8 is not None and days_held >= 2 and bar["close"] > ema8:
                return (actual_entry - bar["close"]) / risk

    last = df.iloc[min(start + MAX_HOLD_DAYS - 1, len(df) - 1)]["close"]
    return ((last - actual_entry) / risk if is_long
            else (actual_entry - last) / risk)


# ── Walk-forward on one symbol ────────────────────────────────────────────────

def backtest_symbol(symbol: str, df: pd.DataFrame) -> list[float]:
    """Return list of pnl_r values for each signal found."""
    df = add_emas(df.copy())
    pnls: list[float] = []
    cooldowns: dict[str, int] = {}

    for i in range(MIN_BARS, len(df) - 1):
        window = df.iloc[: i + 1].copy()
        try:
            signals = scan_symbol(symbol, window)
        except Exception:
            continue
        for sig in signals:
            strat = sig["strategy"]
            if i - cooldowns.get(strat, -9999) < COOLDOWN_DAYS:
                continue
            cooldowns[strat] = i
            pnl = simulate_trade(df, i,
                                  sig["entry"], sig["stop"],
                                  sig["tp1"],   sig["tp2"],
                                  sig.get("signal_type", "long"))
            pnls.append(round(pnl, 3))

    return pnls


# ── Score one parameter combination ───────────────────────────────────────────

def score_combo(data: dict[str, pd.DataFrame]) -> dict:
    """Run backtest on all watchlist symbols with current Config, return metrics."""
    all_pnls: list[float] = []
    for sym, df in data.items():
        all_pnls.extend(backtest_symbol(sym, df))

    n = len(all_pnls)
    if n < MIN_SIGNALS:
        return {"signals": n, "win_rate": 0, "pf": 0, "total_r": 0, "score": 0}

    wins   = [p for p in all_pnls if p > 0]
    losses = [p for p in all_pnls if p <= 0]
    wr     = len(wins) / n * 100
    pf     = sum(wins) / abs(sum(losses)) if losses else 99.0
    total  = sum(all_pnls)
    score  = pf * wr   # primary objective

    return {"signals": n, "win_rate": round(wr, 1), "pf": round(pf, 2),
            "total_r": round(total, 2), "score": round(score, 2)}


# ── Apply winning params to config.py ─────────────────────────────────────────

def apply_to_config(best_params: dict):
    """Write winning parameter values into config.py."""
    text = (BOT_DIR / "config.py").read_text()

    for attr, value in best_params.items():
        # Match lines like:   CONT_TIGHT_PCT   = 8.0
        import re
        pattern = rf"^(\s*{attr}\s*=\s*)(.+)$"
        replacement = lambda m: m.group(1) + repr(value)
        new_text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
        if new_text != text:
            text = new_text
        else:
            # Attribute not found — append to the threshold section
            text = text.replace(
                "    # Global R targets",
                f"    {attr:<20} = {repr(value)}\n    # Global R targets"
            )

    (BOT_DIR / "config.py").write_text(text)
    print("  config.py updated with winning parameters ✓")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 65)
    print("  PARAMETER OPTIMIZER  —  Grid Search on Watchlist")
    print("=" * 65)

    # Build the full combination list
    keys   = list(PARAM_GRID.keys())
    values = list(PARAM_GRID.values())
    combos = list(itertools.product(*values))
    print(f"\n  Parameters: {keys}")
    print(f"  Combinations to test: {len(combos)}")
    print(f"  Universe: {len(Config.BASE_WATCHLIST)} watchlist symbols × 3 years\n")

    # Fetch & cache all data upfront (do this once, not per combo)
    print("  Fetching historical data…")
    data: dict[str, pd.DataFrame] = {}
    for i, sym in enumerate(Config.BASE_WATCHLIST, 1):
        df = fetch_bars(sym)
        if df is not None and len(df) >= MIN_BARS:
            data[sym] = df
            print(f"    [{i:>3}/{len(Config.BASE_WATCHLIST)}] {sym:<6}  {len(df)} bars")
        else:
            print(f"    [{i:>3}/{len(Config.BASE_WATCHLIST)}] {sym:<6}  skip")
        time.sleep(0.25)   # rate limit

    print(f"\n  Data ready: {len(data)} symbols loaded")
    print(f"  Running {len(combos)} parameter combinations…\n")

    results = []
    for idx, combo in enumerate(combos, 1):
        # Apply this combo to Config (monkey-patch so pattern_detector reads it)
        params = dict(zip(keys, combo))
        for k, v in params.items():
            setattr(cfg_module.Config, k, v)

        metrics = score_combo(data)
        metrics["params"] = params
        results.append(metrics)

        bar = "#" * int(idx / len(combos) * 30)
        print(f"  [{bar:<30}] {idx:>3}/{len(combos)}  "
              f"PF={metrics['pf']:>5.2f}  WR={metrics['win_rate']:>4.1f}%  "
              f"n={metrics['signals']:>4}  score={metrics['score']:>7.2f}  "
              f"{params}", end="\r")

    print("\n")

    # Sort by score descending
    results.sort(key=lambda x: x["score"], reverse=True)

    print("  TOP 10 COMBINATIONS:")
    print(f"  {'Rank':<5} {'Score':>8} {'PF':>6} {'WR%':>6} {'Signals':>8}  Parameters")
    print("  " + "-" * 75)
    for rank, r in enumerate(results[:10], 1):
        p = r["params"]
        param_str = "  ".join(f"{k}={v}" for k, v in p.items())
        print(f"  {rank:<5} {r['score']:>8.2f} {r['pf']:>6.2f} {r['win_rate']:>5.1f}% "
              f"{r['signals']:>8}  {param_str}")

    best = results[0]
    print(f"\n  WINNER: score={best['score']:.2f}  PF={best['pf']}  "
          f"WR={best['win_rate']}%  signals={best['signals']}")
    print(f"  Params: {best['params']}\n")

    # Apply winner to config.py
    print("  Applying winning parameters to config.py…")
    apply_to_config(best["params"])

    # Save full results to Excel for review
    out = pd.DataFrame([
        {**r["params"], **{k: v for k, v in r.items() if k != "params"}}
        for r in results
    ]).sort_values("score", ascending=False)
    out_path = BOT_DIR.parent / "Parameter_Optimization_Results.xlsx"
    out.to_excel(out_path, index=False)
    print(f"  Full results → {out_path}")

    print("\n  Done! Restart START BOT.bat or run RUN BACKTEST.bat to use new params.")
    print("=" * 65 + "\n")
    input("Press Enter to close…")


if __name__ == "__main__":
    main()
