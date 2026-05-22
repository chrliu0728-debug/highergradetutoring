"""HigherGrade Tutoring API — Flask + SQLite.

Replaces the browser-localStorage data layer with a real server-side store.
All endpoints are mounted under /api/* so Caddy can reverse-proxy just that
prefix while continuing to serve the static site directly.
"""

import hashlib
import hmac
import json
import os
import secrets
import smtplib
import time
from email.message import EmailMessage
from functools import wraps

from flask import Flask, g, jsonify, request, make_response

from db import (
    connect, init_db,
    row_to_student, row_to_class, row_to_role,
    row_to_basestat, row_to_tx, row_to_staff,
)

ADMIN_PASSCODE = os.environ.get("HIGHERGRADE_ADMIN_PASSCODE", "HigherGrade Tutoring")

# Shared secret the Discord bot sends as `Authorization: Bearer <token>`.
# Generate with `python3 -c "import secrets; print(secrets.token_hex(32))"`
# and put it in /etc/highergrade.env (BOT_API_TOKEN=…) on the VM, then
# the same value into the bot's environment.
BOT_API_TOKEN = os.environ.get("HIGHERGRADE_BOT_TOKEN", "")

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
]
LUCK_COST              = 800
CLICKER_RATE           = 100
TRANSFER_KEEP_RATIO    = 0.5
SPIDER_THRESHOLD       = 20
CLASS_POINT_TO_INDIV   = 10
CLASS_BANK_DAILY_RATE  = 0.05
TX_MAX                 = 2000
MAZEWIZ_ROLE_ID        = "mazewiz"
MONEY_TREE_ROLE_ID     = "money_tree"
CLICKER_ROLE_ID        = "clicker"
CRANE_ROLE_ID          = "crane"
CRANE_GLOBAL_LIMIT     = None    # unlimited — any student who completes the claim flow gets one
DOOR_MAZE_LENGTH       = 310
MONEY_TREE_COST        = 6000

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

# ── Reserved easter-egg email ────────────────────────────────────────
# burntout@gmail.com is a hidden door (the bedroom scene at /bedroom.html),
# not a real camper. Block it everywhere a student record could be
# created or matched so it can never accidentally end up in the students
# table — nor be wiped by an admin reset.
RESERVED_STUDENT_EMAILS = {"burntout@gmail.com"}


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
MANUAL_DAILY_CAP       = 50              # max manual-clicker pts per UTC day
AUTO_DAILY_CAP_PER_LV  = 14              # max auto-clicker pts per level per day


def default_stats():
    return {
        "privatePoints": 0, "totalPointsEarned": 0, "luck": 0,
        "perfectScores": 0, "classAnswers": 0, "pointExchanges": 0,
        "bathroomVisits": 0, "badWords": 0,
        "clickerClicks": 0, "clickerPointsEarned": 0, "spiderShown": False,
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

def send_email(to, subject, body, reply_to=None):
    """Best-effort SMTP send. If credentials aren't configured we log
    and return False — never raise — so the caller's HTTP path is safe."""
    if not SMTP_USER or not SMTP_PASS:
        return False
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
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        return True
    except Exception as e:  # noqa: BLE001 — we want to swallow everything
        # Log and continue — registration / contact responses still succeed
        try:
            from flask import current_app
            current_app.logger.warning("SMTP send to %s failed: %s", to, e)
        except Exception:
            pass
        return False


def _send_registration_confirm(student_email, name, parent_email=None, amount=None):
    amount_str = f"${int(amount)} CAD" if amount else "your registration fee"
    subject = "You're registered for HigherGrade Tutoring Summer Camp 2026 🎉"
    body = (
        f"Hi {name or 'there'},\n\n"
        f"Thanks for registering for HigherGrade Tutoring's Summer Camp 2026!\n\n"
        f"📅 Camp dates: August 4 – August 15, 2026\n"
        f"   Week 1 (Tue–Fri): Aug 4, 5, 6, 7\n"
        f"   Week 2 (Mon–Sat): Aug 10, 11, 12, 13, 14, 15\n"
        f"⏰ Hours: 9:00 AM – 3:30 PM daily\n"
        f"📍 Location: Abbey Park High School, 1455 Glen Abbey Gate, Oakville, ON\n"
        f"   Directions: https://www.google.com/maps/dir/?api=1&destination=Abbey+Park+High+School%2C+1455+Glen+Abbey+Gate%2C+Oakville%2C+ON\n\n"
        f"💸 Final step — please send {amount_str} by e-Transfer\n"
        f"To complete your registration, please send a {amount_str} Interac e-Transfer to:\n\n"
        f"      lucas.liu.ca2009@gmail.com\n\n"
        f"In the message field, please include the camper's full name so we can match\n"
        f"the payment to your registration. Heads up: the camper's account will stay\n"
        f"FROZEN (limited access to the student portal) until our staff confirms the\n"
        f"e-Transfer. Once we see it, we'll unfreeze the account within 24 hours.\n\n"
        f"📺 Parent / camper info session — video meeting\n"
        f"We're hosting a kickoff video meeting at 10:00 AM EST on Saturday, July 18, 2026.\n"
        f"We'll walk through the daily schedule, drop-off / pick-up logistics, what to\n"
        f"bring, and answer any questions you have. The meeting link will be emailed to\n"
        f"this address closer to the date — please mark your calendar.\n\n"
        f"A few things to know:\n"
        f"• Please bring a device (laptop, tablet, or Chromebook) and your own lunch\n"
        f"  each day. A water bottle is also a good idea. All math materials are\n"
        f"  provided.\n"
        f"• 🍱 Food partners (in progress): our team is currently in talks with\n"
        f"  local restaurants and food spots about partnering with the camp. If\n"
        f"  any food places confirm, we'll send a separate email with a link\n"
        f"  where parents can pre-purchase lunches for their camper on specific\n"
        f"  days — completely optional. If you'd rather just pack lunch, that's\n"
        f"  perfectly fine and is what we expect by default.\n"
        f"• Sign in to your dashboard at {SITE_URL}/student-portal.html\n"
        f"  to track points, see your class, and find the hidden mini-game.\n"
        f"• Questions? Reply to this email — it goes straight to the organizers.\n\n"
        f"See you August 4!\n"
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


def _points_frozen():
    return _meta_get("points_frozen", "0") == "1"


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
            return jsonify(ok=True, kind=None, student=None)
        if s["kind"] == "student":
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (s["studentId"],)).fetchone()
            return jsonify(ok=True, kind="student", student=row_to_student(row))
        return jsonify(ok=True, kind=s["kind"], student=None)

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
        row = g.db.execute(
            "SELECT * FROM students WHERE LOWER(TRIM(studentEmail)) = ? AND password = ?",
            (email, pwd),
        ).fetchone()
        if not row:
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
        if token:
            g.db.execute("DELETE FROM sessions WHERE token = ?", (token,))
        return _clear_session_cookie(make_response(jsonify(ok=True)))

    @app.route("/api/auth/admin/unlock", methods=["POST"])
    def auth_admin_unlock():
        data = request.get_json(silent=True) or {}
        passcode = (data.get("passcode") or "").strip().lower()
        expected = ADMIN_PASSCODE.strip().lower()
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
            if cur < amount: return jsonify(ok=False, error=f"You only have {cur} points."), 400

            received = int(amount * TRANSFER_KEEP_RATIO)
            lost = amount - received
            from_stats["privatePoints"]  = cur - amount
            from_stats["pointExchanges"] = from_stats.get("pointExchanges", 0) + 1
            to_stats["privatePoints"]    = to_stats.get("privatePoints", 0) + received
            to_stats["totalPointsEarned"] = to_stats.get("totalPointsEarned", 0) + received

            g.db.execute("UPDATE students SET stats = ? WHERE id = ?", (json.dumps(from_stats), from_id))
            g.db.execute("UPDATE students SET stats = ? WHERE id = ?", (json.dumps(to_stats), to_id))

            from_name = _full_name(from_row)
            to_name   = _full_name(to_row)
            # Lost points → transactions bank.
            if lost > 0:
                bank_prev = int(_meta_get("transactions_bank", "0") or "0")
                _meta_set("transactions_bank", str(bank_prev + lost))
                _log_tx(type="bank_deposit", scope="bank",
                        subjectId="transactions_bank", subjectName="Transactions Bank",
                        relatedId=from_id, relatedName=from_name,
                        amount=lost,
                        description=f"+{lost} pts deposited from {from_name} → {to_name} transfer ({amount} sent)")
            _log_tx(type="transfer_out", scope="student", subjectId=from_id,
                    subjectName=from_name, relatedId=to_id, relatedName=to_name,
                    amount=-amount,
                    description=f"Sent {amount} pts to {to_name} · {lost} pts deposited to the transactions bank")
            _log_tx(type="transfer_in", scope="student", subjectId=to_id,
                    subjectName=to_name, relatedId=from_id, relatedName=from_name,
                    amount=received,
                    description=f"Received {received} pts from {from_name} ({amount} sent, 50% kept)")

        return jsonify(ok=True, data={"sent": amount, "received": received, "lost": lost})

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
            if stats["clickerClicks"] % CLICKER_RATE == 0:
                if stats.get("dailyManualPts", 0) >= MANUAL_DAILY_CAP:
                    capped = True   # would have earned, but you've hit today's manual cap
                else:
                    earned = 1
                    stats["dailyManualPts"]      = stats.get("dailyManualPts", 0) + 1
                    stats["privatePoints"]       = stats.get("privatePoints", 0) + 1
                    stats["totalPointsEarned"]   = stats.get("totalPointsEarned", 0) + 1
                    stats["clickerPointsEarned"] = stats.get("clickerPointsEarned", 0) + 1
                    if stats["clickerPointsEarned"] >= SPIDER_THRESHOLD and not stats.get("spiderShown"):
                        spider = True
                        stats["spiderShown"] = True
            g.db.execute("UPDATE students SET stats = ? WHERE id = ?", (json.dumps(stats), sid))
            if earned > 0:
                _log_tx(type="clicker", scope="student", subjectId=sid,
                        subjectName=_full_name(row), amount=earned,
                        description=f"Earned {earned} pt from clicker ({stats['clickerClicks']} total clicks)")
        return jsonify(ok=True, data={
            "clicks": stats["clickerClicks"], "earned": earned, "spider": spider,
            "clickerPointsEarned": stats["clickerPointsEarned"],
            "capped": capped,
            "dailyManualPts": stats.get("dailyManualPts", 0),
            "manualCap": MANUAL_DAILY_CAP,
        })

    @app.route("/api/students/me/auto-click", methods=["POST"])
    @require_student
    @block_when_frozen
    def auto_click():
        """Time-based passive auto-accrual. Replaces the old per-minute
        UI-click trigger — the 'clickers don't actually click' issue.

        Each clicker level grants the student 1 point every AUTO_INTERVAL_MIN
        (=6) minutes of real time, regardless of whether the portal tab is
        open. When the student hits this endpoint we look at the elapsed
        time since their last accrual, work out how many ticks they've
        earned, multiply by their level, then cap at the per-day ceiling
        (AUTO_DAILY_CAP_PER_LV * level). The lastAutoAt timestamp is
        always advanced to now so they can't stockpile across the cap."""
        AUTO_INTERVAL_MIN = 6
        AUTO_INTERVAL_MS  = AUTO_INTERVAL_MIN * 60 * 1000

        sid = g.session["studentId"]
        now_ms = int(time.time() * 1000)
        today  = time.strftime("%Y-%m-%d", time.gmtime())

        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row: return jsonify(ok=False, error="Student not found."), 404
            roles = json.loads(row["roles"] or "[]")
            if CLICKER_ROLE_ID not in roles:
                return jsonify(ok=False, error="No clicker role."), 400
            extras = json.loads(row["extras"] or "{}")
            level = int(extras.get("clickerLevel") or 0)
            if level <= 0:
                return jsonify(ok=False, error="Clicker level is 0."), 400

            cap = AUTO_DAILY_CAP_PER_LV * level

            stats = {**default_stats(), **json.loads(row["stats"] or "{}")}
            if stats.get("dailyClickerDate") != today:
                stats["dailyClickerDate"] = today
                stats["dailyManualPts"]   = 0
                stats["dailyAutoPts"]     = 0

            last = int(extras.get("lastAutoAt") or 0)
            if last <= 0:
                # First accrual — set baseline; nothing earned yet.
                extras["lastAutoAt"] = now_ms
                g.db.execute("UPDATE students SET extras = ? WHERE id = ?",
                             (json.dumps(extras), sid))
                return jsonify(ok=True, data={
                    "earned": 0, "level": level,
                    "dailyAutoPts": stats.get("dailyAutoPts", 0),
                    "autoCap": cap,
                    "nextTickInSec": AUTO_INTERVAL_MIN * 60,
                    "intervalMin": AUTO_INTERVAL_MIN,
                })

            elapsed = max(0, now_ms - last)
            ticks   = elapsed // AUTO_INTERVAL_MS
            potential = int(ticks) * level

            remaining_cap = max(0, cap - stats.get("dailyAutoPts", 0))
            earned = min(potential, remaining_cap)

            if earned > 0:
                stats["dailyAutoPts"]        = stats.get("dailyAutoPts", 0) + earned
                stats["privatePoints"]       = stats.get("privatePoints", 0) + earned
                stats["totalPointsEarned"]   = stats.get("totalPointsEarned", 0) + earned
                stats["clickerPointsEarned"] = stats.get("clickerPointsEarned", 0) + earned

            # Always advance the timestamp to "now" so partial intervals don't
            # accumulate across the cap. The next tick window starts fresh.
            extras["lastAutoAt"] = now_ms

            g.db.execute("UPDATE students SET stats = ?, extras = ? WHERE id = ?",
                         (json.dumps(stats), json.dumps(extras), sid))

            if earned > 0:
                _log_tx(type="clicker", scope="student", subjectId=sid,
                        subjectName=_full_name(row), amount=earned,
                        description=f"Auto-clicker (Lv {level}) +{earned} pt"
                                    + (" · cap reached" if earned >= remaining_cap and earned < potential else ""))

        next_tick_sec = AUTO_INTERVAL_MIN * 60
        return jsonify(ok=True, data={
            "earned": earned,
            "level":  level,
            "dailyAutoPts": stats.get("dailyAutoPts", 0),
            "autoCap": cap,
            "nextTickInSec": next_tick_sec,
            "intervalMin": AUTO_INTERVAL_MIN,
            "capped": (earned >= remaining_cap and potential > remaining_cap),
        })

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

            awarded = 0
            if not already_rewarded and tier_pts > 0:
                awarded = tier_pts
                extras["doorsRewarded"] = True
                stats = {**default_stats(), **json.loads(row["stats"] or "{}")}
                stats["privatePoints"]     = stats.get("privatePoints", 0) + awarded
                stats["totalPointsEarned"] = stats.get("totalPointsEarned", 0) + awarded
                g.db.execute(
                    "UPDATE students SET stats = ?, extras = ? WHERE id = ?",
                    (json.dumps(stats), json.dumps(extras), sid),
                )
                _log_tx(type="earn", scope="student", subjectId=sid,
                        subjectName=_full_name(row), amount=awarded,
                        description=f"🚪 Maze complete · {correct}/{total} ({pct:.0f}%) · +{awarded} pts")
            else:
                g.db.execute(
                    "UPDATE students SET extras = ? WHERE id = ?",
                    (json.dumps(extras), sid),
                )
        return jsonify(ok=True, data={
            "correct": correct,
            "total":   total,
            "percent": round(pct, 2),
            "tierPoints":     tier_pts,
            "awarded":        awarded,
            "alreadyRewarded": already_rewarded,
            "completions":    completions,
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
        qid = "inf-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(3)
        row = g.db.execute("SELECT COALESCE(MAX(position), 0) AS m FROM infinity_questions").fetchone()
        pos = (row["m"] or 0) + 1
        g.db.execute(
            "INSERT INTO infinity_questions (id, question, answer, wrongAnswer, position, createdAt)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (qid, q, a, w, pos, int(time.time())),
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
        g.db.execute(
            "UPDATE infinity_questions SET question = ?, answer = ?, wrongAnswer = ? WHERE id = ?",
            (q, a, w, qid),
        )
        return jsonify(ok=True)

    @app.route("/api/admin/infinity-questions/<qid>", methods=["DELETE"])
    @require_admin
    def admin_delete_infinity_question(qid):
        g.db.execute("DELETE FROM infinity_questions WHERE id = ?", (qid,))
        return jsonify(ok=True)

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
            class_id = row["classId"]
            if not class_id:
                return jsonify(ok=False, error="You need to be assigned to a class first — ask an admin."), 400
            roles = json.loads(row["roles"] or "[]")
            if MAZEWIZ_ROLE_ID in roles:
                return jsonify(ok=False, error="You already hold the Maze Wizard title!"), 400

            classmates = g.db.execute(
                "SELECT * FROM students WHERE classId = ?", (class_id,),
            ).fetchall()
            for cm in classmates:
                cm_roles = json.loads(cm["roles"] or "[]")
                if MAZEWIZ_ROLE_ID in cm_roles:
                    return jsonify(ok=False, error=f"Too late — {_full_name(cm)} already claimed Maze Wizard for your class."), 400

            roles.append(MAZEWIZ_ROLE_ID)
            g.db.execute("UPDATE students SET roles = ? WHERE id = ?", (json.dumps(roles), sid))
            _log_tx(type="role_assigned", scope="student", subjectId=sid,
                    subjectName=_full_name(row), amount=0,
                    description="🧙 Claimed the Maze Wizard title for their class")
        return jsonify(ok=True)

    # ── Classes ────────────────────────────────────────────────────
    @app.route("/api/classes", methods=["GET"])
    def list_classes():
        rows = g.db.execute("SELECT * FROM classes").fetchall()
        return jsonify(ok=True, data=[row_to_class(r) for r in rows])

    @app.route("/api/classes", methods=["PUT"])
    @require_admin
    def replace_classes():
        data = request.get_json(silent=True) or {}
        arr = data.get("classes") or []
        with g.db:
            g.db.execute("DELETE FROM classes")
            for c in arr:
                g.db.execute(
                    """INSERT INTO classes (id, name, classPoints, classBank, bankLastUpdate, createdAt)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (c["id"], c["name"],
                     int(c.get("classPoints") or 0),
                     float(c.get("classBank") or 0),
                     int(c["bankLastUpdate"]) if c.get("bankLastUpdate") else None,
                     c.get("createdAt") or ""),
                )
        return jsonify(ok=True, count=len(arr))

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
            g.db.execute("DELETE FROM staff")
            for i, s in enumerate(arr):
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

    @app.route("/api/admin/students/<sid>/base-stat", methods=["POST"])
    @require_admin
    def admin_student_base_stat(sid):
        """Adjust an admin-defined base stat for a student. The stat's
        `pointsPerUnit` (set on the Base Stats admin page) determines
        how many private points the student gains or loses per unit.
        Body: { catId: str, delta: int }."""
        d = request.get_json(silent=True) or {}
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
            "student": row_to_student(updated),
        })

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
        row = g.db.execute("SELECT id FROM students WHERE id = ?", (sid,)).fetchone()
        if not row:
            return jsonify(ok=False, error="Student not found"), 404
        g.db.execute("UPDATE students SET frozen = ? WHERE id = ?", (frozen, sid))
        # If we're freezing, drop any active student sessions so the next
        # request from that camper bounces them back to the login screen.
        if frozen:
            g.db.execute(
                "DELETE FROM sessions WHERE kind = 'student' AND studentId = ?",
                (sid,),
            )
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
    DEFAULT_STUDENT_CAP = 100

    def _student_cap():
        try:
            return int(_meta_get("student_cap", str(DEFAULT_STUDENT_CAP)) or DEFAULT_STUDENT_CAP)
        except (TypeError, ValueError):
            return DEFAULT_STUDENT_CAP

    # Registration pricing window — set manually from the admin panel.
    REG_TIERS = {
        "closed": {"open": False, "price": 0,   "label": "Closed"},
        "early":  {"open": True,  "price": 135, "label": "Early-bird"},
        "normal": {"open": True,  "price": 150, "label": "Normal"},
        "late":   {"open": True,  "price": 200, "label": "Late"},
    }

    def _reg_tier():
        t = _meta_get("reg_tier", "closed") or "closed"
        return t if t in REG_TIERS else "closed"

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
        if not first or not last:
            return jsonify(ok=False, error="First and last name are required."), 400
        if _is_reserved_email(student_email):
            return jsonify(ok=False, error="That email isn't available — please use a different one."), 400
        # Cap check — count active (non-waitlisted) registrations.
        cap = _student_cap()
        active_count = g.db.execute(
            "SELECT COUNT(*) AS n FROM registrations WHERE COALESCE(waitlisted, 0) = 0"
        ).fetchone()["n"]
        waitlisted = 1 if active_count >= cap else 0
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
        g.db.execute(
            """INSERT INTO registrations
               (id, createdAt, firstName, lastName, dob, studentEmail, school,
                parentFirst, parentLast, relationship, parentPhone, parentEmail,
                emerg1Name, emerg1Phone, emerg1Relationship,
                hobbies, whyJoin, consentPhoto, password, waitlisted, pickupPeople)
               VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rid, int(time.time()),
                first, last,
                (d.get("dob") or "").strip() or None,
                (d.get("student_email") or "").strip() or None,
                (d.get("school") or "").strip() or None,
                (d.get("parent_first") or "").strip() or None,
                (d.get("parent_last")  or "").strip() or None,
                (d.get("relationship") or "").strip() or None,
                (d.get("parent_phone") or "").strip() or None,
                (d.get("parent_email") or "").strip() or None,
                (d.get("emerg1_name") or "").strip() or None,
                (d.get("emerg1_phone") or "").strip() or None,
                (d.get("emerg1_relationship") or "").strip() or None,
                (d.get("hobbies") or None),
                (d.get("why_join") or None),
                1 if d.get("consent_photo") else 0,
                password,
                waitlisted,
                pickup_json,
            ),
        )
        # Auto-provision a frozen student account so the camper can attempt
        # to sign in once staff confirms the $75 e-Transfer. Skip silently
        # if email is blank or a student record already exists for this
        # email.
        try:
            normalized_email = (d.get("student_email") or "").strip()
            if normalized_email:
                already = g.db.execute(
                    "SELECT id FROM students WHERE LOWER(TRIM(studentEmail)) = ?",
                    (normalized_email.lower(),),
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
                        "frozen":       1,
                    }))
        except Exception:  # noqa: BLE001
            pass
        try:
            _send_registration_confirm(
                (d.get("student_email") or "").strip(),
                f"{first} {last}".strip(),
                (d.get("parent_email") or "").strip() or None,
                amount=REG_TIERS[_reg_tier()]["price"] or None,
            )
        except Exception:  # noqa: BLE001
            pass
        return jsonify(ok=True, id=rid, waitlisted=bool(waitlisted))

    @app.route("/api/settings/student-cap", methods=["GET"])
    def settings_student_cap():
        cap = _student_cap()
        active = g.db.execute(
            "SELECT COUNT(*) AS n FROM registrations WHERE COALESCE(waitlisted, 0) = 0"
        ).fetchone()["n"]
        waitlisted = g.db.execute(
            "SELECT COUNT(*) AS n FROM registrations WHERE COALESCE(waitlisted, 0) = 1"
        ).fetchone()["n"]
        return jsonify(ok=True, cap=cap, active=active, waitlisted=waitlisted)

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
        return jsonify(ok=True, tier=t, open=info["open"],
                       price=info["price"], label=info["label"])

    @app.route("/api/admin/settings/reg-tier", methods=["POST"])
    @require_admin
    def settings_reg_tier_set():
        d = request.get_json(silent=True) or {}
        t = (d.get("tier") or "").strip()
        if t not in REG_TIERS:
            return jsonify(ok=False, error="tier must be: closed, early, normal, or late"), 400
        _meta_set("reg_tier", t)
        info = REG_TIERS[t]
        return jsonify(ok=True, tier=t, open=info["open"],
                       price=info["price"], label=info["label"])

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

    @app.route("/api/students/me/contribute-class-points", methods=["POST"])
    @require_student
    @block_when_frozen
    def contribute_class_points():
        d = request.get_json(silent=True) or {}
        try:
            amount = int(d.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0
        if amount <= 0:
            return jsonify(ok=False, error="Enter a positive amount to contribute."), 400
        sid = g.session["studentId"]
        with g.db:
            row = g.db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
            if not row:
                return jsonify(ok=False, error="Student not found."), 404
            if not row["classId"]:
                return jsonify(ok=False, error="You're not assigned to a class yet."), 400
            stats  = {**default_stats(), **json.loads(row["stats"] or "{}")}
            extras = json.loads(row["extras"] or "{}")
            cur_pp = stats.get("privatePoints", 0)
            if cur_pp < amount:
                return jsonify(ok=False, error=f"You only have {cur_pp} points."), 400
            stats["privatePoints"] = cur_pp - amount
            bucket = int(extras.get("classContribution") or 0) + amount
            class_pts_to_bank = bucket // CLASS_CONTRIBUTION_THRESHOLD
            extras["classContribution"] = bucket % CLASS_CONTRIBUTION_THRESHOLD
            g.db.execute(
                "UPDATE students SET stats = ?, extras = ? WHERE id = ?",
                (json.dumps(stats), json.dumps(extras), sid),
            )
            from_name = _full_name(row)
            cls_name = (row["className"] or "your class")
            if class_pts_to_bank > 0:
                cls_row = g.db.execute(
                    "SELECT * FROM classes WHERE id = ?", (row["classId"],),
                ).fetchone()
                if cls_row:
                    new_bank = float(cls_row["classBank"] or 0) + class_pts_to_bank
                    g.db.execute(
                        "UPDATE classes SET classBank = ?, bankLastUpdate = ? WHERE id = ?",
                        (new_bank, int(time.time() * 1000), cls_row["id"]),
                    )
                    _log_tx(type="class_bank_deposit", scope="class",
                            subjectId=cls_row["id"], subjectName=cls_row["name"],
                            relatedId=sid, relatedName=from_name,
                            amount=class_pts_to_bank,
                            description=f"🔒 +{class_pts_to_bank} class pt from {from_name} contribution · locked until exams")
            _log_tx(type="class_contribute", scope="student",
                    subjectId=sid, subjectName=from_name,
                    amount=-amount,
                    description=f"Contributed {amount} pts toward class points · bucket {extras['classContribution']}/{CLASS_CONTRIBUTION_THRESHOLD}"
                                + (f" · cashed out {class_pts_to_bank} class pt to {cls_name} bank" if class_pts_to_bank else ""))
        return jsonify(ok=True, data={
            "contributed": amount,
            "bucket": extras["classContribution"],
            "threshold": CLASS_CONTRIBUTION_THRESHOLD,
            "classPointsBanked": class_pts_to_bank,
            "newPrivatePoints": stats["privatePoints"],
        })

    @app.route("/api/admin/registrations", methods=["GET"])
    @require_admin
    def admin_list_registrations():
        rows = g.db.execute(
            "SELECT * FROM registrations ORDER BY createdAt DESC"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["consentPhoto"] = bool(d.get("consentPhoto"))
            try:
                d["pickupPeople"] = json.loads(d.get("pickupPeople") or "[]")
            except (TypeError, ValueError):
                d["pickupPeople"] = []
            out.append(d)
        return jsonify(ok=True, data=out)

    @app.route("/api/admin/registrations/<rid>", methods=["DELETE"])
    @require_admin
    def admin_delete_registration(rid):
        g.db.execute("DELETE FROM registrations WHERE id = ?", (rid,))
        return jsonify(ok=True)

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
            "className":   s.get("className"),
            "privatePoints":     int(stats.get("privatePoints") or 0),
            "totalPointsEarned": int(stats.get("totalPointsEarned") or 0),
            "roles":       s.get("roles") or [],
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
        row = g.db.execute(
            "SELECT * FROM students WHERE LOWER(TRIM(studentEmail)) = ? AND password = ?",
            (email, pwd),
        ).fetchone()
        if not row:
            return jsonify(ok=False, error="No matching camp account."), 401
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

    @app.route("/api/bot/chests", methods=["POST"])
    @require_bot
    def bot_chest_create():
        d = request.get_json(silent=True) or {}
        guild_id = (d.get("guildId") or "").strip()
        code     = (d.get("code") or "").strip()
        role_id  = (d.get("roleId") or "").strip()
        if not guild_id or not code or not role_id:
            return jsonify(ok=False, error="guildId, code, and roleId are required."), 400
        existing = g.db.execute(
            "SELECT id FROM discord_chests WHERE guildId = ? AND code = ?",
            (guild_id, code),
        ).fetchone()
        if existing:
            return jsonify(ok=False, error="A chest with that code already exists in this server."), 409
        cid = "chest-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(3)
        g.db.execute(
            """INSERT INTO discord_chests
               (id, code, description, imageUrl, roleId, roleName, guildId,
                channelId, messageId, createdBy, createdAt, claimedBy)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]')""",
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
            ),
        )
        return jsonify(ok=True, data={"id": cid})

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
            out.append(d)
        return jsonify(ok=True, data=out)

    @app.route("/api/bot/chests/<cid>", methods=["DELETE"])
    @require_bot
    def bot_chest_delete(cid):
        g.db.execute("DELETE FROM discord_chests WHERE id = ?", (cid,))
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
            if chest_id:
                chest = g.db.execute(
                    "SELECT * FROM discord_chests WHERE id = ? AND code = ? AND guildId = ?",
                    (chest_id, code, guild_id),
                ).fetchone()
            else:
                chest = g.db.execute(
                    "SELECT * FROM discord_chests WHERE guildId = ? AND code = ?",
                    (guild_id, code),
                ).fetchone()
            if not chest:
                return jsonify(ok=False, error="That code doesn't open this chest."), 404
            try:
                claimed = json.loads(chest["claimedBy"] or "[]")
            except Exception:  # noqa: BLE001
                claimed = []
            already = discord_id in claimed
            if not already:
                claimed.append(discord_id)
                g.db.execute(
                    "UPDATE discord_chests SET claimedBy = ? WHERE id = ?",
                    (json.dumps(claimed), chest["id"]),
                )
        return jsonify(ok=True, data={
            "chestId":     chest["id"],
            "roleId":      chest["roleId"],
            "roleName":    chest["roleName"],
            "description": chest["description"],
            "alreadyClaimed": already,
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
    "frozen",
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
    extras = {k: v for k, v in raw.items()
              if k not in KNOWN_STUDENT_COLS and k not in {"stats", "roles", "baseStats"}}
    s["extras"]    = json.dumps(extras)
    return s


def _insert_student(s):
    g.db.execute(
        """INSERT OR REPLACE INTO students
           (id, firstName, lastName, studentEmail, password, parentEmail, phone,
            school, grade, classId, className, registeredAt,
            stats, roles, baseStats, extras, frozen)
           VALUES
           (:id, :firstName, :lastName, :studentEmail, :password, :parentEmail, :phone,
            :school, :grade, :classId, :className, :registeredAt,
            :stats, :roles, :baseStats, :extras, :frozen)""",
        s,
    )


def _full_name(row):
    fn = (row["firstName"] or "").strip()
    ln = (row["lastName"]  or "").strip()
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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
