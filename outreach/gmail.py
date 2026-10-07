"""Gmail API: drafts, sending (only after approval), and reading reply threads."""
import base64
import html
import mimetypes
import re
from email.message import EmailMessage
from pathlib import Path
from . import config

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
_svc = None


def service():
    global _svc
    if _svc:
        return _svc
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    if config.GMAIL_TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(config.GMAIL_TOKEN), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not config.GMAIL_CREDENTIALS.exists():
                raise SystemExit("credentials.json missing - see README 'Gmail setup'")
            flow = InstalledAppFlow.from_client_secrets_file(str(config.GMAIL_CREDENTIALS), SCOPES)
            creds = flow.run_local_server(port=0)
        config.GMAIL_TOKEN.write_text(creds.to_json())
    _svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return _svc


_sig = None


def signature_html() -> str:
    """Her Gmail signature (Settings -> Signature), so drafts look like ones she writes by hand."""
    global _sig
    if _sig is None:
        try:
            aliases = service().users().settings().sendAs().list(userId="me").execute().get("sendAs", [])
            me = next((a for a in aliases if a["sendAsEmail"].lower() == config.SENDER_EMAIL.lower()),
                      next((a for a in aliases if a.get("isPrimary")), {}))
            _sig = me.get("signature", "") or ""
        except Exception:  # noqa: BLE001 - a missing signature must never block a draft
            _sig = ""
    return _sig


def _html_to_text(h):
    h = re.sub(r"(?i)<br\s*/?>|</div>|</p>|</li>", "\n", h)
    return html.unescape(re.sub(r"<[^>]+>", "", h)).strip()


def _body_html(body):
    """Plain text -> simple HTML; lines starting with '- ' become a bullet list."""
    out, bullets = [], []
    for line in body.split("\n"):
        if line.startswith("- "):
            bullets.append(f"<li>{html.escape(line[2:])}</li>")
            continue
        if bullets:
            out.append("<ul>" + "".join(bullets) + "</ul>")
            bullets = []
        out.append(html.escape(line) + "<br>")
    if bullets:
        out.append("<ul>" + "".join(bullets) + "</ul>")
    return "<div>" + "".join(out) + "</div>"


def _build(to, subject, body, attachment: Path | None = None, thread_headers: dict | None = None,
           signature: bool = True):
    m = EmailMessage()
    m["To"] = to
    m["From"] = f"{config.SENDER_NAME} <{config.SENDER_EMAIL}>"
    m["Subject"] = subject
    for k, v in (thread_headers or {}).items():
        m[k] = v
    sig = signature_html() if signature else ""
    m.set_content(body + (f"\n\n{_html_to_text(sig)}" if sig else ""))
    m.add_alternative(_body_html(body) + (f'<div><br></div><div class="gmail_signature">{sig}</div>' if sig else ""),
                      subtype="html")
    if attachment and attachment.exists():
        ctype, _ = mimetypes.guess_type(attachment.name)
        maintype, subtype = (ctype or "application/pdf").split("/")
        m.add_attachment(attachment.read_bytes(), maintype=maintype, subtype=subtype,
                         filename=attachment.name)
    return {"raw": base64.urlsafe_b64encode(m.as_bytes()).decode()}


def create_draft(to, subject, body, attachment=None, thread_id=None, thread_headers=None):
    msg = _build(to, subject, body, attachment, thread_headers)
    if thread_id:
        msg["threadId"] = thread_id
    d = service().users().drafts().create(userId="me", body={"message": msg}).execute()
    return d["id"], d["message"].get("threadId")


def send_draft(draft_id):
    sent = service().users().drafts().send(userId="me", body={"id": draft_id}).execute()
    return sent.get("threadId")


def delete_draft(draft_id):
    service().users().drafts().delete(userId="me", id=draft_id).execute()


def thread_messages(thread_id):
    """[(from, date, snippet, plain_body)] for a thread."""
    t = service().users().threads().get(userId="me", id=thread_id, format="full").execute()
    out = []
    for m in t.get("messages", []):
        h = {x["name"].lower(): x["value"] for x in m["payload"].get("headers", [])}
        out.append({"from": h.get("from", ""), "date": h.get("date", ""),
                    "ts": int(m.get("internalDate", "0")) / 1000, "labels": m.get("labelIds", []),
                    "message_id": h.get("message-id", ""), "subject": h.get("subject", ""),
                    "snippet": m.get("snippet", ""), "body": _plain(m["payload"])[:4000]})
    return out


def _plain(payload):
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode(errors="ignore")
    for p in payload.get("parts", []) or []:
        t = _plain(p)
        if t:
            return t
    return ""


def draft_exists(draft_id) -> bool:
    from googleapiclient.errors import HttpError
    try:
        service().users().drafts().get(userId="me", id=draft_id, format="minimal").execute()
        return True
    except HttpError as e:
        if e.resp.status == 404:
            return False
        raise


def sent_by_me_after(thread_id, after_iso) -> str | None:
    """ISO time of a message she SENT in this thread after `after_iso` (drafts excluded), else None."""
    from datetime import datetime, timezone
    after = datetime.fromisoformat(after_iso).timestamp()
    for m in thread_messages(thread_id):
        if (config.SENDER_EMAIL.lower() in m["from"].lower() and "DRAFT" not in m["labels"]
                and m["ts"] >= after - 60):
            return datetime.fromtimestamp(m["ts"], timezone.utc).isoformat(timespec="seconds")
    return None


def email_myself(subject, body):
    """The daily digest goes to her own inbox (never to anyone else)."""
    msg = _build(config.SENDER_EMAIL, subject, body, signature=False)
    service().users().messages().send(userId="me", body=msg).execute()
