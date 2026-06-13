"""
Inbound reply handling for the sponsor outreach campaign.

Reads new replies from the Zoho mailbox over IMAP, guesses whether each one is a
rejection, and can send a (human-edited) reply back with optional attachments.
Threading-friendly: the Discord bot calls these from a worker thread so the
async event loop never blocks on the network.

Credentials are shared with the emailer via the environment:
    ZOHO_EMAIL, ZOHO_PASSWORD   (login + app password)
    FROM_EMAIL                  (alias shown in the From field)
    IMAP_HOST                   (default: imap.zoho.com — set imappro... if needed)
    IMAP_PORT                   (default: 993)
    IMAP_FOLDER                 (default: INBOX)
"""
from __future__ import annotations

import email
import imaplib
import os
import smtplib
from email.header import decode_header, make_header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr

ZOHO_EMAIL = os.environ.get("ZOHO_EMAIL", "")
ZOHO_PASSWORD = os.environ.get("ZOHO_PASSWORD", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL") or ZOHO_EMAIL
IMAP_HOST = os.environ.get("IMAP_HOST", "imap.zohocloud.ca")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
IMAP_FOLDER = os.environ.get("IMAP_FOLDER", "INBOX")
IMAP_ARCHIVE_FOLDER = os.environ.get("IMAP_ARCHIVE_FOLDER", "Archive")
# Persistent keyword stamped on every message we've already relayed to Discord,
# so the relay tracks "handled" independent of the read/unread flag (mail read
# in Zoho webmail is still SEEN, which is why a plain UNSEEN search misses it).
RELAY_KEYWORD = os.environ.get("IMAP_RELAY_FLAG", "HGTRelayed")


def _is_bounce(from_email, subject):
    """True for automated delivery-failure / bounce notices, which we skip
    relaying (they'd flood the channel) but still mark handled."""
    fe = (from_email or "").lower()
    if "mailer-daemon" in fe or "postmaster" in fe:
        return True
    subj = (subject or "").lower()
    return any(s in subj for s in (
        "undelivered mail", "mail delivery", "returned to sender",
        "delivery status notification", "delivery failure", "failure notice",
        "undeliverable", "delivery has failed"))
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.zohocloud.ca")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))

# Phrases that strongly suggest a rejection. Lower-cased substring match. This is
# a heuristic — a human still reviews every one before any reply goes out.
REJECTION_PHRASES = (
    "not interested", "no thank", "no thanks", "we decline", "must decline",
    "have to decline", "respectfully decline", "unable to", "won't be able",
    "will not be able", "not able to", "cannot support", "can't support",
    "cannot commit", "not a good fit", "not the right fit", "not at this time",
    "not at this point", "no longer", "regret to inform", "unfortunately",
    "we'll pass", "we will pass", "have to pass", "going to pass",
    "not in a position", "budget doesn't", "no budget", "do not wish",
    "don't wish", "please remove", "remove me", "unsubscribe", "stop emailing",
    "not accepting", "we are not", "we're not",
)
# Phrases that suggest interest — used only to LOWER the rejection confidence so
# we don't mislabel a "yes" as a rejection.
INTEREST_PHRASES = (
    "interested", "happy to", "would love", "let's", "lets ", "sounds great",
    "yes we", "we can", "we would", "count us in", "glad to", "tell me more",
    "more information", "more info", "call", "meeting", "schedule",
)

# Several gracious rejection drafts. We pre-load a RANDOM one each time so the
# replies aren't word-for-word identical (which helps avoid spam flags), and a
# human can still edit it before sending.
REJECTION_REPLY_VARIANTS = (
    ("Hello,\n\n"
     "Thank you so much for taking the time to consider our request and for "
     "getting back to us. We completely understand, and we're grateful for "
     "your consideration.\n\n"
     "If anything changes down the road, we'd be glad to reconnect. Wishing "
     "you and your team all the best.\n\n"
     "Warm regards,\nLucas Liu\nHigher Grade Tutoring\nhighergradetutoring.ca"),
    ("Hi there,\n\n"
     "I really appreciate you getting back to me and for considering our camp "
     "— no worries at all, and thank you for the kind reply. It means a lot "
     "that you took the time.\n\n"
     "Should the timing ever line up better in the future, we'd love to stay "
     "in touch. All the best to you and the team.\n\n"
     "Sincerely,\nLucas Liu\nHigher Grade Tutoring\nhighergradetutoring.ca"),
    ("Hello,\n\n"
     "Thanks so much for your honest and quick response — we totally "
     "understand, and there are absolutely no hard feelings. We're thankful "
     "you even considered supporting our students.\n\n"
     "If things change later on, our door is always open. Take care and have "
     "a wonderful rest of your week.\n\n"
     "Warmly,\nLucas Liu\nHigher Grade Tutoring\nhighergradetutoring.ca"),
    ("Hi,\n\n"
     "Thank you for the thoughtful reply, and for considering us. We "
     "completely respect your decision and are grateful for your time.\n\n"
     "We'd be delighted to reconnect down the line if the opportunity fits "
     "better then. Wishing you continued success.\n\n"
     "Best regards,\nLucas Liu\nHigher Grade Tutoring\nhighergradetutoring.ca"),
)
DEFAULT_REJECTION_REPLY = REJECTION_REPLY_VARIANTS[0]


def draft_for_rejection():
    """A randomly-chosen gracious rejection draft (varied to dodge spam flags)."""
    import random
    return random.choice(REJECTION_REPLY_VARIANTS)


def _decode(value):
    """Decode a possibly RFC2047-encoded header to a plain str."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _plain_body(msg):
    """Best-effort plain-text body of an email.message.Message."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and \
                    "attachment" not in str(part.get("Content-Disposition", "")):
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", "replace")
                except Exception:
                    continue
        # fall back to any text/html stripped crudely
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    import re
                    html = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", "replace")
                    return re.sub(r"<[^>]+>", " ", html)
                except Exception:
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8", "replace")
    except Exception:
        return str(msg.get_payload())


def classify_rejection(text):
    """(is_rejection: bool, confidence: float 0-1) from the reply body."""
    t = (text or "").lower()
    hits = sum(1 for p in REJECTION_PHRASES if p in t)
    interest = sum(1 for p in INTEREST_PHRASES if p in t)
    if hits == 0:
        return False, 0.0
    # More rejection cues raise confidence; interest cues lower it.
    confidence = min(1.0, 0.45 + 0.2 * hits - 0.15 * interest)
    return confidence >= 0.5, round(max(0.0, confidence), 2)


def fetch_new_replies(seen_ids):
    """Return a list of new (UNSEEN, not-already-posted) replies as dicts:
        {message_id, from_name, from_email, subject, body, references, preview,
         is_rejection, confidence}
    `seen_ids` is a set of message-ids already posted this session — we don't
    re-post those. We do NOT mark messages \\Seen here, so an unhandled one
    survives a bot restart; mark_handled() flags it once a human deals with it."""
    if not ZOHO_EMAIL or not ZOHO_PASSWORD:
        raise RuntimeError("Missing ZOHO_EMAIL / ZOHO_PASSWORD for IMAP.")
    out = []
    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        imap.login(ZOHO_EMAIL, ZOHO_PASSWORD)
        imap.select(IMAP_FOLDER)   # read-write: lets us mark messages relayed
        # Find everything not yet relayed — by our persistent keyword, NOT the
        # read/unread flag. (Mail read in Zoho webmail is still SEEN, so the old
        # UNSEEN search relayed nothing — the root cause of replies not showing.)
        typ, data = imap.search(None, "UNKEYWORD", RELAY_KEYWORD)
        if typ != "OK":
            return out
        for num in data[0].split():
            # BODY.PEEK so we don't set the \Seen flag.
            typ, msg_data = imap.fetch(num, "(BODY.PEEK[])")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            mid = (_decode(msg.get("Message-ID")) or "").strip() or num.decode()
            from_name, from_email = parseaddr(_decode(msg.get("From")))
            subject = _decode(msg.get("Subject"))
            # Already posted this session, or an automated bounce → mark relayed
            # so it won't be reconsidered, and don't post it.
            if mid in seen_ids or _is_bounce(from_email, subject):
                try:
                    imap.store(num, "+FLAGS", RELAY_KEYWORD)
                except Exception:
                    pass
                continue
            body = _plain_body(msg).strip()
            references = " ".join(filter(None, [
                _decode(msg.get("References")), mid]))
            is_rej, conf = classify_rejection(body)
            out.append({
                "message_id": mid,
                "from_name": from_name or from_email,
                "from_email": from_email,
                "subject": subject,
                "body": body,
                "references": references.strip(),
                "preview": (body[:1500] + "…") if len(body) > 1500 else body,
                "is_rejection": is_rej,
                "confidence": conf,
            })
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return out


def mark_handled(message_id):
    """Flag the original message \\Seen so it won't be re-fetched after a human
    has dealt with it. Best-effort; silently ignores if it can't find it."""
    if not message_id:
        return
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        imap.login(ZOHO_EMAIL, ZOHO_PASSWORD)
        imap.select(IMAP_FOLDER)
        typ, data = imap.search(None, "HEADER", "Message-ID", message_id)
        if typ == "OK":
            for num in data[0].split():
                imap.store(num, "+FLAGS", "\\Seen")
        imap.logout()
    except Exception:
        pass


def mark_relayed(message_id):
    """Stamp a message with the relay keyword so it's never relayed to Discord
    again — survives bot restarts and is independent of read-state. Best-effort."""
    if not message_id:
        return
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        imap.login(ZOHO_EMAIL, ZOHO_PASSWORD)
        imap.select(IMAP_FOLDER)
        typ, data = imap.search(None, "HEADER", "Message-ID", message_id)
        if typ == "OK":
            for num in data[0].split():
                imap.store(num, "+FLAGS", RELAY_KEYWORD)
        imap.logout()
    except Exception:
        pass


def archive_message(message_id, folder=None, extra_flag=None):
    """Mark the original message handled (\\Seen), optionally stamp a custom
    IMAP keyword (`extra_flag`, e.g. "Info" for sent rejections), then move it
    out of the inbox into the archive folder (created if missing). The keyword
    is set before the move so the copy carries it. Best-effort; returns True if
    the message was actually moved."""
    if not message_id:
        return False
    folder = folder or IMAP_ARCHIVE_FOLDER
    moved = False
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        imap.login(ZOHO_EMAIL, ZOHO_PASSWORD)
        try:
            imap.create(folder)        # no-op (NO) if it already exists
        except Exception:
            pass
        imap.select(IMAP_FOLDER)
        typ, data = imap.search(None, "HEADER", "Message-ID", message_id)
        if typ == "OK":
            for num in data[0].split():
                imap.store(num, "+FLAGS", "\\Seen")   # handled regardless
                if extra_flag:
                    try:
                        imap.store(num, "+FLAGS", extra_flag)  # custom keyword
                    except Exception:
                        pass
                res = imap.copy(num, folder)
                if res and res[0] == "OK":
                    imap.store(num, "+FLAGS", "\\Deleted")
                    moved = True
            if moved:
                imap.expunge()
        imap.logout()
    except Exception:
        pass
    return moved


def flag_important(message_id):
    """Flag the original message as important (\\Flagged) in the inbox so the
    team can spot an accepted reply for manual follow-up. Best-effort."""
    if not message_id:
        return False
    ok = False
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        imap.login(ZOHO_EMAIL, ZOHO_PASSWORD)
        imap.select(IMAP_FOLDER)
        typ, data = imap.search(None, "HEADER", "Message-ID", message_id)
        if typ == "OK":
            for num in data[0].split():
                imap.store(num, "+FLAGS", "\\Flagged")
                ok = True
        imap.logout()
    except Exception:
        pass
    return ok


def send_reply(to_email, subject, body, in_reply_to="", references="",
               attachments=None):
    """Send a reply email (optionally threaded + with attachments).
    `attachments` is a list of (filename, bytes)."""
    if not to_email:
        raise ValueError("No recipient address.")
    msg = MIMEMultipart()
    msg["From"] = FROM_EMAIL
    msg["To"] = to_email
    subject = subject or ""
    msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.attach(MIMEText(body, "plain"))
    for filename, blob in (attachments or []):
        part = MIMEApplication(blob, Name=filename)
        part["Content-Disposition"] = f'attachment; filename="{filename}"'
        msg.attach(part)
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.login(ZOHO_EMAIL, ZOHO_PASSWORD)
        smtp.sendmail(FROM_EMAIL, to_email, msg.as_string())
