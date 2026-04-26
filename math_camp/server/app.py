"""HigherGrade Tutoring API — Flask + SQLite.

Replaces the browser-localStorage data layer with a real server-side store.
All endpoints are mounted under /api/* so Caddy can reverse-proxy just that
prefix while continuing to serve the static site directly.
"""

import json
import os
import secrets
import time
from functools import wraps

from flask import Flask, g, jsonify, request, make_response

from db import (
    connect, init_db,
    row_to_student, row_to_class, row_to_role,
    row_to_basestat, row_to_tx, row_to_staff,
)

ADMIN_PASSCODE = os.environ.get("HIGHERGRADE_ADMIN_PASSCODE", "HigherGrade Tutoring")
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
DOOR_MAZE_LENGTH       = 300
MONEY_TREE_COST        = 6000

# Tiered reward for completing the 300-door math maze. The pct is
# (correct / total) * 100. First entry wins; entries are inclusive.
DOOR_REWARD_TIERS = [
    (100, 2100),
    ( 95, 1900),
    ( 90, 1700),
    ( 85, 1500),
    ( 80, 1300),
    ( 75, 1100),
    ( 70,  900),
    ( 65,  700),
    ( 60,  500),
    ( 55,  300),
    ( 50,  100),
]

def reward_for_pct(pct):
    for cutoff, pts in DOOR_REWARD_TIERS:
        if pct >= cutoff:
            return pts
    return 0
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
        g.session = s
        return fn(*a, **kw)
    return wrapper


# ── Route registry ────────────────────────────────────────────────────

def register_routes(app):

    # health
    @app.route("/api/health")
    def health():
        return jsonify(ok=True, ts=int(time.time()))

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
        row = g.db.execute(
            "SELECT * FROM students WHERE LOWER(TRIM(studentEmail)) = ? AND password = ?",
            (email, pwd),
        ).fetchone()
        if not row:
            return jsonify(ok=False, error="No matching account"), 401
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
        s = _normalize_student(data)
        _insert_student(s)
        return jsonify(ok=True, data=s)

    @app.route("/api/students", methods=["PUT"])
    @require_admin
    def replace_students():
        data = request.get_json(silent=True) or {}
        arr = data.get("students") or []
        if not isinstance(arr, list):
            return jsonify(ok=False, error="Body must be { students: [...] }"), 400
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
            from_stats["privatePoints"]  = cur - amount
            from_stats["pointExchanges"] = from_stats.get("pointExchanges", 0) + 1
            to_stats["privatePoints"]    = to_stats.get("privatePoints", 0) + received
            to_stats["totalPointsEarned"] = to_stats.get("totalPointsEarned", 0) + received

            g.db.execute("UPDATE students SET stats = ? WHERE id = ?", (json.dumps(from_stats), from_id))
            g.db.execute("UPDATE students SET stats = ? WHERE id = ?", (json.dumps(to_stats), to_id))

            from_name = _full_name(from_row)
            to_name   = _full_name(to_row)
            _log_tx(type="transfer_out", scope="student", subjectId=from_id,
                    subjectName=from_name, relatedId=to_id, relatedName=to_name,
                    amount=-amount,
                    description=f"Sent {amount} pts to {to_name} · {amount-received} pts lost in transfer")
            _log_tx(type="transfer_in", scope="student", subjectId=to_id,
                    subjectName=to_name, relatedId=from_id, relatedName=from_name,
                    amount=received,
                    description=f"Received {received} pts from {from_name} ({amount} sent, 50% kept)")

        return jsonify(ok=True, data={"sent": amount, "received": received, "lost": amount - received})

    @app.route("/api/students/me/luck", methods=["POST"])
    @require_student
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
    def auto_click():
        """Fired by the student-portal once per minute per clicker level
        (frontend setInterval). Server-validates that the student holds
        the clicker role and enforces a per-level daily cap, separate
        from the manual cap."""
        sid = g.session["studentId"]
        today = time.strftime("%Y-%m-%d", time.gmtime())
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
            stats["autoClickerClicks"] = stats.get("autoClickerClicks", 0) + 1

            earned, capped = 0, False
            if stats["autoClickerClicks"] % CLICKER_RATE == 0:
                if stats.get("dailyAutoPts", 0) >= cap:
                    capped = True
                else:
                    earned = 1
                    stats["dailyAutoPts"]        = stats.get("dailyAutoPts", 0) + 1
                    stats["privatePoints"]       = stats.get("privatePoints", 0) + 1
                    stats["totalPointsEarned"]   = stats.get("totalPointsEarned", 0) + 1
                    stats["clickerPointsEarned"] = stats.get("clickerPointsEarned", 0) + 1

            g.db.execute("UPDATE students SET stats = ? WHERE id = ?", (json.dumps(stats), sid))
            if earned > 0:
                _log_tx(type="clicker", scope="student", subjectId=sid,
                        subjectName=_full_name(row), amount=earned,
                        description=f"Auto-clicker (Lv {level}) +{earned} pt")
        return jsonify(ok=True, data={
            "earned": earned, "level": level, "capped": capped,
            "autoClickerClicks": stats["autoClickerClicks"],
            "dailyAutoPts": stats.get("dailyAutoPts", 0),
            "autoCap": cap,
        })

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
            _log_tx(type="earn", scope="student", subjectId=sid,
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
        if total < DOOR_MAZE_LENGTH:
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

    @app.route("/api/students/me/claim-money-tree", methods=["POST"])
    @require_student
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
            _log_tx(type="earn", scope="student", subjectId=sid,
                    subjectName=_full_name(my_row), amount=0,
                    description="🌳 Claimed the Money Tree (criss-cross door pattern)")
        return jsonify(ok=True)

    @app.route("/api/students/me/money-tree/activate", methods=["POST"])
    @require_student
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
            _log_tx(type="earn", scope="student", subjectId=sid,
                    subjectName=_full_name(row), amount=new_priv - cur,
                    description=f"🌳 Money Tree activated · spent {MONEY_TREE_COST}, doubled remainder · {cur} → {new_priv}")
        return jsonify(ok=True, data={"newPrivatePoints": new_priv})

    @app.route("/api/students/me/money-tree/gift", methods=["POST"])
    @require_student
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
            _log_tx(type="earn", scope="student", subjectId=sid,
                    subjectName=_full_name(from_row),
                    relatedId=to_id, relatedName=_full_name(to_row),
                    amount=0, description=f"🌳 Gifted Money Tree to {_full_name(to_row)}")
        return jsonify(ok=True)

    @app.route("/api/students/me/mazewiz", methods=["POST"])
    @require_student
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
            _log_tx(type="earn", scope="student", subjectId=sid,
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
                g.db.execute(
                    """INSERT INTO staff
                       (id, category, name, role, image, quote, age, school, gender, pronouns, interests, bio, transcript, position)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (s["id"], s.get("category") or "", s.get("name") or "",
                     s.get("role") or "", s.get("image") or "", s.get("quote") or "",
                     s.get("age") or "", s.get("school") or "",
                     s.get("gender") or "", s.get("pronouns") or "",
                     s.get("interests") or "", s.get("bio") or "",
                     s.get("transcript") or "", i),
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
                    g.db.execute(
                        """INSERT INTO staff
                           (id, category, name, role, image, quote, age, school, gender, pronouns, interests, bio, transcript, position)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (s["id"], s.get("category") or "", s.get("name") or "",
                         s.get("role") or "", s.get("image") or "", s.get("quote") or "",
                         s.get("age") or "", s.get("school") or "",
                         s.get("gender") or "", s.get("pronouns") or "",
                         s.get("interests") or "", s.get("bio") or "",
                         s.get("transcript") or "", i),
                    )
                imported["staff"] = len(data["staff"])
        return jsonify(ok=True, imported=imported)

    # ── Dev reset ──────────────────────────────────────────────────
    @app.route("/api/admin/reset", methods=["POST"])
    @require_admin
    def dev_reset():
        with g.db:
            g.db.execute("DELETE FROM students")
            g.db.execute("DELETE FROM classes")
            g.db.execute("DELETE FROM transactions")
            g.db.execute("DELETE FROM base_stat_categories")
            g.db.execute("DELETE FROM roles")
            g.db.execute("DELETE FROM sessions WHERE kind = 'student'")
        # Re-seed defaults for the empty tables
        from db import _seed
        with g.db:
            _seed(g.db)
        return jsonify(ok=True)


# ── Helpers ───────────────────────────────────────────────────────────

KNOWN_STUDENT_COLS = {
    "id", "firstName", "lastName", "studentEmail", "password",
    "parentEmail", "phone", "school", "grade",
    "classId", "className", "registeredAt",
}

def _normalize_student(raw):
    if not raw.get("id"):
        raw["id"] = "student-" + str(int(time.time() * 1000)) + "-" + secrets.token_hex(3)
    s = {k: raw.get(k) for k in KNOWN_STUDENT_COLS}
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
            stats, roles, baseStats, extras)
           VALUES
           (:id, :firstName, :lastName, :studentEmail, :password, :parentEmail, :phone,
            :school, :grade, :classId, :className, :registeredAt,
            :stats, :roles, :baseStats, :extras)""",
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
