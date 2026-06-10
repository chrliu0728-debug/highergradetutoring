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
                  sent_in_batch=0, batch_size=BATCH_START_SIZE,
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
    the next send slot — no need to pause the queue."""
    with _REPLY_LOCK:
        _REPLY_QUEUE.append(payload)
    return len(_REPLY_QUEUE)


def _next_reply():
    with _REPLY_LOCK:
        return _REPLY_QUEUE.popleft() if _REPLY_QUEUE else None


def replies_pending():
    return len(_REPLY_QUEUE)

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
MIN_GAP_MIN = 3.0   # shortest gap between two emails (minutes)
MAX_GAP_MIN = 6.0   # longest gap between two emails (minutes)

# Warm-up ramp: send a batch, then take a long rest, then a BIGGER batch after a
# SHORTER rest, and so on — the classic "don't look like a spam cannon" pattern.
#   batch 1: 20 emails -> rest 4h00 ; batch 2: 30 -> 3h45 ; batch 3: 40 -> 3h30 ...
BATCH_START_SIZE = 20    # emails in the first batch
BATCH_STEP_SIZE  = 10    # +this many emails each subsequent batch
REST_START_MIN   = 240   # rest after the first batch (minutes) = 4 hours
REST_STEP_MIN    = 15    # rest shrinks by this each batch (4h00, 3h45, 3h30 ...)
                         # No floor — keeps shrinking down to 0 (then no rest).

# Once the ramp rests hit ~0, we still force a break: at least this long, this
# often, measured over active sending time. (Default: 1 hour every 12 hours.)
MANDATORY_BREAK_EVERY_HOURS = 12
MANDATORY_BREAK_MINUTES     = 60

# Column letters used when writing results back to the sheet
COL_EMAIL          = "D"  # used to highlight duplicate emails yellow
COL_TYPE           = "E"  # industry/template type (auto-corrected from the name)
COL_DATE_CONTACTED = "F"
COL_STATUS         = "G"

# Row highlight colours (background fills across B..L):
COLOR_GREEN  = {"red": 0.71, "green": 0.88, "blue": 0.66}  # email sent
COLOR_ORANGE = {"red": 0.99, "green": 0.60, "blue": 0.24}  # invalid email (no @/.)
COLOR_PURPLE = {"red": 0.76, "green": 0.61, "blue": 0.96}  # Government (skipped)
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

EMAIL_TEMPLATES = {
    "Business": {
        "subject": "Sponsorship & Partnership Opportunity — Higher Grade Tutoring Summer Math Camp",
        "body": """\
{greeting}

On behalf of Higher Grade Tutoring — an aspiring student-run non-profit currently operating as an NGO — I am writing to introduce our 2-Week Intensive Math Summer Camp, taking place from August 4th to August 15th at Sheridan College. The program is designed to prepare 100 incoming high school freshmen, along with a secondary remedial track for Grade 8 students, to master the full body of tested freshman mathematics in advance of the coming school year.

Our instructional team comprises high school juniors and seniors who maintain a minimum 95% average in a Grade 12 university-level mathematics course, working under the supervision of first-aid-certified staff aged 18 and over. We uphold a strict 1:5 staff-to-student ratio at all times, frequently improving to 1:3 or 1:4 with standby staff on hand.

We would be grateful for your organization's support, which we welcome in either of two forms. The first is a financial sponsorship. The second, and equally valuable to us, is helping us extend our reach: should you be willing to promote the camp to your employees and share it across your social media channels, we will recognize that support as equivalent to a $500 contribution.

In appreciation of those who support us, we offer the following:

- Contributions of $500 or more (or an equivalent in-kind or promotional contribution): a dedicated acknowledgement, a featured placement on the sponsorship page of our website, and a logo displayed at three times the size of our $100-tier supporters.
- Contributions of $1,000 or more: all of the above, in addition to a speaking opportunity at our closing ceremony before all campers and their families, and an interactive logo in our website header presenting your organization's details, a profile of the representative who championed the partnership, accompanying photographs, and your branch location.

As a non-profit, we direct 100% of all funds and donated resources toward our operating costs and reinvestment in the community. This includes our formal incorporation fees and campers' school supplies, the prizes for our student incentive program and final examination awards, daily meals for our volunteer student staff, and our end-of-program excellence awards recognizing outstanding students and staff. Any surplus is donated directly to local school boards.

Should your organization wish to explore this opportunity, I would be glad to provide further details at your convenience. Thank you for your time and consideration.

Sincerely,
Lucas Liu
Higher Grade Tutoring
lucas.liu.ca2009@gmail.com
+1 343-368-2005
highergradetutoring.ca
""",
    },
    "Churches": {
        "subject": "Partnership Invitation — Higher Grade Tutoring Summer Math Camp",
        "body": """\
{greeting}

On behalf of Higher Grade Tutoring — an aspiring student-run non-profit currently operating as an NGO — I am writing to introduce our 2-Week Intensive Math Summer Camp, taking place from August 4th to August 15th at Sheridan College. The program is designed to prepare 100 incoming high school freshmen, along with a secondary remedial track for Grade 8 students, to master the full body of tested freshman mathematics in advance of the coming school year, offering local families both confidence and meaningful academic support.

Our instructional team comprises high school juniors and seniors who maintain a minimum 95% average in a Grade 12 university-level mathematics course, working under the supervision of first-aid-certified staff aged 18 and over. We uphold a strict 1:5 staff-to-student ratio at all times, frequently improving to 1:3 or 1:4 with standby staff on hand.

We would be sincerely grateful for your congregation's support. Beyond any financial contribution, one of the most meaningful ways you can assist is by permitting us to display our materials within your church and by sharing the camp across your social media channels. Should you be able to help us reach families in this way, we will recognize it as equivalent to a $500 contribution.

In appreciation of those who support us, we offer the following:

- Contributions of $500 or more (or an equivalent in-kind or promotional contribution): a dedicated acknowledgement, a featured placement on the sponsorship page of our website, and a logo displayed at three times the size of our $100-tier supporters.
- Contributions of $1,000 or more: all of the above, in addition to a speaking opportunity at our closing ceremony before all campers and their families, and an interactive logo in our website header presenting your details, a profile of the individual who championed the partnership, accompanying photographs, and your location.

As a non-profit, we direct 100% of all funds and donated resources toward our operating costs and reinvestment in the community. This includes our formal incorporation fees and campers' school supplies, the prizes for our student incentive program and final examination awards, daily meals for our volunteer student staff, and our end-of-program excellence awards recognizing outstanding students and staff. Any surplus is donated directly to local school boards.

Should your community wish to learn more, I would be honored to provide further information. Thank you for your time and for your service to our neighborhood.

Sincerely,
Lucas Liu
Higher Grade Tutoring
lucas.liu.ca2009@gmail.com
+1 343-368-2005
highergradetutoring.ca
""",
    },
    "Government": {
        "subject": "Partnership Proposal — Higher Grade Tutoring Summer Math Camp",
        "body": """\
{greeting}

On behalf of Higher Grade Tutoring — an aspiring student-run non-profit currently operating as an NGO — I am writing to introduce our 2-Week Intensive Math Summer Camp, taking place from August 4th to August 15th at Sheridan College. The program is designed to prepare 100 incoming high school freshmen, along with a secondary remedial track for Grade 8 students, to master the full body of tested freshman mathematics within two weeks, ensuring they begin the school year academically prepared.

We hold rigor and safety as central priorities. Our instructional team comprises high school juniors and seniors who maintain a minimum 95% average in a Grade 12 university-level mathematics course, working under the supervision of first-aid-certified staff aged 18 and over. We uphold a strict 1:5 staff-to-student ratio at all times, frequently improving to 1:3 or 1:4 with standby staff on hand.

I am writing to ask whether {name} would consider entering into a partnership with us. As a trusted public institution, your endorsement would be invaluable; we would be honored to have you vouch for the camp as a reputable program and assist in promoting our materials to families throughout the community. We would also welcome any financial support or local advertising that enables us to reach a greater number of students and families.

Beyond promotion, we would be grateful for your guidance in two respects: any assistance in expediting our formal non-profit incorporation, and your advice on structuring the daily operations of the camp to ensure the safest and most effective experience for our students. Any individual who provides such guidance will be recognized as a Partner within the Partners section of our staff team on our About Us page.

For any supporter or sponsor, we offer the following in appreciation:

- Contributions of $500 or more (or an equivalent contribution in advertising support): a dedicated acknowledgement, a featured placement on the sponsorship page of our website, and a logo displayed at three times the size of our $100-tier supporters.
- Contributions of $1,000 or more: all of the above, in addition to a speaking opportunity at our closing ceremony before all campers and their families, and an interactive logo in our website header presenting your details, a profile of the representative who championed the partnership, accompanying photographs, and your location.

As a non-profit, we direct 100% of all funds and donated resources toward our operating costs and reinvestment in the community. This includes our formal incorporation fees and campers' school supplies, the prizes for our student incentive program and final examination awards, daily meals for our volunteer student staff, and our end-of-program excellence awards recognizing outstanding students and staff. Any surplus is donated directly to local school boards.

I would be pleased to provide any documentation you may require and to answer any questions. Thank you for considering a partnership with Higher Grade Tutoring.

Respectfully,
Lucas Liu
Higher Grade Tutoring
lucas.liu.ca2009@gmail.com
+1 343-368-2005
highergradetutoring.ca
""",
    },
    "Insurance": {
        "subject": "Insurance Partnership Inquiry — Higher Grade Tutoring Summer Math Camp",
        "body": """\
{greeting}

On behalf of Higher Grade Tutoring — an aspiring student-run non-profit currently operating as an NGO — I am writing to introduce our 2-Week Intensive Math Summer Camp, taking place from August 4th to August 15th at Sheridan College. The program is designed to prepare 100 incoming high school freshmen, along with a secondary remedial track for Grade 8 students, to master the full body of tested freshman mathematics in advance of the coming school year.

Given that we will be serving more than 100 young students, third-party liability coverage and student safety are foremost among our priorities. Our instructional team comprises high school juniors and seniors who maintain a minimum 95% average in a Grade 12 university-level mathematics course, working under the supervision of first-aid-certified staff aged 18 and over. We uphold a strict 1:5 staff-to-student ratio at all times, frequently improving to 1:3 or 1:4 with standby staff on hand.

I am writing to ask whether {name} would consider supporting us through local advertising of the camp and, where possible, a discount on our third-party liability insurance coverage.

We would also greatly value your professional expertise. Any guidance on the precautions we should adopt to both reduce our insurance costs and enhance student safety would be invaluable to an organization at our stage. Any individual who provides such guidance will be recognized as a Partner within the Partners section of our staff team on our About Us page.

In appreciation of those who support us, we offer the following:

- Contributions of $500 or more (or an equivalent contribution in advertising support): a dedicated acknowledgement, a featured placement on the sponsorship page of our website, and a logo displayed at three times the size of our $100-tier supporters.
- Contributions of $1,000 or more: all of the above, in addition to a speaking opportunity at our closing ceremony before all campers and their families, and an interactive logo in our website header presenting your company's details, a profile of the representative who championed the partnership, accompanying photographs, and your branch location.

As a non-profit, we direct 100% of all funds and donated resources toward our operating costs and reinvestment in the community. This includes our formal incorporation fees and campers' school supplies, the prizes for our student incentive program and final examination awards, daily meals for our volunteer student staff, and our end-of-program excellence awards recognizing outstanding students and staff. Any surplus is donated directly to local school boards.

I would be glad to share further details regarding our operations and safety planning, and to answer any questions you may have. Thank you for your time and consideration.

Sincerely,
Lucas Liu
Higher Grade Tutoring
lucas.liu.ca2009@gmail.com
+1 343-368-2005
highergradetutoring.ca
""",
    },
    "Non Profit": {
        "subject": "Partnership & Guidance Request — Higher Grade Tutoring Summer Math Camp",
        "body": """\
{greeting}

On behalf of Higher Grade Tutoring — an aspiring student-run non-profit currently operating as an NGO — I am writing to introduce our 2-Week Intensive Math Summer Camp, taking place from August 4th to August 15th at Sheridan College. The program is designed to prepare 100 incoming high school freshmen, along with a secondary remedial track for Grade 8 students, to master the full body of tested freshman mathematics in advance of the coming school year, with any surplus revenue donated directly to local school boards.

Our instructional team comprises high school juniors and seniors who maintain a minimum 95% average in a Grade 12 university-level mathematics course, working under the supervision of first-aid-certified staff aged 18 and over. We uphold a strict 1:5 staff-to-student ratio at all times, frequently improving to 1:3 or 1:4 with standby staff on hand.

As a fellow organization dedicated to community impact, we would be honored to have your support. We hope you might assist us in promoting the camp through local advertising and, where you are able, in navigating and expediting our formal incorporation as a non-profit.

We would also be most grateful for your guidance. As an aspiring student-run non-profit, any advice you can offer on the effective operation of an organization such as ours would be of tremendous value. Any individual who provides such guidance will be recognized as a Partner within the Partners section of our staff team on our About Us page.

In appreciation of those who support us, we offer the following:

- Contributions of $500 or more (or an equivalent contribution in advertising support): a dedicated acknowledgement, a featured placement on the sponsorship page of our website, and a logo displayed at three times the size of our $100-tier supporters.
- Contributions of $1,000 or more: all of the above, in addition to a speaking opportunity at our closing ceremony before all campers and their families, and an interactive logo in our website header presenting your details, a profile of the individual who championed the partnership, accompanying photographs, and your location.

As a non-profit, we direct 100% of all funds and donated resources toward our operating costs and reinvestment in the community. This includes our formal incorporation fees and campers' school supplies, the prizes for our student incentive program and final examination awards, daily meals for our volunteer student staff, and our end-of-program excellence awards recognizing outstanding students and staff. Any surplus is donated directly to local school boards.

I would welcome the opportunity to connect and to learn from your experience. Thank you for considering your support of a developing organization.

Sincerely,
Lucas Liu
Higher Grade Tutoring
lucas.liu.ca2009@gmail.com
+1 343-368-2005
highergradetutoring.ca
""",
    },
    "Food": {
        "subject": "Sponsorship Opportunity — Higher Grade Tutoring Summer Math Camp",
        "body": """\
{greeting}

On behalf of Higher Grade Tutoring — an aspiring student-run non-profit currently operating as an NGO — I am writing to introduce our 2-Week Intensive Math Summer Camp, taking place from August 4th to August 15th at Sheridan College. The program is designed to prepare 100 incoming high school freshmen, along with a secondary remedial track for Grade 8 students, to master the full body of tested freshman mathematics in advance of the coming school year.

Our instructional team comprises high school juniors and seniors who maintain a minimum 95% average in a Grade 12 university-level mathematics course, working under the supervision of first-aid-certified staff aged 18 and over. We uphold a strict 1:5 staff-to-student ratio at all times, frequently improving to 1:3 or 1:4 with standby staff on hand.

Our camp is sustained by the dedication of 30 unpaid student staff and volunteers, and we would welcome your support in keeping them well-nourished throughout the program. Should you be able to sponsor our team with food and beverages — providing a single day's meal for all 30 members — we will recognize your generosity as equivalent to a $500 contribution.

We would also be grateful for your assistance in raising awareness: should you be willing to display one of our posters within your establishment, we will acknowledge you at our closing ceremony and feature you as a sponsor on our website.

For full transparency, here is how we recognize those who support us:

- Contributions of $500 or more (or an equivalent in-kind contribution, such as a day of meals): a dedicated acknowledgement, a featured placement on the sponsorship page of our website, and a logo displayed at three times the size of our $100-tier supporters.
- Contributions of $1,000 or more: all of the above, in addition to a speaking opportunity at our closing ceremony before all campers and their families, and an interactive logo in our website header presenting your business details, a profile of the individual who championed the partnership, accompanying photographs, and your location.

As a non-profit, we direct 100% of all funds and donated resources toward our operating costs and reinvestment in the community. This includes our formal incorporation fees and campers' school supplies, the prizes for our student incentive program and final examination awards, daily meals for our volunteer student staff, and our end-of-program excellence awards recognizing outstanding students and staff. Any surplus is donated directly to local school boards.

Should you wish to participate, I would be glad to coordinate dates and logistics at your convenience. Thank you for your time and consideration.

Sincerely,
Lucas Liu
Higher Grade Tutoring
lucas.liu.ca2009@gmail.com
+1 343-368-2005
highergradetutoring.ca
""",
    },
    "Education": {
        "subject": "Partnership Inquiry — Higher Grade Tutoring Summer Math Camp",
        "body": """\
{greeting}

On behalf of Higher Grade Tutoring — an aspiring student-run non-profit currently operating as an NGO — I am writing to introduce our 2-Week Intensive Math Summer Camp, taking place from August 4th to August 15th at Sheridan College. The program is designed to prepare 100 incoming high school freshmen, along with a secondary remedial track for Grade 8 students, to master the full body of tested freshman mathematics in advance of the coming school year.

Our instructional team comprises high school juniors and seniors who maintain a minimum 95% average in a Grade 12 university-level mathematics course, working under the supervision of first-aid-certified staff aged 18 and over. We uphold a strict 1:5 staff-to-student ratio at all times, frequently improving to 1:3 or 1:4 with standby staff on hand.

As an institution devoted to education, you are exceptionally well-positioned to support our students. I am writing to ask whether you would be able to provide school supplies for the camp — in particular, whether we might borrow your instructional drawing pads or iPads for the two-week duration of the program. Should you be able to lend us this equipment, we will recognize your support as equivalent to a $500 contribution. We would, of course, return all borrowed equipment in its original condition.

We would also greatly value your expertise regarding our curriculum. Should you be able to offer guidance on its structure, the educator who provides it will be recognized as a Partner within the Partners section of our staff team on our About Us page.

In appreciation of those who support us, we offer the following:

- Contributions of $500 or more (or an equivalent in-kind contribution, such as lent equipment): a dedicated acknowledgement, a featured placement on the sponsorship page of our website, and a logo displayed at three times the size of our $100-tier supporters.
- Contributions of $1,000 or more: all of the above, in addition to a speaking opportunity at our closing ceremony before all campers and their families, and an interactive logo in our website header presenting your details, a profile of the individual who championed the partnership, accompanying photographs, and your location.

As a non-profit, we direct 100% of all funds and donated resources toward our operating costs and reinvestment in the community. This includes our formal incorporation fees and campers' school supplies, the prizes for our student incentive program and final examination awards, daily meals for our volunteer student staff, and our end-of-program excellence awards recognizing outstanding students and staff. Any surplus is donated directly to local school boards.

I would be glad to discuss logistics, scheduling, and insurance at your convenience. Thank you for considering your support of our students.

Sincerely,
Lucas Liu
Higher Grade Tutoring
lucas.liu.ca2009@gmail.com
+1 343-368-2005
highergradetutoring.ca
""",
    },
    "Commercial": {
        "subject": "Partnership Opportunity — Higher Grade Tutoring Summer Math Camp",
        "body": """\
{greeting}

On behalf of Higher Grade Tutoring — an aspiring student-run non-profit currently operating as an NGO — I am writing to introduce our 2-Week Intensive Math Summer Camp, taking place from August 4th to August 15th at Sheridan College. The program is designed to prepare 100 incoming high school freshmen, along with a secondary remedial track for Grade 8 students, to master the full body of tested freshman mathematics in advance of the coming school year.

Our instructional team comprises high school juniors and seniors who maintain a minimum 95% average in a Grade 12 university-level mathematics course, working under the supervision of first-aid-certified staff aged 18 and over. We uphold a strict 1:5 staff-to-student ratio at all times, frequently improving to 1:3 or 1:4 with standby staff on hand.

We would welcome the opportunity to partner with you in reaching a greater number of local families. Should you be able to promote our camp on your website and permit us to display our advertisements within your venue, we will recognize that support as equivalent to a $500 contribution.

In appreciation of those who support us, we offer the following:

- Contributions of $500 or more (or an equivalent promotional contribution): a dedicated acknowledgement, a featured placement on the sponsorship page of our website, and a logo displayed at three times the size of our $100-tier supporters.
- Contributions of $1,000 or more: all of the above, in addition to a speaking opportunity at our closing ceremony before all campers and their families, and an interactive logo in our website header presenting your company's details, a profile of the representative who championed the partnership, accompanying photographs, and your location.

As a non-profit, we direct 100% of all funds and donated resources toward our operating costs and reinvestment in the community. This includes our formal incorporation fees and campers' school supplies, the prizes for our student incentive program and final examination awards, daily meals for our volunteer student staff, and our end-of-program excellence awards recognizing outstanding students and staff. Any surplus is donated directly to local school boards.

Should this opportunity be of interest, I would be glad to provide further details and our promotional materials. Thank you for your time and consideration.

Sincerely,
Lucas Liu
Higher Grade Tutoring
lucas.liu.ca2009@gmail.com
+1 343-368-2005
highergradetutoring.ca
""",
    },
}

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
        if org_type in EMAIL_TEMPLATES and org_type != "Commercial":
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


def order_by_location(sponsors):
    """Reorder the pending sponsors into the location-interleaved queue.

    Locations are drawn in sheet order within each town. If a town has no one
    left when its turn comes up, that slot is simply skipped. Any sponsor whose
    Location isn't one of the five known towns is appended at the very end."""
    keymap = {
        LOC_OAKVILLE.lower():     "oak",
        LOC_MISSISSAUGA.lower():  "miss",
        LOC_BURLINGTON.lower():   "burl",
        LOC_MILTON.lower():       "mil",
        LOC_HALTON_HILLS.lower(): "hal",
    }
    keymap.update(LOCATION_ALIASES)  # fold in known misspellings
    buckets = {"oak": [], "miss": [], "burl": [], "mil": [], "hal": [], "other": []}
    for s in sponsors:
        buckets[keymap.get(s["location"].lower(), "other")].append(s)

    named = ("oak", "miss", "burl", "mil", "hal")
    ordered = []
    gen = _pattern_tokens()
    while any(buckets[k] for k in named):
        tok = next(gen)
        if buckets[tok]:
            ordered.append(buckets[tok].pop(0))

    # Unknown / blank locations didn't fit the pattern — send them last.
    if buckets["other"]:
        print(f"  ℹ️  {len(buckets['other'])} sponsor(s) have an unrecognized Location "
              f"— they'll be sent last.")
        ordered.extend(buckets["other"])

    return ordered

# ─────────────────────────────────────────────
# ROW HIGHLIGHTS
# ─────────────────────────────────────────────
_HL_FIRST_COL = 1    # column B, 0-based
_HL_LAST_COL  = 12   # column L exclusive, 0-based


def email_is_valid(addr):
    """A usable email needs both an @ and a dot."""
    return "@" in addr and "." in addr


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
    sheet.spreadsheet.batch_update({"requests": reqs})


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
    sheet.batch_update([
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
    sheet.batch_update(
        [{"range": f"{COL_TYPE}{s['row']}", "values": [[s["type"]]]}
         for s in changes],
        value_input_option="USER_ENTERED")
    return len(changes)


def highlight_duplicate(sheet, row_number):
    # Paints the Email cell of a duplicate "Not Contacted" row yellow so you can
    # spot it at a glance. We leave its Status alone so you can decide what to do.
    sheet.format(
        f"{COL_EMAIL}{row_number}",
        {"backgroundColor": {"red": 1, "green": 1, "blue": 0}},
    )


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
    dup_count = 0
    for s in pending:
        key = s["email"].lower()
        if key in seen:
            print(f"  🟡 [DUP] Row {s['row']}: {s['name']} <{s['email']}> "
                  f"already emailed — highlighting yellow, skipping.")
            if not TEST_MODE:
                highlight_duplicate(sheet, s["row"])
            dup_count += 1
            continue
        seen.add(key)
        to_send.append(s)
    print(f"   {dup_count} duplicate(s) skipped, {len(to_send)} to send.\n")

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
    #    towns are skipped, not stalled; only unknown/'Whatever' go last) ──
    sponsors = order_by_location(to_send)

    if not sponsors:
        print("Nothing to send. (Everyone is already contacted, a duplicate, or has no email.)")
        STATUS.update(state="done", message="Nothing to send.")
        return

    today_str = datetime.now().strftime("%a %B %d")  # e.g. "Fri June 05"
    total = len(sponsors)
    print(f"⏳ Queue mode: random {MIN_GAP_MIN:.0f}-{MAX_GAP_MIN:.0f} min between "
          f"emails. Batches of {BATCH_START_SIZE}, +{BATCH_STEP_SIZE} each, "
          f"resting {REST_START_MIN//60}h then shrinking by {REST_STEP_MIN} min. "
          f"{total} to send.")
    print("   Keep this script running the whole time. (Ctrl+C to stop.)\n")

    sent = 0
    failed = 0
    replies_sent = 0
    sent_in_batch = 0
    batch_size = BATCH_START_SIZE
    rest_min = REST_START_MIN
    last_break = time.time()        # clock for the mandatory 12h break
    idx = 0                         # next outreach sponsor index
    STATUS.update(state="running", total=total, sent=0, failed=0,
                  sent_in_batch=0, batch_size=batch_size, resting_until=0.0)

    # Keep running until Stop. Queued replies are sent FIRST at each slot; once
    # the outreach list is exhausted the loop idles, still flushing replies.
    while True:
        if not wait_while_paused():
            print("\n⏹ Stopped by user.")
            break

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
            except Exception as e:
                failed += 1
                print(f"  ❌ Reply to {reply.get('to_email')} FAILED — {e}")

        elif idx < total:
            # 2) Next outreach email.
            sponsor = sponsors[idx]
            idx += 1
            STATUS["current"] = sponsor["name"]
            template = EMAIL_TEMPLATES[sponsor["type"]]
            greeting = f"Dear {sponsor['contact']}," if sponsor["contact"] else f"Dear {sponsor['name']} Team,"
            subject = template["subject"].format(name=sponsor["name"])
            body = template["body"].format(name=sponsor["name"], greeting=greeting)
            if sponsor["note"]:
                body += f"\nP.S. {sponsor['note']}\n"
            to_email = ZOHO_EMAIL if TEST_MODE else sponsor["email"]
            try:
                send_email(to_email, subject, body)
                loc = sponsor["location"] or "—"
                print(f"  ✅ [{idx}/{total}] Sent [{loc}/{sponsor['type']}] → {sponsor['name']} <{to_email}>")
                sent += 1
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
            last_break = time.time()       # idle time doesn't count toward 12h
            interruptible_sleep(20)
            continue

        sent_in_batch += 1
        STATUS.update(state="running", sent=sent, failed=failed,
                      sent_in_batch=sent_in_batch, batch_size=batch_size)

        # ── Pacing before the next send ──
        now = time.time()
        if sent_in_batch >= batch_size:
            wait_min = max(0, rest_min)            # the (shrinking) batch rest
            sent_in_batch = 0
            batch_size += BATCH_STEP_SIZE
            rest_min -= REST_STEP_MIN               # no floor — can reach 0
            label = f"batch done → resting {wait_min:.0f} min"
        else:
            wait_min = random.uniform(MIN_GAP_MIN, MAX_GAP_MIN)
            label = f"{sent_in_batch}/{batch_size} this batch"

        # Mandatory 1h break every 12h of active sending.
        if now - last_break >= MANDATORY_BREAK_EVERY_HOURS * 3600:
            wait_min = max(wait_min, MANDATORY_BREAK_MINUTES)
            print(f"\n🌙 {MANDATORY_BREAK_EVERY_HOURS}h of sending — taking a "
                  f"{MANDATORY_BREAK_MINUTES}-min break.\n")

        wait_secs = wait_min * 60
        if wait_secs >= 60:
            STATUS.update(state="resting", resting_until=now + wait_secs)
        print(f"     …waiting {wait_min:.1f} min ({label}).")
        interruptible_sleep(wait_secs)
        STATUS.update(state="running", resting_until=0.0)
        if wait_secs >= (MANDATORY_BREAK_MINUTES - 5) * 60:
            last_break = time.time()                # a long rest resets the 12h clock

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
