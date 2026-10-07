"""Client-side tools the agent can call, plus guardrails enforced in code (not just in the prompt)."""
import json
from datetime import datetime, timezone, date, timedelta
from . import config, db, hunter, postings

def server_tools():
    return [{"type": "web_search_20250305", "name": "web_search",
             "max_uses": config.MAX_SEARCHES_PER_COMPANY}]

CLIENT_TOOLS = [
    {"name": "get_state",
     "description": "Current record for this company: posting, contacts, drafted/sent messages, replies, events.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "read_posting",
     "description": "Fetch the job posting text (title, team, location, description) from the job board API.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "save_contact",
     "description": "Record a person to contact. linkedin_url must be a real linkedin.com/in/ URL seen in search results.",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string"}, "title": {"type": "string"},
         "linkedin_url": {"type": "string"},
         "role": {"type": "string", "enum": ["hiring_manager", "recruiter", "engineer"]},
         "why": {"type": "string", "description": "evidence they currently work there and own this team/req"}},
         "required": ["name", "title", "linkedin_url", "role", "why"]}},
    {"name": "find_email",
     "description": "Look up a VERIFIED work email via Hunter.io. Returns nothing if unverified.",
     "input_schema": {"type": "object", "properties": {
         "contact_id": {"type": "integer"}, "first_name": {"type": "string"},
         "last_name": {"type": "string"}, "domain": {"type": "string", "description": "e.g. rubrik.com"}},
         "required": ["contact_id", "first_name", "last_name", "domain"]}},
    {"name": "draft_message",
     "description": "Queue a message for her approval. Nothing is sent until she approves it.",
     "input_schema": {"type": "object", "properties": {
         "contact_id": {"type": "integer"},
         "channel": {"type": "string", "enum": ["email", "linkedin_connect", "linkedin_inmail"]},
         "kind": {"type": "string", "enum": ["initial", "followup", "reply"]},
         "subject": {"type": "string", "description": "email only"},
         "body": {"type": "string"}},
         "required": ["contact_id", "channel", "kind", "body"]}},
    {"name": "set_status",
     "description": "Finish this run for the company.",
     "input_schema": {"type": "object", "properties": {
         "status": {"type": "string", "enum": ["waiting", "done", "skipped"]},
         "note": {"type": "string"}}, "required": ["status", "note"]}},
]


def business_days_since(iso: str) -> int:
    start = datetime.fromisoformat(iso).date()
    d, n = start, 0
    while d < date.today():
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


class ToolRunner:
    def __init__(self, c, company_id):
        self.c, self.cid, self.finished = c, company_id, False

    def run(self, name, args):
        try:
            return getattr(self, "t_" + name)(**args)
        except Exception as e:  # noqa: BLE001 - report errors back to the model
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def t_get_state(self):
        return db.company_state(self.c, self.cid)

    def t_read_posting(self, url):
        return postings.read_posting(url)

    def t_save_contact(self, name, title, linkedin_url, role, why):
        if "linkedin.com/in/" not in linkedin_url:
            return {"ok": False, "error": "linkedin_url must be a linkedin.com/in/ profile URL"}
        n = self.c.execute("SELECT COUNT(*) FROM contacts WHERE company_id=?", (self.cid,)).fetchone()[0]
        if n >= config.MAX_CONTACTS_PER_COMPANY:
            return {"ok": False, "error": f"already {n} contacts (max {config.MAX_CONTACTS_PER_COMPANY})"}
        url = linkedin_url.split("?")[0].rstrip("/") + "/"
        cur = self.c.execute(
            "INSERT OR IGNORE INTO contacts(company_id,name,title,linkedin_url,role,why,created_at) "
            "VALUES(?,?,?,?,?,?,?)", (self.cid, name, title, url, role, why, db.now()))
        self.c.commit()
        db.log(self.c, self.cid, "contact", f"{name} ({title})")
        return {"ok": True, "contact_id": cur.lastrowid}

    def t_find_email(self, contact_id, first_name, last_name, domain):
        res = hunter.find_email(first_name, last_name, domain)
        if res.get("ok"):
            self.c.execute("UPDATE contacts SET email=?, email_source=? WHERE id=? AND company_id=?",
                           (res["email"], f"hunter score={res['score']} {res['verification']}", contact_id, self.cid))
            self.c.commit()
        return res

    def t_draft_message(self, contact_id, channel, kind, body, subject=""):
        ct = self.c.execute("SELECT * FROM contacts WHERE id=? AND company_id=?", (contact_id, self.cid)).fetchone()
        if not ct:
            return {"ok": False, "error": "unknown contact_id for this company"}
        problems = []
        if "—" in body or "—" in subject:
            problems.append("contains an em dash - use hyphens")
        if channel == "linkedin_connect" and len(body) > config.CONNECT_NOTE_LIMIT:
            problems.append(f"connect note is {len(body)} chars (max {config.CONNECT_NOTE_LIMIT})")
        if channel == "linkedin_connect" and any(ch.isdigit() for ch in body):
            problems.append("connect notes must not contain numbers or metrics - make it human")
        if channel == "email" and kind == "initial" and sum(any(ch.isdigit() for ch in w) for w in body.split()) > 2:
            problems.append("too many numbers - at most one metric in the whole email")
        if channel == "email" and not ct["email"]:
            problems.append("no verified email for this contact - use linkedin_connect")
        if channel == "email" and kind == "initial" and not subject:
            problems.append("email needs a subject")
        for bad in (" solo", " alone"):
            if bad in body.lower():
                problems.append(f"never write '{bad.strip()}'")
        prev = [dict(m) for m in self.c.execute(
            "SELECT * FROM messages WHERE contact_id=? ORDER BY id", (contact_id,))]
        if kind == "initial" and any(m["kind"] == "initial" and m["status"] != "rejected_by_user" for m in prev):
            problems.append("an initial message already exists for this contact")
        if kind == "followup":
            sent = [m for m in prev if m["status"] == "sent"]
            fups = [m for m in prev if m["kind"] == "followup" and m["status"] != "rejected_by_user"]
            if not sent:
                problems.append("nothing sent yet - no follow-up")
            elif business_days_since(sent[-1]["sent_at"]) < config.FOLLOWUP_AFTER_BUSINESS_DAYS:
                problems.append("too early for a follow-up")
            if len(fups) >= config.MAX_FOLLOWUPS:
                problems.append("follow-up limit reached - stop")
        if problems:
            return {"ok": False, "fix": problems}
        thread = None
        if channel == "email" and kind in ("followup", "reply"):
            last = next((m for m in reversed(prev) if m["gmail_thread_id"]), None)
            if last:
                thread = last["gmail_thread_id"]
                base = last["subject"] or ""
                subject = subject or (base if base.lower().startswith("re:") else f"Re: {base}")
        cur = self.c.execute(
            "INSERT INTO messages(contact_id,channel,kind,subject,body,gmail_thread_id,created_at) VALUES(?,?,?,?,?,?,?)",
            (contact_id, channel, kind, subject, body, thread, db.now()))
        self.c.commit()
        db.log(self.c, self.cid, "draft", f"{kind} {channel} -> {ct['name']}")
        return {"ok": True, "message_id": cur.lastrowid, "status": "awaiting her approval"}

    def t_set_status(self, status, note):
        self.c.execute("UPDATE companies SET status=?, notes=?, updated_at=? WHERE id=?",
                       (status, note, db.now(), self.cid))
        self.c.commit()
        db.log(self.c, self.cid, "status", f"{status}: {note}")
        self.finished = True
        return {"ok": True}


def as_result(tool_use_id, payload):
    return {"type": "tool_result", "tool_use_id": tool_use_id,
            "content": json.dumps(payload, default=str)[:12000]}
