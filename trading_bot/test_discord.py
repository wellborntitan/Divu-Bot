import requests

import os
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass
WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "")

payload = {
    "embeds": [{
        "title": "Trading Bot - Test Alert",
        "description": (
            "Discord alerts are working!\n\n"
            "You will receive alerts here for:\n"
            "- New trade entries (Entry / TP1 / TP2 / Stop)\n"
            "- Take profit hits\n"
            "- Stop loss hits\n"
            "- End of day summaries"
        ),
        "color": 52480,
        "footer": {"text": "Paper Trading | Risk 1% per trade"}
    }]
}

r = requests.post(WEBHOOK, json=payload)
if r.status_code in (200, 204):
    print("SUCCESS - Check your Discord channel!")
else:
    print(f"ERROR {r.status_code}: {r.text}")

input("Press Enter to close...")
