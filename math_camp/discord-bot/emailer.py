"""
Sponsor Email Automation — Higher Grade Tutoring
------------------------------------------------
Reads sponsors from the "Sponsor Tracker" Google Sheet, emails the ones marked
"Not Contacted" that have a real email address, then updates the sheet to mark
them "Contacted" with today's date.

Requirements:
    pip install gspread google-auth

Setup:
    1. Create a Google Cloud service account and download the JSON key.
    2. Rename the key to service_account.json next to this script.
    3. Share the Google Sheet with the service account email (Editor access,
       since this script writes back to the sheet).
    4. Fill in the CONFIG section below.

Sheet layout (header is on ROW 4):
    B: Sponsor / Organization     F: Date Contacted
    C: Contact Name               G: Status   ("Not Contacted" / "Contacted")
    D: Email / Phone              H: Follow-Up Date
    E: Type   (Business / Nonprofit / Institution)    I: Notes
"""

import collections
import os
import random
import smtplib
import subprocess
import sys
import threading
import time
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import gspread
from google.oauth2.service_account import Credentials

import replies   # reply sender (same folder); used to flush queued replies

# Folder this script lives in — used to locate service_account.json no matter
# which directory you run the script from.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def keep_mac_awake():
    """Stop the Mac from sleeping while this script runs.

    Uses macOS's built-in `caffeinate` tool. We launch it as a background
    process tied to OUR process id (-w <pid>): caffeinate keeps the system
    awake until this script exits, then quits on its own. So it cleans up
    automatically even if the script crashes or you hit Ctrl+C.

    Flags: -i = don't idle-sleep the system, -s = stay awake on AC power,
           -d = keep the display awake too (handy so you can see progress).

    NOTE: this can't override CLOSING the laptop lid — a closed lid still
    sleeps unless an external monitor/power/keyboard is attached. So leave
    the lid OPEN for the duration of the queue.
    """
    if sys.platform != "darwin":
        return  # Not a Mac — nothing to do.
    try:
        subprocess.Popen(["caffeinate", "-i", "-s", "-d", "-w", str(os.getpid())])
        print("☕ caffeinate started — your Mac won't sleep while this runs "
              "(keep the lid open).\n")
    except FileNotFoundError:
        print("⚠️  Couldn't start caffeinate — your Mac may sleep mid-queue.\n")


# ─────────────────────────────────────────────
# PAUSE / STOP CONTROL + QUEUE CONTROLLER
# (driven by the Discord bot's slash commands)
# ─────────────────────────────────────────────
PAUSE_EVENT = threading.Event()
PAUSE_EVENT.set()          # set = running, cleared = paused
STOP_EVENT = threading.Event()   # set = user asked to stop the queue

# Live status the bot reads for /queue-status. Updated by run() as it works.
STATUS = {
    "state": "idle",         # idle|starting|running|resting|done|stopped|error
    "sent": 0, "failed": 0, "total": 0,
    "current": "", "sent_in_batch": 0, "batch_size": 0,
    "resting_until": 0.0,    # epoch seconds the current rest ends (0 if not resting)
    "rest_reason": "",       # why it's resting — set only for LONG breaks, not 3-6m gaps
    "message": "",
}
_worker_thread = None


def interruptible_sleep(seconds):
    """Sleep `seconds`, but: freeze the countdown while PAUSED, and bail early
    if STOP was pressed. Lets Pause take effect even mid-wait."""
    remaining = float(seconds)
    while remaining > 0:
        if STOP_EVENT.is_set():
            return
        if PAUSE_EVENT.is_set():
            step = min(1.0, remaining)
            time.sleep(step)
            remaining -= step
        else:
            time.sleep(0.3)   # paused — hold here without counting down


def wait_while_paused():
    """Block before an action while paused. Returns False if STOP was pressed."""
    while not PAUSE_EVENT.is_set() and not STOP_EVENT.is_set():
        time.sleep(0.3)
    return not STOP_EVENT.is_set()


def is_running():
    return _worker_thread is not None and _worker_thread.is_alive()


def is_paused():
    return not PAUSE_EVENT.is_set()


def pause():
    PAUSE_EVENT.clear()


def resume():
    PAUSE_EVENT.set()


def request_stop():
    STOP_EVENT.set()
    PAUSE_EVENT.set()        # unblock any wait so the worker can exit


def start_queue():
    """Launch the send loop on a background thread. (ok, message)."""
    global _worker_thread
    if is_running():
        return False, "The queue is already running."
    if not ZOHO_EMAIL or not ZOHO_PASSWORD:
        return False, "Missing Zoho credentials (set ZOHO_EMAIL / ZOHO_PASSWORD)."
    STOP_EVENT.clear()
    PAUSE_EVENT.set()
    STATUS.update(state="starting", sent=0, failed=0, total=0, current="",
                  sent_in_batch=0, batch_size=0,
                  resting_until=0.0, message="")
    _worker_thread = threading.Thread(target=_run_guarded, name="emailer",
                                      daemon=True)
    _worker_thread.start()
    return True, "Queue started."


def status():
    s = dict(STATUS)
    s["paused"] = is_paused()
    s["running"] = is_running()
    s["replies_pending"] = replies_pending()
    return s


# ── Priority reply queue (filled by the Discord 'Send' button) ──
_REPLY_QUEUE = collections.deque()
_REPLY_LOCK = threading.Lock()


def enqueue_reply(payload):
    """Queue a human-approved reply. payload = dict(to_email, subject, body,
    in_reply_to, references, attachments, by). Queued replies are sent FIRST at
    the next send slot — and each new one jumps to the FRONT of the reply line
    (appendleft) so the just-sent reply is the very next thing to go out."""
    with _REPLY_LOCK:
        _REPLY_QUEUE.appendleft(payload)
    return len(_REPLY_QUEUE)


def _next_reply():
    with _REPLY_LOCK:
        return _REPLY_QUEUE.popleft() if _REPLY_QUEUE else None


def replies_pending():
    return len(_REPLY_QUEUE)


# ── Outreach queue (exposed so the Discord layer can preview / edit / skip the
# upcoming emails before they send). The send loop fills _OUTREACH once it has
# built the ordered sponsor list; _OUTREACH_IDX is the next not-yet-handled row.
_OUTREACH: list = []
_OUTREACH_IDX = 0
_OUTREACH_LOCK = threading.Lock()


def _set_outreach(sponsors):
    """Called by run() once the ordered send list is built."""
    global _OUTREACH, _OUTREACH_IDX
    with _OUTREACH_LOCK:
        _OUTREACH = sponsors
        _OUTREACH_IDX = 0


def _ensure_email(s):
    """Generate this sponsor's outreach email once and cache it on the dict, so
    a preview matches exactly what will send. Returns (subject, body), applying
    any human override."""
    if "subject" not in s or "body" not in s:
        subj, body = build_email(s["type"], s["name"])
        if s.get("note"):
            body += f"\n\nP.S. {s['note']}\n"
        s["subject"], s["body"] = subj, body
    subj = s["override_subject"] if s.get("override_subject") else s["subject"]
    body = s["override_body"] if s.get("override_body") is not None else s["body"]
    return subj, body


def _next_outreach():
    """Advance to and return the next not-skipped upcoming sponsor (or None)."""
    global _OUTREACH_IDX
    with _OUTREACH_LOCK:
        while _OUTREACH_IDX < len(_OUTREACH):
            s = _OUTREACH[_OUTREACH_IDX]
            _OUTREACH_IDX += 1
            if not s.get("skip"):
                return s
        return None


def outreach_position():
    with _OUTREACH_LOCK:
        return _OUTREACH_IDX


def outreach_upcoming(limit=10):
    """The next `limit` not-yet-sent, not-skipped outreach emails, each with the
    exact subject/body that will go out (so edits act on the real thing)."""
    out = []
    with _OUTREACH_LOCK:
        for s in _OUTREACH[_OUTREACH_IDX:]:
            if s.get("skip"):
                continue
            subj, body = _ensure_email(s)
            out.append({
                "row": s["row"], "name": s["name"], "type": s["type"],
                "location": s.get("location", ""), "email": s["email"],
                "subject": subj, "body": body,
                "edited": bool(s.get("override_body") is not None
                               or s.get("override_subject")),
            })
            if len(out) >= limit:
                break
    return out


def _find_upcoming(row):
    """(caller holds the lock) the upcoming sponsor with this sheet row, or None."""
    for s in _OUTREACH[_OUTREACH_IDX:]:
        if s.get("row") == row and not s.get("skip"):
            return s
    return None


def set_outreach_override(row, subject, body):
    """Override the email for an upcoming sponsor. (ok, name_or_error)."""
    with _OUTREACH_LOCK:
        s = _find_upcoming(row)
        if s is None:
            return False, "That email isn't in the upcoming queue (already sent or skipped)."
        if subject is not None and subject.strip():
            s["override_subject"] = subject.strip()
        s["override_body"] = body
        return True, s["name"]


def skip_outreach(row):
    """Drop an upcoming sponsor so it's never emailed this run. (ok, name_or_error)."""
    with _OUTREACH_LOCK:
        s = _find_upcoming(row)
        if s is None:
            return False, "That email isn't in the upcoming queue (already sent or skipped)."
        s["skip"] = True
        return True, s["name"]

# ─────────────────────────────────────────────
# CONFIG — fill these in before running
# ─────────────────────────────────────────────

# Secrets now come from the environment (server: /etc/highergrade-bot.env;
# local: a .env file). Nothing sensitive is hard-coded here anymore.
ZOHO_EMAIL    = os.environ.get("ZOHO_EMAIL", "")     # account you LOG IN as
ZOHO_PASSWORD = os.environ.get("ZOHO_PASSWORD", "")  # Zoho APP password

# The address sponsors actually SEE in the "From" field — your alias. It MUST be
# added to your Zoho account first (Settings → Mail Accounts). Defaults to the
# login address if unset.
FROM_EMAIL    = os.environ.get("FROM_EMAIL") or ZOHO_EMAIL

# Path to the service-account JSON key (defaults to one sitting next to this
# file). On the server, point SHEETS_KEY_FILE at the deployed key.
GOOGLE_SHEETS_KEY_FILE = os.environ.get("SHEETS_KEY_FILE", "service_account.json")
SPREADSHEET_ID         = os.environ.get("SPREADSHEET_ID",
                                        "1rpxeQHzEVRJXAeICNcdvJdFHeTul9p-LIVOBhlRx47o")
WORKSHEET_NAME         = os.environ.get("WORKSHEET_NAME", "Sponsor Tracker")
HEADER_ROW             = int(os.environ.get("HEADER_ROW", "4"))

# Safety switch: when True, every email goes to YOU (ZOHO_EMAIL) and the sheet is
# NOT modified — perfect for a test run. Set EMAILER_TEST_MODE=1 to force it on.
TEST_MODE = os.environ.get("EMAILER_TEST_MODE", "") not in ("", "0", "false", "False")

# ── Anti-spam pacing ──────────────────────────────────────────────────────
# Between two emails we wait a RANDOM number of minutes in [MIN, MAX] so there's
# no fixed, detectable pattern.
MIN_GAP_MIN = 7.0    # shortest gap between two emails (minutes)
MAX_GAP_MIN = 11.0   # longest gap between two emails (minutes)

# 'Whatever'-location sponsors are held out of the queue by default (the team
# sends those by hand, after everything else). Flip this to 1 in the env when
# you're ready to send them — they'll be the only 'Not Contacted' rows left.
INCLUDE_WHATEVER = os.environ.get("INCLUDE_WHATEVER", "0") == "1"

# ── Warm-up batch ramp ────────────────────────────────────────────────────
# Outreach goes out in growing batches with shrinking rests between them, so a
# sending address warms up gently instead of blasting from cold. Within a batch,
# emails still space out on the human-like MIN/MAX_GAP_MIN gap above. After each
# batch the bot rests, then the NEXT batch is BATCH_STEP larger and its rest
# BATCH_REST_STEP_MIN shorter — until the rest reaches BATCH_REST_FLOOR_MIN,
# where the batch size and rest both freeze and hold (steady cruise).
#   20 emails → rest 180m → 25 → 165m → 30 → 150m → … → 75 → 15m → 75 / 15m …
# (Lands at ~160 outreach emails/day once warmed up — well under Gmail's cap.)
# Only OUTREACH counts toward a batch; human-approved replies are priority sends
# and don't consume the batch quota.
BATCH_START          = 20    # emails in the very first batch
BATCH_STEP           = 5     # each batch sends this many more than the last
BATCH_REST_START_MIN = 180   # rest after the first batch (minutes)
BATCH_REST_STEP_MIN  = 15    # each batch's rest is this many minutes shorter
BATCH_REST_FLOOR_MIN = 15    # rest never drops below this; batch size freezes here too


def _batch_ramp_steps():
    """Number of ramp steps until the rest reaches its floor (after which the
    batch size and rest both hold steady)."""
    return max(0, (BATCH_REST_START_MIN - BATCH_REST_FLOOR_MIN) // BATCH_REST_STEP_MIN)


def _batch_size(n):
    """Size of batch index `n` (0-based). Grows by BATCH_STEP each batch, then
    freezes once the rest has ramped down to its floor, so volume doesn't climb
    forever."""
    n = min(max(0, n), _batch_ramp_steps())
    return BATCH_START + BATCH_STEP * n


def _batch_rest_min(n):
    """Rest in minutes after batch index `n`, shrinking by BATCH_REST_STEP_MIN
    each batch down to BATCH_REST_FLOOR_MIN."""
    return max(BATCH_REST_FLOOR_MIN, BATCH_REST_START_MIN - BATCH_REST_STEP_MIN * max(0, n))

# Column letters used when writing results back to the sheet
COL_EMAIL          = "D"  # used to highlight duplicate emails yellow
COL_TYPE           = "E"  # industry/template type (auto-corrected from the name)
COL_DATE_CONTACTED = "F"
COL_STATUS         = "G"

# Row highlight colours (background fills across B..L):
COLOR_GREEN  = {"red": 0.71, "green": 0.88, "blue": 0.66}  # email sent
COLOR_ORANGE = {"red": 0.99, "green": 0.60, "blue": 0.24}  # invalid email (no @/.)
COLOR_PURPLE = {"red": 0.76, "green": 0.61, "blue": 0.96}  # Government (skipped)
COLOR_PINK   = {"red": 1.0,  "green": 0.60, "blue": 0.80}  # duplicate (team review)
COLOR_CLEAR  = {"red": 1.0,  "green": 1.0,  "blue": 1.0}   # cleared / white

# ─────────────────────────────────────────────
# LOCATION-BASED SEND ORDER
# ─────────────────────────────────────────────
# Emails are sent in a location-interleaved order so we're not blasting one
# town all at once. The pattern (each letter = one email from that location):
#
#   o M o M B  o M o M B  m   o M o M B  o M o M B  m   h   ...repeat
#
# Read as:
#   • Alternate Oakville / Mississauga.
#   • After every 2 O/M pairs (o M o M), insert 1 Burlington.
#   • After every 2 Burlington blocks, insert 1 Milton.
#   • After every 2 Milton inserts, insert 1 Halton Hills.
#
# Tune the three numbers below if you ever want different spacing.
OM_PAIRS_PER_BURLINGTON = 2   # how many Oakville/Mississauga pairs before a Burlington
BURLINGTONS_PER_MILTON  = 2   # how many Burlington blocks before a Milton
MILTONS_PER_HALTON      = 2   # how many Milton inserts before a Halton Hills

# Location names as they appear in column J (matched case-insensitively).
LOC_OAKVILLE     = "Oakville"
LOC_MISSISSAUGA  = "Mississauga"
LOC_BURLINGTON   = "Burlington"
LOC_MILTON       = "Milton"
LOC_HALTON_HILLS = "Halton Hills"

# Common misspellings → which bucket they belong to. The sheet's dropdown
# currently has "Missisauga" (missing an 's'); rather than fight the dropdown,
# we just treat these typos as the real town. Add more here if others crop up.
LOCATION_ALIASES = {
    "missisauga":   "miss",   # the actual typo in your sheet's dropdown
    "mississuaga":  "miss",
    "mississauga":  "miss",
    "oakvile":      "oak",
    "burlinton":    "burl",
    "halton hill":  "hal",
    "haltonhills":  "hal",
}

# ─────────────────────────────────────────────
# EMAIL TEMPLATES
# Keys must match the values in the "Type" column (Column E) exactly.
# {name} = organization name, {greeting} = personalized opening line.
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# EMAIL TEMPLATES — randomized assembler
# Every send picks one paraphrase of each paragraph, so no two outgoing emails
# are identical (far harder to fingerprint than a handful of fixed drafts) while
# the facts and the ask never change. The greeting is simply "Hi <company>,".
# Combinations per category ≈ 4×4×3×3×3×4×5×5 ≈ 43,000.
# ─────────────────────────────────────────────

GREETING = "Hi {name},"

INTRO_VARIANTS = [
    "On behalf of Higher Grade Tutoring — a student-run non-profit currently operating as an NGO — I'm reaching out to introduce our 2-Week Intensive Math Summer Camp, running from August 4th to August 15th at Sheridan College. The program prepares 100 incoming high school freshmen, plus a secondary remedial track for Grade 8 students, to master the full body of tested freshman mathematics before the school year begins.",
    "I'm writing on behalf of Higher Grade Tutoring, an aspiring student-run non-profit (currently an NGO), to share our 2-Week Intensive Math Summer Camp. From August 4th to August 15th at Sheridan College, we prepare 100 incoming freshmen — along with a Grade 8 remedial track — to walk into high school having mastered the full freshman math curriculum.",
    "Higher Grade Tutoring is a student-run non-profit, presently operating as an NGO, and I'd like to introduce our 2-Week Intensive Math Summer Camp. Held from August 4th through the 15th at Sheridan College, it's designed to help 100 incoming high school freshmen, and a Grade 8 remedial group, master the full body of tested freshman mathematics ahead of September.",
    "My name is Lucas, and I help run Higher Grade Tutoring, a student-led non-profit currently operating as an NGO. I wanted to tell you about our 2-Week Intensive Math Summer Camp, taking place August 4th to 15th at Sheridan College, where we prepare 100 incoming freshmen — plus a secondary remedial track for Grade 8 students — to start the school year fully ready for high school math.",
]

TEAM_VARIANTS = [
    "Our instructors are high school juniors and seniors who hold at least a 95% average in a Grade 12 university-level math course, all working under first-aid-certified staff aged 18 and over. We keep a strict 1:5 staff-to-student ratio at all times, and often better — 1:3 or 1:4 — with standby staff on hand.",
    "The teaching team is made up of juniors and seniors maintaining a minimum 95% average in Grade 12 university-level mathematics, supervised throughout by first-aid-certified staff who are 18 or older. A 1:5 staff-to-student ratio is enforced at all times, frequently tightening to 1:3 or 1:4 thanks to standby staff.",
    "Every instructor is a high school junior or senior carrying a 95% or higher average in a Grade 12 university-level math course, overseen by first-aid-certified staff aged 18 and up. We never exceed a 1:5 staff-to-student ratio, and with standby staff we routinely run at 1:3 or 1:4.",
    "Our staff are juniors and seniors with a 95%+ average in Grade 12 university-level math, all working under the supervision of first-aid-certified adults aged 18 and over. We hold a firm 1:5 staff-to-student ratio at minimum, often improving to 1:3 or 1:4 with backup staff present.",
]

# The one category-specific paragraph — the actual ask. {name} is the org name.
CATEGORY_ASKS = {
    "Business": [
        "We would be grateful for your organization's support, in either of two forms. The first is a financial sponsorship. The second, equally valuable to us, is helping us reach further: if you're willing to promote the camp to your employees and share it across your social media channels, we'll recognize that support as equivalent to a $500 contribution.",
        "There are two ways your organization could help, and we'd welcome either. One is a financial sponsorship; the other — just as meaningful to us — is amplifying our reach. Should you be willing to share the camp with your employees and across your social channels, we will treat that as the equivalent of a $500 contribution.",
        "We'd be thankful for your organization's backing in whichever form suits you best: a direct financial sponsorship, or simply helping us spread the word. If you can promote the camp to your staff and on your social media, we'll count it as equivalent to a $500 contribution.",
    ],
    "Churches": [
        "We would be sincerely grateful for your congregation's support. Beyond any financial gift, one of the most meaningful ways you can help is by letting us display our materials within your church and sharing the camp on your social media channels. Should you help us reach families this way, we'll recognize it as equivalent to a $500 contribution.",
        "Your congregation's support would mean a great deal to us. Aside from any financial contribution, a wonderful way to help is to allow our materials to be displayed in your church and to share the camp with your community online. If you're able to help us reach families like this, we'll treat it as equivalent to a $500 contribution.",
        "We'd be honored to have your church's support. More than any donation, you could help enormously by permitting us to post our materials in your church and sharing the camp across your social channels — and we'll recognize that as equivalent to a $500 contribution.",
    ],
    "Government": [
        "I'm writing to ask whether {name} would consider partnering with us. As a trusted public institution, your endorsement would be invaluable — we'd be honored to have you vouch for the camp as a reputable program and help promote our materials to families across the community. We would also welcome any financial support or local advertising that helps us reach more students. Beyond that, we'd be grateful for guidance in two areas: help expediting our formal non-profit incorporation, and advice on structuring daily operations for the safest, most effective experience. Anyone who provides such guidance will be recognized as a Partner in the Partners section of our About Us page.",
        "Would {name} consider entering a partnership with us? Coming from a trusted public institution, your endorsement would carry real weight, and we'd be honored to have you vouch for the camp and help share our materials with local families. Any financial support or local advertising would also be deeply appreciated. We'd likewise value your guidance — both in expediting our non-profit incorporation and in shaping safe, effective daily operations — and anyone who offers it will be named a Partner in the Partners section of our About Us page.",
        "I'd like to ask whether {name} might partner with us. As a respected public body, your endorsement would be invaluable: we'd be grateful to have you confirm the camp as a reputable program and help promote it to families in the community, alongside any financial support or local advertising you can offer. We'd also welcome your guidance on expediting our formal incorporation and on running the camp's daily operations as safely and effectively as possible — and we recognize anyone who advises us as a Partner on our About Us page.",
    ],
    "Insurance": [
        "Because we'll be serving more than 100 young students, third-party liability coverage and student safety are among our highest priorities. I'm writing to ask whether {name} would support us through local advertising of the camp and, where possible, a discount on our third-party liability insurance. We'd also greatly value your professional expertise: any guidance on the precautions we should adopt to both lower our insurance costs and improve student safety would be invaluable at our stage. Anyone who provides such guidance will be recognized as a Partner in the Partners section of our About Us page.",
        "With over 100 young students in our care, third-party liability and safety sit at the top of our list. Might {name} support us by advertising the camp locally and, if possible, offering a discount on our third-party liability coverage? Your professional insight would mean a great deal too — any advice on precautions that reduce our insurance costs while strengthening student safety would be invaluable to us — and we'll recognize anyone who advises us as a Partner on our About Us page.",
        "Serving 100+ young students means third-party liability coverage and safety are foremost for us. We'd be grateful if {name} could help through local advertising and, where feasible, a discount on our third-party liability insurance. We'd also deeply appreciate your expertise on the precautions worth adopting to lower our premiums and keep students safer — and anyone who shares that guidance will be named a Partner in the Partners section of our About Us page.",
    ],
    "Non Profit": [
        "As a fellow organization devoted to community impact, we'd be honored to have your support. We're hoping you might help promote the camp through local advertising and, where you're able, help us navigate and expedite our formal incorporation as a non-profit. We'd also be grateful for your guidance — any advice on running an organization like ours would be of tremendous value, and anyone who provides it will be recognized as a Partner in the Partners section of our About Us page.",
        "Since you share our commitment to community impact, your support would mean a great deal. We'd love your help spreading the word locally and, if you're able, guidance through the process of formally incorporating as a non-profit. Any wisdom you can share about operating an organization like ours would be invaluable — and we'll recognize anyone who offers it as a Partner on our About Us page.",
        "As another organization dedicated to giving back, we'd be honored by your support. We hope you might help advertise the camp locally and, where possible, help us navigate and speed up our non-profit incorporation. We'd also treasure your guidance on running an organization such as ours, and we recognize anyone who advises us as a Partner in the Partners section of our About Us page.",
    ],
    "Food": [
        "Our camp runs on the dedication of 30 unpaid student staff and volunteers, and we'd welcome your help keeping them well-fed throughout the program. If you're able to sponsor our team with food and beverages — covering a single day's meal for all 30 members — we'll recognize your generosity as equivalent to a $500 contribution. We'd also be grateful if you'd display one of our posters in your establishment; in return we'll acknowledge you at our closing ceremony and feature you as a sponsor on our website.",
        "Thirty unpaid student staff and volunteers make this camp possible, and keeping them nourished is no small task. Should you be able to sponsor a single day's meal for all 30 with food and drink, we'll recognize that generosity as equivalent to a $500 contribution. And if you're willing to put up one of our posters in your shop, we'll thank you at our closing ceremony and feature you as a sponsor on our website.",
        "The camp is powered by 30 unpaid student staff and volunteers, and we'd love your help keeping them fed during the two weeks. Sponsoring one day's meal for all 30 with food and beverages would be recognized as equivalent to a $500 contribution. We'd also be grateful if you'd display a poster in your establishment — we'll acknowledge you at our closing ceremony and list you as a sponsor on our website.",
    ],
    "Education": [
        "As an institution devoted to education, you're exceptionally well-placed to support our students. I'm writing to ask whether you could provide school supplies for the camp — in particular, whether we might borrow your instructional drawing pads or iPads for the program's two weeks. If you can lend us this equipment, we'll recognize your support as equivalent to a $500 contribution, and of course return everything in its original condition. We'd also greatly value your expertise on our curriculum; should you offer guidance on its structure, the educator who provides it will be recognized as a Partner in the Partners section of our About Us page.",
        "Few are better positioned than an education-focused institution to help our students. Might you be able to provide school supplies — specifically, to lend us your instructional drawing pads or iPads for the two-week program? We'd recognize such a loan as equivalent to a $500 contribution and return everything exactly as we received it. Your insight on our curriculum would also be invaluable, and any educator who offers guidance will be named a Partner on our About Us page.",
        "Given your dedication to education, you could make a real difference for our students. We'd like to ask whether you might supply school materials for the camp, or lend us your instructional drawing pads or iPads for its two-week duration — support we'd recognize as equivalent to a $500 contribution, with all equipment returned in its original condition. We'd also be grateful for your expertise on our curriculum, and we'll recognize any educator who advises us as a Partner in the Partners section of our About Us page.",
    ],
    "Commercial": [
        "We'd welcome the chance to partner with you in reaching more local families. If you're able to promote the camp on your website and let us display our materials within your venue, we'll recognize that support as equivalent to a $500 contribution.",
        "We'd love to work with you to reach more families in the community. Should you be willing to feature the camp on your website and allow our materials to be displayed in your venue, we'll treat that support as equivalent to a $500 contribution.",
        "Partnering with you to reach more local families would be wonderful. If you can promote the camp on your website and permit us to display our advertisements in your space, we'll recognize it as equivalent to a $500 contribution.",
    ],
}

TIERS_VARIANTS = [
    "In appreciation of those who support us, we offer the following:\n\n- Contributions of $500 or more (or an equivalent in-kind or promotional contribution): a dedicated acknowledgement, a featured placement on the sponsorship page of our website, and a logo displayed at three times the size of our $100-tier supporters.\n- Contributions of $1,000 or more: all of the above, plus a speaking opportunity at our closing ceremony before all campers and their families, and an interactive logo in our website header presenting your details, a profile of the representative who championed the partnership, accompanying photographs, and your location.",
    "Here is how we recognize the partners who support us:\n\n- $500 or more (or an equivalent in-kind or promotional contribution): a dedicated acknowledgement, a featured spot on our website's sponsorship page, and your logo shown at three times the size of our $100-tier supporters'.\n- $1,000 or more: everything above, and additionally a speaking opportunity at our closing ceremony before all campers and their families, plus an interactive logo in our website header featuring your details, a profile of the representative who championed the partnership, photographs, and your location.",
    "For full transparency, here is what supporters receive in return:\n\n- A contribution of $500 or more (or its equivalent in kind or in promotion): a dedicated acknowledgement, a featured placement on our sponsorship page, and a logo three times the size of our $100-tier supporters'.\n- A contribution of $1,000 or more: all of the $500 benefits, a speaking opportunity at our closing ceremony before every camper and family, and an interactive logo in our website header presenting your details, a profile of the representative who championed the partnership, accompanying photographs, and your location.",
]

NONPROFIT_VARIANTS = [
    "As a non-profit, we direct 100% of all funds and donated resources toward our operating costs and reinvestment in the community — our formal incorporation fees, campers' school supplies, the prizes for our student incentive program and final-examination awards, daily meals for our volunteer student staff, and our end-of-program excellence awards for outstanding students and staff. Any surplus is donated directly to local school boards.",
    "Being a non-profit, we put 100% of every dollar and donated resource straight into running the camp and giving back: incorporation fees, school supplies for campers, prizes for our incentive program and final exams, daily meals for our volunteer student staff, and excellence awards at the program's end. Whatever remains is donated directly to local school boards.",
    "Every contribution goes a long way — as a non-profit, 100% of our funds and donated resources support our operating costs and the community: formal incorporation fees, campers' supplies, prizes for our student incentive program and final-exam awards, daily meals for our volunteer student staff, and end-of-program excellence awards. Any surplus is donated directly to local school boards.",
]

CLOSING_VARIANTS = [
    "I'd be glad to share more details at your convenience. Thank you very much for your time and consideration.",
    "I'd be happy to answer any questions or provide further information whenever it suits you. Thank you for taking the time to consider this.",
    "Please let me know if you'd like to learn more — I'd welcome the chance to tell you about it. Thank you sincerely for your time.",
    "I would be delighted to provide anything further you might need. Thank you kindly for your time and consideration.",
]

SIGNOFFS = ["Sincerely,", "Warm regards,", "Best regards,", "Kind regards,", "With appreciation,"]

SIGNATURE = ("Lucas Liu\n"
             "Higher Grade Tutoring\n"
             "lucas.liu.ca2009@gmail.com\n"
             "+1 343-368-2005\n"
             "highergradetutoring.ca")

SUBJECT_VARIANTS = [
    "Sponsorship & Partnership Opportunity — Higher Grade Tutoring Summer Math Camp",
    "Partnership Invitation — Higher Grade Tutoring Summer Math Camp",
    "Supporting 100 Local Students — Higher Grade Tutoring Summer Math Camp",
    "A Partnership Opportunity — Higher Grade Tutoring Summer Math Camp",
    "Higher Grade Tutoring Summer Math Camp — Partnership & Sponsorship",
]

# The 8 valid template categories (used by the name-based classifier).
EMAIL_CATEGORIES = set(CATEGORY_ASKS)


def build_email(org_type, name):
    """Assemble a unique outreach email from interchangeable, equivalent
    paraphrases — a different combination every send, so no two come out
    identical while the facts and the ask stay constant. Returns (subject, body)."""
    asks = CATEGORY_ASKS.get(org_type) or CATEGORY_ASKS["Commercial"]
    blocks = [
        GREETING.format(name=name),
        random.choice(INTRO_VARIANTS),
        random.choice(TEAM_VARIANTS),
        random.choice(asks).format(name=name),
        random.choice(TIERS_VARIANTS),
        random.choice(NONPROFIT_VARIANTS),
        random.choice(CLOSING_VARIANTS),
        random.choice(SIGNOFFS) + "\n" + SIGNATURE,
    ]
    return random.choice(SUBJECT_VARIANTS), "\n\n".join(blocks)

# ─────────────────────────────────────────────
# LOAD SPONSORS FROM GOOGLE SHEETS
# ─────────────────────────────────────────────

def get_worksheet():
    # Read-write scopes: this script both reads and updates the sheet.
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    key_path = os.path.join(BASE_DIR, GOOGLE_SHEETS_KEY_FILE)
    creds  = Credentials.from_service_account_file(key_path, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).worksheet(WORKSHEET_NAME)


# ─────────────────────────────────────────────
# INDUSTRY FROM THE NAME
# ─────────────────────────────────────────────
# The scraper stamps every business "Commercial". Rather than re-pull anything
# from Google, we read the organisation's NAME and pick the best-matching email
# template. Most names match nothing specific and stay "Commercial"; a name only
# becomes Food / Churches / Education / Insurance / Non Profit / Government when
# it clearly says so. Checks are ordered most-specific first.

_TYPE_KEYWORDS = [
    ("Government", ("city of ", "town of ", "region of ", "municipalit",
                    "ministry of ", "government", "fire department",
                    "police service", "public library", "service canada",
                    "provincial", "federal")),
    ("Churches",   ("church", "chapel", "cathedral", "parish", "mosque",
                    "masjid", "temple", "synagogue", "gurdwara", "congregation",
                    "ministries", "gospel", "baptist", "pentecostal",
                    "evangel", "worship centre", "worship center")),
    ("Education",  ("school", "academy", " college", "university", "montessori",
                    "tutoring", "learning centre", "learning center",
                    "institute of", "polytechnic")),
    ("Insurance",  ("insurance", "assurance")),
    ("Non Profit", ("foundation", "charity", "charitable", "non-profit",
                    "nonprofit", " society", " association", "food bank",
                    "shelter", "united way", "volunteer", "goodwill",
                    "salvation army", "habitat for humanity", "ngo")),
    ("Food",       ("restaurant", "cafe", "café", "coffee", "bakery", "bistro",
                    " grill", "pizza", "pizzeria", "sushi", "ramen", "noodle",
                    "kitchen", "diner", "eatery", "tavern", " pub ", "brewery",
                    "brewpub", " deli", "bbq", "barbecue", "steakhouse",
                    "donut", "doughnut", "ice cream", "gelato", "creamery",
                    "juice", "smoothie", "catering", "burger", "taco",
                    "shawarma", "poutine", "wings", "bar & grill")),
]


# Well-known food/coffee/fast-food chains whose NAME has no category word
# (e.g. "Tim Hortons"). Checked first so they classify as Food.
FOOD_BRANDS = (
    "tim hortons", "tim horton", "mcdonald", "starbucks", "subway", "wendy",
    "burger king", "kfc", "popeyes", "pizza hut", "domino", "a&w",
    "dairy queen", "harvey", "booster juice", "second cup", "five guys",
    "chipotle", "taco bell", "swiss chalet", "boston pizza", "mary brown",
    "freshii", "osmow", "pita pit", "mr sub", "mr. sub", "country style",
    "coffee culture", "williams fresh", "panera", "dunkin", "baskin robbins",
    "cobs bread", "krispy kreme", "panago", "pizza nova", "pizzaville",
    "mucho burrito", "quesada", "new york fries", "jollibee", "popeye",
)


def classify_type_from_name(name):
    """Best-matching email-template type for a business name. Defaults to
    'Commercial' when nothing specific matches."""
    n = f" {name.lower()} "   # pad so leading/trailing keywords still match
    if any(b in n for b in FOOD_BRANDS):
        return "Food"
    for type_name, keywords in _TYPE_KEYWORDS:
        if any(k in n for k in keywords):
            return type_name
    return "Commercial"


def load_sponsors(sheet):
    """Return (pending, contacted_emails).

    pending           – list of "Not Contacted" rows that are ready to email.
    contacted_emails  – set of lowercased email addresses that have ALREADY
                        been contacted, used to catch duplicates.
    """
    # head=HEADER_ROW tells gspread the headers are on row 4, not row 1.
    records = sheet.get_all_records(head=HEADER_ROW)

    pending = []
    contacted_emails = set()
    flags = {"orange": [], "purple": []}   # rows to paint after (invalid / govt)
    for i, row in enumerate(records):
        # records[i] corresponds to worksheet row (HEADER_ROW + 1 + i).
        sheet_row = HEADER_ROW + 1 + i

        name     = str(row.get("Sponsor / Organization", "")).strip()
        contact  = str(row.get("Contact Name", "")).strip()
        email    = str(row.get("Email", "")).strip()
        org_type = str(row.get("Type", "")).strip()
        status   = str(row.get("Status", "")).strip()
        note     = str(row.get("Notes", "")).strip()
        location = str(row.get("Location", "")).strip()

        if not name:
            continue  # blank/spacer row

        # Remember every address we've already contacted so we can skip dupes.
        if status.lower() == "contacted" and "@" in email:
            contacted_emails.add(email.lower())
            continue

        # Only email rows that haven't been contacted yet.
        if status.lower() != "not contacted":
            continue

        # Decide the industry from the NAME first (needed to skip Government).
        # Honour an explicit, specific label the team set; otherwise (blank,
        # "Commercial", or unrecognised) derive it from the title.
        if org_type in EMAIL_CATEGORIES and org_type != "Commercial":
            chosen_type = org_type            # trust the team's manual label
        else:
            chosen_type = classify_type_from_name(name)

        # Government: never email — skip and flag the row purple.
        if chosen_type == "Government":
            print(f"  [SKIP] Row {sheet_row}: {name} is Government — purple, skipping.")
            flags["purple"].append(sheet_row)
            continue

        # Email checks. Blank -> nothing to send (leave any no-email highlight).
        # A non-empty value with no @/. is invalid -> flag orange and skip.
        if not email:
            continue
        if not email_is_valid(email):
            print(f"  [SKIP] Row {sheet_row}: {name} has an invalid email "
                  f"'{email}' — orange, skipping.")
            flags["orange"].append(sheet_row)
            continue

        pending.append({
            "name":      name,
            "contact":   contact,
            "email":     email,
            "type":      chosen_type,
            "orig_type": org_type,   # what the sheet had, to detect changes
            "note":      note,
            "location":  location,
            "row":       sheet_row,
        })

    return pending, contacted_emails, flags


# ─────────────────────────────────────────────
# LOCATION-INTERLEAVED ORDERING
# ─────────────────────────────────────────────

def _pattern_tokens():
    """Infinite generator of location keys following the nested rule:
       o M o M B  o M o M B  m   ... (see the LOCATION config block above)."""
    burl_count = 0
    mil_count  = 0
    while True:
        for _ in range(OM_PAIRS_PER_BURLINGTON):
            yield "oak"
            yield "miss"
        yield "burl"
        burl_count += 1
        if burl_count % BURLINGTONS_PER_MILTON == 0:
            yield "mil"
            mil_count += 1
            if mil_count % MILTONS_PER_HALTON == 0:
                yield "hal"


def order_shuffled_by_category(sponsors):
    """Build the send queue: shuffle every pending sponsor, then group by
    category (Type) so same-type emails go together. Shuffling first makes both
    the category order AND the order within each category random, so no run looks
    patterned. 'Whatever'-location rows are held back for manual sending (see
    INCLUDE_WHATEVER)."""
    if INCLUDE_WHATEVER:
        queue = list(sponsors)
    else:
        queue = [s for s in sponsors
                 if (s.get("location") or "").strip().lower() != "whatever"]
        held = len(sponsors) - len(queue)
        if held:
            print(f"  ⏸️  Holding back {held} 'Whatever'-tagged sponsor(s) — "
                  f"set INCLUDE_WHATEVER=1 when you want to send them.")

    random.shuffle(queue)
    # dict keeps first-seen order; the queue is shuffled, so category order is
    # itself random and each category's members stay in random order.
    groups = {}
    for s in queue:
        groups.setdefault(s["type"], []).append(s)
    ordered = []
    for cat in groups:
        ordered.extend(groups[cat])
    if groups:
        print("  🔀 Shuffled, then grouped by category: "
              + ", ".join(f"{c}={len(v)}" for c, v in groups.items()))
    return ordered

# ─────────────────────────────────────────────
# ROW HIGHLIGHTS
# ─────────────────────────────────────────────
_HL_FIRST_COL = 1    # column B, 0-based
_HL_LAST_COL  = 12   # column L exclusive, 0-based


def email_is_valid(addr):
    """A usable email needs both an @ and a dot."""
    return "@" in addr and "." in addr


def _retry(fn, *args, **kwargs):
    """Run a Sheets API call, backing off on 429 rate-limit / quota errors so a
    burst of writes doesn't crash the run."""
    delay = 10
    for attempt in range(6):
        try:
            return fn(*args, **kwargs)
        except Exception as e:   # noqa: BLE001
            msg = str(e)
            limited = ("429" in msg or "Quota exceeded" in msg
                       or "RATE_LIMIT" in msg)
            if limited and attempt < 5:
                print(f"   ⏳ Sheets rate-limited — waiting {delay}s "
                      f"(retry {attempt + 1}/5)...")
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            raise


def set_rows_color(sheet, rows, color):
    """Paint the B..L background of each given row, in one batched call."""
    rows = sorted({r for r in rows if r})
    if not rows:
        return
    reqs = [{"repeatCell": {
        "range": {"sheetId": sheet.id, "startRowIndex": r - 1, "endRowIndex": r,
                  "startColumnIndex": _HL_FIRST_COL, "endColumnIndex": _HL_LAST_COL},
        "cell": {"userEnteredFormat": {"backgroundColor": color}},
        "fields": "userEnteredFormat.backgroundColor"}} for r in rows]
    _retry(sheet.spreadsheet.batch_update, {"requests": reqs})


def _is_attention_bg(bg):
    """True for our yellow (no-email) / orange (invalid) 'needs an email' fills —
    the ones we clear once a valid email appears. Excludes green/purple/white."""
    if not bg:
        return False
    r, g, b = bg.get("red", 1), bg.get("green", 1), bg.get("blue", 1)
    return b < 0.55 and r > 0.85 and g > 0.4   # yellow-ish or orange-ish


def reconcile_highlights(sheet):
    """Your partner fixes a flagged row by typing an email in. On the next run we
    notice the row now has a VALID email but still wears a yellow/orange fill and
    clear it back to white. Returns how many were cleared."""
    email_col = ord(COL_EMAIL) - ord("A") + 1
    emails = sheet.col_values(email_col)           # column D, 1-based index
    last = len(emails)
    if last <= HEADER_ROW:
        return 0
    meta = sheet.spreadsheet.fetch_sheet_metadata(params={
        "ranges": [f"{WORKSHEET_NAME}!B{HEADER_ROW + 1}:B{last}"],
        "fields": "sheets.data.rowData.values.effectiveFormat.backgroundColor"})
    rowdata = meta["sheets"][0]["data"][0].get("rowData", [])
    clear = []
    for offset, rd in enumerate(rowdata):
        row = HEADER_ROW + 1 + offset
        email = emails[row - 1].strip() if row - 1 < len(emails) else ""
        if not email_is_valid(email):
            continue
        bg = (rd.get("values") or [{}])[0].get("effectiveFormat", {}) \
            .get("backgroundColor")
        if _is_attention_bg(bg):
            clear.append(row)
    set_rows_color(sheet, clear, COLOR_CLEAR)
    return len(clear)


# ─────────────────────────────────────────────
# SEND EMAILS VIA ZOHO SMTP + WRITE BACK TO SHEET
# ─────────────────────────────────────────────

def send_email(to_email, subject, body):
    # We open a FRESH connection for every email. With a 5-minute gap between
    # sends, a single long-lived connection would sit idle and Zoho would drop
    # it, causing later sends to fail. Connecting per-send avoids that entirely.
    msg = MIMEMultipart()
    msg["From"]    = FROM_EMAIL          # alias the recipient sees
    msg["To"]      = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    with smtplib.SMTP_SSL("smtp.zohocloud.ca", 465) as smtp:
        smtp.login(ZOHO_EMAIL, ZOHO_PASSWORD)
        # Envelope sender is the alias too, but we authenticate as ZOHO_EMAIL.
        smtp.sendmail(FROM_EMAIL, to_email, msg.as_string())


def mark_contacted(sheet, row_number, date_str):
    # Updates Date Contacted and Status for one sponsor row, then paints the row
    # green to show at a glance that it's been sent.
    # NOTE: we deliberately do NOT touch the Notes column — that's your P.S. input.
    _retry(sheet.batch_update, [
        {"range": f"{COL_DATE_CONTACTED}{row_number}", "values": [[date_str]]},
        {"range": f"{COL_STATUS}{row_number}",         "values": [["Contacted"]]},
    ])
    set_rows_color(sheet, [row_number], COLOR_GREEN)


def update_types(sheet, sponsors):
    """Write each sponsor's name-derived Type back into column E, so the sheet
    itself shows the corrected industry before we start emailing. One batched
    call. Only writes rows whose Type actually changed."""
    changes = [s for s in sponsors if s.get("type") and s["type"] != s.get("orig_type")]
    if not changes:
        return 0
    _retry(sheet.batch_update,
           [{"range": f"{COL_TYPE}{s['row']}", "values": [[s["type"]]]}
            for s in changes],
           value_input_option="USER_ENTERED")
    return len(changes)


def highlight_duplicates(sheet, rows):
    """Paint duplicate rows PINK (whole row, B..L) so the team can review/merge
    them. Batched + 429-retried via set_rows_color (one API call)."""
    set_rows_color(sheet, rows, COLOR_PINK)


def run():
    keep_mac_awake()
    print("📋 Connecting to Google Sheets...")
    sheet = get_worksheet()
    pending, contacted_emails, flags = load_sponsors(sheet)
    print(f"   Found {len(pending)} not-contacted sponsor(s); "
          f"{len(contacted_emails)} address(es) already contacted.\n")

    if TEST_MODE:
        print("🧪 TEST_MODE is ON — all emails go to YOU and the sheet will NOT be changed.\n")

    # ── Highlight housekeeping (real send only) ───────────────────────
    if not TEST_MODE:
        cleared = reconcile_highlights(sheet)   # partner added an email -> un-flag
        if cleared:
            print(f"   ✅ Cleared {cleared} highlight(s) — those rows now have a "
                  f"valid email.")
        set_rows_color(sheet, flags["purple"], COLOR_PURPLE)   # Government
        set_rows_color(sheet, flags["orange"], COLOR_ORANGE)   # invalid emails
        if flags["purple"] or flags["orange"]:
            print(f"   🟣 {len(flags['purple'])} Government row(s) purpled; "
                  f"🟠 {len(flags['orange'])} invalid-email row(s) oranged.")

    # ── Duplicate guard ──────────────────────────────────────────────
    # Skip any pending email that's already been contacted (or that appears
    # twice among the pending rows), and paint its Email cell yellow.
    print("🔎 Checking for duplicate emails...")
    seen     = set(contacted_emails)  # everything already contacted
    to_send  = []
    dup_rows = []
    for s in pending:
        key = s["email"].lower()
        if key in seen:
            dup_rows.append(s["row"])
            continue
        seen.add(key)
        to_send.append(s)
    if not TEST_MODE and dup_rows:
        highlight_duplicates(sheet, dup_rows)   # ONE batched write, not N
    print(f"   {len(dup_rows)} duplicate(s) skipped, {len(to_send)} to send.\n")

    # ── Auto-correct the Type from each name, before queueing ─────────
    # The scraper stamps everything "Commercial"; here we fix the sheet's Type
    # column to the name-derived industry so the right template is used and the
    # sheet reads correctly. (Skipped in test mode — it doesn't touch the sheet.)
    if not TEST_MODE:
        changed = update_types(sheet, to_send)
        if changed:
            print(f"   🏷️  Re-typed {changed} row(s) from their names "
                  f"(e.g. Tim Hortons → Food).")

    # ── Order by location (Oakville/Mississauga alternate up front; empty
    #    shuffled, then grouped by category; 'Whatever' rows are held back) ──
    sponsors = order_shuffled_by_category(to_send)
    # Expose the ordered list so the Discord layer can preview / edit / skip the
    # upcoming emails before they go out.
    _set_outreach(sponsors)

    if not sponsors:
        print("Nothing to send. (Everyone is already contacted, a duplicate, or has no email.)")
        STATUS.update(state="done", message="Nothing to send.")
        return

    today_str = datetime.now().strftime("%a %B %d")  # e.g. "Fri June 05"
    total = len(sponsors)
    print(f"⏳ Queue mode: warm-up batches — {BATCH_START} emails, then "
          f"+{BATCH_STEP} each batch; rest starts at {BATCH_REST_START_MIN}m and "
          f"shrinks {BATCH_REST_STEP_MIN}m per batch down to {BATCH_REST_FLOOR_MIN}m. "
          f"Within a batch, random {MIN_GAP_MIN:.0f}-{MAX_GAP_MIN:.0f} min between "
          f"emails. {total} to send.")
    print("   Keep this script running the whole time. (Ctrl+C to stop.)\n")

    sent = 0
    failed = 0
    replies_sent = 0
    batch_idx = 0                   # which warm-up batch we're on (0-based)
    count_in_batch = 0              # OUTREACH emails sent in the current batch
    STATUS.update(state="running", total=total, sent=0, failed=0,
                  sent_in_batch=0, batch_size=_batch_size(0),
                  resting_until=0.0)

    # Keep running until Stop. Queued replies are sent FIRST at each slot; once
    # the outreach list is exhausted the loop idles, still flushing replies.
    while True:
        if not wait_while_paused():
            print("\n⏹ Stopped by user.")
            break

        did_outreach = False    # was this slot an outreach send? (drives batch pacing)
        reply = _next_reply()
        if reply is not None:
            # 1) Priority: a human-approved reply.
            to = ZOHO_EMAIL if TEST_MODE else reply["to_email"]
            STATUS["current"] = f"reply → {reply.get('to_email', '')}"
            try:
                replies.send_reply(to, reply.get("subject", ""),
                                   reply.get("body", ""),
                                   reply.get("in_reply_to", ""),
                                   reply.get("references", ""),
                                   reply.get("attachments"))
                replies_sent += 1
                by = f" (by {reply['by']})" if reply.get("by") else ""
                print(f"  ↩️  Sent reply → {to}{by}")
                # Once the reply is actually out, archive the original email it
                # was answering (set by the 'Rejected → Send' flow).
                if not TEST_MODE and reply.get("archive_message_id"):
                    try:
                        if replies.archive_message(reply["archive_message_id"],
                                                   extra_flag="Info"):
                            print("     📥 archived the original (flagged Info)")
                    except Exception as e:
                        print(f"     (archive failed: {e})")
            except Exception as e:
                failed += 1
                print(f"  ❌ Reply to {reply.get('to_email')} FAILED — {e}")

        elif (sponsor := _next_outreach()) is not None:
            # 2) Next outreach email (skips any the team dropped via /queue-next).
            did_outreach = True
            idx = outreach_position()        # 1-based position of this send
            STATUS["current"] = sponsor["name"]
            # The exact, one-of-a-kind email — generated once and cached, with any
            # human edit from /queue-next applied (see _ensure_email / build_email).
            subject, body = _ensure_email(sponsor)
            to_email = ZOHO_EMAIL if TEST_MODE else sponsor["email"]
            try:
                send_email(to_email, subject, body)
                loc = sponsor["location"] or "—"
                edited = " ✏️" if (sponsor.get("override_body") is not None
                                   or sponsor.get("override_subject")) else ""
                print(f"  ✅ [{idx}/{total}]{edited} Sent [{loc}/{sponsor['type']}] → {sponsor['name']} <{to_email}>")
                sent += 1
                count_in_batch += 1          # only outreach counts toward the batch
                if not TEST_MODE:
                    mark_contacted(sheet, sponsor["row"], today_str)  # row → green
            except Exception as e:
                failed += 1
                print(f"  ❌ [{idx}/{total}] FAILED → {sponsor['name']} <{sponsor['email']}> — {e}")

        else:
            # 3) Outreach done — idle, still watching for replies to flush.
            STATUS.update(state="idle", current="",
                          message=f"{sent} sent, {replies_sent} replies — "
                                  f"standing by for replies (/queue-stop to end).")
            interruptible_sleep(20)
            continue

        STATUS.update(state="running", sent=sent, failed=failed,
                      sent_in_batch=count_in_batch)

        # ── Pacing before the next send ──
        # Within a batch, every email waits a human-like 7-11 min (whole seconds +
        # a sub-second jitter so it never lands on a clean boundary — e.g.
        # 8m 32.473s). When a batch fills up, the bot takes the warm-up rest
        # instead; the next batch is then BATCH_STEP larger with a shorter rest,
        # until both level off at the floor (see the BATCH_* constants).
        now = time.time()
        rest_reason = ""           # set ONLY for a between-batch warm-up rest
        bsize = _batch_size(batch_idx)
        if did_outreach and count_in_batch >= bsize:
            rest_min = _batch_rest_min(batch_idx)
            wait_secs = rest_min * 60
            nxt = _batch_size(batch_idx + 1)
            rest_reason = f"🌙 batch {batch_idx + 1} done ({bsize} sent) — resting {rest_min}m"
            print(f"\n🌙 Batch {batch_idx + 1} complete ({bsize} emails) — resting "
                  f"{rest_min} min, then the next batch sends {nxt}.\n")
            count_in_batch = 0
            batch_idx += 1
            STATUS.update(batch_size=_batch_size(batch_idx))
        else:
            base_secs = random.randint(int(MIN_GAP_MIN * 60), int(MAX_GAP_MIN * 60))
            wait_secs = base_secs + random.random()

        if wait_secs >= 60:
            STATUS.update(state="resting", resting_until=now + wait_secs,
                          rest_reason=rest_reason)
        _m, _s = divmod(wait_secs, 60)
        print(f"     …waiting {int(_m)}m {_s:06.3f}s ({sent} sent · next in).")
        interruptible_sleep(wait_secs)
        STATUS.update(state="running", resting_until=0.0, rest_reason="")

    STATUS.update(state=("stopped" if STOP_EVENT.is_set() else "done"),
                  sent=sent, failed=failed, current="", resting_until=0.0,
                  message=f"{sent} sent, {replies_sent} replies, {failed} failed.")
    print(f"\n🎉 Done! {sent} sent, {replies_sent} replies, {failed} failed.")


def _run_guarded():
    """Thread target used by start_queue(): run the queue, recording any crash
    in STATUS so the bot's /queue-status can surface it."""
    try:
        run()
    except Exception as exc:   # noqa: BLE001
        import traceback
        traceback.print_exc()
        STATUS.update(state="error", message=str(exc))


if __name__ == "__main__":
    # Manual/local run in the console. On the server the Discord bot imports this
    # module and drives start_queue() / pause() / resume() / status() instead.
    run()
