"""
GRADE 9 FLOOR QUESTIONS — what's actually behind the dungeon's doors.
--------------------------------------------------------------------
Every floor of the dungeon asks one Grade 9 question. The five units are
the same five the door maze walks through, so a camper meets the same
curriculum going down as they did going across:

    Unit 1  Grade 8 Review        Unit 4  Geometry
    Unit 2  Algebra               Unit 5  Linear Relations
    Unit 3  Data

The dungeon has no bottom, so the floors can't be a fixed list. Instead
the units cycle ten floors at a time — floors 1-10 are Unit 1, 11-20 are
Unit 2, and so on — which means every fifty floors is one full lap of the
course. Each lap the numbers get bigger: `tier` is the lap count, and it
widens the ranges the generators draw from without changing the shape of
the question. Floor 3 and floor 253 ask the same KIND of thing; floor 253
asks it with uglier numbers.

Nothing in here is above Grade 9, and nothing in here is below it either
once the tiers kick in — the ranges grow, the concepts don't.

Questions are generated per request rather than stored. The answer never
leaves this process: `generate()` hands the caller both sides and which
one is right, and the caller records only the side.
"""
import secrets


# ── Random helpers (secrets, not random — these decide points) ────────
def rint(lo, hi):
    """Inclusive integer in [lo, hi]."""
    if hi < lo:
        lo, hi = hi, lo
    return lo + secrets.randbelow(hi - lo + 1)


def pick(seq):
    seq = list(seq)
    return seq[secrets.randbelow(len(seq))]


UNIT_LABELS = [
    "Unit 1 · Grade 8 Review",
    "Unit 2 · Algebra",
    "Unit 3 · Data",
    "Unit 4 · Geometry",
    "Unit 5 · Linear Relations",
]

FLOORS_PER_UNIT = 10          # ten floors of a unit before the next one
UNITS = len(UNIT_LABELS)
LAP = FLOORS_PER_UNIT * UNITS  # 50 floors is one full pass of the course
TIER_MAX = 6                   # ranges stop growing here; floor 300+ is plenty


def unit_for_floor(floor):
    return ((max(1, int(floor)) - 1) // FLOORS_PER_UNIT) % UNITS


def tier_for_floor(floor):
    """How many full laps of the curriculum are behind you. Widens the
    number ranges; capped so floor 900 isn't arithmetic nobody can do in
    a four-second window."""
    return min(TIER_MAX, (max(1, int(floor)) - 1) // LAP)


def _scale(tier):
    """Multiplier on the size of the numbers a generator draws."""
    return 1 + tier


# ── Unit 1: Grade 8 review ────────────────────────────────────────────
def _unit1(t):
    s = _scale(t)
    kind = pick(["add", "sub", "mul", "div", "square", "sqrt",
                 "percent", "order", "neg", "frac_of"])
    if kind == "add":
        a, b = rint(15, 99 * s), rint(10, 99 * s)
        return f"{a} + {b}", a + b
    if kind == "sub":
        a = rint(40, 120 * s)
        b = rint(5, a - 1)
        return f"{a} − {b}", a - b
    if kind == "mul":
        a, b = rint(2, 12 + 3 * t), rint(2, 12 + 3 * t)
        return f"{a} × {b}", a * b
    if kind == "div":
        b, ans = rint(2, 12 + 2 * t), rint(2, 12 + 2 * t)
        return f"{ans * b} ÷ {b}", ans
    if kind == "square":
        a = rint(3, 13 + 2 * t)
        return f"{a}²", a * a
    if kind == "sqrt":
        ans = rint(3, 12 + 2 * t)
        return f"√{ans * ans}", ans
    if kind == "percent":
        pct = pick([10, 20, 25, 50, 75])
        v = rint(2, 10 * s) * 4
        return f"{pct}% of {v}", v * pct // 100
    if kind == "order":
        a, b, c = rint(2, 9 * s), rint(2, 9), rint(2, 9)
        return f"{a} + {b} × {c}", a + b * c
    if kind == "neg":
        a, b = rint(1, 20 * s), rint(1, 20 * s)
        return f"−{a} + {b}", -a + b
    denom = pick([2, 3, 4, 5, 6])
    num = rint(1, denom - 1)
    v = denom * rint(2, 12 * s)
    return f"{num}/{denom} of {v}", v * num // denom


# ── Unit 2: Algebra ───────────────────────────────────────────────────
def _unit2(t):
    s = _scale(t)
    kind = pick(["solve1", "solve_sub", "solve2", "evaluate",
                 "distrib", "like_terms", "two_step"])
    if kind == "solve1":
        x, b = rint(1, 25 * s), rint(1, 30 * s)
        return f"x + {b} = {x + b},  x = ?", x
    if kind == "solve_sub":
        x, b = rint(1, 25 * s), rint(1, 25 * s)
        return f"x − {b} = {x - b},  x = ?", x
    if kind == "solve2":
        x, a, b = rint(1, 12 + 2 * t), rint(2, 8), rint(1, 20 * s)
        return f"{a}x + {b} = {a * x + b},  x = ?", x
    if kind == "evaluate":
        x, a, b = rint(2, 10 + 2 * t), rint(2, 9), rint(1, 15 * s)
        return f"If x = {x}, then {a}x + {b} = ?", a * x + b
    if kind == "distrib":
        a, b, c = rint(2, 8), rint(2, 12 * s), rint(2, 10 * s)
        return f"{a}({b} + {c}) = ?", a * (b + c)
    if kind == "like_terms":
        a, b, x = rint(2, 8), rint(2, 8), rint(2, 6 + t)
        return f"If x = {x}, then {a}x + {b}x = ?", (a + b) * x
    # Two-step with a subtraction, which is where Grade 9 usually trips.
    x, a, b = rint(2, 12 + 2 * t), rint(2, 9), rint(1, 20 * s)
    return f"{a}x − {b} = {a * x - b},  x = ?", x


# ── Unit 3: Data ──────────────────────────────────────────────────────
def _unit3(t):
    s = _scale(t)
    kind = pick(["mean3", "mean5", "median", "mode", "range", "flips"])
    if kind in ("mean3", "mean5"):
        n = 3 if kind == "mean3" else 5
        nums = [rint(2, 30 * s) for _ in range(n)]
        # Nudge the first value so the mean lands on a whole number —
        # both doors show integers, so a fractional mean is unanswerable.
        nums[0] += (n - sum(nums) % n) % n
        return f"Mean of {', '.join(map(str, nums))}", sum(nums) // n
    if kind == "median":
        nums = [rint(1, 30 * s) for _ in range(5)]
        ans = sorted(nums)[2]
        return f"Median of {', '.join(map(str, nums))}", ans
    if kind == "mode":
        m = rint(2, 15 * s)
        others = []
        while len(others) < 3:
            v = rint(1, 20 * s)
            if v != m and v not in others:
                others.append(v)
        nums = [m, m] + others
        # Shuffle so the repeat isn't always sitting at the front.
        for i in range(len(nums) - 1, 0, -1):
            j = secrets.randbelow(i + 1)
            nums[i], nums[j] = nums[j], nums[i]
        return f"Mode of {', '.join(map(str, nums))}", m
    if kind == "range":
        nums = [rint(1, 50 * s) for _ in range(5)]
        return f"Range of {', '.join(map(str, nums))}", max(nums) - min(nums)
    n = rint(2, 4 + min(2, t))
    return f"Total possible outcomes flipping {n} coins", 2 ** n


# ── Unit 4: Geometry ──────────────────────────────────────────────────
TRIPLES = [(3, 4, 5), (6, 8, 10), (5, 12, 13), (9, 12, 15),
           (8, 15, 17), (9, 40, 41), (7, 24, 25), (20, 21, 29)]


def _unit4(t):
    s = _scale(t)
    kind = pick(["rect_area", "square_area", "rect_perim", "tri_area",
                 "volume", "pyth", "angle_sum", "comp_sup", "circle_ish"])
    if kind == "rect_area":
        w, h = rint(3, 15 * s), rint(3, 15 * s)
        return f"Area of rectangle {w} × {h}", w * h
    if kind == "square_area":
        side = rint(3, 14 + 2 * t)
        return f"Area of square with side {side}", side * side
    if kind == "rect_perim":
        w, h = rint(3, 20 * s), rint(3, 20 * s)
        return f"Perimeter of rectangle {w} × {h}", 2 * (w + h)
    if kind == "tri_area":
        b, h = rint(2, 12 * s) * 2, rint(2, 14 * s)
        return f"Area of triangle (base {b}, height {h})", b * h // 2
    if kind == "volume":
        a, b, c = rint(2, 8 + t), rint(2, 8 + t), rint(2, 8 + t)
        return f"Volume of box {a}×{b}×{c}", a * b * c
    if kind == "pyth":
        leg_a, leg_b, hyp = pick(TRIPLES)
        return (f"Hypotenuse of right triangle with legs {leg_a} and {leg_b}", hyp)
    if kind == "angle_sum":
        a, b = rint(20, 80), rint(20, 79)
        while a + b >= 175:
            a, b = rint(20, 80), rint(20, 79)
        return f"Third angle of triangle with angles {a}° and {b}°", 180 - a - b
    if kind == "comp_sup":
        a = rint(20, 80)
        if secrets.randbelow(2):
            return f"Complement of {a}°", 90 - a
        return f"Supplement of {a}°", 180 - a
    # Circumference/area of a circle to the nearest whole unit is a
    # calculator question; the diameter/radius relation is not.
    r = rint(2, 20 * s)
    return f"Diameter of a circle with radius {r}", 2 * r


# ── Unit 5: Linear relations ──────────────────────────────────────────
def _unit5(t):
    s = _scale(t)
    kind = pick(["evaluate_y", "find_x", "slope_2pt", "y_intercept",
                 "point_on_line", "x_intercept"])
    if kind == "evaluate_y":
        m, b, x = rint(1, 6 + t), rint(1, 12 * s), rint(1, 8 + t)
        return f"In y = {m}x + {b}, find y when x = {x}", m * x + b
    if kind == "find_x":
        m, b, x = rint(2, 5 + t), rint(1, 10 * s), rint(1, 8 + t)
        return f"In y = {m}x + {b}, find x when y = {m * x + b}", x
    if kind == "slope_2pt":
        m, x1, y1, dx = rint(1, 5 + t), rint(0, 5 * s), rint(0, 10 * s), rint(1, 4)
        return (f"Slope of line through ({x1}, {y1}) and ({x1 + dx}, {y1 + m * dx})", m)
    if kind == "y_intercept":
        m, b = rint(1, 6 + t), rint(1, 15 * s)
        return f"y-intercept of y = {m}x + {b}", b
    if kind == "x_intercept":
        # Kept whole: y = mx − mk crosses the x-axis at k.
        m, k = rint(2, 6 + t), rint(1, 9 + t)
        return f"x-intercept of y = {m}x − {m * k}", k
    m, b, x = rint(1, 4 + t), rint(1, 10 * s), rint(1, 6 + t)
    return f"Line y = {m}x + {b} passes through ({x}, ?). The y-value is", m * x + b


UNIT_GENS = (_unit1, _unit2, _unit3, _unit4, _unit5)


# ── Decoys ────────────────────────────────────────────────────────────
def wrong_answer(correct):
    """A plausible near-miss. Scaled off the answer so a decoy next to
    2,184 isn't obviously the small one, and never equal to the answer."""
    span = max(3, min(round(abs(correct) * 0.2), 40))
    for _ in range(16):
        delta = rint(1, span) * (1 if secrets.randbelow(2) else -1)
        if delta and correct + delta != correct:
            return correct + delta
    return correct + 1


def generate(floor):
    """One floor's question.

    Returns (question, correct, wrong, unit_label). The caller decides
    which door gets which value and records only the side — the answer
    itself is never handed to the browser.
    """
    floor = max(1, int(floor or 1))
    unit = unit_for_floor(floor)
    tier = tier_for_floor(floor)
    question, correct = UNIT_GENS[unit](tier)
    wrong = wrong_answer(correct)
    label = UNIT_LABELS[unit]
    if tier:
        # Same course, deeper lap — say so, so the ramp reads as intentional.
        label += f" · lap {tier + 1}"
    return question, int(correct), int(wrong), label
