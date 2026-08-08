"""SQLite connection helpers + one-time schema/seed bootstrap."""

import json
import os
import sqlite3
from pathlib import Path

import crypto

# ── At-rest-encrypted PII columns ─────────────────────────────────────
# These student/registration columns are stored encrypted when an
# encryption key is configured (see crypto.py) and decrypted transparently
# on read. Deliberately EXCLUDED: points/roles/stats/baseStats (gamification,
# per requirement), functional identifiers (id, classId, className,
# registeredAt, frozen, emailIndex), and referral/discount/payment fields.
STUDENT_ENC_FIELDS = (
    "firstName", "lastName", "studentEmail", "password",
    "parentEmail", "phone", "school", "grade",
)
REGISTRATION_ENC_FIELDS = (
    "firstName", "lastName", "dob", "studentEmail", "school",
    "parentFirst", "parentLast", "relationship", "parentPhone", "parentEmail",
    "emerg1Name", "emerg1Phone", "emerg1Relationship",
    "hobbies", "whyJoin", "medicalInfo", "password", "pickupPeople",
    "referrerEmail",
)

# On the production VM the DB lives outside the git checkout at
# /var/lib/highergrade/app.db — the systemd unit sets HIGHERGRADE_DB to
# that path explicitly (see deploy/highergrade-api.service). When you run
# the server directly for local development and don't set HIGHERGRADE_DB,
# fall back to a writable file next to this module so `python app.py`
# works out of the box instead of failing on the un-writable /var path.
DB_PATH = os.environ.get(
    "HIGHERGRADE_DB",
    str(Path(__file__).parent / "dev.db"),
)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create the DB file (and parent dir) if missing, apply schema, seed defaults.
    Designed to be called ONCE at process start (before gunicorn forks workers)."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        # WAL journal is set once on the file; subsequent connections inherit it.
        # Wrap in try/except so concurrent process starts don't crash.
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass
        conn.executescript(SCHEMA_PATH.read_text())
        _migrate(conn)
        _seed(conn)
        _encrypt_existing(conn)
    finally:
        conn.close()


def _encrypt_existing(conn):
    """One-time, idempotent pass that encrypts pre-existing plaintext PII and
    backfills the email blind index. Only runs when an encryption key is
    configured; safe to run on every startup (already-encrypted rows and
    already-indexed rows are skipped). Never raises — a failure here must not
    stop the server from booting."""
    if not crypto.enabled():
        return
    try:
        _encrypt_table(conn, "students", STUDENT_ENC_FIELDS)
        _encrypt_table(conn, "registrations", REGISTRATION_ENC_FIELDS)
    except Exception:  # noqa: BLE001
        pass


def _encrypt_table(conn, table, fields):
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if "id" not in cols:
        return
    has_index = "emailIndex" in cols
    has_email = "studentEmail" in cols
    present = [f for f in fields if f in cols]
    sel = ["id"] + present + (["emailIndex"] if has_index else []) \
        + (["studentEmail"] if (has_email and "studentEmail" not in present) else [])
    for row in conn.execute(f"SELECT {', '.join(sel)} FROM {table}").fetchall():
        sets, params = [], []
        for f in present:
            val = row[f]
            if val is not None and not crypto.is_encrypted(val):
                sets.append(f"{f} = ?")
                params.append(crypto.enc(val))
        if has_index and not row["emailIndex"] and has_email:
            # studentEmail may already be encrypted from a prior partial run.
            plain_email = crypto.dec(row["studentEmail"])
            idx = crypto.blind(plain_email)
            if idx:
                sets.append("emailIndex = ?")
                params.append(idx)
        if sets:
            params.append(row["id"])
            conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id = ?", params)


def _migrate(conn):
    """Ad-hoc lightweight migrations for already-deployed tables that
    don't pick up new columns from CREATE TABLE IF NOT EXISTS."""
    def _has_column(table, col):
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r["name"] == col for r in rows)
    try:
        if _has_column("infinity_questions", "id") and not _has_column("infinity_questions", "wrongAnswer"):
            conn.execute(
                "ALTER TABLE infinity_questions ADD COLUMN wrongAnswer TEXT NOT NULL DEFAULT ''"
            )
    except sqlite3.OperationalError:
        pass
    try:
        if _has_column("registrations", "id") and not _has_column("registrations", "password"):
            conn.execute("ALTER TABLE registrations ADD COLUMN password TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        if _has_column("registrations", "id") and not _has_column("registrations", "waitlisted"):
            conn.execute(
                "ALTER TABLE registrations ADD COLUMN waitlisted INTEGER NOT NULL DEFAULT 0"
            )
    except sqlite3.OperationalError:
        pass
    try:
        if _has_column("registrations", "id") and not _has_column("registrations", "pickupPeople"):
            conn.execute(
                "ALTER TABLE registrations ADD COLUMN pickupPeople TEXT NOT NULL DEFAULT '[]'"
            )
    except sqlite3.OperationalError:
        pass
    # Campus free-movement-during-breaks choice ('allow' | 'no'). Added after
    # launch; pre-existing rows stay NULL and render as "—" in the admin view.
    try:
        if _has_column("registrations", "id") and not _has_column("registrations", "campusRoaming"):
            conn.execute("ALTER TABLE registrations ADD COLUMN campusRoaming TEXT")
    except sqlite3.OperationalError:
        pass
    # Delivery mode (in-person vs online), medical notes, discount code, and the
    # post-discount amount due. Added after launch; pre-existing rows stay NULL.
    for _col, _type in (
        ("deliveryMode",   "TEXT"),
        ("medicalInfo",    "TEXT"),
        ("discountCode",   "TEXT"),
        ("amountDue",      "REAL"),
        ("paymentMethod",  "TEXT"),
        ("referrerEmail",  "TEXT"),
        # Referral program v2 — each camper gets their own permanent 6-digit
        # code (referralCode) and, if they were referred, the code they
        # entered (referredByCode). Pre-existing rows stay NULL.
        ("referralCode",   "TEXT"),
        ("referredByCode", "TEXT"),
        # Sponsor-location payment (extra compounding 5% off when paid at a
        # partner store). Pre-existing rows stay NULL.
        ("sponsorLocation", "TEXT"),
        # Stripe card payment: the Stripe PaymentIntent id (for reconciliation)
        # and the unix timestamp the card charge completed. NULL until paid by card.
        ("paymentRef",      "TEXT"),
        ("paidAt",          "INTEGER"),
    ):
        try:
            if _has_column("registrations", "id") and not _has_column("registrations", _col):
                conn.execute(f"ALTER TABLE registrations ADD COLUMN {_col} {_type}")
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reg_refcode ON registrations(referralCode)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reg_referredby ON registrations(referredByCode)")
    except sqlite3.OperationalError:
        pass
    # Chests gained imageUrl + channelId + messageId for the public-message
    # button flow. Add them to existing tables that pre-date the change.
    # Chests later gained a points reward and an optional cap on how many
    # different people may claim them. `points` defaults to 50 for chests
    # that pre-date the reward; `maxClaims` stays NULL = unlimited.
    # `removeRole*` came later still — the role a chest strips on unlock.
    for col in (("imageUrl", "TEXT"), ("channelId", "TEXT"), ("messageId", "TEXT"),
                ("points", "INTEGER NOT NULL DEFAULT 50"), ("maxClaims", "INTEGER"),
                ("removeRoleId", "TEXT"), ("removeRoleName", "TEXT"),
                ("bonusPoints", "INTEGER NOT NULL DEFAULT 0"),
                ("bonusCount", "INTEGER NOT NULL DEFAULT 0"),
                ("ignoreCase", "INTEGER NOT NULL DEFAULT 0"),
                ("ignoreSpaces", "INTEGER NOT NULL DEFAULT 0"),
                # Per-person wait between passcode attempts. The DEFAULT is
                # what backfills every already-placed chest to 15s.
                ("cooldownSeconds", "INTEGER NOT NULL DEFAULT 15"),
                ("attachments", "TEXT NOT NULL DEFAULT '[]'")):
        try:
            if _has_column("discord_chests", "id") and not _has_column("discord_chests", col[0]):
                conn.execute(f"ALTER TABLE discord_chests ADD COLUMN {col[0]} {col[1]}")
        except sqlite3.OperationalError:
            pass
    # Homework: where the camper ran /submit, so feedback has somewhere to go
    # when their DMs are shut. Added after the table shipped.
    try:
        if _has_column("discord_links", "discordId") and \
                not _has_column("discord_links", "feedbackChannelId"):
            conn.execute("ALTER TABLE discord_links ADD COLUMN feedbackChannelId TEXT")
    except sqlite3.OperationalError:
        pass
    for _col, _type in (
        ("originChannelId", "TEXT"),
        # Quiz scoring, added after the table shipped.
        ("scoreEarned",   "INTEGER"),
        ("scoreTotal",    "INTEGER"),
        ("pointsAwarded", "INTEGER NOT NULL DEFAULT 0"),
        ("returnedFiles", "TEXT NOT NULL DEFAULT '[]'"),
    ):
        try:
            if _has_column("homework_submissions", "id") and \
                    not _has_column("homework_submissions", _col):
                conn.execute(f"ALTER TABLE homework_submissions ADD COLUMN {_col} {_type}")
        except sqlite3.OperationalError:
            pass
    # Playtest mode — a student session minted by an admin remembers the
    # admin token it came from so Esc can hand the cookie back.
    try:
        if _has_column("sessions", "token") and not _has_column("sessions", "adminToken"):
            conn.execute("ALTER TABLE sessions ADD COLUMN adminToken TEXT")
    except sqlite3.OperationalError:
        pass
    # Staff transcript file uploads — added after launch.
    try:
        if _has_column("staff", "id") and not _has_column("staff", "transcriptFile"):
            conn.execute("ALTER TABLE staff ADD COLUMN transcriptFile TEXT")
    except sqlite3.OperationalError:
        pass
    # Per-teacher referral code (admin-only) — links recruited campers back to
    # the teacher. Added after launch; existing staff get one minted lazily on
    # the next admin save (see replace_staff).
    try:
        if _has_column("staff", "id") and not _has_column("staff", "referralCode"):
            conn.execute("ALTER TABLE staff ADD COLUMN referralCode TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_staff_refcode ON staff(referralCode)")
    except sqlite3.OperationalError:
        pass
    # Blind-index columns for email lookups once PII is encrypted at rest.
    for _tbl in ("students", "registrations"):
        try:
            if _has_column(_tbl, "id") and not _has_column(_tbl, "emailIndex"):
                conn.execute(f"ALTER TABLE {_tbl} ADD COLUMN emailIndex TEXT")
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{_tbl}_emailidx ON {_tbl}(emailIndex)"
                )
        except sqlite3.OperationalError:
            pass
    # `frozen` column on students. Added after launch — existing students
    # were already paid up so they are grandfathered in as unfrozen
    # (DEFAULT 0). Newly created students default to frozen=1 via the
    # server-side _normalize_student helper.
    try:
        if _has_column("students", "id") and not _has_column("students", "frozen"):
            conn.execute(
                "ALTER TABLE students ADD COLUMN frozen INTEGER NOT NULL DEFAULT 0"
            )
    except sqlite3.OperationalError:
        pass
    # Indexes for post-launch columns — created HERE (not in schema.sql) and
    # unconditionally, now that every column above is guaranteed to exist on
    # both fresh and already-deployed databases. Doing this in schema.sql would
    # crash startup on an existing DB, because CREATE TABLE IF NOT EXISTS is a
    # no-op there and the index would reference a not-yet-added column.
    for _stmt in (
        "CREATE INDEX IF NOT EXISTS idx_students_emailidx ON students(emailIndex)",
        "CREATE INDEX IF NOT EXISTS idx_reg_emailidx ON registrations(emailIndex)",
        "CREATE INDEX IF NOT EXISTS idx_reg_refcode ON registrations(referralCode)",
        "CREATE INDEX IF NOT EXISTS idx_reg_referredby ON registrations(referredByCode)",
        "CREATE INDEX IF NOT EXISTS idx_staff_refcode ON staff(referralCode)",
    ):
        try:
            conn.execute(_stmt)
        except sqlite3.OperationalError:
            pass


# ── Seed defaults ─────────────────────────────────────────────────────

DEFAULT_ROLES = [
    {
        "id": "mazewiz",
        "name": "Maze Wizard",
        "icon": "🧙",
        "color": "#8B5CF6",
        "description": "The first student from their class to find the hidden staff sign-in page. Grants permission to view classmates' private stats and roles.",
        "special": 1,
    },
    {
        "id": "money_tree",
        "name": "Money Tree",
        "icon": "🌳",
        "color": "#10B981",
        "description": "A one-time-use enchantment hidden behind a secret door pattern. Spend 6,000 private points to activate and double whatever you have left. Can also be gifted to another student.",
        "special": 1,
    },
    {
        "id": "clicker",
        "name": "Clicker",
        "icon": "🖱",
        "color": "#3B82F6",
        "description": "Auto-clicker — taps the click button on your behalf once per minute per level. Stack levels for more daily auto-points. Admin upgrades raise the level.",
        "special": 1,
    },
    {
        "id": "crane",
        "name": "Paper Crane",
        "icon": "🕊",
        "color": "#EC4899",
        "description": "Hidden mini-game role. Receive secret hints and updates from camp instructors that point you toward the next puzzle. Only 11 cranes ever exist across the camp.",
        "special": 1,
    },
    {
        "id": "calamity_catalyst",
        "name": "Calamity Catalyst",
        "icon": "🍋",
        "color": "#84CC16",
        "description": "The key to the shattered blade. Holders see a lime bubble drifting through the Support Us page — and only they can take the trial hidden behind it.",
        "special": 1,
    },
    {
        "id": "lime_sword",
        "name": "Lime Sword",
        "icon": "🗡",
        "color": "#65A30D",
        "description": "Reforged by cutting 140 of 150 limes out of the air in a single minute. The blade points toward the spider.",
        "special": 1,
    },
]

# Base stats are entirely admin-defined now. A fresh database starts
# with no categories — the admin adds whatever they want at
# /admin-base-stats.html. Each category's `pointsPerUnit` decides how
# many points a student earns (or loses) when the count changes by 1.
DEFAULT_BASE_STATS = []

# Built-in stats that roll out to already-seeded databases too (same
# treatment as DEFAULT_ROLES). Hand-raising is the classroom one: every
# +1 is worth 2 points via pointsPerUnit, and staff can undo abuse by
# bumping it back down or awarding negative points.
BUILTIN_BASE_STATS = [
    {"id": "hand_raised", "name": "Hand Raised", "icon": "✋",
     "pointsPerUnit": 2, "position": 0},
]

DEFAULT_STAFF = [
    {"id": "alex-chen", "category": "organizers",
     "name": "Alex Chen", "role": "Lead Organizer",
     "image": "placeholder_team_alex_chen_headshot.png",
     "quote": "I wanted to build the math experience I wish I had in Grade 9.",
     "age": "17", "school": "Abbey Park High School — Grade 12",
     "gender": "Male", "pronouns": "he / him",
     "interests": "Number theory, jazz piano, chess, competitive math, Oakville pizza discourse.",
     "bio": "Hi! I'm Alex — a Grade 12 HDSB student and the one who kicked this whole thing off.\n\nI fell in love with number theory after stumbling onto a Numberphile video about Fermat primes late one night, and I've been hooked ever since. I've written the CEMC Euclid three years running and reached the CMO qualifier last year.\n\nThe idea for this camp came out of frustration — I was sitting through a slow Grade 9 math class and realized I'd learned more in one afternoon of self-study than the whole semester. I wanted to give the next wave of Grade 9s a preview of what math can actually feel like.",
     "transcript": "Grade 12 HDSB student. 98% MCV4U, 96% MHF4U. Euclid 2026 top 10%. CMO Qualifier 2024."},
    {"id": "jordan-park", "category": "organizers",
     "name": "Jordan Park", "role": "Curriculum Design",
     "image": "placeholder_team_jordan_park_headshot.png",
     "quote": "A good proof should click into place like a satisfying puzzle.",
     "age": "16", "school": "Abbey Park High School — Grade 11",
     "gender": "Non-binary", "pronouns": "they / them",
     "interests": "Geometry, origami, peer tutoring, documentary films.",
     "bio": "I'm Jordan, a Grade 11 HDSB student, and I handle most of the curriculum design and lesson planning.\n\nGeometry is my favourite unit — there's something deeply satisfying about a proof that just clicks into place. I'm the one writing most of the Unit 4 material, plus the 'Putting it all together' day where we combine everything we've learned about shapes and coordinate geometry.\n\nOutside of camp work, I tutor at my school's peer tutoring program and run a small origami club.",
     "transcript": "Grade 11 HDSB student. 95% MCR3U, 97% MPM2D."},
    {"id": "maya-patel", "category": "teaching_staff",
     "name": "Maya Patel", "role": "Exam & Competitions Lead",
     "image": "placeholder_team_maya_patel_headshot.png",
     "quote": "Math contests aren't about speed — they're about seeing problems differently.",
     "age": "17", "school": "Abbey Park High School — Grade 12",
     "gender": "Female", "pronouns": "she / her",
     "interests": "Competitive math, cross-country running, bullet journalling.",
     "bio": "I'm Maya, a Grade 12 HDSB student. I've participated in CEMC contests every year since Grade 7 and scored top 25% on the Euclid last year.\n\nAt the camp, I design the weekend unit tests and the two final exams (Applications + Thinking) to closely match what Grade 9 students will actually see in class — plus a few stretch questions to push the strongest of you.\n\nWhen I'm not doing math, I'm probably running — I'm on my school's cross-country team.",
     "transcript": "Grade 12 HDSB student. 97% MHF4U, 95% MDM4U. Euclid 2026 top 25%. CEMC Cayley/Fermat gold pins."},
    {"id": "priya-nair", "category": "teaching_staff",
     "name": "Priya Nair", "role": "Data & Linear Relations Lead",
     "image": "placeholder_team_priya_nair_headshot.png",
     "quote": "Statistics is everywhere — most people just haven't been given the right lens.",
     "age": "17", "school": "Abbey Park High School — Grade 12",
     "gender": "Female", "pronouns": "she / her",
     "interests": "Statistics, competitive Scrabble, Studio Ghibli films.",
     "bio": "I'm Priya, a Grade 12 HDSB student, and I lead the Data unit and part of Linear Relations.\n\nI'm particularly passionate about making statistics feel intuitive — it's everywhere in daily life and usually taught in the most abstract way possible. I want students to leave this camp able to look at any graph or dataset and actually understand what it's telling them.\n\nOutside of math, I play competitive Scrabble (yes, that's a thing).",
     "transcript": "Grade 12 HDSB student. 95% MDM4U, 94% MHF4U. 3 years peer tutoring at Abbey Park."},
    {"id": "riley-singh", "category": "teaching_assistants",
     "name": "Riley Singh", "role": "Teaching Assistant",
     "image": "placeholder_team_riley_singh_headshot.png",
     "quote": "Stuck on a problem? That's where it gets interesting.",
     "age": "16", "school": "Abbey Park High School — Grade 11",
     "gender": "Male", "pronouns": "he / him",
     "interests": "Calculators, coding, ultimate frisbee.",
     "bio": "Grade 11 HDSB student and camp TA. My job is to sit alongside you during practice periods when you're stuck on a problem. I was a Grade 9 here two years ago — I remember exactly what MTH1W feels like the first time, and I'm here to make that easier for you.",
     "transcript": "Grade 11 HDSB student. 94% MCR3U. MTH1W: 96%. MPM2D: 95%."},
    {"id": "sam-rivera", "category": "general_staff",
     "name": "Sam Rivera", "role": "Logistics & Outreach",
     "image": "placeholder_team_sam_rivera_headshot.png",
     "quote": "Behind every smooth camp day is a spreadsheet I'll never show you.",
     "age": "16", "school": "Abbey Park High School — Grade 11",
     "gender": "Non-binary", "pronouns": "they / them",
     "interests": "Statistics, economics, sports analytics, project management.",
     "bio": "I'm Sam — Grade 11 HDSB student and the person keeping this whole operation organized.\n\nSchedules, school partnerships, parent communication, day-of logistics — that's all me. I'll be the voice behind most of the emails your family receives from us.\n\nI'm less of a pure math person and more of a 'math-adjacent' one; I love stats, data viz, and how math shows up in economics and sports.",
     "transcript": ""},
    {"id": "leo-zhang", "category": "general_staff",
     "name": "Leo Zhang", "role": "Tech & Resources",
     "image": "placeholder_team_leo_zhang_headshot.png",
     "quote": "Nothing satisfies me more than a clean solution to a hard problem.",
     "age": "16", "school": "Abbey Park High School — Grade 11",
     "gender": "Male", "pronouns": "he / him",
     "interests": "Algorithms, cryptography, Project Euler, restoring old graphing calculators.",
     "bio": "I'm Leo — Grade 11 HDSB student in charge of anything that involves a screen. This website, the problem set PDFs, and the online practice portal are all my work.\n\nI love the overlap between math and computer science, especially algorithms, cryptography, and number theory. If you notice a weird typo or broken link, please let me know.",
     "transcript": ""},
    {"id": "ms-thompson", "category": "supervisors",
     "name": "Ms. Thompson", "role": "Faculty Advisor",
     "image": "placeholder_team_ms_thompson_headshot.png",
     "quote": "These students built something I couldn't have imagined at their age.",
     "age": "42", "school": "Abbey Park High School — Math Department",
     "gender": "Female", "pronouns": "she / her",
     "interests": "Teaching, mentorship, gardening, crime novels.",
     "bio": "I've been teaching math at Abbey Park High School for 14 years, most of them with a Grade 9 class on my timetable.\n\nWhen Alex first pitched me this camp in October, I said yes before they'd finished the sentence. My role here is purely supervisory — I provide oversight, safety, and a quiet presence, but everything you see here was built by the students.",
     "transcript": "Ontario Certified Teacher (OCT). B.Sc. Mathematics, University of Toronto. B.Ed., York University. 14 years teaching secondary math."},
    {"id": "mr-daniels", "category": "partners",
     "name": "Mr. Daniels", "role": "HDSB Curriculum Liaison",
     "image": "placeholder_team_mr_daniels_headshot.png",
     "quote": "Student-led programs like this are exactly what modern education needs.",
     "age": "48", "school": "Halton District School Board",
     "gender": "Male", "pronouns": "he / him",
     "interests": "Curriculum development, community programs, chess.",
     "bio": "Curriculum coordinator at HDSB. I connected this team with board resources, reviewed their MTH1W alignment against the Ontario curriculum expectations, and helped secure space at Abbey Park.",
     "transcript": ""},
]


def _seed(conn):
    """Insert defaults. Roles are upserted so new built-in roles roll out
    even on DBs that were already seeded earlier. Other tables only seed
    if empty."""
    conn.executemany(
        "INSERT OR IGNORE INTO roles (id, name, icon, color, description, special) VALUES (:id, :name, :icon, :color, :description, :special)",
        DEFAULT_ROLES,
    )

    cur = conn.execute("SELECT COUNT(*) AS n FROM base_stat_categories")
    if cur.fetchone()["n"] == 0:
        conn.executemany(
            "INSERT INTO base_stat_categories (id, name, icon, pointsPerUnit, position) VALUES (:id, :name, :icon, :pointsPerUnit, :position)",
            DEFAULT_BASE_STATS,
        )
    # Built-ins are upserted every boot so they appear on databases that
    # were seeded before the stat existed. Note the same caveat as roles:
    # deleting one of these on the admin page brings it back on restart.
    conn.executemany(
        "INSERT OR IGNORE INTO base_stat_categories (id, name, icon, pointsPerUnit, position) VALUES (:id, :name, :icon, :pointsPerUnit, :position)",
        BUILTIN_BASE_STATS,
    )

    cur = conn.execute("SELECT COUNT(*) AS n FROM staff")
    if cur.fetchone()["n"] == 0:
        rows = []
        for i, s in enumerate(DEFAULT_STAFF):
            r = dict(s)
            r["position"] = i
            rows.append(r)
        conn.executemany(
            """INSERT INTO staff
               (id, category, name, role, image, quote, age, school, gender, pronouns, interests, bio, transcript, position)
               VALUES
               (:id, :category, :name, :role, :image, :quote, :age, :school, :gender, :pronouns, :interests, :bio, :transcript, :position)""",
            rows,
        )


# ── JSON helpers ──────────────────────────────────────────────────────

def row_to_student(r):
    if r is None:
        return None
    d = dict(r)
    # Decrypt PII columns transparently (no-op on plaintext / when no key).
    for k in STUDENT_ENC_FIELDS:
        if k in d:
            d[k] = crypto.dec(d[k])
    for k in ("stats", "roles", "baseStats", "extras"):
        try:
            d[k] = json.loads(d.get(k) or ("[]" if k == "roles" else "{}"))
        except Exception:
            d[k] = [] if k == "roles" else {}
    extras = d.pop("extras", {}) or {}
    if isinstance(extras, dict):
        for k, v in extras.items():
            d.setdefault(k, v)
    d["frozen"] = bool(d.get("frozen"))
    return d


def row_to_class(r):
    if r is None:
        return None
    d = dict(r)
    return d


def row_to_role(r):
    if r is None:
        return None
    d = dict(r)
    d["special"] = bool(d.get("special"))
    return d


def row_to_basestat(r):
    if r is None:
        return None
    return dict(r)


def row_to_tx(r):
    if r is None:
        return None
    return dict(r)


def row_to_staff(r):
    if r is None:
        return None
    d = dict(r)
    # transcriptFile is stored as JSON text; deserialize so the
    # frontend can read d.transcriptFile.data and .name directly.
    raw = d.get("transcriptFile")
    if raw:
        try:
            d["transcriptFile"] = json.loads(raw)
        except (TypeError, ValueError):
            d["transcriptFile"] = None
    else:
        d["transcriptFile"] = None
    return d
