"""Hunter.io email finder. Returns an address only when Hunter verifies it - never a guess."""
import requests
from . import config

BASE = "https://api.hunter.io/v2"


def find_email(first_name: str, last_name: str, domain: str) -> dict:
    if not config.HUNTER_API_KEY:
        return {"ok": False, "reason": "HUNTER_API_KEY not set - use LinkedIn instead"}
    r = requests.get(f"{BASE}/email-finder", timeout=20, params={
        "domain": domain, "first_name": first_name, "last_name": last_name,
        "api_key": config.HUNTER_API_KEY})
    if r.status_code != 200:
        return {"ok": False, "reason": f"hunter http {r.status_code}: {r.text[:200]}"}
    d = (r.json() or {}).get("data") or {}
    email, score = d.get("email"), d.get("score") or 0
    status = ((d.get("verification") or {}).get("status")) or "unknown"
    if not email:
        return {"ok": False, "reason": "no email found"}
    if status != "valid" and score < config.HUNTER_MIN_SCORE:
        # Treat unverified / low-confidence as NOT found: guessed addresses hurt deliverability.
        return {"ok": False, "reason": f"unverified (status={status}, score={score}) - not using it"}
    return {"ok": True, "email": email, "score": score, "verification": status}
