"""SQLite state: the agent's memory across runs."""
import sqlite3
from datetime import datetime, timezone
from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  title TEXT,
  url TEXT UNIQUE,
  applied_on TEXT,
  status TEXT DEFAULT 'new',          -- new | in_progress | waiting | done | skipped
  notes TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY,
  company_id INTEGER REFERENCES companies(id),
  name TEXT, title TEXT, linkedin_url TEXT, email TEXT,
  email_source TEXT, role TEXT,        -- role: hiring_manager | recruiter | engineer
  why TEXT, created_at TEXT,
  UNIQUE(company_id, linkedin_url)
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  contact_id INTEGER REFERENCES contacts(id),
  channel TEXT,                        -- email | linkedin_connect | linkedin_inmail
  kind TEXT DEFAULT 'initial',         -- initial | followup | reply
  subject TEXT, body TEXT,
  status TEXT DEFAULT 'drafted',       -- drafted | approved | sent | rejected_by_user | replied
  gmail_draft_id TEXT, gmail_thread_id TEXT,
  created_at TEXT, sent_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, company_id INTEGER, day TEXT,
  input INTEGER, output INTEGER, cache_read INTEGER, cache_write INTEGER,
  searches INTEGER, cost_usd REAL, at TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  company_id INTEGER, kind TEXT, detail TEXT, at TEXT
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def log(c, company_id, kind, detail):
    c.execute("INSERT INTO events(company_id,kind,detail,at) VALUES(?,?,?,?)",
              (company_id, kind, detail, now()))
    c.commit()


def company_state(c, company_id):
    """Everything the agent needs to know about one company, as plain dicts."""
    co = dict(c.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone())
    contacts = []
    for ct in c.execute("SELECT * FROM contacts WHERE company_id=?", (company_id,)):
        ct = dict(ct)
        ct["messages"] = [dict(m) for m in c.execute(
            "SELECT id,channel,kind,subject,body,status,created_at,sent_at FROM messages "
            "WHERE contact_id=? ORDER BY id", (ct["id"],))]
        contacts.append(ct)
    co["contacts"] = contacts
    co["events"] = [dict(e) for e in c.execute(
        "SELECT kind,detail,at FROM events WHERE company_id=? ORDER BY id DESC LIMIT 15", (company_id,))]
    return co
