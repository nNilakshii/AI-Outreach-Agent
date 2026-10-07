"""Read a job posting's text from the board's public API (no scraping of logged-in pages)."""
import html
import re
import requests

UA = {"User-Agent": "outreach-agent/0.1"}


def _strip(h: str) -> str:
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h or "")
    h = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>", "\n", h)
    t = html.unescape(re.sub(r"<[^>]+>", " ", h))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", t)).strip()


def read_posting(url: str) -> dict:
    try:
        m = re.search(r"jobs\.ashbyhq\.com/([^/]+)/([0-9a-f-]{36})", url)
        if m:
            org, jid = m.groups()
            r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{org}", headers=UA, timeout=20).json()
            j = next((x for x in r.get("jobs", []) if x.get("id") == jid), None)
            if j:
                return {"ok": True, "title": j.get("title"), "location": j.get("location"),
                        "department": j.get("department"), "team": j.get("team"),
                        "text": (j.get("descriptionPlain") or "")[:8000]}
        m = re.search(r"jobs\.lever\.co/([^/]+)/([0-9a-f-]{36})", url)
        if m:
            org, jid = m.groups()
            j = requests.get(f"https://api.lever.co/v0/postings/{org}/{jid}?mode=json", headers=UA, timeout=20).json()
            lists = "\n".join(f"{l['text']}:\n{_strip(l['content'])}" for l in j.get("lists", []))
            return {"ok": True, "title": j.get("text"), "location": (j.get("categories") or {}).get("location"),
                    "team": (j.get("categories") or {}).get("team"),
                    "text": (j.get("descriptionPlain", "") + "\n" + lists)[:8000]}
        m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/?(?:jobs/|.*token=)(\d+)", url) \
            or re.search(r"gh_jid=(\d+)", url)
        if m and len(m.groups()) == 2:
            org, jid = m.groups()
            j = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{org}/jobs/{jid}", headers=UA, timeout=20).json()
            if j.get("title"):
                return {"ok": True, "title": j["title"], "location": (j.get("location") or {}).get("name"),
                        "departments": [d["name"] for d in j.get("departments", [])],
                        "text": _strip(html.unescape(j.get("content", "")))[:8000]}
        # Fallback: plain fetch
        r = requests.get(url, headers=UA, timeout=20)
        return {"ok": r.ok, "text": _strip(r.text)[:8000], "note": "generic fetch; may be incomplete"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}
