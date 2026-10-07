"""Settings, loaded from .env (never committed)."""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=True)   # .env wins over empty shell vars

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = os.getenv("OUTREACH_MODEL", "claude-sonnet-5-5")
HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "")

SENDER_NAME = os.getenv("SENDER_NAME", "Nilakshi Nagrale")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "nilakshii.nagrale@gmail.com")

DB_PATH = Path(os.getenv("OUTREACH_DB", ROOT / "data" / "outreach.db"))
APPLICATIONS_CSV = Path(os.getenv("APPLICATIONS_CSV", ROOT / "data" / "applications.csv"))
FACTS_PATH = Path(os.getenv("FACTS_PATH", ROOT / "profile" / "facts.md"))
# Where tailored resumes live: <RESUME_DIR>/<YYYY-MM-DD>/SDE_Nilakshi_Nagrale_<Company>.pdf
RESUME_DIR = Path(os.path.expanduser(os.getenv("RESUME_DIR", "~/Downloads/resume-toolkit/Job Applications")))

GMAIL_CREDENTIALS = ROOT / "credentials.json"   # OAuth client from Google Cloud console
GMAIL_TOKEN = ROOT / "token.json"               # created on first login

MAX_CONTACTS_PER_COMPANY = int(os.getenv("MAX_CONTACTS_PER_COMPANY", "2"))
FOLLOWUP_AFTER_BUSINESS_DAYS = int(os.getenv("FOLLOWUP_AFTER_BUSINESS_DAYS", "5"))
MAX_FOLLOWUPS = int(os.getenv("MAX_FOLLOWUPS", "1"))
MAX_AGENT_TURNS = int(os.getenv("MAX_AGENT_TURNS", "12"))
CONNECT_NOTE_LIMIT = 200
HUNTER_MIN_SCORE = int(os.getenv("HUNTER_MIN_SCORE", "90"))

# --- cost control -------------------------------------------------------------
MAX_SEARCHES_PER_COMPANY = int(os.getenv("MAX_SEARCHES_PER_COMPANY", "3"))
DAILY_BUDGET_USD = float(os.getenv("DAILY_BUDGET_USD", "1.00"))
# Estimates only - check the Pricing page in the Claude Console and update these.
PRICE_INPUT_PER_MTOK = float(os.getenv("PRICE_INPUT_PER_MTOK", "3"))
PRICE_OUTPUT_PER_MTOK = float(os.getenv("PRICE_OUTPUT_PER_MTOK", "15"))
PRICE_PER_SEARCH = float(os.getenv("PRICE_PER_SEARCH", "0.01"))

# --- hands-off mode -----------------------------------------------------------
# gmail_drafts: emails land in Gmail Drafts; YOU approve by pressing Send in Gmail.
# cli:          approve in the terminal with `python -m outreach review`.
APPROVAL_MODE = os.getenv("APPROVAL_MODE", "gmail_drafts")
SCAN_RESUME_DAYS = int(os.getenv("SCAN_RESUME_DAYS", "3"))   # auto-import from Job Applications/<date>/
