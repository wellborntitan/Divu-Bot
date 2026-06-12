"""
Technical indicator calculations.
All functions operate on pandas Series or DataFrames.
"""
import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def adr_pct(df: pd.DataFrame, period: int = 20) -> float:
    """Average Daily Range as % of close (last `period` days)."""
    recent = df.tail(period)
    avg_range = (recent["high"] - recent["low"]).mean()
    avg_close = recent["close"].mean()
    return round((avg_range / avg_close) * 100, 2)


def volume_ratio(df: pd.DataFrame, period: int = 20) -> float:
    """Latest volume vs N-day average."""
    avg = df["volume"].iloc[-period-1:-1].mean()
    if avg == 0:
        return 0.0
    return round(df["volume"].iloc[-1] / avg, 2)


def volume_slope(df: pd.DataFrame, period: int = 20) -> float:
    """
    Linear regression slope of volume over last `period` days.
    Negative = declining volume (good for base detection).
    """
    vols = df["volume"].iloc[-period:].values
    x = np.arange(len(vols))
    if len(vols) < 3:
        return 0.0
    slope = np.polyfit(x, vols, 1)[0]
    return float(slope)


def add_emas(df: pd.DataFrame) -> pd.DataFrame:
    """Add EMA 8, 21, 50, 200 columns to dataframe."""
    df = df.copy()
    df["ema8"]  = ema(df["close"], 8)
    df["ema21"] = ema(df["close"], 21)
    df["ema50"] = ema(df["close"], 50)
    df["ema200"]= ema(df["close"], 200)
    return df


def is_ema_bullish_stack(df: pd.DataFrame) -> bool:
    """8 EMA > 21 EMA > 50 EMA on last bar."""
    last = df.iloc[-1]
    return last["ema8"] > last["ema21"] > last["ema50"]


def ema8_slope_positive(df: pd.DataFrame, lookback: int = 5) -> bool:
    """8 EMA has been rising for last N bars."""
    return df["ema8"].iloc[-1] > df["ema8"].iloc[-lookback]


def price_above_ema(df: pd.DataFrame, ema_col: str = "ema50") -> bool:
    return df["close"].iloc[-1] > df[ema_col].iloc[-1]


def pct_from_ema(df: pd.DataFrame, ema_col: str = "ema8") -> float:
    """% distance of current close from an EMA."""
    last = df.iloc[-1]
    return round(((last["close"] - last[ema_col]) / last[ema_col]) * 100, 2)


def base_tightness(df: pd.DataFrame, period: int = 10) -> float:
    """
    Measure how tight the last `period` bars are.
    Returns the price range as % of close. Lower = tighter.
    """
    recent = df.tail(period)
    rng = recent["high"].max() - recent["low"].min()
    mid = recent["close"].mean()
    return round((rng / mid) * 100, 2)


def find_flat_resistance(df: pd.DataFrame, lookback: int = 60,
                          tolerance: float = 0.015) -> float | None:
    """
    Find a flat horizontal resistance level that price has tested 3+ times.
    Returns the resistance price or None.
    """
    highs = df["high"].iloc[-lookback:].values
    # Cluster highs within tolerance
    sorted_highs = np.sort(highs)[::-1]
    for candidate in sorted_highs:
        touches = np.sum(np.abs(highs - candidate) / candidate < tolerance)
        if touches >= 3:
            return round(float(candidate), 2)
    return None


def find_swing_high(df: pd.DataFrame, lookback: int = 60) -> float:
    """The most recent significant swing high."""
    return float(df["high"].iloc[-lookback:].max())


def find_downtrend_trendline(df: pd.DataFrame,
                              lookback: int = 60) -> tuple[float, float] | None:
    """
    Fit a descending trendline to the recent lower highs.
    Returns (slope, intercept) of the line, or None if not a downtrend.
    """
    highs = df["high"].iloc[-lookback:].values
    x = np.arange(len(highs))
    slope, intercept = np.polyfit(x, highs, 1)
    # Only return if slope is negative (actual downtrend)
    if slope < 0:
        return (float(slope), float(intercept))
    return None


def trendline_value_at(slope: float, intercept: float, idx: int) -> float:
    """Price on the trendline at bar index `idx`."""
    return slope * idx + intercept


def detect_ignition_candle(df: pd.DataFrame, lookback: int = 20,
                             vol_mult: float = 2.0,
                             body_pct_min: float = 0.02) -> int | None:
    """
    Find the index of an ignition candle within the last `lookback` bars.
    An ignition candle is:
      - Volume >= vol_mult * 20-day avg
      - Bullish (close > open)
      - Body >= body_pct_min of close price
    Returns the bar index relative to the full df, or None.
    """
    avg_vol = df["volume"].iloc[-lookback-20:-lookback].mean()
    search = df.iloc[-lookback:]
    for i in range(len(search) - 1, -1, -1):  # Search from most recent
        row = search.iloc[i]
        body = row["close"] - row["open"]
        body_pct = body / row["close"] if row["close"] > 0 else 0
        if (row["volume"] >= avg_vol * vol_mult and
                row["close"] > row["open"] and
                body_pct >= body_pct_min):
            return len(df) - lookback + i
    return None


def low_vol_pullback_after(df: pd.DataFrame, ignition_idx: int,
                            window: int = 7) -> bool:
    """
    Check if volume declined (low-vol pullback) in the bars after ignition.
    """
    if ignition_idx is None:
        return False
    after = df.iloc[ignition_idx + 1 : ignition_idx + 1 + window]
    if len(after) < 2:
        return False
    ignition_vol = df["volume"].iloc[ignition_idx]
    avg_after_vol = after["volume"].mean()
    return avg_after_vol < ignition_vol * 0.65  # At least 35% lower than ignition


def price_near_ema8(df: pd.DataFrame, threshold: float = 0.04) -> bool:
    """Price within `threshold`% of 8 EMA."""
    return abs(pct_from_ema(df, "ema8")) < threshold * 100
