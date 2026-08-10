"""
GRADE 9 FLOOR QUESTIONS — what's actually behind the dungeon's doors.
--------------------------------------------------------------------
Ontario Grade 9 mathematics (MTH1W), five strands, one question per floor:

    Number                   powers and exponent laws, scientific notation,
                             rationals and irrationals, ratio, rate, percent
    Algebra                  expressions and terms, multi-step equations,
                             inequalities, patterns, exponential growth
                             and decay, coding traces
    Data                     central tendency, quartiles and box plots,
                             dispersion, outliers, sampling, scatter plots
    Geometry & Measurement   composite shapes, prisms, cones, Pythagoras,
                             the Cartesian plane, transformations
    Financial Literacy       budgets, appreciation and depreciation,
                             simple and compound interest

The dungeon has no bottom, so the floors can't be a fixed list. The strands
cycle ten floors at a time — 1-10 Number, 11-20 Algebra, and so on — which
makes fifty floors one full lap of the course. `tier` counts the laps, and
it does two things: it widens the numbers, and it unlocks the harder
question types within each strand. Floor 5 asks you to evaluate a power.
Floor 255 asks you to apply the exponent laws to a quotient of powers with
a negative result. The course is the same; how much of it you have to hold
in your head at once is not.

Two doors means two answers, so every question resolves to a single value.
The DECOY is the point of difficulty here, not the arithmetic: it is the
answer you get from the specific mistake the question invites — the sign
error, the radius used as a diameter, the cone volume without the third,
the exponent added where it should have been multiplied. A wrong door is
never the obviously-small number next to the obviously-right one, so
guessing from shape doesn't work and the only way through is to do it.

Nothing here is above Grade 9. It is, deliberately, all of Grade 9.
"""
import math
import secrets


# ── Random helpers (secrets, not random — these decide points) ────────
def rint(lo, hi):
    """Inclusive integer in [lo, hi]."""
    lo, hi = int(lo), int(hi)
    if hi < lo:
        lo, hi = hi, lo
    return lo + secrets.randbelow(hi - lo + 1)


def pick(seq):
    seq = list(seq)
    return seq[secrets.randbelow(len(seq))]


def coin():
    return secrets.randbelow(2) == 0


def shuffled(seq):
    out = list(seq)
    for i in range(len(out) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        out[i], out[j] = out[j], out[i]
    return out


def num(x):
    """Render an answer for a door. Whole numbers stay whole; everything
    else lands on two decimals with no trailing noise."""
    if isinstance(x, str):
        return x
    if isinstance(x, int) or float(x).is_integer():
        return str(int(x))
    return f"{round(float(x), 2):g}"


STRAND_LABELS = [
    "Number",
    "Algebra",
    "Data",
    "Geometry & Measurement",
    "Financial Literacy",
]

FLOORS_PER_STRAND = 10
STRANDS = len(STRAND_LABELS)
LAP = FLOORS_PER_STRAND * STRANDS   # fifty floors is one pass of the course
TIER_MAX = 5


def strand_for_floor(floor):
    return ((max(1, int(floor)) - 1) // FLOORS_PER_STRAND) % STRANDS


def tier_for_floor(floor):
    """Laps completed. Widens the numbers and unlocks the harder question
    types; capped so floor 900 isn't unreadable rather than difficult."""
    return min(TIER_MAX, (max(1, int(floor)) - 1) // LAP)


# ══ NUMBER ════════════════════════════════════════════════════════════
def n_power_value(t):
    """Evaluate a power. Decoy: base × exponent, the classic."""
    b = rint(2, 5 + t)
    e = rint(2, 3 + min(2, t))
    return f"{b}^{e}", b ** e, b * e


def n_percent_of(t):
    p = pick([5, 12, 15, 18, 24, 35, 45, 60, 80])
    v = rint(2, 20 + 20 * t) * 20
    # Decoy: the decimal one place out — dividing by 10 instead of 100.
    return f"{p}% of {v}", v * p / 100, v * p / 10


def n_ratio_share(t):
    """Share a total in a ratio. Decoy: the other share."""
    a, b = rint(2, 5), rint(2, 7)
    while a == b or math.gcd(a, b) != 1:
        a, b = rint(2, 5), rint(2, 7)
    parts = rint(3, 9 + 3 * t)
    total = (a + b) * parts
    return (f"${total} is split in the ratio {a}:{b}. The larger share is",
            max(a, b) * parts, min(a, b) * parts)


def n_unit_rate(t):
    """Unit rate, then scaled back up — two steps, not one. Decoy: the
    unit rate itself, which is as far as most people get."""
    n = pick([4, 5, 6, 8, 12])
    per = rint(3, 15 + 8 * t)
    m = n + rint(2, 6)
    return (f"{n} identical items cost ${n * per}. At that rate {m} cost",
            m * per, per)


def n_integer_order(t):
    a, b, c = rint(2, 6 + t), rint(2, 9 + 2 * t), rint(2, 5)
    # −a² + b×c: the decoy is (−a)², the sign error everybody makes.
    return (f"−{a}² + {b} × {c}", -(a ** 2) + b * c, (a ** 2) + b * c)


def n_exponent_law_product(t):
    """a^m × a^n. Decoy: exponents multiplied instead of added."""
    a = rint(2, 4)
    m, n = rint(2, 4), rint(2, 4)
    return (f"Simplify {a}^{m} × {a}^{n}. The exponent is", m + n, m * n)


def n_exponent_law_quotient(t):
    """a^m ÷ a^n. Decoy: exponents divided."""
    a = rint(2, 5)
    n = rint(2, 4)
    m = n + rint(2, 5)
    return (f"Simplify {a}^{m} ÷ {a}^{n}. The exponent is", m - n, m + n)


def n_exponent_power_of_power(t):
    a = rint(2, 5)
    m, n = rint(2, 4), rint(2, 4)
    return (f"Simplify ({a}^{m})^{n}. The exponent is", m * n, m + n)


def n_negative_exponent(t):
    """a^−n as a fraction: give the denominator. Decoy: −a^n."""
    a = rint(2, 5)
    n = rint(2, 3)
    return (f"{a}^−{n} = 1/? — the denominator is", a ** n, -(a ** n))


def n_scientific_notation(t):
    """Write a number in scientific notation: give the exponent."""
    d = rint(1, 9)
    dec = rint(10, 99)
    zeros = rint(3, 8)
    value = f"{d}{dec}" + "0" * zeros
    exp = len(value) - 1
    return (f"{int(value):,} in scientific notation is a × 10^? — the exponent is",
            exp, exp + 1)      # decoy: counting the zeros, not the places


def n_sci_small(t):
    zeros = rint(2, 5)
    tail = rint(11, 99)
    value = "0." + "0" * zeros + str(tail)
    exp = -(zeros + 1)
    return (f"{value} in scientific notation is a × 10^? — the exponent is",
            exp, -zeros)


def n_rational_or_not(t):
    """Straight recall, and the doors are words rather than numbers."""
    irrational = [("√2", "√2"), ("√{}".format(pick([3, 5, 7, 10, 11])), None),
                  ("π", "π"), ("√50", None), ("2π", None)]
    rational = [("√{}".format(pick([4, 9, 16, 25, 36, 49, 64, 81])), None),
                ("0.75", None), ("−7", None), ("2/3", None), ("0.3333…", None),
                ("1.7", None)]
    if coin():
        v = pick(irrational)[0]
        return f"Is {v} rational or irrational?", "irrational", "rational"
    v = pick(rational)[0]
    return f"Is {v} rational or irrational?", "rational", "irrational"


def n_percent_change(t):
    start = rint(2, 12 + 6 * t) * 25
    pct = pick([10, 15, 20, 25, 40])
    up = coin()
    end = start * (100 + pct) // 100 if up else start * (100 - pct) // 100
    word = "increases" if up else "decreases"
    # Decoy: applying the change the other way.
    other = start * (100 - pct) // 100 if up else start * (100 + pct) // 100
    return (f"{start} {word} by {pct}%. The new value is", end, other)


def n_reverse_percent(t):
    """After a discount, find the original. Decoy: adding the % back on."""
    pct = pick([10, 20, 25, 40])
    orig = rint(2, 10 + 4 * t) * 20
    after = orig * (100 - pct) // 100
    return (f"After a {pct}% discount an item costs ${after}. Before the discount it was",
            orig, after * (100 + pct) // 100)


NUMBER = [
    (0, n_power_value), (0, n_percent_of), (0, n_integer_order), (0, n_unit_rate),
    (0, n_ratio_share), (0, n_rational_or_not),
    (1, n_exponent_law_product), (1, n_exponent_law_quotient), (1, n_percent_change),
    (2, n_exponent_power_of_power), (2, n_scientific_notation), (2, n_reverse_percent),
    (3, n_negative_exponent), (3, n_sci_small),
]


# ══ ALGEBRA ═══════════════════════════════════════════════════════════
def a_collect_like_terms(t):
    a, b = rint(3, 9 + 2 * t), rint(2, 8 + 2 * t)
    c, d = rint(2, 7), rint(2, 7)
    # (ax + c) + (bx − d): coefficient of x. Decoy: everything added.
    return (f"Simplify ({a}x + {c}) + ({b}x − {d}). The coefficient of x is",
            a + b, a + b + c - d)


def a_expand(t):
    a, b, c = rint(2, 6 + t), rint(2, 8 + 2 * t), rint(2, 9 + 2 * t)
    # a(bx + c): the constant term. Decoy: c left undistributed.
    return (f"Expand {a}({b}x + {c}). The constant term is", a * c, c)


def a_expand_two(t):
    a, b, c, d = rint(2, 5 + t), rint(2, 6), rint(2, 5 + t), rint(2, 6)
    # a(bx + 1) − c(dx + 1): coefficient of x. Decoy: the minus dropped.
    return (f"Simplify {a}({b}x + 1) − {c}({d}x + 1). The coefficient of x is",
            a * b - c * d, a * b + c * d)


def a_solve_two_step(t):
    """ax − b = value. Decoy: subtracting b again instead of adding it
    back — kept whole by making b a multiple of a, so the wrong door is a
    tidy number rather than a tell-tale decimal."""
    x = rint(2, 12 + 3 * t)
    a = rint(2, 9)
    b = a * rint(1, 4 + t)
    val = a * x - b
    return (f"Solve {a}x − {b} = {val}", x, (val - b) // a)


def a_solve_both_sides(t):
    x = rint(2, 10 + 2 * t)
    a = rint(3, 8)
    b = rint(1, a - 1)
    c = rint(2, 15 + 5 * t)
    # ax + c = bx + (a−b)x... build it so both sides are honest:
    left_const = c
    right_const = (a - b) * x + c
    return (f"Solve {a}x + {left_const} = {b}x + {right_const}",
            x, right_const - left_const)      # decoy: ignoring the x on the right


def a_solve_bracket(t):
    x = rint(2, 9 + 2 * t)
    a, b = rint(2, 6), rint(1, 9 + 2 * t)
    val = a * (x + b)
    return (f"Solve {a}(x + {b}) = {val}", x, val // a + b)   # decoy: sign flipped


def a_inequality(t):
    x = rint(2, 12 + 3 * t)
    a, b = rint(2, 7), rint(1, 15 + 4 * t)
    val = a * x + b
    return (f"Solve {a}x + {b} ≤ {val}. The largest whole number x can be is",
            x, x + 1)


def a_inequality_negative(t):
    """Dividing by a negative flips the sign — the decoy is not flipping."""
    x = rint(2, 9 + 2 * t)
    a = rint(2, 6)
    val = -a * x
    return (f"Solve −{a}x ≥ {val}. The largest whole number x can be is",
            x, -x)


def a_linear_pattern(t):
    """nth term of a linear growing pattern. Decoy: off by one step."""
    first = rint(2, 12 + 4 * t)
    step = rint(3, 9 + 3 * t)
    n = rint(8, 20 + 8 * t)
    terms = ", ".join(str(first + step * i) for i in range(4))
    return (f"Pattern {terms}, … The {n}th term is",
            first + step * (n - 1), first + step * n)


def a_nonlinear_pattern(t):
    """Doubling pattern — non-linear. Decoy: treating it as linear."""
    first = rint(2, 5 + t)
    n = rint(5, 8 + min(3, t))
    terms = ", ".join(str(first * 2 ** i) for i in range(4))
    linear_step = first * 2 - first
    return (f"Pattern {terms}, … The {n}th term is",
            first * 2 ** (n - 1), first + linear_step * (n - 1))


def a_exponential_growth(t):
    start = rint(2, 8 + 3 * t) * 50
    n = rint(3, 5 + min(3, t))
    return (f"A population of {start} doubles each year. After {n} years it is",
            start * 2 ** n, start * 2 * n)      # decoy: doubled n times, added


def a_exponential_decay(t):
    """Decoy: divided by 2n instead of 2^n — halving treated as linear."""
    n = rint(2, 4 + min(2, t))
    start = 2 ** (n + rint(2, 4)) * rint(1, 3) * n
    return (f"{start} halves each hour. After {n} hours it is",
            start // 2 ** n, start // (2 * n))


def a_coding_trace(t):
    """Trace a loop — the coding strand, as a value question."""
    start = rint(1, 9 + 2 * t)
    step = rint(2, 7 + t)
    reps = rint(4, 9 + 3 * t)
    return (f"total = {start}. A loop repeats {reps} times, each time adding {step}. "
            f"total is now", start + step * reps, step * reps)


def a_intersection(t):
    """Point of intersection of two lines — give x. Decoy: the y-value."""
    x = rint(1, 8 + 2 * t)
    m1 = rint(2, 6)
    m2 = rint(1, m1 - 1) if m1 > 1 else 1
    b1 = rint(1, 10 + 3 * t)
    y = m1 * x + b1
    b2 = y - m2 * x
    c2 = "" if m2 == 1 else str(m2)
    return (f"Lines y = {m1}x + {b1} and y = {c2}x + {b2} meet. The x-value is",
            x, y)


ALGEBRA = [
    (0, a_collect_like_terms), (0, a_expand), (0, a_solve_two_step),
    (0, a_linear_pattern), (0, a_coding_trace),
    (1, a_expand_two), (1, a_solve_bracket), (1, a_inequality),
    (1, a_exponential_growth),
    (2, a_solve_both_sides), (2, a_nonlinear_pattern), (2, a_exponential_decay),
    (3, a_inequality_negative), (3, a_intersection),
]


# ══ DATA ══════════════════════════════════════════════════════════════
def _sorted_set(n, lo, hi):
    return sorted(rint(lo, hi) for _ in range(n))


def d_mean(t):
    n = pick([4, 5, 6])
    nums = [rint(2, 30 + 15 * t) for _ in range(n)]
    nums[0] += (n - sum(nums) % n) % n          # keep the mean whole
    return (f"Mean of {', '.join(map(str, shuffled(nums)))}",
            sum(nums) // n, sorted(nums)[n // 2])   # decoy: the median


def d_median_even(t):
    """Even count — the median is the average of the middle two, which is
    exactly the step people skip. Decoy: the lower middle value."""
    nums = _sorted_set(6, 1, 40 + 20 * t)
    while (nums[2] + nums[3]) % 2:
        nums = _sorted_set(6, 1, 40 + 20 * t)
    return (f"Median of {', '.join(map(str, shuffled(nums)))}",
            (nums[2] + nums[3]) // 2, nums[2])


def d_range(t):
    nums = [rint(1, 60 + 30 * t) for _ in range(6)]
    return (f"Range of {', '.join(map(str, nums))}",
            max(nums) - min(nums), max(nums))


def d_mode(t):
    m = rint(2, 20 + 10 * t)
    others = []
    while len(others) < 4:
        v = rint(1, 30 + 15 * t)
        if v != m and v not in others:
            others.append(v)
    nums = shuffled([m, m, m] + others)
    return (f"Mode of {', '.join(map(str, nums))}", m, max(nums))


def d_missing_for_mean(t):
    """Find the value that makes the mean. Genuinely a two-step problem."""
    n = pick([4, 5])
    target = rint(6, 20 + 8 * t)
    nums = [rint(2, target + 10) for _ in range(n - 1)]
    missing = target * n - sum(nums)
    while missing < 1:
        nums = [rint(1, target) for _ in range(n - 1)]
        missing = target * n - sum(nums)
    return (f"{', '.join(map(str, nums))} and one more value have a mean of "
            f"{target}. The missing value is", missing, target)


def d_quartile(t):
    """Q1 of a seven-value set — the median of the lower half. Decoy: the
    second-smallest value, which is what you get counting instead of halving."""
    nums = _sorted_set(7, 1, 50 + 25 * t)
    # Decoy: the smallest value — counting to the quarter instead of taking
    # the median of the lower half.
    return (f"First quartile (Q1) of {', '.join(map(str, shuffled(nums)))}",
            nums[1], nums[0])


def d_iqr(t):
    nums = _sorted_set(7, 1, 50 + 25 * t)
    q1, q3 = nums[1], nums[5]
    return (f"Interquartile range of {', '.join(map(str, shuffled(nums)))}",
            q3 - q1, nums[6] - nums[0])         # decoy: the full range


def d_outlier_bound(t):
    """1.5 × IQR above Q3 — the box plot rule."""
    q1 = rint(4, 20 + 8 * t)
    q3 = q1 + pick([4, 8, 12, 16, 20])
    iqr = q3 - q1
    return (f"A data set has Q1 = {q1} and Q3 = {q3}. Values above which number "
            f"are potential outliers?", q3 + 1.5 * iqr, q3 + iqr)


def d_sample_percent(t):
    pop = rint(4, 20 + 10 * t) * 50
    pct = pick([10, 20, 25, 40])
    return (f"A sample of {pct}% is taken from a population of {pop}. The sample size is",
            pop * pct // 100, pop - pop * pct // 100)


def d_scatter_slope(t):
    """Rate of change read off two points of a line of best fit."""
    m = rint(2, 9 + 3 * t)
    x1 = rint(1, 6)
    dx = rint(2, 5)
    y1 = rint(2, 20 + 8 * t)
    return (f"A line of best fit passes through ({x1}, {y1}) and "
            f"({x1 + dx}, {y1 + m * dx}). Its rate of change is",
            m, m * dx)                          # decoy: the rise, not the slope


DATA = [
    (0, d_mean), (0, d_range), (0, d_mode), (0, d_sample_percent),
    (1, d_median_even), (1, d_missing_for_mean),
    (2, d_quartile), (2, d_scatter_slope),
    (3, d_iqr), (3, d_outlier_bound),
]


# ══ GEOMETRY & MEASUREMENT ════════════════════════════════════════════
TRIPLES = [(3, 4, 5), (6, 8, 10), (5, 12, 13), (9, 12, 15),
           (8, 15, 17), (7, 24, 25), (20, 21, 29), (12, 35, 37)]


def g_triangle_area(t):
    b = rint(2, 12 + 5 * t) * 2
    h = rint(3, 16 + 6 * t)
    return (f"Area of a triangle with base {b} and height {h}",
            b * h // 2, b * h)                  # decoy: forgetting the half


def g_composite_area(t):
    """A rectangle with a square bitten out of it."""
    w, h = rint(6, 14 + 5 * t), rint(5, 12 + 5 * t)
    s = rint(2, min(w, h) - 1)
    return (f"A {w} × {h} rectangle has a {s} × {s} square cut out of it. "
            f"The remaining area is", w * h - s * s, w * h + s * s)


def g_prism_volume(t):
    a, b, c = rint(3, 9 + 3 * t), rint(3, 9 + 3 * t), rint(3, 9 + 3 * t)
    return (f"Volume of a rectangle-based prism {a} × {b} × {c}",
            a * b * c, 2 * (a * b + b * c + a * c))   # decoy: surface area


def g_prism_surface(t):
    a, b, c = rint(2, 8 + 2 * t), rint(2, 8 + 2 * t), rint(2, 8 + 2 * t)
    return (f"Surface area of a rectangle-based prism {a} × {b} × {c}",
            2 * (a * b + b * c + a * c), a * b * c)


def g_triangular_prism(t):
    b, h, L = rint(2, 8) * 2, rint(3, 10 + 3 * t), rint(4, 14 + 5 * t)
    return (f"Volume of a triangle-based prism (base {b}, height {h}, length {L})",
            b * h // 2 * L, b * h * L)


def g_cone_volume(t):
    """πr²h/3 to the nearest whole unit. Decoy: the cylinder — the third
    left off, which is the mistake the formula exists to catch."""
    r = rint(2, 6 + t)
    h = rint(3, 12 + 3 * t) * 3
    cone = math.pi * r * r * h / 3
    cyl = math.pi * r * r * h
    return (f"Volume of a cone with radius {r} and height {h}, to the nearest "
            f"whole unit (use π ≈ 3.14)", round(cone), round(cyl))


def g_cylinder_from_diameter(t):
    """Given the DIAMETER. Decoy: using it as the radius."""
    d = rint(2, 7 + t) * 2
    h = rint(3, 12 + 4 * t)
    r = d / 2
    return (f"Volume of a cylinder with diameter {d} and height {h}, to the "
            f"nearest whole unit (use π ≈ 3.14)",
            round(math.pi * r * r * h), round(math.pi * d * d * h))


def g_pythagoras_hyp(t):
    a, b, c = pick(TRIPLES)
    return (f"Hypotenuse of a right triangle with legs {a} and {b}", c, a + b)


def g_pythagoras_leg(t):
    """Find a LEG, not the hypotenuse — subtraction, not addition."""
    a, b, c = pick(TRIPLES)
    return (f"A right triangle has hypotenuse {c} and one leg {a}. The other leg is",
            b, round(math.hypot(a, c)))


def g_angle_sum(t):
    a = rint(20, 80)
    b = rint(20, 175 - a)
    # Decoy: subtracting only one of the two given angles.
    return (f"Third angle of a triangle with angles {a}° and {b}°",
            180 - a - b, 180 - max(a, b))


def g_translation(t):
    x, y = rint(-9 - 3 * t, 9 + 3 * t), rint(-9 - 3 * t, 9 + 3 * t)
    dx, dy = rint(-8, 8), rint(-8, 8)
    return (f"Point ({x}, {y}) is translated {dx} right and {dy} up. "
            f"The image's x-coordinate is", x + dx, x - dx)


def g_reflection(t):
    x, y = rint(-12 - 4 * t, 12 + 4 * t), rint(-12 - 4 * t, 12 + 4 * t)
    if coin():
        return (f"Point ({x}, {y}) is reflected in the x-axis. "
                f"The image's y-coordinate is", -y, y)
    return (f"Point ({x}, {y}) is reflected in the y-axis. "
            f"The image's x-coordinate is", -x, x)


def g_x_intercept(t):
    """Where a line crosses the x-axis. Decoy: the y-intercept."""
    m = rint(2, 6 + t)
    k = rint(1, 9 + 3 * t)
    return (f"x-intercept of y = {m}x − {m * k}", k, -m * k)


def g_distance_on_axis(t):
    x1, x2 = rint(-15 - 5 * t, 0), rint(1, 15 + 5 * t)
    y = rint(-9, 9)
    return (f"Distance between ({x1}, {y}) and ({x2}, {y})",
            x2 - x1, x2 + x1)


GEOMETRY = [
    (0, g_triangle_area), (0, g_prism_volume), (0, g_pythagoras_hyp),
    (0, g_angle_sum), (0, g_distance_on_axis),
    (1, g_composite_area), (1, g_prism_surface), (1, g_translation),
    (2, g_triangular_prism), (2, g_reflection), (2, g_x_intercept),
    (3, g_cone_volume), (3, g_pythagoras_leg), (3, g_cylinder_from_diameter),
]


# ══ FINANCIAL LITERACY ════════════════════════════════════════════════
def f_budget_left(t):
    income = rint(8, 24 + 10 * t) * 100
    rent = income * pick([30, 35, 40]) // 100
    food = rint(1, 4 + t) * 60
    other = rint(1, 5 + t) * 40
    return (f"Monthly income ${income}. Rent ${rent}, food ${food}, other "
            f"${other}. What's left is", income - rent - food - other,
            income - rent)                       # decoy: only the big expense


def f_savings_percent(t):
    income = rint(6, 20 + 8 * t) * 100
    pct = pick([5, 10, 12, 15, 20, 25])
    saved = income * pct // 100
    return (f"Someone earning ${income} a month saves ${saved}. That's what percent?",
            pct, 100 - pct)


def f_depreciation_one(t):
    value = rint(4, 16 + 6 * t) * 500
    pct = pick([10, 15, 20, 25])
    return (f"A ${value} asset depreciates {pct}% in a year. It's now worth",
            value * (100 - pct) // 100, value * pct // 100)


def f_appreciation_two(t):
    """Two years of appreciation — compounding, not doubling the rate."""
    value = rint(4, 14 + 5 * t) * 500
    pct = pick([10, 20, 25])
    after = round(value * (1 + pct / 100) ** 2)
    simple = value + 2 * (value * pct // 100)
    return (f"A ${value} asset appreciates {pct}% a year. After 2 years, "
            f"to the nearest dollar, it's worth", after, simple)


def f_simple_interest(t):
    p = rint(4, 20 + 8 * t) * 100
    r = pick([2, 3, 4, 5, 6])
    y = rint(2, 5 + min(3, t))
    return (f"${p} earns {r}% simple interest a year. The interest after {y} "
            f"years is", p * r * y // 100, p * r // 100)   # decoy: one year


def f_compound_interest(t):
    """Compound vs simple — the decoy is the simple-interest answer."""
    p = rint(4, 16 + 6 * t) * 250
    r = pick([4, 5, 8, 10])
    y = rint(2, 3 + min(2, t))
    comp = round(p * (1 + r / 100) ** y)
    simple = p + p * r * y // 100
    return (f"${p} earns {r}% compound interest a year. After {y} years the "
            f"balance, to the nearest dollar, is", comp, simple)


def f_unit_price_choice(t):
    """Which is cheaper per unit — answer is the better unit price."""
    per = rint(2, 8 + 2 * t)
    n1 = pick([4, 5, 6])
    n2 = n1 + rint(2, 5)
    cheaper = per
    dearer = per + rint(1, 3)
    return (f"Pack A: {n1} for ${n1 * dearer}. Pack B: {n2} for ${n2 * cheaper}. "
            f"The better unit price is", cheaper, dearer)


def f_income_after_deduction(t):
    gross = rint(8, 26 + 10 * t) * 100
    pct = pick([15, 20, 25, 30])
    return (f"Gross pay ${gross}, deductions {pct}%. Take-home pay is",
            gross * (100 - pct) // 100, gross * pct // 100)


FINANCE = [
    (0, f_budget_left), (0, f_savings_percent), (0, f_depreciation_one),
    (0, f_unit_price_choice),
    (1, f_simple_interest), (1, f_income_after_deduction),
    (2, f_appreciation_two),
    (3, f_compound_interest),
]


STRANDS_GENS = (NUMBER, ALGEBRA, DATA, GEOMETRY, FINANCE)


# ── Choosing a question ───────────────────────────────────────────────
def _choose(bank, tier):
    """Everything unlocked at this tier, weighted toward the hard end.

    Deeper laps don't just add hard questions to the pile — they stop
    asking the easy ones as often. A question whose minimum tier is close
    to the current one is worth more draws than a floor-1 warm-up."""
    unlocked = [(min_t, fn) for min_t, fn in bank if min_t <= tier]
    if not unlocked:
        unlocked = bank[:1]
    weighted = []
    for min_t, fn in unlocked:
        weight = 1 + min_t * 2 if tier else 1
        weighted.extend([fn] * weight)
    return pick(weighted)


def generate(floor):
    """One floor's question.

    Returns (question, correct, wrong, label). The caller decides which
    door gets which value and records only the side — the answer itself
    never goes to the browser.
    """
    floor = max(1, int(floor or 1))
    strand = strand_for_floor(floor)
    tier = tier_for_floor(floor)
    gen = _choose(STRANDS_GENS[strand], tier)
    question, correct, wrong = gen(tier)

    correct_s, wrong_s = num(correct), num(wrong)
    # A decoy that lands on the answer is no decoy. Nudge it rather than
    # redraw the whole question, so the mistake it models stays intact.
    if correct_s == wrong_s:
        if isinstance(correct, str):
            wrong_s = "rational" if correct_s == "irrational" else "irrational"
        else:
            step = max(1, abs(int(correct)) // 10)
            wrong_s = num(int(correct) + (step if coin() else -step))
        if correct_s == wrong_s:
            wrong_s = num(int(float(correct_s)) + 1)

    label = STRAND_LABELS[strand]
    if tier:
        label += f" · lap {tier + 1}"
    return question, correct_s, wrong_s, label
