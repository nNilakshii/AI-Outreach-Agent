"""Commands: auto | import | run | replies | review | status | note | daily."""
import argparse
import csv
import re
import subprocess
import sys
import webbrowser
from datetime import date, timedelta
from . import config, db, gmail
from .tools import business_days_since


def cmd_import(args):
    c = db.conn()
    n = 0
    with open(args.csv or config.APPLICATIONS_CSV, newline="") as f:
        for row in csv.DictReader(f):
            cur = c.execute("INSERT OR IGNORE INTO companies(name,title,url,applied_on,updated_at) VALUES(?,?,?,?,?)",
                            (row["company"].strip(), row.get("title", "").strip(), row["url"].strip(),
                             row.get("applied_on", "").strip(), db.now()))
            n += cur.rowcount
    c.commit()
    print(f"imported {n} new application(s)")


def _due(c, co):
    """Does this company need the agent right now?"""
    if co["status"] in ("new", "in_progress"):
        return True, "new application"
    if co["status"] != "waiting":
        return False, ""
    rows = c.execute("""SELECT m.* FROM messages m JOIN contacts ct ON ct.id=m.contact_id
                        WHERE ct.company_id=? ORDER BY m.id""", (co["id"],)).fetchall()
    if any(m["status"] == "drafted" for m in rows):
        return False, ""                                   # waiting on her approval
    if any(m["kind"] == "reply_in" and m["status"] == "new" for m in rows):
        return True, "a contact replied"
    sent = [m for m in rows if m["status"] == "sent"]
    if sent and not any(m["status"] == "replied" for m in rows):
        fups = [m for m in rows if m["kind"] == "followup"]
        if len(fups) < config.MAX_FOLLOWUPS and business_days_since(sent[-1]["sent_at"]) >= config.FOLLOWUP_AFTER_BUSINESS_DAYS:
            return True, "no reply yet - follow-up may be due"
    return False, ""


def cmd_run(args):
    from .agent import run_company
    c = db.conn()
    q = "SELECT * FROM companies"
    rows = [r for r in c.execute(q) if not args.company or args.company.lower() in r["name"].lower()]
    done = 0
    from .agent import spent_today
    for co in rows:
        due, why = _due(c, co)
        if not due and not args.company:
            continue
        if spent_today(c) >= config.DAILY_BUDGET_USD:
            print(f"\ndaily budget ${config.DAILY_BUDGET_USD:.2f} reached (est. ${spent_today(c):.2f}) - "
                  "remaining companies wait for tomorrow. Raise DAILY_BUDGET_USD in .env to change this.")
            break
        print(f"\n== {co['name']} - {co['title']} ({why or 'forced'})")
        state = run_company(c, co["id"], reason=why)
        c.execute("""UPDATE messages SET status='seen' WHERE kind='reply_in' AND status='new'
                     AND contact_id IN (SELECT id FROM contacts WHERE company_id=?)""", (co["id"],))
        c.commit()
        print(f"   status: {state['status']} - {state.get('notes') or ''}")
        done += 1
        if args.limit and done >= args.limit:
            break
    print(f"\nagent ran on {done} compan{'y' if done == 1 else 'ies'}; est. spend today ${spent_today(c):.2f}")
    return done


def cmd_replies(args):
    """Check Gmail threads of sent emails; record replies so the agent reacts on the next run."""
    c = db.conn()
    rows = c.execute("""SELECT m.*, ct.company_id, ct.name AS who FROM messages m JOIN contacts ct ON ct.id=m.contact_id
                        WHERE m.status='sent' AND m.gmail_thread_id IS NOT NULL""").fetchall()
    found = 0
    for m in rows:
        for t in gmail.thread_messages(m["gmail_thread_id"]):
            if config.SENDER_EMAIL.lower() in t["from"].lower():
                continue
            exists = c.execute("SELECT 1 FROM messages WHERE contact_id=? AND kind='reply_in' AND body=?",
                               (m["contact_id"], t["body"] or t["snippet"])).fetchone()
            if exists:
                continue
            c.execute("INSERT INTO messages(contact_id,channel,kind,subject,body,status,gmail_thread_id,created_at) "
                      "VALUES(?,?,?,?,?,?,?,?)", (m["contact_id"], "email", "reply_in", t["subject"],
                      t["body"] or t["snippet"], "new", m["gmail_thread_id"], db.now()))
            c.execute("UPDATE messages SET status='replied' WHERE id=?", (m["id"],))
            c.execute("UPDATE companies SET status='waiting' WHERE id=?", (m["company_id"],))
            db.log(c, m["company_id"], "reply", f"{m['who']}: {t['snippet'][:150]}")
            found += 1
            print(f"REPLY from {m['who']}: {t['snippet'][:160]}")
    c.commit()
    print(f"{found} new repl{'y' if found == 1 else 'ies'}. LinkedIn replies: mark them with "
          f"`python -m outreach note <company> \"<what they said>\"`")


def _resume_for(company):
    key = re.sub(r"[^a-z0-9]", "", company.lower())
    hits = sorted(p for p in config.RESUME_DIR.glob("*/SDE_Nilakshi_Nagrale_*.pdf")
                  if key and key in re.sub(r"[^a-z0-9]", "", p.stem.lower().split("nagrale_")[-1]))
    return hits[-1] if hits else None


def _clip(text):
    try:
        subprocess.run(["pbcopy"], input=text.encode(), check=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def cmd_review(args):
    """The approval gate. Nothing leaves without a 'y' here."""
    c = db.conn()
    rows = c.execute("""SELECT m.*, ct.name AS who, ct.title AS who_title, ct.email, ct.linkedin_url,
                               co.name AS company, co.id AS company_id
                        FROM messages m JOIN contacts ct ON ct.id=m.contact_id JOIN companies co ON co.id=ct.company_id
                        WHERE m.status='drafted' ORDER BY co.name, m.id""").fetchall()
    if not rows:
        print("nothing to review")
        return
    print(f"{len(rows)} draft(s) waiting.  y = approve & send   e = edit   s = skip for now   n = reject   q = quit\n")
    for m in rows:
        print("=" * 78)
        print(f"{m['company']}  |  {m['kind']} {m['channel']}  ->  {m['who']} ({m['who_title']})")
        print(f"to: {m['email'] or m['linkedin_url']}")
        if m["subject"]:
            print(f"subject: {m['subject']}")
        print("-" * 78 + f"\n{m['body']}\n" + "-" * 78)
        if m["channel"] == "linkedin_connect":
            print(f"({len(m['body'])}/{config.CONNECT_NOTE_LIMIT} chars)")
        a = input("[y/e/s/n/q] > ").strip().lower()
        if a == "q":
            break
        if a == "s":
            continue
        if a == "n":
            c.execute("UPDATE messages SET status='rejected_by_user' WHERE id=?", (m["id"],))
            db.log(c, m["company_id"], "rejected_draft", m["who"])
            c.commit()
            continue
        body, subject = m["body"], m["subject"]
        if a == "e":
            print("Paste the new body, end with a single '.' line:")
            lines = []
            while (ln := input()) != ".":
                lines.append(ln)
            body = "\n".join(lines) or body
            if m["channel"] == "email":
                subject = input(f"subject [{subject}]: ").strip() or subject
            a = "y"
        if a != "y":
            continue
        if m["channel"] == "email":
            attach = _resume_for(m["company"]) if m["kind"] == "initial" else None
            thread = m["gmail_thread_id"]
            did, tid = gmail.create_draft(m["email"], subject, body, attachment=attach, thread_id=thread)
            tid = gmail.send_draft(did) or tid
            c.execute("UPDATE messages SET status='sent', body=?, subject=?, gmail_thread_id=?, sent_at=? WHERE id=?",
                      (body, subject, tid, db.now(), m["id"]))
            print(f"  sent{' with ' + attach.name if attach else ''}")
        else:
            ok = _clip(body)
            webbrowser.open(m["linkedin_url"])
            print(f"  opened profile; note {'copied to clipboard' if ok else 'shown above'}. "
                  "Click Connect -> Add a note -> paste -> Send.")
            if input("  did you send it? [y/n] > ").strip().lower() != "y":
                continue
            c.execute("UPDATE messages SET status='sent', body=?, sent_at=? WHERE id=?", (body, db.now(), m["id"]))
        db.log(c, m["company_id"], "sent", f"{m['kind']} {m['channel']} -> {m['who']}")
        c.commit()


def cmd_note(args):
    """Record something that happened outside Gmail (e.g. a LinkedIn reply) so the agent can react."""
    c = db.conn()
    co = c.execute("SELECT * FROM companies WHERE name LIKE ?", (f"%{args.company}%",)).fetchone()
    if not co:
        sys.exit("no such company")
    ct = c.execute("SELECT * FROM contacts WHERE company_id=? ORDER BY id LIMIT 1", (co["id"],)).fetchone()
    if ct:
        c.execute("INSERT INTO messages(contact_id,channel,kind,body,status,created_at) VALUES(?,?,?,?,?,?)",
                  (ct["id"], "linkedin_connect", "reply_in", args.text, "new", db.now()))
    db.log(c, co["id"], "note", args.text)
    c.execute("UPDATE companies SET status='waiting' WHERE id=?", (co["id"],))
    c.commit()
    print("noted - the agent will act on it next run")


def _key(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def import_from_resumes(c, days=None):
    """New tailored resumes in Job Applications/<date>/ = new applications. The agent finds the posting itself."""
    days = days if days is not None else config.SCAN_RESUME_DAYS
    known = [(_key(r["name"]), r["applied_on"]) for r in c.execute("SELECT name, applied_on FROM companies")]
    added = 0
    for d in range(days):
        day = (date.today() - timedelta(days=d)).isoformat()
        folder = config.RESUME_DIR / day
        if not folder.is_dir():
            continue
        for pdf in sorted(folder.glob("*_Nilakshi_Nagrale_*.pdf")):
            company = pdf.stem.split("Nilakshi_Nagrale_", 1)[-1]
            k = _key(company)
            if any(k and (k in kk or kk in k) for kk, _ in known):
                continue
            c.execute("INSERT INTO companies(name,title,url,applied_on,updated_at) VALUES(?,?,?,?,?)",
                      (company, "", None, day, db.now()))
            known.append((k, day))
            added += 1
    c.commit()
    return added


def sync_drafts(c):
    """Gmail approval: a draft that disappeared was either sent by her (approved) or deleted (rejected)."""
    n_sent = n_rej = 0
    for m in c.execute("SELECT * FROM messages WHERE status='drafted' AND gmail_draft_id IS NOT NULL").fetchall():
        if gmail.draft_exists(m["gmail_draft_id"]):
            continue
        sent_at = gmail.sent_by_me_after(m["gmail_thread_id"], m["created_at"]) if m["gmail_thread_id"] else None
        if sent_at:
            c.execute("UPDATE messages SET status='sent', sent_at=? WHERE id=?", (sent_at, m["id"]))
            n_sent += 1
        else:
            c.execute("UPDATE messages SET status='rejected_by_user' WHERE id=?", (m["id"],))
            n_rej += 1
    c.commit()
    return n_sent, n_rej


def push_email_drafts(c):
    """Put new email drafts into Gmail Drafts (resume attached on first emails). She approves by pressing Send."""
    rows = c.execute("""SELECT m.*, ct.email, co.name AS company FROM messages m
                        JOIN contacts ct ON ct.id=m.contact_id JOIN companies co ON co.id=ct.company_id
                        WHERE m.status='drafted' AND m.channel='email' AND m.gmail_draft_id IS NULL""").fetchall()
    for m in rows:
        attach = _resume_for(m["company"]) if m["kind"] == "initial" else None
        did, tid = gmail.create_draft(m["email"], m["subject"], m["body"], attachment=attach, thread_id=m["gmail_thread_id"])
        c.execute("UPDATE messages SET gmail_draft_id=?, gmail_thread_id=? WHERE id=?", (did, tid, m["id"]))
    c.commit()
    return len(rows)


def build_digest(c, emails_ready, new_replies, sent, spend):
    li = c.execute("""SELECT m.*, ct.name AS who, ct.title AS who_title, ct.linkedin_url, co.name AS company
                      FROM messages m JOIN contacts ct ON ct.id=m.contact_id JOIN companies co ON co.id=ct.company_id
                      WHERE m.status='drafted' AND m.channel LIKE 'linkedin%'""").fetchall()
    lines = [f"Outreach agent - {date.today().isoformat()}", "",
             f"Emails waiting in Gmail Drafts: {emails_ready}  (open Drafts, edit if you like, press Send = approved;"
             " delete = rejected)",
             f"Replies received: {new_replies}   Sent since last run: {sent}   Est. spend today: ${spend:.2f}", ""]
    if li:
        lines += [f"LinkedIn notes to send ({len(li)}): open the profile -> Connect -> Add a note -> paste", ""]
        for m in li:
            lines += [f"{m['company']} - {m['who']} ({m['who_title']})", m["linkedin_url"], m["body"], ""]
            c.execute("UPDATE messages SET status='sent', sent_at=? WHERE id=?", (db.now(), m["id"]))
    c.commit()
    return "\n".join(lines), len(li)


def cmd_auto(args):
    """Hands-off run (scheduled daily): import -> sync -> agent -> Gmail drafts -> digest to her inbox."""
    c = db.conn()
    print(f"### auto run {db.now()}")
    if config.APPLICATIONS_CSV.exists():
        cmd_import(argparse.Namespace(csv=None))
    print(f"imported {import_from_resumes(c)} new application(s) from Job Applications/")
    sent, rej = sync_drafts(c)
    print(f"gmail: {sent} draft(s) you sent, {rej} you deleted")
    before = c.execute("SELECT COUNT(*) FROM messages WHERE kind='reply_in'").fetchone()[0]
    cmd_replies(args)
    new_replies = c.execute("SELECT COUNT(*) FROM messages WHERE kind='reply_in'").fetchone()[0] - before
    cmd_run(argparse.Namespace(company=None, limit=args.limit))
    emails = push_email_drafts(c)
    from .agent import spent_today
    body, n_li = build_digest(c, emails, new_replies, sent, spent_today(c))
    if emails or n_li or new_replies:
        gmail.email_myself(f"Outreach: {emails} email draft(s), {n_li} LinkedIn note(s), {new_replies} repl(ies)", body)
        print("digest emailed to you")
    else:
        print("nothing new - no digest sent")


def cmd_status(args):
    c = db.conn()
    for co in c.execute("SELECT * FROM companies ORDER BY updated_at DESC"):
        msgs = c.execute("""SELECT m.status, COUNT(*) n FROM messages m JOIN contacts ct ON ct.id=m.contact_id
                            WHERE ct.company_id=? GROUP BY m.status""", (co["id"],)).fetchall()
        tally = ", ".join(f"{r['n']} {r['status']}" for r in msgs) or "no messages"
        print(f"{co['name'][:22]:22} {co['status']:11} {tally:40} {(co['notes'] or '')[:60]}")


def cmd_daily(args):
    """Morning routine: pull replies, let the agent act, then open the approval queue."""
    try:
        cmd_replies(args)
    except SystemExit as e:
        print(f"(skipping Gmail reply check: {e})")
    cmd_run(args)
    cmd_review(args)


def main():
    p = argparse.ArgumentParser(prog="outreach")
    s = p.add_subparsers(dest="cmd", required=True)
    i = s.add_parser("import"); i.add_argument("--csv"); i.set_defaults(f=cmd_import)
    r = s.add_parser("run"); r.add_argument("--company"); r.add_argument("--limit", type=int); r.set_defaults(f=cmd_run)
    s.add_parser("replies").set_defaults(f=cmd_replies)
    s.add_parser("review").set_defaults(f=cmd_review)
    au = s.add_parser("auto"); au.add_argument("--limit", type=int); au.set_defaults(f=cmd_auto)
    s.add_parser("status").set_defaults(f=cmd_status)
    n = s.add_parser("note"); n.add_argument("company"); n.add_argument("text"); n.set_defaults(f=cmd_note)
    d = s.add_parser("daily"); d.add_argument("--company"); d.add_argument("--limit", type=int); d.set_defaults(f=cmd_daily)
    a = p.parse_args()
    a.f(a)
