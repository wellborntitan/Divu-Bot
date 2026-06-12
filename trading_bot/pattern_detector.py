"""
Pattern detection for all 6 strategies.

Each detector receives a DataFrame of daily OHLCV bars (with EMA columns added)
and returns a Signal dict or None.

Signal dict schema:
{
    "symbol":       str,
    "strategy":     str,
    "signal_type":  "long" | "short",
    "entry":        float,
    "stop":         float,
    "tp1":          float,
    "tp2":          float,
    "volume_ratio": float,
    "adr_pct":      float,
    "notes":        str,
}
"""
import numpy as np

from config import Config
import decision_logger as _dlog
from indicators import (
    add_emas, adr_pct, volume_ratio, volume_slope,
    base_tightness, find_flat_resistance, find_swing_high,
    find_downtrend_trendline, trendline_value_at,
    detect_ignition_candle, low_vol_pullback_after,
    price_near_ema8, is_ema_bullish_stack, ema8_slope_positive,
    pct_from_ema,
)


def _risk_targets(entry: float, stop: float,
                   rr1: float | None = None, rr2: float | None = None,
                   is_short: bool = False) -> tuple[float, float]:
    """Use Config RR values by default; caller can override."""
    if rr1 is None:
        rr1 = getattr(Config, "RR1", 1.0)
    if rr2 is None:
        rr2 = getattr(Config, "RR2", 2.0)
    risk = abs(entry - stop)
    if is_short:
        return round(entry - risk * rr1, 2), round(entry - risk * rr2, 2)
    return round(entry + risk * rr1, 2), round(entry + risk * rr2, 2)


# ─────────────────────────────────────────────
# 1. CONTINUATION MODEL (PRIMARY — 80%+ win rate)
#    Weekly breakout → Ignition → Low-vol pullback
#    → Tight pattern into 8 EMA → Volume breakout
# ─────────────────────────────────────────────
def check_continuation_model(symbol: str, df_raw) -> dict | None:
    STRAT = "Continuation Model (PRIMARY)"
    if df_raw is None or len(df_raw) < 40:
        return None

    df    = add_emas(df_raw)
    last  = df.iloc[-1]
    vol_r = volume_ratio(df)
    log   = _dlog.get_logger()
    if log: log.start_symbol(symbol, STRAT)

    def _fail(label, detail=""):
        if log:
            log.check(label, False, detail)
            log.rejected()
        return None

    def _pass(label, detail=""):
        if log: log.check(label, True, detail)

    # 1. EMA stack
    ema_ok = is_ema_bullish_stack(df)
    if not ema_ok:
        return _fail("EMA bullish stack (8>21>50)", f"8={last.get('ema8',0):.2f} 21={last.get('ema21',0):.2f} 50={last.get('ema50',0):.2f}")
    _pass("EMA bullish stack (8>21>50)")

    # 2. Ignition candle
    ign_lookback = getattr(Config, "CONT_IGN_LOOKBACK", 30)
    ign_idx = detect_ignition_candle(df, lookback=ign_lookback)
    if ign_idx is None:
        return _fail("Ignition candle", f"none in last {ign_lookback} bars")
    _pass("Ignition candle", f"{len(df)-ign_idx}d ago")

    # 3. Low-vol pullback
    lvp_window = getattr(Config, "CONT_LVP_WINDOW", 8)
    lvp_ok = low_vol_pullback_after(df, ign_idx, window=lvp_window)
    if not lvp_ok:
        return _fail("Low-vol pullback after ignition", f"no quiet consolidation in {lvp_window}d window")
    _pass("Low-vol pullback", f"{lvp_window}d window")

    # 4. Base tightness
    tight     = base_tightness(df, period=7)
    tight_pct = getattr(Config, "CONT_TIGHT_PCT", 8.0)
    if tight > tight_pct:
        return _fail("Base tightness", f"{tight:.1f}% > {tight_pct:.1f}% max")
    _pass("Base tightness", f"{tight:.1f}% <= {tight_pct:.1f}%")

    # 5. EMA proximity
    ema_prox   = getattr(Config, "CONT_EMA_PROX", 0.05)
    ema_pct    = pct_from_ema(df)
    ema_ok2    = price_near_ema8(df, threshold=ema_prox)
    if not ema_ok2:
        return _fail("Price near 8 EMA", f"{ema_pct:.1f}% away > {ema_prox*100:.0f}% max")
    _pass("Price near 8 EMA", f"{ema_pct:.1f}% away")

    # 6. Breakout above 7-day high
    recent_high = df["high"].iloc[-8:-1].max()
    if last["close"] <= recent_high:
        return _fail("Close > 7-day high", f"close ${last['close']:.2f} <= high ${recent_high:.2f}")
    _pass("Close > 7-day high", f"${last['close']:.2f} > ${recent_high:.2f}")

    # 7. Volume surge
    min_vol = getattr(Config, "CONT_MIN_VOL", 1.5)
    if vol_r < min_vol:
        return _fail("Volume surge", f"{vol_r:.2f}x < {min_vol:.1f}x min")
    _pass("Volume surge", f"{vol_r:.2f}x >= {min_vol:.1f}x")

    entry = round(last["close"], 2)
    stop  = round(min(last["ema8"], df["low"].iloc[-3:].min()) * 0.995, 2)
    tp1, tp2 = _risk_targets(entry, stop)
    notes = f"Ignition {len(df)-ign_idx}d ago | Tight {tight:.1f}% | 8EMA {ema_pct:.1f}%"

    if log: log.signal_fired(entry, stop, tp1, tp2, notes)
    return {
        "symbol": symbol, "strategy": STRAT, "signal_type": "long",
        "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2,
        "volume_ratio": vol_r, "adr_pct": adr_pct(df), "notes": notes,
    }


# ─────────────────────────────────────────────
# 2. FLAT TOP BASE BREAKOUT
#    3-month flat resistance + ignition + low-vol flag
#    + 10AM volume anomaly (checked separately in intraday scan)
# ─────────────────────────────────────────────
def check_flat_top_breakout(symbol: str, df_raw) -> dict | None:
    STRAT = "Flat Top Base Breakout"
    if df_raw is None or len(df_raw) < 45:
        return None

    df    = add_emas(df_raw)
    last  = df.iloc[-1]
    vol_r = volume_ratio(df)
    log   = _dlog.get_logger()
    if log: log.start_symbol(symbol, STRAT)

    def _fail(label, detail=""):
        if log: log.check(label, False, detail); log.rejected()
        return None
    def _pass(label, detail=""):
        if log: log.check(label, True, detail)

    resist_tol = getattr(Config, "FLAT_RESIST_TOL", 0.015)
    resistance = find_flat_resistance(df, lookback=65, tolerance=resist_tol)
    if resistance is None:
        return _fail("Flat resistance exists", f"none found (tol {resist_tol*100:.1f}%)")
    _pass("Flat resistance", f"${resistance:.2f}")

    vol_slp = volume_slope(df, period=25)
    if vol_slp > 0:
        return _fail("Volume declining in base", f"slope {vol_slp:+.3f} > 0")
    _pass("Volume declining in base", f"slope {vol_slp:+.3f}")

    flat_break_min = getattr(Config, "FLAT_BREAK_MIN", 0.005)
    flat_min_vol   = getattr(Config, "FLAT_MIN_VOL", 1.8)
    if last["close"] <= resistance * (1 + flat_break_min):
        return _fail("Close > resistance", f"${last['close']:.2f} not >{flat_break_min*100:.1f}% above ${resistance:.2f}")
    _pass("Close > resistance", f"${last['close']:.2f}")
    if vol_r < flat_min_vol:
        return _fail("Volume on breakout", f"{vol_r:.2f}x < {flat_min_vol:.1f}x min")
    _pass("Volume on breakout", f"{vol_r:.2f}x")

    ign_idx = detect_ignition_candle(df, lookback=20)
    if ign_idx is None:
        return _fail("Ignition candle", "none in last 20 bars")
    _pass("Ignition candle", f"{len(df)-ign_idx}d ago")

    entry = round(last["close"], 2)
    stop  = round(resistance * 0.985, 2)
    tp1, tp2 = _risk_targets(entry, stop)
    notes = f"Resistance ${resistance:.2f} | Vol declining in base"

    if log: log.signal_fired(entry, stop, tp1, tp2, notes)
    return {
        "symbol": symbol, "strategy": STRAT, "signal_type": "long",
        "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2,
        "volume_ratio": vol_r, "adr_pct": adr_pct(df), "notes": notes,
    }


def check_flat_top_volume_anomaly(symbol: str, df_intraday) -> bool:
    """
    Intraday check: is 10AM volume > open bar volume?
    Returns True if volume anomaly detected (massive buying pressure).
    """
    if df_intraday is None or len(df_intraday) < 4:
        return False
    try:
        # Get the 9:30 open bar and ~10:00 bar
        bars = df_intraday.between_time("09:30", "10:15")
        if len(bars) < 3:
            return False
        open_vol  = bars.iloc[0]["volume"]   # 9:30 bar
        ten_am_vol = bars.iloc[2]["volume"]  # ~10:00 bar (3rd 5-min bar)
        return ten_am_vol > open_vol * 1.2   # 20% higher = anomaly
    except Exception:
        return False


# ─────────────────────────────────────────────
# 3. STAGE 2 BREAKOUT (Leading Sector)
#    Big base → ignition candle → low-vol consolidation
#    → tight daily pattern → volume breakout
# ─────────────────────────────────────────────
def check_stage2_breakout(symbol: str, df_raw) -> dict | None:
    STRAT = "Stage 2 Base Breakout"
    if df_raw is None or len(df_raw) < 60:
        return None

    df    = add_emas(df_raw)
    last  = df.iloc[-1]
    vol_r = volume_ratio(df)
    log   = _dlog.get_logger()
    if log: log.start_symbol(symbol, STRAT)

    def _fail(label, detail=""):
        if log: log.check(label, False, detail); log.rejected()
        return None
    def _pass(label, detail=""):
        if log: log.check(label, True, detail)

    base_range_pct = base_tightness(df, period=60)
    if base_range_pct > 60:
        return _fail("Big base (60d range < 60%)", f"{base_range_pct:.1f}% > 60%")
    _pass("Big base", f"{base_range_pct:.1f}% range")

    ign_vol_mult = getattr(Config, "S2_IGN_VOL_MULT", 2.5)
    ign_idx = detect_ignition_candle(df, lookback=25, vol_mult=ign_vol_mult)
    if ign_idx is None:
        return _fail("Ignition candle", f"none >{ign_vol_mult}x vol in last 25 bars")
    _pass("Ignition candle", f"{len(df)-ign_idx}d ago")

    if not low_vol_pullback_after(df, ign_idx, window=10):
        return _fail("Low-vol consolidation after ignition", "no quiet period found")
    _pass("Low-vol consolidation")

    tight    = base_tightness(df, period=5)
    s2_tight = getattr(Config, "S2_TIGHT_PCT", 10.0)
    if tight > s2_tight:
        return _fail("Recent 5-bar tightness", f"{tight:.1f}% > {s2_tight:.1f}% max")
    _pass("Recent 5-bar tightness", f"{tight:.1f}%")

    if not ema8_slope_positive(df, lookback=5):
        return _fail("8 EMA turning up", "slope flat/negative")
    _pass("8 EMA turning up")

    base_high = df["high"].iloc[-60:-1].max()
    if last["close"] < base_high * 0.995:
        return _fail("Close >= base high", f"${last['close']:.2f} < ${base_high:.2f}")
    _pass("Close >= base high", f"${last['close']:.2f}")

    s2_min_vol = getattr(Config, "S2_MIN_VOL", 2.0)
    if vol_r < s2_min_vol:
        return _fail("Volume on breakout", f"{vol_r:.2f}x < {s2_min_vol:.1f}x min")
    _pass("Volume on breakout", f"{vol_r:.2f}x")

    entry = round(last["close"], 2)
    stop  = round(df["low"].iloc[-5:].min() * 0.99, 2)
    tp1, tp2 = _risk_targets(entry, stop)
    notes = f"Big base {base_range_pct:.1f}% range | Ignition {len(df)-ign_idx}d ago"

    if log: log.signal_fired(entry, stop, tp1, tp2, notes)
    return {
        "symbol": symbol, "strategy": STRAT, "signal_type": "long",
        "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2,
        "volume_ratio": vol_r, "adr_pct": adr_pct(df), "notes": notes,
    }


# ─────────────────────────────────────────────
# 4. DOWNTREND TRENDLINE REVERSAL
#    Stage 4 → Stage 2: earnings/catalyst gap up,
#    EMA stack turning bullish, break above downtrend line
# ─────────────────────────────────────────────
def check_downtrend_reversal(symbol: str, df_raw) -> dict | None:
    STRAT = "Downtrend Trendline Reversal"
    if df_raw is None or len(df_raw) < 60:
        return None

    df    = add_emas(df_raw)
    last  = df.iloc[-1]
    vol_r = volume_ratio(df)
    log   = _dlog.get_logger()
    if log: log.start_symbol(symbol, STRAT)

    def _fail(label, detail=""):
        if log: log.check(label, False, detail); log.rejected()
        return None
    def _pass(label, detail=""):
        if log: log.check(label, True, detail)

    price_60d_ago = df["close"].iloc[-60]
    price_now     = last["close"]
    drop_pct      = (price_now / price_60d_ago - 1) * 100
    dt_min_drop   = getattr(Config, "DT_MIN_DROP", 0.85)
    if price_now > price_60d_ago * dt_min_drop:
        return _fail("Prior downtrend", f"only {drop_pct:.1f}% drop, need >{(1-dt_min_drop)*100:.0f}%")
    _pass("Prior downtrend", f"{drop_pct:.1f}% drop vs 60d ago")

    recent_tightness = base_tightness(df, period=20)
    if recent_tightness > 40:
        return _fail("Base forming (20d tightness < 40%)", f"{recent_tightness:.1f}%")
    _pass("Base forming", f"{recent_tightness:.1f}% tightness")

    if not ema8_slope_positive(df, lookback=5):
        return _fail("8 EMA turning up", "slope flat/negative")
    _pass("8 EMA turning up")

    tl = find_downtrend_trendline(df, lookback=60)
    if tl is None:
        return _fail("Descending trendline found", "not enough pivot highs")
    slope, intercept = tl
    tl_value_today   = trendline_value_at(slope, intercept, len(df) - 1)
    _pass("Descending trendline", f"@ ${tl_value_today:.2f}")

    if last["close"] < tl_value_today:
        return _fail("Close > trendline", f"${last['close']:.2f} < ${tl_value_today:.2f}")
    _pass("Close > trendline", f"${last['close']:.2f}")

    dt_min_vol = getattr(Config, "DT_MIN_VOL", 1.8)
    if vol_r < dt_min_vol:
        return _fail("Volume on breakout", f"{vol_r:.2f}x < {dt_min_vol:.1f}x min")
    _pass("Volume on breakout", f"{vol_r:.2f}x")

    entry = round(last["close"], 2)
    stop  = round(tl_value_today * 0.98, 2)
    tp1, tp2 = _risk_targets(entry, stop)
    notes = f"Trendline break @ ${tl_value_today:.2f} | {drop_pct:.0f}% drop vs 60d ago"

    if log: log.signal_fired(entry, stop, tp1, tp2, notes)
    return {
        "symbol": symbol, "strategy": STRAT, "signal_type": "long",
        "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2,
        "volume_ratio": vol_r, "adr_pct": adr_pct(df), "notes": notes,
    }


# ─────────────────────────────────────────────
# 5. DISTRIBUTION BASE BREAKDOWN (SHORT)
#    Huge base → distribution → trendline break
#    below EMAs with high volume → short
# ─────────────────────────────────────────────
def check_distribution_breakdown(symbol: str, df_raw) -> dict | None:
    STRAT = "Distribution Base Breakdown (SHORT)"
    if df_raw is None or len(df_raw) < 60:
        return None

    df    = add_emas(df_raw)
    last  = df.iloc[-1]
    vol_r = volume_ratio(df)
    log   = _dlog.get_logger()
    if log: log.start_symbol(symbol, STRAT)

    def _fail(label, detail=""):
        if log: log.check(label, False, detail); log.rejected()
        return None
    def _pass(label, detail=""):
        if log: log.check(label, True, detail)

    if last["close"] > last["ema8"] or last["close"] > last["ema21"]:
        return _fail("Price below 8 & 21 EMA",
                     f"close ${last['close']:.2f} ema8 ${last.get('ema8',0):.2f} ema21 ${last.get('ema21',0):.2f}")
    _pass("Price below 8 & 21 EMA")

    avg_vol_60  = df["volume"].iloc[-60:-30].mean()
    recent_vols = df["volume"].iloc[-30:]
    dist_mult   = getattr(Config, "DIST_DIST_MULT", 2.0)
    max_rv      = recent_vols.max()
    if max_rv < avg_vol_60 * dist_mult:
        return _fail("Distribution day", f"max vol {max_rv/avg_vol_60:.1f}x < {dist_mult:.1f}x avg")
    _pass("Distribution day", f"{max_rv/avg_vol_60:.1f}x volume spike")

    vol_slp = volume_slope(df, period=20)
    if vol_slp > 0:
        return _fail("Volume declining in base", f"slope {vol_slp:+.3f} > 0")
    _pass("Volume declining", f"slope {vol_slp:+.3f}")

    lows = df["low"].iloc[-30:].values
    x    = np.arange(len(lows))
    slope_lows, intercept_lows = np.polyfit(x, lows, 1)
    tl_support_today = trendline_value_at(slope_lows, intercept_lows, len(lows) - 1)
    _pass("Support trendline identified", f"@ ${tl_support_today:.2f}")

    if last["close"] > tl_support_today:
        return _fail("Close < support trendline", f"${last['close']:.2f} > ${tl_support_today:.2f}")
    _pass("Close < support trendline", f"${last['close']:.2f}")

    dist_min_vol = getattr(Config, "DIST_MIN_VOL", 1.5)
    if vol_r < dist_min_vol:
        return _fail("Volume on breakdown", f"{vol_r:.2f}x < {dist_min_vol:.1f}x min")
    _pass("Volume on breakdown", f"{vol_r:.2f}x")

    entry = round(last["close"], 2)
    stop  = round(max(last["high"], tl_support_today * 1.015), 2)
    tp1, tp2 = _risk_targets(entry, stop, is_short=True)
    notes = f"Below 8/21 EMA | Support break @ ${tl_support_today:.2f} | Stop ${stop}"

    if log: log.signal_fired(entry, stop, tp1, tp2, notes, direction="SHORT")
    return {
        "symbol": symbol, "strategy": STRAT, "signal_type": "short",
        "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2,
        "volume_ratio": vol_r, "adr_pct": adr_pct(df), "notes": notes,
    }


# ─────────────────────────────────────────────
# 6. ACCUMULATION BASE → SWING HIGH BREAKOUT
#    Volume declining into resistance,
#    8 EMA test, dual break (trendline + swing high)
# ─────────────────────────────────────────────
def check_accumulation_breakout(symbol: str, df_raw) -> dict | None:
    STRAT = "Accumulation Base Breakout"
    if df_raw is None or len(df_raw) < 50:
        return None

    df    = add_emas(df_raw)
    last  = df.iloc[-1]
    vol_r = volume_ratio(df)
    log   = _dlog.get_logger()
    if log: log.start_symbol(symbol, STRAT)

    def _fail(label, detail=""):
        if log: log.check(label, False, detail); log.rejected()
        return None
    def _pass(label, detail=""):
        if log: log.check(label, True, detail)

    swing_high = find_swing_high(df, lookback=60)
    if last["close"] < swing_high * 0.995:
        return _fail("Close >= swing high", f"${last['close']:.2f} < ${swing_high:.2f}")
    _pass("Close >= swing high", f"${swing_high:.2f}")

    vol_slp = volume_slope(df, period=20)
    if vol_slp > 0:
        return _fail("Volume declining into resistance", f"slope {vol_slp:+.3f} > 0")
    _pass("Volume declining", f"slope {vol_slp:+.3f}")

    tl = find_downtrend_trendline(df, lookback=40)
    if tl is None:
        return _fail("Descending trendline in base", "not enough pivot highs")
    slope, intercept = tl
    tl_value_today   = trendline_value_at(slope, intercept, len(df) - 1)
    _pass("Descending trendline", f"@ ${tl_value_today:.2f}")

    if last["close"] < tl_value_today:
        return _fail("Close > trendline (dual break)", f"${last['close']:.2f} < ${tl_value_today:.2f}")
    _pass("Close > trendline", f"${last['close']:.2f}")

    acc_min_vol = getattr(Config, "ACC_MIN_VOL", 2.0)
    if vol_r < acc_min_vol:
        return _fail("Volume on dual break", f"{vol_r:.2f}x < {acc_min_vol:.1f}x min")
    _pass("Volume on dual break", f"{vol_r:.2f}x")

    recent_lows = df["low"].iloc[-6:-1]
    ema8_recent = df["ema8"].iloc[-6:-1]
    ema_tested  = any(recent_lows.values[i] <= ema8_recent.values[i] * 1.01
                      for i in range(len(recent_lows)))
    if not ema_tested:
        return _fail("8 EMA tested in last 5 bars", "price never touched 8 EMA recently")
    _pass("8 EMA tested recently")

    entry = round(last["close"], 2)
    stop  = round(last["ema8"] * 0.995, 2)
    tp1, tp2 = _risk_targets(entry, stop)
    notes = f"Swing high ${swing_high:.2f} | Trendline ${tl_value_today:.2f} | Dual break"

    if log: log.signal_fired(entry, stop, tp1, tp2, notes)
    return {
        "symbol": symbol, "strategy": STRAT, "signal_type": "long",
        "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2,
        "volume_ratio": vol_r, "adr_pct": adr_pct(df), "notes": notes,
    }


# ─────────────────────────────────────────────
# MASTER SCANNER — run all strategies on one symbol
# ─────────────────────────────────────────────
def scan_symbol(symbol: str, df_daily, df_intraday=None) -> list[dict]:
    """
    Run all 6 strategy detectors on a symbol.
    Respects Config.STRATEGY_ENABLED (set by optimize_bot.py after backtest).
    Returns list of triggered signals (usually 0 or 1).
    """
    signals = []

    # Names MUST match the STRAT strings inside each detector (and the keys
    # in Config.STRATEGY_ENABLED) exactly, or optimizer settings are ignored.
    checkers = [
        ("Continuation Model (PRIMARY)",        check_continuation_model),
        ("Flat Top Base Breakout",              check_flat_top_breakout),
        ("Stage 2 Base Breakout",               check_stage2_breakout),
        ("Downtrend Trendline Reversal",        check_downtrend_reversal),
        ("Distribution Base Breakdown (SHORT)", check_distribution_breakdown),
        ("Accumulation Base Breakout",          check_accumulation_breakout),
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

    return signals