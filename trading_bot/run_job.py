#!/usr/bin/env python3
"""
run_job.py -- Single-job runner for GitHub Actions
====================================================
Runs exactly one bot job then exits.  GitHub Actions handles scheduling.

Usage:
    python run_job.py morning_scan
    python run_job.py intraday_scan
    python run_job.py monitor_positions
    python run_job.py eod_summary
"""
import os
import sys
from pathlib import Path

# Load .env when running locally
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

import decision_logger as _dlog

JOB = sys.argv[1].lower() if len(sys.argv) > 1 else ""

if not JOB:
    print("Usage: python run_job.py <morning_scan|intraday_scan|monitor_positions|eod_summary>")
    sys.exit(1)

# Initialise decision logger (same as live bot)
_dlog.set_logger(_dlog.DecisionLogger(mode="bot"))

print(f"[run_job] Starting: {JOB}")

if JOB == "morning_scan":
    from main import morning_scan
    morning_scan()

elif JOB == "intraday_scan":
    from main import intraday_scan
    intraday_scan()

elif JOB == "monitor_positions":
    from main import monitor_positions
    monitor_positions()

elif JOB == "eod_summary":
    from main import eod_summary
    eod_summary()

elif JOB == "session_loop":
    from main import session_loop
    session_loop()

else:
    print(f"Unknown job: {JOB!r}")
    print("Valid: morning_scan  intraday_scan  monitor_positions  eod_summary  session_loop")
    sys.exit(1)

print(f"[run_job] Done: {JOB}")
