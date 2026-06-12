"""
Trading Bot Setup Wizard
Opens Alpaca + Discord in your browser, collects your keys,
writes .env, installs packages, and launches the bot.
"""
import os
import subprocess
import sys
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, ".env")
REQ_FILE = os.path.join(BASE_DIR, "requirements.txt")


# ── Colour palette ────────────────────────────────────────────────────────────
BG       = "#0d0f14"
CARD     = "#161a23"
ACCENT   = "#00c805"
RED      = "#ff3b30"
TEXT     = "#e8eaed"
SUBTEXT  = "#8a8d96"
BORDER   = "#2a2d36"
BTN_BG   = "#1e2330"
BTN_HOV  = "#252b3b"


class SetupWizard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Trading Bot — Setup Wizard")
        self.geometry("720x780")
        self.resizable(False, False)
        self.configure(bg=BG)

        # Center window
        self.update_idletasks()
        x = (self.winfo_screenwidth()  - 720) // 2
        y = (self.winfo_screenheight() - 780) // 2
        self.geometry(f"+{x}+{y}")

        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill="x", padx=30, pady=(28, 0))
        tk.Label(hdr, text="⚡  Trading Bot Setup", font=("Segoe UI", 22, "bold"),
                 bg=BG, fg=TEXT).pack(anchor="w")
        tk.Label(hdr, text="Follow the 4 steps below — takes about 3 minutes.",
                 font=("Segoe UI", 11), bg=BG, fg=SUBTEXT).pack(anchor="w", pady=(4, 0))

        sep = tk.Frame(self, bg=BORDER, height=1)
        sep.pack(fill="x", padx=30, pady=18)

        # ── STEP 1: Alpaca ────────────────────────────────────────────────────
        self._section("STEP 1 — Get your Alpaca Paper API Keys",
                       "Click the button to open Alpaca. Log in → top-right avatar → "
                       "Paper Trading → API Keys → Generate New Key.\n"
                       "Paste both keys below.")

        self._open_btn("🔑  Open Alpaca Paper Dashboard",
                        "https://app.alpaca.markets/paper-trading/overview")

        self.alpaca_key    = self._field("API Key ID",     show="")
        self.alpaca_secret = self._field("Secret Key",     show="*")

        sep2 = tk.Frame(self, bg=BORDER, height=1)
        sep2.pack(fill="x", padx=30, pady=18)

        # ── STEP 2: Discord ───────────────────────────────────────────────────
        self._section("STEP 2 — Create a Discord Webhook",
                       "Click to open Discord. Choose (or create) a channel for alerts.\n"
                       "Right-click channel → Edit Channel → Integrations → Webhooks "
                       "→ New Webhook → Copy URL.  Paste below.")

        self._open_btn("💬  Open Discord", "https://discord.com/channels/@me")

        self.discord_url = self._field("Discord Webhook URL", show="")

        sep3 = tk.Frame(self, bg=BORDER, height=1)
        sep3.pack(fill="x", padx=30, pady=18)

        # ── STEP 3 + 4: Launch ────────────────────────────────────────────────
        self._section("STEP 3 — Install & Launch",
                       "Click the green button. The wizard will write your .env file,\n"
                       "install Python packages, and start the bot automatically.")

        launch_btn = tk.Button(
            self, text="🚀  Install Packages & Launch Bot",
            font=("Segoe UI", 13, "bold"),
            bg=ACCENT, fg="#000000", activebackground="#00a804",
            relief="flat", cursor="hand2", padx=20, pady=12,
            command=self._launch,
        )
        launch_btn.pack(padx=30, pady=(0, 14), fill="x")

        # Log output
        self.log = scrolledtext.ScrolledText(
            self, height=8, bg=CARD, fg=ACCENT,
            font=("Consolas", 10), relief="flat",
            insertbackground=ACCENT, state="disabled",
            bd=0, highlightthickness=1, highlightbackground=BORDER,
        )
        self.log.pack(padx=30, pady=(0, 20), fill="both")

    def _section(self, title: str, subtitle: str):
        f = tk.Frame(self, bg=BG)
        f.pack(fill="x", padx=30, pady=(0, 6))
        tk.Label(f, text=title, font=("Segoe UI", 12, "bold"),
                 bg=BG, fg=TEXT).pack(anchor="w")
        tk.Label(f, text=subtitle, font=("Segoe UI", 10),
                 bg=BG, fg=SUBTEXT, justify="left").pack(anchor="w", pady=(3, 0))

    def _open_btn(self, label: str, url: str):
        btn = tk.Button(
            self, text=label,
            font=("Segoe UI", 11),
            bg=BTN_BG, fg=TEXT, activebackground=BTN_HOV,
            relief="flat", cursor="hand2", padx=14, pady=9,
            command=lambda: webbrowser.open(url),
        )
        btn.pack(padx=30, pady=(8, 10), anchor="w")

    def _field(self, label: str, show: str = "") -> tk.Entry:
        f = tk.Frame(self, bg=BG)
        f.pack(fill="x", padx=30, pady=(0, 8))
        tk.Label(f, text=label, font=("Segoe UI", 10),
                 bg=BG, fg=SUBTEXT).pack(anchor="w")
        entry = tk.Entry(
            f, show=show,
            font=("Consolas", 11), bg=CARD, fg=TEXT,
            insertbackground=TEXT, relief="flat",
            highlightthickness=1, highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        entry.pack(fill="x", ipady=8, pady=(3, 0))
        return entry

    # ── Logic ─────────────────────────────────────────────────────────────────
    def _log(self, msg: str):
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.config(state="disabled")
        self.update()

    def _launch(self):
        key    = self.alpaca_key.get().strip()
        secret = self.alpaca_secret.get().strip()
        durl   = self.discord_url.get().strip()

        if not key or not secret:
            messagebox.showerror("Missing", "Please paste your Alpaca API Key ID and Secret Key.")
            return
        if not durl.startswith("https://discord.com/api/webhooks/"):
            messagebox.showerror("Missing / Wrong",
                "Discord URL should start with:\nhttps://discord.com/api/webhooks/...")
            return

        threading.Thread(target=self._run_setup,
                         args=(key, secret, durl), daemon=True).start()

    def _run_setup(self, key: str, secret: str, durl: str):
        # 1. Write .env
        self._log("📝 Writing .env file...")
        env_content = (
            f"ALPACA_API_KEY={key}\n"
            f"ALPACA_SECRET_KEY={secret}\n"
            f"ALPACA_BASE_URL=https://paper-api.alpaca.markets\n"
            f"DISCORD_WEBHOOK_URL={durl}\n"
            f"RISK_PCT=0.01\n"
            f"MAX_POSITION_PCT=0.10\n"
            f"MIN_PRICE=5.0\n"
            f"MIN_AVG_VOLUME=500000\n"
            f"MIN_ADR_PCT=3.0\n"
        )
        with open(ENV_FILE, "w") as f:
            f.write(env_content)
        self._log("✅ .env saved.")

        # 2. Install dependencies
        self._log("📦 Installing Python packages (this may take ~30 seconds)...")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", REQ_FILE, "--quiet"],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                self._log("⚠️  pip output:\n" + result.stderr[:500])
            else:
                self._log("✅ Packages installed.")
        except subprocess.TimeoutExpired:
            self._log("⚠️  pip timed out. Trying to continue anyway...")

        # 3. Launch bot in a new Command Prompt window
        self._log("🚀 Launching bot...")
        main_py = os.path.join(BASE_DIR, "main.py")
        try:
            subprocess.Popen(
                f'start cmd /k "cd /d "{BASE_DIR}" && python main.py"',
                shell=True,
            )
            self._log("")
            self._log("✅ Bot is running in a new window!")
            self._log("")
            self._log("📋 What happens next:")
            self._log("   09:15 ET  — Morning scan fires on all symbols")
            self._log("   10:05 ET  — Intraday flat-top volume check")
            self._log("   Every 5m  — Monitors open positions for TP/SL")
            self._log("   16:15 ET  — End-of-day summary to Discord")
            self._log("")
            self._log("💬 All alerts will appear in your Discord channel.")
            self._log("   Close the bot window to stop it at any time.")
        except Exception as e:
            self._log(f"❌ Could not launch: {e}")
            self._log(f"   Manually run:  python \"{main_py}\"")


if __name__ == "__main__":
    app = SetupWizard()
    app.mainloop()
