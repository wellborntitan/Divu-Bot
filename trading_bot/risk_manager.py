"""
Position sizing — 1% account risk per trade.

Shares = (account_equity * RISK_PCT) / (entry - stop)
Capped at MAX_POSITION_PCT of account.
"""
import math
from config import Config


def calculate_shares(entry: float, stop: float, account_equity: float,
                      is_short: bool = False) -> int:
    """
    Return the number of shares to trade given entry/stop and account size.
    """
    risk_per_trade = account_equity * Config.RISK_PCT
    stop_distance  = abs(entry - stop)

    if stop_distance == 0:
        return 0

    raw_shares = risk_per_trade / stop_distance

    # Cap at MAX_POSITION_PCT of account
    max_shares = (account_equity * Config.MAX_POSITION_PCT) / entry

    shares = min(raw_shares, max_shares)
    return max(1, math.floor(shares))


def calculate_dollar_risk(shares: int, entry: float, stop: float) -> float:
    return round(abs(entry - stop) * shares, 2)


def trim_shares(original_shares: int, trim_fraction: float) -> int:
    """How many shares to sell at a trim target."""
    return max(1, round(original_shares * trim_fraction))
