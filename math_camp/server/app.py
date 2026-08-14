"""HigherGrade Tutoring API — Flask + SQLite.

Replaces the browser-localStorage data layer with a real server-side store.
All endpoints are mounted under /api/* so Caddy can reverse-proxy just that
prefix while continuing to serve the static site directly.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import smtplib
import time
from email.message import EmailMessage
from functools import wraps

from flask import Flask, g, jsonify, request, make_response

import clicker as clicker_econ
import crypto
import dungeon
import floors
import stripe_pay
from db import (
    connect, init_db,
    row_to_student, row_to_class, row_to_role,
    row_to_basestat, row_to_tx, row_to_staff,
    STUDENT_ENC_FIELDS, REGISTRATION_ENC_FIELDS,
)

ADMIN_PASSCODE = os.environ.get("HIGHERGRADE_ADMIN_PASSCODE", "HigherGrade Tutoring")

# Shared secret the Discord bot sends as `Authorization: Bearer <token>`.
# Generate with `python3 -c "import secrets; print(secrets.token_hex(32))"`
# and put it in /etc/highergrade.env (BOT_API_TOKEN=…) on the VM, then
# the same value into the bot's environment.
BOT_API_TOKEN = os.environ.get("HIGHERGRADE_BOT_TOKEN", "")

# Passphrase gate for the bulk registration CSV export — the high-risk
# "download everyone's data incl. camp passwords" action, and the one place
# encrypted PII is decrypted in bulk. On top of the admin session, the export
# refuses to run unless this matches. Set HG_EXPORT_PASSPHRASE in the VM env.
EXPORT_PASSPHRASE = os.environ.get("HG_EXPORT_PASSPHRASE", "")

# ── SMTP / email config ─────────────────────────────────────────────
# All four are pulled from the systemd EnvironmentFile (/etc/highergrade.env)
# on the VM so the Gmail app password never lands in git. Gmail SMTP defaults.
SMTP_HOST       = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT       = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER       = os.environ.get("SMTP_USER", "")
SMTP_PASS       = os.environ.get("SMTP_PASS", "")
SMTP_FROM       = os.environ.get("SMTP_FROM",
                                 "HigherGrade Tutoring <h.ghergradetutor.ng@gmail.com>")
ORGANIZER_EMAIL = os.environ.get("ORGANIZER_EMAIL", "lucas.liu.ca2009@gmail.com")
SITE_URL        = os.environ.get("SITE_URL", "https://highergradetutoring.ca")
COOKIE_NAME    = "hg_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days
SECURE_COOKIE  = os.environ.get("HIGHERGRADE_SECURE_COOKIE", "1") != "0"

# Field constants ported from students-data.js
STAT_FIELD_KEYS = [
    "privatePoints", "totalPointsEarned", "luck", "perfectScores",
    "classAnswers", "pointExchanges", "bathroomVisits", "badWords",
    "clickerClicks", "clickerPointsEarned", "spiderShown",
    # Dungeon economy. `shards` is the dungeon currency; `luckSpent` is the
    # running total of points sunk into luck, which the discovery roll needs.
    "shards", "luckSpent",
    # The clicker's fractional carry — see clicker.accrue.
    "clickerBank",
]
LUCK_COST              = 200
CLICKER_RATE           = 100
# Duplication is a roll on every click, not a milestone — see
# clicker.duplicate_chance. Clicking is one way to earn more clickers;
# buying them is the other.
# ...and only so many times. A clicker is permanent passive income, and the
# click COUNT has no daily cap (only the points do), so an unbounded ladder
# would let one afternoon of clicking print points for the rest of camp.
# Clickers staff grant by hand, and clickers bought with points, are not
# affected by this ceiling — it only bounds the free ones.
CLICKER_DUPLICATE_MAX   = clicker_econ.DUPLICATE_LIMIT
# How often the open tab settles production. Production is continuous and
# the remainder is banked, so this only affects how quickly a camper SEES
# their points arrive — never how many they get.
CLICKER_POLL_SEC        = 60
TRANSFER_KEEP_RATIO    = 0.5
# Once a camper has taken the final tally, transferring is the only thing
# they can still do — and it's worse. 40% arrives instead of 50%, and the
# lossless buy-out is gone.
TALLIED_TRANSFER_KEEP_RATIO = 0.4
# Pay this flat fee on a transfer and the recipient gets 100% instead of
# the usual 50%. Charged on top of the amount sent, and it goes to the
# transactions bank exactly like the tax it replaces.
LOSSLESS_TRANSFER_COST = 400
# Seconds a person must wait between passcode attempts on one chest, when
# the chest doesn't specify its own. Stops a short code being brute-forced
# by hammering the button.
CHEST_DEFAULT_COOLDOWN = 15
CHEST_MAX_COOLDOWN     = 3600
# Ceiling on how many files one chest's record tracks. Well past anything
# reasonable — it exists so a runaway loop can't grow a row without bound.
CHEST_MAX_ATTACHMENTS  = 200
SPIDER_THRESHOLD       = 20
CLASS_POINT_TO_INDIV   = 10
CLASS_BANK_DAILY_RATE  = 0.05
TX_MAX                 = 2000
# Combined size of the whole sponsors blob (all sponsors + their base64 logos and
# photos) stored in one meta row. Generous headroom for ~30 sponsors; bump freely.
SPONSORS_MAX_BYTES     = 50 * 1024 * 1024
MAZEWIZ_ROLE_ID        = "mazewiz"
MONEY_TREE_ROLE_ID     = "money_tree"
CLICKER_ROLE_ID        = "clicker"
CRANE_ROLE_ID          = "crane"
CRANE_GLOBAL_LIMIT     = None    # unlimited — any student who completes the claim flow gets one
# The lime trial: holders of the Calamity Catalyst see a lime bubble on the
# Support Us page, and cutting half of the 200 limes reforges the Lime Sword.
# Keep LIME_TOTAL/LIME_TARGET in step with TOTAL/TARGET in lime-challenge.js —
# a client that passes on numbers the server doesn't accept can't claim.
CALAMITY_ROLE_ID       = "calamity_catalyst"
LIME_SWORD_ROLE_ID     = "lime_sword"
LIME_TOTAL             = 200
LIME_TARGET            = 100
# Cut every single one and the run is a full combo: a one-time 1000-point
# bounty and the osu Champion role. Awarded once — the role is the receipt,
# so a second perfect run re-earns nothing.
OSU_ROLE_ID            = "osu_champion"
LIME_FC_POINTS         = 1000
# Per-lime score runs 1000 (cut the instant it appears) down to 400 (cut just
# before it fades), so these bracket any honest run of `hits` limes.
LIME_MIN_PER_HIT       = 400
LIME_MAX_PER_HIT       = 1000
DOOR_MAZE_LENGTH       = 310
MONEY_TREE_COST        = 6000

# ── Pay-at-a-sponsor-location option ─────────────────────────────────
# Families can pay their registration fee in person at one of these partner
# stores. Doing so earns an EXTRA 5% off, compounded on top of any discount
# already applied (i.e. 5% of the remaining amount, not +5 percentage points).
SPONSOR_PAY_LOCATIONS = {
    "game_time": {
        "name": "Game Time Collectibles",
        "url": "https://www.google.com/maps/place/Game+Time+Collectibles/@43.51261,-79.6439848,17z/data=!3m1!4b1!4m6!3m5!1s0x882b452c1180140b:0x839ae84e50cf34be!8m2!3d43.51261!4d-79.6414099!16s%2Fg%2F11h7cqqv2m",
    },
    "max_faucets": {
        "name": "Max Faucets",
        "url": "https://www.google.com/maps/place/MAX+Faucets/@43.4339484,-79.7028398,17z/data=!3m1!4b1!4m6!3m5!1s0x882b5f148535c175:0x75750f162da469d9!8m2!3d43.4339484!4d-79.7002649!16s%2Fg%2F11jcl6xs9r",
    },
}
SPONSOR_PAY_DISCOUNT = 0.05


def _sponsor_loc_name(loc):
    e = SPONSOR_PAY_LOCATIONS.get(loc or "")
    return e["name"] if e else None


def _sponsor_loc_url(loc):
    e = SPONSOR_PAY_LOCATIONS.get(loc or "")
    return e["url"] if e else None

# Tiered reward for completing the 300-door math maze. The pct is
# (correct / scoredDoors) * 100. The scored-door count is MAZE_LENGTH-1
# because the first floor is a freebie. Each tier is the previous
# reward × 1.5 + 300, with a 300-pt floor for any completion below 50%.
DOOR_REWARD_TIERS = [
    (100, 3450),
    ( 95, 3150),
    ( 90, 2850),
    ( 85, 2550),
    ( 80, 2250),
    ( 75, 1950),
    ( 70, 1650),
    ( 65, 1350),
    ( 60, 1050),
    ( 55,  750),
    ( 50,  450),
]
DOOR_REWARD_FLOOR = 300

# The maze pays shards too, not just the one-time points.
#
# It's presented as a dungeon descent and every floor of the actual dungeon
# pays shards, so a 310-door descent paying nothing was simply a hole. Paid
# per CORRECT door, so a sloppy run pays less, and paid on EVERY completion
# — the maze is deliberately replayable for the Money Tree hunt, and a
# reward that only ever lands once isn't a reason to go back down.
#
# The rate is deliberately below the dungeon's. A perfect descent is ~12,400
# shards, about what a 60-floor dungeon run pays — and the maze is far
# easier per question, so it should not be the better way to earn.
MAZE_SHARDS_PER_DOOR = 40
# There is no cap. Run the maze all day and get paid for all of it.
#
# What there IS: the descent has to have actually taken place. `correct`
# and `total` come from the browser, so without something anchoring them a
# repeatable payout is just an endpoint you can POST in a loop. Rather than
# limiting how much an honest camper can earn — which is what a daily
# ceiling does — the server now times the descent itself: entering the maze
# stamps a start, and a claim only pays if enough time has passed since
# that stamp for the doors to have been walked.
#
# 180 seconds over 309 scored doors is 0.58s a door. Nobody reads a
# question, thinks, and clicks that fast, so this never touches real play —
# it only rules out a script. Reloading the page restarts the descent from
# door 1 anyway (the run lives in a JS variable), so re-stamping on entry
# costs a legitimate camper nothing.
MAZE_MIN_DESCENT_S = 180

# ── Playtest account ─────────────────────────────────────────────────
# "HGT TEST" is the throwaway camper an admin becomes when they enter
# playtest mode from the admin panel. It's a real students row (so every
# student feature behaves exactly as it does for a camper) but it has a
# fixed id, is never registerable, and is hidden from other campers.
PLAYTEST_STUDENT_ID = "hgt-test"
PLAYTEST_EMAIL      = "playtest@highergradetutoring.ca"

# ── Reserved emails ──────────────────────────────────────────────────
# burntout@gmail.com is a hidden door (the bedroom scene at /bedroom.html),
# not a real camper, and PLAYTEST_EMAIL belongs to the account above.
# Block both everywhere a student record could be created or matched so
# neither can accidentally end up registered — nor be wiped by a reset.
RESERVED_STUDENT_EMAILS = {"burntout@gmail.com", PLAYTEST_EMAIL}


def _is_reserved_email(email):
    return (email or "").strip().lower() in RESERVED_STUDENT_EMAILS


def _normalize_role_name(name):
    """Normalize a role name for case + whitespace + 'Camp · ' prefix
    insensitive matching between Discord roles and camp roles."""
    if not name:
        return ""
    n = name.lower().strip()
    for prefix in ("camp · ", "camp - ", "camp: ", "camp ", "camp·", "camp:"):
        if n.startswith(prefix):
            n = n[len(prefix):].strip()
            break
    return "".join(c for c in n if not c.isspace())

# ── Vulgar Vault — staff-controlled rotating-code stash ──────────────
# Admin-applied penalties (manual deductions for bad activities) deposit
# the lost points into this vault. Students can drain the entire vault
# from a widget at the bottom of the leaderboard if they enter the
# current 5-digit code, which rotates every minute and is only visible
# to staff in the admin panel.
VULGAR_VAULT_PERIOD     = 60        # seconds — how often the code rotates
VULGAR_VAULT_CODE_LEN   = 5
VULGAR_VAULT_GRACE      = 1         # minutes of look-back accepted on claim
# The HMAC key is derived from the admin passcode by default so the
# code is not predictable from public knowledge alone, but admins
# can override via env var if they want.
VULGAR_VAULT_SECRET = os.environ.get(
    "HIGHERGRADE_VAULT_SECRET",
    "vulgar-vault::" + ADMIN_PASSCODE,
).encode("utf-8")


def _vulgar_code(minute=None):
    """Return the 5-digit rotating code for the given epoch-minute.
    Defaults to the current minute. The same minute always yields the
    same code, so admins and students see matching values within a
    single rotation window."""
    if minute is None:
        minute = int(time.time()) // VULGAR_VAULT_PERIOD
    digest = hmac.new(VULGAR_VAULT_SECRET, str(minute).encode("ascii"), hashlib.sha256).digest()
    n = int.from_bytes(digest[:4], "big") % (10 ** VULGAR_VAULT_CODE_LEN)
    return f"{n:0{VULGAR_VAULT_CODE_LEN}d}"

def reward_for_pct(pct):
    for cutoff, pts in DOOR_REWARD_TIERS:
        if pct >= cutoff:
            return pts
    return DOOR_REWARD_FLOOR
# Manual clicking keeps its daily cap. It is not a production limit — it's
# what stops an auto-clicker script from turning a browser tab into an
# income. The clickers you BUY have no cap; they're paced by their rate.
MANUAL_DAILY_CAP       = 50              # max manual-clicker pts per UTC day


def default_stats():
    return {
        "privatePoints": 0, "totalPointsEarned": 0, "luck": 0,
        "perfectScores": 0, "classAnswers": 0, "pointExchanges": 0,
        "bathroomVisits": 0, "badWords": 0,
        "clickerClicks": 0, "clickerPointsEarned": 0, "spiderShown": False,
        "shards": 0, "luckSpent": 0, "clickerBank": 0.0,
        # The shards→points cash-out is once per camper, all of it at once.
        "cashedOut": False,
    }


# ── App factory ───────────────────────────────────────────────────────

def create_app():
    app = Flask(__name__)
    init_db()

    @app.before_request
    def _open_db():
        g.db = connect()

    @app.teardown_request
    def _close_db(exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.errorhandler(404)
    def _nf(e):
        return jsonify(ok=False, error="Not found"), 404

    @app.errorhandler(500)
    def _ise(e):
        app.logger.exception("server error")
        return jsonify(ok=False, error="Server error"), 500

    register_routes(app)
    return app


# ── Email helper ──────────────────────────────────────────────────────

# Pragmatic address shape check — not RFC-exhaustive, but rejects the
# typos we actually see (missing @, missing domain, missing TLD, spaces).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _looks_like_email(addr):
    return bool(_EMAIL_RE.match((addr or "").strip()))


def send_email(to, subject, body, reply_to=None):
    """Best-effort SMTP send. Never raises — the caller's HTTP path is
    always safe. Returns a status string the caller can branch on:
        "sent"      – the server accepted the message
        "no_config" – SMTP creds aren't configured (our side)
        "refused"   – the recipient address was rejected by the server,
                      i.e. it doesn't exist / can't receive mail (their side)
        "error"     – any other SMTP / network failure (our side)
    Only "refused" should ever block a user flow; the rest are our problem
    and must stay non-blocking so an outage can't break registration."""
    if not SMTP_USER or not SMTP_PASS:
        try:
            from flask import current_app
            current_app.logger.warning(
                "SMTP send to %s skipped: SMTP_USER/SMTP_PASS not configured "
                "(set them in /etc/highergrade.env and restart highergrade-api).",
                to,
            )
        except Exception:
            pass
        return "no_config"
    msg = EmailMessage()
    msg["From"]    = SMTP_FROM
    msg["To"]      = to
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        return "sent"
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPNotSupportedError) as e:
        # The server rejected the recipient outright — treat as a bad address.
        try:
            from flask import current_app
            current_app.logger.warning("SMTP recipient %s refused: %s", to, e)
        except Exception:
            pass
        return "refused"
    except smtplib.SMTPResponseException as e:
        # 5xx aimed at the recipient (e.g. 550 no such user) → bad address.
        # Anything else (auth, rate, connection) is our side → non-blocking.
        if 500 <= getattr(e, "smtp_code", 0) < 600:
            try:
                from flask import current_app
                current_app.logger.warning("SMTP send to %s refused: %s", to, e)
            except Exception:
                pass
            return "refused"
        try:
            from flask import current_app
            current_app.logger.warning("SMTP send to %s failed: %s", to, e)
        except Exception:
            pass
        return "error"
    except Exception as e:  # noqa: BLE001 — swallow everything else (our side)
        try:
            from flask import current_app
            current_app.logger.warning("SMTP send to %s failed: %s", to, e)
        except Exception:
            pass
        return "error"


# ── Discount codes ─────────────────────────────────────────────────────────
# Codes are stored in the discount_codes table and matched case-insensitively,
# ignoring all whitespace ("Higher Grade Report Card" == "highergradereportcard").
# Evergreen codes (multiUse = 1) always work; single-use codes are consumed on
# first use. See schema.sql for the seeded evergreen 15% code.
def _normalize_code(code):
    return "".join((code or "").split()).lower()


def _lookup_discount(db, code):
    """Return the discount_codes row for a code if it's currently usable
    (exists, and either evergreen or not yet used), else None."""
    norm = _normalize_code(code)
    if not norm:
        return None
    row = db.execute("SELECT * FROM discount_codes WHERE code = ?", (norm,)).fetchone()
    if not row or (row["used"] and not row["multiUse"]):
        return None
    return row


def _consume_discount(db, code, used_by):
    """Mark a single-use code as used so it can't be redeemed again. Evergreen
    codes (multiUse = 1) are left untouched."""
    db.execute(
        "UPDATE discount_codes SET used = 1, usedAt = ?, usedBy = ? "
        "WHERE code = ? AND multiUse = 0 AND used = 0",
        (int(time.time()), used_by, _normalize_code(code)),
    )


def _gen_crane_code(db):
    """Mint, store, and return a fresh single-use 10% 'crane' code (label form)."""
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no easily-confused characters
    for _ in range(20):
        label = "CRANE-" + "".join(secrets.choice(alphabet) for _ in range(5))
        norm = _normalize_code(label)
        if not db.execute("SELECT 1 FROM discount_codes WHERE code = ?", (norm,)).fetchone():
            db.execute(
                "INSERT INTO discount_codes (code, label, percent, multiUse, used, createdAt, kind) "
                "VALUES (?, ?, 0.10, 0, 0, ?, 'crane')",
                (norm, label, int(time.time())),
            )
            return label
    return None


# ── Referral codes ──────────────────────────────────────────────────────────
# Every camper gets a permanent, never-expiring 6-digit code to share; every
# teacher gets one too (admin-only). Codes are unique across BOTH the
# registrations and staff tables so a single code someone enters at
# registration resolves unambiguously to exactly one referrer.
REFERRAL_CODE_LEN = 6


def _referral_code_taken(db, code):
    return bool(
        db.execute("SELECT 1 FROM registrations WHERE referralCode = ?", (code,)).fetchone()
        or db.execute("SELECT 1 FROM staff WHERE referralCode = ?", (code,)).fetchone()
    )


def _gen_referral_code(db, avoid=None):
    """Mint a fresh unique 6-digit referral code (zero-padded, e.g. '004217').
    `avoid` is an optional set of codes already handed out in the current
    batch but not yet committed (used when re-seeding all staff at once)."""
    avoid = avoid or set()
    for _ in range(60):
        code = f"{secrets.randbelow(10 ** REFERRAL_CODE_LEN):0{REFERRAL_CODE_LEN}d}"
        if code in avoid or _referral_code_taken(db, code):
            continue
        return code
    return None


def _normalize_referral_input(code):
    """A user-entered referral code: strip whitespace and any stray non-digits
    (people paste '004-217' or '004 217'), keep digits only."""
    return "".join(ch for ch in (code or "") if ch.isdigit())


def _resolve_referral_code(db, code):
    """Resolve an entered code to its owner, or None if it matches nobody.
    Returns {'type': 'camper'|'teacher', 'name': str, 'code': str}."""
    code = _normalize_referral_input(code)
    if len(code) != REFERRAL_CODE_LEN:
        return None
    r = db.execute(
        "SELECT firstName, lastName FROM registrations WHERE referralCode = ?", (code,)
    ).fetchone()
    if r:
        fn = (crypto.dec(r["firstName"]) or "").strip()
        ln = (crypto.dec(r["lastName"]) or "").strip()
        name = f"{fn} {ln}".strip()
        return {"type": "camper", "name": name or "A camper", "code": code}
    s = db.execute("SELECT name FROM staff WHERE referralCode = ?", (code,)).fetchone()
    if s:
        return {"type": "teacher", "name": (s["name"] or "").strip() or "A teacher", "code": code}
    return None


def _fmt_amount(amount):
    """Money string: '$135 CAD' for whole dollars, '$114.75 CAD' with cents,
    or a generic phrase when the amount is unknown."""
    if not amount:
        return "your registration fee"
    return f"${int(amount)} CAD" if float(amount).is_integer() else f"${amount:.2f} CAD"


def _security_notice():
    """Reusable reassurance about how card + personal data is protected. Added
    to the registration and payment emails so families know their information
    and card details are encrypted."""
    return (
        "🔒 How your information is protected\n"
        "• Card payments are handled by Stripe, a PCI-DSS Level 1 certified\n"
        "  processor (the highest security level in the industry). Your full card\n"
        "  number is encrypted the instant you enter it and goes straight to\n"
        "  Stripe — it never passes through or is stored on our servers, and our\n"
        "  staff never see it.\n"
        "• Everything you submit reaches us over an encrypted HTTPS/TLS\n"
        "  connection, so it can't be read in transit.\n"
        "• Your personal details — names, email, phone, date of birth, school,\n"
        "  medical notes and emergency contacts — are encrypted at rest in our\n"
        "  database with AES-based (Fernet) encryption, unreadable without our\n"
        "  private key.\n"
        "• We never sell or share your information; only camp staff who need it\n"
        "  can access it.\n\n"
    )


def _referral_free_deal(amount_str):
    """The "refer 4 → camp is free" deal, shown to families paying by e-Transfer
    or in person (not to card payers, who've already paid in full)."""
    return (
        "🎉 Here's a deal — get camp for FREE!\n"
        f"Refer 4 friends who register and pay, and we'll refund your entire\n"
        f"{amount_str} fee — your camper attends completely free. Just have each\n"
        "friend enter YOUR email address in the \"Were you referred?\" box when\n"
        "they sign up. (You also earn $20 for every friend you refer along the\n"
        "way, so there's a reward even before you hit 4.)\n\n"
    )


def _send_registration_confirm(student_email, name, parent_email=None, amount=None,
                               referral_code=None):
    """Sent right after registration — for EVERY registrant, so it must stay
    payment-method-neutral (someone who pays instantly by card should not be
    told to e-Transfer). It confirms the registration, points to the payment
    options on the confirmation screen, and reassures on data security. The
    method-specific instructions (with the referral deal) go out separately
    from _send_payment_instructions() only once the family picks e-Transfer /
    cash / in-person; card payers get _send_camp_welcome() instead.

    Returns the send status for the registrant's address ("sent", "refused",
    "no_config", "error") so the caller can detect a bad email."""
    subject = "Thanks for registering — choose how to pay to hold your spot"
    body = (
        f"Hi {name or 'there'},\n\n"
        f"Thank you for registering for HigherGrade Tutoring's Summer Camp 2026!\n"
        f"We've received your camper's registration.\n\n"
        f"Your spot is HELD but not fully secured until payment is complete. On\n"
        f"the confirmation screen right after registering, you can:\n\n"
        f"   💳 Pay instantly by card — your camper's account unlocks right away, or\n"
        f"   📧 Choose e-Transfer, cash on Day 1, or paying in person at a sponsor —\n"
        f"      pick one and we'll email you the full instructions (plus a deal to\n"
        f"      get camp completely free!).\n\n"
        f"Until payment is confirmed, your camper's account stays FROZEN — they\n"
        f"won't be able to sign in to the student portal yet. As soon as you're\n"
        f"paid up, we'll send a second email with everything you need to know:\n"
        f"camp dates, location, what to bring, the parent info session, and how to\n"
        f"log in.\n\n"
        f"{_security_notice()}"
        f"Questions? Just reply to this email — it goes straight to the organizers.\n\n"
        f"Thanks again, and talk soon!\n"
        f"— The HigherGrade Tutoring team\n"
    )
    status = "no_config"
    if student_email:
        status = send_email(student_email, subject, body, reply_to=ORGANIZER_EMAIL)
    if parent_email and parent_email.lower() != (student_email or "").lower():
        parent_status = send_email(parent_email, subject, body, reply_to=ORGANIZER_EMAIL)
        if not student_email:
            status = parent_status
    return status


def _send_camp_welcome(student_email, name, parent_email=None, free=False):
    """Sent when a camper's account is unlocked — either an admin confirmed
    payment, they paid by card, or (free=True) they registered while camp is
    running FREE. Carries all of the camp logistics. Returns the send status
    for the registrant's address so callers that need deliverability (the free
    registration path) can detect a bad email."""
    subject = "You're all set for HigherGrade Tutoring Summer Camp 2026 🎉"
    if free:
        opening = (
            f"Great news — your camper's spot is confirmed, and camp is completely\n"
            f"FREE this year! No payment needed. Their student-portal account is\n"
            f"already unlocked. Here's everything you need to know.\n\n"
        )
    else:
        opening = (
            f"Great news — we've confirmed your payment and your camper's spot is\n"
            f"officially secured! Their student-portal account is now unlocked. Here's\n"
            f"everything you need to know.\n\n"
        )
    body = (
        f"Hi {name or 'there'},\n\n"
        f"{opening}"
        f"📅 Camp dates: August 4 – August 15, 2026\n"
        f"   Week 1 (Tue–Fri): Aug 4, 5, 6, 7\n"
        f"   Week 2 (Mon–Sat): Aug 10, 11, 12, 13, 14, 15\n"
        f"⏰ Hours: 8:45 AM – 4:45 PM daily\n"
        f"📍 Location: Sheridan College — Trafalgar Campus, 1430 Trafalgar Road, Oakville, ON\n"
        f"   Directions: https://www.google.com/maps/dir/?api=1&destination=Sheridan+College+Trafalgar+Campus%2C+1430+Trafalgar+Road%2C+Oakville%2C+ON\n\n"
        f"📺 Parent / camper info session — video meeting\n"
        f"We're hosting a kickoff video meeting at 10:00 AM EST on Saturday, July 18, 2026.\n"
        f"We'll walk through the daily schedule, drop-off / pick-up logistics, what to\n"
        f"bring, and answer any questions you have. The meeting link will be emailed to\n"
        f"this address closer to the date — please mark your calendar.\n\n"
        f"A few things to know:\n"
        f"• 🍱 Lunch is NOT provided — every camper must bring their own lunch\n"
        f"  each day. Please pack something they'll enjoy for the lunch break.\n"
        f"• Please also bring a device (laptop, tablet, or Chromebook) and a water\n"
        f"  bottle each day. All math materials are provided.\n"
        f"• Sign in to your dashboard at {SITE_URL}/student-portal/student-portal.html\n"
        f"  to track points, see your class, and find the hidden mini-game.\n"
        f"• Questions? Reply to this email — it goes straight to the organizers.\n\n"
        f"See you August 4!\n"
        f"— The HigherGrade Tutoring team\n"
    )
    status = "no_config"
    if student_email:
        status = send_email(student_email, subject, body, reply_to=ORGANIZER_EMAIL)
    if parent_email and parent_email.lower() != (student_email or "").lower():
        parent_status = send_email(parent_email, subject, body, reply_to=ORGANIZER_EMAIL)
        if not student_email:
            status = parent_status
    return status


def _send_payment_instructions(method, student_email, name, parent_email=None,
                               amount=None, sponsor_name=None):
    """Sent once a family SELECTS a non-card payment method (e-Transfer, cash on
    Day 1, or paying in person at a sponsor). Carries the how-to-pay details,
    the "refer 4 → free" deal, and the security notice. Card payers never get
    this — they've already paid, and get _send_camp_welcome() instead."""
    amount_str = _fmt_amount(amount)
    if method == "e_transfer":
        subject = "How to pay for HigherGrade Camp — e-Transfer instructions"
        how = (
            f"Thanks for choosing to pay by e-Transfer! To secure your camper's\n"
            f"spot, please send {amount_str} by Interac e-Transfer to:\n\n"
            f"      {ORGANIZER_EMAIL}\n\n"
            f"⚠️ IMPORTANT — in the e-Transfer message / comment field, include:\n"
            f"   1. Your child's FULL NAME\n"
            f"   2. The HIGH SCHOOL they will be attending\n"
            f"That's how we match your payment to your registration.\n\n"
            f"Until we receive and confirm your e-Transfer (usually within 24\n"
            f"hours), your camper's account stays FROZEN.\n\n"
        )
    elif method == "cash":
        subject = "How to pay for HigherGrade Camp — cash on Day 1"
        how = (
            f"You're all set to pay {amount_str} in cash on Day 1 of camp. Please\n"
            f"bring it to check-in on the first morning. Until our staff collect\n"
            f"and confirm it, your camper's account stays FROZEN — once we do,\n"
            f"we'll unlock the account right away.\n\n"
        )
    elif method == "sponsor":
        where = sponsor_name or "our sponsor store"
        subject = f"How to pay for HigherGrade Camp — pay in person at {where}"
        how = (
            f"You've chosen to pay in person at {where}, with your extra 5% off\n"
            f"already applied — your total is {amount_str}. Head to the store, pay\n"
            f"at the counter, and mention your camper's FULL NAME. Until the\n"
            f"sponsor confirms your payment with us, your camper's account stays\n"
            f"FROZEN — once confirmed, we'll unlock it right away.\n\n"
        )
    else:
        return  # unknown method — nothing to send
    body = (
        f"Hi {name or 'there'},\n\n"
        f"{how}"
        f"{_referral_free_deal(amount_str)}"
        f"{_security_notice()}"
        f"Questions? Just reply to this email — it goes straight to the organizers.\n\n"
        f"Thanks, and talk soon!\n"
        f"— The HigherGrade Tutoring team\n"
    )
    if student_email:
        send_email(student_email, subject, body, reply_to=ORGANIZER_EMAIL)
    if parent_email and parent_email.lower() != (student_email or "").lower():
        send_email(parent_email, subject, body, reply_to=ORGANIZER_EMAIL)


# ── Auth helpers ──────────────────────────────────────────────────────

def _new_token():
    return secrets.token_urlsafe(32)


def _set_session_cookie(resp, token):
    resp.set_cookie(
        COOKIE_NAME, token,
        max_age=COOKIE_MAX_AGE,
        httponly=True, secure=SECURE_COOKIE,
        samesite="Lax", path="/",
    )
    return resp


def _clear_session_cookie(resp):
    resp.set_cookie(COOKIE_NAME, "", max_age=0, path="/")
    return resp


def _current_session():
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    row = g.db.execute(
        "SELECT * FROM sessions WHERE token = ?", (token,),
    ).fetchone()
    return dict(row) if row else None


def _playtest_admin_token(sess):
    """If `sess` is a playtest session (an admin wearing the student view),
    return the admin token to hand the cookie back to. Returns None for
    ordinary sessions, and also when the parent admin session has since
    expired or been logged out — in that case there's nothing to go back
    to and the playtest session is just a plain student session."""
    if not sess:
        return None
    token = sess.get("adminToken") if isinstance(sess, dict) else None
    if not token:
        return None
    row = g.db.execute(
        "SELECT token FROM sessions WHERE token = ? AND kind = 'admin'", (token,),
    ).fetchone()
    return row["token"] if row else None


def require_admin(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        s = _current_session()
        if not s or s["kind"] != "admin":
            return jsonify(ok=False, error="Admin authentication required"), 401
        return fn(*a, **kw)
    return wrapper


def require_student(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        s = _current_session()
        if not s or s["kind"] != "student" or not s["studentId"]:
            return jsonify(ok=False, error="Student authentication required"), 401
        # Frozen accounts can READ (GET) but every mutating request is
        # rejected until staff confirms the e-Transfer and unfreezes the
        # account. The UI also paints a blocking overlay; this is the
        # defense-in-depth check for direct-API attempts.
        if request.method != "GET":
            row = g.db.execute(
                "SELECT frozen FROM students WHERE id = ?",
                (s["studentId"],),
            ).fetchone()
            if row and row["frozen"]:
                return jsonify(
                    ok=False,
                    frozen=True,
                    error="Your camp account is pending payment confirmation — actions are disabled until our staff confirms the e-Transfer.",
                ), 423
        g.session = s
        return fn(*a, **kw)
    return wrapper


def require_bot(fn):
    """The Discord bot authenticates via a shared secret rather than a
    cookie. Reject every request unless BOT_API_TOKEN is configured AND
    the Authorization header carries it as a Bearer token. constant-time
    compare so we don't leak the token via timing."""
    @wraps(fn)
    def wrapper(*a, **kw):
        if not BOT_API_TOKEN:
            return jsonify(ok=False, error="Bot integration is disabled on this server."), 503
        header = request.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return jsonify(ok=False, error="Bot authentication required."), 401
        supplied = header[len(prefix):].strip()
        if not hmac.compare_digest(supplied, BOT_API_TOKEN):
            return jsonify(ok=False, error="Bad bot token."), 403
        return fn(*a, **kw)
    return wrapper


def _meta_get(key, default=None):
    row = g.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def _meta_set(key, value):
    g.db.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _eastern_today_str():
    """Today's date (YYYY-MM-DD) in America/Toronto, so the open-call day
    boundary follows our local 5-8 PM window rather than UTC."""
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Toronto")).strftime("%Y-%m-%d")
    except Exception:
        return datetime.utcnow().strftime("%Y-%m-%d")


def _call_schedule():
    """The open-call schedule as {date: {number, name}} from the meta store."""
    try:
        d = json.loads(_meta_get("open_call_schedule", "{}") or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _call_schedule_days(n=10):
    """The next `n` days (from today, Eastern) as a list of
    {date, label, number, name} — blank where nobody's signed up yet."""
    from datetime import datetime, timedelta
    try:
        from zoneinfo import ZoneInfo
        base = datetime.now(ZoneInfo("America/Toronto")).date()
    except Exception:
        base = datetime.utcnow().date()
    sched = _call_schedule()
    out = []
    for i in range(n):
        day = base + timedelta(days=i)
        ds = day.strftime("%Y-%m-%d")
        e = sched.get(ds) or {}
        out.append({"date": ds, "label": day.strftime("%a, %b %d"),
                    "number": (e.get("number") or ""),
                    "name": (e.get("name") or ""),
                    "discord_id": (e.get("discord_id") or "")})
    return out


def _call_schedule_upsert(entries):
    """Upsert/clear schedule entries ({date, number, name}); a blank number AND
    name clears that day. Prunes past days, then saves. Returns the new dict."""
    import re
    sched = _call_schedule()
    for item in entries:
        if not isinstance(item, dict):
            continue
        ds = str(item.get("date") or "").strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", ds):
            continue
        number = str(item.get("number") or "").strip()[:40]
        name = str(item.get("name") or "").strip()[:60]
        did = str(item.get("discord_id") or "").strip()[:32]
        if number or name:
            if not did:  # preserve an existing Discord id (e.g. on an admin edit)
                did = (sched.get(ds) or {}).get("discord_id", "")
            entry = {"number": number, "name": name}
            if did:
                entry["discord_id"] = did
            sched[ds] = entry
        else:
            sched.pop(ds, None)
    today = _eastern_today_str()
    sched = {k: v for k, v in sched.items() if k >= today}  # drop past days
    with g.db:
        _meta_set("open_call_schedule", json.dumps(sched))
    return sched


def _points_frozen():
    return _meta_get("points_frozen", "0") == "1"


def _camp_free():
    """True when the camp is running in FREE mode — registration costs $0, no
    payment step, and the camper's account unlocks immediately. Toggled by an
    admin from the dashboard."""
    return _meta_get("camp_free", "0") == "1"


def block_when_frozen(fn):
    """Reject the request with 423 Locked if point transactions are frozen.
    Admin sessions still bypass — they can edit transactions if needed."""
    @wraps(fn)
    def wrapper(*a, **kw):
        s = _current_session()
        is_admin = bool(s and s["kind"] == "admin")
        if not is_admin and _points_frozen():
            return jsonify(
                ok=False,
                frozen=True,
                error="Point transactions are currently frozen by an admin.",
            ), 423
        return fn(*a, **kw)
    return wrapper


# ── Route registry ────────────────────────────────────────────────────

def register_routes(app):

    # health
    @app.route("/api/health")
    def health():
        return jsonify(ok=True, ts=int(time.time()))

    # ── Public inbound-message endpoints ───────────────────────────
    # Saves to the contact_messages table — admins read them in the
    # /admin-messages.html dashboard rather than receiving an email.
    @app.route("/api/contact", methods=["POST"])
    def contact_form():
        data = request.get_json(silent=True) or {}
        name    = (data.get("name") or "").strip()
        email   = (data.get("email") or "").strip()
        org     = (data.get("org") or "").strip()
        type_   = (data.get("type") or "General Inquiry").strip()
        message = (data.get("message") or "").strip()
        if not name or not email or not message:
            return jsonify(ok=False, error="Name, email, and message are required."), 400
        if "@" not in email or len(message) > 5000 or len(name) > 200:
            return jsonify(ok=False, error="Invalid input."), 400
        mid = "msg-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(3)
        g.db.execute(
            """INSERT INTO contact_messages
               (id, createdAt, source, type, name, email, org, message, isRead)
               VALUES (?, ?, 'contact', ?, ?, ?, ?, ?, 0)""",
            (mid, int(time.time()), type_, name, email, org or None, message),
        )
        return jsonify(ok=True)

    @app.route("/api/sponsor-inquiry", methods=["POST"])
    def sponsor_inquiry():
        data = request.get_json(silent=True) or {}
        tier = (data.get("tier") or "").strip().lower()
        name = (data.get("name") or "").strip()
        email = (data.get("email") or "").strip()
        org = (data.get("org") or "").strip()
        notes = (data.get("notes") or "").strip()

        tier_titles = {
            "supporter": "Become a Supporter ($100+)",
            "partner":   "Become a Partner ($500+)",
            "title":     "Become Title Sponsor ($1,000+)",
        }
        title = tier_titles.get(tier, "Sponsorship inquiry")

        if not name or not email:
            return jsonify(ok=False, error="Name and email are required."), 400
        if "@" not in email or len(name) > 200:
            return jsonify(ok=False, error="Invalid input."), 400

        mid = "msg-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(3)
        g.db.execute(
            """INSERT INTO contact_messages
               (id, createdAt, source, type, name, email, org, message, isRead)
               VALUES (?, ?, 'sponsor', ?, ?, ?, ?, ?, 0)""",
            (mid, int(time.time()), title, name, email, org or None, notes or None),
        )
        return jsonify(ok=True)

    # ── Admin: contact-message inbox ───────────────────────────────
    @app.route("/api/admin/contact-messages", methods=["GET"])
    @require_admin
    def admin_contact_messages():
        rows = g.db.execute(
            "SELECT * FROM contact_messages ORDER BY createdAt DESC"
        ).fetchall()
        return jsonify(ok=True, messages=[dict(r) for r in rows])

    @app.route("/api/admin/contact-messages/<mid>", methods=["DELETE"])
    @require_admin
    def admin_contact_message_delete(mid):
        g.db.execute("DELETE FROM contact_messages WHERE id = ?", (mid,))
        return jsonify(ok=True)

    @app.route("/api/admin/contact-messages/<mid>/read", methods=["POST"])
    @require_admin
    def admin_contact_message_read(mid):
        d = request.get_json(silent=True) or {}
        is_read = 1 if d.get("read", True) else 0
        g.db.execute(
            "UPDATE contact_messages SET isRead = ? WHERE id = ?", (is_read, mid)
        )
        return jsonify(ok=True, read=bool(is_read))

    # ── Auth ───────────────────────────────────────────────────────
    @app.route("/api/auth/me", methods=["GET"])
    def auth_me():
        s = _current_session()
        if not s:
            return jsonify(ok=True, kind=None, student=None, playtest=False)
        playtest = bool(_playtest_admin_token(s))
        if s["kind"] == "student":
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (s["studentId"],)).fetchone()
            return jsonify(ok=True, kind="student", student=row_to_student(row),
                           playtest=playtest)
        return jsonify(ok=True, kind=s["kind"], student=None, playtest=playtest)

    @app.route("/api/auth/student/login", methods=["POST"])
    def auth_student_login():
        data = request.get_json(silent=True) or {}
        email = (data.get("email") or "").strip().lower()
        pwd   = data.get("password")
        if not email or pwd is None:
            return jsonify(ok=False, error="Email and password required"), 400
        # Reserved easter-egg emails never have a student record. The
        # bedroom-door client check on /student-portal.html handles them.
        if _is_reserved_email(email):
            return jsonify(ok=False, error="No matching account"), 401
        # Look up by email (blind index when encrypted), then check the
        # password in the app since it's stored encrypted, not queryable.
        clause, param = _email_clause(email)
        row = g.db.execute(
            f"SELECT * FROM students WHERE {clause}", (param,),
        ).fetchone()
        if not row or not hmac.compare_digest(str(crypto.dec(row["password"]) or ""), str(pwd)):
            return jsonify(ok=False, error="No matching account"), 401
        # Frozen accounts CAN sign in — the portal renders a blocking
        # overlay that locks out all actions until staff unfreezes them,
        # and require_student rejects mutating requests on the server.
        token = _new_token()
        g.db.execute(
            "INSERT INTO sessions (token, kind, studentId, createdAt) VALUES (?, 'student', ?, ?)",
            (token, row["id"], int(time.time())),
        )
        resp = make_response(jsonify(ok=True, student=row_to_student(row)))
        return _set_session_cookie(resp, token)

    @app.route("/api/auth/student/logout", methods=["POST"])
    def auth_student_logout():
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return _clear_session_cookie(make_response(jsonify(ok=True)))
        sess = _current_session()
        back = _playtest_admin_token(sess)
        g.db.execute("DELETE FROM sessions WHERE token = ?", (token,))
        if back:
            # Playtest session — "Log out" drops the admin back into the
            # admin panel rather than signing them out of everything.
            resp = make_response(jsonify(ok=True, playtest=True, returnTo="/admin/admin.html"))
            return _set_session_cookie(resp, back)
        return _clear_session_cookie(make_response(jsonify(ok=True)))

    # ── Playtest mode ──────────────────────────────────────────────
    # Lets an admin see the site exactly as a camper does, signed in as
    # the reserved "HGT TEST" account, without giving up their admin
    # session — pressing Esc anywhere swaps the cookie straight back.
    @app.route("/api/auth/playtest/start", methods=["POST"])
    @require_admin
    def auth_playtest_start():
        admin_token = request.cookies.get(COOKIE_NAME)
        row = _ensure_playtest_student()
        token = _new_token()
        g.db.execute(
            "INSERT INTO sessions (token, kind, studentId, createdAt, adminToken)"
            " VALUES (?, 'student', ?, ?, ?)",
            (token, PLAYTEST_STUDENT_ID, int(time.time()), admin_token),
        )
        resp = make_response(jsonify(ok=True, student=row_to_student(row), playtest=True))
        return _set_session_cookie(resp, token)

    @app.route("/api/auth/playtest/stop", methods=["POST"])
    def auth_playtest_stop():
        sess = _current_session()
        back = _playtest_admin_token(sess)
        if not back:
            # Not in playtest (or the admin session expired while we were
            # in there). Nothing to restore — say so and let the client
            # send the user to the passcode gate.
            return jsonify(ok=False, error="Not in playtest mode"), 409
        g.db.execute("DELETE FROM sessions WHERE token = ?", (sess["token"],))
        resp = make_response(jsonify(ok=True, returnTo="/admin/admin.html"))
        return _set_session_cookie(resp, back)

    @app.route("/api/auth/admin/unlock", methods=["POST"])
    def auth_admin_unlock():
        data = request.get_json(silent=True) or {}
        # Ignore capitalization AND all whitespace, matching the promise the
        # passcode gate shows the user ("capitalization and spaces are
        # ignored"). Previously only leading/trailing space was stripped, so a
        # correct passcode typed with different internal spacing was rejected.
        passcode = "".join((data.get("passcode") or "").split()).lower()
        expected = "".join(ADMIN_PASSCODE.split()).lower()
        if passcode != expected:
            return jsonify(ok=False, error="Invalid passcode"), 401
        token = _new_token()
        g.db.execute(
            "INSERT INTO sessions (token, kind, studentId, createdAt) VALUES (?, 'admin', NULL, ?)",
            (token, int(time.time())),
        )
        resp = make_response(jsonify(ok=True))
        return _set_session_cookie(resp, token)

    @app.route("/api/auth/admin/logout", methods=["POST"])
    def auth_admin_logout():
        # Only drop the cookie if the current session is actually an admin
        # session. Otherwise we'd be logging out a logged-in student that
        # happens to share the same cookie name.
        token = request.cookies.get(COOKIE_NAME)
        if token:
            row = g.db.execute(
                "SELECT kind FROM sessions WHERE token = ?", (token,),
            ).fetchone()
            if row and row["kind"] == "admin":
                g.db.execute("DELETE FROM sessions WHERE token = ?", (token,))
                return _clear_session_cookie(make_response(jsonify(ok=True)))
        return jsonify(ok=True)

    # ── Students ───────────────────────────────────────────────────
    @app.route("/api/students", methods=["GET"])
    def list_students():
        sess = _current_session()
        is_admin = sess and sess["kind"] == "admin"
        my_id = sess["studentId"] if (sess and sess["kind"] == "student") else None
        rows = g.db.execute("SELECT * FROM students").fetchall()
        out = []
        for r in rows:
            d = row_to_student(r)
            # The playtest camper is staff scaffolding, not a real entrant —
            # keep it off the leaderboard and out of every roster except the
            # admin's own and the playtest session's view of itself.
            if d["id"] == PLAYTEST_STUDENT_ID and not is_admin and d["id"] != my_id:
                continue
            if not is_admin and d["id"] != my_id:
                # Public view — strip sensitive fields
                for k in ("password", "parentEmail", "parentPhone",
                          "parentFirst", "parentLast", "phone", "dob"):
                    d.pop(k, None)
            out.append(d)
        return jsonify(ok=True, data=out)

    @app.route("/api/students", methods=["POST"])
    def create_student():
        data = request.get_json(silent=True) or {}
        if _is_reserved_email(data.get("studentEmail") or data.get("student_email")):
            return jsonify(ok=False, error="That email isn't available — it's reserved."), 400
        s = _normalize_student(data)
        _insert_student(s)
        # Best-effort registration confirmation email — never blocks creation.
        student_email = (data.get("studentEmail") or data.get("student_email") or "").strip()
        parent_email  = (data.get("parentEmail")  or data.get("parent_email")  or "").strip()
        full_name = ((data.get("firstName") or "") + " " + (data.get("lastName") or "")).strip()
        try:
            _send_registration_confirm(student_email, full_name, parent_email or None,
                                       amount=REG_TIERS[_reg_tier()]["price"] or None)
        except Exception:  # noqa: BLE001
            pass
        return jsonify(ok=True, data=s)

    @app.route("/api/students", methods=["PUT"])
    @require_admin
    def replace_students():
        data = request.get_json(silent=True) or {}
        arr = data.get("students") or []
        if not isinstance(arr, list):
            return jsonify(ok=False, error="Body must be { students: [...] }"), 400
        # Drop any reserved-email rows defensively so they can never get
        # persisted via a bulk replace from the client.
        arr = [r for r in arr if not _is_reserved_email(r.get("studentEmail"))]
        with g.db:
            g.db.execute("DELETE FROM students")
            for raw in arr:
                _insert_student(_normalize_student(raw))
        return jsonify(ok=True, count=len(arr))

    @app.route("/api/students/<sid>", methods=["GET"])
    def get_student(sid):
        row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
        if not row:
            return jsonify(ok=False, error="Not found"), 404
        return jsonify(ok=True, data=row_to_student(row))

    @app.route("/api/students/<sid>", methods=["DELETE"])
    @require_admin
    def delete_student(sid):
        g.db.execute("DELETE FROM students WHERE id = ?", (sid,))
        return jsonify(ok=True)

    # ── Student-side actions (server-validated) ────────────────────
    @app.route("/api/students/me/transfer", methods=["POST"])
    @require_student
    @block_when_frozen
    def transfer():
        data = request.get_json(silent=True) or {}
        to_id = data.get("toId")
        amount = int(data.get("amount") or 0)
        lossless = bool(data.get("lossless"))
        if amount <= 0:
            return jsonify(ok=False, error="Enter a positive amount to transfer."), 400
        from_id = g.session["studentId"]
        if from_id == to_id:
            return jsonify(ok=False, error="You can't transfer points to yourself."), 400

        with g.db:
            from_row = g.db.execute("SELECT * FROM students WHERE id = ?", (from_id,)).fetchone()
            to_row   = g.db.execute("SELECT * FROM students WHERE id = ?", (to_id,)).fetchone()
            if not from_row: return jsonify(ok=False, error="Your account was not found."), 404
            if not to_row:   return jsonify(ok=False, error="Recipient not found."), 404

            from_stats = {**default_stats(), **json.loads(from_row["stats"] or "{}")}
            to_stats   = {**default_stats(), **json.loads(to_row["stats"] or "{}")}
            cur = from_stats.get("privatePoints", 0)
            if cur == 0: return jsonify(ok=False, error="You have 0 points!"), 400
            # After a final tally, transferring is the only thing left — and
            # it costs more. 60% is lost instead of 50%, and the lossless
            # option is gone entirely: there's nothing left to buy your way
            # out with.
            tallied = bool(_extras(from_row).get("finalTally"))
            keep_ratio = (TALLIED_TRANSFER_KEEP_RATIO if tallied
                          else TRANSFER_KEEP_RATIO)
            if tallied and lossless:
                return jsonify(ok=False, error=(
                    "Lossless transfers are gone — you've cashed out. "
                    f"{int((1 - keep_ratio) * 100)}% is lost on every transfer now."
                )), 400
            # The lossless fee is charged ON TOP of the amount sent, so the
            # sender needs to cover both.
            fee, fee_off = dungeon.discounted(
                LOSSLESS_TRANSFER_COST if lossless else 0,
                from_stats.get("luck", 0))
            total = amount + fee
            if cur < total:
                if fee:
                    return jsonify(ok=False, error=(
                        f"A lossless transfer of {amount} pts costs {total} pts "
                        f"in total ({amount} sent + {fee} fee). You only have {cur}."
                    )), 400
                return jsonify(ok=False, error=f"You only have {cur} points."), 400

            received = amount if lossless else int(amount * keep_ratio)
            lost = amount - received
            from_stats["privatePoints"]  = cur - total
            from_stats["pointExchanges"] = from_stats.get("pointExchanges", 0) + 1
            to_stats["privatePoints"]    = to_stats.get("privatePoints", 0) + received
            to_stats["totalPointsEarned"] = to_stats.get("totalPointsEarned", 0) + received

            g.db.execute("UPDATE students SET stats = ? WHERE id = ?", (json.dumps(from_stats), from_id))
            g.db.execute("UPDATE students SET stats = ? WHERE id = ?", (json.dumps(to_stats), to_id))

            from_name = _full_name(from_row)
            to_name   = _full_name(to_row)
            # Everything the sender parts with beyond what actually lands in
            # the recipient's account — the 50% tax, or the lossless fee that
            # replaces it — goes to the transactions bank.
            to_bank = lost + fee
            if to_bank > 0:
                bank_prev = int(_meta_get("transactions_bank", "0") or "0")
                _meta_set("transactions_bank", str(bank_prev + to_bank))
                why = (f"{fee} pt lossless fee" if lossless
                       else f"{lost} pts")
                _log_tx(type="bank_deposit", scope="bank",
                        subjectId="transactions_bank", subjectName="Transactions Bank",
                        relatedId=from_id, relatedName=from_name,
                        amount=to_bank,
                        description=f"+{to_bank} pts deposited from {from_name} → {to_name} transfer ({amount} sent, {why})")
            _log_tx(type="transfer_out", scope="student", subjectId=from_id,
                    subjectName=from_name, relatedId=to_id, relatedName=to_name,
                    amount=-amount,
                    description=(
                        f"Sent {amount} pts to {to_name} in full — no transfer tax"
                        if lossless else
                        f"Sent {amount} pts to {to_name} · {lost} pts deposited to the transactions bank"
                    ))
            if fee:
                _log_tx(type="transfer_fee", scope="student", subjectId=from_id,
                        subjectName=from_name, relatedId=to_id, relatedName=to_name,
                        amount=-fee,
                        description=f"Paid {fee} pts so {to_name} received all {amount} pts instead of {int(amount * TRANSFER_KEEP_RATIO)}")
            _log_tx(type="transfer_in", scope="student", subjectId=to_id,
                    subjectName=to_name, relatedId=from_id, relatedName=from_name,
                    amount=received,
                    description=(
                        f"Received all {received} pts from {from_name} — they paid the {fee} pt lossless fee"
                        if lossless else
                        f"Received {received} pts from {from_name} ({amount} sent, 50% kept)"
                    ))

        return jsonify(ok=True, data={"sent": amount, "received": received,
                                      "lost": lost, "fee": fee,
                                      "spent": total, "lossless": lossless})

    @app.route("/api/students/me/luck", methods=["POST"])
    @require_student
    @block_when_frozen
    def invest_luck():
        sid = g.session["studentId"]
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row: return jsonify(ok=False, error="Student not found."), 404
            stats = {**default_stats(), **json.loads(row["stats"] or "{}")}
            cur = stats.get("privatePoints", 0)
            if cur == 0: return jsonify(ok=False, error="You have 0 points! Ask an admin to award you some before you can upgrade your stats."), 400
            if cur < LUCK_COST: return jsonify(ok=False, error=f"You need {LUCK_COST} points to invest. You only have {cur} — keep earning!"), 400
            stats["privatePoints"] = cur - LUCK_COST
            stats["luck"] = stats.get("luck", 0) + 1
            g.db.execute("UPDATE students SET stats = ? WHERE id = ?", (json.dumps(stats), sid))
            _log_tx(type="luck", scope="student", subjectId=sid,
                    subjectName=_full_name(row), amount=-LUCK_COST,
                    description=f"Invested {LUCK_COST} pts → luck now {stats['luck']}")
        return jsonify(ok=True, data={"newLuck": stats["luck"], "remaining": stats["privatePoints"]})

    @app.route("/api/students/me/click", methods=["POST"])
    @require_student
    @block_when_frozen
    def clicker_tap():
        sid = g.session["studentId"]
        today = time.strftime("%Y-%m-%d", time.gmtime())
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row: return jsonify(ok=False, error="Student not found."), 404
            stats = {**default_stats(), **json.loads(row["stats"] or "{}")}
            # Reset daily counters at the start of a new UTC day.
            if stats.get("dailyClickerDate") != today:
                stats["dailyClickerDate"] = today
                stats["dailyManualPts"]   = 0
                stats["dailyAutoPts"]     = 0
            stats["clickerClicks"] = stats.get("clickerClicks", 0) + 1
            earned, spider, capped = 0, False, False
            luck_label = None

            # ── The clicker duplicates ──
            # Every click gets a roll. On a hit the clicker splits in two:
            # another clicker, and the role to go with it if they somehow
            # don't have it yet (a clicker without the role is inert — the
            # auto endpoint checks for both).
            extras = json.loads(row["extras"] or "{}")
            duplicated = False
            luck_eff = dungeon.luck_effectiveness(stats.get("luck", 0))
            if clicker_econ.rolls_duplicate(luck_eff):
                from_clicks = int(extras.get("clickerLevelsFromClicks") or 0)
                if from_clicks < CLICKER_DUPLICATE_MAX:
                    extras["clickerLevel"] = int(extras.get("clickerLevel") or 0) + 1
                    extras["clickerLevelsFromClicks"] = from_clicks + 1
                    roles = json.loads(row["roles"] or "[]")
                    if CLICKER_ROLE_ID not in roles:
                        roles.append(CLICKER_ROLE_ID)
                        g.db.execute("UPDATE students SET roles = ? WHERE id = ?",
                                     (json.dumps(roles), sid))
                    g.db.execute("UPDATE students SET extras = ? WHERE id = ?",
                                 (json.dumps(extras), sid))
                    duplicated = True
                    _log_tx(type="clicker", scope="student", subjectId=sid,
                            subjectName=_full_name(row), amount=0,
                            description=(f"🖱 A clicker duplicated on click "
                                         f"{stats['clickerClicks']:,} → "
                                         f"×{extras['clickerLevel']}"))

            if stats["clickerClicks"] % CLICKER_RATE == 0:
                if stats.get("dailyManualPts", 0) >= MANUAL_DAILY_CAP:
                    capped = True   # would have earned, but you've hit today's manual cap
                else:
                    # Luck rides the manual point the same way it rides a
                    # staff award. It counts as ONE against the daily cap
                    # however it multiplies — the cap is there to bound
                    # clicking, not to tax being lucky.
                    mult, luck_label = dungeon.luck_point_multiplier(
                        stats.get("luck", 0))
                    earned = 1 * mult
                    stats["dailyManualPts"]      = stats.get("dailyManualPts", 0) + 1
                    stats["privatePoints"]       = stats.get("privatePoints", 0) + earned
                    stats["totalPointsEarned"]   = stats.get("totalPointsEarned", 0) + earned
                    stats["clickerPointsEarned"] = stats.get("clickerPointsEarned", 0) + earned
                    if stats["clickerPointsEarned"] >= SPIDER_THRESHOLD and not stats.get("spiderShown"):
                        spider = True
                        stats["spiderShown"] = True
            g.db.execute("UPDATE students SET stats = ? WHERE id = ?", (json.dumps(stats), sid))
            if earned > 0:
                _log_tx(type="clicker", scope="student", subjectId=sid,
                        subjectName=_full_name(row), amount=earned,
                        description=(f"Earned {earned} pt from clicker "
                                     f"({stats['clickerClicks']} total clicks)"
                                     + (f" · 🍀 luck {luck_label}d it" if luck_label else "")))
        return jsonify(ok=True, data={
            "clicks": stats["clickerClicks"], "earned": earned, "spider": spider,
            "clickerPointsEarned": stats["clickerPointsEarned"],
            "capped": capped,
            "dailyManualPts": stats.get("dailyManualPts", 0),
            "manualCap": MANUAL_DAILY_CAP,
            "luckLabel": luck_label,
            "duplicated": duplicated,
            "clickerLevel": int(extras.get("clickerLevel") or 0),
            "duplicateChance": clicker_econ.duplicate_chance(luck_eff),
            "duplicateOdds": clicker_econ.DUPLICATE_ODDS,
            "duplicatesLeft": max(0, CLICKER_DUPLICATE_MAX
                                  - int(extras.get("clickerLevelsFromClicks") or 0)),
        })

    def _clicker_state(row):
        """(clickers, efficiency, luck effectiveness) for this camper."""
        extras = json.loads(row["extras"] or "{}") or {}
        stats  = {**default_stats(), **json.loads(row["stats"] or "{}")}
        return (int(extras.get("clickerLevel") or 0),
                int(extras.get("clickerEfficiency") or 0),
                dungeon.luck_effectiveness(stats.get("luck", 0)))

    def _clicker_payload(row, **extra):
        """The panel's whole view of the clicker, straight off the row."""
        n, eff, luck_eff = _clicker_state(row)
        stats = {**default_stats(), **json.loads(row["stats"] or "{}")}
        out = clicker_econ.summary(n, eff, luck_eff, stats.get("privatePoints", 0))
        # Prices are what THIS camper pays — luck haggles here like it does
        # everywhere else, and a price quoted before the discount is a lie.
        luck = stats.get("luck", 0)
        out["nextClickerCost"], out["clickerLuckOff"] = dungeon.discounted(
            out["nextClickerCost"], luck)
        if out["nextEfficiencyCost"] is not None:
            out["nextEfficiencyCost"], out["efficiencyLuckOff"] = dungeon.discounted(
                out["nextEfficiencyCost"], luck)
        else:
            out["efficiencyLuckOff"] = 0
        # The bulk button needs its own quote — ten clickers priced one at a
        # time as the count climbs, then discounted like everything else.
        out["tenCost"], out["tenLuckOff"] = dungeon.discounted(
            clicker_econ.clickers_cost(n, 10), luck)
        out["points"] = stats.get("privatePoints", 0)
        out["bank"] = round(float(stats.get("clickerBank") or 0.0), 4)
        out["dailyAutoPts"] = stats.get("dailyAutoPts", 0)
        out.update(extra)
        return out

    def _accrue_clickers(sid, row, now_ms):
        """Pay out whatever the camper's clickers have produced since we
        last looked, and bank the fraction that's left over.

        Returns (earned, stats, extras) with both dicts already updated in
        memory — the caller writes them. Splitting it out this way means
        every entry point (the poll, a purchase, opening the panel) settles
        production first, so a camper can never buy a clicker with points
        their existing clickers had already earned but not been paid.
        """
        stats  = {**default_stats(), **json.loads(row["stats"] or "{}")}
        extras = json.loads(row["extras"] or "{}") or {}
        today  = time.strftime("%Y-%m-%d", time.gmtime())
        if stats.get("dailyClickerDate") != today:
            stats["dailyClickerDate"] = today
            stats["dailyManualPts"]   = 0
            stats["dailyAutoPts"]     = 0

        n   = int(extras.get("clickerLevel") or 0)
        eff = int(extras.get("clickerEfficiency") or 0)
        luck_eff = dungeon.luck_effectiveness(stats.get("luck", 0))
        last = int(extras.get("lastAutoAt") or 0)

        if last <= 0 or n <= 0:
            # No baseline yet, or nothing producing: start the clock now so
            # the first stretch isn't paid retroactively from epoch zero.
            extras["lastAutoAt"] = now_ms
            return 0, stats, extras

        earned, bank = clicker_econ.accrue(
            n, eff, luck_eff, now_ms - last, stats.get("clickerBank") or 0.0)
        stats["clickerBank"] = bank
        extras["lastAutoAt"] = now_ms
        if earned > 0:
            stats["dailyAutoPts"]        = stats.get("dailyAutoPts", 0) + earned
            stats["privatePoints"]       = stats.get("privatePoints", 0) + earned
            stats["totalPointsEarned"]   = stats.get("totalPointsEarned", 0) + earned
            stats["clickerPointsEarned"] = stats.get("clickerPointsEarned", 0) + earned
        return earned, stats, extras

    @app.route("/api/students/me/auto-click", methods=["POST"])
    @require_student
    @block_when_frozen
    def auto_click():
        """Settle whatever the camper's clickers have produced.

        Production is continuous and uncapped: a clicker earns
        BASE_POINTS_PER_DAY a day, forever, whether or not the tab is open.
        Polling more often does not earn more — the fractional remainder is
        banked and carried, so the same wall-clock time always pays the same
        amount. See clicker.py for the arithmetic and why it's lossless.
        """
        sid = g.session["studentId"]
        now_ms = int(time.time() * 1000)
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            earned, stats, extras = _accrue_clickers(sid, row, now_ms)
            g.db.execute("UPDATE students SET stats = ?, extras = ? WHERE id = ?",
                         (json.dumps(stats), json.dumps(extras), sid))
            if earned > 0:
                _log_tx(type="clicker", scope="student", subjectId=sid,
                        subjectName=_full_name(row), amount=earned,
                        description=(f"Clickers ×{extras.get('clickerLevel', 0)} "
                                     f"+{earned} pt"))
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
        return jsonify(ok=True, data=_clicker_payload(
            row, earned=earned, pollSec=CLICKER_POLL_SEC))

    @app.route("/api/students/me/clicker", methods=["GET"])
    @require_student
    def clicker_state():
        """The panel's read. Settles production on the way through so the
        numbers on screen are never stale."""
        sid = g.session["studentId"]
        now_ms = int(time.time() * 1000)
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            earned, stats, extras = _accrue_clickers(sid, row, now_ms)
            g.db.execute("UPDATE students SET stats = ?, extras = ? WHERE id = ?",
                         (json.dumps(stats), json.dumps(extras), sid))
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
        return jsonify(ok=True, data=_clicker_payload(
            row, earned=earned, pollSec=CLICKER_POLL_SEC))

    @app.route("/api/students/me/clicker/buy", methods=["POST"])
    @require_student
    @block_when_frozen
    def clicker_buy():
        """Buy clickers with points. Each one costs 15% more than the last,
        so `qty` is priced one at a time as the count climbs — buying five
        at once costs exactly what buying five separately would."""
        d = request.get_json(silent=True) or {}
        raw = d.get("qty")
        try:
            # `or 1` would turn an explicit 0 into a purchase of 1 — ask for
            # none, get charged for one. Default only when it's absent.
            qty = 1 if raw is None else int(raw)
        except (TypeError, ValueError):
            return jsonify(ok=False, error="How many?"), 400
        if qty < 1 or qty > 100:
            return jsonify(ok=False, error="Buy between 1 and 100 at a time."), 400

        sid = g.session["studentId"]
        now_ms = int(time.time() * 1000)
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            locked = _tally_locked(row)
            if locked:
                return jsonify(ok=False, error=locked), 403
            # Settle production first — those points are already earned.
            _, stats, extras = _accrue_clickers(sid, row, now_ms)
            owned = int(extras.get("clickerLevel") or 0)
            listed = clicker_econ.clickers_cost(owned, qty)
            cost, luck_off = dungeon.discounted(listed, stats.get("luck", 0))
            have = int(stats.get("privatePoints", 0))
            if have < cost:
                return jsonify(ok=False, error=(
                    f"{qty} more clicker{'s' if qty > 1 else ''} costs {cost:,} points "
                    f"and you have {have:,}."), shortfall=cost - have), 400

            stats["privatePoints"] = have - cost
            extras["clickerLevel"] = owned + qty
            roles = json.loads(row["roles"] or "[]")
            if CLICKER_ROLE_ID not in roles:
                roles.append(CLICKER_ROLE_ID)
                g.db.execute("UPDATE students SET roles = ? WHERE id = ?",
                             (json.dumps(roles), sid))
            g.db.execute("UPDATE students SET stats = ?, extras = ? WHERE id = ?",
                         (json.dumps(stats), json.dumps(extras), sid))
            _log_tx(type="clicker", scope="student", subjectId=sid,
                    subjectName=_full_name(row), amount=-cost,
                    description=(f"🖱 Bought {qty} clicker{'s' if qty > 1 else ''} "
                                 f"for {cost:,} pts → ×{extras['clickerLevel']}"
                                 + (f" (🍀 {luck_off:,} off)" if luck_off else "")))
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
        return jsonify(ok=True, data=_clicker_payload(
            row, bought=qty, paid=cost, luckOff=luck_off, pollSec=CLICKER_POLL_SEC))

    @app.route("/api/students/me/clicker/efficiency", methods=["POST"])
    @require_student
    @block_when_frozen
    def clicker_efficiency():
        """One efficiency level. Applies to every clicker owned, so it's
        worth more the bigger the farm — which is what makes the choice
        between another clicker and another level an actual choice."""
        sid = g.session["studentId"]
        now_ms = int(time.time() * 1000)
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            locked = _tally_locked(row)
            if locked:
                return jsonify(ok=False, error=locked), 403
            _, stats, extras = _accrue_clickers(sid, row, now_ms)
            level = int(extras.get("clickerEfficiency") or 0)
            listed = clicker_econ.efficiency_cost(level)
            if listed is None:
                return jsonify(ok=False, error="Your clickers are as fast as they get."), 400
            cost, luck_off = dungeon.discounted(listed, stats.get("luck", 0))
            have = int(stats.get("privatePoints", 0))
            if have < cost:
                return jsonify(ok=False, error=(
                    f"Efficiency {level + 1} costs {cost:,} points and you have "
                    f"{have:,}."), shortfall=cost - have), 400

            stats["privatePoints"] = have - cost
            extras["clickerEfficiency"] = level + 1
            g.db.execute("UPDATE students SET stats = ?, extras = ? WHERE id = ?",
                         (json.dumps(stats), json.dumps(extras), sid))
            _log_tx(type="clicker", scope="student", subjectId=sid,
                    subjectName=_full_name(row), amount=-cost,
                    description=(f"🖱 Clicking efficiency → Lv {level + 1} "
                                 f"for {cost:,} pts"
                                 + (f" (🍀 {luck_off:,} off)" if luck_off else "")))
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
        return jsonify(ok=True, data=_clicker_payload(
            row, efficiencyBought=True, paid=cost, luckOff=luck_off,
            pollSec=CLICKER_POLL_SEC))

    # Admin grant/revoke endpoints removed — Paper Crane has no cap any more,
    # so any interested student can just claim it themselves.

    @app.route("/api/admin/students/<sid>/clicker-upgrade", methods=["POST"])
    @require_admin
    def admin_clicker_upgrade(sid):
        """Bumps the student's clickerLevel by +1 and ensures the role is
        present. First call grants the role; each subsequent call is an
        upgrade (Lv 1 → 2 → 3, …)."""
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row: return jsonify(ok=False, error="Student not found."), 404
            roles = json.loads(row["roles"] or "[]")
            if CLICKER_ROLE_ID not in roles:
                roles.append(CLICKER_ROLE_ID)
            extras = json.loads(row["extras"] or "{}")
            extras["clickerLevel"] = int(extras.get("clickerLevel") or 0) + 1
            g.db.execute(
                "UPDATE students SET roles = ?, extras = ? WHERE id = ?",
                (json.dumps(roles), json.dumps(extras), sid),
            )
            _log_tx(type="role_assigned", scope="student", subjectId=sid,
                    subjectName=_full_name(row), amount=0,
                    description=f"🖱 Clicker role upgraded to Lv {extras['clickerLevel']}")
        return jsonify(ok=True, data={"clickerLevel": extras["clickerLevel"]})

    @app.route("/api/admin/students/<sid>/clicker-downgrade", methods=["POST"])
    @require_admin
    def admin_clicker_downgrade(sid):
        """Decreases clickerLevel by 1. Removes the role when level hits 0."""
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row: return jsonify(ok=False, error="Student not found."), 404
            roles = json.loads(row["roles"] or "[]")
            extras = json.loads(row["extras"] or "{}")
            level = int(extras.get("clickerLevel") or 0)
            if level <= 0:
                return jsonify(ok=False, error="Clicker level is already 0."), 400
            extras["clickerLevel"] = level - 1
            if extras["clickerLevel"] == 0:
                roles = [r for r in roles if r != CLICKER_ROLE_ID]
            g.db.execute(
                "UPDATE students SET roles = ?, extras = ? WHERE id = ?",
                (json.dumps(roles), json.dumps(extras), sid),
            )
        return jsonify(ok=True, data={"clickerLevel": extras["clickerLevel"]})

    @app.route("/api/students/me/maze/start", methods=["POST"])
    @require_student
    def maze_start():
        """Stamp the beginning of a descent.

        This is the whole anti-abuse story for maze shards, and it replaced a
        daily cap. A cap punishes the camper who genuinely wants to run the
        maze ten times; a start stamp only asks that the ten runs took as
        long as ten runs take.

        Called every time the maze view opens. Re-entering restarts the clock,
        which is correct: the run itself lives in a browser variable, so
        coming back in always means starting from door 1.
        """
        sid = g.session["studentId"]
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            extras = _extras(row)
            extras["mazeStartedAt"] = int(time.time())
            g.db.execute("UPDATE students SET extras = ? WHERE id = ?",
                         (json.dumps(extras), sid))
        return jsonify(ok=True, data={"minSeconds": MAZE_MIN_DESCENT_S})

    @app.route("/api/students/me/claim-doors", methods=["POST"])
    @require_student
    @block_when_frozen
    def claim_doors():
        """Score-based reward for the 300-door math maze.

        Body: { correct: int, total: int }
          - total must be >= 300
          - correct in [0, total]
        Reward = tier(correct/total*100), see DOOR_REWARD_TIERS.
        First completion: awards points + flips extras.doorsRewarded.
        Subsequent completions: counts toward extras.doorsCompleted but
        award nothing (keeps the maze re-playable for fun + the Money
        Tree pattern hunt without granting unlimited points).

        Server returns `completions` so the client can show the
        Money-Tree-pattern hint after the third successful descent.
        """
        data = request.get_json(silent=True) or {}
        correct = int(data.get("correct") or 0)
        total   = int(data.get("total")   or 0)
        # Scored doors = MAZE_LENGTH - 1 (the first floor is a freebie),
        # so the client submits 299. Use that as the floor.
        if total < DOOR_MAZE_LENGTH - 1:
            return jsonify(ok=False, error="Maze not complete."), 400
        correct = max(0, min(total, correct))
        pct = (correct / total) * 100 if total else 0
        tier_pts = reward_for_pct(pct)

        sid = g.session["studentId"]
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404

            extras = json.loads(row["extras"] or "{}")
            already_rewarded = bool(extras.get("doorsRewarded") or extras.get("doors_claimed"))
            extras["doorsCompleted"] = int(extras.get("doorsCompleted") or 0) + 1
            completions = extras["doorsCompleted"]

            stats = {**default_stats(), **json.loads(row["stats"] or "{}")}

            # ── Points: the original tier, first completion only ──
            awarded = 0
            if not already_rewarded and tier_pts > 0:
                awarded = tier_pts
                extras["doorsRewarded"] = True
                stats["privatePoints"]     = stats.get("privatePoints", 0) + awarded
                stats["totalPointsEarned"] = stats.get("totalPointsEarned", 0) + awarded
                _log_tx(type="earn", scope="student", subjectId=sid,
                        subjectName=_full_name(row), amount=awarded,
                        description=f"🚪 Maze complete · {correct}/{total} ({pct:.0f}%) · +{awarded} pts")

            # ── Shards: every completion, per correct door, no ceiling ──
            now_s = int(time.time())
            started = int(extras.get("mazeStartedAt") or 0)
            elapsed = now_s - started if started else None

            shards = correct * MAZE_SHARDS_PER_DOOR
            shard_note = None
            if shards > 0 and (not started or elapsed < MAZE_MIN_DESCENT_S):
                shards = 0
                shard_note = ("that descent came back faster than it can be "
                              "walked. Start from door 1 and the shards will "
                              "be there at the bottom.")
            if shards > 0:
                stats["shards"] = int(stats.get("shards", 0)) + shards
                # Consumed: this descent has been paid for, and the next
                # payout needs a fresh trip through the door.
                extras["mazeStartedAt"] = 0
                _log_tx(type="earn", scope="student", subjectId=sid,
                        subjectName=_full_name(row), amount=0,
                        description=(f"💎 Maze descent · {correct}/{total} doors "
                                     f"right · +{shards:,} shards"))

            g.db.execute(
                "UPDATE students SET stats = ?, extras = ? WHERE id = ?",
                (json.dumps(stats), json.dumps(extras), sid),
            )
        return jsonify(ok=True, data={
            "correct": correct,
            "total":   total,
            "percent": round(pct, 2),
            "tierPoints":     tier_pts,
            "awarded":        awarded,
            "alreadyRewarded": already_rewarded,
            "completions":    completions,
            "shards":         shards,
            "shardsPerDoor":  MAZE_SHARDS_PER_DOOR,
            "shardNote":      shard_note,
            "shardBalance":   int(stats.get("shards", 0)),
        })

    # ── Infinity mode (post-dungeon endless mode) ─────────────────
    INFINITY_REWARD = 6

    @app.route("/api/infinity-questions", methods=["GET"])
    def list_infinity_questions():
        rows = g.db.execute(
            "SELECT * FROM infinity_questions ORDER BY position ASC, createdAt ASC"
        ).fetchall()
        return jsonify(ok=True, data=[dict(r) for r in rows])

    @app.route("/api/admin/infinity-questions", methods=["POST"])
    @require_admin
    def admin_add_infinity_question():
        d = request.get_json(silent=True) or {}
        q = (d.get("question") or "").strip()
        a = (d.get("answer") or "").strip()
        w = (d.get("wrongAnswer") or "").strip()
        if not q or not a or not w:
            return jsonify(ok=False, error="Question, correct answer, and decoy are all required."), 400
        if w == a:
            return jsonify(ok=False, error="The decoy must differ from the correct answer."), 400
        try:
            diff = max(1, min(int(d.get("difficulty") or 3), 5))
        except (TypeError, ValueError):
            diff = 3
        unit = (d.get("unit") or "").strip()
        qid = "inf-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(3)
        row = g.db.execute("SELECT COALESCE(MAX(position), 0) AS m FROM infinity_questions").fetchone()
        pos = (row["m"] or 0) + 1
        g.db.execute(
            "INSERT INTO infinity_questions"
            " (id, question, answer, wrongAnswer, position, createdAt, source, unit, difficulty)"
            " VALUES (?, ?, ?, ?, ?, ?, 'staff', ?, ?)",
            (qid, q, a, w, pos, int(time.time()), unit, diff),
        )
        return jsonify(ok=True, id=qid)

    @app.route("/api/admin/infinity-questions/<qid>", methods=["PATCH"])
    @require_admin
    def admin_edit_infinity_question(qid):
        d = request.get_json(silent=True) or {}
        q = (d.get("question") or "").strip()
        a = (d.get("answer") or "").strip()
        w = (d.get("wrongAnswer") or "").strip()
        if not q or not a or not w:
            return jsonify(ok=False, error="Question, correct answer, and decoy are all required."), 400
        if w == a:
            return jsonify(ok=False, error="The decoy must differ from the correct answer."), 400
        sets = ["question = ?", "answer = ?", "wrongAnswer = ?"]
        params = [q, a, w]
        if d.get("difficulty") is not None:
            try:
                sets.append("difficulty = ?")
                params.append(max(1, min(int(d.get("difficulty")), 5)))
            except (TypeError, ValueError):
                sets.pop()
        if d.get("unit") is not None:
            sets.append("unit = ?")
            params.append((d.get("unit") or "").strip())
        params.append(qid)
        g.db.execute(
            f"UPDATE infinity_questions SET {', '.join(sets)} WHERE id = ?", params)
        return jsonify(ok=True)

    @app.route("/api/admin/infinity-questions/<qid>", methods=["DELETE"])
    @require_admin
    def admin_delete_infinity_question(qid):
        g.db.execute("DELETE FROM infinity_questions WHERE id = ?", (qid,))
        return jsonify(ok=True)

    @app.route("/api/admin/infinity/answers", methods=["GET"])
    @require_admin
    def admin_infinity_answers():
        """What the camp has actually been practising.

        Two views in one call: a per-camper roll-up (how many questions,
        how many right, which strand they're worst at) and the raw tail of
        recent answers. `studentId` narrows both to one camper.
        """
        sid = (request.args.get("studentId") or "").strip()
        try:
            limit = max(1, min(int(request.args.get("limit") or 200), 1000))
        except (TypeError, ValueError):
            limit = 200

        names = {r["id"]: _full_name(r)
                 for r in g.db.execute("SELECT * FROM students").fetchall()}

        where, params = "", []
        if sid:
            where, params = " WHERE studentId = ?", [sid]

        totals = [dict(r) for r in g.db.execute(
            "SELECT studentId, COUNT(*) AS answered,"
            "       SUM(correct) AS correct,"
            "       MAX(answeredAt) AS lastAt,"
            "       COUNT(DISTINCT questionId) AS distinctQs"
            f"  FROM infinity_answers{where}"
            " GROUP BY studentId ORDER BY answered DESC", params).fetchall()]
        for t in totals:
            t["name"] = names.get(t["studentId"], "(removed)")
            t["answered"] = int(t["answered"] or 0)
            t["correct"] = int(t["correct"] or 0)
            t["accuracy"] = (round(100 * t["correct"] / t["answered"])
                             if t["answered"] else 0)

        by_unit = [dict(r) for r in g.db.execute(
            "SELECT studentId, unit, COUNT(*) AS n, SUM(correct) AS c"
            f"  FROM infinity_answers{where}"
            " GROUP BY studentId, unit", params).fetchall()]

        recent = [dict(r) for r in g.db.execute(
            "SELECT * FROM infinity_answers" + where +
            " ORDER BY answeredAt DESC LIMIT ?", params + [limit]).fetchall()]
        for r in recent:
            r["name"] = names.get(r["studentId"], "(removed)")

        bank = g.db.execute(
            "SELECT COUNT(*) AS n,"
            " SUM(CASE WHEN source = 'generated' THEN 1 ELSE 0 END) AS generated,"
            " SUM(CASE WHEN source != 'generated' THEN 1 ELSE 0 END) AS staff"
            " FROM infinity_questions").fetchone()

        return jsonify(ok=True, data={
            "totals": totals, "byUnit": by_unit, "recent": recent,
            "bank": {"total": int(bank["n"] or 0),
                     "generated": int(bank["generated"] or 0),
                     "staff": int(bank["staff"] or 0)},
        })

    @app.route("/api/students/me/infinity-answer", methods=["POST"])
    @require_student
    @block_when_frozen
    def infinity_answer():
        """Award +6 points per submitted answer in infinity mode."""
        sid = g.session["studentId"]
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            stats = {**default_stats(), **json.loads(row["stats"] or "{}")}
            stats["privatePoints"]     = stats.get("privatePoints", 0) + INFINITY_REWARD
            stats["totalPointsEarned"] = stats.get("totalPointsEarned", 0) + INFINITY_REWARD
            g.db.execute(
                "UPDATE students SET stats = ? WHERE id = ?",
                (json.dumps(stats), sid),
            )
            _log_tx(type="earn", scope="student", subjectId=sid,
                    subjectName=_full_name(row), amount=INFINITY_REWARD,
                    description="∞ Infinity-mode answer · +6 pts")
        return jsonify(ok=True, data={"awarded": INFINITY_REWARD})

    @app.route("/api/students/me/claim-money-tree", methods=["POST"])
    @require_student
    @block_when_frozen
    def claim_money_tree():
        """Globally unique role: only one student in the entire DB can
        ever hold Money Tree. The first student to find the criss-cross
        door pattern (R W R W R W R W R) and call this endpoint claims
        it; everyone else gets a 409."""
        sid = g.session["studentId"]
        with g.db:
            # Is anyone already holding Money Tree?
            rows = g.db.execute("SELECT id, roles FROM students").fetchall()
            for r in rows:
                roles = json.loads(r["roles"] or "[]")
                if MONEY_TREE_ROLE_ID in roles:
                    return jsonify(ok=False, error="The Money Tree has already been claimed by another student."), 409

            my_row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not my_row:
                return jsonify(ok=False, error="Student not found."), 404
            my_roles = json.loads(my_row["roles"] or "[]")
            if MONEY_TREE_ROLE_ID not in my_roles:
                my_roles.append(MONEY_TREE_ROLE_ID)
            g.db.execute("UPDATE students SET roles = ? WHERE id = ?", (json.dumps(my_roles), sid))
            _log_tx(type="role_assigned", scope="student", subjectId=sid,
                    subjectName=_full_name(my_row), amount=0,
                    description="🌳 Claimed the Money Tree (criss-cross door pattern)")
        return jsonify(ok=True)

    @app.route("/api/students/me/money-tree/activate", methods=["POST"])
    @require_student
    @block_when_frozen
    def money_tree_activate():
        """Spend MONEY_TREE_COST private points and double whatever's left.
        Removes the role on success (one-time use)."""
        sid = g.session["studentId"]
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            roles = json.loads(row["roles"] or "[]")
            if MONEY_TREE_ROLE_ID not in roles:
                return jsonify(ok=False, error="You don't hold the Money Tree."), 400
            stats = {**default_stats(), **json.loads(row["stats"] or "{}")}
            cur = stats.get("privatePoints", 0)
            if cur < MONEY_TREE_COST:
                return jsonify(ok=False, error=f"You need at least {MONEY_TREE_COST} private points to activate the Money Tree."), 400
            new_priv = (cur - MONEY_TREE_COST) * 2
            stats["privatePoints"] = new_priv
            roles = [r for r in roles if r != MONEY_TREE_ROLE_ID]
            g.db.execute(
                "UPDATE students SET stats = ?, roles = ? WHERE id = ?",
                (json.dumps(stats), json.dumps(roles), sid),
            )
            # Money-tree cost is "spending" — those points go to the
            # transactions bank (not the Vulgar Vault, which is reserved
            # for staff-applied penalties). Only the up-front cost flows
            # in; the doubling reward is generated for the student.
            bank_prev = int(_meta_get("transactions_bank", "0") or "0")
            _meta_set("transactions_bank", str(bank_prev + MONEY_TREE_COST))
            who = _full_name(row)
            _log_tx(type="bank_deposit", scope="bank",
                    subjectId="transactions_bank", subjectName="Transactions Bank",
                    relatedId=sid, relatedName=who,
                    amount=MONEY_TREE_COST,
                    description=f"+{MONEY_TREE_COST} pts deposited from {who} · 🌳 Money Tree activation cost")
            _log_tx(type="earn", scope="student", subjectId=sid,
                    subjectName=who, amount=new_priv - cur,
                    description=f"🌳 Money Tree activated · spent {MONEY_TREE_COST} (→ bank), doubled remainder · {cur} → {new_priv}")
        return jsonify(ok=True, data={"newPrivatePoints": new_priv})

    @app.route("/api/students/me/money-tree/gift", methods=["POST"])
    @require_student
    @block_when_frozen
    def money_tree_gift():
        """Transfer the Money Tree role from the signed-in student to another."""
        data = request.get_json(silent=True) or {}
        to_id = data.get("toId")
        if not to_id:
            return jsonify(ok=False, error="Pick a classmate to gift the Money Tree to."), 400
        sid = g.session["studentId"]
        if to_id == sid:
            return jsonify(ok=False, error="You can't gift the Money Tree to yourself."), 400
        with g.db:
            from_row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            to_row   = g.db.execute("SELECT * FROM students WHERE id = ?", (to_id,)).fetchone()
            if not from_row: return jsonify(ok=False, error="Your account was not found."), 404
            if not to_row:   return jsonify(ok=False, error="Recipient not found."), 404

            from_roles = json.loads(from_row["roles"] or "[]")
            if MONEY_TREE_ROLE_ID not in from_roles:
                return jsonify(ok=False, error="You don't hold the Money Tree."), 400
            to_roles = json.loads(to_row["roles"] or "[]")
            if MONEY_TREE_ROLE_ID in to_roles:
                return jsonify(ok=False, error=f"{_full_name(to_row)} already holds the Money Tree."), 400

            from_roles = [r for r in from_roles if r != MONEY_TREE_ROLE_ID]
            to_roles.append(MONEY_TREE_ROLE_ID)
            g.db.execute("UPDATE students SET roles = ? WHERE id = ?", (json.dumps(from_roles), sid))
            g.db.execute("UPDATE students SET roles = ? WHERE id = ?", (json.dumps(to_roles), to_id))
            _log_tx(type="role_assigned", scope="student", subjectId=to_id,
                    subjectName=_full_name(to_row),
                    relatedId=sid, relatedName=_full_name(from_row),
                    amount=0, description=f"🌳 Received Money Tree from {_full_name(from_row)}")
        return jsonify(ok=True)

    @app.route("/api/students/me/claim-crane", methods=["POST"])
    @require_student
    @block_when_frozen
    def claim_crane():
        """Unlimited — any student who completes the claim flow earns the
        Paper Crane. Each student can still only hold one."""
        sid = g.session["studentId"]
        with g.db:
            my_row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not my_row:
                return jsonify(ok=False, error="Student not found."), 404
            my_roles = json.loads(my_row["roles"] or "[]")
            if CRANE_ROLE_ID in my_roles:
                return jsonify(ok=False, error="You already hold the Paper Crane."), 400

            my_roles.append(CRANE_ROLE_ID)
            g.db.execute("UPDATE students SET roles = ? WHERE id = ?", (json.dumps(my_roles), sid))
            _log_tx(type="role_assigned", scope="student", subjectId=sid,
                    subjectName=_full_name(my_row), amount=0,
                    description="🕊 Claimed the Paper Crane")
        return jsonify(ok=True, data={"remaining": None})

    def _role_ids_named(*names):
        """Every role id whose NAME matches one of `names`, ignoring case and
        spacing. Staff routinely create a role by hand before the code that
        uses it ships, and roles.name is UNIQUE — so a seeded row with the
        tidy id loses the race and never gets inserted at all. Matching on
        the name means the hand-made role works exactly the same."""
        want = {"".join((n or "").lower().split()) for n in names}
        return {r["id"] for r in g.db.execute("SELECT id, name FROM roles").fetchall()
                if "".join((r["name"] or "").lower().split()) in want}

    @app.route("/api/students/me/claim-lime-sword", methods=["POST"])
    @require_student
    @block_when_frozen
    def claim_lime_sword():
        """Awarded for clearing the lime trial behind the Calamity Catalyst
        bubble on the Support Us page. The browser reports the run, so the
        numbers are sanity-checked rather than trusted: only a Catalyst
        holder can claim, the hit count has to clear the bar without
        exceeding the number of limes that exist, and the score has to sit
        inside what those hits could actually have earned.

        A cleared run pays out three ways: the Lime Sword and the note that
        falls out of it land in the inventory (once each), the score converts
        to shards (every run, taxed like any other earning), and a full combo
        — every one of the limes, nothing missed — adds the osu Champion role
        and its one-time bounty."""
        sid = g.session["studentId"]
        d = request.get_json(silent=True) or {}
        try:
            hits  = int(d.get("hits") or 0)
            score = int(d.get("score") or 0)
        except (TypeError, ValueError):
            return jsonify(ok=False, error="hits and score must be whole numbers."), 400
        if not (LIME_TARGET <= hits <= LIME_TOTAL):
            return jsonify(
                ok=False,
                error=f"You need {LIME_TARGET} of the {LIME_TOTAL} limes — that run had {hits}.",
            ), 400
        if not (hits * LIME_MIN_PER_HIT <= score <= hits * LIME_MAX_PER_HIT):
            return jsonify(ok=False, error="That score doesn't match that many limes."), 400

        full_combo = hits >= LIME_TOTAL
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            roles = json.loads(row["roles"] or "[]")
            catalyst = _role_ids_named("Calamity Catalyst") | {CALAMITY_ROLE_ID}
            if not catalyst.intersection(roles):
                return jsonify(
                    ok=False,
                    error="The blade only answers to a Calamity Catalyst.",
                ), 403
            swords = _role_ids_named("Lime Sword") | {LIME_SWORD_ROLE_ID}
            already = bool(swords.intersection(roles))
            champs = _role_ids_named("osu Champion") | {OSU_ROLE_ID}
            champ_already = bool(champs.intersection(roles))
            # The bounty is one-time, and holding the role is what says it was
            # already paid. Everything else about a full combo is repeatable.
            pay_bounty = full_combo and not champ_already
            dirty = False
            if not already:
                roles.append(LIME_SWORD_ROLE_ID)
                dirty = True
                _log_tx(type="role_assigned", scope="student", subjectId=sid,
                        subjectName=_full_name(row), amount=0,
                        description=(f"🗡 Reforged the Lime Sword — {hits}/{LIME_TOTAL} limes "
                                     f"cut for {score:,} points"))
            if pay_bounty:
                roles.append(OSU_ROLE_ID)
                dirty = True
                _log_tx(type="role_assigned", scope="student", subjectId=sid,
                        subjectName=_full_name(row), amount=0,
                        description=(f"🎯 osu Champion — full combo on the lime trial, "
                                     f"{hits}/{LIME_TOTAL} with nothing missed"))
            if dirty:
                g.db.execute("UPDATE students SET roles = ? WHERE id = ?",
                             (json.dumps(roles), sid))

            # The blade and the note it hides go into the bag, one copy each
            # — the trial is replayable, and nobody needs a stack of notes.
            have = _owned(row)
            missing = {i: 1 for i in dungeon.LIME_SWORD_REWARD if not have.get(i)}
            if missing:
                _grant_items(sid, row, missing)

            # Score converts to shards on every cleared run, taxed like any
            # other earning so it shows up on the tax return with the rest.
            gross = score // dungeon.LIME_SCORE_PER_SHARD
            net, tax = dungeon.split_tax(gross)
            if gross > 0:
                stats = _wallet(row)
                stats["shards"] = int(stats.get("shards", 0)) + net
                _save_stats(sid, stats)
                label = (f"Lime trial — {hits}/{LIME_TOTAL} limes for {score:,} points")
                _receipt(sid, "earning", label, gross, tax, net)
                _log_tx(type="earn", scope="student", subjectId=sid,
                        subjectName=_full_name(row), amount=0,
                        description=f"💎 {label} · +{net:,} shards after {tax:,} tax")

        # Outside the transaction above — _award_points opens its own, and
        # sqlite3's context manager commits the outer block on the inner exit.
        awarded = 0
        if pay_bounty:
            body, st = _award_points(
                sid, LIME_FC_POINTS,
                f"Full combo on the lime trial — {hits}/{LIME_TOTAL} limes, "
                f"{score:,} points, nothing missed",
                "the Lime Sword",
            )
            if st == 200:
                awarded = int((body.get("data") or {}).get("applied") or 0)

        note = dungeon.ITEMS["spider_hunt_note"]
        return jsonify(ok=True, data={
            "alreadyHeld": already, "hits": hits, "score": score,
            "fullCombo": full_combo,
            "champion": full_combo or champ_already,
            "championIsNew": pay_bounty,
            "pointsAwarded": awarded,
            "itemsGranted": sorted(missing.keys()),
            "shards": net, "shardsGross": gross, "shardTax": tax,
            "note": {"id": "spider_hunt_note", "name": note["name"],
                     "text": note["note"]},
        })

    # ══ Dungeon economy ════════════════════════════════════════════
    # Shards, the shop, the inventory, the run loop and the tax cycle.
    # The run is server-authoritative: the browser is never told which
    # door is correct, and the clock the speed multiplier is measured
    # against is the server's, not the tab's.

    def _wallet(row):
        return {**default_stats(), **json.loads(row["stats"] or "{}")}

    def _extras(row):
        try:
            e = json.loads(row["extras"] or "{}")
            return e if isinstance(e, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _dungeon_barred(row):
        """Why this camper can't be in the dungeon right now, or None.

        Access is its own flag rather than a reading of the maze history.
        Staff need to be able to take the dungeon away without also
        un-completing the door maze — `doorsRewarded` is what stops a
        second claim of the maze's points, so clearing it to lock someone
        out would hand them the reward again."""
        e = _extras(row)
        if e.get("dungeonBanned"):
            return "The dungeon is closed to you right now — ask a camp instructor."
        beaten = bool(e.get("doorsRewarded")) or int(e.get("doorsCompleted") or 0) >= 1
        if not beaten:
            return "Finish the door maze first — the dungeon is behind it."
        return None

    def _window_for(gear, run, now=None):
        """The answer window, including the Thorn Ring's panic burst.

        The ring only matters for fifteen seconds of a run, so the bonus
        can't live on the loadout — it has to be asked for at the moment a
        question is served or answered.
        """
        base = float(gear.get("window") or dungeon.BASE_WINDOW_S)
        try:
            until = int(run["panicUntil"] or 0)
        except (KeyError, IndexError, TypeError):
            until = 0
        if until and (now or int(time.time() * 1000)) < until:
            # From the run, not the loadout: the ring that granted this is
            # already gone by the time the next question is served.
            try:
                base += float(run["panicBonus"] or 0.0)
            except (KeyError, IndexError, TypeError):
                pass
        return base

    def _floor_limit_ms(gear):
        """How long a floor lasts. The Healing Orb trades three minutes for
        thirty seconds; that's its entire cost, so it has to be respected
        everywhere the clock is read, not just where it's drawn."""
        lim = (gear or {}).get("floorTimeLimitMs")
        return int(lim) if lim else dungeon.FLOOR_TIME_LIMIT_MS

    def _tally_locked(row):
        """Everything except sending points shuts after a final tally.

        One place to ask, so a new spend endpoint can't quietly forget: the
        whole point of cashing out is that there is nothing left to spend on.
        """
        if _extras(row).get("finalTally"):
            return ("You've taken your final tally — the only thing still open "
                    "is sending points to a classmate.")
        return None

    def _save_stats(sid, stats):
        g.db.execute("UPDATE students SET stats = ? WHERE id = ?",
                     (json.dumps(stats), sid))

    def _owned(row):
        """{itemId: qty} across inventory AND equipped, so the counterpart
        discount still applies to gear you're currently wearing."""
        out = {}
        try:
            for e in json.loads(row["inventory"] or "[]"):
                if isinstance(e, dict) and e.get("id"):
                    out[e["id"]] = out.get(e["id"], 0) + int(e.get("qty") or 0)
        except Exception:  # noqa: BLE001
            pass
        try:
            for item_id in (json.loads(row["equipped"] or "{}") or {}).values():
                if item_id:
                    out[item_id] = out.get(item_id, 0) + 1
        except Exception:  # noqa: BLE001
            pass
        return out

    def _save_inventory(sid, owned_map, equipped):
        """Write the split. `owned_map` is TOTAL ownership — the same shape
        `_owned` returns, inventory plus whatever is worn — and the equipped
        copies are taken back out here before the loose inventory is stored.

        Doing the subtraction at the single place that writes is the whole
        point. Callers used to hand `_owned(row)` straight back in, which
        counts an equipped helmet once for the slot and once for the bag; on
        every buy, every starter pick and every answered door in the dungeon
        the worn gear was written into the inventory again, so a run cloned a
        camper's whole loadout one floor at a time."""
        equipped = equipped or {}
        loose = dict(owned_map)
        for worn in equipped.values():
            if worn in loose:
                loose[worn] -= 1
        inv = [{"id": k, "qty": v} for k, v in sorted(loose.items()) if v > 0]
        g.db.execute("UPDATE students SET inventory = ?, equipped = ? WHERE id = ?",
                     (json.dumps(inv), json.dumps(equipped), sid))

    def _grant_items(sid, row, additions):
        """Drop items straight into a camper's bag, no shop involved.

        `_save_inventory` takes total ownership and splits off what's worn,
        so the additions go on top of `_owned` untouched."""
        equipped = json.loads(row["equipped"] or "{}") or {}
        owned = _owned(row)
        for item_id, qty in additions.items():
            owned[item_id] = owned.get(item_id, 0) + int(qty)
        _save_inventory(sid, owned, equipped)

    def _receipt(sid, kind, description, gross, tax, net, reclaimed=0):
        rid = "rcpt-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(3)
        g.db.execute(
            "INSERT INTO receipts"
            " (id, studentId, at, kind, description, gross, tax, net, reclaimed)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, sid, int(time.time() * 1000), kind, description,
             int(gross), int(tax), int(net), int(reclaimed)),
        )
        return rid

    def _gear_for(row):
        stats = _wallet(row)
        try:
            equipped = json.loads(row["equipped"] or "{}") or {}
        except Exception:  # noqa: BLE001
            equipped = {}
        return dungeon.loadout(equipped, stats.get("luck", 0)), equipped, stats

    @app.route("/api/dungeon/catalogue", methods=["GET"])
    def dungeon_catalogue():
        """The shop shelf. Prices come back per-student where a counterpart
        discount applies, and locked items say why they're locked rather
        than being hidden."""
        sess = _current_session()
        owned, deepest, shards, luck = {}, 0, 0, 0
        if sess and sess["kind"] == "student":
            row = g.db.execute("SELECT * FROM students WHERE id = ?",
                               (sess["studentId"],)).fetchone()
            if row:
                owned = _owned(row)
                shards = _wallet(row).get("shards", 0)
                luck = _wallet(row).get("luck", 0)
                dr = g.db.execute(
                    "SELECT MAX(deepest) AS d FROM dungeon_runs WHERE studentId = ?",
                    (sess["studentId"],)).fetchone()
                deepest = int((dr["d"] if dr else 0) or 0)
        unlocked = deepest >= dungeon.INTERMEDIATE_UNLOCK_FLOOR
        out = []
        for it in dungeon.ITEMS.values():
            p = dungeon.price_for(it["id"], owned.keys())
            # The shelf shows what this camper pays, luck included — a
            # discount you only find out about at the till isn't a discount,
            # it's a surprise.
            payable, luck_off = dungeon.discounted(p["price"], luck)
            locked = it["tier"] == "intermediate" and not unlocked
            out.append({
                **{k: it[k] for k in ("id", "name", "tier", "slot", "blurb",
                                      "shardBonus", "maxHp", "defense", "window",
                                      "evadeChance", "negateChance", "flatNegate",
                                      "stackable", "capacity", "consumable",
                                      "reveals", "counterpart", "realWorld",
                                      "quest", "note", "noteBack")},
                "full": p["full"], "price": payable, "saved": p["saved"],
                "listed": p["price"], "luckOff": luck_off,
                "owned": owned.get(it["id"], 0),
                "locked": locked,
                "lockReason": (f"Reach floor {dungeon.INTERMEDIATE_UNLOCK_FLOOR} to unlock"
                               if locked else None),
            })
        return jsonify(ok=True, data={
            "items": out, "shards": shards, "deepest": deepest,
            "taxRate": dungeon.TAX_RATE,
            "slots": list(dungeon.SLOTS),
            "starterChoices": list(dungeon.STARTER_CHOICES),
            "unlockFloor": dungeon.INTERMEDIATE_UNLOCK_FLOOR,
            "discount": dungeon.COUNTERPART_DISCOUNT,
            "luck": luck, "luckDiscount": dungeon.luck_discount(luck),
        })

    @app.route("/api/students/me/dungeon", methods=["GET"])
    @require_student
    def dungeon_me():
        """Everything the inventory screen needs in one call."""
        sid = g.session["studentId"]
        row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
        if not row:
            return jsonify(ok=False, error="Student not found."), 404
        gear, equipped, stats = _gear_for(row)
        owned = _owned(row)
        inv = json.loads(row["inventory"] or "[]")
        extras = json.loads(row["extras"] or "{}")
        run = g.db.execute(
            "SELECT * FROM dungeon_runs WHERE studentId = ? AND endedAt IS NULL"
            " ORDER BY startedAt DESC LIMIT 1", (sid,)).fetchone()
        dr = g.db.execute("SELECT MAX(deepest) AS d FROM dungeon_runs WHERE studentId = ?",
                          (sid,)).fetchone()
        owed = g.db.execute(
            "SELECT COALESCE(SUM(tax),0) AS t, COUNT(*) AS n FROM receipts"
            " WHERE studentId = ? AND reclaimed = 0", (sid,)).fetchone()
        return jsonify(ok=True, data={
            "shards": stats.get("shards", 0),
            "points": stats.get("privatePoints", 0),
            "luck": stats.get("luck", 0),
            "luckSpent": stats.get("luckSpent", 0),
            "nextLuckCost": (dungeon.luck_level_cost(stats.get("luck", 0) + 1)
                             if stats.get("luck", 0) < dungeon.LUCK_MAX else None),
            "luckMax": dungeon.LUCK_MAX,
            "luckEffectiveness": dungeon.luck_effectiveness(stats.get("luck", 0)),
            "cashedOut": bool(stats.get("cashedOut")),
            "conversionFee": dungeon.discounted(
                dungeon.CONVERSION_FEE_POINTS, stats.get("luck", 0))[0],
            "luckDiscount": dungeon.luck_discount(stats.get("luck", 0)),
            # The bag stays readable when the dungeon is closed — losing
            # access shouldn't lose you the things you already earned.
            "barred": _dungeon_barred(row),
            "inventory": inv, "equipped": equipped, "owned": owned,
            "gear": {k: v for k, v in gear.items() if k != "items"},
            "starterClaimed": bool(extras.get("dungeonStarterClaimed")),
            "deepest": int((dr["d"] if dr else 0) or 0),
            "activeRun": bool(run),
            "unclaimedTax": int(owed["t"] or 0),
            "openReceipts": int(owed["n"] or 0),
            # Checkpoints held, and the floor one would drop them on. The
            # entry screen offers the choice; nothing is ever spent silently.
            "checkpoints": int(owned.get("checkpoint", 0)),
            "resumeFloor": _resume_floor(sid),
            # When this is true the gameverse is over for this camper and
            # the portal drops back to points, roles and the ledger.
            "worldSpiderKilled": bool(extras.get("worldSpiderKilled")),
            # Cashed out: shop shut, dungeon shut, transfers only.
            "finalTally": bool(extras.get("finalTally")),
            "tallyKeepRatio": TALLIED_TRANSFER_KEEP_RATIO,
            "transferKeepRatio": TRANSFER_KEEP_RATIO,
            # Boss-relevant totals, so the shop can say what a relic buys.
            "spiderBonus": round(gear.get("spiderBonus", 0.0), 4),
            "bossWindow": round(gear.get("bossWindow", 0.0), 2),
            "activeRunFloor": int(run["floor"]) if run else 0,
        })

    @app.route("/api/students/me/dungeon/starter", methods=["POST"])
    @require_student
    @block_when_frozen
    def dungeon_starter():
        """One free beginner item, once. Everything else is bought — points
        convert into shards, and gear only makes the run easier."""
        sid = g.session["studentId"]
        item_id = (request.get_json(silent=True) or {}).get("itemId") or ""
        if item_id not in dungeon.STARTER_CHOICES:
            return jsonify(ok=False, error="Pick one of the starter items."), 400
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            locked = _tally_locked(row)
            if locked:
                return jsonify(ok=False, error=locked), 403
            barred = _dungeon_barred(row)
            if barred:
                return jsonify(ok=False, error=barred), 403
            extras = json.loads(row["extras"] or "{}")
            if extras.get("dungeonStarterClaimed"):
                return jsonify(ok=False, error="You've already taken your free pick."), 400
            owned = _owned(row)
            owned[item_id] = owned.get(item_id, 0) + 1
            extras["dungeonStarterClaimed"] = item_id
            equipped = json.loads(row["equipped"] or "{}") or {}
            _save_inventory(sid, owned, equipped)
            g.db.execute("UPDATE students SET extras = ? WHERE id = ?",
                         (json.dumps(extras), sid))
        return jsonify(ok=True, data={"itemId": item_id,
                                      "name": dungeon.ITEMS[item_id]["name"]})

    @app.route("/api/students/me/dungeon/buy", methods=["POST"])
    @require_student
    @block_when_frozen
    def dungeon_buy():
        d = request.get_json(silent=True) or {}
        item_id = (d.get("itemId") or "").strip()
        try:
            qty = max(1, min(int(d.get("qty") or 1), 500))
        except (TypeError, ValueError):
            qty = 1
        it = dungeon.ITEMS.get(item_id)
        if not it:
            return jsonify(ok=False, error="No such item."), 404
        # Quest items are listed so the inventory can name them, but they
        # cost nothing — buying one would be a free grant.
        if it["quest"]:
            return jsonify(ok=False, error="That one isn't for sale — go and earn it."), 400
        if not it["stackable"]:
            qty = 1
        sid = g.session["studentId"]
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            barred = _dungeon_barred(row)
            if barred:
                return jsonify(ok=False, error=barred), 403
            # You can rummage through your own bag mid-run and swap what
            # you're holding, but the shop is upstairs. Restocking from
            # inside a fight turns a run into a shopping trip with a
            # health bar.
            if _active_run(sid):
                return jsonify(
                    ok=False,
                    error="No shopping from inside the dungeon — you can still "
                          "swap what's already in your bag."), 400
            if _extras(row).get("finalTally"):
                return jsonify(ok=False,
                               error="You've cashed out. The shop is shut."), 400
            owned = _owned(row)
            # The Goblin Mask is bought with shards AND with whatever else
            # you value most. Resolved server-side so "most expensive" can't
            # be argued with, and quest items are exempt — they aren't
            # yours to trade.
            sacrificed = None
            if it.get("sacrifice"):
                candidates = [(dungeon.ITEMS[i]["cost"], i) for i in owned
                              if owned[i] > 0 and i in dungeon.ITEMS
                              and not dungeon.ITEMS[i].get("quest")
                              and dungeon.ITEMS[i]["cost"] > 0]
                if not candidates:
                    return jsonify(ok=False, error=(
                        "The mask wants something of yours as well, and your "
                        "bag is empty.")), 400
                sacrificed = max(candidates)[1]
            if it["tier"] == "intermediate":
                dr = g.db.execute(
                    "SELECT MAX(deepest) AS d FROM dungeon_runs WHERE studentId = ?",
                    (sid,)).fetchone()
                if int((dr["d"] if dr else 0) or 0) < dungeon.INTERMEDIATE_UNLOCK_FLOOR:
                    return jsonify(
                        ok=False,
                        error=(f"Locked — reach floor "
                               f"{dungeon.INTERMEDIATE_UNLOCK_FLOOR} first."),
                    ), 403
            if not it["stackable"] and owned.get(item_id):
                return jsonify(ok=False, error=f"You already own the {it['name']}."), 400

            stats = _wallet(row)
            p = dungeon.price_for(item_id, owned.keys())
            # Luck's discount lands on the shelf price BEFORE tax, so the
            # withholding is worked out on what the camper actually pays
            # rather than on a price nobody was charged.
            listed = p["price"] * qty
            shelf, luck_off = dungeon.discounted(listed, stats.get("luck", 0))
            total, tax = dungeon.purchase_total(shelf)
            have = int(stats.get("shards", 0))
            if have < total:
                return jsonify(
                    ok=False, shortfall=total - have,
                    error=(f"That costs {total:,} shards with tax and you have "
                           f"{have:,} — {total - have:,} short."),
                ), 400

            stats["shards"] = have - total
            _save_stats(sid, stats)
            owned[item_id] = owned.get(item_id, 0) + qty
            equipped_now = json.loads(row["equipped"] or "{}") or {}
            # The sacrifice is taken at the same moment as the shards, so a
            # failed purchase never eats an item.
            sacrificed_name = None
            if sacrificed:
                sacrificed_name = dungeon.ITEMS[sacrificed]["name"]
                owned[sacrificed] -= 1
                if owned[sacrificed] <= 0:
                    owned.pop(sacrificed, None)
                    equipped_now = {k: v for k, v in equipped_now.items()
                                    if v != sacrificed}
            _save_inventory(sid, owned, equipped_now)
            label = it["name"] + (f" ×{qty}" if qty > 1 else "")
            rid = _receipt(sid, "purchase",
                           f"Purchased {label}"
                           + (f" — sacrificed {sacrificed_name}" if sacrificed_name else "")
                           + (f" (🍀 {luck_off:,} off)" if luck_off else ""),
                           shelf, tax, shelf)
        return jsonify(ok=True, data={
            "itemId": item_id, "qty": qty, "shelf": shelf, "tax": tax,
            "paid": total, "saved": p["saved"] * qty,
            "listed": listed, "luckOff": luck_off,
            "sacrificed": sacrificed_name,
            "shards": stats["shards"], "receiptId": rid,
        })

    @app.route("/api/students/me/dungeon/equip", methods=["POST"])
    @require_student
    @block_when_frozen
    def dungeon_equip():
        """Equip or unequip. Passing a null itemId clears the slot."""
        d = request.get_json(silent=True) or {}
        slot = (d.get("slot") or "").strip()
        item_id = (d.get("itemId") or "").strip() or None
        if slot not in dungeon.SLOTS:
            return jsonify(ok=False, error="No such equipment slot."), 400
        sid = g.session["studentId"]
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            equipped = json.loads(row["equipped"] or "{}") or {}
            if item_id:
                it = dungeon.ITEMS.get(item_id)
                if not it:
                    return jsonify(ok=False, error="No such item."), 404
                if it["slot"] != slot:
                    return jsonify(ok=False,
                                   error=f"The {it['name']} doesn't go in that slot."), 400
                if not _owned(row).get(item_id):
                    return jsonify(ok=False, error="You don't own that."), 400
                equipped[slot] = item_id
            else:
                equipped.pop(slot, None)
            # _owned is total ownership; _save_inventory splits off the worn
            # copies against the equipped map we just changed.
            _save_inventory(sid, _owned(row), equipped)
            gear = dungeon.loadout(equipped, _wallet(row).get("luck", 0))
        return jsonify(ok=True, data={"equipped": equipped,
                                      "gear": {k: v for k, v in gear.items()
                                               if k != "items"}})

    @app.route("/api/students/me/dungeon/convert", methods=["POST"])
    @require_student
    @block_when_frozen
    def dungeon_convert():
        """Shards ⇄ points, across a counter that charges to be used.

        Two rules keep the counter from being a machine you can run in a
        loop. Every conversion, either direction, costs a flat
        CONVERSION_FEE_POINTS up front — flat, so it doesn't scale away at
        volume the way a percentage does. And cashing shards in is a
        once-ever, all-of-it decision: `stats.cashedOut` is the receipt, and
        the amount isn't the camper's to choose. Shards go one way after
        that, which is the direction the dungeon pays in anyway."""
        d = request.get_json(silent=True) or {}
        direction = (d.get("direction") or "").strip()
        try:
            amount = int(d.get("amount") or 0)
        except (TypeError, ValueError):
            return jsonify(ok=False, error="Enter a whole number."), 400
        sid = g.session["studentId"]
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            locked = _tally_locked(row)
            if locked:
                return jsonify(ok=False, error=locked), 403
            barred = _dungeon_barred(row)
            if barred:
                return jsonify(ok=False, error=barred), 403
            stats = _wallet(row)
            have_pts = int(stats.get("privatePoints", 0))
            # Luck haggles the counter down like any other price.
            fee, fee_off = dungeon.discounted(dungeon.CONVERSION_FEE_POINTS,
                                              stats.get("luck", 0))
            if direction not in ("shards-to-points", "points-to-shards"):
                return jsonify(ok=False, error="Pick a direction."), 400
            if have_pts < fee:
                return jsonify(
                    ok=False,
                    error=(f"The counter charges {fee:,} points to trade, and you "
                           f"have {have_pts:,}."),
                ), 400

            if direction == "shards-to-points":
                if stats.get("cashedOut"):
                    return jsonify(
                        ok=False,
                        error="You've already cashed your shards in. That one only "
                              "happens once — shards buy gear from here on.",
                    ), 400
                # All of them, whatever the browser asked for.
                amount = int(stats.get("shards", 0))
                if amount <= 0:
                    return jsonify(ok=False, error="You have no shards to cash in."), 400
                pts, spent = dungeon.shards_to_points(amount)
                if pts <= 0:
                    return jsonify(
                        ok=False,
                        error=f"{dungeon.SHARDS_PER_POINT} shards make 1 point — "
                              f"you need at least that many.",
                    ), 400
                net, tax = dungeon.split_tax(pts)
                stats["shards"] -= spent
                # Fee first, then the proceeds — a camper who can't cover the
                # fee never gets here, so this can't push anyone negative.
                stats["privatePoints"] = have_pts - fee + net
                stats["totalPointsEarned"] = stats.get("totalPointsEarned", 0) + net
                stats["cashedOut"] = True
                _save_stats(sid, stats)
                rid = _receipt(sid, "conversion",
                               f"Cashed out {spent:,} shards → {pts:,} points"
                               f" (−{fee:,} pt counter fee)", pts, tax, net)
                _log_tx(type="earn", scope="student", subjectId=sid,
                        subjectName=_full_name(row), amount=net - fee,
                        description=(f"💎 cashed out {spent:,} shards → {net:,} pts"
                                     f" (after {tax:,} tax, −{fee:,} fee)"))
                return jsonify(ok=True, data={"gross": pts, "tax": tax, "net": net,
                                              "fee": fee, "cashedOut": True,
                                              "shards": stats["shards"],
                                              "points": stats["privatePoints"],
                                              "receiptId": rid})
            if direction == "points-to-shards":
                if amount <= 0:
                    return jsonify(ok=False, error="Enter an amount above zero."), 400
                if have_pts - fee < amount:
                    return jsonify(
                        ok=False,
                        error=(f"That's {amount:,} points plus the {fee:,}-point "
                               f"counter fee, and you have {have_pts:,}."),
                    ), 400
                gross = dungeon.points_to_shards(amount)
                net, tax = dungeon.split_tax(gross)
                stats["privatePoints"] = have_pts - fee - amount
                stats["shards"] = stats.get("shards", 0) + net
                _save_stats(sid, stats)
                rid = _receipt(sid, "conversion",
                               f"Converted {amount:,} points → {gross:,} shards"
                               f" (−{fee:,} pt counter fee)",
                               gross, tax, net)
                _log_tx(type="spend", scope="student", subjectId=sid,
                        subjectName=_full_name(row), amount=-(amount + fee),
                        description=(f"💎 {amount:,} pts → {net:,} shards"
                                     f" (after {tax:,} tax, −{fee:,} fee)"))
                return jsonify(ok=True, data={"gross": gross, "tax": tax, "net": net,
                                              "fee": fee,
                                              "shards": stats["shards"],
                                              "points": stats["privatePoints"],
                                              "receiptId": rid})
        return jsonify(ok=False, error="Pick a direction."), 400

    @app.route("/api/students/me/dungeon/luck", methods=["POST"])
    @require_student
    @block_when_frozen
    def dungeon_buy_luck():
        """Luck costs points and the price climbs 12% a level, so the
        guaranteed double at 40 is a camp-long project rather than a
        weekend one."""
        sid = g.session["studentId"]
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            stats = _wallet(row)
            lv = int(stats.get("luck", 0))
            if lv >= dungeon.LUCK_MAX:
                return jsonify(ok=False,
                               error=f"Luck is maxed at {dungeon.LUCK_MAX}."), 400
            cost = dungeon.luck_level_cost(lv + 1)
            have = int(stats.get("privatePoints", 0))
            if have < cost:
                return jsonify(
                    ok=False, shortfall=cost - have,
                    error=(f"Luck {lv + 1} costs {cost:,} points and you have "
                           f"{have:,} — {cost - have:,} short."),
                ), 400
            stats["privatePoints"] = have - cost
            stats["luck"] = lv + 1
            stats["luckSpent"] = int(stats.get("luckSpent", 0)) + cost
            _save_stats(sid, stats)
            _log_tx(type="luck", scope="student", subjectId=sid,
                    subjectName=_full_name(row), amount=-cost,
                    description=f"🍀 Luck {lv} → {lv + 1} for {cost:,} pts")
        return jsonify(ok=True, data={
            "luck": stats["luck"], "spent": cost,
            "points": stats["privatePoints"],
            "effectiveness": dungeon.luck_effectiveness(stats["luck"]),
            "nextCost": (dungeon.luck_level_cost(stats["luck"] + 1)
                         if stats["luck"] < dungeon.LUCK_MAX else None),
        })

    # ── The run ────────────────────────────────────────────────────
    def _run_state(run, gear=None):
        d = dict(run)
        d.pop("correctSide", None)      # never leaves the server
        # questionMeta carries the ANSWER to whatever is currently on screen —
        # it's there so the answer step can log what was asked. Sending the
        # row wholesale would hand the client the answer to the open door,
        # which is the one thing this endpoint must never do.
        d.pop("questionMeta", None)
        d.pop("hurtLog", None)
        if gear:
            d["gear"] = {k: v for k, v in gear.items() if k != "items"}
        d["floorLimitMs"] = _floor_limit_ms(gear)
        d["floorBase"] = dungeon.base_shards(d["floor"])
        # What a wrong door on THIS floor would cost, after armour — the
        # number climbs with depth, and players should see it climbing.
        if gear:
            for key, fast in (("dangerFast", True), ("dangerSlow", False)):
                raw = dungeon.wrong_door_damage(d["floor"], fast=fast)
                d[key] = round(raw * (1 - dungeon.damage_reduction(gear["defense"]))
                               * (1 - gear["flatNegate"]))
        return d

    def _active_run(sid):
        return g.db.execute(
            "SELECT * FROM dungeon_runs WHERE studentId = ? AND endedAt IS NULL"
            " ORDER BY startedAt DESC LIMIT 1", (sid,)).fetchone()

    def _arrows(owned):
        """Which arrow the bow will fire — the fancy one first."""
        for aid in ("drill_needle_arrow", "basic_arrow"):
            if owned.get(aid, 0) > 0:
                return aid
        return None

    def _bank(sid, run, alive):
        """End a run and pay out. Walk out and you keep everything; die and
        30% stays in the dungeon."""
        gross = int(run["escrow"] or 0)
        kept = gross if alive else round(gross * dungeon.DEATH_KEEP_FRACTION)
        net, tax = dungeon.split_tax(kept)
        row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
        stats = _wallet(row)
        stats["shards"] = int(stats.get("shards", 0)) + net
        _save_stats(sid, stats)
        g.db.execute(
            "UPDATE dungeon_runs SET endedAt = ?, outcome = ? WHERE id = ?",
            (int(time.time() * 1000), "alive" if alive else "dead", run["id"]))
        rid = None
        # Needed by the return value whether or not there was anything to
        # bank — a run that ends carrying nothing still started somewhere.
        start = int((run["startFloor"] if "startFloor" in run.keys() else 1) or 1)
        if kept > 0:
            label = (f"Dungeon run — floors {start}–{run['deepest']}" if alive else
                     f"Died on floor {run['floor']} — 70% of {gross:,} recovered")
            rid = _receipt(sid, "earning", label, kept, tax, net)
            _log_tx(type="earn", scope="student", subjectId=sid,
                    subjectName=_full_name(row), amount=0,
                    description=(f"💎 {label} · +{net:,} shards after {tax:,} tax"))
        return {"gross": gross, "kept": kept, "tax": tax, "net": net,
                "shards": stats["shards"], "alive": alive,
                "deepest": run["deepest"], "startFloor": start, "receiptId": rid}

    @app.route("/api/dungeon/run", methods=["GET"])
    @require_student
    def dungeon_run_get():
        run = _active_run(g.session["studentId"])
        if not run:
            return jsonify(ok=True, data=None)
        row = g.db.execute("SELECT * FROM students WHERE id = ?",
                           (g.session["studentId"],)).fetchone()
        gear, _, _ = _gear_for(row)
        return jsonify(ok=True, data=_run_state(run, gear))

    def _resume_floor(sid):
        """The floor this camper's last run ended on — what a Checkpoint buys
        them back. 0 when they've never finished a run, since there's nothing
        to return to."""
        r = g.db.execute(
            "SELECT floor FROM dungeon_runs WHERE studentId = ? AND endedAt IS NOT NULL"
            " ORDER BY endedAt DESC LIMIT 1", (sid,)).fetchone()
        return int((r["floor"] if r else 0) or 0)

    @app.route("/api/dungeon/run/start", methods=["POST"])
    @require_student
    @block_when_frozen
    def dungeon_run_start():
        sid = g.session["studentId"]
        body = request.get_json(silent=True) or {}
        want_checkpoint = bool(body.get("useCheckpoint"))
        with g.db:
            if _active_run(sid):
                return jsonify(ok=False, error="You're already in the dungeon."), 400
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            locked = _tally_locked(row)
            if locked:
                return jsonify(ok=False, error=locked), 403
            barred = _dungeon_barred(row)
            if barred:
                return jsonify(ok=False, error=barred), 403
            gear, equipped, _ = _gear_for(row)

            # A Checkpoint is only spent when it actually buys something. If
            # the last run ended on floor 1 there's nothing to skip, so the
            # item stays in the bag rather than vanishing for no benefit.
            start_floor, spent_checkpoint = 1, False
            if want_checkpoint:
                owned = _owned(row)
                resume = _resume_floor(sid)
                if owned.get("checkpoint", 0) <= 0:
                    return jsonify(ok=False, error="You don't have a Checkpoint."), 400
                if resume <= 1:
                    return jsonify(
                        ok=False,
                        error="No checkpoint to return to — your last run ended on floor 1."), 400
                owned["checkpoint"] -= 1
                _save_inventory(sid, {k: v for k, v in owned.items() if v > 0}, equipped)
                start_floor, spent_checkpoint = resume, True

            rid = "run-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(3)
            now = int(time.time() * 1000)
            g.db.execute(
                "INSERT INTO dungeon_runs"
                " (id, studentId, startedAt, floor, deepest, hp, maxHp, escrow,"
                "  floorStart, startFloor)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (rid, sid, now, start_floor, start_floor,
                 gear["maxHp"], gear["maxHp"], now, start_floor))
            run = g.db.execute("SELECT * FROM dungeon_runs WHERE id = ?", (rid,)).fetchone()
        state = _run_state(run, gear)
        state["usedCheckpoint"] = spent_checkpoint
        state["startFloor"] = start_floor
        return jsonify(ok=True, data=state)

    # How hard a floor is. A BAND, not a ceiling — the floor of the band
    # matters more than the top of it.
    #
    # It used to be "difficulty <= cap", which meant floor 300 could still
    # serve a difficulty-3 question. Deep floors were therefore only harder
    # on average, and a camper who knew the easy material could ride the
    # low end of the distribution a very long way. The band now moves as a
    # whole: past floor 100 nothing below difficulty 5 is served at all.
    def _difficulty_band(floor_no):
        tier = floors.tier_for_floor(floor_no)
        if tier <= 0:
            return 3, 4          # floors 1–50: the gentle end, but not trivial
        if tier == 1:
            return 4, 5          # 51–100
        return 5, 5              # 101+: hard questions only, forever

    def _difficulty_cap(floor_no):
        return _difficulty_band(floor_no)[1]

    def _draw_question(sid, floor_no):
        """Pick this floor's question.

        Preference order:
          1. a bank question this camper has never answered, inside the
             floor's difficulty band,
          2. any bank question they've never answered,
          3. the one they answered longest ago,
          4. a procedurally generated floor (floors.py) if the bank is empty.

        Step 4 is what keeps "infinity" honest. The bank is 1,200 questions
        plus whatever staff add, which is a lot but not infinite, and a mode
        that runs out of questions and stops isn't endless. floors.py can
        generate forever, so the descent continues either way.
        """
        lo, hi = _difficulty_band(floor_no)
        pick = g.db.execute(
            "SELECT q.* FROM infinity_questions q"
            " WHERE q.difficulty BETWEEN ? AND ?"
            "   AND NOT EXISTS (SELECT 1 FROM infinity_answers a"
            "                    WHERE a.studentId = ? AND a.questionId = q.id)"
            " ORDER BY RANDOM() LIMIT 1", (lo, hi, sid)).fetchone()
        if not pick:
            # Out of unseen questions in the band — widen upward first, so
            # running dry makes a floor harder rather than easier.
            pick = g.db.execute(
                "SELECT q.* FROM infinity_questions q"
                " WHERE q.difficulty >= ?"
                "   AND NOT EXISTS (SELECT 1 FROM infinity_answers a"
                "                    WHERE a.studentId = ? AND a.questionId = q.id)"
                " ORDER BY RANDOM() LIMIT 1", (lo, sid)).fetchone()
        if not pick:
            pick = g.db.execute(
                "SELECT q.* FROM infinity_questions q"
                " WHERE NOT EXISTS (SELECT 1 FROM infinity_answers a"
                "                    WHERE a.studentId = ? AND a.questionId = q.id)"
                " ORDER BY RANDOM() LIMIT 1", (sid,)).fetchone()
        if not pick:
            # Everything has been seen at least once — go round again,
            # oldest first, so it's the least fresh one that comes back.
            pick = g.db.execute(
                "SELECT q.* FROM infinity_questions q"
                " LEFT JOIN (SELECT questionId, MAX(answeredAt) AS seen"
                "              FROM infinity_answers WHERE studentId = ?"
                "             GROUP BY questionId) a ON a.questionId = q.id"
                " ORDER BY COALESCE(a.seen, 0) ASC, RANDOM() LIMIT 1",
                (sid,)).fetchone()
        if pick:
            return {"id": pick["id"], "question": pick["question"],
                    "answer": str(pick["answer"]), "wrong": str(pick["wrongAnswer"]),
                    "unit": pick["unit"] or "Grade 9",
                    "difficulty": int(pick["difficulty"] or 3)}
        question, correct, wrong, unit_label = floors.generate(floor_no)
        return {"id": f"floor-{floor_no}", "question": question,
                "answer": str(correct), "wrong": str(wrong),
                "unit": unit_label, "difficulty": _difficulty_cap(floor_no)}

    def _log_infinity_answer(sid, run, side, correct, elapsed, floor):
        """One row per door taken. Never raises: a tracking write must not be
        able to cost a camper a run they answered correctly."""
        try:
            meta = json.loads(run["questionMeta"] or "{}")
        except (TypeError, ValueError):
            meta = {}
        try:
            g.db.execute(
                "INSERT INTO infinity_answers"
                " (id, studentId, questionId, question, answer, chosen, correct,"
                "  unit, difficulty, floor, elapsedMs, runId, answeredAt)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("ians-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(4),
                 sid, run["questionId"] or "", meta.get("question", ""),
                 str(meta.get("answer", "")), side, 1 if correct else 0,
                 meta.get("unit", ""), int(meta.get("difficulty") or 0),
                 int(floor), int(elapsed * 1000), run["id"],
                 int(time.time() * 1000)))
        except Exception:
            pass

    @app.route("/api/dungeon/run/question", methods=["POST"])
    @require_student
    @block_when_frozen
    def dungeon_run_question():
        """Serve the current floor's question. The server decides which door
        is right and starts the clock — the browser is told neither.

        Questions come from `infinity_questions` — the seeded Grade 9 bank
        plus anything staff have written — filtered to what this camper
        hasn't answered yet, so a descent doesn't ask the same thing twice.
        floors.py still generates a floor when the bank can't supply one,
        which is what stops an endless mode from running out."""
        sid = g.session["studentId"]
        with g.db:
            run = _active_run(sid)
            if not run:
                return jsonify(ok=False, error="You're not in the dungeon."), 400
            floor_no = int(run["floor"] or 1)
            drawn = _draw_question(sid, floor_no)
            question, correct, wrong = drawn["question"], drawn["answer"], drawn["wrong"]
            unit_label = drawn["unit"]
            left_correct = secrets.randbelow(2) == 0
            now = int(time.time() * 1000)
            # correctSide is still the only thing the resolve step trusts.
            # questionMeta rides along so the answer can be logged against
            # the question that was actually on screen.
            g.db.execute(
                "UPDATE dungeon_runs SET questionAt = ?, questionId = ?, correctSide = ?,"
                " questionMeta = ? WHERE id = ?",
                (now, drawn["id"], "L" if left_correct else "R",
                 json.dumps({"question": question, "answer": correct,
                             "unit": unit_label, "difficulty": drawn["difficulty"]}),
                 run["id"]))
            floor_start = run["floorStart"] or now
        row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
        gear, _, _ = _gear_for(row)
        cut = (1 - dungeon.damage_reduction(gear["defense"])) * (1 - gear["flatNegate"])
        misses = int(run["wrongCount"] or 0)
        # The Fortune Scale tips towards a door — and is wrong 30% of the
        # time. The lie is decided HERE, server-side, so it's the same lie
        # every time this question is looked at rather than a fresh coin
        # flip the browser could re-roll until it liked the answer.
        hint_side = None
        if gear.get("revealChance", 0.0) > 0:
            truthful = secrets.randbelow(10000) < int(gear["revealChance"] * 10000)
            right_side = "L" if left_correct else "R"
            hint_side = right_side if truthful else ("R" if left_correct else "L")
        return jsonify(ok=True, data={
            "questionId": f"floor-{floor_no}", "question": question,
            "unit": unit_label,
            "left":  str(correct if left_correct else wrong),
            "right": str(wrong if left_correct else correct),
            "hintSide": hint_side,
            "hintAccuracy": round(gear.get("revealChance", 0.0), 3) or None,
            "floor": run["floor"], "askedAt": now,
            "floorDeadline": floor_start + _floor_limit_ms(gear),
            "dangerFast": round(dungeon.wrong_door_damage(run["floor"], True, misses) * cut),
            "dangerSlow": round(dungeon.wrong_door_damage(run["floor"], False, misses) * cut),
            "misses": misses,
            "floorBase": dungeon.base_shards(run["floor"]),
        })

    @app.route("/api/dungeon/run/answer", methods=["POST"])
    @require_student
    @block_when_frozen
    def dungeon_run_answer():
        """Resolve a door. Elapsed time comes from the server's own clock, so
        the speed multiplier can't be edited from the tab."""
        sid = g.session["studentId"]
        body = request.get_json(silent=True) or {}
        side = (body.get("side") or "").upper()
        if side not in ("L", "R"):
            return jsonify(ok=False, error="Pick a door."), 400
        # The spec asks for the clock to stop while the tab is hidden, which
        # only the browser can observe. We take its word for it but clamp
        # hard: never more than the time that actually passed, and never
        # more than BLUR_CREDIT_CAP_MS in total. Worst case a tampered
        # client claims a ×2.0 — which is exactly what an honest fast
        # answer already earns, so the ceiling is the same either way.
        BLUR_CREDIT_CAP_MS = 5 * 60 * 1000
        try:
            blur_ms = max(0, min(int(body.get("blurMs") or 0), BLUR_CREDIT_CAP_MS))
        except (TypeError, ValueError):
            blur_ms = 0
        with g.db:
            run = _active_run(sid)
            if not run:
                return jsonify(ok=False, error="You're not in the dungeon."), 400
            if not run["correctSide"] or not run["questionAt"]:
                return jsonify(ok=False, error="No question is open."), 400
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            gear, equipped, stats = _gear_for(row)
            owned = _owned(row)
            luck = int(stats.get("luck", 0))
            now = int(time.time() * 1000)
            raw_ms = now - int(run["questionAt"])
            elapsed = max(0.0, (raw_ms - min(blur_ms, max(0, raw_ms))) / 1000.0)
            floor = int(run["floor"])
            correct = (side == run["correctSide"])

            # A drawn bow spends an arrow and may proc on the drill needle.
            arrow_bonus, proc = 1.0, None
            damage_soften = 0.0
            if run["bowDrawn"]:
                aid = run["arrowType"] or _arrows(owned)
                if aid and owned.get(aid, 0) > 0:
                    owned[aid] -= 1
                    if aid == "drill_needle_arrow" and secrets.randbelow(1000) < 30:
                        if secrets.randbelow(2) == 0:
                            arrow_bonus, proc = 1.1, "shards"
                        else:
                            damage_soften, proc = 0.20, "damage"
                _save_inventory(sid, {k: v for k, v in owned.items() if v > 0}, equipped)

            payload = {"correct": correct, "elapsed": round(elapsed, 2),
                       "speed": round(dungeon.speed_multiplier(
                           elapsed, _window_for(gear, run, now)), 3),
                       "window": _window_for(gear, run, now), "proc": proc}

            # Log the door before resolving its consequences. Every question
            # every camper answers ends up here — right or wrong, whether or
            # not the run survives it — which is what makes the admin view a
            # record of what the camp actually practised rather than a record
            # of what it got right.
            _log_infinity_answer(sid, run, side, correct, elapsed, floor)

            if correct:
                gained = dungeon.floor_reward(floor, elapsed, gear, luck, arrow_bonus)
                found = 0
                if secrets.randbelow(10000) < int(dungeon.LUCK_DISCOVERY_CHANCE * 10000):
                    found = dungeon.luck_discovery(stats.get("luckSpent", 0), floor)
                escrow = int(run["escrow"] or 0) + gained + found
                nxt = floor + 1

                # Extendo-Leggings: step over the next floor and take its
                # shards anyway. Rolled once per cleared door, and it pays
                # the skipped floor at a flat speed rather than the one you
                # happened to answer at — you weren't there.
                skipped = []
                while (gear.get("skipFloorChance", 0.0) > 0
                       and len(skipped) < 3
                       and secrets.randbelow(10000)
                           < int(gear["skipFloorChance"] * 10000)):
                    bonus = dungeon.floor_reward(nxt, 2.0, gear, luck)
                    escrow += bonus
                    skipped.append({"floor": nxt, "shards": bonus})
                    nxt += 1

                # Eco-Friendly Boots: a streak of right answers is worth
                # health. Tracked on the run, and any wrong door resets it.
                streak = int(run["streak"] or 0) + 1
                healed = 0
                hp_now = int(run["hp"])
                if (gear.get("streakEvery") and gear.get("streakHeal")
                        and streak % int(gear["streakEvery"]) == 0):
                    healed = min(int(gear["streakHeal"]),
                                 int(run["maxHp"]) - hp_now)
                    healed = max(0, healed)
                    hp_now += healed

                g.db.execute(
                    "UPDATE dungeon_runs SET escrow = ?, floor = ?, deepest = ?,"
                    " floorStart = ?, wrongCount = 0, questionAt = NULL,"
                    " correctSide = NULL, bowDrawn = 0, arrowType = NULL,"
                    " streak = ?, hp = ? WHERE id = ?",
                    (escrow, nxt, max(int(run["deepest"]), nxt), now,
                     streak, hp_now, run["id"]))
                payload.update({"gained": gained, "found": found, "escrow": escrow,
                                "floor": nxt, "hp": hp_now, "maxHp": run["maxHp"],
                                "streak": streak, "healed": healed,
                                "skipped": skipped})
                return jsonify(ok=True, data=payload)

            # Wrong door: 120 inside the window, 60 after and for every
            # later mistake on the same floor. Loss is 90% of what that door
            # would actually have paid at this speed.
            repeats = int(run["wrongCount"] or 0)
            first = repeats == 0
            raw = dungeon.wrong_door_damage(
                floor, fast=(first and elapsed <= dungeon.FAST_WRONG_CUTOFF_S),
                repeats=repeats)
            dealt, why = dungeon.apply_damage(raw, gear, luck)
            if damage_soften and dealt:
                dealt = round(dealt * (1.0 - damage_soften))
            lost = min(int(run["escrow"] or 0),
                       dungeon.wrong_door_loss(floor, elapsed, gear, luck))
            escrow = int(run["escrow"] or 0) - lost
            hp = int(run["hp"]) - dealt

            # The earring eats one killing blow, then shatters.
            earring_used = False
            if hp <= 0 and gear["hasEarring"] and not int(run["earringUsed"] or 0):
                hp = 1
                earring_used = True
                equipped.pop("earring", None)
                owned.pop("earring", None)
                _save_inventory(sid, {k: v for k, v in owned.items() if v > 0}, equipped)

            # Thorn Ring: the first time health falls through its threshold,
            # every window grows for fifteen seconds — and the ring is spent.
            panic_until = int(run["panicUntil"] or 0)
            panic_bonus = float(run["panicBonus"] or 0.0)
            panic_fired = False
            if (gear.get("panicWindowBonus")
                    and 0 < hp <= int(run["maxHp"]) * gear.get("panicThreshold", 0.0)
                    and owned.get("thorn_ring")):
                panic_until = now + int(gear.get("panicDurationMs") or 0)
                panic_bonus = float(gear.get("panicWindowBonus") or 0.0)
                panic_fired = True
                owned.pop("thorn_ring", None)
                equipped = {k: v for k, v in equipped.items() if v != "thorn_ring"}
                _save_inventory(sid, {k: v for k, v in owned.items() if v > 0}, equipped)

            # Keep a short rolling log of damage, timestamped. It exists for
            # the Goblin Mask, which heals a share of "the last twelve
            # seconds" and therefore needs to know what those cost.
            try:
                hurt = json.loads(run["hurtLog"] or "[]")
            except (TypeError, ValueError):
                hurt = []
            if dealt:
                hurt.append({"t": now, "d": int(dealt)})
            hurt = [h for h in hurt if now - int(h.get("t") or 0) <= 60000][-40:]

            g.db.execute(
                "UPDATE dungeon_runs SET hp = ?, escrow = ?, wrongCount = ?,"
                " questionAt = NULL, correctSide = NULL, bowDrawn = 0, arrowType = NULL,"
                " earringUsed = ?, streak = 0, hurtLog = ?, panicUntil = ?,"
                " panicBonus = ? WHERE id = ?",
                (max(0, hp), escrow, int(run["wrongCount"] or 0) + 1,
                 1 if (earring_used or int(run["earringUsed"] or 0)) else 0,
                 json.dumps(hurt), panic_until or None, panic_bonus, run["id"]))
            payload.update({"damage": dealt, "blocked": why, "lost": lost,
                            "escrow": escrow, "hp": max(0, hp),
                            "maxHp": run["maxHp"], "floor": floor,
                            "earringUsed": earring_used, "streak": 0,
                            "panic": (gear.get("panicDurationMs") if panic_fired else 0)})
            if hp <= 0:
                # Anything that promised to vanish on death makes good on it
                # before the run banks — the Perfectionist necklace is only
                # worth its price because it can't be re-worn afterwards.
                lost_items = []
                for iid in gear.get("vanishOnDeath", []):
                    if owned.get(iid):
                        owned.pop(iid, None)
                        equipped = {k: v for k, v in equipped.items() if v != iid}
                        lost_items.append(dungeon.ITEMS[iid]["name"])
                if lost_items:
                    _save_inventory(sid, {k: v for k, v in owned.items() if v > 0},
                                    equipped)
                    payload["vanished"] = lost_items
                run = g.db.execute("SELECT * FROM dungeon_runs WHERE id = ?",
                                   (run["id"],)).fetchone()
                payload["death"] = _bank(sid, run, alive=False)
        return jsonify(ok=True, data=payload)

    @app.route("/api/dungeon/run/skip", methods=["POST"])
    @require_student
    @block_when_frozen
    def dungeon_run_skip():
        """The floor timer running out.

        This used to move you on for free — no shards, but no damage. That
        turned out to be the way past the whole dungeon: any question you
        couldn't answer, you waited out, and you never had to risk a door.
        Someone walked to floor 219 that way. Timing out is now a failed
        floor and costs what a slow wrong door costs, armour included. You
        still advance, and you still lose nothing you were carrying — but
        stalling is no longer safer than answering.
        """
        sid = g.session["studentId"]
        with g.db:
            run = _active_run(sid)
            if not run:
                return jsonify(ok=False, error="You're not in the dungeon."), 400
            now = int(time.time() * 1000)
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            gear, _, _ = _gear_for(row)
            # The orb's shorter clock has to be read here too, or a camper
            # wearing it gets booted by the browser at 30s and told by the
            # server there's still time.
            if now < int(run["floorStart"] or now) + _floor_limit_ms(gear):
                return jsonify(ok=False, error="There's still time on this floor."), 400
            floor = int(run["floor"])
            raw = dungeon.wrong_door_damage(floor, fast=False,
                                            repeats=int(run["wrongCount"] or 0))
            cut = ((1 - dungeon.damage_reduction(gear["defense"]))
                   * (1 - gear["flatNegate"]))
            damage = max(1, round(raw * cut))
            hp = int(run["hp"]) - damage
            nxt = floor + 1
            if hp <= 0:
                g.db.execute("UPDATE dungeon_runs SET hp = 0 WHERE id = ?", (run["id"],))
                run = g.db.execute("SELECT * FROM dungeon_runs WHERE id = ?",
                                   (run["id"],)).fetchone()
                death = _bank(sid, run, alive=False)
                return jsonify(ok=True, data={"floor": floor, "reason": "timeout",
                                              "damage": damage, "hp": 0,
                                              "death": death})
            g.db.execute(
                "UPDATE dungeon_runs SET floor = ?, deepest = ?, floorStart = ?,"
                " hp = ?, wrongCount = 0, questionAt = NULL, correctSide = NULL,"
                " bowDrawn = 0, arrowType = NULL WHERE id = ?",
                (nxt, max(int(run["deepest"]), nxt), now, hp, run["id"]))
        return jsonify(ok=True, data={"floor": nxt, "reason": "timeout",
                                      "damage": damage, "hp": hp})

    def _regen_tick(run, gear, now):
        """Healing Orb. Pays out whole ticks since it last paid.

        Server-side and time-based rather than a browser interval, so it
        keeps healing while the tab is backgrounded and can't be sped up by
        anything the client does. Returns the new HP.
        """
        hp = int(run["hp"])
        per = int(gear.get("regenHp") or 0)
        every = int(gear.get("regenEveryMs") or 0)
        if per <= 0 or every <= 0 or hp <= 0:
            return hp, 0
        last = int(run["regenAt"] or 0) or int(run["startedAt"] or now)
        ticks = max(0, (now - last) // every)
        if ticks <= 0:
            return hp, 0
        healed = min(per * ticks, int(run["maxHp"]) - hp)
        healed = max(0, healed)
        g.db.execute("UPDATE dungeon_runs SET hp = ?, regenAt = ? WHERE id = ?",
                     (hp + healed, last + ticks * every, run["id"]))
        return hp + healed, healed

    @app.route("/api/dungeon/run/regen", methods=["POST"])
    @require_student
    @block_when_frozen
    def dungeon_run_regen():
        """Let the orb catch up. The client pings this; the server decides
        how much time has actually passed."""
        sid = g.session["studentId"]
        with g.db:
            run = _active_run(sid)
            if not run:
                return jsonify(ok=True, data=None)
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            gear, _, _ = _gear_for(row)
            hp, healed = _regen_tick(run, gear, int(time.time() * 1000))
        return jsonify(ok=True, data={"hp": hp, "healed": healed,
                                      "maxHp": int(run["maxHp"])})

    @app.route("/api/dungeon/run/ability", methods=["POST"])
    @require_student
    @block_when_frozen
    def dungeon_run_ability():
        """Fire an equipped item's ability.

        Two exist. `ishkode` (the Goblin Mask) stops the floor clock for ten
        seconds and gives back 80% of the health the last twelve seconds
        cost — which is why every hit is written to hurtLog. `job` (the Job
        Application) sends the wrong door away, which on the server's side
        just means the answer is no longer a secret for this question.

        Cooldowns are held per ability on the run and checked here, so the
        browser holding down space achieves nothing.
        """
        sid = g.session["studentId"]
        d = request.get_json(silent=True) or {}
        want = (d.get("ability") or "").strip().lower()
        with g.db:
            run = _active_run(sid)
            if not run:
                return jsonify(ok=False, error="You're not in the dungeon."), 400
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            gear, _, _ = _gear_for(row)
            spec = next((a for a in gear.get("abilities", []) if a["id"] == want), None)
            if not spec:
                return jsonify(ok=False, error="You aren't carrying that."), 400
            now = int(time.time() * 1000)
            try:
                fired = json.loads(run["abilityAt"] or "{}")
            except (TypeError, ValueError):
                fired = {}
            last = int(fired.get(want) or 0)
            left = spec["cooldownMs"] - (now - last)
            if last and left > 0:
                return jsonify(ok=False, code="cooldown",
                               error=f"{round(left / 1000)}s until you can do that again.",
                               data={"readyIn": left}), 429

            out = {"ability": want, "cooldownMs": spec["cooldownMs"]}
            if want == "ishkode":
                try:
                    hurt = json.loads(run["hurtLog"] or "[]")
                except (TypeError, ValueError):
                    hurt = []
                window_ms = 12000
                recent = sum(int(h.get("d") or 0) for h in hurt
                             if now - int(h.get("t") or 0) <= window_ms)
                heal = int(round(recent * 0.80))
                hp = min(int(run["maxHp"]), int(run["hp"]) + heal)
                # Ten seconds of stopped clock is ten seconds added to the
                # floor's deadline — the floor timer is a wall-clock deadline,
                # so freezing it means moving it.
                freeze_ms = 10000
                g.db.execute(
                    "UPDATE dungeon_runs SET hp = ?, floorStart = ?, questionAt = ?,"
                    " abilityAt = ? WHERE id = ?",
                    (hp, int(run["floorStart"] or now) + freeze_ms,
                     (int(run["questionAt"]) + freeze_ms) if run["questionAt"] else None,
                     json.dumps({**fired, want: now}), run["id"]))
                out.update({"healed": heal, "hp": hp, "freezeMs": freeze_ms,
                            "recentDamage": recent})
            elif want == "job":
                if not run["correctSide"]:
                    return jsonify(ok=False, error="No door to scare."), 400
                g.db.execute("UPDATE dungeon_runs SET abilityAt = ? WHERE id = ?",
                             (json.dumps({**fired, want: now}), run["id"]))
                out["flee"] = "L" if run["correctSide"] == "R" else "R"
            else:
                return jsonify(ok=False, error="Nothing happens."), 400
        return jsonify(ok=True, data=out)

    @app.route("/api/dungeon/run/draw", methods=["POST"])
    @require_student
    @block_when_frozen
    def dungeon_run_draw():
        """Right-click draws the bow. The bonus lands on the NEXT room and
        spends an arrow when it does."""
        sid = g.session["studentId"]
        with g.db:
            run = _active_run(sid)
            if not run:
                return jsonify(ok=False, error="You're not in the dungeon."), 400
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            _, equipped, _ = _gear_for(row)
            weapon = dungeon.ITEMS.get(equipped.get("weapon") or "")
            if not weapon or "bow" not in weapon["id"]:
                return jsonify(ok=False, error="You don't have a bow equipped."), 400
            aid = _arrows(_owned(row))
            if not aid:
                # No penalty for a dry quiver — the bow just does nothing.
                return jsonify(ok=True, data={"drawn": False,
                                              "reason": "No arrows in the quiver."})
            g.db.execute("UPDATE dungeon_runs SET bowDrawn = 1, arrowType = ? WHERE id = ?",
                         (aid, run["id"]))
        return jsonify(ok=True, data={"drawn": True, "arrow": aid,
                                      "name": dungeon.ITEMS[aid]["name"]})

    @app.route("/api/dungeon/run/exit", methods=["POST"])
    @require_student
    @block_when_frozen
    def dungeon_run_exit():
        sid = g.session["studentId"]
        with g.db:
            run = _active_run(sid)
            if not run:
                return jsonify(ok=False, error="You're not in the dungeon."), 400
            result = _bank(sid, run, alive=True)
            # Something is waiting at the door. The run banks first — they
            # walked out and earned what they earned — and the encounter is
            # its own thing on top, so losing it never costs them the run.
            boss = _maybe_start_boss(sid, run)
            if boss:
                result["boss"] = boss
        return jsonify(ok=True, data=result)

    # ══ THE SPIDER ═════════════════════════════════════════════════════
    def _boss_eligible(row):
        """Only for campers who've been handed Dungeon Explorer, and only
        while there's still a world to end."""
        extras = _extras(row)
        if extras.get("worldSpiderKilled"):
            return False
        roles = set(json.loads(row["roles"] or "[]"))
        return bool(_role_ids_named("Dungeon Explorer") & roles)

    def _active_boss(sid):
        return g.db.execute(
            "SELECT * FROM boss_fights WHERE studentId = ? AND endedAt IS NULL"
            " ORDER BY startedAt DESC LIMIT 1", (sid,)).fetchone()

    def _boss_state(fight, luck=0):
        eff = dungeon.luck_effectiveness(luck)
        return {
            "id": fight["id"], "phase": int(fight["phase"]),
            "round": int(fight["round"]),
            "spiderHp": int(fight["spiderHp"]), "spiderMaxHp": int(fight["spiderMaxHp"]),
            "hp": int(fight["hp"]), "maxHp": int(fight["maxHp"]),
            "hits": int(fight["hits"]), "dodged": int(fight["dodged"]),
            "webbed": int(fight["webbed"]),
            "outcome": fight["outcome"],
            "hitsToKill": dungeon.BOSS_HITS_TO_KILL,
            "swordDamage": dungeon.LIME_SWORD_DAMAGE,
            "p1Rounds": dungeon.BOSS_P1_ROUNDS,
            "dodgeWindowMs": dungeon.BOSS_DODGE_WINDOW_MS,
            "gapMinMs": dungeon.BOSS_GAP_MIN_MS, "gapMaxMs": dungeon.BOSS_GAP_MAX_MS,
            "p3DodgeWindowMs": dungeon.BOSS_P3_DODGE_WINDOW_MS,
            "p3GapMs": dungeon.BOSS_P3_GAP_MS,
            "p3AttackWindowMs": dungeon.BOSS_P3_ATTACK_WINDOW_MS,
            "missChance": round(dungeon.boss_miss_chance(eff), 4),
            "critChance": round(dungeon.boss_crit_chance(eff), 4),
            "webDamage": dungeon.boss_web_damage(eff),
            "luck": luck,
        }

    def _maybe_start_boss(sid, run):
        row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
        if not row or not _boss_eligible(row):
            return None
        if _active_boss(sid):
            return None
        if secrets.randbelow(10000) >= int(dungeon.BOSS_TRIGGER_CHANCE * 10000):
            return None
        gear, _, stats = _gear_for(row)
        fid = "boss-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(3)
        max_hp = max(int(gear["maxHp"] or 0), dungeon.BASE_MAX_HP)
        g.db.execute(
            "INSERT INTO boss_fights"
            " (id, studentId, startedAt, phase, round, spiderHp, spiderMaxHp,"
            "  hp, maxHp, runId)"
            " VALUES (?, ?, ?, 1, 0, ?, ?, ?, ?, ?)",
            (fid, sid, int(time.time() * 1000), dungeon.SPIDER_MAX_HP,
             dungeon.SPIDER_MAX_HP, max_hp, max_hp, run["id"]))
        fight = g.db.execute("SELECT * FROM boss_fights WHERE id = ?", (fid,)).fetchone()
        return _boss_state(fight, int(stats.get("luck", 0)))

    @app.route("/api/dungeon/boss", methods=["GET"])
    @require_student
    def boss_get():
        sid = g.session["studentId"]
        fight = _active_boss(sid)
        if not fight:
            return jsonify(ok=True, data=None)
        row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
        _, _, stats = _gear_for(row)
        state = _boss_state(fight, int(stats.get("luck", 0)))
        state["hasLimeSword"] = _owned(row).get("lime_sword", 0) > 0
        return jsonify(ok=True, data=state)

    @app.route("/api/dungeon/boss/question", methods=["POST"])
    @require_student
    @block_when_frozen
    def boss_question():
        """Serve one question for the fight. `kind` is 'dodge' or 'attack';
        phase 3 has one of each open at the same time, which is the whole
        difficulty of phase 3."""
        sid = g.session["studentId"]
        d = request.get_json(silent=True) or {}
        kind = "attack" if (d.get("kind") == "attack") else "dodge"
        with g.db:
            fight = _active_boss(sid)
            if not fight:
                return jsonify(ok=False, error="Nothing is attacking you."), 400
            phase = int(fight["phase"])
            if kind == "attack" and phase < 3:
                return jsonify(ok=False, error="You can't hurt it yet."), 400
            # Deep-dungeon difficulty: the spider doesn't ask easy questions.
            drawn = _draw_question(sid, 150)
            window = (dungeon.BOSS_P3_ATTACK_WINDOW_MS if kind == "attack"
                      else (dungeon.BOSS_P3_DODGE_WINDOW_MS if phase >= 3
                            else dungeon.BOSS_DODGE_WINDOW_MS))
            # Relics buy thinking time. This is the main reason to own one.
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            gear, _, _ = _gear_for(row)
            window += int(max(0.0, gear.get("bossWindow", 0.0)) * 1000)
            # Cat Ears stretch every window in the game, and the spider's
            # count as windows.
            window = int(round(window * float(gear.get("windowMultiplier", 1.0) or 1.0)))
            now = int(time.time() * 1000)
            left_correct = secrets.randbelow(2) == 0
            slot = {
                "side": "L" if left_correct else "R",
                "deadline": now + window + dungeon.BOSS_GRACE_MS,
                "qid": drawn["id"], "question": drawn["question"],
                "answer": drawn["answer"], "unit": drawn["unit"],
                "difficulty": drawn["difficulty"],
            }
            # Write the ONE key rather than the whole object. In phase 3 the
            # dodge and the swing are fetched at the same moment, and a
            # read-modify-write of the whole JSON lets whichever request
            # commits last silently drop the other one's question.
            g.db.execute(
                "UPDATE boss_fights SET pending ="
                " json_set(COALESCE(NULLIF(pending, ''), '{}'), '$.' || ?, json(?))"
                " WHERE id = ?",
                (kind, json.dumps(slot), fight["id"]))
        return jsonify(ok=True, data={
            "kind": kind, "question": drawn["question"], "unit": drawn["unit"],
            "left": drawn["answer"] if left_correct else drawn["wrong"],
            "right": drawn["wrong"] if left_correct else drawn["answer"],
            "windowMs": window, "graceMs": dungeon.BOSS_GRACE_MS,
            "servedAt": now,
        })

    @app.route("/api/dungeon/boss/answer", methods=["POST"])
    @require_student
    @block_when_frozen
    def boss_answer():
        """Resolve a dodge or a swing.

        A dodge is right-and-in-time or it isn't: miss it and a web lands on
        the screen. A swing that lands still has to get past the miss roll,
        and may crit. Both rolls are made here, server-side, so luck is the
        camper's stat rather than the browser's opinion.
        """
        sid = g.session["studentId"]
        d = request.get_json(silent=True) or {}
        kind = "attack" if (d.get("kind") == "attack") else "dodge"
        side = (d.get("side") or "").upper()
        with g.db:
            fight = _active_boss(sid)
            if not fight:
                return jsonify(ok=False, error="Nothing is attacking you."), 400
            pending = json.loads(fight["pending"] or "{}")
            slot = pending.get(kind)
            if not slot:
                return jsonify(ok=False, error="Nothing to answer."), 400
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            gear, _, stats = _gear_for(row)
            luck = int(stats.get("luck", 0))
            eff = dungeon.luck_effectiveness(luck)
            now = int(time.time() * 1000)
            in_time = now <= int(slot["deadline"])
            right = side in ("L", "R") and side == slot["side"]
            solved = bool(right and in_time)

            # The fight is practice too — every question asked in here lands
            # in the same log as every floor.
            _log_boss_answer(sid, fight, slot, side, solved, kind)

            out = {"kind": kind, "solved": solved, "inTime": in_time,
                   "correct": right, "answer": str(slot.get("answer", ""))}
            hp = int(fight["hp"])
            spider_hp = int(fight["spiderHp"])
            hits = int(fight["hits"])
            dodged = int(fight["dodged"])
            webbed = int(fight["webbed"])

            if kind == "dodge":
                if solved:
                    dodged += 1
                    out["web"] = "missed"
                else:
                    # Armour works on webs too. A relic set turns a 60-point
                    # web into something you can afford to eat.
                    evaded = secrets.randbelow(10000) < int(
                        min(0.95, gear.get("evadeChance", 0.0)) * 10000)
                    if evaded:
                        dodged += 1
                        out["web"] = "evaded"
                    else:
                        raw = dungeon.boss_web_damage(eff)
                        cut = ((1 - dungeon.damage_reduction(gear["defense"]))
                               * (1 - gear["flatNegate"]))
                        dmg = max(1, round(raw * cut))
                        hp = max(0, hp - dmg)
                        webbed += 1
                        out["web"] = "hit"
                        out["damage"] = dmg
            else:
                if solved:
                    missed = secrets.randbelow(10000) < int(
                        dungeon.boss_miss_chance(eff) * 10000)
                    if missed:
                        out["swing"] = "missed"
                    else:
                        crit = secrets.randbelow(10000) < int(
                            dungeon.boss_crit_chance(eff) * 10000)
                        dmg = round(dungeon.LIME_SWORD_DAMAGE
                                    * (1 + max(0.0, gear.get("spiderBonus", 0.0)))
                                    * float(gear.get("damageMultiplier", 1.0) or 1.0)
                                    * (dungeon.BOSS_CRIT_MULTIPLIER if crit else 1))
                        spider_hp = max(0, spider_hp - dmg)
                        hits += 1
                        out["swing"] = "crit" if crit else "hit"
                        out["damage"] = dmg
                else:
                    out["swing"] = "fumbled"

            rnd = int(fight["round"]) + (1 if kind == "dodge" else 0)
            outcome = None
            if spider_hp <= 0:
                outcome = "won"
            elif hp <= 0:
                outcome = "died"
            # Same reasoning as the write above: clear only this slot, so
            # resolving a dodge can't wipe a swing that's still on screen.
            g.db.execute(
                "UPDATE boss_fights SET"
                " pending = json_remove(COALESCE(NULLIF(pending, ''), '{}'), '$.' || ?),"
                " hp = ?, spiderHp = ?, hits = ?,"
                " dodged = ?, webbed = ?, round = ?, outcome = COALESCE(?, outcome),"
                " endedAt = CASE WHEN ? IS NULL THEN endedAt ELSE ? END WHERE id = ?",
                (kind, hp, spider_hp, hits, dodged, webbed, rnd,
                 outcome, outcome, now, fight["id"]))
            fight = g.db.execute("SELECT * FROM boss_fights WHERE id = ?",
                                 (fight["id"],)).fetchone()
            state = _boss_state(fight, luck)
        out["state"] = state
        return jsonify(ok=True, data=out)

    def _log_boss_answer(sid, fight, slot, side, solved, kind):
        try:
            g.db.execute(
                "INSERT INTO infinity_answers"
                " (id, studentId, questionId, question, answer, chosen, correct,"
                "  unit, difficulty, floor, elapsedMs, runId, answeredAt)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)",
                ("ians-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(4),
                 sid, slot.get("qid", ""), slot.get("question", ""),
                 str(slot.get("answer", "")), side, 1 if solved else 0,
                 f"Spider · {kind}", int(slot.get("difficulty") or 0),
                 fight["id"], int(time.time() * 1000)))
        except Exception:
            pass

    @app.route("/api/dungeon/boss/phase", methods=["POST"])
    @require_student
    @block_when_frozen
    def boss_phase():
        """Move the fight on. Phase 2 needs the twenty dodges of phase 1
        behind it; phase 3 needs Marcus to have said his piece AND the Lime
        Sword actually in hand, because that's the whole point of what he
        came to say."""
        sid = g.session["studentId"]
        d = request.get_json(silent=True) or {}
        try:
            want = int(d.get("phase") or 0)
        except (TypeError, ValueError):
            want = 0
        with g.db:
            fight = _active_boss(sid)
            if not fight:
                return jsonify(ok=False, error="Nothing is attacking you."), 400
            cur = int(fight["phase"])
            if want != cur + 1 or want > 3:
                return jsonify(ok=False, error="Not yet."), 400
            if want == 2 and int(fight["round"]) < dungeon.BOSS_P1_ROUNDS:
                return jsonify(ok=False, error="It isn't done with you yet."), 400
            if want == 3:
                row = g.db.execute("SELECT * FROM students WHERE id = ?",
                                   (sid,)).fetchone()
                _, equipped, _ = _gear_for(row)
                if equipped.get("weapon") != "lime_sword":
                    return jsonify(
                        ok=False,
                        error="Equip the Lime Sword — nothing else touches it."), 400
            g.db.execute("UPDATE boss_fights SET phase = ?, pending = '{}' WHERE id = ?",
                         (want, fight["id"]))
            fight = g.db.execute("SELECT * FROM boss_fights WHERE id = ?",
                                 (fight["id"],)).fetchone()
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            _, _, stats = _gear_for(row)
        return jsonify(ok=True, data=_boss_state(fight, int(stats.get("luck", 0))))

    @app.route("/api/dungeon/boss/flee", methods=["POST"])
    @require_student
    def boss_flee():
        """Closing the tab shouldn't leave a fight open forever."""
        sid = g.session["studentId"]
        with g.db:
            fight = _active_boss(sid)
            if fight:
                g.db.execute(
                    "UPDATE boss_fights SET endedAt = ?, outcome = 'abandoned'"
                    " WHERE id = ?", (int(time.time() * 1000), fight["id"]))
        return jsonify(ok=True)

    def _liquidate(sid, row, stats, reason):
        """Sell the bag, cash every shard, and return what happened.

        Shared by the final tally and the World Spider ending, because they
        are the same act with different framing. Quest items are not sold —
        they cost nothing, so 'selling' them would pay nothing and lose the
        camper the only things in there with a story attached. Luck is not
        touched: it isn't stock, it's who they are by now.
        """
        owned = _owned(row)
        item_shards, sold = 0, []
        for iid, qty in owned.items():
            spec = dungeon.ITEMS.get(iid)
            if not spec or spec.get("quest"):
                continue
            worth = int(spec["cost"]) * int(qty)
            item_shards += worth
            sold.append({"id": iid, "name": spec["name"], "qty": int(qty),
                         "shards": worth})
        total_shards = int(stats.get("shards", 0)) + item_shards
        gained, spent = dungeon.shards_to_points(total_shards)
        before = {
            "points": int(stats.get("privatePoints", 0)),
            "shards": int(stats.get("shards", 0)),
            "itemShards": item_shards,
            "inventory": json.loads(row["inventory"] or "[]"),
            "equipped": json.loads(row["equipped"] or "{}"),
        }
        stats["privatePoints"] = int(stats.get("privatePoints", 0)) + gained
        stats["totalPointsEarned"] = int(stats.get("totalPointsEarned", 0)) + gained
        stats["shards"] = 0
        _save_stats(sid, stats)
        # Quest items survive: they're the only things worth keeping.
        keep = {iid: q for iid, q in owned.items()
                if (dungeon.ITEMS.get(iid) or {}).get("quest")}
        _save_inventory(sid, keep, {})
        _log_tx(type="earn", scope="student", subjectId=sid,
                subjectName=_full_name(row), amount=gained,
                description=(f"{reason} — everything sold and cashed out: "
                             f"+{gained:,} points from {total_shards:,} shards."))
        return {"pointsGained": gained, "shardsCashed": spent,
                "totalShards": total_shards, "sold": sold, "before": before}

    @app.route("/api/students/me/final-tally", methods=["POST"])
    @require_student
    @block_when_frozen
    def final_tally():
        """Cash out of the game, permanently.

        Everything in the bag is sold, every shard becomes points, and the
        dungeon, the shop, the clicker and the luck counter all close. What
        stays open is sending points to a classmate — at a worse rate, with
        no way to buy the loss away.

        Deliberately one-way and deliberately awkward to trigger: the client
        has to send confirm:"FINAL TALLY". Nothing about this is recoverable
        by the camper, so it should not be reachable by a mis-click.
        """
        sid = g.session["studentId"]
        d = request.get_json(silent=True) or {}
        if (d.get("confirm") or "").strip().upper() != "FINAL TALLY":
            return jsonify(ok=False, error="Type FINAL TALLY to confirm."), 400
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            extras = _extras(row)
            if extras.get("finalTally"):
                return jsonify(ok=False, error="You've already cashed out."), 400
            if _active_run(sid):
                return jsonify(ok=False, error="Walk out of the dungeon first."), 400
            stats = {**default_stats(), **json.loads(row["stats"] or "{}")}
            result = _liquidate(sid, row, stats, "🧾 Final tally")
            extras["finalTally"] = True
            extras["finalTallyAt"] = int(time.time() * 1000)
            # Kept so staff can undo it. Nothing else can.
            extras["finalTallyBefore"] = result.pop("before")
            g.db.execute("UPDATE students SET extras = ? WHERE id = ?",
                         (json.dumps(extras), sid))
        result["transferKeepRatio"] = TALLIED_TRANSFER_KEEP_RATIO
        return jsonify(ok=True, data=result)

    @app.route("/api/dungeon/boss/world-spider", methods=["POST"])
    @require_student
    @block_when_frozen
    def boss_world_spider():
        """The last question anyone in this camp gets asked.

        There is only one answer. Declining returns a refusal and the prompt
        comes back — the choice is theatre, and deliberately so. Saying yes
        liquidates the account: every item sold, every shard cashed, the
        dungeon and everything attached to it closed. Points, roles and the
        transaction history survive, and so does luck.
        """
        sid = g.session["studentId"]
        d = request.get_json(silent=True) or {}
        choice = (d.get("choice") or "").strip().lower()
        if choice != "yes":
            # Not an error the caller can fix by trying something else.
            return jsonify(ok=False, code="rejected",
                           error="bug detected, user input incorrect"), 409
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            extras = _extras(row)
            if extras.get("worldSpiderKilled"):
                return jsonify(ok=False, error="It's already over."), 400
            won = g.db.execute(
                "SELECT 1 FROM boss_fights WHERE studentId = ? AND outcome = 'won'"
                " LIMIT 1", (sid,)).fetchone()
            if not won:
                return jsonify(ok=False, error="You haven't beaten it."), 400

            stats = {**default_stats(), **json.loads(row["stats"] or "{}")}
            # Same act as the final tally, different framing — and luck is
            # left alone by both.
            result = _liquidate(sid, row, stats, "🕷 The World Spider is dead")
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()

            extras["worldSpiderKilled"] = True
            extras["worldSpiderAt"] = int(time.time() * 1000)
            # The tally state comes with it: the shop is shut and transfers
            # are the only thing still moving.
            extras["finalTally"] = True
            extras["finalTallyAt"] = int(time.time() * 1000)
            # Kept so staff can put someone back together if this turns out
            # to have been a mistake. It is otherwise unrecoverable.
            extras["worldSpiderBefore"] = result.pop("before")
            g.db.execute("UPDATE students SET extras = ? WHERE id = ?",
                         (json.dumps(extras), sid))

            _grant_items(sid, row, {"peace_note": 1})
            note = dungeon.ITEMS["peace_note"]
        result["note"] = {"id": "peace_note", "name": note["name"],
                          "text": note["note"], "back": note["noteBack"]}
        return jsonify(ok=True, data=result)

    # ── Receipts and the tax cycle ─────────────────────────────────
    @app.route("/api/students/me/receipts", methods=["GET"])
    @require_student
    def dungeon_receipts():
        sid = g.session["studentId"]
        kind = (request.args.get("kind") or "").strip()
        sql = "SELECT * FROM receipts WHERE studentId = ?"
        args = [sid]
        if kind and kind != "all":
            sql += " AND kind = ?"; args.append(kind)
        for field, op in (("from", ">="), ("to", "<=")):
            raw = request.args.get(field)
            if raw:
                try:
                    sql += f" AND at {op} ?"; args.append(int(raw))
                except (TypeError, ValueError):
                    pass
        sql += " ORDER BY at DESC LIMIT 500"
        rows = [dict(r) for r in g.db.execute(sql, args).fetchall()]
        return jsonify(ok=True, data={
            "receipts": rows,
            "totals": {
                "gross": sum(r["gross"] for r in rows),
                "tax":   sum(r["tax"] for r in rows),
                "net":   sum(r["net"] for r in rows),
                "count": len(rows),
            },
            "unclaimedTax": int(g.db.execute(
                "SELECT COALESCE(SUM(tax),0) AS t FROM receipts"
                " WHERE studentId = ? AND reclaimed = 0", (sid,)).fetchone()["t"] or 0),
        })

    @app.route("/api/students/me/tax/file", methods=["POST"])
    @require_student
    @block_when_frozen
    def dungeon_file_tax():
        """File a return. The student adds up the tax on their own receipts
        and enters the total; get it exactly right and all of it comes back.
        Wrong just says whether they're high or low — no penalty, no
        deadline, retry as often as they like. The arithmetic IS the lesson."""
        sid = g.session["studentId"]
        d = request.get_json(silent=True) or {}
        try:
            claimed = int(d.get("claimed"))
        except (TypeError, ValueError):
            return jsonify(ok=False, error="Enter the total tax as a whole number."), 400
        if claimed < 0:
            return jsonify(ok=False, error="A total can't be negative."), 400
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            open_rows = g.db.execute(
                "SELECT id, tax FROM receipts WHERE studentId = ? AND reclaimed = 0",
                (sid,)).fetchall()
            owed = sum(int(r["tax"]) for r in open_rows)
            if not open_rows:
                return jsonify(ok=False, error="Nothing to claim — no unfiled receipts."), 400
            correct = (claimed == owed)
            fid = "file-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(3)
            g.db.execute(
                "INSERT INTO tax_filings (id, studentId, at, claimed, owed, correct, refunded)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (fid, sid, int(time.time() * 1000), claimed, owed,
                 1 if correct else 0, owed if correct else 0),
            )
            if not correct:
                return jsonify(ok=True, data={
                    "correct": False, "claimed": claimed,
                    "receiptCount": len(open_rows),
                    "hint": ("That's higher than what was actually withheld."
                             if claimed > owed else
                             "That's lower than what was actually withheld."),
                    "message": ("Not quite — add up the Tax column on every receipt "
                                "you haven't filed yet and try again."),
                })
            stats = _wallet(row)
            stats["shards"] = int(stats.get("shards", 0)) + owed
            _save_stats(sid, stats)
            g.db.execute(
                "UPDATE receipts SET reclaimed = 1 WHERE studentId = ? AND reclaimed = 0",
                (sid,))
            # The refund itself is logged for the ledger but files as already
            # reclaimed — it carries no tax, and leaving it open would let a
            # student "file" an empty return afterwards.
            _receipt(sid, "refund",
                     f"Tax return — {len(open_rows)} receipt(s) filed", owed, 0, owed,
                     reclaimed=1)
        return jsonify(ok=True, data={
            "correct": True, "refunded": owed, "receiptCount": len(open_rows),
            "shards": stats["shards"],
        })

    @app.route("/api/students/me/tax/filings", methods=["GET"])
    @require_student
    def dungeon_filings():
        sid = g.session["studentId"]
        rows = g.db.execute(
            "SELECT * FROM tax_filings WHERE studentId = ? ORDER BY at DESC LIMIT 50",
            (sid,)).fetchall()
        return jsonify(ok=True, data=[dict(r) for r in rows])

    # ══ Dungeon administration ═════════════════════════════════════
    # One screen for staff: who is in the dungeon economy, what they're
    # carrying, and what their shard balance is. Everything here is
    # admin-only and everything that moves shards writes to the ledger.

    def _item_name(item_id):
        it = dungeon.ITEMS.get(item_id)
        return it["name"] if it else item_id

    @app.route("/api/admin/dungeon/overview", methods=["GET"])
    @require_admin
    def admin_dungeon_overview():
        """Every camper's shards, bag and dungeon standing, in one read."""
        rows = g.db.execute("SELECT * FROM students ORDER BY firstName, lastName").fetchall()
        deepest = {r["studentId"]: r["d"] for r in g.db.execute(
            "SELECT studentId, MAX(deepest) AS d FROM dungeon_runs GROUP BY studentId"
        ).fetchall()}
        active = {r["studentId"] for r in g.db.execute(
            "SELECT DISTINCT studentId FROM dungeon_runs WHERE endedAt IS NULL"
        ).fetchall()}
        owed = {r["studentId"]: (r["t"] or 0) for r in g.db.execute(
            "SELECT studentId, SUM(tax) AS t FROM receipts WHERE reclaimed = 0"
            " GROUP BY studentId"
        ).fetchall()}
        out = []
        for row in rows:
            stats = _wallet(row)
            extras = _extras(row)
            equipped = {}
            try:
                equipped = json.loads(row["equipped"] or "{}") or {}
            except Exception:  # noqa: BLE001
                equipped = {}
            inv = []
            try:
                inv = json.loads(row["inventory"] or "[]") or []
            except Exception:  # noqa: BLE001
                inv = []
            items = [{"id": e.get("id"), "name": _item_name(e.get("id")),
                      "qty": int(e.get("qty") or 0),
                      "quest": bool((dungeon.ITEMS.get(e.get("id")) or {}).get("quest"))}
                     for e in inv if isinstance(e, dict) and e.get("id")]
            worn = [{"slot": k, "id": v, "name": _item_name(v)}
                    for k, v in equipped.items() if v]
            beaten = (bool(extras.get("doorsRewarded"))
                      or int(extras.get("doorsCompleted") or 0) >= 1)
            out.append({
                "id": row["id"],
                "name": _full_name(row),
                "frozen": bool(row["frozen"]),
                "shards": int(stats.get("shards", 0)),
                "points": int(stats.get("privatePoints", 0)),
                "luck": int(stats.get("luck", 0)),
                "cashedOut": bool(stats.get("cashedOut")),
                "mazeBeaten": beaten,
                "banned": bool(extras.get("dungeonBanned")),
                "hasAccess": beaten and not extras.get("dungeonBanned"),
                "deepest": int(deepest.get(row["id"]) or 0),
                "inRun": row["id"] in active,
                "unclaimedTax": int(owed.get(row["id"]) or 0),
                "items": items,
                "equipped": worn,
                "itemCount": sum(i["qty"] for i in items) + len(worn),
            })
        return jsonify(ok=True, data={
            "students": out,
            "conversionFee": dungeon.CONVERSION_FEE_POINTS,
        })

    @app.route("/api/admin/dungeon/shards", methods=["POST"])
    @require_admin
    def admin_set_shards():
        """Set or adjust one camper's shards.

        `mode` is "set" or "add" — add takes a negative to remove. Balances
        floor at zero rather than going negative, and every change lands on
        the transaction log with the staff reason attached, because a shard
        balance that moves with no paper trail is the thing staff will be
        asked about later."""
        d = request.get_json(silent=True) or {}
        sid = (d.get("studentId") or "").strip()
        mode = (d.get("mode") or "set").strip()
        reason = (d.get("reason") or "").strip() or "Staff adjustment"
        try:
            amount = int(d.get("amount"))
        except (TypeError, ValueError):
            return jsonify(ok=False, error="Amount must be a whole number."), 400
        if mode not in ("set", "add"):
            return jsonify(ok=False, error="mode must be 'set' or 'add'."), 400
        if abs(amount) > 100_000_000:
            return jsonify(ok=False, error="That number is out of range."), 400
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            stats = _wallet(row)
            before = int(stats.get("shards", 0))
            after = max(0, amount if mode == "set" else before + amount)
            stats["shards"] = after
            _save_stats(sid, stats)
            delta = after - before
            _log_tx(type="admin_shards", scope="student", subjectId=sid,
                    subjectName=_full_name(row), amount=0,
                    description=(f"💎 Shards {before:,} → {after:,} "
                                 f"({delta:+,}) · {reason}"))
        return jsonify(ok=True, data={"studentId": sid, "before": before,
                                      "after": after, "delta": delta})

    @app.route("/api/admin/dungeon/access", methods=["POST"])
    @require_admin
    def admin_dungeon_access():
        """Open or close the dungeon for one camper, or for everyone.

        Closing sets extras.dungeonBanned and leaves the maze history
        alone, so nobody gets to re-claim the maze reward on the way back
        in. Any run currently in progress is banked as a walk-out rather
        than deleted — the camper keeps what they'd earned to that point,
        which is what they'd have got by leaving on their own."""
        d = request.get_json(silent=True) or {}
        allow = bool(d.get("allow"))
        target = (d.get("studentId") or "").strip()
        everyone = bool(d.get("everyone"))
        if not target and not everyone:
            return jsonify(ok=False, error="Name a student, or pass everyone."), 400
        changed, banked = [], 0
        with g.db:
            if everyone:
                rows = g.db.execute("SELECT * FROM students").fetchall()
            else:
                rows = g.db.execute("SELECT * FROM students WHERE id = ?",
                                    (target,)).fetchall()
                if not rows:
                    return jsonify(ok=False, error="Student not found."), 404
            for row in rows:
                extras = _extras(row)
                was = bool(extras.get("dungeonBanned"))
                if was == (not allow):
                    continue                      # already in the wanted state
                if allow:
                    extras.pop("dungeonBanned", None)
                else:
                    extras["dungeonBanned"] = True
                g.db.execute("UPDATE students SET extras = ? WHERE id = ?",
                             (json.dumps(extras), row["id"]))
                changed.append(row["id"])
                if not allow:
                    run = g.db.execute(
                        "SELECT * FROM dungeon_runs WHERE studentId = ? AND endedAt IS NULL"
                        " ORDER BY startedAt DESC LIMIT 1", (row["id"],)).fetchone()
                    if run:
                        _bank(row["id"], run, alive=True)
                        banked += 1
                _log_tx(type="role_assigned", scope="student", subjectId=row["id"],
                        subjectName=_full_name(row), amount=0,
                        description=("🗝 Dungeon access restored by staff" if allow
                                     else "🚧 Dungeon access closed by staff"))
        return jsonify(ok=True, data={"changed": len(changed), "banked": banked,
                                      "allow": allow})

    @app.route("/api/admin/dungeon/repair-inventories", methods=["POST"])
    @require_admin
    def admin_repair_inventories():
        """Undo the duplication.

        Every buy, starter pick and answered door used to write equipped
        gear back into the loose inventory, so a run cloned a camper's
        loadout a floor at a time. The fix is in _save_inventory; this
        cleans up what the bug already wrote: anything currently worn is
        dropped from the loose bag (it can't be in both), and anything
        non-stackable is clamped to one. Stackable items — arrows — are
        left alone, since a real stack of 80 is legitimate."""
        fixed = []
        with g.db:
            for row in g.db.execute("SELECT * FROM students").fetchall():
                try:
                    inv = json.loads(row["inventory"] or "[]") or []
                    equipped = json.loads(row["equipped"] or "{}") or {}
                except Exception:  # noqa: BLE001
                    continue
                worn = {v for v in equipped.values() if v}
                out, dropped = [], 0
                for e in inv:
                    if not isinstance(e, dict) or not e.get("id"):
                        continue
                    iid = e["id"]
                    qty = int(e.get("qty") or 0)
                    if qty <= 0:
                        continue
                    it = dungeon.ITEMS.get(iid) or {}
                    if iid in worn:
                        dropped += qty          # the worn copy is the only one
                        continue
                    if not it.get("stackable") and qty > 1:
                        dropped += qty - 1
                        qty = 1
                    out.append({"id": iid, "qty": qty})
                if dropped:
                    g.db.execute("UPDATE students SET inventory = ? WHERE id = ?",
                                 (json.dumps(sorted(out, key=lambda x: x["id"])), row["id"]))
                    fixed.append({"id": row["id"], "name": _full_name(row),
                                  "removed": dropped})
        return jsonify(ok=True, data={"students": fixed,
                                      "totalRemoved": sum(f["removed"] for f in fixed)})

    # ══ Chests ═════════════════════════════════════════════════════
    # Every one-time reward in camp is a "chest": something a camper can
    # open exactly once, with a flag somewhere saying they already did.
    # Staff need to be able to hand one back — a camper who lost a claim
    # to a bug, a demo that needs re-running, a reward given by mistake.
    #
    # Each entry says where the flag lives and how to clear it. Adding a
    # new one-time reward means adding a row here, and nothing else.
    CHESTS = {
        "maze": {
            "name": "Door maze chest",
            "note": "The coin pile at door 310. Re-opening lets the camper "
                    "claim the maze reward points again.",
            "extras": ["doorsRewarded", "doors_claimed"],
        },
        "dungeon_starter": {
            "name": "Dungeon starter pick",
            "note": "The one free beginner item. Re-opening lets them pick "
                    "again — it does not take the first item back.",
            "extras": ["dungeonStarterClaimed"],
        },
        "lime_sword": {
            "name": "Lime Sword",
            "note": "The reforged blade and the note that falls out of it. "
                    "Re-opening removes the role so the trial can grant it again.",
            "roles": [LIME_SWORD_ROLE_ID],
            "roleNames": ["Lime Sword"],
        },
        "osu_champion": {
            "name": "osu Champion bounty",
            "note": "The full-combo role and its one-time point bounty. "
                    "Re-opening lets a perfect run pay out again.",
            "roles": [OSU_ROLE_ID],
            "roleNames": ["osu Champion"],
        },
        "money_tree": {
            "name": "Money Tree",
            "note": "The criss-cross chamber. Re-opening lets this camper "
                    "claim it again — it stays one-per-camp otherwise.",
            "roles": [MONEY_TREE_ROLE_ID],
            "roleNames": ["Money Tree"],
        },
        "paper_crane": {
            "name": "Paper Crane",
            "note": "Re-opening removes the role so it can be claimed again.",
            "roles": [CRANE_ROLE_ID],
            "roleNames": ["Paper Crane"],
        },
        "spider": {
            "name": "Spider jumpscare",
            "note": "The one-time spider popup on the clicker.",
            "stats": ["spiderShown"],
        },
    }

    def _chest_open(row, spec):
        """Has this camper opened this chest?"""
        extras = _extras(row)
        stats = {**default_stats(), **json.loads(row["stats"] or "{}")}
        roles = set(json.loads(row["roles"] or "[]"))
        for k in spec.get("extras", []):
            if extras.get(k):
                return True
        for k in spec.get("stats", []):
            if stats.get(k):
                return True
        wanted = set(spec.get("roles", []))
        for nm in spec.get("roleNames", []):
            wanted |= _role_ids_named(nm)
        return bool(wanted & roles)

    @app.route("/api/admin/chests", methods=["GET"])
    @require_admin
    def admin_chests():
        """Who has opened what."""
        rows = g.db.execute(
            "SELECT * FROM students ORDER BY firstName, lastName").fetchall()
        out = []
        for row in rows:
            opened = {k: _chest_open(row, spec) for k, spec in CHESTS.items()}
            out.append({
                "id": row["id"], "name": _full_name(row),
                "opened": opened,
                "openedCount": sum(1 for v in opened.values() if v),
            })
        return jsonify(ok=True, data={
            "students": out,
            "chests": [{"key": k, "name": v["name"], "note": v["note"]}
                       for k, v in CHESTS.items()],
        })

    @app.route("/api/admin/chests/reset", methods=["POST"])
    @require_admin
    def admin_chest_reset():
        """Hand one chest back to one camper so they can open it again.

        Clears only the flag that says "already opened" — it does not undo
        the reward. A camper who re-opens the maze chest keeps the points
        they were paid the first time and can be paid again; re-opening the
        Lime Sword removes the role so the trial can grant it, but leaves
        the sword sitting in their bag. That's deliberate: staff asked for
        a way to let someone open a chest again, not a way to confiscate.
        """
        d = request.get_json(silent=True) or {}
        sid = (d.get("studentId") or "").strip()
        key = (d.get("chest") or "").strip()
        spec = CHESTS.get(key)
        if not spec:
            return jsonify(ok=False, error="No such chest."), 400
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            if not _chest_open(row, spec):
                return jsonify(ok=False, error=(
                    f"{_full_name(row)} hasn't opened the {spec['name']} yet — "
                    f"there's nothing to reset.")), 400

            extras = _extras(row)
            stats = {**default_stats(), **json.loads(row["stats"] or "{}")}
            roles = json.loads(row["roles"] or "[]")
            for k in spec.get("extras", []):
                extras.pop(k, None)
            for k in spec.get("stats", []):
                stats[k] = False
            drop = set(spec.get("roles", []))
            for nm in spec.get("roleNames", []):
                drop |= _role_ids_named(nm)
            roles = [r for r in roles if r not in drop]

            g.db.execute(
                "UPDATE students SET extras = ?, stats = ?, roles = ? WHERE id = ?",
                (json.dumps(extras), json.dumps(stats), json.dumps(roles), sid))
            _log_tx(type="role_assigned", scope="student", subjectId=sid,
                    subjectName=_full_name(row), amount=0,
                    description=f"🎁 {spec['name']} reset by staff — can be opened again")
        return jsonify(ok=True, data={"studentId": sid, "chest": key,
                                      "name": spec["name"]})

    # ── Mini-game hints ────────────────────────────────────────────
    @app.route("/api/hints", methods=["GET"])
    def list_hints():
        """Visible to admins and to students holding the Paper Crane role."""
        sess = _current_session()
        if not sess:
            return jsonify(ok=False, error="Auth required"), 401
        if sess["kind"] == "student":
            row = g.db.execute("SELECT roles FROM students WHERE id = ?", (sess["studentId"],)).fetchone()
            roles = json.loads(row["roles"] or "[]") if row else []
            if CRANE_ROLE_ID not in roles:
                return jsonify(ok=False, error="Hints are only visible to Paper Crane holders."), 403
        elif sess["kind"] != "admin":
            return jsonify(ok=False, error="Auth required"), 401
        rows = g.db.execute("SELECT * FROM hints ORDER BY createdAt DESC").fetchall()
        return jsonify(ok=True, data=[dict(r) for r in rows])

    @app.route("/api/admin/hints", methods=["POST"])
    @require_admin
    def create_hint():
        data = request.get_json(silent=True) or {}
        body = (data.get("body") or "").strip()
        if not body:
            return jsonify(ok=False, error="Hint body cannot be empty."), 400
        if len(body) > 2000:
            return jsonify(ok=False, error="Hint body too long (max 2000 chars)."), 400
        hid = "hint-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(3)
        ts  = int(time.time() * 1000)
        g.db.execute("INSERT INTO hints (id, body, createdAt) VALUES (?, ?, ?)", (hid, body, ts))
        return jsonify(ok=True, data={"id": hid, "body": body, "createdAt": ts})

    @app.route("/api/admin/hints/<hid>", methods=["DELETE"])
    @require_admin
    def delete_hint(hid):
        g.db.execute("DELETE FROM hints WHERE id = ?", (hid,))
        return jsonify(ok=True)

    # Role-event audit feed (used by admin-hints.html). Just a filtered
    # view over transactions — every role grant logs with type='role_assigned'.
    @app.route("/api/admin/role-events", methods=["GET"])
    @require_admin
    def list_role_events():
        rows = g.db.execute(
            "SELECT * FROM transactions WHERE type = 'role_assigned' ORDER BY at DESC LIMIT 200"
        ).fetchall()
        return jsonify(ok=True, data=[row_to_tx(r) for r in rows])

    @app.route("/api/students/me/mazewiz", methods=["POST"])
    @require_student
    @block_when_frozen
    def claim_mazewiz():
        sid = g.session["studentId"]
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row: return jsonify(ok=False, error="Student not found."), 404
            # Classes are gone — the camp is one group, so Maze Wizard is a
            # single camp-wide title rather than one per class. The old
            # per-class version is kept below for reference.
            roles = json.loads(row["roles"] or "[]")
            if MAZEWIZ_ROLE_ID in roles:
                return jsonify(ok=False, error="You already hold the Maze Wizard title!"), 400

            everyone = g.db.execute("SELECT * FROM students").fetchall()
            for other in everyone:
                if other["id"] == sid:
                    continue
                if MAZEWIZ_ROLE_ID in json.loads(other["roles"] or "[]"):
                    return jsonify(ok=False, error=f"Too late — {_full_name(other)} already claimed Maze Wizard."), 400

            # class_id = row["classId"]
            # if not class_id:
            #     return jsonify(ok=False, error="You need to be assigned to a class first — ask an admin."), 400
            # classmates = g.db.execute(
            #     "SELECT * FROM students WHERE classId = ?", (class_id,),
            # ).fetchall()
            # for cm in classmates:
            #     cm_roles = json.loads(cm["roles"] or "[]")
            #     if MAZEWIZ_ROLE_ID in cm_roles:
            #         return jsonify(ok=False, error=f"Too late — {_full_name(cm)} already claimed Maze Wizard for your class."), 400

            roles.append(MAZEWIZ_ROLE_ID)
            g.db.execute("UPDATE students SET roles = ? WHERE id = ?", (json.dumps(roles), sid))
            _log_tx(type="role_assigned", scope="student", subjectId=sid,
                    subjectName=_full_name(row), amount=0,
                    description="🧙 Claimed the Maze Wizard title")
        return jsonify(ok=True)

    # ── Classes ────────────────────────────────────────────────────
    # DISABLED: the camp runs as a single group, so classes are gone from
    # the product. The `classes` table and students' classId/className
    # columns are deliberately left in place so nothing is lost and this
    # can be switched back on by un-commenting.
    #
    # GET still answers with an empty list rather than 404 so any page or
    # cached script that asks for classes degrades quietly instead of
    # erroring.
    @app.route("/api/classes", methods=["GET"])
    def list_classes():
        return jsonify(ok=True, data=[])

    # @app.route("/api/classes", methods=["GET"])
    # def list_classes():
    #     rows = g.db.execute("SELECT * FROM classes").fetchall()
    #     return jsonify(ok=True, data=[row_to_class(r) for r in rows])

    @app.route("/api/classes", methods=["PUT"])
    @require_admin
    def replace_classes():
        # DISABLED with the rest of classes — refuses rather than silently
        # accepting writes that nothing reads any more.
        return jsonify(ok=False, error="Classes are disabled — the camp runs as one group."), 410

    # @app.route("/api/classes", methods=["PUT"])
    # @require_admin
    # def replace_classes():
    #     data = request.get_json(silent=True) or {}
    #     arr = data.get("classes") or []
    #     with g.db:
    #         g.db.execute("DELETE FROM classes")
    #         for c in arr:
    #             g.db.execute(
    #                 """INSERT INTO classes (id, name, classPoints, classBank, bankLastUpdate, createdAt)
    #                    VALUES (?, ?, ?, ?, ?, ?)""",
    #                 (c["id"], c["name"],
    #                  int(c.get("classPoints") or 0),
    #                  float(c.get("classBank") or 0),
    #                  int(c["bankLastUpdate"]) if c.get("bankLastUpdate") else None,
    #                  c.get("createdAt") or ""),
    #             )
    #     return jsonify(ok=True, count=len(arr))

    # ── Roles ──────────────────────────────────────────────────────
    @app.route("/api/roles", methods=["GET"])
    def list_roles():
        rows = g.db.execute("SELECT * FROM roles").fetchall()
        return jsonify(ok=True, data=[row_to_role(r) for r in rows])

    @app.route("/api/roles", methods=["PUT"])
    @require_admin
    def replace_roles():
        data = request.get_json(silent=True) or {}
        arr = data.get("roles") or []
        with g.db:
            g.db.execute("DELETE FROM roles")
            for r in arr:
                g.db.execute(
                    """INSERT INTO roles (id, name, icon, color, description, special)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (r["id"], r["name"], r.get("icon") or "",
                     r.get("color") or "", r.get("description") or "",
                     1 if r.get("special") else 0),
                )
        return jsonify(ok=True, count=len(arr))

    # ── Base stat categories ───────────────────────────────────────
    @app.route("/api/base-stats", methods=["GET"])
    def list_basestats():
        rows = g.db.execute(
            "SELECT * FROM base_stat_categories ORDER BY position ASC"
        ).fetchall()
        return jsonify(ok=True, data=[row_to_basestat(r) for r in rows])

    @app.route("/api/base-stats", methods=["PUT"])
    @require_admin
    def replace_basestats():
        data = request.get_json(silent=True) or {}
        arr = data.get("baseStats") or []
        with g.db:
            g.db.execute("DELETE FROM base_stat_categories")
            for i, bs in enumerate(arr):
                g.db.execute(
                    """INSERT INTO base_stat_categories (id, name, icon, pointsPerUnit, position)
                       VALUES (?, ?, ?, ?, ?)""",
                    (bs["id"], bs["name"], bs.get("icon") or "",
                     int(bs.get("pointsPerUnit") or 0), i),
                )
        return jsonify(ok=True, count=len(arr))

    # ── Transactions ───────────────────────────────────────────────
    @app.route("/api/students/me/transactions", methods=["GET"])
    @require_student
    def my_tx():
        """Just this camper's own ledger, newest first — what the point log
        at the bottom of the portal renders. Rows where they're only the
        *other* party (someone else's transfer, a bank deposit funded by
        their tax) are deliberately left out: those aren't movements of
        their own balance and would double-count in the log."""
        sid = g.session["studentId"]
        try:
            limit = max(1, min(int(request.args.get("limit") or 200), TX_MAX))
        except (TypeError, ValueError):
            limit = 200
        rows = g.db.execute(
            "SELECT * FROM transactions WHERE scope = 'student' AND subjectId = ?"
            " ORDER BY at DESC LIMIT ?",
            (sid, limit),
        ).fetchall()
        return jsonify(ok=True, data=[row_to_tx(r) for r in rows])

    @app.route("/api/transactions", methods=["GET"])
    def list_tx():
        rows = g.db.execute("SELECT * FROM transactions ORDER BY at ASC").fetchall()
        return jsonify(ok=True, data=[row_to_tx(r) for r in rows])

    @app.route("/api/transactions", methods=["PUT"])
    @require_admin
    def replace_tx():
        data = request.get_json(silent=True) or {}
        arr = data.get("transactions") or []
        if len(arr) > TX_MAX:
            arr = arr[-TX_MAX:]
        with g.db:
            g.db.execute("DELETE FROM transactions")
            for t in arr:
                g.db.execute(
                    """INSERT INTO transactions
                       (id, at, type, scope, subjectId, subjectName, relatedId, relatedName, amount, description)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (t["id"], int(t.get("at") or 0), t["type"], t.get("scope"),
                     t.get("subjectId"), t.get("subjectName"),
                     t.get("relatedId"), t.get("relatedName"),
                     int(t.get("amount") or 0), t.get("description") or ""),
                )
        return jsonify(ok=True, count=len(arr))

    @app.route("/api/transactions", methods=["DELETE"])
    @require_admin
    def clear_tx():
        g.db.execute("DELETE FROM transactions")
        return jsonify(ok=True)

    # ── Staff ──────────────────────────────────────────────────────
    @app.route("/api/staff", methods=["GET"])
    def list_staff():
        rows = g.db.execute("SELECT * FROM staff ORDER BY position ASC").fetchall()
        return jsonify(ok=True, data=[row_to_staff(r) for r in rows])

    @app.route("/api/staff", methods=["PUT"])
    @require_admin
    def replace_staff():
        data = request.get_json(silent=True) or {}
        arr = data.get("staff") or []
        with g.db:
            # Preserve each teacher's existing referral code across the
            # delete/re-insert (students may already hold it), and mint a fresh
            # unique one for any staff member who doesn't have a valid code yet.
            existing = {
                r["id"]: r["referralCode"]
                for r in g.db.execute("SELECT id, referralCode FROM staff").fetchall()
            }
            used = set()          # codes spoken for in this batch
            codes = []            # settled code per staff member, by index
            for s in arr:
                code = _normalize_referral_input(s.get("referralCode")) \
                    or _normalize_referral_input(existing.get(s.get("id")))
                if len(code) != REFERRAL_CODE_LEN or code in used:
                    # missing/invalid or a within-batch collision → mint fresh,
                    # avoiding both this batch and every code already in the DB.
                    code = _gen_referral_code(g.db, avoid=used) or ""
                if code:
                    used.add(code)
                codes.append(code or None)
            g.db.execute("DELETE FROM staff")
            for i, s in enumerate(arr):
                tf = s.get("transcriptFile")
                tf_json = json.dumps(tf) if isinstance(tf, dict) and tf.get("data") else None
                g.db.execute(
                    """INSERT INTO staff
                       (id, category, name, role, image, quote, age, school, gender, pronouns, interests, bio, transcript, transcriptFile, referralCode, position)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (s["id"], s.get("category") or "", s.get("name") or "",
                     s.get("role") or "", s.get("image") or "", s.get("quote") or "",
                     s.get("age") or "", s.get("school") or "",
                     s.get("gender") or "", s.get("pronouns") or "",
                     s.get("interests") or "", s.get("bio") or "",
                     s.get("transcript") or "", tf_json, codes[i], i),
                )
        return jsonify(ok=True, count=len(arr))

    # ── Bulk import (one-shot localStorage migration) ──────────────
    @app.route("/api/admin/import", methods=["POST"])
    @require_admin
    def bulk_import():
        data = request.get_json(silent=True) or {}
        imported = {}
        with g.db:
            if "students" in data:
                g.db.execute("DELETE FROM students")
                for raw in data["students"]:
                    _insert_student(_normalize_student(raw))
                imported["students"] = len(data["students"])
            if "classes" in data:
                g.db.execute("DELETE FROM classes")
                for c in data["classes"]:
                    g.db.execute(
                        """INSERT INTO classes (id, name, classPoints, classBank, bankLastUpdate, createdAt)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (c["id"], c["name"],
                         int(c.get("classPoints") or 0),
                         float(c.get("classBank") or 0),
                         int(c["bankLastUpdate"]) if c.get("bankLastUpdate") else None,
                         c.get("createdAt") or ""),
                    )
                imported["classes"] = len(data["classes"])
            if "roles" in data:
                g.db.execute("DELETE FROM roles")
                for r in data["roles"]:
                    g.db.execute(
                        """INSERT INTO roles (id, name, icon, color, description, special)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (r["id"], r["name"], r.get("icon") or "",
                         r.get("color") or "", r.get("description") or "",
                         1 if r.get("special") else 0),
                    )
                imported["roles"] = len(data["roles"])
            if "baseStats" in data:
                g.db.execute("DELETE FROM base_stat_categories")
                for i, bs in enumerate(data["baseStats"]):
                    g.db.execute(
                        """INSERT INTO base_stat_categories (id, name, icon, pointsPerUnit, position)
                           VALUES (?, ?, ?, ?, ?)""",
                        (bs["id"], bs["name"], bs.get("icon") or "",
                         int(bs.get("pointsPerUnit") or 0), i),
                    )
                imported["baseStats"] = len(data["baseStats"])
            if "transactions" in data:
                arr = data["transactions"][-TX_MAX:]
                g.db.execute("DELETE FROM transactions")
                for t in arr:
                    g.db.execute(
                        """INSERT INTO transactions
                           (id, at, type, scope, subjectId, subjectName, relatedId, relatedName, amount, description)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (t["id"], int(t.get("at") or 0), t["type"], t.get("scope"),
                         t.get("subjectId"), t.get("subjectName"),
                         t.get("relatedId"), t.get("relatedName"),
                         int(t.get("amount") or 0), t.get("description") or ""),
                    )
                imported["transactions"] = len(arr)
            if "staff" in data:
                g.db.execute("DELETE FROM staff")
                for i, s in enumerate(data["staff"]):
                    tf = s.get("transcriptFile")
                    tf_json = json.dumps(tf) if isinstance(tf, dict) and tf.get("data") else None
                    g.db.execute(
                        """INSERT INTO staff
                           (id, category, name, role, image, quote, age, school, gender, pronouns, interests, bio, transcript, transcriptFile, position)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (s["id"], s.get("category") or "", s.get("name") or "",
                         s.get("role") or "", s.get("image") or "", s.get("quote") or "",
                         s.get("age") or "", s.get("school") or "",
                         s.get("gender") or "", s.get("pronouns") or "",
                         s.get("interests") or "", s.get("bio") or "",
                         s.get("transcript") or "", tf_json, i),
                    )
                imported["staff"] = len(data["staff"])
        return jsonify(ok=True, imported=imported)

    # ── Settings: point-transaction freeze ─────────────────────────
    @app.route("/api/settings/points-frozen", methods=["GET"])
    def settings_points_frozen():
        return jsonify(ok=True, frozen=_points_frozen())

    @app.route("/api/admin/settings/points-frozen", methods=["POST"])
    @require_admin
    def settings_points_frozen_set():
        data = request.get_json(silent=True) or {}
        frozen = bool(data.get("frozen"))
        _meta_set("points_frozen", "1" if frozen else "0")
        return jsonify(ok=True, frozen=frozen)

    # ── Transactions bank ───────────────────────────────────────────
    # All points lost to the 50% transfer tax accumulate here. Anyone
    # can read the balance; only an admin can withdraw to a student.
    @app.route("/api/transactions-bank", methods=["GET"])
    def transactions_bank_balance():
        return jsonify(ok=True, balance=int(_meta_get("transactions_bank", "0") or "0"))

    @app.route("/api/admin/transactions-bank/withdraw", methods=["POST"])
    @require_admin
    def transactions_bank_withdraw():
        d = request.get_json(silent=True) or {}
        amount = int(d.get("amount") or 0)
        to_id  = (d.get("toId") or "").strip()
        note   = (d.get("note") or "").strip()
        if amount <= 0:
            return jsonify(ok=False, error="Enter a positive amount."), 400
        if not to_id:
            return jsonify(ok=False, error="Pick a recipient."), 400
        with g.db:
            balance = int(_meta_get("transactions_bank", "0") or "0")
            if balance < amount:
                return jsonify(ok=False, error=f"Bank has only {balance} pts."), 400
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (to_id,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Recipient not found."), 404
            stats = {**default_stats(), **json.loads(row["stats"] or "{}")}
            stats["privatePoints"]     = stats.get("privatePoints", 0) + amount
            stats["totalPointsEarned"] = stats.get("totalPointsEarned", 0) + amount
            g.db.execute("UPDATE students SET stats = ? WHERE id = ?", (json.dumps(stats), to_id))
            _meta_set("transactions_bank", str(balance - amount))
            to_name = _full_name(row)
            _log_tx(type="bank_withdraw", scope="bank",
                    subjectId="transactions_bank", subjectName="Transactions Bank",
                    relatedId=to_id, relatedName=to_name,
                    amount=-amount,
                    description=f"−{amount} pts withdrawn → {to_name}" + (f" · {note}" if note else ""))
            _log_tx(type="earn", scope="student", subjectId=to_id,
                    subjectName=to_name, amount=amount,
                    description=f"🏦 Transactions-bank grant · +{amount} pts" + (f" · {note}" if note else ""))
        return jsonify(ok=True, data={"awarded": amount, "newBalance": balance - amount})

    # ── Vulgar Vault — staff penalties + rotating claim code ───────
    # ── Staff point awards ─────────────────────────────────────────
    # One core used by both the admin page and the Discord /award command,
    # so the rules and the ledger entry are identical whichever way staff
    # reach for it.
    AWARD_MAX = 100000

    def _award_points(sid, amount, reason, awarded_by):
        """Give (or take) points with a written reason. Returns
        (response_dict, status). Negative amounts deduct and floor at zero.

        Luck rides on both directions. An award can come out doubled or
        tripled; a deduction can bounce off entirely. Both roll off the
        same effectiveness curve as everything else luck touches, and
        both are recorded on the transaction as what actually happened —
        the log says +600 (luck tripled 200), not a silent +600, because
        staff will be asked why the number isn't what they typed.

        The roll deliberately lives here rather than at the callers, so
        every path that awards points — staff, the Discord bot, the maze,
        the lime trial's bounty — gets it without being told to. Points
        arriving from a shard conversion do NOT come through here, and
        that's the point: multiplying those would hand the exchange
        counter back the loop the fee was there to close.
        """
        reason = (reason or "").strip()
        if not reason:
            return {"ok": False, "error": "Give a brief reason — it goes on the transaction."}, 400
        if amount == 0:
            return {"ok": False, "error": "Amount can't be zero."}, 400
        if abs(amount) > AWARD_MAX:
            return {"ok": False, "error": f"Amount looks suspiciously large (max {AWARD_MAX})."}, 400

        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return {"ok": False, "error": "Student not found."}, 404
            stats = {**default_stats(), **json.loads(row["stats"] or "{}")}
            cur = int(stats.get("privatePoints") or 0)

            luck = int(stats.get("luck", 0))
            luck_note = ""
            if amount > 0:
                mult, label = dungeon.luck_point_multiplier(luck)
                applied = amount * mult
                if label:
                    luck_note = f" · 🍀 luck {label}d {amount:,}"
                stats["privatePoints"]     = cur + applied
                # Lifetime earned only ever goes up.
                stats["totalPointsEarned"] = int(stats.get("totalPointsEarned") or 0) + applied
            elif dungeon.luck_shrugs_deduction(luck):
                applied = 0
                luck_note = f" · 🍀 luck shrugged off −{abs(amount):,}"
            else:
                # Never push anyone negative — take what they actually have.
                applied = -min(cur, abs(amount))
                stats["privatePoints"] = cur + applied

            g.db.execute("UPDATE students SET stats = ? WHERE id = ?",
                         (json.dumps(stats), sid))
            who = _full_name(row)
            by = (awarded_by or "staff").strip() or "staff"
            short = reason if len(reason) <= 300 else reason[:297] + "…"
            # Sign follows what was intended, not what landed — a deduction
            # clipped to 0 by an empty balance is still a deduction.
            sign = "+" if amount > 0 else "−"
            shortfall = (applied != amount and amount < 0 and applied != 0
                         and not luck_note)
            _log_tx(
                type="staff_award", scope="student", subjectId=sid, subjectName=who,
                relatedName=by, amount=applied,
                description=f"{sign}{abs(applied)} pts by {by} · {short}"
                            + (f" (asked for {abs(amount)}, only {cur} available)"
                               if shortfall else "")
                            + luck_note,
            )
        return {"ok": True, "data": {
            "studentId": sid, "studentName": who,
            "requested": amount, "applied": applied,
            "luck": luck, "luckNote": luck_note.strip(" ·"),
            "newPrivatePoints": stats["privatePoints"],
            "totalPointsEarned": stats.get("totalPointsEarned", 0),
            "reason": reason, "awardedBy": by,
            "frozen": bool(row["frozen"]),
        }}, 200

    def _award_amount_from(d):
        try:
            return int(d.get("amount") or d.get("points") or 0), None
        except (TypeError, ValueError):
            return 0, "Amount must be a whole number."

    @app.route("/api/admin/students/<sid>/award", methods=["POST"])
    @require_admin
    def admin_award_points(sid):
        d = request.get_json(silent=True) or {}
        amount, err = _award_amount_from(d)
        if err:
            return jsonify(ok=False, error=err), 400
        body, status = _award_points(sid, amount, d.get("reason"),
                                     (d.get("awardedBy") or "an admin"))
        return jsonify(**body), status

    @app.route("/api/bot/award", methods=["POST"])
    @require_bot
    def bot_award_points():
        """Discord side. Accepts either a studentId or the camper's
        discordId, so the bot doesn't need to resolve the link itself."""
        d = request.get_json(silent=True) or {}
        sid = (d.get("studentId") or "").strip()
        if not sid:
            discord_id = (d.get("discordId") or "").strip()
            if not discord_id:
                return jsonify(ok=False, error="studentId or discordId is required."), 400
            link = g.db.execute("SELECT * FROM discord_links WHERE discordId = ?",
                                (discord_id,)).fetchone()
            if not link:
                return jsonify(ok=False, unverified=True,
                               error="That camper hasn't verified their camp account yet."), 404
            sid = link["studentId"]
        amount, err = _award_amount_from(d)
        if err:
            return jsonify(ok=False, error=err), 400
        body, status = _award_points(sid, amount, d.get("reason"),
                                     (d.get("awardedBy") or "staff"))
        return jsonify(**body), status

    # ── Attendance ─────────────────────────────────────────────────
    # Present pays, late costs, absent is neutral. The point movement goes
    # through the same ledger as everything else so it's auditable.
    ATTENDANCE_POINTS = {"present": 250, "late": -50, "absent": 0}

    def _camp_today():
        """Today's date in camp-local time, so a late-evening mark doesn't
        land on tomorrow the way a naive UTC date would."""
        try:
            from zoneinfo import ZoneInfo
            from datetime import datetime
            return datetime.now(ZoneInfo("America/Toronto")).strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            return time.strftime("%Y-%m-%d")

    def _mark_attendance(sid, date, status, marked_by):
        status = (status or "").strip().lower()
        if status not in ATTENDANCE_POINTS:
            return {"ok": False,
                    "error": "Status must be present, late, or absent."}, 400
        date = (date or "").strip() or _camp_today()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            return {"ok": False, "error": "Date must look like YYYY-MM-DD."}, 400

        row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
        if not row:
            return {"ok": False, "error": "Student not found."}, 404
        who = _full_name(row)
        by = (marked_by or "staff").strip() or "staff"

        prev = g.db.execute(
            "SELECT * FROM attendance WHERE studentId = ? AND date = ?", (sid, date),
        ).fetchone()
        if prev and prev["status"] == status:
            return {"ok": True, "data": {
                "studentId": sid, "studentName": who, "date": date, "status": status,
                "pointsApplied": int(prev["pointsApplied"] or 0), "unchanged": True,
            }}, 200

        # Re-marking: undo exactly what the previous mark moved, not its
        # nominal value — a deduction may have been clipped by the balance.
        reversed_pts = 0
        if prev and int(prev["pointsApplied"] or 0) != 0:
            undo = -int(prev["pointsApplied"])
            body, st = _award_points(
                sid, undo,
                f"Attendance correction for {date} (was {prev['status']})", by)
            if st != 200:
                return body, st
            reversed_pts = int((body.get("data") or {}).get("applied") or 0)

        nominal = ATTENDANCE_POINTS[status]
        applied = 0
        if nominal != 0:
            body, st = _award_points(
                sid, nominal, f"Attendance {date}: {status}", by)
            if st != 200:
                return body, st
            applied = int((body.get("data") or {}).get("applied") or 0)

        now = int(time.time())
        if prev:
            g.db.execute(
                """UPDATE attendance SET status = ?, pointsApplied = ?, markedBy = ?,
                   markedAt = ? WHERE id = ?""",
                (status, applied, by, now, prev["id"]))
            aid = prev["id"]
        else:
            aid = "att-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(3)
            g.db.execute(
                """INSERT INTO attendance
                   (id, studentId, date, status, pointsApplied, markedBy, markedAt)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (aid, sid, date, status, applied, by, now))

        fresh = g.db.execute("SELECT stats FROM students WHERE id = ?", (sid,)).fetchone()
        stats = {**default_stats(), **json.loads(fresh["stats"] or "{}")}
        return {"ok": True, "data": {
            "id": aid, "studentId": sid, "studentName": who, "date": date,
            "status": status, "pointsApplied": applied, "reversed": reversed_pts,
            "previousStatus": prev["status"] if prev else None,
            "newPrivatePoints": stats.get("privatePoints", 0),
        }}, 200

    def _attendance_rows(date=None, sid=None):
        sql = "SELECT * FROM attendance WHERE 1=1"
        params = []
        if date:
            sql += " AND date = ?"
            params.append(date)
        if sid:
            sql += " AND studentId = ?"
            params.append(sid)
        sql += " ORDER BY date DESC, markedAt DESC LIMIT 2000"
        out = []
        for r in g.db.execute(sql, tuple(params)).fetchall():
            d = dict(r)
            # Resolve the name here rather than storing a second copy of it
            # — the students table already holds it, encrypted at rest.
            srow = g.db.execute("SELECT * FROM students WHERE id = ?",
                                (d["studentId"],)).fetchone()
            d["studentName"] = _full_name(srow) if srow else "(removed student)"
            out.append(d)
        return out

    @app.route("/api/admin/attendance", methods=["GET"])
    @require_admin
    def admin_attendance_list():
        return jsonify(ok=True,
                       date=(request.args.get("date") or "").strip() or _camp_today(),
                       today=_camp_today(),
                       points=ATTENDANCE_POINTS,
                       data=_attendance_rows((request.args.get("date") or "").strip() or None,
                                             (request.args.get("studentId") or "").strip() or None))

    @app.route("/api/admin/attendance", methods=["POST"])
    @require_admin
    def admin_attendance_mark():
        d = request.get_json(silent=True) or {}
        body, status = _mark_attendance(
            (d.get("studentId") or "").strip(), d.get("date"),
            d.get("status"), d.get("markedBy") or "an admin")
        return jsonify(**body), status

    @app.route("/api/bot/attendance", methods=["POST"])
    @require_bot
    def bot_attendance_mark():
        d = request.get_json(silent=True) or {}
        sid = (d.get("studentId") or "").strip()
        if not sid:
            discord_id = (d.get("discordId") or "").strip()
            if not discord_id:
                return jsonify(ok=False, error="studentId or discordId is required."), 400
            link = g.db.execute("SELECT * FROM discord_links WHERE discordId = ?",
                                (discord_id,)).fetchone()
            if not link:
                return jsonify(ok=False, unverified=True,
                               error="That camper hasn't verified their camp account yet."), 404
            sid = link["studentId"]
        body, status = _mark_attendance(sid, d.get("date"), d.get("status"),
                                        d.get("markedBy") or "staff")
        return jsonify(**body), status

    @app.route("/api/bot/attendance", methods=["GET"])
    @require_bot
    def bot_attendance_list():
        date = (request.args.get("date") or "").strip() or _camp_today()
        return jsonify(ok=True, date=date, today=_camp_today(),
                       points=ATTENDANCE_POINTS, data=_attendance_rows(date))

    def _apply_penalty(sid, amount, *, kind):
        """Shared core for penalty + curse: deducts points from the
        student, deposits them into the Vulgar Vault, logs both sides.
        Returns (response_dict, status). `kind` is 'penalty' or 'curse'."""
        if amount <= 0:
            return {"ok": False, "error": "Amount must be positive."}, 400
        if amount > 100000:
            return {"ok": False, "error": "Amount looks suspiciously large."}, 400
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return {"ok": False, "error": "Student not found."}, 404
            stats = {**default_stats(), **json.loads(row["stats"] or "{}")}
            cur = stats.get("privatePoints", 0)
            taken = min(cur, amount)  # never go negative
            stats["privatePoints"] = cur - taken
            if kind == "curse":
                stats["badWords"] = stats.get("badWords", 0) + 1
            g.db.execute("UPDATE students SET stats = ? WHERE id = ?", (json.dumps(stats), sid))
            who = _full_name(row)
            # Vault deposit reflects the *requested* penalty amount even
            # if the student couldn't cover it — staff intent is what
            # the rule logs.
            vault_prev = int(_meta_get("vulgar_vault", "0") or "0")
            _meta_set("vulgar_vault", str(vault_prev + amount))
            label = "🤬 Curse-word penalty" if kind == "curse" else "⚠️ Admin penalty"
            _log_tx(type=kind, scope="student", subjectId=sid,
                    subjectName=who, amount=-taken,
                    description=f"{label} −{amount} pts" + (f" (only {taken} available)" if taken < amount else "") +
                                (" (bad-word count +1)" if kind == "curse" else "") +
                                f" · {amount} → Vulgar Vault")
            _log_tx(type="vulgar_deposit", scope="bank",
                    subjectId="vulgar_vault", subjectName="Vulgar Vault",
                    relatedId=sid, relatedName=who,
                    amount=amount,
                    description=f"+{amount} pts from {who} · {label.lower()}")
        return {
            "ok": True,
            "data": {
                "amount": amount,
                "remaining": stats["privatePoints"],
                "badWords": stats.get("badWords", 0),
                "student": row_to_student(g.db.execute(
                    "SELECT * FROM students WHERE id = ?", (sid,)
                ).fetchone()),
            },
        }, 200

    @app.route("/api/bot/base-stat", methods=["POST"])
    @require_bot
    def bot_student_base_stat():
        """Same stat bump as the admin route, reached from Discord. Takes a
        studentId or the camper's discordId."""
        d = request.get_json(silent=True) or {}
        sid = (d.get("studentId") or "").strip()
        if not sid:
            discord_id = (d.get("discordId") or "").strip()
            if not discord_id:
                return jsonify(ok=False, error="studentId or discordId is required."), 400
            link = g.db.execute("SELECT * FROM discord_links WHERE discordId = ?",
                                (discord_id,)).fetchone()
            if not link:
                return jsonify(ok=False, unverified=True,
                               error="That camper hasn't verified their camp account yet."), 404
            sid = link["studentId"]
        return _bump_base_stat(sid, d)

    @app.route("/api/bot/base-stats", methods=["GET"])
    @require_bot
    def bot_base_stats():
        rows = g.db.execute(
            "SELECT * FROM base_stat_categories ORDER BY position ASC").fetchall()
        return jsonify(ok=True, data=[row_to_basestat(r) for r in rows])

    def _bump_base_stat(sid, d):
        """Adjust an admin-defined base stat for a student. The stat's
        `pointsPerUnit` (set on the Base Stats admin page) determines how
        many private points the student gains or loses per unit. Shared by
        the admin route and the Discord one.
        Body: { catId: str, delta: int }. Returns (body, status)."""
        cat_id = (d.get("catId") or "").strip()
        try:
            delta = int(d.get("delta") or 0)
        except (TypeError, ValueError):
            return jsonify(ok=False, error="Delta must be an integer."), 400
        if not cat_id:
            return jsonify(ok=False, error="catId is required."), 400
        if delta == 0:
            return jsonify(ok=False, error="Delta cannot be zero."), 400
        if abs(delta) > 1000:
            return jsonify(ok=False, error="Delta out of range."), 400
        with g.db:
            cat = g.db.execute(
                "SELECT * FROM base_stat_categories WHERE id = ?", (cat_id,)
            ).fetchone()
            if not cat:
                return jsonify(ok=False, error="Stat category not found."), 404
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            ppu = int(cat["pointsPerUnit"] or 0)
            base = json.loads(row["baseStats"] or "{}")
            cur_count = int(base.get(cat_id, 0) or 0)
            new_count = max(0, cur_count + delta)
            applied_delta = new_count - cur_count
            if applied_delta == 0:
                return jsonify(ok=False, error="Stat is already at zero."), 400
            base[cat_id] = new_count
            stats = {**default_stats(), **json.loads(row["stats"] or "{}")}
            point_delta = applied_delta * ppu
            stats["privatePoints"]     = max(0, stats.get("privatePoints", 0) + point_delta)
            stats["totalPointsEarned"] = max(0, stats.get("totalPointsEarned", 0) + point_delta)
            g.db.execute(
                "UPDATE students SET stats = ?, baseStats = ? WHERE id = ?",
                (json.dumps(stats), json.dumps(base), sid),
            )
            who = _full_name(row)
            sign = "+" if point_delta >= 0 else "−"
            label = cat["name"] or cat_id
            _log_tx(type=("stat_award" if point_delta > 0 else "stat_penalty"),
                    scope="student", subjectId=sid, subjectName=who,
                    amount=point_delta,
                    description=f"{cat['icon'] or '📊'} {label} {applied_delta:+d} → {sign}{abs(point_delta)} pts ({sign}{abs(applied_delta)} × {ppu} pts/unit)")
            updated = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
        return jsonify(ok=True, data={
            "appliedDelta": applied_delta,
            "newCount": new_count,
            "pointDelta": point_delta,
            "statName": label,
            "studentName": who,
            "newPrivatePoints": stats["privatePoints"],
            "student": row_to_student(updated),
        }), 200

    @app.route("/api/admin/students/<sid>/base-stat", methods=["POST"])
    @require_admin
    def admin_student_base_stat(sid):
        return _bump_base_stat(sid, request.get_json(silent=True) or {})

    @app.route("/api/admin/students/<sid>/penalty", methods=["POST"])
    @require_admin
    def admin_student_penalty(sid):
        d = request.get_json(silent=True) or {}
        amount = int(d.get("amount") or 0)
        body, status = _apply_penalty(sid, amount, kind="penalty")
        return jsonify(body), status

    @app.route("/api/admin/students/<sid>/curse", methods=["POST"])
    @require_admin
    def admin_student_curse(sid):
        d = request.get_json(silent=True) or {}
        amount = int(d.get("amount") or 0)
        body, status = _apply_penalty(sid, amount, kind="curse")
        return jsonify(body), status

    @app.route("/api/admin/students/<sid>/freeze", methods=["POST"])
    @require_admin
    def admin_student_freeze(sid):
        d = request.get_json(silent=True) or {}
        frozen = 1 if d.get("frozen", 1) else 0
        row = g.db.execute(
            "SELECT id, frozen, firstName, lastName, studentEmail, parentEmail "
            "FROM students WHERE id = ?",
            (sid,),
        ).fetchone()
        if not row:
            return jsonify(ok=False, error="Student not found"), 404
        was_frozen = bool(row["frozen"])
        g.db.execute("UPDATE students SET frozen = ? WHERE id = ?", (frozen, sid))
        # If we're freezing, drop any active student sessions so the next
        # request from that camper bounces them back to the login screen.
        if frozen:
            g.db.execute(
                "DELETE FROM sessions WHERE kind = 'student' AND studentId = ?",
                (sid,),
            )
        # Unfreezing a previously-frozen camper means payment was confirmed —
        # send the welcome email with all the camp details. Best-effort: a
        # mail hiccup must never block the unfreeze itself.
        elif was_frozen:
            try:
                name = _full_name(row)
                _send_camp_welcome(crypto.dec(row["studentEmail"]), name,
                                   crypto.dec(row["parentEmail"]) or None)
            except Exception:  # noqa: BLE001
                pass
        return jsonify(ok=True, frozen=bool(frozen))

    def _vault_state(include_code):
        balance = int(_meta_get("vulgar_vault", "0") or "0")
        now = int(time.time())
        minute = now // VULGAR_VAULT_PERIOD
        seconds_left = VULGAR_VAULT_PERIOD - (now % VULGAR_VAULT_PERIOD)
        out = {"ok": True, "balance": balance, "secondsLeft": seconds_left, "rotation": VULGAR_VAULT_PERIOD}
        if include_code:
            out["code"] = _vulgar_code(minute)
        return out

    @app.route("/api/admin/vulgar-vault", methods=["GET"])
    @require_admin
    def admin_vulgar_vault():
        return jsonify(_vault_state(include_code=True))

    @app.route("/api/vulgar-vault/balance", methods=["GET"])
    def vulgar_vault_balance():
        return jsonify(_vault_state(include_code=False))

    @app.route("/api/students/me/claim-vulgar-vault", methods=["POST"])
    @require_student
    @block_when_frozen
    def claim_vulgar_vault():
        d = request.get_json(silent=True) or {}
        code = "".join(ch for ch in str(d.get("code") or "") if ch.isdigit())
        if len(code) != VULGAR_VAULT_CODE_LEN:
            return jsonify(ok=False, error=f"Enter the current {VULGAR_VAULT_CODE_LEN}-digit code."), 400
        # Accept the current minute and the previous one so a student
        # who reads the code in the last second can still submit.
        now_minute = int(time.time()) // VULGAR_VAULT_PERIOD
        valid = any(
            hmac.compare_digest(_vulgar_code(now_minute - i), code)
            for i in range(VULGAR_VAULT_GRACE + 1)
        )
        if not valid:
            return jsonify(ok=False, error="That code is wrong or expired. The code rotates every minute."), 403
        sid = g.session["studentId"]
        with g.db:
            balance = int(_meta_get("vulgar_vault", "0") or "0")
            if balance <= 0:
                return jsonify(ok=False, error="The Vulgar Vault is empty right now."), 400
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            stats = {**default_stats(), **json.loads(row["stats"] or "{}")}
            stats["privatePoints"]     = stats.get("privatePoints", 0) + balance
            stats["totalPointsEarned"] = stats.get("totalPointsEarned", 0) + balance
            g.db.execute("UPDATE students SET stats = ? WHERE id = ?", (json.dumps(stats), sid))
            _meta_set("vulgar_vault", "0")
            who = _full_name(row)
            _log_tx(type="vulgar_claim", scope="bank",
                    subjectId="vulgar_vault", subjectName="Vulgar Vault",
                    relatedId=sid, relatedName=who,
                    amount=-balance,
                    description=f"−{balance} pts drained → {who} (correct rotating code)")
            _log_tx(type="earn", scope="student", subjectId=sid,
                    subjectName=who, amount=balance,
                    description=f"🤐 Vulgar Vault claim · +{balance} pts (entered the rotating code)")
        return jsonify(ok=True, data={"awarded": balance, "newBalance": 0})

    # ── Camp registration intake ────────────────────────────────────
    # Cap applies to IN-PERSON seats only; online seats are unlimited.
    DEFAULT_STUDENT_CAP = 40

    def _student_cap():
        try:
            return int(_meta_get("student_cap", str(DEFAULT_STUDENT_CAP)) or DEFAULT_STUDENT_CAP)
        except (TypeError, ValueError):
            return DEFAULT_STUDENT_CAP

    def _reconcile_camper_email(email):
        """Keep the campers (students) consistent with registrations for a
        SINGLE email. Scoped on purpose: it only ever touches the email
        passed in, so existing campers are never swept unless this exact
        email is part of the change being made right now.

        Enforces two rules the way the camp wants for future problems:
          • a camper with NO matching registration is removed (orphan)
          • if several campers share the email, the OLDEST account is kept
            and the newer duplicate(s) are removed ("delete the new one")

        Returns the number of student accounts removed."""
        email = (email or "").strip().lower()
        if not email:
            return 0
        clause, param = _email_clause(email)
        reg_count = g.db.execute(
            f"SELECT COUNT(*) AS n FROM registrations WHERE {clause}",
            (param,),
        ).fetchone()["n"]
        # Oldest first, so students[0] is the established account to keep.
        students = g.db.execute(
            f"SELECT id FROM students WHERE {clause} "
            "ORDER BY CAST(COALESCE(registeredAt, '0') AS INTEGER) ASC, id ASC",
            (param,),
        ).fetchall()
        # No registration backs this email → every matching camper is an
        # orphan. Otherwise keep the oldest and drop the duplicates.
        doomed = students if reg_count == 0 else students[1:]
        removed = 0
        for s in doomed:
            g.db.execute(
                "DELETE FROM sessions WHERE kind = 'student' AND studentId = ?",
                (s["id"],),
            )
            g.db.execute("DELETE FROM students WHERE id = ?", (s["id"],))
            removed += 1
        return removed

    # Registration pricing window — set manually from the admin panel.
    # One flat registration fee now (the old early/normal/late tiers were scrapped
    # in favour of discount codes). Registration is simply Open or Closed.
    FLAT_PRICE = 135
    REG_TIERS = {
        "closed": {"open": False, "price": 0,          "label": "Closed"},
        "open":   {"open": True,  "price": FLAT_PRICE,  "label": "Open"},
    }

    def _reg_tier():
        # Any non-'closed' stored value (including the legacy early/normal/late)
        # means registration is open at the single flat price.
        # Defaults to "open" so registration is live by default.
        t = _meta_get("reg_tier", "open") or "open"
        return "closed" if t == "closed" else "open"

    @app.route("/api/camp/register", methods=["POST"])
    def camp_register():
        # Hard freeze — admins can pause new registrations entirely.
        if _meta_get("registrations_frozen", "0") == "1":
            return jsonify(
                ok=False,
                frozen=True,
                reason=(_meta_get("registrations_frozen_reason", "") or ""),
                error="Registrations are temporarily closed by the camp admins. Please check back soon.",
            ), 423
        # Registration tier — admins close intake by setting the tier to "closed".
        if not REG_TIERS[_reg_tier()]["open"]:
            return jsonify(
                ok=False,
                error="Registration is closed right now. Please check back soon.",
            ), 423
        d = request.get_json(silent=True) or {}
        first = (d.get("first_name") or "").strip()
        last  = (d.get("last_name")  or "").strip()
        password = (d.get("password") or "").strip() or None
        student_email = (d.get("student_email") or "").strip()
        parent_email  = (d.get("parent_email")  or "").strip()
        if not first or not last:
            return jsonify(ok=False, error="First and last name are required."), 400
        if _is_reserved_email(student_email):
            return jsonify(ok=False, error="That email isn't available — please use a different one."), 400
        # The student email is the camper's login AND where the confirmation
        # goes, so it must be present and well-formed before we do anything.
        if not _looks_like_email(student_email):
            return jsonify(
                ok=False,
                emailInvalid=True,
                error="We were unable to locate that email address. Please re-register with a valid email address so we can send your confirmation.",
            ), 400

        # Send the confirmation / payment-request email up front. If the mail
        # server rejects the recipient outright, the address doesn't exist —
        # stop here and ask them to re-register BEFORE we persist anything, so
        # no orphan registration is left behind. Any other send failure is our
        # side (SMTP down, etc.) and must NOT block the registration.
        # "Were you referred?" accepts EITHER a friend's email or a staff
        # referral code:
        #   • A friend's EMAIL is stored so staff can verify the referral and
        #     e-Transfer that person their $20 reward.
        #   • A staff CODE instead grants the CAMPER 25% off — staff are never
        #     paid for their code, it's purely a discount for the family.
        # A non-blank value that's neither a valid email nor a real staff code
        # is rejected so the family isn't silently charged full price.
        referred_by = (d.get("referred_by") or d.get("referrer_email") or "").strip()
        referrer_email = None
        staff_code = None
        staff_pct = 0.0
        if referred_by:
            if _looks_like_email(referred_by):
                if referred_by.lower() != student_email.lower():
                    referrer_email = referred_by
            else:
                code = _normalize_referral_input(referred_by)
                row = (g.db.execute("SELECT name FROM staff WHERE referralCode = ?",
                                    (code,)).fetchone() if code else None)
                if not row:
                    return jsonify(ok=False, error="That doesn't look like a valid "
                                   "email or staff referral code. Double-check it, or "
                                   "leave the field blank."), 400
                staff_code = code
                staff_pct = 0.25
        # Discounts DON'T stack: a discount code and a staff referral code both
        # give a percentage off, so we honour whichever is larger. (The only
        # discount that stacks is the 5% sponsor-location payment, applied later
        # on top of this amount.)
        discount_code = (d.get("discount_code") or "").strip()
        disc_row = _lookup_discount(g.db, discount_code) if discount_code else None
        disc_pct = disc_row["percent"] if disc_row else 0.0
        best_pct = max(disc_pct, staff_pct)
        # FREE mode: camp costs nothing, so there's no payment step and the
        # account unlocks immediately (see the student-provision block below).
        free_mode = _camp_free()
        if free_mode:
            amount_due = 0
        else:
            _base_price = REG_TIERS[_reg_tier()]["price"]
            amount_due = (round(_base_price * (1.0 - best_pct), 2)
                          if _base_price else _base_price)

        # Delivery mode decides capacity below: online seats are unlimited,
        # in-person seats are capped.
        mode = (d.get("delivery_mode") or "").strip().lower()
        if mode not in ("in_person", "online"):
            mode = None
        is_online = (mode == "online")
        try:
            if free_mode:
                # No payment needed — send the "you're all set" welcome email
                # (which also verifies the address is deliverable).
                confirm_status = _send_camp_welcome(
                    student_email,
                    f"{first} {last}".strip(),
                    parent_email or None,
                    free=True,
                )
            else:
                confirm_status = _send_registration_confirm(
                    student_email,
                    f"{first} {last}".strip(),
                    parent_email or None,
                    amount=amount_due or None,
                )
        except Exception:  # noqa: BLE001
            confirm_status = "error"
        if confirm_status == "refused":
            return jsonify(
                ok=False,
                emailInvalid=True,
                error="We were unable to send a confirmation to that email address, so we couldn't verify it. Please re-register with a valid email address.",
            ), 400

        # Cap check — in-person seats are capped; online seats are unlimited.
        cap = _student_cap()
        if is_online:
            waitlisted = 0
        else:
            in_person_active = g.db.execute(
                "SELECT COUNT(*) AS n FROM registrations "
                "WHERE COALESCE(waitlisted, 0) = 0 "
                "AND COALESCE(deliveryMode, 'in_person') != 'online'"
            ).fetchone()["n"]
            waitlisted = 1 if in_person_active >= cap else 0
        rid = "reg-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(3)
        # Authorized pickup people — optional list of {name, phone, relationship}.
        raw_pickup = d.get("pickup_people")
        clean_pickup = []
        if isinstance(raw_pickup, list):
            for p in raw_pickup:
                if not isinstance(p, dict):
                    continue
                nm = (p.get("name") or "").strip()
                ph = (p.get("phone") or "").strip()
                rel = (p.get("relationship") or "").strip()
                if not nm and not ph and not rel:
                    continue
                clean_pickup.append({"name": nm, "phone": ph, "relationship": rel})
        pickup_json = json.dumps(clean_pickup)
        # Free-movement-on-campus-during-breaks choice. Only two valid values;
        # anything else is stored as NULL.
        roaming = (d.get("campus_roaming") or "").strip().lower()
        if roaming not in ("allow", "no"):
            roaming = None
        # Medical notes the family wants staff to know about (mode computed up top).
        medical = (d.get("medical_info") or "").strip() or None
        g.db.execute(
            """INSERT INTO registrations
               (id, createdAt, firstName, lastName, dob, studentEmail, school,
                parentFirst, parentLast, relationship, parentPhone, parentEmail,
                emerg1Name, emerg1Phone, emerg1Relationship,
                hobbies, whyJoin, consentPhoto, campusRoaming,
                deliveryMode, medicalInfo, discountCode, amountDue,
                password, waitlisted, pickupPeople, referrerEmail,
                referralCode, referredByCode, emailIndex)
               VALUES
               (:id, :createdAt, :firstName, :lastName, :dob, :studentEmail, :school,
                :parentFirst, :parentLast, :relationship, :parentPhone, :parentEmail,
                :emerg1Name, :emerg1Phone, :emerg1Relationship,
                :hobbies, :whyJoin, :consentPhoto, :campusRoaming,
                :deliveryMode, :medicalInfo, :discountCode, :amountDue,
                :password, :waitlisted, :pickupPeople, :referrerEmail,
                :referralCode, :referredByCode, :emailIndex)""",
            _encrypt_registration({
                "id": rid, "createdAt": int(time.time()),
                "firstName": first, "lastName": last,
                "dob": (d.get("dob") or "").strip() or None,
                "studentEmail": (d.get("student_email") or "").strip() or None,
                "school": (d.get("school") or "").strip() or None,
                "parentFirst": (d.get("parent_first") or "").strip() or None,
                "parentLast": (d.get("parent_last") or "").strip() or None,
                "relationship": (d.get("relationship") or "").strip() or None,
                "parentPhone": (d.get("parent_phone") or "").strip() or None,
                "parentEmail": (d.get("parent_email") or "").strip() or None,
                "emerg1Name": (d.get("emerg1_name") or "").strip() or None,
                "emerg1Phone": (d.get("emerg1_phone") or "").strip() or None,
                "emerg1Relationship": (d.get("emerg1_relationship") or "").strip() or None,
                "hobbies": (d.get("hobbies") or None),
                "whyJoin": (d.get("why_join") or None),
                "consentPhoto": 1 if d.get("consent_photo") else 0,
                "campusRoaming": roaming,
                "deliveryMode": mode,
                "medicalInfo": medical,
                "discountCode": (discount_code or None),
                "amountDue": amount_due,
                "password": password,
                "waitlisted": waitlisted,
                "pickupPeople": pickup_json,
                "referrerEmail": referrer_email,
                "referralCode": None,
                "referredByCode": staff_code,   # staff referral code used (25% off), if any
                "emailIndex": crypto.blind(student_email) if crypto.enabled() else None,
            }),
        )
        # Auto-provision a frozen student account so the camper can attempt
        # to sign in once staff confirms the e-Transfer. Skip silently
        # if email is blank or a student record already exists for this
        # email.
        try:
            normalized_email = (d.get("student_email") or "").strip()
            if normalized_email:
                _ap_clause, _ap_param = _email_clause(normalized_email)
                already = g.db.execute(
                    f"SELECT id FROM students WHERE {_ap_clause}",
                    (_ap_param,),
                ).fetchone()
                if not already:
                    _insert_student(_normalize_student({
                        "firstName":    first,
                        "lastName":     last,
                        "studentEmail": normalized_email,
                        "password":     password,
                        "parentEmail":  (d.get("parent_email") or "").strip() or None,
                        "phone":        (d.get("parent_phone") or "").strip() or None,
                        "school":       (d.get("school") or "").strip() or None,
                        "registeredAt": str(int(time.time())),
                        # FREE mode has no payment gate, so unlock immediately.
                        "frozen":       0 if free_mode else 1,
                    }))
        except Exception:  # noqa: BLE001
            pass
        # Keep campers in sync with registrations for this email: drop any
        # stale duplicate student accounts so there's exactly one camper per
        # registered email. Scoped to this email only — never touches others.
        try:
            _reconcile_camper_email(student_email)
        except Exception:  # noqa: BLE001
            pass
        # Consume a single-use discount code now that the registration is saved
        # (evergreen codes are left in place so they keep working).
        if disc_row:
            try:
                _consume_discount(g.db, discount_code, rid)
            except Exception:  # noqa: BLE001
                pass
        # NOTE: the confirmation / payment-request email was already sent
        # near the top of this handler (before any DB writes) so we could
        # reject undeliverable addresses up front.
        return jsonify(ok=True, id=rid, waitlisted=bool(waitlisted),
                       amountDue=amount_due)

    @app.route("/api/camp/register/<rid>/payment-method", methods=["POST"])
    def camp_register_payment_method(rid):
        """Let a family flag their own registration as "pay in cash" from the
        post-submit success screen (instead of e-Transfer). Unauthenticated,
        like the registration submission itself — `rid` includes a random
        suffix so it isn't guessable, and this can only ever set a payment
        method on a row that already exists."""
        d = request.get_json(silent=True) or {}
        method = (d.get("method") or "").strip().lower()
        if method not in ("cash", "e_transfer", "sponsor"):
            return jsonify(ok=False, error="Invalid payment method."), 400
        # Paying at a sponsor store requires picking which one, and grants the
        # extra compounding 5% off. Any other method clears the sponsor field.
        sponsor_loc = None
        if method == "sponsor":
            sponsor_loc = (d.get("sponsorLocation") or d.get("sponsor_location") or "").strip().lower()
            if sponsor_loc not in SPONSOR_PAY_LOCATIONS:
                return jsonify(ok=False, error="Please choose one of our sponsor locations."), 400
        row = g.db.execute("SELECT * FROM registrations WHERE id = ?", (rid,)).fetchone()
        if not row:
            return jsonify(ok=False, error="Registration not found."), 404
        reg = _decrypt_registration(dict(row))
        # A card-paid registration is already settled — don't let a stray
        # payment-method call overwrite it or re-send instructions.
        if reg.get("paidAt"):
            return jsonify(ok=True, alreadyPaid=True, amount=reg.get("amountDue")), 200
        # Only email when the selection actually CHANGES — so re-clicks, page
        # reloads, or double-fires never re-send the instructions. (For sponsor,
        # a different store counts as a change.)
        prev_method = reg.get("paymentMethod")
        prev_sponsor = reg.get("sponsorLocation")
        selection_changed = (method != prev_method) or (
            method == "sponsor" and sponsor_loc != prev_sponsor)
        g.db.execute(
            "UPDATE registrations SET paymentMethod = ?, sponsorLocation = ? WHERE id = ?",
            (method, sponsor_loc, rid),
        )
        # amountDue stays the canonical post-registration-discount amount; the
        # sponsor 5% is derived so toggling methods can't compound repeatedly.
        base = reg.get("amountDue")
        final = base
        if method == "sponsor" and base is not None:
            final = round(base * (1.0 - SPONSOR_PAY_DISCOUNT), 2)
        # Email the how-to-pay instructions + the "refer 4 → free" deal now that
        # the family has committed to a non-card method — but only once per
        # distinct choice. Best-effort — a mail hiccup must never fail selection.
        if selection_changed:
            try:
                _send_payment_instructions(
                    method,
                    (reg.get("studentEmail") or "").strip() or None,
                    f"{reg.get('firstName') or ''} {reg.get('lastName') or ''}".strip(),
                    (reg.get("parentEmail") or "").strip() or None,
                    amount=final,
                    sponsor_name=_sponsor_loc_name(sponsor_loc),
                )
            except Exception:  # noqa: BLE001
                pass
        return jsonify(ok=True, paymentMethod=method,
                       sponsorLocation=sponsor_loc,
                       sponsorLocationName=_sponsor_loc_name(sponsor_loc),
                       sponsorLocationUrl=_sponsor_loc_url(sponsor_loc),
                       amount=final)

    @app.route("/api/stripe/config", methods=["GET"])
    def stripe_config():
        """Public Stripe config the browser needs to render the card form.
        Never includes the secret key. `enabled` is False (and the frontend
        hides the card option) until keys are set in the VM env."""
        return jsonify(ok=True, **stripe_pay.public_config())

    def _reg_amount_cents(reg):
        """Cents to charge for a registration — the canonical post-discount
        amountDue. The sponsor-location 5% is an in-person-only perk and never
        applies to a card charge, so it's deliberately not subtracted here."""
        amount_due = reg.get("amountDue")
        if amount_due is None or amount_due <= 0:
            return None, None
        return int(round(float(amount_due) * 100)), amount_due

    @app.route("/api/camp/register/<rid>/create-payment-intent", methods=["POST"])
    def camp_register_create_pi(rid):
        """Create a Stripe PaymentIntent for a registration's fee and hand the
        browser its client_secret so Stripe.js can collect + confirm the card.
        We charge the exact post-discount amount already stored on the row, so
        no Stripe coupons/prices are involved. Unauthenticated like the rest of
        the post-submit payment flow — `rid` carries a random suffix."""
        if not stripe_pay.enabled():
            return jsonify(ok=False, error="Card payments aren't set up yet. "
                           "Please use another payment option."), 400
        reg = g.db.execute("SELECT * FROM registrations WHERE id = ?", (rid,)).fetchone()
        if not reg:
            return jsonify(ok=False, error="Registration not found."), 404
        reg = _decrypt_registration(dict(reg))
        if reg.get("paidAt"):
            return jsonify(ok=True, alreadyPaid=True, amount=reg.get("amountDue")), 200
        amount_cents, amount_due = _reg_amount_cents(reg)
        if not amount_cents:
            return jsonify(ok=False, error="There's no amount due on this "
                           "registration to charge."), 400
        student_email = (reg.get("studentEmail") or "").strip() or None
        camper_name = f"{reg.get('firstName') or ''} {reg.get('lastName') or ''}".strip()
        try:
            pi = stripe_pay.create_payment_intent(
                amount_cents=amount_cents,
                rid=rid,
                receipt_email=student_email,
                description=(f"Camp registration — {camper_name}" if camper_name
                             else "Camp registration"),
            )
        except stripe_pay.StripeError as e:
            return jsonify(ok=False, error=e.message), 502
        return jsonify(ok=True, clientSecret=pi.get("client_secret"),
                       amount=amount_due)

    @app.route("/api/camp/register/<rid>/confirm-payment", methods=["POST"])
    def camp_register_confirm_payment(rid):
        """After the browser confirms the card with Stripe, verify the payment
        server-side (retrieve it from Stripe — never trust the client) and, if
        it truly succeeded for the right registration and amount, record it and
        auto-unfreeze the camper's account. Card charges confirm instantly, so
        the account activates right away (unlike e-Transfer / cash)."""
        if not stripe_pay.enabled():
            return jsonify(ok=False, error="Card payments aren't set up yet."), 400
        d = request.get_json(silent=True) or {}
        pi_id = (d.get("paymentIntentId") or d.get("payment_intent") or "").strip()
        if not pi_id:
            return jsonify(ok=False, error="Missing payment reference."), 400

        reg = g.db.execute("SELECT * FROM registrations WHERE id = ?", (rid,)).fetchone()
        if not reg:
            return jsonify(ok=False, error="Registration not found."), 404
        reg = _decrypt_registration(dict(reg))
        if reg.get("paidAt"):
            return jsonify(ok=True, alreadyPaid=True, paymentMethod="card",
                           amount=reg.get("amountDue"), accountActive=True), 200

        try:
            pi = stripe_pay.retrieve_payment_intent(pi_id)
        except stripe_pay.StripeError as e:
            return jsonify(ok=False, error=e.message), 502

        # Authoritative checks against Stripe's copy of the payment:
        #   • it actually succeeded,
        #   • it belongs to THIS registration (metadata.rid), and
        #   • it covers the amount owed.
        expected_cents, amount_due = _reg_amount_cents(reg)
        meta_rid = ((pi.get("metadata") or {}).get("rid") or "")
        received = pi.get("amount_received") or 0
        if pi.get("status") != "succeeded":
            return jsonify(ok=False, error="That payment hasn't completed yet. "
                           "Please try again."), 402
        if meta_rid != rid:
            return jsonify(ok=False, error="That payment doesn't match this "
                           "registration."), 400
        if expected_cents and received < expected_cents:
            return jsonify(ok=False, error="The payment amount didn't match the "
                           "fee due. Please contact us."), 400

        now = int(time.time())
        g.db.execute(
            "UPDATE registrations SET paymentMethod = 'card', sponsorLocation = NULL, "
            "paymentRef = ?, paidAt = ? WHERE id = ?",
            (pi.get("id"), now, rid),
        )
        # Auto-unfreeze the camper's (already-provisioned) student account so
        # they can sign in right away. Matched by email like everywhere else.
        unfroze = False
        student_email = (reg.get("studentEmail") or "").strip() or None
        if student_email:
            try:
                clause, param = _email_clause(student_email)
                row = g.db.execute(
                    f"SELECT id FROM students WHERE {clause}", (param,)
                ).fetchone()
                if row:
                    g.db.execute("UPDATE students SET frozen = 0 WHERE id = ?",
                                 (row["id"],))
                    unfroze = True
            except Exception:  # noqa: BLE001 — payment already captured; never fail here
                pass
        # Paid in full by card → send the "you're all set" welcome email (with
        # all the camp logistics), NOT the payment-required email. Best-effort.
        try:
            camper_name = f"{reg.get('firstName') or ''} {reg.get('lastName') or ''}".strip()
            _send_camp_welcome(student_email, camper_name,
                               (reg.get("parentEmail") or "").strip() or None)
        except Exception:  # noqa: BLE001
            pass
        return jsonify(ok=True, paymentMethod="card", paymentId=pi.get("id"),
                       amount=amount_due, accountActive=unfroze)

    @app.route("/api/settings/student-cap", methods=["GET"])
    def settings_student_cap():
        cap = _student_cap()
        active = g.db.execute(
            "SELECT COUNT(*) AS n FROM registrations WHERE COALESCE(waitlisted, 0) = 0"
        ).fetchone()["n"]
        waitlisted = g.db.execute(
            "SELECT COUNT(*) AS n FROM registrations WHERE COALESCE(waitlisted, 0) = 1"
        ).fetchone()["n"]
        # In-person seats are the capped ones; online is unlimited.
        in_person = g.db.execute(
            "SELECT COUNT(*) AS n FROM registrations "
            "WHERE COALESCE(waitlisted, 0) = 0 AND COALESCE(deliveryMode, 'in_person') != 'online'"
        ).fetchone()["n"]
        online = g.db.execute(
            "SELECT COUNT(*) AS n FROM registrations "
            "WHERE COALESCE(waitlisted, 0) = 0 AND deliveryMode = 'online'"
        ).fetchone()["n"]
        return jsonify(ok=True, cap=cap, active=active, waitlisted=waitlisted,
                       inPersonActive=in_person, onlineActive=online)

    @app.route("/api/stats/enrolled", methods=["GET"])
    def stats_enrolled():
        # Public: how many campers are confirmed (account unfrozen) out of cap.
        enrolled = g.db.execute(
            "SELECT COUNT(*) AS n FROM students WHERE COALESCE(frozen, 1) = 0"
        ).fetchone()["n"]
        return jsonify(ok=True, enrolled=enrolled, cap=_student_cap())

    @app.route("/api/stats/campers", methods=["GET"])
    def stats_campers():
        # Public camper breakdown: total registered, paid (unfrozen) and
        # registered-but-not-yet-paid (frozen). paid + unpaid == total.
        total = g.db.execute(
            "SELECT COUNT(*) AS n FROM students"
        ).fetchone()["n"]
        paid = g.db.execute(
            "SELECT COUNT(*) AS n FROM students WHERE COALESCE(frozen, 1) = 0"
        ).fetchone()["n"]
        return jsonify(ok=True, total=total, paid=paid, unpaid=total - paid,
                       cap=_student_cap())

    @app.route("/api/stats/call-number", methods=["GET"])
    def stats_call_number():
        # Public: today's open-call number (set by staff or via Discord, up to
        # 10 days ahead). Blank number => the contact page shows the fallback.
        today = _eastern_today_str()
        e = _call_schedule().get(today) or {}
        return jsonify(ok=True, date=today,
                       number=(e.get("number") or "").strip(),
                       name=(e.get("name") or "").strip())

    @app.route("/api/admin/call-schedule", methods=["GET"])
    @require_admin
    def admin_call_schedule_get():
        return jsonify(ok=True, days=_call_schedule_days())

    @app.route("/api/admin/call-schedule", methods=["POST"])
    @require_admin
    def admin_call_schedule_set():
        d = request.get_json(silent=True) or {}
        days = d.get("days")
        if not isinstance(days, list):
            return jsonify(ok=False, error="days must be a list"), 400
        sched = _call_schedule_upsert(days)
        return jsonify(ok=True, count=len(sched))

    @app.route("/api/bot/call-schedule", methods=["GET"])
    @require_bot
    def bot_call_schedule_get():
        # Discord bot: same 10-day view, used by /oncall.
        return jsonify(ok=True, days=_call_schedule_days())

    @app.route("/api/bot/call-schedule/claim", methods=["POST"])
    @require_bot
    def bot_call_schedule_claim():
        # Discord bot: one person signs up (or clears) a day. A blank number
        # clears that day's assignment.
        import re
        d = request.get_json(silent=True) or {}
        date_str = str(d.get("date") or "").strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
            return jsonify(ok=False, error="bad or missing date"), 400
        _call_schedule_upsert([{
            "date": date_str,
            "number": d.get("number") or "",
            "name": d.get("name") or "",
            "discord_id": d.get("discord_id") or "",
        }])
        return jsonify(ok=True)

    @app.route("/api/settings/registrations-frozen", methods=["GET"])
    def settings_registrations_frozen():
        return jsonify(
            ok=True,
            frozen=(_meta_get("registrations_frozen", "0") == "1"),
            reason=(_meta_get("registrations_frozen_reason", "") or ""),
        )

    @app.route("/api/admin/settings/registrations-frozen", methods=["POST"])
    @require_admin
    def settings_registrations_frozen_set():
        d = request.get_json(silent=True) or {}
        frozen = bool(d.get("frozen"))
        # Reason is only meaningful while frozen; clear it on reopen.
        reason = (d.get("reason") or "").strip()
        if len(reason) > 1000:
            reason = reason[:1000]
        _meta_set("registrations_frozen", "1" if frozen else "0")
        _meta_set("registrations_frozen_reason", reason if frozen else "")
        return jsonify(ok=True, frozen=frozen, reason=reason if frozen else "")

    @app.route("/api/settings/reg-tier", methods=["GET"])
    def settings_reg_tier():
        t = _reg_tier()
        info = REG_TIERS[t]
        free = _camp_free()
        # In FREE mode the effective price is $0 regardless of tier, and we
        # surface `free` so the registration page can hide the payment step and
        # swap its price copy.
        return jsonify(ok=True, tier=t, open=info["open"],
                       price=0 if free else info["price"],
                       label=("Free" if free else info["label"]),
                       free=free)

    @app.route("/api/settings/camp-free", methods=["GET"])
    def settings_camp_free():
        """Public: is camp free right now? Used to toggle price copy site-wide."""
        free = _camp_free()
        return jsonify(ok=True, free=free,
                       price=0 if free else REG_TIERS["open"]["price"])

    @app.route("/api/admin/settings/camp-free", methods=["POST"])
    @require_admin
    def settings_camp_free_set():
        data = request.get_json(silent=True) or {}
        free = bool(data.get("free"))
        _meta_set("camp_free", "1" if free else "0")
        return jsonify(ok=True, free=free,
                       price=0 if free else REG_TIERS["open"]["price"])

    @app.route("/api/admin/settings/reg-tier", methods=["POST"])
    @require_admin
    def settings_reg_tier_set():
        d = request.get_json(silent=True) or {}
        t = (d.get("tier") or "").strip()
        # Legacy values collapse to the single open tier.
        if t in ("early", "normal", "late"):
            t = "open"
        if t not in REG_TIERS:
            return jsonify(ok=False, error="tier must be: open or closed"), 400
        _meta_set("reg_tier", t)
        info = REG_TIERS[t]
        return jsonify(ok=True, tier=t, open=info["open"],
                       price=info["price"], label=info["label"])

    # ── Discount codes ──────────────────────────────────────────────
    @app.route("/api/discount/crane", methods=["POST"])
    def discount_crane():
        """Mint a fresh single-use 10% 'crane' code for an About-page visitor."""
        label = _gen_crane_code(g.db)
        if not label:
            return jsonify(ok=False, error="Couldn't generate a code — try again."), 500
        return jsonify(ok=True, code=label, percent=10)

    @app.route("/api/discount/check", methods=["GET"])
    def discount_check():
        """Validate a code without consuming it (used for the live preview on
        the registration form). Returns whether it's currently usable + the %."""
        row = _lookup_discount(g.db, request.args.get("code", ""))
        if not row:
            return jsonify(ok=True, valid=False)
        return jsonify(ok=True, valid=True,
                       percent=round(row["percent"] * 100),
                       label=row["label"])

    # ── Trail mini-game config ──────────────────────────────────────
    # JSON blob in meta["trail_config"]:
    #   { "background": "data:image/...|null",
    #     "stages":      { "<idx>": { "logImage": "data:...|null",
    #                                 "waypoints": [ {x,y}, ... 5 ] },
    #                       ... } }
    # All fields are optional — the trail page falls back to its
    # built-in CSS background, CSS log art, and a straight-line path.
    @app.route("/api/trail-config", methods=["GET"])
    def trail_config_get():
        raw = _meta_get("trail_config", "{}") or "{}"
        try:
            cfg = json.loads(raw)
            if not isinstance(cfg, dict):
                cfg = {}
        except (TypeError, ValueError):
            cfg = {}
        return jsonify(ok=True, config=cfg)

    @app.route("/api/admin/trail-config", methods=["PUT"])
    @require_admin
    def trail_config_set():
        d = request.get_json(silent=True) or {}
        cfg = d.get("config")
        if not isinstance(cfg, dict):
            return jsonify(ok=False, error="config must be a JSON object"), 400
        # Clamp the size — refuse blobs > 8 MB so a runaway admin
        # upload can't fill the SQLite file with one row.
        blob = json.dumps(cfg)
        if len(blob) > 8 * 1024 * 1024:
            return jsonify(ok=False, error="Trail config exceeds 8 MB. Try smaller images."), 413
        _meta_set("trail_config", blob)
        return jsonify(ok=True)

    # ── Sponsors ─────────────────────────────────────────────────────
    # Admin-managed list of title sponsors, rendered as the floating
    # bubbles on /sponsors/sponsors.html. JSON blob in meta["sponsors"]:
    #   { "sponsors": [ {
    #       id, name, tagline, logo:"data:…|''", description,
    #       location: { label, url },
    #       staff:  [ { photo:"data:…|''", name, position }, … up to 2 ],
    #       images: [ "data:…", … up to 4 ],
    #       board:      [ "#hex", … up to 3 ],   // modal card; ≥2 ⇒ gradient TL→BR
    #       border:     "#hex|''",                // single border colour
    #       background: [ "#hex", … up to 2 ],   // page fade + backdrop; ≥2 ⇒ gradient
    #     }, … ] }
    # All fields optional — the page falls back to sensible defaults.
    @app.route("/api/sponsors", methods=["GET"])
    def sponsors_get():
        raw = _meta_get("sponsors", "{}") or "{}"
        try:
            cfg = json.loads(raw)
            if not isinstance(cfg, dict):
                cfg = {}
        except (TypeError, ValueError):
            cfg = {}
        if not isinstance(cfg.get("sponsors"), list):
            cfg["sponsors"] = []
        # Light mode (?light=1): drop the heavy base64 carousel images + staff
        # photos so the floating-bubble field loads fast even on weak devices.
        # Only the logo + colours (what a bubble needs) are kept; the full
        # record is fetched lazily when a sponsor is actually opened.
        if request.args.get("light"):
            keep = ("id", "name", "tagline", "logo",
                    "signature", "border", "board", "background", "bubbleBg")
            slim = [{k: s.get(k) for k in keep}
                    for s in cfg["sponsors"] if isinstance(s, dict)]
            return jsonify(ok=True, config={"sponsors": slim})
        return jsonify(ok=True, config=cfg)

    @app.route("/api/admin/sponsors", methods=["PUT"])
    @require_admin
    def sponsors_set():
        d = request.get_json(silent=True) or {}
        cfg = d.get("config")
        if not isinstance(cfg, dict) or not isinstance(cfg.get("sponsors"), list):
            return jsonify(ok=False, error="config must be {sponsors: [...]}"), 400
        # Clamp the size — refuse blobs > 8 MB so a runaway admin upload
        # (base64 logos/photos) can't fill the SQLite file with one row.
        blob = json.dumps(cfg)
        if len(blob) > SPONSORS_MAX_BYTES:
            mb = SPONSORS_MAX_BYTES // (1024 * 1024)
            return jsonify(ok=False, error=f"Sponsors data exceeds {mb} MB. Try smaller images."), 413
        _meta_set("sponsors", blob)
        return jsonify(ok=True)

    @app.route("/api/admin/settings/student-cap", methods=["POST"])
    @require_admin
    def settings_student_cap_set():
        d = request.get_json(silent=True) or {}
        try:
            new_cap = int(d.get("cap") or 0)
        except (TypeError, ValueError):
            return jsonify(ok=False, error="Cap must be a positive integer."), 400
        if new_cap < 1:
            return jsonify(ok=False, error="Cap must be at least 1."), 400
        with g.db:
            _meta_set("student_cap", str(new_cap))
            # Re-balance the waitlist:  the first <cap> registrations by
            # createdAt are active; the rest are waitlisted.
            rows = g.db.execute(
                "SELECT id FROM registrations ORDER BY createdAt ASC"
            ).fetchall()
            for i, r in enumerate(rows):
                wl = 0 if i < new_cap else 1
                g.db.execute(
                    "UPDATE registrations SET waitlisted = ? WHERE id = ?",
                    (wl, r["id"]),
                )
        return jsonify(ok=True, cap=new_cap)

    # ── Class-points contribution from a student's private points ──
    CLASS_CONTRIBUTION_THRESHOLD = 200

    # DISABLED: class points have no meaning now the camp is a single
    # group. The endpoint returns 410 so the old student-portal UI (if a
    # cached copy is still live) fails loudly instead of silently eating
    # a camper's points.
    @app.route("/api/students/me/contribute-class-points", methods=["POST"])
    @require_student
    def contribute_class_points():
        return jsonify(ok=False, error="Class points are disabled — the camp runs as one group."), 410

    # @app.route("/api/students/me/contribute-class-points", methods=["POST"])
    # @require_student
    # @block_when_frozen
    # def contribute_class_points():
    #     d = request.get_json(silent=True) or {}
    #     try:
    #         amount = int(d.get("amount") or 0)
    #     except (TypeError, ValueError):
    #         amount = 0
    #     if amount <= 0:
    #         return jsonify(ok=False, error="Enter a positive amount to contribute."), 400
    #     sid = g.session["studentId"]
    #     with g.db:
    #         row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
    #         if not row:
    #             return jsonify(ok=False, error="Student not found."), 404
    #         if not row["classId"]:
    #             return jsonify(ok=False, error="You're not assigned to a class yet."), 400
    #         stats  = {**default_stats(), **json.loads(row["stats"] or "{}")}
    #         extras = json.loads(row["extras"] or "{}")
    #         cur_pp = stats.get("privatePoints", 0)
    #         if cur_pp < amount:
    #             return jsonify(ok=False, error=f"You only have {cur_pp} points."), 400
    #         stats["privatePoints"] = cur_pp - amount
    #         bucket = int(extras.get("classContribution") or 0) + amount
    #         class_pts_to_bank = bucket // CLASS_CONTRIBUTION_THRESHOLD
    #         extras["classContribution"] = bucket % CLASS_CONTRIBUTION_THRESHOLD
    #         g.db.execute(
    #             "UPDATE students SET stats = ?, extras = ? WHERE id = ?",
    #             (json.dumps(stats), json.dumps(extras), sid),
    #         )
    #         from_name = _full_name(row)
    #         cls_name = (row["className"] or "your class")
    #         if class_pts_to_bank > 0:
    #             cls_row = g.db.execute(
    #                 "SELECT * FROM classes WHERE id = ?", (row["classId"],),
    #             ).fetchone()
    #             if cls_row:
    #                 new_bank = float(cls_row["classBank"] or 0) + class_pts_to_bank
    #                 g.db.execute(
    #                     "UPDATE classes SET classBank = ?, bankLastUpdate = ? WHERE id = ?",
    #                     (new_bank, int(time.time() * 1000), cls_row["id"]),
    #                 )
    #                 _log_tx(type="class_bank_deposit", scope="class",
    #                         subjectId=cls_row["id"], subjectName=cls_row["name"],
    #                         relatedId=sid, relatedName=from_name,
    #                         amount=class_pts_to_bank,
    #                         description=f"🔒 +{class_pts_to_bank} class pt from {from_name} contribution · locked until exams")
    #         _log_tx(type="class_contribute", scope="student",
    #                 subjectId=sid, subjectName=from_name,
    #                 amount=-amount,
    #                 description=f"Contributed {amount} pts toward class points · bucket {extras['classContribution']}/{CLASS_CONTRIBUTION_THRESHOLD}"
    #                             + (f" · cashed out {class_pts_to_bank} class pt to {cls_name} bank" if class_pts_to_bank else ""))
    #     return jsonify(ok=True, data={
    #         "contributed": amount,
    #         "bucket": extras["classContribution"],
    #         "threshold": CLASS_CONTRIBUTION_THRESHOLD,
    #         "classPointsBanked": class_pts_to_bank,
    #         "newPrivatePoints": stats["privatePoints"],
    #     })


    @app.route("/api/admin/registrations", methods=["GET"])
    @require_admin
    def admin_list_registrations():
        rows = g.db.execute(
            "SELECT * FROM registrations ORDER BY createdAt DESC"
        ).fetchall()
        out = []
        for r in rows:
            d = _decrypt_registration(dict(r))
            d["consentPhoto"] = bool(d.get("consentPhoto"))
            try:
                d["pickupPeople"] = json.loads(d.get("pickupPeople") or "[]")
            except (TypeError, ValueError):
                d["pickupPeople"] = []
            # Staff referral code used (25% off): resolve to the staff name.
            if d.get("referredByCode"):
                srow = g.db.execute("SELECT name FROM staff WHERE referralCode = ?",
                                    (d["referredByCode"],)).fetchone()
                d["referredByStaff"] = srow["name"] if srow else None
            else:
                d["referredByStaff"] = None
            # Sponsor-location payment: resolve the store name and the actual
            # amount the family pays (base − 5%).
            loc = d.get("sponsorLocation")
            d["sponsorLocationName"] = _sponsor_loc_name(loc)
            d["sponsorLocationUrl"] = _sponsor_loc_url(loc)
            if d.get("paymentMethod") == "sponsor" and d.get("amountDue") is not None:
                d["sponsorAmount"] = round(d["amountDue"] * (1.0 - SPONSOR_PAY_DISCOUNT), 2)
            else:
                d["sponsorAmount"] = None
            out.append(d)
        return jsonify(ok=True, data=out)

    @app.route("/api/admin/registrations/export", methods=["POST"])
    @require_admin
    def admin_export_registrations():
        """Bulk CSV export of every registration, decrypted server-side — the
        "pull" in encrypt-until-we-pull. High-risk (a portable file with all
        PII + camp passwords), so on top of the admin session it is gated
        behind HG_EXPORT_PASSPHRASE. Without the passphrase, no file."""
        import csv as _csv
        import io as _io
        import datetime as _dt

        body = request.get_json(silent=True) or {}
        supplied = (body.get("passphrase") or "").strip()
        if not EXPORT_PASSPHRASE:
            return jsonify(ok=False,
                           error="Export passphrase is not configured on the server."), 503
        if not supplied or not hmac.compare_digest(supplied, EXPORT_PASSPHRASE):
            return jsonify(ok=False, error="Incorrect export passphrase."), 403

        cols = [
            ("createdAt", "Submitted (UTC)"), ("waitlisted", "Status"),
            ("firstName", "First name"), ("lastName", "Last name"), ("dob", "DOB"),
            ("studentEmail", "Student email"), ("password", "Camp password"),
            ("school", "School"), ("parentFirst", "Parent first"),
            ("parentLast", "Parent last"), ("relationship", "Relationship"),
            ("parentPhone", "Parent phone"), ("parentEmail", "Parent email"),
            ("emerg1Name", "Emergency contact"),
            ("emerg1Relationship", "Emergency relationship"),
            ("emerg1Phone", "Emergency phone"), ("pickupPeople", "Authorized pickup"),
            ("consentPhoto", "Photo consent"), ("campusRoaming", "Campus breaks"),
            ("deliveryMode", "Attends"), ("medicalInfo", "Medical"),
            ("discountCode", "Discount"), ("amountDue", "Amount due"),
            ("paymentMethod", "Payment"), ("sponsorLocation", "Sponsor store"),
            ("referrerEmail", "Referred by (email)"),
            ("hobbies", "Hobbies"), ("whyJoin", "Why join"),
        ]

        def _fmt_pickup(raw):
            try:
                items = json.loads(raw or "[]")
            except (TypeError, ValueError):
                return ""
            parts = []
            for it in (items or []):
                bits = []
                if it.get("name"):         bits.append(it["name"])
                if it.get("relationship"): bits.append("(" + it["relationship"] + ")")
                if it.get("phone"):        bits.append(it["phone"])
                parts.append(" ".join(bits))
            return " | ".join(p for p in parts if p)

        rows = g.db.execute(
            "SELECT * FROM registrations ORDER BY createdAt DESC"
        ).fetchall()
        buf = _io.StringIO()
        w = _csv.writer(buf)
        w.writerow([label for _, label in cols])
        for r in rows:
            d = _decrypt_registration(dict(r))   # decrypt PII for the export
            line = []
            for key, _ in cols:
                v = d.get(key)
                if key == "createdAt":
                    v = (_dt.datetime.utcfromtimestamp(v).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                         if v else "")
                elif key == "consentPhoto":
                    v = "yes" if v else "no"
                elif key == "campusRoaming":
                    v = "free roam" if v == "allow" else ("supervised" if v == "no" else "")
                elif key == "deliveryMode":
                    v = {"online": "online", "in_person": "in person"}.get(v, "")
                elif key == "waitlisted":
                    v = "waitlist" if v else "active"
                elif key == "paymentMethod":
                    v = (_sponsor_loc_name(d.get("sponsorLocation")) if v == "sponsor"
                         else (v or "e_transfer"))
                elif key == "sponsorLocation":
                    v = _sponsor_loc_name(v)
                elif key == "pickupPeople":
                    v = _fmt_pickup(d.get("pickupPeople"))
                line.append("" if v is None else v)
            w.writerow(line)

        ts = _dt.datetime.utcnow().strftime("%Y-%m-%d")
        resp = make_response(buf.getvalue())
        resp.headers["Content-Type"] = "text/csv; charset=utf-8"
        resp.headers["Content-Disposition"] = \
            f'attachment; filename="highergrade-registrations-{ts}.csv"'
        return resp

    @app.route("/api/admin/registrations/<rid>", methods=["DELETE"])
    @require_admin
    def admin_delete_registration(rid):
        """Drop a camper from the registration list (e.g. they withdrew).

        This fully removes them: the registration row, the auto-provisioned
        student-portal account tied to the same email (plus any live
        sessions), and then re-balances the waitlist so a freed-up spot
        promotes the next person in line.

        Pass ?keepAccount=1 to delete only the registration row and leave
        the student-portal account intact.
        """
        reg = g.db.execute(
            "SELECT id, studentEmail FROM registrations WHERE id = ?", (rid,)
        ).fetchone()
        if not reg:
            return jsonify(ok=False, error="Registration not found"), 404

        keep_account = request.args.get("keepAccount") in ("1", "true", "yes")
        email = (crypto.dec(reg["studentEmail"]) or "").strip().lower()
        removed_account = False
        promoted = 0
        with g.db:
            g.db.execute("DELETE FROM registrations WHERE id = ?", (rid,))
            # Remove the matching auto-provisioned student account(s) so a
            # withdrawn camper can no longer sign in. _reconcile_camper_email
            # deletes every camper for this email once no registration backs
            # it (orphan rule), or trims duplicates if another reg remains.
            if email and not keep_account:
                removed_account = _reconcile_camper_email(email) > 0
            # Re-balance the waitlist: first <cap> registrations by createdAt
            # are active, the rest waitlisted. Deleting an active camper thus
            # promotes the oldest waitlisted registration into the open spot.
            cap = _student_cap()
            rows = g.db.execute(
                "SELECT id, waitlisted FROM registrations ORDER BY createdAt ASC"
            ).fetchall()
            for i, r in enumerate(rows):
                wl = 0 if i < cap else 1
                if int(r["waitlisted"] or 0) == 1 and wl == 0:
                    promoted += 1
                g.db.execute(
                    "UPDATE registrations SET waitlisted = ? WHERE id = ?",
                    (wl, r["id"]),
                )
        return jsonify(ok=True, removedAccount=removed_account, promoted=promoted)

    # ── Discord-bot integration ────────────────────────────────────
    # All /api/bot/* endpoints expect Authorization: Bearer <BOT_TOKEN>.
    def _student_summary_for_bot(row, link):
        if not row:
            return None
        s = row_to_student(row)
        stats = s.get("stats") or {}
        return {
            "studentId":   s["id"],
            "discordId":   link["discordId"] if link else None,
            "guildId":     (link["guildId"] if link else None),
            "fullName":    _full_name(row),
            "firstName":   s.get("firstName"),
            "lastName":    s.get("lastName"),
            # "className":   s.get("className"),   # classes disabled
            "className":   "",
            "privatePoints":     int(stats.get("privatePoints") or 0),
            "totalPointsEarned": int(stats.get("totalPointsEarned") or 0),
            "roles":       s.get("roles") or [],
            # Payment not yet confirmed. The bot refuses to verify a frozen
            # account and strips its managed roles on the next sync pass, so
            # freezing on the website also revokes Discord access.
            "frozen":      bool(s.get("frozen")),
        }

    @app.route("/api/bot/link", methods=["POST"])
    @require_bot
    def bot_link():
        d = request.get_json(silent=True) or {}
        discord_id = (d.get("discordId") or "").strip()
        guild_id   = (d.get("guildId") or "").strip() or None
        email = (d.get("email") or "").strip().lower()
        pwd   = d.get("password")
        if not discord_id or not email or pwd is None:
            return jsonify(ok=False, error="discordId, email, and password are required."), 400
        if _is_reserved_email(email):
            return jsonify(ok=False, error="That email isn't a real camp account."), 401
        clause, param = _email_clause(email)
        row = g.db.execute(
            f"SELECT * FROM students WHERE {clause}", (param,),
        ).fetchone()
        if not row or not hmac.compare_digest(str(crypto.dec(row["password"]) or ""), str(pwd)):
            return jsonify(ok=False, error="No matching camp account."), 401
        # Frozen = registered but the payment hasn't been confirmed yet. Those
        # accounts don't get Discord access at all — verification is the gate
        # that unlocks the private channels, so it has to hold the same line
        # the website does.
        if row["frozen"]:
            return jsonify(
                ok=False,
                frozen=True,
                error=("Your camp account is still pending payment confirmation. "
                       "Once our staff confirms your payment you'll be able to verify here."),
            ), 403
        sid = row["id"]
        # Refuse to silently overwrite an existing claim. If a different
        # Discord user already linked this student, the bot must clear
        # the old link first (admin tooling).
        existing = g.db.execute(
            "SELECT * FROM discord_links WHERE studentId = ? AND discordId != ?",
            (sid, discord_id),
        ).fetchone()
        if existing:
            return jsonify(ok=False, error="That camp account is already linked to another Discord user."), 409
        g.db.execute(
            """INSERT INTO discord_links (discordId, studentId, guildId, linkedAt)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(discordId) DO UPDATE SET
                 studentId = excluded.studentId,
                 guildId   = excluded.guildId,
                 linkedAt  = excluded.linkedAt""",
            (discord_id, sid, guild_id, int(time.time())),
        )
        link = {"discordId": discord_id, "guildId": guild_id}
        return jsonify(ok=True, data=_student_summary_for_bot(row, link))

    @app.route("/api/bot/unlink", methods=["POST"])
    @require_bot
    def bot_unlink():
        d = request.get_json(silent=True) or {}
        discord_id = (d.get("discordId") or "").strip()
        if not discord_id:
            return jsonify(ok=False, error="discordId is required."), 400
        g.db.execute("DELETE FROM discord_links WHERE discordId = ?", (discord_id,))
        return jsonify(ok=True)

    @app.route("/api/bot/students", methods=["GET"])
    @require_bot
    def bot_students():
        guild_id = (request.args.get("guildId") or "").strip() or None
        if guild_id:
            rows = g.db.execute(
                """SELECT s.*, dl.discordId AS dl_discordId, dl.guildId AS dl_guildId
                   FROM discord_links dl
                   JOIN students s ON s.id = dl.studentId
                   WHERE dl.guildId = ?""",
                (guild_id,),
            ).fetchall()
        else:
            rows = g.db.execute(
                """SELECT s.*, dl.discordId AS dl_discordId, dl.guildId AS dl_guildId
                   FROM discord_links dl
                   JOIN students s ON s.id = dl.studentId"""
            ).fetchall()
        out = []
        for r in rows:
            link = {"discordId": r["dl_discordId"], "guildId": r["dl_guildId"]}
            summary = _student_summary_for_bot(r, link)
            if summary:
                out.append(summary)
        return jsonify(ok=True, data=out)

    @app.route("/api/bot/me", methods=["GET"])
    @require_bot
    def bot_me():
        discord_id = (request.args.get("discordId") or "").strip()
        if not discord_id:
            return jsonify(ok=False, error="discordId is required."), 400
        link = g.db.execute(
            "SELECT * FROM discord_links WHERE discordId = ?", (discord_id,)
        ).fetchone()
        if not link:
            return jsonify(ok=True, data=None)
        row = g.db.execute(
            "SELECT * FROM students WHERE id = ?", (link["studentId"],)
        ).fetchone()
        return jsonify(ok=True, data=_student_summary_for_bot(row, dict(link)))

    def _norm_code(code, *, ignore_case, ignore_spaces):
        """Fold a passcode according to a chest's matching rules."""
        s = str(code or "")
        if ignore_spaces:
            s = re.sub(r"\s+", "", s)
        else:
            s = s.strip()
        if ignore_case:
            s = s.casefold()
        return s

    def _code_matches(chest, typed):
        """Does `typed` open this chest, under the chest's own rules?"""
        ic = bool(chest["ignoreCase"])
        isp = bool(chest["ignoreSpaces"])
        want = _norm_code(chest["code"], ignore_case=ic, ignore_spaces=isp)
        got  = _norm_code(typed,        ignore_case=ic, ignore_spaces=isp)
        return bool(want) and want == got

    @app.route("/api/bot/feedback-channel", methods=["GET", "POST"])
    @require_bot
    def bot_feedback_channel():
        """The private channel used to reach a camper whose DMs are shut.
        Remembered per camper so one channel is reused rather than a new one
        appearing for every marked quiz."""
        if request.method == "GET":
            discord_id = (request.args.get("discordId") or "").strip()
        else:
            discord_id = ((request.get_json(silent=True) or {}).get("discordId") or "").strip()
        if not discord_id:
            return jsonify(ok=False, error="discordId is required."), 400
        link = g.db.execute("SELECT * FROM discord_links WHERE discordId = ?",
                            (discord_id,)).fetchone()
        if not link:
            return jsonify(ok=False, unverified=True,
                           error="That Discord user isn't linked to a camp account."), 404
        if request.method == "POST":
            cid = ((request.get_json(silent=True) or {}).get("channelId") or "").strip() or None
            g.db.execute("UPDATE discord_links SET feedbackChannelId = ? WHERE discordId = ?",
                         (cid, discord_id))
            return jsonify(ok=True, data={"channelId": cid})
        return jsonify(ok=True, data={"channelId": link["feedbackChannelId"]})

    def _clean_attachments(raw):
        """Normalize the bot's attachment manifest to [{name, size}, …].
        Returns (list, error). Anything unusable is an error rather than a
        silent drop, so a bug in the bot shows up instead of losing files
        from the record."""
        if raw in (None, ""):
            return [], None
        if not isinstance(raw, list):
            return None, "attachments must be a list."
        if len(raw) > CHEST_MAX_ATTACHMENTS:
            return None, f"At most {CHEST_MAX_ATTACHMENTS} attachments per chest."
        out = []
        for item in raw:
            if isinstance(item, str):
                item = {"name": item}
            if not isinstance(item, dict):
                return None, "Each attachment must be an object with a name."
            name = (item.get("name") or "").strip()
            if not name:
                return None, "Each attachment needs a name."
            try:
                size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            out.append({"name": name[:200], "size": max(0, size)})
        return out, None

    @app.route("/api/bot/chests", methods=["POST"])
    @require_bot
    def bot_chest_create():
        d = request.get_json(silent=True) or {}
        guild_id = (d.get("guildId") or "").strip()
        code     = (d.get("code") or "").strip()
        # roleId is optional — a chest can be points-only, granting nothing.
        role_id  = (d.get("roleId") or "").strip()
        if not guild_id or not code:
            return jsonify(ok=False, error="guildId and code are required."), 400
        ignore_case   = bool(d.get("ignoreCase"))
        ignore_spaces = bool(d.get("ignoreSpaces"))
        # Collision check has to respect the fuzzy rules: with ignore_caps
        # on, "GOLD" and "gold" are the same passcode, and a claim couldn't
        # tell which chest was meant. Compare under the union of both
        # chests' rules so the looser one wins.
        for row in g.db.execute(
            "SELECT * FROM discord_chests WHERE guildId = ?", (guild_id,),
        ).fetchall():
            ic  = ignore_case   or bool(row["ignoreCase"])
            isp = ignore_spaces or bool(row["ignoreSpaces"])
            if _norm_code(row["code"], ignore_case=ic, ignore_spaces=isp) == \
               _norm_code(code,        ignore_case=ic, ignore_spaces=isp):
                return jsonify(
                    ok=False,
                    error=(f"A chest with the passcode `{row['code']}` already exists here "
                           f"— with these matching rules the two would be "
                           f"indistinguishable."),
                ), 409
        # points: 0 disables the reward. maxClaims: None/0/blank = unlimited.
        try:
            points = int(d.get("points") if d.get("points") is not None else 50)
        except (TypeError, ValueError):
            return jsonify(ok=False, error="points must be a whole number."), 400
        if points < 0:
            return jsonify(ok=False, error="points can't be negative."), 400
        if points > 100000:
            return jsonify(ok=False, error="points looks suspiciously large."), 400
        raw_max = d.get("maxClaims")
        max_claims = None
        if raw_max not in (None, "", 0, "0"):
            try:
                max_claims = int(raw_max)
            except (TypeError, ValueError):
                return jsonify(ok=False, error="maxClaims must be a whole number."), 400
            if max_claims < 1:
                return jsonify(ok=False, error="maxClaims must be at least 1 (leave blank for unlimited)."), 400
        # Early-bird bonus. Both halves must be present for it to mean
        # anything, so a lone value is rejected rather than silently ignored.
        try:
            bonus_points = int(d.get("bonusPoints") or 0)
            bonus_count  = int(d.get("bonusCount") or 0)
        except (TypeError, ValueError):
            return jsonify(ok=False, error="Bonus points and count must be whole numbers."), 400
        if bonus_points < 0 or bonus_count < 0:
            return jsonify(ok=False, error="Bonus points and count can't be negative."), 400
        if bonus_points > 100000:
            return jsonify(ok=False, error="Bonus points look suspiciously large."), 400
        if bool(bonus_points) != bool(bonus_count):
            return jsonify(
                ok=False,
                error="An early-bird bonus needs both an amount and how many "
                      "people get it — e.g. 100 x 3.",
            ), 400
        if bonus_count and max_claims is not None and bonus_count > max_claims:
            return jsonify(
                ok=False,
                error=f"The bonus covers {bonus_count} openers but the chest only "
                      f"allows {max_claims}.",
            ), 400
        # Per-person wait between passcode attempts. Omitted = the standard
        # 15s; 0 = no wait at all.
        raw_cd = d.get("cooldownSeconds")
        try:
            cooldown = (CHEST_DEFAULT_COOLDOWN if raw_cd in (None, "")
                        else int(raw_cd))
        except (TypeError, ValueError):
            return jsonify(ok=False, error="cooldownSeconds must be a whole number."), 400
        if cooldown < 0:
            return jsonify(ok=False, error="cooldownSeconds can't be negative."), 400
        if cooldown > CHEST_MAX_COOLDOWN:
            return jsonify(
                ok=False,
                error=f"cooldownSeconds can be at most {CHEST_MAX_COOLDOWN} "
                      f"({CHEST_MAX_COOLDOWN // 60} minutes).",
            ), 400

        attachments, err = _clean_attachments(d.get("attachments"))
        if err:
            return jsonify(ok=False, error=err), 400

        cid = "chest-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(3)
        g.db.execute(
            """INSERT INTO discord_chests
               (id, code, description, imageUrl, roleId, roleName, guildId,
                channelId, messageId, createdBy, createdAt, claimedBy,
                points, maxClaims, removeRoleId, removeRoleName,
                bonusPoints, bonusCount, ignoreCase, ignoreSpaces,
                cooldownSeconds, attachments)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cid, code,
                (d.get("description") or "").strip() or None,
                (d.get("imageUrl") or "").strip() or None,
                role_id,
                (d.get("roleName") or "").strip() or None,
                guild_id,
                (d.get("channelId") or "").strip() or None,
                (d.get("messageId") or "").strip() or None,
                (d.get("createdBy") or "").strip() or None,
                int(time.time()),
                points, max_claims,
                (d.get("removeRoleId") or "").strip() or None,
                (d.get("removeRoleName") or "").strip() or None,
                bonus_points, bonus_count,
                1 if ignore_case else 0, 1 if ignore_spaces else 0,
                cooldown, json.dumps(attachments),
            ),
        )
        return jsonify(ok=True, data={"id": cid, "points": points, "maxClaims": max_claims,
                                      "bonusPoints": bonus_points, "bonusCount": bonus_count,
                                      "ignoreCase": ignore_case,
                                      "ignoreSpaces": ignore_spaces,
                                      "cooldownSeconds": cooldown,
                                      "attachments": attachments})

    @app.route("/api/bot/chests/<cid>/attachments", methods=["POST"])
    @require_bot
    def bot_chest_add_attachments(cid):
        """Append files to a chest's record. The bot has already re-uploaded
        them into the channel — this just keeps the tally honest so
        /chest-list can say how many are hanging off each chest."""
        d = request.get_json(silent=True) or {}
        extra, err = _clean_attachments(d.get("attachments"))
        if err:
            return jsonify(ok=False, error=err), 400
        if not extra:
            return jsonify(ok=False, error="No attachments supplied."), 400
        row = g.db.execute("SELECT attachments FROM discord_chests WHERE id = ?",
                           (cid,)).fetchone()
        if not row:
            return jsonify(ok=False, error="That chest no longer exists."), 404
        try:
            have = json.loads(row["attachments"] or "[]")
            if not isinstance(have, list):
                have = []
        except Exception:  # noqa: BLE001
            have = []
        merged = (have + extra)[:CHEST_MAX_ATTACHMENTS]
        g.db.execute("UPDATE discord_chests SET attachments = ? WHERE id = ?",
                     (json.dumps(merged), cid))
        return jsonify(ok=True, data={"attachments": merged, "count": len(merged),
                                      "added": len(merged) - len(have)})

    @app.route("/api/bot/chests/<cid>/message", methods=["POST"])
    @require_bot
    def bot_chest_set_message(cid):
        """Bot calls this after posting the chest's public message so the
        record knows which channel + message to point back at."""
        d = request.get_json(silent=True) or {}
        channel_id = (d.get("channelId") or "").strip() or None
        message_id = (d.get("messageId") or "").strip() or None
        g.db.execute(
            "UPDATE discord_chests SET channelId = ?, messageId = ? WHERE id = ?",
            (channel_id, message_id, cid),
        )
        return jsonify(ok=True)

    @app.route("/api/bot/chests", methods=["GET"])
    @require_bot
    def bot_chest_list():
        guild_id = (request.args.get("guildId") or "").strip()
        if not guild_id:
            return jsonify(ok=False, error="guildId is required."), 400
        rows = g.db.execute(
            "SELECT * FROM discord_chests WHERE guildId = ? ORDER BY createdAt DESC",
            (guild_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                claimed = json.loads(d.get("claimedBy") or "[]")
            except Exception:  # noqa: BLE001
                claimed = []
            d["claimedBy"]    = claimed
            d["claimedCount"] = len(claimed)
            try:
                files = json.loads(d.get("attachments") or "[]")
            except Exception:  # noqa: BLE001
                files = []
            d["attachments"]      = files if isinstance(files, list) else []
            d["attachmentCount"]  = len(d["attachments"])
            # None when uncapped, so the bot can render "3 / ∞" vs "3 / 10".
            d["remaining"] = (None if d.get("maxClaims") is None
                              else max(0, int(d["maxClaims"]) - len(claimed)))
            out.append(d)
        return jsonify(ok=True, data=out)

    @app.route("/api/bot/chests/<cid>", methods=["DELETE"])
    @require_bot
    def bot_chest_delete(cid):
        g.db.execute("DELETE FROM discord_chests WHERE id = ?", (cid,))
        # Don't leave the cooldown clocks behind for a chest that's gone.
        g.db.execute("DELETE FROM discord_chest_attempts WHERE chestId = ?", (cid,))
        return jsonify(ok=True)

    @app.route("/api/bot/perms", methods=["GET"])
    @require_bot
    def bot_perms_list():
        guild_id = (request.args.get("guildId") or "").strip()
        if not guild_id:
            return jsonify(ok=False, error="guildId required."), 400
        rows = g.db.execute(
            "SELECT * FROM discord_command_perms WHERE guildId = ? ORDER BY command, createdAt",
            (guild_id,),
        ).fetchall()
        return jsonify(ok=True, data=[dict(r) for r in rows])

    @app.route("/api/bot/perms", methods=["POST"])
    @require_bot
    def bot_perms_grant():
        d = request.get_json(silent=True) or {}
        guild_id  = (d.get("guildId") or "").strip()
        command   = (d.get("command") or "").strip()
        role_id   = (d.get("roleId") or "").strip()
        role_name = (d.get("roleName") or "").strip() or None
        created_by = (d.get("createdBy") or "").strip() or None
        if not guild_id or not command or not role_id:
            return jsonify(ok=False, error="guildId, command, roleId required."), 400
        existing = g.db.execute(
            "SELECT id FROM discord_command_perms WHERE guildId = ? AND command = ? AND roleId = ?",
            (guild_id, command, role_id),
        ).fetchone()
        if existing:
            return jsonify(ok=True, data={"id": existing["id"], "alreadyExisted": True})
        pid = "perm-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(3)
        g.db.execute(
            """INSERT INTO discord_command_perms
               (id, guildId, command, roleId, roleName, createdBy, createdAt)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (pid, guild_id, command, role_id, role_name, created_by, int(time.time())),
        )
        return jsonify(ok=True, data={"id": pid, "alreadyExisted": False})

    # — Homework hand-in / marking —
    def _hw_row(r, *, include_feedback=True):
        """Shape a submission for the bot. Decrypts the free-text fields and
        resolves the student's name from the students table rather than
        keeping a second plaintext copy of it here."""
        if r is None:
            return None
        d = dict(r)
        d["notes"] = crypto.dec(d.get("notes")) or ""
        if include_feedback:
            d["feedback"] = crypto.dec(d.get("feedback")) or ""
        else:
            d.pop("feedback", None)
        for key in ("attachments", "returnedFiles"):
            try:
                d[key] = json.loads(d.get(key) or "[]")
            except Exception:  # noqa: BLE001
                d[key] = []
        d["studentName"] = ""
        if d.get("studentId"):
            srow = g.db.execute("SELECT * FROM students WHERE id = ?",
                                (d["studentId"],)).fetchone()
            if srow:
                d["studentName"] = _full_name(srow)
        return d

    @app.route("/api/bot/homework", methods=["POST"])
    @require_bot
    def bot_homework_create():
        d = request.get_json(silent=True) or {}
        guild_id   = (d.get("guildId") or "").strip()
        discord_id = (d.get("discordId") or "").strip()
        if not guild_id or not discord_id:
            return jsonify(ok=False, error="guildId and discordId are required."), 400
        # Only a verified camper can hand work in — otherwise there's no
        # name to label the submission with and nobody to send feedback to.
        link = g.db.execute("SELECT * FROM discord_links WHERE discordId = ?",
                            (discord_id,)).fetchone()
        if not link:
            return jsonify(ok=False, unverified=True,
                           error="Verify your camp account before handing work in."), 403
        srow = g.db.execute("SELECT * FROM students WHERE id = ?",
                            (link["studentId"],)).fetchone()
        if not srow:
            return jsonify(ok=False, unverified=True,
                           error="Your linked camp account no longer exists."), 403
        atts = d.get("attachments")
        if not isinstance(atts, list):
            atts = []
        hid = "hw-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(3)
        now = int(time.time())
        g.db.execute(
            """INSERT INTO homework_submissions
               (id, guildId, discordId, studentId, title, notes, attachments,
                originChannelId, submittedAt, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (hid, guild_id, discord_id, srow["id"],
             (d.get("title") or "").strip()[:200] or None,
             crypto.enc((d.get("notes") or "").strip()[:2000]) or None,
             json.dumps(atts[:10]),
             (d.get("originChannelId") or "").strip() or None, now),
        )
        return jsonify(ok=True, data={
            "id": hid, "studentId": srow["id"], "studentName": _full_name(srow),
            "submittedAt": now,
        })

    @app.route("/api/bot/homework/<hid>/message", methods=["POST"])
    @require_bot
    def bot_homework_set_message(hid):
        d = request.get_json(silent=True) or {}
        g.db.execute(
            "UPDATE homework_submissions SET channelId = ?, messageId = ? WHERE id = ?",
            ((d.get("channelId") or "").strip() or None,
             (d.get("messageId") or "").strip() or None, hid),
        )
        return jsonify(ok=True)

    @app.route("/api/bot/homework/<hid>", methods=["GET"])
    @require_bot
    def bot_homework_get(hid):
        row = g.db.execute("SELECT * FROM homework_submissions WHERE id = ?",
                           (hid,)).fetchone()
        if not row:
            return jsonify(ok=False, error="No such submission."), 404
        return jsonify(ok=True, data=_hw_row(row))

    # Quiz scoring. Every mark on the test is worth this many game points,
    # doubled when the camper scores at or above the bonus threshold.
    POINTS_PER_MARK   = 6
    DOUBLE_AT_PERCENT = 95.0

    def _parse_score(raw):
        """Read a score written as a fraction. Returns
        (earned, total, normalised_text, error).

        The text is handed back exactly as typed — the fraction is what the
        camper sees, never a decimal. "N/A" (or blank) means no quiz was
        handed in, which scores nothing rather than zero."""
        s = (raw or "").strip()
        if not s or s.upper().replace(".", "").replace(" ", "") in ("NA", "N/A".replace("/", "")):
            return None, None, "N/A", ""
        if s.upper() in ("N/A", "NA", "N.A."):
            return None, None, "N/A", ""
        m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*$", s)
        if not m:
            return None, None, "", (
                f"Write the score as a fraction like `18/20`, or `N/A` if they "
                f"didn't hand a quiz in — got `{s}`.")
        earned, total = float(m.group(1)), float(m.group(2))
        if total <= 0:
            return None, None, "", "The bottom of the fraction has to be above 0."
        if earned > total:
            return None, None, "", (
                f"`{s}` scores more than the quiz is out of. Check the fraction.")
        return earned, total, s, ""

    def _score_to_points(earned, total):
        """Marks × 6, doubled at 95% or better. Returns (points, doubled)."""
        if earned is None or not total:
            return 0, False
        pct = (earned / total) * 100.0
        pts = int(round(earned * POINTS_PER_MARK))
        doubled = pct >= DOUBLE_AT_PERCENT
        return (pts * 2 if doubled else pts), doubled

    @app.route("/api/bot/homework/<hid>/mark", methods=["POST"])
    @require_bot
    def bot_homework_mark(hid):
        d = request.get_json(silent=True) or {}
        feedback = (d.get("feedback") or "").strip()
        if not feedback:
            return jsonify(ok=False, error="Feedback can't be empty."), 400
        row = g.db.execute("SELECT * FROM homework_submissions WHERE id = ?",
                           (hid,)).fetchone()
        if not row:
            return jsonify(ok=False, error="No such submission."), 404

        earned, total, score_text, err = _parse_score(d.get("grade") or d.get("score"))
        if err:
            return jsonify(ok=False, error=err), 400
        points, doubled = _score_to_points(earned, total)

        marker = (d.get("markedByName") or "").strip()[:100] or None
        # Re-marking: take back exactly what the previous mark paid before
        # paying the new figure, so a corrected score never stacks.
        previous = int(row["pointsAwarded"] or 0)
        reversed_pts = 0
        if previous:
            body, st = _award_points(
                row["studentId"], -previous,
                f"Re-mark correction: {row['title'] or 'submission'}",
                marker or "staff")
            if st == 200:
                reversed_pts = int((body.get("data") or {}).get("applied") or 0)

        awarded = 0
        award_note = None
        if points > 0 and row["studentId"]:
            body, st = _award_points(
                row["studentId"], points,
                f"📝 {row['title'] or 'Quiz'} · {score_text}"
                + (f" · {POINTS_PER_MARK} pts/mark, DOUBLED for {DOUBLE_AT_PERCENT:g}%+"
                   if doubled else f" · {POINTS_PER_MARK} pts/mark"),
                marker or "staff")
            if st == 200:
                awarded = int((body.get("data") or {}).get("applied") or 0)
            else:
                award_note = body.get("error")

        files = d.get("returnedFiles")
        if not isinstance(files, list):
            files = []

        g.db.execute(
            """UPDATE homework_submissions
               SET status = 'marked', grade = ?, feedback = ?, markedBy = ?,
                   markedByName = ?, markedAt = ?, scoreEarned = ?, scoreTotal = ?,
                   pointsAwarded = ?, returnedFiles = ?
               WHERE id = ?""",
            (score_text or None,
             crypto.enc(feedback[:4000]),
             (d.get("markedBy") or "").strip() or None,
             marker, int(time.time()),
             int(earned) if earned is not None and float(earned).is_integer() else earned,
             int(total) if total is not None and float(total).is_integer() else total,
             awarded, json.dumps(files[:10]), hid),
        )
        fresh = g.db.execute("SELECT * FROM homework_submissions WHERE id = ?",
                             (hid,)).fetchone()
        out = _hw_row(fresh)
        out.update({
            "pointsAwarded": awarded, "doubled": doubled,
            "reversed": reversed_pts, "awardNote": award_note,
            "pointsPerMark": POINTS_PER_MARK, "doubleAt": DOUBLE_AT_PERCENT,
            "submitted": score_text != "N/A",
        })
        return jsonify(ok=True, data=out)

    @app.route("/api/bot/homework/<hid>/dm", methods=["POST"])
    @require_bot
    def bot_homework_dm(hid):
        """Record whether the feedback DM actually reached the student."""
        d = request.get_json(silent=True) or {}
        g.db.execute("UPDATE homework_submissions SET dmDelivered = ? WHERE id = ?",
                     (1 if d.get("delivered") else 0, hid))
        return jsonify(ok=True)

    @app.route("/api/bot/homework", methods=["GET"])
    @require_bot
    def bot_homework_list():
        guild_id = (request.args.get("guildId") or "").strip()
        if not guild_id:
            return jsonify(ok=False, error="guildId is required."), 400
        status = (request.args.get("status") or "").strip()
        discord_id = (request.args.get("discordId") or "").strip()
        sql = "SELECT * FROM homework_submissions WHERE guildId = ?"
        params = [guild_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        if discord_id:
            sql += " AND discordId = ?"
            params.append(discord_id)
        sql += " ORDER BY submittedAt DESC LIMIT 100"
        rows = g.db.execute(sql, tuple(params)).fetchall()
        return jsonify(ok=True, data=[_hw_row(r) for r in rows])

    # — Per-role command parameter locks —
    # A lock pins one parameter of one command to a fixed value for holders
    # of one role. The bot owns the notion of which command/field pairs are
    # lockable and how ties between roles are broken; the server just stores
    # the rows.
    @app.route("/api/bot/locks", methods=["GET"])
    @require_bot
    def bot_locks_list():
        guild_id = (request.args.get("guildId") or "").strip()
        if not guild_id:
            return jsonify(ok=False, error="guildId is required."), 400
        rows = g.db.execute(
            "SELECT * FROM discord_command_locks WHERE guildId = ? ORDER BY command, field, createdAt",
            (guild_id,),
        ).fetchall()
        return jsonify(ok=True, data=[dict(r) for r in rows])

    @app.route("/api/bot/locks", methods=["POST"])
    @require_bot
    def bot_lock_set():
        d = request.get_json(silent=True) or {}
        guild_id = (d.get("guildId") or "").strip()
        command  = (d.get("command") or "").strip()
        role_id  = (d.get("roleId") or "").strip()
        field    = (d.get("field") or "").strip()
        if not guild_id or not command or not role_id or not field:
            return jsonify(ok=False, error="guildId, command, roleId, and field are required."), 400
        value = d.get("value")
        value = None if value is None else str(value)
        lid = "lock-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(3)
        g.db.execute(
            """INSERT INTO discord_command_locks
               (id, guildId, command, roleId, roleName, field, value, createdBy, createdAt)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(guildId, command, roleId, field) DO UPDATE SET
                 value    = excluded.value,
                 roleName = excluded.roleName,
                 createdBy = excluded.createdBy,
                 createdAt = excluded.createdAt""",
            (lid, guild_id, command, role_id,
             (d.get("roleName") or "").strip() or None,
             field, value,
             (d.get("createdBy") or "").strip() or None,
             int(time.time())),
        )
        return jsonify(ok=True)

    @app.route("/api/bot/locks/remove", methods=["POST"])
    @require_bot
    def bot_lock_remove():
        d = request.get_json(silent=True) or {}
        guild_id = (d.get("guildId") or "").strip()
        command  = (d.get("command") or "").strip()
        role_id  = (d.get("roleId") or "").strip()
        field    = (d.get("field") or "").strip()
        if not guild_id or not command or not role_id or not field:
            return jsonify(ok=False, error="guildId, command, roleId, and field are required."), 400
        cur = g.db.execute(
            """DELETE FROM discord_command_locks
               WHERE guildId = ? AND command = ? AND roleId = ? AND field = ?""",
            (guild_id, command, role_id, field),
        )
        return jsonify(ok=True, data={"removed": cur.rowcount})

    # — Role-mirror blocklist (Discord roles that should NOT propagate
    #   to the website's per-student role list) —
    @app.route("/api/bot/role-mirror/blocklist", methods=["GET"])
    @require_bot
    def bot_role_mirror_list():
        guild_id = (request.args.get("guildId") or "").strip()
        if not guild_id:
            return jsonify(ok=False, error="guildId required."), 400
        rows = g.db.execute(
            "SELECT * FROM discord_role_blocklist WHERE guildId = ? ORDER BY createdAt",
            (guild_id,),
        ).fetchall()
        return jsonify(ok=True, data=[dict(r) for r in rows])

    @app.route("/api/bot/role-mirror/blocklist", methods=["POST"])
    @require_bot
    def bot_role_mirror_add():
        d = request.get_json(silent=True) or {}
        guild_id  = (d.get("guildId") or "").strip()
        role_id   = (d.get("roleId") or "").strip()
        role_name = (d.get("roleName") or "").strip() or None
        added_by  = (d.get("addedBy") or "").strip() or None
        if not guild_id or not role_id:
            return jsonify(ok=False, error="guildId and roleId required."), 400
        existing = g.db.execute(
            "SELECT id FROM discord_role_blocklist WHERE guildId = ? AND roleId = ?",
            (guild_id, role_id),
        ).fetchone()
        if existing:
            return jsonify(ok=True, data={"id": existing["id"], "alreadyExisted": True})
        bid = "drb-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(3)
        g.db.execute(
            """INSERT INTO discord_role_blocklist
               (id, guildId, roleId, roleName, addedBy, createdAt)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (bid, guild_id, role_id, role_name, added_by, int(time.time())),
        )
        return jsonify(ok=True, data={"id": bid, "alreadyExisted": False})

    @app.route("/api/bot/role-mirror/blocklist/remove", methods=["POST"])
    @require_bot
    def bot_role_mirror_remove():
        d = request.get_json(silent=True) or {}
        guild_id = (d.get("guildId") or "").strip()
        role_id  = (d.get("roleId") or "").strip()
        if not guild_id or not role_id:
            return jsonify(ok=False, error="guildId and roleId required."), 400
        cur = g.db.execute(
            "DELETE FROM discord_role_blocklist WHERE guildId = ? AND roleId = ?",
            (guild_id, role_id),
        )
        return jsonify(ok=True, data={"removed": cur.rowcount})

    # — Push the union of a member's mirrorable Discord roles into the
    #   student's `roles` field (additive: never removes, just merges in).
    @app.route("/api/bot/students/<sid>/mirror-discord-roles", methods=["POST"])
    @require_bot
    def bot_mirror_discord_roles(sid):
        d = request.get_json(silent=True) or {}
        names_in = d.get("roleNames") or []
        if not isinstance(names_in, list):
            return jsonify(ok=False, error="roleNames must be a list."), 400
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            # Look up every existing camp role once so we can match by
            # normalized name without N queries.
            all_roles = g.db.execute("SELECT * FROM roles").fetchall()
            by_norm = {_normalize_role_name(r["name"]): dict(r) for r in all_roles}

            existing_role_ids = json.loads(row["roles"] or "[]")
            new_role_ids = list(existing_role_ids)
            created = []
            matched = []
            for raw in names_in:
                name = (raw or "").strip()
                if not name:
                    continue
                norm = _normalize_role_name(name)
                if not norm:
                    continue
                if norm in by_norm:
                    rid = by_norm[norm]["id"]
                    matched.append(rid)
                else:
                    # Auto-create a camp role for this Discord role.
                    rid = "role-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(2)
                    g.db.execute(
                        """INSERT INTO roles (id, name, icon, color, description, special)
                           VALUES (?, ?, '🏅', '#3B82F6', 'Auto-created from Discord role.', 0)""",
                        (rid, name),
                    )
                    by_norm[norm] = {"id": rid, "name": name}
                    created.append(rid)
                if rid not in new_role_ids:
                    new_role_ids.append(rid)
            if new_role_ids != existing_role_ids:
                g.db.execute(
                    "UPDATE students SET roles = ? WHERE id = ?",
                    (json.dumps(new_role_ids), sid),
                )
        return jsonify(ok=True, data={
            "added":   list(set(new_role_ids) - set(existing_role_ids)),
            "created": created,
            "matched": matched,
            "studentRoles": new_role_ids,
        })

    @app.route("/api/bot/perms/revoke", methods=["POST"])
    @require_bot
    def bot_perms_revoke():
        d = request.get_json(silent=True) or {}
        guild_id = (d.get("guildId") or "").strip()
        command  = (d.get("command") or "").strip()
        role_id  = (d.get("roleId") or "").strip()
        if not guild_id or not command or not role_id:
            return jsonify(ok=False, error="guildId, command, roleId required."), 400
        cur = g.db.execute(
            "DELETE FROM discord_command_perms WHERE guildId = ? AND command = ? AND roleId = ?",
            (guild_id, command, role_id),
        )
        return jsonify(ok=True, data={"removed": cur.rowcount})

    @app.route("/api/bot/chests/claim", methods=["POST"])
    @require_bot
    def bot_chest_claim():
        d = request.get_json(silent=True) or {}
        guild_id   = (d.get("guildId") or "").strip()
        discord_id = (d.get("discordId") or "").strip()
        code       = (d.get("code") or "").strip()
        chest_id   = (d.get("chestId") or "").strip() or None
        if not guild_id or not discord_id or not code:
            return jsonify(ok=False, error="guildId, discordId, and code are required."), 400
        with g.db:
            # If chestId is supplied (button-driven flow), validate that
            # the typed code matches the SPECIFIC chest the user clicked.
            # Otherwise fall back to the older "any chest with this code".
            # Matching is done in Python rather than SQL because each chest
            # carries its own case/space rules.
            #
            # `target` is the chest the attempt was AIMED at, which exists
            # even when the code is wrong — that's what the cooldown is
            # keyed on, so a wrong guess still starts the clock.
            if chest_id:
                target = g.db.execute(
                    "SELECT * FROM discord_chests WHERE id = ? AND guildId = ?",
                    (chest_id, guild_id),
                ).fetchone()
                chest = target if (target and _code_matches(target, code)) else None
            else:
                target = next(
                    (row for row in g.db.execute(
                        "SELECT * FROM discord_chests WHERE guildId = ?", (guild_id,),
                    ).fetchall() if _code_matches(row, code)),
                    None,
                )
                chest = target

            try:
                already_claimed = target is not None and discord_id in json.loads(
                    target["claimedBy"] or "[]")
            except Exception:  # noqa: BLE001
                already_claimed = False

            # Re-opening a chest you've already opened is a harmless no-op,
            # so it isn't rate-limited. Everything else is.
            if not already_claimed:
                if target is not None:
                    key = target["id"]
                    cooldown = int(target["cooldownSeconds"] or 0)
                else:
                    # A /unlock guess that hit nothing. Rate-limit it per
                    # guild at the strictest cooldown in play, otherwise
                    # /unlock would be a free brute-force channel.
                    key = f"{guild_id}:*"
                    row = g.db.execute(
                        "SELECT MAX(cooldownSeconds) AS m FROM discord_chests WHERE guildId = ?",
                        (guild_id,),
                    ).fetchone()
                    cooldown = int((row["m"] if row else 0) or 0)
                if cooldown > 0:
                    now = int(time.time())
                    prev = g.db.execute(
                        "SELECT at FROM discord_chest_attempts WHERE chestId = ? AND discordId = ?",
                        (key, discord_id),
                    ).fetchone()
                    if prev:
                        waited = now - int(prev["at"] or 0)
                        if 0 <= waited < cooldown:
                            return jsonify(
                                ok=False, cooldown=True,
                                retryAfter=cooldown - waited,
                                cooldownSeconds=cooldown,
                                error=(f"Too fast — wait {cooldown - waited}s before "
                                       f"trying another passcode."),
                            ), 429
                    g.db.execute(
                        "INSERT INTO discord_chest_attempts (chestId, discordId, at)"
                        " VALUES (?, ?, ?)"
                        " ON CONFLICT(chestId, discordId) DO UPDATE SET at = excluded.at",
                        (key, discord_id, now),
                    )

            if not chest:
                return jsonify(ok=False, error="That code doesn't open this chest."), 404
            try:
                claimed = json.loads(chest["claimedBy"] or "[]")
            except Exception:  # noqa: BLE001
                claimed = []

            # A chest is open to everyone who knows the code — the only two
            # limits are "once per person" and the optional total-claims cap.
            already = discord_id in claimed
            max_claims = chest["maxClaims"]
            awarded = 0
            award_skipped = None   # why points weren't given, for the bot's message

            if already:
                # Re-opening is a no-op: no second role grant side effects,
                # and crucially no second points payout.
                return jsonify(ok=True, data={
                    "chestId":     chest["id"],
                    "roleId":      chest["roleId"],
                    "roleName":    chest["roleName"],
                    "description": chest["description"],
                    "alreadyClaimed": True,
                    "points":      int(chest["points"] or 0),
                    "awarded":     0,
                    "claimedCount": len(claimed),
                    "maxClaims":   max_claims,
                    "removeRoleId":   chest["removeRoleId"],
                    "removeRoleName": chest["removeRoleName"],
                })

            if max_claims is not None and len(claimed) >= int(max_claims):
                return jsonify(
                    ok=False, exhausted=True,
                    error=("This chest is empty — it's already been opened the maximum "
                           f"{int(max_claims)} time(s)."),
                ), 409

            claimed.append(discord_id)
            g.db.execute(
                "UPDATE discord_chests SET claimedBy = ? WHERE id = ?",
                (json.dumps(claimed), chest["id"]),
            )

            # Where this person landed in the queue: 1 = first to open it.
            position = len(claimed)
            bonus_count  = int(chest["bonusCount"] or 0)
            bonus_points = int(chest["bonusPoints"] or 0)
            bonus = bonus_points if (bonus_count and position <= bonus_count) else 0

            # Points go to the camp account behind this Discord user. An
            # unverified user still gets the role — they just can't be paid,
            # since there's no account to pay into.
            points = int(chest["points"] or 0) + bonus
            if points > 0:
                link = g.db.execute(
                    "SELECT * FROM discord_links WHERE discordId = ?", (discord_id,)
                ).fetchone()
                if not link:
                    award_skipped = "unverified"
                else:
                    srow = g.db.execute(
                        "SELECT * FROM students WHERE id = ?", (link["studentId"],)
                    ).fetchone()
                    if not srow:
                        award_skipped = "unverified"
                    elif srow["frozen"]:
                        award_skipped = "frozen"
                    else:
                        stats = {**default_stats(), **json.loads(srow["stats"] or "{}")}
                        stats["privatePoints"]     = stats.get("privatePoints", 0) + points
                        stats["totalPointsEarned"] = stats.get("totalPointsEarned", 0) + points
                        g.db.execute("UPDATE students SET stats = ? WHERE id = ?",
                                     (json.dumps(stats), srow["id"]))
                        awarded = points
                        _log_tx(type="earn", scope="student", subjectId=srow["id"],
                                subjectName=_full_name(srow), amount=points,
                                description=f"🗝 Chest unlocked (`{chest['code']}`) · +{points} pts"
                                            + (f" (incl. +{bonus} early-bird bonus, "
                                               f"#{position} of {bonus_count})" if bonus else ""))

        return jsonify(ok=True, data={
            "chestId":     chest["id"],
            "roleId":      chest["roleId"],
            "roleName":    chest["roleName"],
            "description": chest["description"],
            "alreadyClaimed": False,
            "points":      int(chest["points"] or 0),
            "awarded":     awarded,
            "awardSkipped": award_skipped,
            "claimedCount": len(claimed),
            "maxClaims":   max_claims,
            "removeRoleId":   chest["removeRoleId"],
            "removeRoleName": chest["removeRoleName"],
            "position":    position,
            "bonus":       bonus,
            "bonusPoints": bonus_points,
            "bonusCount":  bonus_count,
        })

    # ── Camp reset (scoped) ────────────────────────────────────────
    # Wipes students, classes, and roles (re-seeding the default roles
    # afterwards). Preserves staff, transactions, base-stat categories,
    # hints, and admin sessions so the camp can keep its history while
    # starting fresh on student-side state.
    @app.route("/api/admin/reset", methods=["POST"])
    @require_admin
    def admin_reset():
        """Reset the camp game-board WITHOUT erasing student records.
        Wipes every student's points, roles, base-stat counts, and the
        camp-wide stashes (transactions bank, Vulgar Vault, class
        points + bank). The students themselves — names, emails,
        class assignments, passwords — stay put."""
        zero_stats = json.dumps(default_stats())
        zero_base  = json.dumps({})
        empty_roles = json.dumps([])
        with g.db:
            # Per-student state — keep identity, blank out gameboard.
            g.db.execute(
                "UPDATE students SET stats = ?, roles = ?, baseStats = ?, extras = '{}'",
                (zero_stats, empty_roles, zero_base),
            )
            # Camp-wide stashes back to zero.
            _meta_set("transactions_bank", "0")
            _meta_set("vulgar_vault", "0")
            # Reset class points + bank but keep the class records so
            # student.classId references stay intact.
            g.db.execute(
                "UPDATE classes SET classPoints = 0, classBank = 0, bankLastUpdate = NULL"
            )
            # Wipe the transaction log too — it's a record of points
            # that no longer exist.
            g.db.execute("DELETE FROM transactions")
            # End any active student sessions so the next sign-in pulls
            # the freshly-zeroed stats.
            g.db.execute("DELETE FROM sessions WHERE kind = 'student'")
            # Re-seed default roles in case any were renamed/deleted —
            # students hold no role refs after the reset, but the
            # role definitions need to exist for future awards.
            from db import _seed
            _seed(g.db)
        return jsonify(ok=True)


# ── Helpers ───────────────────────────────────────────────────────────

KNOWN_STUDENT_COLS = {
    "id", "firstName", "lastName", "studentEmail", "password",
    "parentEmail", "phone", "school", "grade",
    "classId", "className", "registeredAt",
    "frozen", "inventory", "equipped",
}

def _normalize_student(raw):
    if not raw.get("id"):
        raw["id"] = "student-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(3)
    s = {k: raw.get(k) for k in KNOWN_STUDENT_COLS}
    # `frozen` defaults to 1 (frozen until staff confirms payment). Existing
    # rows that flow back through admin-side bulk saves carry their current
    # state through unchanged.
    if "frozen" in raw and raw.get("frozen") is not None:
        s["frozen"] = 1 if raw.get("frozen") else 0
    else:
        s["frozen"] = 1
    s["stats"]     = json.dumps({**default_stats(), **(raw.get("stats") or {})})
    s["roles"]     = json.dumps(raw.get("roles") or [])
    s["baseStats"] = json.dumps(raw.get("baseStats") or {})
    # These arrive decoded from row_to_student on a round-trip through the
    # admin bulk save, so re-encode rather than storing a Python list.
    s["inventory"] = json.dumps(raw.get("inventory") or [])
    s["equipped"]  = json.dumps(raw.get("equipped") or {})
    extras = {k: v for k, v in raw.items()
              if k not in KNOWN_STUDENT_COLS and k not in {"stats", "roles", "baseStats"}}
    s["extras"]    = json.dumps(extras)
    return s


def _insert_student(s):
    s = dict(s)  # never mutate the caller's dict
    # Blind index from the plaintext email BEFORE we encrypt it.
    s["emailIndex"] = crypto.blind(s.get("studentEmail")) if crypto.enabled() else None
    for f in STUDENT_ENC_FIELDS:
        if f in s:
            s[f] = crypto.enc(s[f])
    g.db.execute(
        """INSERT OR REPLACE INTO students
           (id, firstName, lastName, studentEmail, password, parentEmail, phone,
            school, grade, classId, className, registeredAt,
            stats, roles, baseStats, extras, emailIndex, frozen,
            inventory, equipped)
           VALUES
           (:id, :firstName, :lastName, :studentEmail, :password, :parentEmail, :phone,
            :school, :grade, :classId, :className, :registeredAt,
            :stats, :roles, :baseStats, :extras, :emailIndex, :frozen,
            :inventory, :equipped)""",
        s,
    )


def _ensure_playtest_student():
    """Return the "HGT TEST" students row, creating it if it's missing.

    It's a completely ordinary camper record — that's the whole point, so
    playtest mode exercises the real student code paths — except that it
    has a fixed id, an unguessable password on a reserved email (so it can
    never be signed into from the login form), and starts unfrozen so the
    payment overlay doesn't block the very thing we're trying to look at.
    Recreated on demand, so an admin reset or bulk student replace that
    drops it is harmless."""
    row = g.db.execute(
        "SELECT * FROM students WHERE id = ?", (PLAYTEST_STUDENT_ID,),
    ).fetchone()
    if row:
        return row
    _insert_student(_normalize_student({
        "id":           PLAYTEST_STUDENT_ID,
        "firstName":    "HGT",
        "lastName":     "TEST",
        "studentEmail": PLAYTEST_EMAIL,
        "password":     secrets.token_urlsafe(32),
        "school":       "Playtest account",
        "grade":        "—",
        "registeredAt": str(int(time.time())),
        "frozen":       0,
    }))
    return g.db.execute(
        "SELECT * FROM students WHERE id = ?", (PLAYTEST_STUDENT_ID,),
    ).fetchone()


def _email_clause(email):
    """(where_fragment, param) to match a students/registrations row by email.
    Uses the keyed blind index when encryption is on (studentEmail is then
    ciphertext and can't be SQL-normalized), else the plaintext column."""
    if crypto.enabled():
        return "emailIndex = ?", crypto.blind(email)
    return "LOWER(TRIM(studentEmail)) = ?", (email or "").strip().lower()


def _encrypt_registration(reg):
    """Encrypt the PII columns of a registration dict in place, then return it."""
    for f in REGISTRATION_ENC_FIELDS:
        if reg.get(f) is not None:
            reg[f] = crypto.enc(reg[f])
    return reg


def _decrypt_registration(d):
    """Decrypt the PII columns of a registration dict (built from dict(row))."""
    for f in REGISTRATION_ENC_FIELDS:
        if d.get(f) is not None:
            d[f] = crypto.dec(d[f])
    return d


def _full_name(row):
    # firstName/lastName may be encrypted at rest — decrypt before use so
    # transaction logs etc. never capture ciphertext.
    fn = (crypto.dec(row["firstName"]) or "").strip()
    ln = (crypto.dec(row["lastName"])  or "").strip()
    full = (fn + " " + ln).strip()
    return full or "(no name)"


def _log_tx(**entry):
    entry.setdefault("at", int(time.time() * 1000))
    entry.setdefault("id", "tx-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(3))
    g.db.execute(
        """INSERT INTO transactions
           (id, at, type, scope, subjectId, subjectName, relatedId, relatedName, amount, description)
           VALUES (:id, :at, :type, :scope, :subjectId, :subjectName, :relatedId, :relatedName, :amount, :description)""",
        {
            "id": entry["id"], "at": entry["at"], "type": entry["type"],
            "scope": entry.get("scope"), "subjectId": entry.get("subjectId"),
            "subjectName": entry.get("subjectName"),
            "relatedId": entry.get("relatedId"),
            "relatedName": entry.get("relatedName"),
            "amount": entry.get("amount") or 0,
            "description": entry.get("description") or "",
        },
    )
    # Trim to TX_MAX
    cur = g.db.execute("SELECT COUNT(*) AS n FROM transactions")
    n = cur.fetchone()["n"]
    if n > TX_MAX:
        g.db.execute(
            "DELETE FROM transactions WHERE id IN (SELECT id FROM transactions ORDER BY at ASC LIMIT ?)",
            (n - TX_MAX,),
        )


app = create_app()


def _register_static(app):
    """LOCAL-DEV ONLY — serve the static site from the same origin as the API.

    In production Caddy serves the HTML/CSS/JS itself and reverse-proxies only
    /api/* to this app (see deploy/Caddyfile), so these routes are never hit.
    But when you run this file directly for local testing there is no Caddy, so
    the browser loads the pages from wherever you opened them and their
    ``fetch('/api/...')`` calls resolve against THAT origin — never reaching
    Flask. The symptom is the whole API silently failing: registration shows
    "closed" (the reg-tier fetch falls back to closed), sign-in never
    authenticates, and staff profiles come up empty. Serving the pages from
    this same process fixes all three at once."""
    from flask import send_from_directory

    site_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def _static_site(path):
        # A real /api/* 404 that fell through to here — keep it JSON.
        if path.startswith("api/"):
            return jsonify(ok=False, error="Not found"), 404
        # Never serve dot-files/dot-dirs (.git, .env, …) or traversal.
        if any(seg.startswith(".") for seg in path.split("/") if seg):
            return jsonify(ok=False, error="Not found"), 404
        candidate = os.path.normpath(os.path.join(site_root, path))
        if candidate != site_root and not candidate.startswith(site_root + os.sep):
            return jsonify(ok=False, error="Not found"), 404
        if path and os.path.isfile(candidate):
            return send_from_directory(site_root, path)
        if path and os.path.isdir(candidate) and os.path.isfile(os.path.join(candidate, "index.html")):
            return send_from_directory(candidate, "index.html")
        return send_from_directory(site_root, "index.html")


if __name__ == "__main__":
    # ── Local development convenience (never runs under gunicorn/prod) ──
    # 1. Serve the static pages same-origin so browser /api/* calls reach us.
    # 2. Drop the Secure flag on the session cookie so login persists over
    #    plain http://localhost (a Secure cookie is silently dropped on HTTP,
    #    which would make sign-in appear to "work" then instantly log out).
    SECURE_COOKIE = False
    _register_static(app)
    # Default to 5001, not 5000: on macOS the AirPlay Receiver (Control
    # Center) squats on port 5000 and answers every request with 403, which
    # silently breaks the whole API in local dev. Override with PORT=… if you
    # want a different one. Production is unaffected (gunicorn binds 5000 on
    # the VM, where there's no AirPlay).
    port = int(os.environ.get("PORT", "5001"))
    print(f"HigherGrade dev server → http://localhost:{port}/index.html")
    print(f"  DB: {os.environ.get('HIGHERGRADE_DB', '(local dev.db)')}")
    app.run(host="127.0.0.1", port=port, debug=True)
