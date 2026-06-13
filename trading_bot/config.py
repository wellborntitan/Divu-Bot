import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Alpaca
    ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY", "")
    ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
    ALPACA_BASE_URL   = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    PAPER_TRADING     = "paper" in os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

    # Discord
    DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

    # Risk
    RISK_PCT         = float(os.getenv("RISK_PCT", "0.01"))   # 1% account risk per trade
    MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.10"))

    # Scanner filters
    MIN_PRICE      = float(os.getenv("MIN_PRICE", "5.0"))
    MIN_AVG_VOLUME = int(os.getenv("MIN_AVG_VOLUME", "500000"))
    MIN_ADR_PCT    = float(os.getenv("MIN_ADR_PCT", "3.0"))

    # How many bars to fetch for pattern detection
    LOOKBACK_DAYS = 90

    # Trim schedule (fractions of original shares to sell at each target)
    TRIM_TP1 = 0.25   # Sell 25% at TP1
    TRIM_TP2 = 0.50   # Sell 50% at TP2 (of original)
    # Remaining 25% trails the 8 EMA

    # Don't re-enter the same symbol+strategy within this many days
    # (must match the backtest so live behavior == tested behavior)
    COOLDOWN_DAYS = 15

    # Reject any signal whose stop is further than this % from entry.
    # Far-away stops (e.g. regression trendlines after a sharp move) make
    # R-multiples meaningless and turn trades into unmanaged buy-and-hold.
    MAX_STOP_PCT = 12.0

    # ── Per-detector thresholds (tuned by parameter_optimizer.py / optimize_bot.py)
    # These replace hardcoded values inside pattern_detector.py.

    # Continuation Model
    CONT_TIGHT_PCT    = 8.0    # max base tightness % (lower = stricter)
    CONT_MIN_VOL      = 1.5    # min volume ratio on breakout day
    CONT_EMA_PROX     = 0.05   # max % price can be above 8 EMA
    CONT_IGN_LOOKBACK = 30     # bars to look back for ignition candle
    CONT_LVP_WINDOW   = 8      # bars after ignition to check for low-vol pullback

    # Flat Top Breakout
    FLAT_RESIST_TOL   = 0.015  # % tolerance to define flat resistance
    FLAT_MIN_VOL      = 1.8    # min volume ratio on breakout day
    FLAT_BREAK_MIN    = 0.005  # min % close must clear resistance by

    # Stage 2 Breakout
    S2_TIGHT_PCT      = 10.0   # max tightness of recent 5-bar pattern
    S2_MIN_VOL        = 2.0    # min volume ratio
    S2_IGN_VOL_MULT   = 2.5    # ignition candle volume multiple

    # Downtrend Reversal — re-enabled with stricter criteria (2026-06-13)
    # Entry now requires: tight base ≤25%, 8 EMA crossed above 21 EMA,
    # and 2.5x volume. These changes replace the loose 40% base / slope-only EMA
    # checks that generated 254 garbage signals at 37% win rate.
    DT_MIN_DROP       = 0.85   # price_now must be < price_60d_ago x this
    DT_MIN_VOL        = 2.5    # min volume ratio (raised from 1.8 — need conviction)

    # Distribution Breakdown (Short)
    DIST_DIST_MULT    = 2.0    # distribution candle must be > avg_vol x this
    DIST_MIN_VOL      = 1.5    # min volume ratio

    # Accumulation Breakout
    ACC_MIN_VOL       = 2.0    # min volume ratio

    # Global R targets (applied to all strategies)
    RR1               = 1.0    # first take-profit R multiple
    RR2               = 2.0    # second take-profit R multiple

    # ── Strategy tuning (auto-updated by optimize_bot.py after each backtest)
    # Keys must match sig["strategy"] returned by each detector exactly.
    # Backtest results (2026-06-13):
    #   Continuation Model:      61.9% win, PF 1.99 — primary edge, keep
    #   Stage 2 Base Breakout:   50% win, PF 1.01 — small sample, keep for more data
    #   Accumulation Breakout:   100% win, PF 999 — too few, keep
    #   Downtrend Reversal:      37.4% win at PF 0.88 with old criteria → re-written
    #     New criteria: tight base ≤25%, 8 EMA must cross 21 EMA, 2.5x vol
    #     Re-enabled — backtest will confirm the tighter version's edge
    #   Flat Top / Distribution Breakdown: disabled (low sample + poor PF)
    STRATEGY_ENABLED = {
        "Continuation Model (PRIMARY)":        True,
        "Flat Top Base Breakout":              False,   # disabled: insufficient edge in backtest
        "Stage 2 Base Breakout":               True,
        "Downtrend Trendline Reversal":        True,    # re-enabled with stricter criteria
        "Distribution Base Breakdown (SHORT)": False,   # disabled: insufficient edge
        "Accumulation Base Breakout":          True,
    }
    STRATEGY_MIN_VOL_RATIO = {
        "Continuation Model (PRIMARY)":        0,       # threshold handled inside detector
        "Flat Top Base Breakout":              0,
        "Stage 2 Base Breakout":               2.5,     # raised by optimizer
        "Downtrend Trendline Reversal":        0,
        "Distribution Base Breakdown (SHORT)": 0,
        "Accumulation Base Breakout":          0,
    }
    STRATEGY_RR2 = {
        "Continuation Model (PRIMARY)":        2.0,
        "Flat Top Base Breakout":              2.0,
        "Stage 2 Base Breakout":               1.5,    # lowered by optimizer
        "Downtrend Trendline Reversal":        1.5,    # lowered by optimizer
        "Distribution Base Breakdown (SHORT)": 1.5,
        "Accumulation Base Breakout":          2.0,
    }

    # Core watchlist — leading momentum stocks in active sectors
    # Expanded dynamically each morning with top movers
    BASE_WATCHLIST = [
        # Quantum
        "RGTI", "QBTS", "IONQ", "QUBT", "ARQQ",
        # AI / Semiconductors
        "NVDA", "AMD", "AVGO", "SMCI", "ARM", "MRVL", "ANET",
        # Mega-cap momentum
        "TSLA", "META", "AMZN", "GOOGL", "MSFT", "AAPL",
        # High-growth / fintech
        "HOOD", "RDDT", "SHOP", "COIN", "MSTR", "APP",
        # Energy (nuclear)
        "SMR", "OKLO", "NNE", "BWXT",
        # Space / defense
        "RKLB", "ASTS", "PL",
        # Crypto miners
        "RIOT", "MARA", "WULF", "CLSK", "CIFR",
        # Biotech momentum
        "RXRX", "SOUN", "BBAI",
        # ETFs for sector reference
        "QQQ", "SPY", "IWM",
        # S&P 500 liquid large-caps
        "JPM", "GS", "MS", "BAC", "V", "MA", "PYPL",
        "NFLX", "DIS", "CRM", "NOW", "SNOW", "PLTR", "NET",
        "UBER", "LYFT", "ABNB", "DASH",
        "XOM", "CVX", "OXY", "SLB",
        "LMT", "RTX", "NOC", "BA",
        "UNH", "LLY", "PFE", "MRNA", "BNTX",
    ]
